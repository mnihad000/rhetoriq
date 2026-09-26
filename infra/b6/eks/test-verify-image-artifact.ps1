$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$sourceSha = "7d1c4f2ab91bb5a557050b9c591ea621fd6c1652"
$runId = "test"
$accountId = "123456789012"
$region = "us-east-2"
$tag = "$($sourceSha.Substring(0, 12))-$runId-gha42-1"
$names = @("backend", "b5", "flink", "frontend")
$validDigest = "sha256:" + ("a" * 64)
$testRoot = Join-Path ([IO.Path]::GetTempPath()) "rhetoriq-image-artifact-test-$PID"
$artifactDir = Join-Path $testRoot "artifact"
New-Item -ItemType Directory -Force -Path $artifactDir | Out-Null
$metadataPath = Join-Path $artifactDir "images-ecr.json"
$valuesPath = Join-Path $artifactDir "values-images-eks.yaml"
$outputPath = Join-Path $testRoot "verified.yaml"
$repositories = [ordered]@{}
foreach ($name in $names) {
    $repositories[$name] = "$accountId.dkr.ecr.$region.amazonaws.com/rhetoriq-$runId-$name"
}

# Builds fresh fixture objects for every case so no mutation leaks between scenarios.
# The mocked ECR returns the same digest as the artifact unless a case overrides it.
function New-Scenario([string]$Digest = $validDigest) {
    $images = [ordered]@{}
    foreach ($name in $names) {
        $images[$name] = [ordered]@{
            name = $name; source_sha = $sourceSha; run_id = $runId; region = $region
            account_id = $accountId; tag = $tag; repository = $repositories[$name]
            digest = $Digest; manifest_media_type = "application/vnd.oci.image.manifest.v1+json"
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
    $values = [System.Collections.Generic.List[string]]::new()
    $values.Add("images:")
    foreach ($name in $names) {
        $values.Add("  ${name}:")
        $values.Add("    repository: $($repositories[$name])")
        $values.Add('    digest: "' + $Digest + '"')
    }
    $global:ecrDigest = $Digest
    $global:externalCalls = 0
    return @{ Metadata = $metadata; Values = $values }
}
function Write-Scenario($Scenario) {
    $Scenario.Metadata | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $metadataPath -Encoding UTF8
    $Scenario.Values | Set-Content -LiteralPath $valuesPath -Encoding UTF8
}

function terraform {
    $global:externalCalls++
    $global:LASTEXITCODE = 0
    return ($repositories | ConvertTo-Json -Compress)
}
function aws {
    $global:externalCalls++
    $global:LASTEXITCODE = 0
    if ($args[0] -eq "sts") { return $accountId }
    if ($args[0] -eq "ecr" -and $args[1] -eq "describe-images") {
        return (@{ imageDetails = @(@{
            imageDigest = $global:ecrDigest
            imageManifestMediaType = "application/vnd.oci.image.manifest.v1+json"
            imageTags = @($tag)
        }) } | ConvertTo-Json -Depth 5 -Compress)
    }
    throw "Unexpected mock AWS command: $args"
}
function Invoke-Verifier([string]$Region = $region) {
    & "$PSScriptRoot/verify-image-artifact.ps1" -ArtifactDir $artifactDir -SourceSha $sourceSha -RunId $runId -Region $Region -Output $outputPath
}
function Assert-Fails([scriptblock]$Action, [string]$MessagePattern, [string]$Description) {
    $message = $null
    try { & $Action | Out-Null } catch { $message = $_.Exception.Message }
    if ($null -eq $message) { throw "Expected failure: $Description" }
    if ($message -cnotmatch $MessagePattern) { throw "Wrong failure for ${Description}: $message" }
    if (Test-Path -LiteralPath $outputPath) { throw "A failed verification wrote deployment values: $Description" }
}

try {
    Write-Scenario (New-Scenario)
    Invoke-Verifier | Out-Null
    if (-not (Test-Path -LiteralPath $outputPath)) { throw "Verified values file was not written." }
    Remove-Item -LiteralPath $outputPath

    $scenario = New-Scenario
    $scenario.Metadata.images.Remove("frontend")
    Write-Scenario $scenario
    Assert-Fails { Invoke-Verifier } "^Artifact must contain exactly backend, b5, flink, and frontend\.$" "missing image"

    $scenario = New-Scenario
    $scenario.Values[$scenario.Values.Count - 1] = '    digest: "sha256:' + ("d" * 64) + '"'
    Write-Scenario $scenario
    Assert-Fails { Invoke-Verifier } "^Digest-only values file mismatch:" "mismatched YAML digest"

    Write-Scenario (New-Scenario)
    $global:ecrDigest = "sha256:" + ("c" * 64)
    Assert-Fails { Invoke-Verifier } "^backend ECR manifest digest mismatch:" "mismatched live ECR digest"

    Write-Scenario (New-Scenario -Digest ("sha256:" + ("0" * 64)))
    Assert-Fails { Invoke-Verifier } "^Placeholder backend ECR manifest digest\.$" "all-zero placeholder digest"

    foreach ($malformed in @(("sha256:" + ("a" * 63)), ("sha256:" + ("A" * 64)), ("sha512:" + ("a" * 64)))) {
        Write-Scenario (New-Scenario -Digest $malformed)
        Assert-Fails { Invoke-Verifier } "^Invalid backend ECR manifest digest\.$" "malformed digest $malformed"
    }

    foreach ($badRegion in @("US-EAST-2", "us-east-2;x", "useast2", "")) {
        Write-Scenario (New-Scenario)
        Assert-Fails { Invoke-Verifier -Region $badRegion } "^Region has an invalid AWS region form\.$" "invalid region '$badRegion'"
        if ($global:externalCalls -ne 0) { throw "Invalid region '$badRegion' reached Terraform or AWS." }
    }
    Write-Host "Artifact verifier passed valid, missing-image, YAML-mismatch, ECR-mismatch, placeholder, malformed-digest, and invalid-region scenarios."
} finally {
    $resolvedRoot = [IO.Path]::GetFullPath($testRoot)
    $resolvedTemp = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
    if (-not $resolvedRoot.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a test directory outside the temporary directory."
    }
    Remove-Item -LiteralPath $resolvedRoot -Recurse -Force -ErrorAction SilentlyContinue
}
