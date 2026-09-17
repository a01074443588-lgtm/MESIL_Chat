from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import shlex
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
DEFAULT_FIXTURE_DIR = (
    BACKEND_ROOT / "tests" / "fixtures" / "handwriting_vision_compare_v1"
)
DEFAULT_NVIDIA_ENV_FILE = PROJECT_ROOT / ".env.care-briefing.local"
DEFAULT_X370D_ENV_FILE = PROJECT_ROOT / ".env.x370d.local"
NANO_MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
QWEN_MODEL = "qwen3-vl:8b-instruct"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
REQUIRED_KEYS = (
    "event_time",
    "quantity_value",
    "quantity_unit",
    "completion_status",
    "follow_up_time",
    "follow_up_status",
    "privacy_note",
    "unreadable_fields",
)
PROMPT = (
    "This image is a synthetic handwritten care note with no real person or "
    "record. Read only the visible handwritten facts and do not invent a name. "
    "Return exactly one JSON object with exactly these keys: event_time, "
    "quantity_value, quantity_unit, completion_status, follow_up_time, "
    "follow_up_status, privacy_note, unreadable_fields. Normalize times to 24-hour "
    "HH:MM. quantity_value must be a number or null. quantity_unit must be one of "
    "ml, percent, min, g, drops, or null. Status values must be completed, pending, "
    "or unknown. privacy_note must be synthetic. unreadable_fields must be an array "
    "of field names and must be empty when all facts are readable. Return JSON only."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare handwriting OCR contracts with one bounded provider."
    )
    parser.add_argument("--provider", choices=("nano", "qwen"), required=True)
    parser.add_argument("--phase", choices=("preflight", "suite"), required=True)
    parser.add_argument("--case-id")
    parser.add_argument("--fixture-dir", type=Path, default=DEFAULT_FIXTURE_DIR)
    parser.add_argument("--nvidia-env-file", type=Path, default=DEFAULT_NVIDIA_ENV_FILE)
    parser.add_argument("--x370d-env-file", type=Path, default=DEFAULT_X370D_ENV_FILE)
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dry-run", action="store_true")
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


def _load_safe_fixture(
    fixture_dir: Path,
) -> tuple[dict[str, Any], list[tuple[dict[str, Any], bytes]]]:
    fixture_dir = fixture_dir.resolve()
    manifest_path = fixture_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("fixture_version") != "handwriting_vision_compare_v1":
        raise ValueError("허용된 손글씨 fixture 버전이 아닙니다.")
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
    cases = manifest.get("cases")
    if not isinstance(cases, list) or len(cases) != 6:
        raise ValueError("손글씨 비교 사례는 정확히 6건이어야 합니다.")

    loaded: list[tuple[dict[str, Any], bytes]] = []
    seen: set[str] = set()
    for case in cases:
        if not isinstance(case, dict):
            raise ValueError("손글씨 사례 형식이 잘못됐습니다.")
        case_id = case.get("case_id")
        filename = case.get("file")
        if (
            not isinstance(case_id, str)
            or case_id in seen
            or not isinstance(filename, str)
            or Path(filename).name != filename
            or case.get("media_type") != "image/png"
        ):
            raise ValueError("손글씨 사례 식별자 또는 파일명이 안전하지 않습니다.")
        seen.add(case_id)
        data = (fixture_dir / filename).read_bytes()
        if len(data) != case.get("size_bytes"):
            raise ValueError(f"{case_id} 파일 크기가 manifest와 다릅니다.")
        if _sha256_bytes(data) != case.get("sha256"):
            raise ValueError(f"{case_id} 파일 해시가 manifest와 다릅니다.")
        expected = case.get("expected")
        if not isinstance(expected, dict) or set(expected) != set(REQUIRED_KEYS[:6]):
            raise ValueError(f"{case_id} 정답 계약이 잘못됐습니다.")
        loaded.append((case, data))
    return manifest, loaded


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


def _normalize_unit(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    compact = value.strip().lower().replace("%", "percent")
    aliases = {
        "milliliter": "ml",
        "milliliters": "ml",
        "minute": "min",
        "minutes": "min",
        "gram": "g",
        "grams": "g",
        "drop": "drops",
    }
    return aliases.get(compact, compact)


def _parse_content(content: Any) -> tuple[dict[str, Any] | None, str | None]:
    if not isinstance(content, str) or not content.strip():
        return None, "empty_content"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None, "invalid_json"
    if not isinstance(parsed, dict):
        return None, "json_not_object"
    if set(parsed) != set(REQUIRED_KEYS):
        return None, "unexpected_json_keys"
    if not isinstance(parsed.get("unreadable_fields"), list):
        return None, "invalid_unreadable_fields"
    sanitized: dict[str, Any] = {}
    for key in REQUIRED_KEYS:
        value = parsed.get(key)
        if key == "unreadable_fields":
            sanitized[key] = [str(item)[:64] for item in value[:8]]
        elif value is None or isinstance(value, (bool, int, float)):
            sanitized[key] = value
        elif isinstance(value, str):
            sanitized[key] = value[:96]
        else:
            return None, f"invalid_type_{key}"
    return sanitized, None


def _evaluate(
    parsed: dict[str, Any] | None,
    expected: dict[str, Any],
) -> dict[str, Any]:
    if parsed is None:
        return {
            "quality_passed": False,
            "passed_checks": 0,
            "total_checks": 8,
            "checks": {},
        }
    quantity = parsed.get("quantity_value")
    expected_quantity = expected.get("quantity_value")
    checks = {
        "event_time": _normalize_time(parsed.get("event_time"))
        == expected.get("event_time"),
        "quantity_value": isinstance(quantity, (int, float))
        and not isinstance(quantity, bool)
        and float(quantity) == float(expected_quantity),
        "quantity_unit": _normalize_unit(parsed.get("quantity_unit"))
        == expected.get("quantity_unit"),
        "completion_status": str(parsed.get("completion_status", "")).lower()
        == expected.get("completion_status"),
        "follow_up_time": _normalize_time(parsed.get("follow_up_time"))
        == expected.get("follow_up_time"),
        "follow_up_status": str(parsed.get("follow_up_status", "")).lower()
        == expected.get("follow_up_status"),
        "privacy_note": "synthetic" in str(parsed.get("privacy_note", "")).lower(),
        "unreadable_fields": parsed.get("unreadable_fields") == [],
    }
    passed_checks = sum(bool(value) for value in checks.values())
    return {
        "quality_passed": passed_checks == len(checks),
        "passed_checks": passed_checks,
        "total_checks": len(checks),
        "checks": checks,
    }


def _post_json(
    url: str,
    payload: dict[str, Any],
    timeout_seconds: float,
    headers: dict[str, str] | None = None,
) -> tuple[dict[str, Any], int | None]:
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    request_headers.update(headers or {})
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers=request_headers,
        method="POST",
    )
    with urlopen(request, timeout=timeout_seconds) as response:
        parsed = json.loads(response.read().decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("provider response is not an object")
        return parsed, getattr(response, "status", None)


def _post_qwen_via_ssh(
    payload: dict[str, Any],
    ssh_settings: dict[str, str],
    timeout_seconds: float,
) -> tuple[dict[str, Any], int | None]:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    remote_code = (
        "import sys,json;"
        "from urllib.request import Request,urlopen;"
        "data=sys.stdin.buffer.read();"
        "request=Request('http://127.0.0.1:11434/api/chat',data=data,"
        "headers={'Content-Type':'application/json','Accept':'application/json'},"
        "method='POST');"
        f"response=urlopen(request,timeout={min(timeout_seconds, 900)!r});"
        "sys.stdout.buffer.write(response.read())"
    )
    try:
        client.connect(
            hostname=ssh_settings["address"],
            port=int(ssh_settings["port"]),
            username=ssh_settings["username"],
            password=ssh_settings["password"],
            timeout=min(timeout_seconds, 30),
            banner_timeout=min(timeout_seconds, 30),
            auth_timeout=min(timeout_seconds, 30),
        )
        stdin, stdout, _ = client.exec_command(
            f"python3 -c {shlex.quote(remote_code)}",
            timeout=timeout_seconds,
        )
        request_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        stdin.channel.sendall(request_bytes)
        stdin.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        response_bytes = stdout.read()
        if exit_code != 0:
            raise OSError("x370d_ssh_provider_error")
        parsed = json.loads(response_bytes.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("x370d response is not an object")
        return parsed, 200
    finally:
        client.close()


def _error_class(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return "http_error"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(error, (URLError, OSError)):
        return "connection_error"
    if isinstance(error, (json.JSONDecodeError, ValueError)):
        return "invalid_provider_json"
    return "internal_error"


def _nano_payload(image: bytes) -> dict[str, Any]:
    encoded = base64.b64encode(image).decode("ascii")
    return {
        "model": NANO_MODEL,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{encoded}"},
                    },
                ],
            }
        ],
        "temperature": 0.2,
        "top_k": 1,
        "max_tokens": 512,
        "chat_template_kwargs": {"enable_thinking": False},
        "stream": False,
    }


def _qwen_payload(image: bytes, keep_alive: int | str) -> dict[str, Any]:
    return {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "user",
                "content": PROMPT,
                "images": [base64.b64encode(image).decode("ascii")],
            }
        ],
        "stream": False,
        "think": False,
        "keep_alive": keep_alive,
        "options": {"temperature": 0.2, "top_k": 1, "num_predict": 512},
    }


def _request_metadata(payload: dict[str, Any], image_size: int) -> dict[str, Any]:
    return {
        "media_type": "image/png",
        "image_size_bytes": image_size,
        "request_size_bytes": len(
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
        ),
        "asset_data_logged": False,
    }


def _extract_nano(response: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None, {}
    message = choices[0].get("message")
    if not isinstance(message, dict):
        return None, {}
    usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
    return message.get("content"), {
        "prompt_tokens": usage.get("prompt_tokens"),
        "completion_tokens": usage.get("completion_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "finish_reason": choices[0].get("finish_reason"),
    }


def _extract_qwen(response: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    return content, {
        "total_duration_ms": round(response.get("total_duration", 0) / 1_000_000, 3)
        if isinstance(response.get("total_duration"), int)
        else None,
        "load_duration_ms": round(response.get("load_duration", 0) / 1_000_000, 3)
        if isinstance(response.get("load_duration"), int)
        else None,
        "prompt_eval_count": response.get("prompt_eval_count"),
        "eval_count": response.get("eval_count"),
        "done_reason": response.get("done_reason"),
    }


def _run_case(
    *,
    provider: str,
    case: dict[str, Any],
    image: bytes,
    timeout: float,
    api_key: str,
    ollama_base_url: str,
    ssh_settings: dict[str, str],
    keep_alive: int | str,
) -> dict[str, Any]:
    started = time.monotonic()
    response: dict[str, Any] | None = None
    status: int | None = None
    transport_error: str | None = None
    payload = _nano_payload(image) if provider == "nano" else _qwen_payload(image, keep_alive)
    try:
        if provider == "nano":
            response, status = _post_json(
                f"{NVIDIA_BASE_URL}/chat/completions",
                payload,
                timeout,
                headers={"Authorization": f"Bearer {api_key}"},
            )
        else:
            if ollama_base_url:
                response, status = _post_json(
                    f"{ollama_base_url.rstrip('/')}/api/chat",
                    payload,
                    timeout,
                )
            else:
                response, status = _post_qwen_via_ssh(
                    payload,
                    ssh_settings,
                    timeout,
                )
    except Exception as error:
        status = error.code if isinstance(error, HTTPError) else None
        transport_error = _error_class(error)
    elapsed_ms = max(0, round((time.monotonic() - started) * 1000))
    if provider == "nano":
        content, provider_metrics = _extract_nano(response or {})
    else:
        content, provider_metrics = _extract_qwen(response or {})
    parsed, parse_error = _parse_content(content)
    evaluation = _evaluate(parsed, case["expected"])
    raw_bytes = content.encode("utf-8") if isinstance(content, str) else b""
    return {
        "case_id": case["case_id"],
        "transport_ok": response is not None,
        "transport_error": transport_error,
        "http_status": status,
        "elapsed_ms": elapsed_ms,
        "contract_passed": parse_error is None,
        "parse_error": parse_error,
        "content_present": bool(isinstance(content, str) and content.strip()),
        "content_length": len(content) if isinstance(content, str) else 0,
        "content_sha256": _sha256_bytes(raw_bytes) if raw_bytes else None,
        "parsed_output": parsed,
        "evaluation": evaluation,
        "provider_metrics": provider_metrics,
        "request": _request_metadata(payload, len(image)),
    }


def _unload_qwen(
    ollama_base_url: str,
    ssh_settings: dict[str, str],
    timeout: float,
) -> bool:
    payload = {"model": QWEN_MODEL, "keep_alive": 0}
    try:
        if ollama_base_url:
            _post_json(
                f"{ollama_base_url.rstrip('/')}/api/generate",
                payload,
                min(timeout, 30),
            )
        else:
            unload_payload = {
                "model": QWEN_MODEL,
                "messages": [],
                "stream": False,
                "keep_alive": 0,
            }
            _post_qwen_via_ssh(unload_payload, ssh_settings, min(timeout, 30))
        return True
    except Exception:
        return False


def main() -> int:
    args = _parse_args()
    if args.timeout <= 0:
        print("--timeout은 0보다 커야 합니다.", file=sys.stderr)
        return 2
    if args.output.exists():
        print(f"기존 결과 파일을 덮어쓰지 않습니다: {args.output}", file=sys.stderr)
        return 2
    try:
        manifest, cases = _load_safe_fixture(args.fixture_dir)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"합성 손글씨 시험자료 검증 실패: {error}", file=sys.stderr)
        return 2

    if args.phase == "preflight":
        selected_id = args.case_id or cases[0][0]["case_id"]
        selected = [item for item in cases if item[0]["case_id"] == selected_id]
        if len(selected) != 1:
            print("사전검증 case-id가 fixture에 없습니다.", file=sys.stderr)
            return 2
        cases = selected
    elif args.case_id:
        print("--case-id는 preflight에서만 사용합니다.", file=sys.stderr)
        return 2

    api_key = ""
    ollama_base_url = ""
    ssh_settings: dict[str, str] = {}
    if args.provider == "nano":
        api_key = os.environ.get("NVIDIA_API_KEY", "").strip()
        if not api_key:
            api_key = _read_env_value(args.nvidia_env_file, "NVIDIA_API_KEY")
        if not api_key and not args.dry_run:
            print("NVIDIA API 키가 보호 설정에 없습니다.", file=sys.stderr)
            return 2
    else:
        ollama_base_url = _read_env_value(
            args.x370d_env_file,
            "X370D_OLLAMA_BASE_URL",
        )
        ssh_settings = {
            "address": _read_env_value(args.x370d_env_file, "X370D_ADDRESS"),
            "port": _read_env_value(args.x370d_env_file, "X370D_PORT") or "22",
            "username": _read_env_value(args.x370d_env_file, "X370D_USERNAME"),
            "password": _read_env_value(args.x370d_env_file, "X370D_PASSWORD"),
        }
        if not ollama_base_url and not args.dry_run and not all(ssh_settings.values()):
            print("x370d Ollama 보호 주소 또는 SSH 보호 설정이 필요합니다.", file=sys.stderr)
            return 2

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "provider": args.provider,
                    "phase": args.phase,
                    "case_ids": [case["case_id"] for case, _ in cases],
                    "external_calls": 0,
                    "model_calls": 0,
                    "raw_media_logged": False,
                },
                ensure_ascii=False,
            )
        )
        return 0

    keep_alive: int | str = 0 if args.phase == "preflight" else "5m"
    runs: list[dict[str, Any]] = []
    for case, image in cases:
        run = _run_case(
            provider=args.provider,
            case=case,
            image=image,
            timeout=args.timeout,
            api_key=api_key,
            ollama_base_url=ollama_base_url,
            ssh_settings=ssh_settings,
            keep_alive=keep_alive,
        )
        runs.append(run)
        print(
            f"[{len(runs)}/{len(cases)}] {case['case_id']} "
            f"transport_ok={run['transport_ok']} "
            f"contract_passed={run['contract_passed']} "
            f"quality_passed={run['evaluation']['quality_passed']} "
            f"elapsed_ms={run['elapsed_ms']}",
            file=sys.stderr,
            flush=True,
        )

    unload_ok = None
    if args.provider == "qwen" and args.phase == "suite":
        unload_ok = _unload_qwen(ollama_base_url, ssh_settings, args.timeout)
    elapsed_values = [run["elapsed_ms"] for run in runs]
    report = {
        "diagnostic_version": "handwriting_vision_comparison_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "phase": args.phase,
        "provider": "nvidia_hosted" if args.provider == "nano" else "x370d_internal",
        "model": NANO_MODEL if args.provider == "nano" else QWEN_MODEL,
        "fixture_version": manifest["fixture_version"],
        "fixture_manifest_sha256": _sha256_bytes(
            (args.fixture_dir / "manifest.json").read_bytes()
        ),
        "prompt_sha256": _sha256_bytes(PROMPT.encode("utf-8")),
        "summary": {
            "runs": len(runs),
            "transport_successes": sum(run["transport_ok"] for run in runs),
            "contract_passes": sum(run["contract_passed"] for run in runs),
            "quality_passes": sum(
                run["evaluation"]["quality_passed"] for run in runs
            ),
            "average_passed_checks": round(
                sum(run["evaluation"]["passed_checks"] for run in runs) / len(runs),
                3,
            ),
            "average_elapsed_ms": round(sum(elapsed_values) / len(elapsed_values), 1),
            "max_elapsed_ms": max(elapsed_values),
            "unload_ok": unload_ok,
        },
        "runs": runs,
        "privacy": {
            "synthetic": True,
            "external_deidentified": args.provider == "nano",
            "contains_direct_identifiers": False,
            "contains_real_person_or_record": False,
            "raw_media_saved_in_report": False,
            "raw_output_saved": False,
            "api_key_logged": False,
            "endpoint_logged": False,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "provider": args.provider,
                "phase": args.phase,
                "runs": report["summary"]["runs"],
                "transport_successes": report["summary"]["transport_successes"],
                "contract_passes": report["summary"]["contract_passes"],
                "quality_passes": report["summary"]["quality_passes"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return (
        0
        if report["summary"]["transport_successes"] == len(runs)
        and report["summary"]["contract_passes"] == len(runs)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
