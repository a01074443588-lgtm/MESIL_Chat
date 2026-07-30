"""개발 전용 가명 조직, 직원, 어르신 자료를 멱등하게 생성합니다."""

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
    RecipientRoom,
    Resident,
    Room,
    RoomMembership,
    User,
    utcnow,
)
from app.services import (  # noqa: E402
    clear_staff_job,
    create_employee,
    ensure_bootstrap_admin,
    ensure_reference_data,
    ensure_system_rooms,
    set_staff_job,
    set_staff_position_title,
    set_staff_unit_assignments,
    sync_auto_memberships,
)
from submission_accounts import load_submission_accounts


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SMCODI 채팅방 가명 시험자료 생성")
    parser.add_argument(
        "--staff-password",
        required=True,
        help="가명 직원들이 함께 사용할 12자 이상의 시험용 비밀번호",
    )
    return parser.parse_args()


def get_or_create_unit(db, organization_id, unit_type: str, name: str) -> OrgUnit:
    unit = db.scalar(
        select(OrgUnit).where(
            OrgUnit.organization_id == organization_id,
            OrgUnit.unit_type == unit_type,
            OrgUnit.name == name,
        )
    )
    if unit is None:
        stable_code = hashlib.sha256(
            f"{unit_type}:{name}".encode("utf-8")
        ).hexdigest()[:10]
        unit = OrgUnit(
            organization_id=organization_id,
            unit_type=unit_type,
            internal_code=f"DEMO-{unit_type.upper()}-{stable_code}",
            name=name,
            is_test_data=True,
        )
        db.add(unit)
        db.flush()
    return unit


def get_or_create_seed_room(db, organization_id, *, kind: str, unit=None, job=None):
    query = select(Room).where(
        Room.organization_id == organization_id,
        Room.kind == kind,
    )
    if unit is not None:
        query = query.where(Room.scope_unit_id == unit.id)
    elif job is not None:
        query = query.where(Room.job_code == job.code)
    else:
        query = query.where(
            Room.scope_unit_id.is_(None),
            Room.job_code.is_(None),
        )
    room = db.scalar(query)
    if room is not None:
        return room
    suffix = {
        "business": "전체방",
        "department": "전체방",
        "floor": "직원방",
        "team": "방",
    }
    if kind == "job":
        name = f"{job.name}방"
    elif kind == "all":
        name = "전체 직원방"
    else:
        separator = "" if kind == "team" else " "
        name = f"{unit.name}{separator}{suffix[kind]}"
    room = Room(
        organization_id=organization_id,
        name=name,
        kind=kind,
        scope_unit_id=unit.id if unit is not None else None,
        job_code=job.code if job is not None else None,
        resident_scope="floor" if kind == "floor" else "all",
        is_test_data=True,
    )
    db.add(room)
    db.flush()
    return room


def ensure_support_room(db, organization_id, actor: User, staff_ids: set) -> Room:
    room = db.scalar(
        select(Room).where(
            Room.organization_id == organization_id,
            Room.kind == "custom",
            Room.name == "영양·복지·의료방",
        )
    )
    if room is None:
        room = Room(
            organization_id=organization_id,
            name="영양·복지·의료방",
            kind="custom",
            resident_scope="all",
            created_by_id=actor.id,
            is_test_data=True,
        )
        db.add(room)
        db.flush()
    room.is_active = True
    room.sort_order = 80

    now = utcnow()
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
    reusable_by_staff = {}
    for membership in memberships:
        reusable_by_staff.setdefault(membership.staff_id, membership)

    for membership in active_by_staff.values():
        if membership.staff_id not in staff_ids:
            membership.left_at = now
    for staff_id in staff_ids:
        if staff_id in active_by_staff:
            active_by_staff[staff_id].source = "manual"
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
                organization_id=organization_id,
                room_id=room.id,
                staff_id=staff_id,
                source="manual",
                created_by=actor.id,
            )
        )
    return room


JOB_CODES = {
    "사회복지사": "social_worker",
    "영양사": "dietitian",
    "조리원": "cook",
    "위생원": "sanitation_worker",
    "작업치료사": "occupational_therapist",
    "요양보호사": "caregiver",
    "간호조무사": "nursing_assistant",
}


def employee(
    units: dict[tuple[str, str], OrgUnit],
    *,
    username: str,
    name: str,
    code: str,
    division: str,
    department: str | None,
    occupation: str | None,
    team: str | None,
    position_title: str | None = None,
    processor: bool = False,
) -> dict:
    return {
        "username": username,
        "full_name": f"{name}(가명)",
        "employee_code": code,
        "business_id": units[("business", division)].id,
        "department_id": (
            units[("department", department)].id if department else None
        ),
        "job_code": JOB_CODES[occupation] if occupation else None,
        "position_title": position_title,
        "floor_id": None,
        "team_id": units[("team", team)].id if team else None,
        "can_process_records": processor,
    }


def build_employees(units: dict[tuple[str, str], OrgUnit]) -> list[dict]:
    submission_accounts = load_submission_accounts()
    result = [
        employee(
            units,
            username="representative",
            name="가상 대표",
            code="DEMO-EXEC-01",
            division="시설",
            department=None,
            occupation=None,
            team=None,
            position_title="대표",
            processor=True,
        ),
        employee(
            units,
            username="director",
            name="가상 원장",
            code="DEMO-EXEC-02",
            division="시설",
            department=None,
            occupation=None,
            team=None,
            position_title="원장",
            processor=True,
        ),
    ]
    for number in range(1, 4):
        result.append(
            employee(
                units,
                username=(
                    submission_accounts.social
                    if number == 1
                    else f"fsw{number:02d}"
                ),
                name=f"시설 사회복지사 {number:02d}",
                code=f"DEMO-FSW-{number:02d}",
                division="시설",
                department="복지",
                occupation="사회복지사",
                team=None,
                position_title=(
                    "선임사회복지사" if number == 1 else "사회복지사"
                ),
                processor=True,
            )
        )
    result.extend(
        [
            employee(
                units,
                username="dietitian",
                name="시설 영양사",
                code="DEMO-DIET-01",
                division="시설",
                department="영양",
                occupation="영양사",
                team=None,
            ),
            employee(
                units,
                username="hygiene",
                name="시설 위생원",
                code="DEMO-HYG-01",
                division="시설",
                department="영양",
                occupation="위생원",
                team=None,
            ),
            employee(
                units,
                username="therapist",
                name="시설 작업치료사",
                code="DEMO-OT-01",
                division="시설",
                department="의료",
                occupation="작업치료사",
                team=None,
                processor=True,
            ),
        ]
    )
    for number in range(1, 5):
        result.append(
            employee(
                units,
                username=f"cook{number:02d}",
                name=f"시설 조리원 {number:02d}",
                code=f"DEMO-COOK-{number:02d}",
                division="시설",
                department="영양",
                occupation="조리원",
                team=None,
            )
        )
    floor_counts = {2: 5, 3: 6, 4: 6, 5: 6}
    for floor, count in floor_counts.items():
        for number in range(1, count + 1):
            username = f"fcare{floor}_{number:02d}"
            if floor == 3 and number == 1:
                username = submission_accounts.care_a
            elif floor == 3 and number == 2:
                username = submission_accounts.care_b
            elif floor == 4 and number == 1:
                username = "care4"
            result.append(
                employee(
                    units,
                    username=username,
                    name=f"시설 {floor}층 요양보호사 {number:02d}",
                    code=f"DEMO-FC-{floor}{number:02d}",
                    division="시설",
                    department="요양",
                    occupation="요양보호사",
                    team=f"{floor}층",
                )
            )
    for number in range(1, 3):
        result.append(
            employee(
                units,
                username="nurse" if number == 1 else "fnurse02",
                name=f"시설 간호조무사 {number:02d}",
                code=f"DEMO-FN-{number:02d}",
                division="시설",
                department="의료",
                occupation="간호조무사",
                team=None,
                position_title="간호팀장" if number == 1 else "간호조무사",
                processor=True,
            )
        )
    result.extend(
        [
            employee(
                units,
                username="daysw",
                name="주간보호 사회복지사",
                code="DEMO-DSW-01",
                division="주간보호",
                department="복지",
                occupation="사회복지사",
                team="주간보호",
                position_title="사회복지사",
                processor=True,
            ),
            employee(
                units,
                username="daynurse",
                name="주간보호 간호조무사",
                code="DEMO-DN-01",
                division="주간보호",
                department="의료",
                occupation="간호조무사",
                team="주간보호",
                position_title="간호조무사",
                processor=True,
            ),
            employee(
                units,
                username="homesw",
                name="방문요양 사회복지사",
                code="DEMO-HSW-01",
                division="방문요양",
                department="복지",
                occupation="사회복지사",
                team="방문요양",
                position_title="사회복지사",
                processor=True,
            ),
        ]
    )
    for number in range(1, 7):
        result.append(
            employee(
                units,
                username=f"dcare{number:02d}",
                name=f"주간보호 요양보호사 {number:02d}",
                code=f"DEMO-DC-{number:02d}",
                division="주간보호",
                department="요양",
                occupation="요양보호사",
                team="주간보호",
            )
        )
    for number in range(1, 31):
        result.append(
            employee(
                units,
                username=f"hcare{number:02d}",
                name=f"방문요양 요양보호사 {number:02d}",
                code=f"DEMO-HC-{number:02d}",
                division="방문요양",
                department="요양",
                occupation="요양보호사",
                team="방문요양",
            )
        )
    return result


def seed_residents(
    db,
    organization_id,
    units: dict[tuple[str, str], OrgUnit],
) -> tuple[int, int]:
    plan = [
        ("daycare", "주간", "1층", 24),
        ("facility", "시설2층", "2층", 7),
        ("facility", "시설3층", "3층", 13),
        ("facility", "시설4층", "4층", 14),
        ("facility", "시설5층", "5층", 15),
    ]
    created = 0
    existing = 0
    for service_type, prefix, floor_name, count in plan:
        room_code = f"DEMO-ROOM-{service_type.upper()}-{floor_name}"
        recipient_room = db.scalar(
            select(RecipientRoom).where(
                RecipientRoom.organization_id == organization_id,
                RecipientRoom.internal_code == room_code,
            )
        )
        if recipient_room is None:
            recipient_room = RecipientRoom(
                organization_id=organization_id,
                internal_code=room_code,
                name=f"{floor_name} 가명 생활구역",
                floor=floor_name,
                floor_unit_id=units[("floor", floor_name)].id,
            )
            db.add(recipient_room)
            db.flush()
        else:
            recipient_room.floor_unit_id = units[("floor", floor_name)].id
        for number in range(1, count + 1):
            display_name = f"{prefix}-어르신-{number:02d}(가명)"
            internal_code = f"DEMO-{service_type.upper()}-{floor_name}-{number:02d}"
            resident = db.scalar(
                select(Resident).where(
                    Resident.organization_id == organization_id,
                    Resident.internal_code == internal_code,
                )
            )
            if resident is None:
                resident = Resident(
                    organization_id=organization_id,
                    internal_code=internal_code,
                    display_name=display_name,
                    service_type=service_type,
                    room_id=recipient_room.id,
                    is_test_data=True,
                )
                db.add(resident)
                created += 1
            else:
                resident.display_name = display_name
                resident.room_id = recipient_room.id
                resident.is_active = True
                existing += 1
    return created, existing


def main() -> None:
    args = parse_args()
    if settings.environment not in {"development", "test"}:
        raise SystemExit("가명자료 생성은 development 또는 test 환경에서만 허용됩니다.")
    if len(args.staff_password) < 12:
        raise SystemExit("시험용 비밀번호도 12자 이상이어야 합니다.")

    unit_names = {
        "business": ["시설", "주간보호", "방문요양"],
        "department": ["영양", "복지", "의료", "요양"],
        "floor": ["1층", "2층", "3층", "4층", "5층"],
        "team": ["2층", "3층", "4층", "5층", "주간보호", "방문요양"],
    }
    with SessionLocal() as db:
        organization = ensure_reference_data(db)
        ensure_system_rooms(db, organization)
        if not settings.bootstrap_admin_username or not settings.bootstrap_admin_password:
            raise SystemExit(
                "가명자료 생성 전 BOOTSTRAP_ADMIN_USERNAME과 "
                "BOOTSTRAP_ADMIN_PASSWORD를 설정하세요."
            )
        admin = ensure_bootstrap_admin(
            db,
            username=settings.bootstrap_admin_username,
            password=settings.bootstrap_admin_password,
            display_name=settings.bootstrap_admin_name,
        )
        db.commit()
        units = {
            (unit_type, name): get_or_create_unit(
                db, organization.id, unit_type, name
            )
            for unit_type, names in unit_names.items()
            for name in names
        }
        db.flush()
        room_configs = [
            (units[("business", "시설")], "시설 전체방", "facility", None, 10),
            (units[("business", "주간보호")], "주간보호방", "daycare", None, 20),
            (units[("business", "방문요양")], "방문요양방", "homecare", None, 30),
            (units[("team", "2층")], "2층방", "floor", units[("floor", "2층")], 40),
            (units[("team", "3층")], "3층방", "floor", units[("floor", "3층")], 50),
            (units[("team", "4층")], "4층방", "floor", units[("floor", "4층")], 60),
            (units[("team", "5층")], "5층방", "floor", units[("floor", "5층")], 70),
            (units[("department", "복지")], "복지방", "all", None, 90),
        ]
        for unit, room_name, resident_scope, resident_floor, sort_order in room_configs:
            room = get_or_create_seed_room(
                db,
                organization.id,
                kind=unit.unit_type,
                unit=unit,
            )
            room.name = room_name
            room.resident_scope = resident_scope
            room.resident_scope_unit_id = (
                resident_floor.id if resident_floor is not None else None
            )
            room.sort_order = sort_order
            room.is_active = True
        db.flush()
        created_users = 0
        updated_users = 0
        seeded_users: list[User] = []
        for values in build_employees(units):
            user = db.scalar(select(User).where(User.username == values["username"]))
            if user is None:
                user = create_employee(
                    db,
                    {
                        **values,
                        "password": args.staff_password,
                        "role": "staff",
                    },
                    admin.id,
                )
                user.must_change_password = False
                db.commit()
                created_users += 1
            else:
                if user.staff is None:
                    raise SystemExit(f"직원정보가 연결되지 않은 계정입니다: {user.username}")
                user.display_name = values["full_name"]
                user.staff.display_name = values["full_name"]
                user.can_process_records = values["can_process_records"]
                user.staff.internal_code = values["employee_code"]
                user.is_active = True
                user.staff.is_active = True
                user.staff.employment_status = "active"
                user.staff.terminated_at = None
                if values["job_code"]:
                    set_staff_job(db, user.staff, values["job_code"], admin.id)
                else:
                    clear_staff_job(db, user.staff, admin.id)
                set_staff_position_title(
                    user.staff,
                    values.get("position_title"),
                    admin.id,
                )
                set_staff_unit_assignments(db, user.staff, values, admin.id)
                sync_auto_memberships(db, user)
                updated_users += 1
            seeded_users.append(user)
        support_staff_ids = {
            user.staff.id
            for user in seeded_users
            if user.staff is not None
            and user.staff.current_unit("department") is not None
            and user.staff.current_unit("department").name in {"영양", "복지", "의료"}
        }
        ensure_support_room(db, organization.id, admin, support_staff_ids)
        created_residents, existing_residents = seed_residents(
            db,
            organization.id,
            units,
        )
        db.commit()

    print(f"가명 직원: 신규 {created_users}명, 기존 갱신 {updated_users}명")
    print(
        f"가명 어르신: 신규 {created_residents}명, 기존 유지 {existing_residents}명"
    )
    print("시설 37명·주간보호 8명·방문요양 31명, 총 76명 구조입니다.")
    print("실제 직원 또는 어르신 개인정보는 포함하지 않았습니다.")


if __name__ == "__main__":
    main()
