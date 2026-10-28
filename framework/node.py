import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Gauge, generate_latest

from framework.settings import INTERNAL_KEY

app = FastAPI(title='Storage node', version='2.0')
root = Path(os.getenv('DATA_DIR', '/data/chunks'))
root.mkdir(parents=True, exist_ok=True)
node_id = os.getenv('NODE_ID', os.getenv('HOSTNAME', 'node1'))
node_url = os.getenv('NODE_URL') or f'http://{node_id}.storage:8000'
lock = threading.RLock()
DISK = Gauge('storage_node_disk_free_bytes', 'Free disk space', ['node'])
CHUNKS = Gauge('storage_node_chunks', 'Physical chunks', ['node'])
BYTES = Gauge('storage_node_stored_bytes', 'Physical stored bytes', ['node'])


def internal(x_internal_key: str = Header(default='')):
    import secrets
    if not INTERNAL_KEY or not secrets.compare_digest(x_internal_key, INTERNAL_KEY):
        raise HTTPException(401, 'Invalid internal key')


def chunk_path(digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise HTTPException(400, 'Expected lowercase SHA-256 hash')
    return root / digest


def usage():
    files = [p for p in root.iterdir() if re.fullmatch(r'[0-9a-f]{64}', p.name)]
    free = shutil.disk_usage(root).free
    total = sum(p.stat().st_size for p in files if p.exists())
    DISK.labels(node_id).set(free)
    CHUNKS.labels(node_id).set(len(files))
    BYTES.labels(node_id).set(total)
    return {'free_bytes': free, 'chunk_count': len(files), 'stored_bytes': total}


@app.get('/health')
def health():
    return {'status': 'ok', 'node_id': node_id}


@app.get('/metrics')
def metrics():
    usage()
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.get('/inventory', dependencies=[Depends(internal)])
def inventory():
    return {p.name: p.stat().st_size for p in root.iterdir()
            if re.fullmatch(r'[0-9a-f]{64}', p.name) and p.exists()}


@app.put('/chunks/{digest}', dependencies=[Depends(internal)])
async def put_chunk(digest: str, request: Request):
    target = chunk_path(digest)
    temporary = root / ('.' + uuid.uuid4().hex)
    hasher = hashlib.sha256()
    size = 0
    try:
        with temporary.open('wb') as output:
            async for block in request.stream():
                size += len(block)
                if size > 8 * 1024 * 1024:
                    raise HTTPException(413, 'Chunk exceeds 8 MiB limit')
                hasher.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if hasher.hexdigest() != digest:
            raise HTTPException(400, 'Chunk hash mismatch')
        with lock:
            os.replace(temporary, target)
        return {'hash': digest, 'bytes': size}
    finally:
        temporary.unlink(missing_ok=True)


@app.get('/chunks/{digest}', dependencies=[Depends(internal)])
def get_chunk(digest: str):
    with lock:
        path = chunk_path(digest)
        if not path.exists():
            raise HTTPException(404, 'Chunk missing')
        body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise HTTPException(409, 'Bit rot detected')
    return Response(body, media_type='application/octet-stream')


@app.delete('/chunks/{digest}', status_code=204, dependencies=[Depends(internal)])
def delete_chunk(digest: str):
    with lock:
        chunk_path(digest).unlink(missing_ok=True)
    return Response(status_code=204)


def heartbeat_loop():
    session = requests.Session()
    session.trust_env = False
    while True:
        try:
            session.post(os.getenv('METADATA_URL', 'http://loadbalancer/internal') + '/nodes/heartbeat',
                         json={'node_id': node_id, 'url': node_url, **usage()},
                         headers={'X-Internal-Key': INTERNAL_KEY}, timeout=4).raise_for_status()
        except (requests.RequestException, OSError):
            pass
        time.sleep(3)


@app.on_event('startup')
def start():
    threading.Thread(target=heartbeat_loop, daemon=True).start()
