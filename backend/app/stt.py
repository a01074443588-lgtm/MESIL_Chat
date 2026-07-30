from pathlib import Path

import httpx

from .config import settings


class SttError(RuntimeError):
    pass


def transcribe_audio(path: Path, *, mime_type: str) -> str:
    if not settings.stt_enabled:
        raise SttError("로컬 음성 판독 기능이 꺼져 있습니다.")
    if settings.stt_shared_token is None:
        raise SttError("로컬 음성 판독 연결 암호가 설정되지 않았습니다.")
    if not path.is_file():
        raise SttError("판독할 원본 음성파일을 찾을 수 없습니다.")

    try:
        with path.open("rb") as source:
            response = httpx.post(
                f"{settings.stt_service_url.rstrip('/')}/transcribe",
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
                timeout=settings.stt_timeout_seconds,
            )
        response.raise_for_status()
        payload = response.json()
    except httpx.ConnectError as exc:
        raise SttError(
            "사무실 PC의 로컬 음성 판독기가 실행되지 않았습니다. "
            "scripts/start-local-stt.ps1을 먼저 실행해 주세요."
        ) from exc
    except httpx.TimeoutException as exc:
        raise SttError("음성 판독 제한시간을 초과했습니다.") from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise SttError(f"로컬 음성 판독 요청에 실패했습니다: {exc}") from exc

    text = str(payload.get("text") or "").strip()
    if not text:
        raise SttError("음성에서 확인할 수 있는 말소리를 찾지 못했습니다.")
    return text
