[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Workspace,
    [Parameter(Mandatory)][string]$StateBucket,
    [Parameter(Mandatory)][string]$BootstrapState,
    [Parameter(Mandatory)][string]$BootstrapVarFile,
    [Parameter(Mandatory)][string]$FoundationVarFile,
    [Parameter(Mandatory)][string]$PlatformVarFile,
    [string]$Region = "us-east-2",
    [string]$TaskName = "RhetoriQ-B6-EKS-Teardown",
    [switch]$EvidenceExported,
    [switch]$DeadlineGuard,
    [switch]$ApproveAwsDestruction
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
if (-not $ApproveAwsDestruction) { throw "Pass -ApproveAwsDestruction to destroy the ephemeral AWS environment." }
if (-not $EvidenceExported -and -not $DeadlineGuard) {
    throw "Export evidence first, then pass -EvidenceExported. The scheduled deadline uses -DeadlineGuard."
}
Set-Location -LiteralPath $Workspace

terraform -chdir=infra/terraform/eks-demo/foundation init -reconfigure `
    -backend-config="bucket=$StateBucket" -backend-config="key=foundation/terraform.tfstate" `
    -backend-config="region=$Region" -backend-config="encrypt=true" -backend-config="use_lockfile=true" | Out-Null
$cluster = terraform -chdir=infra/terraform/eks-demo/foundation output -raw cluster_name
$repositories = terraform -chdir=infra/terraform/eks-demo/foundation output -json ecr_repositories | ConvertFrom-Json
$hostedZoneId = (terraform -chdir=infra/terraform/eks-demo/foundation output -raw hosted_zone_id).Trim()
$publicHostname = (terraform -chdir=infra/terraform/eks-demo/foundation output -raw public_hostname).Trim().TrimEnd('.')
$runId = $cluster -replace '^rhetoriq-', ''
$context = "rhetoriq-$cluster"
$accountId = (aws sts get-caller-identity --output json | ConvertFrom-Json).Account
if (-not $accountId) { throw "AWS caller identity could not be verified for teardown." }
$auditDirectory = Join-Path "evidence/b6-runs" "$runId-teardown"
New-Item -ItemType Directory -Force -Path $auditDirectory | Out-Null
terraform -chdir=infra/terraform/eks-demo/foundation state pull | Set-Content -LiteralPath (Join-Path $auditDirectory "foundation-state-before-destroy.json")

try {
    aws eks update-kubeconfig --region $Region --name $cluster --alias $context | Out-Null
    $apiPod = (kubectl --context $context -n rhetoriq-demo get pod -l app.kubernetes.io/component=api -o jsonpath='{.items[0].metadata.name}' 2>$null).Trim()
    if ($apiPod) {
        kubectl --context $context -n rhetoriq-demo exec $apiPod -- python -m events.flink_savepoint --timeout-seconds 120
        $consumers = @(
            "outbox-publisher", "document-worker", "signal-worker", "investigation-worker",
            "projection-worker", "enrichment-worker", "elasticsearch-worker", "neo4j-worker", "minilm-worker"
        )
        kubectl --context $context -n rhetoriq-demo scale deployment $consumers --replicas=0 | Out-Null
    }
}
catch {
    Write-Warning "Controlled savepoint/scale-down did not complete; teardown continues to honor the deadline."
}

terraform -chdir=infra/terraform/eks-demo/platform init -reconfigure `
    -backend-config="bucket=$StateBucket" -backend-config="key=platform/terraform.tfstate" `
    -backend-config="region=$Region" -backend-config="encrypt=true" -backend-config="use_lockfile=true" | Out-Null
terraform -chdir=infra/terraform/eks-demo/platform state pull | Set-Content -LiteralPath (Join-Path $auditDirectory "platform-state-before-destroy.json")
terraform -chdir=infra/terraform/eks-demo/platform destroy -input=false -auto-approve `
    -var-file=$PlatformVarFile -var deploy_application=false
if ($LASTEXITCODE -ne 0) { throw "STOP: platform destruction failed; foundation was not touched." }

foreach ($property in $repositories.PSObject.Properties) {
    $repositoryName = $property.Value.Split('/')[-1]
    $images = aws ecr list-images --region $Region --repository-name $repositoryName --query 'imageIds' --output json | ConvertFrom-Json
    if ($images.Count -gt 0) {
        $imageJson = $images | ConvertTo-Json -Compress
        aws ecr batch-delete-image --region $Region --repository-name $repositoryName --image-ids $imageJson | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "ECR image deletion failed for $repositoryName" }
    }
}

terraform -chdir=infra/terraform/eks-demo/foundation destroy -input=false -auto-approve `
    -var-file=$FoundationVarFile -var network_policy_enforcing_mode=strict
if ($LASTEXITCODE -ne 0) { throw "Foundation destruction failed." }

function Invoke-AwsJson([string[]]$Arguments) {
    $raw = & aws @Arguments --output json
    if ($LASTEXITCODE -ne 0) { throw "AWS residual query failed: aws $($Arguments -join ' ')" }
    return $raw | ConvertFrom-Json
}

$roles = @(Invoke-AwsJson -Arguments @("iam", "list-roles", "--query", "Roles[?starts_with(RoleName, 'rhetoriq-$runId')].RoleName"))
$clusters = @((Invoke-AwsJson -Arguments @("eks", "list-clusters", "--region", $Region)).clusters | Where-Object { $_ -eq $cluster })
$volumes = @((Invoke-AwsJson -Arguments @("ec2", "describe-volumes", "--region", $Region, "--filters", "Name=tag:run-id,Values=$runId")).Volumes | ForEach-Object VolumeId)
$addresses = @((Invoke-AwsJson -Arguments @("ec2", "describe-addresses", "--region", $Region, "--filters", "Name=tag:run-id,Values=$runId")).Addresses | ForEach-Object AllocationId)
$vpcs = @((Invoke-AwsJson -Arguments @("ec2", "describe-vpcs", "--region", $Region, "--filters", "Name=tag:run-id,Values=$runId")).Vpcs | ForEach-Object VpcId)
$loadBalancerName = ("rhetoriq-$cluster").Substring(0, [Math]::Min(32, ("rhetoriq-$cluster").Length))
$loadBalancers = @((Invoke-AwsJson -Arguments @("elbv2", "describe-load-balancers", "--region", $Region)).LoadBalancers | Where-Object LoadBalancerName -eq $loadBalancerName | ForEach-Object LoadBalancerArn)
$repositoryNames = @($repositories.PSObject.Properties | ForEach-Object { $_.Value.Split('/')[-1] })
$ecrRepositories = @((Invoke-AwsJson -Arguments @("ecr", "describe-repositories", "--region", $Region)).repositories | Where-Object { $_.repositoryName -in $repositoryNames } | ForEach-Object repositoryName)
$logGroups = @((Invoke-AwsJson -Arguments @("logs", "describe-log-groups", "--region", $Region, "--log-group-name-prefix", "/aws/eks/$cluster/")).logGroups | ForEach-Object logGroupName)
$budgets = @((Invoke-AwsJson -Arguments @("budgets", "describe-budgets", "--account-id", $accountId)).Budgets | Where-Object BudgetName -eq "$cluster-25-usd" | ForEach-Object BudgetName)
$tagged = @((Invoke-AwsJson -Arguments @("resourcegroupstaggingapi", "get-resources", "--region", $Region, "--tag-filters", "Key=run-id,Values=$runId")).ResourceTagMappingList | ForEach-Object ResourceARN)

$certificates = @()
foreach ($certificate in @((Invoke-AwsJson -Arguments @("acm", "list-certificates", "--region", $Region)).CertificateSummaryList)) {
    $certificateTags = (Invoke-AwsJson -Arguments @("acm", "list-tags-for-certificate", "--region", $Region, "--certificate-arn", $certificate.CertificateArn)).Tags
    if ($certificateTags | Where-Object { $_.Key -eq "run-id" -and $_.Value -eq $runId }) { $certificates += $certificate.CertificateArn }
}
$topics = @()
foreach ($topic in @((Invoke-AwsJson -Arguments @("sns", "list-topics", "--region", $Region)).Topics)) {
    $topicTags = (Invoke-AwsJson -Arguments @("sns", "list-tags-for-resource", "--region", $Region, "--resource-arn", $topic.TopicArn)).Tags
    if ($topicTags | Where-Object { $_.Key -eq "run-id" -and $_.Value -eq $runId }) { $topics += $topic.TopicArn }
}
$route53Records = @()
if ($hostedZoneId -and $publicHostname) {
    $recordSets = (Invoke-AwsJson -Arguments @("route53", "list-resource-record-sets", "--hosted-zone-id", $hostedZoneId)).ResourceRecordSets
    $route53Records = @($recordSets | Where-Object {
        $name = $_.Name.TrimEnd('.')
        $name -eq $publicHostname -or ($_.Type -eq "CNAME" -and $name.EndsWith(".$publicHostname"))
    } | ForEach-Object { "$($_.Type) $($_.Name)" })
}

$residual = [ordered]@{
    eksClusters = $clusters
    ebsVolumes = $volumes
    elasticIps = $addresses
    loadBalancers = $loadBalancers
    ecrRepositories = $ecrRepositories
    vpcs = $vpcs
    iamRoles = $roles
    acmCertificates = $certificates
    route53Records = $route53Records
    budgets = $budgets
    snsTopics = $topics
    cloudWatchLogGroups = $logGroups
    taggedResources = $tagged
}
$residualPath = Join-Path $auditDirectory "residual-inventory.json"
$residual | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $residualPath
$remaining = @($residual.GetEnumerator() | Where-Object { @($_.Value).Count -gt 0 })
if ($remaining.Count -gt 0) {
    throw "Residual AWS inventory is not empty. Retaining the bootstrap state bucket; inspect $residualPath."
}

terraform -chdir=infra/terraform/eks-demo/bootstrap init | Out-Null
Copy-Item -LiteralPath $BootstrapState -Destination (Join-Path $auditDirectory "bootstrap-state-before-destroy.tfstate") -Force
terraform -chdir=infra/terraform/eks-demo/bootstrap destroy -input=false -auto-approve `
    -state=$BootstrapState -var-file=$BootstrapVarFile
if ($LASTEXITCODE -ne 0) { throw "Bootstrap state bucket destruction failed." }

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
Write-Host "Teardown completed with an empty explicit residual inventory. Audit files are under $auditDirectory."
