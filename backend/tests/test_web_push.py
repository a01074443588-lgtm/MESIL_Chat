from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.push import send_web_push_to_users


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

        tested = client.post("/api/push/test", json={}, headers=ORIGIN)
        assert tested.status_code == 202, tested.text
        assert len(sent_payloads) == 1

        assert send_web_push_to_users({user_id}, is_test=True) == 1
        assert len(sent_payloads) == 2
        assert "새 메시지" not in sent_payloads[0]["data"]
        assert "정상적으로 연결" in sent_payloads[0]["data"]

        logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert logout.status_code == 204, logout.text

        assert send_web_push_to_users({user_id}, is_test=True) == 0
        assert len(sent_payloads) == 2
