[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ArtifactDir,
    [Parameter(Mandatory)][string]$SourceSha,
    [Parameter(Mandatory)][string]$RunId,
    [string]$Region = "us-east-2",
    [string]$Output = "infra/b6/generated/values-images-eks.yaml"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Assert-Equal([object]$Actual, [object]$Expected, [string]$Description) {
    if ([string]$Actual -cne [string]$Expected) {
        throw "$Description mismatch: expected '$Expected', got '$Actual'."
    }
}

if ($SourceSha -cnotmatch '^[0-9a-f]{40}$') { throw "SourceSha must be a full lowercase Git SHA." }
if ($RunId -cnotmatch '^[a-z0-9][a-z0-9_-]{0,39}$') { throw "RunId has an invalid repository-name form." }
$metadataPath = Join-Path $ArtifactDir "images-ecr.json"
$valuesPath = Join-Path $ArtifactDir "values-images-eks.yaml"
if (-not (Test-Path -LiteralPath $metadataPath -PathType Leaf)) { throw "Missing images-ecr.json." }
if (-not (Test-Path -LiteralPath $valuesPath -PathType Leaf)) { throw "Missing values-images-eks.yaml." }
$metadata = Get-Content -LiteralPath $metadataPath -Raw | ConvertFrom-Json
$names = @("backend", "b5", "flink", "frontend")
Assert-Equal $metadata.schema_version 1 "Artifact schema version"
Assert-Equal $metadata.source_sha $SourceSha "Artifact source SHA"
Assert-Equal $metadata.run_id $RunId "Artifact run ID"
Assert-Equal $metadata.region $Region "Artifact region"
if ([string]$metadata.account_id -cnotmatch '^[0-9]{12}$') { throw "Invalid artifact AWS account ID." }
if ([string]$metadata.github_run_id -cnotmatch '^[0-9]+$') { throw "Invalid GitHub run ID." }
if ([string]$metadata.github_run_attempt -cnotmatch '^[0-9]+$') { throw "Invalid GitHub run attempt." }
if ([string]$metadata.workflow_sha -cnotmatch '^[0-9a-f]{40}$') { throw "Invalid workflow SHA." }
$expectedTag = "$($SourceSha.Substring(0, 12))-$RunId-gha$($metadata.github_run_id)-$($metadata.github_run_attempt)"
Assert-Equal $metadata.tag $expectedTag "Artifact tag"
Assert-Equal $metadata.github_run_url "https://github.com/mnihad000/rhetoriq/actions/runs/$($metadata.github_run_id)" "GitHub run URL"

$imageNames = @($metadata.images.PSObject.Properties.Name)
if ($imageNames.Count -ne 4 -or @($imageNames | Where-Object { $_ -cnotin $names }).Count -ne 0) {
    throw "Artifact must contain exactly backend, b5, flink, and frontend."
}

$repositoriesJson = & terraform -chdir=infra/terraform/eks-demo/foundation output -json ecr_repositories | Out-String
if ($LASTEXITCODE -ne 0) { throw "Could not read foundation ECR repository outputs." }
$repositories = $repositoriesJson | ConvertFrom-Json
$repositoryNames = @($repositories.PSObject.Properties.Name)
if ($repositoryNames.Count -ne 4 -or @($repositoryNames | Where-Object { $_ -cnotin $names }).Count -ne 0) {
    throw "Foundation output must contain exactly four ECR repositories."
}
$accountId = (& aws sts get-caller-identity --query Account --output text | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw "Could not verify AWS account identity." }
Assert-Equal $accountId $metadata.account_id "AWS account"

$expectedLines = [System.Collections.Generic.List[string]]::new()
$expectedLines.Add("images:")
foreach ($name in $names) {
    $image = $metadata.images.$name
    Assert-Equal $image.name $name "$name name"
    Assert-Equal $image.source_sha $SourceSha "$name source SHA"
    Assert-Equal $image.run_id $RunId "$name run ID"
    Assert-Equal $image.region $Region "$name region"
    Assert-Equal $image.account_id $accountId "$name account"
    Assert-Equal $image.tag $expectedTag "$name tag"
    Assert-Equal $image.platform "linux/amd64" "$name platform"
    Assert-Equal $image.identity_kind "ecr_manifest" "$name identity kind"
    Assert-Equal $image.github_run_id $metadata.github_run_id "$name GitHub run ID"
    Assert-Equal $image.github_run_attempt $metadata.github_run_attempt "$name GitHub run attempt"
    Assert-Equal $image.workflow_sha $metadata.workflow_sha "$name workflow SHA"
    if ([string]$image.digest -cnotmatch '^sha256:[0-9a-f]{64}$') { throw "Invalid $name ECR manifest digest." }
    if ([string]$image.build_image_id -cnotmatch '^sha256:[0-9a-f]{64}$') { throw "Invalid $name build image ID." }
    if ([string]$image.manifest_media_type -cnotin @(
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json"
    )) { throw "Invalid $name ECR manifest media type." }

    $repository = $repositories.$name
    $expectedRepository = "$accountId.dkr.ecr.$Region.amazonaws.com/rhetoriq-$RunId-$name"
    Assert-Equal $repository $expectedRepository "$name foundation repository"
    Assert-Equal $image.repository $repository "$name artifact repository"

    $detailJson = & aws ecr describe-images --region $Region --repository-name "rhetoriq-$RunId-$name" --image-ids "imageTag=$expectedTag" --output json | Out-String
    if ($LASTEXITCODE -ne 0) { throw "Could not query ECR image $name by tag." }
    $details = $detailJson | ConvertFrom-Json
    if (@($details.imageDetails).Count -ne 1) { throw "Expected one ECR image for $name tag." }
    Assert-Equal $details.imageDetails[0].imageDigest $image.digest "$name ECR manifest digest"
    Assert-Equal $details.imageDetails[0].imageManifestMediaType $image.manifest_media_type "$name ECR manifest media type"
    if ($expectedTag -cnotin @($details.imageDetails[0].imageTags)) { throw "ECR tag missing from $name image." }

    $expectedLines.Add("  ${name}:")
    $expectedLines.Add("    repository: $repository")
    $expectedLines.Add('    digest: "' + $image.digest + '"')
}
$expectedValues = $expectedLines -join "`n"
$actualValues = (Get-Content -LiteralPath $valuesPath -Raw).Replace("`r`n", "`n").TrimEnd("`n")
Assert-Equal $actualValues $expectedValues "Digest-only values file"

$outputPath = [IO.Path]::GetFullPath($Output)
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPath) | Out-Null
Copy-Item -LiteralPath $valuesPath -Destination $outputPath -Force
Write-Host "Verified four ECR manifest digests for $SourceSha and wrote $outputPath"
