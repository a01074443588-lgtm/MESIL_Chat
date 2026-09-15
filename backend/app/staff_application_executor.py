from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from hashlib import sha256
import hmac
import re
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text, tuple_
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    AuditEvent,
    OrgUnit,
    Staff,
    StaffApplicationEntry,
    StaffApplicationRun,
    StaffJobCode,
    StaffPositionCode,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    StaffSyncBatch,
    User,
    utcnow,
)
from .staff_application_manifest import (
    _current_alembic_revision,
    _fingerprint,
)


EXPECTED_ENTRY_COUNTS = {
    "staff": 75,
    "source_account": 89,
    "service_period": 89,
    "service_assignment": 89,
}
EXPECTED_ENTRY_TOTAL = sum(EXPECTED_ENTRY_COUNTS.values())

PROTECTED_TABLE_NAMES = (
    "staff",
    "users",
    "staff_organization_assignments",
    "staff_job_assignments",
    "staff_source_accounts",
    "staff_service_periods",
    "staff_service_assignments",
    "organization_units",
    "staff_position_codes",
    "staff_hub_room_memberships",
    "staff_hub_messages",
    "staff_hub_message_read_receipts",
    "staff_hub_rooms",
    "auth_sessions",
    "staff_hub_push_subscriptions",
    "staff_sync_batches",
    "staff_sync_items",
    "staff_sync_review_drafts",
    "audit_events",
)

ENTITY_ORDER = {
    "staff": 0,
    "source_account": 1,
    "service_period": 2,
    "service_assignment": 3,
}

STATE_KEYS = {
    "staff": {
        "id",
        "organization_id",
        "internal_code",
        "display_name",
        "job_title",
        "position_title",
        "employment_status",
        "is_test_data",
        "is_active",
        "terminated_at",
        "deleted_at",
    },
    "source_account": {
        "id",
        "organization_id",
        "staff_id",
        "source_system",
        "service_type",
        "external_id",
        "display_name_snapshot",
        "job_name_snapshot",
        "employment_status",
        "source_status",
        "latest_start_date",
        "latest_end_date",
        "is_active",
    },
    "service_period": {
        "id",
        "organization_id",
        "staff_id",
        "source_account_id",
        "start_date",
        "end_date",
        "employment_status",
    },
    "service_assignment": {
        "id",
        "organization_id",
        "staff_id",
        "source_account_id",
        "service_period_id",
        "service_type",
        "business_unit_id",
        "department_unit_id",
        "floor_unit_id",
        "team_unit_id",
        "job_code",
        "job_title_snapshot",
        "position_code_id",
        "position_title_snapshot",
        "start_date",
        "end_date",
        "assignment_basis",
        "source_batch_id",
        "note",
        "is_test_data",
    },
}

NATURAL_KEY_FIELDS = {
    "staff": {"internal_code"},
    "source_account": {"source_system", "service_type", "external_id"},
    "service_period": {"source_account_id", "start_date"},
    "service_assignment": {"service_period_id", "start_date"},
}

UUID_STATE_FIELDS = {
    "id",
    "organization_id",
    "staff_id",
    "source_account_id",
    "service_period_id",
    "business_unit_id",
    "department_unit_id",
    "floor_unit_id",
    "team_unit_id",
    "position_code_id",
    "source_batch_id",
}

HEX_64 = re.compile(r"^[0-9a-f]{64}$")
HEX_32 = re.compile(r"^[0-9a-f]{32}$")
SERVICE_TYPES = {"facility", "daycare", "homecare"}
EMPLOYMENT_STATUSES = {"active", "leave", "retired", "unknown"}
ASSIGNMENT_BASES = {"carefor_auto", "admin_confirmed"}


class StaffApplicationExecutionError(ValueError):
    """Raised when an immutable staff application run cannot be applied safely."""


@dataclass(frozen=True)
class StaffApplicationExecutionResult:
    run_id: UUID
    batch_id: UUID
    manifest_fingerprint: str
    created_counts: dict[str, int]
    audit_event_id: UUID


@dataclass(frozen=True)
class _PreparedEntry:
    stored: StaffApplicationEntry
    state: dict[str, Any]
    natural_key: dict[str, Any]

    @property
    def entity_type(self) -> str:
        return self.stored.entity_type

    @property
    def row_id(self) -> UUID:
        return self.stored.row_id


def _fail(message: str) -> StaffApplicationExecutionError:
    return StaffApplicationExecutionError(message)


def _uuid(value: Any, label: str, *, optional: bool = False) -> UUID | None:
    if value is None and optional:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as exc:
        raise _fail(f"{label} UUID가 올바르지 않습니다.") from exc


def _date(value: Any, label: str, *, optional: bool = False) -> date | None:
    if value is None and optional:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise _fail(f"{label} 날짜가 올바르지 않습니다.") from exc


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _evidence_path(filename: str, label: str) -> Path:
    if not filename or Path(filename).name != filename:
        raise _fail(f"{label} 파일 이름이 올바르지 않습니다.")
    root = (Path(settings.upload_dir).resolve().parent / "backups").resolve()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise _fail(f"{label} 파일이 허용된 백업 폴더 밖에 있습니다.") from exc
    if not path.is_file():
        raise _fail(f"{label} 파일을 찾을 수 없습니다.")
    return path


def _normalize_protected_tables(
    value: Any,
    *,
    label: str,
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise _fail(f"{label} 보호표 목록이 없습니다.")
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, dict):
            raise _fail(f"{label} 보호표 항목이 올바르지 않습니다.")
        table = str(item.get("table") or "")
        if table not in PROTECTED_TABLE_NAMES or table in seen:
            raise _fail(f"{label} 보호표 이름이 올바르지 않습니다: {table or '없음'}")
        seen.add(table)
        try:
            row_count = int(item.get("row_count"))
        except (TypeError, ValueError) as exc:
            raise _fail(f"{label} {table} 행 수가 올바르지 않습니다.") from exc
        digest = str(item.get("row_digest_md5") or "").lower()
        if row_count < 0 or not HEX_32.fullmatch(digest):
            raise _fail(f"{label} {table} 행 지문이 올바르지 않습니다.")
        normalized.append(
            {
                "table": table,
                "row_count": row_count,
                "row_digest_md5": digest,
            }
        )
    if seen != set(PROTECTED_TABLE_NAMES):
        missing = sorted(set(PROTECTED_TABLE_NAMES) - seen)
        raise _fail(f"{label} 보호표가 빠졌습니다: {', '.join(missing)}")
    # The PowerShell backup script fingerprints the already sorted output lines.
    # Keep the evidence order verbatim: PowerShell culture-aware sorting is not
    # guaranteed to match Python's Unicode/code-point ordering for underscores.
    return normalized


def _protected_state_fingerprint(tables: list[dict[str, Any]]) -> str:
    lines = [
        f"{item['table']}|{item['row_count']}|{item['row_digest_md5']}"
        for item in tables
    ]
    return sha256("\n".join(lines).encode("utf-8")).hexdigest().upper()


def _capture_protected_tables(
    db: Session,
    table_order: tuple[str, ...] | list[str] = PROTECTED_TABLE_NAMES,
) -> list[dict[str, Any]]:
    schema = settings.database_schema
    if not re.fullmatch(r"[A-Za-z0-9_]+", schema):
        raise _fail("데이터베이스 스키마 이름이 올바르지 않습니다.")
    result: list[dict[str, Any]] = []
    if len(table_order) != len(PROTECTED_TABLE_NAMES) or set(table_order) != set(
        PROTECTED_TABLE_NAMES
    ):
        raise _fail("현재 DB 보호표 조회 순서가 올바르지 않습니다.")
    for table_name in table_order:
        row = db.execute(
            text(
                f'SELECT COUNT(*)::bigint, '
                f"MD5(COALESCE(STRING_AGG(TO_JSONB(t)::text, E'\\n' "
                f'ORDER BY id::text), \'\')) '
                f'FROM "{schema}"."{table_name}" AS t'
            )
        ).one()
        result.append(
            {
                "table": table_name,
                "row_count": int(row[0]),
                "row_digest_md5": str(row[1]).lower(),
            }
        )
    return result


def _verify_evidence(db: Session, run: StaffApplicationRun) -> None:
    proof = run.backup_proof
    receipt = run.restore_receipt
    if run.status != "evidence_ready" or proof is None or receipt is None:
        raise _fail("백업과 격리 복원 증빙이 모두 완료된 실행기록만 적용할 수 있습니다.")
    if not receipt.cleanup_succeeded:
        raise _fail("격리 복원 DB 정리 성공이 확인되지 않았습니다.")

    current_revision = _current_alembic_revision(db)
    if (
        proof.database_schema != settings.database_schema
        or receipt.restored_schema != settings.database_schema
    ):
        raise _fail("백업·복원 스키마가 현재 개발 DB와 다릅니다.")
    if (
        proof.alembic_revision != current_revision
        or receipt.restored_alembic_revision != current_revision
    ):
        raise _fail("백업·복원 DB 버전이 현재 DB와 다릅니다.")
    if proof.dump_sha256 != receipt.dump_sha256:
        raise _fail("백업 증명과 복원 영수증의 dump 지문이 다릅니다.")

    evidence_path = _evidence_path(proof.evidence_filename, "백업 증명")
    receipt_path = _evidence_path(receipt.receipt_filename, "복원 영수증")
    dump_path = _evidence_path(proof.dump_filename, "백업 dump")
    if not hmac.compare_digest(_file_sha256(evidence_path), proof.evidence_sha256.upper()):
        raise _fail("백업 증명 파일 지문이 저장값과 다릅니다.")
    if not hmac.compare_digest(_file_sha256(receipt_path), receipt.receipt_sha256.upper()):
        raise _fail("복원 영수증 파일 지문이 저장값과 다릅니다.")
    if dump_path.stat().st_size != proof.dump_bytes:
        raise _fail("백업 dump 크기가 저장값과 다릅니다.")
    if not hmac.compare_digest(_file_sha256(dump_path), proof.dump_sha256.upper()):
        raise _fail("백업 dump 지문이 저장값과 다릅니다.")

    proof_tables = _normalize_protected_tables(
        proof.protected_tables,
        label="백업",
    )
    receipt_tables = _normalize_protected_tables(
        receipt.restored_tables,
        label="복원",
    )
    if proof_tables != receipt_tables:
        raise _fail("백업과 격리 복원의 보호표 지문이 다릅니다.")
    proof_state = _protected_state_fingerprint(proof_tables)
    if (
        not hmac.compare_digest(proof_state, proof.protected_state_fingerprint.upper())
        or not hmac.compare_digest(proof_state, receipt.restored_state_fingerprint.upper())
    ):
        raise _fail("백업·복원 전체 보호상태 지문이 다릅니다.")

    current_tables = _capture_protected_tables(
        db,
        [item["table"] for item in proof_tables],
    )
    if current_tables != proof_tables:
        changed = [
            expected["table"]
            for expected, current in zip(proof_tables, current_tables, strict=True)
            if expected != current
        ]
        raise _fail(
            "백업 뒤 현재 DB 보호자료가 달라졌습니다: " + ", ".join(changed)
        )
    current_state = _protected_state_fingerprint(current_tables)
    if not hmac.compare_digest(current_state, proof.protected_state_fingerprint.upper()):
        raise _fail("현재 DB 전체 보호상태 지문이 백업과 다릅니다.")


def _stored_entry_payload(entry: StaffApplicationEntry) -> dict[str, Any]:
    return {
        "entry_key": entry.entry_key,
        "entity_type": entry.entity_type,
        "action": entry.action,
        "row_id": str(entry.row_id),
        "natural_key": entry.natural_key,
        "before_state": entry.before_state,
        "after_state": entry.after_state,
        "before_fingerprint": entry.before_fingerprint,
        "after_fingerprint": entry.after_fingerprint,
        "dependency_keys": list(entry.dependency_keys or []),
    }


def _recomputed_manifest_fingerprint(
    run: StaffApplicationRun,
    batch: StaffSyncBatch,
    entries: list[StaffApplicationEntry],
) -> str:
    return _fingerprint(
        {
            "manifest_schema_version": run.manifest_schema_version,
            "organization_id": run.organization_id,
            "batch_id": run.batch_id,
            "source_file_sha256": batch.file_sha256,
            "plan_schema_version": run.plan_schema_version,
            "plan_fingerprint": run.plan_fingerprint,
            "change_fingerprint": run.change_fingerprint,
            "organization_catalog_version": run.organization_catalog_version,
            "organization_catalog_fingerprint": run.organization_catalog_fingerprint,
            "leave_access_policy_version": run.leave_access_policy_version,
            "leave_access_policy_fingerprint": run.leave_access_policy_fingerprint,
            "entries": [_stored_entry_payload(entry) for entry in entries],
        }
    )


def _validate_entry_shape(
    entry: StaffApplicationEntry,
    *,
    organization_id: UUID,
) -> _PreparedEntry:
    entity_type = entry.entity_type
    if entity_type not in EXPECTED_ENTRY_COUNTS:
        raise _fail(f"지원하지 않는 manifest 행 종류입니다: {entity_type}")
    if entry.action != "create" or entry.before_state is not None or entry.before_fingerprint:
        raise _fail("첫 실제 직원 반영은 create-only manifest만 허용합니다.")
    if entry.entry_key != f"{entity_type}:{entry.row_id}":
        raise _fail("manifest 행 키와 행 ID가 일치하지 않습니다.")
    state = entry.after_state
    natural_key = entry.natural_key
    if not isinstance(state, dict) or set(state) != STATE_KEYS[entity_type]:
        raise _fail(f"{entry.entry_key} 저장값 열 구성이 올바르지 않습니다.")
    if not isinstance(natural_key, dict) or set(natural_key) != NATURAL_KEY_FIELDS[entity_type]:
        raise _fail(f"{entry.entry_key} 자연키 구성이 올바르지 않습니다.")
    if _uuid(state.get("id"), f"{entry.entry_key} id") != entry.row_id:
        raise _fail(f"{entry.entry_key} 저장 ID가 manifest ID와 다릅니다.")
    if _uuid(state.get("organization_id"), f"{entry.entry_key} 기관") != organization_id:
        raise _fail(f"{entry.entry_key} 기관이 실행기록과 다릅니다.")
    recomputed = _fingerprint(state)
    if not HEX_64.fullmatch(str(entry.after_fingerprint or "")) or not hmac.compare_digest(
        recomputed,
        str(entry.after_fingerprint),
    ):
        raise _fail(f"{entry.entry_key} 저장값 지문이 일치하지 않습니다.")
    for field in UUID_STATE_FIELDS & set(state):
        _uuid(
            state[field],
            f"{entry.entry_key} {field}",
            optional=field
            in {
                "business_unit_id",
                "department_unit_id",
                "floor_unit_id",
                "team_unit_id",
                "position_code_id",
                "source_batch_id",
            },
        )
    return _PreparedEntry(entry, dict(state), dict(natural_key))


def _validate_manifest(
    db: Session,
    run: StaffApplicationRun,
    expected_manifest_fingerprint: str,
) -> tuple[StaffSyncBatch, list[_PreparedEntry]]:
    if not HEX_64.fullmatch(expected_manifest_fingerprint):
        raise _fail("예상 manifest 지문 형식이 올바르지 않습니다.")
    if not hmac.compare_digest(run.manifest_fingerprint, expected_manifest_fingerprint):
        raise _fail("화면에서 확인한 manifest 지문과 저장된 실행기록이 다릅니다.")
    if run.manifest_schema_version != 1 or run.plan_schema_version != 2:
        raise _fail("지원하지 않는 직원 반영 manifest 버전입니다.")

    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == run.batch_id,
            StaffSyncBatch.organization_id == run.organization_id,
        )
    )
    if batch is None:
        raise _fail("원본 직원 동기화 배치를 찾을 수 없습니다.")
    entries = list(
        db.scalars(
            select(StaffApplicationEntry)
            .where(StaffApplicationEntry.run_id == run.id)
            .order_by(StaffApplicationEntry.ordinal)
        ).all()
    )
    if run.entry_count != EXPECTED_ENTRY_TOTAL or len(entries) != EXPECTED_ENTRY_TOTAL:
        raise _fail("직원 반영 manifest는 정확히 342행이어야 합니다.")
    if [entry.ordinal for entry in entries] != list(range(1, EXPECTED_ENTRY_TOTAL + 1)):
        raise _fail("manifest 행 순서가 연속적이지 않습니다.")
    if len({entry.entry_key for entry in entries}) != len(entries):
        raise _fail("manifest 행 키가 중복됩니다.")
    if len({entry.row_id for entry in entries}) != len(entries):
        raise _fail("manifest 행 ID가 중복됩니다.")
    counts = Counter(entry.entity_type for entry in entries)
    if dict(counts) != EXPECTED_ENTRY_COUNTS:
        raise _fail("manifest 종류별 행 수가 75·89·89·89와 다릅니다.")
    if [ENTITY_ORDER[entry.entity_type] for entry in entries] != sorted(
        ENTITY_ORDER[entry.entity_type] for entry in entries
    ):
        raise _fail("manifest 의존 행 순서가 올바르지 않습니다.")

    prepared = [
        _validate_entry_shape(entry, organization_id=run.organization_id)
        for entry in entries
    ]
    recomputed_manifest = _recomputed_manifest_fingerprint(run, batch, entries)
    if not hmac.compare_digest(recomputed_manifest, run.manifest_fingerprint):
        raise _fail("저장된 manifest 전체 지문을 다시 계산한 결과가 다릅니다.")
    return batch, prepared


def _validate_dependency_graph(entries: list[_PreparedEntry]) -> None:
    by_key = {item.stored.entry_key: item for item in entries}
    seen: set[str] = set()
    for item in entries:
        dependencies = list(item.stored.dependency_keys or [])
        if len(dependencies) != len(set(dependencies)) or any(
            key not in by_key or key not in seen for key in dependencies
        ):
            raise _fail(f"{item.stored.entry_key} manifest 의존관계가 올바르지 않습니다.")
        expected_types = {
            "staff": Counter(),
            "source_account": Counter({"staff": 1}),
            "service_period": Counter({"staff": 1, "source_account": 1}),
            "service_assignment": Counter(
                {"staff": 1, "source_account": 1, "service_period": 1}
            ),
        }[item.entity_type]
        actual_types = Counter(by_key[key].entity_type for key in dependencies)
        if actual_types != expected_types:
            raise _fail(f"{item.stored.entry_key} 의존 행 종류가 올바르지 않습니다.")
        seen.add(item.stored.entry_key)


def _validate_cross_references(
    db: Session,
    run: StaffApplicationRun,
    batch: StaffSyncBatch,
    entries: list[_PreparedEntry],
) -> None:
    grouped = {
        entity_type: [item for item in entries if item.entity_type == entity_type]
        for entity_type in EXPECTED_ENTRY_COUNTS
    }
    staff_by_id = {item.row_id: item for item in grouped["staff"]}
    account_by_id = {item.row_id: item for item in grouped["source_account"]}
    period_by_id = {item.row_id: item for item in grouped["service_period"]}

    staff_codes: set[str] = set()
    for item in grouped["staff"]:
        state = item.state
        code = str(state["internal_code"])
        if not code or item.natural_key["internal_code"] != code or code in staff_codes:
            raise _fail(f"{item.stored.entry_key} 직원번호 자연키가 올바르지 않습니다.")
        staff_codes.add(code)
        if (
            state["is_test_data"] is not False
            or state["is_active"] is not True
            or state["terminated_at"] is not None
            or state["deleted_at"] is not None
            or str(state["employment_status"]) not in EMPLOYMENT_STATUSES - {"unknown"}
        ):
            raise _fail(f"{item.stored.entry_key} 실제 직원 상태가 올바르지 않습니다.")

    account_keys: set[tuple[str, str, str]] = set()
    for item in grouped["source_account"]:
        state = item.state
        staff_id = _uuid(state["staff_id"], "원본계정 직원")
        service_type = str(state["service_type"])
        natural = (
            str(state["source_system"]),
            service_type,
            str(state["external_id"]),
        )
        if (
            staff_id not in staff_by_id
            or natural != (
                str(item.natural_key["source_system"]),
                str(item.natural_key["service_type"]),
                str(item.natural_key["external_id"]),
            )
            or natural in account_keys
            or natural[0] != "carefor"
            or service_type not in SERVICE_TYPES
        ):
            raise _fail(f"{item.stored.entry_key} 원본계정 연결이 올바르지 않습니다.")
        account_keys.add(natural)
        start = _date(state["latest_start_date"], "원본계정 시작일")
        end = _date(state["latest_end_date"], "원본계정 종료일", optional=True)
        if end is not None and end <= start:
            raise _fail(f"{item.stored.entry_key} 원본계정 기간이 올바르지 않습니다.")
        if str(state["employment_status"]) not in EMPLOYMENT_STATUSES:
            raise _fail(f"{item.stored.entry_key} 원본계정 재직상태가 올바르지 않습니다.")

    period_keys: set[tuple[UUID, date]] = set()
    open_periods: set[UUID] = set()
    for item in grouped["service_period"]:
        state = item.state
        staff_id = _uuid(state["staff_id"], "근무기간 직원")
        account_id = _uuid(state["source_account_id"], "근무기간 원본계정")
        start = _date(state["start_date"], "근무기간 시작일")
        end = _date(state["end_date"], "근무기간 종료일", optional=True)
        account = account_by_id.get(account_id)
        if (
            staff_id not in staff_by_id
            or account is None
            or _uuid(account.state["staff_id"], "원본계정 직원") != staff_id
            or _uuid(item.natural_key["source_account_id"], "근무기간 자연키")
            != account_id
            or _date(item.natural_key["start_date"], "근무기간 자연키") != start
            or (account_id, start) in period_keys
            or (end is not None and end <= start)
        ):
            raise _fail(f"{item.stored.entry_key} 근무기간 연결이 올바르지 않습니다.")
        if end is None and account_id in open_periods:
            raise _fail("같은 원본계정에 열린 근무기간이 중복됩니다.")
        period_keys.add((account_id, start))
        if end is None:
            open_periods.add(account_id)

    assignment_keys: set[tuple[UUID, date]] = set()
    open_assignments: set[tuple[UUID, str]] = set()
    unit_references: dict[UUID, str] = {}
    job_codes: set[str] = set()
    position_ids: set[UUID] = set()
    for item in grouped["service_assignment"]:
        state = item.state
        staff_id = _uuid(state["staff_id"], "서비스배정 직원")
        account_id = _uuid(state["source_account_id"], "서비스배정 원본계정")
        period_id = _uuid(state["service_period_id"], "서비스배정 근무기간")
        start = _date(state["start_date"], "서비스배정 시작일")
        end = _date(state["end_date"], "서비스배정 종료일", optional=True)
        service_type = str(state["service_type"])
        account = account_by_id.get(account_id)
        period = period_by_id.get(period_id)
        if (
            staff_id not in staff_by_id
            or account is None
            or period is None
            or _uuid(account.state["staff_id"], "원본계정 직원") != staff_id
            or str(account.state["service_type"]) != service_type
            or _uuid(period.state["staff_id"], "근무기간 직원") != staff_id
            or _uuid(period.state["source_account_id"], "근무기간 원본계정")
            != account_id
            or _date(period.state["start_date"], "근무기간 시작일") != start
            or _date(period.state["end_date"], "근무기간 종료일", optional=True)
            != end
            or _uuid(item.natural_key["service_period_id"], "서비스배정 자연키")
            != period_id
            or _date(item.natural_key["start_date"], "서비스배정 자연키") != start
            or (period_id, start) in assignment_keys
            or service_type not in SERVICE_TYPES
            or str(state["assignment_basis"]) not in ASSIGNMENT_BASES
            or _uuid(state["source_batch_id"], "서비스배정 원본 배치") != batch.id
            or state["is_test_data"] is not False
            or (end is not None and end <= start)
        ):
            raise _fail(f"{item.stored.entry_key} 서비스배정 연결이 올바르지 않습니다.")
        if end is None and (staff_id, service_type) in open_assignments:
            raise _fail("같은 직원·서비스의 열린 현재배정이 중복됩니다.")
        assignment_keys.add((period_id, start))
        if end is None:
            open_assignments.add((staff_id, service_type))
        for field, expected_type in (
            ("business_unit_id", "business"),
            ("department_unit_id", "department"),
            ("floor_unit_id", "floor"),
            ("team_unit_id", "team"),
        ):
            unit_id = _uuid(state[field], field, optional=field != "business_unit_id")
            if unit_id is not None:
                unit_references[unit_id] = expected_type
        if state["job_code"]:
            job_codes.add(str(state["job_code"]))
        position_id = _uuid(state["position_code_id"], "직위코드", optional=True)
        if position_id is not None:
            position_ids.add(position_id)

    units = {
        unit.id: unit
        for unit in db.scalars(select(OrgUnit).where(OrgUnit.id.in_(unit_references))).all()
    }
    for unit_id, expected_type in unit_references.items():
        unit = units.get(unit_id)
        if (
            unit is None
            or unit.organization_id != run.organization_id
            or unit.unit_type != expected_type
            or not unit.is_active
            or unit.is_test_data
        ):
            raise _fail(f"실제 {expected_type} 조직 연결이 올바르지 않습니다.")
    jobs = {
        job.code: job
        for job in db.scalars(select(StaffJobCode).where(StaffJobCode.code.in_(job_codes))).all()
    }
    if any(code not in jobs or not jobs[code].is_active for code in job_codes):
        raise _fail("서비스배정 직종코드가 현재 직종표와 다릅니다.")
    positions = {
        position.id: position
        for position in db.scalars(
            select(StaffPositionCode).where(StaffPositionCode.id.in_(position_ids))
        ).all()
    }
    if any(
        position_id not in positions
        or positions[position_id].organization_id != run.organization_id
        or not positions[position_id].is_active
        for position_id in position_ids
    ):
        raise _fail("서비스배정 직위코드가 현재 직위표와 다릅니다.")

    _validate_database_collisions(
        db,
        run.organization_id,
        grouped,
        staff_codes,
        account_keys,
        period_keys,
        open_periods,
        assignment_keys,
        open_assignments,
    )


def _validate_database_collisions(
    db: Session,
    organization_id: UUID,
    grouped: dict[str, list[_PreparedEntry]],
    staff_codes: set[str],
    account_keys: set[tuple[str, str, str]],
    period_keys: set[tuple[UUID, date]],
    open_periods: set[UUID],
    assignment_keys: set[tuple[UUID, date]],
    open_assignments: set[tuple[UUID, str]],
) -> None:
    model_by_entity = {
        "staff": Staff,
        "source_account": StaffSourceAccount,
        "service_period": StaffServicePeriod,
        "service_assignment": StaffServiceAssignment,
    }
    for entity_type, model in model_by_entity.items():
        ids = [item.row_id for item in grouped[entity_type]]
        if db.scalar(select(model.id).where(model.id.in_(ids)).limit(1)) is not None:
            raise _fail(f"{entity_type} manifest 행 ID가 이미 존재합니다.")
    if db.scalar(
        select(Staff.id)
        .where(
            Staff.organization_id == organization_id,
            Staff.internal_code.in_(staff_codes),
        )
        .limit(1)
    ) is not None:
        raise _fail("manifest 직원번호가 이미 존재합니다.")
    if account_keys and db.scalar(
        select(StaffSourceAccount.id)
        .where(
            StaffSourceAccount.organization_id == organization_id,
            tuple_(
                StaffSourceAccount.source_system,
                StaffSourceAccount.service_type,
                StaffSourceAccount.external_id,
            ).in_(account_keys),
        )
        .limit(1)
    ) is not None:
        raise _fail("케어포 원본계정 자연키가 이미 존재합니다.")
    if period_keys and db.scalar(
        select(StaffServicePeriod.id)
        .where(
            tuple_(
                StaffServicePeriod.source_account_id,
                StaffServicePeriod.start_date,
            ).in_(period_keys)
        )
        .limit(1)
    ) is not None:
        raise _fail("근무기간 자연키가 이미 존재합니다.")
    if open_periods and db.scalar(
        select(StaffServicePeriod.id)
        .where(
            StaffServicePeriod.source_account_id.in_(open_periods),
            StaffServicePeriod.end_date.is_(None),
        )
        .limit(1)
    ) is not None:
        raise _fail("원본계정에 열린 근무기간이 이미 존재합니다.")
    if assignment_keys and db.scalar(
        select(StaffServiceAssignment.id)
        .where(
            tuple_(
                StaffServiceAssignment.service_period_id,
                StaffServiceAssignment.start_date,
            ).in_(assignment_keys)
        )
        .limit(1)
    ) is not None:
        raise _fail("서비스배정 자연키가 이미 존재합니다.")
    if open_assignments and db.scalar(
        select(StaffServiceAssignment.id)
        .where(
            tuple_(
                StaffServiceAssignment.staff_id,
                StaffServiceAssignment.service_type,
            ).in_(open_assignments),
            StaffServiceAssignment.end_date.is_(None),
        )
        .limit(1)
    ) is not None:
        raise _fail("직원·서비스의 열린 현재배정이 이미 존재합니다.")


def _insert_manifest_rows(
    db: Session,
    entries: list[_PreparedEntry],
    *,
    applied_by_id: UUID,
) -> dict[str, int]:
    grouped = {
        entity_type: [item for item in entries if item.entity_type == entity_type]
        for entity_type in EXPECTED_ENTRY_COUNTS
    }
    for item in grouped["staff"]:
        state = item.state
        db.add(
            Staff(
                id=item.row_id,
                organization_id=_uuid(state["organization_id"], "직원 기관"),
                internal_code=str(state["internal_code"]),
                display_name=str(state["display_name"]),
                job_title=str(state["job_title"]),
                position_title=state["position_title"],
                employment_status=str(state["employment_status"]),
                is_test_data=False,
                is_active=True,
                terminated_at=None,
                deleted_at=None,
            )
        )
    db.flush()

    for item in grouped["source_account"]:
        state = item.state
        db.add(
            StaffSourceAccount(
                id=item.row_id,
                organization_id=_uuid(state["organization_id"], "원본계정 기관"),
                staff_id=_uuid(state["staff_id"], "원본계정 직원"),
                source_system=str(state["source_system"]),
                service_type=str(state["service_type"]),
                external_id=str(state["external_id"]),
                display_name_snapshot=str(state["display_name_snapshot"]),
                job_name_snapshot=str(state["job_name_snapshot"]),
                employment_status=str(state["employment_status"]),
                source_status=str(state["source_status"]),
                latest_start_date=_date(state["latest_start_date"], "원본계정 시작일"),
                latest_end_date=_date(
                    state["latest_end_date"],
                    "원본계정 종료일",
                    optional=True,
                ),
                is_active=bool(state["is_active"]),
            )
        )
    db.flush()

    for item in grouped["service_period"]:
        state = item.state
        db.add(
            StaffServicePeriod(
                id=item.row_id,
                organization_id=_uuid(state["organization_id"], "근무기간 기관"),
                staff_id=_uuid(state["staff_id"], "근무기간 직원"),
                source_account_id=_uuid(
                    state["source_account_id"],
                    "근무기간 원본계정",
                ),
                start_date=_date(state["start_date"], "근무기간 시작일"),
                end_date=_date(state["end_date"], "근무기간 종료일", optional=True),
                employment_status=str(state["employment_status"]),
            )
        )
    db.flush()

    for item in grouped["service_assignment"]:
        state = item.state
        db.add(
            StaffServiceAssignment(
                id=item.row_id,
                organization_id=_uuid(state["organization_id"], "서비스배정 기관"),
                staff_id=_uuid(state["staff_id"], "서비스배정 직원"),
                source_account_id=_uuid(
                    state["source_account_id"],
                    "서비스배정 원본계정",
                ),
                service_period_id=_uuid(
                    state["service_period_id"],
                    "서비스배정 근무기간",
                ),
                service_type=str(state["service_type"]),
                business_unit_id=_uuid(state["business_unit_id"], "사업부"),
                department_unit_id=_uuid(
                    state["department_unit_id"],
                    "부서",
                    optional=True,
                ),
                floor_unit_id=_uuid(state["floor_unit_id"], "층", optional=True),
                team_unit_id=_uuid(state["team_unit_id"], "팀", optional=True),
                job_code=str(state["job_code"]) if state["job_code"] else None,
                job_title_snapshot=state["job_title_snapshot"],
                position_code_id=_uuid(
                    state["position_code_id"],
                    "직위코드",
                    optional=True,
                ),
                position_title_snapshot=state["position_title_snapshot"],
                start_date=_date(state["start_date"], "서비스배정 시작일"),
                end_date=_date(
                    state["end_date"],
                    "서비스배정 종료일",
                    optional=True,
                ),
                assignment_basis=str(state["assignment_basis"]),
                source_batch_id=_uuid(state["source_batch_id"], "원본 배치"),
                note=str(state["note"]),
                is_test_data=False,
                created_by=applied_by_id,
                updated_by=applied_by_id,
            )
        )
    db.flush()
    return dict(EXPECTED_ENTRY_COUNTS)


def apply_staff_application_run(
    db: Session,
    *,
    run_id: UUID,
    expected_manifest_fingerprint: str,
    applied_by_id: UUID,
) -> StaffApplicationExecutionResult:
    """Apply one immutable 342-row create-only manifest without committing.

    The caller owns the outer transaction and must commit only after any
    surrounding operational checks pass. All writes made by this function are
    enclosed in a savepoint so an insertion or flush failure leaves no partial
    staff rows behind.
    """

    if db.new or db.dirty or db.deleted:
        raise _fail("직원 반영 전 세션에 저장되지 않은 다른 변경이 없어야 합니다.")
    run = db.scalar(
        select(StaffApplicationRun)
        .where(StaffApplicationRun.id == run_id)
        .with_for_update()
    )
    if run is None:
        raise _fail("직원 반영 실행기록을 찾을 수 없습니다.")
    actor = db.get(User, applied_by_id)
    if (
        actor is None
        or actor.organization_id != run.organization_id
        or not actor.is_active
        or actor.role != "admin"
    ):
        raise _fail("같은 기관의 활성 관리자만 직원 반영을 실행할 수 있습니다.")

    batch, entries = _validate_manifest(db, run, expected_manifest_fingerprint)
    _verify_evidence(db, run)
    _validate_dependency_graph(entries)
    _validate_cross_references(db, run, batch, entries)

    audit_id = uuid4()
    try:
        with db.begin_nested():
            created_counts = _insert_manifest_rows(
                db,
                entries,
                applied_by_id=applied_by_id,
            )
            run.status = "stale"
            run.updated_at = utcnow()
            db.add(
                AuditEvent(
                    id=audit_id,
                    organization_id=run.organization_id,
                    actor_id=applied_by_id,
                    action="staff.application_applied",
                    target_type="staff_application_run",
                    target_id=run.id,
                    details={
                        "batch_id": str(run.batch_id),
                        "manifest_fingerprint": run.manifest_fingerprint,
                        "created_counts": created_counts,
                        "login_accounts_created": 0,
                        "room_memberships_created": 0,
                    },
                    is_test_data=settings.environment != "production",
                )
            )
            db.flush()
    except StaffApplicationExecutionError:
        raise
    except (SQLAlchemyError, ValueError, TypeError, RuntimeError) as exc:
        raise _fail("직원정보 저장 중 오류가 발생해 전체 적용을 취소했습니다.") from exc

    return StaffApplicationExecutionResult(
        run_id=run.id,
        batch_id=run.batch_id,
        manifest_fingerprint=run.manifest_fingerprint,
        created_counts=created_counts,
        audit_event_id=audit_id,
    )
