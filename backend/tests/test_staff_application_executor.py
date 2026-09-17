from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
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
    StaffApplicationBackupProof,
    StaffApplicationEntry,
    StaffApplicationRestoreReceipt,
    StaffApplicationRun,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    StaffSyncBatch,
    User,
)
from app.staff_application_executor import (
    EXPECTED_ENTRY_COUNTS,
    StaffApplicationExecutionError,
    _capture_protected_tables,
    _file_sha256,
    _fingerprint,
    _protected_state_fingerprint,
    _recomputed_manifest_fingerprint,
    apply_staff_application_run,
)
@dataclass
class ExecutorFixture:
    db: Session
    run: StaffApplicationRun
    batch: StaffSyncBatch
    admin: User
    entries: list[StaffApplicationEntry]
    baseline: dict[str, int]


def _count(db: Session, model) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _baseline(db: Session) -> dict[str, int]:
    return {
        "staff": _count(db, Staff),
        "users": _count(db, User),
        "source_accounts": _count(db, StaffSourceAccount),
        "service_periods": _count(db, StaffServicePeriod),
        "service_assignments": _count(db, StaffServiceAssignment),
        "memberships": _count(db, RoomMembership),
        "audit": _count(db, AuditEvent),
    }


def _entry(
    *,
    entity_type: str,
    row_id: UUID,
    natural_key: dict,
    after_state: dict,
    dependency_keys: list[str],
) -> dict:
    return {
        "entry_key": f"{entity_type}:{row_id}",
        "entity_type": entity_type,
        "action": "create",
        "row_id": row_id,
        "natural_key": natural_key,
        "before_state": None,
        "after_state": after_state,
        "before_fingerprint": None,
        "after_fingerprint": _fingerprint(after_state),
        "dependency_keys": dependency_keys,
    }


def _manifest_rows(
    organization_id: UUID,
    batch_id: UUID,
    business_ids: dict[str, UUID],
) -> list[dict]:
    rows: list[dict] = []
    staff_rows: list[tuple[UUID, str, str]] = []
    account_specs: list[tuple[UUID, str, str, str]] = []

    for index in range(75):
        staff_id = uuid4()
        code = f"EXEC-{index + 1:03d}"
        staff_key = f"staff:{staff_id}"
        staff_rows.append((staff_id, code, staff_key))
        rows.append(
            _entry(
                entity_type="staff",
                row_id=staff_id,
                natural_key={"internal_code": code},
                after_state={
                    "id": str(staff_id),
                    "organization_id": str(organization_id),
                    "internal_code": code,
                    "display_name": f"실행기 시험직원 {index + 1:03d}",
                    "job_title": "",
                    "position_title": None,
                    "employment_status": "active",
                    "is_test_data": False,
                    "is_active": True,
                    "terminated_at": None,
                    "deleted_at": None,
                },
                dependency_keys=[],
            )
        )
        first_service = ("facility", "daycare", "homecare")[index % 3]
        account_specs.append(
            (staff_id, staff_key, first_service, f"EXEC-{index + 1:03d}-A")
        )
        if index < 14:
            second_service = "daycare" if first_service != "daycare" else "homecare"
            account_specs.append(
                (staff_id, staff_key, second_service, f"EXEC-{index + 1:03d}-B")
            )

    account_rows: list[dict] = []
    period_rows: list[dict] = []
    assignment_rows: list[dict] = []
    for account_index, (staff_id, staff_key, service_type, external_id) in enumerate(
        account_specs,
        start=1,
    ):
        account_id = uuid4()
        account_key = f"source_account:{account_id}"
        period_id = uuid4()
        period_key = f"service_period:{period_id}"
        assignment_id = uuid4()
        start_date = "2025-01-01"
        account_rows.append(
            _entry(
                entity_type="source_account",
                row_id=account_id,
                natural_key={
                    "source_system": "carefor",
                    "service_type": service_type,
                    "external_id": external_id,
                },
                after_state={
                    "id": str(account_id),
                    "organization_id": str(organization_id),
                    "staff_id": str(staff_id),
                    "source_system": "carefor",
                    "service_type": service_type,
                    "external_id": external_id,
                    "display_name_snapshot": f"실행기 시험직원 {account_index:03d}",
                    "job_name_snapshot": "",
                    "employment_status": "active",
                    "source_status": "재직",
                    "latest_start_date": start_date,
                    "latest_end_date": None,
                    "is_active": True,
                },
                dependency_keys=[staff_key],
            )
        )
        period_rows.append(
            _entry(
                entity_type="service_period",
                row_id=period_id,
                natural_key={
                    "source_account_id": str(account_id),
                    "start_date": start_date,
                },
                after_state={
                    "id": str(period_id),
                    "organization_id": str(organization_id),
                    "staff_id": str(staff_id),
                    "source_account_id": str(account_id),
                    "start_date": start_date,
                    "end_date": None,
                    "employment_status": "active",
                },
                dependency_keys=[staff_key, account_key],
            )
        )
        assignment_rows.append(
            _entry(
                entity_type="service_assignment",
                row_id=assignment_id,
                natural_key={
                    "service_period_id": str(period_id),
                    "start_date": start_date,
                },
                after_state={
                    "id": str(assignment_id),
                    "organization_id": str(organization_id),
                    "staff_id": str(staff_id),
                    "source_account_id": str(account_id),
                    "service_period_id": str(period_id),
                    "service_type": service_type,
                    "business_unit_id": str(business_ids[service_type]),
                    "department_unit_id": None,
                    "floor_unit_id": None,
                    "team_unit_id": None,
                    "job_code": None,
                    "job_title_snapshot": None,
                    "position_code_id": None,
                    "position_title_snapshot": None,
                    "start_date": start_date,
                    "end_date": None,
                    "assignment_basis": "carefor_auto",
                    "source_batch_id": str(batch_id),
                    "note": "",
                    "is_test_data": False,
                },
                dependency_keys=[staff_key, account_key, period_key],
            )
        )

    assert len(account_rows) == 89
    return rows + account_rows + period_rows + assignment_rows


def _write_evidence_files(
    backup_root: Path,
    protected_tables: list[dict],
    revision: str,
) -> dict[str, str | int]:
    backup_root.mkdir(parents=True, exist_ok=True)
    token = uuid4().hex
    dump_path = backup_root / f"executor-{token}.dump"
    dump_path.write_bytes(b"executor-isolated-backup")
    dump_sha = _file_sha256(dump_path)
    state_fingerprint = _protected_state_fingerprint(protected_tables)
    evidence_path = backup_root / f"executor-{token}.json"
    evidence_path.write_text(
        json.dumps({"fixture": "executor-backup", "token": token}),
        encoding="utf-8",
    )
    receipt_path = backup_root / f"executor-{token}.restore-receipt.json"
    receipt_path.write_text(
        json.dumps({"fixture": "executor-restore", "token": token}),
        encoding="utf-8",
    )
    return {
        "dump_filename": dump_path.name,
        "dump_bytes": dump_path.stat().st_size,
        "dump_sha": dump_sha,
        "evidence_filename": evidence_path.name,
        "evidence_sha": _file_sha256(evidence_path),
        "receipt_filename": receipt_path.name,
        "receipt_sha": _file_sha256(receipt_path),
        "state_fingerprint": state_fingerprint,
        "revision": revision,
    }


def _build_fixture(db: Session, backup_root: Path) -> ExecutorFixture:
    admin = db.scalar(select(User).where(User.username == "admin"))
    assert admin is not None
    organization = db.get(Organization, admin.organization_id)
    assert organization is not None

    unit_token = uuid4().hex[:8]
    business_ids: dict[str, UUID] = {}
    for service_type, name in (
        ("facility", "시설"),
        ("daycare", "주간보호"),
        ("homecare", "방문요양"),
    ):
        unit = OrgUnit(
            organization_id=organization.id,
            unit_type="business",
            internal_code=f"EXEC-{unit_token}-{service_type}",
            name=f"{name}-{unit_token}",
            is_active=True,
            is_test_data=False,
        )
        db.add(unit)
        db.flush()
        business_ids[service_type] = unit.id

    batch = StaffSyncBatch(
        organization_id=organization.id,
        original_name=f"executor-{unit_token}.json",
        file_sha256=(unit_token * 8)[:64],
        status="previewed",
        summary={"fixture": True},
        created_by_id=admin.id,
    )
    db.add(batch)
    db.flush()

    run = StaffApplicationRun(
        organization_id=organization.id,
        batch_id=batch.id,
        manifest_schema_version=1,
        plan_schema_version=2,
        plan_fingerprint="1" * 64,
        change_fingerprint="2" * 64,
        manifest_fingerprint="0" * 64,
        organization_catalog_version="fixture-v1",
        organization_catalog_fingerprint="3" * 64,
        leave_access_policy_version="fixture-v1",
        leave_access_policy_fingerprint="4" * 64,
        policy_snapshot={"leave_access_policy": "fixture"},
        plan_summary={
            "candidate_count": 75,
            "prepared_candidates": 75,
            "login_account_changes": 0,
            "room_membership_changes": 0,
        },
        entry_count=342,
        status="prepared",
        created_by_id=admin.id,
    )
    db.add(run)
    db.flush()
    manifest_rows = _manifest_rows(organization.id, batch.id, business_ids)
    for ordinal, item in enumerate(manifest_rows, start=1):
        db.add(
            StaffApplicationEntry(
                run_id=run.id,
                ordinal=ordinal,
                entry_key=item["entry_key"],
                entity_type=item["entity_type"],
                action=item["action"],
                row_id=item["row_id"],
                natural_key=item["natural_key"],
                before_state=item["before_state"],
                after_state=item["after_state"],
                before_fingerprint=item["before_fingerprint"],
                after_fingerprint=item["after_fingerprint"],
                dependency_keys=item["dependency_keys"],
            )
        )
    db.flush()
    entries = list(
        db.scalars(
            select(StaffApplicationEntry)
            .where(StaffApplicationEntry.run_id == run.id)
            .order_by(StaffApplicationEntry.ordinal)
        ).all()
    )
    run.manifest_fingerprint = _recomputed_manifest_fingerprint(run, batch, entries)
    db.flush()

    # Backup manifests preserve PowerShell's culture-aware line ordering,
    # which can differ from Python sorting. A reversed fixture guards against
    # accidentally normalizing the evidence order in the executor.
    protected_tables = list(reversed(_capture_protected_tables(db)))
    revision = "025_staff_application_manifest"
    files = _write_evidence_files(backup_root, protected_tables, revision)
    now = datetime.now(timezone.utc)
    proof = StaffApplicationBackupProof(
        run_id=run.id,
        evidence_filename=str(files["evidence_filename"]),
        evidence_sha256=str(files["evidence_sha"]),
        dump_filename=str(files["dump_filename"]),
        dump_bytes=int(files["dump_bytes"]),
        dump_sha256=str(files["dump_sha"]),
        database_schema=settings.database_schema,
        alembic_revision=revision,
        protected_state_fingerprint=str(files["state_fingerprint"]),
        protected_tables=protected_tables,
        source_created_at=now,
        raw_payload={"fixture": True},
        verified_by_id=admin.id,
    )
    receipt = StaffApplicationRestoreReceipt(
        run_id=run.id,
        receipt_filename=str(files["receipt_filename"]),
        receipt_sha256=str(files["receipt_sha"]),
        dump_sha256=str(files["dump_sha"]),
        restored_schema=settings.database_schema,
        restored_alembic_revision=revision,
        restored_state_fingerprint=str(files["state_fingerprint"]),
        restored_tables=protected_tables,
        started_at=now,
        finished_at=now,
        cleanup_succeeded=True,
        raw_payload={"fixture": True},
        verified_by_id=admin.id,
    )
    db.add_all([proof, receipt])
    run.status = "evidence_ready"
    db.flush()
    return ExecutorFixture(
        db=db,
        run=run,
        batch=batch,
        admin=admin,
        entries=entries,
        baseline=_baseline(db),
    )


@pytest.fixture
def executor_fixture(tmp_path, monkeypatch):
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir(parents=True)
    monkeypatch.setattr(settings, "upload_dir", upload_dir.as_posix())
    monkeypatch.setattr(
        "app.staff_application_executor._current_alembic_revision",
        lambda db: "025_staff_application_manifest",
    )
    with TestClient(app):
        with SessionLocal() as db:
            fixture = _build_fixture(db, tmp_path / "backups")
            try:
                yield fixture
            finally:
                db.rollback()


def test_executor_rejects_incomplete_evidence(executor_fixture: ExecutorFixture) -> None:
    fixture = executor_fixture
    fixture.run.status = "backup_verified"
    fixture.db.flush()

    with pytest.raises(StaffApplicationExecutionError, match="증빙"):
        apply_staff_application_run(
            fixture.db,
            run_id=fixture.run.id,
            expected_manifest_fingerprint=fixture.run.manifest_fingerprint,
            applied_by_id=fixture.admin.id,
        )

    assert _baseline(fixture.db) == fixture.baseline


def test_executor_rejects_manifest_and_current_backup_fingerprint_changes(
    executor_fixture: ExecutorFixture,
) -> None:
    fixture = executor_fixture
    with pytest.raises(StaffApplicationExecutionError, match="예상 manifest|확인한 manifest"):
        apply_staff_application_run(
            fixture.db,
            run_id=fixture.run.id,
            expected_manifest_fingerprint="f" * 64,
            applied_by_id=fixture.admin.id,
        )

    original_name = fixture.admin.display_name
    fixture.admin.display_name = original_name + " 변경"
    fixture.db.flush()
    with pytest.raises(StaffApplicationExecutionError, match="보호자료"):
        apply_staff_application_run(
            fixture.db,
            run_id=fixture.run.id,
            expected_manifest_fingerprint=fixture.run.manifest_fingerprint,
            applied_by_id=fixture.admin.id,
        )
    fixture.admin.display_name = original_name
    fixture.db.flush()
    assert _baseline(fixture.db) == fixture.baseline


def test_executor_rolls_back_every_insert_when_middle_step_fails(
    executor_fixture: ExecutorFixture,
    monkeypatch,
) -> None:
    fixture = executor_fixture

    def fail_after_first_staff(db, entries, *, applied_by_id):
        del applied_by_id
        first = next(item for item in entries if item.entity_type == "staff")
        state = first.state
        db.add(
            Staff(
                id=first.row_id,
                organization_id=UUID(str(state["organization_id"])),
                internal_code=str(state["internal_code"]),
                display_name=str(state["display_name"]),
                job_title="",
                employment_status="active",
                is_test_data=False,
                is_active=True,
            )
        )
        db.flush()
        raise RuntimeError("injected middle failure")

    monkeypatch.setattr(
        "app.staff_application_executor._insert_manifest_rows",
        fail_after_first_staff,
    )
    with pytest.raises(StaffApplicationExecutionError, match="전체 적용을 취소"):
        apply_staff_application_run(
            fixture.db,
            run_id=fixture.run.id,
            expected_manifest_fingerprint=fixture.run.manifest_fingerprint,
            applied_by_id=fixture.admin.id,
        )

    assert _baseline(fixture.db) == fixture.baseline
    fixture.db.refresh(fixture.run)
    assert fixture.run.status == "evidence_ready"
    assert fixture.db.scalar(
        select(func.count(AuditEvent.id)).where(
            AuditEvent.action == "staff.application_applied",
            AuditEvent.target_id == fixture.run.id,
        )
    ) == 0


def test_executor_applies_exact_342_rows_without_login_or_room_and_blocks_repeat(
    executor_fixture: ExecutorFixture,
) -> None:
    fixture = executor_fixture
    before_users = _count(fixture.db, User)
    before_memberships = _count(fixture.db, RoomMembership)

    result = apply_staff_application_run(
        fixture.db,
        run_id=fixture.run.id,
        expected_manifest_fingerprint=fixture.run.manifest_fingerprint,
        applied_by_id=fixture.admin.id,
    )

    assert result.created_counts == EXPECTED_ENTRY_COUNTS
    assert _count(fixture.db, Staff) == fixture.baseline["staff"] + 75
    assert _count(fixture.db, StaffSourceAccount) == fixture.baseline["source_accounts"] + 89
    assert _count(fixture.db, StaffServicePeriod) == fixture.baseline["service_periods"] + 89
    assert (
        _count(fixture.db, StaffServiceAssignment)
        == fixture.baseline["service_assignments"] + 89
    )
    assert _count(fixture.db, User) == before_users
    assert _count(fixture.db, RoomMembership) == before_memberships
    fixture.db.refresh(fixture.run)
    assert fixture.run.status == "stale"
    audit = fixture.db.get(AuditEvent, result.audit_event_id)
    assert audit is not None
    assert audit.action == "staff.application_applied"
    assert audit.details["login_accounts_created"] == 0
    assert audit.details["room_memberships_created"] == 0

    with pytest.raises(StaffApplicationExecutionError):
        apply_staff_application_run(
            fixture.db,
            run_id=fixture.run.id,
            expected_manifest_fingerprint=fixture.run.manifest_fingerprint,
            applied_by_id=fixture.admin.id,
        )

    assert _count(fixture.db, Staff) == fixture.baseline["staff"] + 75
