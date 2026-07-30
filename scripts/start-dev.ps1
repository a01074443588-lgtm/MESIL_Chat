$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$backendPath = Join-Path $projectRoot "backend"
$frontendPath = Join-Path $projectRoot "frontend"
$envPath = Join-Path $projectRoot ".env"
$envExamplePath = Join-Path $projectRoot ".env.example"

if (-not (Test-Path -LiteralPath $envPath)) {
    Copy-Item -LiteralPath $envExamplePath -Destination $envPath
    Write-Host "Created .env. Change the administrator password, then run this script again." -ForegroundColor Yellow
    exit 1
}

$envText = Get-Content -Raw -Encoding UTF8 -LiteralPath $envPath
if ($envText -notmatch "(?m)^POSTGRES_PASSWORD=.+$" -or $envText -match "반드시-새로운") {
    Write-Host ".env의 POSTGRES_PASSWORD를 긴 개발용 비밀번호로 설정해 주세요." -ForegroundColor Yellow
    exit 1
}

if ($envText -match "(?im)^STT_ENABLED=(true|1|yes|on)\s*$") {
    & (Join-Path $PSScriptRoot "start-local-stt.ps1")
    if ($LASTEXITCODE -ne 0) { throw "로컬 음성 판독 서비스 시작에 실패했습니다." }
}

Push-Location $projectRoot
try {
    docker compose up -d db
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL 시작에 실패했습니다." }
}
finally {
    Pop-Location
}

$backendCommand = "Set-Location -LiteralPath '$backendPath'; uv sync --extra test; uv run alembic upgrade head; uv run uvicorn app.main:app --host 0.0.0.0 --port 8000"
$frontendCommand = "Set-Location -LiteralPath '$frontendPath'; npm install --ignore-scripts --no-audit --no-fund; npm run dev -- --host 0.0.0.0 --port 3100"

Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    $backendCommand
)
Start-Process -FilePath "powershell.exe" -ArgumentList @(
    "-NoExit",
    "-NoProfile",
    "-Command",
    $frontendCommand
)

Write-Host "SMCODI Chat development servers started." -ForegroundColor Green
Write-Host "App: http://localhost:3100"
Write-Host "Health: http://localhost:8000/api/health"
Write-Host "Close both PowerShell windows to stop the servers."
