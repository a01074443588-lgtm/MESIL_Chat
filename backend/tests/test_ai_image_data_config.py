from app.config import Settings


def test_external_real_image_data_defaults_to_disabled(monkeypatch):
    monkeypatch.delenv("AI_ASSIST_ALLOW_EXTERNAL_REAL_IMAGE_DATA", raising=False)

    settings = Settings(_env_file=None)

    assert settings.ai_assist_allow_external_real_image_data is False


def test_external_real_image_data_requires_explicit_true(monkeypatch):
    monkeypatch.setenv("AI_ASSIST_ALLOW_EXTERNAL_REAL_IMAGE_DATA", "true")

    settings = Settings(_env_file=None)

    assert settings.ai_assist_allow_external_real_image_data is True
