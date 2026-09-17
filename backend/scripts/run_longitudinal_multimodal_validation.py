from __future__ import annotations

import argparse
import base64
from concurrent.futures import Future, ThreadPoolExecutor
import hashlib
import json
import re
import shlex
import socket
import sys
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import httpx

from app.multimodal_correction_policy import decide_multimodal_correction


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
FIXTURE_DIR = BACKEND_ROOT / "tests" / "fixtures" / "longitudinal_multimodal_v1"
ASSET_DIR = PROJECT_ROOT / "data" / "runtime" / "longitudinal_multimodal_v1"
TARGET_FILE = PROJECT_ROOT / "data" / "runtime" / "longitudinal_care_target.local.json"
X370D_ENV_FILE = PROJECT_ROOT / ".env.x370d.local"
PROJECT_ENV_FILE = PROJECT_ROOT / ".env"
QWEN_MODEL = "qwen3-vl:8b-instruct"
ALIAS = "검증어르신(가명)"
OCR_PROMPT = (
    "이미지에 실제로 보이는 손글씨 한글과 숫자를 위에서 아래 순서대로 한 번씩 "
    "그대로 옮겨 적으세요. 추측·요약·설명 없이 텍스트만 출력하세요."
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="실제 손글씨와 음성을 내부 DEV 경로에서만 검증합니다."
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _read_env_value(path: Path, name: str) -> str:
    if not path.is_file():
        return ""
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator and key.strip() == name:
            return value.strip().strip('"').strip("'")
    return ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON 객체가 아닙니다: {path.name}")
    return payload


def _validate_inputs() -> tuple[dict[str, Any], dict[str, Any], str]:
    manifest = _load_json(FIXTURE_DIR / "manifest.json")
    truth = _load_json(FIXTURE_DIR / "ground_truth.json")
    target = _load_json(TARGET_FILE)
    display_name = str(target.get("display_name") or "").strip()
    privacy = manifest.get("privacy")
    if manifest.get("fixture_version") != "longitudinal_multimodal_v1":
        raise ValueError("허용된 멀티모달 fixture 버전이 아닙니다.")
    if not isinstance(privacy, dict) or not all(
        (
            privacy.get("synthetic_fixture") is True,
            privacy.get("official_record") is False,
            privacy.get("external_transfer_allowed") is False,
        )
    ):
        raise ValueError("내부 전용 합성자료 안전 계약이 일치하지 않습니다.")
    if truth.get("ground_truth_is_model_input") is not False:
        raise ValueError("정답표가 모델 입력과 분리되어 있지 않습니다.")
    if len(display_name) < 2 or target.get("is_test_data") is not True:
        raise ValueError("DEV 로컬 검증 대상 연결정보가 안전하지 않습니다.")
    cases = truth.get("cases")
    if not isinstance(cases, list) or len(cases) != 5:
        raise ValueError("실제 손글씨 정답은 정확히 5건이어야 합니다.")
    assets = manifest.get("assets")
    if not isinstance(assets, list) or len(assets) != 7:
        raise ValueError("보호 원본은 이미지 5건·음성 2건이어야 합니다.")
    for asset in assets:
        path = ASSET_DIR / str(asset["file"])
        data = path.read_bytes()
        if len(data) != asset["size_bytes"] or _sha256(data) != asset["sha256"]:
            raise ValueError(f"보호 원본 무결성이 다릅니다: {asset['asset_id']}")
    return manifest, truth, display_name


def _redact(text: str, display_name: str, *, remove_identity_header: bool) -> str:
    clean = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if display_name:
        pattern = r"\s*".join(re.escape(char) for char in display_name)
        clean = re.sub(pattern, ALIAS, clean, flags=re.IGNORECASE)
    # OCR/STT가 실명을 비슷한 다른 이름으로 오인식해도 추적 결과에는
    # 사람 이름처럼 보이는 문자열을 남기지 않는다. 일반 문장 오탐을
    # 막기 위해 이름 뒤에 어르신·가상 표식·시간이 붙은 경우만 가린다.
    surname = "김이박최정강조윤장임한오서신권황안송류홍전고문양손배백허유남심노하곽성차주우구민진지엄채원천방공현함변염여추도소석선설마길연위표명기반왕금옥육인맹제모탁국어은편용"
    person_like = re.compile(
        rf"(?<![가-힣])[{surname}]\s*[가-힣]\s*[가-힣](?=\s*(?:어르신|가상|\d{{1,2}}[:시]))"
    )
    clean = person_like.sub(ALIAS, clean)
    raw_lines = [line.strip() for line in clean.splitlines() if line.strip()]
    if remove_identity_header:
        first_date_index = next(
            (
                index
                for index, line in enumerate(raw_lines)
                if re.search(r"\d{1,2}\s*월\s*\d{1,2}\s*일", line)
            ),
            None,
        )
        if first_date_index is not None and first_date_index > 0:
            raw_lines = raw_lines[first_date_index:]
    lines: list[str] = []
    for line in raw_lines:
        if remove_identity_header and "가상자료" in line:
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def _normalized(text: str) -> str:
    return "".join(re.findall(r"[0-9A-Za-z가-힣%/]", text.lower()))


def _levenshtein(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def _character_accuracy(candidate: str, reference: str) -> float:
    normalized_candidate = _normalized(candidate)
    normalized_reference = _normalized(reference)
    if not normalized_reference:
        return 1.0 if not normalized_candidate else 0.0
    distance = _levenshtein(normalized_candidate, normalized_reference)
    return round(max(0.0, 1 - distance / len(normalized_reference)), 4)


def _preservation(candidate: str, terms: dict[str, list[list[str]]]) -> dict[str, Any]:
    compact = _normalized(candidate)
    categories: dict[str, dict[str, Any]] = {}
    for category in ("time", "quantity", "unit", "completion_status", "negation"):
        groups = terms.get(category, [])
        checks = [
            any(_normalized(alternative) in compact for alternative in alternatives)
            for alternatives in groups
        ]
        categories[category] = {
            "preserved": sum(checks),
            "total": len(checks),
            "rate": round(sum(checks) / len(checks), 4) if checks else None,
            "checks": checks,
        }
    return categories


def _unsupported_numeric_tokens(candidate: str, reference: str) -> list[str]:
    token_pattern = re.compile(r"\d+(?:[:/.]\d+)?", re.IGNORECASE)
    reference_tokens = set(token_pattern.findall(reference))
    return sorted(set(token_pattern.findall(candidate)) - reference_tokens)


def _post_json(url: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
    request = Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        parsed = json.loads(response.read().decode("utf-8"))
    if not isinstance(parsed, dict):
        raise ValueError("내부 모델 응답이 JSON 객체가 아닙니다.")
    return parsed


def _post_qwen_via_ssh(
    payload: dict[str, Any], ssh_settings: dict[str, str], timeout: float
) -> dict[str, Any]:
    import paramiko

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    remote_code = (
        "import sys,json;"
        "from urllib.request import Request,urlopen;"
        "data=sys.stdin.buffer.read();"
        "request=Request('http://127.0.0.1:11434/api/chat',data=data,"
        "headers={'Content-Type':'application/json'},method='POST');"
        f"response=urlopen(request,timeout={min(timeout, 900)!r});"
        "sys.stdout.buffer.write(response.read())"
    )
    try:
        client.connect(
            hostname=ssh_settings["address"],
            port=int(ssh_settings["port"]),
            username=ssh_settings["username"],
            password=ssh_settings["password"],
            timeout=min(timeout, 30),
            banner_timeout=min(timeout, 30),
            auth_timeout=min(timeout, 30),
        )
        stdin, stdout, _ = client.exec_command(
            f"python3 -c {shlex.quote(remote_code)}", timeout=timeout
        )
        stdin.channel.sendall(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        stdin.channel.shutdown_write()
        exit_code = stdout.channel.recv_exit_status()
        response_bytes = stdout.read()
        if exit_code != 0:
            raise OSError("x370d_internal_provider_error")
        parsed = json.loads(response_bytes.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("x370d 내부 응답이 JSON 객체가 아닙니다.")
        return parsed
    finally:
        client.close()


def _qwen_call(
    image: bytes,
    prompt: str,
    *,
    timeout: float,
    ollama_base_url: str,
    ssh_settings: dict[str, str],
) -> tuple[str, dict[str, Any]]:
    payload = {
        "model": QWEN_MODEL,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image).decode("ascii")],
            }
        ],
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 4096},
    }
    started = time.monotonic()
    if ollama_base_url:
        response = _post_json(
            f"{ollama_base_url.rstrip('/')}/api/chat", payload, timeout
        )
    else:
        response = _post_qwen_via_ssh(payload, ssh_settings, timeout)
    elapsed_ms = round((time.monotonic() - started) * 1000)
    message = response.get("message")
    content = message.get("content") if isinstance(message, dict) else ""
    if not isinstance(content, str) or not content.strip():
        raise ValueError("내부 Qwen3-VL 판독문이 비어 있습니다.")
    metrics = {
        "elapsed_ms": elapsed_ms,
        "load_duration_ms": round(response.get("load_duration", 0) / 1_000_000, 3)
        if isinstance(response.get("load_duration"), int)
        else None,
        "prompt_eval_count": response.get("prompt_eval_count"),
        "eval_count": response.get("eval_count"),
        "raw_media_logged": False,
        "endpoint_logged": False,
    }
    return content.strip(), metrics


def _unload_qwen(
    *, timeout: float, ollama_base_url: str, ssh_settings: dict[str, str]
) -> bool:
    payload = {
        "model": QWEN_MODEL,
        "messages": [],
        "stream": False,
        "keep_alive": 0,
    }
    try:
        if ollama_base_url:
            _post_json(f"{ollama_base_url.rstrip('/')}/api/chat", payload, min(timeout, 30))
        else:
            _post_qwen_via_ssh(payload, ssh_settings, min(timeout, 30))
        return True
    except Exception:
        return False


def _transcribe(
    path: Path, *, display_name: str
) -> tuple[str, dict[str, Any]]:
    token = _read_env_value(PROJECT_ENV_FILE, "STT_SHARED_TOKEN")
    if len(token) < 24:
        raise ValueError("격리 DEV STT 보호 토큰이 없습니다.")
    port = int(_read_env_value(PROJECT_ENV_FILE, "DEV_STT_PORT") or "8767")
    with wave.open(str(path), "rb") as source:
        audio_duration = source.getnframes() / float(source.getframerate())
    started = time.monotonic()
    with path.open("rb") as source:
        response = httpx.post(
            f"http://127.0.0.1:{port}/transcribe",
            headers={"X-STT-Token": token},
            files={"file": (path.name, source, "audio/wav")},
            data={
                "initial_prompt": "돌봄 관찰 기록을 또박또박 읽는 합성 시험 음성입니다.",
                "hotwords": "혈압, 부축, 수분, 섭취, 재확인, 배변, 넘어지지 않음",
            },
            timeout=600,
        )
    elapsed_ms = round((time.monotonic() - started) * 1000)
    response.raise_for_status()
    payload = response.json()
    transcript = _redact(str(payload.get("text") or ""), display_name, remove_identity_header=False)
    if not transcript:
        raise ValueError("격리 DEV STT 전사문이 비어 있습니다.")
    processing_seconds = float(payload.get("duration_seconds") or elapsed_ms / 1000)
    return transcript, {
        "model": str(payload.get("model") or "faster-whisper"),
        "processing_location": "isolated_dev_local",
        "elapsed_ms": elapsed_ms,
        "audio_duration_seconds": round(audio_duration, 3),
        "real_time_factor": round(processing_seconds / audio_duration, 3),
        "raw_media_logged": False,
        "endpoint_logged": False,
    }


def _correction_prompt(initial_ocr: str, transcript: str | None, mode: str) -> str:
    evidence_instruction = (
        "음성은 손글씨 전체를 읽은 보완 근거입니다. 이미지와 일치하는 부분만 글자 교정에 사용하세요."
        if mode == "full_reading"
        else "음성은 줄거리 설명 힌트일 뿐 정답이 아닙니다. 음성만으로 시간·수량·단위·혈압·"
        "배변 상태·완료 여부·부정 표현·후속 확인을 추가하지 마세요."
        if mode == "story_hint"
        else "별도 음성 근거가 없습니다. 이미지에서 직접 확인되는 글자만 교정하세요."
    )
    transcript_block = transcript or "없음"
    return (
        "실제 손글씨 이미지와 1차 OCR을 대조하여 직원 승인 전 교정 제안문을 만드세요. "
        "이름·검증 표식은 출력하지 말고 본문만 옮기세요. 새 사실을 만들거나 요약하지 말고, "
        "읽을 수 없는 곳은 [확인 필요]로 남기세요. "
        f"{evidence_instruction}\n\n1차 OCR:\n{initial_ocr}\n\n음성 전사:\n{transcript_block}\n\n"
        "교정 제안문만 출력하세요."
    )


def _error_class(error: Exception) -> str:
    if isinstance(error, HTTPError):
        return "internal_http_error"
    if isinstance(error, (TimeoutError, socket.timeout, httpx.TimeoutException)):
        return "timeout"
    if isinstance(error, (URLError, OSError, httpx.ConnectError)):
        return "connection_error"
    return "validation_error"


def main() -> int:
    args = _parse_args()
    if args.output.exists():
        print("기존 결과 파일은 덮어쓰지 않습니다.", file=sys.stderr)
        return 2
    try:
        manifest, truth, display_name = _validate_inputs()
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"보호 원본·계약 검증 실패: {type(error).__name__}", file=sys.stderr)
        return 2

    image_assets = {
        asset["case_id"]: asset
        for asset in manifest["assets"]
        if asset.get("case_id")
    }
    audio_mappings = {
        mapping["handwriting_case_id"]: mapping
        for mapping in manifest["audio_evidence"]["mappings"]
    }
    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "handwriting_cases": len(truth["cases"]),
                    "audio_cases": len(audio_mappings),
                    "external_calls": 0,
                    "official_record_writes": 0,
                },
                ensure_ascii=False,
            )
        )
        return 0

    ollama_base_url = _read_env_value(X370D_ENV_FILE, "X370D_OLLAMA_BASE_URL")
    ssh_settings = {
        "address": _read_env_value(X370D_ENV_FILE, "X370D_ADDRESS"),
        "port": _read_env_value(X370D_ENV_FILE, "X370D_PORT") or "22",
        "username": _read_env_value(X370D_ENV_FILE, "X370D_USERNAME"),
        "password": _read_env_value(X370D_ENV_FILE, "X370D_PASSWORD"),
    }
    if not ollama_base_url and not all(ssh_settings.values()):
        print("x370d 보호 연결 설정이 없습니다.", file=sys.stderr)
        return 2

    audio_by_id = {asset["asset_id"]: asset for asset in manifest["assets"]}
    def transcribe_safely(
        _case_id: str, mapping: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        asset = audio_by_id[mapping["audio_id"]]
        try:
            result = _transcribe(
                ASSET_DIR / asset["file"], display_name=display_name
            )
            print(f"[STT] {mapping['audio_id']} success", file=sys.stderr, flush=True)
            return result
        except Exception as error:
            result = (
                "",
                {"error_class": _error_class(error), "processing_location": "isolated_dev_local"},
            )
            print(f"[STT] {mapping['audio_id']} failed", file=sys.stderr, flush=True)
            return result

    # STT runs on the isolated DEV service while Qwen3-VL performs the first
    # image read.  Qwen calls themselves remain serial to avoid x370d queueing.
    transcript_executor = ThreadPoolExecutor(max_workers=max(1, len(audio_mappings)))
    transcript_futures: dict[str, Future[tuple[str, dict[str, Any]]]] = {
        case_id: transcript_executor.submit(transcribe_safely, case_id, mapping)
        for case_id, mapping in audio_mappings.items()
    }

    results: list[dict[str, Any]] = []
    for index, case in enumerate(truth["cases"], start=1):
        case_started = time.monotonic()
        case_id = case["case_id"]
        image_asset = image_assets[case_id]
        image = (ASSET_DIR / image_asset["file"]).read_bytes()
        mapping = audio_mappings.get(case_id)
        transcript = ""
        transcript_metrics: dict[str, Any] = {}
        mode = mapping["correction_mode"] if mapping else "image_only"
        initial_text = ""
        correction_text = ""
        initial_metrics: dict[str, Any] = {}
        correction_metrics: dict[str, Any] = {}
        errors: list[str] = []
        try:
            raw_initial, initial_metrics = _qwen_call(
                image,
                OCR_PROMPT,
                timeout=args.timeout,
                ollama_base_url=ollama_base_url,
                ssh_settings=ssh_settings,
            )
            initial_text = _redact(raw_initial, display_name, remove_identity_header=True)
        except Exception as error:
            errors.append(f"initial_ocr:{_error_class(error)}")
        if case_id in transcript_futures:
            transcript, transcript_metrics = transcript_futures[case_id].result()
        correction_decision = decide_multimodal_correction(
            initial_text,
            transcript or None,
            mode,
        )
        if initial_text and correction_decision.request_ai_correction:
            try:
                raw_correction, correction_metrics = _qwen_call(
                    image,
                    _correction_prompt(initial_text, transcript or None, mode),
                    timeout=args.timeout,
                    ollama_base_url=ollama_base_url,
                    ssh_settings=ssh_settings,
                )
                correction_text = _redact(
                    raw_correction, display_name, remove_identity_header=True
                )
            except Exception as error:
                errors.append(f"ai_correction_suggestion:{_error_class(error)}")
        reference = case["accuracy_reference_text"]
        initial_evaluation = {
            "normalized_character_accuracy": _character_accuracy(initial_text, reference),
            "preservation": _preservation(initial_text, case["preservation_terms"]),
            "unsupported_numeric_tokens": _unsupported_numeric_tokens(initial_text, reference),
        }
        correction_evaluation = {
            "normalized_character_accuracy": _character_accuracy(correction_text, reference),
            "preservation": _preservation(correction_text, case["preservation_terms"]),
            "unsupported_numeric_tokens": _unsupported_numeric_tokens(correction_text, reference),
        }
        audio_evaluation = None
        if transcript:
            audio_evaluation = {
                "character_accuracy_applicable": mode == "full_reading",
                "normalized_character_accuracy": (
                    _character_accuracy(transcript, reference)
                    if mode == "full_reading"
                    else None
                ),
                "preservation": _preservation(transcript, case["preservation_terms"]),
                "unsupported_numeric_tokens": _unsupported_numeric_tokens(
                    transcript, reference
                ),
                "story_hint_used_as_ground_truth": False,
            }
        revisions = [
            {
                "revision_no": 1,
                "stage": "initial_ocr",
                "status": "completed" if initial_text else "failed",
                "text": initial_text or None,
                "evidence_refs": [image_asset["asset_id"]],
            }
        ]
        if mapping:
            revisions.append(
                {
                    "revision_no": 2,
                    "stage": "audio_transcript",
                    "status": "completed" if transcript else "failed",
                    "text": transcript or None,
                    "correction_mode": mode,
                    "evidence_refs": [mapping["audio_id"]],
                }
            )
        revisions.extend(
            [
                {
                    "revision_no": len(revisions) + 1,
                    "stage": "ai_correction_suggestion",
                    "status": (
                        "completed"
                        if correction_text
                        else "failed"
                        if correction_decision.request_ai_correction
                        else "not_requested"
                    ),
                    "text": correction_text or None,
                    "evidence_refs": [
                        image_asset["asset_id"],
                        *([mapping["audio_id"]] if mapping else []),
                    ],
                },
                {
                    "revision_no": len(revisions) + 2,
                    "stage": "staff_approved_final",
                    "status": "awaiting_staff_approval",
                    "text": None,
                    "evidence_refs": [],
                },
            ]
        )
        results.append(
            {
                "case_id": case_id,
                "correction_mode": mode,
                "initial_ocr": {
                    "provider": "x370d_internal_ollama",
                    "model": QWEN_MODEL,
                    "processing_location": "internal",
                    "text": initial_text or None,
                    "metrics": initial_metrics,
                    "evaluation": initial_evaluation,
                },
                "audio_transcript": {
                    "text": transcript or None,
                    "mode": mode if mapping else None,
                    "metrics": transcript_metrics,
                    "evaluation": audio_evaluation,
                    "ground_truth_authority": False,
                    "supporting_correction_evidence": bool(mapping),
                    "hint_only": mode == "story_hint",
                },
                "ai_correction_suggestion": {
                    "provider": "x370d_internal_ollama",
                    "model": QWEN_MODEL,
                    "text": correction_text or None,
                    "metrics": correction_metrics,
                    "evaluation": correction_evaluation,
                    "officially_accepted": False,
                    "request_decision": correction_decision.as_dict(),
                },
                "staff_approved_final": {
                    "status": "awaiting_staff_approval",
                    "text": None,
                    "accuracy_available": False,
                },
                "revision_history": revisions,
                "errors": errors,
                "privacy": {
                    "synthetic_fixture": True,
                    "official_record": False,
                    "external_transfer_allowed": False,
                    "direct_identifier_saved": False,
                },
                "timing": {
                    "initial_ocr_elapsed_ms": initial_metrics.get("elapsed_ms"),
                    "audio_elapsed_ms": transcript_metrics.get("elapsed_ms"),
                    "correction_elapsed_ms": correction_metrics.get("elapsed_ms"),
                    "case_wall_clock_elapsed_ms": round(
                        (time.monotonic() - case_started) * 1000
                    ),
                    "image_audio_parallelized": bool(mapping),
                },
            }
        )
        print(
            f"[OCR {index}/5] {case_id} initial={bool(initial_text)} correction={bool(correction_text)}",
            file=sys.stderr,
            flush=True,
        )

    transcript_executor.shutdown(wait=True)

    unload_ok = _unload_qwen(
        timeout=args.timeout,
        ollama_base_url=ollama_base_url,
        ssh_settings=ssh_settings,
    )
    category_totals = {
        category: {
            "initial_preserved": sum(
                item["initial_ocr"]["evaluation"]["preservation"][category]["preserved"]
                for item in results
            ),
            "correction_preserved": sum(
                item["ai_correction_suggestion"]["evaluation"]["preservation"][category]["preserved"]
                for item in results
            ),
            "total": sum(
                item["initial_ocr"]["evaluation"]["preservation"][category]["total"]
                for item in results
            ),
        }
        for category in ("time", "quantity", "unit", "completion_status", "negation")
    }
    for values in category_totals.values():
        total = values["total"]
        values["initial_rate"] = round(values["initial_preserved"] / total, 4) if total else None
        values["correction_rate"] = round(values["correction_preserved"] / total, 4) if total else None
    report = {
        "schema_version": "longitudinal_multimodal_internal_validation_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fixture_version": manifest["fixture_version"],
        "result": "completed_with_staff_approval_pending"
        if all(not item["errors"] for item in results)
        else "completed_with_processing_failures",
        "summary": {
            "handwriting_cases": 5,
            "audio_cases": 2,
            "initial_ocr_successes": sum(bool(item["initial_ocr"]["text"]) for item in results),
            "audio_transcription_successes": sum(bool(text) for text, _ in transcripts.values()),
            "correction_suggestion_successes": sum(
                bool(item["ai_correction_suggestion"]["text"]) for item in results
            ),
            "staff_approved_finals": 0,
            "average_initial_character_accuracy": round(
                sum(
                    item["initial_ocr"]["evaluation"]["normalized_character_accuracy"]
                    for item in results
                )
                / len(results),
                4,
            ),
            "average_correction_character_accuracy": round(
                sum(
                    item["ai_correction_suggestion"]["evaluation"]["normalized_character_accuracy"]
                    for item in results
                )
                / len(results),
                4,
            ),
            "average_successful_correction_character_accuracy": round(
                sum(
                    item["ai_correction_suggestion"]["evaluation"]["normalized_character_accuracy"]
                    for item in results
                    if item["ai_correction_suggestion"]["text"]
                )
                / max(
                    1,
                    sum(
                        bool(item["ai_correction_suggestion"]["text"])
                        for item in results
                    ),
                ),
                4,
            ),
            "preservation": category_totals,
            "unsupported_numeric_tokens_after_correction": sum(
                len(
                    item["ai_correction_suggestion"]["evaluation"][
                        "unsupported_numeric_tokens"
                    ]
                )
                for item in results
            ),
            "qwen_unload_ok": unload_ok,
        },
        "cases": results,
        "safety": {
            "ground_truth_sent_to_model": False,
            "external_api_calls": 0,
            "official_record_writes": 0,
            "staff_approval_fabricated": False,
            "source_media_saved_in_report": False,
            "source_paths_saved_in_report": False,
            "direct_identifiers_saved_in_report": False,
            "secrets_or_endpoints_saved_in_report": False,
            "public_production_changes": 0,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "result": report["result"],
                "initial_ocr_successes": report["summary"]["initial_ocr_successes"],
                "audio_transcription_successes": report["summary"]["audio_transcription_successes"],
                "correction_suggestion_successes": report["summary"]["correction_suggestion_successes"],
                "staff_approved_finals": 0,
                "external_api_calls": 0,
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )
    return 0 if report["result"] == "completed_with_staff_approval_pending" else 1


if __name__ == "__main__":
    raise SystemExit(main())
