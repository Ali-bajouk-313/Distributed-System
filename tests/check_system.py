"""Integration checks against the running Compose services; uses an isolated bucket."""
import hashlib
import json
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
from storage.ring import HashRing

BASE = 'http://localhost:8000'


def call(method, path, **kwargs):
    response = requests.request(method, BASE + path, timeout=60, **kwargs)
    response.raise_for_status()
    return response


def run():
    bucket = 'verification-' + uuid.uuid4().hex
    prefix = '/objects/' + bucket + '/'
    before = call('GET', '/stats').json()
    body = b'A' * (4 * 1024 * 1024) + b'B' * 12345
    first = call('PUT', prefix + 'first.bin', data=body).json()
    second = call('PUT', prefix + 'copy.bin', data=body, params={'compress': True}).json()
    assert first['chunks'] == second['chunks']
    after = call('GET', '/stats').json()
    assert after['unique_chunks'] - before['unique_chunks'] <= 2
    assert after['chunk_references'] - before['chunk_references'] == 4
    assert call('GET', prefix + 'first.bin').content == body
    call('DELETE', prefix + 'first.bin')
    assert call('GET', prefix + 'copy.bin').content == body
    call('DELETE', prefix + 'copy.bin')
    # Exercise actual compression using unique content.
    compressed = (uuid.uuid4().hex.encode() + b'compress-me') * 20000
    call('PUT', prefix + 'compressed', data=compressed, params={'compress': True})
    assert call('GET', prefix + 'compressed').content == compressed
    call('PUT', prefix + 'compressed', data=b'version two')
    assert call('GET', prefix + 'compressed', params={'version': 1}).content == compressed
    assert call('GET', prefix + 'compressed').content == b'version two'
    assert len(call('GET', '/buckets/' + bucket + '/objects', params={'search': 'compressed'}).json()) == 2
    call('DELETE', prefix + 'compressed')
    call('DELETE', prefix + 'compressed')
    call('PUT', prefix + 'empty', data=b'')
    assert call('GET', prefix + 'empty').content == b''
    call('DELETE', prefix + 'empty')
    # Check node integrity validation and corruption detection directly.
    direct = 'http://localhost:8001/chunks/'
    digest = hashlib.sha256(b'node-check').hexdigest()
    assert requests.put(direct + digest, data=b'wrong', timeout=10).status_code == 400
    assert requests.get(direct + '../invalid', timeout=10).status_code in (400, 404)
    nodes = call('GET', '/nodes').json()
    assert len(nodes) == 4 and all(node['alive'] for node in nodes), nodes
    keys = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(20000)]
    initial = HashRing(['node1', 'node2', 'node3', 'node4'])
    added = HashRing(['node1', 'node2', 'node3', 'node4', 'node5'])
    removed = HashRing(['node1', 'node2', 'node3'])
    additions = [key for key in keys if initial.owner(key) != added.owner(key)]
    removals = [key for key in keys if initial.owner(key) != removed.owner(key)]
    assert all(added.owner(key) == 'node5' for key in additions)
    assert all(initial.owner(key) == 'node4' for key in removals)
    result = {'integration': 'PASS', 'sample_chunks': len(keys),
              'adding_fifth_node_moved_percent': round(100 * len(additions) / len(keys), 2),
              'removing_fourth_node_moved_percent': round(100 * len(removals) / len(keys), 2),
              'healthy_storage_nodes': len(nodes),
              'checked_at_unix': time.time()}
    Path('docs/verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run()
