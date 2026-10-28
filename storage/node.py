import hashlib
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Request, Response

app = FastAPI(title='Content-addressed storage node')
root = Path(os.getenv('DATA_DIR', './data/chunks'))
root.mkdir(parents=True, exist_ok=True)
node_id = os.getenv('NODE_ID', 'node1')
lock = threading.RLock()


def chunk_path(digest):
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise HTTPException(400, 'Expected a lowercase SHA-256 digest')
    return root / digest


@app.get('/health')
def health():
    return {'status': 'ok', 'node_id': node_id}


@app.put('/chunks/{digest}')
async def put_chunk(digest: str, request: Request):
    path = chunk_path(digest)
    body = await request.body()
    if hashlib.sha256(body).hexdigest() != digest:
        raise HTTPException(400, 'Chunk hash mismatch')
    with lock:
        temporary = root / ('.' + uuid.uuid4().hex)
        try:
            temporary.write_bytes(body)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'hash': digest, 'bytes': len(body)}


@app.get('/chunks/{digest}')
def get_chunk(digest: str):
    path = chunk_path(digest)
    with lock:
        if not path.exists():
            raise HTTPException(404, 'Chunk not found')
        body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != digest:
        raise HTTPException(409, 'Stored chunk is corrupt')
    return Response(body, media_type='application/octet-stream')


@app.delete('/chunks/{digest}', status_code=204)
def delete_chunk(digest: str):
    with lock:
        chunk_path(digest).unlink(missing_ok=True)
    return Response(status_code=204)


def heartbeat():
    coordinator = os.getenv('METADATA_URL', 'http://localhost:8000')
    while True:
        try:
            requests.post(coordinator + '/nodes/heartbeat', json={
                'node_id': node_id,
                'url': os.getenv('NODE_URL', f'http://{node_id}:8000'),
                'free_bytes': shutil.disk_usage(root).free,
                'chunk_count': sum(1 for path in root.iterdir() if re.fullmatch(r'[0-9a-f]{64}', path.name)),
            }, timeout=3).raise_for_status()
        except requests.RequestException:
            pass
        time.sleep(5)


@app.on_event('startup')
def start_heartbeat():
    threading.Thread(target=heartbeat, daemon=True).start()
