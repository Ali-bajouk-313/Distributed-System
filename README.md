# Atlas - Distributed Object Storage

Tasks 1-6: content-addressed chunks, consistent hashing, replication and zfec erasure coding, recovery workers, FastAPI access and a React console.

- UI: http://localhost:8088
- API documentation: http://localhost:8000/docs
- API key: read API_KEY from the ignored .env file.
- Start: powershell -ExecutionPolicy Bypass -File scripts/setup.ps1
- Run guide and honest verification status: [docs/RUN-AND-DEMONSTRATE.md](docs/RUN-AND-DEMONSTRATE.md)
- Architecture and phase flowcharts: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- Illustrated report: [output/pdf/Atlas-Storage-Report.pdf](output/pdf/Atlas-Storage-Report.pdf)

The integrated services are in framework/. Earlier task-stage code is preserved in storage/. Tests use real storage services; pytest evidence is in docs/api-results.xml. The full workload generator supports 5,000 small files and ten 100-MiB objects; consult the run guide for which measurements actually completed.

Tasks 7-8 are bonus work: Kubernetes, monitoring and GitHub workflow configurations are included, but a complete remote deployment is not claimed. Do not commit .env or .local-ca.pem.
