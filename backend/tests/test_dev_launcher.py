from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.config import settings
from app.main import app


ORIGIN = {"origin": "http://testserver"}


def test_development_launcher_switches_users_without_their_password(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_launcher_enabled", True)
    monkeypatch.setattr(settings, "dev_launcher_username", "test_dev_launcher")
    monkeypatch.setattr(
        settings,
        "dev_launcher_password",
        SecretStr("DevLauncherTest!234"),
    )
    monkeypatch.setattr(settings, "dev_launcher_name", "개발자 전환 시험")

    with TestClient(app, base_url="http://localhost") as client:
        login = client.post(
            "/api/auth/login",
            json={
                "username": " test_dev_launcher ",
                "password": "  DevLauncherTest!234  ",
            },
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text
        assert login.json()["user"]["is_dev_launcher"] is True

        employees = client.get("/api/employees")
        assert employees.status_code == 200, employees.text
        assert not any(
            item["username"] == "test_dev_launcher"
            for item in employees.json()
        )

        users = client.get("/api/dev/users")
        assert users.status_code == 200, users.text
        target = next(item for item in users.json() if item["username"] == "admin")

        switched = client.post(
            f"/api/dev/switch/{target['id']}",
            json={},
            headers=ORIGIN,
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["user"]["username"] == "admin"
        assert switched.json()["user"]["is_dev_impersonated"] is True

        current = client.get("/api/auth/me")
        assert current.status_code == 200, current.text
        assert current.json()["username"] == "admin"
        assert current.json()["is_dev_impersonated"] is True

        returned = client.post("/api/dev/return", json={}, headers=ORIGIN)
        assert returned.status_code == 200, returned.text
        assert returned.json()["user"]["username"] == "test_dev_launcher"
        assert returned.json()["user"]["is_dev_launcher"] is True

        launcher_current = client.get("/api/auth/me")
        assert launcher_current.status_code == 200, launcher_current.text
        assert launcher_current.json()["is_dev_launcher"] is True


def test_launcher_return_recovers_missing_controller_cookie(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_launcher_enabled", True)
    monkeypatch.setattr(settings, "dev_launcher_username", "test_dev_launcher")
    monkeypatch.setattr(
        settings,
        "dev_launcher_password",
        SecretStr("DevLauncherTest!234"),
    )
    monkeypatch.setattr(settings, "dev_launcher_name", "개발자 전환 시험")

    with TestClient(app, base_url="http://localhost") as client:
        login = client.post(
            "/api/auth/login",
            json={
                "username": "test_dev_launcher",
                "password": "DevLauncherTest!234",
            },
            headers=ORIGIN,
        )
        assert login.status_code == 200, login.text

        users = client.get("/api/dev/users")
        assert users.status_code == 200, users.text
        target = next(item for item in users.json() if item["username"] == "admin")
        switched = client.post(
            f"/api/dev/switch/{target['id']}",
            json={},
            headers=ORIGIN,
        )
        assert switched.status_code == 200, switched.text
        assert switched.json()["user"]["is_dev_impersonated"] is True

        client.cookies.delete(settings.dev_launcher_cookie_name)
        returned = client.post("/api/dev/return", json={}, headers=ORIGIN)
        assert returned.status_code == 200, returned.text
        assert returned.json()["user"]["username"] == "test_dev_launcher"
        assert returned.json()["user"]["is_dev_launcher"] is True
        assert client.cookies.get(settings.dev_launcher_cookie_name)

        launcher_current = client.get("/api/auth/me")
        assert launcher_current.status_code == 200, launcher_current.text
        assert launcher_current.json()["username"] == "test_dev_launcher"
        assert launcher_current.json()["is_dev_launcher"] is True


def test_public_hostname_without_proxy_header_uses_secure_session_cookie(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "dev_launcher_enabled", True)
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "dev_launcher_username", "test_dev_launcher")
    monkeypatch.setattr(
        settings,
        "dev_launcher_password",
        SecretStr("DevLauncherTest!234"),
    )
    monkeypatch.setattr(
        settings,
        "allowed_origins",
        "http://localhost:8080,https://chat.silvermedical.kr",
    )
    public_headers = {
        # 공개 게이트웨이가 원래 Host를 백엔드에 전달하는 상황입니다.
        "host": "chat.silvermedical.kr",
        "origin": "https://chat.silvermedical.kr",
    }

    with TestClient(
        app,
        # Caddy와 백엔드 사이의 HTTP 요청에서 X-Forwarded-Proto가
        # 누락된 상황을 재현합니다.
        base_url="http://chat.silvermedical.kr",
    ) as client:
        launcher_login = client.post(
            "/api/auth/login",
            json={
                "username": "test_dev_launcher",
                "password": "DevLauncherTest!234",
            },
            headers=public_headers,
        )
        assert launcher_login.status_code == 404, launcher_login.text

        admin_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "AdminPass!234",
            },
            headers=public_headers,
        )
        assert admin_login.status_code == 200, admin_login.text
        assert "; Secure" in admin_login.headers["set-cookie"]


def test_localhost_http_session_cookie_remains_non_secure(monkeypatch):
    monkeypatch.setattr(settings, "environment", "development")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(
        settings,
        "allowed_origins",
        "http://localhost:8080,https://chat.silvermedical.kr",
    )

    with TestClient(app, base_url="http://localhost") as client:
        admin_login = client.post(
            "/api/auth/login",
            json={
                "username": "admin",
                "password": "AdminPass!234",
            },
            headers={"origin": "http://localhost:8080"},
        )
        assert admin_login.status_code == 200, admin_login.text
        assert "; Secure" not in admin_login.headers["set-cookie"]
        current = client.get("/api/auth/me")
        assert current.status_code == 200, current.text
