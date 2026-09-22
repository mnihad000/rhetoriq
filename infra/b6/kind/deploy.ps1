[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [string]$ImageValues = "infra/b6/generated/values-images-kind.yaml",
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to deploy the full chart." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }
if (-not (Test-Path -LiteralPath $ImageValues)) { throw "Missing generated digest values: $ImageValues" }

& "$PSScriptRoot/preflight.ps1" -Context $Context | Out-Host
& "$PSScriptRoot/test-network-policy.ps1" -Context $Context -ApproveClusterMutation | Out-Host
kubectl --context $Context -n rhetoriq-demo get secret rhetoriq-secrets -o name | Out-Null
kubectl --context $Context -n cert-manager wait --for=condition=Available deployment/cert-manager --timeout=60s | Out-Null
kubectl --context $Context -n trust-manager wait --for=condition=Available deployment/trust-manager --timeout=60s | Out-Null
kubectl --context $Context -n ingress-nginx wait --for=condition=Available deployment/ingress-nginx-controller --timeout=60s | Out-Null

helm upgrade --install rhetoriq deploy/helm/rhetoriq --namespace rhetoriq-demo `
    --values deploy/helm/rhetoriq/values-kind.yaml --values $ImageValues `
    --set global.imagePullPolicy=IfNotPresent --atomic --wait --wait-for-jobs --timeout 30m
if ($LASTEXITCODE -ne 0) { throw "Helm deployment failed." }

kubectl --context $Context -n rhetoriq-demo get pods,jobs,pvc,certificate
Write-Host "Deployment complete. This is not B3-B5 qualification; run the bounded smoke and recovery gates separately."

