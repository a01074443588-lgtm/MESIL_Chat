from __future__ import annotations

import json
import logging
from datetime import timezone
from hashlib import sha256
from typing import Any, Iterable, Literal
from urllib.parse import urlencode
from uuid import UUID

from pywebpush import WebPushException, webpush
from sqlalchemy import select

from .config import settings
from .database import SessionLocal
from .models import LoginSession, PushSubscription, Staff, User, utcnow
from .mobile_push import send_mobile_push_to_users, send_voice_call_mobile_push

logger = logging.getLogger(__name__)

WEB_PUSH_TTL_SECONDS = 24 * 60 * 60
VOICE_CALL_WEB_PUSH_TTL_SECONDS = 60
_TRANSIENT_WEB_PUSH_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def voice_call_ttl_seconds(expires_at_ms: int, *, now_ms: int | None = None) -> int:
    current = now_ms if now_ms is not None else int(utcnow().timestamp() * 1000)
    remaining_ms = expires_at_ms - current
    if remaining_ms <= 0:
        return 0
    return min(VOICE_CALL_WEB_PUSH_TTL_SECONDS, max(1, (remaining_ms + 999) // 1000))


def _web_push_status_code(exc: WebPushException) -> int | None:
    return getattr(getattr(exc, "response", None), "status_code", None)


def _is_transient_web_push_failure(exc: WebPushException) -> bool:
    status_code = _web_push_status_code(exc)
    return status_code is None or status_code in _TRANSIENT_WEB_PUSH_STATUS_CODES


def _send_web_push_payload(
    user_ids: Iterable[UUID],
    *,
    payload: dict[str, Any],
    ttl_seconds: int = WEB_PUSH_TTL_SECONDS,
    endpoint: str | None = None,
) -> int:
    recipient_ids = set(user_ids)
    if not settings.web_push_active or not recipient_ids:
        return 0

    now = utcnow()
    sent_count = 0
    with SessionLocal() as db:
        subscription_query = (
            select(PushSubscription)
            .join(User, User.id == PushSubscription.user_id)
            .join(Staff, Staff.id == User.staff_id)
            .join(LoginSession, LoginSession.id == PushSubscription.login_session_id)
            .where(
                PushSubscription.user_id.in_(recipient_ids),
                PushSubscription.is_active.is_(True),
                User.is_active.is_(True),
                User.organization_id == Staff.organization_id,
                Staff.is_active.is_(True),
                Staff.deleted_at.is_(None),
                Staff.employment_status == "active",
                LoginSession.revoked_at.is_(None),
            )
        )
        if endpoint is not None:
            subscription_query = subscription_query.where(
                PushSubscription.endpoint_hash
                == sha256(endpoint.encode("utf-8")).hexdigest()
            )
        subscriptions = db.scalars(subscription_query).all()

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
                        ttl=ttl_seconds,
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


def send_web_push_to_users(
    user_ids: Iterable[UUID],
    *,
    room_id: UUID | None = None,
    message_id: UUID | None = None,
    comment_id: UUID | None = None,
    notification_kind: Literal["message", "comment"] = "message",
    is_test: bool = False,
    endpoint: str | None = None,
) -> int:
    """Send a privacy-safe notification after the chat transaction has committed."""
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
    recipient_ids = set(user_ids)
    sent_count = _send_web_push_payload(
        recipient_ids,
        payload=payload,
        endpoint=endpoint,
    )
    if not is_test:
        sent_count += send_mobile_push_to_users(
            recipient_ids,
            payload=payload,
            ttl_seconds=WEB_PUSH_TTL_SECONDS,
        )
    return sent_count


def send_voice_call_web_push(
    user_ids: Iterable[UUID],
    *,
    call_id: UUID,
    room_id: UUID,
    caller_user_id: UUID,
    caller_name: str,
    call_mode: Literal["audio", "video"],
    member_count: int,
    cancelled: bool = False,
    expires_at_ms: int | None = None,
) -> int:
    """Notify a hidden or closed web app about a short-lived incoming call."""
    tag = f"mesil-chat-call-{call_id}"
    if cancelled:
        payload = {
            "title": "MESIL_Chat",
            "body": "통화 요청이 종료되었습니다.",
            "url": "/",
            "tag": tag,
            "kind": "voice_call_cancel",
            "event": "voice_call_cancel",
            "call_id": str(call_id),
            "room_id": str(room_id),
        }
    else:
        call_label = "영상통화" if call_mode == "video" else "음성통화"
        expires_at = expires_at_ms or int(utcnow().timestamp() * 1000) + 50_000
        target_url = "/?" + urlencode(
            {
                "room": str(room_id),
                "call": str(call_id),
                "call_mode": call_mode,
                "call_expires": expires_at,
            }
        )
        payload = {
            "title": f"MESIL_Chat {call_label}",
            "body": "통화 요청이 도착했습니다.",
            "url": target_url,
            "tag": tag,
            "kind": "voice_call",
            "event": "voice_call_invite",
            "call_id": str(call_id),
            "room_id": str(room_id),
            "call_mode": call_mode,
            "member_count": member_count,
            "expires_at": expires_at,
        }
    ttl_seconds = (
        VOICE_CALL_WEB_PUSH_TTL_SECONDS
        if cancelled
        else voice_call_ttl_seconds(expires_at)
    )
    if ttl_seconds <= 0:
        return 0
    return _send_web_push_payload(
        user_ids,
        payload=payload,
        ttl_seconds=ttl_seconds,
    )


def send_voice_call_notifications(
    user_ids: Iterable[UUID],
    **kwargs: Any,
) -> int:
    recipient_ids = set(user_ids)
    if not recipient_ids:
        return 0

    call_id = kwargs["call_id"]
    cancelled = bool(kwargs.get("cancelled", False))
    mobile_payload = build_voice_call_mobile_payload(**kwargs)
    # 모바일 네이티브 거절 토큰은 Web Push 계약에 전달하지 않는다. Web
    # 알림은 로그인 세션이 있는 브라우저 흐름을 유지하고, Android FCM만
    # 이름·방·사용자 정보가 없는 불투명 토큰을 사용한다.
    web_kwargs = {
        key: value
        for key, value in kwargs.items()
        if key != "mobile_action_token"
    }

    sent_count = 0
    # Android is the lock-screen delivery path.  A slow or broken browser
    # subscription must never prevent the native invite/cancel from starting.
    try:
        sent_count += send_voice_call_mobile_push(
            recipient_ids,
            payload=mobile_payload,
        )
    except Exception as exc:
        logger.warning(
            "Android 통화 푸시 경로 실패: call_id_hash=%s error_type=%s",
            sha256(str(call_id).encode("utf-8")).hexdigest()[:12],
            type(exc).__name__,
        )
    try:
        sent_count += send_voice_call_web_push(recipient_ids, **web_kwargs)
    except Exception as exc:
        logger.warning(
            "브라우저 통화 푸시 경로 실패: call_id_hash=%s error_type=%s",
            sha256(str(call_id).encode("utf-8")).hexdigest()[:12],
            type(exc).__name__,
        )
    return sent_count


def build_voice_call_mobile_payload(**kwargs: Any) -> dict[str, Any]:
    """Build the privacy-minimal native call payload from one server expiry."""

    call_id = kwargs["call_id"]
    cancelled = bool(kwargs.get("cancelled", False))
    if cancelled:
        return {
            "title": "MESIL_Chat",
            "body": "통화 요청이 종료되었습니다.",
            "tag": f"mesil-chat-call-{call_id}",
            "kind": "voice_call_cancel",
            "event": "voice_call_cancel",
            "call_id": str(call_id),
            "action_token": kwargs.get("mobile_action_token"),
        }
    call_mode = kwargs["call_mode"]
    call_label = "영상통화" if call_mode == "video" else "음성통화"
    expires_at = int(
        kwargs.get("expires_at_ms")
        or int(utcnow().timestamp() * 1000) + 50_000
    )
    return {
        "title": f"MESIL_Chat {call_label}",
        "body": "로그인 후 발신자를 확인해 주세요.",
        "tag": f"mesil-chat-call-{call_id}",
        "kind": "voice_call",
        "event": "voice_call_invite",
        "call_id": str(call_id),
        "call_state": "ringing",
        "action_token": kwargs["mobile_action_token"],
        "expires_at": expires_at,
    }
