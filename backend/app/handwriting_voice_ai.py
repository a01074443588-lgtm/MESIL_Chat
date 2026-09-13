"""Internal-only contextual refinement for handwriting and voice evidence.

This module never selects an external provider.  It returns ``None`` on any
configuration, transport, or contract failure so the caller can keep the
deterministic evidence comparison available for staff editing.
"""

from __future__ import annotations

import json
from time import perf_counter
from typing import Any, Callable

import httpx

from .ai_settings_store import effective_provider_settings, load_ai_settings, resolved_model_role
from .config import settings
from .finals_readiness import endpoint_scope


InternalPost = Callable[..., Any]


_FINAL_TEXT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "final_text": {"type": "string"},
        "source_rows": {
            "type": "array",
            "items": {"type": "integer", "minimum": 1},
        },
    },
    "required": ["final_text", "source_rows"],
    "additionalProperties": False,
}


def _internal_text_target() -> tuple[str, str, int] | None:
    """Return a protected internal Ollama target without exposing its address."""

    if settings.environment == "test":
        return None
    document, _persisted = load_ai_settings()
    provider = effective_provider_settings(document).get("ollama")
    if provider is None or provider.enabled is False:
        return None
    role = resolved_model_role(document, "text")
    if role is None or role.provider != "ollama":
        role = resolved_model_role(document, "precision")
    if role is None or role.provider != "ollama":
        return None
    base_url = (provider.base_url or settings.ai_review_base_url).strip().rstrip("/")
    model = (role.model or provider.model or settings.ai_review_model).strip()
    if not base_url or not model or endpoint_scope(base_url) not in {"local", "internal"}:
        return None
    timeout_seconds = min(180, max(10, int(provider.timeout_seconds or 90)))
    return base_url, model, timeout_seconds


def _content_document(response: Any) -> dict[str, Any] | None:
    try:
        response.raise_for_status()
        body = response.json()
        content = body["message"]["content"]
        if isinstance(content, dict):
            document = content
        else:
            document = json.loads(str(content))
        final_text = str(document.get("final_text") or "").strip()
        source_rows = document.get("source_rows")
        if not final_text or not isinstance(source_rows, list):
            return None
        return {
            "final_text": final_text,
            "source_rows": [int(item) for item in source_rows if str(item).isdigit()],
        }
    except (httpx.HTTPError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def refine_handwriting_with_internal_ai(
    *,
    initial_ocr: str,
    transcript: str,
    deterministic_suggestion: str,
    alignments: list[dict[str, Any]],
    audio_quality: dict[str, Any],
    mode: str,
    post: InternalPost | None = None,
) -> dict[str, Any] | None:
    """Refine evidence with one internal request and no external fallback."""

    if mode != "full_reading":
        return None
    target = _internal_text_target()
    if target is None:
        return None
    base_url, model, timeout_seconds = target
    prompt = {
        "task": "손글씨 OCR과 직원이 읽은 음성 전사를 함께 보고 자연스러운 최종문 초안을 작성",
        "rules": [
            "OCR과 음성에 근거가 있는 내용만 사용한다.",
            "음성에만 있는 시험 소개, 이름 소개, 지시 문장은 추가하지 않는다.",
            "OCR과 음성이 같은 부분은 유지한다.",
            "한쪽 문장이 명백히 깨졌고 다른 쪽과 앞뒤 문맥이 뒷받침할 때만 자연스러운 쪽을 사용한다.",
            "시간, 날짜, 수량, 단위, 이름, 약명, 진단, 완료상태가 충돌하면 어느 쪽도 선택하지 말고 '[항목 확인 필요]'로 남긴다.",
            "새 시간, 수량, 이름, 약명, 진단, 완료상태, 후속조치를 만들지 않는다.",
            "설명이나 이유를 쓰지 말고 최종문만 작성한다.",
        ],
        "initial_ocr": initial_ocr,
        "audio_transcript": transcript,
        "deterministic_suggestion": deterministic_suggestion,
        "audio_quality": {
            "status": audio_quality.get("status"),
            "reasons": audio_quality.get("reasons", []),
        },
        "aligned_rows": [
            {
                "row_no": row.get("row_no"),
                "ocr": row.get("image_text"),
                "audio": row.get("audio_text"),
                "status": row.get("status"),
                "similarity": row.get("similarity"),
            }
            for row in alignments
        ],
    }
    started = perf_counter()
    request_kwargs = {
        "json": {
            "model": model,
            "stream": False,
            "think": False,
            "format": _FINAL_TEXT_SCHEMA,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "당신은 돌봄 손글씨 교정 보조자입니다. 두 근거를 합치되 "
                        "근거 없는 사실은 절대 만들지 않고 JSON 계약만 반환합니다."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(prompt, ensure_ascii=False),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
                "num_predict": 1200,
            },
            "keep_alive": "10m",
        },
    }
    try:
        if post is not None:
            response = post(
                f"{base_url}/api/chat",
                **request_kwargs,
                timeout=timeout_seconds,
                trust_env=False,
            )
        else:
            with httpx.Client(timeout=timeout_seconds, trust_env=False) as client:
                response = client.post(
                    f"{base_url}/api/chat",
                    **request_kwargs,
                )
    except (httpx.HTTPError, OSError, TimeoutError, ValueError, TypeError):
        return None
    document = _content_document(response)
    if document is None:
        return None
    return {
        **document,
        "provider": "ollama",
        "model": model,
        "processing_location": endpoint_scope(base_url),
        "external_transfer": False,
        "elapsed_ms": round((perf_counter() - started) * 1000),
    }
