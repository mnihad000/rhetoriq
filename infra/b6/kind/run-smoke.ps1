[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [string]$ImageValues = "infra/b6/generated/values-images-kind.yaml",
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to create the bounded smoke Job." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }

helm upgrade rhetoriq deploy/helm/rhetoriq --namespace rhetoriq-demo `
    --values deploy/helm/rhetoriq/values-kind.yaml --values $ImageValues `
    --set smoke.enabled=true --set evidence.enabled=false --atomic --wait --wait-for-jobs --timeout 30m
if ($LASTEXITCODE -ne 0) { throw "Smoke release failed." }
$status = helm status rhetoriq --namespace rhetoriq-demo --output json | ConvertFrom-Json
$job = "b6-smoke-$($status.version)"
kubectl --context $Context -n rhetoriq-demo wait --for=condition=Complete "job/$job" --timeout=30m | Out-Null
kubectl --context $Context -n rhetoriq-demo logs "job/$job"

helm upgrade rhetoriq deploy/helm/rhetoriq --namespace rhetoriq-demo `
    --values deploy/helm/rhetoriq/values-kind.yaml --values $ImageValues `
    --set smoke.enabled=false --set evidence.enabled=true --atomic --wait --wait-for-jobs --timeout 15m
if ($LASTEXITCODE -ne 0) { throw "Evidence snapshot release failed." }
$status = helm status rhetoriq --namespace rhetoriq-demo --output json | ConvertFrom-Json
$evidenceJob = "b6-evidence-$($status.version)"
kubectl --context $Context -n rhetoriq-demo wait --for=condition=Complete "job/$evidenceJob" --timeout=15m | Out-Null
Write-Host "Smoke and application evidence Jobs completed. Export the evidence PVC before any teardown."

