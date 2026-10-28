import logging
import os
import threading
import time
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

from framework import engine
from framework.jobs import QUEUE, connection
from framework.store import store


def execute(job):
    def progress(current, total):
        job.update(progress=current, total=total, updated=time.time())
        store.set('jobs/' + job['id'], job)
    kind = job['kind']
    if kind in ('repair', 'scrub', 'rebalance'):
        return engine.maintain(kind, job['payload'], progress)
    if kind == 'gc':
        return engine.garbage_collect(progress)
    if kind in ('index', 'reindex'):
        from framework.search import delete_index, ensure_index, index_object
        ensure_index()
        if kind == 'reindex':
            records = list(store.list('objects/').values())
        else:
            bucket, key = job['payload']['bucket'], job['payload']['key']
            record = store.get('objects/' + engine.object_id(bucket, key))
            if record is None:
                delete_index(bucket, key, deferred=False)
                return {'indexed': 0}
            records = [record]
        for number, record in enumerate(records):
            index_object(record, deferred=False)
            progress(number + 1, len(records))
        return {'indexed': len(records)}
    raise ValueError('Unknown maintenance job')


def handle(channel, method, properties, body):
    job_id = json.loads(body)['id']
    try:
        with store.locked('job-' + job_id, timeout=5):
            job = store.get('jobs/' + job_id)
            if not job or job['status'] == 'completed':
                channel.basic_ack(method.delivery_tag)
                return
            if job['kind'] in ('index', 'reindex') and not store.get('settings/search_ready', False):
                job['status'] = 'pending'
                store.set('jobs/' + job_id, job)
                channel.basic_ack(method.delivery_tag)
                return
            if job['status'] == 'running' and time.time() - job.get('updated', 0) < 300:
                channel.basic_nack(method.delivery_tag, requeue=True)
                time.sleep(0.2)
                return
            job.update(status='running', worker=os.getenv('HOSTNAME', 'worker'),
                       attempts=job['attempts'] + 1, updated=time.time())
            store.set('jobs/' + job_id, job)
        result = execute(job)
        job.update(status='completed', result=result, finished=time.time(), progress=job.get('total', 0))
        store.set('jobs/' + job_id, job)
        channel.basic_ack(method.delivery_tag)
    except Exception as error:
        logging.exception('Maintenance job failed')
        job = store.get('jobs/' + job_id)
        if job:
            job.update(status='pending' if job['attempts'] < 5 else 'failed', error=str(error), updated=time.time())
            store.set('jobs/' + job_id, job)
        # The durable outbox retries pending jobs with bounded attempts.
        channel.basic_ack(method.delivery_tag)


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *args):
        pass


def main():
    threading.Thread(target=lambda: HTTPServer(('0.0.0.0', 8000), Health).serve_forever(), daemon=True).start()
    while True:
        try:
            conn, channel = connection()
            channel.basic_qos(prefetch_count=1)
            channel.basic_consume(queue=QUEUE, on_message_callback=handle)
            channel.start_consuming()
        except Exception:
            logging.exception('Worker reconnecting')
            time.sleep(5)


if __name__ == '__main__':
    main()
