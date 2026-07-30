$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$runtimeDirectory = Join-Path $projectRoot "data\runtime"
$cloudflaredPath = Join-Path $runtimeDirectory "cloudflared.exe"
$pidPath = Join-Path $runtimeDirectory "cloudflared.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "No running public tunnel was recorded."
    exit 0
}

$tunnelPid = (Get-Content -LiteralPath $pidPath -Raw).Trim()
if ($tunnelPid -notmatch "^\d+$") {
    throw "The public tunnel PID file is invalid: $pidPath"
}

$process = Get-Process -Id $tunnelPid -ErrorAction SilentlyContinue
if ($process) {
    $actualPath = $process.Path
    if (-not $actualPath -or (Resolve-Path -LiteralPath $actualPath).Path -ne (Resolve-Path -LiteralPath $cloudflaredPath).Path) {
        throw "The recorded PID is not cloudflared; no process was stopped."
    }
    Stop-Process -Id $tunnelPid
    $process.WaitForExit(10000)
}
Remove-Item -LiteralPath $pidPath -Force
Write-Host "Public tunnel stopped." -ForegroundColor Green
