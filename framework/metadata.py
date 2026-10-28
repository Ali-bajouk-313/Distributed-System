import logging
import secrets
import threading
import time
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from prometheus_client import Gauge, Counter, CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field

from framework.jobs import dispatch_outbox, enqueue
from framework.settings import FAILURE_TIMEOUT, INTERNAL_KEY, SCRUB_INTERVAL
from framework.store import store

app = FastAPI(title='Replicated metadata control plane', version='2.0')
DEGRADED = Gauge('cluster_degraded_objects', 'Degraded objects')
UNAVAILABLE = Gauge('cluster_unavailable_objects', 'Unavailable objects')
NODES = Gauge('cluster_healthy_nodes', 'Healthy nodes')
REPAIRED = Counter('cluster_repaired_fragments_total', 'Repaired fragments')
REPAIR_READ = Counter('cluster_repair_read_bytes_total', 'Recovery source bytes')
REPAIR_WRITE = Counter('cluster_repair_write_bytes_total', 'Recovery target bytes')
_observed = {'repaired': 0, 'read_bytes': 0, 'write_bytes': 0}


def internal(x_internal_key: str = Header(default='')):
    if not INTERNAL_KEY or not secrets.compare_digest(x_internal_key, INTERNAL_KEY):
        raise HTTPException(401, 'Invalid internal key')


class Heartbeat(BaseModel):
    node_id: str = Field(pattern=r'^[a-zA-Z0-9-]{1,80}$')
    url: str
    free_bytes: int = Field(ge=0)
    chunk_count: int = Field(ge=0)
    stored_bytes: int = Field(default=0, ge=0)


@app.get('/health')
def health():
    store.get('settings/ready')
    return {'status': 'ok', 'backend': 'etcd', 'members': 3}


@app.get('/metrics')
def metrics():
    from framework.engine import cluster_status
    status = cluster_status()
    DEGRADED.set(status['degraded_objects'])
    UNAVAILABLE.set(status['unavailable_objects'])
    NODES.set(status['healthy_nodes'])
    for key, metric in [('repaired', REPAIRED), ('read_bytes', REPAIR_READ), ('write_bytes', REPAIR_WRITE)]:
        value = status['repair_totals'][key]
        metric.inc(max(0, value - _observed[key]))
        _observed[key] = value
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post('/nodes/heartbeat', dependencies=[Depends(internal)])
def heartbeat(message: Heartbeat, request: Request):
    old = store.get('nodes/' + message.node_id)
    node = message.model_dump()
    parsed = urlsplit(message.url)
    if parsed.scheme != 'http' or parsed.port != 8000 or not parsed.hostname:
        raise HTTPException(400, 'Unexpected node URL')
    address = request.headers.get('x-real-ip') or request.client.host
    node.update(last_seen=time.time(), failed=False,
                direct_url=f'http://{address}:8000')
    store.set('nodes/' + message.node_id, node)
    if (old is None or old.get('failed')) and not store.get('settings/maintenance', {'paused': False})['paused']:
        enqueue('rebalance', unique='membership')
    return {'status': 'ok'}


def scheduler():
    while True:
        try:
            # Both control-plane instances compete for the same fenced lease.
            with store.locked('scheduler', timeout=1):
                settings = store.get('settings/maintenance', {'paused': False})
                for node in store.list('nodes/').values():
                    if not node.get('failed') and time.time() - node['last_seen'] > FAILURE_TIMEOUT:
                        node['failed'] = True
                        store.set('nodes/' + node['node_id'], node)
                        if not settings['paused']:
                            enqueue('repair', {'failed_node': node['node_id']}, unique=node['node_id'])
                if not settings['paused']:
                    last = store.get('settings/last_scrub', 0)
                    if time.time() - last > SCRUB_INTERVAL:
                        enqueue('scrub', unique='periodic')
                        store.set('settings/last_scrub', time.time())
                    last_gc = store.get('settings/last_gc', 0)
                    if time.time() - last_gc > 120:
                        enqueue('gc', unique='periodic')
                        store.set('settings/last_gc', time.time())
                dispatch_outbox()
        except Exception:
            logging.exception('Control-plane scheduling cycle failed; retrying')
        time.sleep(3)


def search_readiness_loop():
    from framework.search import ensure_index
    while True:
        try:
            ensure_index()
        except Exception:
            pass
        time.sleep(15)


@app.on_event('startup')
def start():
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=search_readiness_loop, daemon=True).start()
