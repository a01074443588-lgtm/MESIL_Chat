import base64
import hashlib
import hmac
import threading
from types import SimpleNamespace
from uuid import uuid4

from fastapi import Response
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app import main as main_module
from app.models import AuditEvent, VoiceCallInvitation
from sqlalchemy import select
from app.realtime import manager
from app import voice_call_turn


ORIGIN = {"origin": "http://testserver"}


def _post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def _login(client: TestClient, username: str, password: str) -> dict:
    response = _post(
        client,
        "/api/auth/login",
        {"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def _create_and_activate_staff(
    admin: TestClient,
    client: TestClient,
    *,
    username: str,
    full_name: str,
    temporary_password: str,
    password: str,
) -> dict:
    created = _post(
        admin,
        "/api/employees",
        {
            "username": username,
            "full_name": full_name,
            "password": temporary_password,
            "role": "staff",
        },
    )
    assert created.status_code == 201, created.text
    _login(client, username, temporary_password)
    changed = _post(
        client,
        "/api/auth/password",
        {
            "current_password": temporary_password,
            "new_password": password,
        },
    )
    assert changed.status_code == 200, changed.text
    return created.json()


def test_voice_call_config_is_authenticated_and_not_cached(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    monkeypatch.setattr(settings, "voice_call_stun_urls", "stun:stun.cloudflare.com:3478")
    with TestClient(app) as anonymous:
        assert anonymous.get("/api/voice-calls/config").status_code == 401

    with TestClient(app) as admin:
        _login(admin, "admin", "AdminPass!234")
        response = admin.get("/api/voice-calls/config")
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json() == {
            "enabled": True,
            "max_participants": settings.voice_call_max_participants,
            "max_video_participants": settings.voice_call_max_video_participants,
            "ice_servers": [
                {"urls": ["stun:stun.cloudflare.com:3478"], "username": None, "credential": None}
            ],
        }


def test_disabled_voice_call_config_returns_no_ice_servers(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", False)
    response = main_module.voice_call_config(
        Response(),
        SimpleNamespace(id=uuid4()),
    )

    assert response.enabled is False
    assert response.ice_servers == []


def test_reviewer_voice_call_config_stays_disabled(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    response = main_module.voice_call_config(
        Response(),
        SimpleNamespace(id=uuid4(), _reviewer_experience="care_worker"),
    )

    assert response.enabled is False
    assert response.ice_servers == []


def test_voice_call_config_uses_short_lived_cloudflare_turn_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    monkeypatch.setattr(settings, "voice_call_cloudflare_turn_key_id", "turn-key-id")
    monkeypatch.setattr(
        settings,
        "voice_call_cloudflare_turn_api_token",
        SecretStr("server-only-token"),
    )
    voice_call_turn.clear_voice_call_turn_cache()
    requests: list[dict] = []

    class FakeResponse:
        status_code = 201

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "iceServers": [
                    {
                        "urls": [
                            "stun:stun.cloudflare.com:3478",
                            "stun:stun.cloudflare.com:53",
                        ]
                    },
                    {
                        "urls": [
                            "turn:turn.cloudflare.com:3478?transport=udp",
                            "turn:turn.cloudflare.com:53?transport=udp",
                            "turns:turn.cloudflare.com:5349?transport=tcp",
                            "turns:turn.cloudflare.com:443?transport=tcp",
                        ],
                        "username": "short-user",
                        "credential": "short-credential",
                    },
                ]
            }

    def fake_post(url: str, **kwargs):
        requests.append({"url": url, **kwargs})
        return FakeResponse()

    monkeypatch.setattr(voice_call_turn.httpx, "post", fake_post)
    try:
        with TestClient(app) as admin:
            _login(admin, "admin", "AdminPass!234")
            first = admin.get("/api/voice-calls/config")
            second = admin.get("/api/voice-calls/config")
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert len(requests) == 1
        assert requests[0]["headers"]["Authorization"] == "Bearer server-only-token"
        assert requests[0]["json"] == {"ttl": 86400}
        servers = first.json()["ice_servers"]
        assert servers[0]["urls"] == ["stun:stun.cloudflare.com:3478"]
        assert servers[1] == {
            "urls": [
                "turn:turn.cloudflare.com:3478?transport=udp",
                "turns:turn.cloudflare.com:5349?transport=tcp",
                "turns:turn.cloudflare.com:443?transport=tcp",
            ],
            "username": "short-user",
            "credential": "short-credential",
        }
    finally:
        voice_call_turn.clear_voice_call_turn_cache()


def test_voice_call_config_uses_short_lived_local_coturn_credentials(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    monkeypatch.setattr(settings, "voice_call_stun_urls", "stun:stun.cloudflare.com:3478")
    monkeypatch.setattr(
        settings,
        "voice_call_turn_urls",
        (
            "turn:turn.silvermedical.kr:3478?transport=udp,"
            "turn:turn.silvermedical.kr:3478?transport=tcp"
        ),
    )
    monkeypatch.setattr(settings, "voice_call_turn_username", None)
    monkeypatch.setattr(settings, "voice_call_turn_credential", None)
    monkeypatch.setattr(
        settings,
        "voice_call_coturn_auth_secret",
        SecretStr("server-only-coturn-secret"),
    )
    monkeypatch.setattr(settings, "voice_call_coturn_ttl_seconds", 3600)
    monkeypatch.setattr(voice_call_turn.time, "time", lambda: 2_000_000_000)

    with TestClient(app) as admin:
        admin_user = _login(admin, "admin", "AdminPass!234")
        response = admin.get("/api/voice-calls/config")

    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    turn_server = response.json()["ice_servers"][1]
    username = f"2000003600:{admin_user['id']}"
    expected_credential = base64.b64encode(
        hmac.new(
            b"server-only-coturn-secret",
            username.encode("utf-8"),
            hashlib.sha1,
        ).digest()
    ).decode("ascii")
    assert turn_server == {
        "urls": [
            "turn:turn.silvermedical.kr:3478?transport=udp",
            "turn:turn.silvermedical.kr:3478?transport=tcp",
        ],
        "username": username,
        "credential": expected_credential,
    }


def test_room_members_can_exchange_bounded_voice_call_signaling(monkeypatch) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    relayed: list[tuple[set, dict]] = []

    async def capture_relay(user_ids: set, payload: dict) -> int:
        if payload.get("event") == "voice_call_delivery_status":
            return len(user_ids)
        relayed.append((user_ids, payload))
        return len(user_ids)

    monkeypatch.setattr(manager, "send_to_users", capture_relay)
    with (
        TestClient(app) as admin,
        TestClient(app) as caller,
        TestClient(app) as receiver,
        TestClient(app) as third,
    ):
        _login(admin, "admin", "AdminPass!234")
        caller_user = _create_and_activate_staff(
            admin,
            caller,
            username="voice-call-caller",
            full_name="통화 시험 가람",
            temporary_password="111111",
            password="caller1",
        )
        receiver_user = _create_and_activate_staff(
            admin,
            receiver,
            username="voice-call-receiver",
            full_name="통화 시험 나래",
            temporary_password="222222",
            password="receive2",
        )
        third_user = _create_and_activate_staff(
            admin,
            third,
            username="video-call-third",
            full_name="영상 시험 다온",
            temporary_password="333333",
            password="third333",
        )
        created_room = _post(
            caller,
            "/api/staff-rooms",
            {"name": "통화 시험방", "member_ids": [receiver_user["id"]]},
        )
        assert created_room.status_code == 201, created_room.text
        room_id = created_room.json()["id"]
        call_id = "811d2ca5-0dd7-44fd-b258-d677fae62965"
        relayed.clear()

        with caller.websocket_connect("/api/ws", headers=ORIGIN) as caller_ws:
            assert caller_ws.receive_json()["event"] == "ready"

            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": call_id,
                    "room_id": room_id,
                }
            )
            started = caller_ws.receive_json()
            assert started["event"] == "voice_call_started"
            assert started["member_count"] == 2
            assert len(relayed) == 1
            incoming_user_ids, incoming = relayed[-1]
            assert {str(user_id) for user_id in incoming_user_ids} == {
                receiver_user["id"]
            }
            assert incoming.pop("expires_at") > 0
            assert incoming == {
                "event": "voice_call_invite",
                "call_id": call_id,
                "room_id": room_id,
                "room_name": "통화 시험방",
                "call_mode": "audio",
                "caller_user_id": caller_user["id"],
                "caller_name": "통화 시험 가람",
                "member_count": 2,
            }

            caller_ws.send_json(
                {
                    "event": "voice_call_join",
                    "call_id": call_id,
                    "room_id": room_id,
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"
            joined_user_ids, joined = relayed[-1]
            assert {str(user_id) for user_id in joined_user_ids} == {
                receiver_user["id"]
            }
            assert joined["event"] == "voice_call_join"
            assert joined["participant_user_id"] == caller_user["id"]

            caller_ws.send_json(
                {
                    "event": "voice_call_mode",
                    "call_id": call_id,
                    "room_id": room_id,
                    "call_mode": "video",
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"
            mode_user_ids, mode_changed = relayed[-1]
            assert {str(user_id) for user_id in mode_user_ids} == {
                receiver_user["id"]
            }
            assert mode_changed["event"] == "voice_call_mode"
            assert mode_changed["call_mode"] == "video"
            assert mode_changed["participant_user_id"] == caller_user["id"]

            caller_ws.send_json(
                {
                    "event": "voice_call_mode",
                    "call_id": call_id,
                    "room_id": room_id,
                    "call_mode": "screen",
                }
            )
            invalid_mode = caller_ws.receive_json()
            assert invalid_mode["event"] == "voice_call_error"
            assert invalid_mode["code"] == "invalid_call"
            assert invalid_mode["source_event"] == "voice_call_mode"

            caller_ws.send_json(
                {
                    "event": "voice_call_signal",
                    "call_id": call_id,
                    "room_id": room_id,
                    "target_user_id": receiver_user["id"],
                    "signal": {"kind": "offer", "sdp": "v=0"},
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"
            signal_user_ids, signal = relayed[-1]
            assert {str(user_id) for user_id in signal_user_ids} == {
                receiver_user["id"]
            }
            assert signal["event"] == "voice_call_signal"
            assert signal["sender_user_id"] == caller_user["id"]
            assert signal["signal"] == {"kind": "offer", "sdp": "v=0"}

            caller_ws.send_json(
                {
                    "event": "voice_call_signal",
                    "call_id": call_id,
                    "room_id": room_id,
                    "target_user_id": caller_user["id"],
                    "signal": {"kind": "offer", "sdp": "v=0"},
                }
            )
            rejected = caller_ws.receive_json()
            assert rejected["event"] == "voice_call_error"
            assert rejected["code"] == "invalid_call"

            caller_ws.send_json(
                {
                    "event": "voice_call_leave",
                    "call_id": call_id,
                    "room_id": room_id,
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"
            left_user_ids, left = relayed[-1]
            assert {str(user_id) for user_id in left_user_ids} == {
                receiver_user["id"]
            }
            assert left["event"] == "voice_call_leave"
            assert left["participant_user_id"] == caller_user["id"]

            caller_ws.send_json(
                {
                    "event": "voice_call_cancel",
                    "call_id": call_id,
                    "room_id": room_id,
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"
            cancelled_user_ids, cancelled = relayed[-1]
            assert {str(user_id) for user_id in cancelled_user_ids} == {
                receiver_user["id"]
            }
            assert cancelled["event"] == "voice_call_cancel"
            assert cancelled["participant_user_id"] == caller_user["id"]

            video_call_id = "8fb4d66d-a91c-4b71-b16b-b0d1e8444f16"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": video_call_id,
                    "room_id": room_id,
                    "call_mode": "video",
                }
            )
            video_started = caller_ws.receive_json()
            assert video_started["event"] == "voice_call_started"
            assert video_started["call_mode"] == "video"
            assert relayed[-1][1]["event"] == "voice_call_invite"
            assert relayed[-1][1]["call_mode"] == "video"

            video_room = _post(
                caller,
                "/api/staff-rooms",
                {
                    "name": "영상 인원 제한방",
                    "member_ids": [receiver_user["id"], third_user["id"]],
                },
            )
            assert video_room.status_code == 201, video_room.text
            monkeypatch.setattr(settings, "voice_call_max_video_participants", 2)
            selected_call_id = "c0346396-45c7-4f65-9834-188768544227"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": selected_call_id,
                    "room_id": video_room.json()["id"],
                    "call_mode": "video",
                    "recipient_user_ids": [receiver_user["id"]],
                }
            )
            selected_started = caller_ws.receive_json()
            assert selected_started["event"] == "voice_call_started"
            assert selected_started["member_count"] == 2
            selected_user_ids, selected_invite = relayed[-1]
            assert {str(user_id) for user_id in selected_user_ids} == {
                receiver_user["id"]
            }
            assert selected_invite["member_count"] == 2

            relay_count = len(relayed)
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": selected_call_id,
                    "room_id": video_room.json()["id"],
                    "call_mode": "video",
                    "recipient_user_ids": [receiver_user["id"]],
                }
            )
            duplicate = caller_ws.receive_json()
            assert duplicate["event"] == "voice_call_error"
            assert len(relayed) == relay_count

            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": "93550bda-1113-4dcb-895f-d404685902e2",
                    "room_id": video_room.json()["id"],
                    "call_mode": "video",
                }
            )
            video_too_large = caller_ws.receive_json()
            assert video_too_large["event"] == "voice_call_error"
            assert video_too_large["code"] == "invalid_call"
            assert "2명까지" in video_too_large["message"]
            assert "대상을 줄여" in video_too_large["message"]

            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": "b02e12ca-8cc7-4225-b415-fc4ae741656b",
                    "room_id": room_id,
                    "call_mode": "screen",
                }
            )
            invalid_mode = caller_ws.receive_json()
            assert invalid_mode["event"] == "voice_call_error"
            assert invalid_mode["code"] == "invalid_call"

        self_room = next(
            room for room in caller.get("/api/rooms").json() if room["kind"] == "self"
        )
        with caller.websocket_connect("/api/ws", headers=ORIGIN) as caller_ws:
            assert caller_ws.receive_json()["event"] == "ready"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": call_id,
                    "room_id": self_room["id"],
                }
            )
            blocked = caller_ws.receive_json()
            assert blocked["event"] == "voice_call_error"
            assert blocked["code"] == "invalid_call"

        organization_room = next(
            room for room in caller.get("/api/rooms").json() if room["kind"] == "all"
        )
        with caller.websocket_connect("/api/ws", headers=ORIGIN) as caller_ws:
            assert caller_ws.receive_json()["event"] == "ready"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": call_id,
                    "room_id": organization_room["id"],
                }
            )
            blocked = caller_ws.receive_json()
            assert blocked["event"] == "voice_call_error"
            assert blocked["code"] == "invalid_call"


def test_pending_call_survives_process_memory_and_native_decline_is_opaque(
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings, "voice_call_enabled", True)
    captured_pushes: list[dict] = []
    relayed: list[tuple[set, dict]] = []
    push_ready = threading.Event()

    def capture_push(**kwargs) -> None:
        captured_pushes.append(kwargs)
        push_ready.set()

    async def capture_relay(user_ids: set, payload: dict) -> None:
        relayed.append((user_ids, payload))

    monkeypatch.setattr(main_module, "_schedule_voice_call_delivery", capture_push)
    monkeypatch.setattr(manager, "send_to_users", capture_relay)

    with (
        TestClient(app) as admin,
        TestClient(app) as caller,
        TestClient(app) as receiver,
        TestClient(app) as anonymous,
    ):
        _login(admin, "admin", "AdminPass!234")
        caller_user = _create_and_activate_staff(
            admin,
            caller,
            username="persistent-call-caller",
            full_name="통화 보존 발신자",
            temporary_password="444444",
            password="caller444",
        )
        receiver_user = _create_and_activate_staff(
            admin,
            receiver,
            username="persistent-call-receiver",
            full_name="통화 보존 수신자",
            temporary_password="555555",
            password="receive555",
        )
        created_room = _post(
            caller,
            "/api/staff-rooms",
            {"name": "재실행 통화 시험방", "member_ids": [receiver_user["id"]]},
        )
        assert created_room.status_code == 201, created_room.text
        room_id = created_room.json()["id"]
        call_id = "2baf7b49-1f44-4af7-ae33-fcf822209008"

        with caller.websocket_connect("/api/ws", headers=ORIGIN) as caller_ws:
            assert caller_ws.receive_json()["event"] == "ready"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": call_id,
                    "room_id": room_id,
                    "recipient_user_ids": [receiver_user["id"]],
                }
            )
            assert caller_ws.receive_json()["event"] == "voice_call_started"

        assert push_ready.wait(1), "수신자용 모바일 통화 토큰이 생성되지 않았습니다."
        assert len(captured_pushes) == 1
        action_token = captured_pushes[0]["mobile_action_token"]
        assert action_token
        assert "caller_name" in captured_pushes[0]

        receipt = anonymous.post(
            f"/api/voice-calls/{call_id}/delivery-receipt",
            headers={**ORIGIN, "x-mesil-call-action": action_token},
            json={"stage": "device_received", "success": True, "device_hash": "a" * 64},
        )
        assert receipt.status_code == 204, receipt.text
        duplicate_receipt = anonymous.post(
            f"/api/voice-calls/{call_id}/delivery-receipt",
            headers={**ORIGIN, "x-mesil-call-action": action_token},
            json={"stage": "device_received", "success": True, "device_hash": "a" * 64},
        )
        assert duplicate_receipt.status_code == 204
        wrong_call_receipt = anonymous.post(
            "/api/voice-calls/6c4a711a-4f00-47ee-a53b-a67c16591f13/delivery-receipt",
            headers={**ORIGIN, "x-mesil-call-action": action_token},
            json={"stage": "device_received", "success": True, "device_hash": "b" * 64},
        )
        assert wrong_call_receipt.status_code == 403
        for index in range(24):
            bounded_receipt = anonymous.post(
                f"/api/voice-calls/{call_id}/delivery-receipt",
                headers={**ORIGIN, "x-mesil-call-action": action_token},
                json={
                    "stage": "notification_posted",
                    "success": True,
                    "device_hash": f"{index + 1:064x}",
                },
            )
            assert bounded_receipt.status_code == 204
        with SessionLocal() as db:
            receipts = list(db.scalars(select(AuditEvent).where(
                AuditEvent.action == "voice_call.delivery_receipt",
                AuditEvent.target_id == call_id,
            )))
            assert len(receipts) == 20
            initial_receipts = [r for r in receipts if r.details.get("device_hash") == "a" * 64]
            assert len(initial_receipts) == 1
            assert initial_receipts[0].details == {
                "stage": "device_received",
                "success": True,
                "error_type": None,
                "elapsed_ms": None,
                "device_hash": "a" * 64,
            }

        # 프로세스 메모리를 잃어도 DB의 초대 상태로 복구해야 합니다.
        main_module.VOICE_CALL_PARTICIPANTS.clear()
        pending = receiver.get("/api/voice-calls/pending")
        assert pending.status_code == 200, pending.text
        assert pending.headers["cache-control"] == "private, no-store"
        assert pending.json() == [
            {
                "call_id": call_id,
                "room_id": room_id,
                "room_name": "재실행 통화 시험방",
                "caller_user_id": caller_user["id"],
                "caller_name": "통화 보존 발신자",
                "call_mode": "audio",
                "member_count": 2,
                "call_state": "ringing",
                "expires_at": pending.json()[0]["expires_at"],
            }
        ]

        denied = anonymous.post(
            f"/api/voice-calls/{call_id}/native-decline",
            headers={**ORIGIN, "x-mesil-call-action": "wrong-opaque-token"},
        )
        assert denied.status_code == 403

        relay_count_before_decline = len(relayed)
        declined = anonymous.post(
            f"/api/voice-calls/{call_id}/native-decline",
            headers={**ORIGIN, "x-mesil-call-action": action_token},
        )
        assert declined.status_code == 204, declined.text
        assert receiver.get("/api/voice-calls/pending").json() == []
        assert len(relayed) == relay_count_before_decline + 1
        decline_user_ids, decline_payload = relayed[-1]
        assert {str(user_id) for user_id in decline_user_ids} == {
            caller_user["id"]
        }
        assert decline_payload == {
            "event": "voice_call_decline",
            "call_id": call_id,
            "room_id": room_id,
            "room_name": "재실행 통화 시험방",
            "call_mode": "audio",
            "participant_user_id": receiver_user["id"],
            "participant_name": "통화 보존 수신자",
        }
        with SessionLocal() as verification_db:
            persisted_invitation = verification_db.get(
                VoiceCallInvitation,
                call_id,
            )
            assert persisted_invitation is not None
            assert persisted_invitation.state == "declined"
            assert persisted_invitation.ended_at is not None

        duplicate = anonymous.post(
            f"/api/voice-calls/{call_id}/native-decline",
            headers={**ORIGIN, "x-mesil-call-action": action_token},
        )
        assert duplicate.status_code == 204
        assert len(relayed) == relay_count_before_decline + 1

        cancelled_call_id = "2baf7b49-1f44-4af7-ae33-fcf822209108"
        with caller.websocket_connect("/api/ws", headers=ORIGIN) as caller_ws:
            assert caller_ws.receive_json()["event"] == "ready"
            caller_ws.send_json(
                {
                    "event": "voice_call_invite",
                    "call_id": cancelled_call_id,
                    "room_id": room_id,
                    "recipient_user_ids": [receiver_user["id"]],
                }
            )
            assert caller_ws.receive_json()["event"] == "voice_call_started"
            assert captured_pushes[-1].get("cancelled", False) is False
            caller_ws.send_json(
                {
                    "event": "voice_call_cancel",
                    "call_id": cancelled_call_id,
                    "room_id": room_id,
                }
            )
            caller_ws.send_json({"event": "ping"})
            assert caller_ws.receive_json()["event"] == "pong"

        cancelled_push = captured_pushes[-1]
        assert cancelled_push["cancelled"] is True
        assert cancelled_push["mobile_action_token"]
        cancelled_receipt = anonymous.post(
            f"/api/voice-calls/{cancelled_call_id}/delivery-receipt",
            headers={
                **ORIGIN,
                "x-mesil-call-action": cancelled_push["mobile_action_token"],
            },
            json={
                "stage": "notification_cancelled",
                "success": True,
                "device_hash": "c" * 64,
            },
        )
        assert cancelled_receipt.status_code == 204, cancelled_receipt.text
