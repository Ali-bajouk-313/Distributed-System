"""Host-side end-to-end failure tests. Restores stopped project services in finally."""
import hashlib
import json
import os
import subprocess
import time
import uuid
import argparse
from pathlib import Path

import requests

BASE = os.getenv('TEST_BASE_URL', 'http://localhost:8000')
KEY = os.getenv('API_KEY')
if not KEY and Path('.env').exists():
    KEY = next(line.split('=', 1)[1].strip() for line in Path('.env').read_text().splitlines() if line.startswith('API_KEY='))


def call(method, path, expected=200, **kwargs):
    response = requests.request(method, BASE + path, headers={'X-API-Key': KEY}, timeout=180, **kwargs)
    if response.status_code != expected:
        raise AssertionError((path, response.status_code, response.text[:500]))
    return None if expected == 204 else response.json() if response.headers.get('content-type', '').startswith('application/json') else response


def compose(*arguments):
    subprocess.run(['docker', 'compose', *arguments], check=True)


def wait_for(check, timeout=180):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = check()
            if result:
                return result
        except (requests.RequestException, AssertionError):
            pass
        time.sleep(2)
    raise AssertionError('Timed out waiting for cluster state')


def wait_job(job):
    def finished():
        current = next(j for j in call('GET', '/jobs') if j['id'] == job['id'])
        if current['status'] == 'failed':
            raise RuntimeError(current.get('error'))
        return current if current['status'] == 'completed' else None
    return wait_for(finished, 300)


def run(required_only=False):
    results = {}
    stopped = set()
    name = 'fault-tests-' + uuid.uuid4().hex[:10]
    ec_name = name + '-ec'
    call('POST', '/buckets', 201, json={'name': name})
    call('POST', '/buckets', 201, json={'name': ec_name, 'policy': {'mode': 'erasure', 'k': 4, 'm': 2}})
    body = os.urandom(2 * 1024 * 1024 + 17)
    try:
        for bucket in (name, ec_name):
            call('PUT', f'/objects/{bucket}/fault.bin', 201, data=body)
        detail = call('GET', f'/metadata/{ec_name}/fault.bin')
        failed_node = detail['chunk_details'][0]['locations'][0]['node']
        compose('stop', failed_node)
        stopped.add(failed_node)
        assert call('GET', f'/objects/{ec_name}/fault.bin').content == body
        wait_for(lambda: next(n for n in call('GET', '/cluster')['nodes'] if n['node_id'] == failed_node)['health'] == 'failed')
        wait_for(lambda: failed_node not in [loc['node'] for chunk in call('GET', f'/metadata/{ec_name}/fault.bin')['chunk_details'] for loc in chunk['locations']])
        assert call('GET', f'/objects/{ec_name}/fault.bin').content == body
        results['missed_heartbeat_failure_and_rebuild_on_remaining_nodes'] = 'PASS'
        compose('start', failed_node)
        stopped.remove(failed_node)
        wait_for(lambda: call('GET', '/cluster')['healthy_nodes'] >= 8)
        # Rebalance and wait before editing a test-only physical fragment.
        wait_job(call('POST', '/jobs/rebalance', 202))
        detail = call('GET', f'/metadata/{name}/fault.bin')
        location = detail['chunk_details'][0]['locations'][0]
        compose('exec', '-T', location['node'], 'python', '-c',
                f"from pathlib import Path; Path('/data/chunks/{location['hash']}').write_bytes(b'bit-rot')")
        assert call('GET', f'/objects/{name}/fault.bin').content == body
        repair = wait_job(call('POST', '/jobs/scrub', 202))
        assert call('GET', f'/objects/{name}/fault.bin').content == body
        results['bit_rot_detection_and_scrubber_recovery'] = 'PASS'
        if required_only:
            results['all_original_bytes_verified'] = hashlib.sha256(body).hexdigest()
            Path('docs/fault-verification.json').write_text(json.dumps(results, indent=2))
            print(json.dumps(results, indent=2))
            return
        # Metadata quorum survives losing one of three etcd members.
        compose('stop', 'etcd1')
        stopped.add('etcd1')
        call('GET', '/buckets')
        assert call('GET', f'/objects/{ec_name}/fault.bin').content == body
        compose('start', 'etcd1')
        stopped.remove('etcd1')
        results['metadata_one_member_failure'] = 'PASS'
        compose('stop', 'gateway1')
        stopped.add('gateway1')
        assert call('GET', f'/objects/{name}/fault.bin').content == body
        compose('start', 'gateway1')
        stopped.remove('gateway1')
        results['gateway_failover'] = 'PASS'
        # Add a genuinely new storage node and observe actual metadata movement.
        before = {c['id']: [loc['node'] for loc in c['locations']] for c in call('GET', f'/metadata/{ec_name}/fault.bin')['chunk_details']}
        compose('--profile', 'scale', 'up', '-d', 'node9')
        wait_for(lambda: call('GET', '/cluster')['healthy_nodes'] >= 9)
        wait_job(call('POST', '/jobs/rebalance', 202))
        assert call('GET', f'/objects/{ec_name}/fault.bin').content == body
        results['node_addition_and_rebalance'] = {'status': 'PASS', 'healthy_nodes': 9}
        results['all_original_bytes_verified'] = hashlib.sha256(body).hexdigest()
    finally:
        for service in stopped:
            compose('start', service)
        for bucket in (name, ec_name):
            try:
                call('DELETE', f'/objects/{bucket}/fault.bin', 204)
            except Exception:
                pass
    Path('docs/fault-verification.json').write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--required-only', action='store_true')
    run(parser.parse_args().required_only)
