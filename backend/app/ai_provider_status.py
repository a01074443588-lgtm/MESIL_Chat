from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .ai_assist_schemas import (
    AiProviderCheckName,
    AiProviderCheckResponse,
    AiProviderCheckStatus,
    AiProviderConnectionResponse,
    AiProviderId,
    AiProviderProcessingLocation,
    AiProviderStatusResponse,
)
from .config import settings
from .finals_readiness import endpoint_scope
from .local_ai import nemotron_is_configured


JsonGetter = Callable[[str, float], dict[str, Any]]
CHECK_NAMES: tuple[AiProviderCheckName, ...] = (
    "configuration",
    "authentication",
    "model_list",
    "capabilities",
    "structured_contract",
    "latency",
)


def _urllib_json_get(url: str, timeout_seconds: float) -> dict[str, Any]:
    request = Request(url, headers={"Accept": "application/json"}, method="GET")
    with urlopen(request, timeout=timeout_seconds) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("AI 제공자 상태 응답이 JSON 객체가 아닙니다.")
    return parsed


def _check(
    name: AiProviderCheckName,
    status: AiProviderCheckStatus,
    detail: str,
    *,
    latency_ms: int | None = None,
) -> AiProviderCheckResponse:
    return AiProviderCheckResponse(
        name=name,
        status=status,
        latency_ms=latency_ms,
        detail=detail,
    )


def _rules_status() -> AiProviderConnectionResponse:
    return AiProviderConnectionResponse(
        provider="rules",
        display_name="규칙 기반 기본 기능",
        adapter_available=True,
        configured=True,
        state="ready",
        processing_location="rules",
        endpoint_scope="rules",
        credential_configured=False,
        configured_model="quick-period-summary-v1",
        discovered_models=["quick-period-summary-v1"],
        capabilities=["text", "structured_output", "offline"],
        enabled_features=["instant_period_summary", "rules_fallback"],
        requires_deidentified_approval=False,
        checks=[
            _check("configuration", "passed", "별도 설정 없이 사용할 수 있습니다."),
            _check(
                "authentication",
                "not_required",
                "인증정보가 필요하지 않습니다.",
            ),
            _check(
                "model_list",
                "not_required",
                "규칙 엔진은 모델 목록을 사용하지 않습니다.",
            ),
            _check("capabilities", "passed", "즉시 요약과 안전 fallback을 제공합니다."),
            _check("structured_contract", "passed", "애플리케이션 스키마로 결과를 생성합니다."),
            _check("latency", "passed", "네트워크 추론을 사용하지 않습니다.", latency_ms=0),
        ],
        notice="API 키나 GPU가 없어도 즉시 사용할 수 있는 기본 경로입니다.",
    )


def _ollama_status(json_get: JsonGetter) -> AiProviderConnectionResponse:
    base_url = settings.ai_review_base_url.strip().rstrip("/")
    scope = endpoint_scope(base_url)
    configured_model = settings.ai_review_model.strip() or None
    if scope not in {"local", "internal"}:
        return AiProviderConnectionResponse(
            provider="ollama",
            display_name="Ollama / 로컬 호환 Endpoint",
            adapter_available=True,
            configured=False,
            state="unavailable",
            processing_location="local",
            endpoint_scope=scope,
            credential_configured=False,
            configured_model=configured_model,
            requires_deidentified_approval=False,
            checks=[
                _check(
                    "configuration",
                    "failed",
                    "로컬 또는 내부망 HTTP Endpoint만 자동 점검합니다.",
                ),
                _check(
                    "authentication",
                    "not_required",
                    "현재 Ollama 경로는 별도 인증을 사용하지 않습니다.",
                ),
                *[
                    _check(name, "not_run", "안전한 Endpoint 설정 전에는 실행하지 않습니다.")
                    for name in CHECK_NAMES[2:]
                ],
            ],
            notice="외부 주소로 분류된 Ollama Endpoint에는 자동 접속하지 않습니다.",
        )

    started = perf_counter()
    try:
        payload = json_get(f"{base_url}/api/tags", 1.5)
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    except (HTTPError, URLError, TimeoutError, OSError, ValueError):
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
        return AiProviderConnectionResponse(
            provider="ollama",
            display_name="Ollama / 로컬 호환 Endpoint",
            adapter_available=True,
            configured=True,
            state="unavailable",
            processing_location="local",
            endpoint_scope=scope,
            credential_configured=False,
            configured_model=configured_model,
            requires_deidentified_approval=False,
            checks=[
                _check("configuration", "passed", "로컬 또는 내부망 Endpoint 형식입니다."),
                _check(
                    "authentication",
                    "not_required",
                    "현재 Ollama 경로는 별도 인증을 사용하지 않습니다.",
                ),
                _check("model_list", "failed", "모델 목록을 가져오지 못했습니다."),
                _check("capabilities", "not_run", "모델 목록 확인 후 점검합니다."),
                _check("structured_contract", "not_run", "모델 목록 확인 후 점검합니다."),
                _check(
                    "latency",
                    "failed",
                    "Endpoint 응답을 완료하지 못했습니다.",
                    latency_ms=elapsed_ms,
                ),
            ],
            notice="Endpoint 설정은 있으나 현재 연결할 수 없습니다.",
        )

    models = payload.get("models")
    discovered_models = (
        sorted(
            {
                str(item.get("name") or item.get("model")).strip()
                for item in models
                if isinstance(item, dict)
                and (item.get("name") or item.get("model"))
            }
        )[:100]
        if isinstance(models, list)
        else []
    )
    has_models = bool(discovered_models)
    return AiProviderConnectionResponse(
        provider="ollama",
        display_name="Ollama / 로컬 호환 Endpoint",
        adapter_available=True,
        configured=True,
        state="ready" if has_models else "unavailable",
        processing_location="local",
        endpoint_scope=scope,
        credential_configured=False,
        configured_model=configured_model,
        discovered_models=discovered_models,
        capabilities=[],
        enabled_features=[],
        requires_deidentified_approval=False,
        checks=[
            _check("configuration", "passed", "로컬 또는 내부망 Endpoint 형식입니다."),
            _check(
                "authentication",
                "not_required",
                "현재 Ollama 경로는 별도 인증을 사용하지 않습니다.",
            ),
            _check(
                "model_list",
                "passed" if has_models else "failed",
                (
                    f"모델 {len(discovered_models)}개를 확인했습니다."
                    if has_models
                    else "사용 가능한 모델이 없습니다."
                ),
            ),
            _check("capabilities", "not_run", "모델별 기능 점검은 다음 구현 단위입니다."),
            _check(
                "structured_contract",
                "not_run",
                "비식별 사전검증 전에는 기능을 활성화하지 않습니다.",
            ),
            _check(
                "latency",
                "passed",
                "모델 목록 응답 시간을 측정했습니다.",
                latency_ms=elapsed_ms,
            ),
        ],
        notice=(
            "연결과 모델 목록은 확인됐지만 구조화 계약 통과 전에는 AI 기능을 자동 활성화하지 않습니다."
            if has_models
            else "Endpoint에 연결됐지만 사용 가능한 모델이 없습니다."
        ),
    )


def _nvidia_status() -> AiProviderConnectionResponse:
    configured = nemotron_is_configured()
    return AiProviderConnectionResponse(
        provider="nvidia",
        display_name="NVIDIA Hosted API",
        adapter_available=True,
        configured=configured,
        state="configured_unverified" if configured else "unconfigured",
        processing_location="external",
        endpoint_scope="external",
        credential_configured=configured,
        configured_model=settings.nvidia_nemotron_model.strip() or None,
        requires_deidentified_approval=True,
        checks=[
            _check(
                "configuration",
                "passed" if configured else "failed",
                "보호된 서버 설정에서 키 존재를 확인했습니다."
                if configured
                else "보호된 서버 설정에 키가 없습니다.",
            ),
            *[
                _check(
                    name,
                    "not_run",
                    "명시적 연결 점검 전에는 외부 요청을 보내지 않습니다.",
                )
                for name in CHECK_NAMES[1:]
            ],
        ],
        notice=(
            "키 존재만 확인했으며 인증·모델·계약·지연과 무료 한도·결제 조건은 확인하지 않았습니다."
            if configured
            else "키 없이도 규칙 기반 기본 기능은 계속 사용할 수 있습니다."
        ),
    )


def _not_implemented_status(
    provider: AiProviderId,
    display_name: str,
    *,
    processing_location: AiProviderProcessingLocation = "external",
) -> AiProviderConnectionResponse:
    return AiProviderConnectionResponse(
        provider=provider,
        display_name=display_name,
        adapter_available=False,
        configured=False,
        state="not_implemented",
        processing_location=processing_location,
        endpoint_scope="unconfigured",
        credential_configured=False,
        requires_deidentified_approval=processing_location == "external",
        checks=[
            _check(name, "not_run", "Provider Adapter가 아직 구현되지 않았습니다.")
            for name in CHECK_NAMES
        ],
        notice="지원 예정 공급자이며 현재 제품 기능에는 활성화되지 않습니다.",
    )


def collect_ai_provider_status(
    *,
    json_get: JsonGetter = _urllib_json_get,
) -> AiProviderStatusResponse:
    """Return capability candidates without accepting, returning, or testing secret values."""

    return AiProviderStatusResponse(
        providers=[
            _rules_status(),
            _ollama_status(json_get),
            _nvidia_status(),
            _not_implemented_status("openai", "OpenAI API"),
            _not_implemented_status("gemini", "Google Gemini API"),
            _not_implemented_status("anthropic", "Anthropic API"),
            _not_implemented_status(
                "openai_compatible",
                "OpenAI 호환 Endpoint",
                processing_location="unconfigured",
            ),
        ]
    )
