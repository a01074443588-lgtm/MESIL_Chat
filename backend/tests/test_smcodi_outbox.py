from __future__ import annotations

from datetime import datetime, timezone
import inspect
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException, Response
from sqlalchemy import delete, func, select

from app.confirmed_records import ConfirmedWorkRecord, ConfirmedWorkRecordVersion
from app.database import Base, SessionLocal, engine
from app.models import (
    Message,
    Organization,
    Resident,
    Room,
    Staff,
    User,
    WorkItem,
)
from app.smcodi_outbox import (
    SmcodiOutboxNotFoundError,
    SmcodiOutboxSourceError,
    _payload_hash,
    _require_outbox_user,
    create_or_get_handover_preview,
    get_handover_preview,
    get_smcodi_handover_preview,
    list_handover_previews,
    list_smcodi_handover_previews,
    preview_smcodi_handover,
    router,
)
from app.smcodi_outbox_models import (
    SmcodiHandoverOutbox,
    SmcodiHandoverTargetMapping,
)
from app.smcodi_outbox_schemas import SmcodiOutboxPreviewRequest


@pytest.fixture(scope="module", autouse=True)
def _create_outbox_tables():
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _remove_outbox_test_organizations():
    yield
    with SessionLocal() as db:
        organization_ids = list(
            db.scalars(
                select(Organization.id).where(
                    Organization.internal_code.like("smcodi-outbox-org-%")
                )
            ).all()
        )
        if not organization_ids:
            return
        db.execute(
            delete(SmcodiHandoverOutbox).where(
                SmcodiHandoverOutbox.organization_id.in_(organization_ids)
            )
        )
        db.execute(
            delete(SmcodiHandoverTargetMapping).where(
                SmcodiHandoverTargetMapping.organization_id.in_(organization_ids)
            )
        )
        record_ids = list(
            db.scalars(
                select(ConfirmedWorkRecord.id).where(
                    ConfirmedWorkRecord.organization_id.in_(organization_ids)
                )
            ).all()
        )
        if record_ids:
            db.execute(
                delete(ConfirmedWorkRecordVersion).where(
                    ConfirmedWorkRecordVersion.record_id.in_(record_ids)
                )
            )
            db.execute(
                delete(ConfirmedWorkRecord).where(
                    ConfirmedWorkRecord.id.in_(record_ids)
                )
            )
        db.execute(
            delete(WorkItem).where(WorkItem.organization_id.in_(organization_ids))
        )
        db.execute(
            delete(Message).where(Message.organization_id.in_(organization_ids))
        )
        db.execute(
            delete(Resident).where(Resident.organization_id.in_(organization_ids))
        )
        db.execute(delete(Room).where(Room.organization_id.in_(organization_ids)))
        db.execute(delete(User).where(User.organization_id.in_(organization_ids)))
        db.execute(delete(Staff).where(Staff.organization_id.in_(organization_ids)))
        db.execute(delete(Organization).where(Organization.id.in_(organization_ids)))
        db.commit()


def _new_organization(db) -> Organization:
    suffix = uuid4().hex[:12]
    organization = Organization(
        internal_code=f"smcodi-outbox-org-{suffix}",
        name=f"SMCODI 대기함 시험기관 {suffix}",
        service_type="facility_care",
    )
    db.add(organization)
    db.flush()
    return organization


def _seed_source(
    db,
    *,
    organization: Organization | None = None,
    is_test_data: bool = True,
    urgency: str = "medium",
) -> tuple[ConfirmedWorkRecord, ConfirmedWorkRecordVersion, User]:
    suffix = uuid4().hex[:12]
    organization = organization or _new_organization(db)
    staff = Staff(
        organization_id=organization.id,
        internal_code=f"OUTBOX-{suffix}",
        display_name=f"대기함 시험자 {suffix}",
        job_title="사회복지사",
        position_title="업무처리자",
        employment_status="active",
        is_test_data=is_test_data,
        is_active=True,
    )
    db.add(staff)
    db.flush()
    user = User(
        organization_id=organization.id,
        staff_id=staff.id,
        username=f"outbox-{suffix}",
        display_name=staff.display_name,
        password_hash="not-used-in-service-test",
        can_process_records=True,
    )
    db.add(user)
    db.flush()
    room = Room(
        organization_id=organization.id,
        kind="custom",
        name=f"대기함 시험방 {suffix}",
        resident_scope="all",
        created_by_id=user.id,
        is_test_data=is_test_data,
    )
    resident = Resident(
        organization_id=organization.id,
        internal_code=f"RECIPIENT-{suffix}",
        display_name=f"연계 시험 어르신 {suffix}",
        service_type="facility",
        status="active",
        is_test_data=is_test_data,
        is_active=True,
    )
    db.add_all([room, resident])
    db.flush()
    occurred_at = datetime(2026, 8, 4, 9, 15, tzinfo=timezone.utc)
    message = Message(
        organization_id=organization.id,
        room_id=room.id,
        sender_id=user.id,
        message_type="chat",
        body="식사량과 수분 섭취를 확인했습니다.",
        resident_id=resident.id,
        is_test_data=is_test_data,
        created_at=occurred_at,
    )
    db.add(message)
    db.flush()
    work_item = WorkItem(
        organization_id=organization.id,
        source_message_id=message.id,
        resident_id=resident.id,
        status="confirmed",
        source_snapshot={"message_id": str(message.id)},
        document_types=[],
        confirmed_by_id=user.id,
        confirmed_at=occurred_at,
        is_test_data=is_test_data,
    )
    db.add(work_item)
    db.flush()
    snapshot = {
        "work_item_id": str(work_item.id),
        "resident_id": str(resident.id),
        "occurred_at": occurred_at.isoformat().replace("+00:00", "Z"),
        "service_context": "facility_care",
        "work_category": "nutrition",
        "observation_text": "아침 식사량이 절반이었습니다.",
        "action_text": "물을 권유하고 간호팀에 공유했습니다.",
        "result_text": "물 200ml를 드셨습니다.",
        "measurement_text": "식사량 1/2, 물 200ml",
        "urgency": urgency,
        "needs_follow_up": True,
        "evidence": [],
    }
    content_hash = _payload_hash(snapshot)
    record = ConfirmedWorkRecord(
        organization_id=organization.id,
        work_item_id=work_item.id,
        resident_id=resident.id,
        occurred_at=occurred_at,
        service_context="facility_care",
        work_category="nutrition",
        observation_text=snapshot["observation_text"],
        action_text=snapshot["action_text"],
        result_text=snapshot["result_text"],
        measurement_text=snapshot["measurement_text"],
        urgency=urgency,
        needs_follow_up=True,
        lifecycle_status="active",
        current_version=1,
        current_content_hash=content_hash,
        confirmed_by_user_id=user.id,
        confirmed_at=occurred_at,
        is_test_data=is_test_data,
    )
    db.add(record)
    db.flush()
    version = ConfirmedWorkRecordVersion(
        organization_id=organization.id,
        record_id=record.id,
        version=1,
        content_hash=content_hash,
        snapshot=snapshot,
        change_reason="시험 확정",
        created_by_user_id=user.id,
    )
    db.add(version)
    db.flush()
    return record, version, user


def _verified_mapping(
    db,
    *,
    record: ConfirmedWorkRecord,
    actor: User,
) -> SmcodiHandoverTargetMapping:
    mapping = db.scalar(
        select(SmcodiHandoverTargetMapping).where(
            SmcodiHandoverTargetMapping.organization_id == record.organization_id,
            SmcodiHandoverTargetMapping.local_resident_id == record.resident_id,
            SmcodiHandoverTargetMapping.is_active.is_(True),
        )
    )
    if mapping is not None:
        return mapping
    mapping = SmcodiHandoverTargetMapping(
        organization_id=record.organization_id,
        local_resident_id=record.resident_id,
        target_recipient_id=uuid4(),
        target_unit_id=uuid4(),
        verified_by_user_id=actor.id,
        verified_at=datetime.now(timezone.utc),
        is_active=True,
        is_test_data=record.is_test_data,
    )
    db.add(mapping)
    db.flush()
    return mapping


def _mapped_request(
    db,
    record: ConfirmedWorkRecord,
    actor: User,
    *,
    version: int | None = None,
):
    mapping = _verified_mapping(db, record=record, actor=actor)
    return SmcodiOutboxPreviewRequest(
        source_record_id=record.id,
        source_version=version,
        target_mapping_id=mapping.id,
    )


@pytest.mark.parametrize(
    ("source_urgency", "expected_urgency"),
    [
        ("low", "normal"),
        ("medium", "caution"),
        ("high", "caution"),
        ("urgent", "urgent"),
    ],
)
def test_ready_preview_preserves_confirmed_version_and_maps_urgency(
    source_urgency: str,
    expected_urgency: str,
):
    with SessionLocal() as db:
        record, version, actor = _seed_source(db, urgency=source_urgency)
        result = create_or_get_handover_preview(
            db,
            actor=actor,
            request=_mapped_request(db, record, actor),
        )

        assert result.created is True
        assert result.item.status == "ready"
        assert result.item.validation_issues == []
        assert result.item.source_version_id == version.id
        assert result.item.source_version == 1
        assert result.item.source_content_hash == version.content_hash
        assert result.item.is_test_data is True
        assert result.item.target_mapping_id is not None
        assert result.item.payload["contract"] == "smcodi_staff_hub_handover_v1"
        assert result.item.payload["occurred_at"] == version.snapshot["occurred_at"]
        assert result.item.payload["observation_text"] == (
            version.snapshot["observation_text"]
        )
        assert result.item.payload["action_text"] == version.snapshot["action_text"]
        assert result.item.payload["result_text"] == version.snapshot["result_text"]
        assert result.item.payload["measurement_text"] == (
            version.snapshot["measurement_text"]
        )
        assert result.item.payload["needs_follow_up"] is True
        assert result.item.payload["urgency"] == expected_urgency
        assert result.item.payload["source_record_id"] == str(record.id)
        assert result.item.payload["source_version_id"] == str(version.id)
        assert result.item.payload["source_content_hash"] == version.content_hash
        assert result.item.payload["idempotency_key"] == result.item.idempotency_key


def test_missing_mapping_is_saved_blocked_with_visible_validation_issues():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        result = create_or_get_handover_preview(
            db,
            actor=actor,
            request=SmcodiOutboxPreviewRequest(source_record_id=record.id),
        )

        assert result.item.status == "blocked"
        assert {issue["code"] for issue in result.item.validation_issues} == {
            "missing_target_mapping",
        }
        assert result.item.payload["recipient_id"] is None
        assert result.item.payload["target_unit_id"] is None
        assert result.item.payload["assigned_user_id"] is None


def test_arbitrary_or_cross_resident_mapping_never_becomes_ready():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        arbitrary = create_or_get_handover_preview(
            db,
            actor=actor,
            request=SmcodiOutboxPreviewRequest(
                source_record_id=record.id,
                target_mapping_id=uuid4(),
            ),
        )
        assert arbitrary.item.status == "blocked"
        assert [issue["code"] for issue in arbitrary.item.validation_issues] == [
            "unverified_target_mapping"
        ]
        assert arbitrary.item.target_recipient_id is None

        other_record, _other_version, other_actor = _seed_source(
            db,
            organization=db.get(Organization, record.organization_id),
        )
        other_mapping = _verified_mapping(
            db,
            record=other_record,
            actor=other_actor,
        )
        cross_resident = create_or_get_handover_preview(
            db,
            actor=actor,
            request=SmcodiOutboxPreviewRequest(
                source_record_id=record.id,
                target_mapping_id=other_mapping.id,
            ),
        )
        assert cross_resident.item.status == "blocked"
        assert cross_resident.item.target_recipient_id is None


def test_withdrawn_confirmed_record_is_always_blocked():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        request = _mapped_request(db, record, actor)
        record.lifecycle_status = "withdrawn"
        db.flush()

        result = create_or_get_handover_preview(db, actor=actor, request=request)

        assert result.item.status == "blocked"
        assert "source_record_inactive" in {
            issue["code"] for issue in result.item.validation_issues
        }


def test_existing_ready_preview_is_downgraded_after_source_withdrawal():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        request = _mapped_request(db, record, actor)
        first = create_or_get_handover_preview(db, actor=actor, request=request)
        assert first.item.status == "ready"

        record.lifecycle_status = "withdrawn"
        db.flush()
        repeated = create_or_get_handover_preview(
            db,
            actor=actor,
            request=request,
        )

        assert repeated.item.id == first.item.id
        assert repeated.created is False
        assert repeated.item.status == "blocked"
        assert "source_record_inactive" in {
            issue["code"] for issue in repeated.item.validation_issues
        }
        ready_items, ready_total = list_handover_previews(
            db,
            actor=actor,
            status="ready",
        )
        assert ready_total == 0
        assert ready_items == []


def test_ready_preview_is_downgraded_when_verified_mapping_becomes_invalid():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        request = _mapped_request(db, record, actor)
        first = create_or_get_handover_preview(db, actor=actor, request=request)
        assert first.item.status == "ready"

        mapping = db.get(SmcodiHandoverTargetMapping, request.target_mapping_id)
        assert mapping is not None
        mapping.is_active = False
        db.flush()

        item = get_handover_preview(
            db,
            actor=actor,
            outbox_id=first.item.id,
        )
        assert item is not None
        assert item.status == "blocked"
        assert "unverified_target_mapping" in {
            issue["code"] for issue in item.validation_issues
        }
        _ready_items, ready_total = list_handover_previews(
            db,
            actor=actor,
            status="ready",
        )
        assert ready_total == 0


def test_same_source_version_target_and_payload_is_idempotent():
    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        request = _mapped_request(db, record, actor)
        first = create_or_get_handover_preview(db, actor=actor, request=request)
        repeated = create_or_get_handover_preview(db, actor=actor, request=request)

        assert first.created is True
        assert repeated.created is False
        assert repeated.item.id == first.item.id
        assert repeated.item.payload_hash == first.item.payload_hash
        assert repeated.item.idempotency_key == first.item.idempotency_key
        assert db.scalar(
            select(func.count(SmcodiHandoverOutbox.id)).where(
                SmcodiHandoverOutbox.source_record_id == record.id
            )
        ) == 1


def test_selected_version_must_exist_and_current_version_must_be_consistent():
    with SessionLocal() as db:
        record, version, actor = _seed_source(db)
        second_snapshot = {
            **version.snapshot,
            "observation_text": "점심 식사량도 절반이었습니다.",
        }
        second_hash = _payload_hash(second_snapshot)
        second_version = ConfirmedWorkRecordVersion(
            organization_id=record.organization_id,
            record_id=record.id,
            version=2,
            content_hash=second_hash,
            snapshot=second_snapshot,
            change_reason="점심 경과 추가",
            created_by_user_id=actor.id,
        )
        db.add(second_version)
        record.current_version = 2
        record.current_content_hash = second_hash
        record.observation_text = second_snapshot["observation_text"]
        db.flush()

        old_preview = create_or_get_handover_preview(
            db,
            actor=actor,
            request=_mapped_request(db, record, actor, version=version.version),
        )
        assert old_preview.item.source_version == 1
        assert old_preview.item.source_version_id == version.id
        assert old_preview.item.payload["observation_text"] == (
            "아침 식사량이 절반이었습니다."
        )

        with pytest.raises(SmcodiOutboxNotFoundError):
            create_or_get_handover_preview(
                db,
                actor=actor,
                request=_mapped_request(db, record, actor, version=99),
            )

        record.current_content_hash = "f" * 64
        db.flush()
        with pytest.raises(SmcodiOutboxSourceError):
            create_or_get_handover_preview(
                db,
                actor=actor,
                request=_mapped_request(db, record, actor, version=version.version),
            )


def test_organization_and_test_data_are_isolated():
    with SessionLocal() as db:
        organization = _new_organization(db)
        test_record, _test_version, test_actor = _seed_source(
            db,
            organization=organization,
            is_test_data=True,
        )
        real_record, _real_version, real_actor = _seed_source(
            db,
            organization=organization,
            is_test_data=False,
        )
        test_preview = create_or_get_handover_preview(
            db,
            actor=test_actor,
            request=_mapped_request(db, test_record, test_actor),
        )
        real_preview = create_or_get_handover_preview(
            db,
            actor=real_actor,
            request=_mapped_request(db, real_record, real_actor),
        )

        test_items, test_total = list_handover_previews(db, actor=test_actor)
        real_items, real_total = list_handover_previews(db, actor=real_actor)
        assert test_total == 1
        assert [item.id for item in test_items] == [test_preview.item.id]
        assert real_total == 1
        assert [item.id for item in real_items] == [real_preview.item.id]
        assert get_handover_preview(
            db,
            actor=test_actor,
            outbox_id=real_preview.item.id,
        ) is None
        with pytest.raises(SmcodiOutboxNotFoundError):
            create_or_get_handover_preview(
                db,
                actor=test_actor,
                request=_mapped_request(db, real_record, real_actor),
            )

        _foreign_record, _foreign_version, foreign_actor = _seed_source(db)
        with pytest.raises(SmcodiOutboxNotFoundError):
            create_or_get_handover_preview(
                db,
                actor=foreign_actor,
                request=_mapped_request(db, test_record, test_actor),
            )


def test_only_admin_or_record_processor_can_use_outbox():
    admin = SimpleNamespace(role="admin", can_process_records=False)
    processor = SimpleNamespace(role="staff", can_process_records=True)
    ordinary_staff = SimpleNamespace(role="staff", can_process_records=False)
    reviewer = SimpleNamespace(
        role="admin",
        can_process_records=True,
        _reviewer_experience="care",
    )

    assert _require_outbox_user(admin) is admin
    assert _require_outbox_user(processor) is processor
    with pytest.raises(HTTPException) as exc_info:
        _require_outbox_user(ordinary_staff)
    assert exc_info.value.status_code == 403
    with pytest.raises(HTTPException) as reviewer_error:
        _require_outbox_user(reviewer)
    assert reviewer_error.value.status_code == 403


def test_router_is_preview_and_read_only_without_external_send_path():
    routes = {(route.path, frozenset(route.methods or [])) for route in router.routes}
    assert routes == {
        ("/api/smcodi-outbox/preview", frozenset({"POST"})),
        ("/api/smcodi-outbox", frozenset({"GET"})),
        ("/api/smcodi-outbox/{outbox_id}", frozenset({"GET"})),
    }
    assert all("send" not in path and "dispatch" not in path for path, _ in routes)

    import app.smcodi_outbox as module

    source = inspect.getsource(module)
    assert "import httpx" not in source
    assert "import requests" not in source
    assert "urllib.request" not in source


def test_main_registers_outbox_routes_and_success_responses_are_not_cached():
    from app.main import app

    app_paths = set(app.openapi()["paths"])
    assert {
        "/api/smcodi-outbox/preview",
        "/api/smcodi-outbox",
        "/api/smcodi-outbox/{outbox_id}",
    }.issubset(app_paths)

    with SessionLocal() as db:
        record, _version, actor = _seed_source(db)
        request = _mapped_request(db, record, actor)
        preview_response = Response()
        preview = preview_smcodi_handover(
            payload=request,
            response=preview_response,
            actor=actor,
            db=db,
        )
        assert preview_response.headers["Cache-Control"] == "private, no-store"

        list_response = Response()
        listed = list_smcodi_handover_previews(
            response=list_response,
            status=None,
            source_record_id=None,
            limit=100,
            offset=0,
            actor=actor,
            db=db,
        )
        assert listed.total == 1
        assert list_response.headers["Cache-Control"] == "private, no-store"

        detail_response = Response()
        detail = get_smcodi_handover_preview(
            outbox_id=preview.item.id,
            response=detail_response,
            actor=actor,
            db=db,
        )
        assert detail.id == preview.item.id
        assert detail_response.headers["Cache-Control"] == "private, no-store"


def test_migration_follows_ai_assist_and_contains_no_delivery_columns():
    migration_path = (
        Path(__file__).resolve().parents[1]
        / "migrations"
        / "postgres_versions"
        / "029_smcodi_handover_outbox.py"
    )
    source = migration_path.read_text(encoding="utf-8")
    assert 'down_revision: str | None = "028_ai_assist"' in source
    assert "smcodi_handover_target_mappings" in source
    assert "refusing destructive downgrade" in source
    assert "sent_at" not in source
    assert "external_response" not in source
    assert "delivery_status" not in source
