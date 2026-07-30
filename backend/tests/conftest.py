import os
from pathlib import Path
from urllib.parse import quote_plus
from uuid import uuid4

from sqlalchemy import create_engine


TEST_DATA_DIR = Path(__file__).resolve().parent / ".test-data"
TEST_DATA_DIR.mkdir(parents=True, exist_ok=True)
TEST_UPLOAD_DIR = TEST_DATA_DIR / f"uploads-{uuid4().hex}"
TEST_SCHEMA = f"smcodi_test_{uuid4().hex}"


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
os.environ["LOGIN_ATTEMPT_WINDOW_MINUTES"] = "15"
os.environ["LOGIN_PAIR_LIMIT"] = "5"
os.environ["LOGIN_CLIENT_LIMIT"] = "30"
os.environ["OCR_PROVIDER"] = "stub"
os.environ["OCR_MODEL"] = "test-ocr"
os.environ["STT_ENABLED"] = "true"
os.environ["STT_PROVIDER"] = "stub"
os.environ["STT_MODEL"] = "test-whisper"
os.environ["STT_SHARED_TOKEN"] = "test-local-stt-token"
os.environ["AI_REVIEW_PROVIDER"] = "stub"
os.environ["AI_REVIEW_MODEL"] = "test-ai-review"


def pytest_sessionfinish(session, exitstatus):
    del session, exitstatus
    if not TEST_SCHEMA.startswith("smcodi_test_"):
        raise RuntimeError("시험 스키마 이름 검증에 실패했습니다.")
    with schema_engine.connect() as connection:
        connection.exec_driver_sql(f'DROP SCHEMA "{TEST_SCHEMA}" CASCADE')
    schema_engine.dispose()
