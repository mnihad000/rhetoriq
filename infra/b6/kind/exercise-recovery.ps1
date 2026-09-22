[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to restart demo workloads." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }
$namespace = "rhetoriq-demo"

$apiPod = (kubectl --context $Context -n $namespace get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}').Trim()
kubectl --context $Context -n $namespace exec $apiPod -- python -m events.flink_savepoint --timeout-seconds 120
if ($LASTEXITCODE -ne 0) { throw "Final pre-restart Flink savepoint failed." }

foreach ($workload in @("deployment/document-worker", "deployment/elasticsearch-worker", "deployment/flink-taskmanager")) {
    kubectl --context $Context -n $namespace rollout restart $workload | Out-Null
    kubectl --context $Context -n $namespace rollout status $workload --timeout=10m | Out-Null
}
foreach ($workload in @("statefulset/flink-jobmanager", "statefulset/postgres", "statefulset/kafka", "statefulset/elasticsearch", "statefulset/neo4j")) {
    kubectl --context $Context -n $namespace rollout restart $workload | Out-Null
    kubectl --context $Context -n $namespace rollout status $workload --timeout=15m | Out-Null
}

$apiPod = (kubectl --context $Context -n $namespace get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}').Trim()
kubectl --context $Context -n $namespace exec $apiPod -- python -c "import json,urllib.request; print(json.load(urllib.request.urlopen('http://localhost:8000/health/ready',timeout=10)))"
if ($LASTEXITCODE -ne 0) { throw "API readiness failed after recovery exercise." }
Write-Host "Recovery restarts completed. Compare retained smoke counts and collect fresh evidence before making any demo claim."

