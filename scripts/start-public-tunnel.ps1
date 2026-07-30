param(
    [string]$TokenFile
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot "data\runtime"
$cloudflaredPath = Join-Path $runtimeDirectory "cloudflared.exe"
$pidPath = Join-Path $runtimeDirectory "cloudflared.pid"
$logPath = Join-Path $runtimeDirectory "cloudflared.log"
$configPath = Join-Path $runtimeDirectory "cloudflared.yml"
if (-not $TokenFile) {
    $TokenFile = Join-Path $runtimeDirectory "cloudflared.token"
}

if (-not (Test-Path -LiteralPath $cloudflaredPath)) {
    throw "cloudflared was not found: $cloudflaredPath"
}
if (-not (Test-Path -LiteralPath $configPath) -and -not (Test-Path -LiteralPath $TokenFile)) {
    throw "Cloudflare tunnel config or token file was not found."
}
if (Test-Path -LiteralPath $pidPath) {
    $existingPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
    if ($existingPid -match "^\d+$" -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Public tunnel is already running. PID: $existingPid" -ForegroundColor Green
        exit 0
    }
    Remove-Item -LiteralPath $pidPath -Force
}

Push-Location $projectRoot
try {
    docker compose up -d db backend frontend gateway
    if ($LASTEXITCODE -ne 0) {
        throw "SMCODI chat services failed to start."
    }
}
finally {
    Pop-Location
}

$arguments = if (Test-Path -LiteralPath $configPath) {
    @(
        "tunnel",
        "--config", $configPath,
        "--no-autoupdate",
        "--loglevel", "info",
        "--logfile", $logPath,
        "--pidfile", $pidPath,
        "run"
    )
}
else {
    @(
        "tunnel",
        "--no-autoupdate",
        "--loglevel", "info",
        "--logfile", $logPath,
        "--pidfile", $pidPath,
        "run",
        "--token-file", $TokenFile
    )
}
Start-Process `
    -FilePath $cloudflaredPath `
    -ArgumentList $arguments `
    -WorkingDirectory $runtimeDirectory `
    -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds(20)
do {
    Start-Sleep -Milliseconds 500
    if (Test-Path -LiteralPath $pidPath) {
        $startedPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
        if ($startedPid -match "^\d+$" -and (Get-Process -Id $startedPid -ErrorAction SilentlyContinue)) {
            Write-Host "Public tunnel started. PID: $startedPid" -ForegroundColor Green
            Write-Host "URL: https://chat.silvermedical.kr"
            exit 0
        }
    }
} while ((Get-Date) -lt $deadline)

throw "Cloudflare tunnel did not connect before timeout. Check: $logPath"
