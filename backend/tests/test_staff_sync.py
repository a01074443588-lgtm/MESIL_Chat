import json
from datetime import date
from hashlib import sha256
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    OrgUnit,
    Organization,
    RoomMembership,
    Staff,
    StaffJobAssignment,
    StaffOrganizationAssignment,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    StaffSyncBatch,
    StaffSyncItem,
    StaffSyncReviewDraft,
    User,
)
from app.staff_sync import build_staff_application_plan, normalize_staff_review_draft


ORIGIN = {"origin": "http://testserver"}


APPLICATION_PLAN_PROTECTED_MODELS = (
    Staff,
    User,
    StaffSourceAccount,
    StaffServicePeriod,
    StaffServiceAssignment,
    StaffOrganizationAssignment,
    StaffJobAssignment,
    RoomMembership,
    AuditEvent,
)


def application_plan_protected_state() -> dict[str, dict[str, object]]:
    """Return count and full-row digest without exposing protected row values."""

    with SessionLocal() as db:
        result: dict[str, dict[str, object]] = {}
        for model in APPLICATION_PLAN_PROTECTED_MODELS:
            column_names = [attribute.key for attribute in model.__mapper__.column_attrs]
            serialized_rows = sorted(
                json.dumps(
                    {
                        column_name: getattr(row, column_name)
                        for column_name in column_names
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    default=str,
                    separators=(",", ":"),
                )
                for row in db.scalars(select(model)).all()
            )
            result[model.__tablename__] = {
                "count": len(serialized_rows),
                "digest": sha256("\n".join(serialized_rows).encode("utf-8")).hexdigest(),
            }
        return result


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def test_carefor_staff_sync_is_preview_only_and_keeps_multi_account_history(tmp_path):
    roster_path = tmp_path / "carefor_staff.local.json"
    roster_path.write_text(
        json.dumps(
            {
                "generated_at": "2026-08-01T09:00:00+09:00",
                "staff": [
                    {
                        "external_id": "CF-FAC-A",
                        "display_name": "가명 겸직 직원",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "birth_date": "1980-01-02",
                        "gender": "남",
                        "start_date": "2024-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DAY-A",
                        "display_name": "가명 겸직 직원",
                        "service_type": "daycare",
                        "status": "휴직",
                        "job_name": "요양보호사",
                        "start_date": "2025-01-01",
                        "is_active": False,
                    },
                    {
                        "external_id": "CF-RETIRE",
                        "display_name": "가명 퇴사 예정 직원",
                        "service_type": "facility",
                        "status": "퇴사",
                        "job_name": "사회복지사",
                        "start_date": "2023-01-01",
                        "end_date": "2026-08-01",
                        "is_active": False,
                    },
                    {
                        "external_id": "CF-NEW",
                        "display_name": "가명 신규 직원",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2026-08-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-NAME-CONFLICT",
                        "display_name": "가명 이름충돌 직원",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "간호조무사",
                        "start_date": "2026-07-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-RETIRED-CONFLICT",
                        "display_name": "가명 퇴사 충돌 직원",
                        "service_type": "daycare",
                        "status": "퇴사",
                        "job_name": "기타",
                        "start_date": "2023-01-01",
                        "end_date": "2024-01-01",
                        "is_active": False,
                    },
                    {
                        "external_id": "CF-TEST-SAME",
                        "display_name": "시험직원과 같은 이름",
                        "service_type": "daycare",
                        "status": "재직",
                        "job_name": "사회복지사",
                        "start_date": "2026-07-15",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DUPLICATE",
                        "display_name": "가명 중복 직원 1",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2026-06-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DUPLICATE",
                        "display_name": "가명 중복 직원 2",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2026-06-01",
                        "is_active": True,
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    original_path = settings.carefor_staff_roster_path
    settings.carefor_staff_roster_path = roster_path.as_posix()
    try:
        with TestClient(app) as client:
            login_admin(client)
            with SessionLocal() as db:
                organization = db.scalar(select(Organization).limit(1))
                multi_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-MULTI",
                    display_name="가명 겸직 직원",
                    job_title="요양보호사",
                )
                retiring_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-RETIRE",
                    display_name="가명 퇴사 예정 직원",
                    job_title="사회복지사",
                )
                same_name_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-NAME-CONFLICT",
                    display_name="가명 이름충돌 직원",
                    job_title="간호조무사",
                )
                retired_conflict_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-RETIRED-CONFLICT",
                    display_name="가명 퇴사 충돌 직원",
                    job_title="사무원",
                )
                same_name_test_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-TEST-SAME",
                    display_name="시험직원과 같은 이름",
                    job_title="사회복지사",
                    is_test_data=True,
                )
                missing_staff = Staff(
                    organization_id=organization.id,
                    internal_code="STAFF-SYNC-MISSING",
                    display_name="가명 명단누락 직원",
                    job_title="요양보호사",
                )
                db.add_all(
                    [
                        multi_staff,
                        retiring_staff,
                        same_name_staff,
                        retired_conflict_staff,
                        same_name_test_staff,
                        missing_staff,
                    ]
                )
                db.flush()
                accounts = [
                    StaffSourceAccount(
                        organization_id=organization.id,
                        staff_id=multi_staff.id,
                        service_type="facility",
                        external_id="CF-FAC-A",
                        display_name_snapshot=multi_staff.display_name,
                        job_name_snapshot="요양보호사",
                        employment_status="active",
                        source_status="재직",
                        latest_start_date=date(2024, 1, 1),
                    ),
                    StaffSourceAccount(
                        organization_id=organization.id,
                        staff_id=multi_staff.id,
                        service_type="daycare",
                        external_id="CF-DAY-A",
                        display_name_snapshot=multi_staff.display_name,
                        job_name_snapshot="요양보호사",
                        employment_status="active",
                        source_status="재직",
                        latest_start_date=date(2025, 1, 1),
                    ),
                    StaffSourceAccount(
                        organization_id=organization.id,
                        staff_id=retiring_staff.id,
                        service_type="facility",
                        external_id="CF-RETIRE",
                        display_name_snapshot=retiring_staff.display_name,
                        job_name_snapshot="사회복지사",
                        employment_status="active",
                        source_status="재직",
                        latest_start_date=date(2023, 1, 1),
                    ),
                    StaffSourceAccount(
                        organization_id=organization.id,
                        staff_id=missing_staff.id,
                        service_type="homecare",
                        external_id="CF-MISSING",
                        display_name_snapshot=missing_staff.display_name,
                        job_name_snapshot="요양보호사",
                        employment_status="active",
                        source_status="재직",
                        latest_start_date=date(2022, 1, 1),
                    ),
                ]
                db.add_all(accounts)
                db.flush()
                db.add_all(
                    [
                        StaffServicePeriod(
                            organization_id=organization.id,
                            staff_id=multi_staff.id,
                            source_account_id=accounts[0].id,
                            start_date=date(2024, 1, 1),
                        ),
                        StaffServicePeriod(
                            organization_id=organization.id,
                            staff_id=multi_staff.id,
                            source_account_id=accounts[1].id,
                            start_date=date(2025, 1, 1),
                        ),
                    ]
                )
                db.commit()
                before = {
                    "staff": db.scalar(select(func.count()).select_from(Staff)),
                    "users": db.scalar(select(func.count()).select_from(User)),
                    "accounts": db.scalar(select(func.count()).select_from(StaffSourceAccount)),
                    "periods": db.scalar(select(func.count()).select_from(StaffServicePeriod)),
                }

            status_response = client.get(
                "/api/admin/carefor-staff-sync/status",
                headers=ORIGIN,
            )
            assert status_response.status_code == 200, status_response.text
            assert status_response.json()["status"] == "ready"
            assert status_response.json()["row_count"] == 9
            assert status_response.json()["unique_name_count"] == 8

            preview_response = client.post(
                "/api/admin/carefor-staff-sync/preview",
                headers=ORIGIN,
            )
            assert preview_response.status_code == 201, preview_response.text
            preview = preview_response.json()
            assert preview["status"] == "preview"
            assert preview["summary"] == {
                "new": 2,
                "update": 0,
                "leave": 1,
                "retire": 1,
                "unchanged": 1,
                "conflict": 3,
                "total": 8,
                "actionable": 4,
                "missing_not_retired": 1,
            }
            by_key = {item["source_key"]: item for item in preview["items"]}
            assert by_key["daycare:CF-DAY-A"]["change_type"] == "leave"
            assert (
                by_key["daycare:CF-DAY-A"]["current_snapshot"]["other_active_source_count"]
                == 1
            )
            assert by_key["facility:CF-RETIRE"]["change_type"] == "retire"
            assert by_key["homecare:CF-NEW"]["change_type"] == "new"
            assert by_key["daycare:CF-TEST-SAME"]["change_type"] == "new"
            assert by_key["facility:CF-NAME-CONFLICT"]["status"] == "blocked"
            assert by_key["daycare:CF-RETIRED-CONFLICT"]["status"] == "blocked"
            assert by_key["facility:CF-DUPLICATE"]["status"] == "blocked"
            protected_payload = by_key["facility:CF-FAC-A"]["incoming_payload"]
            assert "birth_date" not in protected_payload
            assert "gender" not in protected_payload
            assert protected_payload["identity_evidence"] == "name_birth"
            assert len(protected_payload["identity_token"]) == 64

            assignment_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{preview['id']}/assignment-preview",
                headers=ORIGIN,
            )
            assert assignment_response.status_code == 200, assignment_response.text
            retired_conflict = next(
                candidate
                for candidate in assignment_response.json()["candidates"]
                if candidate["display_name"] == "가명 퇴사 충돌 직원"
            )
            assert retired_conflict["review_status"] == "blocked"
            assert retired_conflict["requires_assignment_confirmation"] is False
            assert retired_conflict["accounts"][0]["assignment_required"] is False
            assert retired_conflict["accounts"][0]["assignment_ready"] is False
            assert all(
                "조직·직종·직위 중 관리자가 정할 항목" not in note
                for note in retired_conflict["notes"]
            )

            with SessionLocal() as db:
                after = {
                    "staff": db.scalar(select(func.count()).select_from(Staff)),
                    "users": db.scalar(select(func.count()).select_from(User)),
                    "accounts": db.scalar(select(func.count()).select_from(StaffSourceAccount)),
                    "periods": db.scalar(select(func.count()).select_from(StaffServicePeriod)),
                }
                audit = db.scalar(
                    select(AuditEvent)
                    .where(AuditEvent.action == "staff.carefor_sync_preview_created")
                    .order_by(AuditEvent.created_at.desc())
                )
            assert after == before
            assert audit is not None
            assert audit.details["safety"] == "preview_only_missing_rows_not_retired"

            apply_response = client.post(
                f"/api/admin/carefor-staff-sync/batches/{preview['id']}/apply",
                json={"item_ids": []},
                headers=ORIGIN,
            )
            assert apply_response.status_code == 404
    finally:
        settings.carefor_staff_roster_path = original_path


def test_carefor_staff_identity_evidence_splits_names_and_saves_contract_doctor(
    tmp_path,
):
    roster_path = tmp_path / "carefor_staff.local.json"
    roster_path.write_text(
        json.dumps(
            {
                "staff": [
                    {
                        "external_id": "CF-SAME-FAC",
                        "display_name": "생년확인 겸직직원",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "birth_date": "1982-03-04",
                        "start_date": "2024-01-01",
                    },
                    {
                        "external_id": "CF-SAME-DAY",
                        "display_name": "생년확인 겸직직원",
                        "service_type": "daycare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "birth_date": "1982-03-04",
                        "start_date": "2024-01-01",
                    },
                    {
                        "external_id": "CF-SAME-HOME",
                        "display_name": "생년확인 겸직직원",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "birth_date": "1982-03-04",
                        "start_date": "2024-01-01",
                    },
                    {
                        "external_id": "CF-NAME-DOCTOR",
                        "display_name": "생년구분 동명이인",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "기타",
                        "birth_date": "1955-05-06",
                        "start_date": "2025-01-01",
                    },
                    {
                        "external_id": "CF-NAME-DAY-RETIRED",
                        "display_name": "생년구분 동명이인",
                        "service_type": "daycare",
                        "status": "퇴사",
                        "job_name": "요양보호사",
                        "birth_date": "1975-07-08",
                        "start_date": "2023-01-01",
                        "end_date": "2025-12-31",
                    },
                    {
                        "external_id": "CF-NAME-HOME",
                        "display_name": "생년구분 동명이인",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "birth_date": "1975-07-08",
                        "start_date": "2026-01-01",
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    original_path = settings.carefor_staff_roster_path
    settings.carefor_staff_roster_path = roster_path.as_posix()
    try:
        with TestClient(app) as client:
            login_admin(client)
            with SessionLocal() as db:
                organization = db.scalar(select(Organization).limit(1))
                required_units = (
                    ("business", "시설"),
                    ("business", "주간보호"),
                    ("business", "방문요양"),
                    ("department", "요양"),
                    ("department", "의료"),
                    ("team", "주간보호"),
                    ("team", "방문요양"),
                )
                for index, (unit_type, name) in enumerate(required_units, start=1):
                    if db.scalar(
                        select(OrgUnit).where(
                            OrgUnit.organization_id == organization.id,
                            OrgUnit.unit_type == unit_type,
                            OrgUnit.name == name,
                        )
                    ) is None:
                        db.add(
                            OrgUnit(
                                organization_id=organization.id,
                                unit_type=unit_type,
                                internal_code=f"IDENTITY-TEST-{index}",
                                name=name,
                                is_test_data=True,
                            )
                        )
                db.commit()

            batch_response = client.post(
                "/api/admin/carefor-staff-sync/preview",
                headers=ORIGIN,
            )
            assert batch_response.status_code == 201, batch_response.text
            batch = batch_response.json()
            assert all(
                "birth_date" not in item["incoming_payload"]
                for item in batch["items"]
            )
            assert batch["summary"]["retire"] == 1
            assert batch["summary"]["conflict"] == 0

            preview_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch['id']}/assignment-preview",
                headers=ORIGIN,
            )
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            assert preview["summary"]["candidate_count"] == 3

            concurrent = next(
                candidate
                for candidate in preview["candidates"]
                if candidate["display_name"] == "생년확인 겸직직원"
            )
            assert concurrent["account_count"] == 3
            assert concurrent["identity_status"] == "verified_identity"
            assert concurrent["identity_evidence"] == "name_birth"
            assert concurrent["requires_identity_confirmation"] is False
            assert concurrent["review_status"] == "proposal_ready"

            same_name_candidates = [
                candidate
                for candidate in preview["candidates"]
                if candidate["display_name"] == "생년구분 동명이인"
            ]
            assert len(same_name_candidates) == 2
            assert all(
                candidate["same_name_candidate_count"] == 2
                for candidate in same_name_candidates
            )
            caregiver = next(
                candidate
                for candidate in same_name_candidates
                if candidate["account_count"] == 2
            )
            doctor = next(
                candidate
                for candidate in same_name_candidates
                if candidate["account_count"] == 1
            )
            assert caregiver["identity_status"] == "verified_identity"
            assert caregiver["review_status"] == "proposal_ready"
            assert {account["employment_status"] for account in caregiver["accounts"]} == {
                "active",
                "retired",
            }
            assert doctor["review_status"] == "assignment_review"
            assert doctor["accounts"][0]["job"]["match_status"] == "manual"

            doctor_account = doctor["accounts"][0]
            save_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch['id']}/review-drafts/{doctor['candidate_key']}",
                json={
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [
                        {
                            "source_key": doctor_account["source_key"],
                            "job_code": "contract_doctor",
                        }
                    ],
                    "note": "동명이인과 별도인 계약의사로 확인",
                },
                headers=ORIGIN,
            )
            assert save_response.status_code == 200, save_response.text
            saved = save_response.json()
            assert saved["completion_status"] == "complete"
            assert saved["person_groups"][0]["source_keys"] == [
                doctor_account["source_key"]
            ]
            assert saved["account_assignments"][0]["job_code"] == "contract_doctor"
            assert saved["account_assignments"][0]["department_name"] == "의료"
    finally:
        settings.carefor_staff_roster_path = original_path


def test_carefor_staff_sync_requires_admin(tmp_path):
    roster_path = tmp_path / "carefor_staff.local.json"
    roster_path.write_text(
        json.dumps(
            {
                "staff": [
                    {
                        "external_id": "CF-ANON",
                        "display_name": "가명 익명 접근",
                        "service_type": "facility",
                        "status": "재직",
                        "is_active": True,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original_path = settings.carefor_staff_roster_path
    settings.carefor_staff_roster_path = roster_path.as_posix()
    try:
        with TestClient(app) as client:
            assert client.get("/api/admin/carefor-staff-sync/status").status_code == 401
            assert client.post("/api/admin/carefor-staff-sync/preview").status_code == 401
            assert (
                client.get(
                    f"/api/admin/carefor-staff-sync/batches/{uuid4()}/assignment-preview"
                ).status_code
                == 401
            )
            assert (
                client.get(
                    f"/api/admin/carefor-staff-sync/batches/{uuid4()}/application-plan"
                ).status_code
                == 401
            )
            assert (
                client.put(
                    f"/api/admin/carefor-staff-sync/batches/{uuid4()}/review-drafts/name-test",
                    json={
                        "identity_decision": "pending",
                        "person_groups": [],
                        "account_assignments": [],
                        "note": "",
                    },
                ).status_code
                == 401
            )
    finally:
        settings.carefor_staff_roster_path = original_path


def test_carefor_staff_assignment_preview_groups_people_and_never_writes(
    tmp_path,
    monkeypatch,
):
    roster_path = tmp_path / "carefor_staff.local.json"
    roster_path.write_text(
        json.dumps(
            {
                "staff": [
                    {
                        "external_id": "CF-PERSON-FAC",
                        "display_name": "배정검토 겸직직원",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-PERSON-DAY",
                        "display_name": "배정검토 겸직직원",
                        "service_type": "daycare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-PERSON-HOME",
                        "display_name": "배정검토 겸직직원",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "요양보호사",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-READY",
                        "display_name": "배정검토 기본직원",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "사회복지사",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DEPARTMENT",
                        "display_name": "배정검토 부서직원",
                        "service_type": "daycare",
                        "status": "재직",
                        "job_name": "보조원(운전사)",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DIRECTOR",
                        "display_name": "배정검토 시설장",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "시설장",
                        "birth_date": "1970-02-03",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DIRECTOR-DAY",
                        "display_name": "배정검토 시설장",
                        "service_type": "daycare",
                        "status": "재직",
                        "job_name": "시설장(관리책임자)",
                        "birth_date": "1970-02-03",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-DIRECTOR-HOME",
                        "display_name": "배정검토 시설장",
                        "service_type": "homecare",
                        "status": "재직",
                        "job_name": "시설장(관리책임자)",
                        "birth_date": "1970-02-03",
                        "start_date": "2025-01-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-HISTORY-FACILITY",
                        "display_name": "과거이력 휴직직원",
                        "service_type": "facility",
                        "status": "휴직",
                        "job_name": "사회복지사",
                        "birth_date": "1990-04-05",
                        "start_date": "2024-05-01",
                        "is_active": True,
                    },
                    {
                        "external_id": "CF-HISTORY-DAYCARE",
                        "display_name": "과거이력 휴직직원",
                        "service_type": "daycare",
                        "status": "퇴사",
                        "job_name": "사무원",
                        "birth_date": "1990-04-05",
                        "start_date": "2024-05-01",
                        "end_date": "2024-06-01",
                        "is_active": False,
                    },
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    original_path = settings.carefor_staff_roster_path
    settings.carefor_staff_roster_path = roster_path.as_posix()
    try:
        with TestClient(app) as client:
            login_admin(client)
            with SessionLocal() as db:
                organization = db.scalar(select(Organization).limit(1))
                required_units = (
                    ("business", "시설"),
                    ("business", "주간보호"),
                    ("business", "방문요양"),
                    ("department", "복지"),
                    ("department", "요양"),
                    ("team", "주간보호"),
                    ("team", "방문요양"),
                )
                for index, (unit_type, name) in enumerate(required_units, start=1):
                    existing = db.scalar(
                        select(OrgUnit).where(
                            OrgUnit.organization_id == organization.id,
                            OrgUnit.unit_type == unit_type,
                            OrgUnit.name == name,
                        )
                    )
                    if existing is None:
                        db.add(
                            OrgUnit(
                                organization_id=organization.id,
                                unit_type=unit_type,
                                internal_code=f"ASSIGNMENT-TEST-{index}",
                                name=name,
                                is_test_data=True,
                            )
                        )
                db.commit()
            batch_response = client.post(
                "/api/admin/carefor-staff-sync/preview",
                headers=ORIGIN,
            )
            assert batch_response.status_code == 201, batch_response.text
            batch_id = batch_response.json()["id"]

            tracked_models = (
                Staff,
                User,
                StaffSourceAccount,
                StaffServicePeriod,
                StaffSyncBatch,
                StaffSyncItem,
                StaffOrganizationAssignment,
                StaffJobAssignment,
            )
            with SessionLocal() as db:
                before = {
                    model.__tablename__: db.scalar(select(func.count()).select_from(model))
                    for model in tracked_models
                }

            response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/assignment-preview",
                headers=ORIGIN,
            )
            assert response.status_code == 200, response.text
            preview = response.json()
            assert preview["summary"] == {
                "account_count": 10,
                "candidate_count": 5,
                "proposal_ready": 3,
                "identity_review": 1,
                "assignment_review": 1,
                "blocked": 0,
                "multi_account_groups": 3,
                "assignment_confirmation_candidates": 1,
                "director_accounts": 3,
                "history_only_accounts": 1,
                "review_required_total": 2,
                "decision_saved": 0,
                "decision_complete": 0,
                "decision_draft": 0,
            }
            by_name = {
                candidate["display_name"]: candidate
                for candidate in preview["candidates"]
            }

            multi = by_name["배정검토 겸직직원"]
            assert multi["account_count"] == 3
            assert multi["identity_status"] == "confirmation_required"
            assert multi["review_status"] == "identity_review"
            assert multi["requires_identity_confirmation"] is True
            assert multi["linked_staff_ids"] == []
            multi_departments = {
                account["service_type"]: next(
                    proposal["unit_id"]
                    for proposal in account["organizations"]
                    if proposal["unit_type"] == "department"
                )
                for account in multi["accounts"]
            }
            assert all(multi_departments.values())
            assert len(set(multi_departments.values())) == 3
            assert all(
                next(
                    proposal["match_status"]
                    for proposal in account["organizations"]
                    if proposal["unit_type"] == "team"
                )
                == "not_proposed"
                for account in multi["accounts"]
            )

            ready = by_name["배정검토 기본직원"]
            assert ready["review_status"] == "proposal_ready"
            ready_account = ready["accounts"][0]
            assert ready_account["job"]["code"] == "social_worker"
            assert ready_account["assignment_ready"] is True
            org_by_type = {
                item["unit_type"]: item for item in ready_account["organizations"]
            }
            assert org_by_type["business"]["proposed_name"] == "방문요양"
            assert org_by_type["department"]["proposed_name"] == "복지"
            assert org_by_type["team"]["proposed_name"] is None
            assert org_by_type["team"]["match_status"] == "not_proposed"
            assert org_by_type["floor"]["match_status"] == "not_proposed"
            assert org_by_type["business"]["match_status"] == "matched"
            assert org_by_type["business"]["unit_id"] is not None

            department_review = by_name["배정검토 부서직원"]
            assert department_review["review_status"] == "proposal_ready"
            assert department_review["accounts"][0]["job"]["code"] == "driver_assistant"
            assert department_review["accounts"][0]["assignment_ready"] is True
            department_by_type = {
                item["unit_type"]: item
                for item in department_review["accounts"][0]["organizations"]
            }
            assert department_by_type["department"]["match_status"] == "not_proposed"

            history = by_name["과거이력 휴직직원"]
            assert history["review_status"] == "proposal_ready"
            assert history["requires_assignment_confirmation"] is False
            assert history["identity_status"] == "verified_identity"
            assert history["account_count"] == 2
            history_by_status = {
                account["employment_status"]: account
                for account in history["accounts"]
            }
            assert history_by_status["leave"]["assignment_required"] is True
            retired_history = history_by_status["retired"]
            assert retired_history["assignment_required"] is False
            assert retired_history["assignment_ready"] is True
            assert any("이력만 보존" in note for note in retired_history["notes"])
            retired_department = next(
                item
                for item in retired_history["organizations"]
                if item["unit_type"] == "department"
            )
            assert retired_department["match_status"] == "manual"

            with SessionLocal() as db:
                organization_id = db.scalar(select(Organization.id).limit(1))
                assert organization_id is not None
                normalized_history, history_issues = normalize_staff_review_draft(
                    db,
                    organization_id,
                    history,
                    {
                        "identity_decision": "not_required",
                        "person_groups": [],
                        "account_assignments": [
                            {
                                "source_key": retired_history["source_key"],
                                "department_name": "과거 임시 부서",
                                "job_code": "과거 임시 직종",
                                "position_title": "과거 임시 직위",
                            }
                        ],
                        "note": "퇴사 이력은 현재 배정에서 제외",
                    },
                )
            assert history_issues == []
            assert normalized_history["person_groups"][0]["source_keys"] == [
                account["source_key"] for account in history["accounts"]
            ]
            normalized_retired = next(
                assignment
                for assignment in normalized_history["account_assignments"]
                if assignment["source_key"] == retired_history["source_key"]
            )
            assert normalized_retired == {
                "source_key": retired_history["source_key"],
                "department_name": None,
                "job_code": None,
                "position_title": None,
            }

            director = by_name["배정검토 시설장"]
            assert director["review_status"] == "assignment_review"
            assert director["requires_identity_confirmation"] is False
            assert director["identity_status"] == "verified_identity"
            assert director["account_count"] == 3
            assert all(
                account["job"]["match_status"] == "matched"
                and account["job"]["code"] == "facility_director"
                and account["job"]["proposed_name"] == "시설장"
                and account["job"]["required_for_review"] is False
                for account in director["accounts"]
            )
            assert all(
                account["position"]["proposed_name"] is None
                and account["position"]["match_status"] == "not_proposed"
                and account["position"]["manual_confirmation"] is False
                for account in director["accounts"]
            )

            multi_source_keys = [account["source_key"] for account in multi["accounts"]]
            pending_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{multi['candidate_key']}",
                json={
                    "identity_decision": "pending",
                    "person_groups": [],
                    "account_assignments": [],
                    "note": "동일인 확인 전 임시 메모",
                },
                headers=ORIGIN,
            )
            assert pending_response.status_code == 200, pending_response.text
            pending_draft = pending_response.json()
            assert pending_draft["completion_status"] == "draft"
            assert pending_draft["revision"] == 1
            assert "동일인 여부" in pending_draft["completion_issues"][0]

            incomplete_plan_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-plan",
                headers=ORIGIN,
            )
            assert incomplete_plan_response.status_code == 200, incomplete_plan_response.text
            incomplete_plan = incomplete_plan_response.json()
            assert incomplete_plan["read_only"] is True
            assert incomplete_plan["apply_locked"] is True
            assert incomplete_plan["can_apply"] is False
            assert incomplete_plan["summary"]["incomplete_review_count"] == 2
            incomplete_by_name = {
                item["display_name"]: item for item in incomplete_plan["candidates"]
            }
            assert incomplete_by_name["배정검토 겸직직원"]["outcome"] == "blocked"
            assert (
                "review_decision_incomplete"
                in incomplete_by_name["배정검토 겸직직원"]["blocker_codes"]
            )
            assert incomplete_by_name["배정검토 시설장"]["outcome"] == "blocked"
            assert (
                "review_decision_missing"
                in incomplete_by_name["배정검토 시설장"]["blocker_codes"]
            )

            complete_multi_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{multi['candidate_key']}",
                json={
                    "expected_revision": 1,
                    "identity_decision": "same_person",
                    "person_groups": [
                        {
                            "source_keys": multi_source_keys,
                        }
                    ],
                    "account_assignments": [],
                    "note": "세 서비스 동일인 검토",
                },
                headers=ORIGIN,
            )
            assert complete_multi_response.status_code == 200, complete_multi_response.text
            complete_multi = complete_multi_response.json()
            assert complete_multi["completion_status"] == "complete"
            assert complete_multi["completion_issues"] == []
            assert complete_multi["revision"] == 2

            custom_multi_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{multi['candidate_key']}",
                json={
                    "expected_revision": 2,
                    "identity_decision": "custom_groups",
                    "person_groups": [
                        {
                            "source_keys": multi_source_keys[:2],
                        },
                        {
                            "source_keys": [multi_source_keys[2]],
                        },
                    ],
                    "account_assignments": [],
                    "note": "두 계정만 동일인인 경우도 보존",
                },
                headers=ORIGIN,
            )
            assert custom_multi_response.status_code == 200, custom_multi_response.text
            assert custom_multi_response.json()["completion_status"] == "complete"
            assert custom_multi_response.json()["revision"] == 3

            stale_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{multi['candidate_key']}",
                json={
                    "expected_revision": 2,
                    "identity_decision": "separate_people",
                    "person_groups": [
                        {
                            "source_keys": [source_key],
                        }
                        for source_key in multi_source_keys
                    ],
                    "account_assignments": [],
                    "note": "오래된 화면",
                },
                headers=ORIGIN,
            )
            assert stale_response.status_code == 409

            director_source_keys = [
                account["source_key"] for account in director["accounts"]
            ]
            original_flush = Session.flush

            def raise_first_review_draft_conflict(session, *args, **kwargs):
                if any(
                    isinstance(item, StaffSyncReviewDraft) for item in session.new
                ):
                    raise IntegrityError(
                        "simulated duplicate review draft",
                        {},
                        Exception("duplicate"),
                    )
                return original_flush(session, *args, **kwargs)

            monkeypatch.setattr(Session, "flush", raise_first_review_draft_conflict)
            conflict_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{director['candidate_key']}",
                json={
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [],
                    "note": "동시 저장 충돌 모의",
                },
                headers=ORIGIN,
            )
            assert conflict_response.status_code == 409
            monkeypatch.setattr(Session, "flush", original_flush)

            driver_rejection = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{department_review['candidate_key']}",
                json={
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [],
                    "note": "",
                },
                headers=ORIGIN,
            )
            assert driver_rejection.status_code == 422

            director_incomplete_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{director['candidate_key']}",
                json={
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [],
                    "note": "계정별 소속 부서 확인 전",
                },
                headers=ORIGIN,
            )
            assert director_incomplete_response.status_code == 200
            assert director_incomplete_response.json()["completion_status"] == "draft"
            director_issues = director_incomplete_response.json()["completion_issues"]
            assert len(director_issues) == 3
            assert all("부서를 선택" in issue for issue in director_issues)
            assert all("실제 자격 직종" not in issue for issue in director_issues)
            assert all("직위를 선택" not in issue for issue in director_issues)
            director_response = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{director['candidate_key']}",
                json={
                    "expected_revision": 1,
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [
                        {
                            "source_key": director_source_keys[0],
                            "department_name": "복지",
                        },
                        {
                            "source_key": director_source_keys[1],
                            "department_name": "복지",
                        },
                        {
                            "source_key": director_source_keys[2],
                            "department_name": "복지",
                        },
                    ],
                    "note": "시설·주간보호·방문요양의 서비스별 소속 부서 확인",
                },
                headers=ORIGIN,
            )
            assert director_response.status_code == 200, director_response.text
            director_draft = director_response.json()
            assert director_draft["completion_status"] == "complete"
            assert director_draft["person_groups"][0]["source_keys"] == director_source_keys
            assert director_draft["person_groups"][0]["primary_job_code"] is None
            assert director_draft["person_groups"][0]["primary_position_title"] is None
            assert all(
                assignment["job_code"] is None
                and assignment["position_title"] is None
                and assignment["department_name"] == "복지"
                for assignment in director_draft["account_assignments"]
            )

            ready_rejection = client.put(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{ready['candidate_key']}",
                json={
                    "identity_decision": "not_required",
                    "person_groups": [],
                    "account_assignments": [],
                    "note": "",
                },
                headers=ORIGIN,
            )
            assert ready_rejection.status_code == 422

            refreshed_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/assignment-preview",
                headers=ORIGIN,
            )
            assert refreshed_response.status_code == 200, refreshed_response.text
            refreshed = refreshed_response.json()
            assert refreshed["summary"]["review_required_total"] == 2
            assert refreshed["summary"]["decision_saved"] == 2
            assert refreshed["summary"]["decision_complete"] == 2
            assert refreshed["summary"]["decision_draft"] == 0
            refreshed_by_name = {
                item["display_name"]: item for item in refreshed["candidates"]
            }
            assert refreshed_by_name["배정검토 겸직직원"]["review_draft"][
                "identity_decision"
            ] == "custom_groups"

            protected_before_plan = application_plan_protected_state()
            first_plan_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-plan",
                headers=ORIGIN,
            )
            assert first_plan_response.status_code == 200, first_plan_response.text
            first_plan = first_plan_response.json()

            locked_apply_response = client.post(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/apply",
                json={"item_ids": []},
                headers=ORIGIN,
            )
            assert locked_apply_response.status_code == 404

            second_plan_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-plan",
                headers=ORIGIN,
            )
            assert second_plan_response.status_code == 200, second_plan_response.text
            second_plan = second_plan_response.json()
            protected_after_plan = application_plan_protected_state()

            assert first_plan["read_only"] is True
            assert first_plan["apply_locked"] is True
            assert first_plan["can_apply"] is False
            assert first_plan["plan_fingerprint"] == second_plan["plan_fingerprint"]
            assert first_plan == second_plan
            assert protected_after_plan == protected_before_plan
            assert first_plan["summary"]["login_account_changes"] == 0
            assert first_plan["summary"]["room_membership_changes"] == 0
            assert first_plan["summary"]["incomplete_review_count"] == 0
            assert first_plan["summary"]["complete_review_count"] == 2
            assert {
                "staff",
                "users",
                "staff_source_accounts",
                "staff_service_periods",
                "staff_service_assignments",
                "staff_organization_assignments",
                "staff_job_assignments",
                "staff_hub_room_memberships",
                "audit_events",
            }.issubset(first_plan["protected_tables"])

            plan_by_name = {
                item["display_name"]: item for item in first_plan["candidates"]
            }
            multi_plan = plan_by_name["배정검토 겸직직원"]
            assert multi_plan["decision_source"] == "saved_review"
            assert multi_plan["outcome"] == "prepared"
            assert "multi_service_assignment_model" not in multi_plan["blocker_codes"]
            assert sum(
                person["service_assignment_actions"]["create"]
                for person in multi_plan["person_plans"]
            ) == multi_plan["account_count"]

            history_plan = plan_by_name["과거이력 휴직직원"]
            assert history_plan["decision_source"] == "auto_proposal"
            assert history_plan["outcome"] == "prepared"
            assert "leave_access_policy" not in history_plan["blocker_codes"]
            assert history_plan["history_only_account_count"] == 1
            assert len(history_plan["person_plans"]) == 1
            history_person_plan = history_plan["person_plans"][0]
            assert history_person_plan["employment_status"] == "leave"
            assert history_person_plan["current_assignment_account_count"] == 1
            assert history_person_plan["history_only_account_count"] == 1
            assert history_person_plan["access_policy"] == "leave_suspended"
            assert (
                history_person_plan["current_assignment_account_count"]
                + history_person_plan["history_only_account_count"]
                == history_plan["account_count"]
            )
            assert "leave_access_policy" not in history_person_plan["blocker_codes"]

            global_blocker_codes = {item["code"] for item in first_plan["blockers"]}
            assert "multi_service_assignment_model" not in global_blocker_codes
            assert "staff_directory_login_coupled" not in global_blocker_codes
            assert "leave_access_policy" not in global_blocker_codes
            assert "real_organization_catalog_missing" not in global_blocker_codes
            assert global_blocker_codes == {"rollback_manifest_missing"}
            assert first_plan["leave_access_policy"]["login"] == "blocked"
            assert first_plan["leave_access_policy"]["push"] == "blocked"

            with SessionLocal() as db:
                assert db.scalar(
                    select(func.count())
                    .select_from(StaffSyncReviewDraft)
                    .where(StaffSyncReviewDraft.batch_id == batch_id)
                ) == 2
                audit = db.scalar(
                    select(AuditEvent)
                    .where(AuditEvent.action == "staff.carefor_review_draft_saved")
                    .order_by(AuditEvent.created_at.desc())
                )
                assert audit is not None
                assert audit.details["safety"] == "review_draft_only_no_staff_mutation"
                assert "display_name" not in audit.details
                assert "external_id" not in audit.details

            with SessionLocal() as db:
                after = {
                    model.__tablename__: db.scalar(select(func.count()).select_from(model))
                    for model in tracked_models
                }
            assert after == before
    finally:
        settings.carefor_staff_roster_path = original_path


def test_staff_application_plan_is_deterministic_for_blocked_targets_and_periods():
    with TestClient(app):
        with SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            assert admin is not None
            organization_id = admin.organization_id
            linked_staff = [
                Staff(
                    organization_id=organization_id,
                    internal_code=f"PLAN-LINK-{index}",
                    display_name=f"계획 연결 직원 {index}",
                    job_title="요양보호사",
                    employment_status="active",
                    is_test_data=False,
                    is_active=True,
                )
                for index in (1, 2)
            ]
            db.add_all(linked_staff)
            db.flush()

            entries = [
                {
                    "source_key": "facility:PLAN-MULTI-FAC",
                    "service_type": "facility",
                    "external_id": "PLAN-MULTI-FAC",
                    "change_type": "update",
                    "status": "pending",
                    "current_staff_id": linked_staff[0].id,
                    "incoming_payload": {
                        "display_name": "계획 다중연결",
                        "job_name": "요양보호사",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": "2025-01-01",
                        "end_date": None,
                    },
                    "current_snapshot": None,
                    "conflict_reason": None,
                },
                {
                    "source_key": "daycare:PLAN-MULTI-DAY",
                    "service_type": "daycare",
                    "external_id": "PLAN-MULTI-DAY",
                    "change_type": "update",
                    "status": "pending",
                    "current_staff_id": linked_staff[1].id,
                    "incoming_payload": {
                        "display_name": "계획 다중연결",
                        "job_name": "요양보호사",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": "2025-01-01",
                        "end_date": None,
                    },
                    "current_snapshot": None,
                    "conflict_reason": None,
                },
                {
                    "source_key": "homecare:PLAN-NO-START",
                    "service_type": "homecare",
                    "external_id": "PLAN-NO-START",
                    "change_type": "new",
                    "status": "pending",
                    "current_staff_id": None,
                    "incoming_payload": {
                        "display_name": "계획 입사일누락",
                        "job_name": "요양보호사",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": None,
                        "end_date": None,
                    },
                    "current_snapshot": None,
                    "conflict_reason": None,
                },
            ]
            candidates = [
                {
                    "candidate_key": "person-plan-multi",
                    "display_name": "계획 다중연결",
                    "review_status": "proposal_ready",
                    "review_draft": None,
                    "accounts": [
                        {
                            "source_key": entry["source_key"],
                            "external_id": entry["external_id"],
                            "service_type": entry["service_type"],
                            "employment_status": "active",
                            "assignment_required": True,
                            "organizations": [],
                        }
                        for entry in entries[:2]
                    ],
                },
                {
                    "candidate_key": "person-plan-no-start",
                    "display_name": "계획 입사일누락",
                    "review_status": "proposal_ready",
                    "review_draft": None,
                    "accounts": [
                        {
                            "source_key": entries[2]["source_key"],
                            "external_id": entries[2]["external_id"],
                            "service_type": entries[2]["service_type"],
                            "employment_status": "active",
                            "assignment_required": True,
                            "organizations": [],
                        }
                    ],
                },
            ]

            try:
                stable_batch_id = uuid4()
                plans = [
                    build_staff_application_plan(
                        db,
                        organization_id,
                        batch_id=stable_batch_id,
                        file_sha256="0" * 64,
                        batch_status="preview",
                        entries=entries,
                        candidates=candidates,
                    )
                    for _ in range(2)
                ]
                assert plans[0]["plan_fingerprint"] == plans[1]["plan_fingerprint"]
                by_name = {
                    candidate["display_name"]: candidate
                    for candidate in plans[0]["candidates"]
                }
                multi_person = by_name["계획 다중연결"]["person_plans"][0]
                assert multi_person["target_staff_id"] is None
                assert multi_person["staff_action"] == "blocked"
                assert "multiple_linked_staff" in multi_person["blocker_codes"]

                missing_period_person = by_name["계획 입사일누락"]["person_plans"][0]
                assert missing_period_person["staff_action"] == "blocked"
                assert missing_period_person["service_period_actions"]["blocked"] == 1
                assert "invalid_service_period" in missing_period_person["blocker_codes"]
                assert plans[0]["summary"]["source_account_blocked"] == 3
                assert plans[0]["summary"]["service_period_blocked"] == 3
                assert plans[0]["summary"]["service_assignment_blocked"] == 3
                assert plans[0]["summary"]["staff_blocked"] == 2
                assert plans[0]["summary"]["staff_create"] == 0
            finally:
                db.rollback()


def test_application_plan_keeps_matching_service_assignment_unchanged() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            staff = Staff(
                organization_id=organization.id,
                internal_code="PLAN-SERVICE-NO-CHANGE",
                display_name="계획 서비스 유지 직원",
                job_title="사회복지사",
                employment_status="active",
                is_active=True,
            )
            db.add(staff)
            db.flush()
            account = StaffSourceAccount(
                organization_id=organization.id,
                staff_id=staff.id,
                source_system="carefor",
                service_type="facility",
                external_id="PLAN-SERVICE-ACCOUNT",
                display_name_snapshot=staff.display_name,
                job_name_snapshot="사회복지사",
                employment_status="active",
                source_status="재직",
                latest_start_date=date(2024, 1, 1),
                latest_end_date=None,
                is_active=True,
            )
            db.add(account)
            db.flush()
            period = StaffServicePeriod(
                organization_id=organization.id,
                staff_id=staff.id,
                source_account_id=account.id,
                start_date=date(2024, 1, 1),
                end_date=None,
                employment_status="active",
            )
            db.add(period)
            db.flush()
            db.add(
                StaffServiceAssignment(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_account_id=account.id,
                    service_period_id=period.id,
                    service_type="facility",
                    job_code="social_worker",
                    job_title_snapshot="사회복지사",
                    start_date=period.start_date,
                    end_date=None,
                    assignment_basis="carefor_auto",
                )
            )
            db.flush()

            source_key = "facility:PLAN-SERVICE-ACCOUNT"
            entries = [
                {
                    "source_key": source_key,
                    "service_type": "facility",
                    "external_id": "PLAN-SERVICE-ACCOUNT",
                    "current_staff_id": staff.id,
                    "incoming_payload": {
                        "display_name": staff.display_name,
                        "job_name": "사회복지사",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": "2024-01-01",
                        "end_date": None,
                    },
                }
            ]
            candidates = [
                {
                    "candidate_key": "person-plan-service-no-change",
                    "display_name": staff.display_name,
                    "review_status": "proposal_ready",
                    "review_draft": None,
                    "accounts": [
                        {
                            "source_key": source_key,
                            "external_id": "PLAN-SERVICE-ACCOUNT",
                            "service_type": "facility",
                            "employment_status": "active",
                            "assignment_required": True,
                            "organizations": [],
                            "job": {"code": "social_worker"},
                            "position": {},
                        }
                    ],
                }
            ]

            try:
                plan = build_staff_application_plan(
                    db,
                    organization.id,
                    batch_id=uuid4(),
                    file_sha256="1" * 64,
                    batch_status="preview",
                    entries=entries,
                    candidates=candidates,
                )
                person_plan = plan["candidates"][0]["person_plans"][0]
                assert person_plan["staff_action"] == "no_change"
                assert person_plan["source_account_actions"]["no_change"] == 1
                assert person_plan["service_period_actions"]["no_change"] == 1
                assert person_plan["service_assignment_actions"]["no_change"] == 1
                assert plan["summary"]["service_assignment_no_change"] == 1
            finally:
                db.rollback()


def test_application_plan_keeps_closed_and_current_same_service_history() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            staff = Staff(
                organization_id=organization.id,
                internal_code="PLAN-SERVICE-HISTORY-CURRENT",
                display_name="계획 과거 현재 시설 직원",
                job_title="사회복지사",
                employment_status="active",
                is_active=True,
            )
            db.add(staff)
            db.flush()

            specs = [
                {
                    "external_id": "PLAN-SERVICE-HISTORY",
                    "source_status": "퇴사",
                    "employment_status": "retired",
                    "start_date": date(2020, 1, 1),
                    "end_date": date(2022, 12, 31),
                    "is_active": False,
                },
                {
                    "external_id": "PLAN-SERVICE-CURRENT",
                    "source_status": "재직",
                    "employment_status": "active",
                    "start_date": date(2023, 1, 1),
                    "end_date": None,
                    "is_active": True,
                },
            ]
            entries: list[dict[str, object]] = []
            candidate_accounts: list[dict[str, object]] = []
            for spec in specs:
                account = StaffSourceAccount(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_system="carefor",
                    service_type="facility",
                    external_id=str(spec["external_id"]),
                    display_name_snapshot=staff.display_name,
                    job_name_snapshot="사회복지사",
                    employment_status=str(spec["employment_status"]),
                    source_status=str(spec["source_status"]),
                    latest_start_date=spec["start_date"],
                    latest_end_date=spec["end_date"],
                    is_active=bool(spec["is_active"]),
                )
                db.add(account)
                db.flush()
                period = StaffServicePeriod(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_account_id=account.id,
                    start_date=spec["start_date"],
                    end_date=spec["end_date"],
                    employment_status=str(spec["employment_status"]),
                )
                db.add(period)
                db.flush()
                db.add(
                    StaffServiceAssignment(
                        organization_id=organization.id,
                        staff_id=staff.id,
                        source_account_id=account.id,
                        service_period_id=period.id,
                        service_type="facility",
                        job_code="social_worker",
                        job_title_snapshot="사회복지사",
                        start_date=spec["start_date"],
                        end_date=spec["end_date"],
                        assignment_basis="carefor_auto",
                    )
                )
                source_key = f"facility:{spec['external_id']}"
                entries.append(
                    {
                        "source_key": source_key,
                        "service_type": "facility",
                        "external_id": spec["external_id"],
                        "current_staff_id": staff.id,
                        "incoming_payload": {
                            "display_name": staff.display_name,
                            "job_name": "사회복지사",
                            "employment_status": spec["employment_status"],
                            "source_status": spec["source_status"],
                            "start_date": spec["start_date"].isoformat(),
                            "end_date": (
                                spec["end_date"].isoformat()
                                if spec["end_date"] is not None
                                else None
                            ),
                        },
                    }
                )
                candidate_accounts.append(
                    {
                        "source_key": source_key,
                        "external_id": spec["external_id"],
                        "service_type": "facility",
                        "employment_status": spec["employment_status"],
                        "assignment_required": spec["end_date"] is None,
                        "organizations": [],
                        "job": {"code": "social_worker"},
                        "position": {},
                    }
                )
            db.flush()

            candidates = [
                {
                    "candidate_key": "person-plan-service-history-current",
                    "display_name": staff.display_name,
                    "review_status": "proposal_ready",
                    "review_draft": None,
                    "accounts": candidate_accounts,
                }
            ]
            try:
                plan = build_staff_application_plan(
                    db,
                    organization.id,
                    batch_id=uuid4(),
                    file_sha256="3" * 64,
                    batch_status="preview",
                    entries=entries,
                    candidates=candidates,
                )
                person_plan = plan["candidates"][0]["person_plans"][0]
                assert person_plan["staff_action"] == "no_change"
                assert "invalid_service_period" not in person_plan["blocker_codes"]
                assert person_plan["source_account_actions"]["no_change"] == 2
                assert person_plan["service_period_actions"] == {
                    "create": 0,
                    "update": 0,
                    "no_change": 2,
                    "blocked": 0,
                }
                assert person_plan["service_assignment_actions"] == {
                    "create": 0,
                    "update": 0,
                    "no_change": 2,
                    "blocked": 0,
                }
            finally:
                db.rollback()


def test_application_plan_closes_open_history_before_rehire_create() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            staff = Staff(
                organization_id=organization.id,
                internal_code="PLAN-SERVICE-REHIRE",
                display_name="계획 재입사 직원",
                job_title="사회복지사",
                employment_status="active",
                is_active=True,
            )
            db.add(staff)
            db.flush()
            account = StaffSourceAccount(
                organization_id=organization.id,
                staff_id=staff.id,
                source_system="carefor",
                service_type="facility",
                external_id="PLAN-SERVICE-REHIRE",
                display_name_snapshot=staff.display_name,
                job_name_snapshot="사회복지사",
                employment_status="active",
                source_status="재직",
                latest_start_date=date(2024, 1, 1),
                latest_end_date=None,
                is_active=True,
            )
            db.add(account)
            db.flush()
            old_period = StaffServicePeriod(
                organization_id=organization.id,
                staff_id=staff.id,
                source_account_id=account.id,
                start_date=date(2024, 1, 1),
                end_date=None,
                employment_status="active",
            )
            db.add(old_period)
            db.flush()
            db.add(
                StaffServiceAssignment(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_account_id=account.id,
                    service_period_id=old_period.id,
                    service_type="facility",
                    job_code="social_worker",
                    job_title_snapshot="사회복지사",
                    start_date=old_period.start_date,
                    end_date=None,
                    assignment_basis="carefor_auto",
                )
            )
            db.flush()

            source_key = "facility:PLAN-SERVICE-REHIRE"
            entries = [
                {
                    "source_key": source_key,
                    "service_type": "facility",
                    "external_id": "PLAN-SERVICE-REHIRE",
                    "current_staff_id": staff.id,
                    "incoming_payload": {
                        "display_name": staff.display_name,
                        "job_name": "사회복지사",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": "2026-01-01",
                        "end_date": None,
                    },
                }
            ]
            candidates = [
                {
                    "candidate_key": "person-plan-service-rehire",
                    "display_name": staff.display_name,
                    "review_status": "proposal_ready",
                    "review_draft": None,
                    "accounts": [
                        {
                            "source_key": source_key,
                            "external_id": "PLAN-SERVICE-REHIRE",
                            "service_type": "facility",
                            "employment_status": "active",
                            "assignment_required": True,
                            "organizations": [],
                            "job": {"code": "social_worker"},
                            "position": {},
                        }
                    ],
                }
            ]

            try:
                plan = build_staff_application_plan(
                    db,
                    organization.id,
                    batch_id=uuid4(),
                    file_sha256="2" * 64,
                    batch_status="preview",
                    entries=entries,
                    candidates=candidates,
                )
                person_plan = plan["candidates"][0]["person_plans"][0]
                assert person_plan["staff_action"] == "no_change"
                assert person_plan["source_account_actions"]["update"] == 1
                assert person_plan["service_period_actions"] == {
                    "create": 1,
                    "update": 1,
                    "no_change": 0,
                    "blocked": 0,
                }
                assert person_plan["service_assignment_actions"] == {
                    "create": 1,
                    "update": 1,
                    "no_change": 0,
                    "blocked": 0,
                }
            finally:
                db.rollback()
