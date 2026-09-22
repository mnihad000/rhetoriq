[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$RunId,
    [string]$Region = "us-east-2",
    [string]$Output = "infra/b6/generated/values-images-eks.yaml",
    [switch]$ApproveImagePush
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveImagePush) { throw "Pass -ApproveImagePush to build and push four immutable ECR images." }
$commit = (git rev-parse --short=12 HEAD).Trim()
$tag = "$commit-$RunId"
$repositories = terraform -chdir=infra/terraform/eks-demo/foundation output -json ecr_repositories | ConvertFrom-Json
$registry = $repositories.backend.Split('/')[0]
aws ecr get-login-password --region $Region | docker login --username AWS --password-stdin $registry | Out-Null
if ($LASTEXITCODE -ne 0) { throw "ECR login failed." }

$images = @(
    @{ Name = "backend"; Dockerfile = "backend/Dockerfile"; Context = "backend" },
    @{ Name = "b5"; Dockerfile = "infra/b5/Dockerfile"; Context = "." },
    @{ Name = "flink"; Dockerfile = "infra/flink/Dockerfile"; Context = "." },
    @{ Name = "frontend"; Dockerfile = "frontend/Dockerfile"; Context = "frontend" }
)
$resolved = [ordered]@{}
foreach ($image in $images) {
    $repository = $repositories.($image.Name)
    $reference = "${repository}:$tag"
    docker buildx build --platform linux/amd64 --push --tag $reference --file $image.Dockerfile $image.Context
    if ($LASTEXITCODE -ne 0) { throw "ECR image push failed: $($image.Name)" }
    $digest = (docker buildx imagetools inspect $reference --format '{{json .Manifest.Digest}}').Trim('"')
    if ($digest -notmatch '^sha256:[0-9a-f]{64}$') { throw "Could not resolve pushed digest for $reference" }
    $resolved[$image.Name] = @{ repository = $repository; digest = $digest }
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
Write-Host "Pushed immutable images and wrote digest-only values to $Output."

