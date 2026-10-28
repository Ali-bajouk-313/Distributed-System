import os

API_KEY = os.environ.get('API_KEY', '')
INTERNAL_KEY = os.environ.get('INTERNAL_KEY', '')
ETCD_ENDPOINTS = os.environ.get('ETCD_ENDPOINTS', 'http://etcd1:2379,http://etcd2:2379,http://etcd3:2379').split(',')
RABBITMQ_URL = os.environ.get('RABBITMQ_URL', 'amqp://storage:storage-lab@rabbit:5672/%2F')
ELASTIC_URL = os.environ.get('ELASTIC_URL', 'http://elasticsearch:9200')
CHUNK_SIZE = int(os.environ.get('CHUNK_SIZE', str(4 * 1024 * 1024)))
FAILURE_TIMEOUT = int(os.environ.get('FAILURE_TIMEOUT', '20'))
SCRUB_INTERVAL = int(os.environ.get('SCRUB_INTERVAL', '60'))
RATE_LIMIT = int(os.environ.get('RATE_LIMIT', '6000'))
