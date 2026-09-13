from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .ai_settings_store import load_ai_settings
from .config import settings


class SttError(RuntimeError):
    pass


@dataclass(frozen=True)
class SttTranscriptionResult:
    text: str
    model: str
    processing_seconds: float | None
    normalization: dict[str, Any]
    audio_quality: dict[str, Any]


def _safe_service_error(response: httpx.Response) -> str:
    """Return a Korean recovery message without leaking the protected URL."""

    detail = ""
    try:
        detail = str(response.json().get("detail") or "").strip()
    except (ValueError, AttributeError):
        detail = ""
    allowed_prefixes = (
        "빈 음성파일",
        "녹음된 내용",
        "녹음에서 들리는 말소리",
        "녹음 파일 형식",
        "녹음 파일에서 소리 정보",
        "녹음 파일을 안전한 음성 형식",
        "음성에서 확인할 수 있는 말소리",
        "음성 받아쓰기를 완료하지 못했습니다",
    )
    if response.status_code == 422 and detail.startswith(allowed_prefixes):
        return detail
    if response.status_code == 413:
        return "녹음 파일이 너무 큽니다. 짧게 나누어 다시 녹음해 주세요."
    if response.status_code == 401:
        return "내부 음성 받아쓰기 연결을 확인하지 못했습니다. 관리자에게 알려 주세요."
    return "내부 음성 받아쓰기 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."


def _runtime_configuration() -> tuple[bool, str, int]:
    document, protected_settings_exist = load_ai_settings()
    provider = document.providers.get("local_stt")
    if protected_settings_exist and provider is not None:
        return (
            bool(provider.enabled),
            provider.base_url or settings.stt_service_url,
            provider.timeout_seconds or settings.stt_timeout_seconds,
        )
    return (
        settings.stt_enabled,
        settings.stt_service_url,
        settings.stt_timeout_seconds,
    )


def stt_readiness(*, timeout_seconds: float = 2.0) -> dict[str, object]:
    """Probe only service state; never send a recording or protected text."""

    enabled, service_url, _ = _runtime_configuration()
    if not enabled:
        return {
            "ready": False,
            "status": "disabled",
            "message": "내부 음성 받아쓰기가 현재 꺼져 있습니다. 관리자에게 알려 주세요.",
        }
    if settings.stt_shared_token is None:
        return {
            "ready": False,
            "status": "configuration_error",
            "message": "내부 음성 받아쓰기 연결 설정을 확인해 주세요.",
        }
    try:
        response = httpx.get(
            f"{service_url.rstrip('/')}/health",
            timeout=max(0.2, min(timeout_seconds, 3.0)),
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException:
        return {
            "ready": False,
            "status": "timeout",
            "message": "내부 음성 받아쓰기 준비 확인이 지연되고 있습니다. 잠시 뒤 다시 눌러 주세요.",
        }
    except (httpx.HTTPError, ValueError, AttributeError):
        return {
            "ready": False,
            "status": "unavailable",
            "message": "내부 음성 받아쓰기 서비스가 준비되지 않았습니다. 관리자에게 알려 주세요.",
        }
    ready = bool(payload.get("ok")) and bool(payload.get("loaded"))
    return {
        "ready": ready,
        "status": "ready" if ready else "starting",
        "message": (
            "내부 음성 받아쓰기가 준비됐습니다."
            if ready
            else "내부 음성 받아쓰기 모델을 준비하고 있습니다. 잠시 뒤 다시 눌러 주세요."
        ),
    }


def transcribe_audio_with_metadata(
    path: Path,
    *,
    mime_type: str,
    initial_prompt: str | None = None,
    hotwords: str | None = None,
) -> SttTranscriptionResult:
    enabled, service_url, timeout_seconds = _runtime_configuration()
    if not enabled:
        raise SttError("로컬 음성 판독 기능이 꺼져 있습니다.")
    if settings.stt_shared_token is None:
        raise SttError("로컬 음성 판독 연결 암호가 설정되지 않았습니다.")
    if not path.is_file():
        raise SttError("판독할 원본 음성파일을 찾을 수 없습니다.")

    try:
        with path.open("rb") as source:
            response = httpx.post(
                f"{service_url.rstrip('/')}/transcribe",
                headers={
                    "X-STT-Token": settings.stt_shared_token.get_secret_value(),
                },
                files={
                    "file": (
                        path.name,
                        source,
                        mime_type or "application/octet-stream",
                    )
                },
                data={
                    "initial_prompt": (initial_prompt or "")[:6_000],
                    "hotwords": (hotwords or "")[:4_000],
                },
                timeout=timeout_seconds,
            )
        if response.is_error:
            raise SttError(_safe_service_error(response))
        payload = response.json()
    except httpx.ConnectError as exc:
        raise SttError(
            "로컬 음성 판독 서비스가 응답하지 않습니다. "
            "잠시 후 다시 시도하거나 관리자에게 알려 주세요."
        ) from exc
    except httpx.TimeoutException as exc:
        raise SttError("음성 판독 제한시간을 초과했습니다.") from exc
    except SttError:
        raise
    except httpx.HTTPError as exc:
        raise SttError(
            "내부 음성 받아쓰기 요청을 처리하지 못했습니다. 잠시 후 다시 시도해 주세요."
        ) from exc
    except ValueError as exc:
        raise SttError("내부 음성 받아쓰기 응답을 확인하지 못했습니다.") from exc

    text = str(payload.get("text") or "").strip()
    if not text:
        raise SttError("음성에서 확인할 수 있는 말소리를 찾지 못했습니다.")
    processing_seconds = payload.get("duration_seconds")
    return SttTranscriptionResult(
        text=text,
        model=str(payload.get("model") or "local-whisper"),
        processing_seconds=(
            float(processing_seconds)
            if isinstance(processing_seconds, (int, float))
            else None
        ),
        normalization=(
            dict(payload.get("normalization") or {})
            if isinstance(payload.get("normalization"), dict)
            else {}
        ),
        audio_quality=(
            dict(payload.get("audio_quality") or {})
            if isinstance(payload.get("audio_quality"), dict)
            else {
                "status": "unknown",
                "reading_pace": "unknown",
                "message": "음성 전사가 준비됐습니다.",
                "reasons": [],
                "metrics": {},
            }
        ),
    )


def transcribe_audio(
    path: Path,
    *,
    mime_type: str,
    initial_prompt: str | None = None,
    hotwords: str | None = None,
) -> str:
    """Keep the existing text-only contract for maintenance callers."""

    return transcribe_audio_with_metadata(
        path,
        mime_type=mime_type,
        initial_prompt=initial_prompt,
        hotwords=hotwords,
    ).text
