[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PlatformVarFile,
    [Parameter(Mandatory)][string]$ImageValues,
    [Parameter(Mandatory)][ValidateSet("smoke", "evidence")][string]$Stage,
    [Parameter(Mandatory)][ValidateSet("Plan", "Apply")][string]$Action,
    [Parameter(Mandatory)][string]$PlanFile,
    [switch]$ApproveBillableAwsChanges
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$imageConfig = Get-Content -LiteralPath $ImageValues -Raw
function Get-ImageDigest([string]$Name) {
    $match = [regex]::Match($imageConfig, "(?ms)^\s{2}$([regex]::Escape($Name)):\s*.*?^\s{4}digest:\s*[`"']?(sha256:[0-9a-f]{64})")
    if (-not $match.Success) { throw "Missing sha256 digest for image $Name." }
    return $match.Groups[1].Value
}
$backendDigest = Get-ImageDigest "backend"
$b5Digest = Get-ImageDigest "b5"
$flinkDigest = Get-ImageDigest "flink"
$frontendDigest = Get-ImageDigest "frontend"
$smoke = if ($Stage -eq "smoke") { "true" } else { "false" }
$evidence = if ($Stage -eq "evidence") { "true" } else { "false" }
$planPath = [IO.Path]::GetFullPath($PlanFile)
if ($Action -eq "Plan") {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $planPath) | Out-Null
    terraform -chdir=infra/terraform/eks-demo/platform plan -input=false `
        -var-file=$PlatformVarFile -var deploy_application=true -var "smoke_enabled=$smoke" -var "evidence_enabled=$evidence" `
        -var "backend_digest=$backendDigest" -var "b5_digest=$b5Digest" `
        -var "flink_digest=$flinkDigest" -var "frontend_digest=$frontendDigest" -out=$planPath
    if ($LASTEXITCODE -ne 0) { throw "EKS $Stage plan failed." }
    Write-Host "Saved $Stage plan to $planPath. Review it before a separate Apply invocation."
    exit 0
}
if (-not $ApproveBillableAwsChanges) { throw "Pass -ApproveBillableAwsChanges to apply the reviewed $Stage plan." }
if (-not (Test-Path -LiteralPath $planPath)) { throw "Reviewed plan file does not exist." }
terraform -chdir=infra/terraform/eks-demo/platform apply -input=false $planPath
if ($LASTEXITCODE -ne 0) { throw "EKS $Stage apply failed." }
Write-Host "$Stage completed. Export evidence before teardown after the evidence stage."
