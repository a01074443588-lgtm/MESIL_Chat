import os
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

from sqlalchemy import create_engine
import pytest


TEST_DATA_DIR = Path(__file__).resolve().parent / ".test-data"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEST_UPLOAD_DIR = TEST_DATA_DIR / f"uploads-{uuid4().hex}"
TEST_SCHEMA = f"smcodi_test_{uuid4().hex}"
TEST_AI_SETTINGS_FILE = TEST_DATA_DIR / f"ai-settings-{uuid4().hex}.json"
TEST_AI_PROBE_FILE = TEST_DATA_DIR / f"ai-probes-{uuid4().hex}.json"
TEST_AI_SECRET_DIR = TEST_DATA_DIR / f"ai-secrets-{uuid4().hex}"


def database_url() -> str:
    configured = os.environ.get("DATABASE_URL", "").strip()
    if configured:
        return configured
    password = os.environ.get("POSTGRES_PASSWORD", "").strip()
    if not password:
        raise RuntimeError(
            "PostgreSQL 자동화 시험에는 POSTGRES_PASSWORD 환경변수가 필요합니다."
        )
    user = quote_plus(os.environ.get("POSTGRES_USER", "smcodi_chat"))
    host = os.environ.get("POSTGRES_HOST", "127.0.0.1")
    port = os.environ.get("POSTGRES_PORT", "55433")
    name = quote_plus(os.environ.get("POSTGRES_DB", "smcodi_chat"))
    return (
        f"postgresql+psycopg://{user}:{quote_plus(password)}@{host}:{port}/{name}"
    )


TEST_DATABASE_URL = database_url()
schema_engine = create_engine(TEST_DATABASE_URL, isolation_level="AUTOCOMMIT")
with schema_engine.connect() as connection:
    connection.exec_driver_sql(f'CREATE SCHEMA "{TEST_SCHEMA}"')

os.environ["DATABASE_URL"] = TEST_DATABASE_URL
os.environ["DATABASE_SCHEMA"] = TEST_SCHEMA
os.environ["AUTO_CREATE_SCHEMA"] = "true"
os.environ["UPLOAD_DIR"] = TEST_UPLOAD_DIR.as_posix()
os.environ["BOOTSTRAP_ADMIN_USERNAME"] = "admin"
os.environ["BOOTSTRAP_ADMIN_PASSWORD"] = "AdminPass!234"
os.environ["BOOTSTRAP_ADMIN_NAME"] = "시험 관리자"
os.environ["ALLOWED_ORIGINS"] = "http://testserver"
os.environ["ENVIRONMENT"] = "test"
os.environ["REVIEWER_ACCESS_ENABLED"] = "false"
os.environ["REVIEWER_PASSWORD_LOGIN_ENABLED"] = "false"
os.environ["LOGIN_ATTEMPT_WINDOW_MINUTES"] = "15"
os.environ["LOGIN_PAIR_LIMIT"] = "5"
os.environ["LOGIN_CLIENT_LIMIT"] = "30"
os.environ["OCR_PROVIDER"] = "stub"
os.environ["OCR_MODEL"] = "test-ocr"
# OCR 경로 선택이 개발용 .env 값에 따라 달라지면 동일한 회귀시험이
# 개발 PC와 CI에서 서로 다른 엔진을 타게 된다. 구조화 템플릿 엔진은
# 전용 시험에서 따로 검증하고, 공통 업무흐름 시험은 기존 안전 교정
# 경로로 고정한다.
os.environ["OCR_TEMPLATE_ENGINE_ENABLED"] = "false"
os.environ["STT_ENABLED"] = "true"
os.environ["STT_PROVIDER"] = "stub"
os.environ["STT_MODEL"] = "test-whisper"
os.environ["STT_SHARED_TOKEN"] = "test-local-stt-token"
os.environ["AI_REVIEW_PROVIDER"] = "stub"
os.environ["AI_REVIEW_MODEL"] = "test-ai-review"
os.environ["AI_SETTINGS_FILE"] = TEST_AI_SETTINGS_FILE.as_posix()
os.environ["AI_PROVIDER_PROBE_FILE"] = TEST_AI_PROBE_FILE.as_posix()
os.environ["AI_SECRET_DIR"] = TEST_AI_SECRET_DIR.as_posix()


@pytest.fixture
def public_media_root(tmp_path):
    """Create all required media under the disposable test root, never in source."""
    from public_synthetic_media import write_synthetic_media

    root = tmp_path / "synthetic-only"
    assets = root / "backend/tests/fixtures/finalist_coded_synthetic_v1"
    names = [f"coded-handwriting-source-{n:02d}.jpg" for n in range(1, 6)]
    names += ["coded-handwriting-vital-01.png", "coded-voice-water-01.wav"]
    for code, name in enumerate(names, 1):
        write_synthetic_media(assets / name, code)
    for rel in [
        "backend/tests/fixtures/m21_actual_workflow_v2/new_admission_health.pdf",
        "backend/tests/fixtures/nano_omni_deidentified_v1/synthetic_care_card.png",
        "backend/tests/fixtures/nano_omni_deidentified_v1/synthetic_care_message.wav",
    ]:
        write_synthetic_media(root / rel)
    return root


@pytest.fixture
def migration_fixture():
    """Exercise historical constraints without downgrading another test's rows."""
    from sqlalchemy.orm import Session
    from app.database import Base, engine
    from app.models import (
        Organization, User, Room, Message, DomainModule,
        MessageAttachment, AttachmentTextExtraction,
    )

    schema = f"smcodi_test_migration_{uuid4().hex}"
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
            connection.exec_driver_sql(f'SET LOCAL search_path TO "{schema}", public')
            Base.metadata.create_all(connection)
            with Session(bind=connection, join_transaction_mode="create_savepoint") as db:
                organization = Organization(internal_code="SYN-MIGRATION", name="합성 migration 기관")
                db.add(organization)
                db.flush()
                user = User(organization_id=organization.id, username="synthetic-migration",
                            display_name="합성 시험자", password_hash="not-a-login-hash")
                room = Room(organization_id=organization.id, name="합성 migration 방",
                            kind="custom", is_test_data=True)
                db.add_all([user, room, DomainModule(code="staff_hub", name="합성", data_owner="synthetic")])
                db.flush()
                message = Message(organization_id=organization.id, room_id=room.id,
                                  sender_id=user.id, body="합성 원본 보존 기록", is_test_data=True)
                db.add(message)
                db.flush()
                attachment = MessageAttachment(organization_id=organization.id, message_id=message.id,
                    uploader_id=user.id, storage_key="synthetic-migration", original_name="synthetic.txt",
                    mime_type="text/plain", size_bytes=1, sha256="a" * 64)
                db.add(attachment)
                db.flush()
                extraction = AttachmentTextExtraction(organization_id=organization.id,
                    attachment_id=attachment.id, status="completed", provider="stub", model_name="synthetic",
                    extracted_text="합성 원문", requested_by_id=user.id)
                db.add(extraction)
                db.flush()
                yield connection
        finally:
            # Roll back only the schema and synthetic rows created in this transaction.
            transaction.rollback()


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    if not TEST_SCHEMA.startswith("smcodi_test_"):
        raise RuntimeError("시험 스키마 이름 검증에 실패했습니다.")
    with schema_engine.connect() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{TEST_SCHEMA}" CASCADE')
    schema_engine.dispose()
