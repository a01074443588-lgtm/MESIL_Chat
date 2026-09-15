from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, LoginSession, utcnow
from app.security import token_digest


ORIGIN = {"origin": "http://testserver"}


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


def test_opening_app_renews_regular_login_for_one_year():
    with TestClient(app) as client:
        login(client, "admin", "AdminPass!234")
        token = client.cookies.get(settings.session_cookie_name)
        assert token
        with SessionLocal() as db:
            login_session = db.scalar(
                select(LoginSession).where(LoginSession.token_hash == token_digest(token))
            )
            assert login_session is not None
            login_session.expires_at = utcnow() + timedelta(hours=1)
            db.commit()

        current = client.get("/api/auth/me")
        assert current.status_code == 200, current.text
        assert f"Max-Age={settings.session_hours * 60 * 60}" in current.headers[
            "set-cookie"
        ]
        with SessionLocal() as db:
            renewed = db.scalar(
                select(LoginSession).where(LoginSession.token_hash == token_digest(token))
            )
            assert renewed is not None
            assert renewed.expires_at >= utcnow() + timedelta(days=364)


def test_password_change_session_control_admin_reset_and_rate_limit():
    with TestClient(app) as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        invalid_created = post(
            admin_client,
            "/api/employees",
            {
                "username": ".abc",
                "full_name": "잘못된 아이디 시험",
                "password": "abcdef",
                "role": "staff",
                "employee_code": "SEC-BAD",
            },
        )
        assert invalid_created.status_code == 422, invalid_created.text
        created = post(
            admin_client,
            "/api/employees",
            {
                "username": "security-user",
                "full_name": "가상 보안직원",
                "password": "abcdef",
                "role": "staff",
                "employee_code": "SEC-001",
            },
        )
        assert created.status_code == 201, created.text
        employee_id = created.json()["id"]
        assert created.json()["must_change_password"] is True

        primary = TestClient(app)
        primary.headers["user-agent"] = "SMCODI Test Primary"
        first_login = login(primary, "security-user", "abcdef")
        assert first_login["user"]["must_change_password"] is True
        blocked_room = primary.get("/api/rooms")
        assert blocked_room.status_code == 403
        assert "비밀번호" in blocked_room.json()["detail"]

        current_availability = primary.get(
            "/api/auth/username-availability?username=security-user"
        )
        assert current_availability.status_code == 200, current_availability.text
        assert current_availability.json() == {
            "username": "security-user",
            "available": True,
            "is_current": True,
        }
        duplicate_availability = primary.get(
            "/api/auth/username-availability?username=admin"
        )
        assert duplicate_availability.status_code == 200, duplicate_availability.text
        assert duplicate_availability.json()["available"] is False
        available_username = primary.get(
            "/api/auth/username-availability?username=security.renamed"
        )
        assert available_username.status_code == 200, available_username.text
        assert available_username.json()["available"] is True
        korean_username = primary.get(
            "/api/auth/username-availability?username=가상직원"
        )
        assert korean_username.status_code == 200, korean_username.text
        assert korean_username.json()["available"] is True

        wrong_current = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "WrongTemporary!234",
                "new_username": "admin",
            },
        )
        assert wrong_current.status_code == 400, wrong_current.text

        duplicate_username = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "abcdef",
                "new_username": "admin",
            },
        )
        assert duplicate_username.status_code == 409, duplicate_username.text

        invalid_username = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "abcdef",
                "new_username": ".abc",
            },
        )
        assert invalid_username.status_code == 422, invalid_username.text
        assert primary.get("/api/auth/me").json()["username"] == "security-user"

        changed = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "abcdef",
                "new_username": "  security.renamed  ",
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["must_change_password"] is False
        assert changed.json()["username"] == "security.renamed"
        assert primary.get("/api/rooms").status_code == 200
        assert primary.get("/api/admin/staff-directory", headers=ORIGIN).status_code == 403
        old_username_login = post(
            TestClient(app),
            "/api/auth/login",
            {"username": "security-user", "password": "abcdef"},
        )
        assert old_username_login.status_code == 401
        with SessionLocal() as db:
            audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.action == "auth.username_changed",
                    AuditEvent.target_id == employee_id,
                )
                .order_by(AuditEvent.created_at.desc())
            )
            assert audit is not None
            assert audit.details == {
                "old_username": "security-user",
                "new_username": "security.renamed",
                "username_changed": True,
                "password_changed": False,
            }

        secondary = TestClient(app)
        secondary.headers["user-agent"] = "SMCODI Test Secondary"
        login(secondary, "security.renamed", "abcdef")
        sessions = primary.get("/api/auth/sessions")
        assert sessions.status_code == 200, sessions.text
        assert len(sessions.json()) == 2
        assert sum(item["is_current"] for item in sessions.json()) == 1

        too_short = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "abcdef",
                "new_password": "12345",
            },
        )
        assert too_short.status_code == 422, too_short.text

        with secondary.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            changed_again = post(
                primary,
                "/api/auth/password",
                {
                    "current_password": "abcdef",
                    "new_password": "123456",
                },
            )
            assert changed_again.status_code == 200, changed_again.text
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "비밀번호" in forced["reason"]
        assert secondary.get("/api/auth/me").status_code == 401
        assert len(primary.get("/api/auth/sessions").json()) == 1

        third = TestClient(app)
        third.headers["user-agent"] = "SMCODI Test Third"
        login(third, "security.renamed", "123456")
        sessions = primary.get("/api/auth/sessions").json()
        third_session = next(item for item in sessions if not item["is_current"])
        with third.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            revoked = primary.delete(
                f"/api/auth/sessions/{third_session['id']}",
                headers=ORIGIN,
            )
            assert revoked.status_code == 204, revoked.text
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "다른 기기" in forced["reason"]
        assert third.get("/api/auth/me").status_code == 401

        with primary.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            reset = post(
                admin_client,
                f"/api/employees/{employee_id}/reset-password",
                {"temporary_password": "654321"},
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["must_change_password"] is True
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "초기화" in forced["reason"]
        assert primary.get("/api/auth/me").status_code == 401
        old_login = post(
            TestClient(app),
            "/api/auth/login",
            {"username": "security.renamed", "password": "123456"},
        )
        assert old_login.status_code == 401
        reset_login = login(
            TestClient(app),
            "security.renamed",
            "654321",
        )
        assert reset_login["user"]["must_change_password"] is True

        limited_client = TestClient(app)
        for _ in range(5):
            failed = post(
                limited_client,
                "/api/auth/login",
                {"username": "rate-limit-user", "password": "WrongPassword!"},
            )
            assert failed.status_code == 401
        limited = post(
            limited_client,
            "/api/auth/login",
            {"username": "rate-limit-user", "password": "WrongPassword!"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
