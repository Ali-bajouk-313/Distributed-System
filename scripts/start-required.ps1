$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, wait until its engine is running, then retry.' }
docker compose up -d --no-build etcd1 etcd2 etcd3 rabbit elasticsearch metadata metadata2 gateway1 gateway2 worker1 worker2 loadbalancer node1 node2 node3 node4 node5 node6 node7 node8 ui
if ($LASTEXITCODE -ne 0) { throw 'Some services did not start. Run docker compose ps and inspect their logs. If images are missing, run scripts/setup.ps1.' }
Write-Host 'UI: http://localhost:8088 | Swagger: http://localhost:8000/docs'
Write-Host 'Use API_KEY from .env. Run docker compose ps to inspect service readiness.'
