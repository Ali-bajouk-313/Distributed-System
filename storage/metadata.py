import hashlib
import json
import os
import sqlite3
import threading
import time
import zlib
from pathlib import Path
from urllib.parse import urlsplit

import requests
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel

from storage.ring import HashRing

app = FastAPI(title='Distributed object metadata and placement')
database = Path(os.getenv('DATABASE', './data/metadata.sqlite'))
database.parent.mkdir(parents=True, exist_ok=True)
db = sqlite3.connect(database, check_same_thread=False)
db.row_factory = sqlite3.Row
db.executescript('''
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS objects (
 bucket TEXT, key TEXT, version INTEGER, size INTEGER, created REAL,
 chunks TEXT, PRIMARY KEY(bucket,key,version));
CREATE TABLE IF NOT EXISTS chunks (
 hash TEXT PRIMARY KEY, node TEXT, refs INTEGER, codec TEXT);
CREATE TABLE IF NOT EXISTS nodes (
 node_id TEXT PRIMARY KEY, url TEXT, last_seen REAL);
CREATE TABLE IF NOT EXISTS versions (
 bucket TEXT, key TEXT, last_version INTEGER, PRIMARY KEY(bucket,key));
''')
# Additive migration preserves data from the initial single-copy implementation.
columns = {row['name'] for row in db.execute('PRAGMA table_info(chunks)')}
for name, declaration in [('targets', "TEXT NOT NULL DEFAULT '[]'"),
                           ('w', 'INTEGER NOT NULL DEFAULT 1'),
                           ('r', 'INTEGER NOT NULL DEFAULT 1')]:
    if name not in columns:
        db.execute(f'ALTER TABLE chunks ADD COLUMN {name} {declaration}')
db.execute("UPDATE chunks SET targets=json_array(node) WHERE targets='[]'")
node_columns = {row['name'] for row in db.execute('PRAGMA table_info(nodes)')}
for name in ('free_bytes', 'chunk_count'):
    if name not in node_columns:
        db.execute(f'ALTER TABLE nodes ADD COLUMN {name} INTEGER NOT NULL DEFAULT 0')
db.execute('INSERT OR IGNORE INTO versions SELECT bucket,key,MAX(version) FROM objects GROUP BY bucket,key')
db.commit()
lock = threading.RLock()
nodes = json.loads(os.getenv('STORAGE_NODES', json.dumps({
    f'node{i}': f'http://node{i}:8000' for i in range(1, 5)
})))
ring = HashRing(nodes)
node_addresses = {}
chunk_size = int(os.getenv('CHUNK_SIZE', str(4 * 1024 * 1024)))
replication_factor = int(os.getenv('REPLICATION_FACTOR', '3'))
write_quorum = int(os.getenv('WRITE_QUORUM', str(replication_factor // 2 + 1)))
read_quorum = int(os.getenv('READ_QUORUM', str(replication_factor // 2 + 1)))
if not (1 <= replication_factor <= len(nodes)
        and 1 <= write_quorum <= replication_factor
        and 1 <= read_quorum <= replication_factor
        and write_quorum + read_quorum > replication_factor):
    raise ValueError('Require 1 <= W,R <= N <= node count and W + R > N')
if chunk_size <= 0:
    raise ValueError('CHUNK_SIZE must be positive')


def endpoint(node):
    address = node_addresses.get(node)
    if address:
        parsed = urlsplit(nodes[node])
        return f'{parsed.scheme}://{address}:{parsed.port or 80}'
    return nodes[node]


def remote(method, node, digest, **kwargs):
    try:
        result = requests.request(method, endpoint(node) + '/chunks/' + digest,
                                  timeout=(2, 10), **kwargs)
        result.raise_for_status()
        return result
    except requests.RequestException as error:
        raise HTTPException(503, f'Storage operation failed on {node}') from error


def write_replicas(targets, stored_digest, stored, required):
    acknowledgments = 0
    for node in targets:
        try:
            remote('PUT', node, stored_digest, data=stored)
            acknowledgments += 1
        except HTTPException:
            pass
    if acknowledgments < required:
        raise HTTPException(503, f'Write quorum failed: {acknowledgments}/{required}')
    return acknowledgments


def read_replicas(chunk, require_quorum=True):
    targets = json.loads(chunk['targets'])
    stored_digest = chunk['codec'].split(':')[1]
    responses = 0
    valid = None
    damaged = []
    for node in targets:
        try:
            response = requests.get(endpoint(node) + '/chunks/' + stored_digest, timeout=(2, 10))
            if response.status_code == 404:
                responses += 1
                damaged.append(node)
            elif response.status_code == 200 and hashlib.sha256(response.content).hexdigest() == stored_digest:
                responses += 1
                valid = response.content
            else:
                damaged.append(node)
        except requests.RequestException:
            damaged.append(node)
    if (require_quorum and responses < chunk['r']) or valid is None:
        raise HTTPException(503, f'Read quorum/content unavailable: {responses}/{chunk["r"]}')
    return valid, damaged


@app.get('/config')
def configuration():
    return {'replication_factor': replication_factor, 'write_quorum': write_quorum,
            'read_quorum': read_quorum, 'chunk_size': chunk_size, 'virtual_nodes': 128}


@app.post('/repair')
def repair():
    repaired = 0
    unavailable = 0
    with lock:
        for chunk in db.execute('SELECT * FROM chunks WHERE refs>0').fetchall():
            try:
                stored, damaged = read_replicas(chunk, require_quorum=False)
            except HTTPException:
                unavailable += 1
                continue
            for node in damaged:
                try:
                    remote('PUT', node, chunk['codec'].split(':')[1], data=stored)
                    repaired += 1
                except HTTPException:
                    unavailable += 1
    return {'repaired_replicas': repaired, 'unavailable_replicas_or_chunks': unavailable}


def recovery_loop():
    while True:
        time.sleep(10)
        try:
            repair()
        except Exception:
            import logging
            logging.exception('Replica recovery cycle failed')


@app.on_event('startup')
def start_recovery():
    threading.Thread(target=recovery_loop, daemon=True).start()


class Heartbeat(BaseModel):
    node_id: str
    url: str
    free_bytes: int = 0
    chunk_count: int = 0


@app.post('/nodes/heartbeat')
def heartbeat(message: Heartbeat, request: Request):
    if message.node_id not in nodes or nodes[message.node_id] != message.url:
        raise HTTPException(400, 'Unknown node or unexpected URL')
    # Cache the container's observed address. Docker DNS can take a long time to
    # resolve a stopped service; connecting to its last address has a bounded timeout.
    if request.client:
        node_addresses[message.node_id] = request.client.host
    with lock, db:
        db.execute('INSERT OR REPLACE INTO nodes VALUES (?,?,?,?,?)',
                   (message.node_id, message.url, time.time(), message.free_bytes, message.chunk_count))
    return {'status': 'ok'}


@app.get('/nodes')
def list_nodes():
    with lock:
        seen = {r['node_id']: dict(r) for r in db.execute('SELECT * FROM nodes')}
    return [{'node_id': node, 'url': url,
             'last_seen': seen.get(node, {}).get('last_seen'),
             'free_bytes': seen.get(node, {}).get('free_bytes', 0),
             'chunk_count': seen.get(node, {}).get('chunk_count', 0),
             'alive': time.time() - seen.get(node, {}).get('last_seen', 0) < 15}
            for node, url in nodes.items()]


@app.get('/health')
def health():
    return {'status': 'ok', 'configured_nodes': len(nodes)}


@app.put('/objects/{bucket}/{key:path}')
async def upload(bucket: str, key: str, request: Request, compress: bool = False):
    # Spool the request to disk so large uploads do not occupy RAM in full.
    import tempfile
    with tempfile.TemporaryFile() as incoming:
        async for block in request.stream():
            incoming.write(block)
        size = incoming.tell()
        incoming.seek(0)
        with lock:
            hashes = []
            try:
                while True:
                    block = incoming.read(chunk_size)
                    if not block:
                        break
                    digest = hashlib.sha256(block).hexdigest()
                    hashes.append(digest)
                    row = db.execute('SELECT * FROM chunks WHERE hash=?', (digest,)).fetchone()
                    if row is None:
                        codec = 'zlib' if compress else 'raw'
                        stored = zlib.compress(block) if compress else block
                        # Address the original content; node validates the actual stored bytes.
                        stored_digest = hashlib.sha256(stored).hexdigest()
                        targets = ring.owners(digest, replication_factor)
                        write_replicas(targets, stored_digest, stored, write_quorum)
                        db.execute('INSERT INTO chunks (hash,node,refs,codec,targets,w,r) VALUES (?,?,0,?,?,?,?)',
                                   (digest, targets[0], codec + ':' + stored_digest,
                                    json.dumps(targets), write_quorum, read_quorum))
                    else:
                        codec, stored_digest = row['codec'].split(':')
                        stored = zlib.compress(block) if codec == 'zlib' else block
                        write_replicas(json.loads(row['targets']), stored_digest, stored, row['w'])
                    db.execute('UPDATE chunks SET refs=refs+1 WHERE hash=?', (digest,))
                db.execute('INSERT OR IGNORE INTO versions VALUES (?,?,0)', (bucket, key))
                db.execute('UPDATE versions SET last_version=last_version+1 WHERE bucket=? AND key=?', (bucket, key))
                version = db.execute('SELECT last_version FROM versions WHERE bucket=? AND key=?', (bucket, key)).fetchone()[0]
                db.execute('INSERT INTO objects VALUES (?,?,?,?,?,?)',
                           (bucket, key, version, size, time.time(), json.dumps(hashes)))
                db.commit()
            except Exception:
                db.rollback()
                raise
    return {'bucket': bucket, 'key': key, 'version': version, 'size': size, 'chunks': hashes}


def object_row(bucket, key, version):
    if version is None:
        row = db.execute('SELECT * FROM objects WHERE bucket=? AND key=? ORDER BY version DESC LIMIT 1', (bucket, key)).fetchone()
    else:
        row = db.execute('SELECT * FROM objects WHERE bucket=? AND key=? AND version=?', (bucket, key, version)).fetchone()
    if row is None:
        raise HTTPException(404, 'Object version not found')
    return row


@app.get('/objects/{bucket}/{key:path}')
def download(bucket: str, key: str, version: int | None = None):
    from starlette.responses import StreamingResponse
    import tempfile
    output = tempfile.TemporaryFile()
    try:
        with lock:
            row = object_row(bucket, key, version)
            for digest in json.loads(row['chunks']):
                chunk = db.execute('SELECT * FROM chunks WHERE hash=?', (digest,)).fetchone()
                codec, stored_digest = chunk['codec'].split(':')
                stored, _ = read_replicas(chunk)
                try:
                    body = zlib.decompress(stored) if codec == 'zlib' else stored
                except zlib.error as error:
                    raise HTTPException(409, 'Compressed chunk corrupt') from error
                if hashlib.sha256(body).hexdigest() != digest:
                    raise HTTPException(409, 'Object integrity check failed')
                output.write(body)
        output.seek(0)
    except Exception:
        output.close()
        raise
    def stream():
        try:
            while block := output.read(chunk_size):
                yield block
        finally:
            output.close()
    return StreamingResponse(stream(), media_type='application/octet-stream')


@app.get('/buckets/{bucket}/objects')
def list_objects(bucket: str, search: str = ''):
    with lock:
        rows = db.execute('SELECT bucket,key,version,size,created FROM objects WHERE bucket=? AND instr(key,?)>0 ORDER BY key,version', (bucket, search)).fetchall()
    return [dict(row) for row in rows]


@app.delete('/objects/{bucket}/{key:path}', status_code=204)
def delete_object(bucket: str, key: str, version: int | None = None):
    with lock:
        row = object_row(bucket, key, version)
        try:
            for digest in json.loads(row['chunks']):
                db.execute('UPDATE chunks SET refs=refs-1 WHERE hash=?', (digest,))
            db.execute('DELETE FROM objects WHERE bucket=? AND key=? AND version=?', (bucket, key, row['version']))
            db.commit()
        except Exception:
            db.rollback()
            raise
        # Keep zero-reference records until remote deletion succeeds; retry with /gc.
        collect_garbage()
    return Response(status_code=204)


@app.post('/gc')
def collect_garbage():
    removed = 0
    with lock:
        for chunk in db.execute('SELECT * FROM chunks WHERE refs=0').fetchall():
            complete = True
            for node in json.loads(chunk['targets']):
                try:
                    remote('DELETE', node, chunk['codec'].split(':')[1])
                except HTTPException:
                    complete = False
            if not complete:
                continue
            with db:
                db.execute('DELETE FROM chunks WHERE hash=? AND refs=0', (chunk['hash'],))
            removed += 1
    return {'removed': removed}


@app.get('/stats')
def stats():
    with lock:
        return {'objects': db.execute('SELECT COUNT(*) FROM objects').fetchone()[0],
                'unique_chunks': db.execute('SELECT COUNT(*) FROM chunks WHERE refs>0').fetchone()[0],
                'chunk_references': db.execute('SELECT COALESCE(SUM(refs),0) FROM chunks').fetchone()[0]}
