param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath,
    [switch]$ConfirmDatabaseReset
)

$ErrorActionPreference = "Stop"
$utf8 = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
chcp.com 65001 | Out-Null
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path

if (-not $ConfirmDatabaseReset) {
    throw "Restore overwrites the current SMCODI_CHAT schema. Add -ConfirmDatabaseReset."
}
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env is missing."
}

$settings = @{}
Get-Content -Encoding UTF8 -LiteralPath $envPath | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        $settings[$matches[1].Trim()] = $matches[2].Trim()
    }
}
$database = if ($settings.POSTGRES_DB) { $settings.POSTGRES_DB } else { "smcodi_chat" }
$user = if ($settings.POSTGRES_USER) { $settings.POSTGRES_USER } else { "smcodi_chat" }
$schema = if ($settings.DATABASE_SCHEMA) { $settings.DATABASE_SCHEMA } else { "smcodi" }
$containerPath = "/tmp/smcodi_chat_restore.dump"

Push-Location $projectRoot
try {
    docker compose up -d db
    if ($LASTEXITCODE -ne 0) { throw "Failed to start PostgreSQL." }
    docker compose stop backend 2>$null
    $containerId = (docker compose ps -q db).Trim()
    if (-not $containerId) { throw "PostgreSQL container was not found." }
    docker cp $resolvedBackup "${containerId}:$containerPath"
    if ($LASTEXITCODE -ne 0) { throw "Failed to copy the restore file." }
    docker exec $containerId pg_restore `
        --username $user `
        --dbname $database `
        --schema $schema `
        --clean `
        --if-exists `
        --no-owner `
        $containerPath
    if ($LASTEXITCODE -ne 0) { throw "PostgreSQL restore failed." }
    docker exec $containerId rm -f $containerPath
}
finally {
    Pop-Location
}

Write-Host "Database restore completed." -ForegroundColor Green
Write-Host "Restore data/uploads from the same backup point separately."
