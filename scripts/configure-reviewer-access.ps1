param(
    [Parameter(Mandatory = $true)]
    [ValidateSet("Enable", "Disable")]
    [string]$Mode,

    [string]$EnvPath = "",
    [string]$AccessEndsAt = "2026-08-31T23:59:59+09:00",
    [string]$CareUsername,
    [string]$SocialUsername,
    [string]$SecondaryUsername,
    [string]$ChatRoomName = "시설 전체방"
)

$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($EnvPath)) {
    $EnvPath = Join-Path (Split-Path -Parent $PSScriptRoot) ".env"
}

if (-not (Test-Path -LiteralPath $EnvPath)) {
    throw "환경설정 파일을 찾을 수 없습니다: $EnvPath"
}

if ($Mode -eq "Enable") {
    foreach ($requiredValue in @{
        CareUsername = $CareUsername
        SocialUsername = $SocialUsername
        SecondaryUsername = $SecondaryUsername
    }.GetEnumerator()) {
        if ([string]::IsNullOrWhiteSpace($requiredValue.Value)) {
            throw "$($requiredValue.Key) 값을 입력해 주세요."
        }
    }

    $parsedEnd = [DateTimeOffset]::MinValue
    if (-not [DateTimeOffset]::TryParse($AccessEndsAt, [ref]$parsedEnd)) {
        throw "AccessEndsAt은 시간대가 포함된 날짜여야 합니다."
    }
}

$lines = [System.Collections.Generic.List[string]]::new()
Get-Content -LiteralPath $EnvPath -Encoding UTF8 | ForEach-Object {
    $lines.Add($_)
}

function Set-EnvValue {
    param(
        [System.Collections.Generic.List[string]]$Content,
        [string]$Key,
        [string]$Value
    )

    $prefix = "$Key="
    for ($index = 0; $index -lt $Content.Count; $index++) {
        if ($Content[$index].StartsWith($prefix, [StringComparison]::Ordinal)) {
            $Content[$index] = "$prefix$Value"
            return
        }
    }
    $Content.Add("$prefix$Value")
}

Set-EnvValue $lines "REVIEWER_ACCESS_ENABLED" ($Mode -eq "Enable").ToString().ToLowerInvariant()

if ($Mode -eq "Enable") {
    Set-EnvValue $lines "REVIEWER_ACCESS_ENDS_AT" $AccessEndsAt
    Set-EnvValue $lines "REVIEWER_SESSION_MINUTES" "45"
    Set-EnvValue $lines "REVIEWER_CARE_USERNAME" $CareUsername.Trim()
    Set-EnvValue $lines "REVIEWER_SOCIAL_USERNAME" $SocialUsername.Trim()
    Set-EnvValue $lines "REVIEWER_SECONDARY_USERNAME" $SecondaryUsername.Trim()
    Set-EnvValue $lines "REVIEWER_CHAT_ROOM_NAME" $ChatRoomName.Trim()
    Set-EnvValue $lines "REVIEWER_RATE_LIMIT" "8"
    Set-EnvValue $lines "REVIEWER_RATE_WINDOW_MINUTES" "1"
    Set-EnvValue $lines "REVIEWER_SESSION_LIMIT_PER_CLIENT" "4"

    $existingSecret = $lines |
        Where-Object { $_.StartsWith("REVIEWER_SESSION_SECRET=", [StringComparison]::Ordinal) } |
        Select-Object -First 1
    $secretValue = if ($existingSecret) {
        $existingSecret.Substring("REVIEWER_SESSION_SECRET=".Length).Trim()
    } else {
        ""
    }
    if ($secretValue.Length -lt 32 -or $secretValue -match "CHANGE_ME|replace") {
        $randomBytes = [byte[]]::new(48)
        $randomNumberGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
        try {
            $randomNumberGenerator.GetBytes($randomBytes)
        } finally {
            $randomNumberGenerator.Dispose()
        }
        $secretValue = [Convert]::ToBase64String($randomBytes)
        Set-EnvValue $lines "REVIEWER_SESSION_SECRET" $secretValue
    }
}

$utf8WithoutBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText(
    (Resolve-Path -LiteralPath $EnvPath),
    (($lines -join [Environment]::NewLine) + [Environment]::NewLine),
    $utf8WithoutBom
)

if ($Mode -eq "Enable") {
    Write-Host "심사위원 체험을 켰습니다. 종료시각: $AccessEndsAt" -ForegroundColor Green
} else {
    Write-Host "심사위원 체험을 껐습니다. 기존 체험 세션은 다음 요청부터 차단됩니다." -ForegroundColor Yellow
}
Write-Host "설정을 반영하려면 backend 컨테이너를 재생성해 주세요."
