from __future__ import annotations

from datetime import datetime, timezone
from time import perf_counter
import base64
import io
import hashlib

from fastapi import APIRouter, Depends, HTTPException, Response, status

from .ai_hardware import detect_ai_hardware, recommend_execution_mode
from .ai_privacy import classify_ai_input
from .ai_provider_adapters import (
    PROVIDER_DEFINITIONS,
    collect_provider_public_statuses,
    invalidate_provider_probe,
    test_provider_connection,
    list_provider_models,
    generate_ai_assist_result,
)
from .ai_router import build_routing_preview
from .ai_secret_store import provider_secret_env_name, save_provider_secret
from .ai_settings_store import central_editable_policy, central_feature_selection, central_image_binding, effective_central_models, effective_model_roles, effective_provider_settings, load_ai_settings, resolved_model_role, ROLE_FEATURES, save_ai_settings, _validate_endpoint
from .config import settings
from .ai_system_schemas import (
    AiCentralModels,
    AiCentralConnectionRequest,
    AiCentralImageCheckRequest,
    AiModeUpdateRequest,
    AiModelRoleSelection,
    AiModelRoles,
    AiModelRolesSaveResponse,
    AiPrivacyClassificationRequest,
    AiPrivacyClassificationResponse,
    AiProviderConnectRequest,
    AiProviderConnectResponse,
    AiProviderCredentialUpdateRequest,
    AiProviderCredentialUpdateResponse,
    AiProviderPublicStatus,
    AiProviderSettingsPatch,
    AiProviderTestRequest,
    AiProviderTestResponse,
    AiRoutingPreviewRequest,
    AiRoutingPreviewResponse,
    AiRuntimeRouteStatus,
    AiRoleModelOption,
    AiRoleModelOptions,
    AiSettingsSaveResponse,
    AiSettingsUpdateRequest,
    AiSystemProviderId,
    AiSystemStatusResponse,
)
from .dependencies import require_admin
from .models import User
from .record_text_ai import recent_record_model_runs, local_json_request


router = APIRouter(prefix="/api/ai/system", tags=["ai-system"])
_CENTRAL_CATALOG: dict[str, dict] = {}


def _central_model_public() -> dict:
    document, _ = load_ai_settings()
    policy = central_editable_policy(document)
    public = policy.model_dump(mode="json", exclude={"base_url", "image_verification_bindings"})
    public["environment_override"] = bool(settings.ai_central_models_json)
    public["recent_runs"] = recent_record_model_runs()
    public["features"] = {
        feature: {"model": selected.model if (selected := central_feature_selection(policy, feature)) else None, "provider": selected.provider if selected else policy.default_provider, "inheritance": "override" if feature in policy.feature_overrides else "text_default_model"}
        for feature in ("search_summary", "care_record_question")
    }
    public["connection"] = _CENTRAL_CATALOG.get(
        _central_catalog_key(document),
        {
            "status": "needs_check",
            "models": [],
            "message": "연결 확인이 필요합니다.",
            "failure_code": None,
        },
    )
    image_selection = central_feature_selection(policy, "image_reading")
    public["image_verified"] = bool(image_selection and central_image_binding(document, image_selection) in policy.image_verification_bindings)
    legacy = document.model_roles.vision
    public["legacy_image_fallback"] = legacy.model_dump() if legacy else None
    return public


def _central_catalog_key(document):
    policy = central_editable_policy(document)
    provider = effective_provider_settings(document).get(policy.default_provider)
    return hashlib.sha256(str((policy.default_provider, provider.base_url if provider else None, policy.text_default_model)).encode()).hexdigest()


def _central_catalog(document):
    policy = central_editable_policy(document)
    if policy.default_provider == "rules":
        return {
            "status": "rules",
            "models": [],
            "message": "AI 없이 기본 검색과 요약을 사용합니다.",
            "failure_code": None,
            "image_capabilities_verified": False,
        }
    provider = effective_provider_settings(document).get(policy.default_provider)
    started = perf_counter()
    if provider is None or not provider.base_url:
        result = {
            "status": "failed",
            "models": [],
            "message": "AI 서버 주소가 설정되지 않았습니다.",
            "failure_code": "address_missing",
            "current_model_listed": False,
            "image_capabilities_verified": False,
        }
    else:
        try:
            if policy.default_provider == "ollama":
                response = local_json_request(provider.base_url, "/api/tags", None, 3)
                raw_models = response.get("models")
                if not isinstance(raw_models, list):
                    raise TypeError("catalog_format_invalid")
                models = sorted(
                    {
                        item["name"]
                        for item in raw_models
                        if isinstance(item, dict)
                        and isinstance(item.get("name"), str)
                        and "cloud" not in item["name"].lower()
                    }
                )
            else:
                models = list_provider_models(
                    policy.default_provider,
                    provider.model_copy(update={"timeout_seconds": 5}),
                )
            if not models:
                result = {
                    "status": "failed",
                    "models": [],
                    "message": "AI 서버에 사용할 수 있는 모델이 없습니다.",
                    "failure_code": "catalog_empty",
                    "current_model_listed": False,
                    "image_capabilities_verified": False,
                }
            else:
                result = {
                    "status": "connected",
                    "models": models,
                    "message": "모델 목록을 확인했습니다. 기능 검증은 별도입니다.",
                    "failure_code": None,
                    "current_model_listed": policy.text_default_model in models,
                    "image_capabilities_verified": False,
                }
        except (ConnectionError, TimeoutError, OSError):
            result = {
                "status": "failed",
                "models": [],
                "message": "AI 서버에 연결하지 못했습니다. 서버 실행과 네트워크를 확인해 주세요.",
                "failure_code": "connection_failed",
                "current_model_listed": False,
                "image_capabilities_verified": False,
            }
        except (TypeError, ValueError):
            result = {
                "status": "failed",
                "models": [],
                "message": "AI 서버의 모델 목록 형식을 확인하지 못했습니다.",
                "failure_code": "catalog_format_invalid",
                "current_model_listed": False,
                "image_capabilities_verified": False,
            }
        except Exception:
            result = {
                "status": "failed",
                "models": [],
                "message": "모델 목록을 확인하지 못했습니다. 저장된 설정은 유지됩니다.",
                "failure_code": "catalog_failed",
                "current_model_listed": False,
                "image_capabilities_verified": False,
            }
    result["elapsed_ms"] = round((perf_counter()-started)*1000)
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    _CENTRAL_CATALOG[_central_catalog_key(document)] = result
    return result


_ONE_CLICK_EXTERNAL_PROVIDERS: frozenset[AiSystemProviderId] = frozenset(
    {"openai", "gemini", "nvidia", "anthropic", "ollama_cloud"}
)


def _ai_settings_write_allowed(user: User) -> bool:
    if getattr(user, "_reviewer_experience", None) is not None:
        return False
    mentor_username = (settings.mentor_reviewer_username or "").strip()
    return not (
        settings.reviewer_password_login_enabled
        and mentor_username
        and user.username == mentor_username
    )


def require_ai_settings_write_admin(
    user: User = Depends(require_admin),
) -> User:
    if not _ai_settings_write_allowed(user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="멘토 전체검토 계정에서는 AI 연결과 설정을 변경할 수 없습니다.",
        )
    return user


def _model_for_role(
    provider: AiProviderPublicStatus,
    role: str,
) -> str | None:
    models = list(provider.models)
    if not models:
        return None
    if role == "vision":
        tokens = {
            "nvidia": ("nano-omni", "vision", "-vl"),
            "openai": ("gpt-5", "gpt-4o", "vision"),
            "gemini": (
                "gemini-3.7",
                "gemini-3.6",
                "gemini-3.5",
                "gemini-3.1",
                "gemini-3-flash",
                "gemini-2.5",
                "gemini-2.0",
            ),
            "anthropic": ("claude-sonnet", "claude-3-5"),
            "ollama": ("-vl", "vision"),
            "ollama_cloud": ("-vl", "vision"),
        }.get(provider.provider, ())
        for token in tokens:
            match = next((item for item in models if token in item.lower()), None)
            if match:
                return match
    if role == "precision":
        for token in (
            "35b",
            "ultra",
            "sonnet",
            "gpt-5",
            "gemini-3.7",
            "gemini-3.6",
            "gemini-3.5",
            "gemini-3.1",
            "gemini-3-flash",
            "gemini-2.5",
        ):
            match = next((item for item in models if token in item.lower()), None)
            if match:
                return match
    if provider.configured_model in models:
        return provider.configured_model
    return models[0]


def _provider_supports_role(provider: AiProviderPublicStatus, role: str) -> bool:
    capability = {
        "text": "text",
        "vision": "image",
        "stt": "speech_to_text",
        "precision": "text",
    }[role]
    if not provider.enabled or provider.connection_state != "ready":
        return False
    if capability not in provider.capabilities:
        return False
    if role != "stt" and not provider.structured_contract_verified:
        return False
    return bool(_model_for_role(provider, role))


def _auto_select_model_roles(
    settings_document: AiSettingsUpdateRequest,
    provider_statuses: list[AiProviderPublicStatus],
) -> AiModelRoles:
    by_id = {item.provider: item for item in provider_statuses}
    priority_ordered = [
        by_id[provider]
        for provider in settings_document.provider_priority
        if provider in by_id
    ]
    if settings_document.mode == "fully_local":
        ordered = [
            item
            for item in priority_ordered
            if item.processing_location in {"local", "internal", "rules"}
        ]
    elif settings_document.mode == "local_first":
        ordered = sorted(
            priority_ordered,
            key=lambda item: (
                0 if item.processing_location in {"local", "internal", "rules"} else 1,
                priority_ordered.index(item),
            ),
        )
    elif settings_document.mode == "api_first":
        ordered = sorted(
            priority_ordered,
            key=lambda item: (
                0 if item.processing_location == "external" else 1,
                priority_ordered.index(item),
            ),
        )
    else:
        ordered = priority_ordered

    def choose(role: str, candidates: list[AiProviderPublicStatus]):
        for item in candidates:
            if _provider_supports_role(item, role):
                model = _model_for_role(item, role)
                if model:
                    return AiModelRoleSelection(provider=item.provider, model=model)
        return None

    text = choose("text", ordered)
    if text is None:
        text = AiModelRoleSelection(provider="rules", model="quick-period-summary-v1")
    vision = choose("vision", ordered)
    stt_candidates = ordered + [
        item
        for item in provider_statuses
        if item.provider not in {entry.provider for entry in ordered}
    ]
    stt = choose("stt", stt_candidates)
    precision = choose("precision", ordered) or text
    return AiModelRoles(
        text=text,
        vision=vision,
        stt=stt,
        precision=precision,
    )


def _role_model_options(
    provider_statuses: list[AiProviderPublicStatus],
) -> AiRoleModelOptions:
    """Return only connected role-capable models observed by the backend."""

    options: dict[str, list[AiRoleModelOption]] = {
        "text": [],
        "vision": [],
        "stt": [],
        "precision": [],
    }
    seen: dict[str, set[tuple[AiSystemProviderId, str]]] = {
        role: set() for role in options
    }
    for role in options:
        for provider in provider_statuses:
            if not _provider_supports_role(provider, role):
                continue
            model = _model_for_role(provider, role)
            if not model:
                continue
            key = (provider.provider, model)
            if key in seen[role]:
                continue
            seen[role].add(key)
            options[role].append(
                AiRoleModelOption(
                    provider=provider.provider,
                    model=model,
                    processing_location=provider.processing_location,
                    external_transmission=provider.processing_location == "external",
                    cost_notice=(
                        "cost_possible"
                        if provider.processing_location == "external"
                        else "no_extra_cost"
                    ),
                )
            )
    return AiRoleModelOptions(**options)


def _connection_failure_message(error_code: str | None) -> str:
    if error_code in {"key_unconfigured", "authentication_failed"}:
        return "API 키가 올바르지 않습니다."
    if error_code in {"billing_required", "free_tier_exhausted", "quota_exceeded"}:
        return "키는 확인됐지만 현재 사용하려면 결제 또는 무료 한도가 필요합니다."
    if error_code == "rate_limited":
        return "키는 확인됐지만 공급자가 요청을 일시적으로 제한했습니다. 잠시 후 다시 확인해 주세요."
    if error_code == "timeout":
        return "응답이 너무 오래 걸려 사용을 시작하지 않았습니다."
    if error_code == "contract_validation_failed":
        return "연결은 됐지만 필요한 결과 형식을 지원하지 않습니다."
    if error_code in {"unsupported_input", "invalid_response"}:
        return "이 계정에서 사용할 수 있는 모델이 없습니다."
    return "현재 AI 서비스에 연결할 수 없습니다. 잠시 후 다시 시도해 주세요."


def _no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Pragma"] = "no-cache"


def _system_snapshot() -> AiSystemStatusResponse:
    settings_document, settings_saved = load_ai_settings()
    provider_statuses = collect_provider_public_statuses(effective_provider_settings(settings_document))
    ollama_status = next(
        item for item in provider_statuses if item.provider == "ollama"
    )
    stt_status = next(
        item for item in provider_statuses if item.provider == "local_stt"
    )
    external_credentials = [
        item.provider
        for item in provider_statuses
        if item.processing_location == "external" and item.credential_configured
    ]
    hardware = detect_ai_hardware(
        ollama_ready=ollama_status.connection_state == "ready",
        ollama_processing_location=ollama_status.processing_location,
        local_models=ollama_status.models,
        stt_ready=stt_status.connection_state == "ready",
        stt_model=stt_status.configured_model,
        external_credentials=external_credentials,
    )
    recommendation = recommend_execution_mode(hardware)
    baseline_plan = build_routing_preview(
        AiRoutingPreviewRequest(text="", capability="text"),
        settings_document,
        provider_statuses,
        hardware,
    )
    primary = baseline_plan.candidates[0]
    runtime = AiRuntimeRouteStatus(
        selected_mode=settings_document.mode,
        recommended_mode=recommendation.mode,
        current_path=primary.path,
        provider=primary.provider,
        model=primary.model,
        processing_location=primary.processing_location,
        external_transmission=primary.external_transmission,
        fallback_active=primary.provider == "rules",
        fallback_state="baseline" if primary.provider == "rules" else "primary",
        fallback_reason=(
            "검증된 AI 공급자가 없어 규칙 기반 기본 경로를 사용합니다."
            if primary.provider == "rules"
            else None
        ),
    )
    return AiSystemStatusResponse(
        generated_at=datetime.now(timezone.utc),
        hardware=hardware,
        recommendation=recommendation,
        runtime=runtime,
        providers=provider_statuses,
        model_roles=effective_model_roles(settings_document),
        model_role_options=_role_model_options(provider_statuses),
        provider_priority=settings_document.provider_priority,
        fallback_order=settings_document.fallback_order,
        max_attempts=settings_document.max_attempts,
        total_timeout_seconds=settings_document.total_timeout_seconds,
        external_deidentified_enabled=settings_document.external_deidentified_enabled,
        settings_saved=settings_saved,
    )


@router.get("/status", response_model=AiSystemStatusResponse)
def ai_system_status(
    response: Response,
    admin: User = Depends(require_admin),
) -> AiSystemStatusResponse:
    _no_store(response)
    return _system_snapshot().model_copy(
        update={"settings_write_allowed": _ai_settings_write_allowed(admin)}
    )


@router.get("/central-models")
def central_model_status(response: Response, _admin: User = Depends(require_admin)):
    _no_store(response)
    return _central_model_public()


@router.post("/central-models/catalog")
def central_model_catalog(response: Response, _admin: User = Depends(require_ai_settings_write_admin)):
    _no_store(response)
    document, _ = load_ai_settings()
    return _central_catalog(document)


@router.put("/central-models/connection")
def central_model_connection(request: AiCentralConnectionRequest, response: Response, _admin: User = Depends(require_ai_settings_write_admin)):
    _no_store(response)
    if settings.ai_central_models_json:
        raise HTTPException(status_code=409, detail="보호된 환경 설정이 적용 중입니다.")
    document, _ = load_ai_settings()
    policy = central_editable_policy(document)
    provider = "rules" if request.environment == "rules" else request.provider if request.environment == "external" else "ollama"
    if request.environment == "external" and provider not in _ONE_CLICK_EXTERNAL_PROVIDERS:
        raise HTTPException(status_code=422, detail="지원되는 외부 AI 제공자를 선택해 주세요.")
    changes = {"default_provider": provider, "execution_environment": request.environment}
    if provider == "ollama":
        if request.base_url:
            try:
                _validate_endpoint("ollama", request.base_url)
            except ValueError as error:
                raise HTTPException(status_code=422, detail="기관 내부 서버 주소를 확인해 주세요.") from error
            changes["base_url"] = request.base_url
        elif request.environment == "local":
            from pathlib import Path
            changes["base_url"] = "http://host.docker.internal:11434" if Path("/.dockerenv").exists() else "http://127.0.0.1:11434"
        elif not policy.base_url:
            raise HTTPException(status_code=422, detail="기관 서버 주소를 입력해 주세요.")
    try:
        policy = AiCentralModels.model_validate({**policy.model_dump(), **changes})
        save_ai_settings(document.model_copy(update={"central_models": policy}))
    except ValueError as error:
        raise HTTPException(status_code=422, detail="연결 주소와 선택한 환경을 확인해 주세요.") from error
    return _central_model_public()


@router.post("/central-models/verify-image")
def central_image_check(request: AiCentralImageCheckRequest, response: Response, _admin: User = Depends(require_ai_settings_write_admin)):
    """Only a generated non-personal image is sent; never stored care material."""
    _no_store(response)
    document, _ = load_ai_settings()
    policy = effective_central_models(document)
    provider = effective_provider_settings(document).get(policy.default_provider)
    selected = AiModelRoleSelection(provider=policy.default_provider, model=request.model)
    from PIL import Image, ImageDraw, ImageFont
    canvas = Image.new("RGB", (200, 90), "white")
    ImageDraw.Draw(canvas).text((25, 15), "582", font=ImageFont.load_default(size=48), fill="black")
    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    started = perf_counter()
    verified = False
    try:
        if not provider:
            raise ValueError("unconfigured")
        if policy.default_provider == "ollama":
            info = local_json_request(provider.base_url, "/api/show", {"model": request.model}, 3)
            if "vision" not in info.get("capabilities", []) or info.get("remote_model") or info.get("remote_host") or "cloud" in request.model.lower():
                raise ValueError("image_unverified")
            result = local_json_request(provider.base_url, "/api/chat", {"model": request.model, "stream": False, "think": False, "format": "json", "options": {"num_predict": 64, "num_ctx": 2048}, "messages": [{"role": "user", "content": 'Read only the digits in this image. Return {"digits":"..."}.', "images": [base64.b64encode(buffer.getvalue()).decode()]}]}, 15)
            import json
            verified = result.get("model") == request.model and result.get("done") is True and json.loads(result["message"]["content"]).get("digits") == "582"
        elif policy.default_provider in _ONE_CLICK_EXTERNAL_PROVIDERS:
            result, _ = generate_ai_assist_result(policy.default_provider, provider.model_copy(update={"timeout_seconds": 15}), model=request.model, task_type="image_text", question="시험 그림에 보이는 숫자만 읽어 주세요.", sources=[], images=[{"content": buffer.getvalue(), "mime_type": "image/png", "filename": "synthetic-digits.png"}])
            verified = "582" in (result.visible_text or result.answer)
    except Exception:
        verified = False
    if verified and not settings.ai_central_models_json:
        binding = central_image_binding(document, selected)
        # A slow check must not replace a newer admin selection or endpoint.
        latest, _ = load_ai_settings()
        if central_image_binding(latest, selected) != binding or latest.central_models.default_provider != policy.default_provider:
            raise HTTPException(status_code=409, detail="확인 중 연결이 변경되었습니다. 현재 환경에서 다시 확인해 주세요.")
        policy = latest.central_models.model_copy(deep=True)
        policy.image_verification_bindings = list(dict.fromkeys([*policy.image_verification_bindings, binding]))[-50:]
        save_ai_settings(latest.model_copy(update={"central_models": policy}))
    return {"verified": verified, "model": request.model, "synthetic_image_only": True, "latency_ms": round((perf_counter()-started)*1000), "message": "합성 이미지 입력과 숫자 판독을 확인했습니다. 실제 서류 품질 검증은 별도입니다." if verified else "이미지 기능을 확인하지 못했습니다. 기존 안전한 판독 경로를 유지합니다."}


@router.put("/settings/central-models")
def update_central_models(document: AiCentralModels, response: Response, _admin: User = Depends(require_ai_settings_write_admin)):
    _no_store(response)
    current, _ = load_ai_settings()
    if settings.ai_central_models_json:
        raise HTTPException(status_code=409, detail="보호된 환경 설정이 적용 중입니다.")
    if document.text_fallback_model != current.central_models.text_fallback_model or document.fallback_qualified != current.central_models.fallback_qualified:
        raise HTTPException(status_code=422, detail="대체 모델은 서버에서 성능·안정성 검증 후 등록해야 합니다.")
    # Endpoints and log-policy approval are protected server configuration;
    # ordinary model selection cannot silently change either of them.
    merged = document.model_copy(update={"inheritance_version": 2, "default_provider": current.central_models.default_provider, "execution_environment": current.central_models.execution_environment, "base_url": current.central_models.base_url, "real_record_logging_verified": current.central_models.real_record_logging_verified, "image_verification_bindings": current.central_models.image_verification_bindings})
    save_ai_settings(current.model_copy(update={"central_models": merged}))
    return _central_model_public()

@router.put("/settings", response_model=AiSettingsSaveResponse)
def update_ai_system_settings(
    document: AiSettingsUpdateRequest,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiSettingsSaveResponse:
    _no_store(response)
    try:
        current, _ = load_ai_settings()
        merged_providers = dict(current.providers)
        for provider, incoming in document.providers.items():
            previous = merged_providers.get(provider)
            changes = incoming.model_dump(exclude_unset=True)
            merged_providers[provider] = (
                previous.model_copy(update=changes)
                if previous is not None
                else incoming
            )
        # 기능별 모델은 연결·기능 확인 결과를 검증하는 전용 API에서만
        # 바꾼다. 과거의 전체 설정 저장 요청이 임의 모델을 덮어쓰는
        # 우회 경로가 되거나 사용자의 직접 선택을 지우지 않게 한다.
        merged = document.model_copy(
            update={
                "providers": merged_providers,
                "model_roles": current.model_roles,
                "central_models": current.central_models,
            }
        )
        saved = save_ai_settings(merged)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return AiSettingsSaveResponse(
        mode=saved.mode,
        message="AI 설정을 보호된 서버 저장소에 저장했습니다.",
    )


@router.put("/settings/mode", response_model=AiSettingsSaveResponse)
def update_ai_execution_mode(
    document: AiModeUpdateRequest,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiSettingsSaveResponse:
    """Save one easy mode without accepting stale provider settings from the UI."""
    _no_store(response)
    try:
        current, _ = load_ai_settings()
        merged = current.model_copy(
            update={
                "mode": document.mode,
                "external_deidentified_enabled": document.mode != "fully_local",
            }
        )
        saved = save_ai_settings(merged)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return AiSettingsSaveResponse(
        mode=saved.mode,
        message="실행 방식을 저장했습니다.",
    )


@router.put(
    "/settings/model-roles",
    response_model=AiModelRolesSaveResponse,
)
def update_ai_model_roles(
    document: AiModelRoles,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiModelRolesSaveResponse:
    """Save role choices without exposing or replacing protected provider settings."""
    _no_store(response)
    current, _ = load_ai_settings()
    statuses = collect_provider_public_statuses(current.providers)
    available = _role_model_options(statuses)
    for role_name in ("text", "vision", "stt", "precision"):
        incoming = getattr(document, role_name)
        previous = resolved_model_role(current, role_name)
        if incoming is None:
            if role_name in {"text", "precision"}:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="대화·문서 정리와 보고서·상세 분석 모델을 선택해 주세요.",
                )
            continue
        valid = any(
            option.provider == incoming.provider and option.model == incoming.model
            for option in getattr(available, role_name)
        )
        if not valid and incoming != previous:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="현재 연결과 기능 확인을 통과한 모델만 선택할 수 있습니다.",
            )
    if current.central_models.inheritance_version == 2:
        policy = effective_central_models(current).model_copy(deep=True)
        for role_name, feature in ROLE_FEATURES.items():
            selection = getattr(document, role_name)
            if selection is None:
                policy.feature_overrides.pop(feature, None)
            else:
                policy.feature_overrides[feature] = selection
        saved = save_ai_settings(current.model_copy(update={"central_models": policy}))
    else:
        saved = save_ai_settings(current.model_copy(update={"model_roles": document}))
    return AiModelRolesSaveResponse(
        mode=saved.mode,
        model_roles=effective_model_roles(saved),
        message="기능별 AI 선택을 저장했습니다.",
    )


@router.put(
    "/providers/{provider}/settings",
    response_model=AiSettingsSaveResponse,
)
def update_ai_provider_settings(
    provider: AiSystemProviderId,
    document: AiProviderSettingsPatch,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiSettingsSaveResponse:
    """Save one provider without committing unrelated unsaved screen fields."""
    _no_store(response)
    if provider not in PROVIDER_DEFINITIONS:
        raise HTTPException(status_code=404, detail="지원하지 않는 AI 공급자입니다.")
    try:
        current, _ = load_ai_settings()
        previous = current.providers.get(provider) or AiProviderSettingsPatch()
        changes = document.model_dump(exclude_unset=True)
        policy = current.central_models.model_copy(deep=True)
        if policy.inheritance_version == 2:
            central_changes = {}
            if provider == "ollama" and "base_url" in changes:
                central_changes["base_url"] = changes.pop("base_url")
            if provider == policy.default_provider and "model" in changes:
                central_changes["text_default_model"] = changes.pop("model")
            if central_changes and settings.ai_central_models_json:
                raise HTTPException(status_code=409, detail="보호된 환경 설정이 적용 중입니다.")
            policy = AiCentralModels.model_validate({**policy.model_dump(), **central_changes})
        providers = dict(current.providers)
        providers[provider] = previous.model_copy(update=changes)
        merged = current.model_copy(update={"providers": providers, "central_models": policy})
        saved = save_ai_settings(merged)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return AiSettingsSaveResponse(
        mode=saved.mode,
        message="이 공급자의 사용 설정을 보호된 서버 저장소에 저장했습니다.",
    )


@router.put(
    "/providers/{provider}/credential",
    response_model=AiProviderCredentialUpdateResponse,
)
def update_ai_provider_credential(
    provider: AiSystemProviderId,
    document: AiProviderCredentialUpdateRequest,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiProviderCredentialUpdateResponse:
    _no_store(response)
    if provider_secret_env_name(provider) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="이 공급자는 API 키를 저장하지 않습니다.",
        )
    try:
        save_provider_secret(provider, document.api_key)
        invalidate_provider_probe(provider)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    return AiProviderCredentialUpdateResponse(
        provider=provider,
        message="API 키를 보호된 서버 저장소에 저장했습니다. 응답에는 키를 포함하지 않습니다.",
    )


@router.post(
    "/providers/{provider}/connect",
    response_model=AiProviderConnectResponse,
)
def connect_ai_provider(
    provider: AiSystemProviderId,
    document: AiProviderConnectRequest,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiProviderConnectResponse:
    """Store, verify, select, and enable one external provider in one action."""
    _no_store(response)
    if provider not in _ONE_CLICK_EXTERNAL_PROVIDERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="이 연결 방식은 외부 AI 공급자에서만 사용할 수 있습니다.",
        )
    current, _ = load_ai_settings()
    previous = current.providers.get(provider) or AiProviderSettingsPatch()
    if document.api_key is not None:
        try:
            save_provider_secret(provider, document.api_key)
            invalidate_provider_probe(provider)
        except ValueError as error:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="API 키를 보호 저장소에 저장하지 못했습니다.",
            ) from error

    requested_model = document.model.strip() if document.model else None
    probe_model = requested_model or previous.model
    if (
        provider == "gemini"
        and requested_model is None
        and probe_model in {"gemini-2.5-flash", "gemini-2.0-flash"}
    ):
        # 기존 기본값만 남은 DEV는 실제 목록을 다시 확인해 최신 권장
        # Flash 모델을 선택한다. 사용자가 모델을 명시한 경우에는 보존한다.
        probe_model = None

    probe_settings = previous.model_copy(
        update={"enabled": False, "model": probe_model}
    )
    providers = dict(current.providers)
    # 연결 실패 시에도 기존 선택 모델은 잃지 않도록 비활성 상태만 먼저 저장한다.
    providers[provider] = previous.model_copy(update={"enabled": False})
    disabled_document = current.model_copy(update={"providers": providers})
    save_ai_settings(disabled_document)

    result = test_provider_connection(
        provider,
        probe_settings,
        run_contract_test=True,
    )
    if not result.connection_ok or result.structured_contract_ok is not True:
        return AiProviderConnectResponse(
            provider=provider,
            connected=False,
            enabled=False,
            selected_model=None,
            structured_contract_ok=False,
            latency_ms=result.latency_ms,
            error_code=result.error_code,
            message=_connection_failure_message(result.error_code),
            deidentified_fixture_used=result.deidentified_fixture_used,
            external_call_performed=result.external_call_performed,
        )

    selected_model = result.selected_model
    if not selected_model or selected_model not in result.models:
        return AiProviderConnectResponse(
            provider=provider,
            connected=False,
            enabled=False,
            selected_model=None,
            structured_contract_ok=False,
            latency_ms=result.latency_ms,
            error_code="provider_unavailable",
            message="이 계정에서 사용할 수 있는 모델이 없습니다.",
            deidentified_fixture_used=result.deidentified_fixture_used,
            external_call_performed=result.external_call_performed,
        )

    providers[provider] = previous.model_copy(
        update={"enabled": True, "model": selected_model}
    )
    enabled_document = current.model_copy(
        update={
            "providers": providers,
            "external_deidentified_enabled": (
                current.mode != "fully_local"
            ),
        }
    )
    saved = save_ai_settings(enabled_document)
    saved_status = next(
        item for item in collect_provider_public_statuses(saved.providers)
        if item.provider == provider
    )
    connected = bool(
        saved_status.enabled
        and saved_status.connection_state == "ready"
        and saved_status.structured_contract_verified
        and saved_status.configured_model == selected_model
    )
    if not connected:
        providers[provider] = providers[provider].model_copy(update={"enabled": False})
        rollback_document = saved.model_copy(update={"providers": providers})
        save_ai_settings(rollback_document)
    return AiProviderConnectResponse(
        provider=provider,
        connected=connected,
        enabled=connected,
        selected_model=selected_model if connected else None,
        structured_contract_ok=connected,
        latency_ms=result.latency_ms,
        error_code=None if connected else "provider_unavailable",
        message=(
            "연결되었습니다. 사용할 AI와 모델을 자동으로 설정했습니다."
            if connected
            else "연결 결과를 저장하지 못해 사용을 시작하지 않았습니다."
        ),
        deidentified_fixture_used=result.deidentified_fixture_used,
        external_call_performed=result.external_call_performed,
    )


@router.post(
    "/providers/{provider}/test",
    response_model=AiProviderTestResponse,
)
def test_ai_provider(
    provider: AiSystemProviderId,
    document: AiProviderTestRequest,
    response: Response,
    _admin: User = Depends(require_ai_settings_write_admin),
) -> AiProviderTestResponse:
    _no_store(response)
    if provider not in PROVIDER_DEFINITIONS:
        raise HTTPException(status_code=404, detail="지원하지 않는 AI 공급자입니다.")
    settings_document, _ = load_ai_settings()
    provider_settings = effective_provider_settings(settings_document).get(provider)
    policy = effective_central_models(settings_document)
    if provider_settings and provider == policy.default_provider and policy.inheritance_version == 2:
        provider_settings = provider_settings.model_copy(update={"timeout_seconds": policy.timeout_seconds})
    if provider_settings is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="공급자 설정이 없습니다.",
        )
    return test_provider_connection(
        provider,
        provider_settings,
        run_contract_test=document.run_contract_test,
    )


@router.post(
    "/privacy/classify",
    response_model=AiPrivacyClassificationResponse,
)
def classify_ai_data(
    document: AiPrivacyClassificationRequest,
    response: Response,
    _admin: User = Depends(require_admin),
) -> AiPrivacyClassificationResponse:
    _no_store(response)
    return classify_ai_input(document)


@router.post("/routing/preview", response_model=AiRoutingPreviewResponse)
def preview_ai_route(
    document: AiRoutingPreviewRequest,
    response: Response,
    _admin: User = Depends(require_admin),
) -> AiRoutingPreviewResponse:
    _no_store(response)
    settings_document, _ = load_ai_settings()
    provider_statuses = collect_provider_public_statuses(settings_document.providers)
    ollama_status = next(
        item for item in provider_statuses if item.provider == "ollama"
    )
    stt_status = next(
        item for item in provider_statuses if item.provider == "local_stt"
    )
    hardware = detect_ai_hardware(
        ollama_ready=ollama_status.connection_state == "ready",
        ollama_processing_location=ollama_status.processing_location,
        local_models=ollama_status.models,
        stt_ready=stt_status.connection_state == "ready",
        stt_model=stt_status.configured_model,
        external_credentials=[
            item.provider
            for item in provider_statuses
            if item.processing_location == "external" and item.credential_configured
        ],
    )
    return build_routing_preview(
        document,
        settings_document,
        provider_statuses,
        hardware,
    )
