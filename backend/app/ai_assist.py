from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
from time import perf_counter
from typing import Any, Protocol, Sequence
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Response, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from .ai_assist_models import (
    AI_TASK_TYPES,
    AI_TERMINAL_STATUSES,
    AiAssistConversation,
    AiAssistProviderAttempt,
    AiAssistSource,
    AiAssistTurn,
)
from .ai_assist_schemas import (
    AiAssistConfigResponse,
    AiProviderStatusResponse,
    AiAssistResult,
    AiAssistShareResponse,
    AiAssistSourceSnapshot,
    AiAssistTaskRequest,
    AiAssistTurnResponse,
    AiConversationDetail,
    AiEvidence,
    AiResidentNameCandidate,
    AiServiceContext,
    AiTaskType,
)
from .ai_provider_status import collect_ai_provider_status
from .ai_hardware import detect_ai_hardware
from .ai_provider_adapters import (
    ProviderAdapterError,
    collect_provider_public_statuses,
    generate_ai_assist_result,
)
from .ai_router import build_routing_preview
from .ai_settings_store import load_ai_settings
from .ai_system_schemas import AiRoutingPreviewRequest
from .codex_worker_client import (
    CodexWorkerAnalysis,
    CodexWorkerClient,
    CodexWorkerError,
    CodexWorkerHealth,
    CodexWorkerImage,
    CodexWorkerInvalidResponse,
    validate_loopback_base_url,
)
from .config import settings as app_settings
from .attachment_review_policy import attachment_evidence_text, requires_staff_review
from .database import SessionLocal, get_db
from .dependencies import get_current_user, require_admin
from .finals_readiness import endpoint_scope
from .local_ai import (
    LocalAiError,
    RoomSummaryResult,
    local_summary_provider_status,
    nemotron_is_configured,
    summarize_room_messages,
)
from .models import (
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    MessageReadReceipt,
    MessageResidentLink,
    OcrCorrectionMemory,
    Resident,
    Room,
    RoomMembership,
    Staff,
    User,
)
from .ocr import (
    OcrError,
    extract_report_text,
    find_spelling_candidates,
    get_ai_lexicon_context,
)
from .push import send_web_push_to_users
from .realtime import manager
from .services import message_response, room_member_user_ids



@asynccontextmanager
async def _ai_assist_lifespan(_app: Any):
    with SessionLocal() as db:
        recover_stale_ai_turns(db)
    yield


router = APIRouter(tags=["ai-assist"], lifespan=_ai_assist_lifespan)

MAX_CONTEXT_TEXT_CHARS = 125_000
MAX_CODEX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_CODEX_IMAGES = 10
SHARED_MESSAGE_MAX_CHARS = 2000
FALLBACK_MODEL = "deterministic-rules-v1"
FALLBACK_REASON_MESSAGES = {
    "key_unconfigured": "이전 공급자의 보호 키가 설정되지 않아 다음 경로로 전환했습니다.",
    "authentication_failed": "이전 공급자 인증에 실패해 다음 경로로 전환했습니다.",
    "quota_exceeded": "이전 공급자의 사용 한도를 초과해 다음 경로로 전환했습니다.",
    "rate_limited": "이전 공급자의 요청 제한으로 다음 경로로 전환했습니다.",
    "timeout": "이전 공급자가 제한시간 안에 응답하지 않아 다음 경로로 전환했습니다.",
    "provider_unavailable": "이전 공급자를 사용할 수 없어 다음 경로로 전환했습니다.",
    "unsupported_input": "이전 공급자가 입력 형식을 지원하지 않아 다음 경로로 전환했습니다.",
    "contract_validation_failed": "이전 결과가 구조화 계약을 통과하지 못해 다음 경로로 전환했습니다.",
    "privacy_blocked": "개인정보 보호 정책에 따라 외부 경로를 차단하고 보호 경로로 전환했습니다.",
    "local_service_unavailable": "이전 내부 AI를 사용할 수 없어 안전한 기본 경로로 전환했습니다.",
    "invalid_response": "이전 이미지 결과가 품질 기준을 통과하지 못해 다음 경로로 전환했습니다.",
    "time_budget_exhausted": "전체 처리 제한시간에 도달해 안전한 기본 경로로 전환했습니다.",
    "attempt_limit_exhausted": "허용된 AI 시도 횟수를 모두 사용해 안전한 기본 경로로 전환했습니다.",
}
LOCAL_AI_PROMPT_VERSION = "local-ai-summary-v1"
NO_STORE_CACHE_CONTROL = "private, no-store"
STALE_AI_TURN_MINUTES = 10

DEFAULT_QUESTIONS: dict[AiTaskType, str] = {
    "image_text": "첨부 이미지에 보이는 글자를 그대로 읽어 주세요.",
    "image_explain": "첨부 이미지의 내용을 업무 관점에서 설명해 주세요.",
    "audio_summary": (
        "첨부된 음성의 받아쓰기를 파일 순서와 파일명 구분을 유지해 "
        "종합 정리해 주세요."
    ),
    "summary": "원본 메시지와 근거자료를 요약해 주세요.",
    "history_search": "원본 메시지와 관련된 업무 이력을 찾아 정리해 주세요.",
    "risk_check": "원본 메시지에서 주의해야 할 위험과 확인사항을 정리해 주세요.",
    "question": "원본 메시지와 근거자료를 확인해 주세요.",
}

ALLOWED_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "queued": frozenset({"preparing", "cancelled", "failed"}),
    "preparing": frozenset({"extracting", "cancelled", "failed"}),
    "extracting": frozenset({"searching", "cancelled", "failed"}),
    "searching": frozenset({"reasoning", "cancelled", "failed"}),
    "reasoning": frozenset({"validating", "cancelled", "failed"}),
    "validating": frozenset({"completed", "cancelled", "failed"}),
    "completed": frozenset(),
    "failed": frozenset(),
    "cancelled": frozenset(),
}


class WorkerClient(Protocol):
    @property
    def configured(self) -> bool: ...

    def analyze(
        self,
        *,
        task_type: AiTaskType,
        question: str,
        sources: list[AiAssistSourceSnapshot],
        image_bytes: bytes | None = None,
        image_mime_type: str | None = None,
        images: list[CodexWorkerImage] | None = None,
    ) -> CodexWorkerAnalysis: ...

    def health(self) -> CodexWorkerHealth: ...


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be true or false.")


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number.") from exc
    if not minimum <= value <= maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}.")
    return value


@dataclass(frozen=True)
class AiAssistRuntimeSettings:
    allow_external_real_data: bool = False
    allow_external_real_image_data: bool = False
    worker_url: str = "http://127.0.0.1:8767"
    worker_token: str = ""
    worker_timeout_seconds: float = 130

    @classmethod
    def from_env(cls) -> "AiAssistRuntimeSettings":
        return cls(
            allow_external_real_data=_env_bool(
                "AI_ASSIST_ALLOW_EXTERNAL_REAL_DATA",
                False,
            ),
            allow_external_real_image_data=(
                app_settings.ai_assist_allow_external_real_image_data
            ),
            worker_url=os.environ.get(
                "AI_ASSIST_CODEX_WORKER_URL",
                "http://127.0.0.1:8767",
            ).strip(),
            worker_token=os.environ.get("CODEX_WORKER_SHARED_TOKEN", "").strip(),
            worker_timeout_seconds=_env_float(
                "AI_ASSIST_CODEX_TIMEOUT_SECONDS",
                130,
                10,
                610,
            ),
        )

    def build_worker_client(self) -> CodexWorkerClient:
        return CodexWorkerClient(
            base_url=self.worker_url,
            shared_token=self.worker_token,
            timeout_seconds=self.worker_timeout_seconds,
        )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _forbidden(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)


def _require_ai_assist_user(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin" and not user.can_process_records:
        raise _forbidden("기록관리 권한이 있는 직원만 AI 확인을 사용할 수 있습니다.")
    return user


def _not_found(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)


def _set_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = NO_STORE_CACHE_CONTROL


def _can_search_confirmed_records(user: User | None) -> bool:
    return bool(
        user is not None
        and (user.role == "admin" or user.can_process_records)
    )


def _local_ai_endpoint_is_safe() -> bool:
    provider = app_settings.ai_review_provider.strip().lower()
    if provider == "stub":
        return True
    if provider not in {"chain", "ollama", "local"}:
        return True
    try:
        validate_loopback_base_url(app_settings.ai_review_base_url)
    except ValueError:
        parsed = urlsplit(app_settings.ai_review_base_url.strip().rstrip("/"))
        return bool(
            parsed.scheme == "http"
            and parsed.hostname == "localhost"
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    return True


def ensure_room_permission(db: Session, user: User, room_id: UUID) -> Room:
    room = db.get(Room, room_id)
    if room is None or room.organization_id != user.organization_id or not room.is_active:
        raise _not_found("대화방을 찾을 수 없습니다.")
    if user.staff_id is None:
        raise _forbidden("직원 소속정보를 확인할 수 없습니다.")
    membership = db.scalar(
        select(RoomMembership.id).where(
            RoomMembership.organization_id == user.organization_id,
            RoomMembership.room_id == room.id,
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
    )
    if membership is None:
        raise _forbidden("이 대화방의 AI 도움을 사용할 권한이 없습니다.")
    return room


def _message_for_user(db: Session, user: User, message_id: UUID) -> Message:
    message = db.get(Message, message_id)
    if message is None or message.organization_id != user.organization_id:
        raise _not_found("원본 메시지를 찾을 수 없습니다.")
    ensure_room_permission(db, user, message.room_id)
    if message.lifecycle_status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="회수된 메시지는 AI 도움에 사용할 수 없습니다.",
        )
    return message


def get_owned_conversation(
    db: Session,
    user: User,
    conversation_id: UUID,
    *,
    for_update: bool = False,
) -> AiAssistConversation:
    statement = select(AiAssistConversation).where(
        AiAssistConversation.id == conversation_id,
        AiAssistConversation.organization_id == user.organization_id,
    )
    if for_update:
        statement = statement.with_for_update()
    conversation = db.scalar(statement)
    if conversation is None:
        raise _not_found("AI 대화를 찾을 수 없습니다.")
    if conversation.owner_user_id != user.id:
        raise _forbidden("다른 직원의 AI 질문은 열 수 없습니다.")
    if conversation.room_id is not None:
        ensure_room_permission(db, user, conversation.room_id)
    return conversation


def get_owned_turn(
    db: Session,
    user: User,
    turn_id: UUID,
    *,
    for_update: bool = False,
) -> AiAssistTurn:
    statement = (
        select(AiAssistTurn)
        .join(AiAssistConversation)
        .where(
            AiAssistTurn.id == turn_id,
            AiAssistTurn.organization_id == user.organization_id,
        )
    )
    if for_update:
        statement = statement.with_for_update()
    turn = db.scalar(statement)
    if turn is None:
        raise _not_found("AI 작업을 찾을 수 없습니다.")
    get_owned_conversation(db, user, turn.conversation_id)
    if not _turn_review_sources_current(db, turn):
        raise HTTPException(409, "근거의 직원 확인 상태가 변경되었습니다. 원본 확인 후 AI 작업을 다시 실행해 주세요.")
    return turn


def _turn_review_sources_current(db, turn):
    for source in turn.sources:
        if source.attachment_id is None:
            continue
        attachment = db.get(MessageAttachment, source.attachment_id, populate_existing=True)
        if attachment is not None and requires_staff_review(attachment):
            db.expire(attachment, ["text_extraction"])
            current = attachment_evidence_text(attachment)
            if not current or current[:12_000] != (source.content_text or ""):
                return False
    return True


def _conversation_message_active(
    db: Session,
    conversation: AiAssistConversation,
) -> bool:
    lifecycle_status = db.scalar(
        select(Message.lifecycle_status).where(
            Message.id == conversation.message_id,
            Message.organization_id == conversation.organization_id,
        )
    )
    return lifecycle_status == "active"


def _clear_turn_result(turn: AiAssistTurn) -> None:
    turn.answer = None
    turn.visible_text = None
    turn.evidence = []
    turn.uncertainties = []
    turn.recommended_actions = []
    turn.result_payload = None
    turn.result_validated = False
    turn.share_ready_at = None


def _cancel_for_recalled_source(db: Session, turn: AiAssistTurn) -> None:
    """Cancel unfinished work while preserving completed rows for audit.

    Completed results stay in the database as an audit record, but response
    builders redact them whenever their source message is no longer active.
    """

    if turn.status in AI_TERMINAL_STATUSES:
        return
    _clear_turn_result(turn)
    turn.status = "cancelled"
    turn.cancelled_at = _now()
    turn.error_code = "source_message_recalled"
    turn.error_message = "원본 메시지가 회수되어 AI 작업을 취소했습니다."
    turn.updated_at = _now()
    turn.conversation.status = "cancelled"
    turn.conversation.updated_at = _now()
    db.commit()


def _processing_may_continue(db: Session, turn: AiAssistTurn) -> bool:
    db.refresh(turn)
    if turn.status == "cancelled":
        if turn.result_validated or turn.answer or turn.result_payload:
            _clear_turn_result(turn)
            db.commit()
        return False
    if turn.status in AI_TERMINAL_STATUSES:
        return False
    if not _conversation_message_active(db, turn.conversation):
        _cancel_for_recalled_source(db, turn)
        return False
    if not _turn_review_sources_current(db, turn):
        _clear_turn_result(turn)
        turn.status = "cancelled"
        turn.error_code = "staff_review_changed"
        turn.error_message = "근거의 직원 확인 상태가 변경되었습니다."
        db.commit()
        return False
    return True


def _attachments_for_task(
    db: Session,
    message: Message,
    task: AiAssistTaskRequest,
) -> list[MessageAttachment]:
    selected: MessageAttachment | None = None
    if task.attachment_id is not None:
        selected = db.get(MessageAttachment, task.attachment_id)
        if (
            selected is None
            or selected.organization_id != message.organization_id
            or selected.message_id != message.id
        ):
            raise _not_found("이 메시지의 첨부파일을 찾을 수 없습니다.")

    expected_prefix = None
    if task.task_type in {"image_text", "image_explain"}:
        expected_prefix = "image/"
    elif task.task_type == "audio_summary":
        expected_prefix = "audio/"
    if expected_prefix is None:
        return [selected] if selected is not None else []
    if selected is not None and not selected.mime_type.startswith(expected_prefix):
        kind = "이미지" if expected_prefix == "image/" else "음성"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"선택한 첨부파일은 {kind} 파일이 아닙니다.",
        )

    query = (
        select(MessageAttachment)
        .where(
            MessageAttachment.message_id == message.id,
            MessageAttachment.mime_type.startswith(expected_prefix),
        )
        .order_by(
            MessageAttachment.upload_ordinal,
            MessageAttachment.created_at,
            MessageAttachment.id,
        )
    )
    if expected_prefix == "audio/" and selected is not None:
        return [selected]
    attachments = list(db.scalars(query))
    if not attachments:
        kind = "이미지" if expected_prefix == "image/" else "음성"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"확인할 {kind} 첨부파일을 선택해 주세요.",
        )
    if len(attachments) > MAX_CODEX_IMAGES:
        kind = "이미지는" if expected_prefix == "image/" else "음성파일은"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{kind} 한 번에 최대 {MAX_CODEX_IMAGES}개까지 확인할 수 있습니다.",
        )
    return attachments


def _text_sha256(value: str | None) -> str | None:
    if not value:
        return None
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _attachment_extracted_text(db: Session, attachment_id: UUID) -> str | None:
    extraction = db.scalar(
        select(AttachmentTextExtraction).where(
            AttachmentTextExtraction.attachment_id == attachment_id
        )
    )
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        return None
    attachment = db.get(MessageAttachment, attachment_id)
    return attachment_evidence_text(attachment, extraction) or None if attachment is not None else None


VALID_SERVICE_CONTEXTS = frozenset({"facility", "daycare", "homecare"})


def _resident_is_explicit_test_data(resident: Resident) -> bool:
    # Carefor imports in this DEV database historically inherited the old
    # environment-wide test flag. Their source identity is authoritative.
    return bool(
        resident.is_test_data is True
        and not resident.internal_code.startswith("SMCODI:carefor:")
    )


def _message_is_explicit_test_data(
    db: Session,
    message: Message,
    attachments: Sequence[MessageAttachment] = (),
) -> bool:
    """Return true only when every actor and referenced person is test data.

    Older DEV rows can carry ``message.is_test_data=True`` even after real staff
    started using the room.  A single legacy flag must never authorize external
    transmission of a real employee, uploader, or resident record.
    """

    if message.is_test_data is not True:
        return False
    room = db.get(Room, message.room_id)
    if (
        room is None
        or room.organization_id != message.organization_id
        or room.is_test_data is not True
    ):
        return False
    sender = db.get(User, message.sender_id)
    sender_staff = db.get(Staff, sender.staff_id) if sender and sender.staff_id else None
    if (
        sender is None
        or sender.organization_id != message.organization_id
        or sender_staff is None
        or sender_staff.organization_id != message.organization_id
        or sender_staff.is_test_data is not True
    ):
        return False
    for attachment in attachments:
        uploader = db.get(User, attachment.uploader_id)
        uploader_staff = (
            db.get(Staff, uploader.staff_id)
            if uploader is not None and uploader.staff_id is not None
            else None
        )
        if (
            attachment.organization_id != message.organization_id
            or attachment.message_id != message.id
            or uploader is None
            or uploader.organization_id != message.organization_id
            or uploader_staff is None
            or uploader_staff.organization_id != message.organization_id
            or uploader_staff.is_test_data is not True
        ):
            return False
    referenced_resident_ids: set[UUID] = set()
    if message.resident_id is not None:
        referenced_resident_ids.add(message.resident_id)
    referenced_resident_ids.update(
        db.scalars(
            select(MessageResidentLink.resident_id).where(
                MessageResidentLink.organization_id == message.organization_id,
                MessageResidentLink.message_id == message.id,
                MessageResidentLink.status != "rejected",
            )
        )
    )
    for resident_id in referenced_resident_ids:
        resident = db.get(Resident, resident_id)
        if (
            resident is None
            or resident.organization_id != message.organization_id
            or not _resident_is_explicit_test_data(resident)
        ):
            return False
    return True


def _resident_service_context(
    db: Session,
    organization_id: UUID,
    resident_id: UUID | None,
) -> AiServiceContext | None:
    if resident_id is None:
        return None
    resident = db.get(Resident, resident_id)
    if (
        resident is None
        or resident.organization_id != organization_id
        or resident.service_type not in VALID_SERVICE_CONTEXTS
    ):
        return None
    return resident.service_type  # type: ignore[return-value]


def _resolve_service_context(
    db: Session,
    message: Message,
    requested: AiServiceContext | None,
    *,
    previous: AiServiceContext | None = None,
) -> tuple[AiServiceContext | None, str | None, str | None]:
    """Resolve an explicit, record-backed service scope without guessing."""

    if requested is not None:
        return requested, "request", None

    direct = _resident_service_context(
        db,
        message.organization_id,
        message.resident_id,
    )
    if direct is not None:
        return direct, "message_resident", None

    confirmed_ids = list(
        db.scalars(
            select(MessageResidentLink.resident_id).where(
                MessageResidentLink.organization_id == message.organization_id,
                MessageResidentLink.message_id == message.id,
                MessageResidentLink.status == "confirmed",
            )
        )
    )
    confirmed_contexts = {
        context
        for resident_id in confirmed_ids
        if (
            context := _resident_service_context(
                db,
                message.organization_id,
                resident_id,
            )
        )
        is not None
    }
    if len(confirmed_contexts) == 1:
        return confirmed_contexts.pop(), "confirmed_resident", None
    if len(confirmed_contexts) > 1:
        return (
            None,
            "all_active_services",
            "여러 서비스가 연결되어 시설·주간보호·방문요양의 현재 이용자 명단을 함께 확인합니다.",
        )

    room = db.get(Room, message.room_id)
    if room is not None and room.organization_id == message.organization_id:
        if room.resident_scope in VALID_SERVICE_CONTEXTS:
            return room.resident_scope, "room_scope", None  # type: ignore[return-value]
        if room.resident_scope == "floor":
            return "facility", "room_floor_scope", None

    # Reusing an earlier explicit/record-backed scope in the same AI conversation
    # is deterministic; an ``all`` room by itself never chooses a service.
    if previous in VALID_SERVICE_CONTEXTS:
        return previous, "previous_turn", None
    return (
        None,
        "all_active_services",
        "서비스 구분을 정할 수 없어 시설·주간보호·방문요양의 현재 이용자 명단을 함께 확인합니다.",
    )


def _turn_service_metadata(turn: AiAssistTurn) -> dict[str, Any]:
    for source in sorted(turn.sources, key=lambda item: item.ordinal):
        if source.source_type == "message":
            return dict(source.source_meta or {})
    return {}


def _normalize_resident_name(value: str) -> str:
    return re.sub(r"\s*\((?:가명|시험[^)]*)\)\s*$", "", value).strip()


def _active_service_residents(
    db: Session,
    organization_id: UUID,
    service_context: AiServiceContext | None,
) -> list[Resident]:
    statement = select(Resident).where(
        Resident.organization_id == organization_id,
        Resident.service_type.in_(VALID_SERVICE_CONTEXTS),
        Resident.status == "active",
        Resident.is_active.is_(True),
    )
    if service_context is not None:
        statement = statement.where(Resident.service_type == service_context)
    return list(
        db.scalars(
            statement.order_by(
                Resident.service_type,
                Resident.sort_order,
                Resident.display_name,
                Resident.id,
            )
        )
    )


def _resident_name_candidates(
    db: Session,
    turn: AiAssistTurn,
    visible_text: str,
    service_context: AiServiceContext | None,
) -> list[dict[str, Any]]:
    """Build internal roster suggestions without modifying or exporting OCR text."""

    residents = _active_service_residents(
        db,
        turn.organization_id,
        service_context,
    )
    residents_by_name: dict[str, list[Resident]] = {}
    for resident in residents:
        normalized = _normalize_resident_name(resident.display_name)
        if normalized:
            residents_by_name.setdefault(normalized, []).append(resident)
    if not residents_by_name:
        return []

    spelling_candidates = find_spelling_candidates(
        visible_text,
        preferred_terms=list(residents_by_name),
    )
    result: list[dict[str, Any]] = []
    for spelling in spelling_candidates:
        recognized = spelling.get("recognized", "").strip()
        candidate = spelling.get("candidate", "").strip()
        matched_residents = residents_by_name.get(candidate, [])
        if not recognized or not matched_residents:
            continue
        confidence = round(SequenceMatcher(None, recognized, candidate).ratio(), 3)
        for resident in matched_residents:
            result.append(
                {
                    "recognized": recognized,
                    "candidate": candidate,
                    "resident_id": str(resident.id),
                    "service_type": resident.service_type,
                    "confidence": confidence,
                }
            )
            if len(result) >= 8:
                return result
    return result


def _local_ocr_endpoint_is_safe() -> bool:
    provider = app_settings.ocr_provider.strip().lower()
    if provider == "stub":
        return True
    if provider != "ollama":
        return False
    try:
        validate_loopback_base_url(app_settings.ocr_base_url)
    except ValueError:
        parsed = urlsplit(app_settings.ocr_base_url.strip().rstrip("/"))
        return bool(
            parsed.scheme == "http"
            and parsed.hostname == "localhost"
            and parsed.port is not None
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        )
    return True


def _ensure_local_image_text_source(
    db: Session,
    turn: AiAssistTurn,
    message: Message,
    attachment: MessageAttachment | None,
) -> None:
    """Create one local OCR source when an ordinary shared image has none."""

    if (
        turn.task_type != "image_text"
        or attachment is None
        or attachment.organization_id != turn.organization_id
        or attachment.message_id != message.id
        or not attachment.mime_type.startswith("image/")
    ):
        return
    source = next(
        (
            item
            for item in turn.sources
            if item.source_type == "attachment"
            and item.attachment_id == attachment.id
        ),
        None,
    )
    existing_text = _attachment_extracted_text(db, attachment.id)
    if existing_text:
        if source is not None and not source.content_text:
            source.content_text = existing_text[:12_000]
            source.content_sha256 = _text_sha256(source.content_text)
        return

    extraction = db.scalar(
        select(AttachmentTextExtraction).where(
            AttachmentTextExtraction.attachment_id == attachment.id
        )
    )
    # pending/processing/failed means another extraction was already requested
    # or the one allowed attempt has a recorded result. Do not create a loop.
    if extraction is not None:
        return

    extraction = AttachmentTextExtraction(
        organization_id=turn.organization_id,
        attachment_id=attachment.id,
        status="processing",
        provider=app_settings.ocr_provider[:40],
        model_name=app_settings.ocr_model[:120],
        requested_by_id=turn.requested_by_user_id,
        started_at=_now(),
    )
    db.add(extraction)
    if source is not None:
        source.source_meta = {
            **(source.source_meta or {}),
            "local_ocr_created_for_turn_id": str(turn.id),
        }
    db.flush()
    try:
        if not _local_ocr_endpoint_is_safe():
            raise OcrError("로컬 OCR 주소가 안전한 내부 주소가 아닙니다.")
        upload_dir = Path(app_settings.upload_dir).resolve()
        target = (upload_dir / attachment.storage_key).resolve()
        if target.parent != upload_dir or not target.is_file():
            raise OcrError("판독할 원본 첨부파일을 찾을 수 없습니다.")
        linked_resident = (
            db.get(Resident, message.resident_id)
            if message.resident_id is not None
            else None
        )
        room = db.get(Room, message.room_id)
        extracted_text = extract_report_text(
            target,
            room_name=room.name if room is not None else "대화방",
            resident_name=(
                linked_resident.display_name if linked_resident is not None else None
            ),
        ).strip()[:12_000]
        if not extracted_text:
            raise OcrError("이미지에서 판독된 글이 없습니다.")
    except OcrError as exc:
        extraction.status = "failed"
        extraction.error_message = str(exc)[:500]
        extraction.completed_at = _now()
        return
    except Exception:
        extraction.status = "failed"
        extraction.error_message = "로컬 OCR 작업을 완료하지 못했습니다."
        extraction.completed_at = _now()
        return

    extraction.status = "completed"
    extraction.extracted_text = extracted_text
    extraction.original_extracted_text = extracted_text
    extraction.error_message = None
    extraction.completed_at = _now()
    if source is not None:
        source.content_text = extracted_text
        source.content_sha256 = _text_sha256(extracted_text)


def _persist_attachment_visible_text(
    db: Session,
    turn: AiAssistTurn,
    attachment: MessageAttachment,
    extracted_text: str,
) -> None:
    """Persist AI OCR as the attachment source while preserving review history."""

    if turn.task_type != "image_text" or not turn.result_validated:
        return
    if (
        attachment.organization_id != turn.organization_id
        or not attachment.mime_type.startswith("image/")
    ):
        return

    extracted_text = extracted_text.strip()[:12_000]
    if not extracted_text:
        return
    extraction = db.scalar(
        select(AttachmentTextExtraction).where(
            AttachmentTextExtraction.attachment_id == attachment.id
        )
    )
    provider = (turn.provider or "ai_assist")[:40]
    model_name = (turn.provider_model or turn.provider or "ai_assist")[:120]
    if extraction is None:
        extraction = AttachmentTextExtraction(
            organization_id=turn.organization_id,
            attachment_id=attachment.id,
            status="completed",
            provider=provider,
            model_name=model_name,
            extracted_text=extracted_text,
            original_extracted_text=extracted_text,
            reviewed_text=None,
            error_message=None,
            requested_by_id=turn.requested_by_user_id,
            completed_at=_now(),
        )
        db.add(extraction)
        return

    if extraction.organization_id != turn.organization_id:
        return
    source = next(
        (
            item
            for item in turn.sources
            if item.source_type == "attachment"
            and item.attachment_id == attachment.id
        ),
        None,
    )
    provisional_for_current_turn = bool(
        source is not None
        and (source.source_meta or {}).get("local_ocr_created_for_turn_id")
        == str(turn.id)
        and extraction.status != "reviewed"
    )
    if extraction.status == "reviewed" or extraction.reviewed_text is not None:
        # A staff-confirmed reading is canonical and must never be replaced by
        # a later automatic image_text run.
        return

    if provisional_for_current_turn:
        # Local OCR created only to seed this AI turn is an intermediate value,
        # so the validated provider result remains the first persisted reading.
        extraction.original_extracted_text = extracted_text
    elif extraction.original_extracted_text is None:
        # Preserve the earliest automatic reading for audit/recovery while the
        # editable, unreviewed value below advances to the latest explicit run.
        extraction.original_extracted_text = extraction.extracted_text or extracted_text
    extraction.extracted_text = extracted_text
    extraction.error_message = None
    extraction.completed_at = _now()
    extraction.status = "completed"
    extraction.provider = provider
    extraction.model_name = model_name


def _postprocess_image_text_result(
    db: Session,
    turn: AiAssistTurn,
    attachments: Sequence[MessageAttachment],
) -> bool:
    if not turn.result_validated:
        return False
    metadata = _turn_service_metadata(turn)
    raw_context = metadata.get("service_context")
    service_context: AiServiceContext | None = (
        raw_context if raw_context in VALID_SERVICE_CONTEXTS else None
    )
    payload = dict(turn.result_payload or {})
    payload["service_context"] = service_context
    payload["service_context_source"] = metadata.get("service_context_source")
    payload["service_context_notice"] = metadata.get("service_context_notice")
    if turn.task_type == "image_text":
        raw_image_texts = payload.get("image_texts")
        image_texts = raw_image_texts if isinstance(raw_image_texts, list) else []
        if len(attachments) > 1:
            image_numbers = [
                item.get("image_no")
                for item in image_texts
                if isinstance(item, dict)
                and isinstance(item.get("image_no"), int)
            ]
            if (
                len(image_numbers) != len(attachments)
                or set(image_numbers) != set(range(1, len(attachments) + 1))
            ):
                return False
        by_image_no = {
            item.get("image_no"): item
            for item in image_texts
            if isinstance(item, dict)
            and isinstance(item.get("image_no"), int)
            and isinstance(item.get("text"), str)
            and item.get("text", "").strip()
        }
        if len(attachments) == 1 and not by_image_no and turn.visible_text:
            by_image_no[1] = {
                "image_no": 1,
                "filename": attachments[0].original_name,
                "text": turn.visible_text,
            }

        section_texts: list[str] = []
        normalized_image_texts: list[dict[str, Any]] = []
        missing_names: list[str] = []
        for image_no, attachment in enumerate(attachments, 1):
            item = by_image_no.get(image_no)
            if item is None:
                missing_names.append(attachment.original_name)
                continue
            extracted_text = str(item["text"]).strip()[:12_000]
            if not extracted_text:
                missing_names.append(attachment.original_name)
                continue
            normalized_image_texts.append(
                {
                    "image_no": image_no,
                    "filename": attachment.original_name,
                    "text": extracted_text,
                }
            )
            section_texts.append(
                f"[이미지 {image_no} · {attachment.original_name}]\n{extracted_text}"
            )
            _persist_attachment_visible_text(db, turn, attachment, extracted_text)

        if normalized_image_texts:
            payload["image_texts"] = normalized_image_texts
            combined = (
                normalized_image_texts[0]["text"]
                if len(attachments) == 1
                else "\n\n".join(section_texts)[:120_000]
            )
            turn.visible_text = combined
            payload["visible_text"] = combined
        if missing_names and len(attachments) > 1:
            notice = "일부 이미지의 글자를 구분해 저장하지 못했습니다: " + ", ".join(
                missing_names[:3]
            )
            if notice not in turn.uncertainties:
                turn.uncertainties = [*turn.uncertainties, notice][:20]
            payload["uncertainties"] = turn.uncertainties
        payload["resident_name_candidates"] = _resident_name_candidates(
            db,
            turn,
            turn.visible_text or "",
            service_context,
        )
    else:
        payload.setdefault("resident_name_candidates", [])
    turn.result_payload = payload
    return True


def _require_audio_transcripts(
    db: Session,
    task: AiAssistTaskRequest,
    attachments: Sequence[MessageAttachment],
) -> None:
    if task.task_type != "audio_summary":
        return
    pending_names = [
        attachment.original_name
        for attachment in attachments
        if not _attachment_extracted_text(db, attachment.id)
    ]
    if pending_names:
        names = ", ".join(pending_names[:5])
        remainder = (
            f" 외 {len(pending_names) - 5}개" if len(pending_names) > 5 else ""
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "다음 음성파일의 받아쓰기와 직원 확인을 먼저 완료해 주세요: "
                f"{names}{remainder}. 모든 변환이 끝난 뒤 다시 요약해 주세요."
            ),
        )


def _add_source(
    db: Session,
    turn: AiAssistTurn,
    *,
    ordinal: int,
    source_type: str,
    label: str,
    content_text: str | None,
    room_id: UUID | None = None,
    message_id: UUID | None = None,
    attachment_id: UUID | None = None,
    confirmed_record_id: UUID | None = None,
    source_meta: dict[str, Any] | None = None,
) -> AiAssistSource:
    normalized_text = content_text.strip()[:12_000] if content_text else None
    source = AiAssistSource(
        organization_id=turn.organization_id,
        turn=turn,
        ordinal=ordinal,
        source_type=source_type,
        room_id=room_id,
        message_id=message_id,
        attachment_id=attachment_id,
        confirmed_record_id=confirmed_record_id,
        label=label[:240],
        content_text=normalized_text,
        content_sha256=_text_sha256(normalized_text),
        source_meta=source_meta or {},
        included_in_prompt=False,
    )
    db.add(source)
    return source


def _append_service_roster_source(
    db: Session,
    turn: AiAssistTurn,
    service_context: AiServiceContext | None,
) -> None:
    residents = _active_service_residents(
        db,
        turn.organization_id,
        service_context,
    )
    service_labels = {
        "facility": "시설",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }
    roster_lines: list[str] = []
    service_types = (
        (service_context,)
        if service_context is not None
        else ("facility", "daycare", "homecare")
    )
    for service_type in service_types:
        service_names = [
            name
            for resident in residents
            if resident.service_type == service_type
            and (name := _normalize_resident_name(resident.display_name))
        ]
        if service_context is None:
            roster_lines.append(f"[{service_labels[service_type]}]")
        roster_lines.extend(
            (f"- {name}" for name in service_names)
            if service_names
            else ("- 현재 이용자 없음",)
        )
    # A roster is personal data regardless of historical DEV test flags.
    turn.contains_real_data = True
    _add_source(
        db,
        turn,
        ordinal=len(turn.sources) + 1,
        source_type="manual_context",
        label="현재 이용자 이름 명단",
        content_text="\n".join(roster_lines),
        room_id=turn.conversation.room_id,
        message_id=turn.conversation.message_id,
        source_meta={
            "service_context": service_context or "all",
            "work_category": "active_resident_roster",
        },
    )


def _append_long_term_care_context_source(
    db: Session,
    turn: AiAssistTurn,
    message: Message,
    attachments: Sequence[MessageAttachment],
    service_context: AiServiceContext | None,
) -> None:
    source_text = "\n".join(
        value
        for value in [
            message.body,
            *(
                _attachment_extracted_text(db, attachment.id)
                for attachment in attachments
            ),
        ]
        if value
    )
    resident_rows = list(
        db.scalars(
            select(Resident).where(
                Resident.organization_id == turn.organization_id,
            )
        )
    )
    known_resident_names = {
        _normalize_resident_name(resident.display_name)
        for resident in resident_rows
        if _normalize_resident_name(resident.display_name)
    }
    allowed_resident_names = {
        _normalize_resident_name(resident.display_name)
        for resident in resident_rows
        if resident.is_active
        and resident.status == "active"
        and (
            service_context is None
            or resident.service_type == service_context
        )
        and _normalize_resident_name(resident.display_name)
    }
    learned_corrections = []
    for memory in db.scalars(
        select(OcrCorrectionMemory)
        .where(
            OcrCorrectionMemory.organization_id == turn.organization_id,
            OcrCorrectionMemory.occurrence_count >= 2,
        )
        .order_by(
            OcrCorrectionMemory.occurrence_count.desc(),
            OcrCorrectionMemory.updated_at.desc(),
        )
        .limit(200)
    ):
        corrected = _normalize_resident_name(memory.corrected_text)
        if (
            corrected in known_resident_names
            and corrected not in allowed_resident_names
        ):
            continue
        learned_corrections.append(
            (memory.recognized_text, memory.corrected_text)
        )
    context = get_ai_lexicon_context(
        source_text,
        correction_pairs=learned_corrections,
    )
    safe_context = {
        "safety_rule": context.get("safety_rule"),
        "matched_situations": context.get("matched_situations", []),
        "relevant_corrections": context.get("relevant_corrections", {}),
        "standard_terms": list(context.get("standard_terms", []))[:120],
    }
    _add_source(
        db,
        turn,
        ordinal=len(turn.sources) + 1,
        source_type="manual_context",
        label="장기요양 용어·상황별 표현 사전",
        content_text=json.dumps(safe_context, ensure_ascii=False),
        room_id=message.room_id,
        message_id=message.id,
        source_meta={"work_category": "long_term_care_context"},
    )


def _append_staff_roster_source(db: Session, turn: AiAssistTurn) -> None:
    staff_rows = list(
        db.scalars(
            select(Staff)
            .where(
                Staff.organization_id == turn.organization_id,
                Staff.is_active.is_(True),
                Staff.is_test_data.is_(False),
                Staff.employment_status.in_({"active", "leave"}),
                Staff.deleted_at.is_(None),
            )
            .order_by(Staff.display_name, Staff.id)
        )
    )
    names: list[str] = []
    for staff in staff_rows:
        name = staff.display_name.strip()
        if not name or re.search(r"\((?:가명|시험)[^)]*\)", name):
            continue
        if name not in names:
            names.append(name)
    if not names:
        return
    turn.contains_real_data = True
    _add_source(
        db,
        turn,
        ordinal=len(turn.sources) + 1,
        source_type="manual_context",
        label="현재 재직·휴직 직원 이름 명단",
        content_text="\n".join(f"- {name}" for name in names),
        room_id=turn.conversation.room_id,
        message_id=turn.conversation.message_id,
        source_meta={"work_category": "active_staff_roster"},
    )


def _create_turn_sources(
    db: Session,
    turn: AiAssistTurn,
    message: Message,
    attachments: Sequence[MessageAttachment],
    task: AiAssistTaskRequest,
    *,
    previous_service_context: AiServiceContext | None = None,
    include_service_roster: bool = False,
) -> None:
    service_context, context_source, context_notice = _resolve_service_context(
        db,
        message,
        task.service_context,
        previous=previous_service_context,
    )
    if task.deidentified_confirmed_by_user:
        context_source = "user_deidentified_confirmation"
        context_notice = (
            "비식별 승인 자료이므로 실제 이용자·직원 명단은 포함하지 않았습니다."
        )
    _add_source(
        db,
        turn,
        ordinal=1,
        source_type="message",
        label="원본 메시지",
        content_text=message.body,
        room_id=message.room_id,
        message_id=message.id,
        source_meta={
            "message_type": message.message_type,
            "service_context": service_context,
            "service_context_source": context_source,
            "service_context_notice": context_notice,
            "deidentified_confirmed_by_user": (
                task.deidentified_confirmed_by_user
            ),
        },
    )
    attachment_count = len(attachments)
    for attachment_no, attachment in enumerate(attachments, 1):
        extracted_text = _attachment_extracted_text(db, attachment.id)
        if requires_staff_review(attachment) and not extracted_text:
            continue
        is_image = attachment.mime_type.startswith("image/")
        is_audio = attachment.mime_type.startswith("audio/")
        if is_image:
            label = (
                f"첨부 이미지 {attachment_no}/{attachment_count} · "
                f"{attachment.original_name}"
            )
        elif is_audio:
            label = (
                f"첨부 음성 {attachment_no}/{attachment_count} · "
                f"{attachment.original_name}"
            )
        else:
            label = f"원본 메시지 첨부파일 · {attachment.original_name}"
        _add_source(
            db,
            turn,
            ordinal=len(turn.sources) + 1,
            source_type="attachment",
            label=label,
            content_text=extracted_text,
            room_id=message.room_id,
            message_id=message.id,
            attachment_id=attachment.id,
            source_meta={
                "mime_type": attachment.mime_type,
                "filename": attachment.original_name,
                "image_no": attachment_no if is_image else None,
                "image_count": attachment_count if is_image else None,
                "audio_no": attachment_no if is_audio else None,
                "audio_count": attachment_count if is_audio else None,
            },
        )
    if include_service_roster:
        _append_service_roster_source(db, turn, service_context)
        _append_staff_roster_source(db, turn)
        _append_long_term_care_context_source(
            db,
            turn,
            message,
            attachments,
            service_context,
        )


def _previous_turn_service_context(
    db: Session,
    conversation: AiAssistConversation,
) -> AiServiceContext | None:
    previous = db.scalar(
        select(AiAssistTurn)
        .where(
            AiAssistTurn.conversation_id == conversation.id,
            AiAssistTurn.status == "completed",
            AiAssistTurn.result_validated.is_(True),
        )
        .order_by(AiAssistTurn.sequence_no.desc())
        .limit(1)
    )
    if previous is None:
        return None
    raw_context = (previous.result_payload or {}).get("service_context")
    return raw_context if raw_context in VALID_SERVICE_CONTEXTS else None


def _conversation_has_image_attachment(
    db: Session,
    conversation: AiAssistConversation,
) -> bool:
    validated_image_turn_id = db.scalar(
        select(AiAssistTurn.id)
        .where(
            AiAssistTurn.conversation_id == conversation.id,
            AiAssistTurn.task_type.in_({"image_text", "image_explain"}),
            AiAssistTurn.status == "completed",
            AiAssistTurn.result_validated.is_(True),
        )
        .order_by(AiAssistTurn.sequence_no)
        .limit(1)
    )
    if validated_image_turn_id is None:
        return False
    attachment_id = db.scalar(
        select(AiAssistSource.attachment_id)
        .where(
            AiAssistSource.turn_id == validated_image_turn_id,
            AiAssistSource.attachment_id.is_not(None),
        )
        .order_by(AiAssistSource.ordinal)
        .limit(1)
    )
    attachment = db.get(MessageAttachment, attachment_id) if attachment_id else None
    return bool(
        attachment is not None
        and attachment.message_id == conversation.message_id
        and attachment.mime_type.startswith("image/")
    )


def _append_previous_turn_context(
    db: Session,
    turn: AiAssistTurn,
    conversation: AiAssistConversation,
) -> None:
    """Carry verified prior questions and answers into follow-up turns."""

    previous_turns = list(
        db.scalars(
            select(AiAssistTurn)
            .where(
                AiAssistTurn.conversation_id == conversation.id,
                AiAssistTurn.sequence_no < turn.sequence_no,
                AiAssistTurn.status == "completed",
                AiAssistTurn.result_validated.is_(True),
                AiAssistTurn.answer.is_not(None),
            )
            .order_by(AiAssistTurn.sequence_no.desc())
            .limit(5)
        )
    )
    next_ordinal = len(turn.sources) + 1
    for previous in reversed(previous_turns):
        if not _turn_review_sources_current(db, previous):
            continue
        sections = [
            f"이전 질문:\n{previous.question}",
            f"검증된 이전 답변:\n{previous.answer}",
        ]
        if previous.visible_text:
            sections.insert(
                1,
                (
                    "이전 원본 판독문(자동 교정하지 않은 원문):\n"
                    f"{previous.visible_text}"
                ),
            )
        candidate_lines: list[str] = []
        raw_previous_candidates = (previous.result_payload or {}).get(
            "resident_name_candidates"
        ) or []
        if not isinstance(raw_previous_candidates, list):
            raw_previous_candidates = []
        for raw_candidate in raw_previous_candidates[:8]:
            if not isinstance(raw_candidate, dict):
                continue
            recognized = str(raw_candidate.get("recognized") or "").strip()[:100]
            candidate = str(raw_candidate.get("candidate") or "").strip()[:100]
            if not recognized or not candidate:
                continue
            candidate_lines.append(f"- {recognized} → {candidate}")
            try:
                resident_id = UUID(str(raw_candidate.get("resident_id")))
            except (TypeError, ValueError):
                turn.contains_real_data = True
                continue
            resident = db.get(Resident, resident_id)
            if (
                resident is None
                or resident.organization_id != turn.organization_id
                or not _resident_is_explicit_test_data(resident)
            ):
                # Once an actual roster name enters follow-up context, the turn
                # must not cross the external-provider gate.
                turn.contains_real_data = True
        if candidate_lines:
            sections.append(
                "서버 내부 명단 대조 후보(자동 수정·확정 아님):\n"
                + "\n".join(candidate_lines)
            )
        content = "\n\n".join(sections)
        _add_source(
            db,
            turn,
            ordinal=next_ordinal,
            source_type="manual_context",
            label=f"이전 AI 대화 {previous.sequence_no}",
            content_text=content,
            room_id=conversation.room_id,
            message_id=conversation.message_id,
            source_meta={
                "sequence_no": previous.sequence_no,
                "task_type": previous.task_type,
            },
        )
        next_ordinal += 1


def _history_keywords(question: str, body: str) -> list[str]:
    ignored = {
        "그리고",
        "관련",
        "내용",
        "메시지",
        "업무",
        "확인",
        "정리",
        "해주세요",
    }
    words = re.findall(r"[0-9A-Za-z가-힣]{2,}", f"{question} {body}")
    unique: list[str] = []
    for word in words:
        if word in ignored or word in unique:
            continue
        unique.append(word)
    return unique[:8]


def _append_room_history_sources(
    db: Session,
    turn: AiAssistTurn,
    message: Message,
) -> None:
    keywords = _history_keywords(turn.question, message.body)
    if not keywords:
        return
    matches = list(
        db.scalars(
            select(Message)
            .where(
                Message.organization_id == turn.organization_id,
                Message.room_id == message.room_id,
                Message.id != message.id,
                Message.lifecycle_status == "active",
                or_(*(Message.body.ilike(f"%{word}%") for word in keywords)),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(10)
        )
    )
    next_ordinal = len(turn.sources) + 1
    for match in reversed(matches):
        _add_source(
            db,
            turn,
            ordinal=next_ordinal,
            source_type="message",
            label=f"같은 방의 이전 메시지 {match.created_at.date().isoformat()}",
            content_text=match.body,
            room_id=match.room_id,
            message_id=match.id,
            source_meta={"created_at": match.created_at.isoformat()},
        )
        next_ordinal += 1


def _append_optional_confirmed_record_sources(
    db: Session,
    turn: AiAssistTurn,
    conversation: AiAssistConversation,
    requester: User | None,
) -> None:
    """Use revision-027 search only after its stable adapter is available.

    The future module may expose ``search_confirmed_records_for_ai`` returning
    dictionaries with id, label, text, and metadata. A missing module or hook is
    deliberately a no-op so this slice never imports an unfinished API eagerly.
    """

    if not _can_search_confirmed_records(requester):
        return

    try:
        module = importlib.import_module(".confirmed_records", package=__package__)
    except ModuleNotFoundError as exc:
        expected_names = {f"{__package__}.confirmed_records", "confirmed_records"}
        if exc.name in expected_names:
            return
        raise
    searcher = getattr(module, "search_confirmed_records_for_ai", None)
    if callable(searcher):
        rows = searcher(
            db=db,
            organization_id=turn.organization_id,
            room_id=conversation.room_id,
            query=turn.question,
            limit=10,
        )
    else:
        stable_search = getattr(module, "search_confirmed_records", None)
        filters_type = getattr(module, "ConfirmedRecordSearchFilters", None)
        if not callable(stable_search) or filters_type is None:
            return
        source_message = db.get(Message, conversation.message_id)
        keywords = _history_keywords(
            turn.question,
            source_message.body if source_message is not None else "",
        )
        filters = filters_type(
            resident_id=source_message.resident_id if source_message else None,
            q=None if source_message and source_message.resident_id else (
                keywords[0] if keywords else None
            ),
            lifecycle_status="active",
            limit=10,
            offset=0,
        )
        page = stable_search(
            db,
            organization_id=turn.organization_id,
            filters=filters,
        )
        rows = [
            {
                "id": hit.record.id,
                "label": f"{hit.resident_name} 확정 업무기록",
                "text": "\n".join(
                    item
                    for item in (
                        hit.record.observation_text,
                        hit.record.action_text,
                        hit.record.result_text,
                        hit.record.measurement_text,
                    )
                    if item
                ),
                "metadata": {
                    "occurred_at": hit.record.occurred_at.isoformat(),
                    "service_context": hit.record.service_context,
                    "work_category": hit.record.work_category,
                    "urgency": hit.record.urgency,
                },
            }
            for hit in page.items
        ]
    next_ordinal = len(turn.sources) + 1
    for row in rows or []:
        if not isinstance(row, dict):
            continue
        try:
            record_id = UUID(str(row["id"]))
        except (KeyError, TypeError, ValueError):
            continue
        text_value = str(row.get("text") or "").strip() or None
        label = str(row.get("label") or "확정 업무기록")
        metadata = row.get("metadata")
        _add_source(
            db,
            turn,
            ordinal=next_ordinal,
            source_type="confirmed_record",
            label=label,
            content_text=text_value,
            room_id=conversation.room_id,
            confirmed_record_id=record_id,
            source_meta=metadata if isinstance(metadata, dict) else {},
        )
        next_ordinal += 1


def transition_ai_turn(db: Session, turn: AiAssistTurn, new_status: str) -> None:
    locked_turn = db.scalar(
        select(AiAssistTurn)
        .where(AiAssistTurn.id == turn.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_turn is None:
        raise _not_found("AI 작업을 찾을 수 없습니다.")
    turn = locked_turn
    allowed = ALLOWED_STATUS_TRANSITIONS.get(turn.status, frozenset())
    if new_status not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"AI 작업 상태를 {turn.status}에서 {new_status}(으)로 바꿀 수 없습니다.",
        )
    now = _now()
    turn.status = new_status
    turn.updated_at = now
    turn.conversation.status = new_status
    turn.conversation.updated_at = now
    if new_status == "preparing" and turn.started_at is None:
        turn.started_at = now
    elif new_status == "completed":
        turn.completed_at = now
        turn.share_ready_at = now
    elif new_status == "cancelled":
        turn.cancelled_at = now
        turn.share_ready_at = None
    elif new_status == "failed":
        turn.completed_at = now
        turn.share_ready_at = None
    db.commit()


def _next_attempt_number(db: Session, turn_id: UUID) -> int:
    return int(
        db.scalar(
            select(func.coalesce(func.max(AiAssistProviderAttempt.attempt_no), 0)).where(
                AiAssistProviderAttempt.turn_id == turn_id
            )
        )
        or 0
    ) + 1


def _begin_attempt(
    db: Session,
    turn: AiAssistTurn,
    *,
    provider: str,
    model: str,
    input_bytes: int,
    transmitted_external: bool,
    attempt_status: str = "started",
    error_code: str | None = None,
    error_message: str | None = None,
) -> AiAssistProviderAttempt:
    now = _now()
    attempt = AiAssistProviderAttempt(
        organization_id=turn.organization_id,
        turn_id=turn.id,
        attempt_no=_next_attempt_number(db, turn.id),
        provider=provider,
        model_name=model,
        status=attempt_status,
        transmitted_external=transmitted_external,
        input_bytes=max(0, input_bytes),
        error_code=error_code,
        error_message=error_message,
        response_meta={},
        started_at=now,
        finished_at=now if attempt_status in {"skipped", "failed", "completed"} else None,
        latency_ms=0 if attempt_status in {"skipped", "completed"} else None,
    )
    db.add(attempt)
    db.commit()
    return attempt


def _finish_attempt(
    db: Session,
    attempt: AiAssistProviderAttempt,
    *,
    attempt_status: str,
    started: float,
    error_code: str | None = None,
    error_message: str | None = None,
    response_meta: dict[str, Any] | None = None,
) -> None:
    attempt.status = attempt_status
    attempt.error_code = error_code
    attempt.error_message = error_message[:500] if error_message else None
    attempt.response_meta = response_meta or {}
    attempt.finished_at = _now()
    attempt.latency_ms = max(0, int((perf_counter() - started) * 1000))
    db.commit()


def _source_snapshots(turn: AiAssistTurn) -> list[AiAssistSourceSnapshot]:
    snapshots: list[AiAssistSourceSnapshot] = []
    used_chars = 0
    for source in sorted(turn.sources, key=lambda item: item.ordinal):
        text_value = source.content_text
        if text_value:
            remaining = MAX_CONTEXT_TEXT_CHARS - used_chars
            if remaining <= 0:
                text_value = None
            else:
                text_value = text_value[:remaining]
                used_chars += len(text_value)
        snapshots.append(
            AiAssistSourceSnapshot(
                source_no=source.ordinal,
                source_type=source.source_type,
                label=source.label,
                text=text_value,
                metadata={
                    key: value
                    for key, value in source.source_meta.items()
                    if key
                    in {
                        "message_type",
                        "mime_type",
                        "filename",
                        "image_no",
                        "image_count",
                        "audio_no",
                        "audio_count",
                        "created_at",
                        "occurred_at",
                        "service_context",
                        "work_category",
                        "urgency",
                        "sequence_no",
                        "task_type",
                    }
                },
            )
        )
    return snapshots


def _worker_input_bytes(
    task_type: str,
    question: str,
    sources: list[AiAssistSourceSnapshot],
    images: Sequence[CodexWorkerImage],
) -> int:
    context_bytes = len(
        json.dumps(
            {
                "task_type": task_type,
                "question": question,
                "sources": [source.model_dump(mode="json") for source in sources],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return context_bytes + sum(len(image.content) for image in images)


def _load_image_attachments(
    attachments: Sequence[MessageAttachment],
) -> list[CodexWorkerImage]:
    upload_root = Path(app_settings.upload_dir).resolve()
    images: list[CodexWorkerImage] = []
    for image_no, attachment in enumerate(attachments, 1):
        if attachment.mime_type not in {"image/jpeg", "image/png", "image/webp"}:
            raise CodexWorkerInvalidResponse(
                f"{image_no}번 첨부파일은 지원하지 않는 이미지 형식입니다."
            )
        if attachment.size_bytes < 1 or attachment.size_bytes > MAX_CODEX_IMAGE_BYTES:
            raise CodexWorkerInvalidResponse(
                f"{image_no}번 이미지 용량이 AI 확인 범위를 벗어났습니다."
            )
        source_path = (upload_root / attachment.storage_key).resolve()
        if not source_path.is_relative_to(upload_root) or not source_path.is_file():
            raise CodexWorkerInvalidResponse(
                f"{image_no}번 첨부 이미지 파일을 안전하게 읽을 수 없습니다."
            )
        content = source_path.read_bytes()
        if len(content) != attachment.size_bytes or len(content) > MAX_CODEX_IMAGE_BYTES:
            raise CodexWorkerInvalidResponse(
                f"{image_no}번 첨부 이미지 크기를 확인할 수 없습니다."
            )
        images.append(
            CodexWorkerImage(
                content=content,
                mime_type=attachment.mime_type,
                filename=attachment.original_name,
                image_no=image_no,
            )
        )
    return images


def _validate_worker_evidence(
    analysis: CodexWorkerAnalysis,
    sources: list[AiAssistSourceSnapshot],
) -> None:
    valid_numbers = {source.source_no for source in sources}
    if any(item.source_no not in valid_numbers for item in analysis.result.evidence):
        raise CodexWorkerInvalidResponse("Codex 결과의 근거번호가 원본과 맞지 않습니다.")


class IncompleteMultiImageResult(CodexWorkerInvalidResponse):
    code = "incomplete_multi_image_result"


def _validate_worker_image_texts(
    analysis: CodexWorkerAnalysis,
    *,
    task_type: AiTaskType,
    expected_image_count: int,
) -> None:
    if task_type != "image_text" or expected_image_count <= 1:
        return
    image_numbers = [item.image_no for item in analysis.result.image_texts]
    expected_numbers = set(range(1, expected_image_count + 1))
    if (
        len(image_numbers) != expected_image_count
        or set(image_numbers) != expected_numbers
    ):
        raise IncompleteMultiImageResult(
            "일부 이미지의 판독 결과가 누락되었습니다. 다시 시도해 주세요."
        )


def _image_texts_from_attachment_sources(
    sources: Sequence[AiAssistSourceSnapshot],
) -> list[dict[str, Any]]:
    image_texts: list[dict[str, Any]] = []
    for source in sources:
        image_no = source.metadata.get("image_no")
        filename = source.metadata.get("filename")
        if (
            source.source_type != "attachment"
            or not source.text
            or not isinstance(image_no, int)
            or not isinstance(filename, str)
            or not filename
        ):
            continue
        image_texts.append(
            {
                "image_no": image_no,
                "filename": filename,
                "text": source.text,
            }
        )
    return sorted(image_texts, key=lambda item: item["image_no"])


def _combined_attachment_source_text(
    sources: Sequence[AiAssistSourceSnapshot],
) -> str | None:
    attachment_sources = [
        source
        for source in sources
        if source.source_type == "attachment" and source.text
    ]
    if len(attachment_sources) == 1:
        return attachment_sources[0].text
    sections = [
        f"[{source.label}]\n{source.text}"
        for source in attachment_sources
    ]
    return "\n\n".join(sections)[:12_000] if sections else None


_RULES_COMPLETION_MARKERS = (
    "완료했습니다",
    "완료했습니다.",
    "확인 완료",
    "점검 완료",
    "조치 완료",
    "처리 완료",
)
_RULES_FAST_PATH_BLOCKERS = (
    "어르신",
    "보호자",
    "환자",
    "혈압",
    "체온",
    "맥박",
    "호흡",
    "통증",
    "식사",
    "수분",
    "배설",
    "투약",
    "낙상",
    "욕창",
    "상처",
    "의식",
    "부축",
    "간호",
    "병원",
    "응급",
    "검사",
    "재측정",
    "확인 필요",
    "미완료",
    "예정",
    "불명확",
    "충돌",
    "요청",
    "문의",
    "동의",
    "상담",
    "않았",
    "못했",
)


def _rules_fact_text(sources: Sequence[AiAssistSourceSnapshot]) -> str | None:
    source = next(
        (
            item
            for item in sources
            if item.source_type == "message" and (item.text or "").strip()
        ),
        None,
    )
    if source is None:
        return None
    text = re.sub(r"^\[[^\]\n]{1,160}\]\s*", "", source.text.strip())
    for disclaimer in (
        "모든 내용은 합성 시험자료입니다.",
        "실제 어르신이나 운영 기록과 무관합니다.",
        "NO REAL PERSON OR RECORD.",
    ):
        text = text.replace(disclaimer, " ")
    return re.sub(r"\s+", " ", text).strip()[:500] or None


def _rules_summary_guidance(text: str) -> tuple[str, list[str], list[str]]:
    if any(marker in text for marker in ("불명확", "충돌", "확인 필요")):
        return (
            f"원문에서 확인이 필요한 내용: {text}",
            ["서로 다른 수치나 상태를 임의로 확정하지 말고 원문을 확인해 주세요."],
            ["담당자가 원문과 실제 상태를 대조한 뒤 결과를 기록해 주세요."],
        )
    if any(marker in text for marker in ("미완료", "예정", "재확인")):
        return (
            f"원문에 남은 확인·후속조치: {text}",
            ["예정된 확인이나 후속조치의 완료 여부는 아직 확정되지 않았습니다."],
            ["담당자가 후속 결과를 확인해 원문에 남겨 주세요."],
        )
    if any(marker in text for marker in _RULES_COMPLETION_MARKERS):
        return (
            f"원문에 완료로 기록된 내용: {text}",
            [],
            ["추가 확인이 필요한지는 담당자가 원문을 확인해 주세요."],
        )
    return (
        f"원문에서 확인한 내용: {text}",
        ["규칙 기반 결과이므로 사람의 최종 확인이 필요합니다."],
        ["필요한 후속조치가 있는지 담당자가 원문을 확인해 주세요."],
    )


def deterministic_local_fallback(
    *,
    task_type: AiTaskType,
    sources: list[AiAssistSourceSnapshot],
) -> AiAssistResult:
    display_sources = sources
    if task_type in {"image_text", "image_explain"}:
        # Roster sources stay available to protected internal name matching,
        # but one image result must not expose the full roster as evidence.
        display_sources = [
            source
            for source in sources
            if source.source_type in {"message", "attachment"}
        ]
    evidence = [
        AiEvidence(source_no=source.source_no, statement=source.text[:500])
        for source in display_sources
        if source.text
    ][:10]
    visible_text = None
    image_texts: list[dict[str, Any]] = []
    if task_type == "image_text":
        visible_text = _combined_attachment_source_text(display_sources)
        image_texts = _image_texts_from_attachment_sources(display_sources)
    if visible_text:
        answer = "기존 텍스트 추출 결과를 표시했습니다. 원본 이미지와 대조해 주세요."
        uncertainties = ["전문 AI의 이미지·문맥 판단은 수행되지 않았습니다."]
        recommended_actions = ["원본을 확인하고 필요한 경우 담당자에게 문의해 주세요."]
    elif task_type == "summary" and (fact_text := _rules_fact_text(display_sources)):
        answer, uncertainties, recommended_actions = _rules_summary_guidance(fact_text)
    else:
        answer = (
            "전문 AI 연결을 사용할 수 없어 자동 판단을 만들지 않았습니다. "
            "표시된 원문 근거를 확인해 주세요."
        )
        uncertainties = ["전문 AI의 이미지·문맥 판단은 수행되지 않았습니다."]
        recommended_actions = ["원문을 확인하고 필요한 경우 담당자에게 문의해 주세요."]
    return AiAssistResult(
        answer=answer,
        visible_text=visible_text,
        image_texts=image_texts,
        evidence=evidence,
        uncertainties=uncertainties,
        recommended_actions=recommended_actions,
    )


def _simple_completed_summary_fast_path(
    turn: AiAssistTurn,
    message: Message,
    attachments: Sequence[MessageAttachment],
) -> bool:
    if (
        turn.task_type != "summary"
        or turn.question.strip() != DEFAULT_QUESTIONS["summary"]
        or attachments
        or message.action_item is not None
        or message.resident_id is not None
        or message.comments
        or any(link.status == "confirmed" for link in message.resident_links)
    ):
        return False
    sources = _source_snapshots(turn)
    if len(sources) != 1 or sources[0].source_type != "message":
        return False
    fact_text = _rules_fact_text(sources)
    if not fact_text or len(fact_text) > 240 or "\n" in fact_text:
        return False
    if not any(marker in fact_text for marker in _RULES_COMPLETION_MARKERS):
        return False
    return not any(marker in fact_text for marker in _RULES_FAST_PATH_BLOCKERS)


def _complete_simple_summary_fast_path(db: Session, turn: AiAssistTurn) -> None:
    started = perf_counter()
    for next_status in ("preparing", "extracting", "searching", "reasoning", "validating"):
        transition_ai_turn(db, turn, next_status)
    payload = dict(turn.result_payload or {})
    payload["fast_path"] = {
        "applied": True,
        "reason": "simple_completed_nonresident_summary",
        "ai_enhancement_skipped": True,
    }
    turn.result_payload = payload
    turn.prompt_version = "instant-rules-fast-path-v1"
    turn.elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    db.commit()
    transition_ai_turn(db, turn, "completed")


def _local_ai_entries(
    *,
    question: str,
    sources: list[AiAssistSourceSnapshot],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for index, source in enumerate(sources, 1):
        entry: dict[str, Any] = {
            "number": index,
            "source": source.label,
            "source_type": source.source_type,
            "body": source.text or "텍스트가 추출되지 않았습니다.",
            **source.metadata,
        }
        if index == 1:
            entry["request"] = question
        entries.append(entry)
    return entries


def _local_ai_assist_result(
    *,
    task_type: AiTaskType,
    question: str,
    sources: list[AiAssistSourceSnapshot],
    external_allowed: bool,
) -> tuple[RoomSummaryResult, AiAssistResult]:
    if not any(source.text for source in sources):
        raise LocalAiError("로컬 AI가 확인할 텍스트 근거가 없습니다.")

    local_summary = summarize_room_messages(
        entries=_local_ai_entries(question=question, sources=sources),
        external_allowed=external_allowed,
    )
    evidence = [
        AiEvidence(source_no=source.source_no, statement=source.text[:500])
        for source in sources
        if source.text
    ][:10]
    visible_text = None
    image_texts: list[dict[str, Any]] = []
    if task_type == "image_text":
        visible_text = _combined_attachment_source_text(sources)
        image_texts = _image_texts_from_attachment_sources(sources)
    uncertainty = (
        "로컬 AI는 이미지 원본 대신 저장된 텍스트 추출 결과를 확인했습니다."
        if task_type in {"image_text", "image_explain"}
        else "AI가 정리한 내용이므로 중요한 판단은 원문과 대조해야 합니다."
    )
    return local_summary, AiAssistResult(
        answer=local_summary.summary,
        visible_text=visible_text,
        image_texts=image_texts,
        evidence=evidence,
        uncertainties=[uncertainty],
        recommended_actions=["원문 근거를 확인한 뒤 필요한 업무에 활용해 주세요."],
    )


def _adaptive_execution_context(
    turn: AiAssistTurn,
    sources: list[AiAssistSourceSnapshot],
):
    settings_document, settings_saved = load_ai_settings()
    if not settings_saved:
        return None
    from .ai_settings_store import effective_provider_settings
    settings_document = settings_document.model_copy(update={"providers": effective_provider_settings(settings_document)})
    provider_statuses = collect_provider_public_statuses(settings_document.providers)
    ollama_status = next(
        item for item in provider_statuses if item.provider == "ollama"
    )
    stt_status = next(
        item for item in provider_statuses if item.provider == "local_stt"
    )
    hardware = detect_ai_hardware(
        ollama_ready=ollama_status.connection_state == "ready",
        ollama_processing_location=ollama_status.processing_location,
        local_models=ollama_status.models,
        stt_ready=stt_status.connection_state == "ready",
        stt_model=stt_status.configured_model,
        external_credentials=[
            item.provider
            for item in provider_statuses
            if item.processing_location == "external" and item.credential_configured
        ],
    )
    if turn.task_type in {"image_text", "image_explain"}:
        capability = "image"
        model_role = "vision"
    elif turn.task_type in {"summary", "history_search", "risk_check", "audio_summary"}:
        capability = "text"
        model_role = "precision"
    else:
        capability = "text"
        model_role = "text"
    classification_text = "\n".join(
        source.text or "" for source in sources if source.text
    )[:MAX_CONTEXT_TEXT_CHARS]
    plan = build_routing_preview(
        AiRoutingPreviewRequest(
            text=classification_text,
            contains_real_data=turn.contains_real_data,
            deidentified_confirmed_by_user=not turn.contains_real_data,
            capability=capability,
            model_role=model_role,
        ),
        settings_document,
        provider_statuses,
        hardware,
    )
    return settings_document, plan


def _validate_adaptive_result_quality(
    result: AiAssistResult,
    *,
    task_type: AiTaskType,
    sources: list[AiAssistSourceSnapshot],
    image_count: int,
) -> None:
    valid_source_numbers = {source.source_no for source in sources}
    if any(item.source_no not in valid_source_numbers for item in result.evidence):
        raise ProviderAdapterError(
            "contract_validation_failed",
            "AI 결과가 입력에 없는 근거번호를 사용했습니다.",
        )
    if task_type == "image_text" and image_count > 1:
        expected_numbers = set(range(1, image_count + 1))
        if {item.image_no for item in result.image_texts} != expected_numbers:
            raise ProviderAdapterError(
                "contract_validation_failed",
                "일부 이미지의 구조화 판독 결과가 누락되었습니다.",
            )
    if task_type not in {"image_text", "image_explain"}:
        return
    visible_parts = [
        result.answer.strip(),
        (result.visible_text or "").strip(),
        *(item.text.strip() for item in result.image_texts),
    ]
    placeholder_values = {
        "aiassistresult",
        "ai assist result",
        "result",
        "response",
    }
    meaningful_parts = [
        item
        for item in visible_parts
        if item and " ".join(item.casefold().split()) not in placeholder_values
    ]
    if sum(len(item) for item in meaningful_parts) < 12:
        raise ProviderAdapterError(
            "invalid_response",
            "이미지 판독 결과가 최소 품질 기준을 충족하지 못했습니다.",
        )


def _execute_adaptive_ai_assist(
    db: Session,
    turn: AiAssistTurn,
    *,
    settings_document,
    plan,
    sources: list[AiAssistSourceSnapshot],
    images: list[CodexWorkerImage],
    input_bytes: int,
) -> CodexWorkerAnalysis | None:
    rules_candidate = next(
        candidate for candidate in plan.candidates if candidate.provider == "rules"
    )
    ai_candidates = [
        candidate for candidate in plan.candidates if candidate.provider != "rules"
    ][: max(0, plan.max_attempts - 1)]
    candidates = [*ai_candidates, rules_candidate]
    route_started = perf_counter()
    image_payloads = [
        {
            "content": image.content,
            "mime_type": image.mime_type,
            "filename": image.filename,
            "image_no": image.image_no,
        }
        for image in images
    ]
    for candidate in candidates:
        if not _processing_may_continue(db, turn):
            return None
        elapsed_seconds = perf_counter() - route_started
        if candidate.provider != "rules" and elapsed_seconds >= plan.total_timeout_seconds:
            _begin_attempt(
                db,
                turn,
                provider=candidate.provider,
                model=candidate.model,
                input_bytes=input_bytes,
                transmitted_external=False,
                attempt_status="skipped",
                error_code="time_budget_exhausted",
                error_message="AI 전체 제한시간이 끝나 규칙 경로로 전환했습니다.",
            )
            continue
        attempt_started = perf_counter()
        attempt = _begin_attempt(
            db,
            turn,
            provider=(
                "local_deterministic"
                if candidate.provider == "rules"
                else candidate.provider
            ),
            model=(FALLBACK_MODEL if candidate.provider == "rules" else candidate.model),
            input_bytes=input_bytes,
            transmitted_external=candidate.external_transmission,
        )
        if candidate.provider == "rules":
            result = deterministic_local_fallback(
                task_type=turn.task_type,
                sources=sources,
            )
            _finish_attempt(
                db,
                attempt,
                attempt_status="completed",
                started=attempt_started,
                response_meta={"route": "rules"},
            )
            return CodexWorkerAnalysis(
                result=result,
                provider="local_deterministic",
                model=FALLBACK_MODEL,
                prompt_version="adaptive-router-v1",
                elapsed_ms=max(0, round((perf_counter() - attempt_started) * 1000)),
            )
        provider_settings = settings_document.providers.get(candidate.provider)
        if provider_settings is None:
            _finish_attempt(
                db,
                attempt,
                attempt_status="failed",
                started=attempt_started,
                error_code="provider_unavailable",
                error_message="공급자 설정이 없습니다.",
            )
            continue
        remaining_seconds = max(
            1,
            round(plan.total_timeout_seconds - elapsed_seconds),
        )
        bounded_settings = provider_settings.model_copy(
            update={
                "timeout_seconds": min(
                    provider_settings.timeout_seconds or remaining_seconds,
                    remaining_seconds,
                )
            }
        )
        try:
            result, elapsed_ms = generate_ai_assist_result(
                candidate.provider,
                bounded_settings,
                model=candidate.model,
                task_type=turn.task_type,
                question=turn.question,
                sources=sources,
                images=image_payloads,
            )
            _validate_adaptive_result_quality(
                result,
                task_type=turn.task_type,
                sources=sources,
                image_count=len(images),
            )
        except ProviderAdapterError as error:
            _finish_attempt(
                db,
                attempt,
                attempt_status="failed",
                started=attempt_started,
                error_code=error.code,
                error_message=str(error),
                response_meta={"route": candidate.path},
            )
            continue
        _finish_attempt(
            db,
            attempt,
            attempt_status="completed",
            started=attempt_started,
            response_meta={"route": candidate.path},
        )
        for source in turn.sources:
            source.included_in_prompt = True
        db.commit()
        return CodexWorkerAnalysis(
            result=result,
            provider=candidate.provider,
            model=candidate.model,
            prompt_version="adaptive-router-v1",
            elapsed_ms=elapsed_ms,
        )
    return None


def _apply_result(
    turn: AiAssistTurn,
    result: AiAssistResult,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    elapsed_ms: int,
) -> None:
    validated = AiAssistResult.model_validate(result)
    payload = validated.model_dump(mode="json")
    turn.answer = validated.answer
    turn.visible_text = validated.visible_text
    turn.evidence = [item.model_dump(mode="json") for item in validated.evidence]
    turn.uncertainties = validated.uncertainties
    turn.recommended_actions = validated.recommended_actions
    turn.result_payload = payload
    turn.result_validated = True
    turn.provider = provider
    turn.provider_model = model
    turn.prompt_version = prompt_version
    turn.elapsed_ms = max(0, elapsed_ms)
    turn.error_code = None
    turn.error_message = None


def _apply_instant_rules_baseline(turn: AiAssistTurn) -> None:
    result = deterministic_local_fallback(
        task_type=turn.task_type,
        sources=_source_snapshots(turn),
    )
    _apply_result(
        turn,
        result,
        provider="local_deterministic",
        model=FALLBACK_MODEL,
        prompt_version="instant-baseline-v1",
        elapsed_ms=0,
    )
    payload = dict(turn.result_payload or {})
    payload["instant_baseline"] = True
    turn.result_payload = payload


def _fail_incomplete_multi_image_result(
    db: Session,
    turn: AiAssistTurn,
) -> AiAssistTurn:
    _clear_turn_result(turn)
    turn.error_code = IncompleteMultiImageResult.code
    turn.error_message = (
        "일부 이미지의 판독이 완료되지 않았습니다. 잠시 후 다시 시도해 주세요."
    )
    db.flush()
    transition_ai_turn(db, turn, "failed")
    db.refresh(turn)
    return turn


def process_ai_turn(
    db: Session,
    turn: AiAssistTurn,
    *,
    runtime: AiAssistRuntimeSettings | None = None,
    worker_client: WorkerClient | None = None,
    attachments: Sequence[MessageAttachment] = (),
) -> AiAssistTurn:
    if turn.status != "queued":
        if turn.status in AI_TERMINAL_STATUSES:
            return turn
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 처리 중인 AI 작업입니다.",
        )
    runtime = runtime or AiAssistRuntimeSettings.from_env()
    worker = worker_client or runtime.build_worker_client()
    conversation = turn.conversation
    message = db.get(Message, conversation.message_id)
    if message is None:
        turn.error_code = "source_message_missing"
        turn.error_message = "원본 메시지를 찾을 수 없습니다."
        transition_ai_turn(db, turn, "failed")
        return turn

    if not _processing_may_continue(db, turn):
        db.refresh(turn)
        return turn
    if turn.task_type == "audio_summary":
        audio_sources = [
            source
            for source in turn.sources
            if source.source_type == "attachment"
            and str((source.source_meta or {}).get("mime_type") or "").startswith(
                "audio/"
            )
        ]
        incomplete_sources = [
            source for source in audio_sources if not (source.content_text or "").strip()
        ]
        if not audio_sources or incomplete_sources:
            pending_names = [
                str((source.source_meta or {}).get("filename") or source.label)
                for source in incomplete_sources
            ]
            suffix = (
                " 미완료 파일: " + ", ".join(pending_names[:5])
                if pending_names
                else ""
            )
            turn.error_code = "audio_transcript_pending"
            turn.error_message = (
                "모든 음성의 글자 변환이 완료되어야 종합 정리할 수 있습니다."
                f"{suffix}"
            )
            transition_ai_turn(db, turn, "failed")
            return turn

    transition_ai_turn(db, turn, "preparing")
    if not _processing_may_continue(db, turn):
        return turn
    transition_ai_turn(db, turn, "extracting")
    if not _processing_may_continue(db, turn):
        return turn
    for attachment in attachments:
        _ensure_local_image_text_source(db, turn, message, attachment)
    db.commit()
    if not _processing_may_continue(db, turn):
        return turn
    transition_ai_turn(db, turn, "searching")
    if not _processing_may_continue(db, turn):
        return turn
    if turn.task_type == "history_search":
        _append_room_history_sources(db, turn, message)
        requester = db.get(User, turn.requested_by_user_id)
        _append_optional_confirmed_record_sources(
            db,
            turn,
            conversation,
            requester,
        )
        db.commit()
        db.refresh(turn)
    if not _processing_may_continue(db, turn):
        return turn
    transition_ai_turn(db, turn, "reasoning")
    if not _processing_may_continue(db, turn):
        return turn

    sources = _source_snapshots(turn)
    images: list[CodexWorkerImage] = []
    if turn.task_type in {"image_text", "image_explain"}:
        try:
            images = _load_image_attachments(attachments)
        except CodexWorkerError:
            images = []
    input_bytes = _worker_input_bytes(
        turn.task_type,
        turn.question,
        sources,
        images,
    )

    analysis: CodexWorkerAnalysis | None = None
    incomplete_multi_image_result = False
    adaptive_context = _adaptive_execution_context(turn, sources)
    # The legacy flag may still opt deidentified/test turns into the legacy
    # external fallback. It can never override the real-data block.
    external_route_allowed = (
        not turn.contains_real_data and runtime.allow_external_real_data
    )
    if adaptive_context is not None:
        settings_document, adaptive_plan = adaptive_context
        analysis = _execute_adaptive_ai_assist(
            db,
            turn,
            settings_document=settings_document,
            plan=adaptive_plan,
            sources=sources,
            images=images,
            input_bytes=input_bytes,
        )
    elif turn.contains_real_data:
        _begin_attempt(
            db,
            turn,
            provider="codex_worker",
            model="configured-at-worker",
            input_bytes=input_bytes,
            transmitted_external=False,
            attempt_status="skipped",
            error_code="external_real_data_disabled",
            error_message="실제자료는 외부전송 기본 차단 정책이 적용됩니다.",
        )
    elif not worker.configured:
        _begin_attempt(
            db,
            turn,
            provider="codex_worker",
            model="configured-at-worker",
            input_bytes=input_bytes,
            transmitted_external=False,
            attempt_status="skipped",
            error_code="codex_worker_unconfigured",
            error_message="Codex 작업기 인증이 준비되지 않았습니다.",
        )
    else:
        attempt = _begin_attempt(
            db,
            turn,
            provider="codex_worker",
            model="configured-at-worker",
            input_bytes=input_bytes,
            transmitted_external=True,
        )
        attempt_started = perf_counter()
        try:
            if not _processing_may_continue(db, turn):
                return turn
            analysis = worker.analyze(
                task_type=turn.task_type,
                question=turn.question,
                sources=sources,
                images=images,
            )
            _validate_worker_evidence(analysis, sources)
            _validate_worker_image_texts(
                analysis,
                task_type=turn.task_type,
                expected_image_count=len(attachments),
            )
        except CodexWorkerError as exc:
            _finish_attempt(
                db,
                attempt,
                attempt_status="failed",
                started=attempt_started,
                error_code=exc.code,
                error_message=exc.public_message,
            )
            incomplete_multi_image_result = isinstance(
                exc,
                IncompleteMultiImageResult,
            )
            analysis = None
        else:
            _finish_attempt(
                db,
                attempt,
                attempt_status="completed",
                started=attempt_started,
                response_meta={"prompt_version": analysis.prompt_version},
            )
            for source in turn.sources:
                source.included_in_prompt = True
            db.commit()

    if not _processing_may_continue(db, turn):
        return turn
    if incomplete_multi_image_result:
        return _fail_incomplete_multi_image_result(db, turn)
    transition_ai_turn(db, turn, "validating")
    if not _processing_may_continue(db, turn):
        return turn
    if analysis is not None:
        _apply_result(
            turn,
            analysis.result,
            provider=analysis.provider,
            model=analysis.model,
            prompt_version=analysis.prompt_version,
            elapsed_ms=analysis.elapsed_ms,
        )
    else:
        local_result: tuple[RoomSummaryResult, AiAssistResult] | None = None
        local_input_bytes = _worker_input_bytes(
            turn.task_type,
            turn.question,
            sources,
            [],
        )
        local_endpoint_safe = _local_ai_endpoint_is_safe()
        if not local_endpoint_safe:
            _begin_attempt(
                db,
                turn,
                provider="local_ai",
                model="configured-local-chain",
                input_bytes=local_input_bytes,
                transmitted_external=False,
                attempt_status="skipped",
                error_code="unsafe_local_ai_url",
                error_message=(
                    "로컬 AI 주소가 루프백 또는 Docker 호스트 주소가 아니어서 "
                    "호출하지 않았습니다."
                ),
            )
        else:
            local_started = perf_counter()
            local_attempt = _begin_attempt(
                db,
                turn,
                provider="local_ai",
                model="configured-local-chain",
                input_bytes=local_input_bytes,
                transmitted_external=False,
            )
            try:
                if not _processing_may_continue(db, turn):
                    return turn
                # False를 명시하므로 기존 체인의 Nemotron 분기는 이 단계에서
                # 실행될 수 없고, 안전하게 확인된 로컬 주소만 호출합니다.
                local_result = _local_ai_assist_result(
                    task_type=turn.task_type,
                    question=turn.question,
                    sources=sources,
                    external_allowed=False,
                )
            except LocalAiError as exc:
                _finish_attempt(
                    db,
                    local_attempt,
                    attempt_status="failed",
                    started=local_started,
                    error_code="local_ai_unavailable",
                    error_message=str(exc),
                )
            else:
                local_summary, assist_result = local_result
                local_attempt.provider = local_summary.provider
                local_attempt.model_name = local_summary.model
                _finish_attempt(
                    db,
                    local_attempt,
                    attempt_status="completed",
                    started=local_started,
                    response_meta={"route": "local-first"},
                )
                if not _processing_may_continue(db, turn):
                    return turn
                _apply_result(
                    turn,
                    assist_result,
                    provider=local_summary.provider,
                    model=local_summary.model,
                    prompt_version=LOCAL_AI_PROMPT_VERSION,
                    elapsed_ms=local_summary.elapsed_ms,
                )

        provider_setting = app_settings.ai_review_provider.strip().lower()
        external_fallback_allowed = (
            local_result is None
            and external_route_allowed
            and app_settings.ai_review_external_enabled
            and provider_setting in {"chain", "nvidia", "nemotron"}
            and (local_endpoint_safe or provider_setting in {"nvidia", "nemotron"})
        )
        if external_fallback_allowed:
            external_started = perf_counter()
            external_attempt = _begin_attempt(
                db,
                turn,
                provider="nvidia",
                model=app_settings.nvidia_nemotron_model,
                input_bytes=local_input_bytes,
                transmitted_external=True,
            )
            try:
                if not _processing_may_continue(db, turn):
                    return turn
                local_result = _local_ai_assist_result(
                    task_type=turn.task_type,
                    question=turn.question,
                    sources=sources,
                    external_allowed=True,
                )
            except LocalAiError as exc:
                _finish_attempt(
                    db,
                    external_attempt,
                    attempt_status="failed",
                    started=external_started,
                    error_code="external_ai_unavailable",
                    error_message=str(exc),
                )
            else:
                external_summary, assist_result = local_result
                external_attempt.provider = external_summary.provider
                external_attempt.model_name = external_summary.model
                _finish_attempt(
                    db,
                    external_attempt,
                    attempt_status="completed",
                    started=external_started,
                    response_meta={"route": "external-after-local"},
                )
                if not _processing_may_continue(db, turn):
                    return turn
                _apply_result(
                    turn,
                    assist_result,
                    provider=external_summary.provider,
                    model=external_summary.model,
                    prompt_version=LOCAL_AI_PROMPT_VERSION,
                    elapsed_ms=external_summary.elapsed_ms,
                )

        if local_result is None:
            if not _processing_may_continue(db, turn):
                return turn
            fallback_started = perf_counter()
            fallback_attempt = _begin_attempt(
                db,
                turn,
                provider="local_deterministic",
                model=FALLBACK_MODEL,
                input_bytes=input_bytes,
                transmitted_external=False,
            )
            fallback_result = deterministic_local_fallback(
                task_type=turn.task_type,
                sources=sources,
            )
            fallback_elapsed = max(
                0,
                int((perf_counter() - fallback_started) * 1000),
            )
            _finish_attempt(
                db,
                fallback_attempt,
                attempt_status="completed",
                started=fallback_started,
            )
            _apply_result(
                turn,
                fallback_result,
                provider="local_deterministic",
                model=FALLBACK_MODEL,
                prompt_version="local-fallback-v1",
                elapsed_ms=fallback_elapsed,
            )
    if not _postprocess_image_text_result(db, turn, attachments):
        return _fail_incomplete_multi_image_result(db, turn)
    db.commit()
    if not _processing_may_continue(db, turn):
        return turn
    transition_ai_turn(db, turn, "completed")
    db.refresh(turn)
    return turn


def create_ai_conversation(
    db: Session,
    user: User,
    message_id: UUID,
    task: AiAssistTaskRequest,
    *,
    runtime: AiAssistRuntimeSettings | None = None,
    worker_client: WorkerClient | None = None,
    process_immediately: bool = True,
) -> AiAssistConversation:
    message = _message_for_user(db, user, message_id)
    attachments = _attachments_for_task(db, message, task)
    _require_audio_transcripts(db, task, attachments)
    conversation = AiAssistConversation(
        organization_id=user.organization_id,
        owner_user_id=user.id,
        room_id=message.room_id,
        message_id=message.id,
        title=(task.question or DEFAULT_QUESTIONS[task.task_type])[:160],
        status="queued",
    )
    db.add(conversation)
    db.flush()
    deidentified_approved = bool(task.deidentified_confirmed_by_user)
    turn = AiAssistTurn(
        organization_id=user.organization_id,
        conversation_id=conversation.id,
        requested_by_user_id=user.id,
        sequence_no=1,
        task_type=task.task_type,
        question=task.question or DEFAULT_QUESTIONS[task.task_type],
        contains_real_data=not (
            deidentified_approved
            or _message_is_explicit_test_data(db, message, attachments)
        ),
        status="queued",
        evidence=[],
        uncertainties=[],
        recommended_actions=[],
        result_validated=False,
    )
    db.add(turn)
    db.flush()
    _create_turn_sources(
        db,
        turn,
        message,
        attachments,
        task,
        include_service_roster=bool(
            not deidentified_approved
            and
            task.task_type in {"image_text", "image_explain", "audio_summary"}
            and bool(attachments)
            and all(
                item.mime_type.startswith(("image/", "audio/"))
                for item in attachments
            )
        ),
    )
    _apply_instant_rules_baseline(turn)
    use_fast_path = _simple_completed_summary_fast_path(turn, message, attachments)
    db.commit()
    db.refresh(conversation)
    if use_fast_path:
        _complete_simple_summary_fast_path(db, turn)
        db.refresh(conversation)
    elif process_immediately:
        process_ai_turn(
            db,
            turn,
            runtime=runtime,
            worker_client=worker_client,
            attachments=attachments,
        )
    return get_owned_conversation(db, user, conversation.id)


def add_ai_turn(
    db: Session,
    user: User,
    conversation_id: UUID,
    task: AiAssistTaskRequest,
    *,
    runtime: AiAssistRuntimeSettings | None = None,
    worker_client: WorkerClient | None = None,
    process_immediately: bool = True,
) -> AiAssistConversation:
    conversation = get_owned_conversation(db, user, conversation_id, for_update=True)
    message = _message_for_user(db, user, conversation.message_id)
    attachments = _attachments_for_task(db, message, task)
    _require_audio_transcripts(db, task, attachments)
    active_turn = db.scalar(
        select(AiAssistTurn.id).where(
            AiAssistTurn.conversation_id == conversation.id,
            AiAssistTurn.status.not_in(AI_TERMINAL_STATUSES),
        )
    )
    if active_turn is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이 AI 대화에서 아직 처리 중인 작업이 있습니다.",
        )
    sequence_no = int(
        db.scalar(
            select(func.coalesce(func.max(AiAssistTurn.sequence_no), 0)).where(
                AiAssistTurn.conversation_id == conversation.id
            )
        )
        or 0
    ) + 1
    previous_service_context = _previous_turn_service_context(db, conversation)
    deidentified_approved = bool(task.deidentified_confirmed_by_user)
    turn = AiAssistTurn(
        organization_id=user.organization_id,
        conversation_id=conversation.id,
        requested_by_user_id=user.id,
        sequence_no=sequence_no,
        task_type=task.task_type,
        question=task.question or DEFAULT_QUESTIONS[task.task_type],
        contains_real_data=not (
            deidentified_approved
            or _message_is_explicit_test_data(db, message, attachments)
        ),
        status="queued",
        evidence=[],
        uncertainties=[],
        recommended_actions=[],
        result_validated=False,
    )
    conversation.status = "queued"
    db.add(turn)
    db.flush()
    _create_turn_sources(
        db,
        turn,
        message,
        attachments,
        task,
        previous_service_context=previous_service_context,
        include_service_roster=bool(
            not deidentified_approved
            and (
                task.task_type == "audio_summary"
            or (
                task.task_type
                in {"image_text", "image_explain", "summary", "risk_check", "question"}
                and _conversation_has_image_attachment(db, conversation)
            )
            )
        ),
    )
    _append_previous_turn_context(db, turn, conversation)
    _apply_instant_rules_baseline(turn)
    use_fast_path = _simple_completed_summary_fast_path(turn, message, attachments)
    db.commit()
    if use_fast_path:
        _complete_simple_summary_fast_path(db, turn)
    elif process_immediately:
        process_ai_turn(
            db,
            turn,
            runtime=runtime,
            worker_client=worker_client,
            attachments=attachments,
        )
    return get_owned_conversation(db, user, conversation.id)


def process_ai_turn_background(turn_id: UUID) -> None:
    """Process one queued turn without retaining the request's DB session."""

    with SessionLocal() as db:
        turn = db.scalar(
            select(AiAssistTurn)
            .options(
                selectinload(AiAssistTurn.sources),
                selectinload(AiAssistTurn.conversation),
            )
            .where(AiAssistTurn.id == turn_id)
        )
        if turn is None or turn.status != "queued":
            return
        attachments = [
            attachment
            for source in sorted(turn.sources, key=lambda item: item.ordinal)
            if source.attachment_id is not None
            and (attachment := db.get(MessageAttachment, source.attachment_id))
            is not None
        ]
        try:
            process_ai_turn(db, turn, attachments=attachments)
        except Exception:
            # Background failures must leave a terminal, non-shareable record but
            # must not persist exception text that could contain local paths or data.
            db.rollback()
            failed_turn = db.get(AiAssistTurn, turn_id)
            if failed_turn is None or failed_turn.status in AI_TERMINAL_STATUSES:
                return
            failed_turn.error_code = "ai_assist_background_failed"
            failed_turn.error_message = "AI 작업을 완료하지 못했습니다. 다시 시도해 주세요."
            if "failed" in ALLOWED_STATUS_TRANSITIONS.get(
                failed_turn.status,
                frozenset(),
            ):
                transition_ai_turn(db, failed_turn, "failed")
            else:
                failed_turn.status = "failed"
                failed_turn.completed_at = _now()
                failed_turn.share_ready_at = None
                db.commit()


def recover_stale_ai_turns(
    db: Session,
    *,
    stale_after_minutes: int = STALE_AI_TURN_MINUTES,
) -> int:
    """Fail abandoned non-terminal turns after an application restart.

    BackgroundTasks are process-local.  Without this recovery, a restart can
    leave the UI polling a queued/preparing turn forever.  We retain the row
    for audit and expose a safe retry message instead of replaying data to any
    provider automatically.
    """

    cutoff = _now() - timedelta(minutes=max(1, stale_after_minutes))
    stale_turns = list(
        db.scalars(
            select(AiAssistTurn)
            .options(selectinload(AiAssistTurn.conversation))
            .where(
                AiAssistTurn.status.not_in(AI_TERMINAL_STATUSES),
                AiAssistTurn.updated_at <= cutoff,
            )
            .with_for_update()
        )
    )
    if not stale_turns:
        return 0
    now = _now()
    for turn in stale_turns:
        _clear_turn_result(turn)
        turn.status = "failed"
        turn.error_code = "ai_assist_interrupted_by_restart"
        turn.error_message = (
            "서버 재시작으로 AI 작업이 중단되었습니다. 다시 요청해 주세요."
        )
        turn.completed_at = now
        turn.updated_at = now
        turn.conversation.status = "failed"
        turn.conversation.updated_at = now
    db.commit()
    return len(stale_turns)


def cancel_ai_turn(db: Session, user: User, turn_id: UUID) -> AiAssistTurn:
    turn = get_owned_turn(db, user, turn_id, for_update=True)
    if turn.status == "cancelled":
        return turn
    if turn.status in AI_TERMINAL_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="이미 종료된 AI 작업입니다.",
        )
    _clear_turn_result(turn)
    transition_ai_turn(db, turn, "cancelled")
    db.refresh(turn)
    return turn


def ensure_turn_shareable(turn: AiAssistTurn) -> None:
    if (
        turn.status != "completed"
        or not turn.result_validated
        or turn.share_ready_at is None
        or not turn.answer
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="검증까지 완료된 AI 결과만 대화방에 공유할 수 있습니다.",
        )


def _share_body(turn: AiAssistTurn) -> str:
    def shortened(value: str, limit: int) -> str:
        normalized = value.strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: max(1, limit - 1)].rstrip() + "…"

    payload = dict(turn.result_payload or {})
    trailing_sections: list[str] = []
    answer_limit = 450 if turn.visible_text else 1300
    trailing_sections.append(
        "[AI 답변]\n" + shortened(turn.answer or "", answer_limit)
    )

    uncertainties = [
        str(item).strip()
        for item in (turn.uncertainties or [])
        if str(item).strip()
    ]
    if uncertainties:
        uncertainty_text = "\n".join(f"- {item}" for item in uncertainties)
        trailing_sections.append(
            "[확인 필요]\n" + shortened(uncertainty_text, 200)
        )

    raw_candidates = payload.get("resident_name_candidates") or []
    if not isinstance(raw_candidates, list):
        raw_candidates = []
    candidate_lines: list[str] = []
    service_labels = {
        "facility": "시설",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }
    for raw in raw_candidates[:3]:
        if not isinstance(raw, dict):
            continue
        recognized = str(raw.get("recognized") or "").strip()
        candidate = str(raw.get("candidate") or "").strip()
        if not recognized or not candidate:
            continue
        service_label = service_labels.get(str(raw.get("service_type") or ""), "")
        confidence = raw.get("confidence")
        try:
            confidence_label = f", {round(float(confidence) * 100)}%"
        except (TypeError, ValueError):
            confidence_label = ""
        candidate_lines.append(
            f"- {recognized} → {candidate} ({service_label}{confidence_label})"
        )
    if candidate_lines:
        trailing_sections.append(
            "[이름 확인 후보 · 확정 아님]\n"
            + shortened("\n".join(candidate_lines), 180)
        )

    prefix = "[AI 도움 결과 · 직원 확인 필요]\n"
    suffix = "\n\n※ 원본 판독문과 이름 후보를 담당자가 확인해 주세요."
    sections = list(trailing_sections)
    if turn.visible_text:
        ocr_header = "[원본 판독문 · 자동 교정 안 함]\n"
        non_ocr = "\n\n".join(trailing_sections)
        separator_chars = 2 if non_ocr else 0
        ocr_limit = max(
            300,
            SHARED_MESSAGE_MAX_CHARS
            - len(prefix)
            - len(suffix)
            - len(ocr_header)
            - len(non_ocr)
            - separator_chars,
        )
        sections.insert(
            0,
            ocr_header + shortened(turn.visible_text, ocr_limit),
        )
    body = prefix + "\n\n".join(sections)
    available = SHARED_MESSAGE_MAX_CHARS - len(suffix)
    if len(body) > available:
        body = body[: max(1, available - 1)].rstrip() + "…"
    return body + suffix


def share_ai_turn(db: Session, user: User, turn_id: UUID) -> AiAssistShareResponse:
    turn = get_owned_turn(db, user, turn_id, for_update=True)
    ensure_turn_shareable(turn)
    if turn.shared_message_id is not None and turn.shared_at is not None:
        return AiAssistShareResponse(
            turn_id=turn.id,
            message_id=turn.shared_message_id,
            shared_at=turn.shared_at,
            created=False,
        )
    conversation = turn.conversation
    _message_for_user(db, user, conversation.message_id)
    original = db.scalar(
        select(Message)
        .where(
            Message.id == conversation.message_id,
            Message.organization_id == user.organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if original is None or original.lifecycle_status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="회수된 메시지의 AI 결과는 공유할 수 없습니다.",
        )
    shared = Message(
        organization_id=user.organization_id,
        room_id=original.room_id,
        sender_id=user.id,
        message_type="chat",
        body=_share_body(turn),
        resident_id=original.resident_id,
        resident_ref=original.resident_ref,
        extra_data={
            "ai_assist": {
                "conversation_id": str(conversation.id),
                "turn_id": str(turn.id),
                "provider": turn.provider,
                "result_validated": True,
            }
        },
        lifecycle_status="active",
        is_test_data=original.is_test_data,
    )
    db.add(shared)
    db.flush()
    db.add(
        MessageReadReceipt(
            organization_id=user.organization_id,
            message_id=shared.id,
            user_id=user.id,
            is_test_data=shared.is_test_data,
        )
    )
    turn.shared_message_id = shared.id
    turn.shared_at = _now()
    db.commit()
    return AiAssistShareResponse(
        turn_id=turn.id,
        message_id=shared.id,
        shared_at=turn.shared_at,
        created=True,
    )


def turn_response(
    turn: AiAssistTurn,
    *,
    source_active: bool = True,
) -> AiAssistTurnResponse:
    if not source_active:
        return AiAssistTurnResponse(
            id=turn.id,
            status="cancelled",
            task_type=turn.task_type,
            question=None,
            answer=None,
            visible_text=None,
            evidence=[],
            uncertainties=[],
            recommended_actions=[],
            provider=None,
            model=None,
            processing_location="unconfigured",
            external_transmission=False,
            deidentification_status="unknown",
            fallback_active=False,
            fallback_state="blocked",
            fallback_reason=None,
            enhancement_status="failed",
            elapsed_ms=None,
            error_message="원본 메시지가 회수되어 AI 결과를 표시하지 않습니다.",
            created_at=turn.created_at,
            completed_at=turn.completed_at,
        )
    payload = dict(turn.result_payload or {})
    resident_name_candidates: list[AiResidentNameCandidate] = []
    for raw_candidate in payload.get("resident_name_candidates") or []:
        try:
            resident_name_candidates.append(
                AiResidentNameCandidate.model_validate(raw_candidate)
            )
        except (TypeError, ValueError):
            continue
    raw_context = payload.get("service_context")
    attempts = list(getattr(turn, "provider_attempts", None) or [])
    completed_attempts = [
        attempt for attempt in attempts if attempt.status == "completed"
    ]
    selected_attempt = completed_attempts[-1] if completed_attempts else None
    external_transmission = bool(
        selected_attempt is not None and selected_attempt.transmitted_external
    )
    selected_response_meta = (
        getattr(selected_attempt, "response_meta", None)
        if selected_attempt is not None
        else None
    )
    selected_route = (
        selected_response_meta.get("route")
        if isinstance(selected_response_meta, dict)
        else None
    )
    if external_transmission:
        processing_location = "external"
    elif selected_route in {"rules", "local", "internal"}:
        processing_location = selected_route
    elif selected_route == "external-after-local":
        processing_location = "external"
    elif turn.provider == "local_deterministic":
        processing_location = "rules"
    elif turn.provider in {"local_ollama", "ollama", "whisper"}:
        configured_scope = endpoint_scope(app_settings.ai_review_base_url)
        processing_location = (
            configured_scope if configured_scope in {"local", "internal"} else "local"
        )
    elif selected_attempt is not None:
        processing_location = "local"
    else:
        processing_location = "unconfigured"
    selected_attempt_number = (
        selected_attempt.attempt_no if selected_attempt is not None else None
    )
    prior_failed_attempts = [
        attempt
        for attempt in attempts
        if selected_attempt_number is not None
        and getattr(attempt, "attempt_no", 0) < selected_attempt_number
        and getattr(attempt, "status", None) in {"failed", "skipped"}
    ]
    fallback_active = bool(prior_failed_attempts)
    fallback_reason = None
    if prior_failed_attempts:
        reason_messages = list(
            dict.fromkeys(
                FALLBACK_REASON_MESSAGES.get(
                    getattr(attempt, "error_code", None),
                    "이전 AI 경로가 실패해 다음 안전 경로로 전환했습니다.",
                )
                for attempt in prior_failed_attempts
            )
        )
        fallback_reason = " ".join(reason_messages[:2])[:500]
    if processing_location == "rules":
        fallback_state = "rules"
    elif fallback_active:
        fallback_state = "fallback"
    elif turn.status == "completed":
        fallback_state = "primary"
    elif turn.status == "failed":
        fallback_state = "blocked"
    else:
        fallback_state = "unknown"
    if external_transmission:
        deidentification_status = "confirmed_deidentified"
    elif turn.contains_real_data:
        deidentification_status = "protected_local"
    else:
        deidentification_status = "not_sent_external"
    if turn.status not in AI_TERMINAL_STATUSES:
        enhancement_status = "pending"
    elif turn.status == "completed" and processing_location == "rules":
        enhancement_status = "baseline"
    elif turn.status == "completed":
        enhancement_status = "completed"
    else:
        enhancement_status = "failed"
    return AiAssistTurnResponse(
        id=turn.id,
        status=turn.status,
        task_type=turn.task_type,
        question=turn.question,
        answer=turn.answer,
        visible_text=turn.visible_text,
        evidence=[AiEvidence.model_validate(item) for item in (turn.evidence or [])],
        uncertainties=list(turn.uncertainties or []),
        recommended_actions=list(turn.recommended_actions or []),
        service_context=(
            raw_context if raw_context in VALID_SERVICE_CONTEXTS else None
        ),
        service_context_source=payload.get("service_context_source"),
        service_context_notice=payload.get("service_context_notice"),
        resident_name_candidates=resident_name_candidates,
        provider=turn.provider,
        model=turn.provider_model,
        processing_location=processing_location,
        external_transmission=external_transmission,
        deidentification_status=deidentification_status,
        fallback_active=fallback_active,
        fallback_state=fallback_state,
        fallback_reason=fallback_reason,
        enhancement_status=enhancement_status,
        elapsed_ms=turn.elapsed_ms,
        error_message=turn.error_message,
        created_at=turn.created_at,
        completed_at=turn.completed_at,
    )


def conversation_detail(
    db: Session,
    user: User,
    conversation_id: UUID,
) -> AiConversationDetail:
    conversation = db.scalar(
        select(AiAssistConversation)
        .options(selectinload(AiAssistConversation.turns))
        .where(
            AiAssistConversation.id == conversation_id,
            AiAssistConversation.organization_id == user.organization_id,
        )
        .execution_options(populate_existing=True)
    )
    if conversation is None:
        raise _not_found("AI 대화를 찾을 수 없습니다.")
    if conversation.owner_user_id != user.id:
        raise _forbidden("다른 직원의 AI 질문은 열 수 없습니다.")
    if conversation.room_id is not None:
        ensure_room_permission(db, user, conversation.room_id)
    source_active = _conversation_message_active(db, conversation)
    if not source_active:
        for turn in list(conversation.turns):
            if turn.status not in AI_TERMINAL_STATUSES:
                _cancel_for_recalled_source(db, turn)
    return AiConversationDetail(
        id=conversation.id,
        message_id=conversation.message_id,
        status=conversation.status if source_active else "cancelled",
        turns=[
            turn_response(turn, source_active=source_active and _turn_review_sources_current(db, turn))
            for turn in conversation.turns
        ],
    )


@router.post(
    "/api/messages/{message_id}/ai-conversations",
    response_model=AiConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
def create_ai_conversation_route(
    message_id: UUID,
    payload: AiAssistTaskRequest,
    background_tasks: BackgroundTasks,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiConversationDetail:
    _set_no_store(http_response)
    conversation = create_ai_conversation(
        db,
        user,
        message_id,
        payload,
        process_immediately=False,
    )
    turn_id = db.scalar(
        select(AiAssistTurn.id)
        .where(
            AiAssistTurn.conversation_id == conversation.id,
            AiAssistTurn.status == "queued",
        )
        .order_by(AiAssistTurn.sequence_no.desc())
        .limit(1)
    )
    if turn_id is not None:
        background_tasks.add_task(process_ai_turn_background, turn_id)
    return conversation_detail(db, user, conversation.id)


@router.get(
    "/api/messages/{message_id}/ai-conversations/latest",
    response_model=AiConversationDetail | None,
)
def get_latest_ai_conversation_route(
    message_id: UUID,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiConversationDetail | None:
    _set_no_store(http_response)
    conversation_id = db.scalar(
        select(AiAssistConversation.id)
        .where(
            AiAssistConversation.organization_id == user.organization_id,
            AiAssistConversation.owner_user_id == user.id,
            AiAssistConversation.message_id == message_id,
        )
        .order_by(
            AiAssistConversation.updated_at.desc(),
            AiAssistConversation.created_at.desc(),
        )
        .limit(1)
    )
    if conversation_id is None:
        return None
    return conversation_detail(db, user, conversation_id)


@router.get(
    "/api/ai-conversations/{conversation_id}",
    response_model=AiConversationDetail,
)
def get_ai_conversation_route(
    conversation_id: UUID,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiConversationDetail:
    _set_no_store(http_response)
    return conversation_detail(db, user, conversation_id)


@router.post(
    "/api/ai-conversations/{conversation_id}/turns",
    response_model=AiConversationDetail,
    status_code=status.HTTP_201_CREATED,
)
def add_ai_turn_route(
    conversation_id: UUID,
    payload: AiAssistTaskRequest,
    background_tasks: BackgroundTasks,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiConversationDetail:
    _set_no_store(http_response)
    conversation = add_ai_turn(
        db,
        user,
        conversation_id,
        payload,
        process_immediately=False,
    )
    turn_id = db.scalar(
        select(AiAssistTurn.id)
        .where(
            AiAssistTurn.conversation_id == conversation.id,
            AiAssistTurn.status == "queued",
        )
        .order_by(AiAssistTurn.sequence_no.desc())
        .limit(1)
    )
    if turn_id is not None:
        background_tasks.add_task(process_ai_turn_background, turn_id)
    return conversation_detail(db, user, conversation.id)


@router.get("/api/ai-turns/{turn_id}", response_model=AiAssistTurnResponse)
def get_ai_turn_route(
    turn_id: UUID,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiAssistTurnResponse:
    _set_no_store(http_response)
    turn = get_owned_turn(db, user, turn_id)
    source_active = _conversation_message_active(db, turn.conversation)
    if not source_active and turn.status not in AI_TERMINAL_STATUSES:
        _cancel_for_recalled_source(db, turn)
    return turn_response(turn, source_active=source_active)


@router.post("/api/ai-turns/{turn_id}/cancel", response_model=AiAssistTurnResponse)
def cancel_ai_turn_route(
    turn_id: UUID,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiAssistTurnResponse:
    _set_no_store(http_response)
    turn = cancel_ai_turn(db, user, turn_id)
    source_active = _conversation_message_active(db, turn.conversation)
    return turn_response(turn, source_active=source_active)


@router.post("/api/ai-turns/{turn_id}/share", response_model=AiAssistShareResponse)
async def share_ai_turn_route(
    turn_id: UUID,
    background_tasks: BackgroundTasks,
    http_response: Response,
    user: User = Depends(_require_ai_assist_user),
    db: Session = Depends(get_db),
) -> AiAssistShareResponse:
    _set_no_store(http_response)
    response = share_ai_turn(db, user, turn_id)
    shared_message = db.get(Message, response.message_id)
    if shared_message is not None and response.created:
        response_payload = message_response(shared_message, db=db, viewer_id=user.id)
        member_ids = room_member_user_ids(db, shared_message.room_id)
        push_recipient_ids = member_ids - {user.id}
        if push_recipient_ids:
            background_tasks.add_task(
                send_web_push_to_users,
                push_recipient_ids,
                room_id=shared_message.room_id,
                message_id=shared_message.id,
            )
        await manager.send_to_users(
            member_ids,
            {
                "event": "message_created",
                "message": response_payload.model_dump(mode="json"),
            },
        )
    return response


@router.get("/api/ai-assist/config", response_model=AiAssistConfigResponse)
def ai_assist_config_route(
    http_response: Response,
    _user: User = Depends(get_current_user),
) -> AiAssistConfigResponse:
    _set_no_store(http_response)
    runtime = AiAssistRuntimeSettings.from_env()
    worker = runtime.build_worker_client()
    codex_ready = False
    if worker.configured:
        try:
            codex_ready = worker.health().credential_ready
        except CodexWorkerError:
            codex_ready = False
    from .stt import stt_readiness

    whisper_ready = bool(stt_readiness(timeout_seconds=2.0)["ready"])
    local_ai_ready, local_ai_status_message = local_summary_provider_status()
    return AiAssistConfigResponse(
        enabled=True,
        codex_ready=codex_ready,
        whisper_ready=whisper_ready,
        local_fallback_ready=True,
        worker_configured=worker.configured,
        external_real_data_enabled=False,
        external_real_image_data_enabled=False,
        nemotron_ready=nemotron_is_configured(),
        local_ai_ready=local_ai_ready,
        local_ai_status_message=local_ai_status_message,
        task_types=list(AI_TASK_TYPES),
    )


@router.get("/api/ai/providers/status", response_model=AiProviderStatusResponse)
def ai_provider_status_route(
    http_response: Response,
    _admin: User = Depends(require_admin),
) -> AiProviderStatusResponse:
    """Report safe readiness without accepting or returning credentials."""

    _set_no_store(http_response)
    return collect_ai_provider_status()
