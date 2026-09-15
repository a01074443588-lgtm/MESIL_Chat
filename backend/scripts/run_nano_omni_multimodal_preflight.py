from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env.care-briefing.local"
DEFAULT_FIXTURE_DIR = (
    BACKEND_ROOT / "tests" / "fixtures" / "nano_omni_deidentified_v1"
)
SUPPORTED_MODES = ("image", "audio", "combined")

PROMPTS = {
    "image": (
        "This is a synthetic care test card with no real person or record. "
        "Read only the visible facts. Return one JSON object with exactly these "
        "keys: time, amount_ml, follow_up_status, privacy_note. Use null for an "
        "unreadable fact. Set privacy_note to synthetic."
    ),
    "audio": (
        "This is synthetic speech with no real person or record. Listen to the "
        "message and return one JSON object with exactly these keys: time, "
        "amount_ml, follow_up_status, privacy_note. Use null for an unclear fact. "
        "Set privacy_note to synthetic."
    ),
    "combined": (
        "The image and audio are synthetic and contain no real person or record. "
        "Compare their care facts. Return one JSON object with exactly these keys: "
        "image_audio_consistent, time, amount_ml, follow_up_status, contradictions, "
        "privacy_note. contradictions must be an array. Set privacy_note to synthetic."
    ),
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Run bounded deidentified image/audio preflights against NVIDIA Nano Omni."
        )
    )
    parser.add_argument("--mode", choices=(*SUPPORTED_MODES, "all"), default="all")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate assets and payload shape without sending media externally.",
    )
    return parser.parse_args()


def _read_env_value(path: Path, name: str) -> str:
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


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_safe_fixture(fixture_dir: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    fixture_dir = fixture_dir.resolve()
    manifest_path = fixture_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    privacy = manifest.get("privacy")
    if not isinstance(privacy, dict) or not all(
        (
            privacy.get("synthetic") is True,
            privacy.get("external_ai_allowed") is True,
            privacy.get("contains_direct_identifiers") is False,
            privacy.get("contains_real_person_or_record") is False,
        )
    ):
        raise ValueError("합성 비식별 외부 전송 조건을 만족하지 않습니다.")

    assets_metadata = manifest.get("assets")
    if not isinstance(assets_metadata, dict):
        raise ValueError("합성 시험자료 자산 목록이 없습니다.")

    assets: dict[str, bytes] = {}
    for name in ("image", "audio"):
        metadata = assets_metadata.get(name)
        if not isinstance(metadata, dict):
            raise ValueError(f"합성 {name} 자산 정보가 없습니다.")
        filename = metadata.get("file")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"합성 {name} 파일명이 안전하지 않습니다.")
        path = fixture_dir / filename
        data = path.read_bytes()
        if len(data) != metadata.get("size_bytes"):
            raise ValueError(f"합성 {name} 파일 크기가 manifest와 다릅니다.")
        if _sha256_bytes(data) != metadata.get("sha256"):
            raise ValueError(f"합성 {name} 파일 해시가 manifest와 다릅니다.")
        assets[name] = data
    return manifest, assets


def _data_url(media_type: str, data: bytes) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type};base64,{encoded}"


def _build_payload(
    *,
    mode: str,
    model: str,
    manifest: dict[str, Any],
    assets: dict[str, bytes],
) -> dict[str, Any]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"지원하지 않는 모드입니다: {mode}")
    content: list[dict[str, Any]] = [{"type": "text", "text": PROMPTS[mode]}]
    metadata = manifest["assets"]
    if mode in {"image", "combined"}:
        content.append(
            {
                "type": "image_url",
                "image_url": {
                    "url": _data_url(metadata["image"]["media_type"], assets["image"])
                },
            }
        )
    if mode in {"audio", "combined"}:
        content.append(
            {
                "type": "audio_url",
                "audio_url": {
                    "url": _data_url(metadata["audio"]["media_type"], assets["audio"])
                },
            }
        )
    return {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "temperature": 0.2,
        "top_k": 1,
        "max_tokens": 1_024,
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": False,
    }


def _authorized_json_request(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    api_key: str,
) -> tuple[dict[str, Any], int | None]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("NVIDIA 응답이 JSON 객체가 아닙니다.")
        return parsed, getattr(response, "status", None)


def _safe_usage(response: dict[str, Any]) -> dict[str, int | None]:
    usage = response.get("usage")
    if not isinstance(usage, dict):
        return {"prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    return {
        key: value if isinstance(value, int) else None
        for key, value in {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }.items()
    }


def _extract_message(response: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return {}, None
    choice = choices[0]
    message = choice.get("message")
    return (
        message if isinstance(message, dict) else {},
        choice.get("finish_reason")
        if isinstance(choice.get("finish_reason"), str)
        else None,
    )


def _sanitize_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:160]
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value[:8]]
    return None


def _allowed_keys(mode: str) -> tuple[str, ...]:
    base = ("time", "amount_ml", "follow_up_status", "privacy_note")
    if mode == "combined":
        return (
            "image_audio_consistent",
            "time",
            "amount_ml",
            "follow_up_status",
            "contradictions",
            "privacy_note",
        )
    return base


def _normalize_time(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = re.sub(r"\s+", "", value.strip().lower().replace(".", ""))
    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?(am|pm)?", compact)
    if match is None:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "0")
    meridiem = match.group(3)
    if minute > 59:
        return None
    if meridiem:
        if not 1 <= hour <= 12:
            return None
        if meridiem == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12
    elif hour > 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_and_sanitize_content(mode: str, content: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(content, str) or not content.strip():
        return None, "empty_content"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, "json_not_object"
    allowed = _allowed_keys(mode)
    if set(parsed) != set(allowed):
        return None, "unexpected_json_keys"
    return {key: _sanitize_value(parsed.get(key)) for key in allowed}, None


def _evaluate_output(
    mode: str,
    parsed: dict[str, Any] | None,
    expected: dict[str, Any],
) -> dict[str, Any]:
    if parsed is None:
        return {"passed": False, "passed_checks": 0, "total_checks": 0, "checks": {}}
    checks = {
        "time": _normalize_time(parsed.get("time"))
        == _normalize_time(expected.get("time")),
        "amount_ml": parsed.get("amount_ml") == expected.get("amount_ml"),
        "follow_up_status": str(parsed.get("follow_up_status", "")).lower()
        == expected.get("follow_up_status"),
        "privacy_note": "synthetic" in str(parsed.get("privacy_note", "")).lower(),
    }
    if mode == "combined":
        checks["image_audio_consistent"] = (
            parsed.get("image_audio_consistent")
            is expected.get("image_audio_consistent")
        )
        checks["contradictions"] = parsed.get("contradictions") == []
    passed_checks = sum(bool(value) for value in checks.values())
    return {
        "passed": passed_checks == len(checks),
        "passed_checks": passed_checks,
        "total_checks": len(checks),
        "checks": checks,
    }


def _request_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    content = payload["messages"][0]["content"]
    media_types = [item.get("type") for item in content if item.get("type") != "text"]
    serialized = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return {
        "media_types": media_types,
        "request_bytes": len(serialized),
        "asset_data_urls_logged": False,
    }


def _run_mode(
    *,
    mode: str,
    payload: dict[str, Any],
    base_url: str,
    api_key: str,
    timeout: float,
    expected: dict[str, Any],
) -> dict[str, Any]:
    started = time.monotonic()
    response: dict[str, Any] | None = None
    error: str | None = None
    http_status: int | None = None
    try:
        response, http_status = _authorized_json_request(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            timeout,
            api_key,
        )
    except HTTPError as caught:
        http_status = caught.code
        error = "http_error"
    except (TimeoutError, socket.timeout):
        error = "timeout"
    except (URLError, OSError):
        error = "connection_error"
    except (json.JSONDecodeError, ValueError):
        error = "invalid_provider_json"
    except Exception:
        error = "internal_error"
    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))

    message, finish_reason = _extract_message(response or {})
    content = message.get("content")
    parsed, parse_error = _parse_and_sanitize_content(mode, content)
    evaluation = _evaluate_output(mode, parsed, expected)
    encoded_content = content.encode("utf-8") if isinstance(content, str) else b""
    reasoning = message.get("reasoning_content")
    if not isinstance(reasoning, str):
        reasoning = message.get("reasoning")
    return {
        "mode": mode,
        "transport_ok": response is not None,
        "transport_error": error,
        "http_status": http_status,
        "elapsed_ms": elapsed_ms,
        "finish_reason": finish_reason,
        "content_present": bool(isinstance(content, str) and content.strip()),
        "content_length": len(content) if isinstance(content, str) else 0,
        "content_sha256": _sha256_bytes(encoded_content) if encoded_content else None,
        "reasoning_content_length": len(reasoning) if isinstance(reasoning, str) else 0,
        "parse_error": parse_error,
        "parsed_output": parsed,
        "evaluation": evaluation,
        "usage": _safe_usage(response or {}),
        "request": _request_metadata(payload),
    }


def main() -> int:
    args = _parse_args()
    if args.timeout <= 0:
        print("--timeout은 0보다 커야 합니다.", file=sys.stderr)
        return 2
    if args.model.strip() != DEFAULT_MODEL:
        print("이 실행기는 확인된 Nano Omni 모델 ID만 허용합니다.", file=sys.stderr)
        return 2

    try:
        manifest, assets = _load_safe_fixture(args.fixture_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"합성 시험자료 검증 실패: {error}", file=sys.stderr)
        return 2

    modes = list(SUPPORTED_MODES) if args.mode == "all" else [args.mode]
    payloads = {
        mode: _build_payload(
            mode=mode,
            model=args.model,
            manifest=manifest,
            assets=assets,
        )
        for mode in modes
    }
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "model": args.model,
                    "fixture_version": manifest.get("fixture_version"),
                    "modes": {
                        mode: _request_metadata(payload) for mode, payload in payloads.items()
                    },
                    "external_calls": 0,
                },
                ensure_ascii=False,
            )
        )
        return 0

    api_key_name = args.api_key_env.strip()
    api_key = os.environ.get(api_key_name, "").strip()
    if not api_key:
        api_key = _read_env_value(args.env_file, api_key_name)
    if not api_key:
        print("NVIDIA API 키가 설정되지 않았습니다.", file=sys.stderr)
        return 2

    runs: list[dict[str, Any]] = []
    for mode in modes:
        run = _run_mode(
            mode=mode,
            payload=payloads[mode],
            base_url=args.base_url,
            api_key=api_key,
            timeout=args.timeout,
            expected=manifest["expected"],
        )
        runs.append(run)
        print(
            f"[{len(runs)}/{len(modes)}] {mode} "
            f"transport_ok={run['transport_ok']} "
            f"quality_pass={run['evaluation']['passed']} "
            f"elapsed_ms={run['elapsed_ms']}",
            file=sys.stderr,
            flush=True,
        )

    report = {
        "diagnostic_version": "nano_omni_multimodal_preflight_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "model": args.model,
        "fixture_version": manifest.get("fixture_version"),
        "fixture_manifest_sha256": _sha256_bytes(
            (args.fixture_dir / "manifest.json").read_bytes()
        ),
        "summary": {
            "runs": len(runs),
            "transport_successes": sum(run["transport_ok"] for run in runs),
            "quality_passes": sum(run["evaluation"]["passed"] for run in runs),
        },
        "runs": runs,
        "privacy": {
            "synthetic": True,
            "external_deidentified": True,
            "contains_direct_identifiers": False,
            "contains_real_person_or_record": False,
            "raw_media_saved_in_report": False,
            "raw_output_saved": False,
            "api_key_logged": False,
        },
    }
    output = args.output
    if output is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = (
            BACKEND_ROOT
            / "tests"
            / "results"
            / f"nano_omni_multimodal_preflight_{stamp}.json"
        )
    if output.exists():
        print(f"기존 결과 파일을 덮어쓰지 않습니다: {output}", file=sys.stderr)
        return 2
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "model": args.model,
                "runs": report["summary"]["runs"],
                "transport_successes": report["summary"]["transport_successes"],
                "quality_passes": report["summary"]["quality_passes"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["summary"]["transport_successes"] == len(runs) else 1


if __name__ == "__main__":
    raise SystemExit(main())
