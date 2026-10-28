$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
docker info --format '{{.ServerVersion}}' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop, wait until its engine is running, then retry.' }
docker compose up -d --no-build etcd1 etcd2 etcd3 rabbit elasticsearch metadata metadata2 gateway1 gateway2 worker1 worker2 loadbalancer node1 node2 node3 node4 node5 node6 node7 node8
if ($LASTEXITCODE -ne 0) { throw 'Some services did not start. Run docker compose ps and inspect their logs.' }
if (-not (Test-Path ui/dist/index.html)) { throw 'UI build is missing. Run npm.cmd install and npm.cmd run build in the ui folder.' }
$taskUiAlreadyRunning = $false
try { $taskUiAlreadyRunning = (Invoke-WebRequest -UseBasicParsing -Uri http://localhost:8088 -TimeoutSec 3).StatusCode -eq 200 } catch { }
if (-not $taskUiAlreadyRunning) {
    New-Item -ItemType Directory -Force tmp | Out-Null
    Start-Process -FilePath node -ArgumentList 'scripts/ui_server.cjs' -WorkingDirectory (Get-Location).Path -WindowStyle Hidden -RedirectStandardOutput 'tmp/ui-start.log' -RedirectStandardError 'tmp/ui-start-error.log'
}
Write-Host 'Open http://localhost:8088 and use the API_KEY from .env.'
Write-Host 'Swagger: http://localhost:8000/docs'
