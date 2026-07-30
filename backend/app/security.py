import base64
import hashlib
import hmac
import json
import math
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Request
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .config import settings
from .models import LoginAttempt, LoginSession, User, utcnow


password_hasher = PasswordHasher()
dummy_password_hash = password_hasher.hash("SMCODI invalid login placeholder")
LOCAL_DEVELOPMENT_HOSTS = {"localhost", "127.0.0.1", "::1"}
REVIEWER_TOKEN_PREFIX = "rv1"
REVIEWER_EXPERIENCES = {"care", "social_worker", "realtime_secondary"}
REVIEWER_SESSION_USER_AGENT_PREFIX = "[reviewer:"


class InvalidReviewerSessionToken(ValueError):
    pass


@dataclass(frozen=True)
class ReviewerSessionContext:
    experience: str
    expires_at: datetime


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _base64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _base64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")


def _reviewer_signing_key() -> bytes:
    if settings.reviewer_session_secret is None:
        raise RuntimeError("심사위원 체험 세션 서명키가 설정되지 않았습니다.")
    return settings.reviewer_session_secret.get_secret_value().encode("utf-8")


def create_reviewer_session_token(
    experience: str,
    expires_at: datetime,
) -> str:
    if experience not in REVIEWER_EXPERIENCES:
        raise ValueError("지원하지 않는 심사 체험입니다.")
    payload = {
        "experience": experience,
        "exp": int(_as_utc(expires_at).timestamp()),
        "nonce": secrets.token_urlsafe(32),
    }
    encoded_payload = _base64url_encode(
        json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )
    signature = hmac.new(
        _reviewer_signing_key(),
        f"{REVIEWER_TOKEN_PREFIX}.{encoded_payload}".encode("ascii"),
        hashlib.sha256,
    ).digest()
    return (
        f"{REVIEWER_TOKEN_PREFIX}.{encoded_payload}."
        f"{_base64url_encode(signature)}"
    )


def reviewer_session_context(
    token: str,
    *,
    now: datetime | None = None,
) -> ReviewerSessionContext | None:
    if not token.startswith(f"{REVIEWER_TOKEN_PREFIX}."):
        return None
    if settings.reviewer_session_secret is None:
        raise InvalidReviewerSessionToken(
            "심사 체험 세션을 더 이상 확인할 수 없습니다."
        )
    try:
        prefix, encoded_payload, encoded_signature = token.split(".", 2)
        expected_signature = hmac.new(
            _reviewer_signing_key(),
            f"{prefix}.{encoded_payload}".encode("ascii"),
            hashlib.sha256,
        ).digest()
        supplied_signature = _base64url_decode(encoded_signature)
        if not hmac.compare_digest(expected_signature, supplied_signature):
            raise InvalidReviewerSessionToken("심사 체험 세션 서명이 올바르지 않습니다.")
        payload = json.loads(_base64url_decode(encoded_payload).decode("utf-8"))
        experience = payload["experience"]
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    except (
        InvalidReviewerSessionToken,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        if isinstance(exc, InvalidReviewerSessionToken):
            raise
        raise InvalidReviewerSessionToken(
            "심사 체험 세션 정보를 확인할 수 없습니다."
        ) from exc
    if experience not in REVIEWER_EXPERIENCES:
        raise InvalidReviewerSessionToken("심사 체험 종류를 확인할 수 없습니다.")
    if expires_at <= (now or utcnow()):
        raise InvalidReviewerSessionToken("심사 체험 세션이 만료되었습니다.")
    return ReviewerSessionContext(
        experience=experience,
        expires_at=expires_at,
    )


def validate_reviewer_session_user(
    token: str,
    user: User,
    *,
    now: datetime | None = None,
) -> ReviewerSessionContext | None:
    if not token.startswith(f"{REVIEWER_TOKEN_PREFIX}."):
        return None
    if not settings.reviewer_access_active:
        raise InvalidReviewerSessionToken("심사위원 체험 기간이 종료되었습니다.")
    context = reviewer_session_context(token, now=now)
    if context is None:
        return None
    expected_username = (
        settings.reviewer_care_username
        if context.experience == "care"
        else (
            settings.reviewer_social_username
            if context.experience == "social_worker"
            else settings.reviewer_secondary_username
        )
    )
    expected_processor_access = context.experience == "social_worker"
    if (
        not expected_username
        or user.username != expected_username
        or user.role != "staff"
        or user.staff is None
        or not user.staff.is_test_data
        or not user.staff.is_active
        or user.staff.employment_status != "active"
        or user.must_change_password
        or user.can_process_records is not expected_processor_access
    ):
        raise InvalidReviewerSessionToken(
            "심사 체험 계정의 안전 조건을 확인할 수 없습니다."
        )
    return context


def reviewer_session_user_agent(
    experience: str,
    user_agent: str | None,
) -> str:
    if experience not in REVIEWER_EXPERIENCES:
        raise ValueError("지원하지 않는 심사 체험입니다.")
    return f"{REVIEWER_SESSION_USER_AGENT_PREFIX}{experience}] {user_agent or ''}"[:300]


def is_reviewer_login_session(login_session: LoginSession) -> bool:
    return (login_session.user_agent or "").startswith(
        REVIEWER_SESSION_USER_AGENT_PREFIX
    )


def client_key_from_request(request: Request) -> str:
    client_address = request.client.host if request.client else "unknown"
    if settings.trust_proxy_headers:
        forwarded_for = request.headers.get("x-forwarded-for", "")
        if forwarded_for:
            client_address = forwarded_for.split(",", 1)[0].strip() or client_address
    return hashlib.sha256(client_address.encode("utf-8")).hexdigest()


def is_local_development_request(request: Request) -> bool:
    hostname = (request.url.hostname or "").strip().lower()
    return hostname in LOCAL_DEVELOPMENT_HOSTS


def secure_cookie_for_request(request: Request) -> bool:
    if settings.cookie_secure:
        return True
    host_header = request.headers.get("host", "").strip()
    try:
        forwarded_hostname = urlsplit(f"//{host_header}").hostname
    except ValueError:
        forwarded_hostname = None
    hostname = (forwarded_hostname or request.url.hostname or "").strip().lower()
    if not hostname or hostname in LOCAL_DEVELOPMENT_HOSTS:
        return False
    for allowed_origin in settings.origin_list:
        parsed_origin = urlsplit(allowed_origin)
        if parsed_origin.scheme.lower() != "https":
            continue
        if hostname == (parsed_origin.hostname or "").lower():
            return True
    return False


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def login_retry_after(db: Session, username: str, client_key: str) -> int | None:
    now = utcnow()
    window = timedelta(minutes=settings.login_attempt_window_minutes)
    cutoff = now - window
    db.execute(delete(LoginAttempt).where(LoginAttempt.attempted_at < cutoff))
    pair_attempts = db.scalars(
        select(LoginAttempt)
        .where(
            LoginAttempt.username == username,
            LoginAttempt.client_key == client_key,
            LoginAttempt.attempted_at >= cutoff,
        )
        .order_by(LoginAttempt.attempted_at)
    ).all()
    client_attempts = db.scalars(
        select(LoginAttempt)
        .where(
            LoginAttempt.client_key == client_key,
            LoginAttempt.attempted_at >= cutoff,
        )
        .order_by(LoginAttempt.attempted_at)
    ).all()
    blocked_until = []
    if len(pair_attempts) >= settings.login_pair_limit:
        blocked_until.append(_as_utc(pair_attempts[-settings.login_pair_limit].attempted_at) + window)
    if len(client_attempts) >= settings.login_client_limit:
        blocked_until.append(
            _as_utc(client_attempts[-settings.login_client_limit].attempted_at) + window
        )
    if not blocked_until:
        return None
    seconds = (max(blocked_until) - now).total_seconds()
    return max(1, math.ceil(seconds))


def record_failed_login(db: Session, username: str, client_key: str) -> None:
    db.add(LoginAttempt(username=username, client_key=client_key))


def clear_failed_logins(db: Session, username: str, client_key: str) -> None:
    db.execute(
        delete(LoginAttempt).where(
            LoginAttempt.username == username,
            LoginAttempt.client_key == client_key,
        )
    )


def create_login_session(
    db: Session,
    user: User,
    user_agent: str | None,
    client_key: str,
    *,
    impersonated_by_user_id: UUID | None = None,
    expires_in_minutes: int | None = None,
    session_token: str | None = None,
    expires_at_override: datetime | None = None,
) -> tuple[str, LoginSession]:
    token = session_token or secrets.token_urlsafe(48)
    expires_at = (
        _as_utc(expires_at_override)
        if expires_at_override is not None
        else (
            utcnow() + timedelta(minutes=expires_in_minutes)
            if expires_in_minutes is not None
            else utcnow() + timedelta(hours=settings.session_hours)
        )
    )
    login_session = LoginSession(
        user_id=user.id,
        token_hash=token_digest(token),
        expires_at=expires_at,
        user_agent=(user_agent or "")[:300] or None,
        client_key=client_key,
        impersonated_by_user_id=impersonated_by_user_id,
    )
    db.add(login_session)
    db.commit()
    db.refresh(login_session)
    return token, login_session
