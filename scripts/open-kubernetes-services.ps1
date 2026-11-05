$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
$taskKubeconfig = Join-Path (Get-Location) 'tmp/kubeconfig'
if (-not (Test-Path $taskKubeconfig)) { throw 'Run the Kubernetes setup first to create tmp/kubeconfig.' }
$taskForwarding = @(
    @{Name='api'; Service='loadbalancer'; Ports='8100:80'},
    @{Name='ui'; Service='ui'; Ports='8188:80'},
    @{Name='prometheus'; Service='prometheus'; Ports='9091:9090'},
    @{Name='grafana'; Service='grafana'; Ports='3001:3000'},
    @{Name='rabbitmq'; Service='rabbit'; Ports='15674:15672'}
)
foreach ($taskForward in $taskForwarding) {
    $taskPort = [int]($taskForward.Ports.Split(':')[0])
    if (Get-NetTCPConnection -LocalPort $taskPort -State Listen -ErrorAction SilentlyContinue) { continue }
    Start-Process -FilePath kubectl -ArgumentList @('--kubeconfig',$taskKubeconfig,'-n','atlas','port-forward',('service/'+$taskForward.Service),$taskForward.Ports,'--address=127.0.0.1') -WindowStyle Hidden -RedirectStandardOutput ('tmp/kubernetes-'+$taskForward.Name+'.log') -RedirectStandardError ('tmp/kubernetes-'+$taskForward.Name+'-error.log')
}
Write-Host 'Kubernetes UI http://localhost:8188 | API http://localhost:8100/docs'
Write-Host 'Grafana http://localhost:3001 | Prometheus http://localhost:9091 | RabbitMQ http://localhost:15674'
