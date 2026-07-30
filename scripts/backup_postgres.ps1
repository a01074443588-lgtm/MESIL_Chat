param(
    [string]$OutputDirectory,
    [switch]$IncludeAttachments
)

$ErrorActionPreference = "Stop"
$utf8 = [System.Text.UTF8Encoding]::new($false)
$OutputEncoding = $utf8
[Console]::InputEncoding = $utf8
[Console]::OutputEncoding = $utf8
chcp.com 65001 | Out-Null
$projectRoot = Split-Path -Parent $PSScriptRoot
$envPath = Join-Path $projectRoot ".env"
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $projectRoot "data\backups"
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

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$backupPath = Join-Path $OutputDirectory "smcodi_chat_postgres_$timestamp.dump"
$manifestPath = Join-Path $OutputDirectory "smcodi_chat_postgres_$timestamp.json"
$attachmentsPath = Join-Path $OutputDirectory "smcodi_chat_attachments_$timestamp.zip"
$containerPath = "/tmp/smcodi_chat_$timestamp.dump"

Push-Location $projectRoot
try {
    docker compose up -d db
    if ($LASTEXITCODE -ne 0) { throw "Failed to start PostgreSQL." }
    $containerId = (docker compose ps -q db).Trim()
    if (-not $containerId) { throw "PostgreSQL container was not found." }
    docker exec $containerId pg_dump `
        --username $user `
        --dbname $database `
        --schema $schema `
        --format custom `
        --file $containerPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create PostgreSQL backup." }
    docker cp "${containerId}:$containerPath" $backupPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to copy the backup file." }
    docker exec $containerId rm -f $containerPath
}
finally {
    Pop-Location
}

$attachmentsArchive = $null
if ($IncludeAttachments) {
    $uploadDirectory = Join-Path $projectRoot "data\uploads"
    $uploadFiles = @(
        Get-ChildItem -LiteralPath $uploadDirectory -File -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne ".gitkeep" }
    )
    if ($uploadFiles.Count -gt 0) {
        Compress-Archive -Path (Join-Path $uploadDirectory "*") -DestinationPath $attachmentsPath
        $attachmentsArchive = Split-Path -Leaf $attachmentsPath
    }
}

$manifest = [ordered]@{
    created_at = (Get-Date).ToString("o")
    database = $database
    schema = $schema
    format = "PostgreSQL custom"
    database_backup = (Split-Path -Leaf $backupPath)
    attachments_archive = $attachmentsArchive
    note = if ($IncludeAttachments) {
        "Database and current attachments were archived at the same backup point."
    } else {
        "Database only. Prototype chat photos were intentionally not archived."
    }
}
$manifest | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath $manifestPath

Write-Host "Database backup: $backupPath" -ForegroundColor Green
Write-Host "Backup manifest: $manifestPath"
if ($attachmentsArchive) {
    Write-Host "Attachment archive: $attachmentsPath"
} else {
    Write-Host "Attachments skipped. Add -IncludeAttachments when they must be moved."
}
