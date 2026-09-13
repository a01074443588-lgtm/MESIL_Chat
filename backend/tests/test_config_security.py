import pytest
from pydantic import ValidationError

from app.config import Settings


def production_settings(**overrides):
    values = {
        "environment": "production",
        "cookie_secure": True,
        "trust_proxy_headers": True,
        "dev_launcher_enabled": False,
        "allowed_origins": "https://chat.silvermedical.kr",
    }
    values.update(overrides)
    return Settings(**values)


def test_production_security_accepts_https_gateway_configuration():
    settings = production_settings()
    assert settings.origin_list == ["https://chat.silvermedical.kr"]
    assert "chat.silvermedical.kr" in settings.trusted_host_list


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("cookie_secure", False),
        ("trust_proxy_headers", False),
        ("dev_launcher_enabled", True),
        ("allowed_origins", "http://localhost:8080"),
    ],
)
def test_production_security_rejects_unsafe_configuration(field, value):
    with pytest.raises(ValidationError):
        production_settings(**{field: value})


def test_password_reviewer_requires_one_configured_account_and_expiry(monkeypatch):
    for environment_variable in (
        "MENTOR_REVIEWER_USERNAME",
        "REVIEWER_SOCIAL_USERNAME",
        "REVIEWER_ACCESS_ENDS_AT",
        "REVIEWER_SESSION_SECRET",
    ):
        monkeypatch.delenv(environment_variable, raising=False)

    with pytest.raises(ValidationError):
        Settings(_env_file=None, reviewer_password_login_enabled=True)

    settings = Settings(
        reviewer_password_login_enabled=True,
        reviewer_password_login_experience="social_worker",
        reviewer_social_username="reviewer-one",
        reviewer_access_ends_at="2026-09-26T23:59:59+09:00",
        reviewer_session_secret="x" * 32,
    )

    assert settings.reviewer_password_login_enabled is True
    assert settings.reviewer_password_login_experience == "social_worker"
    assert settings.reviewer_access_active is True


def test_password_reviewer_can_use_git_ignored_mentor_username():
    settings = Settings(
        reviewer_password_login_enabled=True,
        reviewer_password_login_experience="social_worker",
        mentor_reviewer_username="mentor-reviewer",
        reviewer_access_ends_at="2026-09-26T23:59:59+09:00",
        reviewer_session_secret="x" * 32,
    )

    assert settings.reviewer_username_for_experience("social_worker") == "mentor-reviewer"
    assert settings.reviewer_usernames == ["mentor-reviewer"]
    assert settings.reviewer_social_username is None


def test_self_chat_can_be_disabled_for_an_exactly_five_room_presentation() -> None:
    settings = Settings(_env_file=None, self_chat_enabled=False)

    assert settings.self_chat_enabled is False
