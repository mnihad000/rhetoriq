[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [switch]$ApproveClusterMutation
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveClusterMutation) { throw "Pass -ApproveClusterMutation to create and remove the isolated policy-test namespace." }
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }

$namespace = "rhetoriq-netpol-test"
$manifest = @"
apiVersion: v1
kind: Namespace
metadata: {name: $namespace}
---
apiVersion: v1
kind: Pod
metadata: {name: server, namespace: $namespace, labels: {app: server}}
spec:
  containers:
    - name: server
      image: busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0
      command: [httpd, -f, -p, "8080"]
---
apiVersion: v1
kind: Service
metadata: {name: server, namespace: $namespace}
spec: {selector: {app: server}, ports: [{port: 8080, targetPort: 8080}]}
---
apiVersion: v1
kind: Pod
metadata: {name: allowed, namespace: $namespace, labels: {access: allowed}}
spec: {containers: [{name: client, image: busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0, command: [sleep, "3600"]}]}
---
apiVersion: v1
kind: Pod
metadata: {name: denied, namespace: $namespace}
spec: {containers: [{name: client, image: busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0, command: [sleep, "3600"]}]}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: server-default-deny, namespace: $namespace}
spec: {podSelector: {matchLabels: {app: server}}, policyTypes: [Ingress]}
---
apiVersion: networking.k8s.io/v1
kind: NetworkPolicy
metadata: {name: server-allow, namespace: $namespace}
spec:
  podSelector: {matchLabels: {app: server}}
  policyTypes: [Ingress]
  ingress: [{from: [{podSelector: {matchLabels: {access: allowed}}}], ports: [{protocol: TCP, port: 8080}]}]
"@

try {
    $manifest | kubectl --context $Context apply -f - | Out-Null
    kubectl --context $Context -n $namespace wait --for=condition=Ready pod/server pod/allowed pod/denied --timeout=180s | Out-Null
    kubectl --context $Context -n $namespace exec allowed -- wget -qO- --timeout=3 http://server:8080 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Allowed NetworkPolicy path failed." }
    kubectl --context $Context -n $namespace exec denied -- wget -qO- --timeout=3 http://server:8080 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) { throw "Denied NetworkPolicy path unexpectedly succeeded; enforcement is absent." }
    Write-Host "NetworkPolicy enforcement passed: allowed path succeeded and denied path failed."
}
finally {
    kubectl --context $Context delete namespace $namespace --ignore-not-found --wait=false | Out-Null
}

