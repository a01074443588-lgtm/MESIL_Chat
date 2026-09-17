# 공개 소스의 로컬 설치

이 패키지는 2026년 9월 18일 공개 실행 이미지의 Backend·Frontend 소스와 실행 중인 STT 소스를 반영한 제출 기준본 A입니다. 운영 DB·첨부·계정·비밀키를 포함하지 않습니다. 이전 공개본의 로컬 설치 구성은 유지했으며, 이번 소스 패키지 전체에 대해 새 DB 설치와 전체 회귀시험을 다시 실행한 것은 아닙니다. 실제 실행 확인과 패키지 검사 범위는 [제출 버전 기록](RELEASE_BASELINE_20260918.md)에 구분했습니다. 과거 공개 시험의 분리 기준은 [공개 시험 안내](../backend/tests/PUBLIC_TEST_SCOPE.md)를 참고하세요.

Docker와 Docker Compose가 설치된 새 작업 폴더에서 실행합니다. 이 설정은 로컬 포트 18080만 열고 새로운 데이터 볼륨을 사용합니다. 기존 기관 서비스의 계정·DB·첨부를 복사하지 마세요.

현재 위치: 공개 후보의 루트 폴더(`docker-compose.yml`과 `.env.example`이 보이는 위치).

```powershell
if (-not (Test-Path .\docker-compose.yml) -or -not (Test-Path .\.env.example)) { throw "공개 후보의 루트 폴더에서 실행하세요." }
Copy-Item .\.env.example .\.env
# .env의 POSTGRES_PASSWORD와 BOOTSTRAP_ADMIN_PASSWORD를 서로 다른 새 값으로 지정합니다.
# 다른 실행 환경과 겹치면 GATEWAY_PORT와 CHAT_ORIGINS를 함께 변경합니다.
docker compose -p mesil_public_demo up -d --build
$healthLimit = [TimeSpan]::FromSeconds(120)
$healthWatch = [Diagnostics.Stopwatch]::StartNew()
$healthReady = $false
$httpClient = [Net.Http.HttpClient]::new()
$httpClient.Timeout = [Threading.Timeout]::InfiniteTimeSpan
try {
    do {
        $remaining = $healthLimit - $healthWatch.Elapsed
        if ($remaining -le [TimeSpan]::Zero) { break }
        if ($remaining.TotalMilliseconds -lt 1) { break }

        $requestTimeoutMs = [Math]::Floor($remaining.TotalMilliseconds)
        $requestCancellation = [Threading.CancellationTokenSource]::new()
        $response = $null
        try {
            $requestCancellation.CancelAfter([int]$requestTimeoutMs)
            $response = $httpClient.GetAsync(
                "http://localhost:18080/api/health",
                $requestCancellation.Token
            ).GetAwaiter().GetResult()
            if (
                $healthWatch.Elapsed -lt $healthLimit -and
                $response.StatusCode -eq [Net.HttpStatusCode]::OK
            ) {
                $health = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() |
                    ConvertFrom-Json
                if ($healthWatch.Elapsed -lt $healthLimit) {
                    $healthReady = $true
                    break
                }
            }
        } catch {
            # 새 DB 마이그레이션 중의 502, 연결 실패와 요청 제한시간 초과는 기한 안에서 다시 확인합니다.
        } finally {
            if ($null -ne $response) { $response.Dispose() }
            $requestCancellation.Dispose()
        }

        $remaining = $healthLimit - $healthWatch.Elapsed
        if ($remaining -le [TimeSpan]::Zero) { break }
        Start-Sleep -Milliseconds ([Math]::Min(2000, [Math]::Floor($remaining.TotalMilliseconds)))
    } while ($true)
} finally {
    $httpClient.Dispose()
    $healthWatch.Stop()
}
if (-not $healthReady) {
    throw "서비스 준비가 2분 안에 완료되지 않았습니다. docker compose -p mesil_public_demo ps와 docker compose -p mesil_public_demo logs backend를 확인하세요."
}
$health
```

브라우저에서 http://localhost:18080 을 열고 직접 정한 관리자 계정으로 로그인합니다. 빈 새 DB로 시작하며, 발표 서버의 방·승인 기록·계정은 이 소스에 들어 있지 않습니다. 관리 화면에서 합성 직원과 방을 추가할 수 있습니다.

DB 포트는 외부에 열지 않습니다. 파일·DB는 해당 Compose 프로젝트의 전용 볼륨에 생깁니다. 종료는 `docker compose -p mesil_public_demo stop`, 재시작은 `docker compose -p mesil_public_demo start`입니다. 자료를 보존하려면 볼륨 삭제 옵션을 사용하지 마세요.

AI에는 별도로 설치한 내부 Ollama 모델과 STT 서비스가 필요합니다. 이 패키지는 모델을 다운로드하거나 외부 AI 계정을 연결하지 않습니다. 모델 이름·내부 서비스 주소를 운영자가 설정하고 합성자료로 확인하세요. AI 없이도 로그인과 채팅의 기본 동작부터 확인할 수 있습니다.

통화는 마이크·카메라 권한과 안전한 브라우저 접속 환경이 필요합니다. localhost의 같은 장비 시험과 외부 장비/망의 연결 조건은 다릅니다. 외부망에는 별도의 STUN/TURN·HTTPS 검증이 필요하며 이 예시는 이를 구축하지 않습니다. Chat은 GitHub와 `/downloads`에서 APK를 제공하지 않습니다. Android에서는 모바일 브라우저 또는 PWA(홈 화면에 설치하는 웹앱)로 접속합니다.

`mobile/`에는 웹/native 알림 계약을 설명하는 Java 참고 파일 4개만 있습니다. Gradle 설정·Android 리소스·서명 설정이 없으므로 이 공개 후보는 **완성된 Android 빌드 프로젝트를 포함하지 않습니다**. Talk용 APK, Chat용 APK, 서명키, 기기정보와 비공개 APK 메타데이터도 포함하지 않습니다.

이 공개 후보의 `scripts/`에는 Git에서 제외된 별도 시연 계정 파일을 역할별로 읽는 참고 도우미 `submission_accounts.py`만 있습니다. 입력 계정 파일은 공개 후보에 없으며, 이 도우미는 계정 생성·설치·백업 도구가 아닙니다. **DB·첨부파일 백업·복원 스크립트는 포함하지 않습니다.** Compose의 DB와 첨부는 서로 다른 전용 볼륨에 저장되지만, 이는 자동 백업이 아닙니다. 운영 전에는 DB 논리 백업과 첨부파일 볼륨 백업을 별도로 설계하고, 실제 복원 시험을 통과한 절차만 사용하세요.

## 개발 시험

Node.js 22.13 이상과 Python 3.12, uv가 필요합니다. Frontend 시험은 production build 결과를 이용합니다.

현재 위치: 공개 후보의 루트 폴더.

```powershell
Push-Location .\frontend
npm ci --ignore-scripts --no-audit --no-fund
npm run build
npm test
npm run lint
Pop-Location
```

Backend 시험은 비어 있는 PostgreSQL 시험 DB에만 연결하세요. `DATABASE_URL`에는 직접 만든 시험 DB의 URL을 환경변수로 설정합니다. 운영 DB를 지정하지 마세요. 시험은 자체 이름의 스키마를 만들고 종료 시 정리합니다.

현재 위치: 공개 후보의 루트 폴더. 아래 명령 전에 별도로 준비한 빈 PostgreSQL 시험 DB의 접속 URL을 현재 PowerShell 세션의 `DATABASE_URL`에 지정합니다.

```powershell
if (-not $env:DATABASE_URL) { throw "빈 PostgreSQL 시험 DB의 DATABASE_URL을 먼저 지정하세요." }
Push-Location .\backend
uv sync --extra test --frozen
# DATABASE_URL은 이 터미널에서 안전하게 설정합니다. 비밀값을 문서나 Git에 넣지 마세요.
uv run --no-sync pytest
Pop-Location
```

기관 운영 전에는 HTTPS, 최소권한, 백업·복원, 보관·삭제 기준과 개인정보 안내를 별도로 정해야 합니다. 이 문서는 기관 운영 배포 승인이나 법적 적합성 확인서가 아닙니다.
