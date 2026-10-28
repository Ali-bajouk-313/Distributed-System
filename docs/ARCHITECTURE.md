# Architecture and assignment coverage

## Components

The React console calls a FastAPI gateway through Nginx. Gateways share metadata
in a three-member etcd cluster. Storage nodes own independent persistent volumes
and expose hash-verified PUT, GET and DELETE chunk endpoints. A metadata control
plane receives heartbeats, marks missed-heartbeat nodes failed after 20 seconds,
and creates durable maintenance jobs. RabbitMQ distributes those jobs to workers.
Elasticsearch indexes latest object metadata for prefix, size, date and tag search.

The original parts 1-3 implementation is retained under `storage/` for reference;
the integrated application is under `framework/`. Earlier task-specific notes
describe the earlier SQLite stage and are superseded by this document.

## Write path

```mermaid
flowchart TD
    A[API key and shared rate limit] --> B[Stream request into temporary file]
    B --> C[Read fixed 4 MiB chunk]
    C --> D[SHA-256 of original bytes]
    D --> E{Content and protection policy already known?}
    E -- yes --> F[Reuse chunk identity and verify write policy]
    E -- no --> G[Optional zlib compression]
    G --> H{Bucket policy}
    H -- replication --> I[N copies on distinct ring nodes; require W acknowledgments]
    H -- erasure --> J[zfec Reed-Solomon k data plus m parity fragments]
    I --> K[Hash and persist each fragment]
    J --> K
    F --> L[Commit ordered chunk references and object version]
    K --> L
    L --> M[Durable metadata-index job]
    M --> N[Elasticsearch metadata search]
```

## Read path

```mermaid
flowchart TD
    A[Authenticate and parse optional byte range] --> B[Read object version metadata]
    B --> C[Find intersecting chunks and their recorded locations]
    C --> D{Protection mode}
    D -- replication --> E[Read quorum R; require a valid immutable copy]
    D -- erasure --> F[Fetch any k valid fragments and decode]
    E --> G[Decompress and verify original SHA-256]
    F --> G
    G --> H[Spool verified requested bytes]
    H --> I[Return 200 or 206 with range and ETag headers]
```

## Failure and repair path

```mermaid
flowchart TD
    A[Heartbeat missing for 20 seconds] --> B[Mark node failed]
    B --> C[Persist repair job in etcd outbox]
    C --> D[Publish durable RabbitMQ message]
    D --> E[Worker claims job]
    E --> F[Choose healthy source copies or k fragments]
    F --> G[Rebuild bytes and verify original hash]
    G --> H[Select distinct healthy replacement node]
    H --> I[Persist replacement before changing metadata]
    I --> J[Commit replacement location and record repair traffic]
    J --> K[Garbage collect obsolete physical copies later]
```

## Scrubbing path

```mermaid
flowchart TD
    A[Periodic scrub or manual scan] --> B[Re-read every referenced physical fragment]
    B --> C[Compare stored bytes with recorded SHA-256]
    C --> D{Missing or corrupt?}
    D -- no --> E[Record successful scrub]
    D -- yes --> F[Recover from verified healthy sources]
    F --> G[Replace corrupt fragment atomically]
    G --> E
```

## Coverage and limits

1. Python virtual environment, Git/GitHub remote and container tools are present.
2. Eight storage nodes, SHA-256 validation, disk persistence and heartbeats are implemented.
3. Fixed chunking, optional compression, version metadata, virtual-node consistent
   hashing and reference-counted deduplication are implemented and exercised.
4. Quorum replication, selectable zfec coding, failure relocation and background
   scrubbing are implemented. Small-workload repair measurements completed; the
   complete interrupted failure suite still requires a successful final rerun.
5. FastAPI bucket/object APIs, multipart/streaming, ranges, metadata, filters,
   API keys, shared rate limiting, cluster status and Swagger are implemented.
   Ten API tests passed; Elasticsearch testing awaits service readiness.
6. React browsing, upload/download, metadata, search and maintenance views are built.
   A real file upload was observed in the live UI. Search depends on Elasticsearch.
7. Bonus configuration includes Nginx, RabbitMQ workers, replicated etcd,
   Kubernetes StatefulSets and monitoring provisioning.
8. Bonus files include service Dockerfiles, Compose, Kubernetes manifests, HPA,
   deployment rendering and GitHub workflows. No remote cloud deployment is claimed.

This is a local educational implementation. Internal services rely on network
isolation and an internal key; transport TLS and multi-user authorization are not
configured. API keys grant workspace-wide access. Data remains hash-validated
during every read. Metadata operations use fenced etcd transactions; a single
logical metadata lock intentionally limits write concurrency for this lab.
