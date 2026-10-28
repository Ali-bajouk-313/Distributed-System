"""API contract tests against the real load-balanced deployment (not mocked storage)."""
import hashlib
import itertools
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
import requests

from framework.coding import decode, encode, sha

BASE = os.getenv('TEST_BASE_URL', 'http://loadbalancer')
KEY = os.environ['API_KEY']


def call(method, path, expected=200, **kwargs):
    response = requests.request(method, BASE + path, headers={'X-API-Key': KEY, **kwargs.pop('headers', {})},
                                timeout=180, **kwargs)
    assert response.status_code == expected, (response.status_code, response.text[:1000])
    return response


@pytest.fixture(scope='module')
def bucket():
    name = 'pytest-' + uuid.uuid4().hex[:12]
    call('POST', '/buckets', 201, json={'name': name})
    return name


def test_authentication_required():
    assert requests.get(BASE + '/buckets', timeout=10).status_code == 401
    assert requests.get(BASE + '/buckets', headers={'X-API-Key': 'incorrect'}, timeout=10).status_code == 401


def test_bucket_validation_and_listing(bucket):
    call('POST', '/buckets', 409, json={'name': bucket})
    call('POST', '/buckets', 422, json={'name': 'invalid NAME'})
    call('POST', '/buckets', 422, json={'name': 'bad-quorum', 'policy': {'n': 3, 'w': 1, 'r': 1}})
    assert bucket in [b['name'] for b in call('GET', '/buckets').json()]


def test_streaming_round_trip_and_ranges(bucket):
    body = os.urandom(4 * 1024 * 1024 + 1027)
    path = f'/objects/{bucket}/nested/stream.bin'
    record = call('PUT', path, 201, data=body).json()
    assert record['sha256'] == hashlib.sha256(body).hexdigest()
    assert len(record['chunks']) == 2
    assert call('GET', path).content == body
    for header, expected in [('bytes=0-19', body[:20]), ('bytes=-31', body[-31:]),
                             ('bytes=4194290-4194320', body[4194290:4194321]),
                             ('bytes=10-', body[10:])]:
        response = call('GET', path, 206, headers={'Range': header})
        assert response.content == expected
        assert response.headers['Accept-Ranges'] == 'bytes'
        assert response.headers['Content-Range'].startswith('bytes ')
    call('GET', path, 416, headers={'Range': 'bytes=999999999-'})
    call('GET', path, 416, headers={'Range': 'bytes=0-1,3-4'})
    call('DELETE', path, 204)
    call('GET', path, 404)


def test_deduplication_and_versions(bucket):
    body = os.urandom(27000)
    first = call('PUT', f'/objects/{bucket}/same-a', 201, data=body).json()
    copy = call('PUT', f'/objects/{bucket}/same-b', 201, data=body).json()
    assert first['chunks'] == copy['chunks']
    detail = call('GET', f'/metadata/{bucket}/same-b').json()
    assert detail['chunk_details'][0]['refs'] == 2
    call('DELETE', f'/objects/{bucket}/same-a', 204)
    assert call('GET', f'/objects/{bucket}/same-b').content == body
    second = call('PUT', f'/objects/{bucket}/same-b', 201, data=b'new-version').json()
    assert second['version'] == 2
    assert call('GET', f'/objects/{bucket}/same-b?version=1').content == body
    call('DELETE', f'/objects/{bucket}/same-b', 204)
    assert call('GET', f'/objects/{bucket}/same-b').content == body
    call('DELETE', f'/objects/{bucket}/same-b', 204)


def test_multipart_empty_and_metadata(bucket):
    record = call('POST', f'/uploads/{bucket}', 201, files={'file': ('empty.txt', b'', 'text/plain')}).json()
    assert record['size'] == 0
    assert call('GET', f'/objects/{bucket}/empty.txt').content == b''
    assert call('GET', f'/metadata/{bucket}/empty.txt').json()['health'] == 'healthy'
    call('DELETE', f'/objects/{bucket}/empty.txt', 204)


def test_elasticsearch_filters(bucket):
    path = f'/objects/{bucket}/search/example.json'
    call('PUT', path, 201, data=b'{"test":true}', headers={'X-Object-Tags': '{"team":"pytest"}'})
    result = None
    for _ in range(25):
        result = call('GET', '/search', params={'bucket': bucket, 'prefix': 'search/',
            'min_size': 5, 'max_size': 1000, 'after': '2020-01-01T00:00:00Z',
            'tags': '{"team":"pytest"}'}).json()
        if result['total']:
            break
        time.sleep(0.5)
    assert result['engine'] == 'elasticsearch' and result['total'] == 1
    assert result['objects'][0]['key'] == 'search/example.json'
    assert call('GET', f'/buckets/{bucket}/objects?prefix=search/').json()['total'] == 1
    call('DELETE', path, 204)


def test_concurrent_gateway_versions(bucket):
    path = f'/objects/{bucket}/concurrent'
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda n: call('PUT', path, 201, data=f'value-{n}'.encode()).json(), range(4)))
    assert sorted(r['version'] for r in results) == [1, 2, 3, 4]
    for version in range(1, 5):
        call('DELETE', path + f'?version={version}', 204)


def test_erasure_bucket_and_compression():
    name = 'pytest-ec-' + uuid.uuid4().hex[:10]
    call('POST', '/buckets', 201, json={'name': name, 'policy': {'mode': 'erasure', 'k': 4, 'm': 2, 'compress': True}})
    body = (b'erasure-and-compression' * 5000) + os.urandom(123)
    call('PUT', f'/objects/{name}/encoded.bin', 201, data=body)
    detail = call('GET', f'/metadata/{name}/encoded.bin').json()
    locations = detail['chunk_details'][0]['locations']
    assert len(locations) == 6 and len({loc['node'] for loc in locations}) == 6
    assert call('GET', f'/objects/{name}/encoded.bin').content == body
    call('DELETE', f'/objects/{name}/encoded.bin', 204)


def test_erasure_every_four_fragments():
    policy = {'mode': 'erasure', 'k': 4, 'm': 2, 'compress': False}
    body = os.urandom(1048583)
    blocks, length = encode(body, policy)
    for indexes in itertools.combinations(range(6), 4):
        assert decode([blocks[i] for i in indexes], list(indexes), length, policy, sha(body)) == body


def test_load_balancer_and_cluster():
    instances = {call('GET', '/buckets').headers['X-Gateway-Instance'] for _ in range(8)}
    assert len(instances) >= 2
    cluster = call('GET', '/cluster').json()
    assert cluster['healthy_nodes'] >= 8
    assert cluster['metadata_backend'].startswith('etcd')


def test_rate_limit_shared_across_gateways():
    try:
        call('PUT', '/admin/rate-limit', json={'requests_per_minute': 1})
        call('GET', '/buckets', 429)
    finally:
        call('PUT', '/admin/rate-limit', json={'requests_per_minute': 6000})
