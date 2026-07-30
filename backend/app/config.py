from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPLOAD_DIR = (PROJECT_ROOT / "data" / "uploads").as_posix()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "SMCODI 채팅방"
    environment: str = "development"
    database_url: str | None = None
    database_schema: str = "smcodi"
    postgres_host: str = "127.0.0.1"
    postgres_port: int = Field(default=55433, ge=1, le=65535)
    postgres_user: str = "smcodi_chat"
    postgres_password: SecretStr | None = None
    postgres_db: str = "smcodi_chat"
    allowed_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    session_cookie_name: str = "smcodi_chat_session"
    session_hours: int = 12
    cookie_secure: bool = False
    trust_proxy_headers: bool = False
    login_attempt_window_minutes: int = Field(default=15, ge=1, le=1440)
    login_pair_limit: int = Field(default=5, ge=2, le=100)
    login_client_limit: int = Field(default=30, ge=5, le=1000)
    reviewer_access_enabled: bool = False
    reviewer_access_ends_at: datetime | None = None
    reviewer_session_minutes: int = Field(default=45, ge=10, le=120)
    reviewer_care_username: str | None = Field(default=None, max_length=80)
    reviewer_social_username: str | None = Field(default=None, max_length=80)
    reviewer_secondary_username: str | None = Field(default=None, max_length=80)
    reviewer_chat_room_name: str = Field(
        default="시설 전체방",
        min_length=1,
        max_length=100,
    )
    reviewer_session_secret: SecretStr | None = Field(default=None, min_length=32)
    reviewer_rate_limit: int = Field(default=8, ge=2, le=100)
    reviewer_rate_window_minutes: int = Field(default=1, ge=1, le=60)
    reviewer_session_limit_per_client: int = Field(default=4, ge=1, le=20)
    auto_create_schema: bool = False
    bootstrap_admin_username: str | None = None
    bootstrap_admin_password: str | None = Field(default=None, min_length=12)
    bootstrap_admin_name: str = "시스템 관리자"
    dev_launcher_enabled: bool = False
    dev_launcher_username: str = "local_dev_launcher"
    dev_launcher_password: SecretStr | None = Field(default=None, min_length=16)
    dev_launcher_name: str = "개발자 사용자 전환"
    dev_launcher_cookie_name: str = "smcodi_chat_dev_controller"
    dev_impersonation_minutes: int = Field(default=120, ge=10, le=480)
    max_message_length: int = 2000
    upload_dir: str = DEFAULT_UPLOAD_DIR
    max_attachment_bytes: int = Field(
        default=30 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
    )
    max_attachments_per_message: int = Field(default=4, ge=1, le=10)
    ocr_provider: str = "ollama"
    ocr_model: str = "qwen3-vl:8b-instruct"
    ocr_base_url: str = "http://127.0.0.1:11434"
    ocr_timeout_seconds: int = Field(default=180, ge=10, le=900)
    ocr_image_bands: int = Field(default=3, ge=1, le=4)
    ocr_lexicon_path: str = (PROJECT_ROOT / "data" / "ocr_lexicon.local.json").as_posix()
    smcodi_resident_lexicon_path: str = (
        PROJECT_ROOT / "data" / "smcodi_residents.local.json"
    ).as_posix()
    carefor_resident_roster_path: str = (
        PROJECT_ROOT / "data" / "carefor_residents.local.json"
    ).as_posix()
    carefor_staff_roster_path: str = (
        PROJECT_ROOT / "data" / "carefor_staff.local.json"
    ).as_posix()
    carefor_identity_map_path: str = (
        PROJECT_ROOT / "data" / "carefor_identity_map.local.json"
    ).as_posix()
    stt_enabled: bool = False
    stt_provider: str = "local_whisper_service"
    stt_model: str = "whisper-small"
    stt_service_url: str = "http://127.0.0.1:8766"
    stt_shared_token: SecretStr | None = None
    stt_timeout_seconds: int = Field(default=600, ge=30, le=1800)
    ai_review_provider: str = "chain"
    ai_review_model: str = "qwen3.6:35b"
    ai_review_local_models: str = "qwen3.6:35b,gemma4:e4b"
    ai_review_base_url: str = "http://127.0.0.1:11434"
    ai_review_timeout_seconds: int = Field(default=45, ge=10, le=900)
    ai_review_external_enabled: bool = False
    nvidia_api_key: SecretStr | None = None
    nvidia_api_key_file: str = (
        PROJECT_ROOT / "data" / "runtime" / "nvidia_api_key.local"
    ).as_posix()
    nvidia_api_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nemotron_model: str = "nvidia/nemotron-3-ultra-550b-a55b"
    nvidia_api_timeout_seconds: int = Field(default=120, ge=10, le=300)
    nvidia_summary_timeout_seconds: int = Field(default=45, ge=10, le=120)
    web_push_enabled: bool = False
    web_push_vapid_private_key_path: str = "/data/runtime/webpush_vapid_private.pem"
    web_push_vapid_public_key: str | None = None
    web_push_vapid_subject: str = "https://chat.silvermedical.kr"
    organization_code: str = "silvermedical-demo"
    organization_name: str = "실버메디컬 가명기관"
    organization_service_type: str = "facility_care"

    @field_validator("database_schema")
    @classmethod
    def validate_database_schema(cls, value: str) -> str:
        if not value or not value.replace("_", "").isalnum() or value[0].isdigit():
            raise ValueError("DATABASE_SCHEMA은 영문, 숫자, 밑줄로만 지정해야 합니다.")
        return value

    @field_validator("web_push_vapid_subject")
    @classmethod
    def normalize_web_push_vapid_subject(cls, value: str) -> str:
        normalized = value.strip()
        if normalized.startswith(("http://", "https://")):
            return normalized.rstrip("/")
        return normalized

    @model_validator(mode="after")
    def validate_production_security(self):
        if self.reviewer_access_enabled:
            reviewer_usernames = {
                (self.reviewer_care_username or "").strip(),
                (self.reviewer_social_username or "").strip(),
                (self.reviewer_secondary_username or "").strip(),
            }
            if "" in reviewer_usernames or len(reviewer_usernames) != 3:
                raise ValueError(
                    "심사위원 체험 계정은 서로 다른 요양·사회복지·실시간 가상계정으로 "
                    "지정해야 합니다."
                )
            if self.reviewer_session_secret is None:
                raise ValueError("심사위원 체험 세션 서명키가 필요합니다.")
            if self.reviewer_access_ends_at is None:
                raise ValueError("심사위원 체험 종료시각이 필요합니다.")
            if self.reviewer_access_ends_at.tzinfo is None:
                raise ValueError("심사위원 체험 종료시각에는 시간대 정보가 필요합니다.")
        if self.environment != "production":
            return self
        if not self.cookie_secure:
            raise ValueError("운영환경에서는 COOKIE_SECURE=true가 필요합니다.")
        if not self.trust_proxy_headers:
            raise ValueError("운영환경에서는 신뢰 게이트웨이 설정이 필요합니다.")
        if self.dev_launcher_enabled:
            raise ValueError("운영환경에서는 개발자 런처를 사용할 수 없습니다.")
        if not self.origin_list or any(
            not origin.startswith("https://")
            or "localhost" in origin
            or "127.0.0.1" in origin
            for origin in self.origin_list
        ):
            raise ValueError(
                "운영환경의 ALLOWED_ORIGINS에는 HTTPS 운영 주소만 등록해야 합니다."
            )
        return self

    @property
    def origin_list(self) -> list[str]:
        return [item.strip().rstrip("/") for item in self.allowed_origins.split(",") if item.strip()]

    @property
    def dev_launcher_active(self) -> bool:
        return self.environment == "development" and self.dev_launcher_enabled

    @property
    def web_push_active(self) -> bool:
        return bool(
            self.web_push_enabled
            and self.web_push_vapid_public_key
            and self.web_push_vapid_subject
            and Path(self.web_push_vapid_private_key_path).is_file()
        )

    @property
    def reviewer_access_active(self) -> bool:
        if not self.reviewer_access_enabled or self.reviewer_access_ends_at is None:
            return False
        ends_at = self.reviewer_access_ends_at
        if ends_at.tzinfo is None:
            return False
        return datetime.now(timezone.utc) < ends_at.astimezone(timezone.utc)

    @property
    def sqlalchemy_database_url(self) -> str:
        if self.database_url:
            if not self.database_url.startswith(
                ("postgresql://", "postgresql+psycopg://")
            ):
                raise RuntimeError(
                    "SMCODI 호환 데이터 계층은 PostgreSQL만 지원합니다. "
                    "DATABASE_URL을 postgresql+psycopg 형식으로 지정하세요."
                )
            return self.database_url
        if self.postgres_password is None:
            raise RuntimeError(
                "POSTGRES_PASSWORD가 없습니다. .env에 개발용 PostgreSQL 비밀번호를 설정하세요."
            )
        password = quote_plus(self.postgres_password.get_secret_value())
        user = quote_plus(self.postgres_user)
        database = quote_plus(self.postgres_db)
        return (
            f"postgresql+psycopg://{user}:{password}@"
            f"{self.postgres_host}:{self.postgres_port}/{database}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
