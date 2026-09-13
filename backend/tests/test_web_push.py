import json
from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

from fastapi.testclient import TestClient
from pywebpush import WebPushException

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import User
from app.push import (
    VOICE_CALL_WEB_PUSH_TTL_SECONDS,
    WEB_PUSH_TTL_SECONDS,
    send_voice_call_web_push,
    send_web_push_to_users,
)
from app.security import hash_password


ORIGIN = {"origin": "http://testserver"}


def test_voice_call_push_opens_the_call_and_cancel_closes_the_same_tag(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "test-vapid-private.pem"
    private_key.write_text("test-only-key", encoding="utf-8")
    monkeypatch.setattr(settings, "web_push_enabled", True)
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "B" + "A" * 86)
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key_path",
        private_key.as_posix(),
    )
    sent_payloads = []
    monkeypatch.setattr("app.push.webpush", lambda **kwargs: sent_payloads.append(kwargs))
    fixed_now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    monkeypatch.setattr("app.push.utcnow", lambda: fixed_now)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        registered = client.post(
            "/api/push/subscriptions",
            json={
                "endpoint": "https://push.example.test/voice-call-device",
                "expiration_time": None,
                "keys": {"p256dh": "p" * 80, "auth": "a" * 24},
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text

        call_id = uuid4()
        room_id = uuid4()
        caller_id = uuid4()
        assert send_voice_call_web_push(
            {user_id},
            call_id=call_id,
            room_id=room_id,
            caller_user_id=caller_id,
            caller_name="통화 시험 직원",
            call_mode="video",
            member_count=2,
            expires_at_ms=int(fixed_now.timestamp() * 1000) + 37_000,
        ) == 1
        incoming = json.loads(sent_payloads[-1]["data"])
        incoming_query = parse_qs(urlparse(incoming["url"]).query)
        assert incoming["kind"] == "voice_call"
        assert incoming["event"] == "voice_call_invite"
        assert incoming["tag"] == f"mesil-chat-call-{call_id}"
        assert incoming_query["room"] == [str(room_id)]
        assert incoming_query["call"] == [str(call_id)]
        assert incoming_query["caller"] == [str(caller_id)]
        assert incoming_query["call_mode"] == ["video"]
        assert sent_payloads[-1]["ttl"] == 37

        assert send_voice_call_web_push(
            {user_id},
            call_id=call_id,
            room_id=room_id,
            caller_user_id=caller_id,
            caller_name="통화 시험 직원",
            call_mode="video",
            member_count=2,
            cancelled=True,
        ) == 1
        cancelled = json.loads(sent_payloads[-1]["data"])
        assert cancelled["kind"] == "voice_call_cancel"
        assert cancelled["event"] == "voice_call_cancel"
        assert cancelled["tag"] == incoming["tag"]
        assert sent_payloads[-1]["ttl"] == VOICE_CALL_WEB_PUSH_TTL_SECONDS
        logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert logout.status_code == 204, logout.text


def test_leave_staff_does_not_receive_push_and_returns_to_existing_subscription(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "test-vapid-private.pem"
    private_key.write_text("test-only-key", encoding="utf-8")
    monkeypatch.setattr(settings, "web_push_enabled", True)
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "B" + "A" * 86)
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key_path",
        private_key.as_posix(),
    )
    sent_payloads = []
    monkeypatch.setattr("app.push.webpush", lambda **kwargs: sent_payloads.append(kwargs))

    username = "leave-push-user"
    password = "LeavePush!234"
    with TestClient(app) as admin_client:
        admin_login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert admin_login.status_code == 200, admin_login.text
        created = admin_client.post(
            "/api/employees",
            json={
                "username": username,
                "full_name": "휴직 알림 시험직원",
                "password": password,
                "role": "staff",
                "employee_code": "LEAVE-PUSH-001",
            },
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        user_id = created.json()["id"]

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.must_change_password = False
            user.password_hash = hash_password(password)
            db.commit()

        employee_client = TestClient(app)
        login = employee_client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        registered = employee_client.post(
            "/api/push/subscriptions",
            json={
                "endpoint": "https://push.example.test/leave-policy-device",
                "expiration_time": None,
                "keys": {"p256dh": "p" * 80, "auth": "a" * 24},
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text
        assert send_web_push_to_users({user_id}, is_test=True) == 1

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.staff is not None
            user.staff.employment_status = "leave"
            db.commit()
        assert send_web_push_to_users({user_id}, is_test=True) == 0

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.staff is not None
            user.staff.employment_status = "active"
            db.commit()
        assert send_web_push_to_users({user_id}, is_test=True) == 1
        assert len(sent_payloads) == 2


def test_session_bound_web_push_subscription_stops_after_logout(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "test-vapid-private.pem"
    private_key.write_text("test-only-key", encoding="utf-8")
    monkeypatch.setattr(settings, "web_push_enabled", True)
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "B" + "A" * 86)
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key_path",
        private_key.as_posix(),
    )
    sent_payloads = []

    def fake_webpush(**kwargs):
        sent_payloads.append(kwargs)

    monkeypatch.setattr("app.push.webpush", fake_webpush)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]

        config = client.get("/api/push/config")
        assert config.status_code == 200, config.text
        assert config.json()["enabled"] is True

        registered = client.post(
            "/api/push/subscriptions",
            json={
                "endpoint": "https://push.example.test/device-token",
                "expiration_time": None,
                "keys": {
                    "p256dh": "p" * 80,
                    "auth": "a" * 24,
                },
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["active"] is True
        assert registered.json()["resubscribe_required"] is False

        tested = client.post("/api/push/test", json={}, headers=ORIGIN)
        assert tested.status_code == 202, tested.text
        assert len(sent_payloads) == 1

        assert send_web_push_to_users({user_id}, is_test=True) == 1
        assert len(sent_payloads) == 2
        assert "새 메시지" not in sent_payloads[0]["data"]
        assert "정상적으로 연결" in sent_payloads[0]["data"]
        assert sent_payloads[0]["ttl"] == WEB_PUSH_TTL_SECONDS

        logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert logout.status_code == 204, logout.text

        assert send_web_push_to_users({user_id}, is_test=True) == 0
        assert len(sent_payloads) == 2


def test_web_push_retries_once_for_temporary_failure(monkeypatch, tmp_path):
    private_key = tmp_path / "test-vapid-private.pem"
    private_key.write_text("test-only-key", encoding="utf-8")
    monkeypatch.setattr(settings, "web_push_enabled", True)
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "B" + "A" * 86)
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key_path",
        private_key.as_posix(),
    )
    sent_payloads = []

    def temporary_failure_then_success(**kwargs):
        sent_payloads.append(kwargs)
        if len(sent_payloads) == 1:
            raise WebPushException(
                "temporary",
                response=SimpleNamespace(status_code=503),
            )

    monkeypatch.setattr("app.push.webpush", temporary_failure_then_success)

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        registered = client.post(
            "/api/push/subscriptions",
            json={
                "endpoint": "https://push.example.test/retry-device-token",
                "expiration_time": None,
                "keys": {
                    "p256dh": "p" * 80,
                    "auth": "a" * 24,
                },
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text

        tested = client.post("/api/push/test", json={}, headers=ORIGIN)
        assert tested.status_code == 202, tested.text
        assert len(sent_payloads) == 2
        assert all(
            payload["ttl"] == WEB_PUSH_TTL_SECONDS
            for payload in sent_payloads
        )

        room_id = uuid4()
        message_id = uuid4()
        comment_id = uuid4()
        assert (
            send_web_push_to_users(
                {user_id},
                room_id=room_id,
                message_id=message_id,
                comment_id=comment_id,
                notification_kind="comment",
            )
            == 1
        )
        comment_payload = json.loads(sent_payloads[-1]["data"])
        assert comment_payload["body"] == "새 댓글이 도착했습니다."
        assert comment_payload["kind"] == "comment"
        assert comment_payload["url"] == (
            f"/?room={room_id}&message={message_id}"
        )
        assert comment_payload["tag"] == f"mesil-chat-comment-{comment_id}"

        next_comment_id = uuid4()
        assert (
            send_web_push_to_users(
                {user_id},
                room_id=room_id,
                message_id=message_id,
                comment_id=next_comment_id,
                notification_kind="comment",
            )
            == 1
        )
        next_comment_payload = json.loads(sent_payloads[-1]["data"])
        assert next_comment_payload["url"] == comment_payload["url"]
        assert next_comment_payload["tag"] == (
            f"mesil-chat-comment-{next_comment_id}"
        )
        assert next_comment_payload["tag"] != comment_payload["tag"]


def test_expired_push_endpoint_requires_new_browser_subscription(
    monkeypatch,
    tmp_path,
):
    private_key = tmp_path / "test-vapid-private.pem"
    private_key.write_text("test-only-key", encoding="utf-8")
    monkeypatch.setattr(settings, "web_push_enabled", True)
    monkeypatch.setattr(settings, "web_push_vapid_public_key", "B" + "A" * 86)
    monkeypatch.setattr(
        settings,
        "web_push_vapid_private_key_path",
        private_key.as_posix(),
    )
    attempted_endpoints = []

    def expired_endpoint(**kwargs):
        attempted_endpoints.append(kwargs["subscription_info"]["endpoint"])
        raise WebPushException(
            "expired",
            response=SimpleNamespace(status_code=410),
        )

    monkeypatch.setattr("app.push.webpush", expired_endpoint)
    stale_endpoint = "https://push.example.test/expired-device-token"

    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        subscription_payload = {
            "endpoint": stale_endpoint,
            "expiration_time": None,
            "keys": {
                "p256dh": "p" * 80,
                "auth": "a" * 24,
            },
        }
        registered = client.post(
            "/api/push/subscriptions",
            json=subscription_payload,
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text

        tested = client.post("/api/push/test", json={}, headers=ORIGIN)
        assert tested.status_code == 502, tested.text
        assert attempted_endpoints.count(stale_endpoint) == 1
        assert send_web_push_to_users({user_id}, is_test=True) == 0

        stale_reregistered = client.post(
            "/api/push/subscriptions",
            json=subscription_payload,
            headers=ORIGIN,
        )
        assert stale_reregistered.status_code == 201, stale_reregistered.text
        assert stale_reregistered.json() == {
            "enabled": True,
            "active": False,
            "message": (
                "이 기기의 알림 주소가 만료되었습니다. "
                "기존 알림을 해제하고 새 알림 주소를 만들어야 합니다."
            ),
            "resubscribe_required": True,
            "reason_code": "endpoint_expired",
        }

        fresh_registration = client.post(
            "/api/push/subscriptions",
            json={
                **subscription_payload,
                "endpoint": "https://push.example.test/fresh-device-token",
            },
            headers=ORIGIN,
        )
        assert fresh_registration.status_code == 201, fresh_registration.text
        assert fresh_registration.json()["active"] is True
        assert fresh_registration.json()["resubscribe_required"] is False
