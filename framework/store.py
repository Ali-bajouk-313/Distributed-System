"""etcd v3 JSON API, failover, leases and fenced distributed metadata transactions."""
import base64
import contextlib
import json
import random
import threading
import time
import uuid

import requests
from fastapi import HTTPException

from framework.settings import ETCD_ENDPOINTS


def b64(value):
    return base64.b64encode(value.encode() if isinstance(value, str) else value).decode()


def key_path(key):
    return '/distributed/v2/' + key


def put_op(key, value, lease=None):
    payload = {'key': b64(key_path(key)), 'value': b64(json.dumps(value, separators=(',', ':')))}
    if lease:
        payload['lease'] = str(lease)
    return {'request_put': payload}


def delete_op(key):
    return {'request_delete_range': {'key': b64(key_path(key))}}


class EtcdStore:
    def __init__(self):
        self.endpoints = list(ETCD_ENDPOINTS)
        self.local = threading.local()

    def call(self, method, payload):
        session = getattr(self.local, 'session', None)
        if session is None:
            session = requests.Session()
            session.trust_env = False
            self.local.session = session
        for endpoint in self.endpoints:
            try:
                response = session.post(endpoint + '/v3/' + method, json=payload, timeout=(1, 8))
                response.raise_for_status()
                result = response.json()
                if 'error' in result:
                    continue
                return result
            except (requests.RequestException, ValueError):
                continue
        raise HTTPException(503, 'Metadata quorum unavailable')

    def get(self, key, default=None):
        result = self.call('kv/range', {'key': b64(key_path(key))})
        rows = result.get('kvs', [])
        return json.loads(base64.b64decode(rows[0]['value'])) if rows else default

    def list(self, prefix):
        start = key_path(prefix)
        end = start[:-1] + chr(ord(start[-1]) + 1)
        result = self.call('kv/range', {'key': b64(start), 'range_end': b64(end)})
        return {base64.b64decode(row['key']).decode().removeprefix(key_path('')):
                json.loads(base64.b64decode(row['value'])) for row in result.get('kvs', [])}

    def set(self, key, value, lease=None):
        self.call('kv/put', put_op(key, value, lease)['request_put'])

    def delete(self, key):
        self.call('kv/deleterange', {'key': b64(key_path(key))})

    def transaction(self, operations, token=None, lock_name='metadata'):
        compare = []
        if token:
            compare = [{'key': b64(key_path('locks/' + lock_name)), 'target': 'VALUE',
                        'result': 'EQUAL', 'value': b64(json.dumps(token))}]
        result = self.call('kv/txn', {'compare': compare, 'success': operations, 'failure': []})
        if not result.get('succeeded'):
            raise HTTPException(409, 'Metadata transaction lost its lock; retry operation')

    @contextlib.contextmanager
    def locked(self, name='metadata', timeout=60):
        token = uuid.uuid4().hex
        lease = self.call('lease/grant', {'TTL': 30})['ID']
        stop_renewal = threading.Event()
        def renew():
            while not stop_renewal.wait(8):
                for endpoint in self.endpoints:
                    try:
                        # Close after one streamed gRPC-gateway response.
                        with requests.post(endpoint + '/v3/lease/keepalive', json={'ID': lease},
                                           timeout=(1, 4), stream=True) as response:
                            response.raise_for_status()
                            next(response.iter_lines())
                        break
                    except (requests.RequestException, StopIteration):
                        continue
        renewal = None
        deadline = time.monotonic() + timeout
        acquired = False
        try:
            while time.monotonic() < deadline:
                result = self.call('kv/txn', {
                    'compare': [{'key': b64(key_path('locks/' + name)), 'target': 'CREATE',
                                 'result': 'EQUAL', 'create_revision': '0'}],
                    'success': [put_op('locks/' + name, token, lease)], 'failure': []})
                if result.get('succeeded'):
                    acquired = True
                    renewal = threading.Thread(target=renew, daemon=True)
                    renewal.start()
                    yield token
                    return
                time.sleep(random.uniform(0.03, 0.09))
            raise HTTPException(503, 'Metadata busy; retry request')
        finally:
            stop_renewal.set()
            if acquired:
                self.call('kv/txn', {
                    'compare': [{'key': b64(key_path('locks/' + name)), 'target': 'VALUE',
                                 'result': 'EQUAL', 'value': b64(json.dumps(token))}],
                    'success': [delete_op('locks/' + name)], 'failure': []})
            try:
                self.call('lease/revoke', {'ID': lease})
            except HTTPException:
                pass


store = EtcdStore()
