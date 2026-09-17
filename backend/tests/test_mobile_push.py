from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.mobile_push import MOBILE_CALL_PUSH_TTL_SECONDS, send_mobile_push_to_users
from app.models import LoginSession, MobilePushDevice, User, utcnow
from app.push import (
    WEB_PUSH_TTL_SECONDS,
    send_voice_call_notifications,
    send_web_push_to_users,
    voice_call_ttl_seconds,
)


ORIGIN = {"origin": "http://testserver"}


def _enable_mobile_push(monkeypatch, tmp_path) -> None:
    credentials_file = tmp_path / "firebase-service-account.json"
    credentials_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(settings, "firebase_project_id", "mesil-test")
    monkeypatch.setattr(
        settings,
        "firebase_credentials_file",
        credentials_file.as_posix(),
    )


def test_android_push_device_is_bound_to_the_login_session(monkeypatch, tmp_path):
    _enable_mobile_push(monkeypatch, tmp_path)
    sent = []

    def capture(token, *, payload, ttl_seconds):
        sent.append((token, payload, ttl_seconds))

    monkeypatch.setattr("app.mobile_push._send_data_message", capture)
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        registered = client.post(
            "/api/mobile-push/devices",
            json={
                "installation_id": "5a215df7-8fb9-4ac8-9a54-251b1481eb30",
                "token": "test-fcm-token-" + "x" * 64,
                "platform": "android",
                "app_version": "1.0",
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text
        assert registered.json()["enabled"] is True
        assert registered.json()["active"] is True

        payload = {"kind": "voice_call", "call_id": str(uuid4())}
        assert send_mobile_push_to_users({user_id}, payload=payload) == 1
        assert sent[-1][1] == payload
        assert sent[-1][2] == MOBILE_CALL_PUSH_TTL_SECONDS

        logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert logout.status_code == 204, logout.text
        assert send_mobile_push_to_users({user_id}, payload=payload) == 0
        assert len(sent) == 1


def test_android_push_stops_for_deactivated_user_and_expired_session(
    monkeypatch,
    tmp_path,
):
    _enable_mobile_push(monkeypatch, tmp_path)
    sent = []

    def capture(token, *, payload, ttl_seconds):
        sent.append((token, payload, ttl_seconds))

    monkeypatch.setattr("app.mobile_push._send_data_message", capture)
    installation_id = "bd2986e8-aeaa-4dc7-bf84-af9e81d10bc7"
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        user_id = login.json()["user"]["id"]
        registered = client.post(
            "/api/mobile-push/devices",
            json={
                "installation_id": installation_id,
                "token": "test-fcm-token-" + "y" * 64,
                "platform": "android",
                "app_version": "1.0",
            },
            headers=ORIGIN,
        )
        assert registered.status_code == 201, registered.text

        payload = {"kind": "voice_call", "call_id": str(uuid4())}
        assert send_mobile_push_to_users({user_id}, payload=payload) == 1

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.is_active = False
            db.commit()
        assert send_mobile_push_to_users({user_id}, payload=payload) == 0

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None
            user.is_active = True
            device = db.scalar(
                select(MobilePushDevice).where(
                    MobilePushDevice.installation_id == installation_id
                )
            )
            assert device is not None
            login_session = db.get(LoginSession, device.login_session_id)
            assert login_session is not None
            login_session.expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        assert send_mobile_push_to_users({user_id}, payload=payload) == 0
        with SessionLocal() as db:
            device = db.scalar(
                select(MobilePushDevice).where(
                    MobilePushDevice.installation_id == installation_id
                )
            )
            assert device is not None
            assert device.is_active is False
            assert device.disabled_at is not None
        assert len(sent) == 1


def test_transient_mobile_failures_do_not_permanently_disable_device(monkeypatch, tmp_path):
    _enable_mobile_push(monkeypatch, tmp_path)
    monkeypatch.setattr(
        "app.mobile_push._send_data_message",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TimeoutError("synthetic_timeout")),
    )
    installation_id = "bd2986e8-aeaa-4dc7-bf84-af9e81d10bc8"
    with TestClient(app) as client:
        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        user_id = login.json()["user"]["id"]
        assert client.post(
            "/api/mobile-push/devices",
            json={
                "installation_id": installation_id,
                "token": "test-fcm-token-" + "z" * 64,
                "platform": "android",
                "app_version": "1.0",
            },
            headers=ORIGIN,
        ).status_code == 201
        for _ in range(6):
            assert send_mobile_push_to_users(
                {user_id}, payload={"kind": "voice_call", "call_id": str(uuid4())}
            ) == 0

    with SessionLocal() as db:
        device = db.scalar(select(MobilePushDevice).where(
            MobilePushDevice.installation_id == installation_id
        ))
        assert device is not None
        assert device.failure_count == 6
        assert device.is_active is True
        assert device.disabled_at is None


def test_registration_does_not_report_ready_when_mobile_push_is_disabled(monkeypatch):
    monkeypatch.setattr(settings, "firebase_project_id", None)
    with TestClient(app) as client:
        client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        )
        response = client.post(
            "/api/mobile-push/devices",
            json={
                "installation_id": "bd2986e8-aeaa-4dc7-bf84-af9e81d10bc9",
                "token": "test-fcm-token-" + "q" * 64,
                "platform": "android",
                "app_version": "1.0",
            },
            headers=ORIGIN,
        )
    assert response.status_code == 201
    assert response.json()["enabled"] is False
    assert response.json()["active"] is False


def test_voice_call_notification_builds_native_invite_and_cancel(monkeypatch):
    captured = []
    monkeypatch.setattr("app.push.send_voice_call_web_push", lambda *_a, **_k: 0)
    monkeypatch.setattr(
        "app.push.send_voice_call_mobile_push",
        lambda user_ids, *, payload: captured.append((set(user_ids), payload)) or 1,
    )
    recipient_id = uuid4()
    call_id = uuid4()
    room_id = uuid4()
    caller_id = uuid4()
    common = {
        "call_id": call_id,
        "room_id": room_id,
        "caller_user_id": caller_id,
        "caller_name": "통화 시험 직원",
        "call_mode": "audio",
        "member_count": 2,
        "mobile_action_token": "opaque-native-action-token",
    }

    assert send_voice_call_notifications({recipient_id}, **common) == 1
    incoming = captured[-1][1]
    assert incoming["kind"] == "voice_call"
    assert incoming["event"] == "voice_call_invite"
    assert incoming["call_id"] == str(call_id)
    assert incoming["call_state"] == "ringing"
    assert incoming["action_token"] == "opaque-native-action-token"
    assert incoming["expires_at"] > 0
    assert "room_id" not in incoming
    assert "caller_user_id" not in incoming
    assert "caller_name" not in incoming
    assert "member_count" not in incoming
    assert incoming["body"] == "로그인 후 발신자를 확인해 주세요."

    assert send_voice_call_notifications(
        {recipient_id},
        **common,
        cancelled=True,
    ) == 1
    cancelled = captured[-1][1]
    assert cancelled["kind"] == "voice_call_cancel"
    assert cancelled["tag"] == incoming["tag"]


def test_voice_call_mobile_delivery_survives_web_push_exception(monkeypatch, caplog):
    captured = []

    def fail_web(*_args, **_kwargs):
        raise TimeoutError("synthetic_web_push_timeout")

    monkeypatch.setattr("app.push.send_voice_call_web_push", fail_web)
    monkeypatch.setattr(
        "app.push.send_voice_call_mobile_push",
        lambda user_ids, *, payload: captured.append((set(user_ids), payload)) or 1,
    )
    recipient_id = uuid4()

    call_id = uuid4()
    private_name = "로그에 남으면 안 되는 합성 이름"
    private_token = "synthetic-opaque-action-token"
    assert send_voice_call_notifications(
        {recipient_id},
        call_id=call_id,
        room_id=uuid4(),
        caller_user_id=uuid4(),
        caller_name=private_name,
        call_mode="audio",
        member_count=2,
        mobile_action_token=private_token,
    ) == 1
    assert len(captured) == 1
    assert captured[0][0] == {recipient_id}
    assert captured[0][1]["kind"] == "voice_call"
    assert private_name not in caplog.text
    assert private_token not in caplog.text
    assert str(call_id) not in caplog.text


def test_voice_call_paths_share_the_persisted_expiry(monkeypatch):
    captured_web = []
    captured_mobile = []
    expires_at_ms = 2_000_000_050_000
    monkeypatch.setattr(
        "app.push.send_voice_call_web_push",
        lambda user_ids, **kwargs: captured_web.append((set(user_ids), kwargs)) or 1,
    )
    monkeypatch.setattr(
        "app.push.send_voice_call_mobile_push",
        lambda user_ids, *, payload: captured_mobile.append((set(user_ids), payload)) or 1,
    )

    assert send_voice_call_notifications(
        {uuid4()},
        call_id=uuid4(),
        room_id=uuid4(),
        caller_user_id=uuid4(),
        caller_name="합성 발신자",
        call_mode="video",
        member_count=2,
        mobile_action_token="synthetic-action-token",
        expires_at_ms=expires_at_ms,
    ) == 2
    assert captured_web[0][1]["expires_at_ms"] == expires_at_ms
    assert captured_mobile[0][1]["expires_at"] == expires_at_ms


def test_voice_call_ttl_never_outlives_the_persisted_expiry():
    assert voice_call_ttl_seconds(50_000, now_ms=0) == 50
    assert voice_call_ttl_seconds(50_001, now_ms=1) == 50
    assert voice_call_ttl_seconds(50_001, now_ms=50_000) == 1
    assert voice_call_ttl_seconds(50_000, now_ms=50_000) == 0
    assert voice_call_ttl_seconds(50_000, now_ms=50_001) == 0


def test_chat_notification_is_sent_to_web_and_android(monkeypatch):
    captured_web = []
    captured_mobile = []
    monkeypatch.setattr(
        "app.push._send_web_push_payload",
        lambda user_ids, *, payload, ttl_seconds=WEB_PUSH_TTL_SECONDS: (
            captured_web.append((set(user_ids), payload, ttl_seconds)) or 1
        ),
    )
    monkeypatch.setattr(
        "app.push.send_mobile_push_to_users",
        lambda user_ids, *, payload, ttl_seconds: (
            captured_mobile.append((set(user_ids), payload, ttl_seconds)) or 1
        ),
    )
    recipient_id = uuid4()
    room_id = uuid4()

    assert send_web_push_to_users({recipient_id}, room_id=room_id) == 2
    assert captured_web[-1][1]["kind"] == "message"
    assert captured_mobile[-1][1] == captured_web[-1][1]
    assert captured_mobile[-1][2] == WEB_PUSH_TTL_SECONDS

    captured_mobile.clear()
    assert send_web_push_to_users({recipient_id}, is_test=True) == 1
    assert captured_mobile == []
