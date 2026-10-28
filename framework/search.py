import hashlib

import requests
from fastapi import HTTPException

from framework.settings import ELASTIC_URL
from framework.store import store

INDEX = 'storage-objects'


def session():
    client = requests.Session()
    client.trust_env = False
    return client


def document_id(bucket, key):
    return hashlib.sha256((bucket + '\0' + key).encode()).hexdigest()


def ensure_index():
    with session() as client:
        response = client.put(ELASTIC_URL + '/' + INDEX, json={'mappings': {'properties': {
            'bucket': {'type': 'keyword'}, 'key': {'type': 'keyword'}, 'size': {'type': 'long'},
            'created': {'type': 'date', 'format': 'epoch_second'}, 'tags': {'type': 'flattened'},
            'mode': {'type': 'keyword'}, 'version': {'type': 'long'}}}}, timeout=5)
        if response.status_code not in (200, 400):
            response.raise_for_status()
        store.set('settings/search_ready', True)


def index_object(record, deferred=True):
    if record is None:
        return
    if deferred:
        from framework.jobs import enqueue
        enqueue('index', {'bucket': record['bucket'], 'key': record['key']}, unique=document_id(record['bucket'], record['key']))
        return
    try:
        ensure_index()
        with session() as client:
            response = client.put(ELASTIC_URL + '/' + INDEX + '/_doc/' + document_id(record['bucket'], record['key']),
                                  json={k: v for k, v in record.items() if k != 'chunks'}, timeout=5)
            response.raise_for_status()
    except requests.RequestException:
        # Outbox retry is persistent; metadata is the source of truth.
        from framework.jobs import enqueue
        enqueue('index', {'bucket': record['bucket'], 'key': record['key']}, unique=document_id(record['bucket'], record['key']))


def delete_index(bucket, key, deferred=True):
    if deferred:
        from framework.jobs import enqueue
        enqueue('index', {'bucket': bucket, 'key': key}, unique=document_id(bucket, key))
        return
    try:
        with session() as client:
            result = client.delete(ELASTIC_URL + '/' + INDEX + '/_doc/' + document_id(bucket, key), timeout=5)
            if result.status_code not in (200, 404):
                result.raise_for_status()
    except requests.RequestException:
        from framework.jobs import enqueue
        enqueue('index', {'bucket': bucket, 'key': key}, unique=document_id(bucket, key))


def search_objects(bucket=None, prefix='', min_size=None, max_size=None, after=None, before=None, tags=None, limit=200):
    filters = []
    if bucket:
        filters.append({'term': {'bucket': bucket}})
    if prefix:
        filters.append({'prefix': {'key': prefix}})
    if min_size is not None or max_size is not None:
        filters.append({'range': {'size': {**({'gte': min_size} if min_size is not None else {}),
                                          **({'lte': max_size} if max_size is not None else {})}}})
    if after is not None or before is not None:
        filters.append({'range': {'created': {**({'gte': after} if after is not None else {}),
                                             **({'lte': before} if before is not None else {})}}})
    for name, value in (tags or {}).items():
        filters.append({'term': {'tags.' + name: value}})
    try:
        with session() as client:
            response = client.post(ELASTIC_URL + '/' + INDEX + '/_search', json={
                'query': {'bool': {'filter': filters}}, 'size': limit, 'sort': [{'key': 'asc'}],
                'track_total_hits': True}, timeout=10)
            response.raise_for_status()
            result = response.json()
            return {'engine': 'elasticsearch', 'total': result['hits']['total']['value'],
                    'objects': [hit['_source'] for hit in result['hits']['hits']]}
    except requests.RequestException as error:
        raise HTTPException(503, 'Elasticsearch unavailable; retry after search indexing recovers') from error
