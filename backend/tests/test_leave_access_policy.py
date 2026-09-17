from fastapi.testclient import TestClient
from sqlalchemy import select
from uuid import UUID

from app.database import SessionLocal
from app.main import app
from app.models import LoginSession, Room, RoomMembership, User
from app.security import hash_password
from app.services import room_member_user_ids, sync_auto_memberships


ORIGIN = {"origin": "http://testserver"}


def test_leave_suspends_access_without_destroying_room_entitlements() -> None:
    username = "leave-policy-user"
    password = "LeavePolicy!234"

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
                "full_name": "휴직 정책 시험직원",
                "password": password,
                "role": "staff",
                "employee_code": "LEAVE-POLICY-001",
            },
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        user_id = UUID(created.json()["id"])

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.staff is not None
            user.must_change_password = False
            user.password_hash = hash_password(password)
            all_room = db.scalar(
                select(Room).where(
                    Room.organization_id == user.organization_id,
                    Room.kind == "all",
                    Room.is_active.is_(True),
                )
            )
            assert all_room is not None
            sync_auto_memberships(db, user)
            db.commit()
            staff_id = user.staff_id
            room_id = all_room.id

        employee_client = TestClient(app)
        active_login = employee_client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=ORIGIN,
        )
        assert active_login.status_code == 200, active_login.text

        with SessionLocal() as db:
            user = db.get(User, user_id)
            assert user is not None and user.staff is not None
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == staff_id,
                    RoomMembership.room_id == room_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert membership is not None
            joined_at = membership.joined_at
            user.staff.employment_status = "leave"
            sync_auto_memberships(db, user)
            db.commit()

        existing_session = employee_client.get("/api/auth/me")
        assert existing_session.status_code == 403
        assert "휴직" in existing_session.json()["detail"]
        assert employee_client.get("/api/auth/me").status_code == 401

        blocked_login = TestClient(app).post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=ORIGIN,
        )
        assert blocked_login.status_code == 403
        assert "휴직" in blocked_login.json()["detail"]

        with SessionLocal() as db:
            membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.staff_id == staff_id,
                    RoomMembership.room_id == room_id,
                )
            )
            assert membership is not None
            assert membership.left_at is None
            assert membership.joined_at == joined_at
            assert user_id not in room_member_user_ids(db, room_id)
            assert db.scalar(
                select(LoginSession).where(
                    LoginSession.user_id == user_id,
                    LoginSession.revoked_at.is_(None),
                )
            ) is None

            user = db.get(User, user_id)
            assert user is not None and user.staff is not None
            user.staff.employment_status = "active"
            sync_auto_memberships(db, user)
            db.commit()
            assert user_id in room_member_user_ids(db, room_id)

        restored_login = TestClient(app).post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=ORIGIN,
        )
        assert restored_login.status_code == 200, restored_login.text
