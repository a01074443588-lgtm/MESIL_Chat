from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any, Callable

from .ai_hardware import recommend_execution_mode
from .ai_privacy import classify_ai_input
from .config import settings as app_settings
from .ai_settings_store import effective_central_models, resolved_model_role
from .ai_system_schemas import (
    AiFailureCode,
    AiHardwareStatus,
    AiProviderPublicStatus,
    AiRouteCandidate,
    AiRoutingPreviewRequest,
    AiRoutingPreviewResponse,
    AiSettingsUpdateRequest,
    AiSystemProviderId,
)


class AiRouteAttemptError(RuntimeError):
    def __init__(self, code: AiFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AiRouteAttempt:
    provider: AiSystemProviderId
    model: str
    status: str
    error_code: AiFailureCode | None
    latency_ms: int
    external_transmission: bool


@dataclass(frozen=True)
class AiRouteExecutionResult:
    value: Any
    selected: AiRouteCandidate
    attempts: tuple[AiRouteAttempt, ...]
    fallback_active: bool


class AiRouteExhausted(RuntimeError):
    def __init__(self, attempts: tuple[AiRouteAttempt, ...]) -> None:
        super().__init__("사용 가능한 AI 경로가 모두 실패했습니다.")
        self.attempts = attempts


RouteRunner = Callable[[AiRouteCandidate], Any]


def _path_order(
    mode: str,
    recommended_mode: str,
    configured_order: list[str],
) -> tuple[str, ...]:
    effective_mode = recommended_mode if mode == "automatic" else mode
    configured = tuple(dict.fromkeys(configured_order))
    if effective_mode == "fully_local":
        return tuple(path for path in configured if path != "external")
    if effective_mode == "local_first":
        local_paths = tuple(path for path in configured if path in {"local", "internal"})
        remaining = tuple(
            path for path in configured if path not in {"local", "internal"}
        )
        return (*local_paths, *remaining)
    external = tuple(path for path in configured if path == "external")
    remaining = tuple(path for path in configured if path != "external")
    return (*external, *remaining)


def _model_for_provider(
    provider: AiSystemProviderId,
    settings: AiSettingsUpdateRequest,
    model_role: str | None = None,
) -> str:
    if model_role in {"text", "vision", "stt", "precision"}:
        selection = resolved_model_role(settings, model_role)
        if selection is not None and selection.provider == provider:
            return selection.model
    # The saved role contract names one primary vision model.  The existing
    # OCR model setting is the protected local/internal vision fallback, so an
    # Ollama vision candidate must not silently inherit the text-only 35B
    # briefing model from the provider default.
    if model_role == "vision" and provider == "ollama":
        return app_settings.ocr_model
    configured = settings.providers.get(provider)
    if configured is not None and configured.model:
        return configured.model
    if provider == "rules":
        return "quick-period-summary-v1"
    return "모델 미설정"


def build_routing_preview(
    request: AiRoutingPreviewRequest,
    settings: AiSettingsUpdateRequest,
    providers: list[AiProviderPublicStatus],
    hardware: AiHardwareStatus,
) -> AiRoutingPreviewResponse:
    privacy = classify_ai_input(request)
    recommendation = recommend_execution_mode(hardware)
    selected_role = (
        resolved_model_role(settings, request.model_role)
        if request.model_role in {"text", "vision", "stt", "precision"}
        else None
    )
    if selected_role is None and request.model_role is None and effective_central_models(settings).inheritance_version == 2:
        selected_role = resolved_model_role(settings, "text")
    # 기능별 모델을 직접 고르는 현재 계약에서는 과거 전역 실행 방식이
    # 저장된 선택을 다시 덮어쓰지 않는다. 호출자가 mode를 명시한 기존
    # 진단 요청만 예전 실행 방식 계약을 그대로 사용한다.
    direct_role_routing = request.mode is None and selected_role is not None
    legacy_selected_mode = request.mode or settings.mode
    selected_mode = "automatic" if direct_role_routing else legacy_selected_mode
    path_order = _path_order(
        selected_mode,
        recommendation.mode,
        list(settings.fallback_order),
    )
    selected_status = next(
        (
            status
            for status in providers
            if selected_role is not None and status.provider == selected_role.provider
        ),
        None,
    )
    selected_path = selected_status.processing_location if selected_status else None
    if direct_role_routing and selected_path in {"local", "internal", "external"}:
        path_order = tuple(dict.fromkeys((selected_path, *path_order)))
    direct_external_selection = (
        direct_role_routing and selected_path == "external"
    )
    external_policy_enabled = (
        settings.external_deidentified_enabled or direct_external_selection
    )
    external_allowed = (
        selected_mode != "fully_local"
        and external_policy_enabled
        and privacy.external_allowed
    )
    blocked_reasons: list[str] = []
    if not privacy.external_allowed:
        blocked_reasons.extend(privacy.reasons)
    elif not external_policy_enabled:
        blocked_reasons.append("사용자 정책에서 비식별 외부 API 사용이 비활성화되어 있습니다.")
    if selected_mode == "fully_local":
        blocked_reasons.append("완전 로컬 모드에서는 외부 공급자를 후보에서 제외합니다.")

    priority = {provider: index for index, provider in enumerate(settings.provider_priority)}
    path_priority = {path: index for index, path in enumerate(path_order)}
    required_capabilities = {request.capability, *request.additional_capabilities}
    candidates: list[AiRouteCandidate] = []
    central = effective_central_models(settings)
    for status in providers:
        provider = status.provider
        if provider == "rules":
            continue
        if central.inheritance_version == 2 and central.default_provider == "rules":
            continue
        if not status.enabled or status.connection_state != "ready":
            continue
        if not required_capabilities.issubset(status.capabilities):
            continue
        if required_capabilities != {"speech_to_text"} and not status.structured_contract_verified:
            continue
        model = _model_for_provider(provider, settings, request.model_role or ("text" if central.inheritance_version == 2 else None))
        legacy_safe_image = request.model_role == "vision" and provider == "ollama" and model == app_settings.ocr_model and status.processing_location in {"local", "internal"}
        if central.inheritance_version == 2 and request.mode is None and not legacy_safe_image and (selected_role is None or provider != selected_role.provider or model != selected_role.model):
            continue
        if (
            "image" in required_capabilities
            and "audio" in required_capabilities
            and "nano-omni" in model.lower()
        ):
            blocked_reasons.append(
                "Nano Omni Hosted의 이미지+음성 동시 입력은 반복 503 검증 결과로 후보에서 제외했습니다."
            )
            continue
        path = status.processing_location
        if path not in {"local", "internal", "external"}:
            continue
        if path == "external" and not external_allowed:
            continue
        if central.inheritance_version == 2 and path == "external" and (selected_role is None or provider != selected_role.provider or model != selected_role.model):
            continue
        if (
            direct_external_selection
            and not settings.external_deidentified_enabled
            and path == "external"
            and (
                selected_role is None
                or provider != selected_role.provider
                or model != selected_role.model
            )
        ):
            continue
        if path not in path_priority:
            continue
        candidates.append(
            AiRouteCandidate(
                provider=provider,
                model=model,
                path=path,
                processing_location=status.processing_location,
                external_transmission=path == "external",
                role="fallback",
                reason=(
                    "관리자가 이 기능에 선택한 AI입니다."
                    if selected_role is not None
                    and selected_role.provider == provider
                    and selected_role.model == model
                    else "연결·지원 기능·구조화 계약 점검을 통과한 외부 후보입니다."
                    if path == "external"
                    else "연결·지원 기능·구조화 계약 점검을 통과한 보호 경로입니다."
                ),
            )
        )
    vision_routing = request.model_role == "vision" and "image" in required_capabilities

    def candidate_order(item: AiRouteCandidate) -> tuple[int, int, int]:
        if (
            selected_role is not None
            and item.provider == selected_role.provider
            and item.model == selected_role.model
        ):
            return (-1, 0, 0)
        if not vision_routing:
            return (
                0,
                path_priority.get(item.path, 99),
                priority.get(item.provider, 99),
            )
        if (
            external_allowed
            and item.provider == "nvidia"
            and "nano-omni" in item.model.lower()
        ):
            role_priority = 0
        elif item.provider == "ollama" and item.path in {"local", "internal"}:
            role_priority = 1
        else:
            role_priority = 2
        return (
            role_priority,
            path_priority.get(item.path, 99),
            priority.get(item.provider, 99),
        )

    candidates.sort(key=candidate_order)
    candidates = candidates[:7]
    candidates.append(
        AiRouteCandidate(
            provider="rules",
            model=_model_for_provider("rules", settings),
            path="rules",
            processing_location="rules",
            external_transmission=False,
            role="fallback",
            reason="API 키·GPU·네트워크가 없어도 동작하는 실패폐쇄 기본 경로입니다.",
        )
    )
    candidates[0] = candidates[0].model_copy(update={"role": "primary"})
    return AiRoutingPreviewResponse(
        selected_mode=selected_mode,
        data_classification=privacy.classification,
        external_transmission_allowed=external_allowed,
        candidates=candidates,
        blocked_reasons=list(dict.fromkeys(blocked_reasons))[:10],
        max_attempts=settings.max_attempts,
        total_timeout_seconds=settings.total_timeout_seconds,
    )


def execute_with_fallback(
    plan: AiRoutingPreviewResponse,
    runners: dict[AiSystemProviderId, RouteRunner],
    *,
    clock: Callable[[], float] = perf_counter,
) -> AiRouteExecutionResult:
    attempts: list[AiRouteAttempt] = []
    started = clock()
    for candidate in plan.candidates[: plan.max_attempts]:
        if clock() - started >= plan.total_timeout_seconds:
            attempts.append(
                AiRouteAttempt(
                    provider=candidate.provider,
                    model=candidate.model,
                    status="blocked",
                    error_code="time_budget_exhausted",
                    latency_ms=0,
                    external_transmission=False,
                )
            )
            break
        runner = runners.get(candidate.provider)
        attempt_started = clock()
        if runner is None:
            attempts.append(
                AiRouteAttempt(
                    provider=candidate.provider,
                    model=candidate.model,
                    status="failed",
                    error_code="provider_unavailable",
                    latency_ms=max(0, round((clock() - attempt_started) * 1000)),
                    external_transmission=candidate.external_transmission,
                )
            )
            continue
        try:
            value = runner(candidate)
        except AiRouteAttemptError as error:
            attempts.append(
                AiRouteAttempt(
                    provider=candidate.provider,
                    model=candidate.model,
                    status="failed",
                    error_code=error.code,
                    latency_ms=max(0, round((clock() - attempt_started) * 1000)),
                    external_transmission=candidate.external_transmission,
                )
            )
            continue
        attempts.append(
            AiRouteAttempt(
                provider=candidate.provider,
                model=candidate.model,
                status="succeeded",
                error_code=None,
                latency_ms=max(0, round((clock() - attempt_started) * 1000)),
                external_transmission=candidate.external_transmission,
            )
        )
        return AiRouteExecutionResult(
            value=value,
            selected=candidate,
            attempts=tuple(attempts),
            fallback_active=len(attempts) > 1,
        )
    raise AiRouteExhausted(tuple(attempts))
