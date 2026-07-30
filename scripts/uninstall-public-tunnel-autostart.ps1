param(
    [string]$TaskName = "SMCODI Chat Public Tunnel"
)

$ErrorActionPreference = "Stop"
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if (-not $task) {
    Write-Host "Public tunnel auto-start is not registered."
    exit 0
}

Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "Public tunnel auto-start was removed." -ForegroundColor Green
Write-Host "Task: $TaskName"
