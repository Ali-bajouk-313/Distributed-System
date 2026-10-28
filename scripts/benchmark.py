"""Reproducible two-workload test; full profile matches the assignment examples.

Measures actual encoded storage and actual bytes fetched/written while restoring
one deliberately missing fragment per unique chunk. Scrub reads are excluded.
Run inside gateway1; output is mounted/copied to docs/benchmarks by the run script.
"""
import argparse
import hashlib
import io
import json
import random
import tempfile
import time
from pathlib import Path

from framework.coding import sha
from framework.engine import delete_object, maintain, node_request, object_id, upload
from framework.store import store


def run(profile='smoke', output='/tmp/benchmark-results.json'):
    full = profile == 'full'
    spec = [('many-small-files', 5000 if full else 24, 1024, 50 * 1024),
            ('few-large-files', 10 if full else 2, 100 * 1024 * 1024,
             100 * 1024 * 1024)]
    results = []
    dataset = []
    previous_pause = store.get('settings/maintenance', {'paused': False})
    store.set('settings/maintenance', {'paused': True})
    policies = {'replication': {'mode': 'replication', 'n': 3, 'w': 2, 'r': 2, 'k': 4, 'm': 2, 'compress': False},
                'erasure': {'mode': 'erasure', 'n': 3, 'w': 2, 'r': 2, 'k': 4, 'm': 2, 'compress': False}}
    try:
        for workload, file_count, low, high in spec:
            for mode, policy in policies.items():
                # A fixed seed regenerates exactly the same bytes for both policies.
                rng = random.Random(20261028)
                name = 'benchmark-' + mode + '-' + ('small' if 'small' in workload else 'large') + '-' + profile
                if store.list('objects/' + name + '/'):
                    raise RuntimeError('Benchmark bucket is not empty; clean it before rerunning full measurements')
                store.set('buckets/' + name, {'name': name, 'policy': policy, 'created': time.time()})
                records = []
                verified_downloads = 0
                unique = set()
                started = time.monotonic()
                for number in range(file_count):
                    size = rng.randint(low, high)
                    # One duplicate in ten small files makes deduplication measurable.
                    if number % 10 == 9 and 'small' in workload:
                        incoming = io.BytesIO(duplicate_body)
                    else:
                        incoming = tempfile.TemporaryFile()
                        remaining = size
                        hasher = hashlib.sha256()
                        while remaining:
                            block = rng.randbytes(min(4 * 1024 * 1024, remaining))
                            incoming.write(block)
                            hasher.update(block)
                            remaining -= len(block)
                        incoming.seek(0)
                        if 'small' in workload:
                            duplicate_body = incoming.read()
                            incoming.seek(0)
                    key = f'{number:05d}.bin'
                    record = upload(name, key, incoming, {'workload': workload, 'measurement': 'benchmark'})
                    incoming.close()
                    records.append(record)
                    unique.update(record['chunks'])
                    if number % 250 == 0 or workload == 'few-large-files':
                        from framework.engine import read_shards
                        downloaded = hashlib.sha256()
                        for identity in record['chunks']:
                            downloaded.update(read_shards(store.get('chunks/' + identity))[0])
                        if downloaded.hexdigest() != record['sha256']:
                            raise RuntimeError('Workload download integrity mismatch')
                        verified_downloads += 1
                    if mode == 'replication':
                        dataset.append({'workload': workload, 'key': key, 'size': record['size'], 'sha256': record['sha256']})
                    if number % 100 == 0:
                        print(f'{workload} {mode}: {number + 1}/{file_count}', flush=True)
                chunks = [store.get('chunks/' + identity) for identity in unique]
                physical = sum(loc['size'] for chunk in chunks for loc in chunk['locations'])
                unique_bytes = sum(chunk['size'] for chunk in chunks)
                logical = sum(record['size'] for record in records)
                for chunk in chunks:
                    location = chunk['locations'][0]
                    response = node_request('DELETE', location['node'], location['hash'])
                    if response is None or response.status_code != 204:
                        raise RuntimeError('Could not remove benchmark test fragment')
                # Limit scrub to this workload's chunks, avoiding changes to unrelated data.
                recovery = maintain('scrub', {'chunk_ids': sorted(unique)})
                result = {'workload': workload, 'mode': mode, 'file_count': file_count,
                    'logical_bytes': logical, 'unique_bytes': unique_bytes, 'physical_bytes': physical,
                    'overhead': physical / logical, 'coding_overhead': physical / unique_bytes,
                    'chunks': len(chunks), 'repair_read_bytes': recovery['read_bytes'],
                    'verified_downloads': verified_downloads,
                    'repair_write_bytes': recovery['write_bytes'], 'repaired_fragments': recovery['repaired'],
                    'seconds': round(time.monotonic() - started, 2),
                    'method': 'live cluster; one missing fragment per unique chunk'}
                results.append(result)
                report = {'profile': profile, 'created': time.time(), 'runs': results, 'dataset': dataset}
                Path(output).write_text(json.dumps(report, indent=2))
                store.set('stats/benchmarks', {key: value for key, value in report.items() if key != 'dataset'})
                print(json.dumps(result), flush=True)
                # Keep source generator + manifest instead of gigabytes of test artifacts.
                for record in records:
                    delete_object(name, record['key'])
                from framework.engine import garbage_collect
                garbage_collect()
    finally:
        store.set('settings/maintenance', previous_pause)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', choices=['smoke', 'full'], default='smoke')
    parser.add_argument('--output', default='/tmp/benchmark-results.json')
    options = parser.parse_args()
    run(options.profile, options.output)
