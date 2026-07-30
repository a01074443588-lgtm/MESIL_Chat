$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$pidPath = Join-Path $projectRoot "data\local-stt.pid"

if (-not (Test-Path -LiteralPath $pidPath)) {
    Write-Host "No saved local STT process was found."
    exit 0
}

$processId = [int](Get-Content -LiteralPath $pidPath -Raw -Encoding ascii)
$process = Get-Process -Id $processId -ErrorAction SilentlyContinue
if ($null -ne $process) {
    Stop-Process -Id $processId
    Write-Host "Local speech-to-text service stopped." -ForegroundColor Green
}
else {
    Write-Host "Local speech-to-text service was already stopped."
}
Remove-Item -LiteralPath $pidPath -Force
