from __future__ import annotations

import json
import logging
from datetime import timezone
from typing import Iterable
from uuid import UUID

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import LoginSession, PushSubscription, User, utcnow

logger = logging.getLogger(__name__)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def send_web_push_to_users(
    user_ids: Iterable[UUID],
    *,
    room_id: UUID | None = None,
    is_test: bool = False,
) -> int:
    """Send a privacy-safe notification after the chat transaction has committed."""
    recipient_ids = set(user_ids)
    if not settings.web_push_active or not recipient_ids:
        return 0

    now = utcnow()
    sent_count = 0
    payload = {
        "title": "MESIL_Chat",
        "body": (
            "휴대전화 알림이 정상적으로 연결되었습니다."
            if is_test
            else "새 메시지가 도착했습니다."
        ),
        "url": f"/?room={room_id}" if room_id else "/",
        "tag": "mesil-chat-test" if is_test else f"mesil-chat-room-{room_id}",
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
                    ttl=300,
                    timeout=15,
                )
                subscription.failure_count = 0
                subscription.last_success_at = now
                sent_count += 1
            except WebPushException as exc:
                subscription.failure_count += 1
                status_code = getattr(getattr(exc, "response", None), "status_code", None)
                logger.warning(
                    "Web Push 전송 실패: subscription_id=%s status=%s error_type=%s",
                    subscription.id,
                    status_code,
                    type(exc).__name__,
                )
                if status_code in {404, 410} or subscription.failure_count >= 5:
                    subscription.is_active = False
                    subscription.disabled_at = now
            except Exception as exc:
                # Background notification failure must never delay or roll back a chat message.
                subscription.failure_count += 1
                logger.warning(
                    "Web Push 전송 오류: subscription_id=%s error_type=%s",
                    subscription.id,
                    type(exc).__name__,
                )
                if subscription.failure_count >= 5:
                    subscription.is_active = False
                    subscription.disabled_at = now
        db.commit()
    return sent_count
