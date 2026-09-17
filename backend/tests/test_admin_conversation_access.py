from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, LoginSession, User
from app.services import utcnow


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


def test_admin_must_reenter_password_and_access_expires_or_can_be_revoked() -> None:
    with TestClient(app) as admin_client:
        _login(admin_client, "admin", "AdminPass!234")
        room = next(
            item for item in admin_client.get("/api/rooms").json()
            if item["kind"] == "all"
        )
        room_id = room["id"]
        message = _post(
            admin_client,
            f"/api/rooms/{room_id}/messages",
            {"body": "관리자 재인증 시험 메시지", "message_type": "chat"},
        )
        assert message.status_code == 201, message.text

        status = admin_client.get("/api/admin/conversation-access")
        assert status.status_code == 200, status.text
        assert status.headers["cache-control"] == "private, no-store"
        assert status.json() == {"active": False, "expires_at": None}
        assert admin_client.get(
            f"/api/admin/conversations/rooms/{room_id}/messages"
        ).status_code == 403

        wrong = _post(
            admin_client,
            "/api/admin/conversation-access",
            {"password": "wrong-password"},
        )
        assert wrong.status_code == 400, wrong.text
        assert "비밀번호" in wrong.json()["detail"]

        granted = _post(
            admin_client,
            "/api/admin/conversation-access",
            {"password": "AdminPass!234"},
        )
        assert granted.status_code == 200, granted.text
        assert granted.headers["cache-control"] == "private, no-store"
        assert granted.json()["active"] is True
        assert granted.json()["expires_at"] is not None
        messages = admin_client.get(
            f"/api/admin/conversations/rooms/{room_id}/messages"
        )
        assert messages.status_code == 200, messages.text
        assert messages.headers["cache-control"] == "private, no-store"
        assert message.json()["id"] in {item["id"] for item in messages.json()}

        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None
            session = db.scalar(
                select(LoginSession)
                .where(
                    LoginSession.user_id == admin.id,
                    LoginSession.revoked_at.is_(None),
                )
                .order_by(LoginSession.created_at.desc())
            )
            assert session is not None
            session.admin_conversation_access_expires_at = utcnow() - timedelta(
                seconds=1
            )
            db.commit()

        expired = admin_client.get("/api/admin/conversation-access")
        assert expired.status_code == 200, expired.text
        assert expired.headers["cache-control"] == "private, no-store"
        assert expired.json() == {"active": False, "expires_at": None}
        assert admin_client.get(
            f"/api/admin/conversations/rooms/{room_id}/messages"
        ).status_code == 403

        regranted = _post(
            admin_client,
            "/api/admin/conversation-access",
            {"password": "AdminPass!234"},
        )
        assert regranted.status_code == 200, regranted.text
        assert regranted.headers["cache-control"] == "private, no-store"
        revoked = admin_client.delete(
            "/api/admin/conversation-access",
            headers=ORIGIN,
        )
        assert revoked.status_code == 204, revoked.text
        assert revoked.headers["cache-control"] == "private, no-store"
        assert admin_client.get("/api/admin/conversation-access").json() == {
            "active": False,
            "expires_at": None,
        }

        created_staff = _post(
            admin_client,
            "/api/employees",
            {
                "username": "conversation-access-staff",
                "full_name": "대화열람 권한시험",
                "password": "123456",
                "role": "staff",
            },
        )
        assert created_staff.status_code == 201, created_staff.text
        with TestClient(app) as staff_client:
            _login(staff_client, "conversation-access-staff", "123456")
            changed = _post(
                staff_client,
                "/api/auth/password",
                {"current_password": "123456", "new_password": "abcdef"},
            )
            assert changed.status_code == 200, changed.text
            assert staff_client.get("/api/admin/conversation-access").status_code == 403

        with SessionLocal() as db:
            actions = set(
                db.scalars(
                    select(AuditEvent.action).where(
                        AuditEvent.action.in_(
                            {
                                "admin.conversation_access_failed",
                                "admin.conversation_access_granted",
                                "admin.conversation_room_viewed",
                                "admin.conversation_access_revoked",
                            }
                        )
                    )
                ).all()
            )
            assert {
                "admin.conversation_access_failed",
                "admin.conversation_access_granted",
                "admin.conversation_room_viewed",
                "admin.conversation_access_revoked",
            } <= actions
