import json
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from pywebpush import WebPushException

from app.config import settings
from app.main import app
from app.push import WEB_PUSH_TTL_SECONDS, send_web_push_to_users


ORIGIN = {"origin": "http://testserver"}


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
        assert (
            send_web_push_to_users(
                {user_id},
                room_id=room_id,
                message_id=message_id,
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
