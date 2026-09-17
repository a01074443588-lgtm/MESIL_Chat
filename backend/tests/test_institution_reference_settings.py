from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app


ORIGIN = {"origin": "http://testserver"}


def login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def test_living_space_can_leave_active_list_without_erasing_resident_history() -> None:
    suffix = uuid4().hex[:8]
    with TestClient(app) as client:
        login_admin(client)
        created_space = client.post(
            "/api/org-units",
            json={"unit_type": "floor", "name": f"생활실-{suffix}"},
            headers=ORIGIN,
        )
        assert created_space.status_code == 201, created_space.text
        space = created_space.json()
        assert space["is_test_data"] is False

        resident = client.post(
            "/api/admin/residents",
            json={
                "display_name": f"가상어르신-{suffix}",
                "service_type": "facility",
                "floor_id": space["id"],
            },
            headers=ORIGIN,
        )
        assert resident.status_code == 201, resident.text
        resident_id = resident.json()["id"]

        removed = client.delete(f"/api/org-units/{space['id']}", headers=ORIGIN)
        assert removed.status_code == 200, removed.text
        assert removed.json()["is_active"] is False
        assert removed.json()["active_resident_count"] == 1

        active_space_ids = {
            item["id"]
            for item in client.get("/api/org-units?unit_type=floor").json()
        }
        assert space["id"] not in active_space_ids
        retained_resident = next(
            item
            for item in client.get("/api/admin/residents").json()
            if item["id"] == resident_id
        )
        assert retained_resident["floor"]["id"] == space["id"]
        assert retained_resident["floor"]["is_active"] is False

        cleared = client.patch(
            f"/api/admin/residents/{resident_id}",
            json={"floor_id": None},
            headers=ORIGIN,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["floor"] is None


def test_used_job_can_leave_selection_and_current_employee_can_be_unassigned() -> None:
    suffix = uuid4().hex[:8]
    job_code = f"custom_{suffix}"
    with TestClient(app) as client:
        login_admin(client)
        created_job = client.post(
            "/api/job-codes",
            json={"code": job_code, "name": f"시험직종-{suffix}"},
            headers=ORIGIN,
        )
        assert created_job.status_code == 201, created_job.text

        employee = client.post(
            "/api/employees",
            json={
                "username": f"setting-{suffix}",
                "full_name": f"가상 직원 {suffix}",
                "password": "SettingTest!234",
                "role": "staff",
                "can_process_records": False,
                "employee_code": f"SET-{suffix}",
                "job_code": job_code,
            },
            headers=ORIGIN,
        )
        assert employee.status_code == 201, employee.text
        employee_id = employee.json()["id"]

        removed = client.delete(f"/api/job-codes/{job_code}", headers=ORIGIN)
        assert removed.status_code == 200, removed.text
        assert removed.json()["is_active"] is False
        assert removed.json()["active_staff_count"] == 1

        retained_employee = next(
            item for item in client.get("/api/employees").json()
            if item["id"] == employee_id
        )
        assert retained_employee["job_code"] == job_code

        cleared = client.patch(
            f"/api/employees/{employee_id}",
            json={"job_code": None},
            headers=ORIGIN,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["job_code"] is None
