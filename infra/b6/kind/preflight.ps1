[CmdletBinding()]
param(
    [string]$Context = "kind-rhetoriq-b6",
    [string]$NodeContainer = "rhetoriq-b6-control-plane"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

foreach ($command in @("docker", "kind", "kubectl", "helm")) {
    if (-not (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "Required command is not installed or not on PATH: $command"
    }
}

$actualContext = (kubectl config current-context).Trim()
if ($actualContext -ne $Context) {
    throw "Refusing to continue: current context is '$actualContext', expected '$Context'."
}

kubectl --context $Context wait --for=condition=Ready node --all --timeout=30s | Out-Null
$storageClass = kubectl --context $Context get storageclass standard -o json | ConvertFrom-Json
if ($storageClass.provisioner -ne "rancher.io/local-path") {
    throw "The kind profile expects the standard local-path StorageClass."
}

$mapCount = [int64](docker exec $NodeContainer sysctl -n vm.max_map_count).Trim()
$dockerBytes = [int64](docker info --format '{{.MemTotal}}')
$node = kubectl --context $Context get node -o json | ConvertFrom-Json
$driveName = ([IO.Path]::GetPathRoot((Get-Location).Path)).TrimEnd('\').TrimEnd(':')
$hostFreeBytes = [int64](Get-PSDrive -Name $driveName).Free
$summary = [ordered]@{
    context = $actualContext
    node = $node.items[0].metadata.name
    kubernetesVersion = $node.items[0].status.nodeInfo.kubeletVersion
    allocatableCpu = $node.items[0].status.allocatable.cpu
    allocatableMemory = $node.items[0].status.allocatable.memory
    dockerMemoryGiB = [math]::Round($dockerBytes / 1GB, 2)
    hostFreeDiskGiB = [math]::Round($hostFreeBytes / 1GB, 2)
    vmMaxMapCount = $mapCount
    storageProvisioner = $storageClass.provisioner
}
$summary | ConvertTo-Json

if ($mapCount -lt 1048576) {
    throw "STOP: vm.max_map_count is $mapCount; the full topology requires 1048576. This script does not change it."
}
if ($hostFreeBytes -lt 20GB) {
    throw "STOP: the workspace drive has less than 20 GiB free for four image builds and seven demo PVCs."
}
