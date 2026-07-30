param(
    [string]$TaskName = "SMCODI Chat Public Tunnel"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $projectRoot "scripts\start-public-tunnel.ps1"

if (-not (Test-Path -LiteralPath $startScript)) {
    throw "Public tunnel start script was not found: $startScript"
}

$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$quotedScript = '"' + $startScript + '"'
$action = New-ScheduledTaskAction `
    -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File $quotedScript" `
    -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
$trigger.Delay = "PT1M"
$principal = New-ScheduledTaskPrincipal `
    -UserId $currentUser `
    -LogonType Interactive `
    -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 2)

Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description "Starts the SMCODI chat containers and Cloudflare Tunnel after Windows sign-in." `
    -Force | Out-Null

Write-Host "Public tunnel auto-start was registered." -ForegroundColor Green
Write-Host "Task: $TaskName"
Write-Host "User: $currentUser"
