"""Immutable chunks, quorum replication, Reed-Solomon stripes and node repair."""
import copy
import hashlib
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import requests
from fastapi import HTTPException

from framework.coding import decode, encode, sha
from framework.settings import CHUNK_SIZE, FAILURE_TIMEOUT, INTERNAL_KEY
from framework.store import delete_op, put_op, store
from storage.ring import HashRing

POOL = ThreadPoolExecutor(max_workers=12)


def object_id(bucket, key):
    return bucket + '/' + hashlib.sha256(key.encode()).hexdigest()


def live_nodes():
    now = time.time()
    return {n['node_id']: n for n in store.list('nodes/').values()
            if not n.get('failed') and now - n['last_seen'] < FAILURE_TIMEOUT}


def policy_id(policy):
    return sha(json.dumps(policy, sort_keys=True).encode())[:16]


def node_request(method, node, digest, body=None):
    record = store.get('nodes/' + node)
    if not record:
        return None
    session = requests.Session()
    session.trust_env = False
    try:
        return session.request(method, record.get('direct_url', record['url']) + '/chunks/' + digest,
                               data=body, headers={'X-Internal-Key': INTERNAL_KEY}, timeout=(1, 15))
    except requests.RequestException:
        return None
    finally:
        session.close()


def read_shards(chunk, quorum=True, minimal=False):
    alive = live_nodes()
    locations = chunk['locations']
    futures = [(location, POOL.submit(node_request, 'GET', location['node'], location['hash']))
               for location in locations if location['node'] in alive]
    valid = []
    responses = 0
    traffic = 0
    bad = [location for location in locations if location['node'] not in alive]
    minimum = chunk['policy']['k'] if chunk['policy']['mode'] == 'erasure' else 1
    for location, future in futures:
        if minimal and len(valid) >= minimum:
            # Outstanding requests are allowed to finish, but repair reads are
            # deliberately selected by recover_data below when measuring traffic.
            continue
        response = future.result()
        if response is not None and response.status_code == 200:
            traffic += len(response.content)
            if sha(response.content) == location['hash']:
                responses += 1
                valid.append((location, response.content))
            else:
                bad.append(location)
        elif response is not None and response.status_code == 404:
            responses += 1
            bad.append(location)
        else:
            bad.append(location)
    required = chunk['policy']['r'] if chunk['policy']['mode'] == 'replication' and quorum else minimum
    if responses < required or len(valid) < minimum:
        raise HTTPException(503, f'Chunk unavailable: {len(valid)} valid fragments; quorum requires {required} responses')
    try:
        body = decode([body for _, body in valid], [location['index'] for location, _ in valid],
                      chunk['encoded_size'], chunk['policy'], chunk['hash'])
    except (ValueError, Exception) as error:
        raise HTTPException(409, 'Content integrity verification failed') from error
    return body, bad, traffic


def write_chunk(data, policy, existing=None):
    digest = sha(data)
    identity = policy_id(policy) + '-' + digest
    chunk = existing or store.get('chunks/' + identity)
    blocks, encoded_size = encode(data, policy)
    alive = live_nodes()
    count = policy['n'] if policy['mode'] == 'replication' else policy['k'] + policy['m']
    if len(alive) < count and not chunk:
        raise HTTPException(503, f'Need {count} healthy nodes; have {len(alive)}')
    if chunk:
        locations = chunk['locations']
    else:
        owners = HashRing(alive).owners(identity, count)
        locations = [{'index': i, 'node': node, 'hash': sha(blocks[i]), 'size': len(blocks[i])}
                     for i, node in enumerate(owners)]
    futures = [(loc, POOL.submit(node_request, 'PUT', loc['node'], loc['hash'], blocks[loc['index']]))
               for loc in locations if loc['node'] in alive]
    acknowledgments = sum(1 for _, future in futures
                          if (response := future.result()) is not None and response.status_code == 200)
    required = policy['w'] if policy['mode'] == 'replication' else count
    if acknowledgments < required:
        raise HTTPException(503, f'Write quorum failed: {acknowledgments}/{required}')
    return chunk or {'id': identity, 'hash': digest, 'size': len(data), 'encoded_size': encoded_size,
                     'policy': policy, 'locations': locations, 'refs': 0, 'created': time.time()}


def upload(bucket, key, incoming, tags=None, content_type='application/octet-stream'):
    from framework.search import index_object
    with store.locked() as token:
        bucket_record = store.get('buckets/' + bucket)
        if not bucket_record:
            raise HTTPException(404, 'Bucket not found')
        policy = bucket_record['policy']
        hashes, chunks = [], {}
        size = 0
        checksum = hashlib.sha256()
        while block := incoming.read(CHUNK_SIZE):
            size += len(block)
            checksum.update(block)
            identity = policy_id(policy) + '-' + sha(block)
            chunk = write_chunk(block, policy, chunks.get(identity))
            hashes.append(identity)
            chunks[identity] = chunk
        oid = object_id(bucket, key)
        version = store.get('versions/' + oid, 0) + 1
        previous = store.get('objects/' + oid)
        record = {'bucket': bucket, 'key': key, 'version': version, 'size': size,
                  'created': time.time(), 'chunks': hashes, 'tags': tags or {},
                  'content_type': content_type, 'sha256': checksum.hexdigest(),
                  'mode': policy['mode'], 'policy': policy}
        # Every version remains addressable until explicitly deleted.
        counts = Counter(hashes)
        operations = []
        for identity, chunk in chunks.items():
            chunk['refs'] += counts[identity]
            operations.append(put_op('chunks/' + identity, chunk))
        operations += [put_op('versions/' + oid, version), put_op('objects/' + oid, record),
                       put_op(f'history/{oid}/{version:012d}', record)]
        store.transaction(operations, token)
    index_object(record)
    return record


def get_object(bucket, key, version=None):
    oid = object_id(bucket, key)
    record = store.get('objects/' + oid) if version is None else store.get(f'history/{oid}/{version:012d}')
    if not record:
        raise HTTPException(404, 'Object not found')
    return record


def delete_object(bucket, key, version=None):
    from framework.jobs import enqueue
    from framework.search import delete_index, index_object
    with store.locked() as token:
        record = get_object(bucket, key, version)
        oid = object_id(bucket, key)
        operations = [delete_op(f'history/{oid}/{record["version"]:012d}')]
        for identity, count in Counter(record['chunks']).items():
            chunk = store.get('chunks/' + identity)
            chunk['refs'] -= count
            operations.append(put_op('chunks/' + identity, chunk))
        remaining = sorted((r for r in store.list('history/' + oid + '/').values()
                            if r['version'] != record['version']), key=lambda r: r['version'])
        latest = remaining[-1] if remaining else None
        operations.append(put_op('objects/' + oid, latest) if latest else delete_op('objects/' + oid))
        store.transaction(operations, token)
    index_object(latest) if latest else delete_index(bucket, key)
    enqueue('gc', unique='periodic')


def object_health(record, chunks, alive):
    degraded = False
    unavailable = False
    for identity in record['chunks']:
        chunk = chunks.get(identity)
        if not chunk:
            return 'unavailable'
        count = sum(location['node'] in alive for location in chunk['locations'])
        policy = chunk['policy']
        needed = policy['r'] if policy['mode'] == 'replication' else policy['k']
        unavailable |= count < needed
        degraded |= count < len(chunk['locations']) or bool(chunk.get('corrupt_locations'))
    return 'unavailable' if unavailable else 'degraded' if degraded else 'healthy'


def cluster_status():
    all_nodes = list(store.list('nodes/').values())
    alive = live_nodes()
    chunks = {chunk['id']: chunk for chunk in store.list('chunks/').values() if chunk['refs'] > 0}
    objects = list(store.list('objects/').values())
    counts = Counter(location['node'] for chunk in chunks.values() for location in chunk['locations'])
    for node in all_nodes:
        node['health'] = 'healthy' if node['node_id'] in alive else 'failed'
        node['assigned_fragments'] = counts[node['node_id']]
    health = Counter(object_health(obj, chunks, alive) for obj in objects)
    jobs = sorted(store.list('jobs/').values(), key=lambda j: j['created'], reverse=True)
    return {'nodes': sorted(all_nodes, key=lambda n: n['node_id']), 'node_count': len(all_nodes),
            'healthy_nodes': len(alive), 'objects': len(objects), 'buckets': len(store.list('buckets/')),
            'logical_bytes': sum(o['size'] for o in store.list('history/').values()),
            'physical_bytes': sum(loc['size'] for c in chunks.values() for loc in c['locations']),
            'unique_chunks': len(chunks), 'chunk_references': sum(c['refs'] for c in chunks.values()),
            'degraded_objects': health['degraded'], 'unavailable_objects': health['unavailable'],
            'free_bytes': sum(n.get('free_bytes', 0) for n in all_nodes if n['node_id'] in alive),
            'jobs': jobs[:30], 'metadata_backend': 'etcd (3 members)',
            'maintenance': store.get('settings/maintenance', {'paused': False}),
            'repair_totals': store.get('stats/repair', {'read_bytes': 0, 'write_bytes': 0, 'repaired': 0})}


def recover_data(chunk, excluded):
    """Read only the minimum verified sources; traffic counts actual response bodies."""
    policy = chunk['policy']
    needed = policy['k'] if policy['mode'] == 'erasure' else 1
    valid = []
    read_bytes = 0
    alive = live_nodes()
    for location in chunk['locations']:
        if location['node'] not in alive or (location['node'], location['index']) in excluded:
            continue
        response = node_request('GET', location['node'], location['hash'])
        if response is not None and response.status_code == 200:
            read_bytes += len(response.content)
            if sha(response.content) == location['hash']:
                valid.append((location, response.content))
        if len(valid) == needed:
            break
    if len(valid) < needed:
        raise HTTPException(503, 'Insufficient healthy fragments for recovery')
    data = decode([body for _, body in valid], [loc['index'] for loc, _ in valid],
                  chunk['encoded_size'], policy, chunk['hash'])
    return data, read_bytes


def maintain(kind, payload=None, progress=None):
    payload = payload or {}
    read_bytes = write_bytes = repaired = checked = 0
    with store.locked(timeout=120) as token:
        selected_ids = set(payload.get('chunk_ids', []))
        chunks = [c for c in store.list('chunks/').values() if c['refs'] > 0
                  and (not selected_ids or c['id'] in selected_ids)]
        alive = live_nodes()
        ring = HashRing(alive) if alive else None
        for number, chunk in enumerate(chunks):
            policy = chunk['policy']
            locations = chunk['locations']
            bad = []
            if kind == 'scrub':
                for location in locations:
                    if location['node'] not in alive:
                        bad.append(location)
                        continue
                    response = node_request('GET', location['node'], location['hash'])
                    checked += 1
                    if response is not None and response.status_code == 200:
                        # Verification traffic is separate from recovery source traffic.
                        if sha(response.content) != location['hash']:
                            bad.append(location)
                    else:
                        bad.append(location)
            else:
                bad = [loc for loc in locations if loc['node'] not in alive]
            desired = ring.owners(chunk['id'], len(locations)) if ring and len(alive) >= len(locations) else None
            if kind == 'rebalance' and desired:
                bad = [loc for loc in locations if loc['node'] != desired[loc['index']]]
            if bad:
                excluded = {(loc['node'], loc['index']) for loc in bad}
                # During rebalancing, old replicas remain valid sources.
                data, traffic = recover_data(chunk, set() if kind == 'rebalance' else excluded)
                read_bytes += traffic
                blocks, _ = encode(data, policy)
                occupied = {loc['node'] for loc in locations if loc not in bad}
                remaining_bad = []
                for location in bad:
                    index = location['index']
                    # Leave the old pointer intact until replacement is verified and committed.
                    candidates = [desired[index]] if kind == 'rebalance' and desired else (
                        [location['node']] if location['node'] in alive else [])
                    candidates += [node for node in (ring.owners(chunk['id'], len(alive)) if ring else [])
                                   if node not in occupied and node not in candidates]
                    replaced = False
                    for target in candidates:
                        # Rebalancing may temporarily share a target with an old index;
                        # the full desired map is distinct at completion.
                        if kind != 'rebalance' and target in occupied:
                            continue
                        block = blocks[index]
                        response = node_request('PUT', target, sha(block), block)
                        if response is None or response.status_code != 200:
                            continue
                        new_location = {'index': index, 'node': target, 'hash': sha(block), 'size': len(block)}
                        locations[index] = new_location
                        occupied.add(target)
                        repaired += 1
                        write_bytes += len(block)
                        replaced = True
                        break
                    if not replaced:
                        remaining_bad.append(location)
                chunk['corrupt_locations'] = remaining_bad
                chunk['last_repair'] = time.time()
                store.transaction([put_op('chunks/' + chunk['id'], chunk)], token)
            if kind == 'scrub':
                chunk['last_scrub'] = time.time()
                store.transaction([put_op('chunks/' + chunk['id'], chunk)], token)
            if progress:
                progress(number + 1, len(chunks))
        stats = store.get('stats/repair', {'read_bytes': 0, 'write_bytes': 0, 'repaired': 0})
        for key, value in [('read_bytes', read_bytes), ('write_bytes', write_bytes), ('repaired', repaired)]:
            stats[key] += value
        store.transaction([put_op('stats/repair', stats)], token)
    return {'read_bytes': read_bytes, 'write_bytes': write_bytes, 'repaired': repaired, 'checked': checked}


def garbage_collect(progress=None):
    removed = 0
    with store.locked(timeout=120) as token:
        records = store.list('chunks/')
        # Includes unreferenced upload remnants and obsolete rebalance copies.
        referenced = {(loc['node'], loc['hash']) for chunk in records.values() if chunk['refs'] > 0
                      for loc in chunk['locations']}
        for node, record in live_nodes().items():
            session = requests.Session()
            session.trust_env = False
            try:
                response = session.get(record.get('direct_url', record['url']) + '/inventory',
                                       headers={'X-Internal-Key': INTERNAL_KEY}, timeout=15)
                response.raise_for_status()
                for digest in response.json():
                    if (node, digest) not in referenced:
                        result = node_request('DELETE', node, digest)
                        removed += int(result is not None and result.status_code == 204)
            except requests.RequestException:
                continue
            finally:
                session.close()
        for key, chunk in records.items():
            if chunk['refs'] == 0:
                # Keep tombstones while offline nodes may still contain old bytes.
                if all(loc['node'] in live_nodes() for loc in chunk['locations']):
                    store.transaction([delete_op(key)], token)
    return {'deleted_physical_chunks': removed}
