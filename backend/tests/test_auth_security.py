from fastapi.testclient import TestClient

from app.main import app


ORIGIN = {"origin": "http://testserver"}


def post(client: TestClient, path: str, payload: dict):
    return client.post(path, json=payload, headers=ORIGIN)


def login(client: TestClient, username: str, password: str):
    response = post(
        client,
        "/api/auth/login",
        {"username": username, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_password_change_session_control_admin_reset_and_rate_limit():
    with TestClient(app) as admin_client:
        login(admin_client, "admin", "AdminPass!234")
        created = post(
            admin_client,
            "/api/employees",
            {
                "username": "security-user",
                "full_name": "가상 보안직원",
                "password": "Temporary!234",
                "role": "staff",
                "employee_code": "SEC-001",
            },
        )
        assert created.status_code == 201, created.text
        employee_id = created.json()["id"]
        assert created.json()["must_change_password"] is True

        primary = TestClient(app)
        primary.headers["user-agent"] = "SMCODI Test Primary"
        first_login = login(primary, "security-user", "Temporary!234")
        assert first_login["user"]["must_change_password"] is True
        blocked_room = primary.get("/api/rooms")
        assert blocked_room.status_code == 403
        assert "비밀번호" in blocked_room.json()["detail"]

        changed = post(
            primary,
            "/api/auth/password",
            {
                "current_password": "Temporary!234",
                "new_password": "FirstSecure!234",
            },
        )
        assert changed.status_code == 200, changed.text
        assert changed.json()["must_change_password"] is False
        assert primary.get("/api/rooms").status_code == 200

        secondary = TestClient(app)
        secondary.headers["user-agent"] = "SMCODI Test Secondary"
        login(secondary, "security-user", "FirstSecure!234")
        sessions = primary.get("/api/auth/sessions")
        assert sessions.status_code == 200, sessions.text
        assert len(sessions.json()) == 2
        assert sum(item["is_current"] for item in sessions.json()) == 1

        with secondary.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            changed_again = post(
                primary,
                "/api/auth/password",
                {
                    "current_password": "FirstSecure!234",
                    "new_password": "SecondSecure!234",
                },
            )
            assert changed_again.status_code == 200, changed_again.text
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "비밀번호" in forced["reason"]
        assert secondary.get("/api/auth/me").status_code == 401
        assert len(primary.get("/api/auth/sessions").json()) == 1

        third = TestClient(app)
        third.headers["user-agent"] = "SMCODI Test Third"
        login(third, "security-user", "SecondSecure!234")
        sessions = primary.get("/api/auth/sessions").json()
        third_session = next(item for item in sessions if not item["is_current"])
        with third.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            revoked = primary.delete(
                f"/api/auth/sessions/{third_session['id']}",
                headers=ORIGIN,
            )
            assert revoked.status_code == 204, revoked.text
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "다른 기기" in forced["reason"]
        assert third.get("/api/auth/me").status_code == 401

        with primary.websocket_connect("/api/ws", headers=ORIGIN) as websocket:
            assert websocket.receive_json()["event"] == "ready"
            reset = post(
                admin_client,
                f"/api/employees/{employee_id}/reset-password",
                {"temporary_password": "ResetSecure!234"},
            )
            assert reset.status_code == 200, reset.text
            assert reset.json()["must_change_password"] is True
            forced = websocket.receive_json()
            assert forced["event"] == "force_logout"
            assert "초기화" in forced["reason"]
        assert primary.get("/api/auth/me").status_code == 401
        old_login = post(
            TestClient(app),
            "/api/auth/login",
            {"username": "security-user", "password": "SecondSecure!234"},
        )
        assert old_login.status_code == 401
        reset_login = login(
            TestClient(app),
            "security-user",
            "ResetSecure!234",
        )
        assert reset_login["user"]["must_change_password"] is True

        limited_client = TestClient(app)
        for _ in range(5):
            failed = post(
                limited_client,
                "/api/auth/login",
                {"username": "rate-limit-user", "password": "WrongPassword!"},
            )
            assert failed.status_code == 401
        limited = post(
            limited_client,
            "/api/auth/login",
            {"username": "rate-limit-user", "password": "WrongPassword!"},
        )
        assert limited.status_code == 429
        assert int(limited.headers["retry-after"]) > 0
