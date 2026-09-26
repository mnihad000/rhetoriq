[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
Set-Location -LiteralPath $repoRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker is required for pinned Helm, kubeconform, and Terraform validation."
}
$python = Join-Path $repoRoot ".venv/Scripts/python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) { throw "Python is required for the B6 render contract validator." }
    $python = $pythonCommand.Source
}

$runtimeLock = Get-Content -LiteralPath infra/b5/runtime-lock.json -Raw | ConvertFrom-Json
$helmImage = $runtimeLock.helm_validation_tool
$kubeconformImage = $runtimeLock.kubeconform_validation_tool
$terraformImage = $runtimeLock.terraform_validation_tool
$generated = Join-Path $repoRoot "infra/b6/generated/validation"
New-Item -ItemType Directory -Force -Path $generated | Out-Null

$profiles = @(
    @{ Name = "kind"; Values = "deploy/helm/rhetoriq/values-kind.yaml"; Kubernetes = "1.37.0" },
    @{ Name = "eks-demo"; Values = "deploy/helm/rhetoriq/values-eks-demo.yaml"; Kubernetes = "1.36.0" }
)
foreach ($profile in $profiles) {
    docker run --rm -v "${repoRoot}:/work" -w /work $helmImage `
        lint deploy/helm/rhetoriq -f $profile.Values | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Helm lint failed for $($profile.Name)." }

    $render = Join-Path $generated "$($profile.Name).yaml"
    docker run --rm -v "${repoRoot}:/work" -w /work $helmImage `
        template rhetoriq deploy/helm/rhetoriq -f $profile.Values |
        Set-Content -LiteralPath $render -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw "Helm render failed for $($profile.Name)." }

    Get-Content -LiteralPath $render -Raw |
        & $python infra/b6/validate-render.py --profile $profile.Name
    if ($LASTEXITCODE -ne 0) { throw "B6 topology validation failed for $($profile.Name)." }

    Get-Content -LiteralPath $render -Raw |
        docker run --rm -i $kubeconformImage -strict -summary `
            -ignore-missing-schemas -kubernetes-version $profile.Kubernetes
    if ($LASTEXITCODE -ne 0) { throw "Kubernetes schema validation failed for $($profile.Name)." }
}

$scripts = Get-ChildItem -LiteralPath (Join-Path $repoRoot "infra/b6") -Recurse -Filter *.ps1
foreach ($script in $scripts) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $script.FullName,
        [ref]$tokens,
        [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) {
        throw "PowerShell parse failed for $($script.FullName): $($errors[0].Message)"
    }
}

docker compose --env-file .env.production.example config --quiet
if ($LASTEXITCODE -ne 0) { throw "Compose compatibility rendering failed." }

foreach ($state in @("bootstrap", "foundation", "platform")) {
    $workingDirectory = "/work/infra/terraform/eks-demo/$state"
    docker run --rm -v "${repoRoot}:/work" -w $workingDirectory $terraformImage `
        fmt -check -recursive | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Terraform format check failed for $state." }
    docker run --rm -v "${repoRoot}:/work" -w $workingDirectory $terraformImage `
        init -backend=false -input=false | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Terraform init failed for $state." }
    docker run --rm -v "${repoRoot}:/work" -w $workingDirectory $terraformImage validate | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "Terraform validate failed for $state." }
}

Write-Host "B6 static validation passed for both profiles and all Terraform states."
