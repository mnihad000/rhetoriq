[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [ValidateRange(1024, 65535)][int]$LocalPort = 8443
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }
kubectl --context $Context -n ingress-nginx get service ingress-nginx-controller -o name | Out-Null
Write-Host "Forwarding https://rhetoriq.local:$LocalPort on loopback. Stop with Ctrl+C."
kubectl --context $Context -n ingress-nginx port-forward service/ingress-nginx-controller "${LocalPort}:443" --address 127.0.0.1
