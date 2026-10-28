import json
import time
import uuid

import pika

from framework.settings import RABBITMQ_URL
from framework.store import store

QUEUE = 'storage.maintenance'


def connection():
    parameters = pika.URLParameters(RABBITMQ_URL)
    parameters.socket_timeout = 5
    parameters.blocked_connection_timeout = 10
    parameters.heartbeat = 0
    conn = pika.BlockingConnection(parameters)
    channel = conn.channel()
    channel.queue_declare(queue=QUEUE, durable=True)
    channel.confirm_delivery()
    return conn, channel


def enqueue(kind, payload=None, unique=None):
    # Durable etcd outbox closes the metadata-commit / broker-publish gap.
    identity = unique or uuid.uuid4().hex
    job_id = kind + '-' + identity
    existing = store.get('jobs/' + job_id)
    if existing and existing['status'] in ('pending', 'queued', 'running'):
        return existing
    job = {'id': job_id, 'kind': kind, 'payload': payload or {}, 'status': 'pending',
           'created': time.time(), 'progress': 0, 'total': 0, 'attempts': 0}
    store.set('jobs/' + job_id, job)
    return job


def dispatch_outbox():
    search_ready = store.get('settings/search_ready', False)
    pending = [job for job in store.list('jobs/').values()
               if (job['kind'] not in ('index', 'reindex') or search_ready)
               and (job['status'] == 'pending' or (job['status'] == 'queued' and time.time() - job.get('published', 0) > 90))]
    pending.sort(key=lambda job: job['kind'] in ('index', 'reindex'))
    if not pending:
        return
    conn, channel = connection()
    try:
        for job in pending:
            channel.basic_publish(exchange='', routing_key=QUEUE, body=json.dumps({'id': job['id']}),
                                  properties=pika.BasicProperties(delivery_mode=2))
            # Mark queued only if a worker has not already claimed/completed the job.
            with store.locked('job-' + job['id']):
                current = store.get('jobs/' + job['id'])
                if current['status'] in ('pending', 'queued'):
                    current.update(status='queued', published=time.time())
                    store.set('jobs/' + job['id'], current)
    finally:
        conn.close()
