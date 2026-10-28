"""Generate a complete portable Kubernetes deployment without embedding secrets."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
items = []


def add(kind, name, spec=None, **fields):
    api = 'apps/v1' if kind in ('Deployment', 'StatefulSet') else 'autoscaling/v2' if kind == 'HorizontalPodAutoscaler' else 'v1'
    item = {'apiVersion': api, 'kind': kind, 'metadata': {'name': name, 'namespace': 'atlas'}, **fields}
    if spec is not None:
        item['spec'] = spec
    items.append(item)


items.append({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': 'atlas'}})
env = [
    {'name': 'ETCD_ENDPOINTS', 'value': ','.join(f'http://etcd-{i}.etcd:2379' for i in range(3))},
    {'name': 'ELASTIC_URL', 'value': 'http://elasticsearch:9200'},
    {'name': 'RABBITMQ_URL', 'value': 'amqp://storage:$(RABBIT_PASSWORD)@rabbit:5672/%2F'},
    {'name': 'FAILURE_TIMEOUT', 'value': '20'}, {'name': 'SCRUB_INTERVAL', 'value': '60'}]


def svc(name, port=8000, target=None, headless=False, labels=None, service_type=None):
    spec = {'selector': labels or {'app': name}, 'ports': [{'port': port, 'targetPort': target or port, 'name': 'http'}]}
    if headless:
        spec.update(clusterIP='None', publishNotReadyAddresses=True)
    if service_type:
        spec['type'] = service_type
    add('Service', name, spec)


def pvc(name='data', size='2Gi'):
    return {'metadata': {'name': name}, 'spec': {'accessModes': ['ReadWriteOnce'], 'resources': {'requests': {'storage': size}}}}


def container(name, image, port=8000, environment=None, mounts=None, resources=None, probe=True, **fields):
    result = {'name': name, 'image': image, 'imagePullPolicy': 'IfNotPresent',
              'ports': [{'containerPort': port}], **fields}
    if environment is not None:
        result['envFrom'] = [{'secretRef': {'name': 'atlas-secrets'}}]
        result['env'] = environment
    if mounts:
        result['volumeMounts'] = mounts
    result['resources'] = resources or {'requests': {'cpu': '50m', 'memory': '96Mi'},
                                       'limits': {'cpu': '1000m', 'memory': '512Mi'}}
    if probe:
        result['livenessProbe'] = {'httpGet': {'path': '/health', 'port': port}, 'initialDelaySeconds': 20, 'periodSeconds': 10}
        result['readinessProbe'] = {'httpGet': {'path': '/health', 'port': port}, 'initialDelaySeconds': 5, 'periodSeconds': 5}
    return result


def deployment(name, image, replicas=1, port=8000, environment=env, mounts=None, volumes=None, **kwargs):
    c = container(name, image, port, environment, mounts, **kwargs)
    spec = {'replicas': replicas, 'selector': {'matchLabels': {'app': name}},
            'template': {'metadata': {'labels': {'app': name}}, 'spec': {'containers': [c]}}}
    if volumes:
        spec['template']['spec']['volumes'] = volumes
    add('Deployment', name, spec)


# Three-member etcd Raft cluster with stable identities and persistent disks.
svc('etcd', 2379, headless=True)
cluster = ','.join(f'etcd-{i}=http://etcd-{i}.etcd:2380' for i in range(3))
etcd = container('etcd', 'quay.io/coreos/etcd:v3.5.17', 2379, probe=False,
    command=['/usr/local/bin/etcd'], args=[
        '--name=$(POD_NAME)', '--data-dir=/etcd-data', '--listen-client-urls=http://0.0.0.0:2379',
        '--advertise-client-urls=http://$(POD_NAME).etcd:2379', '--listen-peer-urls=http://0.0.0.0:2380',
        '--initial-advertise-peer-urls=http://$(POD_NAME).etcd:2380', '--initial-cluster=' + cluster,
        '--initial-cluster-token=atlas-kubernetes', '--initial-cluster-state=new',
        '--max-txn-ops=4096', '--max-request-bytes=8388608'],
    mounts=[{'name': 'data', 'mountPath': '/etcd-data'}])
etcd['env'] = [{'name': 'POD_NAME', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.name'}}}]
etcd['ports'].append({'containerPort': 2380})
for probe in ('livenessProbe', 'readinessProbe'):
    etcd[probe] = {'exec': {'command': ['/usr/local/bin/etcdctl', '--endpoints=http://localhost:2379', 'endpoint', 'health']},
                   'initialDelaySeconds': 20, 'periodSeconds': 10, 'timeoutSeconds': 5, 'failureThreshold': 6}
add('StatefulSet', 'etcd', {'serviceName': 'etcd', 'replicas': 3, 'podManagementPolicy': 'Parallel',
    'selector': {'matchLabels': {'app': 'etcd'}}, 'template': {'metadata': {'labels': {'app': 'etcd'}},
    'spec': {'containers': [etcd]}}, 'volumeClaimTemplates': [pvc()]})

svc('storage', headless=True)
node_env = env + [{'name': 'NODE_ID', 'valueFrom': {'fieldRef': {'fieldPath': 'metadata.name'}}},
                  {'name': 'NODE_URL', 'value': 'http://$(NODE_ID).storage:8000'},
                  {'name': 'METADATA_URL', 'value': 'http://metadata:8000'},
                  {'name': 'DATA_DIR', 'value': '/data/chunks'}]
add('StatefulSet', 'storage', {'serviceName': 'storage', 'replicas': 8, 'podManagementPolicy': 'Parallel',
    'selector': {'matchLabels': {'app': 'storage'}},
    'template': {'metadata': {'labels': {'app': 'storage'}}, 'spec': {'containers': [container(
        'storage', 'distributed-framework:storage', environment=node_env,
        mounts=[{'name': 'data', 'mountPath': '/data'}])]}}, 'volumeClaimTemplates': [pvc(size='10Gi')]})

svc('rabbit', 5672)
rabbit = container('rabbit', 'rabbitmq:4-management', 5672, environment=[], probe=False,
                   mounts=[{'name': 'data', 'mountPath': '/var/lib/rabbitmq'}])
rabbit['env'] = [{'name': 'RABBITMQ_DEFAULT_USER', 'value': 'storage'},
                 {'name': 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS', 'value': '+S 2:2 +A 4'},
                 {'name': 'RABBITMQ_CTL_ERL_ARGS', 'value': '+S 1:1 +A 1'},
                 {'name': 'RABBITMQ_DEFAULT_PASS', 'valueFrom': {'secretKeyRef': {'name': 'atlas-secrets', 'key': 'RABBIT_PASSWORD'}}}]
for probe in ('readinessProbe', 'livenessProbe'):
    rabbit[probe] = {'exec': {'command': ['rabbitmq-diagnostics', '-q', 'ping']},
                    'initialDelaySeconds': 30, 'periodSeconds': 15, 'timeoutSeconds': 10}
add('StatefulSet', 'rabbit', {'serviceName': 'rabbit', 'replicas': 1, 'selector': {'matchLabels': {'app': 'rabbit'}},
    'template': {'metadata': {'labels': {'app': 'rabbit'}}, 'spec': {'containers': [rabbit]}}, 'volumeClaimTemplates': [pvc()]})

svc('elasticsearch', 9200)
elastic = container('elasticsearch', 'docker.elastic.co/elasticsearch/elasticsearch:8.15.0', 9200,
    command=['/usr/share/elasticsearch/bin/elasticsearch'],
    args=['-Ediscovery.type=single-node', '-Expack.security.enabled=false', '-Expack.ml.enabled=false', '-Enetwork.host=0.0.0.0'],
    environment=[], probe=False, mounts=[{'name': 'data', 'mountPath': '/usr/share/elasticsearch/data'}],
    resources={'requests': {'cpu': '100m', 'memory': '512Mi'}, 'limits': {'cpu': '1500m', 'memory': '1536Mi'}})
elastic['env'] = [{'name': name, 'value': value} for name, value in {
    'discovery.type': 'single-node', 'xpack.security.enabled': 'false', 'xpack.ml.enabled': 'false',
    'ES_JAVA_OPTS': '-Xms384m -Xmx384m'}.items()]
for probe in ('livenessProbe', 'readinessProbe'):
    elastic[probe] = {'httpGet': {'path': '/_cluster/health', 'port': 9200},
                      'initialDelaySeconds': 60 if probe == 'livenessProbe' else 20,
                      'periodSeconds': 10, 'timeoutSeconds': 5}
add('StatefulSet', 'elasticsearch', {'serviceName': 'elasticsearch', 'replicas': 1,
    'selector': {'matchLabels': {'app': 'elasticsearch'}},
    'template': {'metadata': {'labels': {'app': 'elasticsearch'}},
    'spec': {'securityContext': {'fsGroup': 1000}, 'containers': [elastic]}},
    'volumeClaimTemplates': [pvc(size='5Gi')]})

for name, role, replicas in [('metadata', 'metadata', 2), ('gateway', 'gateway', 2), ('worker', 'worker', 2)]:
    deployment(name, 'distributed-framework:' + role, replicas)
    svc(name)

# Nginx remains in front of >=2 gateway pods; the Service distributes connections.
nginx = '''events { worker_connections 2048; }
http {
  upstream gateways { server gateway:8000; }
  server {
    listen 80;
    client_max_body_size 2g;
    proxy_request_buffering off;
    proxy_buffering off;
    proxy_read_timeout 900s;
    proxy_send_timeout 900s;
    location / { proxy_pass http://gateways; }
  }
}'''
add('ConfigMap', 'nginx-config', data={'nginx.conf': nginx})
deployment('loadbalancer', 'nginx:stable', 2, port=80, environment=None, probe=False,
           mounts=[{'name': 'config', 'mountPath': '/etc/nginx/nginx.conf', 'subPath': 'nginx.conf'}],
           volumes=[{'name': 'config', 'configMap': {'name': 'nginx-config'}}])
svc('loadbalancer', 80)
deployment('ui', 'distributed-framework:ui', 2, port=80, environment=None, probe=False)
svc('ui', 80, service_type='LoadBalancer')

add('HorizontalPodAutoscaler', 'gateway', {'scaleTargetRef': {'apiVersion': 'apps/v1', 'kind': 'Deployment', 'name': 'gateway'},
    'minReplicas': 2, 'maxReplicas': 6, 'metrics': [{'type': 'Resource', 'resource': {'name': 'cpu',
    'target': {'type': 'Utilization', 'averageUtilization': 65}}}]})

prometheus = '''global:
  scrape_interval: 10s
scrape_configs:
  - job_name: gateway
    kubernetes_sd_configs:
      - role: pod
        namespaces: {names: [atlas]}
    relabel_configs:
      - source_labels: [__meta_kubernetes_pod_label_app]
        action: keep
        regex: gateway|storage|metadata
      - source_labels: [__meta_kubernetes_pod_ip]
        target_label: __address__
        replacement: '$1:8000'
'''
add('ConfigMap', 'prometheus-config', data={'prometheus.yml': prometheus})
add('ServiceAccount', 'prometheus')
items.append({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'Role', 'metadata': {'name': 'prometheus', 'namespace': 'atlas'},
              'rules': [{'apiGroups': [''], 'resources': ['pods'], 'verbs': ['get', 'list', 'watch']}]})
items.append({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'RoleBinding', 'metadata': {'name': 'prometheus', 'namespace': 'atlas'},
              'subjects': [{'kind': 'ServiceAccount', 'name': 'prometheus'}],
              'roleRef': {'kind': 'Role', 'name': 'prometheus', 'apiGroup': 'rbac.authorization.k8s.io'}})
deployment('prometheus', 'prom/prometheus:latest', port=9090, environment=None, probe=False,
           mounts=[{'name': 'config', 'mountPath': '/etc/prometheus/prometheus.yml', 'subPath': 'prometheus.yml'}],
           volumes=[{'name': 'config', 'configMap': {'name': 'prometheus-config'}}])
items[-1]['spec']['template']['spec']['serviceAccountName'] = 'prometheus'
svc('prometheus', 9090)

dashboard = (ROOT / 'deploy/grafana/dashboards/storage.json').read_text()
datasource = 'apiVersion: 1\ndatasources:\n  - name: Prometheus\n    uid: prometheus\n    type: prometheus\n    access: proxy\n    url: http://prometheus:9090\n    isDefault: true\n'
provider = 'apiVersion: 1\nproviders:\n  - name: Atlas\n    type: file\n    options:\n      path: /var/lib/grafana/dashboards\n'
add('ConfigMap', 'grafana-config', data={'datasource.yml': datasource, 'provider.yml': provider, 'storage.json': dashboard})
deployment('grafana', 'grafana/grafana:latest', port=3000, environment=[
    {'name': 'GF_SECURITY_ADMIN_USER', 'value': 'admin'},
    {'name': 'GF_SECURITY_ADMIN_PASSWORD', 'valueFrom': {'secretKeyRef': {'name': 'atlas-secrets', 'key': 'GRAFANA_PASSWORD'}}}],
    probe=False, mounts=[
        {'name': 'config', 'mountPath': '/etc/grafana/provisioning/datasources/datasource.yml', 'subPath': 'datasource.yml'},
        {'name': 'config', 'mountPath': '/etc/grafana/provisioning/dashboards/provider.yml', 'subPath': 'provider.yml'},
        {'name': 'config', 'mountPath': '/var/lib/grafana/dashboards/storage.json', 'subPath': 'storage.json'}],
    volumes=[{'name': 'config', 'configMap': {'name': 'grafana-config'}}])
svc('grafana', 3000)

Path(ROOT / 'deploy/k8s/stack.yaml').write_text(json.dumps({'apiVersion': 'v1', 'kind': 'List', 'items': items}, indent=2) + '\n')
print(f'Generated {len(items)} Kubernetes resources, no embedded secrets.')
