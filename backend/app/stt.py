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


_EXPECTED_STT_DEVICE = "cuda"
_EXPECTED_STT_COMPUTE_TYPE = "float16"


def _runtime_configuration() -> tuple[bool, str, int, str]:
    document, protected_settings_exist = load_ai_settings()
    provider = document.providers.get("local_stt")
    if protected_settings_exist and provider is not None:
        return (
            bool(provider.enabled),
            provider.base_url or settings.stt_service_url,
            provider.timeout_seconds or settings.stt_timeout_seconds,
            provider.model
            or (
                document.model_roles.stt.model
                if document.model_roles.stt is not None
                and document.model_roles.stt.provider == "local_stt"
                else settings.stt_model
            ),
        )
    return (
        settings.stt_enabled,
        settings.stt_service_url,
        settings.stt_timeout_seconds,
        settings.stt_model,
    )


def stt_runtime_enabled() -> bool:
    """Use the same protected runtime source as readiness and transcription."""

    enabled, _, _, _ = _runtime_configuration()
    return bool(enabled)


def _runtime_status(payload: object, *, expected_model: str) -> str:
    """Classify the internal STT runtime without accepting truthy lookalikes."""

    if not isinstance(payload, dict):
        return "unavailable"
    if payload.get("ok") is not True:
        return "unavailable"
    if payload.get("loaded") is not True:
        return "starting" if payload.get("loaded") is False else "runtime_mismatch"
    if (
        payload.get("runtime_verified") is not True
        or payload.get("model") != expected_model
        or payload.get("device") != _EXPECTED_STT_DEVICE
        or payload.get("compute_type") != _EXPECTED_STT_COMPUTE_TYPE
    ):
        return "runtime_mismatch"
    return "ready"


def _runtime_failure_message(status: str) -> str:
    return {
        "starting": "내부 음성 받아쓰기 모델을 준비하고 있습니다. 잠시 뒤 다시 눌러 주세요.",
        "timeout": "내부 음성 받아쓰기 준비 확인이 지연되고 있습니다. 잠시 뒤 다시 눌러 주세요.",
        "configuration_error": "내부 음성 받아쓰기 연결 설정을 확인해 주세요.",
        "runtime_mismatch": "내부 음성 받아쓰기 실행 구성을 확인하지 못했습니다. 관리자에게 알려 주세요.",
    }.get(
        status,
        "내부 음성 받아쓰기 서비스가 준비되지 않았습니다. 관리자에게 알려 주세요.",
    )


def _read_runtime_status(
    service_url: str,
    *,
    expected_model: str,
    timeout_seconds: float,
) -> str:
    try:
        response = httpx.get(
            f"{service_url.rstrip('/')}/health",
            timeout=max(0.2, min(timeout_seconds, 3.0)),
        )
        response.raise_for_status()
        payload = response.json()
    except httpx.TimeoutException:
        return "timeout"
    except (httpx.HTTPError, ValueError, AttributeError):
        return "unavailable"
    return _runtime_status(payload, expected_model=expected_model)


def _token_probe(service_url: str, *, timeout_seconds: float) -> str | None:
    """Verify the shared token with an empty, non-recording request.

    The STT service authenticates before inspecting the upload and returns its
    stable empty-audio validation error only for a valid token.
    """

    assert settings.stt_shared_token is not None
    try:
        response = httpx.post(
            f"{service_url.rstrip('/')}/transcribe",
            headers={"X-STT-Token": settings.stt_shared_token.get_secret_value()},
            files={"file": ("readiness.wav", b"", "audio/wav")},
            timeout=max(0.2, min(timeout_seconds, 3.0)),
        )
    except httpx.TimeoutException:
        return "timeout"
    except httpx.HTTPError:
        return "unavailable"
    if response.status_code == 401:
        return "configuration_error"
    try:
        detail = str(response.json().get("detail") or "")
    except (ValueError, AttributeError):
        detail = ""
    if response.status_code == 422 and detail.startswith("빈 음성파일"):
        return None
    return "unavailable"


def stt_readiness(*, timeout_seconds: float = 2.0) -> dict[str, object]:
    """Probe service state and token without sending a recording or protected text."""

    enabled, service_url, _, expected_model = _runtime_configuration()
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
    status = _read_runtime_status(
        service_url,
        expected_model=expected_model,
        timeout_seconds=timeout_seconds,
    )
    if status == "ready":
        token_status = _token_probe(service_url, timeout_seconds=timeout_seconds)
        if token_status is not None:
            return {
                "ready": False,
                "status": token_status,
                "message": _runtime_failure_message(token_status),
            }
    return {
        "ready": status == "ready",
        "status": status,
        "message": (
            "내부 음성 받아쓰기가 준비됐습니다."
            if status == "ready"
            else _runtime_failure_message(status)
        ),
    }


def transcribe_audio_with_metadata(
    path: Path,
    *,
    mime_type: str,
    initial_prompt: str | None = None,
    hotwords: str | None = None,
) -> SttTranscriptionResult:
    enabled, service_url, timeout_seconds, expected_model = _runtime_configuration()
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

    if not isinstance(payload, dict):
        raise SttError("내부 음성 받아쓰기 응답을 확인하지 못했습니다.")
    if payload.get("model") != expected_model:
        raise SttError(
            "내부 음성 받아쓰기 실행 구성을 확인하지 못했습니다. 관리자에게 알려 주세요."
        )
    response_runtime_fields = {"device", "compute_type", "runtime_verified"}
    if response_runtime_fields & payload.keys() and (
        payload.get("device") != _EXPECTED_STT_DEVICE
        or payload.get("compute_type") != _EXPECTED_STT_COMPUTE_TYPE
        or payload.get("runtime_verified") is not True
    ):
        raise SttError(
            "내부 음성 받아쓰기 실행 구성을 확인하지 못했습니다. 관리자에게 알려 주세요."
        )
    runtime_status = _read_runtime_status(
        service_url,
        expected_model=expected_model,
        timeout_seconds=min(float(timeout_seconds), 3.0),
    )
    if runtime_status != "ready":
        raise SttError(_runtime_failure_message(runtime_status))
    text = str(payload.get("text") or "").strip()
    if not text:
        raise SttError("음성에서 확인할 수 있는 말소리를 찾지 못했습니다.")
    processing_seconds = payload.get("duration_seconds")
    return SttTranscriptionResult(
        text=text,
        model=expected_model,
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
