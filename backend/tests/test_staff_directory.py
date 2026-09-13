from datetime import date

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    OrgUnit,
    Organization,
    Role,
    Room,
    RoomMembership,
    Staff,
    StaffOrganizationAssignment,
    StaffPositionCode,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    User,
)
from app.security import hash_password


ORIGIN = {"origin": "http://testserver"}


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def test_admin_can_register_staff_without_login(request) -> None:
    employee_code = "DIRECT-REGISTER-WITHOUT-LOGIN"
    created_staff_id = None

    def cleanup_created_staff() -> None:
        if created_staff_id is None:
            return
        with SessionLocal() as cleanup_db:
            cleanup_db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            )
            cleanup_db.execute(delete(Staff).where(Staff.id == created_staff_id))
            cleanup_db.commit()

    request.addfinalizer(cleanup_created_staff)

    with TestClient(app) as client:
        unauthorized = client.post(
            "/api/admin/staff-directory",
            json={"full_name": "가상 직원", "employee_code": employee_code},
            headers=ORIGIN,
        )
        assert unauthorized.status_code == 401, unauthorized.text

        login_admin(client)
        created = client.post(
            "/api/admin/staff-directory",
            json={
                "full_name": "  가상 직원  ",
                "employee_code": f"  {employee_code}  ",
            },
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        body = created.json()
        created_staff_id = body["staff_id"]
        assert body["display_name"] == "가상 직원"
        assert body["internal_code"] == employee_code
        assert body["employment_status"] == "active"
        assert body["login_user_id"] is None
        assert body["login_status"] == "not_issued"
        assert body["username"] is None

        duplicate = client.post(
            "/api/admin/staff-directory",
            json={"full_name": "다른 가상 직원", "employee_code": employee_code},
            headers=ORIGIN,
        )
        assert duplicate.status_code == 409, duplicate.text

        with SessionLocal() as verify_db:
            staff = verify_db.get(Staff, created_staff_id)
            assert staff is not None
            assert staff.display_name == "가상 직원"
            assert staff.job_title == "직종 미지정"
            assert verify_db.scalar(
                select(User).where(User.staff_id == created_staff_id)
            ) is None
            audit = verify_db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "staff.created",
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            )
            assert audit is not None
            assert audit.details == {
                "source": "admin_direct",
                "login_issued": False,
            }


def test_admin_can_edit_staff_without_login_account(request) -> None:
    original_code = "DIRECT-EDIT-WITHOUT-LOGIN"
    updated_code = f"{original_code}-UPDATED"
    created_staff_id = None

    def cleanup_created_staff() -> None:
        if created_staff_id is None:
            return
        with SessionLocal() as cleanup_db:
            cleanup_db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            )
            cleanup_db.execute(delete(Staff).where(Staff.id == created_staff_id))
            cleanup_db.commit()

    request.addfinalizer(cleanup_created_staff)

    with TestClient(app) as client:
        login_admin(client)
        created = client.post(
            "/api/admin/staff-directory",
            json={"full_name": "수정 전 직원", "employee_code": original_code},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        created_staff_id = created.json()["staff_id"]

        updated = client.patch(
            f"/api/admin/staff-directory/{created_staff_id}",
            json={
                "full_name": "  수정 후 직원  ",
                "employee_code": f"  {updated_code}  ",
            },
            headers=ORIGIN,
        )
        assert updated.status_code == 200, updated.text
        body = updated.json()
        assert body["display_name"] == "수정 후 직원"
        assert body["internal_code"] == updated_code
        assert body["login_user_id"] is None
        assert body["login_status"] == "not_issued"

        with SessionLocal() as verify_db:
            staff = verify_db.get(Staff, created_staff_id)
            assert staff is not None
            assert staff.display_name == "수정 후 직원"
            assert staff.internal_code == updated_code
            assert verify_db.scalar(
                select(User).where(User.staff_id == created_staff_id)
            ) is None
            audit = verify_db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "staff.updated",
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            )
            assert audit is not None
            assert audit.details == {
                "source": "admin_direct",
                "changed_fields": ["employee_code", "full_name"],
                "login_display_name_synced": False,
            }
            admin_user = verify_db.scalar(
                select(User).where(User.username == "admin")
            )
            assert admin_user is not None
            assert admin_user.staff is not None
            duplicate_code = admin_user.staff.internal_code

        duplicate = client.patch(
            f"/api/admin/staff-directory/{created_staff_id}",
            json={"employee_code": duplicate_code},
            headers=ORIGIN,
        )
        assert duplicate.status_code == 409, duplicate.text


def test_admin_can_terminate_staff_without_login_account(request) -> None:
    employee_code = "DIRECT-TERMINATE-WITHOUT-LOGIN"
    created_staff_id = None

    def cleanup_created_staff() -> None:
        if created_staff_id is None:
            return
        with SessionLocal() as cleanup_db:
            cleanup_db.execute(
                delete(AuditEvent).where(
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            )
            cleanup_db.execute(delete(Staff).where(Staff.id == created_staff_id))
            cleanup_db.commit()

    request.addfinalizer(cleanup_created_staff)

    with TestClient(app) as client:
        login_admin(client)
        created = client.post(
            "/api/admin/staff-directory",
            json={"full_name": "퇴사 시험 직원", "employee_code": employee_code},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        created_staff_id = created.json()["staff_id"]

        terminated = client.post(
            f"/api/admin/staff-directory/{created_staff_id}/terminate",
            headers=ORIGIN,
        )
        assert terminated.status_code == 200, terminated.text
        body = terminated.json()
        assert body["employment_status"] == "retired"
        assert body["login_user_id"] is None
        assert body["login_status"] == "not_issued"
        assert body["terminated_at"] is not None

        repeated = client.post(
            f"/api/admin/staff-directory/{created_staff_id}/terminate",
            headers=ORIGIN,
        )
        assert repeated.status_code == 200, repeated.text

        with SessionLocal() as verify_db:
            staff = verify_db.get(Staff, created_staff_id)
            assert staff is not None
            assert staff.employment_status == "retired"
            assert staff.is_active is False
            assert staff.terminated_at is not None
            assert verify_db.scalar(
                select(User).where(User.staff_id == created_staff_id)
            ) is None
            audits = verify_db.scalars(
                select(AuditEvent).where(
                    AuditEvent.action == "staff.terminated",
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == created_staff_id,
                )
            ).all()
            assert len(audits) == 1
            assert audits[0].details == {
                "source": "admin_direct",
                "login_disabled": False,
                "revoked_sessions": 0,
                "closed_memberships": 0,
            }


def test_staff_directory_keeps_staff_identity_separate_from_login_account(request) -> None:
    with TestClient(app) as client:
        assert client.get("/api/admin/staff-directory", headers=ORIGIN).status_code == 401
        login_admin(client)

        with SessionLocal() as db:
            organization = db.scalar(select(Organization))
            assert organization is not None
            director_position = db.scalar(
                select(StaffPositionCode).where(
                    StaffPositionCode.organization_id == organization.id,
                    StaffPositionCode.name == "대표",
                )
            )
            assert director_position is not None

            staff = Staff(
                organization_id=organization.id,
                internal_code="DIRECTORY-WITHOUT-LOGIN",
                display_name="로그인 없는 겸직 직원",
                job_title="",
                position_title=None,
                employment_status="active",
                is_test_data=False,
                is_active=True,
            )
            db.add(staff)
            db.flush()
            other_organization = Organization(
                internal_code="DIRECTORY-OTHER-ORG",
                name="다른 기관 시험",
                service_type="facility_care",
            )
            db.add(other_organization)
            db.flush()
            foreign_business_unit = OrgUnit(
                organization_id=other_organization.id,
                unit_type="business",
                internal_code="DIRECTORY-FOREIGN-BUSINESS",
                name="다른 기관 사업부",
            )
            wrong_type_unit = OrgUnit(
                organization_id=organization.id,
                unit_type="department",
                internal_code="DIRECTORY-WRONG-TYPE",
                name="종류 불일치 부서",
            )
            db.add_all([foreign_business_unit, wrong_type_unit])
            db.flush()
            admin_role = db.scalar(select(Role).where(Role.code == "admin"))
            assert admin_role is not None
            wrong_link_password = "WrongLinkedUserPass!234"
            wrong_org_user = User(
                organization_id=other_organization.id,
                staff_id=staff.id,
                username="wrong-org-directory-user",
                display_name=staff.display_name,
                password_hash=hash_password(wrong_link_password),
                is_active=True,
            )
            db.add(wrong_org_user)

            other_admin_staff = Staff(
                organization_id=other_organization.id,
                internal_code="DIRECTORY-OTHER-ADMIN",
                display_name="다른 기관 정상 관리자",
                job_title="",
                employment_status="active",
                is_active=True,
            )
            db.add(other_admin_staff)
            db.flush()
            other_admin_password = "OtherOrgAdminPass!234"
            other_admin_user = User(
                organization_id=other_organization.id,
                staff_id=other_admin_staff.id,
                username="other-org-directory-admin",
                display_name=other_admin_staff.display_name,
                password_hash=hash_password(other_admin_password),
                is_active=True,
            )
            other_admin_user.roles.append(admin_role)
            db.add(other_admin_user)

            nested_staff = Staff(
                organization_id=organization.id,
                internal_code="DIRECTORY-INVALID-NESTED-UNITS",
                display_name="조직단위 경계 시험 직원",
                job_title="",
                employment_status="active",
                is_active=True,
            )
            db.add(nested_staff)
            db.flush()
            nested_user = User(
                organization_id=organization.id,
                staff_id=nested_staff.id,
                username="invalid-nested-units-user",
                display_name=nested_staff.display_name,
                password_hash=hash_password("SessionGuardPass!234"),
                is_active=True,
            )
            db.add(nested_user)
            db.add(
                StaffOrganizationAssignment(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    unit_id=wrong_type_unit.id,
                    unit_type="team",
                    start_date=date(2024, 1, 1),
                )
            )
            db.add_all(
                [
                    StaffOrganizationAssignment(
                        organization_id=organization.id,
                        staff_id=nested_staff.id,
                        unit_id=foreign_business_unit.id,
                        unit_type="business",
                        start_date=date(2024, 1, 1),
                    ),
                    StaffOrganizationAssignment(
                        organization_id=organization.id,
                        staff_id=nested_staff.id,
                        unit_id=wrong_type_unit.id,
                        unit_type="team",
                        start_date=date(2024, 1, 1),
                    ),
                ]
            )

            assignment_specs = [
                ("facility", "FAC-001", None, "대표", director_position.id),
                ("daycare", "DAY-001", "office_worker", None, None),
                ("homecare", "HOME-001", "social_worker", None, None),
            ]
            for index, (
                service_type,
                external_id,
                job_code,
                position_title,
                position_code_id,
            ) in enumerate(assignment_specs, start=1):
                account = StaffSourceAccount(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_system="carefor",
                    service_type=service_type,
                    external_id=external_id,
                    display_name_snapshot=staff.display_name,
                    job_name_snapshot=position_title or job_code or "",
                    employment_status="active",
                    source_status="재직",
                    latest_start_date=date(2024, index, 1),
                    latest_end_date=None,
                    is_active=True,
                )
                db.add(account)
                db.flush()
                period = StaffServicePeriod(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_account_id=account.id,
                    start_date=date(2024, index, 1),
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
                        service_type=service_type,
                        business_unit_id=(
                            foreign_business_unit.id
                            if service_type == "facility"
                            else None
                        ),
                        floor_unit_id=(
                            wrong_type_unit.id
                            if service_type == "facility"
                            else None
                        ),
                        job_code=job_code,
                        job_title_snapshot=(
                            "사무원"
                            if job_code == "office_worker"
                            else "사회복지사" if job_code == "social_worker" else None
                        ),
                        position_code_id=position_code_id,
                        position_title_snapshot=position_title,
                        start_date=period.start_date,
                        end_date=None,
                        assignment_basis="admin_confirmed",
                        is_test_data=False,
                    )
                )
            wrong_account = StaffSourceAccount(
                organization_id=other_organization.id,
                staff_id=staff.id,
                source_system="carefor",
                service_type="facility",
                external_id="WRONG-ORG-ACCOUNT",
                display_name_snapshot=staff.display_name,
                job_name_snapshot="다른 기관 자료",
                employment_status="retired",
                source_status="퇴사",
                latest_start_date=date(2023, 1, 1),
                latest_end_date=date(2023, 12, 31),
                is_active=False,
            )
            db.add(wrong_account)
            db.flush()
            wrong_period = StaffServicePeriod(
                organization_id=other_organization.id,
                staff_id=staff.id,
                source_account_id=wrong_account.id,
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                employment_status="retired",
            )
            db.add(wrong_period)
            db.flush()
            db.add(
                StaffServiceAssignment(
                    organization_id=other_organization.id,
                    staff_id=staff.id,
                    source_account_id=wrong_account.id,
                    service_period_id=wrong_period.id,
                    service_type="facility",
                    job_title_snapshot="다른 기관 자료",
                    start_date=wrong_period.start_date,
                    end_date=wrong_period.end_date,
                    assignment_basis="carefor_auto",
                )
            )
            db.commit()
            staff_id = staff.id
            nested_staff_id = nested_staff.id
            nested_user_id = nested_user.id
            wrong_org_user_id = wrong_org_user.id
            other_admin_staff_id = other_admin_staff.id
            other_admin_user_id = other_admin_user.id
            wrong_org_password_hash = wrong_org_user.password_hash
            other_organization_id = other_organization.id
            test_unit_ids = [foreign_business_unit.id, wrong_type_unit.id]

        def cleanup_created_staff() -> None:
            with SessionLocal() as cleanup_db:
                cleanup_db.execute(
                    delete(StaffServiceAssignment).where(
                        StaffServiceAssignment.staff_id == staff_id
                    )
                )
                cleanup_db.execute(
                    delete(RoomMembership).where(
                        RoomMembership.staff_id.in_(
                            [staff_id, nested_staff_id, other_admin_staff_id]
                        )
                    )
                )
                cleanup_db.execute(
                    delete(Room).where(
                        Room.owner_staff_id.in_(
                            [staff_id, nested_staff_id, other_admin_staff_id]
                        )
                    )
                )
                cleanup_db.execute(
                    delete(StaffOrganizationAssignment).where(
                        StaffOrganizationAssignment.staff_id.in_(
                            [staff_id, nested_staff_id, other_admin_staff_id]
                        )
                    )
                )
                cleanup_db.execute(
                    delete(StaffServicePeriod).where(
                        StaffServicePeriod.staff_id == staff_id
                    )
                )
                cleanup_db.execute(
                    delete(StaffSourceAccount).where(
                        StaffSourceAccount.staff_id == staff_id
                    )
                )
                cleanup_db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.actor_id.in_(
                            [
                                wrong_org_user_id,
                                nested_user_id,
                                other_admin_user_id,
                            ]
                        )
                    )
                )
                cleanup_db.execute(
                    delete(AuditEvent).where(
                        AuditEvent.target_type == "staff",
                        AuditEvent.target_id == staff_id,
                    )
                )
                cleanup_db.execute(
                    delete(User).where(
                        User.staff_id.in_(
                            [staff_id, nested_staff_id, other_admin_staff_id]
                        )
                    )
                )
                cleanup_db.execute(
                    delete(Staff).where(
                        Staff.id.in_(
                            [staff_id, nested_staff_id, other_admin_staff_id]
                        )
                    )
                )
                cleanup_db.execute(delete(OrgUnit).where(OrgUnit.id.in_(test_unit_ids)))
                cleanup_db.execute(
                    delete(Organization).where(
                        Organization.id == other_organization_id
                    )
                )
                cleanup_db.commit()

        request.addfinalizer(cleanup_created_staff)

        directory_response = client.get(
            "/api/admin/staff-directory", headers=ORIGIN
        )
        assert directory_response.status_code == 200, directory_response.text
        directory_entry = next(
            item
            for item in directory_response.json()
            if item["staff_id"] == str(staff_id)
        )
        assert directory_entry["login_user_id"] is None
        assert directory_entry["login_status"] == "not_issued"
        assert directory_entry["username"] is None
        assert len(directory_entry["service_assignments"]) == 3
        assert directory_entry["service_assignments"] == sorted(
            directory_entry["service_assignments"],
            key=lambda item: {"facility": 0, "daycare": 1, "homecare": 2}[
                item["service_type"]
            ],
        )
        assert {
            (item["service_type"], item["job_code"], item["position_title_snapshot"])
            for item in directory_entry["service_assignments"]
        } == {
            ("facility", None, "대표"),
            ("daycare", "office_worker", None),
            ("homecare", "social_worker", None),
        }
        facility_assignment = next(
            item
            for item in directory_entry["service_assignments"]
            if item["service_type"] == "facility"
        )
        assert facility_assignment["business"] is None
        assert facility_assignment["floor"] is None
        assert directory_entry["legacy_assignment"]["team"] is None

        login_accounts = client.get("/api/employees", headers=ORIGIN)
        assert login_accounts.status_code == 200, login_accounts.text
        assert all(
            item["employee_code"] != "DIRECTORY-WITHOUT-LOGIN"
            for item in login_accounts.json()
        )
        nested_login_entry = next(
            item for item in login_accounts.json() if item["id"] == str(nested_user_id)
        )
        assert nested_login_entry["business"] is None
        assert nested_login_entry["team"] is None

        staff_id_is_not_a_user_id = client.patch(
            f"/api/employees/{staff_id}",
            json={"full_name": "잘못 변경되면 안 됨"},
            headers=ORIGIN,
        )
        assert staff_id_is_not_a_user_id.status_code == 404

        assignment_update = client.patch(
            f"/api/admin/staff-directory/{staff_id}/service-assignments",
            json={
                "assignments": [
                    {
                        "service_type": service_type,
                        "job_code": "facility_director",
                        "position_title": "대표",
                    }
                    for service_type in ("facility", "daycare", "homecare")
                ]
            },
            headers=ORIGIN,
        )
        assert assignment_update.status_code == 200, assignment_update.text
        updated_assignments = assignment_update.json()["service_assignments"]
        updated_current = [item for item in updated_assignments if item["is_current"]]
        assert {
            (
                item["service_type"],
                item["job_code"],
                item["job_name"],
                item["position_title_snapshot"],
                item["assignment_basis"],
            )
            for item in updated_current
        } == {
            ("facility", "facility_director", "시설장", "대표", "admin_confirmed"),
            ("daycare", "facility_director", "시설장", "대표", "admin_confirmed"),
            ("homecare", "facility_director", "시설장", "대표", "admin_confirmed"),
        }
        updated_history = [item for item in updated_assignments if not item["is_current"]]
        assert len(updated_history) == 3
        assert all(item["end_date"] == date.today().isoformat() for item in updated_history)

        identical_update = client.patch(
            f"/api/admin/staff-directory/{staff_id}/service-assignments",
            json={
                "assignments": [
                    {
                        "service_type": service_type,
                        "job_code": "facility_director",
                        "position_title": "대표",
                    }
                    for service_type in ("facility", "daycare", "homecare")
                ]
            },
            headers=ORIGIN,
        )
        assert identical_update.status_code == 200, identical_update.text
        assert len(identical_update.json()["service_assignments"]) == 6
        with SessionLocal() as verify_db:
            audit = verify_db.scalar(
                select(AuditEvent).where(
                    AuditEvent.action == "staff.service_assignments.updated",
                    AuditEvent.target_type == "staff",
                    AuditEvent.target_id == staff_id,
                )
            )
            assert audit is not None

        logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert logout.status_code == 204, logout.text
        wrong_org_login = client.post(
            "/api/auth/login",
            json={
                "username": "other-org-directory-admin",
                "password": other_admin_password,
            },
            headers=ORIGIN,
        )
        assert wrong_org_login.status_code == 200, wrong_org_login.text
        wrong_org_accounts = client.get("/api/employees", headers=ORIGIN)
        assert wrong_org_accounts.status_code == 200, wrong_org_accounts.text
        assert all(
            item["id"] != str(wrong_org_user_id)
            for item in wrong_org_accounts.json()
        )
        protected_requests = [
            client.patch(
                f"/api/employees/{wrong_org_user_id}",
                json={"full_name": "변경되면 안 되는 이름"},
                headers=ORIGIN,
            ),
            client.post(
                f"/api/employees/{wrong_org_user_id}/reset-password",
                json={"temporary_password": "NeverAppliedPass!234"},
                headers=ORIGIN,
            ),
            client.post(
                f"/api/employees/{wrong_org_user_id}/terminate",
                headers=ORIGIN,
            ),
            client.post(
                f"/api/employees/{wrong_org_user_id}/restore",
                headers=ORIGIN,
            ),
            client.delete(
                f"/api/employees/{wrong_org_user_id}",
                headers=ORIGIN,
            ),
        ]
        assert [response.status_code for response in protected_requests] == [
            404,
            404,
            404,
            404,
            404,
        ]
        with SessionLocal() as verify_db:
            protected_staff = verify_db.get(Staff, staff_id)
            protected_user = verify_db.get(User, wrong_org_user_id)
            assert protected_staff is not None
            assert protected_staff.display_name == "로그인 없는 겸직 직원"
            assert protected_staff.employment_status == "active"
            assert protected_user is not None
            assert protected_user.is_active is True
            assert protected_user.password_hash == wrong_org_password_hash

        other_logout = client.post("/api/auth/logout", json={}, headers=ORIGIN)
        assert other_logout.status_code == 204, other_logout.text
        stale_login = client.post(
            "/api/auth/login",
            json={
                "username": "invalid-nested-units-user",
                "password": "SessionGuardPass!234",
            },
            headers=ORIGIN,
        )
        assert stale_login.status_code == 200, stale_login.text
        with SessionLocal() as mutate_db:
            stale_staff = mutate_db.get(Staff, nested_staff_id)
            assert stale_staff is not None
            stale_staff.organization_id = other_organization_id
            mutate_db.commit()
        assert client.get("/api/auth/me", headers=ORIGIN).status_code == 403
        assert client.get("/api/auth/me", headers=ORIGIN).status_code == 401

        invalid_link_login = client.post(
            "/api/auth/login",
            json={
                "username": "wrong-org-directory-user",
                "password": wrong_link_password,
            },
            headers=ORIGIN,
        )
        assert invalid_link_login.status_code == 403, invalid_link_login.text


def test_service_period_allows_only_one_open_row_per_source_account() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            staff = Staff(
                organization_id=organization.id,
                internal_code="OPEN-PERIOD-GUARD",
                display_name="열린 기간 보호 시험",
                job_title="",
            )
            db.add(staff)
            db.flush()
            account = StaffSourceAccount(
                organization_id=organization.id,
                staff_id=staff.id,
                source_system="carefor",
                service_type="facility",
                external_id="OPEN-PERIOD-GUARD",
                display_name_snapshot=staff.display_name,
                job_name_snapshot="",
                employment_status="active",
                source_status="재직",
                latest_start_date=date(2025, 1, 1),
                is_active=True,
            )
            db.add(account)
            db.flush()
            db.add_all(
                [
                    StaffServicePeriod(
                        organization_id=organization.id,
                        staff_id=staff.id,
                        source_account_id=account.id,
                        start_date=date(2024, 1, 1),
                        end_date=None,
                        employment_status="active",
                    ),
                    StaffServicePeriod(
                        organization_id=organization.id,
                        staff_id=staff.id,
                        source_account_id=account.id,
                        start_date=date(2025, 1, 1),
                        end_date=None,
                        employment_status="active",
                    ),
                ]
            )
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()


def test_service_assignment_allows_only_one_open_row_per_staff_service() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            staff = Staff(
                organization_id=organization.id,
                internal_code="OPEN-ASSIGNMENT-GUARD",
                display_name="열린 배정 보호 시험",
                job_title="",
            )
            db.add(staff)
            db.flush()
            assignments: list[StaffServiceAssignment] = []
            for index in (1, 2):
                account = StaffSourceAccount(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_system="carefor",
                    service_type="facility",
                    external_id=f"OPEN-ASSIGNMENT-GUARD-{index}",
                    display_name_snapshot=staff.display_name,
                    job_name_snapshot="",
                    employment_status="active",
                    source_status="재직",
                    latest_start_date=date(2024 + index, 1, 1),
                    is_active=True,
                )
                db.add(account)
                db.flush()
                period = StaffServicePeriod(
                    organization_id=organization.id,
                    staff_id=staff.id,
                    source_account_id=account.id,
                    start_date=date(2024 + index, 1, 1),
                    end_date=None,
                    employment_status="active",
                )
                db.add(period)
                db.flush()
                assignments.append(
                    StaffServiceAssignment(
                        organization_id=organization.id,
                        staff_id=staff.id,
                        source_account_id=account.id,
                        service_period_id=period.id,
                        service_type="facility",
                        start_date=period.start_date,
                        end_date=None,
                        assignment_basis="carefor_auto",
                    )
                )
            db.add_all(assignments)
            with pytest.raises(IntegrityError):
                db.flush()
            db.rollback()
