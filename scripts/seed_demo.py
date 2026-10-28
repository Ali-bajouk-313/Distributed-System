import io
import json
import os
import time

from framework.engine import upload
from framework.search import ensure_index
from framework.store import store


def seed():
    try:
        ensure_index()
    except Exception:
        # Durable index jobs will catch up when the search service is ready.
        pass
    replicated = {'mode': 'replication', 'n': 3, 'w': 2, 'r': 2, 'k': 4, 'm': 2, 'compress': False}
    erasure = {**replicated, 'mode': 'erasure'}
    for name, policy in [('project-files', replicated), ('media-archive', erasure)]:
        if not store.get('buckets/' + name):
            store.set('buckets/' + name, {'name': name, 'policy': policy, 'created': time.time()})
    examples = [
        ('project-files', 'reports/architecture-notes.txt', b'Atlas distributed object storage\nContent addressing, consistent hashing, quorum replication, Reed-Solomon coding and automated recovery.\n' * 30, 'text/plain'),
        ('project-files', 'config/cluster-settings.json', json.dumps({'nodes': 8, 'replicas': 3, 'quorum': {'write': 2, 'read': 2}, 'chunk_size': '4 MiB'}, indent=2).encode(), 'application/json'),
        ('project-files', 'datasets/sensor-readings.json', json.dumps([{'sensor': n, 'temperature': 20 + n % 9, 'status': 'healthy'} for n in range(1200)], indent=2).encode(), 'application/json'),
        ('project-files', 'reports/experiment-log.txt', b'Upload -> hash -> place -> protect -> verify\n' * 900, 'text/plain'),
        ('project-files', 'datasets/sensor-readings-copy.json', json.dumps([{'sensor': n, 'temperature': 20 + n % 9, 'status': 'healthy'} for n in range(1200)], indent=2).encode(), 'application/json'),
        ('media-archive', 'experiments/sample-recording.bin', os.urandom(8 * 1024 * 1024 + 193), 'application/octet-stream')]
    for bucket, key, body, mime in examples:
        from framework.engine import object_id
        if not store.get('objects/' + object_id(bucket, key)):
            upload(bucket, key, io.BytesIO(body), {'team': 'distributed-systems', 'purpose': 'demo'}, mime)
    print('Demo buckets and six sample objects are ready.')


if __name__ == '__main__':
    seed()
