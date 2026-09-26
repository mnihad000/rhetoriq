$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceSha = "7d1c4f2ab91bb5a557050b9c591ea621fd6c1652"
$runId = "test"
$accountId = "123456789012"
$region = "us-east-2"
$tag = "$($sourceSha.Substring(0, 12))-$runId-gha42-1"
$names = @("backend", "b5", "flink", "frontend")
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "rhetoriq-image-artifact-test-$PID"
$artifactDir = Join-Path $testRoot "artifact"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$metadataPath = Join-Path $artifactDir "images-ecr.json"
$valuesPath = Join-Path $artifactDir "values-images-eks.yaml"
$outputPath = Join-Path $testRoot "verified.yaml"
$digest = "sha256:" + ("a" * 64)
$repositories = [ordered]@{}
$images = [ordered]@{}
foreach ($name in $names) {
    $repository = "$accountId.dkr.ecr.$region.amazonaws.com/rhetoriq-$runId-$name"
    $repositories[$name] = $repository
    $images[$name] = [ordered]@{
        name = $name; source_sha = $sourceSha; run_id = $runId; region = $region
        account_id = $accountId; tag = $tag; repository = $repository
        digest = $digest; manifest_media_type = "application/vnd.oci.image.manifest.v1+json"
        platform = "linux/amd64"; build_image_id = "sha256:" + ("b" * 64)
        identity_kind = "ecr_manifest"; github_run_id = "42"
        github_run_attempt = "1"; workflow_sha = $sourceSha
    }
}
$metadata = [ordered]@{
    schema_version = 1; source_sha = $sourceSha; run_id = $runId; region = $region
    account_id = $accountId; tag = $tag; github_run_id = "42"; github_run_attempt = "1"
    workflow_sha = $sourceSha
    github_run_url = "https://github.com/mnihad000/rhetoriq/actions/runs/42"
    images = $images
}
$values = @("images:")
foreach ($name in $names) {
    $values += "  ${name}:"
    $values += "    repository: $($repositories[$name])"
    $values += '    digest: "' + $digest + '"'
}
$metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
$values | Set-Content -LiteralPath $valuesPath -Encoding UTF8
$global:wrongEcrDigest = $false

function terraform {
    $global:LASTEXITCODE = 0
    return ($repositories | ConvertTo-Json -Compress)
}
function aws {
    $global:LASTEXITCODE = 0
    if ($args[0] -eq "sts") { return $accountId }
    if ($args[0] -eq "ecr" -and $args[1] -eq "describe-images") {
        $returnedDigest = if ($global:wrongEcrDigest) { "sha256:" + ("c" * 64) } else { $digest }
        return (@{ imageDetails = @(@{
            imageDigest = $returnedDigest
            imageManifestMediaType = "application/vnd.oci.image.manifest.v1+json"
            imageTags = @($tag)
        }) } | ConvertTo-Json -Depth 5 -Compress)
    }
    throw "Unexpected mock AWS command: $args"
}
function Assert-Fails([scriptblock]$Action, [string]$Description) {
    try { & $Action } catch { return }
    throw "Expected failure: $Description"
}

try {
    & "$PSScriptRoot/verify-image-artifact.ps1" -ArtifactDir $artifactDir -SourceSha $sourceSha -RunId $runId -Output $outputPath | Out-Null
    if (-not (Test-Path -LiteralPath $outputPath)) { throw "Verified values file was not written." }
    Remove-Item -LiteralPath $outputPath

    $metadata.images.Remove("frontend")
    $metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    Assert-Fails { & "$PSScriptRoot/verify-image-artifact.ps1" -ArtifactDir $artifactDir -SourceSha $sourceSha -RunId $runId -Output $outputPath } "missing image"
    $metadata.images["frontend"] = $images["frontend"]
    $metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $metadataPath -Encoding UTF8

    $tampered = $values.Clone()
    $tampered[-1] = '    digest: "sha256:' + ("d" * 64) + '"'
    $tampered | Set-Content -LiteralPath $valuesPath -Encoding UTF8
    Assert-Fails { & "$PSScriptRoot/verify-image-artifact.ps1" -ArtifactDir $artifactDir -SourceSha $sourceSha -RunId $runId -Output $outputPath } "mismatched YAML digest"
    $values | Set-Content -LiteralPath $valuesPath -Encoding UTF8

    $global:wrongEcrDigest = $true
    Assert-Fails { & "$PSScriptRoot/verify-image-artifact.ps1" -ArtifactDir $artifactDir -SourceSha $sourceSha -RunId $runId -Output $outputPath } "mismatched ECR digest"
    if (Test-Path -LiteralPath $outputPath) { throw "A failed verification wrote deployment values." }
    Write-Host "Artifact verifier passed valid, missing-image, YAML-mismatch, and ECR-mismatch scenarios."
} finally {
    $resolvedRoot = [IO.Path]::GetFullPath($testRoot)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedRoot.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a test directory outside the temporary directory."
    }
    Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction SilentlyContinue
}
