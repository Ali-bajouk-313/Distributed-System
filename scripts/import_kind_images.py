"""Import only the Linux amd64 image manifests, including Docker containerd stores."""
import argparse,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--name',default='atlas');p.add_argument('--archive',default='tmp/kubernetes-images.tar');a=p.parse_args()
images=['distributed-framework:'+role for role in ['gateway','metadata','worker','storage','ui']]+['nginx:stable','quay.io/coreos/etcd:v3.5.17','rabbitmq:4-management','prom/prometheus:latest','grafana/grafana:latest','docker.elastic.co/elasticsearch/elasticsearch:8.15.5']
archive=Path(a.archive);archive.parent.mkdir(exist_ok=True,parents=True)
subprocess.run(['docker','save','--platform','linux/amd64','-o',str(archive),*images],check=True)
with archive.open('rb') as incoming:subprocess.run(['docker','exec','-i',a.name+'-control-plane','ctr','--namespace=k8s.io','images','import','--platform','linux/amd64','--digests','-'],stdin=incoming,stdout=subprocess.DEVNULL,check=True)
print('Imported all lab images into '+a.name)
