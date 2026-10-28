import base64
import hashlib
import json
import os
import re
import secrets
import tempfile
import time
from datetime import datetime
from typing import Literal
from urllib.parse import quote

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest
from pydantic import BaseModel, Field, model_validator
from starlette.responses import StreamingResponse

from framework import engine
from framework.jobs import enqueue
from framework.settings import API_KEY, CHUNK_SIZE, RATE_LIMIT
from framework.store import b64, key_path, store

app = FastAPI(title='Distributed Object Storage Gateway', version='2.0',
              description='Authenticated object access, erasure coding, recovery and cluster management.')
key_scheme = APIKeyHeader(name='X-API-Key', auto_error=False)
REQUESTS = Counter('gateway_requests_total', 'Requests', ['method', 'route', 'status'])
LATENCY = Histogram('gateway_request_seconds', 'Request latency', ['method', 'route'])
TRANSFER = Counter('gateway_transfer_bytes_total', 'Logical bytes transferred', ['direction'])


@app.middleware('http')
async def instrumentation(request, call_next):
    start = time.monotonic()
    response = await call_next(request)
    route = getattr(request.scope.get('route'), 'path', 'unmatched')
    REQUESTS.labels(request.method, route, response.status_code).inc()
    LATENCY.labels(request.method, route).observe(time.monotonic() - start)
    response.headers['X-Gateway-Instance'] = os.getenv('HOSTNAME', 'gateway')
    return response


def authenticate(request: Request, api_key: str | None = Depends(key_scheme)):
    if not API_KEY or not api_key or not secrets.compare_digest(api_key, API_KEY):
        raise HTTPException(401, 'Missing or invalid API key')
    if request.url.path == '/admin/rate-limit':
        return hashlib.sha256(api_key.encode()).hexdigest()[:16]
    minute = int(time.time() // 60)
    identity = hashlib.sha256(api_key.encode()).hexdigest()[:16]
    key = f'rate/{identity}/{minute}'
    limit = store.get('settings/rate_limit', RATE_LIMIT)
    for _ in range(12):
        raw = store.call('kv/range', {'key': b64(key_path(key))}).get('kvs', [])
        count = int(json.loads(base64.b64decode(raw[0]['value']))) if raw else 0
        if count >= limit:
            raise HTTPException(429, 'Rate limit exceeded', headers={'Retry-After': str(60 - int(time.time()) % 60)})
        compare = {'key': b64(key_path(key)), 'target': 'MOD', 'result': 'EQUAL',
                   'mod_revision': raw[0]['mod_revision']} if raw else {
                       'key': b64(key_path(key)), 'target': 'CREATE', 'result': 'EQUAL', 'create_revision': '0'}
        lease = raw[0].get('lease') if raw else store.call('lease/grant', {'TTL': 120})['ID']
        result = store.call('kv/txn', {'compare': [compare], 'success': [{'request_put': {
            'key': b64(key_path(key)), 'value': b64(json.dumps(count + 1)), 'lease': str(lease)}}], 'failure': []})
        if result.get('succeeded'):
            return identity
    raise HTTPException(503, 'Rate limiter busy; retry')


AUTH = [Depends(authenticate)]


class BucketPolicy(BaseModel):
    mode: Literal['replication', 'erasure'] = 'replication'
    n: int = Field(default=3, ge=1, le=32)
    w: int = Field(default=2, ge=1)
    r: int = Field(default=2, ge=1)
    k: int = Field(default=4, ge=1, le=32)
    m: int = Field(default=2, ge=1, le=32)
    compress: bool = False

    @model_validator(mode='after')
    def quorum(self):
        if self.mode == 'replication' and (max(self.w, self.r) > self.n or self.w + self.r <= self.n):
            raise ValueError('Replication requires 1 <= W,R <= N and W+R>N')
        if self.k + self.m > 256:
            raise ValueError('At most 256 erasure fragments')
        return self


class BucketCreate(BaseModel):
    name: str = Field(pattern=r'^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$')
    policy: BucketPolicy = Field(default_factory=BucketPolicy)


@app.get('/health')
def health():
    store.get('settings/ready')
    return {'status': 'ok', 'instance': os.getenv('HOSTNAME', 'gateway'), 'metadata': 'etcd'}


@app.get('/metrics')
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post('/buckets', status_code=201, dependencies=AUTH)
def create_bucket(body: BucketCreate):
    with store.locked() as token:
        if store.get('buckets/' + body.name):
            raise HTTPException(409, 'Bucket already exists')
        record = {'name': body.name, 'policy': body.policy.model_dump(), 'created': time.time()}
        from framework.store import put_op
        store.transaction([put_op('buckets/' + body.name, record)], token)
    return record


@app.get('/buckets', dependencies=AUTH)
def list_buckets():
    buckets = list(store.list('buckets/').values())
    objects = list(store.list('objects/').values())
    for bucket in buckets:
        selected = [o for o in objects if o['bucket'] == bucket['name']]
        bucket.update(objects=len(selected), bytes=sum(o['size'] for o in selected))
    return sorted(buckets, key=lambda b: b['name'])


def parse_tags(value):
    try:
        tags = json.loads(value or '{}')
        if not isinstance(tags, dict) or len(tags) > 32 or any(not isinstance(k, str) or not isinstance(v, str) for k, v in tags.items()):
            raise ValueError()
        return tags
    except (ValueError, TypeError):
        raise HTTPException(400, 'Tags must be a JSON object with at most 32 string pairs')


@app.put('/objects/{bucket}/{key:path}', status_code=201, dependencies=AUTH)
async def put_object(bucket: str, key: str, request: Request, x_object_tags: str = Header(default='{}')):
    if len(key.encode()) > 1024:
        raise HTTPException(400, 'Key exceeds 1024 UTF-8 bytes')
    tags = parse_tags(x_object_tags)
    with tempfile.TemporaryFile() as incoming:
        size = 0
        async for block in request.stream():
            size += len(block)
            incoming.write(block)
        incoming.seek(0)
        record = await run_in_threadpool(engine.upload, bucket, key, incoming, tags,
                                        request.headers.get('content-type', 'application/octet-stream'))
    TRANSFER.labels('upload').inc(size)
    return record


@app.post('/uploads/{bucket}', status_code=201, dependencies=AUTH)
def multipart_upload(bucket: str, file: UploadFile, key: str | None = None, tags: str = '{}'):
    record = engine.upload(bucket, key or file.filename or 'upload.bin', file.file,
                           parse_tags(tags), file.content_type or 'application/octet-stream')
    TRANSFER.labels('upload').inc(record['size'])
    return record


def parse_range(header, size):
    if not header:
        return 0, size - 1, False
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or (not match[1] and not match[2]) or size == 0:
        raise HTTPException(416, 'Unsatisfiable byte range', headers={'Content-Range': f'bytes */{size}'})
    if not match[1]:
        suffix = int(match[2])
        if suffix <= 0:
            raise HTTPException(416, 'Invalid suffix range', headers={'Content-Range': f'bytes */{size}'})
        start, end = max(0, size - suffix), size - 1
    else:
        start = int(match[1])
        end = min(int(match[2]) if match[2] else size - 1, size - 1)
    if start >= size or start > end:
        raise HTTPException(416, 'Unsatisfiable byte range', headers={'Content-Range': f'bytes */{size}'})
    return start, end, True


@app.get('/objects/{bucket}/{key:path}', dependencies=AUTH)
def download_object(bucket: str, key: str, version: int | None = None, range: str | None = Header(default=None)):
    # Spool verified output before headers: a missing chunk yields 503, not a
    # misleading successful response truncated halfway through the stream.
    incoming = tempfile.TemporaryFile()
    try:
        with store.locked() as token:
            record = engine.get_object(bucket, key, version)
            start, end, partial = parse_range(range, record['size'])
            offset = 0
            for identity in record['chunks']:
                chunk = store.get('chunks/' + identity)
                if offset + chunk['size'] > start and offset <= end:
                    body, bad, _ = engine.read_shards(chunk)
                    if bad:
                        enqueue('scrub', unique='periodic')
                    incoming.write(body[max(0, start - offset):min(len(body), end - offset + 1)])
                offset += chunk['size']
        length = max(0, end - start + 1)
        incoming.seek(0)
    except Exception:
        incoming.close()
        raise
    def stream():
        try:
            while block := incoming.read(CHUNK_SIZE):
                TRANSFER.labels('download').inc(len(block))
                yield block
        finally:
            incoming.close()
    headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(length),
               'ETag': '"' + record['sha256'] + '"',
               'Content-Disposition': "attachment; filename*=UTF-8''" + quote(key.rsplit('/', 1)[-1], safe='')}
    if partial:
        headers['Content-Range'] = f'bytes {start}-{end}/{record["size"]}'
    return StreamingResponse(stream(), status_code=206 if partial else 200,
                             media_type=record['content_type'], headers=headers)


@app.get('/metadata/{bucket}/{key:path}', dependencies=AUTH)
def object_metadata(bucket: str, key: str, version: int | None = None):
    record = engine.get_object(bucket, key, version)
    chunks = {identity: store.get('chunks/' + identity) for identity in record['chunks']}
    record['health'] = engine.object_health(record, chunks, engine.live_nodes())
    record['chunk_details'] = list(chunks.values())
    return record


@app.delete('/objects/{bucket}/{key:path}', status_code=204, dependencies=AUTH)
def remove_object(bucket: str, key: str, version: int | None = None):
    engine.delete_object(bucket, key, version)
    return Response(status_code=204)


@app.get('/buckets/{bucket}/objects', dependencies=AUTH)
def list_objects(bucket: str, prefix: str = '', versions: bool = False, limit: int = Query(default=200, ge=1, le=10000)):
    if not store.get('buckets/' + bucket):
        raise HTTPException(404, 'Bucket not found')
    records = store.list(('history/' if versions else 'objects/') + bucket + '/')
    selected = sorted([r for r in records.values() if r['key'].startswith(prefix)],
                      key=lambda r: (r['key'], r['version']))
    return {'total': len(selected), 'objects': selected[:limit]}


@app.get('/search', dependencies=AUTH)
def search(bucket: str | None = None, prefix: str = '', min_size: int | None = Query(default=None, ge=0),
           max_size: int | None = Query(default=None, ge=0), after: datetime | None = None,
           before: datetime | None = None, tags: str = '{}', limit: int = Query(default=200, ge=1, le=10000)):
    from framework.search import search_objects
    return search_objects(bucket, prefix, min_size, max_size, after.timestamp() if after else None,
                          before.timestamp() if before else None, parse_tags(tags), limit)


@app.get('/cluster', dependencies=AUTH)
def cluster():
    return engine.cluster_status()


@app.get('/jobs', dependencies=AUTH)
def jobs():
    return sorted(store.list('jobs/').values(), key=lambda j: j['created'], reverse=True)[:100]


@app.get('/benchmarks', dependencies=AUTH)
def benchmarks():
    return store.get('stats/benchmarks', {'runs': []})


@app.post('/jobs/{kind}', status_code=202, dependencies=AUTH)
def create_job(kind: Literal['repair', 'scrub', 'rebalance', 'gc', 'reindex']):
    return enqueue(kind)


class MaintenanceSettings(BaseModel):
    paused: bool


@app.put('/admin/maintenance', dependencies=AUTH)
def maintenance_settings(body: MaintenanceSettings):
    store.set('settings/maintenance', body.model_dump())
    return body


class RateSettings(BaseModel):
    requests_per_minute: int = Field(ge=1, le=1000000)


@app.put('/admin/rate-limit', dependencies=AUTH)
def rate_settings(body: RateSettings):
    store.set('settings/rate_limit', body.requests_per_minute)
    return body
