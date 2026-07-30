from __future__ import annotations

from collections import Counter
import csv
from datetime import datetime, timezone
from hashlib import sha256
from io import StringIO
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import OrgUnit, RecipientRoom, Resident, ResidentSyncItem


SYNC_RESIDENT_PREFIX = "SMCODI:"
MAX_ROSTER_ROWS = 1_000

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
    "external_id": (
        "external_id",
        "internal_code",
        "recipient_id",
        "resident_id",
        "id",
        "수급자번호",
        "어르신ID",
    ),
    "display_name": (
        "display_name",
        "resident_name",
        "recipient_name",
        "name",
        "어르신명",
        "성명",
    ),
    "service_type": (
        "service_type",
        "service",
        "care_type",
        "서비스",
        "급여종류",
    ),
    "floor": ("floor", "floor_name", "층"),
    "room_name": ("room_name", "room", "생활실", "생활실명"),
    "is_active": ("is_active", "active", "status", "이용상태", "상태"),
}


class ResidentSyncError(ValueError):
    pass


class ResidentSyncStaleError(RuntimeError):
    pass


def _value(row: dict[str, Any], field: str) -> Any:
    for alias in FIELD_ALIASES[field]:
        if alias in row and row[alias] is not None:
            return row[alias]
    return None


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _parse_active(value: Any) -> tuple[bool, str | None]:
    if value is None or value == "":
        return True, None
    if isinstance(value, bool):
        return value, None
    if isinstance(value, (int, float)):
        return bool(value), None
    normalized = _text(value).lower().replace(" ", "")
    if normalized in {"1", "true", "yes", "y", "active", "이용", "재원", "활성"}:
        return True, None
    if normalized in {
        "0",
        "false",
        "no",
        "n",
        "inactive",
        "퇴소",
        "중지",
        "비활성",
        "종료",
    }:
        return False, None
    return True, f"이용상태 값 '{_text(value)}'을(를) 해석할 수 없습니다."


def _parse_generated_at(value: Any) -> datetime | None:
    text_value = _text(value)
    if not text_value:
        return None
    try:
        parsed = datetime.fromisoformat(text_value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResidentSyncError(
            "generated_at은 ISO 날짜·시간 형식이어야 합니다."
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_roster_file(
    content: bytes,
    original_name: str,
) -> tuple[datetime | None, list[dict[str, Any]]]:
    suffix = Path(original_name).suffix.lower()
    if suffix not in {".json", ".csv"}:
        raise ResidentSyncError("JSON 또는 CSV 명단 파일만 올릴 수 있습니다.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ResidentSyncError("명단 파일은 UTF-8 형식이어야 합니다.") from exc

    generated_at: datetime | None = None
    if suffix == ".json":
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ResidentSyncError(
                f"JSON 형식이 올바르지 않습니다. ({exc.lineno}행)"
            ) from exc
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            generated_at = _parse_generated_at(
                payload.get("generated_at") or payload.get("source_generated_at")
            )
            rows = (
                payload.get("residents")
                or payload.get("recipients")
                or payload.get("data")
            )
        else:
            rows = None
    else:
        rows = list(csv.DictReader(StringIO(text)))

    if not isinstance(rows, list) or not rows:
        raise ResidentSyncError(
            "명단에 어르신 행이 없습니다. 빈 명단은 전체 중지로 오인될 수 있어 받지 않습니다."
        )
    if len(rows) > MAX_ROSTER_ROWS:
        raise ResidentSyncError(
            f"한 번에 최대 {MAX_ROSTER_ROWS}명까지만 미리볼 수 있습니다."
        )
    if any(not isinstance(row, dict) for row in rows):
        raise ResidentSyncError("명단의 각 항목은 열 이름과 값으로 구성되어야 합니다.")
    return generated_at, rows


def resident_snapshot(resident: Resident) -> dict[str, Any]:
    return {
        "internal_code": resident.internal_code,
        "display_name": resident.display_name,
        "service_type": resident.service_type,
        "floor": resident.floor.name if resident.floor else None,
        "room_name": resident.room.name if resident.room else None,
        "is_active": resident.is_active,
        "status": resident.status,
    }


def _normalize_row(
    row: dict[str, Any],
    row_number: int,
) -> tuple[dict[str, Any], list[str]]:
    issues: list[str] = []
    external_id = _text(_value(row, "external_id"))
    if not external_id:
        external_id = f"__INVALID_ROW_{row_number:04d}"
        issues.append("외부 식별자(external_id)가 없습니다.")
    elif len(external_id) > 70:
        issues.append("외부 식별자는 70자 이하여야 합니다.")

    display_name = _text(_value(row, "display_name"))
    service_raw = _text(_value(row, "service_type"))
    service_type = SERVICE_ALIASES.get(service_raw.lower())
    floor = _text(_value(row, "floor")) or None
    room_name = _text(_value(row, "room_name")) or None
    is_active, active_issue = _parse_active(_value(row, "is_active"))
    if active_issue:
        issues.append(active_issue)
    if is_active and not display_name:
        issues.append("이용 중인 어르신의 표시 이름이 없습니다.")
    if is_active and service_type is None:
        issues.append(
            "서비스 구분은 facility(시설), daycare(주간보호), "
            "homecare(방문요양) 중 하나여야 합니다."
        )
    if is_active and service_type in {"facility", "daycare"} and not floor:
        issues.append("시설·주간보호 어르신은 층 정보가 필요합니다.")

    return (
        {
            "external_id": external_id,
            "display_name": display_name,
            "service_type": service_type or service_raw,
            "floor": floor,
            "room_name": room_name,
            "is_active": is_active,
        },
        issues,
    )


def _current_matches(current: Resident, incoming: dict[str, Any]) -> bool:
    current_floor = current.floor.name if current.floor else None
    current_room = current.room.name if current.room else None
    desired_room = incoming["room_name"] or _default_room_name(
        incoming["service_type"], incoming["floor"]
    )
    return (
        current.display_name == incoming["display_name"]
        and current.service_type == incoming["service_type"]
        and current_floor == incoming["floor"]
        and current_room == desired_room
        and current.is_active
        and current.status == "active"
    )


def _default_room_name(service_type: str, floor: str | None) -> str:
    if floor:
        return f"{floor} 생활구역"
    return {
        "facility": "시설 생활구역",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }.get(service_type, "미지정 생활구역")


def _floor_units(db: Session, organization_id: UUID) -> dict[str, OrgUnit]:
    units = db.scalars(
        select(OrgUnit).where(
            OrgUnit.organization_id == organization_id,
            OrgUnit.unit_type == "floor",
            OrgUnit.is_active.is_(True),
        )
    ).all()
    return {unit.name: unit for unit in units}


def build_preview_entries(
    db: Session,
    organization_id: UUID,
    rows: list[dict[str, Any]],
    *,
    include_missing_as_deactivate: bool = True,
    managed_external_id_prefixes: tuple[str, ...] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    normalized_rows: list[tuple[dict[str, Any], list[str]]] = [
        _normalize_row(row, index)
        for index, row in enumerate(rows, start=1)
    ]
    identifiers = [
        payload["external_id"]
        for payload, _ in normalized_rows
        if not payload["external_id"].startswith("__INVALID_ROW_")
    ]
    duplicate_ids = {
        external_id
        for external_id, count in Counter(identifiers).items()
        if count > 1
    }

    managed_residents = db.scalars(
        select(Resident).where(
            Resident.organization_id == organization_id,
            Resident.internal_code.like(f"{SYNC_RESIDENT_PREFIX}%"),
        )
    ).all()
    current_by_external_id = {
        resident.internal_code.removeprefix(SYNC_RESIDENT_PREFIX): resident
        for resident in managed_residents
        if managed_external_id_prefixes is None
        or resident.internal_code.removeprefix(SYNC_RESIDENT_PREFIX).startswith(
            managed_external_id_prefixes
        )
    }
    floor_units = _floor_units(db, organization_id)
    seen_external_ids = set(identifiers)
    entries: list[dict[str, Any]] = []
    emitted_external_ids: set[str] = set()

    for payload, issues in normalized_rows:
        external_id = payload["external_id"]
        if external_id in emitted_external_ids:
            continue
        emitted_external_ids.add(external_id)
        current = current_by_external_id.get(external_id)
        if external_id in duplicate_ids:
            issues.append("같은 external_id가 파일 안에 두 번 이상 있습니다.")

        if (
            payload["is_active"]
            and payload["floor"]
            and payload["floor"] not in floor_units
        ):
            issues.append(
                f"활성 조직정보의 층 '{payload['floor']}'을(를) 찾을 수 없습니다."
            )

        if issues:
            change_type = "conflict"
            item_status = "blocked"
            conflict_reason = " ".join(issues)
        elif not payload["is_active"]:
            if current is not None and current.is_active:
                change_type = "deactivate"
                item_status = "pending"
            else:
                change_type = "unchanged"
                item_status = "not_required"
            conflict_reason = None
        elif current is None:
            change_type = "new"
            item_status = "pending"
            conflict_reason = None
        elif _current_matches(current, payload):
            change_type = "unchanged"
            item_status = "not_required"
            conflict_reason = None
        else:
            change_type = "update"
            item_status = "pending"
            conflict_reason = None

        entries.append(
            {
                "external_id": external_id,
                "change_type": change_type,
                "status": item_status,
                "current_resident_id": current.id if current else None,
                "incoming_payload": payload,
                "current_snapshot": resident_snapshot(current) if current else None,
                "conflict_reason": conflict_reason,
            }
        )

    if include_missing_as_deactivate:
        for external_id, current in current_by_external_id.items():
            if external_id in seen_external_ids or not current.is_active:
                continue
            snapshot = resident_snapshot(current)
            entries.append(
                {
                    "external_id": external_id,
                    "change_type": "deactivate",
                    "status": "pending",
                    "current_resident_id": current.id,
                    "incoming_payload": {
                        "external_id": external_id,
                        "display_name": current.display_name,
                        "service_type": current.service_type,
                        "floor": snapshot["floor"],
                        "room_name": snapshot["room_name"],
                        "is_active": False,
                    },
                    "current_snapshot": snapshot,
                    "conflict_reason": None,
                }
            )

    summary = dict(
        Counter(entry["change_type"] for entry in entries)
    )
    for key in ("new", "update", "deactivate", "unchanged", "conflict"):
        summary.setdefault(key, 0)
    summary["total"] = len(entries)
    summary["actionable"] = sum(
        summary[key] for key in ("new", "update", "deactivate")
    )
    return entries, summary


def _room_for_incoming(
    db: Session,
    organization_id: UUID,
    incoming: dict[str, Any],
) -> RecipientRoom:
    floor_name = incoming.get("floor")
    floor_unit = None
    if floor_name:
        floor_unit = db.scalar(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization_id,
                OrgUnit.unit_type == "floor",
                OrgUnit.name == floor_name,
                OrgUnit.is_active.is_(True),
            )
        )
        if floor_unit is None:
            raise ResidentSyncStaleError(
                f"층 '{floor_name}'이(가) 미리보기 이후 중지되거나 변경되었습니다."
            )

    room_name = incoming.get("room_name") or _default_room_name(
        incoming["service_type"], floor_name
    )
    room_key = f"{incoming['service_type']}|{floor_name or ''}|{room_name}"
    internal_code = f"SMCODI-ROOM-{sha256(room_key.encode('utf-8')).hexdigest()[:24]}"
    room = db.scalar(
        select(RecipientRoom).where(
            RecipientRoom.organization_id == organization_id,
            RecipientRoom.internal_code == internal_code,
        )
    )
    if room is None:
        room = RecipientRoom(
            organization_id=organization_id,
            internal_code=internal_code,
            name=room_name,
            floor=floor_name,
            floor_unit_id=floor_unit.id if floor_unit else None,
            is_active=True,
        )
        db.add(room)
        db.flush()
    else:
        room.name = room_name
        room.floor = floor_name
        room.floor_unit_id = floor_unit.id if floor_unit else None
        room.is_active = True
    return room


def _assert_snapshot_is_current(item: ResidentSyncItem, resident: Resident) -> None:
    if item.current_snapshot != resident_snapshot(resident):
        raise ResidentSyncStaleError(
            f"{resident.display_name} 어르신 정보가 미리보기 이후 변경되었습니다. "
            "파일을 다시 올려 확인해 주세요."
        )


def apply_sync_item(
    db: Session,
    item: ResidentSyncItem,
    *,
    is_test_data: bool,
) -> Resident:
    incoming = item.incoming_payload
    internal_code = f"{SYNC_RESIDENT_PREFIX}{item.external_id}"
    if item.change_type == "new":
        existing = db.scalar(
            select(Resident).where(
                Resident.organization_id == item.organization_id,
                Resident.internal_code == internal_code,
            )
        )
        if existing is not None:
            raise ResidentSyncStaleError(
                f"{item.external_id} 어르신이 미리보기 이후 이미 등록되었습니다."
            )
        room = _room_for_incoming(db, item.organization_id, incoming)
        max_sort_order = db.scalar(
            select(func.max(Resident.sort_order)).where(
                Resident.organization_id == item.organization_id,
                Resident.service_type == incoming["service_type"],
            )
        )
        resident = Resident(
            organization_id=item.organization_id,
            internal_code=internal_code,
            display_name=incoming["display_name"],
            status="active",
            room_id=room.id,
            service_type=incoming["service_type"],
            sort_order=(max_sort_order or 0) + 10,
            is_test_data=is_test_data,
            is_active=True,
        )
        db.add(resident)
        db.flush()
        item.current_resident_id = resident.id
        return resident

    resident = (
        db.get(Resident, item.current_resident_id)
        if item.current_resident_id
        else None
    )
    if (
        resident is None
        or resident.organization_id != item.organization_id
        or resident.internal_code != internal_code
    ):
        raise ResidentSyncStaleError(
            f"{item.external_id} 어르신의 현재 정보를 찾을 수 없습니다."
        )
    _assert_snapshot_is_current(item, resident)

    if item.change_type == "deactivate":
        resident.is_active = False
        resident.status = "inactive"
        return resident
    if item.change_type != "update":
        raise ResidentSyncStaleError("승인할 수 없는 변경 유형입니다.")

    room = _room_for_incoming(db, item.organization_id, incoming)
    resident.display_name = incoming["display_name"]
    resident.service_type = incoming["service_type"]
    resident.room_id = room.id
    resident.is_active = True
    resident.status = "active"
    return resident
