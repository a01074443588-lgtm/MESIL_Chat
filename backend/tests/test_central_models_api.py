from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
import pytest

from app import main as main_module
from app import ai_system as ai_system_module
from app.ai_settings_store import load_ai_settings, save_ai_settings
from app.database import SessionLocal
from app.main import app
from app.models import Message, MessageComment, Resident, RoomMembership, User
from sqlalchemy import select

ORIGIN = {"origin": "http://testserver"}


@pytest.fixture(autouse=True)
def restore_configuration():
    previous = load_ai_settings()[0].model_copy(deep=True)
    yield
    save_ai_settings(previous)


def test_admin_settings_and_permission_revocation_during_generation(monkeypatch):
    with TestClient(app) as admin:
        assert admin.post("/api/auth/login", json={"username": "admin", "password": "AdminPass!234"}, headers=ORIGIN).status_code == 200
        old_roles = load_ai_settings()[0].model_roles
        current = admin.get("/api/ai/system/central-models")
        assert current.status_code == 200 and "base_url" not in current.json()
        saved = admin.put("/api/ai/system/settings/central-models", headers=ORIGIN, json={"text_default_model": "test-local:model", "vision_default_model": "test-vision:model", "feature_overrides": {"search_summary": "test-override:model"}})
        assert saved.status_code == 200
        assert saved.json()["features"]["care_record_question"]["model"] == "test-local:model"
        assert saved.json()["features"]["search_summary"]["inheritance"] == "override"
        assert load_ai_settings()[0].model_roles == old_roles
        rejected = admin.put("/api/ai/system/settings/central-models", headers=ORIGIN, json={"text_fallback_model": "unverified:model", "fallback_qualified": True})
        assert rejected.status_code == 422
        name = "scope_" + uuid4().hex[:8]
        employee = admin.post("/api/employees", headers=ORIGIN, json={"username": name, "password": "SyntheticPass!234", "full_name": "합성 권한직원", "role": "staff", "can_process_records": True})
        assert employee.status_code == 201
        uid = UUID(employee.json()["id"])
        room = admin.post("/api/admin/rooms", headers=ORIGIN, json={"name": "합성 동시권한 시험방", "kind": "custom", "member_ids": [str(uid)], "resident_scope": "all"})
        assert room.status_code == 201
        rid = UUID(room.json()["id"])
        with SessionLocal() as db:
            user = db.get(User, uid)
            user.must_change_password = False
            staff_id = user.staff_id
            resident = Resident(organization_id=user.organization_id, internal_code="SYN-" + uuid4().hex, display_name="권한합성어르신", service_type="facility", is_test_data=True)
            db.add(resident)
            db.flush()
            resident_id = resident.id
            message = Message(organization_id=user.organization_id, room_id=rid, sender_id=uid, resident_id=resident_id, body="점심 절반 섭취함. 이후 3/4 섭취함.", is_test_data=True, created_at=datetime(2026, 8, 6, 4, tzinfo=timezone.utc))
            db.add(message)
            db.flush()
            db.add(MessageComment(organization_id=user.organization_id, message_id=message.id, author_id=uid, body="관리자 코멘트 전용 합성표식", is_test_data=True))
            db.commit()
            mid = message.id
        with TestClient(app) as staff:
            assert staff.post("/api/auth/login", headers=ORIGIN, json={"username": name, "password": "SyntheticPass!234"}).status_code == 200
            assert staff.get("/api/ai/system/central-models").status_code == 403
            assert staff.put("/api/ai/system/settings/central-models", headers=ORIGIN, json={}).status_code == 403
            assert staff.post(f"/api/rooms/{rid}/message-search/summary", headers=ORIGIN, json={"message_ids": [str(mid)], "local_model_override": "forbidden:model"}).status_code == 403
            captured = []
            async def inspect_staff_input(**kwargs):
                captured.extend(kwargs["facts"])
                return {
                    "processing_method": "rules", "generation_verified": False,
                    "model_used": None, "ai_elapsed_ms": 0,
                    "error_type": "synthetic_fallback",
                }
            monkeypatch.setattr(main_module, "generate_search_summary", inspect_staff_input)
            summary = staff.post(f"/api/rooms/{rid}/message-search/summary", headers=ORIGIN, json={"message_ids": [str(mid)]})
            assert summary.status_code == 200
            assert "관리자 코멘트 전용 합성표식" not in summary.text
            assert captured and all("관리자 코멘트 전용 합성표식" not in fact["summary"] for fact in captured)
            async def ready_model(*, wait=True):
                return {
                    "status": "ready",
                    "status_ms": 0,
                    "total_ms": 0,
                    "cold_load_ms": 0,
                    "preparation_call_ms": 0,
                }

            async def revoke(**kwargs):
                assert kwargs["facts"]
                with SessionLocal() as other:
                    membership = other.scalar(select(RoomMembership).where(RoomMembership.room_id == rid, RoomMembership.staff_id == staff_id))
                    membership.left_at = datetime.now(timezone.utc)
                    other.commit()
                return {
                    "generation_verified": False,
                    "error_type": "model_server_error",
                    "ai_elapsed_ms": 1,
                    "load_ms": 0,
                    "generation_ms": 0,
                    "prefill_ms": 0,
                }
            monkeypatch.setattr(main_module, "prepare_record_model", ready_model)
            monkeypatch.setattr(main_module, "generate_narrative", revoke)
            answer = staff.post("/api/workdesk/record-question", headers=ORIGIN, json={"start_date": "2026-08-06", "end_date": "2026-08-06", "resident_id": str(resident_id), "question": "식사 이후 경과"})
            assert answer.status_code == 403
            assert "must never be displayed" not in answer.text


def test_central_catalog_distinguishes_connection_failure_from_empty_catalog(monkeypatch):
    with TestClient(app) as admin:
        assert admin.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=ORIGIN,
        ).status_code == 200

        def refuse_connection(*_args, **_kwargs):
            raise ConnectionRefusedError("synthetic refusal")

        monkeypatch.setattr(ai_system_module, "local_json_request", refuse_connection)
        failed = admin.post("/api/ai/system/central-models/catalog", headers=ORIGIN)
        assert failed.status_code == 200
        assert failed.json()["status"] == "failed"
        assert failed.json()["failure_code"] == "connection_failed"
        assert "synthetic refusal" not in failed.text

        monkeypatch.setattr(
            ai_system_module,
            "local_json_request",
            lambda *_args, **_kwargs: {"models": []},
        )
        empty = admin.post("/api/ai/system/central-models/catalog", headers=ORIGIN)
        assert empty.status_code == 200
        assert empty.json()["status"] == "failed"
        assert empty.json()["failure_code"] == "catalog_empty"

        monkeypatch.setattr(
            ai_system_module,
            "local_json_request",
            lambda *_args, **_kwargs: {
                "models": [
                    {"name": "actual:model"},
                    {"name": "remote:cloud"},
                    {"name": 123},
                ]
            },
        )
        connected = admin.post("/api/ai/system/central-models/catalog", headers=ORIGIN)
        assert connected.status_code == 200
        assert connected.json()["status"] == "connected"
        assert connected.json()["models"] == ["actual:model"]
        assert connected.json()["failure_code"] is None
