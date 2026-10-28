"""Destructive only to this script's unique test chunk; temporarily stops project nodes."""
import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path

import requests

BASE = 'http://localhost:8000'


def compose(*args):
    subprocess.run(['docker', 'compose', *args], check=True)


def wait_node(port):
    for _ in range(30):
        try:
            if requests.get(f'http://localhost:{port}/health', timeout=2).ok:
                return
        except requests.RequestException:
            pass
        time.sleep(1)
    raise RuntimeError('Node did not recover')


def run():
    config = requests.get(BASE + '/config', timeout=10).json()
    assert config['replication_factor'] == 3 and config['write_quorum'] == config['read_quorum'] == 2
    body = ('quorum-test-' + uuid.uuid4().hex).encode()
    digest = hashlib.sha256(body).hexdigest()
    path = '/objects/recovery-check/' + uuid.uuid4().hex
    response = requests.put(BASE + path, data=body, timeout=60)
    response.raise_for_status()
    owners = []
    for number in range(1, 5):
        if requests.get(f'http://localhost:{8000 + number}/chunks/{digest}', timeout=5).status_code == 200:
            owners.append(number)
    assert len(owners) == 3, owners
    first, second = owners[:2]
    stopped = []
    try:
        compose('stop', f'node{first}')
        stopped.append(first)
        result = requests.get(BASE + path, timeout=60)
        assert result.status_code == 200 and result.content == body
        result = requests.put(BASE + path, data=body, timeout=60)
        assert result.status_code == 200, result.text
        compose('stop', f'node{second}')
        stopped.append(second)
        assert requests.get(BASE + path, timeout=60).status_code == 503
        assert requests.put(BASE + path, data=body, timeout=60).status_code == 503
    finally:
        for number in stopped:
            compose('start', f'node{number}')
            wait_node(8000 + number)
    # Simulate lost disk chunk, then restore it from the remaining replicas.
    requests.delete(f'http://localhost:{8000 + first}/chunks/{digest}', timeout=5).raise_for_status()
    repair = requests.post(BASE + '/repair', timeout=60)
    repair.raise_for_status()
    repaired = requests.get(f'http://localhost:{8000 + first}/chunks/{digest}', timeout=5)
    assert repaired.content == body
    # Corrupt this test's replica on disk; node must detect it, repair must replace it.
    compose('exec', '-T', f'node{first}', 'python', '-c',
            f"from pathlib import Path; Path('/data/chunks/{digest}').write_bytes(b'corrupt')")
    assert requests.get(f'http://localhost:{8000 + first}/chunks/{digest}', timeout=5).status_code == 409
    assert requests.get(BASE + path, timeout=60).content == body
    requests.post(BASE + '/repair', timeout=60).raise_for_status()
    assert requests.get(f'http://localhost:{8000 + first}/chunks/{digest}', timeout=5).content == body
    for _ in range(2):
        requests.delete(BASE + path, timeout=60).raise_for_status()
    result = {'replica_count': 3, 'one_node_down_read_write': 'PASS',
              'two_replicas_down_quorums_rejected': 'PASS',
              'lost_chunk_recovery': 'PASS', 'corrupt_replica_recovery': 'PASS'}
    Path('docs/recovery-verification.json').write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    run()
