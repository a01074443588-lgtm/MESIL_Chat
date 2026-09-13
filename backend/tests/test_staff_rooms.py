from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Room, RoomMembership, User


ORIGIN = {"origin": "http://testserver"}


def _post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def _patch(client: TestClient, path: str, payload: dict):
    return client.patch(path, json=payload, headers=ORIGIN)


def _login(client: TestClient, username: str, password: str) -> dict:
    response = _post(
        client,
        "/api/auth/login",
        {"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()["user"]


def _create_staff(
    admin: TestClient,
    *,
    username: str,
    full_name: str,
    password: str,
) -> dict:
    response = _post(
        admin,
        "/api/employees",
        {
            "username": username,
            "full_name": full_name,
            "password": password,
            "role": "staff",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _activate_staff(
    client: TestClient,
    *,
    username: str,
    temporary_password: str,
    password: str,
) -> dict:
    user = _login(client, username, temporary_password)
    assert user["must_change_password"] is True
    changed = _post(
        client,
        "/api/auth/password",
        {"current_password": temporary_password, "new_password": password},
    )
    assert changed.status_code == 200, changed.text
    return changed.json()


def test_staff_can_create_invite_and_manage_a_group_room() -> None:
    with TestClient(app) as admin_client:
        _login(admin_client, "admin", "AdminPass!234")
        alice = _create_staff(
            admin_client,
            username="room-owner-a",
            full_name="대화방 시험 가람",
            password="111111",
        )
        bob = _create_staff(
            admin_client,
            username="room-member-b",
            full_name="대화방 시험 나래",
            password="222222",
        )
        carol = _create_staff(
            admin_client,
            username="room-member-c",
            full_name="대화방 시험 다온",
            password="333333",
        )

        with (
            TestClient(app) as alice_client,
            TestClient(app) as bob_client,
            TestClient(app) as carol_client,
        ):
            _activate_staff(
                alice_client,
                username="room-owner-a",
                temporary_password="111111",
                password="alice1",
            )
            _activate_staff(
                bob_client,
                username="room-member-b",
                temporary_password="222222",
                password="bob222",
            )
            _activate_staff(
                carol_client,
                username="room-member-c",
                temporary_password="333333",
                password="carol3",
            )

            directory = alice_client.get("/api/staff-directory/active")
            assert directory.status_code == 200, directory.text
            by_name = {entry["full_name"]: entry for entry in directory.json()}
            assert by_name["대화방 시험 가람"]["id"] == alice["id"]
            assert by_name["대화방 시험 나래"]["id"] == bob["id"]
            assert by_name["대화방 시험 다온"]["id"] == carol["id"]

            created = _post(
                alice_client,
                "/api/staff-rooms",
                {"name": "낙상예방 논의", "member_ids": [bob["id"]]},
            )
            assert created.status_code == 201, created.text
            room = created.json()
            room_id = room["id"]
            assert room["owner_name"] == "대화방 시험 가람"
            assert room["is_owner"] is True
            assert set(room["member_ids"]) == {alice["id"], bob["id"]}

            bob_rooms = bob_client.get("/api/staff-rooms")
            assert bob_rooms.status_code == 200, bob_rooms.text
            assert {item["id"] for item in bob_rooms.json()} >= {room_id}
            invited = _post(
                bob_client,
                f"/api/staff-rooms/{room_id}/members",
                {"member_ids": [carol["id"]]},
            )
            assert invited.status_code == 200, invited.text
            assert set(invited.json()["member_ids"]) == {
                alice["id"],
                bob["id"],
                carol["id"],
            }

            non_owner_rename = _patch(
                carol_client,
                f"/api/staff-rooms/{room_id}",
                {"name": "권한 없는 변경"},
            )
            assert non_owner_rename.status_code == 403

            carol_staff_id = by_name["대화방 시험 다온"]["staff_id"]
            transferred = _post(
                alice_client,
                f"/api/staff-rooms/{room_id}/transfer-owner",
                {"new_owner_staff_id": carol_staff_id},
            )
            assert transferred.status_code == 200, transferred.text
            assert transferred.json()["owner_staff_id"] == carol_staff_id
            assert transferred.json()["is_owner"] is False
            assert _patch(
                alice_client,
                f"/api/staff-rooms/{room_id}",
                {"name": "이전 방장의 변경"},
            ).status_code == 403

            renamed = _patch(
                carol_client,
                f"/api/staff-rooms/{room_id}",
                {"name": "낙상예방 업무방"},
            )
            assert renamed.status_code == 200, renamed.text
            assert renamed.json()["name"] == "낙상예방 업무방"
            assert renamed.json()["is_owner"] is True

            bob_staff_id = by_name["대화방 시험 나래"]["staff_id"]
            removed = carol_client.delete(
                f"/api/staff-rooms/{room_id}/members/{bob_staff_id}",
                headers=ORIGIN,
            )
            assert removed.status_code == 200, removed.text
            assert bob["id"] not in removed.json()["member_ids"]
            assert bob_client.get(f"/api/staff-rooms/{room_id}").status_code == 403

            left = _post(alice_client, f"/api/staff-rooms/{room_id}/leave", {})
            assert left.status_code == 204, left.text
            assert alice_client.get(f"/api/staff-rooms/{room_id}").status_code == 403

            closed = _post(carol_client, f"/api/staff-rooms/{room_id}/close", {})
            assert closed.status_code == 204, closed.text
            assert room_id not in {
                item["id"] for item in carol_client.get("/api/staff-rooms").json()
            }

            with SessionLocal() as db:
                actions = set(
                    db.scalars(
                        select(AuditEvent.action).where(
                            AuditEvent.target_id == UUID(room_id)
                        )
                    ).all()
                )
                assert {
                    "room.staff_created",
                    "room.staff_members_invited",
                    "room.staff_owner_transferred",
                    "room.staff_renamed",
                    "room.staff_member_removed",
                    "room.staff_member_left",
                    "room.staff_closed",
                } <= actions


def test_unnamed_staff_room_reuses_the_same_participant_room() -> None:
    with TestClient(app) as admin_client:
        _login(admin_client, "admin", "AdminPass!234")
        caller = _create_staff(
            admin_client,
            username="room-reuse-caller",
            full_name="대화 재사용 가람",
            password="444444",
        )
        receiver = _create_staff(
            admin_client,
            username="room-reuse-receiver",
            full_name="대화 재사용 나래",
            password="555555",
        )

        with TestClient(app) as caller_client, TestClient(app) as receiver_client:
            _activate_staff(
                caller_client,
                username="room-reuse-caller",
                temporary_password="444444",
                password="caller44",
            )
            _activate_staff(
                receiver_client,
                username="room-reuse-receiver",
                temporary_password="555555",
                password="receiver55",
            )
            first = _post(
                caller_client,
                "/api/staff-rooms",
                {"name": None, "member_ids": [receiver["id"]]},
            )
            second = _post(
                caller_client,
                "/api/staff-rooms",
                {"name": None, "member_ids": [receiver["id"]]},
            )
            assert first.status_code == 201, first.text
            assert second.status_code == 201, second.text
            assert second.json()["id"] == first.json()["id"]
            matching_rooms = [
                room
                for room in caller_client.get("/api/staff-rooms").json()
                if set(room["member_ids"]) == {caller["id"], receiver["id"]}
            ]
            assert len(matching_rooms) == 1


def test_legacy_admin_room_update_transfers_owner_and_validates_trimmed_name() -> None:
    with TestClient(app) as admin_client:
        _login(admin_client, "admin", "AdminPass!234")
        alpha = _create_staff(
            admin_client,
            username="legacy-room-owner-alpha",
            full_name="가람 방장 이전 시험",
            password="444444",
        )
        beta = _create_staff(
            admin_client,
            username="legacy-room-owner-beta",
            full_name="나래 방장 이전 시험",
            password="555555",
        )

        blank_name = _post(
            admin_client,
            "/api/rooms/custom",
            {"name": "    ", "member_ids": [alpha["id"], beta["id"]]},
        )
        assert blank_name.status_code == 422, blank_name.text
        one_character_name = _post(
            admin_client,
            "/api/rooms/custom",
            {"name": " 가 ", "member_ids": [alpha["id"], beta["id"]]},
        )
        assert one_character_name.status_code == 422, one_character_name.text

        created = _post(
            admin_client,
            "/api/rooms/custom",
            {
                "name": "  기존 관리방  ",
                "member_ids": [alpha["id"], beta["id"]],
            },
        )
        assert created.status_code == 201, created.text
        assert created.json()["name"] == "기존 관리방"
        room_id = UUID(created.json()["id"])

        with SessionLocal() as db:
            room = db.get(Room, room_id)
            alpha_user = db.get(User, UUID(alpha["id"]))
            beta_user = db.get(User, UUID(beta["id"]))
            assert room is not None
            assert alpha_user is not None and alpha_user.staff_id is not None
            assert beta_user is not None and beta_user.staff_id is not None
            assert room.owner_staff_id == alpha_user.staff_id
            previous_owner_staff_id = alpha_user.staff_id
            new_owner_staff_id = beta_user.staff_id

        invalid_update_name = _patch(
            admin_client,
            f"/api/rooms/custom/{room_id}",
            {"name": " x "},
        )
        assert invalid_update_name.status_code == 422, invalid_update_name.text

        updated = _patch(
            admin_client,
            f"/api/rooms/custom/{room_id}",
            {
                "name": "  변경 관리방  ",
                "member_ids": [beta["id"]],
            },
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "변경 관리방"
        assert updated.json()["member_ids"] == [beta["id"]]

        with SessionLocal() as db:
            room = db.get(Room, room_id)
            assert room is not None
            assert room.owner_staff_id == new_owner_staff_id
            active_owner_membership = db.scalar(
                select(RoomMembership).where(
                    RoomMembership.room_id == room_id,
                    RoomMembership.staff_id == new_owner_staff_id,
                    RoomMembership.left_at.is_(None),
                )
            )
            assert active_owner_membership is not None
            transfer_audit = db.scalar(
                select(AuditEvent)
                .where(
                    AuditEvent.target_id == room_id,
                    AuditEvent.action == "room.staff_owner_transferred",
                )
                .order_by(AuditEvent.created_at.desc())
            )
            assert transfer_audit is not None
            assert transfer_audit.details == {
                "previous_owner_staff_id": str(previous_owner_staff_id),
                "owner_staff_id": str(new_owner_staff_id),
                "source": "legacy_admin_update",
            }
