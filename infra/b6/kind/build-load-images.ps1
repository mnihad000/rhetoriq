[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [string]$Cluster = "rhetoriq-b6",
    [string]$Output = "infra/b6/generated/values-images-kind.yaml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if ((kubectl config current-context).Trim() -ne $Context) {
    throw "Refusing to load images outside context $Context."
}
$commit = (git rev-parse --short=12 HEAD).Trim()
$tag = "b6-$commit"
$images = @(
    @{ Name = "backend"; Repository = "rhetoriq-backend"; Dockerfile = "backend/Dockerfile"; Context = "backend" },
    @{ Name = "b5"; Repository = "rhetoriq-b5"; Dockerfile = "infra/b5/Dockerfile"; Context = "." },
    @{ Name = "flink"; Repository = "rhetoriq-flink"; Dockerfile = "infra/flink/Dockerfile"; Context = "." },
    @{ Name = "frontend"; Repository = "rhetoriq-frontend"; Dockerfile = "frontend/Dockerfile"; Context = "frontend" }
)

$resolved = [ordered]@{}
foreach ($image in $images) {
    $reference = "$($image.Repository):$tag"
    docker buildx build --platform linux/amd64 --load --tag $reference --file $image.Dockerfile $image.Context
    if ($LASTEXITCODE -ne 0) { throw "Image build failed: $reference" }
    kind load docker-image --name $Cluster $reference
    if ($LASTEXITCODE -ne 0) { throw "kind image load failed: $reference" }

    $inspection = docker exec "$Cluster-control-plane" crictl inspecti $reference | ConvertFrom-Json
    $repositoryPattern = "(^|/)" + [regex]::Escape($image.Repository) + "@sha256:[0-9a-f]{64}$"
    $repoDigest = @($inspection.status.repoDigests | Where-Object { $_ -match $repositoryPattern })[0]
    if (-not $repoDigest) { throw "containerd did not expose a repo digest for $reference" }
    $resolved[$image.Name] = @{ repository = $image.Repository; digest = $repoDigest.Split("@", 2)[1] }
}

$parent = Split-Path -Parent $Output
New-Item -ItemType Directory -Force -Path $parent | Out-Null
$lines = @("images:")
foreach ($name in $resolved.Keys) {
    $lines += "  ${name}:"
    $lines += "    repository: $($resolved[$name].repository)"
    $lines += "    digest: `"$($resolved[$name].digest)`""
}
Set-Content -LiteralPath $Output -Value $lines -Encoding UTF8
Write-Host "Wrote digest-only kind image overrides to $Output"
