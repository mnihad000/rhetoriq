[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [int]$Minutes = 15,
    [string]$RunId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ"),
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to create the temporary evidence exporter pod." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }
if ($Minutes -lt 1 -or $Minutes -gt 60) { throw "Minutes must be between 1 and 60." }

$directory = Join-Path "evidence/b6-runs" $RunId
New-Item -ItemType Directory -Force -Path $directory | Out-Null
kubectl --context $Context -n rhetoriq-demo get pods,jobs,pvc,certificate -o wide | Set-Content (Join-Path $directory "resources.txt")
kubectl --context $Context -n rhetoriq-demo get events --sort-by=.lastTimestamp | Set-Content (Join-Path $directory "events.txt")
kubectl --context $Context -n rhetoriq-demo get pods -o json | Set-Content (Join-Path $directory "pods.json")
kubectl --context $Context -n rhetoriq-demo get secrets -o custom-columns='NAME:.metadata.name,TYPE:.type' | Set-Content (Join-Path $directory "secret-metadata.txt")

$nodeName = (kubectl --context $Context get nodes -o jsonpath='{.items[0].metadata.name}').Trim()
if (-not $nodeName) { throw "No Kubernetes node was available for kubelet memory sampling." }
$samples = [math]::Ceiling($Minutes * 2)
for ($index = 0; $index -lt $samples; $index++) {
    $sample = kubectl --context $Context get --raw "/api/v1/nodes/$nodeName/proxy/stats/summary"
    Add-Content -LiteralPath (Join-Path $directory "kubelet-summary.jsonl") -Value $sample
    if ($index -lt ($samples - 1)) { Start-Sleep -Seconds 30 }
}

$redaction = '(?i)(password|secret|token|authorization|api[_-]?key)(\s*[=:]\s*)[^\s,;]+'
$pods = kubectl --context $Context -n rhetoriq-demo get pods -o name
foreach ($podResource in @($pods | Where-Object { $_ })) {
    $pod = $podResource -replace '^pod/', ''
    $logs = kubectl --context $Context -n rhetoriq-demo logs $pod --all-containers --tail=1000 2>&1 | Out-String
    [regex]::Replace($logs, $redaction, '$1$2<redacted>') | Set-Content (Join-Path $directory "logs-$pod.txt")
}

$exporter = "evidence-export-$($RunId.ToLowerInvariant())"
if ($exporter.Length -gt 63) { $exporter = $exporter.Substring(0, 63).TrimEnd('-') }
$podManifest = @"
apiVersion: v1
kind: Pod
metadata: {name: $exporter, namespace: rhetoriq-demo}
spec:
  restartPolicy: Never
  containers:
    - name: exporter
      image: busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0
      command: [sleep, "3600"]
      volumeMounts: [{name: evidence, mountPath: /evidence, readOnly: true}]
  volumes: [{name: evidence, persistentVolumeClaim: {claimName: evidence}}]
"@
try {
    $podManifest | kubectl --context $Context apply -f - | Out-Null
    kubectl --context $Context -n rhetoriq-demo wait --for=condition=Ready "pod/$exporter" --timeout=120s | Out-Null
    kubectl --context $Context -n rhetoriq-demo cp "${exporter}:/evidence/." (Join-Path $directory "pvc")
}
finally {
    kubectl --context $Context -n rhetoriq-demo delete pod $exporter --ignore-not-found --wait=false | Out-Null
}
Write-Host "Sanitized evidence collected under $directory"
