from __future__ import annotations

import json
from inspect import signature

from fastapi.testclient import TestClient

from app import ai_assist, ai_provider_status
from app.ai_provider_status import collect_ai_provider_status
from app.dependencies import require_admin
from app.main import app


def _provider(response, name: str):  # noqa: ANN001, ANN202
    return next(item for item in response.providers if item.provider == name)


def _model_list_response(_url: str, _timeout: float) -> dict:
    return {
        "models": [
            {"name": "qwen3.6:35b"},
            {"model": "small-local:test"},
        ]
    }


def test_provider_status_contract_discovers_local_models_without_enabling_unverified_ai(
    monkeypatch,
):
    monkeypatch.setattr(
        ai_provider_status.settings,
        "ai_review_base_url",
        "http://127.0.0.1:11434",
    )
    monkeypatch.setattr(ai_provider_status.settings, "ai_review_model", "qwen3.6:35b")
    monkeypatch.setattr(ai_provider_status, "nemotron_is_configured", lambda: True)

    response = collect_ai_provider_status(json_get=_model_list_response)

    assert response.contract_version == "ai_provider_status_v1"
    assert response.secret_values_included is False
    assert [item.provider for item in response.providers] == [
        "rules",
        "ollama",
        "nvidia",
        "openai",
        "gemini",
        "anthropic",
        "openai_compatible",
    ]
    rules = _provider(response, "rules")
    assert rules.state == "ready"
    assert rules.enabled_features == ["instant_period_summary", "rules_fallback"]
    ollama = _provider(response, "ollama")
    assert ollama.state == "ready"
    assert ollama.endpoint_scope == "local"
    assert ollama.discovered_models == ["qwen3.6:35b", "small-local:test"]
    assert ollama.enabled_features == []
    structured_check = next(
        check for check in ollama.checks if check.name == "structured_contract"
    )
    assert structured_check.status == "not_run"
    nvidia = _provider(response, "nvidia")
    assert nvidia.state == "configured_unverified"
    assert nvidia.credential_configured is True
    assert nvidia.external_transmission_default_allowed is False
    assert nvidia.requires_deidentified_approval is True
    auth_check = next(
        check for check in nvidia.checks if check.name == "authentication"
    )
    assert auth_check.status == "not_run"
    assert _provider(response, "openai").state == "not_implemented"

    serialized = json.dumps(response.model_dump(mode="json"), ensure_ascii=False)
    assert "api_key" not in serialized.lower()
    assert "secret_path" not in serialized.lower()


def test_provider_status_does_not_contact_external_ollama_endpoint(monkeypatch):
    calls: list[str] = []

    def unexpected_get(url: str, _timeout: float) -> dict:
        calls.append(url)
        raise AssertionError("external endpoint must not be contacted")

    monkeypatch.setattr(
        ai_provider_status.settings,
        "ai_review_base_url",
        "https://models.example.test/v1",
    )
    monkeypatch.setattr(ai_provider_status, "nemotron_is_configured", lambda: False)

    response = collect_ai_provider_status(json_get=unexpected_get)

    assert calls == []
    ollama = _provider(response, "ollama")
    assert ollama.state == "unavailable"
    assert ollama.endpoint_scope == "external"
    assert ollama.configured is False
    assert ollama.external_transmission_default_allowed is False


def test_provider_status_route_requires_login_and_never_returns_secrets(monkeypatch):
    monkeypatch.setattr(
        ai_provider_status.settings,
        "ai_review_base_url",
        "http://127.0.0.1:11434",
    )
    monkeypatch.setattr(ai_provider_status, "nemotron_is_configured", lambda: False)
    safe_response = collect_ai_provider_status(json_get=_model_list_response)
    monkeypatch.setattr(ai_assist, "collect_ai_provider_status", lambda: safe_response)

    admin_dependency = signature(ai_assist.ai_provider_status_route).parameters[
        "_admin"
    ].default
    assert admin_dependency.dependency is require_admin

    with TestClient(app) as client:
        unauthenticated = client.get("/api/ai/providers/status")
        assert unauthenticated.status_code == 401

        login = client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers={"origin": "http://testserver"},
        )
        assert login.status_code == 200
        response = client.get("/api/ai/providers/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["contract_version"] == "ai_provider_status_v1"
    assert payload["secret_values_included"] is False
    serialized = json.dumps(payload, ensure_ascii=False)
    assert "api_key" not in serialized.lower()
    assert "authorization" not in serialized.lower()
