"""Verify a real Kubernetes deployment, fault tolerance, rebalancing and HPA."""
import argparse,hashlib,json,os,subprocess,time,uuid
from pathlib import Path
import requests
p=argparse.ArgumentParser();p.add_argument('--kubeconfig');p.add_argument('--base',default='http://127.0.0.1:8100');p.add_argument('--skip-hpa',action='store_true');a=p.parse_args()
config=['--kubeconfig',a.kubeconfig] if a.kubeconfig else []
key=os.getenv('API_KEY') or dict(line.split('=',1) for line in Path('.env').read_text(encoding='utf-8-sig').splitlines() if '=' in line)['API_KEY']
client=requests.Session();client.trust_env=False;client.headers['X-API-Key']=key
results={};buckets=[];load=None

def kube(*args,json_output=False):
 r=subprocess.run(['kubectl',*config,'-n','atlas',*args],text=True,capture_output=True,check=True)
 return json.loads(r.stdout) if json_output else r.stdout

def api(method,path,status=200,**kwargs):
 r=client.request(method,a.base+path,timeout=120,**kwargs)
 if r.status_code!=status:raise AssertionError((path,r.status_code,r.text[:300]))
 return r

def wait(check,timeout=300):
 end=time.time()+timeout;last=None
 while time.time()<end:
  try:
   value=check()
   if value:return value
  except (requests.RequestException,AssertionError,subprocess.CalledProcessError,KeyError,StopIteration) as error:last=str(error)
  time.sleep(3)
 raise AssertionError('Condition not reached: '+str(last))

def cluster():return api('GET','/cluster').json()
def pod(name):return kube('get','pod',name,'-o','json',json_output=True)
def ready(name,old=None):
 record=pod(name)
 return record if record['metadata']['uid']!=old and all(c['ready'] for c in record.get('status',{}).get('containerStatuses',[])) and record.get('status',{}).get('containerStatuses') else None

try:
 wait(lambda:cluster()['healthy_nodes']>=8)
 body=os.urandom(16*1024*1024+13);digest=hashlib.sha256(body).hexdigest()
 for mode in ['replication','erasure']:
  name='kube-verify-'+mode+'-'+uuid.uuid4().hex[:8];buckets.append(name)
  policy={'mode':'replication','n':3,'w':2,'r':2} if mode=='replication' else {'mode':'erasure','k':4,'m':2}
  api('POST','/buckets',201,json={'name':name,'policy':policy})
  api('PUT',f'/objects/{name}/proof.bin',201,data=body,headers={'X-Object-Tags':'{"test":"kubernetes"}'})
  assert hashlib.sha256(api('GET',f'/objects/{name}/proof.bin').content).hexdigest()==digest
 results['replication_and_erasure_round_trips']='PASS';print('Verified replicated and erasure downloads',flush=True)
 ec=buckets[1];path=f'/objects/{ec}/proof.bin'
 wait(lambda:api('GET','/search',params={'bucket':ec,'prefix':'proof','tags':'{"test":"kubernetes"}'}).json()['total']>=1)
 results['elasticsearch_search']='PASS'
 # A failed pod must restart with its persistent data and a new UID.
 previous=pod('storage-0')['metadata']['uid'];kube('delete','pod','storage-0','--wait=false')
 wait(lambda:ready('storage-0',previous));assert api('GET',path).content==body
 results['storage_pod_restart_and_persistent_volume']='PASS';print('Verified storage restart and persistent data',flush=True)
 # Remove the last ordinal long enough for heartbeat-driven repair.
 kube('scale','statefulset/storage','--replicas=7')
 wait(lambda:any(n['node_id']=='storage-7' and n['health']=='failed' for n in cluster()['nodes']))
 def relocated():
  detail=api('GET',f'/metadata/{ec}/proof.bin').json()
  return all(loc['node']!='storage-7' for c in detail['chunk_details'] for loc in c['locations'])
 wait(relocated);assert api('GET',path).content==body
 results['missed_heartbeats_and_automatic_repair']='PASS'
 kube('scale','statefulset/storage','--replicas=8');wait(lambda:cluster()['healthy_nodes']>=8)
 previous=pod('etcd-0')['metadata']['uid'];kube('delete','pod','etcd-0','--wait=false');wait(lambda:api('GET','/buckets').status_code==200);wait(lambda:ready('etcd-0',previous))
 results['metadata_member_restart']='PASS'
 gateways=kube('get','pods','-l','app=gateway','-o','json',json_output=True)['items'];gateway=gateways[0]['metadata']['name']
 kube('delete','pod',gateway,'--wait=false');wait(lambda:api('GET',path).content==body)
 results['gateway_pod_failover']='PASS'
 kube('scale','statefulset/storage','--replicas=9');wait(lambda:cluster()['healthy_nodes']>=9)
 joined=wait(lambda:next((n for n in cluster()['nodes'] if n['node_id']=='storage-8' and n.get('assigned_fragments',0)>0),None))
 results['new_storage_node_rebalancing']={'status':'PASS','assigned_fragments':joined['assigned_fragments']};print('Verified storage expansion and rebalancing',flush=True)
 if not a.skip_hpa:
  wait(lambda:len(kube('get','pods','-l','app=gateway','-o','json',json_output=True)['items'])>=2)
  target=next(pod for pod in kube('get','pods','-l','app=gateway','-o','json',json_output=True)['items'] if pod['status'].get('phase')=='Running')['metadata']['name']
  workload='import time,hashlib; stop=time.time()+100; data=b"x"*1048576\nwhile time.time()<stop: hashlib.sha256(data).digest()'
  load=subprocess.Popen(['kubectl',*config,'-n','atlas','exec',target,'--','python','-c',workload],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
  scaled=wait(lambda:(lambda h:h if h.get('status',{}).get('desiredReplicas',0)>2 and h.get('status',{}).get('currentReplicas',0)>2 else None)(kube('get','hpa','gateway','-o','json',json_output=True)),240)
  results['gateway_cpu_autoscaling']={'status':'PASS','replicas':scaled['status']['currentReplicas'],'method':'100 second controlled CPU workload in one gateway pod'}
  print('Verified HPA scale out',flush=True)
 results['verified_sha256']=digest;results['status']='PASS'
finally:
 try:
  if kube('get','statefulset','storage','-o','json',json_output=True)['spec']['replicas']<8:kube('scale','statefulset/storage','--replicas=8')
 except Exception:pass
 if load:
  try:load.wait(timeout=110)
  except subprocess.TimeoutExpired:load.terminate()
 # Keep the expanded nine-node topology; remove only this script's disposable objects.
 for bucket in buckets:
  try:api('DELETE',f'/objects/{bucket}/proof.bin',204)
  except Exception:pass
 Path('docs').mkdir(exist_ok=True);Path('docs/kubernetes-verification.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
print(json.dumps(results,indent=2))
