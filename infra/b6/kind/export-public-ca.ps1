[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [string]$Output = "evidence/b6-runs/kind-demo-ca.crt"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if ((kubectl config current-context).Trim() -ne $Context) { throw "Unexpected kubectl context." }
$certificate = kubectl --context $Context -n rhetoriq-demo get configmap rhetoriq-internal-ca -o jsonpath='{.data.ca\.crt}'
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($certificate)) {
    throw "The public trust-manager CA bundle is unavailable."
}
$parent = Split-Path -Parent ([IO.Path]::GetFullPath($Output))
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$certificate | Set-Content -LiteralPath $Output -Encoding ascii
Write-Host "Wrote the public demo CA certificate to $Output without reading a Kubernetes Secret."
