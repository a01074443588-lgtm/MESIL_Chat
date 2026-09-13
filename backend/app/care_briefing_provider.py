from __future__ import annotations

import json
import socket
import time
from pathlib import Path
from typing import Any, Callable, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from .care_briefing_contract import (
    CareBriefingFailureCode,
    CareBriefingV1Envelope,
    CareBriefingV1Failure,
    CareBriefingV1Input,
    CareBriefingV1Result,
    validate_care_briefing_result,
)
from .care_briefing_nvidia_adapter import (
    NvidiaCompactResult,
    adapt_nvidia_compact_result,
    nvidia_compact_generation_schema,
)


JsonTransport = Callable[[str, dict[str, Any], float], dict[str, Any]]
AuthorizedJsonTransport = Callable[
    [str, dict[str, Any], float, dict[str, str]], dict[str, Any]
]
ValidationDiagnostics = Callable[[str, list[dict[str, str]]], None]


# 실제 Super 비교에서 2,048 토큰 응답이 finish_reason=length로 잘려
# 유효한 JSON을 닫지 못했습니다. 표준 출력의 필드 수는 줄이지 않고,
# 구조화 응답이 끝날 수 있는 최소 여유를 4,096 토큰으로 둡니다.
NVIDIA_MAX_TOKENS = 4_096
_SAFE_FINISH_REASONS = {"stop", "length", "content_filter", "tool_calls"}


class CareBriefingProvider(Protocol):
    """Model-neutral boundary used by comparison and UI layers."""

    provider_name: str
    model: str

    def generate(self, input_data: CareBriefingV1Input) -> CareBriefingV1Envelope:
        ...


def _urllib_json_transport(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    request = Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw_body = response.read().decode("utf-8")
    parsed = json.loads(raw_body)
    if not isinstance(parsed, dict):
        raise ValueError("AI 제공자 응답 본문이 JSON 객체가 아닙니다.")
    return parsed


def _urllib_authorized_json_transport(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    headers: dict[str, str],
) -> dict[str, Any]:
    request = Request(
        url=url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        raw_body = response.read().decode("utf-8")
    parsed = json.loads(raw_body)
    if not isinstance(parsed, dict):
        raise ValueError("AI 제공자 응답 본문이 JSON 객체가 아닙니다.")
    return parsed


def _load_skill_instructions() -> str:
    skill_path = (
        Path(__file__).resolve().parent
        / "ai_skills"
        / "care-briefing-v1"
        / "SKILL.md"
    )
    return skill_path.read_text(encoding="utf-8")


def _failure_envelope(
    *,
    provider: str,
    model: str,
    started_at: float,
    code: CareBriefingFailureCode,
    message: str,
    retryable: bool,
) -> CareBriefingV1Envelope:
    return CareBriefingV1Envelope(
        ok=False,
        failure=CareBriefingV1Failure(
            code=code,
            message=message,
            retryable=retryable,
            # 7단계에서 실제 오프라인 대체 경로를 검증하기 전까지 true로 광고하지 않습니다.
            offline_fallback_available=False,
        ),
        provider=provider,
        model=model,
        elapsed_ms=max(0, round((time.monotonic() - started_at) * 1000)),
    )


def _validation_failure_code(error: ValidationError) -> CareBriefingFailureCode:
    if any(item.get("type") == "missing" for item in error.errors()):
        return "missing_required_output"
    messages = " ".join(str(item.get("msg", "")) for item in error.errors())
    if any(
        fragment in messages
        for fragment in (
            "사건 근거 목록에 없는 식별자",
            "완료를 직접 뒷받침하는 근거",
        )
    ):
        return "unsupported_evidence"
    return "schema_validation_failed"


def _grounding_failure_code(error: ValueError) -> CareBriefingFailureCode:
    message = str(error)
    if any(
        fragment in message
        for fragment in (
            "입력에 없는 근거",
            "사건 근거와 제외 근거",
            "모든 입력 근거",
            "입력에 없는 대상자",
            "사용 또는 제외되지 않은 원문 근거",
            "명시적인 완료 근거",
        )
    ):
        return "unsupported_evidence"
    return "schema_validation_failed"


def _sanitized_adapter_grounding_diagnostics(
    error: ValueError,
) -> list[dict[str, str]]:
    """Map adapter failures to allowlisted rule codes without model content or IDs."""

    rule_by_message = {
        "사건의 source_ids에 중복이 있습니다.": "duplicate_event_source",
        "입력에 없는 근거 식별자가 포함되어 있습니다.": "unknown_event_source",
        "하나의 원문 근거가 여러 사건에 중복 사용되었습니다.": "source_used_by_multiple_events",
        "최종상태 근거는 사건 근거에 포함되어야 합니다.": "status_source_outside_event",
        "완료 근거는 사건 근거에 포함되어야 합니다.": "completion_source_outside_event",
        "완료 상태에는 명시적인 완료 근거가 필요합니다.": "completed_without_direct_evidence",
        "입력에 없는 대상자 식별자가 포함되어 있습니다.": "unknown_subject",
        "입력에 없는 제외 근거 식별자가 포함되어 있습니다.": "unknown_ignored_source",
        "근거 식별자가 사용·제외 목록에 중복되었습니다.": "source_used_and_ignored",
        "모델 출력에서 사용 또는 제외되지 않은 원문 근거가 있습니다.": "uncovered_source",
    }
    return [
        {
            "location": "nvidia_compact_adapter",
            "type": "adapter_rule",
            "message": rule_by_message.get(str(error), "adapter_rule_failed"),
        }
    ]


def _sanitized_validation_errors(
    error: ValidationError,
) -> list[dict[str, str]]:
    """Return only contract locations and messages, never rejected input values."""

    return [
        {
            "location": ".".join(str(part) for part in item.get("loc", ())),
            "type": str(item.get("type", "validation_error")),
            "message": str(item.get("msg", "계약 검증 오류")),
        }
        for item in error.errors(include_input=False, include_context=False)
    ]


def _sanitized_json_shape_diagnostics(
    raw_content: str,
    error: json.JSONDecodeError,
) -> list[dict[str, str]]:
    """Describe only response framing, never model text or rejected values."""

    stripped = raw_content.strip()
    length = len(stripped)
    if length < 1_000:
        length_bucket = "under_1000"
    elif length < 10_000:
        length_bucket = "1000_to_9999"
    else:
        length_bucket = "10000_or_more"
    if not stripped:
        error_position_bucket = "empty"
    elif error.pos <= max(1, length // 10):
        error_position_bucket = "start"
    elif error.pos >= length - max(1, length // 10):
        error_position_bucket = "end"
    else:
        error_position_bucket = "middle"

    lowered = stripped.lower()
    # 실패 응답 원문은 보존하거나 로그에 남기지 않습니다. 다만 허용된
    # 계약 키가 몇 번 시작됐는지는 장문 문자열 문제와 배열 반복 문제를
    # 구분하는 데 필요하므로 개수 구간만 기록합니다.
    key_count_parts: list[str] = []
    for key in (
        "event_id",
        "what_happened",
        "importance_reasons",
        "evidence",
        "supports",
        "fact_summary",
        "document_draft_candidates",
        "analysis_confidence",
        "validation_status",
        "ignored_sources",
        "warnings",
    ):
        count = stripped.count(f'"{key}"')
        bucket = "0" if count == 0 else "1" if count == 1 else "2_to_5" if count <= 5 else "6_or_more"
        key_count_parts.append(f"{key}_keys={bucket}")
    return [
        {
            "location": "response.content",
            "type": "json_shape",
            "message": (
                f"length={length_bucket};"
                f"error_position={error_position_bucket};"
                f"starts_object={str(stripped.startswith('{')).lower()};"
                f"ends_object={str(stripped.endswith('}')).lower()};"
                f"markdown_fence={str('```' in stripped).lower()};"
                f"thinking_tag={str('<think' in lowered or '</think>' in lowered).lower()};"
                f"object_balance={stripped.count('{') - stripped.count('}')};"
                f"array_balance={stripped.count('[') - stripped.count(']')};"
                + ";".join(key_count_parts)
            ),
        }
    ]


def _sanitized_provider_response_metadata(
    response: dict[str, Any],
    *,
    requested_max_tokens: int,
) -> list[dict[str, str]]:
    """Keep only allowlisted completion metadata, never provider text."""

    finish_reason = "other_or_missing"
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        candidate = choices[0].get("finish_reason")
        if isinstance(candidate, str) and candidate in _SAFE_FINISH_REASONS:
            finish_reason = candidate

    completion_tokens = "unknown"
    usage = response.get("usage")
    if isinstance(usage, dict):
        candidate = usage.get("completion_tokens")
        if isinstance(candidate, int) and candidate >= 0:
            completion_tokens = str(candidate)

    reasoning_content_present = "unknown"
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        message = choices[0].get("message")
        if isinstance(message, dict):
            candidate = message.get("reasoning_content")
            if candidate is None:
                reasoning_content_present = "false"
            elif isinstance(candidate, str):
                reasoning_content_present = str(bool(candidate.strip())).lower()

    reasoning_tokens = "unknown"
    if isinstance(usage, dict):
        details = usage.get("completion_tokens_details")
        if isinstance(details, dict):
            candidate = details.get("reasoning_tokens")
            if isinstance(candidate, int) and candidate >= 0:
                reasoning_tokens = str(candidate)

    return [
        {
            "location": "response",
            "type": "provider_metadata",
            "message": (
                f"finish_reason={finish_reason};"
                f"completion_tokens={completion_tokens};"
                f"requested_max_tokens={requested_max_tokens};"
                f"reasoning_content_present={reasoning_content_present};"
                f"reasoning_tokens={reasoning_tokens}"
            ),
        }
    ]


def _raw_result_has_unknown_evidence(
    raw_result: dict[str, Any],
    input_data: CareBriefingV1Input,
) -> bool:
    """Reject invented source ids before model-validation order can mask them."""

    known_source_ids = {source.source_id for source in input_data.sources}
    referenced_source_ids: set[str] = set()

    events = raw_result.get("events")
    if isinstance(events, list):
        for event in events:
            if not isinstance(event, dict):
                continue
            for evidence in event.get("evidence", []):
                if isinstance(evidence, dict) and isinstance(
                    evidence.get("source_id"), str
                ):
                    referenced_source_ids.add(evidence["source_id"])
            for collection_name in (
                "importance_reasons",
                "verification_items",
                "document_draft_candidates",
            ):
                for item in event.get(collection_name, []):
                    if not isinstance(item, dict):
                        continue
                    source_ids = item.get("evidence_source_ids")
                    if isinstance(source_ids, list):
                        referenced_source_ids.update(
                            source_id
                            for source_id in source_ids
                            if isinstance(source_id, str)
                        )

    ignored_sources = raw_result.get("ignored_sources")
    if isinstance(ignored_sources, list):
        referenced_source_ids.update(
            item["source_id"]
            for item in ignored_sources
            if isinstance(item, dict) and isinstance(item.get("source_id"), str)
        )

    return bool(referenced_source_ids - known_source_ids)


def _validated_content_envelope(
    *,
    provider: str,
    model: str,
    started_at: float,
    raw_content: str,
    input_data: CareBriefingV1Input,
    diagnostics: ValidationDiagnostics | None,
) -> CareBriefingV1Envelope:
    """Apply the same JSON, schema, and evidence checks to every model."""

    try:
        raw_result = json.loads(raw_content)
    except json.JSONDecodeError as error:
        if diagnostics is not None:
            diagnostics(
                "invalid_json_shape",
                _sanitized_json_shape_diagnostics(raw_content, error),
            )
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code="invalid_json",
            message="AI 분석 결과가 유효한 JSON이 아닙니다.",
            retryable=True,
        )
    if not isinstance(raw_result, dict):
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code="invalid_json",
            message="AI 분석 결과가 JSON 객체가 아닙니다.",
            retryable=True,
        )
    if _raw_result_has_unknown_evidence(raw_result, input_data):
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code="unsupported_evidence",
            message="AI 분석 결과가 입력에 없는 근거 식별자를 사용했습니다.",
            retryable=True,
        )
    try:
        result = CareBriefingV1Result.model_validate(raw_result)
    except ValidationError as error:
        if diagnostics is not None:
            diagnostics(
                "schema_validation_failed",
                _sanitized_validation_errors(error),
            )
        code = _validation_failure_code(error)
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code=code,
            message=(
                "AI 분석 결과에 필수 항목이 없습니다."
                if code == "missing_required_output"
                else (
                    "AI 분석 결과가 입력에 없는 근거를 사용했거나 "
                    "근거 없이 완료를 판단했습니다."
                    if code == "unsupported_evidence"
                    else "AI 분석 결과가 care_briefing_v1 형식과 다릅니다."
                )
            ),
            retryable=True,
        )
    try:
        validated_result = validate_care_briefing_result(input_data, result)
    except ValueError as error:
        code = _grounding_failure_code(error)
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code=code,
            message=(
                "AI 분석 결과가 입력에 없는 근거를 사용했거나 근거를 누락했습니다."
                if code == "unsupported_evidence"
                else "AI 분석 결과의 기준시각 또는 참조 구조가 올바르지 않습니다."
            ),
            retryable=True,
        )
    return CareBriefingV1Envelope(
        ok=True,
        result=validated_result,
        provider=provider,
        model=model,
        elapsed_ms=max(0, round((time.monotonic() - started_at) * 1000)),
    )


def _validated_nvidia_compact_envelope(
    *,
    provider: str,
    model: str,
    started_at: float,
    raw_content: str,
    input_data: CareBriefingV1Input,
    diagnostics: ValidationDiagnostics | None,
) -> CareBriefingV1Envelope:
    """Validate NVIDIA's compact output, then build the neutral result locally."""

    try:
        raw_result = json.loads(raw_content)
    except json.JSONDecodeError as error:
        if diagnostics is not None:
            diagnostics(
                "invalid_json_shape",
                _sanitized_json_shape_diagnostics(raw_content, error),
            )
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code="invalid_json",
            message="AI 분석 결과가 유효한 JSON이 아닙니다.",
            retryable=True,
        )
    if not isinstance(raw_result, dict):
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code="invalid_json",
            message="AI 분석 결과가 JSON 객체가 아닙니다.",
            retryable=True,
        )
    try:
        compact = NvidiaCompactResult.model_validate(raw_result)
    except ValidationError as error:
        if diagnostics is not None:
            diagnostics(
                "schema_validation_failed",
                _sanitized_validation_errors(error),
            )
        code = _validation_failure_code(error)
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code=code,
            message=(
                "AI 분석 결과에 필수 항목이 없습니다."
                if code == "missing_required_output"
                else "AI 분석 결과가 NVIDIA 중간 형식과 다릅니다."
            ),
            retryable=True,
        )
    try:
        result = adapt_nvidia_compact_result(compact, input_data)
    except ValueError as error:
        if diagnostics is not None:
            diagnostics(
                "adapter_grounding_failed",
                _sanitized_adapter_grounding_diagnostics(error),
            )
        code = _grounding_failure_code(error)
        return _failure_envelope(
            provider=provider,
            model=model,
            started_at=started_at,
            code=code,
            message=(
                "AI 분석 결과가 입력에 없는 근거를 사용했거나 근거를 누락했습니다."
                if code == "unsupported_evidence"
                else "AI 분석 결과의 참조 구조가 올바르지 않습니다."
            ),
            retryable=True,
        )
    return CareBriefingV1Envelope(
        ok=True,
        result=result,
        provider=provider,
        model=model,
        elapsed_ms=max(0, round((time.monotonic() - started_at) * 1000)),
    )


class OllamaCareBriefingProvider:
    """Ollama adapter that emits only validated care_briefing_v1 envelopes."""

    provider_name = "ollama"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 120,
        transport: JsonTransport = _urllib_json_transport,
        skill_instructions: str | None = None,
        diagnostics: ValidationDiagnostics | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.skill_instructions = skill_instructions or _load_skill_instructions()
        self.diagnostics = diagnostics
        if not self.model:
            raise ValueError("Ollama 모델명이 필요합니다.")

    def _payload(self, input_data: CareBriefingV1Input) -> dict[str, Any]:
        return {
            "model": self.model,
            "stream": False,
            "think": False,
            "format": CareBriefingV1Result.model_json_schema(),
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{self.skill_instructions}\n\n"
                        "반드시 care_briefing_v1 출력 JSON 객체 하나만 반환하세요. "
                        "설명문, 마크다운 코드블록, 원문에 없는 추측을 추가하지 마세요. "
                        "같은 사건의 근거는 하나로 묶고 각 설명은 한 문장으로 짧게 쓰세요. "
                        "배열에는 필요한 항목만 넣고 입력 근거 수보다 사건을 불필요하게 "
                        "늘리지 마세요."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        input_data.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": 16384,
                "num_predict": 8192,
            },
            "keep_alive": "10m",
        }

    def generate(self, input_data: CareBriefingV1Input) -> CareBriefingV1Envelope:
        started_at = time.monotonic()
        try:
            response = self.transport(
                f"{self.base_url}/api/chat",
                self._payload(input_data),
                self.timeout_seconds,
            )
        except (TimeoutError, socket.timeout):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="timeout",
                message="AI 제공자 응답 시간이 초과되었습니다.",
                retryable=True,
            )
        except (HTTPError, URLError, OSError):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="provider_unavailable",
                message="AI 제공자에 연결할 수 없습니다.",
                retryable=True,
            )
        except (json.JSONDecodeError, ValueError):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="invalid_json",
                message="AI 제공자 응답 본문을 JSON 객체로 읽을 수 없습니다.",
                retryable=True,
            )
        except Exception:
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="internal_error",
                message="AI 제공자 처리 중 내부 오류가 발생했습니다.",
                retryable=False,
            )

        raw_content = response.get("message", {}).get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="empty_result",
                message="AI 제공자가 분석 결과를 반환하지 않았습니다.",
                retryable=True,
            )
        return _validated_content_envelope(
            provider=self.provider_name,
            model=self.model,
            started_at=started_at,
            raw_content=raw_content,
            input_data=input_data,
            diagnostics=self.diagnostics,
        )


class NvidiaCareBriefingProvider:
    """NVIDIA API adapter guarded by the external de-identification policy."""

    provider_name = "nvidia"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 120,
        transport: AuthorizedJsonTransport = _urllib_authorized_json_transport,
        skill_instructions: str | None = None,
        diagnostics: ValidationDiagnostics | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model.strip()
        self._api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.skill_instructions = skill_instructions or _load_skill_instructions()
        self.diagnostics = diagnostics
        if not self.model:
            raise ValueError("NVIDIA 모델명이 필요합니다.")
        if not self._api_key or self._api_key.lower() in {
            "changeme",
            "placeholder",
            "your_api_key",
            "paste_nvidia_api_key_here",
        }:
            raise ValueError("NVIDIA API 키가 설정되지 않았습니다.")

    def _payload(self, input_data: CareBriefingV1Input) -> dict[str, Any]:
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        f"{self.skill_instructions}\n\n"
                        "NVIDIA 중간 형식 JSON 객체 하나만 짧게 반환하세요. "
                        "source_ids에는 입력에 있는 근거 식별자만 넣고 모든 입력 근거를 "
                        "사건 또는 ignored_sources에 정확히 한 번 배치하세요. "
                        "일상 활동이 정상 종료되었고 '특이사항 없음'처럼 오늘 확인할 "
                        "문제가 없다고 명시된 기록은 사건으로 만들지 말고 "
                        "ignored_sources의 routine_completed로 분류하세요. "
                        "담당자와 완료 여부는 추측하지 말고, completed는 완료를 직접 "
                        "확인하는 completion_source_id가 있는 중요한 후속 업무에만 "
                        "사용하세요. 일상 활동의 정상 종료를 completed 사건으로 만들지 "
                        "마세요. "
                        "설명문, 마크다운 코드블록, 원문 재인용을 추가하지 마세요. "
                        "중첩된 근거 설명과 최종 care_briefing_v1 조립은 애플리케이션이 합니다."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        input_data.model_dump(mode="json"),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            ],
            "temperature": 0,
            "top_p": 1,
            # NVIDIA는 짧은 중간 판단만 반환하고 최종 표준 결과는 로컬에서
            # 조립합니다. 실제 비교에서 확인된 2,048 토큰 절단을 피합니다.
            "max_tokens": NVIDIA_MAX_TOKENS,
            "chat_template_kwargs": {"enable_thinking": False},
            # NVIDIA NIM 공식 구조화 생성 계약은 OpenAI의
            # response_format=json_schema 래퍼가 아니라 최상위
            # guided_json에 JSON Schema 자체를 전달합니다.
            "guided_json": nvidia_compact_generation_schema(input_data),
            "stream": False,
        }

    def generate(self, input_data: CareBriefingV1Input) -> CareBriefingV1Envelope:
        started_at = time.monotonic()
        policy = input_data.transmission_policy
        if (
            policy.privacy_mode != "external_deidentified"
            or not policy.external_ai_allowed
            or policy.contains_direct_identifiers
        ):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="privacy_blocked",
                message="비식별 외부 AI 전송 정책이 확인되지 않아 요청을 차단했습니다.",
                retryable=False,
            )

        try:
            response = self.transport(
                f"{self.base_url}/chat/completions",
                self._payload(input_data),
                self.timeout_seconds,
                {"Authorization": f"Bearer {self._api_key}"},
            )
        except (TimeoutError, socket.timeout):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="timeout",
                message="AI 제공자 응답 시간이 초과되었습니다.",
                retryable=True,
            )
        except (HTTPError, URLError, OSError):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="provider_unavailable",
                message="AI 제공자에 연결할 수 없습니다.",
                retryable=True,
            )
        except (json.JSONDecodeError, ValueError):
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="invalid_json",
                message="AI 제공자 응답 본문을 JSON 객체로 읽을 수 없습니다.",
                retryable=True,
            )
        except Exception:
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="internal_error",
                message="AI 제공자 처리 중 내부 오류가 발생했습니다.",
                retryable=False,
            )

        choices = response.get("choices")
        raw_content = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            if isinstance(message, dict):
                raw_content = message.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            return _failure_envelope(
                provider=self.provider_name,
                model=self.model,
                started_at=started_at,
                code="empty_result",
                message="AI 제공자가 분석 결과를 반환하지 않았습니다.",
                retryable=True,
            )
        envelope = _validated_nvidia_compact_envelope(
            provider=self.provider_name,
            model=self.model,
            started_at=started_at,
            raw_content=raw_content,
            input_data=input_data,
            diagnostics=self.diagnostics,
        )
        if (
            not envelope.ok
            and envelope.failure is not None
            and envelope.failure.code == "invalid_json"
            and self.diagnostics is not None
        ):
            self.diagnostics(
                "provider_response_metadata",
                _sanitized_provider_response_metadata(
                    response,
                    requested_max_tokens=NVIDIA_MAX_TOKENS,
                ),
            )
        return envelope
