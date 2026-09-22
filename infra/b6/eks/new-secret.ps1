[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Context,
    [switch]$ApproveSecretUpload
)

$ErrorActionPreference = "Stop"
if (-not $ApproveSecretUpload) { throw "Pass -ApproveSecretUpload only after separately approving EKS secret upload." }
& "$PSScriptRoot/../kind/new-secret.ps1" -Context $Context -ApproveSecretUpload

