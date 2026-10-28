"""Generate the reproducible local topology; JSON is a valid YAML subset."""
import json
from pathlib import Path

common = {
    'API_KEY': '${API_KEY:?Run scripts/setup.ps1 first}',
    'INTERNAL_KEY': '${INTERNAL_KEY:?Run scripts/setup.ps1 first}',
    'ETCD_ENDPOINTS': 'http://etcd1:2379,http://etcd2:2379,http://etcd3:2379',
    'RABBITMQ_URL': 'amqp://storage:${RABBIT_PASSWORD}@rabbit:5672/%2F',
    'ELASTIC_URL': 'http://elasticsearch:9200',
    'FAILURE_TIMEOUT': '20', 'SCRUB_INTERVAL': '60', 'RATE_LIMIT': '6000'
}
services = {}
volumes = {}


def service(name, image, **kwargs):
    record = {'image': image, 'restart': 'unless-stopped', 'networks': ['distributed'], **kwargs}
    services[name] = record
    return record


def persistent(name):
    volumes[name] = {}
    return name


cluster = ','.join(f'etcd{i}=http://etcd{i}:2380' for i in range(1, 4))
for i in range(1, 4):
    name = f'etcd{i}'
    service(name, 'quay.io/coreos/etcd:v3.5.17', command=[
        '/usr/local/bin/etcd', '--name', name, '--data-dir', '/etcd-data',
        '--listen-client-urls', 'http://0.0.0.0:2379', '--advertise-client-urls', f'http://{name}:2379',
        '--listen-peer-urls', 'http://0.0.0.0:2380', '--initial-advertise-peer-urls', f'http://{name}:2380',
        '--initial-cluster', cluster, '--initial-cluster-token', 'distributed-framework',
        '--initial-cluster-state', 'new', '--max-txn-ops', '4096', '--max-request-bytes', '8388608'],
        volumes=[persistent(name + '-data') + ':/etcd-data'],
        healthcheck={'test': ['CMD', '/usr/local/bin/etcdctl', '--endpoints=http://localhost:2379', 'endpoint', 'health'],
                     'interval': '10s', 'timeout': '5s', 'retries': 12})

service('rabbit', 'rabbitmq:4-management', environment={'RABBITMQ_DEFAULT_USER': 'storage',
    'RABBITMQ_DEFAULT_PASS': '${RABBIT_PASSWORD}', 'RABBITMQ_SERVER_ADDITIONAL_ERL_ARGS': '+S 2:2 +A 4',
    'RABBITMQ_CTL_ERL_ARGS': '+S 1:1 +A 1'}, ports=['127.0.0.1:15673:15672'],
    volumes=[persistent('rabbit-data') + ':/var/lib/rabbitmq'],
    healthcheck={'test': ['CMD', 'rabbitmq-diagnostics', '-q', 'ping'], 'interval': '15s', 'timeout': '20s', 'retries': 12})
service('elasticsearch', 'docker.elastic.co/elasticsearch/elasticsearch:8.15.0',
    init=True, entrypoint=['/usr/share/elasticsearch/bin/elasticsearch'],
    command=['-Ediscovery.type=single-node', '-Expack.security.enabled=false',
             '-Expack.ml.enabled=false', '-Enetwork.host=0.0.0.0'],
    environment={'discovery.type': 'single-node', 'xpack.security.enabled': 'false',
                 'ES_JAVA_OPTS': '-Xms384m -Xmx384m', 'xpack.ml.enabled': 'false'},
    mem_limit='1g', volumes=[persistent('elastic-data') + ':/usr/share/elasticsearch/data'],
    healthcheck={'test': ['CMD-SHELL', 'curl -fsS http://localhost:9200/_cluster/health >/dev/null'],
                 'interval': '10s', 'timeout': '5s', 'retries': 24})

healthy_etcd = {f'etcd{i}': {'condition': 'service_healthy'} for i in range(1, 4)}
for role, names in [('metadata', ['metadata', 'metadata2']), ('gateway', ['gateway1', 'gateway2']),
                    ('worker', ['worker1', 'worker2'])]:
    for name in names:
        service(name, f'distributed-framework:{role}', build={'context': '.', 'dockerfile': f'docker/Dockerfile.{role}'},
                environment=common, depends_on={**healthy_etcd, 'rabbit': {'condition': 'service_healthy'}},
                healthcheck={'test': ['CMD', 'python', '-c',
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health',timeout=3)"],
                    'interval': '10s', 'timeout': '5s', 'retries': 12})

for i in range(1, 10):
    name = f'node{i}'
    item = service(name, 'distributed-framework:storage',
        build={'context': '.', 'dockerfile': 'docker/Dockerfile.storage'},
        environment={**common, 'NODE_ID': name, 'NODE_URL': f'http://{name}:8000',
                     'METADATA_URL': 'http://loadbalancer/internal', 'DATA_DIR': '/data/chunks'},
        volumes=[persistent(name + '-v2-data') + ':/data'],
        depends_on={'loadbalancer': {'condition': 'service_started'}},
        healthcheck={'test': ['CMD', 'python', '-c',
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health',timeout=3)"],
                    'interval': '10s', 'timeout': '5s', 'retries': 6})
    if i == 9:
        item['profiles'] = ['scale']

service('loadbalancer', 'nginx:stable', ports=['127.0.0.1:8000:80'],
        volumes=['./deploy/nginx/api.conf:/etc/nginx/nginx.conf:ro'],
        depends_on={name: {'condition': 'service_started'} for name in ('gateway1', 'gateway2', 'metadata', 'metadata2')})
service('ui', 'distributed-framework:ui', build={'context': './ui', 'dockerfile': 'Dockerfile', 'secrets': ['npm_ca']},
        ports=['127.0.0.1:8088:80'], depends_on={'loadbalancer': {'condition': 'service_started'}})
service('prometheus-v2', 'prom/prometheus:latest', ports=['127.0.0.1:9090:9090'],
        volumes=['./deploy/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro',
                 persistent('prometheus-v2-data') + ':/prometheus'])
service('grafana-v2', 'grafana/grafana:latest', ports=['127.0.0.1:3000:3000'],
        environment={'GF_SECURITY_ADMIN_USER': 'admin', 'GF_SECURITY_ADMIN_PASSWORD': '${GRAFANA_PASSWORD}'},
        volumes=[persistent('grafana-v2-data') + ':/var/lib/grafana',
                 './deploy/grafana/provisioning:/etc/grafana/provisioning:ro',
                 './deploy/grafana/dashboards:/var/lib/grafana/dashboards:ro'])
Path('compose.yaml').write_text(json.dumps({'name': 'distributed', 'services': services,
    'networks': {'distributed': {'external': True, 'name': 'distributed-net'}}, 'volumes': volumes,
    'secrets': {'npm_ca': {'file': '.local-ca.pem'}}}, indent=2) + '\n')
