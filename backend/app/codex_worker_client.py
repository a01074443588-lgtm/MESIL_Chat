from __future__ import annotations

import base64
from dataclasses import dataclass
import ipaddress
import json
from time import monotonic, sleep
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .ai_assist_schemas import (
    AiAssistResult,
    AiAssistSourceSnapshot,
    AiAssistWorkerEnvelope,
    AiTaskType,
)


MAX_WORKER_RESPONSE_BYTES = 512 * 1024
TASK_MODE_MAP: dict[AiTaskType, str] = {
    "image_text": "image_text",
    "image_explain": "image_explain",
    "audio_summary": "record_assist",
    "summary": "record_assist",
    "history_search": "record_assist",
    "risk_check": "record_assist",
    "question": "question",
}


class CodexWorkerError(RuntimeError):
    code = "codex_worker_error"

    def __init__(self, public_message: str) -> None:
        super().__init__(public_message)
        self.public_message = public_message


class CodexWorkerUnavailable(CodexWorkerError):
    code = "codex_worker_unavailable"


class CodexWorkerRejected(CodexWorkerError):
    code = "codex_worker_rejected"


class CodexWorkerInvalidResponse(CodexWorkerError):
    code = "codex_worker_invalid_response"


@dataclass(frozen=True)
class CodexWorkerAnalysis:
    result: AiAssistResult
    provider: str
    model: str
    prompt_version: str
    elapsed_ms: int


@dataclass(frozen=True)
class CodexWorkerHealth:
    credential_ready: bool
    credential_mode: str
    busy: bool


@dataclass(frozen=True)
class CodexWorkerImage:
    content: bytes
    mime_type: str
    filename: str
    image_no: int


DOCKER_HOST_ALIAS = "host.docker.internal"
DOCKER_SERVICE_HOST = "codex-worker"


def validate_loopback_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "http"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Codex 작업기 주소는 로컬 HTTP 기본주소여야 합니다.")
    if parsed.hostname not in {DOCKER_HOST_ALIAS, DOCKER_SERVICE_HOST}:
        try:
            address = ipaddress.ip_address(parsed.hostname)
        except ValueError as exc:
            raise ValueError(
                "Codex 작업기 주소에는 루프백 IP 또는 승인된 Docker 이름만 "
                "사용할 수 있습니다."
            ) from exc
        if not address.is_loopback:
            raise ValueError(
                "Codex 작업기는 루프백 주소 또는 승인된 Docker 이름으로만 "
                "연결할 수 있습니다."
            )
    if parsed.port is None:
        raise ValueError("Codex 작업기 포트가 필요합니다.")
    return normalized


class CodexWorkerClient:
    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:8767",
        shared_token: str | None = None,
        timeout_seconds: float = 130,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.base_url = validate_loopback_base_url(base_url)
        self.shared_token = (shared_token or "").strip()
        if not 10 <= timeout_seconds <= 610:
            raise ValueError("Codex 작업기 제한시간은 10~610초여야 합니다.")
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def configured(self) -> bool:
        return len(self.shared_token) >= 32

    def health(self) -> CodexWorkerHealth:
        """Verify both the local worker and its Codex credential state.

        A token merely means that the backend knows how to authenticate.  The
        worker may still be stopped or may not have a usable Codex credential,
        so UI readiness must be based on this authenticated health response.
        """

        if not self.configured:
            raise CodexWorkerUnavailable("Codex 작업기 인증이 준비되지 않았습니다.")
        headers = {"X-Codex-Worker-Token": self.shared_token}
        try:
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(min(self.timeout_seconds, 2.0)),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                response = client.get("/health")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CodexWorkerUnavailable(
                "Codex 작업기에 연결할 수 없습니다."
            ) from exc
        except httpx.HTTPError as exc:
            raise CodexWorkerUnavailable(
                "Codex 작업기 상태를 확인하지 못했습니다."
            ) from exc

        if len(response.content) > MAX_WORKER_RESPONSE_BYTES:
            raise CodexWorkerInvalidResponse("Codex 작업기 상태 응답이 너무 큽니다.")
        if response.status_code != 200:
            raise CodexWorkerUnavailable("Codex 작업기가 준비되지 않았습니다.")
        try:
            payload = response.json()
            if (
                payload.get("ok") is not True
                or payload.get("service") != "mesil-local-codex-worker"
                or not isinstance(payload.get("credential_ready"), bool)
                or not isinstance(payload.get("credential_mode"), str)
                or not isinstance(payload.get("busy"), bool)
            ):
                raise ValueError("invalid health payload")
        except (ValueError, TypeError) as exc:
            raise CodexWorkerInvalidResponse(
                "Codex 작업기 상태 응답 형식이 올바르지 않습니다."
            ) from exc
        return CodexWorkerHealth(
            credential_ready=payload["credential_ready"],
            credential_mode=payload["credential_mode"][:80],
            busy=payload["busy"],
        )

    def analyze(
        self,
        *,
        task_type: AiTaskType,
        question: str,
        sources: list[AiAssistSourceSnapshot],
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        images: list[CodexWorkerImage] | None = None,
    ) -> CodexWorkerAnalysis:
        if not self.configured:
            raise CodexWorkerUnavailable("Codex 작업기 인증이 준비되지 않았습니다.")
        payload: dict[str, Any] = {
            "mode": TASK_MODE_MAP[task_type],
            "question": question,
            "context": {
                "task_type": task_type,
                "sources": [source.model_dump(mode="json") for source in sources],
            },
        }
        if images and image_bytes is not None:
            raise CodexWorkerRejected("이미지 입력 형식이 중복되었습니다.")
        if images:
            payload["images"] = []
            for image in images:
                if image.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                    raise CodexWorkerRejected("지원하지 않는 이미지 형식입니다.")
                payload["images"].append(
                    {
                        "image_no": image.image_no,
                        "filename": image.filename,
                        "mime_type": image.mime_type,
                        "data_base64": base64.b64encode(image.content).decode("ascii"),
                    }
                )
        elif image_bytes is not None:
            if image_mime_type not in {"image/jpeg", "image/png", "image/webp"}:
                raise CodexWorkerRejected("지원하지 않는 이미지 형식입니다.")
            payload["image"] = {
                "mime_type": image_mime_type,
                "data_base64": base64.b64encode(image_bytes).decode("ascii"),
            }
        encoded = json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Codex-Worker-Token": self.shared_token,
        }
        try:
            deadline = monotonic() + self.timeout_seconds
            retry_delay = 0.5
            with httpx.Client(
                base_url=self.base_url,
                headers=headers,
                timeout=httpx.Timeout(self.timeout_seconds),
                follow_redirects=False,
                trust_env=False,
                transport=self.transport,
            ) as client:
                while True:
                    response = client.post("/analyze", content=encoded)
                    is_busy = False
                    if response.status_code == 429:
                        try:
                            is_busy = response.json().get("code") == "worker_busy"
                        except (ValueError, TypeError):
                            is_busy = False
                    if not is_busy:
                        break
                    remaining = deadline - monotonic()
                    if remaining <= retry_delay:
                        raise CodexWorkerUnavailable(
                            "AI 작업이 밀려 있습니다. 잠시 후 다시 시도해 주세요."
                        )
                    sleep(retry_delay)
                    retry_delay = min(2.0, retry_delay * 2)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise CodexWorkerUnavailable(
                "Codex 작업기에 연결할 수 없습니다."
            ) from exc
        except httpx.HTTPError as exc:
            raise CodexWorkerUnavailable(
                "Codex 작업기 요청을 완료하지 못했습니다."
            ) from exc

        if len(response.content) > MAX_WORKER_RESPONSE_BYTES:
            raise CodexWorkerInvalidResponse("Codex 작업기 응답이 너무 큽니다.")
        if response.status_code in {408, 425, 429, 502, 503, 504}:
            raise CodexWorkerUnavailable("Codex 작업기가 현재 응답할 수 없습니다.")
        if response.status_code != 200:
            raise CodexWorkerRejected("Codex 작업기가 요청을 처리하지 못했습니다.")
        try:
            envelope = AiAssistWorkerEnvelope.model_validate(response.json())
        except (ValueError, ValidationError) as exc:
            raise CodexWorkerInvalidResponse(
                "Codex 작업기 결과 형식이 올바르지 않습니다."
            ) from exc
        return CodexWorkerAnalysis(
            result=envelope.result,
            provider=envelope.provider,
            model=envelope.model,
            prompt_version=envelope.prompt_version,
            elapsed_ms=envelope.elapsed_ms,
        )
