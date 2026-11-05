"""Finish obsolete benchmark search jobs with real Elasticsearch bulk deletes."""
import json
import time
import requests
from framework.engine import object_id
from framework.search import INDEX, document_id
from framework.settings import ELASTIC_URL
from framework.store import put_op, store

objects=store.list('objects/')
jobs=[j for j in store.list('jobs/').values() if j['kind']=='index' and j['status'] in ('pending','queued') and j['payload']['bucket'].startswith('benchmark-') and 'objects/'+object_id(j['payload']['bucket'],j['payload']['key']) not in objects]
client=requests.Session();client.trust_env=False
completed=0
for offset in range(0,len(jobs),200):
 batch=jobs[offset:offset+200]
 body=''.join(json.dumps({'delete':{'_index':INDEX,'_id':document_id(j['payload']['bucket'],j['payload']['key'])}})+'\n' for j in batch)
 result=client.post(ELASTIC_URL+'/_bulk',data=body,headers={'Content-Type':'application/x-ndjson'},timeout=60);result.raise_for_status()
 if result.json().get('errors'):raise RuntimeError('Bulk deletion failed; job states retained')
 operations=[]
 for j in batch:
  current=store.get('jobs/'+j['id'])
  if current['status'] not in ('pending','queued'):continue
  current.update(status='completed',finished=time.time(),result={'indexed':0,'deleted':1,'method':'bulk deletion of removed benchmark metadata'})
  operations.append(put_op('jobs/'+j['id'],current))
 if operations:store.transaction(operations)
 completed+=len(operations)
 print('Completed obsolete benchmark index jobs:',completed,'/',len(jobs),flush=True)
