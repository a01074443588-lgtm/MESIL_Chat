param(
    [Parameter(Mandatory = $true)]
    [string]$BackupPath
)

$ErrorActionPreference = "Stop"
$utf8 = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
chcp.com 65001 | Out-Null

$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (-not (Test-Path -LiteralPath $envPath)) {
    throw ".env is missing."
}
if (-not (Test-Path -LiteralPath $BackupPath)) {
    throw "Backup file was not found."
}
$resolvedBackup = (Resolve-Path -LiteralPath $BackupPath).Path

$settings = @{}
Get-Content -Encoding UTF8 -LiteralPath $envPath | ForEach-Object {
    if ($_ -match "^\s*([^#][^=]*)=(.*)$") {
        $settings[$matches[1].Trim()] = $matches[2].Trim()
    }
}
$user = if ($settings.POSTGRES_USER) { $settings.POSTGRES_USER } else { "smcodi_chat" }
$schema = if ($settings.DATABASE_SCHEMA) { $settings.DATABASE_SCHEMA } else { "smcodi" }
$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$testDatabase = "smcodi_restore_check_$timestamp"
$containerPath = "/tmp/smcodi_restore_check_$timestamp.dump"

Push-Location $projectRoot
try {
    docker compose up -d db
    if ($LASTEXITCODE -ne 0) { throw "Failed to start PostgreSQL." }
    $containerId = (docker compose ps -q db).Trim()
    if (-not $containerId) { throw "PostgreSQL container was not found." }

    docker exec $containerId createdb --username $user $testDatabase
    if ($LASTEXITCODE -ne 0) { throw "Failed to create an isolated restore database." }

    docker cp $resolvedBackup "${containerId}:$containerPath"
    if ($LASTEXITCODE -ne 0) { throw "Failed to copy the backup file." }

    docker exec $containerId pg_restore `
        --username $user `
        --dbname $testDatabase `
        --no-owner `
        $containerPath
    if ($LASTEXITCODE -ne 0) { throw "Isolated PostgreSQL restore failed." }

    $counts = docker exec $containerId psql `
        --username $user `
        --dbname $testDatabase `
        --tuples-only `
        --no-align `
        --command "SELECT 'staff=' || count(*) FROM $schema.staff UNION ALL SELECT 'rooms=' || count(*) FROM $schema.staff_hub_rooms UNION ALL SELECT 'messages=' || count(*) FROM $schema.staff_hub_messages;"
    if ($LASTEXITCODE -ne 0) { throw "Restored data verification failed." }
    $countLines = @($counts | Where-Object { $_ -match "^(staff|rooms|messages)=\d+$" })
    if ($countLines.Count -ne 3) {
        throw "Restored database did not contain the expected SMCODI_CHAT tables."
    }
    Write-Host "Isolated restore verification passed: $($countLines -join ', ')" -ForegroundColor Green
}
finally {
    if ($containerId) {
        docker exec $containerId rm -f $containerPath 2>$null | Out-Null
        docker exec $containerId dropdb --username $user --if-exists --force $testDatabase 2>$null | Out-Null
    }
    Pop-Location
}
