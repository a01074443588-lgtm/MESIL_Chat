param(
    [string]$EnvPath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
$OutputEncoding = [Console]::OutputEncoding

$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $EnvPath) {
    $EnvPath = Join-Path $projectRoot ".env"
}
if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "Release safety check failed: environment file is missing."
}
$resolvedEnvPath = (Resolve-Path -LiteralPath $EnvPath).Path

$values = @{}
Get-Content -Encoding UTF8 -LiteralPath $resolvedEnvPath | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        $values[$matches[1].Trim()] = $matches[2].Trim()
    }
}

if ($values["DEV_LAUNCHER_ENABLED"] -match "^(?i:true|1|yes|on)$") {
    throw "Release safety check failed: set DEV_LAUNCHER_ENABLED=false before submission."
}

if ($values["ENVIRONMENT"] -ne "production") {
    Write-Host "Submission safety check passed: developer launcher is disabled." -ForegroundColor Green
    exit 0
}
if ($values["COOKIE_SECURE"] -ne "true") {
    throw "Release safety check failed: production requires COOKIE_SECURE=true."
}
if ($values["TRUST_PROXY_HEADERS"] -ne "true") {
    throw "Release safety check failed: production requires TRUST_PROXY_HEADERS=true."
}

$allowedOrigins = if ($values["CHAT_ORIGINS"]) {
    $values["CHAT_ORIGINS"]
} else {
    $values["ALLOWED_ORIGINS"]
}
if ($allowedOrigins -ne "https://chat.silvermedical.kr") {
    throw "Release safety check failed: CHAT_ORIGINS must be https://chat.silvermedical.kr."
}

function Assert-StrongSecret([string]$Key, [int]$MinimumLength) {
    $value = $values[$Key]
    if (
        -not $value -or
        $value.Length -lt $MinimumLength -or
        $value -match "(?i:change[_-]?me|change-this|반드시|password)"
    ) {
        throw "Release safety check failed: $Key must be replaced with a strong secret."
    }
}

Assert-StrongSecret "POSTGRES_PASSWORD" 24
if ($values["BOOTSTRAP_ADMIN_USERNAME"]) {
    Assert-StrongSecret "BOOTSTRAP_ADMIN_PASSWORD" 16
}
if ($values["STT_ENABLED"] -match "^(?i:true|1|yes|on)$") {
    Assert-StrongSecret "STT_SHARED_TOKEN" 24
}

Write-Host "Release safety check passed for chat.silvermedical.kr." -ForegroundColor Green
