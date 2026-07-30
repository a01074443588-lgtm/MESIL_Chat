from __future__ import annotations

import json
import logging
from datetime import timezone
from typing import Iterable, Literal
from uuid import UUID

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import LoginSession, PushSubscription, User, utcnow

logger = logging.getLogger(__name__)

WEB_PUSH_TTL_SECONDS = 24 * 60 * 60
_TRANSIENT_WEB_PUSH_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _web_push_status_code(exc: WebPushException) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_transient_web_push_failure(exc: WebPushException) -> bool:
    status_code = _web_push_status_code(exc)
    return status_code is None or status_code in _TRANSIENT_WEB_PUSH_STATUS_CODES


def send_web_push_to_users(
    user_ids: Iterable[UUID],
    *,
    room_id: UUID | None = None,
    message_id: UUID | None = None,
    comment_id: UUID | None = None,
    notification_kind: Literal["message", "comment"] = "message",
    is_test: bool = False,
) -> int:
    """Send a privacy-safe notification after the chat transaction has committed."""
    recipient_ids = set(user_ids)
    if not settings.web_push_active or not recipient_ids:
        return 0

    now = utcnow()
    sent_count = 0
    if is_test:
        body = "휴대전화 알림이 정상적으로 연결되었습니다."
        tag = "mesil-chat-test"
        kind = "test"
    elif notification_kind == "comment":
        body = "새 댓글이 도착했습니다."
        tag = f"mesil-chat-comment-{comment_id or message_id or room_id}"
        kind = "comment"
    else:
        body = "새 메시지가 도착했습니다."
        tag = f"mesil-chat-room-{room_id}"
        kind = "message"

    query_parts: list[str] = []
    if room_id is not None:
        query_parts.append(f"room={room_id}")
    if message_id is not None:
        query_parts.append(f"message={message_id}")
    target_url = f"/?{'&'.join(query_parts)}" if query_parts else "/"
    payload = {
        "title": "MESIL_Chat",
        "body": body,
        "url": target_url,
        "tag": tag,
        "kind": kind,
    }

    with SessionLocal() as db:
        subscriptions = db.scalars(
            select(PushSubscription)
            .join(User, User.id == PushSubscription.user_id)
            .join(LoginSession, LoginSession.id == PushSubscription.login_session_id)
            .where(
                PushSubscription.user_id.in_(recipient_ids),
                PushSubscription.is_active.is_(True),
                User.is_active.is_(True),
                LoginSession.revoked_at.is_(None),
            )
        ).all()

        for subscription in subscriptions:
            login_session = db.get(LoginSession, subscription.login_session_id)
            if login_session is None or _as_utc(login_session.expires_at) <= now:
                subscription.is_active = False
                subscription.disabled_at = now
                continue
            failure: Exception | None = None
            for attempt in range(2):
                try:
                    webpush(
                        subscription_info={
                            "endpoint": subscription.endpoint,
                            "keys": {
                                "p256dh": subscription.p256dh,
                                "auth": subscription.auth,
                            },
                        },
                        data=json.dumps(payload, ensure_ascii=False),
                        vapid_private_key=settings.web_push_vapid_private_key_path,
                        vapid_claims={"sub": settings.web_push_vapid_subject},
                        ttl=WEB_PUSH_TTL_SECONDS,
                        timeout=15,
                    )
                    subscription.failure_count = 0
                    subscription.last_success_at = now
                    sent_count += 1
                    failure = None
                    break
                except WebPushException as exc:
                    failure = exc
                    status_code = _web_push_status_code(exc)
                    if status_code in {404, 410}:
                        break
                    if attempt == 0 and _is_transient_web_push_failure(exc):
                        logger.info(
                            "Web Push 일시 오류 1회 재시도: subscription_id=%s status=%s",
                            subscription.id,
                            status_code,
                        )
                        continue
                    break
                except Exception as exc:
                    # A transport failure without an HTTP response can be temporary.
                    failure = exc
                    if attempt == 0:
                        logger.info(
                            "Web Push 전송 오류 1회 재시도: subscription_id=%s "
                            "error_type=%s",
                            subscription.id,
                            type(exc).__name__,
                        )
                        continue
                    break

            if failure is not None:
                subscription.failure_count += 1
                if isinstance(failure, WebPushException):
                    status_code = _web_push_status_code(failure)
                    logger.warning(
                        "Web Push 전송 실패: subscription_id=%s status=%s "
                        "error_type=%s",
                        subscription.id,
                        status_code,
                        type(failure).__name__,
                    )
                    if status_code in {404, 410}:
                        subscription.is_active = False
                        subscription.disabled_at = now
                else:
                    logger.warning(
                        "Web Push 전송 오류: subscription_id=%s error_type=%s",
                        subscription.id,
                        type(failure).__name__,
                    )
                if subscription.failure_count >= 5:
                    subscription.is_active = False
                    subscription.disabled_at = now
        db.commit()
    return sent_count
