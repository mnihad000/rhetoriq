[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$PlatformVarFile,
    [Parameter(Mandatory)][string]$ImageValues,
    [Parameter(Mandatory)][ValidateSet("Plan", "Apply")][string]$Action,
    [Parameter(Mandatory)][string]$PlanFile,
    [switch]$BudgetSubscriptionConfirmed,
    [switch]$ApproveBillableAwsChanges
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not (Test-Path -LiteralPath $ImageValues)) { throw "Missing digest values file." }
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

$cluster = terraform -chdir=infra/terraform/eks-demo/foundation output -raw cluster_name
$context = "rhetoriq-$cluster"
if ((kubectl config current-context).Trim() -ne $context) { throw "Current context must be $context." }
kubectl --context $context -n rhetoriq-demo get secret rhetoriq-secrets -o name | Out-Null

$planPath = [IO.Path]::GetFullPath($PlanFile)
if ($Action -eq "Plan") {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $planPath) | Out-Null
    terraform -chdir=infra/terraform/eks-demo/platform plan -input=false `
        -var-file=$PlatformVarFile -var deploy_application=true `
        -var "backend_digest=$backendDigest" -var "b5_digest=$b5Digest" `
        -var "flink_digest=$flinkDigest" -var "frontend_digest=$frontendDigest" -out=$planPath
    if ($LASTEXITCODE -ne 0) { throw "EKS application plan failed." }
    Write-Host "Saved application plan to $planPath. Review it before a separate Apply invocation."
    exit 0
}
if (-not $ApproveBillableAwsChanges) { throw "Pass -ApproveBillableAwsChanges to apply the reviewed EKS application plan." }
if (-not $BudgetSubscriptionConfirmed) { throw "STOP: confirm the AWS Budget email subscription before application deployment." }
if (-not (Test-Path -LiteralPath $planPath)) { throw "Reviewed plan file does not exist." }
terraform -chdir=infra/terraform/eks-demo/platform apply -input=false $planPath
if ($LASTEXITCODE -ne 0) { throw "EKS application apply failed." }
kubectl --context $context -n rhetoriq-demo get pods,jobs,pvc,certificate
Write-Host "EKS application deployed. Run the bounded smoke and evidence workflow; do not claim B3-B5 qualification."
