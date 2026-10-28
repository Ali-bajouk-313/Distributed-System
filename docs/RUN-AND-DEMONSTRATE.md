# Run and demonstrate tasks 1-6

## Start

Open PowerShell in the project folder. Start Docker Desktop, then run:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
```

Open **http://localhost:8088**. The lab API key is the `API_KEY` value in `.env`.
The supplied local demo value is `lab-api-key-change-me`. Keep `.env` private.
Swagger documentation is at **http://localhost:8000/docs**; click Authorize and
enter the same API key. RabbitMQ management is at http://localhost:15673 with user
`storage` and `RABBIT_PASSWORD` from `.env`. SQL is not required: metadata uses etcd.

If the UI production build is already present, it can also be served directly:

```powershell
node scripts/ui_server.cjs
```

That server serves the real compiled React application and proxies `/api` to port
8000. It must not run at the same time as the UI container on port 8088.

## Required stack only

Parts 7 and 8 are bonus work. Avoid running two Kubernetes clusters, Prometheus
and Grafana while demonstrating the required system on a memory-constrained laptop.

```powershell
docker compose up -d etcd1 etcd2 etcd3 rabbit elasticsearch
docker compose up -d metadata metadata2 gateway1 gateway2 worker1 worker2 loadbalancer node1 node2 node3 node4 node5 node6 node7 node8
```

The setup uses eight independently persisted storage nodes. A 4+2 stripe requires
six distinct nodes; spare nodes allow rebuilding after a failure without putting
two fragments from the same stripe on one node. The current computer's WSL memory
cap was set to 4 GB after memory exhaustion during verification.

## UI demonstration

1. Connect using the API key.
2. Open Buckets. Create `demo-replicated` with N=3, W=2, R=2.
3. Create `demo-erasure` with mode erasure, k=4 and m=2.
4. Open Objects, select a bucket and upload a text, JSON or binary file.
5. Click its filename to inspect SHA-256, version, tags and chunk placements.
6. Download the object. Compare the checksum with the uploaded file.
7. Upload the same bytes under another key: metadata shares the same chunk IDs.
8. Search using a key prefix; use Filters for size, dates and JSON tags.
9. Open Maintenance to view repair, scrub, rebalance and garbage-collection jobs.

Elasticsearch search is eventually consistent. If its image is still downloading
or it is not healthy, ordinary bucket browsing still uses metadata. Search returns
503 until Elasticsearch is ready; it does not return fabricated results.

## API verification

```powershell
docker compose exec -T -e PYTHONPATH=/app gateway1 python -m pytest tests/test_api_v2.py -v --junitxml=/tmp/api-results.xml
docker compose cp gateway1:/tmp/api-results.xml docs/api-results.xml
```

The recorded initial run passed 10 tests, with the Elasticsearch test deselected
while that image downloaded. These covered authentication, bucket validation,
streaming upload, byte ranges, deduplication, versioning, multipart/empty files,
concurrent writes, erasure/compression, all 15 four-of-six decode combinations,
load balancing and shared rate limiting.

## Recovery verification

```powershell
python scripts/verify_cluster.py
```

This script creates unique test objects, stops one project node, waits for heartbeat
failure detection and relocation, restarts the node, corrupts a test-only replica,
and requests a scrub. It also includes the optional metadata/gateway failover and
node-addition checks. Stopped services are restored in a `finally` block.
Do not run the failure test concurrently with benchmark fragment removal.

An earlier run exposed repair-queue starvation during search unavailability and
stale locks after worker interruption. The source now defers search jobs until
search readiness and renews 30-second metadata leases. A final successful rerun
must be recorded before claiming the complete fault suite passed.

## Workloads and comparison

```powershell
docker compose exec -T -e PYTHONPATH=/app gateway1 python scripts/benchmark.py --profile smoke
docker compose cp gateway1:/tmp/benchmark-results.json docs/benchmark-results.json
```

The bounded profile uses 24 files of 1-50 KiB and two 100-MiB objects for each
protection mode. The full profile generates 5,000 small files and ten 100-MiB
objects. Both policies receive reproducible bytes from the same random seed.
One in ten small files duplicates another file to demonstrate deduplication.

```powershell
docker compose exec -T -e PYTHONPATH=/app gateway1 python scripts/benchmark.py --profile full
```

The complete 5,000-file run was interrupted to respect the presentation deadline;
do not present it as complete. The measured small-file comparison completed:

| Metric | Replication | Reed-Solomon 4+2 |
|---|---:|---:|
| Input files | 24 | 24 |
| Logical bytes | 671,780 | 671,780 |
| Unique original bytes | 620,615 | 620,615 |
| Encoded stored bytes | 1,861,845 | 930,966 |
| Coding overhead | 3.0000x | 1.5001x |
| Effective overhead after deduplication | 2.7715x | 1.3858x |
| Recovery source bytes | 620,615 | 620,644 |
| Recovery target bytes | 620,615 | 155,161 |
| Rebuilt fragments | 22 | 22 |

Recovery traffic counts actual body bytes read/written for one missing fragment
per unique chunk. It excludes scrub-verification reads and HTTP headers. Replication
copies a full original chunk. Erasure recovery reads four quarter-size fragments
and writes one quarter-size replacement. Padding explains the small discrepancy.
The first large-object run uploaded and verified two 100-MiB replicated objects,
but its repair measurement was interrupted by a concurrent node-failure test.

## Evidence

`docs/api-results.xml` contains the real API test output. Screenshots are under
`docs/screenshots/`. Source, Dockerfiles, compose configuration, Kubernetes
templates, workflows, generators and benchmark code are included in the project.
Passwords and internal secrets are intentionally excluded from the report and Git.

## Bonus deployment

The portable manifests are in `deploy/k8s/stack.yaml`. They define storage and etcd
StatefulSets, persistent volumes, probes, two gateways, two workers and an HPA.
`.github/workflows/kubernetes.yml` deploys through a connected GitHub runner and
requires the documented cluster credentials. GitHub itself does not host this
Kubernetes cluster. The local `atlas` kind node was verified Ready, but a complete
application deployment and a GitHub Actions deployment are not yet verified.
