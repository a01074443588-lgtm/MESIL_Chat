$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot "data\runtime"
$pidPath = Join-Path $runtimeDirectory "cloudflared.pid"
$address = "https://chat.silvermedical.kr/api/health"

$running = $false
$tunnelPid = $null
if (Test-Path -LiteralPath $pidPath) {
    $tunnelPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($tunnelPid -match "^\d+$" -and (Get-Process -Id $tunnelPid -ErrorAction SilentlyContinue)) {
        $running = $true
    }
}

$connectionStatus = if ($running) {
    "running (PID $tunnelPid)"
}
else {
    "stopped"
}
Write-Host "Tunnel process: $connectionStatus"
try {
    $health = Invoke-RestMethod -Uri $address -TimeoutSec 15
    if ($health.status -eq "ok") {
        Write-Host "Public URL: healthy" -ForegroundColor Green
        Write-Host "URL: https://chat.silvermedical.kr"
        exit 0
    }
    throw "Unexpected health response."
}
catch {
    Write-Host "Public URL: unavailable" -ForegroundColor Yellow
    exit 1
}
