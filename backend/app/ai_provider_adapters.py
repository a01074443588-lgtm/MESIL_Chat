from __future__ import annotations

import json
import os
import socket
import tempfile
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from threading import RLock
from time import perf_counter
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, Request, build_opener, urlopen

from pydantic import ValidationError

from .ai_assist_schemas import AiAssistResult, AiAssistSourceSnapshot, AiTaskType
from .ai_secret_store import (
    get_provider_secret,
    provider_secret_configured,
    provider_secret_env_name,
    provider_secret_persistence_revision,
)
from .ai_system_schemas import (
    AiCapability,
    AiFailureCode,
    AiProcessingLocation,
    AiProviderPublicStatus,
    AiProviderSettingsPatch,
    AiProviderTestResponse,
    AiSystemProviderId,
)
from .finals_readiness import endpoint_scope
from .config import settings


@dataclass(frozen=True)
class ProviderDefinition:
    provider: AiSystemProviderId
    display_name: str
    capabilities: tuple[AiCapability, ...]
    requires_credential: bool
    external: bool


@dataclass(frozen=True)
class HttpJsonResponse:
    status_code: int
    payload: dict[str, Any]


@dataclass(frozen=True)
class ProviderProbeRecord:
    connection_ok: bool
    models: tuple[str, ...]
    latency_ms: int
    error_code: AiFailureCode | None
    error_message: str | None
    structured_contract_verified: bool
    configuration_fingerprint: str


class ProviderAdapterError(RuntimeError):
    def __init__(self, code: AiFailureCode, message: str) -> None:
        super().__init__(message)
        self.code = code


HttpTransport = Callable[
    [str, str, dict[str, str], dict[str, Any] | None, float],
    HttpJsonResponse,
]


PROVIDER_DEFINITIONS: dict[AiSystemProviderId, ProviderDefinition] = {
    "rules": ProviderDefinition(
        provider="rules",
        display_name="규칙 기반 기본 기능",
        capabilities=("text", "structured_output"),
        requires_credential=False,
        external=False,
    ),
    "ollama": ProviderDefinition(
        provider="ollama",
        display_name="Ollama 로컬·내부 서버",
        capabilities=("text", "image", "structured_output"),
        requires_credential=False,
        external=False,
    ),
    "ollama_cloud": ProviderDefinition(
        provider="ollama_cloud",
        display_name="Ollama Cloud",
        capabilities=("text", "image", "structured_output"),
        requires_credential=True,
        external=True,
    ),
    "nvidia": ProviderDefinition(
        provider="nvidia",
        display_name="NVIDIA Hosted API",
        capabilities=("text", "image", "audio", "structured_output"),
        requires_credential=True,
        external=True,
    ),
    "openai": ProviderDefinition(
        provider="openai",
        display_name="OpenAI API",
        capabilities=("text", "image", "audio", "structured_output"),
        requires_credential=True,
        external=True,
    ),
    "gemini": ProviderDefinition(
        provider="gemini",
        display_name="Google Gemini API",
        capabilities=("text", "image", "audio", "video", "structured_output"),
        requires_credential=True,
        external=True,
    ),
    "anthropic": ProviderDefinition(
        provider="anthropic",
        display_name="Anthropic API",
        capabilities=("text", "image", "structured_output"),
        requires_credential=True,
        external=True,
    ),
    "openai_compatible": ProviderDefinition(
        provider="openai_compatible",
        display_name="OpenAI 호환 Endpoint",
        capabilities=("text", "image", "structured_output"),
        requires_credential=False,
        external=False,
    ),
    "local_stt": ProviderDefinition(
        provider="local_stt",
        display_name="로컬 STT",
        capabilities=("audio", "speech_to_text"),
        requires_credential=False,
        external=False,
    ),
}


_PROBE_LOCK = RLock()
_PROBE_RECORDS: dict[AiSystemProviderId, ProviderProbeRecord] = {}
_PROBE_FILE_VERSION = 1
_PROBE_CONTRACT_VERSION = "ai_assist_result_v1"


def _provider_probe_path() -> Path:
    return Path(settings.ai_provider_probe_file).expanduser().resolve()


def _provider_configuration_binding(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
) -> tuple[str, bool]:
    credential_revision = provider_secret_persistence_revision(provider)
    payload = {
        "provider": provider,
        "base_url": (provider_settings.base_url or "").strip().rstrip("/"),
        "model": (provider_settings.model or "").strip(),
        "contract": _PROBE_CONTRACT_VERSION,
        "credential_revision": credential_revision or "restart-required",
    }
    fingerprint = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return fingerprint, credential_revision is not None


def _read_persisted_probes() -> dict[str, dict[str, Any]]:
    path = _provider_probe_path()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(payload, dict) or payload.get("version") != _PROBE_FILE_VERSION:
        return {}
    providers = payload.get("providers")
    return providers if isinstance(providers, dict) else {}


def _write_persisted_probe(
    provider: AiSystemProviderId,
    record: ProviderProbeRecord | None,
) -> None:
    path = _provider_probe_path()
    providers = _read_persisted_probes()
    if record is None:
        providers.pop(provider, None)
    else:
        providers[provider] = {
            "configuration_fingerprint": record.configuration_fingerprint,
            "models": list(record.models)[:100],
            "latency_ms": record.latency_ms,
            "structured_contract_verified": True,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(
        prefix="ai-provider-probes-",
        suffix=".tmp",
        dir=path.parent,
        text=True,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(
                {"version": _PROBE_FILE_VERSION, "providers": providers},
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


def _persisted_probe(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    persisted: dict[str, dict[str, Any]],
) -> ProviderProbeRecord | None:
    fingerprint, persistable = _provider_configuration_binding(
        provider,
        provider_settings,
    )
    if not persistable:
        return None
    raw = persisted.get(provider)
    if not isinstance(raw, dict) or raw.get("configuration_fingerprint") != fingerprint:
        return None
    models = raw.get("models")
    if not isinstance(models, list) or not all(isinstance(item, str) for item in models):
        return None
    if raw.get("structured_contract_verified") is not True:
        return None
    latency_ms = raw.get("latency_ms")
    if not isinstance(latency_ms, int) or latency_ms < 0:
        return None
    return ProviderProbeRecord(
        connection_ok=True,
        models=tuple(models[:100]),
        latency_ms=latency_ms,
        error_code=None,
        error_message=None,
        structured_contract_verified=True,
        configuration_fingerprint=fingerprint,
    )


def _urllib_transport(
    method: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any] | None,
    timeout_seconds: float,
) -> HttpJsonResponse:
    body = (
        json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if payload is not None
        else None
    )
    request = Request(
        url=url,
        data=body,
        headers={"Accept": "application/json", **headers},
        method=method,
    )
    try:
        scope = endpoint_scope(url)
        if scope in {"local", "internal"}:
            response_context = build_opener(ProxyHandler({})).open(
                request,
                timeout=timeout_seconds,
            )
        else:
            response_context = urlopen(request, timeout=timeout_seconds)
        with response_context as response:
            raw_body = response.read().decode("utf-8")
            status_code = int(getattr(response, "status", 200))
    except HTTPError as error:
        error_payload: dict[str, Any] | None = None
        try:
            raw_error = error.read().decode("utf-8", errors="replace")
            parsed_error = json.loads(raw_error) if raw_error.strip() else {}
            if isinstance(parsed_error, dict):
                error_payload = parsed_error
        except (AttributeError, json.JSONDecodeError, OSError, UnicodeError):
            error_payload = None
        raise ProviderAdapterError(
            _http_failure_code(error.code, error_payload),
            _http_failure_message(error.code, error_payload),
        ) from error
    try:
        parsed = json.loads(raw_body) if raw_body.strip() else {}
    except json.JSONDecodeError as error:
        raise ProviderAdapterError(
            "invalid_response",
            "공급자 응답을 안전한 JSON 객체로 읽을 수 없습니다.",
        ) from error
    if not isinstance(parsed, dict):
        raise ProviderAdapterError(
            "invalid_response",
            "공급자 응답이 JSON 객체가 아닙니다.",
        )
    return HttpJsonResponse(status_code=status_code, payload=parsed)


def _provider_error_marker(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    try:
        return json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    except (TypeError, ValueError):
        return ""


def _http_failure_code(
    status_code: int,
    payload: dict[str, Any] | None = None,
) -> AiFailureCode:
    marker = _provider_error_marker(payload)
    if status_code in {401, 403}:
        return "authentication_failed"
    if status_code == 402:
        return "billing_required"
    if status_code == 429:
        if any(
            token in marker
            for token in (
                "free tier",
                "free_tier",
                "free-tier",
                "generate_content_free_tier",
            )
        ):
            return "free_tier_exhausted"
        if any(
            token in marker
            for token in (
                "billing",
                "payment required",
                "insufficient_quota",
                "insufficient balance",
                "credit balance",
            )
        ):
            return "billing_required"
        if any(token in marker for token in ("quota exceeded", "quota_exceeded")):
            return "quota_exceeded"
        return "rate_limited"
    if status_code == 409:
        return "quota_exceeded"
    if status_code in {408, 504}:
        return "timeout"
    if status_code in {400, 422}:
        return "contract_validation_failed"
    return "provider_unavailable"


def _http_failure_message(
    status_code: int,
    payload: dict[str, Any] | None = None,
) -> str:
    code = _http_failure_code(status_code, payload)
    messages: dict[AiFailureCode, str] = {
        "authentication_failed": "인증에 실패했습니다. 보호된 키 설정을 확인해 주세요.",
        "billing_required": "키는 확인됐지만 현재 사용하려면 결제 설정 또는 잔액이 필요합니다.",
        "free_tier_exhausted": "키는 확인됐지만 현재 사용하려면 결제 또는 무료 한도가 필요합니다.",
        "rate_limited": "공급자가 요청을 일시적으로 제한했습니다. 잠시 후 다시 확인해 주세요.",
        "quota_exceeded": "키는 확인됐지만 현재 사용 한도가 소진되었습니다.",
        "timeout": "공급자 응답 시간이 초과되었습니다.",
        "contract_validation_failed": "공급자가 필요한 구조화 요청을 처리하지 못했습니다.",
        "provider_unavailable": "공급자 서비스를 현재 사용할 수 없습니다.",
    }
    return messages[code]


def _request_headers(provider: AiSystemProviderId) -> dict[str, str]:
    secret = get_provider_secret(provider)
    value = secret.get_secret_value() if secret is not None else ""
    if provider in {"nvidia", "openai", "ollama_cloud"}:
        return {"Authorization": f"Bearer {value}"}
    if provider == "gemini":
        return {"x-goog-api-key": value}
    if provider == "anthropic":
        return {"x-api-key": value, "anthropic-version": "2023-06-01"}
    if provider == "openai_compatible" and value:
        return {"Authorization": f"Bearer {value}"}
    return {}


def _processing_location(
    provider: AiSystemProviderId,
    base_url: str | None,
) -> AiProcessingLocation:
    if provider == "rules":
        return "rules"
    definition = PROVIDER_DEFINITIONS[provider]
    if definition.external:
        return "external"
    scope = endpoint_scope(base_url or "")
    if scope == "local":
        return "local"
    if scope == "internal":
        return "internal"
    if scope == "external":
        return "external"
    return "unconfigured"


def _endpoint_scope(
    provider: AiSystemProviderId,
    base_url: str | None,
) -> str:
    if provider == "rules":
        return "rules"
    return endpoint_scope(base_url or "") if base_url else "unconfigured"


def _list_url(provider: AiSystemProviderId, base_url: str) -> str:
    base = base_url.rstrip("/")
    if provider in {"ollama", "ollama_cloud"}:
        return f"{base}/api/tags"
    if provider == "local_stt":
        return f"{base}/health"
    return f"{base}/models"


def _model_names(provider: AiSystemProviderId, payload: dict[str, Any]) -> list[str]:
    if provider == "local_stt":
        model = payload.get("model")
        return [str(model).strip()] if isinstance(model, str) and model.strip() else []
    raw_models = (
        payload.get("models")
        if provider in {"ollama", "ollama_cloud", "gemini"}
        else payload.get("data")
    )
    if not isinstance(raw_models, list):
        return []
    names: set[str] = set()
    for item in raw_models:
        if isinstance(item, str) and item.strip():
            names.add(item.strip())
            continue
        if not isinstance(item, dict):
            continue
        value = item.get("name") or item.get("model") or item.get("id")
        if isinstance(value, str) and value.strip():
            normalized = value.strip()
            if provider == "gemini" and normalized.startswith("models/"):
                normalized = normalized.removeprefix("models/")
            names.add(normalized)
    return sorted(names)[:100]


def _request_json(
    provider: AiSystemProviderId,
    method: str,
    url: str,
    payload: dict[str, Any] | None,
    timeout_seconds: float,
    transport: HttpTransport,
) -> dict[str, Any]:
    headers = _request_headers(provider)
    if payload is not None:
        headers = {"Content-Type": "application/json", **headers}
    try:
        response = transport(method, url, headers, payload, timeout_seconds)
    except ProviderAdapterError:
        raise
    except (TimeoutError, socket.timeout) as error:
        raise ProviderAdapterError(
            "timeout",
            "공급자 응답 시간이 초과되었습니다.",
        ) from error
    except (URLError, OSError) as error:
        raise ProviderAdapterError(
            "provider_unavailable",
            "공급자 서비스에 연결할 수 없습니다.",
        ) from error
    except (ValueError, TypeError) as error:
        raise ProviderAdapterError(
            "invalid_response",
            "공급자 응답 형식이 올바르지 않습니다.",
        ) from error
    if response.status_code >= 400:
        raise ProviderAdapterError(
            _http_failure_code(response.status_code, response.payload),
            _http_failure_message(response.status_code, response.payload),
        )
    return response.payload


def list_provider_models(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    *,
    transport: HttpTransport = _urllib_transport,
) -> list[str]:
    if provider == "rules":
        return [provider_settings.model or "quick-period-summary-v1"]
    base_url = provider_settings.base_url
    if not base_url:
        raise ProviderAdapterError("provider_unavailable", "Endpoint가 설정되지 않았습니다.")
    definition = PROVIDER_DEFINITIONS[provider]
    if definition.requires_credential and not provider_secret_configured(provider):
        raise ProviderAdapterError("key_unconfigured", "보호된 API 키가 설정되지 않았습니다.")
    payload = _request_json(
        provider,
        "GET",
        _list_url(provider, base_url),
        None,
        float(provider_settings.timeout_seconds or 30),
        transport,
    )
    return _model_names(provider, payload)


def _fixture_prompt() -> str:
    fixture_path = (
        Path(__file__).resolve().parent
        / "fixtures"
        / "care_briefing_contract_probe_v1.json"
    )
    try:
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        case_id = fixture["case_id"]
        case_input = fixture["input"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ProviderAdapterError(
            "contract_validation_failed",
            "비식별 계약 시험자료를 읽을 수 없습니다.",
        ) from error
    return (
        "아래 비식별 합성 시험자료를 읽고 애플리케이션 AiAssistResult JSON 객체 "
        "하나만 반환하세요. answer는 근거에 맞는 짧은 한국어 답변, visible_text는 null, "
        "image_texts는 빈 배열, evidence는 source_no 1만 사용하고, uncertainties와 "
        "recommended_actions는 문자열 배열이어야 합니다. 실제 개인정보가 아닙니다.\n"
        + json.dumps(
            {"case_id": case_id, "source_no": 1, "input": case_input},
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _contract_schema() -> dict[str, Any]:
    return AiAssistResult.model_json_schema()


def _gemini_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    unsupported_keywords = {
        "const",
        "default",
        "examples",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "maxLength",
        "maxProperties",
        "minLength",
        "minProperties",
        "multipleOf",
        "pattern",
        "uniqueItems",
    }

    def clean(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: clean(item)
                for key, item in value.items()
                if key not in unsupported_keywords
            }
        if isinstance(value, list):
            return [clean(item) for item in value]
        return value

    return clean(schema)


def _gemini_generation_config(model: str, schema: dict[str, Any]) -> dict[str, Any]:
    if model.lower().startswith("gemini-3"):
        return {
            "maxOutputTokens": 4096,
            "thinkingConfig": {"thinkingLevel": "low"},
            "responseMimeType": "application/json",
            "responseJsonSchema": _gemini_json_schema(schema),
        }
    return {
        "temperature": 0,
        "responseMimeType": "application/json",
        "responseJsonSchema": schema,
    }


def _contract_request(
    provider: AiSystemProviderId,
    base_url: str,
    model: str,
) -> tuple[str, dict[str, Any]]:
    prompt = _fixture_prompt()
    schema = _contract_schema()
    base = base_url.rstrip("/")
    if provider in {"ollama", "ollama_cloud"}:
        return f"{base}/api/chat", {
            "model": model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [{"role": "user", "content": prompt}],
            "options": {"temperature": 0},
        }
    if provider == "gemini":
        return f"{base}/models/{model}:generateContent", {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": _gemini_generation_config(model, schema),
        }
    if provider == "anthropic":
        return f"{base}/messages", {
            "model": model,
            "max_tokens": 512,
            "temperature": 0,
            "system": "설명문 없이 요청한 JSON 객체 하나만 반환하세요.",
            "messages": [{"role": "user", "content": prompt}],
        }
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 512,
        "stream": False,
    }
    if provider == "nvidia":
        # Ultra may spend the entire small output budget on reasoning and end
        # with finish_reason=length before emitting the guided JSON object.
        # Keep the probe aligned with the real NVIDIA generation path so it
        # measures the application contract instead of reasoning exhaustion.
        payload["max_tokens"] = 4096
        payload["guided_json"] = schema
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "ai_assist_result",
                "strict": True,
                "schema": schema,
            },
        }
    return f"{base}/chat/completions", payload


def _contract_content(provider: AiSystemProviderId, payload: dict[str, Any]) -> str:
    if provider in {"ollama", "ollama_cloud"}:
        message = payload.get("message")
        value = message.get("content") if isinstance(message, dict) else None
    elif provider == "gemini":
        value = None
        output_text = payload.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            value = output_text
        steps = payload.get("steps")
        if value is None and isinstance(steps, list):
            for step in reversed(steps):
                if not isinstance(step, dict) or step.get("type") != "model_output":
                    continue
                content = step.get("content")
                if not isinstance(content, list):
                    continue
                texts = [
                    item.get("text", "")
                    for item in content
                    if isinstance(item, dict)
                    and item.get("type") == "text"
                    and isinstance(item.get("text"), str)
                ]
                joined = "".join(texts).strip()
                if joined:
                    value = joined
                    break
        candidates = payload.get("candidates")
        if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict):
            content = candidates[0].get("content")
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list) and parts and isinstance(parts[0], dict):
                value = parts[0].get("text")
    elif provider == "anthropic":
        content = payload.get("content")
        value = None
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    value = item["text"]
                    break
    else:
        choices = payload.get("choices")
        value = None
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            message = choices[0].get("message")
            value = message.get("content") if isinstance(message, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ProviderAdapterError(
            "invalid_response",
            "공급자가 구조화 시험 결과를 반환하지 않았습니다.",
        )
    return value.strip()


def run_structured_contract_test(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    model: str,
    *,
    transport: HttpTransport = _urllib_transport,
) -> None:
    if provider in {"rules", "local_stt"}:
        if provider == "rules":
            return
        raise ProviderAdapterError(
            "unsupported_input",
            "STT 공급자는 텍스트 구조화 계약 시험 대상이 아닙니다.",
        )
    if not provider_settings.base_url:
        raise ProviderAdapterError("provider_unavailable", "Endpoint가 설정되지 않았습니다.")
    url, request_payload = _contract_request(
        provider,
        provider_settings.base_url,
        model,
    )
    response = _request_json(
        provider,
        "POST",
        url,
        request_payload,
        float(provider_settings.timeout_seconds or 60),
        transport,
    )
    try:
        parsed = json.loads(_contract_content(provider, response))
        result = AiAssistResult.model_validate(parsed)
        if any(item.source_no != 1 for item in result.evidence):
            raise ValueError("입력에 없는 근거번호")
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise ProviderAdapterError(
            "contract_validation_failed",
            "구조화 응답 계약 검증에 실패했습니다.",
        ) from error


def _ai_assist_prompt(
    *,
    task_type: AiTaskType,
    question: str,
    sources: list[AiAssistSourceSnapshot],
) -> str:
    source_payload = [
        {
            "source_no": source.source_no,
            "source_type": source.source_type,
            "label": source.label,
            "text": source.text,
            "metadata": source.metadata,
        }
        for source in sources
    ]
    return (
        "AI 업무 도움 결과를 AiAssistResult JSON 객체 하나로 반환하세요. "
        "answer에는 실제 입력에서 확인한 판독·설명 내용을 쓰고, "
        "AiAssistResult 같은 스키마·클래스 이름만 답으로 쓰지 마세요. "
        "입력에 없는 사실·이름·수치·진단·완료 여부를 추측하지 마세요. "
        "evidence.source_no는 아래 sources에 있는 번호만 사용하세요. "
        "확실하지 않은 내용은 uncertainties에 쓰고 사람이 원문을 확인할 행동은 "
        "recommended_actions에 쓰세요. 이미지 글자 읽기 작업이면 각 첨부 이미지의 "
        "image_no·filename·보이는 text를 image_texts에 빠짐없이 넣으세요. "
        "설명문이나 마크다운 코드블록을 추가하지 마세요.\n"
        + json.dumps(
            {
                "task_type": task_type,
                "question": question,
                "sources": source_payload,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    )


def _ai_assist_request(
    provider: AiSystemProviderId,
    base_url: str,
    model: str,
    prompt: str,
    images: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    schema = AiAssistResult.model_json_schema()
    base = base_url.rstrip("/")
    encoded_images = [
        {
            "mime_type": image["mime_type"],
            "base64": b64encode(image["content"]).decode("ascii"),
        }
        for image in images
    ]
    if provider in {"ollama", "ollama_cloud"}:
        user_message: dict[str, Any] = {"role": "user", "content": prompt}
        if encoded_images:
            user_message["images"] = [item["base64"] for item in encoded_images]
        return f"{base}/api/chat", {
            "model": model,
            "stream": False,
            "think": False,
            "format": schema,
            "messages": [user_message],
            "options": {"temperature": 0},
        }
    if provider == "gemini":
        parts: list[dict[str, Any]] = [{"text": prompt}]
        parts.extend(
            {
                "inline_data": {
                    "mime_type": item["mime_type"],
                    "data": item["base64"],
                }
            }
            for item in encoded_images
        )
        return f"{base}/models/{model}:generateContent", {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": _gemini_generation_config(model, schema),
        }
    if provider == "anthropic":
        content: list[dict[str, Any]] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": item["mime_type"],
                    "data": item["base64"],
                },
            }
            for item in encoded_images
        ]
        content.append({"type": "text", "text": prompt})
        return f"{base}/messages", {
            "model": model,
            "max_tokens": 4096,
            "temperature": 0,
            "system": "요청한 JSON 객체 하나만 반환하세요.",
            "messages": [{"role": "user", "content": content}],
        }
    user_content: str | list[dict[str, Any]] = prompt
    if encoded_images:
        user_content = [{"type": "text", "text": prompt}]
        user_content.extend(
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{item['mime_type']};base64,{item['base64']}"
                },
            }
            for item in encoded_images
        )
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": user_content}],
        "temperature": 0,
        "max_tokens": 4096,
        "stream": False,
    }
    if provider == "nvidia":
        payload["guided_json"] = schema
        payload["chat_template_kwargs"] = {"enable_thinking": False}
    else:
        payload["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "ai_assist_result",
                "strict": True,
                "schema": schema,
            },
        }
    return f"{base}/chat/completions", payload


def generate_ai_assist_result(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    *,
    model: str,
    task_type: AiTaskType,
    question: str,
    sources: list[AiAssistSourceSnapshot],
    images: list[dict[str, Any]],
    transport: HttpTransport = _urllib_transport,
) -> tuple[AiAssistResult, int]:
    if provider in {"rules", "local_stt"}:
        raise ProviderAdapterError(
            "unsupported_input",
            "이 공급자는 생성형 AI 결과를 만들지 않습니다.",
        )
    if not provider_settings.base_url or not model.strip():
        raise ProviderAdapterError(
            "provider_unavailable",
            "Endpoint 또는 모델이 설정되지 않았습니다.",
        )
    definition = PROVIDER_DEFINITIONS[provider]
    if definition.requires_credential and not provider_secret_configured(provider):
        raise ProviderAdapterError(
            "key_unconfigured",
            "보호된 API 키가 설정되지 않았습니다.",
        )
    started = perf_counter()
    url, payload = _ai_assist_request(
        provider,
        provider_settings.base_url,
        model,
        _ai_assist_prompt(
            task_type=task_type,
            question=question,
            sources=sources,
        ),
        images,
    )
    response = _request_json(
        provider,
        "POST",
        url,
        payload,
        float(provider_settings.timeout_seconds or 60),
        transport,
    )
    try:
        raw_result = json.loads(_contract_content(provider, response))
        result = AiAssistResult.model_validate(raw_result)
    except (json.JSONDecodeError, ValidationError, TypeError, ValueError) as error:
        raise ProviderAdapterError(
            "contract_validation_failed",
            "AI 결과가 애플리케이션 구조화 계약과 다릅니다.",
        ) from error
    valid_source_numbers = {source.source_no for source in sources}
    if any(item.source_no not in valid_source_numbers for item in result.evidence):
        raise ProviderAdapterError(
            "contract_validation_failed",
            "AI 결과가 입력에 없는 근거번호를 사용했습니다.",
        )
    if task_type == "image_text" and len(images) > 1:
        expected_numbers = set(range(1, len(images) + 1))
        actual_numbers = {item.image_no for item in result.image_texts}
        if actual_numbers != expected_numbers:
            raise ProviderAdapterError(
                "contract_validation_failed",
                "일부 이미지의 구조화 판독 결과가 누락되었습니다.",
            )
    return result, max(0, round((perf_counter() - started) * 1000))


def _store_probe(
    provider: AiSystemProviderId,
    record: ProviderProbeRecord,
    *,
    persist: bool,
) -> None:
    with _PROBE_LOCK:
        _PROBE_RECORDS[provider] = record
        _write_persisted_probe(provider, record if persist else None)


def clear_provider_probe_cache(*, delete_persisted: bool = False) -> None:
    with _PROBE_LOCK:
        _PROBE_RECORDS.clear()
        if delete_persisted:
            try:
                _provider_probe_path().unlink()
            except FileNotFoundError:
                pass


def invalidate_provider_probe(provider: AiSystemProviderId) -> None:
    with _PROBE_LOCK:
        _PROBE_RECORDS.pop(provider, None)
        _write_persisted_probe(provider, None)


def select_recommended_provider_model(
    provider: AiSystemProviderId,
    models: list[str],
    configured_model: str | None,
) -> str | None:
    """Choose an actually listed model while preferring the existing safe default."""
    normalized = [item.strip() for item in models if item.strip()]
    if configured_model and configured_model.strip() in normalized:
        return configured_model.strip()
    preferences: dict[AiSystemProviderId, tuple[str, ...]] = {
        "openai": ("gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4o-mini"),
        "gemini": (
            "gemini-3.7-flash",
            "gemini-3.6-flash",
            "gemini-3.5-flash",
            "gemini-2.5-flash",
            "gemini-2.0-flash",
        ),
        "nvidia": ("nemotron-3-ultra", "nemotron"),
        "anthropic": ("claude-sonnet-4", "claude-3-5-sonnet"),
        "ollama_cloud": ("qwen3", "gpt-oss"),
        "ollama": ("qwen3",),
        "openai_compatible": ("qwen", "llama"),
        "local_stt": ("faster-whisper", "whisper"),
        "rules": ("quick-period-summary",),
    }
    lowered = [(item, item.lower()) for item in normalized]
    for token in preferences.get(provider, ()):
        for item, candidate in lowered:
            if token in candidate:
                return item
    return normalized[0] if normalized else None


def test_provider_connection(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    *,
    run_contract_test: bool,
    transport: HttpTransport = _urllib_transport,
) -> AiProviderTestResponse:
    started = perf_counter()
    definition = PROVIDER_DEFINITIONS[provider]
    processing_location = _processing_location(provider, provider_settings.base_url)
    configuration_fingerprint, persistable = _provider_configuration_binding(
        provider,
        provider_settings,
    )
    if provider == "rules":
        return AiProviderTestResponse(
            provider="rules",
            connection_ok=True,
            authentication_ok=None,
            selected_model=provider_settings.model or "quick-period-summary-v1",
            models=[provider_settings.model or "quick-period-summary-v1"],
            capabilities=list(definition.capabilities),
            structured_contract_ok=True,
            latency_ms=0,
            message="규칙 기반 기본 기능은 별도 연결 없이 사용할 수 있습니다.",
            external_call_performed=False,
        )
    try:
        models = list_provider_models(provider, provider_settings, transport=transport)
        model = select_recommended_provider_model(
            provider,
            models,
            provider_settings.model,
        ) or ""
        if run_contract_test:
            if not model:
                raise ProviderAdapterError(
                    "provider_unavailable",
                    "구조화 계약을 시험할 모델이 없습니다.",
                )
            run_structured_contract_test(
                provider,
                provider_settings,
                model,
                transport=transport,
            )
        effective_settings = provider_settings.model_copy(
            update={"model": model or provider_settings.model}
        )
        configuration_fingerprint, persistable = _provider_configuration_binding(
            provider,
            effective_settings,
        )
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        record = ProviderProbeRecord(
            connection_ok=True,
            models=tuple(models),
            latency_ms=elapsed_ms,
            error_code=None,
            error_message=None,
            structured_contract_verified=run_contract_test,
            configuration_fingerprint=configuration_fingerprint,
        )
        _store_probe(
            provider,
            record,
            persist=run_contract_test and persistable,
        )
        return AiProviderTestResponse(
            provider=provider,
            connection_ok=True,
            authentication_ok=True if definition.requires_credential else None,
            selected_model=model or None,
            models=models,
            capabilities=list(definition.capabilities),
            structured_contract_ok=True if run_contract_test else None,
            latency_ms=elapsed_ms,
            message=(
                "연결·모델 목록·비식별 구조화 계약 점검을 통과했습니다."
                if run_contract_test
                else "연결과 모델 목록 점검을 통과했습니다."
            ),
            deidentified_fixture_used=run_contract_test,
            external_call_performed=processing_location == "external",
        )
    except ProviderAdapterError as error:
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        record = ProviderProbeRecord(
            connection_ok=False,
            models=(),
            latency_ms=elapsed_ms,
            error_code=error.code,
            error_message=str(error),
            structured_contract_verified=False,
            configuration_fingerprint=configuration_fingerprint,
        )
        _store_probe(provider, record, persist=False)
        return AiProviderTestResponse(
            provider=provider,
            connection_ok=False,
            authentication_ok=(
                False
                if error.code in {"key_unconfigured", "authentication_failed"}
                else None
            ),
            selected_model=None,
            capabilities=list(definition.capabilities),
            structured_contract_ok=False if run_contract_test else None,
            latency_ms=elapsed_ms,
            error_code=error.code,
            message=str(error),
            deidentified_fixture_used=run_contract_test,
            external_call_performed=(
                processing_location == "external" and error.code != "key_unconfigured"
            ),
        )


def _local_probe(
    provider: AiSystemProviderId,
    provider_settings: AiProviderSettingsPatch,
    transport: HttpTransport,
) -> ProviderProbeRecord | None:
    location = _processing_location(provider, provider_settings.base_url)
    if provider not in {"ollama", "local_stt", "openai_compatible"}:
        return None
    if location not in {"local", "internal"} or not provider_settings.enabled:
        return None
    started = perf_counter()
    configuration_fingerprint, _ = _provider_configuration_binding(
        provider,
        provider_settings,
    )
    try:
        short_probe_settings = provider_settings.model_copy(
            update={"timeout_seconds": min(provider_settings.timeout_seconds or 2, 2)}
        )
        models = list_provider_models(provider, short_probe_settings, transport=transport)
        return ProviderProbeRecord(
            connection_ok=True,
            models=tuple(models),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            error_code=None,
            error_message=None,
            structured_contract_verified=False,
            configuration_fingerprint=configuration_fingerprint,
        )
    except ProviderAdapterError as error:
        code: AiFailureCode = (
            "local_service_unavailable"
            if error.code in {"provider_unavailable", "timeout"}
            else error.code
        )
        return ProviderProbeRecord(
            connection_ok=False,
            models=(),
            latency_ms=max(0, round((perf_counter() - started) * 1000)),
            error_code=code,
            error_message="로컬 또는 내부 AI 서비스에 연결할 수 없습니다.",
            structured_contract_verified=False,
            configuration_fingerprint=configuration_fingerprint,
        )


def collect_provider_public_statuses(
    provider_settings: dict[AiSystemProviderId, AiProviderSettingsPatch],
    *,
    transport: HttpTransport = _urllib_transport,
) -> list[AiProviderPublicStatus]:
    statuses: list[AiProviderPublicStatus] = []
    persisted = _read_persisted_probes()
    for provider, definition in PROVIDER_DEFINITIONS.items():
        configured = provider_settings.get(provider) or AiProviderSettingsPatch()
        enabled = True if provider == "rules" else bool(configured.enabled)
        credential_configured = provider_secret_configured(provider)
        base_url = configured.base_url
        scope = _endpoint_scope(provider, base_url)
        location = _processing_location(provider, base_url)
        configuration_fingerprint, _ = _provider_configuration_binding(
            provider,
            configured,
        )
        with _PROBE_LOCK:
            cached_probe = _PROBE_RECORDS.get(provider)
        if (
            cached_probe is None
            or cached_probe.configuration_fingerprint != configuration_fingerprint
        ):
            cached_probe = _persisted_probe(provider, configured, persisted)
            if cached_probe is not None:
                with _PROBE_LOCK:
                    _PROBE_RECORDS[provider] = cached_probe
        probe = _local_probe(provider, configured, transport)
        if probe is None:
            probe = cached_probe
        elif (
            cached_probe is not None
            and cached_probe.structured_contract_verified
            and probe.connection_ok
        ):
            probe = ProviderProbeRecord(
                connection_ok=True,
                models=probe.models or cached_probe.models,
                latency_ms=probe.latency_ms,
                error_code=None,
                error_message=None,
                structured_contract_verified=True,
                configuration_fingerprint=configuration_fingerprint,
            )
        endpoint_configured = provider == "rules" or bool(base_url)
        required_configuration_ready = endpoint_configured and (
            not definition.requires_credential or credential_configured
        )
        if provider == "rules":
            state = "ready"
        elif probe is not None and probe.connection_ok and enabled:
            state = "ready"
        elif probe is not None and not probe.connection_ok:
            state = "unavailable"
        elif enabled and required_configuration_ready:
            state = "configured_unverified"
        else:
            state = "unconfigured"
        models = list(probe.models) if probe is not None else []
        if probe is None and configured.model and configured.model not in models:
            models.insert(0, configured.model)
        error_code = probe.error_code if probe is not None else None
        error_message = probe.error_message if probe is not None else None
        statuses.append(
            AiProviderPublicStatus(
                provider=provider,
                display_name=definition.display_name,
                api_key_env_var=provider_secret_env_name(provider),
                enabled=enabled,
                adapter_available=True,
                credential_configured=credential_configured,
                endpoint_configured=endpoint_configured,
                endpoint_scope=scope,
                processing_location=location,
                connection_state=state,
                configured_model=configured.model,
                models=models[:100],
                capabilities=list(definition.capabilities),
                timeout_seconds=configured.timeout_seconds or 60,
                last_latency_ms=probe.latency_ms if probe is not None else None,
                last_error_code=error_code,
                last_error_message=error_message,
                structured_contract_verified=(
                    True
                    if provider == "rules"
                    else (
                        probe.structured_contract_verified
                        if probe is not None
                        else False
                    )
                ),
            )
        )
    return statuses
