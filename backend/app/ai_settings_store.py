from __future__ import annotations

import json
import hashlib
import os
import tempfile
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

from .ai_system_schemas import (
    AiCentralModels,
    AiModelRoleSelection,
    AiModelRoles,
    AiProviderSettingsPatch,
    AiSettingsUpdateRequest,
    AiSystemProviderId,
)
from .config import settings
from .finals_readiness import endpoint_scope


_LOCK = RLock()
_EXTERNAL_PROVIDERS: frozenset[AiSystemProviderId] = frozenset(
    {
        "ollama_cloud",
        "nvidia",
        "openai",
        "gemini",
        "anthropic",
    }
)


def default_ai_settings() -> AiSettingsUpdateRequest:
    return AiSettingsUpdateRequest(
        mode="automatic",
        external_deidentified_enabled=False,
        provider_priority=[
            "nvidia",
            "openai",
            "gemini",
            "anthropic",
            "ollama_cloud",
            "openai_compatible",
            "ollama",
            "rules",
        ],
        fallback_order=["external", "local", "internal", "rules"],
        max_attempts=settings.ai_provider_max_attempts,
        total_timeout_seconds=settings.ai_provider_total_timeout_seconds,
        model_roles=AiModelRoles(
            text=AiModelRoleSelection(
                provider="ollama",
                model=settings.ai_review_model,
            ),
            vision=AiModelRoleSelection(
                provider="nvidia",
                model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            ),
            stt=AiModelRoleSelection(
                provider="local_stt",
                model=settings.stt_model,
            ),
            precision=AiModelRoleSelection(
                provider="ollama",
                model="qwen3.6:35b",
            ),
        ),
        providers={
            "rules": AiProviderSettingsPatch(
                enabled=True,
                model="quick-period-summary-v1",
                timeout_seconds=1,
            ),
            "ollama": AiProviderSettingsPatch(
                enabled=True,
                base_url=settings.ai_review_base_url,
                model=settings.ai_review_model,
                timeout_seconds=settings.ai_review_timeout_seconds,
            ),
            "nvidia": AiProviderSettingsPatch(
                enabled=settings.ai_review_external_enabled,
                base_url=settings.nvidia_api_base_url,
                model=settings.nvidia_nemotron_model,
                timeout_seconds=settings.nvidia_api_timeout_seconds,
            ),
            "openai": AiProviderSettingsPatch(
                enabled=False,
                base_url=settings.openai_api_base_url,
                model=settings.openai_model,
                timeout_seconds=settings.openai_api_timeout_seconds,
            ),
            "gemini": AiProviderSettingsPatch(
                enabled=False,
                base_url=settings.gemini_api_base_url,
                model=settings.gemini_model,
                timeout_seconds=settings.gemini_api_timeout_seconds,
            ),
            "anthropic": AiProviderSettingsPatch(
                enabled=False,
                base_url=settings.anthropic_api_base_url,
                model=settings.anthropic_model,
                timeout_seconds=settings.anthropic_api_timeout_seconds,
            ),
            "ollama_cloud": AiProviderSettingsPatch(
                enabled=False,
                base_url=settings.ollama_cloud_api_base_url,
                model=settings.ollama_cloud_model or None,
                timeout_seconds=settings.ollama_cloud_api_timeout_seconds,
            ),
            "openai_compatible": AiProviderSettingsPatch(
                enabled=False,
                base_url=settings.openai_compatible_api_base_url or None,
                model=settings.openai_compatible_model or None,
                timeout_seconds=settings.openai_compatible_api_timeout_seconds,
            ),
            "local_stt": AiProviderSettingsPatch(
                enabled=settings.stt_enabled,
                base_url=settings.stt_service_url,
                model=settings.stt_model,
                timeout_seconds=min(900, settings.stt_timeout_seconds),
            ),
        },
    )


def _settings_path() -> Path:
    return Path(settings.ai_settings_file).expanduser().resolve()


def _validate_endpoint(provider: AiSystemProviderId, value: str | None) -> None:
    if not value:
        return
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("AI Endpoint는 인증정보·query·fragment가 없는 HTTP(S) 주소여야 합니다.")
    scope = endpoint_scope(value)
    if provider in {"ollama", "local_stt"} and scope not in {"local", "internal"}:
        raise ValueError("로컬 AI·STT Endpoint는 루프백 또는 내부망 주소여야 합니다.")
    if provider in _EXTERNAL_PROVIDERS and (
        parsed.scheme != "https" or scope != "external"
    ):
        raise ValueError("외부 공급자 Endpoint는 외부 HTTPS 주소여야 합니다.")
    if provider == "openai_compatible" and scope == "external" and parsed.scheme != "https":
        raise ValueError("외부 OpenAI 호환 Endpoint는 HTTPS가 필요합니다.")


def validate_ai_settings(document: AiSettingsUpdateRequest) -> AiSettingsUpdateRequest:
    _validate_endpoint("ollama", document.central_models.base_url)
    for provider, provider_settings in document.providers.items():
        if provider in {"rules", "local_stt"} and provider_settings.enabled is False:
            if provider == "rules":
                raise ValueError("규칙 기반 기본 기능은 비활성화할 수 없습니다.")
        _validate_endpoint(provider, provider_settings.base_url)
    configured_providers = set(document.providers)
    for role in (
        document.model_roles.text,
        document.model_roles.vision,
        document.model_roles.stt,
        document.model_roles.precision,
    ):
        if role is not None and role.provider not in configured_providers:
            raise ValueError("기능별 모델의 공급자 설정이 없습니다.")
    return document


def effective_central_models(document: AiSettingsUpdateRequest | None = None) -> AiCentralModels:
    if document is None:
        document, _ = load_ai_settings()
    policy = document.central_models
    if settings.ai_central_models_json:
        overlay = json.loads(settings.ai_central_models_json)
        policy = AiCentralModels.model_validate({**policy.model_dump(), **overlay})
    _validate_endpoint("ollama", policy.base_url)
    return policy


ROLE_FEATURES = {"text": "document_text", "vision": "image_reading", "precision": "precision", "stt": "stt"}


def central_editable_policy(document: AiSettingsUpdateRequest) -> AiCentralModels:
    """Read compatibility only; saving the admin form makes central authoritative.

    Old role fields remain archived. Vision stays available as the existing
    fallback until an actual image check qualifies the new central selection.
    """
    policy = effective_central_models(document).model_copy(deep=True)
    if policy.inheritance_version == 1:
        legacy_text = document.model_roles.text
        if (
            policy.text_default_model is None
            and legacy_text is not None
            and legacy_text.provider == policy.default_provider
        ):
            policy.text_default_model = legacy_text.model
        for role in ("text", "precision", "stt"):
            selected = getattr(document.model_roles, role)
            if selected is not None and (selected.provider != policy.default_provider or selected.model != policy.text_default_model):
                policy.feature_overrides.setdefault(ROLE_FEATURES[role], selected)
        policy.inheritance_version = 2
    return policy


def central_feature_selection(policy: AiCentralModels, feature: str) -> AiModelRoleSelection | None:
    value = policy.feature_overrides.get(feature)
    if isinstance(value, AiModelRoleSelection):
        return value
    model = value or (policy.vision_default_model or policy.text_default_model if feature == "image_reading" else policy.text_default_model)
    return AiModelRoleSelection(provider=policy.default_provider, model=model) if model else None


def central_image_binding(document: AiSettingsUpdateRequest, selection: AiModelRoleSelection) -> str:
    providers = effective_provider_settings(document)
    provider = providers.get(selection.provider)
    return hashlib.sha256(json.dumps([selection.provider, (provider.base_url or "").rstrip("/") if provider else "", selection.model], ensure_ascii=False).encode()).hexdigest()


def resolved_model_role(document: AiSettingsUpdateRequest, role: str) -> AiModelRoleSelection | None:
    policy = effective_central_models(document)
    if policy.inheritance_version == 1:
        return getattr(document.model_roles, role, None)
    if policy.default_provider == "rules":
        return AiModelRoleSelection(provider="rules", model="quick-period-summary-v1")
    selected = central_feature_selection(policy, ROLE_FEATURES.get(role, "document_text"))
    if role == "vision":
        if selected and central_image_binding(document, selected) in policy.image_verification_bindings:
            return selected
        if policy.vision_fallback_model:
            fallback = AiModelRoleSelection(provider=policy.default_provider, model=policy.vision_fallback_model)
            if central_image_binding(document, fallback) in policy.image_verification_bindings:
                return fallback
        # Existing read-only compatibility route; the router still applies the
        # original per-input privacy policy, including the real-data block.
        return document.model_roles.vision
    return selected


def effective_provider_settings(document: AiSettingsUpdateRequest) -> dict[AiSystemProviderId, AiProviderSettingsPatch]:
    providers = dict(document.providers)
    policy = effective_central_models(document)
    if policy.base_url and "ollama" in providers:
        providers["ollama"] = providers["ollama"].model_copy(update={"base_url": policy.base_url})
    if policy.inheritance_version == 2 and policy.default_provider in providers:
        providers[policy.default_provider] = providers[policy.default_provider].model_copy(update={"model": policy.text_default_model})
    return providers


def effective_model_roles(document: AiSettingsUpdateRequest) -> AiModelRoles:
    rules = AiModelRoleSelection(provider="rules", model="quick-period-summary-v1")
    return AiModelRoles(text=resolved_model_role(document, "text") or rules, precision=resolved_model_role(document, "precision") or rules, vision=resolved_model_role(document, "vision"), stt=resolved_model_role(document, "stt"))


def load_ai_settings() -> tuple[AiSettingsUpdateRequest, bool]:
    path = _settings_path()
    if not path.is_file():
        return default_ai_settings(), False
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        document = AiSettingsUpdateRequest.model_validate(payload)
        return validate_ai_settings(document), True
    except (OSError, ValueError, TypeError):
        return default_ai_settings(), False


def save_ai_settings(document: AiSettingsUpdateRequest) -> AiSettingsUpdateRequest:
    validated = validate_ai_settings(document)
    path = _settings_path()
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        descriptor, temporary_name = tempfile.mkstemp(
            prefix="ai-settings-",
            suffix=".tmp",
            dir=path.parent,
            text=True,
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    validated.model_dump(mode="json"),
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            os.replace(temporary, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            if temporary.exists():
                temporary.unlink()
    return validated
