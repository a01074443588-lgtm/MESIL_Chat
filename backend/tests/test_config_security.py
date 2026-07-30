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
