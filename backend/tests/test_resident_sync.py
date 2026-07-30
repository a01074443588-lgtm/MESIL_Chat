import json
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import AuditEvent, Organization, RecipientRoom, Resident


ORIGIN = {"origin": "http://testserver"}


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def create_floor(client: TestClient, name: str) -> str:
    response = client.post(
        "/api/org-units",
        json={"unit_type": "floor", "name": name},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def preview(
    client: TestClient,
    name: str,
    rows: list[dict],
    *,
    practice_mode: bool = False,
) -> dict:
    content = json.dumps(
        {
            "generated_at": "2026-07-27T09:00:00+09:00",
            "residents": rows,
        },
        ensure_ascii=False,
    ).encode("utf-8")
    response = client.post(
        "/api/admin/resident-sync/preview",
        files={"file": (name, content, "application/json")},
        data={"practice_mode": str(practice_mode).lower()},
        headers=ORIGIN,
    )
    assert response.status_code == 201, response.text
    return response.json()


def apply_items(client: TestClient, batch_id: str, item_ids: list[str]):
    return client.post(
        f"/api/admin/resident-sync/batches/{batch_id}/apply",
        json={"item_ids": item_ids},
        headers=ORIGIN,
    )


def test_admin_approved_resident_roster_sync():
    with TestClient(app) as client:
        login_admin(client)
        floor_id = create_floor(client, "동기화시험3층")

        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            manual_room = RecipientRoom(
                organization_id=organization.id,
                internal_code="MANUAL-SYNC-SAFETY-ROOM",
                name="수동 등록 생활실",
                floor="동기화시험3층",
                floor_unit_id=UUID(floor_id),
            )
            db.add(manual_room)
            db.flush()
            manual_resident = Resident(
                organization_id=organization.id,
                internal_code="MANUAL-SYNC-SAFETY-RESIDENT",
                display_name="가명 수동등록 어르신",
                service_type="facility",
                room_id=manual_room.id,
                is_test_data=True,
            )
            db.add(manual_resident)
            db.commit()
            manual_id = manual_resident.id

        first_batch = preview(
            client,
            "가명_첫명단.json",
            [
                {
                    "external_id": "sync-test-a",
                    "display_name": "가명 동기화 어르신 A",
                    "service_type": "facility_care",
                    "floor": "동기화시험3층",
                    "room_name": "가명 301호",
                    "is_active": True,
                },
                {
                    "external_id": "sync-test-b",
                    "display_name": "가명 동기화 어르신 B",
                    "service_type": "시설",
                    "floor": "동기화시험3층",
                    "room_name": "가명 302호",
                    "is_active": True,
                },
                {
                    "external_id": "sync-test-conflict",
                    "display_name": "가명 확인필요 어르신",
                    "service_type": "facility",
                    "floor": "존재하지않는층",
                    "is_active": True,
                },
            ],
        )
        assert first_batch["summary"]["new"] == 2
        assert first_batch["summary"]["conflict"] == 1
        assert first_batch["status"] == "preview"
        assert all(
            item["status"] == "blocked"
            for item in first_batch["items"]
            if item["change_type"] == "conflict"
        )

        first_ids = [
            item["id"]
            for item in first_batch["items"]
            if item["change_type"] == "new"
        ]
        first_apply = apply_items(client, first_batch["id"], first_ids)
        assert first_apply.status_code == 200, first_apply.text
        assert first_apply.json()["status"] == "applied"

        active_names = {
            resident["display_name"]
            for resident in client.get(
                "/api/admin/residents", headers=ORIGIN
            ).json()
        }
        assert "가명 동기화 어르신 A" in active_names
        assert "가명 동기화 어르신 B" in active_names
        assert "가명 수동등록 어르신" in active_names

        practice_batch = preview(
            client,
            "가명_연습명단.json",
            [
                {
                    "external_id": "sync-practice-only",
                    "display_name": "가명 연습 어르신",
                    "service_type": "facility",
                    "floor": "동기화시험3층",
                    "room_name": "가명 연습실",
                    "is_active": True,
                }
            ],
            practice_mode=True,
        )
        assert practice_batch["source"] == "practice_example"
        assert practice_batch["summary"]["new"] == 1
        assert practice_batch["summary"]["deactivate"] == 0

        second_batch = preview(
            client,
            "가명_변경명단.json",
            [
                {
                    "external_id": "sync-test-a",
                    "display_name": "가명 동기화 어르신 A-변경",
                    "service_type": "facility",
                    "floor": "동기화시험3층",
                    "room_name": "가명 303호",
                    "is_active": True,
                },
                {
                    "external_id": "sync-test-c",
                    "display_name": "가명 동기화 어르신 C",
                    "service_type": "facility",
                    "floor": "동기화시험3층",
                    "room_name": "가명 304호",
                    "is_active": True,
                },
            ],
        )
        assert second_batch["summary"]["update"] == 1
        assert second_batch["summary"]["new"] == 1
        assert second_batch["summary"]["deactivate"] == 1
        assert not any(
            item["current_resident_id"] == str(manual_id)
            for item in second_batch["items"]
        )

        update_id = next(
            item["id"]
            for item in second_batch["items"]
            if item["change_type"] == "update"
        )
        partial_apply = apply_items(client, second_batch["id"], [update_id])
        assert partial_apply.status_code == 200, partial_apply.text
        assert partial_apply.json()["status"] == "partially_applied"
        active_names = {
            resident["display_name"]
            for resident in client.get(
                "/api/admin/residents", headers=ORIGIN
            ).json()
        }
        assert "가명 동기화 어르신 A-변경" in active_names
        assert "가명 동기화 어르신 B" in active_names

        remaining_ids = [
            item["id"]
            for item in partial_apply.json()["items"]
            if item["status"] == "pending"
        ]
        final_apply = apply_items(client, second_batch["id"], remaining_ids)
        assert final_apply.status_code == 200, final_apply.text
        assert final_apply.json()["status"] == "applied"
        assert final_apply.json()["summary"]["remaining"] == 0

        active_names = {
            resident["display_name"]
            for resident in client.get(
                "/api/admin/residents", headers=ORIGIN
            ).json()
        }
        assert "가명 동기화 어르신 A-변경" in active_names
        assert "가명 동기화 어르신 C" in active_names
        assert "가명 동기화 어르신 B" not in active_names
        assert "가명 수동등록 어르신" in active_names

        with SessionLocal() as db:
            deactivated = db.scalar(
                select(Resident).where(
                    Resident.internal_code == "SMCODI:sync-test-b"
                )
            )
            assert deactivated is not None
            assert deactivated.is_active is False
            assert deactivated.status == "inactive"
            audit_actions = set(db.scalars(select(AuditEvent.action)).all())
            assert "recipients.sync_preview_created" in audit_actions
            assert "recipients.sync_changes_applied" in audit_actions


def test_resident_sync_rejects_empty_and_non_admin_access():
    with TestClient(app) as anonymous:
        response = anonymous.post(
            "/api/admin/resident-sync/preview",
            files={
                "file": (
                    "empty.json",
                    b'{"residents": []}',
                    "application/json",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 401

    with TestClient(app) as admin:
        login_admin(admin)
        response = admin.post(
            "/api/admin/resident-sync/preview",
            files={
                "file": (
                    "empty.json",
                    b'{"residents": []}',
                    "application/json",
                )
            },
            headers=ORIGIN,
        )
        assert response.status_code == 422
        assert "빈 명단" in response.json()["detail"]


def test_carefor_alias_roster_status_preview_and_manual_management(tmp_path):
    identity_path = tmp_path / "carefor_identity_map.local.json"
    resident_path = tmp_path / "carefor_residents.local.json"
    staff_path = tmp_path / "carefor_staff.local.json"
    identity_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-27T12:00:00+09:00",
                "sources": {
                    "facility": {
                        "status": "captured",
                        "captured_at": "2026-07-27T11:50:00+09:00",
                        "resident_count": 1,
                        "staff_count": 2,
                    },
                    "daycare": {"status": "login_required"},
                    "homecare": {"status": "login_required"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    resident_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-07-27T12:00:00+09:00",
                "residents": [
                    {
                        "external_id": "carefor:facility:resident:test-a",
                        "display_name": "시설(가명)시험001",
                        "service_type": "facility",
                        "floor": "케어포시험3층",
                        "room_name": "케어포시험3층 생활구역",
                        "is_active": True,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    staff_path.write_text(
        json.dumps({"staff": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    original_paths = (
        settings.carefor_identity_map_path,
        settings.carefor_resident_roster_path,
        settings.carefor_staff_roster_path,
    )
    settings.carefor_identity_map_path = identity_path.as_posix()
    settings.carefor_resident_roster_path = resident_path.as_posix()
    settings.carefor_staff_roster_path = staff_path.as_posix()
    try:
        with TestClient(app) as client:
            login_admin(client)
            floor_id = create_floor(client, "케어포시험3층")

            status_response = client.get(
                "/api/admin/carefor-roster/status",
                headers=ORIGIN,
            )
            assert status_response.status_code == 200, status_response.text
            assert status_response.json()["sources"]["facility"] == {
                "status": "captured",
                "captured_at": "2026-07-27T02:50:00Z",
                "resident_count": 1,
                "staff_count": 2,
                "staff_aliases": [],
            }
            assert (
                status_response.json()["sources"]["daycare"]["status"]
                == "login_required"
            )

            preview_response = client.post(
                "/api/admin/carefor-roster/preview",
                data={"service_type": "facility"},
                headers=ORIGIN,
            )
            assert preview_response.status_code == 201, preview_response.text
            batch = preview_response.json()
            assert batch["source"] == "carefor_read_only_capture"
            assert batch["summary"]["new"] == 1
            assert batch["summary"]["deactivate"] == 0
            item_id = next(
                item["id"]
                for item in batch["items"]
                if item["change_type"] == "new"
            )
            apply_response = apply_items(client, batch["id"], [item_id])
            assert apply_response.status_code == 200, apply_response.text
            assert any(
                resident["display_name"] == "시설(가명)시험001"
                for resident in client.get(
                    "/api/admin/residents",
                    headers=ORIGIN,
                ).json()
            )

            manual_response = client.post(
                "/api/admin/residents",
                json={
                    "display_name": "시설(가명)수동001",
                    "service_type": "facility",
                    "floor_id": floor_id,
                },
                headers=ORIGIN,
            )
            assert manual_response.status_code == 201, manual_response.text
            manual_id = manual_response.json()["id"]
            end_response = client.delete(
                f"/api/admin/residents/{manual_id}",
                headers=ORIGIN,
            )
            assert end_response.status_code == 204, end_response.text
            assert not any(
                resident["id"] == manual_id
                for resident in client.get(
                    "/api/admin/residents",
                    headers=ORIGIN,
                ).json()
            )
    finally:
        (
            settings.carefor_identity_map_path,
            settings.carefor_resident_roster_path,
            settings.carefor_staff_roster_path,
        ) = original_paths
