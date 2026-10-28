import json
from pathlib import Path

queries = [
    ('Request throughput', 'sum(rate(gateway_requests_total[1m]))', 'reqps'),
    ('P95 gateway latency', 'histogram_quantile(0.95, sum by (le) (rate(gateway_request_seconds_bucket[1m])))', 's'),
    ('Healthy storage nodes', 'cluster_healthy_nodes', 'short'),
    ('Degraded objects', 'cluster_degraded_objects', 'short'),
    ('Storage bytes per node', 'storage_node_stored_bytes', 'bytes'),
    ('Chunks per node', 'storage_node_chunks', 'short'),
    ('Free disk per node', 'storage_node_disk_free_bytes', 'bytes'),
    ('Repair rate', 'rate(cluster_repaired_fragments_total[2m])', 'ops'),
    ('Recovery traffic', 'rate(cluster_repair_read_bytes_total[2m]) + rate(cluster_repair_write_bytes_total[2m])', 'Bps'),
    ('Unavailable objects', 'cluster_unavailable_objects', 'short')]
panels = []
for i, (title, expression, unit) in enumerate(queries):
    panels.append({'id': i + 1, 'title': title, 'type': 'timeseries',
        'gridPos': {'x': (i % 2) * 12, 'y': (i // 2) * 8, 'w': 12, 'h': 8},
        'datasource': {'type': 'prometheus', 'uid': 'prometheus'},
        'targets': [{'expr': expression, 'legendFormat': '{{node}}{{instance}}', 'refId': 'A'}],
        'fieldConfig': {'defaults': {'unit': unit}, 'overrides': []}})
dashboard = {'uid': 'atlas-storage', 'title': 'Atlas Distributed Storage', 'schemaVersion': 39,
             'version': 1, 'refresh': '5s', 'time': {'from': 'now-30m', 'to': 'now'},
             'panels': panels, 'tags': ['distributed-systems', 'storage']}
Path('deploy/grafana/dashboards/storage.json').write_text(json.dumps(dashboard, indent=2))
