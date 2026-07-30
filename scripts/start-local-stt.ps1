$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$pidPath = Join-Path $projectRoot "data\local-stt.pid"
$stdoutPath = Join-Path $projectRoot "data\local-stt.stdout.log"
$stderrPath = Join-Path $projectRoot "data\local-stt.stderr.log"
$serviceScript = Join-Path $PSScriptRoot "local_stt_service.py"

if (-not (Test-Path -LiteralPath $envPath)) {
    throw "Local STT start failed: .env is missing."
}

Get-Content -LiteralPath $envPath -Encoding utf8 | ForEach-Object {
    if ($_ -match "^[A-Za-z_][A-Za-z0-9_]*=") {
        $parts = $_.Split("=", 2)
        [Environment]::SetEnvironmentVariable($parts[0], $parts[1], "Process")
    }
}

$pythonPath = $env:STT_LOCAL_PYTHON_PATH
$modelPath = $env:STT_LOCAL_MODEL_PATH
$port = if ($env:STT_LOCAL_PORT) { $env:STT_LOCAL_PORT } else { "8766" }

if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Local STT start failed: STT_LOCAL_PYTHON_PATH is invalid: $pythonPath"
}
if (-not (Test-Path -LiteralPath $modelPath -PathType Container)) {
    throw "Local STT start failed: STT_LOCAL_MODEL_PATH is invalid: $modelPath"
}
if (-not $env:STT_SHARED_TOKEN) {
    throw "Local STT start failed: STT_SHARED_TOKEN is missing."
}

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
    if ($health.ok) {
        Write-Host "Local speech-to-text service is already running." -ForegroundColor Green
        Write-Host "Model: $($health.model) / GPU: $($health.cuda)"
        exit 0
    }
}
catch {
    # 실행 중이 아니면 아래에서 시작합니다.
}

$process = Start-Process `
    -FilePath $pythonPath `
    -ArgumentList @($serviceScript) `
    -WorkingDirectory $projectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutPath `
    -RedirectStandardError $stderrPath `
    -PassThru

Set-Content -LiteralPath $pidPath -Value $process.Id -Encoding ascii

for ($attempt = 0; $attempt -lt 90; $attempt += 1) {
    Start-Sleep -Seconds 1
    if ($process.HasExited) {
        throw "Local STT service exited while starting."
    }
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 2
        if ($health.ok -and $health.loaded) {
            Write-Host "Local speech-to-text service started." -ForegroundColor Green
            Write-Host "Model: $($health.model) / GPU: $($health.cuda) / Port: $port"
            exit 0
        }
    }
    catch {
        # 모델을 메모리에 올리는 동안 기다립니다.
    }
}

throw "Local STT service was not ready within 90 seconds."
