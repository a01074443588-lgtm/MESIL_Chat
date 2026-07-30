from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(
    settings.sqlalchemy_database_url,
    pool_pre_ping=True,
)


@event.listens_for(engine, "connect")
def _set_postgresql_search_path(dbapi_connection, _connection_record) -> None:
    # psycopg의 기본 거래 안에서 SET을 실행하면 첫 rollback 때 검색 경로도
    # 함께 취소될 수 있다. 연결 세션 설정으로 확정해 풀 재사용 시에도 유지한다.
    previous_autocommit = dbapi_connection.autocommit
    dbapi_connection.autocommit = True
    try:
        cursor = dbapi_connection.cursor()
        cursor.execute(f'SET SESSION search_path TO "{settings.database_schema}", public')
        cursor.close()
    finally:
        dbapi_connection.autocommit = previous_autocommit


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
