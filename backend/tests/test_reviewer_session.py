from datetime import timedelta
from uuid import uuid4

from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import LoginSession, utcnow
from app.security import token_digest


ORIGIN = {"origin": "http://testserver"}
TEMPORARY_PASSWORD = "ReviewerTemp!234"
ACTIVE_PASSWORD = "ReviewerActive!234"


def secure_client() -> TestClient:
    return TestClient(app, base_url="https://testserver")


def post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def login(client: TestClient, username: str, password: str):
    response = post(
        client,
        "/api/auth/login",
        {"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_ready_staff(
    admin_client: TestClient,
    *,
    username: str,
    full_name: str,
    employee_code: str,
    can_process_records: bool,
) -> dict:
    created = post(
        admin_client,
        "/api/employees",
        {
            "username": username,
            "full_name": full_name,
            "password": TEMPORARY_PASSWORD,
            "role": "staff",
            "employee_code": employee_code,
            "can_process_records": can_process_records,
        },
    )
    assert created.status_code == 201, created.text
    with secure_client() as staff_client:
        first_login = login(staff_client, username, TEMPORARY_PASSWORD)
        assert first_login["user"]["must_change_password"] is True
        changed = post(
            staff_client,
            "/api/auth/password",
            {
                "current_password": TEMPORARY_PASSWORD,
                "new_password": ACTIVE_PASSWORD,
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["must_change_password"] is False
    return created.json()


def reviewer_session(
    client: TestClient,
    experience: str,
):
    return post(
        client,
        "/api/auth/reviewer-session",
        {"experience": experience},
    )


def test_password_reviewer_uses_regular_login_with_restricted_session(monkeypatch):
    suffix = uuid4().hex[:8]
    reviewer_username = f"review-password-{suffix}"
    sender_username = f"review-password-sender-{suffix}"
    room_name = f"비밀번호 검토방 {suffix}"

    with secure_client() as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        reviewer_user = create_ready_staff(
            admin_client,
            username=reviewer_username,
            full_name="가상 기간제 검토자",
            employee_code=f"RV-PASS-{suffix}",
            can_process_records=True,
        )
        sender_user = create_ready_staff(
            admin_client,
            username=sender_username,
            full_name="가상 검토자료 작성자",
            employee_code=f"RV-PASS-SENDER-{suffix}",
            can_process_records=False,
        )
        room = post(
            admin_client,
            "/api/admin/rooms",
            {
                "name": room_name,
                "kind": "custom",
                "member_ids": [reviewer_user["id"], sender_user["id"]],
                "resident_scope": "all",
            },
        )
        assert room.status_code == 201, room.text
        room_id = room.json()["id"]

        monkeypatch.setattr(settings, "reviewer_access_enabled", False)
        monkeypatch.setattr(settings, "reviewer_password_login_enabled", True)
        monkeypatch.setattr(
            settings,
            "reviewer_password_login_experience",
            "social_worker",
        )
        monkeypatch.setattr(
            settings,
            "reviewer_access_ends_at",
            utcnow() + timedelta(hours=1),
        )
        monkeypatch.setattr(
            settings,
            "reviewer_session_secret",
            SecretStr("reviewer-password-secret-" + "x" * 32),
        )
        monkeypatch.setattr(settings, "reviewer_social_username", None)
        monkeypatch.setattr(settings, "mentor_reviewer_username", reviewer_username)
        monkeypatch.setattr(
            settings,
            "allowed_origins",
            "http://testserver,https://testserver",
        )

        passwordless = reviewer_session(admin_client, "social_worker")
        assert passwordless.status_code == 404

        wrong = post(
            secure_client(),
            "/api/auth/login",
            {"username": reviewer_username, "password": "WrongPassword!"},
        )
        assert wrong.status_code == 401

        with secure_client() as reviewer_client:
            signed_in = post(
                reviewer_client,
                "/api/auth/login",
                {"username": reviewer_username, "password": ACTIVE_PASSWORD},
            )
            assert signed_in.status_code == 200, signed_in.text
            assert signed_in.json()["user"]["is_reviewer_session"] is True
            assert signed_in.json()["user"]["reviewer_experience"] == "social_worker"
            assert reviewer_username not in signed_in.text
            session_token = reviewer_client.cookies.get(settings.session_cookie_name)
            assert session_token is not None and session_token.startswith("rv1.")
            cookie = next(
                item
                for item in signed_in.headers.get_list("set-cookie")
                if item.startswith(f"{settings.session_cookie_name}=")
            ).lower()
            assert "secure" in cookie
            assert "httponly" in cookie
            assert "samesite=lax" in cookie

            assert reviewer_client.get("/api/workdesk/residents").status_code == 200
            reviewer_rooms = reviewer_client.get("/api/rooms")
            assert reviewer_rooms.status_code == 200
            assert {item["id"] for item in reviewer_rooms.json()} == {room_id}
            assert reviewer_client.get("/api/admin/staff-directory").status_code == 403
            blocked_upload = reviewer_client.post(
                f"/api/rooms/{room_id}/messages-with-files",
                headers=ORIGIN,
            )
            assert blocked_upload.status_code == 403

            with reviewer_client.websocket_connect(
                "/api/ws",
                headers={
                    **ORIGIN,
                    "cookie": f"{settings.session_cookie_name}={session_token}",
                },
            ) as websocket:
                assert websocket.receive_json()["event"] == "ready"
                terminated = admin_client.post(
                    f"/api/employees/{reviewer_user['id']}/terminate",
                    headers=ORIGIN,
                )
                assert terminated.status_code == 200, terminated.text
                forced = websocket.receive_json()
                assert forced["event"] == "force_logout"

            assert reviewer_client.get("/api/auth/me").status_code in {401, 403}
            blocked_again = post(
                secure_client(),
                "/api/auth/login",
                {"username": reviewer_username, "password": ACTIVE_PASSWORD},
            )
            assert blocked_again.status_code == 403


def test_mentor_full_login_uses_time_limited_normal_admin_session(
    monkeypatch,
):
    suffix = uuid4().hex[:8]
    reviewer_username = f"mentor-full-{suffix}"
    sender_username = f"mentor-full-sender-{suffix}"

    with secure_client() as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        reviewer_user = create_ready_staff(
            admin_client,
            username=reviewer_username,
            full_name="가상 멘토 전체검토자",
            employee_code=f"MENTOR-FULL-{suffix}",
            can_process_records=True,
        )
        promoted = admin_client.patch(
            f'/api/employees/{reviewer_user["id"]}',
            json={"role": "admin"},
            headers=ORIGIN,
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["role"] == "admin"
        sender_user = create_ready_staff(
            admin_client,
            username=sender_username,
            full_name="가상 멘토 자료작성자",
            employee_code=f"MENTOR-FULL-SENDER-{suffix}",
            can_process_records=False,
        )
        room_ids: list[str] = []
        for index in range(2):
            room = post(
                admin_client,
                "/api/admin/rooms",
                {
                    "name": f"멘토 전체 검토 합성방 {index + 1} {suffix}",
                    "kind": "custom",
                    "member_ids": [reviewer_user["id"], sender_user["id"]],
                    "resident_scope": "all",
                },
            )
            assert room.status_code == 201, room.text
            room_ids.append(room.json()["id"])

        monkeypatch.setattr(settings, "reviewer_access_enabled", False)
        monkeypatch.setattr(settings, "reviewer_password_login_enabled", True)
        monkeypatch.setattr(
            settings,
            "reviewer_password_login_experience",
            "mentor_full",
        )
        monkeypatch.setattr(
            settings,
            "reviewer_access_ends_at",
            utcnow() + timedelta(hours=1),
        )
        monkeypatch.setattr(
            settings,
            "reviewer_session_secret",
            SecretStr("mentor-full-secret-" + "x" * 32),
        )
        monkeypatch.setattr(settings, "mentor_reviewer_username", reviewer_username)
        monkeypatch.setattr(
            settings,
            "allowed_origins",
            "http://testserver,https://testserver",
        )

        with secure_client() as reviewer_client:
            signed_in = post(
                reviewer_client,
                "/api/auth/login",
                {"username": reviewer_username, "password": ACTIVE_PASSWORD},
            )
            assert signed_in.status_code == 200, signed_in.text
            user = signed_in.json()["user"]
            assert user["role"] == "admin"
            assert user["is_reviewer_session"] is False
            assert user["reviewer_experience"] is None
            assert user["can_process_records"] is True

            rooms = reviewer_client.get("/api/rooms")
            assert rooms.status_code == 200, rooms.text
            visible_room_ids = {item["id"] for item in rooms.json()}
            assert set(room_ids).issubset(visible_room_ids)
            assert len(visible_room_ids) >= 2

            staff = reviewer_client.get("/api/admin/staff-directory")
            assert staff.status_code == 200, staff.text
            assert staff.json()
            assert len(staff.json()) >= 2

            managed_rooms = reviewer_client.get("/api/admin/rooms")
            assert managed_rooms.status_code == 200, managed_rooms.text
            assert set(room_ids).issubset(
                {item["id"] for item in managed_rooms.json()}
            )

            residents = reviewer_client.get("/api/admin/residents")
            assert residents.status_code == 200, residents.text
            assert isinstance(residents.json(), list)

            assert reviewer_client.get("/api/health").status_code == 200
            assert reviewer_client.get("/api/push/config").status_code == 200
            ai_status = reviewer_client.get("/api/ai/system/status")
            assert ai_status.status_code == 200, ai_status.text
            assert ai_status.json()["settings_write_allowed"] is False
            assert all(
                provider["secret_value_included"] is False
                for provider in ai_status.json()["providers"]
            )
            assert "base_url" not in ai_status.text.lower()
            blocked_ai_connect = post(
                reviewer_client,
                "/api/ai/system/providers/openai/connect",
                {"api_key": None},
            )
            assert blocked_ai_connect.status_code == 403
            assert "AI 연결" in blocked_ai_connect.json()["detail"]

            created_room = post(
                reviewer_client,
                "/api/admin/rooms",
                {
                    "name": f"멘토 개발자 생성방 {suffix}",
                    "kind": "custom",
                    "member_ids": [reviewer_user["id"]],
                    "resident_scope": "all",
                },
            )
            assert created_room.status_code == 201, created_room.text

            created_message = post(
                reviewer_client,
                f"/api/rooms/{room_ids[0]}/messages",
                {"body": "멘토 개발자 권한 메시지 검증", "message_type": "chat"},
            )
            assert created_message.status_code == 201, created_message.text
            recalled = post(
                reviewer_client,
                f'/api/messages/{created_message.json()["id"]}/recall',
                {"reason": None},
            )
            assert recalled.status_code == 200, recalled.text


def test_reviewer_session_security_contract(monkeypatch):
    suffix = uuid4().hex[:8]
    care_username = f"review-care-{suffix}"
    social_username = f"review-social-{suffix}"
    secondary_username = f"review-second-{suffix}"
    sender_username = f"review-sender-{suffix}"
    room_name = f"심사 체험방 {suffix}"

    with secure_client() as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        care_user = create_ready_staff(
            admin_client,
            username=care_username,
            full_name="가상 요양보호사 심사",
            employee_code=f"RV-CARE-{suffix}",
            can_process_records=False,
        )
        social_user = create_ready_staff(
            admin_client,
            username=social_username,
            full_name="가상 사회복지사 심사",
            employee_code=f"RV-SOCIAL-{suffix}",
            can_process_records=True,
        )
        secondary_user = create_ready_staff(
            admin_client,
            username=secondary_username,
            full_name="가상 실시간 직원 심사",
            employee_code=f"RV-SECOND-{suffix}",
            can_process_records=False,
        )
        sender_user = create_ready_staff(
            admin_client,
            username=sender_username,
            full_name="가상 일반 발신 직원",
            employee_code=f"RV-SENDER-{suffix}",
            can_process_records=False,
        )
        room = post(
            admin_client,
            "/api/admin/rooms",
            {
                "name": room_name,
                "kind": "custom",
                "member_ids": [
                    care_user["id"],
                    social_user["id"],
                    secondary_user["id"],
                    sender_user["id"],
                ],
                "resident_scope": "all",
            },
        )
        assert room.status_code == 201, room.text
        room_id = room.json()["id"]
        other_room = post(
            admin_client,
            "/api/admin/rooms",
            {
                "name": f"심사 제외방 {suffix}",
                "kind": "custom",
                "member_ids": [
                    care_user["id"],
                    social_user["id"],
                    secondary_user["id"],
                    sender_user["id"],
                ],
                "resident_scope": "all",
            },
        )
        assert other_room.status_code == 201, other_room.text
        other_room_id = other_room.json()["id"]

    monkeypatch.setattr(settings, "reviewer_access_enabled", True)
    monkeypatch.setattr(settings, "reviewer_password_login_enabled", False)
    monkeypatch.setattr(
        settings,
        "reviewer_access_ends_at",
        utcnow() + timedelta(hours=1),
    )
    reviewer_secret = SecretStr("reviewer-test-secret-" + "x" * 32)
    monkeypatch.setattr(settings, "reviewer_session_secret", reviewer_secret)
    monkeypatch.setattr(settings, "reviewer_session_minutes", 45)
    monkeypatch.setattr(settings, "reviewer_care_username", care_username)
    monkeypatch.setattr(settings, "reviewer_social_username", social_username)
    monkeypatch.setattr(
        settings,
        "reviewer_secondary_username",
        secondary_username,
    )
    monkeypatch.setattr(settings, "reviewer_chat_room_name", room_name)
    monkeypatch.setattr(settings, "reviewer_rate_limit", 100)
    monkeypatch.setattr(settings, "reviewer_session_limit_per_client", 2)
    monkeypatch.setattr(
        settings,
        "allowed_origins",
        "http://testserver,https://testserver",
    )

    for username in (care_username, social_username, secondary_username):
        with secure_client() as regular_login_client:
            blocked_regular_login = post(
                regular_login_client,
                "/api/auth/login",
                {"username": username, "password": ACTIVE_PASSWORD},
            )
            assert blocked_regular_login.status_code == 403

    with secure_client() as care_client:
        issued = reviewer_session(care_client, "care")
        assert issued.status_code == 200, issued.text
        payload = issued.json()
        assert payload["destination"] == "chat"
        assert payload["room_id"] == room_id
        assert payload["user"]["username"] == "reviewer-care"
        assert care_username not in issued.text
        assert payload["user"]["is_reviewer_session"] is True
        assert payload["user"]["reviewer_experience"] == "care"

        session_token = care_client.cookies.get(settings.session_cookie_name)
        assert session_token is not None
        assert session_token.startswith("rv1.")
        session_cookie = next(
            header
            for header in issued.headers.get_list("set-cookie")
            if header.startswith(f"{settings.session_cookie_name}=")
        ).lower()
        assert "httponly" in session_cookie
        assert "samesite=lax" in session_cookie
        assert "secure" in session_cookie

        me = care_client.get("/api/auth/me")
        assert me.status_code == 200, me.text
        assert me.json()["username"] == "reviewer-care"
        assert care_username not in me.text
        assert me.json()["is_reviewer_session"] is True
        assert me.json()["reviewer_experience"] == "care"
        room_ids = {item["id"] for item in care_client.get("/api/rooms").json()}
        assert room_ids == {room_id}
        assert (
            care_client.get(
                f"/api/rooms/{other_room_id}/messages"
            ).status_code
            == 403
        )
        assert (
            care_client.get(
                f"/api/rooms/{other_room_id}/members"
            ).status_code
            == 403
        )
        assert care_client.get("/api/workdesk/residents").status_code == 403

        with care_client.websocket_connect(
            "/api/ws",
            headers={
                **ORIGIN,
                "cookie": (
                    f"{settings.session_cookie_name}={session_token}"
                ),
            },
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            with secure_client() as sender_client:
                login(sender_client, sender_username, ACTIVE_PASSWORD)
                hidden_room_message = post(
                    sender_client,
                    f"/api/rooms/{other_room_id}/messages",
                    {
                        "body": "심사위원에게 전달되면 안 되는 방 메시지",
                        "message_type": "chat",
                    },
                )
                assert hidden_room_message.status_code == 201
            websocket.send_json({"event": "ping"})
            assert websocket.receive_json()["event"] == "pong"

        with secure_client() as sender_client:
            login(sender_client, sender_username, ACTIVE_PASSWORD)
            visible_room_message = post(
                sender_client,
                f"/api/rooms/{room_id}/messages",
                {
                    "body": "심사 체험방에만 보이는 가명 돌봄 기록",
                    "message_type": "chat",
                },
            )
            assert visible_room_message.status_code == 201

        reused = reviewer_session(care_client, "care")
        assert reused.status_code == 200, reused.text
        assert (
            care_client.cookies.get(settings.session_cookie_name)
            == session_token
        )

        blocked_password = post(
            care_client,
            "/api/auth/password",
            {
                "current_password": ACTIVE_PASSWORD,
                "new_password": "ReviewerChanged!234",
            },
        )
        assert blocked_password.status_code == 403
        assert care_client.get("/api/auth/sessions").status_code == 403
        blocked_revoke = care_client.delete(
            f"/api/auth/sessions/{uuid4()}",
            headers=ORIGIN,
        )
        assert blocked_revoke.status_code == 403
        blocked_revoke_others = post(
            care_client,
            "/api/auth/sessions/revoke-others",
            {},
        )
        assert blocked_revoke_others.status_code == 403
        blocked_push_register = post(
            care_client,
            "/api/push/subscriptions",
            {
                "endpoint": "https://push.example.test/reviewer-device",
                "expiration_time": None,
                "keys": {"p256dh": "p" * 80, "auth": "a" * 24},
            },
        )
        assert blocked_push_register.status_code == 403
        blocked_push_delete = care_client.request(
            "DELETE",
            "/api/push/subscriptions",
            json={"endpoint": "https://push.example.test/reviewer-device"},
            headers=ORIGIN,
        )
        assert blocked_push_delete.status_code == 403
        blocked_push_test = post(care_client, "/api/push/test", {})
        assert blocked_push_test.status_code == 403
        blocked_record_mutation = care_client.patch(
            f"/api/work-items/{uuid4()}",
            json={"status": "dismissed"},
            headers=ORIGIN,
        )
        assert blocked_record_mutation.status_code == 403

        with SessionLocal() as check_db:
            original_session_id = check_db.scalar(
                select(LoginSession.id).where(
                    LoginSession.token_hash == token_digest(session_token)
                )
            )
        monkeypatch.setattr(settings, "reviewer_rate_limit", 1)
        switched = reviewer_session(care_client, "realtime_secondary")
        assert switched.status_code == 200, switched.text
        monkeypatch.setattr(settings, "reviewer_rate_limit", 100)
        assert switched.json()["destination"] == "chat"
        assert switched.json()["room_id"] == room_id
        assert (
            switched.json()["user"]["username"]
            == "reviewer-realtime-secondary"
        )
        assert secondary_username not in switched.text
        assert (
            switched.json()["user"]["reviewer_experience"]
            == "realtime_secondary"
        )
        assert (
            care_client.cookies.get(settings.session_cookie_name)
            != session_token
        )
        switched_token = care_client.cookies.get(settings.session_cookie_name)
        assert switched_token is not None
        with SessionLocal() as check_db:
            switched_session_id = check_db.scalar(
                select(LoginSession.id).where(
                    LoginSession.token_hash == token_digest(switched_token)
                )
            )
        assert original_session_id is not None
        assert switched_session_id == original_session_id
        assert care_client.get("/api/workdesk/residents").status_code == 403

        with secure_client() as stale_client:
            stale_client.cookies.set(
                settings.session_cookie_name,
                session_token,
            )
            assert stale_client.get("/api/auth/me").status_code == 401

    with secure_client() as social_client:
        issued_social = reviewer_session(social_client, "social_worker")
        assert issued_social.status_code == 200, issued_social.text
        social_payload = issued_social.json()
        assert social_payload["destination"] == "care_briefing"
        assert social_payload["room_id"] is None
        assert social_payload["user"]["username"] == "reviewer-social"
        assert social_username not in issued_social.text
        assert social_payload["user"]["can_process_records"] is True
        assert social_client.get("/api/workdesk/residents").status_code == 200
        assert social_client.get("/api/work-items").status_code == 403
        assert social_client.get("/api/document-candidates").status_code == 403
        today = (utcnow() + timedelta(hours=9)).date().isoformat()
        period_review = post(
            social_client,
            "/api/workdesk/period-review",
            {
                "start_date": today,
                "end_date": today,
            },
        )
        assert period_review.status_code == 200, period_review.text
        source_ids = {
            source["message"]["id"]
            for source in period_review.json()["sources"]
        }
        assert visible_room_message.json()["id"] in source_ids
        assert hidden_room_message.json()["id"] not in source_ids
        blocked_hidden_summary = post(
            social_client,
            "/api/workdesk/record-summary",
            {
                "record_usage_tag": "general",
                "evidence_ids": [hidden_room_message.json()["id"]],
            },
        )
        assert blocked_hidden_summary.status_code == 403

        monkeypatch.setattr(settings, "reviewer_access_enabled", False)
        monkeypatch.setattr(settings, "reviewer_session_secret", None)
        ended = social_client.get("/api/auth/me")
        assert ended.status_code == 401
        with secure_client() as ended_client:
            assert reviewer_session(
                ended_client,
                "social_worker",
            ).status_code == 404
        monkeypatch.setattr(settings, "reviewer_session_secret", reviewer_secret)
        monkeypatch.setattr(settings, "reviewer_access_enabled", True)

        refreshed = reviewer_session(social_client, "social_worker")
        assert refreshed.status_code == 200, refreshed.text
        refreshed_token = social_client.cookies.get(
            settings.session_cookie_name
        )
        assert refreshed_token is not None
        with social_client.websocket_connect(
            "/api/ws",
            headers={
                **ORIGIN,
                "cookie": (
                    f"{settings.session_cookie_name}={refreshed_token}"
                ),
            },
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            monkeypatch.setattr(settings, "reviewer_access_enabled", False)
            websocket.send_json({"event": "ping"})
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "만료" in forced["reason"]
        monkeypatch.setattr(settings, "reviewer_access_enabled", True)

        monkeypatch.setattr(
            settings,
            "reviewer_access_ends_at",
            utcnow() + timedelta(seconds=2),
        )
        idle_expiring = reviewer_session(social_client, "social_worker")
        assert idle_expiring.status_code == 200, idle_expiring.text
        idle_token = social_client.cookies.get(settings.session_cookie_name)
        assert idle_token is not None
        with social_client.websocket_connect(
            "/api/ws",
            headers={
                **ORIGIN,
                "cookie": f"{settings.session_cookie_name}={idle_token}",
            },
        ) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "만료" in forced["reason"]
        monkeypatch.setattr(
            settings,
            "reviewer_access_ends_at",
            utcnow() + timedelta(hours=1),
        )

    monkeypatch.setattr(settings, "reviewer_care_username", "admin")
    with secure_client() as unsafe_client:
        unsafe = reviewer_session(unsafe_client, "care")
        assert unsafe.status_code == 503
    monkeypatch.setattr(settings, "reviewer_care_username", care_username)

    isolated_clients: list[TestClient] = []
    try:
        for experience in ("care", "social_worker", "realtime_secondary"):
            client = secure_client()
            isolated_clients.append(client)
            response = reviewer_session(client, experience)
            assert response.status_code == 200, response.text
        assert all(
            client.get("/api/auth/me").status_code == 200
            for client in isolated_clients
        )
    finally:
        for client in isolated_clients:
            client.close()

    cap_tokens: list[str] = []
    with secure_client() as cap_client:
        for experience in ("care", "social_worker", "realtime_secondary"):
            response = reviewer_session(cap_client, experience)
            assert response.status_code == 200, response.text
            cap_tokens.append(
                cap_client.cookies.get(settings.session_cookie_name)
            )
            cap_client.cookies.delete(settings.session_cookie_name)
        with secure_client() as token_client:
            token_client.cookies.set(
                settings.session_cookie_name,
                cap_tokens[0],
            )
            assert token_client.get("/api/auth/me").status_code == 401
            token_client.cookies.set(
                settings.session_cookie_name,
                cap_tokens[1],
            )
            assert token_client.get("/api/auth/me").status_code == 200
            token_client.cookies.set(
                settings.session_cookie_name,
                cap_tokens[2],
            )
            assert token_client.get("/api/auth/me").status_code == 200

    monkeypatch.setattr(settings, "reviewer_rate_limit", 2)
    with secure_client() as limited_client:
        for _ in range(2):
            allowed = reviewer_session(limited_client, "care")
            assert allowed.status_code == 200, allowed.text
            logged_out = post(limited_client, "/api/auth/logout", {})
            assert logged_out.status_code == 204
            assert limited_client.get("/api/auth/me").status_code == 401
        limited = reviewer_session(limited_client, "care")
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
