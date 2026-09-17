from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import socket
import sys
import time
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.care_briefing_contract import CareBriefingV1Input
from app.care_briefing_provider import (
    NvidiaCareBriefingProvider,
    _urllib_authorized_json_transport,
    _validated_nvidia_compact_envelope,
)


DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.care-briefing.local"
DEFAULT_FIXTURE = (
    BACKEND_ROOT / "tests" / "fixtures" / "care_briefing_v1_cases.json"
)
DEFAULT_CASE_ID = "CBV1-SAFETY-FOLLOWUP"


# NVIDIA Build의 각 모델 Prototype에 표시된 생성 조건을 HTTP 요청 본문으로
# 옮긴 값입니다. OpenAI SDK의 extra_body는 실제 HTTP에서는 최상위 필드로
# 병합되므로 chat_template_kwargs를 최상위에 둡니다.
MODEL_PROFILES: dict[str, dict[str, Any]] = {
    "openai/gpt-oss-120b": {
        "profile": "nvidia-build-prototype-2026-08-23",
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 4096,
        "stream": False,
    },
    "openai/gpt-oss-20b": {
        "profile": "nvidia-build-prototype-2026-08-23",
        "temperature": 1,
        "top_p": 1,
        "max_tokens": 4096,
        "stream": False,
    },
    "deepseek-ai/deepseek-v4-flash-0731": {
        "profile": "nvidia-build-prototype-2026-08-23",
        "temperature": 1,
        "top_p": 0.95,
        "max_tokens": 16384,
        "chat_template_kwargs": {
            "thinking": True,
            "reasoning_effort": "high",
        },
        "stream": False,
    },
}


def _effective_profile_parameters(
    profile: dict[str, Any],
    *,
    stream: bool,
    disable_thinking: bool,
) -> dict[str, Any]:
    """Build one diagnostic payload override without mutating the profile."""

    parameters = copy.deepcopy(
        {key: value for key, value in profile.items() if key != "profile"}
    )
    if stream:
        parameters["stream"] = True
    if disable_thinking:
        template_kwargs = parameters.get("chat_template_kwargs")
        if not isinstance(template_kwargs, dict):
            template_kwargs = {}
        template_kwargs["thinking"] = False
        # reasoning_effort=high와 thinking=false를 함께 보내면 원인 분리가
        # 되지 않으므로, 이 진단에서는 추론 강제 조건도 함께 제거합니다.
        template_kwargs.pop("reasoning_effort", None)
        parameters["chat_template_kwargs"] = template_kwargs
    return parameters


def _build_diagnostic_payload(
    provider_payload: dict[str, Any],
    profile: dict[str, Any],
    *,
    stream: bool,
    disable_thinking: bool,
    provider_contract: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build either a Prototype or provider-contract diagnostic request."""

    payload = copy.deepcopy(provider_payload)
    effective_parameters = _effective_profile_parameters(
        profile,
        stream=stream,
        disable_thinking=disable_thinking,
    )

    if provider_contract:
        # 실제 Provider의 compact guided_json 계약은 보존합니다. 모델별
        # Prototype이 chat_template_kwargs를 명시한 경우에만 그 값을
        # 교체하고, 그렇지 않으면 Provider의 추론 비활성 기본값을 유지합니다.
        profile_template_kwargs = effective_parameters.pop(
            "chat_template_kwargs", None
        )
        payload.update(effective_parameters)
        if profile_template_kwargs is not None:
            payload["chat_template_kwargs"] = profile_template_kwargs
    else:
        # NVIDIA Build Prototype에 표시된 조건 자체를 재현할 때는 MESIL
        # Provider의 구조화 생성/추론 조건을 제거합니다.
        payload.pop("guided_json", None)
        payload.pop("chat_template_kwargs", None)
        payload.update(effective_parameters)

    return payload, _effective_profile_parameters(
        profile,
        stream=stream,
        disable_thinking=disable_thinking,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "NVIDIA Build Prototype 조건과 MESIL 공통 호출 조건의 차이를 "
            "비식별 대표 사례로 진단합니다."
        )
    )
    parser.add_argument("--model", required=True, choices=sorted(MODEL_PROFILES))
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE)
    parser.add_argument("--case-id", default=DEFAULT_CASE_ID)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument(
        "--stream",
        action="store_true",
        help=(
            "모델별 Prototype 생성 조건은 유지하고 응답 수집만 SSE 스트리밍으로 "
            "비교 진단합니다."
        ),
    )
    parser.add_argument(
        "--disable-thinking",
        action="store_true",
        help=(
            "DeepSeek 진단에서 thinking=false로 바꾸고 reasoning_effort를 "
            "제거해 최종 content 생성 여부를 분리 진단합니다."
        ),
    )
    parser.add_argument(
        "--provider-contract",
        action="store_true",
        help=(
            "NVIDIA Build Prototype의 모델별 생성 조건과 함께 MESIL Provider의 "
            "guided_json 구조화 출력 계약을 보존해 실제 연결 경로를 진단합니다."
        ),
    )
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def _read_env_value(path: Path, name: str) -> str:
    """Read exactly one secret without importing or logging other values."""

    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def _load_case(path: Path, case_id: str) -> tuple[dict[str, Any], CareBriefingV1Input]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    matches = [case for case in fixture.get("cases", []) if case.get("case_id") == case_id]
    if len(matches) != 1:
        raise ValueError(f"대표 사례를 정확히 하나 찾을 수 없습니다: {case_id}")
    return fixture, CareBriefingV1Input.model_validate(matches[0]["input"])


def _safe_content_metadata(content: Any) -> dict[str, Any]:
    if not isinstance(content, str):
        return {
            "content_present": False,
            "content_length": 0,
            "content_sha256": None,
            "json_object": False,
            "top_level_keys": [],
        }
    encoded = content.encode("utf-8")
    metadata: dict[str, Any] = {
        "content_present": bool(content.strip()),
        "content_length": len(content),
        "content_sha256": hashlib.sha256(encoded).hexdigest(),
        "json_object": False,
        "top_level_keys": [],
    }
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return metadata
    if isinstance(parsed, dict):
        metadata["json_object"] = True
        metadata["top_level_keys"] = sorted(str(key) for key in parsed.keys())
    return metadata


def _safe_usage(response: dict[str, Any]) -> dict[str, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        "prompt_tokens": usage.get("prompt_tokens")
        if isinstance(usage.get("prompt_tokens"), int)
        else None,
        "completion_tokens": usage.get("completion_tokens")
        if isinstance(usage.get("completion_tokens"), int)
        else None,
        "total_tokens": usage.get("total_tokens")
        if isinstance(usage.get("total_tokens"), int)
        else None,
    }


def _contract_failure_code(
    *,
    response: dict[str, Any] | None,
    transport_error: str | None,
    content: Any,
    envelope: Any,
) -> str | None:
    """Classify transport and final-answer failures without saving raw content."""

    if response is None:
        return transport_error or "provider_error"
    if not isinstance(content, str) or not content.strip():
        return "empty_final_content"
    if envelope is not None and envelope.failure is not None:
        return envelope.failure.code
    if envelope is not None and envelope.ok:
        return None
    return "invalid_provider_response"


def _iter_sse_data(lines: Iterable[bytes]) -> Iterable[str]:
    """Yield complete SSE data fields while ignoring comments and event metadata."""

    data_lines: list[str] = []
    for raw_line in lines:
        line = raw_line.decode("utf-8").rstrip("\r\n")
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines.clear()
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            data_lines.append(line.removeprefix("data:").lstrip())
    if data_lines:
        yield "\n".join(data_lines)


def _reconstruct_openai_sse_response(lines: Iterable[bytes]) -> dict[str, Any]:
    """Rebuild one OpenAI-compatible response without persisting raw chunks."""

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    finish_reason: str | None = None
    usage: dict[str, Any] = {}
    event_count = 0

    for data in _iter_sse_data(lines):
        if data == "[DONE]":
            break
        chunk = json.loads(data)
        if not isinstance(chunk, dict):
            raise ValueError("SSE chunk is not a JSON object")
        event_count += 1

        candidate_usage = chunk.get("usage")
        if isinstance(candidate_usage, dict):
            usage = candidate_usage

        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            continue
        choice = choices[0]
        if not isinstance(choice, dict):
            continue
        candidate_finish_reason = choice.get("finish_reason")
        if isinstance(candidate_finish_reason, str):
            finish_reason = candidate_finish_reason

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            continue
        content = delta.get("content")
        if isinstance(content, str):
            content_parts.append(content)
        reasoning = delta.get("reasoning_content")
        if not isinstance(reasoning, str):
            reasoning = delta.get("reasoning")
        if isinstance(reasoning, str):
            reasoning_parts.append(reasoning)

    return {
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "".join(content_parts),
                    "reasoning_content": "".join(reasoning_parts),
                },
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage,
        "_stream_metadata": {"event_count": event_count},
    }


def _urllib_authorized_sse_transport(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    headers: dict[str, str],
) -> dict[str, Any]:
    """Collect an authorized OpenAI-compatible SSE response in memory."""

    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            **headers,
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        return _reconstruct_openai_sse_response(response)


def main() -> int:
    args = _parse_args()
    api_key_name = args.api_key_env.strip()
    api_key = os.environ.get(api_key_name, "").strip()
    if not api_key:
        api_key = _read_env_value(args.env_file, api_key_name)
    if not api_key:
        print(
            f"{api_key_name}가 설정되지 않았습니다. Git 제외 파일을 확인해 주세요.",
            file=sys.stderr,
        )
        return 2

    fixture, input_data = _load_case(args.fixture, args.case_id)
    policy = input_data.transmission_policy
    if (
        policy.privacy_mode != "external_deidentified"
        or not policy.external_ai_allowed
        or policy.contains_direct_identifiers
    ):
        print("비식별 외부 전송 조건을 만족하지 않아 진단을 중단했습니다.", file=sys.stderr)
        return 2

    diagnostics: list[dict[str, Any]] = []

    def collect_diagnostics(code: str, details: dict[str, Any]) -> None:
        diagnostics.append({"code": code, "details": details})

    provider = NvidiaCareBriefingProvider(
        base_url=args.base_url,
        model=args.model,
        api_key=api_key,
        timeout_seconds=args.timeout,
        diagnostics=collect_diagnostics,
    )
    profile = MODEL_PROFILES[args.model]
    if (
        args.disable_thinking
        and args.model != "deepseek-ai/deepseek-v4-flash-0731"
    ):
        print(
            "--disable-thinking은 현재 DeepSeek 진단에만 사용할 수 있습니다.",
            file=sys.stderr,
        )
        return 2
    payload, effective_parameters = _build_diagnostic_payload(
        provider._payload(input_data),
        profile,
        stream=args.stream,
        disable_thinking=args.disable_thinking,
        provider_contract=args.provider_contract,
    )

    started_at = time.monotonic()
    response: dict[str, Any] | None = None
    transport_error: str | None = None
    http_status: int | None = None
    try:
        transport = (
            _urllib_authorized_sse_transport
            if args.stream
            else _urllib_authorized_json_transport
        )
        response = transport(
            f"{args.base_url.rstrip('/')}/chat/completions",
            payload,
            args.timeout,
            {"Authorization": f"Bearer {api_key}"},
        )
    except HTTPError as error:
        http_status = error.code
        transport_error = "http_error"
    except (TimeoutError, socket.timeout):
        transport_error = "timeout"
    except (URLError, OSError):
        transport_error = "connection_error"
    except (json.JSONDecodeError, ValueError):
        transport_error = "invalid_provider_json"
    except Exception:
        transport_error = "internal_error"
    elapsed_ms = max(0, round((time.monotonic() - started_at) * 1000))

    message: dict[str, Any] = {}
    finish_reason: str | None = None
    if response is not None:
        choices = response.get("choices")
        if isinstance(choices, list) and choices and isinstance(choices[0], dict):
            finish_reason = (
                choices[0].get("finish_reason")
                if isinstance(choices[0].get("finish_reason"), str)
                else None
            )
            candidate = choices[0].get("message")
            if isinstance(candidate, dict):
                message = candidate

    content = message.get("content")
    reasoning = message.get("reasoning_content")
    envelope = None
    if isinstance(content, str) and content.strip():
        envelope = _validated_nvidia_compact_envelope(
            provider="nvidia",
            model=args.model,
            started_at=started_at,
            raw_content=content,
            input_data=input_data,
            diagnostics=collect_diagnostics,
        )
    failure_code = _contract_failure_code(
        response=response,
        transport_error=transport_error,
        content=content,
        envelope=envelope,
    )
    if failure_code == "empty_final_content":
        collect_diagnostics(
            "empty_final_content",
            {
                "reasoning_content_present": bool(
                    isinstance(reasoning, str) and reasoning.strip()
                ),
                "finish_reason": finish_reason,
            },
        )

    result: dict[str, Any] = {
        "diagnostic_version": "nvidia_model_profile_v4",
        "request_mode": (
            "provider_contract" if args.provider_contract else "prototype"
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_name": fixture.get("fixture_name"),
        "fixture_version": fixture.get("version"),
        "case_id": args.case_id,
        "model": args.model,
        "profile": profile["profile"],
        "prototype_parameters": effective_parameters,
        "diagnostic_overrides": {
            "disable_thinking": args.disable_thinking,
            "provider_contract": args.provider_contract,
        },
        "transport": {
            "ok": response is not None,
            "error": transport_error,
            "http_status": http_status,
            "elapsed_ms": elapsed_ms,
            "streaming": bool(payload.get("stream")),
            "event_count": (
                response.get("_stream_metadata", {}).get("event_count")
                if isinstance(response, dict)
                and isinstance(response.get("_stream_metadata"), dict)
                else None
            ),
        },
        "response": {
            **_safe_content_metadata(content),
            "reasoning_content_length": len(reasoning)
            if isinstance(reasoning, str)
            else 0,
            "finish_reason": finish_reason,
            "usage": _safe_usage(response or {}),
        },
        "care_briefing_contract": {
            "validated": bool(envelope is not None and envelope.ok),
            "failure_code": failure_code,
        },
        "diagnostics": diagnostics,
        "privacy": {
            "external_deidentified": True,
            "raw_input_saved": False,
            "raw_output_saved": False,
            "api_key_logged": False,
        },
    }

    output = args.output
    if output is None:
        safe_model = args.model.replace("/", "_").replace(":", "_")
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = (
            BACKEND_ROOT
            / "tests"
            / "results"
            / f"nvidia_profile_{safe_model}_{args.case_id.lower()}_{stamp}.json"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(
        json.dumps(
            {
                "model": args.model,
                "transport_ok": result["transport"]["ok"],
                "transport_error": result["transport"]["error"],
                "elapsed_ms": elapsed_ms,
                "finish_reason": finish_reason,
                "content_length": result["response"]["content_length"],
                "reasoning_content_length": result["response"][
                    "reasoning_content_length"
                ],
                "top_level_keys": result["response"]["top_level_keys"],
                "contract_validated": result["care_briefing_contract"]["validated"],
                "failure_code": result["care_briefing_contract"]["failure_code"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if response is not None else 1


if __name__ == "__main__":
    raise SystemExit(main())
