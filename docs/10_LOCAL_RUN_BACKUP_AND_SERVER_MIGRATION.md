# 현재 PC 실행·백업·새 서버 이전

## 최초 준비와 개발 실행

```powershell
Set-Location D:\Projects\SMCODI_CHAT
Copy-Item .\.env.example .\.env
# .env 안의 비밀번호와 관리자 초기 비밀번호를 반드시 변경
powershell -ExecutionPolicy Bypass -File .\scripts\start-dev.ps1
```

개발 화면은 `http://localhost:3100`, API 상태는 `http://localhost:8000/api/health`다. PostgreSQL은 Docker의 이름 있는 볼륨에 보관하므로 컨테이너가 다시 시작되어도 유지된다.

## 전체 컨테이너 실행

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start-local-stt.ps1
docker compose up -d --build
docker compose ps
```

로컬 접속 주소는 `http://localhost:8080`이다. 현재 PC의 8080 포트는
`127.0.0.1`에만 바인딩하고 Cloudflare Tunnel만 외부 HTTPS 요청을 전달한다.

`STT_ENABLED=true`이면 `start-dev.ps1`은 로컬 Whisper 서비스를 자동으로 확인·시작한다. 컨테이너 방식에서는 위 명령처럼 호스트의 판독 서비스를 먼저 실행한다. 새 서버에서는 `.env`의 `STT_LOCAL_PYTHON_PATH`, `STT_LOCAL_MODEL_PATH`, `STT_SHARED_TOKEN`을 새 환경에 맞게 설정한다. 모델 경로를 코드에 직접 넣지 않는다.

프로토타입 제출 전에는 개발자 런처를 끄고 다음 점검이 성공해야 한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-release-safety.ps1
```

## 현재 PC의 임시 HTTPS 운영

직원 접속 주소는 `https://chat.silvermedical.kr`이다. Cloudflare Tunnel은
외부에서 현재 PC로 새 연결을 여는 방식이 아니라, 현재 PC가 Cloudflare로
아웃바운드 연결을 먼저 만드는 방식이다. 라우터나 방화벽의 8080 포트를
외부에 공개하지 않는다.

```powershell
Set-Location D:\Projects\SMCODI_CHAT

# 수동 시작·확인·중지
powershell -ExecutionPolicy Bypass -File .\scripts\start-public-tunnel.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\check-public-tunnel.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\stop-public-tunnel.ps1

# Windows 로그인 후 자동 시작 등록·해제
powershell -ExecutionPolicy Bypass -File .\scripts\install-public-tunnel-autostart.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-public-tunnel-autostart.ps1
```

Cloudflare 연결 토큰과 실행파일은 Git에서 제외된 `data\runtime`에 둔다.
토큰값을 문서·로그·Git에 복사하지 않는다. 외부 주소에서는 개발자 런처
로그인이 서버에서 404로 차단되고, 일반 로그인 쿠키는 전달된 HTTPS 정보를
기준으로 Secure 속성을 사용한다.

## DB 마이그레이션

```powershell
Set-Location D:\Projects\SMCODI_CHAT\backend
uv run alembic upgrade head
uv run alembic current
uv run alembic check
```

운영에서는 앱의 자동 테이블 생성에 의존하지 않고 Alembic만 스키마 변경을 담당한다.

## PostgreSQL 백업

```powershell
Set-Location D:\Projects\SMCODI_CHAT
powershell -ExecutionPolicy Bypass -File .\scripts\backup_postgres.ps1
```

`data\backups`에 `.dump`와 같은 이름의 설명 JSON이 생긴다. 현재 프로토타입 사진은 기본 백업에서 제외한다. 사진 보존이 필요한 운영 시점에는 다음처럼 같은 백업에 포함한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\backup_postgres.ps1 `
  -IncludeAttachments
```

현재 DB를 바꾸지 않고 복원 가능성만 확인하려면:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\test-postgres-restore.ps1 `
  -BackupPath .\data\backups\smcodi_chat_postgres_YYYYMMDD_HHMMSS.dump
```

## 복원

복원은 현재 채팅 스키마를 덮어쓰므로 쓰기를 중지하고 정확한 파일을 확인한 뒤 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\restore_postgres.ps1 `
  -BackupPath .\data\backups\smcodi_chat_postgres_YYYYMMDD_HHMMSS.dump `
  -ConfirmDatabaseReset
```

첨부파일까지 백업한 운영 데이터라면 같은 시점의 첨부 압축파일도 복원하고 전체 검증을 실행한다.

## 새 서버 이전

1. 현재 PC의 채팅 쓰기를 중지한다.
2. PostgreSQL dump를 만들고, 운영상 보존할 첨부파일이 있으면 같은 시점에 함께 백업한다.
3. 새 서버에 저장소·Docker·`.env`와 로컬 Whisper 실행환경을 준비한다.
4. 새 빈 DB에 백업을 복원하고 사진을 복사한다.
5. Alembic 현재 버전과 테이블별 행수·FK·메시지 표본을 대조한다.
6. HTTPS 내부 도메인을 연결한다.
7. 퇴사자·권한·WebSocket·PWA 회귀시험을 통과시킨다.
8. 기존 PC를 일정 기간 읽기 전용 안전사본으로 보관한다.

기존 SMCODI와 합치는 일은 서버 이전과 별도 작업이다. 현재 dump를 기존 SMCODI DB에 직접 복원하지 않는다.

## SilverHome 운영 명령 초안

1. `.env.production.example`을 `.env.production`으로 복사하고 비밀값을 변경한다.
2. 배포 전 안전점검을 통과시킨다.
3. SilverHome 서버에서만 운영 Compose를 실행한다.

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\check-release-safety.ps1 `
  -EnvPath .\.env.production

$env:PRODUCTION_ENV_FILE=".env.production"
docker compose --env-file .env.production -f docker-compose.production.yml up -d --build
```

현재 작업에서는 SilverHome 서버 생성과 방화벽 변경은 실행하지 않았다.
DNS와 HTTPS는 현재 PC용 Cloudflare Tunnel에 임시 연결했다. 서버 이전 시
새 서버에 별도 커넥터를 연결하고 정상 확인 후 현재 PC 커넥터와 자동 시작을
중지한다.
