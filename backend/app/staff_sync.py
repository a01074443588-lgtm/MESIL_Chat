from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from hashlib import sha256
from hmac import new as new_hmac
import json
from secrets import token_bytes
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    OrgUnit,
    Staff,
    StaffJobAssignment,
    StaffJobCode,
    StaffOrganizationAssignment,
    StaffPositionCode,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    User,
)
from .organization_catalog import (
    APPROVED_ORGANIZATION_CATALOG_VERSION,
    approved_organization_catalog_fingerprint,
)
from .staff_access_policy import (
    APPROVED_LEAVE_ACCESS_POLICY_VERSION,
    approved_leave_access_policy_fingerprint,
    approved_leave_access_policy_snapshot,
)


MAX_STAFF_ROWS = 1_000
SERVICE_ALIASES = {
    "facility": "facility",
    "facility_care": "facility",
    "시설": "facility",
    "요양원": "facility",
    "daycare": "daycare",
    "day_care": "daycare",
    "주간": "daycare",
    "주간보호": "daycare",
    "homecare": "homecare",
    "home_care": "homecare",
    "방문": "homecare",
    "방문요양": "homecare",
}
FIELD_ALIASES = {
    "external_id": ("external_id", "carefor_id", "staff_id", "employee_id", "id"),
    "display_name": ("display_name", "staff_name", "employee_name", "name", "성명"),
    "service_type": ("service_type", "service", "source", "급여종류"),
    "source_status": ("status", "employment_status", "carefor_status", "재직상태"),
    "job_name": ("job_name", "job_title", "role", "직종"),
    "birth_date": ("birth_date", "birth", "stmbsdt", "생년월일"),
    "gender": ("gender", "sex", "성별"),
    "start_date": ("start_date", "hire_date", "hireDate", "입사일"),
    "end_date": ("end_date", "retire_date", "retireDate", "퇴사일"),
    "is_active": ("is_active", "active"),
}
SERVICE_BUSINESS_NAMES = {
    "facility": "시설",
    "daycare": "주간보호",
    "homecare": "방문요양",
}
SERVICE_TEAM_NAMES: dict[str, str] = {}
DEPARTMENT_BY_JOB_CODE = {
    "dietitian": "영양",
    "cook": "영양",
    "sanitation_worker": "영양",
    "social_worker": "복지",
    "registered_nurse": "의료",
    "nursing_assistant": "의료",
    "physical_therapist": "의료",
    "occupational_therapist": "의료",
    "caregiver": "요양",
    "contract_doctor": "의료",
}
JOB_NAME_ALIASES = {
    "시설장(관리책임자)": "시설장",
}
FACILITY_DIRECTOR_SOURCE_NAMES = frozenset({"시설장", *JOB_NAME_ALIASES})
SERVICE_ORDER = {"facility": 0, "daycare": 1, "homecare": 2}


class StaffSyncError(ValueError):
    pass


def _value(row: dict[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            return row[alias]
    return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_generated_at(value: Any) -> datetime | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise StaffSyncError("generated_at은 ISO 날짜·시간 형식이어야 합니다.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_date(value: Any, label: str, issues: list[str]) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        issues.append(f"{label} '{raw}'의 날짜 형식을 확인해 주세요.")
        return None


def _parse_private_date(value: Any, label: str, issues: list[str]) -> str | None:
    raw = _text(value)
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10]).isoformat()
    except ValueError:
        issues.append(f"{label} 날짜 형식을 확인해 주세요.")
        return None


def _parse_boolean(value: Any) -> bool | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = _text(value).lower().replace(" ", "")
    if normalized in {"1", "true", "yes", "y", "active", "재직", "활성"}:
        return True
    if normalized in {"0", "false", "no", "n", "inactive", "퇴사", "종료", "비활성"}:
        return False
    return None


def _employment_status(
    source_status: str,
    active_value: Any,
    issues: list[str],
) -> str:
    normalized = source_status.lower().replace(" ", "").replace("_", "")
    active = _parse_boolean(active_value)
    if normalized in {"재직", "근무", "active", "employed", "employment"}:
        status = "active"
    elif normalized in {"휴직", "leave", "onleave", "휴가중"}:
        status = "leave"
    elif normalized in {"퇴사", "퇴직", "retired", "terminated", "inactive", "종료"}:
        status = "retired"
    elif not normalized and active is not None:
        status = "active" if active else "retired"
    else:
        status = "unknown"
        issues.append(f"재직상태 값 '{source_status or active_value}'을(를) 해석할 수 없습니다.")
    if active is not None and status == "active" and not active:
        issues.append("재직상태는 재직이지만 활성 여부는 중지로 되어 있습니다.")
    return status


def parse_staff_roster_file(content: bytes) -> tuple[datetime | None, list[dict[str, Any]]]:
    try:
        payload = json.loads(content.decode("utf-8-sig"))
    except UnicodeDecodeError as exc:
        raise StaffSyncError("직원 명단 파일은 UTF-8 형식이어야 합니다.") from exc
    except json.JSONDecodeError as exc:
        raise StaffSyncError(f"직원 명단 JSON 형식이 올바르지 않습니다. ({exc.lineno}행)") from exc
    if isinstance(payload, list):
        rows = payload
        generated_at = None
    elif isinstance(payload, dict):
        rows = payload.get("staff") or payload.get("employees") or payload.get("data")
        generated_at = _parse_generated_at(
            payload.get("generated_at") or payload.get("source_generated_at")
        )
    else:
        rows = None
        generated_at = None
    if not isinstance(rows, list) or not rows:
        raise StaffSyncError(
            "직원 명단이 비어 있습니다. 빈 명단은 퇴사로 오인될 수 있어 미리보기를 만들지 않습니다."
        )
    if len(rows) > MAX_STAFF_ROWS:
        raise StaffSyncError(f"한 번에 최대 {MAX_STAFF_ROWS}명까지만 미리볼 수 있습니다.")
    if any(not isinstance(row, dict) for row in rows):
        raise StaffSyncError("직원 명단의 각 항목은 열 이름과 값으로 구성되어야 합니다.")
    return generated_at, rows


def _normalize_row(
    row: dict[str, Any],
    row_number: int,
    identity_salt: bytes,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    external_id = _text(_value(row, "external_id"))
    if not external_id:
        external_id = f"__INVALID_ROW_{row_number:04d}"
        issues.append("케어포 외부 ID가 없습니다.")
    elif len(external_id) > 80:
        issues.append("케어포 외부 ID는 80자 이하여야 합니다.")

    display_name = _text(_value(row, "display_name"))
    if not display_name:
        issues.append("직원 이름이 없습니다.")
    elif len(display_name) > 100:
        issues.append("직원 이름은 100자 이하여야 합니다.")

    service_raw = _text(_value(row, "service_type"))
    service_type = SERVICE_ALIASES.get(service_raw.lower())
    if service_type is None:
        issues.append("급여종별은 시설·주간보호·방문요양 중 하나여야 합니다.")

    source_status = _text(_value(row, "source_status"))
    employment_status = _employment_status(
        source_status,
        _value(row, "is_active"),
        issues,
    )
    start_date = _parse_date(_value(row, "start_date"), "입사일", issues)
    end_date = _parse_date(_value(row, "end_date"), "퇴사일", issues)
    birth_date = _parse_private_date(
        _value(row, "birth_date"),
        "생년월일",
        issues,
    )
    if start_date and end_date and end_date <= start_date:
        issues.append("퇴사일은 입사일 다음 날 이후여야 합니다.")
    if employment_status == "retired" and not end_date:
        issues.append("퇴사 상태에는 퇴사일이 필요합니다.")

    normalized_service = service_type or service_raw
    source_key = f"{normalized_service}:{external_id}"
    identity_name = _identity_name(display_name)
    identity_token = (
        new_hmac(
            identity_salt,
            f"{identity_name}|{birth_date}".encode("utf-8"),
            sha256,
        ).hexdigest()
        if identity_name and birth_date
        else None
    )
    return (
        {
            "source_key": source_key,
            "external_id": external_id,
            "display_name": display_name,
            "service_type": normalized_service,
            "job_name": _text(_value(row, "job_name")),
            "employment_status": employment_status,
            "source_status": source_status,
            "start_date": start_date,
            "end_date": end_date,
            "identity_token": identity_token,
            "identity_evidence": "name_birth" if identity_token else "name_only",
        },
        issues,
    )


def _account_snapshot(
    account: StaffSourceAccount,
    all_accounts: list[StaffSourceAccount],
) -> dict[str, Any]:
    staff = account.staff
    other_active = [
        item
        for item in all_accounts
        if item.staff_id == account.staff_id
        and item.id != account.id
        and item.is_active
        and item.employment_status == "active"
    ]
    return {
        "source_key": f"{account.service_type}:{account.external_id}",
        "external_id": account.external_id,
        "display_name": account.display_name_snapshot,
        "service_type": account.service_type,
        "job_name": account.job_name_snapshot,
        "employment_status": account.employment_status,
        "source_status": account.source_status,
        "start_date": account.latest_start_date.isoformat() if account.latest_start_date else None,
        "end_date": account.latest_end_date.isoformat() if account.latest_end_date else None,
        "staff_name": staff.display_name if staff else None,
        "staff_employment_status": staff.employment_status if staff else None,
        "other_active_source_count": len(other_active),
        "other_active_services": sorted({item.service_type for item in other_active}),
    }


def _account_matches(account: StaffSourceAccount, incoming: dict[str, Any]) -> bool:
    return (
        account.display_name_snapshot == incoming["display_name"]
        and account.job_name_snapshot == incoming["job_name"]
        and account.employment_status == incoming["employment_status"]
        and account.source_status == incoming["source_status"]
        and (
            account.latest_start_date.isoformat() if account.latest_start_date else None
        )
        == incoming["start_date"]
        and (
            account.latest_end_date.isoformat() if account.latest_end_date else None
        )
        == incoming["end_date"]
    )


def build_staff_preview_entries(
    db: Session,
    organization_id: UUID,
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    identity_salt = token_bytes(32)
    normalized_rows = [
        _normalize_row(row, index, identity_salt)
        for index, row in enumerate(rows, start=1)
    ]
    active_identity_tokens = {
        incoming["identity_token"]
        for incoming, _ in normalized_rows
        if incoming.get("identity_token")
        and incoming.get("employment_status") in {"active", "leave"}
    }
    valid_source_keys = [
        incoming["source_key"]
        for incoming, _ in normalized_rows
        if not incoming["external_id"].startswith("__INVALID_ROW_")
    ]
    duplicate_keys = {
        source_key
        for source_key, count in Counter(valid_source_keys).items()
        if count > 1
    }

    accounts = list(
        db.scalars(
            select(StaffSourceAccount).where(
                StaffSourceAccount.organization_id == organization_id,
                StaffSourceAccount.source_system == "carefor",
            )
        ).all()
    )
    accounts_by_key = {
        f"{account.service_type}:{account.external_id}": account
        for account in accounts
    }
    staff_rows = list(
        db.scalars(
            select(Staff).where(
                Staff.organization_id == organization_id,
                Staff.deleted_at.is_(None),
            )
        ).all()
    )
    staff_by_name: dict[str, list[Staff]] = {}
    for staff in staff_rows:
        if staff.is_test_data:
            continue
        staff_by_name.setdefault(staff.display_name, []).append(staff)

    entries: list[dict[str, Any]] = []
    emitted_keys: set[str] = set()
    for incoming, issues in normalized_rows:
        source_key = incoming["source_key"]
        if source_key in emitted_keys:
            continue
        emitted_keys.add(source_key)
        if source_key in duplicate_keys:
            issues.append("같은 급여종별과 케어포 외부 ID가 명단에 두 번 이상 있습니다.")
        account = accounts_by_key.get(source_key)
        current_snapshot = _account_snapshot(account, accounts) if account else None
        current_staff_id = account.staff_id if account else None

        if issues:
            change_type, item_status = "conflict", "blocked"
            conflict_reason = " ".join(issues)
        elif account and (account.staff is None or account.staff.deleted_at is not None):
            change_type, item_status = "conflict", "blocked"
            conflict_reason = "케어포 계정에 연결된 직원정보가 없거나 삭제 상태입니다."
        elif account and _account_matches(account, incoming):
            change_type, item_status = "unchanged", "not_required"
            conflict_reason = None
        elif account:
            change_type = {
                "leave": "leave",
                "retired": "retire",
            }.get(incoming["employment_status"], "update")
            item_status, conflict_reason = "pending", None
        else:
            same_name_staff = staff_by_name.get(incoming["display_name"], [])
            if same_name_staff:
                change_type, item_status = "conflict", "blocked"
                conflict_reason = (
                    "같은 이름의 MESIL 직원이 있지만 케어포 외부 ID가 연결되지 않았습니다. "
                    "이름만으로 자동 연결하지 않았습니다."
                )
            elif incoming["employment_status"] == "retired":
                if incoming.get("identity_token") in active_identity_tokens:
                    change_type, item_status, conflict_reason = "retire", "pending", None
                else:
                    change_type, item_status = "conflict", "blocked"
                    conflict_reason = "퇴사 이력을 연결할 기존 MESIL 직원 또는 케어포 계정이 없습니다."
            elif incoming["employment_status"] == "leave":
                change_type, item_status, conflict_reason = "leave", "pending", None
            else:
                change_type, item_status, conflict_reason = "new", "pending", None

        entries.append(
            {
                "source_key": source_key,
                "service_type": incoming["service_type"],
                "external_id": incoming["external_id"],
                "change_type": change_type,
                "status": item_status,
                "current_staff_id": current_staff_id,
                "incoming_payload": incoming,
                "current_snapshot": current_snapshot,
                "conflict_reason": conflict_reason,
            }
        )

    summary = dict(Counter(entry["change_type"] for entry in entries))
    for key in ("new", "update", "leave", "retire", "unchanged", "conflict"):
        summary.setdefault(key, 0)
    summary["total"] = len(entries)
    summary["actionable"] = sum(
        summary[key] for key in ("new", "update", "leave", "retire")
    )
    summary["missing_not_retired"] = sum(
        1
        for account in accounts
        if f"{account.service_type}:{account.external_id}" not in set(valid_source_keys)
    )
    return entries, summary


def _identity_name(value: Any) -> str:
    return "".join(_text(value).split()).casefold()


def _organization_proposal(
    *,
    unit_type: str,
    proposed_name: str | None,
    units_by_type_and_name: dict[tuple[str, str], list[OrgUnit]],
    reason: str,
    parent_unit_id: UUID | None = None,
) -> dict[str, Any]:
    if proposed_name is None:
        return {
            "unit_type": unit_type,
            "proposed_name": None,
            "unit_id": None,
            "match_status": "not_proposed",
            "catalog_is_test_data": None,
            "reason": reason,
        }
    matches = units_by_type_and_name.get((unit_type, proposed_name), [])
    if parent_unit_id is not None:
        matches = [unit for unit in matches if unit.parent_unit_id == parent_unit_id]
    real_matches = [unit for unit in matches if not unit.is_test_data]
    reference_matches = [unit for unit in matches if unit.is_test_data]
    if len(real_matches) > 1 or (not real_matches and len(reference_matches) != 1):
        return {
            "unit_type": unit_type,
            "proposed_name": proposed_name,
            "unit_id": None,
            "match_status": "missing" if not matches else "ambiguous",
            "catalog_is_test_data": None,
            "reason": (
                f"현재 조직표에서 {proposed_name} {unit_type} 항목을 하나로 확정할 수 없습니다."
            ),
        }
    if not real_matches:
        return {
            "unit_type": unit_type,
            "proposed_name": proposed_name,
            "unit_id": None,
            "match_status": "reference_only",
            "catalog_is_test_data": True,
            "reason": "개발 시험 조직표의 이름만 참고하며 실제 조직 ID로 확정하지 않습니다.",
        }
    unit = real_matches[0]
    return {
        "unit_type": unit_type,
        "proposed_name": proposed_name,
        "unit_id": unit.id,
        "match_status": "matched",
        "catalog_is_test_data": False,
        "reason": reason,
    }


def build_staff_assignment_preview(
    db: Session,
    organization_id: UUID,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a read-only person and assignment proposal from stored sync items."""

    units = list(
        db.scalars(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization_id,
                OrgUnit.is_active.is_(True),
            )
        ).all()
    )
    units_by_type_and_name: dict[tuple[str, str], list[OrgUnit]] = {}
    for unit in units:
        units_by_type_and_name.setdefault((unit.unit_type, unit.name), []).append(unit)

    jobs = list(
        db.scalars(select(StaffJobCode).where(StaffJobCode.is_active.is_(True))).all()
    )
    jobs_by_name = {job.name: job for job in jobs}
    entries_by_name: dict[str, list[dict[str, Any]]] = {}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        incoming = entry.get("incoming_payload") or {}
        identity_name = _identity_name(incoming.get("display_name"))
        if not identity_name:
            grouped[f"missing:{entry.get('source_key', '')}"] = [entry]
            continue
        entries_by_name.setdefault(identity_name, []).append(entry)

    for identity_name, name_entries in entries_by_name.items():
        if any(
            not (entry.get("incoming_payload") or {}).get("identity_token")
            for entry in name_entries
        ):
            grouped[f"name:{identity_name}"] = name_entries
            continue
        for entry in name_entries:
            identity_token = str(
                (entry.get("incoming_payload") or {}).get("identity_token")
            )
            grouped.setdefault(f"identity:{identity_token}", []).append(entry)

    candidate_name_counts = Counter(
        _identity_name(
            ((group_entries[0].get("incoming_payload") or {}).get("display_name"))
        )
        for group_entries in grouped.values()
    )

    candidates: list[dict[str, Any]] = []
    for identity_group_key, group_entries in grouped.items():
        group_entries.sort(
            key=lambda item: (
                SERVICE_ORDER.get(str(item.get("service_type")), 99),
                str(item.get("external_id", "")),
            )
        )
        display_name = _text(
            (group_entries[0].get("incoming_payload") or {}).get("display_name")
        )
        identity_name = _identity_name(display_name)
        identity_tokens = {
            str(token)
            for item in group_entries
            if (token := (item.get("incoming_payload") or {}).get("identity_token"))
        }
        has_verified_identity = (
            len(identity_tokens) == 1
            and all(
                (item.get("incoming_payload") or {}).get("identity_evidence")
                == "name_birth"
                for item in group_entries
            )
        )
        linked_staff_ids = {
            str(item["current_staff_id"])
            for item in group_entries
            if item.get("current_staff_id") is not None
        }
        has_unlinked_account = any(
            item.get("current_staff_id") is None for item in group_entries
        )
        has_blocked_account = any(
            item.get("status") == "blocked" or item.get("change_type") == "conflict"
            for item in group_entries
        )
        requires_identity_confirmation = len(group_entries) > 1 and (
            len(linked_staff_ids) > 1
            or (
                not has_verified_identity
                and (has_unlinked_account or len(linked_staff_ids) != 1)
            )
        )

        account_proposals: list[dict[str, Any]] = []
        candidate_notes: list[str] = []
        for entry in group_entries:
            incoming = entry.get("incoming_payload") or {}
            service_type = str(entry.get("service_type") or incoming.get("service_type") or "")
            employment_status = str(incoming.get("employment_status", "unknown"))
            assignment_required = employment_status != "retired"
            source_job_name = _text(incoming.get("job_name"))
            business_name = SERVICE_BUSINESS_NAMES.get(service_type)
            business = _organization_proposal(
                unit_type="business",
                proposed_name=business_name,
                units_by_type_and_name=units_by_type_and_name,
                reason="케어포 급여종별을 기준으로 제안했습니다.",
            )
            business_unit_id = (
                UUID(str(business["unit_id"])) if business.get("unit_id") else None
            )

            manual_generic_job = source_job_name == "기타"
            normalized_job_name = JOB_NAME_ALIASES.get(
                source_job_name,
                source_job_name,
            )
            matched_job = (
                None
                if manual_generic_job
                else jobs_by_name.get(normalized_job_name)
            )
            if manual_generic_job:
                job = {
                    "source_name": source_job_name,
                    "code": None,
                    "proposed_name": None,
                    "match_status": "manual",
                    "required_for_review": True,
                    "reason": "기타 표기만으로 실제 직종을 알 수 없어 관리자가 확인해야 합니다.",
                }
            elif matched_job is None:
                job = {
                    "source_name": source_job_name,
                    "code": None,
                    "proposed_name": None,
                    "match_status": "missing",
                    "required_for_review": True,
                    "reason": "현재 MESIL 직종표에서 같은 이름의 활성 직종을 찾지 못했습니다.",
                }
            else:
                job = {
                    "source_name": source_job_name,
                    "code": matched_job.code,
                    "proposed_name": matched_job.name,
                    "match_status": "matched",
                    "required_for_review": False,
                    "reason": (
                        "케어포 직종명을 MESIL 시설장 직종으로 정규화했습니다."
                        if normalized_job_name != source_job_name
                        else "케어포 직종명과 MESIL 직종명이 정확히 같습니다."
                    ),
                }

            department_name = (
                DEPARTMENT_BY_JOB_CODE.get(matched_job.code) if matched_job else None
            )
            if matched_job is not None and matched_job.code == "driver_assistant":
                department = {
                    "unit_type": "department",
                    "proposed_name": None,
                    "unit_id": None,
                    "match_status": "not_proposed",
                    "catalog_is_test_data": None,
                    "reason": "운전원은 서비스 사업부로 구분하며 부서를 억지로 지정하지 않습니다.",
                }
            elif matched_job is not None and department_name is None:
                department = {
                    "unit_type": "department",
                    "proposed_name": None,
                    "unit_id": None,
                    "match_status": "manual",
                    "catalog_is_test_data": None,
                    "reason": "이 직종의 기본 부서는 정해져 있지 않아 관리자가 확인해야 합니다.",
                }
            elif matched_job is None:
                department = {
                    "unit_type": "department",
                    "proposed_name": None,
                    "unit_id": None,
                    "match_status": "manual",
                    "catalog_is_test_data": None,
                    "reason": "직종을 먼저 확인한 뒤 부서를 선택해야 합니다.",
                }
            else:
                department = _organization_proposal(
                    unit_type="department",
                    proposed_name=department_name,
                    units_by_type_and_name=units_by_type_and_name,
                    reason="MESIL 기본 직종-부서표를 기준으로 제안했습니다.",
                    parent_unit_id=business_unit_id,
                )

            team_name = SERVICE_TEAM_NAMES.get(service_type)
            team = _organization_proposal(
                unit_type="team",
                proposed_name=team_name,
                units_by_type_and_name=units_by_type_and_name,
                reason=(
                    "케어포 급여종별을 기준으로 제안했습니다."
                    if team_name
                    else "승인 조직표에는 서비스별 팀을 두지 않아 제안하지 않습니다."
                ),
                parent_unit_id=business_unit_id,
            )
            floor = _organization_proposal(
                unit_type="floor",
                proposed_name=None,
                units_by_type_and_name=units_by_type_and_name,
                reason="케어포 직원 명단에 근무층 정보가 없어 제안하지 않습니다.",
                parent_unit_id=business_unit_id,
            )

            position = {
                "proposed_name": None,
                "position_id": None,
                "match_status": "not_proposed",
                "manual_confirmation": False,
                "reason": "케어포 직종만으로 별도 직위를 추정하지 않습니다.",
            }

            blocking_org_statuses = {"missing", "ambiguous", "manual"}
            assignment_ready = (
                entry.get("status") != "blocked"
                and entry.get("change_type") != "conflict"
                and (
                    not assignment_required
                    or (
                        business["match_status"] not in blocking_org_statuses
                        and department["match_status"] not in blocking_org_statuses
                        and team["match_status"]
                        not in {"missing", "ambiguous", "manual"}
                        and not job["required_for_review"]
                        and not position["manual_confirmation"]
                    )
                )
            )
            notes: list[str] = []
            if not assignment_required:
                notes.append(
                    "퇴사한 과거 계정은 이력만 보존하며 현재 조직·직종·직위 배정 대상에서 제외합니다."
                )
            else:
                if business["match_status"] == "reference_only":
                    notes.append("사업부는 개발 시험 조직표의 이름만 참고합니다.")
                if department["match_status"] in blocking_org_statuses:
                    notes.append(department["reason"])
                if job["match_status"] != "matched":
                    notes.append(job["reason"])
                if position["manual_confirmation"]:
                    notes.append(position["reason"])
            if entry.get("conflict_reason"):
                notes.append(str(entry["conflict_reason"]))

            account_proposals.append(
                {
                    "source_key": str(entry.get("source_key", "")),
                    "external_id": str(entry.get("external_id", "")),
                    "service_type": service_type,
                    "employment_status": employment_status,
                    "source_status": _text(incoming.get("source_status")),
                    "source_job_name": source_job_name,
                    "assignment_required": assignment_required,
                    "assignment_ready": assignment_ready,
                    "organizations": [business, department, team, floor],
                    "job": job,
                    "position": position,
                    "notes": notes,
                }
            )

        requires_assignment_confirmation = any(
            account["assignment_required"] and not account["assignment_ready"]
            for account in account_proposals
        )
        if requires_identity_confirmation:
            candidate_notes.append(
                "생년 확인 근거가 부족한 같은 이름 계정입니다. 동일인 여부만 먼저 판단해 주세요."
            )
        elif len(group_entries) > 1 and has_verified_identity:
            candidate_notes.append(
                "이름과 생년월일이 일치해 같은 사람의 서비스별 계정으로 묶었습니다."
            )
        if candidate_name_counts[identity_name] > 1 and has_verified_identity:
            candidate_notes.append(
                "동명이지만 생년월일이 달라 별도 인물로 분리했습니다."
            )
        if requires_assignment_confirmation:
            candidate_notes.append("조직·직종·직위 중 관리자가 정할 항목이 있습니다.")
        if has_blocked_account:
            review_status = "blocked"
        elif requires_identity_confirmation:
            review_status = "identity_review"
        elif requires_assignment_confirmation:
            review_status = "assignment_review"
        else:
            review_status = "proposal_ready"

        if has_blocked_account:
            identity_status = "data_conflict"
        elif len(group_entries) > 1 and has_verified_identity:
            identity_status = "verified_identity"
        elif len(group_entries) > 1 and not requires_identity_confirmation:
            identity_status = "linked_accounts"
        elif len(group_entries) > 1:
            identity_status = "confirmation_required"
        elif linked_staff_ids:
            identity_status = "linked_account"
        else:
            identity_status = "single_account"

        candidates.append(
            {
                "candidate_key": "person-"
                + sha256(identity_group_key.encode("utf-8")).hexdigest()[:16],
                "display_name": display_name,
                "identity_evidence": (
                    "name_birth" if has_verified_identity else "name_only"
                ),
                "same_name_candidate_count": candidate_name_counts[identity_name],
                "identity_status": identity_status,
                "review_status": review_status,
                "account_count": len(account_proposals),
                "requires_identity_confirmation": requires_identity_confirmation,
                "requires_assignment_confirmation": requires_assignment_confirmation,
                "linked_staff_ids": sorted(linked_staff_ids),
                "accounts": account_proposals,
                "notes": candidate_notes,
            }
        )

    candidates.sort(key=lambda item: (_identity_name(item["display_name"]), item["candidate_key"]))
    summary = {
        "account_count": len(entries),
        "candidate_count": len(candidates),
        "proposal_ready": sum(
            item["review_status"] == "proposal_ready" for item in candidates
        ),
        "identity_review": sum(
            item["review_status"] == "identity_review" for item in candidates
        ),
        "assignment_review": sum(
            item["review_status"] == "assignment_review" for item in candidates
        ),
        "blocked": sum(item["review_status"] == "blocked" for item in candidates),
        "multi_account_groups": sum(item["account_count"] > 1 for item in candidates),
        "assignment_confirmation_candidates": sum(
            item["requires_assignment_confirmation"] for item in candidates
        ),
        "director_accounts": sum(
            account["source_job_name"] in FACILITY_DIRECTOR_SOURCE_NAMES
            for item in candidates
            for account in item["accounts"]
        ),
        "history_only_accounts": sum(
            not account["assignment_required"]
            for item in candidates
            for account in item["accounts"]
        ),
    }
    return {"summary": summary, "candidates": candidates}


def normalize_staff_review_draft(
    db: Session,
    organization_id: UUID,
    candidate: dict[str, Any],
    decision: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Validate and normalize one review-only decision without applying it."""

    accounts = candidate.get("accounts") or []
    source_order = [str(account.get("source_key", "")) for account in accounts]
    source_keys = set(source_order)
    if not source_order or "" in source_keys:
        raise StaffSyncError("검토 후보의 계정 자료가 올바르지 않습니다.")

    identity_decision = str(decision.get("identity_decision") or "")
    requires_identity = bool(candidate.get("requires_identity_confirmation"))
    if requires_identity:
        allowed_identity_decisions = {
            "pending",
            "same_person",
            "separate_people",
            "custom_groups",
        }
    else:
        allowed_identity_decisions = {"not_required"}
    if identity_decision not in allowed_identity_decisions:
        raise StaffSyncError("이 후보에 맞는 동일인 확인값이 아닙니다.")

    raw_groups = [dict(group) for group in decision.get("person_groups") or []]
    if identity_decision == "pending":
        if raw_groups:
            raise StaffSyncError("동일인 확인 전에는 사람 묶음을 지정할 수 없습니다.")
    elif identity_decision == "not_required" and not raw_groups:
        raw_groups = [
            {
                "source_keys": source_order,
                "primary_source_key": None,
                "primary_job_code": None,
                "primary_position_title": None,
            }
        ]
    elif identity_decision == "separate_people" and not raw_groups:
        raw_groups = [
            {
                "source_keys": [source_key],
                "primary_source_key": None,
                "primary_job_code": None,
                "primary_position_title": None,
            }
            for source_key in source_order
        ]

    flattened_source_keys = [
        str(source_key)
        for group in raw_groups
        for source_key in group.get("source_keys") or []
    ]
    if identity_decision != "pending":
        if len(flattened_source_keys) != len(set(flattened_source_keys)):
            raise StaffSyncError("한 계정이 여러 사람 묶음에 중복되어 있습니다.")
        if set(flattened_source_keys) != source_keys:
            raise StaffSyncError("사람 묶음에는 후보의 모든 계정이 한 번씩 포함되어야 합니다.")
        if identity_decision == "same_person" and (
            len(raw_groups) != 1 or len(flattened_source_keys) != len(source_order)
        ):
            raise StaffSyncError("같은 사람 결정은 모든 계정을 한 묶음으로 지정해야 합니다.")
        if identity_decision == "separate_people" and (
            len(raw_groups) != len(source_order)
            or any(len(group.get("source_keys") or []) != 1 for group in raw_groups)
        ):
            raise StaffSyncError("서로 다른 사람 결정은 계정마다 한 묶음으로 지정해야 합니다.")
        if identity_decision == "not_required" and (
            len(raw_groups) != 1
            or set(raw_groups[0].get("source_keys") or []) != source_keys
        ):
            raise StaffSyncError("동일인 확인이 필요 없는 후보의 사람 묶음이 올바르지 않습니다.")
        if identity_decision == "custom_groups" and (
            len(source_order) < 3
            or len(raw_groups) < 2
            or len(raw_groups) >= len(source_order)
            or not any(len(group.get("source_keys") or []) > 1 for group in raw_groups)
        ):
            raise StaffSyncError("일부 동일인 결정의 계정 묶음을 다시 확인해 주세요.")

    active_jobs = {
        job.code: job
        for job in db.scalars(
            select(StaffJobCode).where(StaffJobCode.is_active.is_(True))
        ).all()
    }
    active_units = list(
        db.scalars(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization_id,
                OrgUnit.is_active.is_(True),
            )
        ).all()
    )
    all_active_departments = {
        unit.name for unit in active_units if unit.unit_type == "department"
    }
    active_departments_by_service: dict[str, set[str]] = {}
    for service_type, business_name in SERVICE_BUSINESS_NAMES.items():
        business_matches = [
            unit
            for unit in active_units
            if unit.unit_type == "business"
            and unit.name == business_name
            and not unit.is_test_data
        ]
        if len(business_matches) == 1:
            active_departments_by_service[service_type] = {
                unit.name
                for unit in active_units
                if unit.unit_type == "department"
                and not unit.is_test_data
                and unit.parent_unit_id == business_matches[0].id
            }
        else:
            active_departments_by_service[service_type] = set(
                all_active_departments
            )
    active_positions = {
        position.name
        for position in db.scalars(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == organization_id,
                StaffPositionCode.is_active.is_(True),
            )
        ).all()
    }
    proposed_positions = {
        str(account["source_key"]): account.get("position", {}).get("proposed_name")
        for account in accounts
    }

    issues: list[str] = []
    if identity_decision == "pending":
        issues.append("같은 이름 계정의 동일인 여부를 선택해 주세요.")

    normalized_groups: list[dict[str, Any]] = []
    source_index = {source_key: index for index, source_key in enumerate(source_order)}
    for group in raw_groups:
        group_source_keys = sorted(
            [str(source_key) for source_key in group.get("source_keys") or []],
            key=source_index.__getitem__,
        )
        normalized_groups.append(
            {
                "source_keys": group_source_keys,
                "primary_source_key": None,
                "primary_job_code": None,
                "primary_position_title": None,
            }
        )
    normalized_groups.sort(
        key=lambda group: min(source_index[key] for key in group["source_keys"])
        if group["source_keys"]
        else len(source_order)
    )
    identity_structure_ready = not requires_identity or identity_decision != "pending"

    raw_assignments = {
        str(assignment.get("source_key", "")): dict(assignment)
        for assignment in decision.get("account_assignments") or []
    }
    if "" in raw_assignments or not set(raw_assignments).issubset(source_keys):
        raise StaffSyncError("검토 후보에 속하지 않은 계정 배정값이 있습니다.")

    service_labels = {
        "facility": "시설",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }
    normalized_assignments: list[dict[str, Any]] = []
    for account in accounts:
        source_key = str(account["source_key"])
        assignment = raw_assignments.get(source_key, {})
        assignment_required = bool(
            account.get(
                "assignment_required",
                account.get("employment_status") != "retired",
            )
        )
        department_name = assignment.get("department_name") or None
        job_code = assignment.get("job_code") or None
        position_title = assignment.get("position_title") or None
        service_type = str(account.get("service_type"))
        label = service_labels.get(service_type, "해당 서비스")
        active_departments = active_departments_by_service.get(
            service_type, all_active_departments
        )

        department = next(
            (
                proposal
                for proposal in account.get("organizations") or []
                if proposal.get("unit_type") == "department"
            ),
            {},
        )
        if not identity_structure_ready or not assignment_required:
            department_name = None
            job_code = None
            position_title = None

        if identity_structure_ready and job_code is not None and department_name is None:
            default_department = DEPARTMENT_BY_JOB_CODE.get(job_code)
            if default_department in active_departments:
                department_name = default_department

        if department_name is not None and department_name not in active_departments:
            raise StaffSyncError("선택한 부서는 현재 기관의 사용 중인 부서가 아닙니다.")
        if job_code is not None and job_code not in active_jobs:
            raise StaffSyncError("선택한 실제 자격 직종은 현재 사용 중인 직종이 아닙니다.")
        allowed_positions = set(active_positions)
        proposed_position = proposed_positions.get(source_key)
        if proposed_position:
            allowed_positions.add(str(proposed_position))
        if position_title is not None and position_title not in allowed_positions:
            raise StaffSyncError("선택한 직위는 현재 기관의 직위 후보가 아닙니다.")

        department_required = (
            identity_structure_ready
            and assignment_required
            and department.get("match_status") == "manual"
            and account.get("job", {}).get("match_status") == "matched"
        )
        job_required = (
            identity_structure_ready
            and assignment_required
            and bool(
                account.get("job", {}).get(
                    "required_for_review",
                    account.get("job", {}).get("match_status") != "matched",
                )
            )
        )
        position_required = (
            identity_structure_ready
            and assignment_required
            and bool(account.get("position", {}).get("manual_confirmation"))
        )
        if department_required and department_name is None:
            issues.append(f"{label} 계정의 부서를 선택해 주세요.")
        if job_required and job_code is None:
            issues.append(f"{label} 계정의 실제 자격 직종을 선택해 주세요.")
        if position_required and position_title is None:
            issues.append(f"{label} 계정의 직위를 선택해 주세요.")

        normalized_assignments.append(
            {
                "source_key": source_key,
                "department_name": department_name,
                "job_code": job_code,
                "position_title": position_title,
            }
        )

    unique_issues = list(dict.fromkeys(issues))
    normalized_payload = {
        "identity_decision": identity_decision,
        "person_groups": normalized_groups,
        "account_assignments": normalized_assignments,
        "note": str(decision.get("note") or "").strip(),
    }
    return normalized_payload, unique_issues


def _stable_plan_value(value: Any) -> Any:
    """Convert plan inputs to a deterministic, JSON-serializable value."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    if isinstance(value, dict):
        return {
            str(key): _stable_plan_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, set):
        return sorted(
            (_stable_plan_value(item) for item in value),
            key=lambda item: json.dumps(item, ensure_ascii=False, sort_keys=True),
        )
    if isinstance(value, (list, tuple)):
        return [_stable_plan_value(item) for item in value]
    if isinstance(value, (UUID, date, datetime)):
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    return value


def _planned_staff_code(organization_id: UUID, source_keys: list[str]) -> str:
    material = f"{organization_id}|{'|'.join(sorted(source_keys))}"
    return "CARE-" + sha256(material.encode("utf-8")).hexdigest()[:12].upper()


def _planned_manifest_id(
    organization_id: UUID,
    entity_type: str,
    natural_key: str,
) -> UUID:
    return uuid5(
        NAMESPACE_URL,
        f"mesil-chat|{organization_id}|{entity_type}|{natural_key}",
    )


def _manifest_state_fingerprint(value: dict[str, Any] | None) -> str | None:
    if value is None:
        return None
    canonical = json.dumps(
        _stable_plan_value(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _build_creation_only_change_entries(
    db: Session,
    organization_id: UUID,
    *,
    batch_id: UUID,
    entries: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
    candidate_plans: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    """Build exact create rows for the currently approved first import slice.

    The first real Carefor import intentionally supports only new Staff and new
    source/history/assignment rows. If current data later requires updates, this
    function returns ``supported=False`` so a run cannot be prepared from an
    incomplete rollback manifest.
    """

    plans_by_candidate = {
        str(plan["candidate_key"]): plan for plan in candidate_plans
    }
    for plan in candidate_plans:
        if plan.get("outcome") != "prepared":
            return [], False
        for person in plan.get("person_plans") or []:
            source_count = len(person.get("source_keys") or [])
            if (
                person.get("staff_action") != "create"
                or person.get("source_account_actions", {}).get("create")
                != source_count
                or person.get("service_period_actions", {}).get("create")
                != source_count
                or person.get("service_assignment_actions", {}).get("create")
                != source_count
                or any(
                    person.get(field, {}).get(action, 0)
                    for field in (
                        "source_account_actions",
                        "service_period_actions",
                        "service_assignment_actions",
                    )
                    for action in ("update", "no_change", "blocked")
                )
            ):
                return [], False

    entry_by_source = {str(entry["source_key"]): entry for entry in entries}
    jobs_by_code = {
        row.code: row for row in db.scalars(select(StaffJobCode)).all()
    }
    positions_by_name = {
        row.name: row
        for row in db.scalars(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == organization_id,
                StaffPositionCode.is_active.is_(True),
            )
        ).all()
    }
    units = list(
        db.scalars(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization_id,
                OrgUnit.is_active.is_(True),
                OrgUnit.is_test_data.is_(False),
            )
        ).all()
    )
    business_by_service: dict[str, OrgUnit] = {}
    for service_type, business_name in SERVICE_BUSINESS_NAMES.items():
        matches = [
            unit
            for unit in units
            if unit.unit_type == "business" and unit.name == business_name
        ]
        if len(matches) != 1:
            return [], False
        business_by_service[service_type] = matches[0]

    change_entries: list[dict[str, Any]] = []

    def append_create(
        *,
        entity_type: str,
        row_id: UUID,
        natural_key: dict[str, Any],
        after_state: dict[str, Any],
        dependency_keys: list[str],
    ) -> str:
        entry_key = f"{entity_type}:{row_id}"
        change_entries.append(
            {
                "entry_key": entry_key,
                "entity_type": entity_type,
                "action": "create",
                "row_id": str(row_id),
                "natural_key": _stable_plan_value(natural_key),
                "before_state": None,
                "after_state": _stable_plan_value(after_state),
                "before_fingerprint": None,
                "after_fingerprint": _manifest_state_fingerprint(after_state),
                "dependency_keys": dependency_keys,
            }
        )
        return entry_key

    for candidate in candidates:
        candidate_key = str(candidate["candidate_key"])
        candidate_plan = plans_by_candidate.get(candidate_key)
        if candidate_plan is None:
            return [], False
        accounts_by_source = {
            str(account["source_key"]): account
            for account in candidate.get("accounts") or []
        }
        draft = _stable_plan_value(candidate.get("review_draft"))
        reviewed_assignments = {
            str(item["source_key"]): item
            for item in (draft or {}).get("account_assignments") or []
        }

        for person in candidate_plan.get("person_plans") or []:
            source_keys = sorted(str(key) for key in person.get("source_keys") or [])
            planned_code = str(person["planned_internal_code"])
            staff_id = _planned_manifest_id(
                organization_id,
                "staff",
                planned_code,
            )
            primary_job_title = ""
            for source_key in source_keys:
                account = accounts_by_source.get(source_key) or {}
                reviewed = reviewed_assignments.get(source_key) or {}
                job_code = reviewed.get("job_code") or (account.get("job") or {}).get(
                    "code"
                )
                job = jobs_by_code.get(str(job_code)) if job_code else None
                if job is not None:
                    primary_job_title = job.name
                    break
            staff_entry_key = append_create(
                entity_type="staff",
                row_id=staff_id,
                natural_key={"internal_code": planned_code},
                after_state={
                    "id": staff_id,
                    "organization_id": organization_id,
                    "internal_code": planned_code,
                    "display_name": str(candidate.get("display_name") or ""),
                    "job_title": primary_job_title,
                    "position_title": None,
                    "employment_status": person["employment_status"],
                    "is_test_data": False,
                    "is_active": True,
                    "terminated_at": None,
                    "deleted_at": None,
                },
                dependency_keys=[],
            )

            for source_key in source_keys:
                entry = entry_by_source.get(source_key)
                account = accounts_by_source.get(source_key)
                if entry is None or account is None:
                    return [], False
                incoming = entry.get("incoming_payload") or {}
                service_type = str(account.get("service_type") or "")
                external_id = str(account.get("external_id") or "")
                start_date = incoming.get("start_date")
                if service_type not in business_by_service or not start_date:
                    return [], False

                account_id = _planned_manifest_id(
                    organization_id,
                    "staff_source_account",
                    f"carefor|{service_type}|{external_id}",
                )
                account_entry_key = append_create(
                    entity_type="source_account",
                    row_id=account_id,
                    natural_key={
                        "source_system": "carefor",
                        "service_type": service_type,
                        "external_id": external_id,
                    },
                    after_state={
                        "id": account_id,
                        "organization_id": organization_id,
                        "staff_id": staff_id,
                        "source_system": "carefor",
                        "service_type": service_type,
                        "external_id": external_id,
                        "display_name_snapshot": incoming.get("display_name") or "",
                        "job_name_snapshot": incoming.get("job_name") or "",
                        "employment_status": incoming.get("employment_status")
                        or "unknown",
                        "source_status": incoming.get("source_status") or "",
                        "latest_start_date": start_date,
                        "latest_end_date": incoming.get("end_date"),
                        "is_active": incoming.get("employment_status") != "retired",
                    },
                    dependency_keys=[staff_entry_key],
                )

                period_id = _planned_manifest_id(
                    organization_id,
                    "staff_service_period",
                    f"{account_id}|{start_date}",
                )
                period_entry_key = append_create(
                    entity_type="service_period",
                    row_id=period_id,
                    natural_key={
                        "source_account_id": account_id,
                        "start_date": start_date,
                    },
                    after_state={
                        "id": period_id,
                        "organization_id": organization_id,
                        "staff_id": staff_id,
                        "source_account_id": account_id,
                        "start_date": start_date,
                        "end_date": incoming.get("end_date"),
                        "employment_status": incoming.get("employment_status")
                        or "unknown",
                    },
                    dependency_keys=[staff_entry_key, account_entry_key],
                )

                organizations = {
                    str(item.get("unit_type")): item
                    for item in account.get("organizations") or []
                }
                business = business_by_service[service_type]
                business_unit_id = organizations.get("business", {}).get("unit_id")
                if str(business_unit_id or "") != str(business.id):
                    return [], False
                reviewed = reviewed_assignments.get(source_key) or {}
                department_unit_id = organizations.get("department", {}).get("unit_id")
                reviewed_department = reviewed.get("department_name")
                if reviewed_department:
                    department_matches = [
                        unit
                        for unit in units
                        if unit.unit_type == "department"
                        and unit.name == str(reviewed_department)
                        and unit.parent_unit_id == business.id
                    ]
                    if len(department_matches) != 1:
                        return [], False
                    department_unit_id = department_matches[0].id

                job_code = reviewed.get("job_code") or (account.get("job") or {}).get(
                    "code"
                )
                job = jobs_by_code.get(str(job_code)) if job_code else None
                position_title = reviewed.get("position_title") or (
                    account.get("position") or {}
                ).get("proposed_name")
                position = positions_by_name.get(str(position_title)) if position_title else None
                assignment_id = _planned_manifest_id(
                    organization_id,
                    "staff_service_assignment",
                    f"{period_id}|{start_date}",
                )
                append_create(
                    entity_type="service_assignment",
                    row_id=assignment_id,
                    natural_key={
                        "service_period_id": period_id,
                        "start_date": start_date,
                    },
                    after_state={
                        "id": assignment_id,
                        "organization_id": organization_id,
                        "staff_id": staff_id,
                        "source_account_id": account_id,
                        "service_period_id": period_id,
                        "service_type": service_type,
                        "business_unit_id": business.id,
                        "department_unit_id": department_unit_id,
                        "floor_unit_id": organizations.get("floor", {}).get("unit_id"),
                        "team_unit_id": organizations.get("team", {}).get("unit_id"),
                        "job_code": job.code if job else None,
                        "job_title_snapshot": job.name if job else None,
                        "position_code_id": position.id if position else None,
                        "position_title_snapshot": str(position_title)
                        if position_title
                        else None,
                        "start_date": start_date,
                        "end_date": incoming.get("end_date"),
                        "assignment_basis": "admin_confirmed"
                        if candidate_plan.get("decision_source") == "saved_review"
                        else "carefor_auto",
                        "source_batch_id": batch_id,
                        "note": "",
                        "is_test_data": False,
                    },
                    dependency_keys=[
                        staff_entry_key,
                        account_entry_key,
                        period_entry_key,
                    ],
                )

    entity_order = {
        "staff": 0,
        "source_account": 1,
        "service_period": 2,
        "service_assignment": 3,
    }
    change_entries.sort(
        key=lambda item: (
            entity_order[str(item["entity_type"])],
            json.dumps(item["natural_key"], ensure_ascii=False, sort_keys=True),
        )
    )
    return change_entries, True


def _staff_application_state_snapshot(
    db: Session,
    organization_id: UUID,
) -> dict[str, Any]:
    """Return only fields that can change the application plan.

    The snapshot is hashed before it leaves this module. It intentionally omits
    usernames, password hashes, and other login secrets.
    """

    staff_rows = list(
        db.scalars(
            select(Staff).where(Staff.organization_id == organization_id)
        ).all()
    )
    source_rows = list(
        db.scalars(
            select(StaffSourceAccount).where(
                StaffSourceAccount.organization_id == organization_id
            )
        ).all()
    )
    period_rows = list(
        db.scalars(
            select(StaffServicePeriod).where(
                StaffServicePeriod.organization_id == organization_id
            )
        ).all()
    )
    service_assignment_rows = list(
        db.scalars(
            select(StaffServiceAssignment).where(
                StaffServiceAssignment.organization_id == organization_id
            )
        ).all()
    )
    unit_rows = list(
        db.scalars(
            select(OrgUnit).where(OrgUnit.organization_id == organization_id)
        ).all()
    )
    organization_assignment_rows = list(
        db.scalars(
            select(StaffOrganizationAssignment).where(
                StaffOrganizationAssignment.organization_id == organization_id
            )
        ).all()
    )
    job_assignment_rows = list(
        db.scalars(
            select(StaffJobAssignment).where(
                StaffJobAssignment.organization_id == organization_id
            )
        ).all()
    )
    position_rows = list(
        db.scalars(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == organization_id
            )
        ).all()
    )
    user_rows = list(
        db.scalars(select(User).where(User.organization_id == organization_id)).all()
    )
    job_rows = list(db.scalars(select(StaffJobCode)).all())

    return {
        "staff": sorted(
            [
                {
                    "id": row.id,
                    "internal_code": row.internal_code,
                    "display_name": row.display_name,
                    "job_title": row.job_title,
                    "position_title": row.position_title,
                    "employment_status": row.employment_status,
                    "is_test_data": row.is_test_data,
                    "is_active": row.is_active,
                    "terminated_at": row.terminated_at,
                    "deleted_at": row.deleted_at,
                }
                for row in staff_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "source_accounts": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "source_system": row.source_system,
                    "service_type": row.service_type,
                    "external_id": row.external_id,
                    "display_name_snapshot": row.display_name_snapshot,
                    "job_name_snapshot": row.job_name_snapshot,
                    "employment_status": row.employment_status,
                    "source_status": row.source_status,
                    "latest_start_date": row.latest_start_date,
                    "latest_end_date": row.latest_end_date,
                    "is_active": row.is_active,
                }
                for row in source_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "service_periods": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "source_account_id": row.source_account_id,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "employment_status": row.employment_status,
                }
                for row in period_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "service_assignments": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "source_account_id": row.source_account_id,
                    "service_period_id": row.service_period_id,
                    "service_type": row.service_type,
                    "business_unit_id": row.business_unit_id,
                    "department_unit_id": row.department_unit_id,
                    "floor_unit_id": row.floor_unit_id,
                    "team_unit_id": row.team_unit_id,
                    "job_code": row.job_code,
                    "job_title_snapshot": row.job_title_snapshot,
                    "position_code_id": row.position_code_id,
                    "position_title_snapshot": row.position_title_snapshot,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "assignment_basis": row.assignment_basis,
                    "source_batch_id": row.source_batch_id,
                    "is_test_data": row.is_test_data,
                }
                for row in service_assignment_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "organization_units": sorted(
            [
                {
                    "id": row.id,
                    "parent_unit_id": row.parent_unit_id,
                    "unit_type": row.unit_type,
                    "internal_code": row.internal_code,
                    "name": row.name,
                    "is_active": row.is_active,
                    "is_test_data": row.is_test_data,
                }
                for row in unit_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "organization_assignments": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "unit_id": row.unit_id,
                    "unit_type": row.unit_type,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "is_test_data": row.is_test_data,
                }
                for row in organization_assignment_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "job_assignments": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "job_code": row.job_code,
                    "job_title": row.job_title,
                    "position_title": row.position_title,
                    "start_date": row.start_date,
                    "end_date": row.end_date,
                    "is_primary": row.is_primary,
                }
                for row in job_assignment_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "job_codes": sorted(
            [
                {
                    "code": row.code,
                    "name": row.name,
                    "is_active": row.is_active,
                }
                for row in job_rows
            ],
            key=lambda row: row["code"],
        ),
        "position_codes": sorted(
            [
                {
                    "id": row.id,
                    "internal_code": row.internal_code,
                    "name": row.name,
                    "is_active": row.is_active,
                }
                for row in position_rows
            ],
            key=lambda row: str(row["id"]),
        ),
        "users": sorted(
            [
                {
                    "id": row.id,
                    "staff_id": row.staff_id,
                    "is_active": row.is_active,
                    "can_process_records": row.can_process_records,
                    "must_change_password": row.must_change_password,
                }
                for row in user_rows
            ],
            key=lambda row: str(row["id"]),
        ),
    }


def build_staff_application_plan(
    db: Session,
    organization_id: UUID,
    *,
    batch_id: UUID,
    file_sha256: str,
    batch_status: str,
    entries: list[dict[str, Any]],
    candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a deterministic, read-only plan. This function never writes."""

    existing_staff = {
        row.id: row
        for row in db.scalars(
            select(Staff).where(Staff.organization_id == organization_id)
        ).all()
    }
    staff_by_code = {row.internal_code: row for row in existing_staff.values()}
    existing_accounts = {
        (row.service_type, row.external_id): row
        for row in db.scalars(
            select(StaffSourceAccount).where(
                StaffSourceAccount.organization_id == organization_id,
                StaffSourceAccount.source_system == "carefor",
            )
        ).all()
    }
    existing_periods: dict[tuple[UUID, date], StaffServicePeriod] = {}
    open_periods_by_account: dict[UUID, list[StaffServicePeriod]] = {}
    for row in db.scalars(
        select(StaffServicePeriod).where(
            StaffServicePeriod.organization_id == organization_id
        )
    ).all():
        existing_periods[(row.source_account_id, row.start_date)] = row
        if row.end_date is None:
            open_periods_by_account.setdefault(row.source_account_id, []).append(row)
    existing_service_assignments: dict[
        tuple[UUID, date], StaffServiceAssignment
    ] = {}
    open_assignments_by_staff_service: dict[
        tuple[UUID, str], list[StaffServiceAssignment]
    ] = {}
    for row in db.scalars(
        select(StaffServiceAssignment).where(
            StaffServiceAssignment.organization_id == organization_id
        )
    ).all():
        existing_service_assignments[(row.service_period_id, row.start_date)] = row
        if row.end_date is None:
            open_assignments_by_staff_service.setdefault(
                (row.staff_id, row.service_type), []
            ).append(row)

    planned_period_closures: set[UUID] = set()
    planned_assignment_closures: set[UUID] = set()

    units = list(
        db.scalars(
            select(OrgUnit).where(OrgUnit.organization_id == organization_id)
        ).all()
    )
    test_units = [row for row in units if row.is_active and row.is_test_data]
    real_units_by_type_and_name: dict[tuple[str, str], list[OrgUnit]] = {}
    for row in units:
        if row.is_active and not row.is_test_data:
            real_units_by_type_and_name.setdefault(
                (row.unit_type, row.name), []
            ).append(row)
    real_business_by_service: dict[str, OrgUnit] = {}
    for service_type, business_name in SERVICE_BUSINESS_NAMES.items():
        matches = real_units_by_type_and_name.get(("business", business_name), [])
        if len(matches) == 1:
            real_business_by_service[service_type] = matches[0]
    jobs_by_code = {
        row.code: row
        for row in db.scalars(select(StaffJobCode)).all()
    }
    positions_by_name = {
        row.name: row
        for row in db.scalars(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == organization_id
            )
        ).all()
    }

    entry_by_source = {str(entry["source_key"]): entry for entry in entries}
    summary = {
        "account_count": len(entries),
        "candidate_count": len(candidates),
        "person_count": 0,
        "auto_decision_count": 0,
        "complete_review_count": 0,
        "incomplete_review_count": 0,
        "staff_create": 0,
        "staff_update": 0,
        "staff_no_change": 0,
        "source_account_create": 0,
        "source_account_update": 0,
        "source_account_no_change": 0,
        "source_account_blocked": 0,
        "service_period_create": 0,
        "service_period_update": 0,
        "service_period_no_change": 0,
        "service_period_blocked": 0,
        "service_assignment_create": 0,
        "service_assignment_update": 0,
        "service_assignment_no_change": 0,
        "service_assignment_blocked": 0,
        "current_assignment_accounts": 0,
        "history_only_accounts": 0,
        "active_people": 0,
        "leave_people": 0,
        "retired_people": 0,
        "prepared_candidates": 0,
        "needs_design_candidates": 0,
        "blocked_candidates": 0,
        "login_account_changes": 0,
        "room_membership_changes": 0,
        "existing_test_staff_changes": 0,
    }
    candidate_plans: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate_key = str(candidate["candidate_key"])
        accounts = [dict(account) for account in candidate.get("accounts") or []]
        review_status = str(candidate.get("review_status") or "blocked")
        raw_draft = candidate.get("review_draft")
        draft = _stable_plan_value(raw_draft) if raw_draft is not None else None
        decision_source = "blocked_source"
        person_groups: list[dict[str, Any]] = []
        reviewed_assignments_by_source: dict[str, dict[str, Any]] = {}
        candidate_blockers: list[str] = []

        if review_status == "proposal_ready":
            decision_source = "auto_proposal"
            person_groups = [
                {"source_keys": [str(account["source_key"]) for account in accounts]}
            ]
            summary["auto_decision_count"] += 1
        elif review_status in {"identity_review", "assignment_review"}:
            if draft is None:
                decision_source = "missing_review"
                candidate_blockers.append("review_decision_missing")
                summary["incomplete_review_count"] += 1
            elif draft.get("completion_status") != "complete":
                decision_source = "saved_review"
                candidate_blockers.append("review_decision_incomplete")
                summary["incomplete_review_count"] += 1
            else:
                decision_source = "saved_review"
                try:
                    normalized_draft, current_issues = normalize_staff_review_draft(
                        db,
                        organization_id,
                        candidate,
                        draft,
                    )
                except StaffSyncError:
                    candidate_blockers.append("review_decision_invalid")
                    summary["incomplete_review_count"] += 1
                else:
                    if current_issues:
                        candidate_blockers.append("review_decision_incomplete")
                        summary["incomplete_review_count"] += 1
                    else:
                        person_groups = [
                            dict(group)
                            for group in normalized_draft.get("person_groups") or []
                        ]
                        reviewed_assignments_by_source = {
                            str(assignment["source_key"]): dict(assignment)
                            for assignment in normalized_draft.get(
                                "account_assignments"
                            )
                            or []
                        }
                        summary["complete_review_count"] += 1
        else:
            candidate_blockers.append("source_data_conflict")

        person_plans: list[dict[str, Any]] = []
        for person_index, group in enumerate(person_groups):
            source_keys = sorted(str(key) for key in group.get("source_keys") or [])
            group_accounts = [
                account for account in accounts if str(account["source_key"]) in source_keys
            ]
            person_blockers: list[str] = []
            if not source_keys or len(group_accounts) != len(source_keys):
                person_blockers.append("review_group_invalid")

            linked_staff_ids = sorted(
                {
                    str(entry_by_source[key].get("current_staff_id"))
                    for key in source_keys
                    if key in entry_by_source
                    and entry_by_source[key].get("current_staff_id") is not None
                }
            )
            if len(linked_staff_ids) > 1:
                person_blockers.append("multiple_linked_staff")
            target_staff_id = (
                linked_staff_ids[0] if len(linked_staff_ids) == 1 else None
            )
            target_staff = (
                existing_staff.get(UUID(target_staff_id)) if target_staff_id else None
            )
            if target_staff_id is not None and target_staff is None:
                person_blockers.append("target_staff_missing")
            elif target_staff is not None:
                if target_staff.deleted_at is not None:
                    person_blockers.append("target_staff_deleted")
                if target_staff.is_test_data:
                    person_blockers.append("target_staff_test_data")

            current_accounts = [
                account
                for account in group_accounts
                if str(account.get("employment_status")) != "retired"
            ]
            history_accounts = [
                account
                for account in group_accounts
                if str(account.get("employment_status")) == "retired"
            ]
            active_accounts = [
                account
                for account in current_accounts
                if str(account.get("employment_status")) == "active"
            ]
            leave_accounts = [
                account
                for account in current_accounts
                if str(account.get("employment_status")) == "leave"
            ]
            if active_accounts:
                employment_status = "active"
            elif leave_accounts:
                employment_status = "leave"
            else:
                employment_status = "retired"
                person_blockers.append("history_only_person")

            service_types = sorted(
                {str(account.get("service_type") or "") for account in current_accounts}
            )
            current_service_counts = Counter(
                str(account.get("service_type") or "")
                for account in current_accounts
            )
            if any(count > 1 for count in current_service_counts.values()):
                person_blockers.append("multiple_current_service_accounts")
            parsed_start_dates: dict[str, date | None] = {}
            for account in group_accounts:
                source_key = str(account.get("source_key"))
                incoming = entry_by_source.get(source_key, {}).get(
                    "incoming_payload"
                ) or {}
                start_date_raw = incoming.get("start_date")
                try:
                    parsed_start_dates[source_key] = (
                        date.fromisoformat(str(start_date_raw))
                        if start_date_raw
                        else None
                    )
                except ValueError:
                    parsed_start_dates[source_key] = None
                if parsed_start_dates[source_key] is None:
                    person_blockers.append("invalid_service_period")

            for account in group_accounts:
                source_key = str(account.get("source_key"))
                start_date = parsed_start_dates.get(source_key)
                incoming = entry_by_source.get(source_key, {}).get(
                    "incoming_payload"
                ) or {}
                is_current_period = not incoming.get("end_date")
                service_type = str(account.get("service_type") or "")
                external_id = str(account.get("external_id") or "")
                existing_account = existing_accounts.get((service_type, external_id))
                if existing_account is None or start_date is None:
                    continue
                exact_period = existing_periods.get(
                    (existing_account.id, start_date)
                )
                if is_current_period:
                    other_open_periods = [
                        period
                        for period in open_periods_by_account.get(
                            existing_account.id, []
                        )
                        if exact_period is None or period.id != exact_period.id
                    ]
                    if len(other_open_periods) > 1 or any(
                        start_date <= period.start_date
                        for period in other_open_periods
                    ):
                        person_blockers.append("invalid_service_period")
                if target_staff is None:
                    continue
                exact_assignment = (
                    existing_service_assignments.get(
                        (exact_period.id, start_date)
                    )
                    if exact_period is not None
                    else None
                )
                if is_current_period:
                    other_open_assignments = [
                        assignment
                        for assignment in open_assignments_by_staff_service.get(
                            (target_staff.id, service_type), []
                        )
                        if exact_assignment is None
                        or assignment.id != exact_assignment.id
                    ]
                    if len(other_open_assignments) > 1 or any(
                        start_date <= assignment.start_date
                        for assignment in other_open_assignments
                    ):
                        person_blockers.append("invalid_service_period")

            planned_code = (
                target_staff.internal_code
                if target_staff is not None
                else _planned_staff_code(organization_id, source_keys)
            )
            if target_staff is None and planned_code in staff_by_code:
                person_blockers.append("internal_code_collision")

            person_hard_blockers = {
                "review_group_invalid",
                "multiple_linked_staff",
                "target_staff_missing",
                "target_staff_deleted",
                "target_staff_test_data",
                "history_only_person",
                "internal_code_collision",
                "invalid_service_period",
                "multiple_current_service_accounts",
            }
            if any(code in person_hard_blockers for code in person_blockers):
                staff_action = "blocked"
            elif target_staff is None:
                staff_action = "create"
            else:
                desired_name = str(candidate.get("display_name") or "")
                if (
                    target_staff.display_name != desired_name
                    or target_staff.employment_status != employment_status
                    or not target_staff.is_active
                    or target_staff.terminated_at is not None
                ):
                    staff_action = "update"
                else:
                    staff_action = "no_change"

            account_actions = {
                "create": 0,
                "update": 0,
                "no_change": 0,
                "blocked": 0,
            }
            period_actions = {
                "create": 0,
                "update": 0,
                "no_change": 0,
                "blocked": 0,
            }
            service_assignment_actions = {
                "create": 0,
                "update": 0,
                "no_change": 0,
                "blocked": 0,
            }
            for account in group_accounts:
                if staff_action == "blocked":
                    account_actions["blocked"] += 1
                    period_actions["blocked"] += 1
                    service_assignment_actions["blocked"] += 1
                    continue
                service_type = str(account.get("service_type") or "")
                external_id = str(account.get("external_id") or "")
                existing_account = existing_accounts.get((service_type, external_id))
                entry = entry_by_source.get(str(account.get("source_key")), {})
                incoming = entry.get("incoming_payload") or {}
                if existing_account is None:
                    account_action = "create"
                elif target_staff is None:
                    # The existing source row still needs to be linked to the
                    # newly planned Staff row during a future write phase.
                    account_action = "update"
                else:
                    desired_account = {
                        "staff_id": target_staff.id if target_staff else None,
                        "display_name_snapshot": incoming.get("display_name") or "",
                        "job_name_snapshot": incoming.get("job_name") or "",
                        "employment_status": incoming.get("employment_status") or "unknown",
                        "source_status": incoming.get("source_status") or "",
                        "latest_start_date": incoming.get("start_date"),
                        "latest_end_date": incoming.get("end_date"),
                        "is_active": incoming.get("employment_status") != "retired",
                    }
                    current_account = {
                        "staff_id": existing_account.staff_id,
                        "display_name_snapshot": existing_account.display_name_snapshot,
                        "job_name_snapshot": existing_account.job_name_snapshot,
                        "employment_status": existing_account.employment_status,
                        "source_status": existing_account.source_status,
                        "latest_start_date": (
                            existing_account.latest_start_date.isoformat()
                            if existing_account.latest_start_date
                            else None
                        ),
                        "latest_end_date": (
                            existing_account.latest_end_date.isoformat()
                            if existing_account.latest_end_date
                            else None
                        ),
                        "is_active": existing_account.is_active,
                    }
                    desired_account["staff_id"] = (
                        str(desired_account["staff_id"])
                        if desired_account["staff_id"] is not None
                        else None
                    )
                    current_account["staff_id"] = (
                        str(current_account["staff_id"])
                        if current_account["staff_id"] is not None
                        else None
                    )
                    account_action = (
                        "no_change"
                        if desired_account == current_account
                        else "update"
                    )
                account_actions[account_action] += 1

                start_date = parsed_start_dates.get(str(account.get("source_key")))
                existing_period = (
                    existing_periods.get((existing_account.id, start_date))
                    if existing_account is not None and start_date is not None
                    else None
                )
                if existing_period is None:
                    if existing_account is not None and not incoming.get("end_date"):
                        open_periods = open_periods_by_account.get(
                            existing_account.id, []
                        )
                        if open_periods:
                            open_period = open_periods[0]
                            if open_period.id not in planned_period_closures:
                                period_actions["update"] += 1
                                planned_period_closures.add(open_period.id)
                    period_action = "create"
                else:
                    desired_end = incoming.get("end_date")
                    current_end = (
                        existing_period.end_date.isoformat()
                        if existing_period.end_date
                        else None
                    )
                    period_action = (
                        "no_change"
                        if current_end == desired_end
                        and existing_period.employment_status
                        == (incoming.get("employment_status") or "unknown")
                        else "update"
                    )
                period_actions[period_action] += 1

                organization_proposals = {
                    str(proposal.get("unit_type")): proposal
                    for proposal in account.get("organizations") or []
                }
                reviewed_assignment = reviewed_assignments_by_source.get(
                    str(account.get("source_key")), {}
                )

                def proposed_unit_id(unit_type: str) -> UUID | None:
                    if (
                        unit_type == "department"
                        and reviewed_assignment.get("department_name")
                    ):
                        matching_units = list(real_units_by_type_and_name.get(
                            (
                                "department",
                                str(reviewed_assignment["department_name"]),
                            ),
                            [],
                        ))
                        business_unit = real_business_by_service.get(service_type)
                        if business_unit is not None:
                            matching_units = [
                                unit
                                for unit in matching_units
                                if unit.parent_unit_id == business_unit.id
                            ]
                        return matching_units[0].id if len(matching_units) == 1 else None
                    raw_unit_id = organization_proposals.get(unit_type, {}).get(
                        "unit_id"
                    )
                    return UUID(str(raw_unit_id)) if raw_unit_id else None

                job_code = reviewed_assignment.get("job_code") or (
                    account.get("job") or {}
                ).get("code")
                job = jobs_by_code.get(str(job_code)) if job_code else None
                position_title = reviewed_assignment.get("position_title") or (
                    account.get("position") or {}
                ).get("proposed_name")
                position = (
                    positions_by_name.get(str(position_title))
                    if position_title
                    else None
                )
                existing_service_assignment = (
                    existing_service_assignments.get(
                        (existing_period.id, start_date)
                    )
                    if existing_period is not None and start_date is not None
                    else None
                )
                if (
                    existing_service_assignment is None
                    and target_staff is not None
                    and not incoming.get("end_date")
                ):
                    open_assignments = open_assignments_by_staff_service.get(
                        (target_staff.id, service_type), []
                    )
                    if open_assignments:
                        open_assignment = open_assignments[0]
                        if open_assignment.id not in planned_assignment_closures:
                            service_assignment_actions["update"] += 1
                            planned_assignment_closures.add(open_assignment.id)
                if start_date is None or staff_action == "blocked":
                    service_assignment_action = "blocked"
                elif existing_service_assignment is None:
                    service_assignment_action = "create"
                else:
                    desired_assignment = {
                        "staff_id": str(target_staff.id) if target_staff else None,
                        "source_account_id": (
                            str(existing_account.id) if existing_account else None
                        ),
                        "service_period_id": str(existing_period.id),
                        "service_type": service_type,
                        "business_unit_id": (
                            str(unit_id)
                            if (unit_id := proposed_unit_id("business"))
                            else None
                        ),
                        "department_unit_id": (
                            str(unit_id)
                            if (unit_id := proposed_unit_id("department"))
                            else None
                        ),
                        "floor_unit_id": (
                            str(unit_id)
                            if (unit_id := proposed_unit_id("floor"))
                            else None
                        ),
                        "team_unit_id": (
                            str(unit_id)
                            if (unit_id := proposed_unit_id("team"))
                            else None
                        ),
                        "job_code": str(job_code) if job_code else None,
                        "job_title_snapshot": job.name if job else None,
                        "position_code_id": str(position.id) if position else None,
                        "position_title_snapshot": (
                            str(position_title) if position_title else None
                        ),
                        "start_date": start_date.isoformat(),
                        "end_date": incoming.get("end_date"),
                        "assignment_basis": (
                            "admin_confirmed"
                            if decision_source == "saved_review"
                            else "carefor_auto"
                        ),
                    }
                    current_service_assignment = {
                        "staff_id": str(existing_service_assignment.staff_id),
                        "source_account_id": str(
                            existing_service_assignment.source_account_id
                        ),
                        "service_period_id": str(
                            existing_service_assignment.service_period_id
                        ),
                        "service_type": existing_service_assignment.service_type,
                        "business_unit_id": (
                            str(existing_service_assignment.business_unit_id)
                            if existing_service_assignment.business_unit_id
                            else None
                        ),
                        "department_unit_id": (
                            str(existing_service_assignment.department_unit_id)
                            if existing_service_assignment.department_unit_id
                            else None
                        ),
                        "floor_unit_id": (
                            str(existing_service_assignment.floor_unit_id)
                            if existing_service_assignment.floor_unit_id
                            else None
                        ),
                        "team_unit_id": (
                            str(existing_service_assignment.team_unit_id)
                            if existing_service_assignment.team_unit_id
                            else None
                        ),
                        "job_code": existing_service_assignment.job_code,
                        "job_title_snapshot": (
                            existing_service_assignment.job_title_snapshot
                        ),
                        "position_code_id": (
                            str(existing_service_assignment.position_code_id)
                            if existing_service_assignment.position_code_id
                            else None
                        ),
                        "position_title_snapshot": (
                            existing_service_assignment.position_title_snapshot
                        ),
                        "start_date": existing_service_assignment.start_date.isoformat(),
                        "end_date": (
                            existing_service_assignment.end_date.isoformat()
                            if existing_service_assignment.end_date
                            else None
                        ),
                        "assignment_basis": existing_service_assignment.assignment_basis,
                    }
                    service_assignment_action = (
                        "no_change"
                        if desired_assignment == current_service_assignment
                        else "update"
                    )
                service_assignment_actions[service_assignment_action] += 1

            person_plan_key = sha256(
                f"{candidate_key}|{person_index}|{'|'.join(source_keys)}".encode("utf-8")
            ).hexdigest()[:16]
            person_plans.append(
                {
                    "plan_key": person_plan_key,
                    "source_keys": source_keys,
                    "planned_internal_code": planned_code,
                    "target_staff_id": target_staff.id if target_staff else None,
                    "staff_action": staff_action,
                    "employment_status": employment_status,
                    "service_types": service_types,
                    "source_account_actions": account_actions,
                    "service_period_actions": period_actions,
                    "service_assignment_actions": service_assignment_actions,
                    "current_assignment_account_count": len(current_accounts),
                    "history_only_account_count": len(history_accounts),
                    "login_action": "none",
                    "access_policy": (
                        "leave_suspended"
                        if employment_status == "leave"
                        else "active_access"
                    ),
                    "blocker_codes": list(dict.fromkeys(person_blockers)),
                }
            )
            summary["person_count"] += 1
            summary[f"staff_{staff_action}"] = summary.get(f"staff_{staff_action}", 0) + 1
            for action, count in account_actions.items():
                summary[f"source_account_{action}"] += count
            for action, count in period_actions.items():
                summary[f"service_period_{action}"] += count
            for action, count in service_assignment_actions.items():
                summary[f"service_assignment_{action}"] += count
            summary["current_assignment_accounts"] += len(current_accounts)
            summary["history_only_accounts"] += len(history_accounts)
            summary[f"{employment_status}_people"] += 1
            candidate_blockers.extend(person_blockers)

        unique_candidate_blockers = list(dict.fromkeys(candidate_blockers))
        hard_blockers = {
            "source_data_conflict",
            "review_decision_missing",
            "review_decision_incomplete",
            "review_decision_invalid",
            "review_group_invalid",
            "multiple_linked_staff",
            "target_staff_missing",
            "target_staff_deleted",
            "target_staff_test_data",
            "history_only_person",
            "internal_code_collision",
            "invalid_service_period",
            "multiple_current_service_accounts",
        }
        if any(code in hard_blockers for code in unique_candidate_blockers):
            outcome = "blocked"
        elif unique_candidate_blockers:
            outcome = "needs_design"
        else:
            outcome = "prepared"
        summary[f"{outcome}_candidates"] += 1
        candidate_plans.append(
            {
                "candidate_key": candidate_key,
                "display_name": str(candidate.get("display_name") or ""),
                "decision_source": decision_source,
                "outcome": outcome,
                "account_count": len(accounts),
                "history_only_account_count": sum(
                    str(account.get("employment_status")) == "retired"
                    for account in accounts
                ),
                "person_plans": person_plans,
                "blocker_codes": unique_candidate_blockers,
                "changes": [
                    "직원정보 계획",
                    f"케어포 계정 {len(accounts)}건",
                    "로그인 발급 없음",
                ],
            }
        )

    catalog_blocked_accounts = sum(
        1
        for candidate in candidates
        for account in candidate.get("accounts") or []
        if account.get("assignment_required")
        and any(
            proposal.get("proposed_name") is not None
            and proposal.get("match_status") != "matched"
            for proposal in account.get("organizations") or []
        )
    )
    blockers: list[dict[str, Any]] = []
    if catalog_blocked_accounts:
        blockers.append(
            {
                "code": "real_organization_catalog_missing",
                "message": "실제 조직표가 없어 시험 조직 이름만 참고할 수 있습니다.",
                "affected_people": summary["person_count"],
                "affected_accounts": catalog_blocked_accounts,
            }
        )
    blockers.append(
        {
            "code": "rollback_manifest_missing",
            "message": "실제 반영 전 백업·실행기록·복구 절차가 아직 없습니다.",
            "affected_people": summary["person_count"],
            "affected_accounts": summary["account_count"],
        }
    )
    candidate_blocker_messages = {
        "source_data_conflict": "원본 자료 충돌을 먼저 해결해야 합니다.",
        "review_decision_missing": "저장되지 않은 관리자 결정안이 있습니다.",
        "review_decision_incomplete": "완료되지 않은 관리자 결정안이 있습니다.",
        "review_decision_invalid": "저장 후 기준표가 바뀌어 다시 확인할 결정안이 있습니다.",
        "review_group_invalid": "사람 묶음에 빠지거나 중복된 케어포 계정이 있습니다.",
        "multiple_linked_staff": "한 사람 묶음이 여러 기존 직원에 연결되어 있습니다.",
        "target_staff_missing": "연결된 기존 직원을 현재 기관에서 찾을 수 없습니다.",
        "target_staff_deleted": "삭제된 기존 직원에 연결된 계정이 있습니다.",
        "target_staff_test_data": "가명 시험직원 연결은 실제 직원 반영에서 보호됩니다.",
        "history_only_person": "퇴사 이력만 있는 사람은 새 현재 직원으로 만들지 않습니다.",
        "internal_code_collision": "계획용 내부 직원번호가 기존 번호와 충돌합니다.",
        "invalid_service_period": "입사일이 없어 근무기간을 안전하게 만들 수 없습니다.",
        "multiple_current_service_accounts": "한 사람에게 같은 서비스의 현재 계정이 여러 개 있어 먼저 확인해야 합니다.",
    }
    existing_blocker_codes = {blocker["code"] for blocker in blockers}
    for blocker_code, message in candidate_blocker_messages.items():
        if blocker_code in existing_blocker_codes:
            continue
        affected_candidates = [
            candidate
            for candidate in candidate_plans
            if blocker_code in candidate["blocker_codes"]
        ]
        if not affected_candidates:
            continue
        blockers.append(
            {
                "code": blocker_code,
                "message": message,
                "affected_people": sum(
                    max(len(candidate["person_plans"]), 1)
                    for candidate in affected_candidates
                ),
                "affected_accounts": sum(
                    candidate["account_count"] for candidate in affected_candidates
                ),
            }
        )
    change_entries, change_manifest_supported = _build_creation_only_change_entries(
        db,
        organization_id,
        batch_id=batch_id,
        entries=entries,
        candidates=candidates,
        candidate_plans=candidate_plans,
    )
    change_material = {
        "organization_id": organization_id,
        "manifest_supported": change_manifest_supported,
        "entries": change_entries,
        "fallback_plan": None if change_manifest_supported else candidate_plans,
    }
    change_canonical = json.dumps(
        _stable_plan_value(change_material),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    change_fingerprint = sha256(change_canonical.encode("utf-8")).hexdigest()
    fingerprint_material = {
        "plan_schema_version": 2,
        "batch": {
            "id": batch_id,
            "file_sha256": file_sha256,
            "status": batch_status,
        },
        "entries": sorted(entries, key=lambda item: str(item.get("source_key"))),
        "decisions": [
            {
                "candidate_key": candidate["candidate_key"],
                "review_status": candidate.get("review_status"),
                "review_draft": (
                    {
                        key: value
                        for key, value in _stable_plan_value(candidate["review_draft"]).items()
                        if key
                        in {
                            "identity_decision",
                            "person_groups",
                            "account_assignments",
                            "completion_status",
                            "completion_issues",
                            "revision",
                        }
                    }
                    if candidate.get("review_draft") is not None
                    else None
                ),
            }
            for candidate in sorted(
                candidates, key=lambda item: str(item.get("candidate_key"))
            )
        ],
        "database_state": _staff_application_state_snapshot(db, organization_id),
        "approved_policy": {
            "organization_catalog_version": APPROVED_ORGANIZATION_CATALOG_VERSION,
            "organization_catalog_fingerprint": approved_organization_catalog_fingerprint(),
            "leave_access_policy": approved_leave_access_policy_snapshot(),
            "leave_access_policy_fingerprint": approved_leave_access_policy_fingerprint(),
        },
        "change_fingerprint": change_fingerprint,
        "change_manifest_supported": change_manifest_supported,
        "plan": {
            "summary": summary,
            "blockers": blockers,
            "candidates": candidate_plans,
        },
    }
    canonical = json.dumps(
        _stable_plan_value(fingerprint_material),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return {
        "plan_schema_version": 2,
        "batch_id": batch_id,
        "file_sha256": file_sha256,
        "read_only": True,
        "apply_locked": True,
        "can_apply": False,
        "plan_fingerprint": sha256(canonical.encode("utf-8")).hexdigest(),
        "change_fingerprint": change_fingerprint,
        "change_manifest_supported": change_manifest_supported,
        "organization_catalog_version": APPROVED_ORGANIZATION_CATALOG_VERSION,
        "organization_catalog_fingerprint": approved_organization_catalog_fingerprint(),
        "leave_access_policy_version": APPROVED_LEAVE_ACCESS_POLICY_VERSION,
        "leave_access_policy_fingerprint": approved_leave_access_policy_fingerprint(),
        "leave_access_policy": approved_leave_access_policy_snapshot(),
        "summary": summary,
        "blockers": blockers,
        "candidates": candidate_plans,
        "safety_notes": [
            "이 조회는 직원·로그인·배정·채팅방·감사기록을 변경하지 않습니다.",
            "로그인 아이디와 비밀번호는 생성하거나 추정하지 않습니다.",
            "휴직자는 로그인·현재 방 접근·새 자동방·업무함·Push가 중지되고 복직 시 보존된 권한에서 복원됩니다.",
            f"활성 시험 조직 {len(test_units)}건은 실제 배정 ID로 사용하지 않습니다.",
        ],
        "protected_tables": [
            "staff",
            "users",
            "staff_source_accounts",
            "staff_service_periods",
            "staff_service_assignments",
            "staff_organization_assignments",
            "staff_job_assignments",
            "staff_hub_room_memberships",
            "audit_events",
        ],
        "_change_entries": change_entries,
    }
