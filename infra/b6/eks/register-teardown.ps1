[CmdletBinding()]
param(
    [Parameter(Mandatory)][datetime]$ExpiresAt,
    [Parameter(Mandatory)][string]$StateBucket,
    [Parameter(Mandatory)][string]$BootstrapState,
    [Parameter(Mandatory)][string]$BootstrapVarFile,
    [Parameter(Mandatory)][string]$FoundationVarFile,
    [Parameter(Mandatory)][string]$PlatformVarFile,
    [string]$Region = "us-east-2",
    [string]$TaskName = "RhetoriQ-B6-EKS-Teardown"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$now = (Get-Date).ToUniversalTime()
$expiry = $ExpiresAt.ToUniversalTime()
if ($expiry -le $now -or $expiry -gt $now.AddHours(8)) {
    throw "The teardown deadline must be in the future and no more than eight hours away."
}

$teardown = (Resolve-Path "$PSScriptRoot/teardown.ps1").Path
$bootstrapStatePath = [IO.Path]::GetFullPath($BootstrapState)
$workspace = (Get-Location).Path
$arguments = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ('"' + $teardown + '"'),
    "-Workspace", ('"' + $workspace + '"'),
    "-StateBucket", ('"' + $StateBucket + '"'),
    "-BootstrapState", ('"' + $bootstrapStatePath + '"'),
    "-BootstrapVarFile", ('"' + (Resolve-Path $BootstrapVarFile).Path + '"'),
    "-FoundationVarFile", ('"' + (Resolve-Path $FoundationVarFile).Path + '"'),
    "-PlatformVarFile", ('"' + (Resolve-Path $PlatformVarFile).Path + '"'),
    "-Region", ('"' + $Region + '"'),
    "-ApproveAwsDestruction", "-DeadlineGuard"
) -join " "
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments
$trigger = New-ScheduledTaskTrigger -Once -At $expiry.ToLocalTime()
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
$task = Get-ScheduledTask -TaskName $TaskName
if ($task.State -eq "Disabled") { throw "The teardown scheduled task was registered but is disabled." }
Write-Host "Registered teardown deadline $($expiry.ToString('o')) as scheduled task $TaskName."
