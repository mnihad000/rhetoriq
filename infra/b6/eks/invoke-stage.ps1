[CmdletBinding()]
param(
    [Parameter(Mandatory)][ValidateSet("bootstrap", "foundation-standard", "platform-prerequisites", "foundation-strict")][string]$Stage,
    [Parameter(Mandatory)][ValidateSet("Plan", "Apply")][string]$Action,
    [Parameter(Mandatory)][string]$PlanFile,
    [Parameter(Mandatory)][string]$StateBucket,
    [Parameter(Mandatory)][string]$BootstrapState,
    [Parameter(Mandatory)][string]$BootstrapVarFile,
    [Parameter(Mandatory)][string]$FoundationVarFile,
    [Parameter(Mandatory)][string]$PlatformVarFile,
    [Parameter(Mandatory)][datetime]$ExpiresAt,
    [string]$Region = "us-east-2",
    [switch]$ApproveAwsChanges
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
foreach ($command in @("aws", "terraform")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Missing required command: $command" }
}
$planPath = [IO.Path]::GetFullPath($PlanFile)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $planPath) | Out-Null

function Initialize-Foundation {
    terraform -chdir=infra/terraform/eks-demo/foundation init -reconfigure `
        -backend-config="bucket=$StateBucket" -backend-config="key=foundation/terraform.tfstate" `
        -backend-config="region=$Region" -backend-config="encrypt=true" -backend-config="use_lockfile=true"
    if ($LASTEXITCODE -ne 0) { throw "Foundation init failed." }
}
function Initialize-Platform {
    terraform -chdir=infra/terraform/eks-demo/platform init -reconfigure `
        -backend-config="bucket=$StateBucket" -backend-config="key=platform/terraform.tfstate" `
        -backend-config="region=$Region" -backend-config="encrypt=true" -backend-config="use_lockfile=true"
    if ($LASTEXITCODE -ne 0) { throw "Platform init failed." }
}
function Select-ClusterContext {
    $cluster = terraform -chdir=infra/terraform/eks-demo/foundation output -raw cluster_name
    aws eks update-kubeconfig --region $Region --name $cluster --alias "rhetoriq-$cluster" | Out-Null
    return "rhetoriq-$cluster"
}

if ($Action -eq "Plan") {
    switch ($Stage) {
        "bootstrap" {
            terraform -chdir=infra/terraform/eks-demo/bootstrap init
            terraform -chdir=infra/terraform/eks-demo/bootstrap plan -input=false -state=$BootstrapState `
                -var-file=$BootstrapVarFile -out=$planPath
        }
        "foundation-standard" {
            Initialize-Foundation
            terraform -chdir=infra/terraform/eks-demo/foundation plan -input=false -var-file=$FoundationVarFile `
                -var network_policy_enforcing_mode=standard -out=$planPath
        }
        "platform-prerequisites" {
            Select-ClusterContext | Out-Null
            Initialize-Platform
            terraform -chdir=infra/terraform/eks-demo/platform plan -input=false -var-file=$PlatformVarFile `
                -var deploy_application=false -out=$planPath
        }
        "foundation-strict" {
            Initialize-Foundation
            terraform -chdir=infra/terraform/eks-demo/foundation plan -input=false -var-file=$FoundationVarFile `
                -var network_policy_enforcing_mode=strict -out=$planPath
        }
    }
    if ($LASTEXITCODE -ne 0) { throw "Terraform plan failed for stage $Stage." }
    Write-Host "Saved $Stage plan to $planPath. Review it before a separate Apply invocation."
    exit 0
}

if (-not $ApproveAwsChanges) { throw "Pass -ApproveAwsChanges only after reviewing the exact saved plan." }
if (-not (Test-Path -LiteralPath $planPath)) { throw "Reviewed plan file does not exist: $planPath" }
$identity = aws sts get-caller-identity --output json | ConvertFrom-Json
if (-not $identity.Account) { throw "AWS caller identity could not be verified." }

if ($Stage -eq "foundation-standard") {
    & "$PSScriptRoot/register-teardown.ps1" -ExpiresAt $ExpiresAt -StateBucket $StateBucket `
        -BootstrapState $BootstrapState -BootstrapVarFile $BootstrapVarFile `
        -FoundationVarFile $FoundationVarFile -PlatformVarFile $PlatformVarFile -Region $Region
    if (-not (Get-ScheduledTask -TaskName "RhetoriQ-B6-EKS-Teardown" -ErrorAction SilentlyContinue)) {
        throw "STOP: teardown task registration was not verifiable."
    }
}

switch ($Stage) {
    "bootstrap" {
        terraform -chdir=infra/terraform/eks-demo/bootstrap apply -input=false -state=$BootstrapState $planPath
    }
    "foundation-standard" {
        Initialize-Foundation
        terraform -chdir=infra/terraform/eks-demo/foundation apply -input=false $planPath
    }
    "platform-prerequisites" {
        Select-ClusterContext | Out-Null
        Initialize-Platform
        terraform -chdir=infra/terraform/eks-demo/platform apply -input=false $planPath
    }
    "foundation-strict" {
        Initialize-Foundation
        terraform -chdir=infra/terraform/eks-demo/foundation apply -input=false $planPath
    }
}
if ($LASTEXITCODE -ne 0) { throw "Terraform apply failed for stage $Stage." }

if ($Stage -eq "foundation-standard") {
    Select-ClusterContext | Out-Null
    Write-Host "STOP: next create and review the platform-prerequisites plan. Do not enable strict mode first."
}
if ($Stage -eq "platform-prerequisites") {
    Write-Host "STOP: next create and review the foundation-strict plan."
}
if ($Stage -eq "foundation-strict") {
    $context = Select-ClusterContext
    kubectl --context $context wait --for=condition=Ready node --all --timeout=10m | Out-Null
    $checkPod = "rhetoriq-sysctl-check"
    try {
        kubectl --context $context -n kube-system run $checkPod --restart=Never `
            --image=busybox@sha256:9db7b59979c38555a39def84a31fb98b5296952f9e3afd4f6f11f05b07adfab0 `
            --command -- sh -c "sysctl -n vm.max_map_count" | Out-Null
        kubectl --context $context -n kube-system wait --for=jsonpath='{.status.phase}'=Succeeded "pod/$checkPod" --timeout=2m | Out-Null
        $mapCount = [int64](kubectl --context $context -n kube-system logs $checkPod).Trim()
        if ($mapCount -lt 1048576) { throw "STOP: EKS node vm.max_map_count is $mapCount." }
    }
    finally {
        kubectl --context $context -n kube-system delete pod $checkPod --ignore-not-found --wait=false | Out-Null
    }
    & "$PSScriptRoot/../kind/test-network-policy.ps1" -Context $context -ApproveClusterMutation
    Write-Host "Strict policy and sysctl checks passed. STOP until the Budget email is confirmed, images are pushed, and Secret upload is separately approved."
}
