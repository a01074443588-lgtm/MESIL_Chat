from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal, engine
from app.main import app
from app.models import (
    AuditEvent,
    Organization,
    OrgUnit,
    Room,
    RoomMembership,
    Staff,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    User,
    utcnow,
)
from app.security import hash_password, verify_password
from app.services import (
    ensure_scope_room,
    issue_existing_staff_login,
    staff_matches_room_rule,
)


class NoCommitSession(Session):
    def commit(self) -> None:  # pragma: no cover - a call is itself a failure
        raise AssertionError("서비스 함수가 caller 대신 commit했습니다.")


def test_department_room_names_include_their_service_and_refresh_existing_name() -> None:
    with TestClient(app):
        pass

    db = NoCommitSession(bind=engine)
    try:
        actor = db.scalar(select(User).where(User.username == "admin"))
        assert actor is not None
        units = {
            unit.internal_code: unit
            for unit in db.scalars(
                select(OrgUnit).where(OrgUnit.organization_id == actor.organization_id)
            ).all()
        }

        facility_room = ensure_scope_room(
            db,
            units["department.facility.medical"],
        )
        daycare_room = ensure_scope_room(
            db,
            units["department.daycare.medical"],
        )
        assert facility_room is not None
        assert daycare_room is not None
        assert facility_room.name == "시설 의료방"
        assert daycare_room.name == "주간보호 의료방"
        assert facility_room.name != daycare_room.name

        facility_room.name = "의료 전체방"
        db.flush()
        refreshed_room = ensure_scope_room(
            db,
            units["department.facility.medical"],
        )
        assert refreshed_room is not None
        assert refreshed_room.id == facility_room.id
        assert refreshed_room.name == "시설 의료방"
    finally:
        db.rollback()
        db.close()


def _add_service_assignment(
    db: Session,
    *,
    staff: Staff,
    service_type: str,
    business: OrgUnit,
    department: OrgUnit | None,
    job_code: str,
    start_date: date,
    end_date: date | None = None,
) -> StaffServiceAssignment:
    external_id = f"LOGIN-TEST-{uuid4().hex}"
    account = StaffSourceAccount(
        organization_id=staff.organization_id,
        staff_id=staff.id,
        source_system="carefor",
        service_type=service_type,
        external_id=external_id,
        display_name_snapshot=staff.display_name,
        job_name_snapshot=job_code,
        employment_status="active" if end_date is None else "retired",
        source_status="재직" if end_date is None else "퇴사",
        latest_start_date=start_date,
        latest_end_date=end_date,
        is_active=end_date is None,
    )
    db.add(account)
    db.flush()
    period = StaffServicePeriod(
        organization_id=staff.organization_id,
        staff_id=staff.id,
        source_account_id=account.id,
        start_date=start_date,
        end_date=end_date,
        employment_status="active" if end_date is None else "retired",
    )
    db.add(period)
    db.flush()
    assignment = StaffServiceAssignment(
        organization_id=staff.organization_id,
        staff_id=staff.id,
        source_account_id=account.id,
        service_period_id=period.id,
        service_type=service_type,
        business_unit_id=business.id,
        department_unit_id=department.id if department is not None else None,
        job_code=job_code,
        job_title_snapshot=job_code,
        start_date=start_date,
        end_date=end_date,
        assignment_basis="carefor_auto",
        is_test_data=True,
    )
    db.add(assignment)
    db.flush()
    return assignment


def test_issue_existing_staff_login_reuses_staff_and_syncs_all_current_services() -> None:
    with TestClient(app):
        pass

    temporary_password = "Temporary!234"
    username = f"existing-{uuid4().hex[:12]}"
    db = NoCommitSession(bind=engine)
    try:
        actor = db.scalar(select(User).where(User.username == "admin"))
        assert actor is not None
        units = {
            unit.internal_code: unit
            for unit in db.scalars(
                select(OrgUnit).where(OrgUnit.organization_id == actor.organization_id)
            ).all()
        }
        staff = Staff(
            organization_id=actor.organization_id,
            internal_code=f"LOGIN-{uuid4().hex}",
            display_name="기존 직원 로그인 시험",
            job_title="겸직",
            employment_status="active",
            is_active=True,
            is_test_data=True,
        )
        db.add(staff)
        db.flush()

        _add_service_assignment(
            db,
            staff=staff,
            service_type="facility",
            business=units["business.facility"],
            department=units["department.facility.welfare"],
            job_code="social_worker",
            start_date=date(2024, 1, 1),
        )
        _add_service_assignment(
            db,
            staff=staff,
            service_type="daycare",
            business=units["business.daycare"],
            department=units["department.daycare.welfare"],
            job_code="office_worker",
            start_date=date(2024, 2, 1),
        )
        _add_service_assignment(
            db,
            staff=staff,
            service_type="homecare",
            business=units["business.homecare"],
            department=None,
            job_code="caregiver",
            start_date=date(2023, 1, 1),
            end_date=date(2024, 1, 1),
        )
        expired_service_room = ensure_scope_room(db, units["business.homecare"])
        assert expired_service_room is not None
        db.expire(staff, ["service_assignments"])

        user = issue_existing_staff_login(
            db,
            staff=staff,
            username=username,
            temporary_password=temporary_password,
            actor_id=actor.id,
        )

        assert user.staff_id == staff.id
        assert user.display_name == staff.display_name
        assert user.role == "staff"
        assert user.can_process_records is False
        assert user.must_change_password is True
        assert verify_password(temporary_password, user.password_hash)

        active_memberships = list(
            db.scalars(
                select(RoomMembership).where(
                    RoomMembership.staff_id == staff.id,
                    RoomMembership.left_at.is_(None),
                )
            ).all()
        )
        member_rooms = {
            room.id: room
            for room in db.scalars(
                select(Room).where(
                    Room.id.in_({membership.room_id for membership in active_memberships})
                )
            ).all()
        }
        assert {room.kind for room in member_rooms.values()} >= {
            "all",
            "self",
            "business",
            "department",
            "job",
        }
        assert {
            room.scope_unit_id
            for room in member_rooms.values()
            if room.scope_unit_id is not None
        } == {
            units["business.facility"].id,
            units["department.facility.welfare"].id,
            units["business.daycare"].id,
            units["department.daycare.welfare"].id,
        }
        assert {
            room.job_code for room in member_rooms.values() if room.job_code is not None
        } == {"social_worker", "office_worker"}
        assert expired_service_room.id not in member_rooms
        assert all(
            staff_matches_room_rule(staff, room)
            for room in member_rooms.values()
            if room.kind not in {"self"}
        )
        assert not staff_matches_room_rule(staff, expired_service_room)

        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action == "staff.login_issued",
                AuditEvent.target_id == staff.id,
            )
        )
        assert audit is not None
        assert audit.actor_id == actor.id
        assert audit.details == {
            "username": username,
            "role": "staff",
            "can_process_records": False,
        }
        assert temporary_password not in str(audit.details)

        with SessionLocal() as separate_db:
            assert (
                separate_db.scalar(select(User.id).where(User.username == username))
                is None
            )
    finally:
        db.rollback()
        db.close()


def test_issue_existing_staff_login_rejects_unsafe_targets_and_credentials() -> None:
    with TestClient(app):
        pass

    db = NoCommitSession(bind=engine)
    try:
        actor = db.scalar(select(User).where(User.username == "admin"))
        assert actor is not None

        active_staff = Staff(
            organization_id=actor.organization_id,
            internal_code=f"VALIDATE-{uuid4().hex}",
            display_name="로그인 검증 시험",
            job_title="시험",
            employment_status="active",
            is_active=True,
            is_test_data=True,
        )
        linked_staff = Staff(
            organization_id=actor.organization_id,
            internal_code=f"LINKED-{uuid4().hex}",
            display_name="연결 검증 시험",
            job_title="시험",
            employment_status="active",
            is_active=True,
            is_test_data=True,
        )
        inactive_staff = Staff(
            organization_id=actor.organization_id,
            internal_code=f"LEAVE-{uuid4().hex}",
            display_name="휴직 검증 시험",
            job_title="시험",
            employment_status="leave",
            is_active=True,
            is_test_data=True,
        )
        deleted_staff = Staff(
            organization_id=actor.organization_id,
            internal_code=f"DELETED-{uuid4().hex}",
            display_name="삭제 검증 시험",
            job_title="시험",
            employment_status="active",
            is_active=False,
            deleted_at=utcnow(),
            is_test_data=True,
        )
        db.add_all([active_staff, linked_staff, inactive_staff, deleted_staff])
        db.flush()
        existing_username = f"linked-{uuid4().hex[:12]}"
        linked_user = User(
            organization_id=actor.organization_id,
            staff_id=linked_staff.id,
            staff=linked_staff,
            username=existing_username,
            display_name=linked_staff.display_name,
            password_hash=hash_password("ExistingPass!234"),
            is_active=True,
        )
        db.add(linked_user)
        db.flush()

        cases = [
            (active_staff, "short-pass", "short", 422),
            (active_staff, ".abc", "Temporary!234", 422),
            (active_staff, existing_username, "Temporary!234", 409),
            (linked_staff, f"new-{uuid4().hex[:12]}", "Temporary!234", 409),
            (inactive_staff, f"leave-{uuid4().hex[:12]}", "Temporary!234", 409),
            (deleted_staff, f"deleted-{uuid4().hex[:12]}", "Temporary!234", 404),
        ]
        for target, candidate_username, password, expected_status in cases:
            with pytest.raises(HTTPException) as exc_info:
                issue_existing_staff_login(
                    db,
                    staff=target,
                    username=candidate_username,
                    temporary_password=password,
                    actor_id=actor.id,
                )
            assert exc_info.value.status_code == expected_status

        other_organization = Organization(
            internal_code=f"OTHER-{uuid4().hex}",
            name="다른 기관",
            service_type="facility",
        )
        db.add(other_organization)
        db.flush()
        other_staff = Staff(
            organization_id=other_organization.id,
            internal_code=f"OTHER-STAFF-{uuid4().hex}",
            display_name="타기관 검증 시험",
            job_title="시험",
            employment_status="active",
            is_active=True,
            is_test_data=True,
        )
        db.add(other_staff)
        db.flush()
        with pytest.raises(HTTPException) as exc_info:
            issue_existing_staff_login(
                db,
                staff=other_staff,
                username=f"other-{uuid4().hex[:12]}",
                temporary_password="Temporary!234",
                actor_id=actor.id,
            )
        assert exc_info.value.status_code == 404
    finally:
        db.rollback()
        db.close()


def test_admin_can_issue_login_for_an_existing_staff_directory_entry() -> None:
    username = f"issued-{uuid4().hex[:12]}"
    temporary_password = "Temporary!234"
    with TestClient(app) as admin:
        assert (
            admin.post(
                "/api/auth/login",
                headers={"origin": "http://testserver"},
                json={"username": "admin", "password": "AdminPass!234"},
            ).status_code
            == 200
        )
        staff_response = admin.post(
            "/api/admin/staff-directory",
            headers={"origin": "http://testserver"},
            json={"full_name": "기존 직원 계정발급", "employee_code": f"ISSUE-{uuid4().hex[:10]}"},
        )
        assert staff_response.status_code == 201
        staff_id = staff_response.json()["staff_id"]

        issued = admin.post(
            f"/api/admin/staff-directory/{staff_id}/login",
            headers={"origin": "http://testserver"},
            json={
                "username": username,
                "temporary_password": temporary_password,
                "can_process_records": True,
            },
        )

        assert issued.status_code == 201
        body = issued.json()
        assert body["staff_id"] == staff_id
        assert body["login_status"] == "enabled"
        assert body["username"] == username
        assert body["can_process_records"] is True
        assert body["must_change_password"] is True

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == username))
        assert user is not None
        assert user.staff_id == UUID(staff_id)
        assert verify_password(temporary_password, user.password_hash)


def test_combined_staff_and_login_creation_is_atomic_on_duplicate_username() -> None:
    employee_code = f"ATOMIC-{uuid4().hex[:10]}"
    with TestClient(app) as admin:
        assert (
            admin.post(
                "/api/auth/login",
                headers={"origin": "http://testserver"},
                json={"username": "admin", "password": "AdminPass!234"},
            ).status_code
            == 200
        )
        response = admin.post(
            "/api/admin/staff-directory",
            headers={"origin": "http://testserver"},
            json={
                "full_name": "원자성 계정발급",
                "employee_code": employee_code,
                "issue_login": True,
                "username": "admin",
                "temporary_password": "Temporary!234",
                "can_process_records": True,
            },
        )
        assert response.status_code == 409

    with SessionLocal() as db:
        staff = db.scalar(select(Staff).where(Staff.internal_code == employee_code))
        assert staff is None
