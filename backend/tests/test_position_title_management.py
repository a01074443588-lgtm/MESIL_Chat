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


def test_admin_manages_position_titles_and_employee_selection() -> None:
    with TestClient(app) as client:
        login_admin(client)

        initial = client.get("/api/position-titles?include_inactive=true")
        assert initial.status_code == 200, initial.text
        assert any(item["name"] == "원장" for item in initial.json())

        created = client.post(
            "/api/position-titles",
            json={"name": "부원장"},
            headers=ORIGIN,
        )
        assert created.status_code == 201, created.text
        position_id = created.json()["id"]

        employee = client.post(
            "/api/employees",
            json={
                "username": "position-test",
                "full_name": "직위 시험 직원",
                "password": "PositionTest!234",
                "role": "staff",
                "can_process_records": False,
                "job_code": "social_worker",
                "position_title": "부원장",
            },
            headers=ORIGIN,
        )
        assert employee.status_code == 201, employee.text
        employee_id = employee.json()["id"]
        assert employee.json()["position_title"] == "부원장"

        blocked = client.patch(
            f"/api/position-titles/{position_id}",
            json={"is_active": False},
            headers=ORIGIN,
        )
        assert blocked.status_code == 409, blocked.text

        renamed = client.patch(
            f"/api/position-titles/{position_id}",
            json={"name": "운영원장"},
            headers=ORIGIN,
        )
        assert renamed.status_code == 200, renamed.text
        assert renamed.json()["name"] == "운영원장"

        employees = client.get("/api/employees")
        renamed_employee = next(
            item for item in employees.json() if item["id"] == employee_id
        )
        assert renamed_employee["position_title"] == "운영원장"

        invalid = client.patch(
            f"/api/employees/{employee_id}",
            json={"position_title": "목록에 없는 직위"},
            headers=ORIGIN,
        )
        assert invalid.status_code == 422, invalid.text

        cleared = client.patch(
            f"/api/employees/{employee_id}",
            json={"position_title": None},
            headers=ORIGIN,
        )
        assert cleared.status_code == 200, cleared.text
        assert cleared.json()["position_title"] is None

        deactivated = client.delete(
            f"/api/position-titles/{position_id}",
            headers=ORIGIN,
        )
        assert deactivated.status_code == 200, deactivated.text
        assert deactivated.json()["is_active"] is False
        assert deactivated.json()["can_delete"] is True

        purged = client.delete(
            f"/api/position-titles/{position_id}/purge",
            headers=ORIGIN,
        )
        assert purged.status_code == 204, purged.text

        remaining = client.get("/api/position-titles?include_inactive=true")
        assert not any(item["id"] == position_id for item in remaining.json())
