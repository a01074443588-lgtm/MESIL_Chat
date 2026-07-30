"""현재 가명 조직과 채팅방을 실버메디컬 운영 구조로 안전하게 정리합니다.

기본 실행은 읽기 전용 미리보기이며, --apply를 붙여야 실제 반영합니다.
기존 방과 대화는 삭제하지 않고 방을 종료 상태로 보존합니다.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

from sqlalchemy import select


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(BACKEND_ROOT))

from app.config import settings  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.models import (  # noqa: E402
    OrgUnit,
    Organization,
    Room,
    RoomMembership,
    Staff,
    User,
    utcnow,
)
from app.services import (  # noqa: E402
    ensure_scope_room,
    record_audit,
    set_staff_unit_assignments,
    sync_auto_memberships,
)


DEPARTMENT_BY_JOB = {
    "dietitian": "영양",
    "cook": "영양",
    "sanitation_worker": "영양",
    "social_worker": "복지",
    "registered_nurse": "의료",
    "nursing_assistant": "의료",
    "physical_therapist": "의료",
    "occupational_therapist": "의료",
    "caregiver": "요양",
}

TEAM_RENAMES = {
    "주간보호팀": "주간보호",
    "방문요양팀": "방문요양",
    "시설2층팀": "2층",
    "시설3층팀": "3층",
    "시설4층팀": "4층",
    "시설5층팀": "5층",
}

TARGET_ROOM_SORT = {
    "전체 직원방": 0,
    "시설 전체방": 10,
    "주간보호방": 20,
    "방문요양방": 30,
    "2층방": 40,
    "3층방": 50,
    "4층방": 60,
    "5층방": 70,
    "영양·복지·의료방": 80,
    "복지방": 90,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="층·팀과 중복 자동방을 실사용 구조로 일괄 정리"
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="미리보기만 하지 않고 실제 데이터에 반영합니다.",
    )
    return parser.parse_args()


def stable_code(unit_type: str, name: str) -> str:
    digest = hashlib.sha256(f"{unit_type}:{name}".encode("utf-8")).hexdigest()[:10]
    return f"CHAT-{unit_type.upper()}-{digest}"


def get_or_create_unit(
    db,
    organization: Organization,
    unit_type: str,
    name: str,
) -> OrgUnit:
    unit = db.scalar(
        select(OrgUnit).where(
            OrgUnit.organization_id == organization.id,
            OrgUnit.unit_type == unit_type,
            OrgUnit.name == name,
        )
    )
    if unit is None:
        unit = OrgUnit(
            organization_id=organization.id,
            unit_type=unit_type,
            internal_code=stable_code(unit_type, name),
            name=name,
            is_active=True,
            is_test_data=settings.environment != "production",
        )
        db.add(unit)
        db.flush()
    else:
        unit.is_active = True
    return unit


def find_unit(db, organization_id, unit_type: str, name: str) -> OrgUnit:
    unit = db.scalar(
        select(OrgUnit).where(
            OrgUnit.organization_id == organization_id,
            OrgUnit.unit_type == unit_type,
            OrgUnit.name == name,
        )
    )
    if unit is None:
        raise RuntimeError(f"{unit_type} 조직정보를 찾을 수 없습니다: {name}")
    return unit


def find_room(
    db,
    organization_id,
    *,
    kind: str,
    unit: OrgUnit | None = None,
) -> Room:
    query = select(Room).where(
        Room.organization_id == organization_id,
        Room.kind == kind,
    )
    if unit is None:
        query = query.where(
            Room.scope_unit_id.is_(None),
            Room.job_code.is_(None),
        )
    else:
        query = query.where(Room.scope_unit_id == unit.id)
    room = db.scalar(query)
    if room is None:
        raise RuntimeError(
            f"기존 채팅방을 찾을 수 없습니다: {kind}/{unit.name if unit else '-'}"
        )
    return room


def get_or_create_custom_room(
    db,
    organization: Organization,
    actor: User,
    name: str,
) -> Room:
    room = db.scalar(
        select(Room).where(
            Room.organization_id == organization.id,
            Room.kind == "custom",
            Room.name == name,
        )
    )
    if room is None:
        room = Room(
            organization_id=organization.id,
            name=name,
            kind="custom",
            resident_scope="all",
            created_by_id=actor.id,
            is_test_data=settings.environment != "production",
        )
        db.add(room)
        db.flush()
    room.is_active = True
    return room


def replace_manual_memberships(
    db,
    room: Room,
    staff_members: list[Staff],
    actor: User,
) -> None:
    now = utcnow()
    desired_ids = {staff.id for staff in staff_members}
    memberships = list(
        db.scalars(
            select(RoomMembership)
            .where(RoomMembership.room_id == room.id)
            .order_by(RoomMembership.joined_at.desc())
        ).all()
    )
    active_by_staff = {
        membership.staff_id: membership
        for membership in memberships
        if membership.left_at is None
    }
    reusable_by_staff: dict = {}
    for membership in memberships:
        reusable_by_staff.setdefault(membership.staff_id, membership)

    for membership in active_by_staff.values():
        if membership.staff_id not in desired_ids:
            membership.left_at = now
    for staff_id in desired_ids:
        active = active_by_staff.get(staff_id)
        if active is not None:
            active.source = "manual"
            continue
        reusable = reusable_by_staff.get(staff_id)
        if reusable is not None:
            reusable.left_at = None
            reusable.joined_at = now
            reusable.source = "manual"
            reusable.created_by = actor.id
            continue
        db.add(
            RoomMembership(
                organization_id=room.organization_id,
                room_id=room.id,
                staff_id=staff_id,
                source="manual",
                joined_at=now,
                created_by=actor.id,
            )
        )


def desired_team_name(staff: Staff) -> str | None:
    business = staff.current_unit("business")
    if business is None:
        return None
    if business.name == "주간보호":
        return "주간보호"
    if business.name == "방문요양":
        return "방문요양"
    if business.name != "시설":
        return None
    floor = staff.current_unit("floor")
    if floor is not None and floor.name in {"2층", "3층", "4층", "5층"}:
        return floor.name
    current_team = staff.current_unit("team")
    if current_team is not None and current_team.name in {"2층", "3층", "4층", "5층"}:
        return current_team.name
    return None


def main() -> None:
    args = parse_args()
    with SessionLocal() as db:
        organization = db.scalar(
            select(Organization).where(
                Organization.internal_code == settings.organization_code
            )
        )
        if organization is None:
            raise SystemExit("정리할 기관정보를 찾을 수 없습니다.")
        actor = db.scalar(
            select(User).where(
                User.organization_id == organization.id,
                User.username == settings.bootstrap_admin_username,
                User.is_active.is_(True),
            )
        )
        if actor is None:
            actor = db.scalar(
                select(User).where(
                    User.organization_id == organization.id,
                    User.is_active.is_(True),
                )
            )
        if actor is None:
            raise SystemExit("감사기록을 남길 관리자 계정을 찾을 수 없습니다.")

        active_staff = list(
            db.scalars(
                select(Staff).where(
                    Staff.organization_id == organization.id,
                    Staff.is_active.is_(True),
                    Staff.employment_status == "active",
                )
            ).all()
        )
        department_changes = 0
        team_changes = 0
        for staff in active_staff:
            current_job = staff.current_job()
            desired_department = DEPARTMENT_BY_JOB.get(
                current_job.job_code if current_job is not None else None
            )
            current_department = staff.current_unit("department")
            if (current_department.name if current_department else None) != desired_department:
                department_changes += 1
            current_team = staff.current_unit("team")
            if (current_team.name if current_team else None) != desired_team_name(staff):
                team_changes += 1

        planned = {
            "active_staff": len(active_staff),
            "floor_assignments_to_clear": sum(
                1 for staff in active_staff if staff.current_unit("floor") is not None
            ),
            "department_assignments_to_change": department_changes,
            "team_assignments_to_change": team_changes,
            "target_rooms": list(TARGET_ROOM_SORT),
        }
        print("운영구조 정리 미리보기")
        for key, value in planned.items():
            print(f"- {key}: {value}")
        if not args.apply:
            print("- 실제 반영하지 않았습니다. 확인 후 --apply를 붙여 실행하세요.")
            return

        departments = {
            name: get_or_create_unit(db, organization, "department", name)
            for name in ("영양", "복지", "의료", "요양")
        }
        teams: dict[str, OrgUnit] = {}
        for old_name, next_name in TEAM_RENAMES.items():
            team = db.scalar(
                select(OrgUnit).where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.unit_type == "team",
                    OrgUnit.name == old_name,
                )
            )
            if team is None:
                team = find_unit(db, organization.id, "team", next_name)
            duplicate = db.scalar(
                select(OrgUnit).where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.unit_type == "team",
                    OrgUnit.name == next_name,
                    OrgUnit.id != team.id,
                )
            )
            if duplicate is not None:
                raise RuntimeError(f"팀 이름이 중복되어 자동 변경할 수 없습니다: {next_name}")
            team.name = next_name
            team.is_active = True
            teams[next_name] = team

        facility = find_unit(db, organization.id, "business", "시설")
        daycare = find_unit(db, organization.id, "business", "주간보호")
        homecare = find_unit(db, organization.id, "business", "방문요양")
        floors = {
            name: find_unit(db, organization.id, "floor", name)
            for name in ("1층", "2층", "3층", "4층", "5층")
        }

        for staff in active_staff:
            current_job = staff.current_job()
            job_code = current_job.job_code if current_job is not None else None
            department_name = DEPARTMENT_BY_JOB.get(job_code)
            team_name = desired_team_name(staff)
            set_staff_unit_assignments(
                db,
                staff,
                {
                    "department_id": (
                        departments[department_name].id if department_name else None
                    ),
                    "floor_id": None,
                    "team_id": teams[team_name].id if team_name else None,
                },
                actor.id,
            )
        db.flush()

        target_rooms: list[Room] = []
        all_room = find_room(db, organization.id, kind="all")
        target_rooms.append(all_room)

        for unit, name, scope in (
            (facility, "시설 전체방", "facility"),
            (daycare, "주간보호방", "daycare"),
            (homecare, "방문요양방", "homecare"),
        ):
            room = find_room(db, organization.id, kind="business", unit=unit)
            room.name = name
            room.resident_scope = scope
            room.resident_scope_unit_id = None
            room.is_active = True
            target_rooms.append(room)

        for floor_name in ("2층", "3층", "4층", "5층"):
            room = find_room(
                db,
                organization.id,
                kind="team",
                unit=teams[floor_name],
            )
            room.name = f"{floor_name}방"
            room.resident_scope = "floor"
            room.resident_scope_unit_id = floors[floor_name].id
            room.is_active = True
            target_rooms.append(room)

        welfare_room = ensure_scope_room(db, departments["복지"])
        if welfare_room is None:
            raise RuntimeError("복지방을 생성하지 못했습니다.")
        welfare_room.name = "복지방"
        welfare_room.resident_scope = "all"
        welfare_room.resident_scope_unit_id = None
        welfare_room.is_active = True
        target_rooms.append(welfare_room)

        support_room = get_or_create_custom_room(
            db,
            organization,
            actor,
            "영양·복지·의료방",
        )
        support_staff = [
            staff
            for staff in active_staff
            if (
                staff.current_unit("department") is not None
                and staff.current_unit("department").name in {"영양", "복지", "의료"}
            )
        ]
        replace_manual_memberships(db, support_room, support_staff, actor)
        target_rooms.append(support_room)
        db.flush()

        target_room_ids = {room.id for room in target_rooms}
        now = utcnow()
        closed_rooms: list[str] = []
        for room in db.scalars(
            select(Room).where(
                Room.organization_id == organization.id,
                Room.kind != "self",
                Room.is_active.is_(True),
            )
        ).all():
            if room.id in target_room_ids:
                continue
            room.is_active = False
            closed_rooms.append(room.name)
            for membership in db.scalars(
                select(RoomMembership).where(
                    RoomMembership.room_id == room.id,
                    RoomMembership.left_at.is_(None),
                )
            ).all():
                membership.left_at = now

        for room in target_rooms:
            room.sort_order = TARGET_ROOM_SORT[room.name]
        db.flush()

        users = list(
            db.scalars(
                select(User).where(
                    User.organization_id == organization.id,
                    User.is_active.is_(True),
                )
            ).all()
        )
        for user in users:
            sync_auto_memberships(db, user)
        replace_manual_memberships(db, support_room, support_staff, actor)
        db.flush()

        target_department_ids = {unit.id for unit in departments.values()}
        target_team_ids = {unit.id for unit in teams.values()}
        for unit in db.scalars(
            select(OrgUnit).where(
                OrgUnit.organization_id == organization.id,
                OrgUnit.unit_type.in_(("department", "team")),
                OrgUnit.is_active.is_(True),
            )
        ).all():
            if (
                unit.unit_type == "department"
                and unit.id not in target_department_ids
            ) or (unit.unit_type == "team" and unit.id not in target_team_ids):
                unit.is_active = False

        record_audit(
            db,
            actor_id=actor.id,
            action="organization.operating_structure_consolidated",
            target_type="organization",
            target_id=organization.id,
            details={
                "floor_assignments_cleared": planned["floor_assignments_to_clear"],
                "closed_rooms": sorted(closed_rooms),
                "active_rooms": [
                    room.name
                    for room in sorted(target_rooms, key=lambda item: item.sort_order)
                ],
                "support_room_members": len(support_staff),
                "data_deleted": False,
            },
        )
        db.commit()
        print("- 실제 반영 완료")
        print(f"- 종료한 중복방: {len(closed_rooms)}개")
        print(f"- 운영방: {', '.join(TARGET_ROOM_SORT)}")
        print(f"- 영양·복지·의료방 참여자: {len(support_staff)}명")


if __name__ == "__main__":
    main()
