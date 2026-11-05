"""Install lab credentials into Kubernetes without printing them."""
import argparse,json,secrets,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--kubeconfig');a=p.parse_args()
path=Path('.env')
if not path.exists():
 values={name:secrets.token_hex(24) for name in ['API_KEY','INTERNAL_KEY','RABBIT_PASSWORD','GRAFANA_PASSWORD']}
 path.write_text('\n'.join(k+'='+v for k,v in values.items())+'\n',encoding='utf-8')
values=dict(line.split('=',1) for line in path.read_text(encoding='utf-8-sig').splitlines() if '=' in line and not line.startswith('#'))
base=['kubectl']+(['--kubeconfig',a.kubeconfig] if a.kubeconfig else [])
namespace={'apiVersion':'v1','kind':'Namespace','metadata':{'name':'atlas'}}
secret={'apiVersion':'v1','kind':'Secret','metadata':{'name':'atlas-secrets','namespace':'atlas'},'stringData':{key:values[key] for key in ['API_KEY','INTERNAL_KEY','RABBIT_PASSWORD','GRAFANA_PASSWORD']}}
for record in [namespace,secret]:subprocess.run(base+['apply','-f','-'],input=json.dumps(record),text=True,check=True)
