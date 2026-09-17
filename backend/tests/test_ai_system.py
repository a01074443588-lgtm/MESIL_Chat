from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app import ai_provider_adapters, ai_router, ai_secret_store, ai_settings_store
from app.ai_hardware import recommend_execution_mode
from app.ai_privacy import classify_ai_input
from app.ai_assist import turn_response
from app.ai_provider_adapters import (
    generate_ai_assist_result,
    HttpJsonResponse,
    clear_provider_probe_cache,
    collect_provider_public_statuses,
    test_provider_connection as run_provider_connection_test,
)
from app.ai_router import (
    AiRouteAttemptError,
    build_routing_preview,
    execute_with_fallback,
)
from app.ai_settings_store import default_ai_settings, save_ai_settings
from app.ai_system import (
    _auto_select_model_roles,
    _role_model_options,
    connect_ai_provider,
    update_ai_model_roles,
    update_ai_execution_mode,
    update_ai_provider_settings,
    update_ai_system_settings,
)
from app.ai_system_schemas import (
    AiGpuInfo,
    AiHardwareStatus,
    AiModeUpdateRequest,
    AiModelRoleSelection,
    AiModelRoles,
    AiPrivacyClassificationRequest,
    AiProviderConnectRequest,
    AiProviderPublicStatus,
    AiProviderSettingsPatch,
    AiProviderTestResponse,
    AiRoutingPreviewRequest,
    AiSettingsUpdateRequest,
)
from app.config import settings
from app.main import app


def _hardware(
    *,
    vram_gb: float = 0,
    ollama_location: str = "unconfigured",
    ollama_ready: bool | None = None,
) -> AiHardwareStatus:
    gpus = [AiGpuInfo(name="Test GPU", vram_gb=vram_gb)] if vram_gb else []
    reachable = (
        ollama_location != "unconfigured" if ollama_ready is None else ollama_ready
    )
    return AiHardwareStatus(
        containerized=False,
        os_name="Test OS",
        cpu_name="Test CPU",
        logical_cpu_count=8,
        system_ram_gb=32,
        nvidia_gpus=gpus,
        max_nvidia_vram_gb=vram_gb,
        ollama_installed_or_reachable=reachable,
        ollama_processing_location=ollama_location,
        local_models=["qwen3.6:35b"] if ollama_location != "unconfigured" else [],
        local_vl_models=["qwen3.6:35b"] if ollama_location != "unconfigured" else [],
        stt_service_ready=False,
        stt_model=None,
        external_credentials_configured=[],
    )


def _status(
    provider: str,
    *,
    location: str,
    capabilities: list[str],
    ready: bool = True,
    contract: bool = True,
    enabled: bool = True,
    model: str | None = None,
    models: list[str] | None = None,
    structured_contract_verified: bool | None = None,
) -> AiProviderPublicStatus:
    selected_model = model or (
        "quick-period-summary-v1" if provider == "rules" else "test-model"
    )
    return AiProviderPublicStatus(
        provider=provider,
        display_name=provider,
        enabled=enabled,
        adapter_available=True,
        credential_configured=location == "external",
        endpoint_configured=True,
        endpoint_scope="rules" if provider == "rules" else location,
        processing_location=location,
        connection_state="ready" if ready else "configured_unverified",
        configured_model=selected_model,
        models=models or [selected_model],
        capabilities=capabilities,
        timeout_seconds=30,
        structured_contract_verified=(
            contract
            if structured_contract_verified is None
            else structured_contract_verified
        ),
    )


@pytest.fixture(autouse=True)
def _clear_probe_records(monkeypatch, tmp_path):
    probe_path = tmp_path / "ai-provider-probes.json"
    monkeypatch.setattr(settings, "ai_provider_probe_file", probe_path.as_posix())
    clear_provider_probe_cache(delete_persisted=True)
    yield
    clear_provider_probe_cache(delete_persisted=True)


def test_no_key_still_exposes_rules_without_external_network(monkeypatch):
    document = default_ai_settings().model_copy(
        update={
            "providers": {
                key: value.model_copy(update={"enabled": key == "rules"})
                for key, value in default_ai_settings().providers.items()
            }
        }
    )
    calls: list[str] = []

    def transport(method, url, headers, payload, timeout):
        del method, headers, payload, timeout
        calls.append(url)
        raise AssertionError("비활성 공급자는 네트워크를 호출하면 안 됩니다.")

    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: False)
    statuses = collect_provider_public_statuses(
        document.providers,
        transport=transport,
    )

    rules = next(item for item in statuses if item.provider == "rules")
    assert rules.connection_state == "ready"
    assert rules.structured_contract_verified is True
    assert calls == []
    assert all(item.secret_value_included is False for item in statuses)


def test_provider_model_and_contract_probe_uses_mocked_deidentified_fixture(monkeypatch):
    document = default_ai_settings()
    provider_settings = document.providers["openai"].model_copy(
        update={"enabled": True, "model": "test-model"}
    )
    requests: list[dict] = []

    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: True)
    monkeypatch.setattr(
        ai_provider_adapters,
        "get_provider_secret",
        lambda _: SecretStr("test-provider-key-value"),
    )

    def transport(method, url, headers, payload, timeout):
        requests.append(
            {
                "method": method,
                "url_suffix": url.rsplit("/", 1)[-1],
                "has_auth": headers.get("Authorization", "").startswith("Bearer "),
                "payload": payload,
                "timeout": timeout,
            }
        )
        if method == "GET":
            return HttpJsonResponse(200, {"data": [{"id": "test-model"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "비식별 합성 사례를 확인했습니다.",
                                    "visible_text": None,
                                    "image_texts": [],
                                    "evidence": [
                                        {
                                            "source_no": 1,
                                            "statement": "대상-A 합성 근거",
                                        }
                                    ],
                                    "uncertainties": [],
                                    "recommended_actions": ["원문 확인"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = run_provider_connection_test(
        "openai",
        provider_settings,
        run_contract_test=True,
        transport=transport,
    )

    assert result.connection_ok is True
    assert result.structured_contract_ok is True
    assert result.deidentified_fixture_used is True
    assert result.external_call_performed is True
    assert result.secret_value_included is False
    assert len(requests) == 2
    assert all(item["has_auth"] for item in requests)
    assert "test-provider-key-value" not in result.model_dump_json()
    prompt_dump = json.dumps(requests[1]["payload"], ensure_ascii=False)
    assert "CBV1-SAFETY-FOLLOWUP" in prompt_dump
    assert "subject-a" in prompt_dump


def test_runtime_contract_fixture_is_packaged_and_deidentified():
    prompt = ai_provider_adapters._fixture_prompt()

    assert "CBV1-SAFETY-FOLLOWUP" in prompt
    assert '"privacy_mode":"external_deidentified"' in prompt
    assert '"contains_direct_identifiers":false' in prompt
    assert '"subject_id":"subject-a"' in prompt


def test_nvidia_contract_probe_disables_thinking_and_keeps_json_budget(monkeypatch):
    provider_settings = default_ai_settings().providers["nvidia"].model_copy(
        update={"enabled": True, "model": "test-nvidia-model"}
    )
    requests: list[dict] = []

    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: True)
    monkeypatch.setattr(
        ai_provider_adapters,
        "get_provider_secret",
        lambda _: SecretStr("test-provider-key-value"),
    )

    def transport(method, url, headers, payload, timeout):
        del url, headers, timeout
        if method == "GET":
            return HttpJsonResponse(200, {"data": [{"id": "test-nvidia-model"}]})
        requests.append(payload)
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "비식별 합성 사례를 확인했습니다.",
                                    "visible_text": None,
                                    "image_texts": [],
                                    "evidence": [
                                        {"source_no": 1, "statement": "합성 근거"}
                                    ],
                                    "uncertainties": [],
                                    "recommended_actions": ["원문 확인"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = run_provider_connection_test(
        "nvidia",
        provider_settings,
        run_contract_test=True,
        transport=transport,
    )

    assert result.connection_ok is True
    assert result.structured_contract_ok is True
    assert len(requests) == 1
    assert requests[0]["max_tokens"] == 4096
    assert requests[0]["chat_template_kwargs"] == {"enable_thinking": False}
    assert requests[0]["guided_json"]["title"] == "AiAssistResult"


def test_internal_provider_transport_bypasses_environment_proxy(monkeypatch):
    captured: dict[str, object] = {}

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"models":[]}'

    class Opener:
        def open(self, request, *, timeout):
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            return Response()

    def fake_build_opener(handler):
        captured["proxies"] = handler.proxies
        return Opener()

    monkeypatch.setattr(ai_provider_adapters, "build_opener", fake_build_opener)
    monkeypatch.setattr(
        ai_provider_adapters,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("내부 Endpoint는 환경 proxy를 사용하면 안 됩니다.")
        ),
    )

    response = ai_provider_adapters._urllib_transport(
        "GET",
        "http://ollama-x370d-dev:11434/api/tags",
        {},
        None,
        2,
    )

    assert response.status_code == 200
    assert response.payload == {"models": []}
    assert captured["proxies"] == {}


def test_verified_provider_probe_survives_restart_without_endpoint_or_secret(
    monkeypatch,
):
    provider_settings = default_ai_settings().providers["openai_compatible"].model_copy(
        update={
            "enabled": True,
            "base_url": "http://127.0.0.1:11434/v1",
            "model": "test-local-model",
        }
    )
    monkeypatch.setattr(
        ai_provider_adapters,
        "provider_secret_persistence_revision",
        lambda _: "protected-file:123:20",
    )
    monkeypatch.setattr(
        ai_provider_adapters,
        "provider_secret_configured",
        lambda _: False,
    )

    def transport(method, url, headers, payload, timeout):
        del url, headers, payload, timeout
        if method == "GET":
            return HttpJsonResponse(200, {"data": [{"id": "test-local-model"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "비식별 합성 사례를 확인했습니다.",
                                    "visible_text": None,
                                    "image_texts": [],
                                    "evidence": [
                                        {
                                            "source_no": 1,
                                            "statement": "대상-A 합성 근거",
                                        }
                                    ],
                                    "uncertainties": [],
                                    "recommended_actions": ["원문 확인"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = run_provider_connection_test(
        "openai_compatible",
        provider_settings,
        run_contract_test=True,
        transport=transport,
    )
    assert result.structured_contract_ok is True
    persisted_payload = Path(settings.ai_provider_probe_file).read_text(encoding="utf-8")
    assert "127.0.0.1" not in persisted_payload
    assert "test-provider-key-value" not in persisted_payload

    clear_provider_probe_cache()
    document = default_ai_settings()
    document.providers["openai_compatible"] = provider_settings
    statuses = collect_provider_public_statuses(document.providers, transport=transport)
    compatible = next(
        item for item in statuses if item.provider == "openai_compatible"
    )
    assert compatible.connection_state == "ready"
    assert compatible.structured_contract_verified is True

    document.providers["openai_compatible"] = provider_settings.model_copy(
        update={"model": "changed-model"}
    )
    changed_statuses = collect_provider_public_statuses(
        document.providers,
        transport=transport,
    )
    changed = next(
        item for item in changed_statuses if item.provider == "openai_compatible"
    )
    assert changed.connection_state == "ready"
    assert changed.structured_contract_verified is False


def test_generic_openai_adapter_returns_validated_ai_assist_result(monkeypatch):
    provider_settings = default_ai_settings().providers["openai"].model_copy(
        update={"enabled": True, "model": "test-model"}
    )
    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: True)
    monkeypatch.setattr(
        ai_provider_adapters,
        "get_provider_secret",
        lambda _: SecretStr("test-provider-key-value"),
    )

    def transport(method, url, headers, payload, timeout):
        del method, url, headers, payload, timeout
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "근거 1을 확인했습니다.",
                                    "visible_text": None,
                                    "image_texts": [],
                                    "evidence": [
                                        {"source_no": 1, "statement": "합성 근거"}
                                    ],
                                    "uncertainties": ["사람 확인 필요"],
                                    "recommended_actions": ["원문 확인"],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result, elapsed_ms = generate_ai_assist_result(
        "openai",
        provider_settings,
        model="test-model",
        task_type="summary",
        question="요약해줘",
        sources=[
            SimpleNamespace(
                source_no=1,
                source_type="message",
                label="합성 메시지",
                text="대상-A 합성 근거",
                metadata={},
            )
        ],
        images=[],
        transport=transport,
    )

    assert result.answer == "근거 1을 확인했습니다."
    assert result.evidence[0].source_no == 1
    assert elapsed_ms >= 0


@pytest.mark.parametrize(
    ("status_code", "expected_code"),
    [
        (400, "contract_validation_failed"),
        (401, "authentication_failed"),
        (402, "billing_required"),
        (429, "rate_limited"),
        (504, "timeout"),
    ],
)
def test_provider_failure_codes_are_sanitized(
    monkeypatch,
    status_code,
    expected_code,
):
    provider_settings = default_ai_settings().providers["openai"].model_copy(
        update={"enabled": True}
    )
    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: True)
    monkeypatch.setattr(
        ai_provider_adapters,
        "get_provider_secret",
        lambda _: SecretStr("test-provider-key-value"),
    )

    def transport(*_args):
        return HttpJsonResponse(status_code, {"error": "raw-provider-secret-detail"})

    result = run_provider_connection_test(
        "openai",
        provider_settings,
        run_contract_test=False,
        transport=transport,
    )

    assert result.connection_ok is False
    assert result.error_code == expected_code
    assert "raw-provider-secret-detail" not in result.message


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        ({"error": {"message": "Free tier quota exhausted"}}, "free_tier_exhausted"),
        ({"error": {"code": "insufficient_quota"}}, "billing_required"),
        ({"error": {"message": "quota exceeded"}}, "quota_exceeded"),
        ({"error": {"message": "too many requests"}}, "rate_limited"),
    ],
)
def test_429_failure_classification_does_not_assume_billing(payload, expected_code):
    assert ai_provider_adapters._http_failure_code(429, payload) == expected_code


def test_secret_and_settings_are_saved_outside_response_payload(monkeypatch, tmp_path):
    secret_dir = tmp_path / "secrets"
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_secret_dir", secret_dir.as_posix())
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(settings, "openai_api_key", None)

    ai_secret_store.save_provider_secret(
        "openai",
        SecretStr("test-secret-value-123456"),
    )
    saved = save_ai_settings(default_ai_settings())

    secret_path = secret_dir / "openai.key"
    assert secret_path.read_text(encoding="utf-8") == "test-secret-value-123456"
    assert settings_path.is_file()
    assert "test-secret-value-123456" not in saved.model_dump_json()
    assert "test-secret-value-123456" not in settings_path.read_text(encoding="utf-8")


def test_settings_api_merge_preserves_hidden_endpoint(monkeypatch, tmp_path):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    original = save_ai_settings(default_ai_settings())
    original_openai_endpoint = original.providers["openai"].base_url
    payload = original.model_dump(mode="json")
    for provider in payload["providers"].values():
        provider.pop("base_url", None)
    incoming = AiSettingsUpdateRequest.model_validate(payload)

    response = update_ai_system_settings(
        incoming,
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert reloaded.providers["openai"].base_url == original_openai_endpoint
    assert response.endpoint_values_included is False
    assert original_openai_endpoint not in response.model_dump_json()


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_provider_card_settings_save_enables_only_selected_provider(
    monkeypatch,
    tmp_path,
    provider,
):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    original = save_ai_settings(
        default_ai_settings().model_copy(
            update={"mode": "api_first", "external_deidentified_enabled": True}
        )
    )
    previous_other_states = {
        key: value.enabled
        for key, value in original.providers.items()
        if key != provider
    }

    response = update_ai_provider_settings(
        provider,
        AiProviderSettingsPatch(enabled=True),
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert response.saved is True
    assert response.mode == "api_first"
    assert reloaded.providers[provider].enabled is True
    assert {
        key: value.enabled
        for key, value in reloaded.providers.items()
        if key != provider
    } == previous_other_states
    assert reloaded.external_deidentified_enabled is True


def test_easy_mode_save_preserves_provider_state_and_restores_external_policy(
    monkeypatch,
    tmp_path,
):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    base = default_ai_settings()
    providers = dict(base.providers)
    providers["gemini"] = providers["gemini"].model_copy(update={"enabled": False})
    save_ai_settings(
        base.model_copy(
            update={
                "mode": "fully_local",
                "external_deidentified_enabled": False,
                "providers": providers,
            }
        )
    )

    response = update_ai_execution_mode(
        AiModeUpdateRequest(mode="local_first"),
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert response.mode == "local_first"
    assert reloaded.external_deidentified_enabled is True
    assert reloaded.providers["gemini"].enabled is False


def test_one_click_provider_connect_saves_verifies_enables_without_overwriting_roles(
    monkeypatch,
    tmp_path,
):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    original = default_ai_settings()
    save_ai_settings(original)
    saved_secrets: list[str] = []
    monkeypatch.setattr(
        "app.ai_system.save_provider_secret",
        lambda provider, secret: saved_secrets.append(
            f"{provider}:{bool(secret.get_secret_value())}"
        ),
    )
    monkeypatch.setattr("app.ai_system.invalidate_provider_probe", lambda _provider: None)
    monkeypatch.setattr(
        "app.ai_system.test_provider_connection",
        lambda *_args, **_kwargs: AiProviderTestResponse(
            provider="openai",
            connection_ok=True,
            authentication_ok=True,
            selected_model="verified-openai-model",
            models=["verified-openai-model"],
            capabilities=["text", "image", "structured_output"],
            structured_contract_ok=True,
            latency_ms=321,
            message="연결됨",
            deidentified_fixture_used=True,
            external_call_performed=True,
        ),
    )

    def statuses(provider_settings, **_kwargs):
        openai_enabled = bool(provider_settings["openai"].enabled)
        return [
            _status(
                "openai",
                location="external",
                enabled=openai_enabled,
                model="verified-openai-model",
                models=["verified-openai-model"],
                capabilities=["text", "image", "structured_output"],
                structured_contract_verified=True,
            ),
            _status(
                "rules",
                location="rules",
                model="quick-period-summary-v1",
                models=["quick-period-summary-v1"],
                capabilities=["text", "structured_output"],
                structured_contract_verified=True,
            ),
        ]

    monkeypatch.setattr("app.ai_system.collect_provider_public_statuses", statuses)
    response = connect_ai_provider(
        "openai",
        AiProviderConnectRequest(api_key=SecretStr("one-click-secret-value")),
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert saved_secrets == ["openai:True"]
    assert response.connected is True
    assert response.enabled is True
    assert response.selected_model == "verified-openai-model"
    assert response.structured_contract_ok is True
    assert response.secret_value_included is False
    assert reloaded.providers["openai"].enabled is True
    assert reloaded.providers["openai"].model == "verified-openai-model"
    assert reloaded.external_deidentified_enabled is True
    assert reloaded.model_roles == original.model_roles
    assert "one-click-secret-value" not in response.model_dump_json()
    assert "one-click-secret-value" not in settings_path.read_text(encoding="utf-8")


def test_one_click_provider_failure_keeps_provider_disabled(monkeypatch, tmp_path):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    base = default_ai_settings()
    providers = dict(base.providers)
    providers["gemini"] = providers["gemini"].model_copy(update={"enabled": True})
    save_ai_settings(base.model_copy(update={"providers": providers}))
    monkeypatch.setattr("app.ai_system.save_provider_secret", lambda *_args: None)
    monkeypatch.setattr("app.ai_system.invalidate_provider_probe", lambda _provider: None)
    monkeypatch.setattr(
        "app.ai_system.test_provider_connection",
        lambda *_args, **_kwargs: AiProviderTestResponse(
            provider="gemini",
            connection_ok=False,
            authentication_ok=False,
            selected_model=None,
            models=[],
            capabilities=["text", "image", "structured_output"],
            structured_contract_ok=False,
            latency_ms=87,
            error_code="authentication_failed",
            message="raw technical error",
            deidentified_fixture_used=True,
            external_call_performed=True,
        ),
    )

    response = connect_ai_provider(
        "gemini",
        AiProviderConnectRequest(api_key=SecretStr("invalid-secret-value")),
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, _ = ai_settings_store.load_ai_settings()

    assert response.connected is False
    assert response.enabled is False
    assert response.message == "API 키가 올바르지 않습니다."
    assert reloaded.providers["gemini"].enabled is False
    assert reloaded.providers["gemini"].model == providers["gemini"].model


def test_provider_probe_selects_listed_model_instead_of_stale_configured_model(
    monkeypatch,
):
    provider_settings = default_ai_settings().providers["openai"].model_copy(
        update={"enabled": True, "model": "removed-model"}
    )
    monkeypatch.setattr(ai_provider_adapters, "provider_secret_configured", lambda _: True)
    monkeypatch.setattr(
        ai_provider_adapters,
        "get_provider_secret",
        lambda _: SecretStr("test-provider-key-value"),
    )

    def transport(method, _url, _headers, _payload, _timeout):
        if method == "GET":
            return HttpJsonResponse(200, {"data": [{"id": "gpt-5-mini"}]})
        return HttpJsonResponse(
            200,
            {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "answer": "합성 결과",
                                    "visible_text": None,
                                    "image_texts": [],
                                    "evidence": [{"source_no": 1, "statement": "합성 근거"}],
                                    "uncertainties": [],
                                    "recommended_actions": [],
                                }
                            )
                        }
                    }
                ]
            },
        )

    result = run_provider_connection_test(
        "openai",
        provider_settings,
        run_contract_test=True,
        transport=transport,
    )

    assert result.connection_ok is True
    assert result.selected_model == "gpt-5-mini"
    assert result.selected_model in result.models


def test_gemini_models_list_prefers_3_7_and_omits_legacy_sampling_options():
    models = ai_provider_adapters._model_names(
        "gemini",
        {
            "models": [
                {"name": "models/gemini-2.5-flash"},
                {"name": "models/gemini-3.7-flash"},
            ]
        },
    )

    assert models == ["gemini-2.5-flash", "gemini-3.7-flash"]
    assert ai_provider_adapters.select_recommended_provider_model(
        "gemini", models, None
    ) == "gemini-3.7-flash"

    contract_url, contract_payload = ai_provider_adapters._contract_request(
        "gemini",
        "https://provider.invalid/v1beta",
        "gemini-3.7-flash",
    )
    generation_url, generation_payload = ai_provider_adapters._ai_assist_request(
        "gemini",
        "https://provider.invalid/v1beta",
        "gemini-3.7-flash",
        "합성 비식별 입력",
        [],
    )
    _, legacy_payload = ai_provider_adapters._contract_request(
        "gemini",
        "https://provider.invalid/v1beta",
        "gemini-2.5-flash",
    )

    assert contract_url.endswith("/models/gemini-3.7-flash:generateContent")
    contract_config = contract_payload["generationConfig"]
    assert "temperature" not in contract_config
    assert contract_config["responseMimeType"] == "application/json"
    assert "default" not in str(contract_config["responseJsonSchema"])
    assert "minLength" not in str(contract_config["responseJsonSchema"])
    assert "maxLength" not in str(contract_config["responseJsonSchema"])
    assert contract_config["thinkingConfig"] == {"thinkingLevel": "low"}
    assert contract_config["maxOutputTokens"] == 4096
    assert generation_url.endswith("/models/gemini-3.7-flash:generateContent")
    generation_config = generation_payload["generationConfig"]
    assert "temperature" not in generation_config
    assert generation_config["responseJsonSchema"]
    assert legacy_payload["generationConfig"]["temperature"] == 0
    assert legacy_payload["generationConfig"]["responseMimeType"] == "application/json"


def test_gemini_interactions_contract_content_reads_model_output_step():
    assert ai_provider_adapters._contract_content(
        "gemini",
        {
            "steps": [
                {
                    "type": "model_output",
                    "content": [
                        {"type": "text", "text": '{"answer":"준비됨"}'}
                    ],
                }
            ]
        },
    ) == '{"answer":"준비됨"}'


@pytest.mark.parametrize(
    ("models", "expected"),
    [
        (["gemini-2.5-flash", "gemini-3.5-flash", "gemini-3.6-flash"], "gemini-3.6-flash"),
        (["gemini-2.5-flash", "gemini-3.5-flash"], "gemini-3.5-flash"),
        (["gemini-2.5-flash", "gemini-3.1-flash"], "gemini-2.5-flash"),
    ],
)
def test_gemini_recommendation_uses_only_the_approved_returned_order(models, expected):
    assert ai_provider_adapters.select_recommended_provider_model(
        "gemini", models, None
    ) == expected


def test_gemini_one_click_connect_migrates_legacy_default_to_available_3_7(
    monkeypatch,
    tmp_path,
):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    base = default_ai_settings()
    providers = dict(base.providers)
    providers["gemini"] = providers["gemini"].model_copy(
        update={"enabled": False, "model": "gemini-2.5-flash"}
    )
    save_ai_settings(base.model_copy(update={"providers": providers}))
    monkeypatch.setattr("app.ai_system.invalidate_provider_probe", lambda _provider: None)

    probed_models: list[str | None] = []

    def test_connection(provider, provider_settings, **_kwargs):
        probed_models.append(provider_settings.model)
        return AiProviderTestResponse(
            provider=provider,
            connection_ok=True,
            authentication_ok=True,
            selected_model="gemini-3.7-flash",
            models=["gemini-2.5-flash", "gemini-3.7-flash"],
            capabilities=["text", "image", "structured_output"],
            structured_contract_ok=True,
            latency_ms=210,
            message="연결됨",
            deidentified_fixture_used=True,
            external_call_performed=True,
        )

    def statuses(provider_settings, **_kwargs):
        enabled = bool(provider_settings["gemini"].enabled)
        model = provider_settings["gemini"].model or "gemini-3.7-flash"
        return [
            _status(
                "gemini",
                location="external",
                enabled=enabled,
                model=model,
                models=["gemini-2.5-flash", "gemini-3.7-flash"],
                capabilities=["text", "image", "structured_output"],
                structured_contract_verified=True,
            ),
            _status(
                "rules",
                location="rules",
                model="quick-period-summary-v1",
                models=["quick-period-summary-v1"],
                capabilities=["text", "structured_output"],
                structured_contract_verified=True,
            ),
        ]

    monkeypatch.setattr("app.ai_system.test_provider_connection", test_connection)
    monkeypatch.setattr("app.ai_system.collect_provider_public_statuses", statuses)

    response = connect_ai_provider(
        "gemini",
        AiProviderConnectRequest(),
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert probed_models == [None]
    assert response.connected is True
    assert response.selected_model == "gemini-3.7-flash"
    assert reloaded.providers["gemini"].enabled is True
    assert reloaded.providers["gemini"].model == "gemini-3.7-flash"


@pytest.mark.parametrize("provider", ["openai", "gemini"])
def test_enabled_verified_external_provider_enters_deidentified_route_only(provider):
    document = default_ai_settings().model_copy(
        update={
            "mode": "api_first",
            "external_deidentified_enabled": True,
            "provider_priority": [
                provider,
                *[
                    item
                    for item in default_ai_settings().provider_priority
                    if item != provider
                ],
            ],
            "providers": {
                **default_ai_settings().providers,
                provider: default_ai_settings().providers[provider].model_copy(
                    update={"enabled": True, "model": "test-model"}
                ),
            },
        }
    )
    statuses = [
        _status(
            provider,
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]

    deidentified = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
        ),
        document,
        statuses,
        _hardware(),
    )
    sensitive = build_routing_preview(
        AiRoutingPreviewRequest(
            text="합성 민감 돌봄자료",
            contains_real_data=True,
        ),
        document,
        statuses,
        _hardware(),
    )

    assert deidentified.candidates[0].provider == provider
    assert deidentified.candidates[0].external_transmission is True
    assert [item.provider for item in sensitive.candidates] == ["rules"]
    assert sensitive.external_transmission_allowed is False


@pytest.mark.parametrize(
    "mode",
    ["automatic", "api_first", "local_first", "fully_local"],
)
def test_each_execution_mode_is_persisted_and_reloaded(monkeypatch, tmp_path, mode):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    base = default_ai_settings()
    saved = save_ai_settings(
        base.model_copy(
            update={
                "mode": mode,
                "external_deidentified_enabled": mode != "fully_local",
            }
        )
    )
    reloaded, exists = ai_settings_store.load_ai_settings()

    assert exists is True
    assert saved.mode == mode
    assert reloaded.mode == mode
    assert reloaded.external_deidentified_enabled is (mode != "fully_local")
    assert settings_path.is_file()
    assert "api_key" not in settings_path.read_text(encoding="utf-8").lower()


def test_easy_execution_modes_drive_automatic_role_order_without_invalid_pairing():
    providers = [
        _status(
            "openai",
            location="external",
            enabled=True,
            model="external-text-model",
            models=["external-text-model"],
            capabilities=["text", "structured_output"],
            structured_contract_verified=True,
        ),
        _status(
            "local_stt",
            location="internal",
            enabled=True,
            model="faster-whisper",
            models=["faster-whisper"],
            capabilities=["audio", "speech_to_text"],
            structured_contract_verified=False,
        ),
        _status(
            "ollama",
            location="internal",
            enabled=True,
            model="internal-text-model",
            models=["internal-text-model"],
            capabilities=["text", "structured_output"],
            structured_contract_verified=True,
        ),
        _status(
            "rules",
            location="rules",
            enabled=True,
            model="quick-period-summary-v1",
            models=["quick-period-summary-v1"],
            capabilities=["text", "structured_output"],
            structured_contract_verified=True,
        ),
    ]
    base = default_ai_settings().model_copy(
        update={"provider_priority": ["openai", "ollama", "rules"]}
    )

    api_roles = _auto_select_model_roles(
        base.model_copy(update={"mode": "api_first"}), providers
    )
    local_roles = _auto_select_model_roles(
        base.model_copy(update={"mode": "local_first"}), providers
    )
    fully_local_roles = _auto_select_model_roles(
        base.model_copy(update={"mode": "fully_local"}), providers
    )

    assert api_roles.text.provider == "openai"
    assert api_roles.text.model == "external-text-model"
    assert local_roles.text.provider == "ollama"
    assert local_roles.text.model == "internal-text-model"
    assert fully_local_roles.text.provider == "ollama"
    assert fully_local_roles.text.model == "internal-text-model"
    assert fully_local_roles.text.provider != "openai"
    assert api_roles.stt is not None and api_roles.stt.provider == "local_stt"
    assert local_roles.stt is not None and local_roles.stt.provider == "local_stt"
    assert fully_local_roles.stt is not None
    assert fully_local_roles.stt.provider == "local_stt"


def test_role_model_options_only_include_ready_verified_capable_models():
    providers = [
        _status(
            "ollama",
            location="internal",
            model="qwen3.6:35b",
            models=["qwen3.6:35b", "qwen3-vl:8b-instruct"],
            capabilities=["text", "image", "structured_output"],
        ),
        _status(
            "local_stt",
            location="internal",
            model="faster-whisper-base",
            models=["faster-whisper-base"],
            capabilities=["audio", "speech_to_text"],
            contract=False,
        ),
        _status(
            "openai",
            location="external",
            model="unverified-external-model",
            capabilities=["text", "image", "structured_output"],
            contract=False,
        ),
        _status(
            "rules",
            location="rules",
            model="quick-period-summary-v1",
            capabilities=["text", "structured_output"],
        ),
    ]

    options = _role_model_options(providers)

    assert {(item.provider, item.model) for item in options.text} == {
        ("rules", "quick-period-summary-v1"),
        ("ollama", "qwen3.6:35b"),
    }
    assert [(item.provider, item.model) for item in options.vision] == [
        ("ollama", "qwen3-vl:8b-instruct"),
    ]
    assert [(item.provider, item.model) for item in options.stt] == [
        ("local_stt", "faster-whisper-base"),
    ]
    assert all(item.provider != "openai" for item in options.precision)
    assert all(item.external_transmission is False for item in options.text)


def test_direct_model_role_save_persists_without_auto_selection(monkeypatch, tmp_path):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    base = default_ai_settings()
    providers = dict(base.providers)
    providers["ollama"] = providers["ollama"].model_copy(
        update={"enabled": True, "model": "qwen3.6:35b"}
    )
    save_ai_settings(base.model_copy(update={"providers": providers}))
    statuses = [
        _status(
            "ollama",
            location="internal",
            model="qwen3.6:35b",
            models=["qwen3.6:35b", "qwen3-vl:8b-instruct"],
            capabilities=["text", "image", "structured_output"],
        ),
        _status(
            "local_stt",
            location="internal",
            model="faster-whisper-base",
            capabilities=["audio", "speech_to_text"],
            contract=False,
        ),
        _status(
            "rules",
            location="rules",
            model="quick-period-summary-v1",
            capabilities=["text", "structured_output"],
        ),
    ]
    monkeypatch.setattr("app.ai_system.collect_provider_public_statuses", lambda *_args, **_kwargs: statuses)
    direct = AiModelRoles(
        text=AiModelRoleSelection(provider="ollama", model="qwen3.6:35b"),
        vision=AiModelRoleSelection(provider="ollama", model="qwen3-vl:8b-instruct"),
        stt=AiModelRoleSelection(provider="local_stt", model="faster-whisper-base"),
        precision=AiModelRoleSelection(provider="rules", model="quick-period-summary-v1"),
    )

    response = update_ai_model_roles(
        direct,
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert response.model_roles == direct
    assert reloaded.model_roles == direct


def test_legacy_full_settings_save_does_not_bypass_verified_role_selection(
    monkeypatch, tmp_path
):
    settings_path = tmp_path / "ai-settings.json"
    monkeypatch.setattr(settings, "ai_settings_file", settings_path.as_posix())
    current = default_ai_settings()
    save_ai_settings(current)
    unverified = current.model_copy(
        update={
            "model_roles": current.model_roles.model_copy(
                update={
                    "vision": AiModelRoleSelection(
                        provider="openai", model="not-verified-for-image"
                    )
                }
            )
        }
    )

    update_ai_system_settings(
        unverified,
        SimpleNamespace(headers={}),
        SimpleNamespace(role="admin"),
    )
    reloaded, saved = ai_settings_store.load_ai_settings()

    assert saved is True
    assert reloaded.model_roles == current.model_roles


def test_direct_selected_model_is_primary_and_unavailable_selection_falls_back():
    base = default_ai_settings().model_copy(
        update={
            "mode": "local_first",
            "external_deidentified_enabled": True,
            "model_roles": default_ai_settings().model_roles.model_copy(
                update={
                    "text": AiModelRoleSelection(
                        provider="openai", model="selected-external-model"
                    )
                }
            ),
        }
    )
    ready = [
        _status(
            "openai",
            location="external",
            model="selected-external-model",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            model="internal-fallback-model",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]
    selected_plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
            model_role="text",
        ),
        base,
        ready,
        _hardware(ollama_location="internal"),
    )
    unavailable = [ready[0].model_copy(update={"connection_state": "unavailable"}), *ready[1:]]
    fallback_plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
            model_role="text",
        ),
        base,
        unavailable,
        _hardware(ollama_location="internal"),
    )

    assert (selected_plan.candidates[0].provider, selected_plan.candidates[0].model) == (
        "openai",
        "selected-external-model",
    )
    assert fallback_plan.candidates[0].provider == "ollama"
    assert base.model_roles.text.provider == "openai"


def test_direct_role_selection_is_not_overridden_by_legacy_saved_execution_mode():
    base = default_ai_settings().model_copy(
        update={
            "mode": "fully_local",
            "external_deidentified_enabled": False,
            "model_roles": default_ai_settings().model_roles.model_copy(
                update={
                    "text": AiModelRoleSelection(
                        provider="openai", model="selected-external-model"
                    )
                }
            ),
        }
    )
    providers = [
        _status(
            "openai",
            location="external",
            model="selected-external-model",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            model="internal-fallback-model",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]

    deidentified = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
            model_role="text",
        ),
        base,
        providers,
        _hardware(ollama_location="internal"),
    )
    sensitive = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 돌봄 기록",
            contains_real_data=True,
            model_role="text",
        ),
        base,
        providers,
        _hardware(ollama_location="internal"),
    )

    assert deidentified.selected_mode == "automatic"
    assert deidentified.external_transmission_allowed is True
    assert (
        deidentified.candidates[0].provider,
        deidentified.candidates[0].model,
    ) == ("openai", "selected-external-model")
    assert sensitive.external_transmission_allowed is False
    assert sensitive.candidates[0].provider == "ollama"
    assert all(not candidate.external_transmission for candidate in sensitive.candidates)


def test_privacy_classifier_blocks_real_uncertain_and_identified_data():
    real = classify_ai_input(
        AiPrivacyClassificationRequest(
            text="대상-A의 낙상 기록",
            contains_real_data=True,
            deidentified_confirmed_by_user=True,
        )
    )
    uncertain = classify_ai_input(
        AiPrivacyClassificationRequest(text="어르신 혈압 기록을 정리해줘")
    )
    identified = classify_ai_input(
        AiPrivacyClassificationRequest(
            text="수급자: 홍길동, 전화 010-1234-5678",
            deidentified_confirmed_by_user=True,
        )
    )

    assert real.classification == "personal_or_care_record"
    assert uncertain.classification == "possibly_sensitive"
    assert identified.direct_identifier_detected is True
    assert not real.external_allowed
    assert not uncertain.external_allowed
    assert not identified.external_allowed


def test_privacy_classifier_accepts_confirmed_deidentified_fixture():
    result = classify_ai_input(
        AiPrivacyClassificationRequest(
            text="대상-A가 복도 이동 중 비틀거려 부축했습니다.",
            deidentified_confirmed_by_user=True,
        )
    )

    assert result.classification == "deidentified_confirmed"
    assert result.external_allowed is True
    assert result.user_confirmation_accepted is True


def test_hardware_recommendation_does_not_force_local_model_install():
    no_gpu = recommend_execution_mode(_hardware())
    sixteen_gb = recommend_execution_mode(_hardware(vram_gb=16))
    internal_server = recommend_execution_mode(
        _hardware(ollama_location="internal")
    )
    unavailable_internal_server = recommend_execution_mode(
        _hardware(ollama_location="internal", ollama_ready=False)
    )

    assert no_gpu.mode == "api_first"
    assert sixteen_gb.mode == "automatic"
    assert internal_server.mode == "local_first"
    assert unavailable_internal_server.mode == "api_first"
    assert all(
        "내부 AI 서버가 연결되어" not in reason
        for reason in unavailable_internal_server.reasons
    )
    assert no_gpu.local_model_install_required is False
    assert any("Qwen 35B" in reason for reason in sixteen_gb.reasons)


def test_router_blocks_external_for_real_data_and_fully_local_mode():
    settings_document = default_ai_settings().model_copy(
        update={"external_deidentified_enabled": True}
    )
    providers = [
        _status(
            "openai",
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]
    real_plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="실제 어르신 기록",
            contains_real_data=True,
            mode="api_first",
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )
    local_plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
            mode="fully_local",
        ),
        settings_document.model_copy(
            update={"mode": "fully_local", "external_deidentified_enabled": False}
        ),
        providers,
        _hardware(ollama_location="internal"),
    )

    assert real_plan.candidates[0].provider == "ollama"
    assert all(not item.external_transmission for item in real_plan.candidates)
    assert all(not item.external_transmission for item in local_plan.candidates)
    assert local_plan.candidates[-1].provider == "rules"


def test_router_uses_external_then_internal_then_rules_for_deidentified_data():
    settings_document = default_ai_settings().model_copy(
        update={"mode": "api_first", "external_deidentified_enabled": True}
    )
    providers = [
        _status(
            "openai",
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]
    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )

    assert [item.provider for item in plan.candidates] == [
        "openai",
        "ollama",
        "rules",
    ]
    assert plan.external_transmission_allowed is True


def test_automatic_router_uses_saved_fallback_order_with_hardware_recommendation():
    settings_document = default_ai_settings().model_copy(
        update={
            "mode": "automatic",
            "external_deidentified_enabled": True,
            "fallback_order": ["local", "internal", "external", "rules"],
        }
    )
    providers = [
        _status(
            "openai",
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "openai_compatible",
            location="local",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]

    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )

    assert [item.path for item in plan.candidates] == [
        "local",
        "internal",
        "external",
        "rules",
    ]


def test_nano_omni_combined_image_audio_is_excluded_after_hosted_503():
    base_settings = default_ai_settings()
    provider_settings = dict(base_settings.providers)
    provider_settings["nvidia"] = provider_settings["nvidia"].model_copy(
        update={
            "model": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
        }
    )
    settings_document = base_settings.model_copy(
        update={
            "external_deidentified_enabled": True,
            "providers": provider_settings,
        }
    )
    providers = [
        _status(
            "nvidia",
            location="external",
            capabilities=["image", "audio", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]
    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 멀티모달 자료",
            deidentified_confirmed_by_user=True,
            capability="image",
            additional_capabilities=["audio"],
        ),
        settings_document,
        providers,
        _hardware(),
    )

    assert [item.provider for item in plan.candidates] == ["rules"]
    assert any("반복 503" in reason for reason in plan.blocked_reasons)


def test_vision_router_uses_nano_then_qwen_vl_for_external_deidentified_image(
    monkeypatch,
):
    monkeypatch.setattr(ai_router.app_settings, "ocr_model", "qwen3-vl:8b-instruct")
    settings_document = default_ai_settings().model_copy(
        update={"mode": "api_first", "external_deidentified_enabled": True}
    )
    providers = [
        _status(
            "openai",
            location="external",
            capabilities=["image", "structured_output"],
        ),
        _status(
            "nvidia",
            location="external",
            capabilities=["image", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["image", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]

    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="비식별 합성 손글씨 시험자료",
            deidentified_confirmed_by_user=True,
            capability="image",
            model_role="vision",
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )

    assert [item.provider for item in plan.candidates[:3]] == [
        "nvidia",
        "ollama",
        "openai",
    ]
    assert "nano-omni" in plan.candidates[0].model
    assert plan.candidates[1].model == "qwen3-vl:8b-instruct"
    assert plan.candidates[-1].provider == "rules"
    assert plan.external_transmission_allowed is True


def test_vision_router_blocks_external_candidates_for_sensitive_image(monkeypatch):
    monkeypatch.setattr(ai_router.app_settings, "ocr_model", "qwen3-vl:8b-instruct")
    settings_document = default_ai_settings().model_copy(
        update={"mode": "api_first", "external_deidentified_enabled": True}
    )
    providers = [
        _status(
            "nvidia",
            location="external",
            capabilities=["image", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["image", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]

    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="합성 민감 돌봄자료",
            contains_real_data=True,
            capability="image",
            model_role="vision",
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )

    assert [item.provider for item in plan.candidates] == ["ollama", "rules"]
    assert plan.candidates[0].model == "qwen3-vl:8b-instruct"
    assert plan.external_transmission_allowed is False
    assert all(not item.external_transmission for item in plan.candidates)


def test_fallback_executor_moves_on_for_timeout_rate_limit_and_contract_error():
    settings_document = default_ai_settings().model_copy(
        update={
            "mode": "api_first",
            "external_deidentified_enabled": True,
            "max_attempts": 4,
            "provider_priority": [
                "openai",
                "nvidia",
                "ollama",
                "gemini",
                "anthropic",
                "ollama_cloud",
                "openai_compatible",
                "rules",
            ],
        }
    )
    providers = [
        _status(
            "openai",
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "nvidia",
            location="external",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "ollama",
            location="internal",
            capabilities=["text", "structured_output"],
        ),
        _status(
            "rules",
            location="rules",
            capabilities=["text", "structured_output"],
        ),
    ]
    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text="대상-A 합성 기록",
            deidentified_confirmed_by_user=True,
        ),
        settings_document,
        providers,
        _hardware(ollama_location="internal"),
    )

    def timeout(_candidate):
        raise AiRouteAttemptError("timeout", "timeout")

    def rate_limit(_candidate):
        raise AiRouteAttemptError("rate_limited", "rate limit")

    def contract_error(_candidate):
        raise AiRouteAttemptError("contract_validation_failed", "contract")

    result = execute_with_fallback(
        plan,
        {
            "openai": timeout,
            "nvidia": rate_limit,
            "ollama": contract_error,
            "rules": lambda _candidate: {"source": "rules"},
        },
    )

    assert result.selected.provider == "rules"
    assert result.fallback_active is True
    assert [attempt.error_code for attempt in result.attempts] == [
        "timeout",
        "rate_limited",
        "contract_validation_failed",
        None,
    ]
    assert all(
        not attempt.external_transmission
        for attempt in result.attempts
        if attempt.provider in {"ollama", "rules"}
    )


def test_ai_turn_response_reports_external_fallback_and_human_review():
    turn = SimpleNamespace(
        id=uuid4(),
        status="completed",
        task_type="summary",
        question=None,
        answer="비식별 시험 결과",
        visible_text=None,
        evidence=[],
        uncertainties=[],
        recommended_actions=[],
        result_payload={},
        provider="nvidia",
        provider_model="test-model",
        elapsed_ms=120,
        error_message=None,
        contains_real_data=False,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        provider_attempts=[
            SimpleNamespace(
                attempt_no=1,
                status="failed",
                transmitted_external=False,
                error_code="timeout",
            ),
            SimpleNamespace(
                attempt_no=2,
                status="completed",
                transmitted_external=True,
                error_code=None,
            ),
        ],
    )

    response = turn_response(turn)

    assert response.processing_location == "external"
    assert response.external_transmission is True
    assert response.deidentification_status == "confirmed_deidentified"
    assert response.fallback_active is True
    assert response.fallback_state == "fallback"
    assert response.fallback_reason == (
        "이전 공급자가 제한시간 안에 응답하지 않아 다음 경로로 전환했습니다."
    )
    assert response.enhancement_status == "completed"
    assert response.human_review_required is True


def test_ai_turn_response_marks_real_data_as_protected_local():
    turn = SimpleNamespace(
        id=uuid4(),
        status="completed",
        task_type="summary",
        question=None,
        answer="보호 경로 결과",
        visible_text=None,
        evidence=[],
        uncertainties=[],
        recommended_actions=[],
        result_payload={},
        provider="local_ollama",
        provider_model="qwen3.6:35b",
        elapsed_ms=120,
        error_message=None,
        contains_real_data=True,
        created_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
        provider_attempts=[
            SimpleNamespace(
                attempt_no=1,
                status="completed",
                transmitted_external=False,
                response_meta={"route": "internal"},
            )
        ],
    )

    response = turn_response(turn)

    assert response.processing_location == "internal"
    assert response.external_transmission is False
    assert response.deidentification_status == "protected_local"
    assert response.fallback_active is False


def test_ai_system_status_requires_admin():
    with TestClient(app) as client:
        response = client.get("/api/ai/system/status")

    assert response.status_code == 401


def test_ai_system_status_and_connect_reject_regular_staff():
    username = f"ai-settings-staff-{uuid4().hex[:8]}"
    origin = {"origin": "http://testserver"}
    with TestClient(app) as admin_client:
        login = admin_client.post(
            "/api/auth/login",
            json={"username": "admin", "password": "AdminPass!234"},
            headers=origin,
        )
        assert login.status_code == 200, login.text
        created = admin_client.post(
            "/api/employees",
            json={
                "username": username,
                "full_name": "AI 설정 권한 시험 직원",
                "password": "123456",
                "role": "staff",
            },
            headers=origin,
        )
        assert created.status_code == 201, created.text

    with TestClient(app) as staff_client:
        login = staff_client.post(
            "/api/auth/login",
            json={"username": username, "password": "123456"},
            headers=origin,
        )
        assert login.status_code == 200, login.text
        assert staff_client.get("/api/ai/system/status").status_code == 403
        connect = staff_client.post(
            "/api/ai/system/providers/openai/connect",
            json={"api_key": "fixture-key", "auto_enable": True},
            headers=origin,
        )
        assert connect.status_code == 403
        role_update = staff_client.put(
            "/api/ai/system/settings/model-roles",
            json={
                "text": {"provider": "rules", "model": "quick-period-summary-v1"},
                "vision": None,
                "stt": None,
                "precision": {"provider": "rules", "model": "quick-period-summary-v1"},
            },
            headers=origin,
        )
        assert role_update.status_code == 403


def test_ai_settings_validation_rejects_external_http_endpoint():
    document = default_ai_settings()
    providers = dict(document.providers)
    providers["openai"] = providers["openai"].model_copy(
        update={"base_url": "http://example.com/v1"}
    )

    with pytest.raises(ValueError, match="외부 HTTPS"):
        ai_settings_store.validate_ai_settings(
            document.model_copy(update={"providers": providers})
        )
