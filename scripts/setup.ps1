$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .env)) {
    $taskInternalKey = [guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')
    $taskRabbitPassword = [guid]::NewGuid().ToString('N')
    $taskGrafanaPassword = [guid]::NewGuid().ToString('N')
    @"
API_KEY=lab-api-key-change-me
INTERNAL_KEY=$taskInternalKey
RABBIT_PASSWORD=$taskRabbitPassword
GRAFANA_PASSWORD=$taskGrafanaPassword
"@ | Set-Content .env
}
if (-not (Test-Path .local-ca.pem)) {
    $taskCertificates = Get-ChildItem Cert:\CurrentUser\Root,Cert:\LocalMachine\Root,Cert:\CurrentUser\CA,Cert:\LocalMachine\CA -ErrorAction SilentlyContinue
    $taskPem = foreach ($taskCertificate in $taskCertificates) {
        '-----BEGIN CERTIFICATE-----'
        [Convert]::ToBase64String($taskCertificate.RawData,[Base64FormattingOptions]::InsertLineBreaks)
        '-----END CERTIFICATE-----'
    }
    $taskPem | Set-Content -Encoding ascii .local-ca.pem
}
docker network inspect distributed-net *> $null
if ($LASTEXITCODE -ne 0) { docker network create distributed-net }
docker build -f docker/Dockerfile.base -t distributed-framework:base .
if ($LASTEXITCODE -ne 0) { throw 'Runtime image build failed' }
docker compose build
if ($LASTEXITCODE -ne 0) { throw 'Service build failed' }
docker compose up -d
if ($LASTEXITCODE -ne 0) { throw 'Container startup failed' }
Write-Host 'UI: http://localhost:8088 | Swagger: http://localhost:8000/docs'
Write-Host 'Local demo API key is configured in .env. Change it before any public deployment.'
