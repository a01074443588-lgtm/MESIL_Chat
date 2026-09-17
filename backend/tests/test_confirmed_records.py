from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select

from app.confirmed_record_schemas import ConfirmedRecordSearchFilters
from app.confirmed_records import (
    ConfirmedRecordError,
    ConfirmedRecordHistoryMutationError,
    ConfirmedWorkRecord,
    ConfirmedWorkRecordSource,
    ConfirmedWorkRecordVersion,
    persist_confirmed_records_from_work_item,
    search_confirmed_records,
)
from app.database import Base, SessionLocal, engine
from app.models import (
    Message,
    MessageResidentLink,
    Organization,
    Resident,
    Room,
    User,
    WorkItem,
)


@pytest.fixture(scope="module", autouse=True)
def _create_confirmed_record_tables():
    # main.py 연결 전에도 신규 모듈 자체를 독립적으로 시험할 수 있게 한다.
    Base.metadata.create_all(bind=engine)


@pytest.fixture(autouse=True)
def _remove_confirmed_record_test_organizations():
    """다른 통합시험이 첫 기관을 선택해도 이 시험자료가 섞이지 않게 정리한다."""

    yield
    with SessionLocal() as db:
        organization_ids = list(
            db.scalars(
                select(Organization.id).where(
                    Organization.internal_code.like("confirmed-record-org-%")
                )
            ).all()
        )
        if not organization_ids:
            return
        record_ids = list(
            db.scalars(
                select(ConfirmedWorkRecord.id).where(
                    ConfirmedWorkRecord.organization_id.in_(organization_ids)
                )
            ).all()
        )
        version_ids = list(
            db.scalars(
                select(ConfirmedWorkRecordVersion.id).where(
                    ConfirmedWorkRecordVersion.organization_id.in_(organization_ids)
                )
            ).all()
        )
        if version_ids:
            db.execute(
                delete(ConfirmedWorkRecordSource).where(
                    ConfirmedWorkRecordSource.record_version_id.in_(version_ids)
                )
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
            delete(MessageResidentLink).where(
                MessageResidentLink.organization_id.in_(organization_ids)
            )
        )
        db.execute(
            delete(WorkItem).where(WorkItem.organization_id.in_(organization_ids))
        )
        db.execute(delete(Message).where(Message.organization_id.in_(organization_ids)))
        db.execute(delete(Resident).where(Resident.organization_id.in_(organization_ids)))
        db.execute(delete(Room).where(Room.organization_id.in_(organization_ids)))
        db.execute(delete(User).where(User.organization_id.in_(organization_ids)))
        db.execute(delete(Organization).where(Organization.id.in_(organization_ids)))
        db.commit()


def _seed_work_item(
    db,
    *,
    resident_count: int = 1,
    lifecycle_status: str = "active",
    occurred_at: datetime | None = None,
) -> tuple[WorkItem, User, Message, list[Resident]]:
    suffix = uuid4().hex[:12]
    organization = Organization(
        internal_code=f"confirmed-record-org-{suffix}",
        name=f"확정기록 시험기관 {suffix}",
        service_type="facility_care",
    )
    db.add(organization)
    db.flush()

    user = User(
        organization_id=organization.id,
        username=f"confirmed-{suffix}",
        display_name="확정기록 시험자",
        password_hash="not-used-in-service-test",
        can_process_records=True,
    )
    db.add(user)
    db.flush()
    room = Room(
        organization_id=organization.id,
        kind="custom",
        name=f"확정기록 시험방 {suffix}",
        resident_scope="all",
        created_by_id=user.id,
        is_test_data=True,
    )
    db.add(room)
    db.flush()

    residents: list[Resident] = []
    for index in range(resident_count):
        resident = Resident(
            organization_id=organization.id,
            internal_code=f"CARE-{suffix}-{index + 1}",
            display_name=f"확정기록 어르신 {suffix}-{index + 1}",
            service_type="facility",
            status="active",
            is_active=True,
            is_test_data=True,
        )
        db.add(resident)
        residents.append(resident)
    db.flush()

    message = Message(
        organization_id=organization.id,
        room_id=room.id,
        sender_id=user.id,
        message_type="chat",
        body="아침 식사량이 절반으로 확인되어 물을 권유했습니다.",
        resident_id=residents[0].id,
        lifecycle_status=lifecycle_status,
        is_test_data=True,
        created_at=occurred_at or datetime(2026, 8, 4, 0, 30, tzinfo=timezone.utc),
    )
    db.add(message)
    db.flush()
    for resident in residents:
        db.add(
            MessageResidentLink(
                organization_id=organization.id,
                message_id=message.id,
                resident_id=resident.id,
                source="manual",
                status="confirmed",
                reviewed_by_id=user.id,
                reviewed_at=datetime.now(timezone.utc),
            )
        )
    work_item = WorkItem(
        organization_id=organization.id,
        source_message_id=message.id,
        resident_id=residents[0].id,
        status="ready",
        source_snapshot={"message_id": str(message.id)},
        document_types=[],
        confirmed_by_id=user.id,
        confirmed_at=datetime.now(timezone.utc),
        is_test_data=True,
    )
    db.add(work_item)
    db.flush()
    return work_item, user, message, residents


def _confirmed_payload(**overrides) -> dict:
    payload = {
        "classification": "nutrition",
        "risk_level": "medium",
        "observation_details": "아침 식사량이 평소보다 적었습니다.",
        "actions_taken": ["물을 권유했습니다.", "간호팀에 공유했습니다."],
        "resident_response": "물을 한 컵 드셨습니다.",
        "measurement_text": "식사량 1/2, 물 200ml",
        "verification_questions": [],
    }
    payload.update(overrides)
    return payload


def test_work_item_confirmation_creates_one_record_per_resident():
    with SessionLocal() as db:
        work_item, user, _message, residents = _seed_work_item(
            db,
            resident_count=2,
        )
        results = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(),
            confirmed_by_user_id=user.id,
        )
        db.commit()

        assert len(results) == 2
        assert {result.record.resident_id for result in results} == {
            resident.id for resident in residents
        }
        assert all(result.record.service_context == "facility_care" for result in results)
        assert all(result.record.current_version == 1 for result in results)
        assert all(result.created and result.appended for result in results)
        assert db.scalar(
            select(func.count(ConfirmedWorkRecordVersion.id)).where(
                ConfirmedWorkRecordVersion.record_id.in_(
                    [result.record.id for result in results]
                )
            )
        ) == 2
        assert db.scalar(
            select(func.count(ConfirmedWorkRecordSource.id)).where(
                ConfirmedWorkRecordSource.record_version_id.in_(
                    [result.version.id for result in results]
                )
            )
        ) == 2


def test_reconfirmation_appends_version_and_same_payload_is_idempotent():
    with SessionLocal() as db:
        work_item, user, _message, _residents = _seed_work_item(db)
        first = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(),
            confirmed_by_user_id=user.id,
        )[0]
        second = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(
                observation_details="점심에도 식사량이 절반으로 확인되었습니다."
            ),
            confirmed_by_user_id=user.id,
            change_reason="직원 재확인",
        )[0]
        repeated = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(
                observation_details="점심에도 식사량이 절반으로 확인되었습니다."
            ),
            confirmed_by_user_id=user.id,
            change_reason="같은 요청 재시도",
        )[0]
        db.commit()

        assert first.record.id == second.record.id == repeated.record.id
        assert first.version.version == 1
        assert second.version.version == 2
        assert repeated.version.id == second.version.id
        assert repeated.appended is False
        versions = db.scalars(
            select(ConfirmedWorkRecordVersion)
            .where(ConfirmedWorkRecordVersion.record_id == first.record.id)
            .order_by(ConfirmedWorkRecordVersion.version)
        ).all()
        assert len(versions) == 2
        assert versions[0].content_hash != versions[1].content_hash
        assert versions[0].snapshot["observation_text"] == (
            "아침 식사량이 평소보다 적었습니다."
        )
        assert versions[1].snapshot["observation_text"] == (
            "점심에도 식사량이 절반으로 확인되었습니다."
        )


def test_recalled_message_cannot_be_new_record_evidence():
    with SessionLocal() as db:
        work_item, user, message, _residents = _seed_work_item(
            db,
            lifecycle_status="recalled",
        )
        with pytest.raises(ConfirmedRecordError) as exc_info:
            persist_confirmed_records_from_work_item(
                db,
                work_item=work_item,
                confirmed_payload=_confirmed_payload(),
                confirmed_by_user_id=user.id,
            )
        assert exc_info.value.code == "recalled_source_message"
        assert "회수한 메시지" in str(exc_info.value)
        assert db.scalar(
            select(func.count(ConfirmedWorkRecord.id)).where(
                ConfirmedWorkRecord.work_item_id == work_item.id
            )
        ) == 0
        assert message.lifecycle_status == "recalled"


def test_database_search_filters_by_actual_time_resident_and_text():
    with SessionLocal() as db:
        actual_time = datetime(2026, 8, 2, 4, 15, tzinfo=timezone.utc)
        work_item, user, _message, residents = _seed_work_item(
            db,
            resident_count=2,
            occurred_at=actual_time + timedelta(days=1),
        )
        results = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(),
            confirmed_by_user_id=user.id,
            occurred_at=actual_time,
        )
        db.commit()

        page = search_confirmed_records(
            db,
            organization_id=work_item.organization_id,
            filters=ConfirmedRecordSearchFilters(
                q=residents[1].display_name,
                resident_id=residents[1].id,
                service_context="facility_care",
                work_category="nutrition",
                occurred_from=actual_time - timedelta(minutes=1),
                occurred_to=actual_time + timedelta(minutes=1),
            ),
        )
        assert page.total == 1
        assert len(page.items) == 1
        result_by_resident = {
            result.record.resident_id: result.record.id for result in results
        }
        assert page.items[0].record.id == result_by_resident[residents[1].id]
        assert page.items[0].record.occurred_at == actual_time

        message_time_page = search_confirmed_records(
            db,
            organization_id=work_item.organization_id,
            filters=ConfirmedRecordSearchFilters(
                occurred_from=actual_time + timedelta(hours=12),
                occurred_to=actual_time + timedelta(days=2),
            ),
        )
        assert all(
            item.record.id not in {result.record.id for result in results}
            for item in message_time_page.items
        )


def test_version_objects_reject_in_place_mutation():
    with SessionLocal() as db:
        work_item, user, _message, _residents = _seed_work_item(db)
        result = persist_confirmed_records_from_work_item(
            db,
            work_item=work_item,
            confirmed_payload=_confirmed_payload(),
            confirmed_by_user_id=user.id,
        )[0]
        db.commit()

        version = db.get(ConfirmedWorkRecordVersion, result.version.id)
        assert version is not None
        version.snapshot = {**version.snapshot, "observation_text": "덮어쓰기 시도"}
        with pytest.raises(ConfirmedRecordHistoryMutationError):
            db.flush()
        db.rollback()


def test_service_context_must_match_resident_assignment():
    with SessionLocal() as db:
        work_item, user, _message, _residents = _seed_work_item(db)
        with pytest.raises(ConfirmedRecordError) as exc_info:
            persist_confirmed_records_from_work_item(
                db,
                work_item=work_item,
                confirmed_payload=_confirmed_payload(),
                confirmed_by_user_id=user.id,
                service_context="day_care",
            )
        assert exc_info.value.code == "service_context_mismatch"


def test_router_contract_exposes_read_only_search_and_detail():
    from app.confirmed_records import router

    routes = {(route.path, frozenset(route.methods or [])) for route in router.routes}
    assert ("/api/confirmed-records", frozenset({"GET"})) in routes
    assert ("/api/confirmed-records/{record_id}", frozenset({"GET"})) in routes
