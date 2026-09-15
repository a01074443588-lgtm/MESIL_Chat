from __future__ import annotations

from datetime import timedelta, timezone
from hashlib import sha256
import logging
from threading import Lock
from typing import Any, Iterable
from uuid import UUID

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import LoginSession, MobilePushDevice, Staff, User, utcnow


logger = logging.getLogger(__name__)
MOBILE_CALL_PUSH_TTL_SECONDS = 60
_firebase_lock = Lock()
_firebase_app: firebase_admin.App | None = None


def _device_id_hash(device_id: UUID) -> str:
    return sha256(str(device_id).encode("utf-8")).hexdigest()[:12]


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _app() -> firebase_admin.App | None:
    global _firebase_app
    if not settings.mobile_push_active:
        return None
    if _firebase_app is not None:
        return _firebase_app
    with _firebase_lock:
        if _firebase_app is None:
            credential = credentials.Certificate(settings.firebase_credentials_file)
            _firebase_app = firebase_admin.initialize_app(
                credential,
                {"projectId": settings.firebase_project_id},
                name="mesil-mobile-push",
            )
    return _firebase_app


def _send_data_message(
    token: str,
    *,
    payload: dict[str, Any],
    ttl_seconds: int,
) -> None:
    app = _app()
    if app is None:
        raise RuntimeError("Android 푸시 서버가 준비되지 않았습니다.")
    data = {
        key: str(value)
        for key, value in payload.items()
        if value is not None
    }
    messaging.send(
        messaging.Message(
            token=token,
            data=data,
            android=messaging.AndroidConfig(
                priority="high",
                ttl=timedelta(seconds=ttl_seconds),
                collapse_key=data.get("tag"),
            ),
        ),
        app=app,
    )


def send_mobile_push_to_users(
    user_ids: Iterable[UUID],
    *,
    payload: dict[str, Any],
    ttl_seconds: int = MOBILE_CALL_PUSH_TTL_SECONDS,
) -> int:
    recipient_ids = set(user_ids)
    if not settings.mobile_push_active or not recipient_ids:
        return 0

    now = utcnow()
    sent_count = 0
    with SessionLocal() as db:
        devices = db.scalars(
            select(MobilePushDevice)
            .join(User, User.id == MobilePushDevice.user_id)
            .join(Staff, Staff.id == User.staff_id)
            .join(LoginSession, LoginSession.id == MobilePushDevice.login_session_id)
            .where(
                MobilePushDevice.user_id.in_(recipient_ids),
                MobilePushDevice.is_active.is_(True),
                User.is_active.is_(True),
                User.organization_id == Staff.organization_id,
                Staff.is_active.is_(True),
                Staff.deleted_at.is_(None),
                Staff.employment_status == "active",
                LoginSession.revoked_at.is_(None),
            )
        ).all()

        for device in devices:
            session = db.get(LoginSession, device.login_session_id)
            if session is None or _as_utc(session.expires_at) <= now:
                device.is_active = False
                device.disabled_at = now
                continue
            try:
                _send_data_message(
                    device.token,
                    payload=payload,
                    ttl_seconds=ttl_seconds,
                )
                device.failure_count = 0
                device.last_success_at = now
                sent_count += 1
            except (messaging.UnregisteredError, messaging.SenderIdMismatchError):
                device.failure_count += 1
                device.is_active = False
                device.disabled_at = now
                logger.warning(
                    "Android 푸시 기기 주소가 만료되었습니다: device_id_hash=%s",
                    _device_id_hash(device.id),
                )
            except Exception as exc:
                device.failure_count += 1
                logger.warning(
                    "Android 푸시 전송 실패: device_id_hash=%s error_type=%s",
                    _device_id_hash(device.id),
                    type(exc).__name__,
                )
        db.commit()
    return sent_count


def send_voice_call_mobile_push(
    user_ids: Iterable[UUID],
    *,
    payload: dict[str, Any],
    ttl_seconds: int = MOBILE_CALL_PUSH_TTL_SECONDS,
) -> int:
    return send_mobile_push_to_users(
        user_ids,
        payload=payload,
        ttl_seconds=ttl_seconds,
    )
