$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env가 없습니다. .env.example을 복사하고 PostgreSQL 비밀번호를 설정하세요."
}

Get-Content -Encoding UTF8 -LiteralPath $envPath | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        [Environment]::SetEnvironmentVariable(
            $matches[1].Trim(),
            $matches[2].Trim(),
            "Process"
        )
    }
}

if (-not $env:POSTGRES_PASSWORD) {
    throw ".env의 POSTGRES_PASSWORD를 설정하세요."
}

Push-Location $projectRoot
try {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # Docker Desktop can write normal container status messages to stderr.
        # Judge the native command by its exit code instead of treating that text
        # as a terminating PowerShell error.
        $ErrorActionPreference = "Continue"
        docker compose up -d db
        $dockerExitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($dockerExitCode -ne 0) { throw "PostgreSQL 시작에 실패했습니다." }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $projectRoot "backend")
try {
    uv sync --extra test
    if ($LASTEXITCODE -ne 0) { throw "Backend dependency setup failed." }
    uv run pytest
    if ($LASTEXITCODE -ne 0) { throw "Backend tests failed." }
    uv run alembic current
    if ($LASTEXITCODE -ne 0) { throw "Database migration status check failed." }
    uv run alembic check
    if ($LASTEXITCODE -ne 0) { throw "Database models and migrations differ." }
    uv run python -m compileall -q app migrations ..\scripts
    if ($LASTEXITCODE -ne 0) { throw "Backend syntax check failed." }
}
finally {
    Pop-Location
}

Push-Location (Join-Path $projectRoot "frontend")
try {
    npm install --ignore-scripts --no-audit --no-fund
    if ($LASTEXITCODE -ne 0) { throw "Frontend dependency setup failed." }
    npm run build
    if ($LASTEXITCODE -ne 0) { throw "Frontend build failed." }
    npm test
    if ($LASTEXITCODE -ne 0) { throw "Frontend rendered-output tests failed." }
    npm run lint
    if ($LASTEXITCODE -ne 0) { throw "Frontend lint failed." }
}
finally {
    Pop-Location
}

Write-Host "PostgreSQL migration, backend tests, and frontend validation passed." -ForegroundColor Green
