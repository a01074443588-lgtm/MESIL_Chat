from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import json
import logging
from pathlib import Path
import re
from shutil import copy2
from typing import Annotated, Any, NoReturn
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .database import Base, SessionLocal, engine, get_db
from .dependencies import get_current_session_and_user, get_current_user, require_admin
from .models import (
    ActionItem,
    AttachmentTextExtraction,
    LoginSession,
    Message,
    MessageAttachment,
    MessageComment,
    MessageReadReceipt,
    MessageResidentLink,
    MessageThreadView,
    OcrCorrectionEvent,
    OcrCorrectionMemory,
    OrgUnit,
    PushSubscription,
    Resident,
    RecipientRoom,
    ResidentSyncBatch,
    ResidentSyncItem,
    Room,
    RoomDigest,
    RoomMembership,
    RoomMembershipOverride,
    Staff,
    StaffJobAssignment,
    StaffJobCode,
    StaffOrganizationAssignment,
    StaffPositionCode,
    User,
    WorkItem,
    WorkItemDocumentDraft,
    utcnow,
)
from .local_ai import LocalAiError, refine_record_draft, summarize_room_messages

logger = logging.getLogger(__name__)
from .ocr import (
    OcrError,
    extract_report_text,
    get_ai_lexicon_context,
)
from .ocr_corrections import (
    PROTECTED_CONTENT_TYPES,
    build_correction_pairs,
    extract_page_visual_signature,
)
from .realtime import manager
from .push import send_web_push_to_users
from .prototype_ai import (
    DAILY_DOCUMENT_TYPES,
    PROTOTYPE_GENERATOR,
    build_document_proposal,
    build_prototype_suggestion,
)
from .schemas import (
    ActionAssigneeResponse,
    ActionItemCreate,
    ActionItemResponse,
    ActionItemUpdate,
    AdminPasswordResetRequest,
    AttachmentResponse,
    AttachmentTextExtractionUpdate,
    CareBriefingCard,
    CareBriefingSummary,
    CareforRosterSourceStatus,
    CareforRosterStatusResponse,
    CareforStaffAliasResponse,
    CustomRoomCreate,
    CustomRoomUpdate,
    DailyDocumentType,
    DocumentCandidateDashboardResponse,
    DocumentType,
    EmployeeCreate,
    EmployeeUpdate,
    LoginRequest,
    LoginResponse,
    MessageCommentCreate,
    MessageCommentResponse,
    MessageCreate,
    MessageForwardRequest,
    MessageDetailResponse,
    MessageResidentLinkUpdate,
    MessageResponse,
    JobCodeCreate,
    JobCodeUpdate,
    JobCodeResponse,
    ManagedCustomRoomResponse,
    ManagedRoomCreate,
    ManagedRoomResponse,
    ManagedRoomUpdate,
    OrgUnitCreate,
    OrgUnitUpdate,
    OrgUnitResponse,
    PasswordChangeRequest,
    PeriodDocumentDraft,
    PeriodRecordEvent,
    PeriodRecordSummaryRequest,
    PeriodRecordSummaryResponse,
    PeriodRecordSummarySelection,
    PeriodWorkdeskRequest,
    PeriodWorkdeskResponse,
    PeriodWorkdeskSource,
    PositionTitleCreate,
    PositionTitleResponse,
    PositionTitleUpdate,
    PushConfigResponse,
    PushSubscriptionCreate,
    PushSubscriptionDelete,
    PushSubscriptionResponse,
    ReadRequest,
    ReadReceiptResponse,
    RecordClassification,
    RecordDraft,
    RecordUsageTag,
    ResidentAdminCreate,
    ResidentResponse,
    ResidentOrderUpdate,
    ResidentSyncApplyRequest,
    ResidentSyncBatchResponse,
    ResidentSyncItemResponse,
    ReviewerSessionRequest,
    ReviewerSessionResponse,
    RoomDigestPoint,
    RoomDigestResponse,
    RoomMessageSearchResponse,
    RoomMemberResponse,
    RoomResponse,
    RoomSearchSummaryRequest,
    RoomSearchSummaryResponse,
    RiskLevel,
    SessionResponse,
    UserResponse,
    WorkItemResponse,
    WorkItemConfirmRequest,
    WorkItemReopenRequest,
    WorkItemDocumentDraftActionRequest,
    WorkItemDocumentDraftResponse,
    WorkItemResidentUpdate,
    WorkItemUpdate,
)
from .resident_sync import (
    ResidentSyncError,
    ResidentSyncStaleError,
    apply_sync_item,
    build_preview_entries,
    parse_roster_file,
)
from .security import (
    clear_failed_logins,
    client_key_from_request,
    create_login_session,
    create_reviewer_session_token,
    dummy_password_hash,
    hash_password,
    InvalidReviewerSessionToken,
    is_reviewer_login_session,
    is_local_development_request,
    login_retry_after,
    record_failed_login,
    REVIEWER_SESSION_USER_AGENT_PREFIX,
    secure_cookie_for_request,
    token_digest,
    reviewer_session_user_agent,
    validate_reviewer_session_user,
    verify_password,
)
from .stt import SttError, transcribe_audio
from .services import (
    active_membership,
    action_item_response,
    attachment_response,
    create_employee,
    ensure_bootstrap_admin,
    ensure_developer_launcher_user,
    ensure_reference_data,
    ensure_scope_room,
    ensure_system_rooms,
    list_user_rooms,
    message_engagement_counts,
    message_response,
    record_audit,
    resident_response,
    room_member_user_ids,
    clear_staff_job,
    set_staff_job,
    set_staff_position_title,
    set_staff_unit_assignments,
    set_user_role,
    staff_matches_room_rule,
    sync_auto_memberships,
    unit_response,
    user_response,
    validate_unit_assignments,
    validate_position_title,
)


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


MAX_RESIDENT_SYNC_FILE_BYTES = 2 * 1024 * 1024


ATTACHMENT_EXTENSIONS = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/aac": ".aac",
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "application/pdf": ".pdf",
}

IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/webp"}
AUDIO_MIME_TYPES = {
    mime_type for mime_type in ATTACHMENT_EXTENSIONS if mime_type.startswith("audio/")
}
VIDEO_MIME_TYPES = {
    mime_type for mime_type in ATTACHMENT_EXTENSIONS if mime_type.startswith("video/")
}


def _has_expected_signature(mime_type: str, content: bytes) -> bool:
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/webp":
        return content.startswith(b"RIFF") and content[8:12] == b"WEBP"
    if mime_type in {"audio/wav", "audio/x-wav"}:
        return content.startswith(b"RIFF") and content[8:12] == b"WAVE"
    if mime_type in {"audio/mp4", "audio/x-m4a"}:
        return content[4:8] == b"ftyp"
    if mime_type in {"video/mp4", "video/quicktime"}:
        return content[4:8] == b"ftyp"
    if mime_type == "audio/webm":
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "video/webm":
        return content.startswith(b"\x1a\x45\xdf\xa3")
    if mime_type == "audio/ogg":
        return content.startswith(b"OggS")
    if mime_type == "audio/mpeg":
        return content.startswith(b"ID3") or (
            len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0
        )
    if mime_type == "audio/aac":
        return len(content) > 1 and content[0] == 0xFF and content[1] & 0xF0 == 0xF0
    if mime_type == "application/pdf":
        return content.startswith(b"%PDF-")
    return False


def _resident_for_room(
    db: Session, room: Room, resident_id: UUID | None
) -> Resident | None:
    if resident_id is None:
        return None
    resident = db.get(Resident, resident_id)
    if (
        resident is None
        or not resident.is_active
        or resident.organization_id != room.organization_id
    ):
        raise HTTPException(status_code=422, detail="선택한 어르신을 찾을 수 없습니다.")
    return resident


@lru_cache(maxsize=4)
def _local_resident_name_map(
    path_text: str,
    modified_at_ns: int,
) -> dict[str, tuple[str, ...]]:
    del modified_at_ns
    path = Path(path_text)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        records = payload.get("residents", []) if isinstance(payload, dict) else payload
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, list):
        return {}
    result: dict[str, tuple[str, ...]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        external_id = str(record.get("external_id", "")).strip()
        if not external_id:
            continue
        names = tuple(
            dict.fromkeys(
                name
                for key in ("display_name", "real_name", "name")
                if len(name := str(record.get(key, "")).strip()) >= 2
            )
        )
        if names:
            result[external_id] = names
    return result


def _resident_name_aliases(resident: Resident) -> set[str]:
    display_name = resident.display_name.strip()
    without_test_label = re.sub(r"\s*\((?:가명|시험)\)\s*$", "", display_name).strip()
    aliases = {
        alias
        for alias in (display_name, without_test_label)
        if len(alias) >= 2
    }
    if resident.internal_code.startswith("SMCODI:"):
        external_id = resident.internal_code.removeprefix("SMCODI:")
        path = Path(settings.smcodi_resident_lexicon_path)
        try:
            modified_at_ns = path.stat().st_mtime_ns
        except OSError:
            modified_at_ns = 0
        aliases.update(
            _local_resident_name_map(path.as_posix(), modified_at_ns).get(
                external_id,
                (),
            )
        )
    return aliases


def _text_mentions_alias(text: str, alias: str) -> bool:
    return (
        re.search(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(alias)}"
            rf"(?:\s*(?:어르신|님))?(?![가-힣A-Za-z0-9])",
            text,
        )
        is not None
    )


def _sync_message_resident_candidates(
    db: Session,
    *,
    message: Message,
    text: str,
    source: str,
) -> list[MessageResidentLink]:
    if source not in {"text_exact", "ocr_exact"} or not text.strip():
        return []
    residents = db.scalars(
        select(Resident).where(
            Resident.organization_id == message.organization_id,
            Resident.is_active.is_(True),
        )
    ).all()
    aliases: dict[str, list[Resident]] = {}
    for resident in residents:
        for alias in _resident_name_aliases(resident):
            aliases.setdefault(alias, []).append(resident)

    matched = {
        owners[0].id: owners[0]
        for alias, owners in aliases.items()
        if len(owners) == 1 and _text_mentions_alias(text, alias)
    }
    if not matched:
        return []
    existing = {
        link.resident_id: link
        for link in db.scalars(
            select(MessageResidentLink).where(
                MessageResidentLink.message_id == message.id,
                MessageResidentLink.resident_id.in_(list(matched)),
            )
        ).all()
    }
    links: list[MessageResidentLink] = []
    for resident_id, resident in matched.items():
        link = existing.get(resident_id)
        if link is None:
            link = MessageResidentLink(
                organization_id=message.organization_id,
                message_id=message.id,
                resident_id=resident.id,
                source=source,
                status="candidate",
            )
            db.add(link)
        links.append(link)
    db.flush()
    return links


def _confirm_manual_resident_link(
    db: Session,
    *,
    message: Message,
    resident: Resident | None,
) -> MessageResidentLink | None:
    if resident is None:
        return None
    link = db.scalar(
        select(MessageResidentLink).where(
            MessageResidentLink.message_id == message.id,
            MessageResidentLink.resident_id == resident.id,
        )
    )
    if link is None:
        link = MessageResidentLink(
            organization_id=message.organization_id,
            message_id=message.id,
            resident_id=resident.id,
            source="manual",
            status="confirmed",
            reviewed_by_id=message.sender_id,
            reviewed_at=utcnow(),
        )
        db.add(link)
    else:
        link.source = "manual"
        link.status = "confirmed"
        link.reviewed_by_id = message.sender_id
        link.reviewed_at = utcnow()
    db.flush()
    return link


def _confirmed_message_resident_links(
    db: Session,
    message_id: UUID,
) -> list[MessageResidentLink]:
    return db.scalars(
        select(MessageResidentLink)
        .where(
            MessageResidentLink.message_id == message_id,
            MessageResidentLink.status == "confirmed",
        )
        .order_by(MessageResidentLink.created_at, MessageResidentLink.id)
    ).all()


def _has_pending_message_resident_candidates(
    db: Session,
    message_id: UUID,
) -> bool:
    return (
        db.scalar(
            select(func.count(MessageResidentLink.id)).where(
                MessageResidentLink.message_id == message_id,
                MessageResidentLink.status == "candidate",
            )
        )
        or 0
    ) > 0


def _message_for_member(db: Session, user: User, message_id: UUID) -> Message:
    message = db.get(Message, message_id)
    if message is None:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    if active_membership(db, user.id, message.room_id) is None:
        raise HTTPException(status_code=403, detail="이 메시지에 접근할 수 없습니다.")
    if (
        getattr(user, "_reviewer_experience", None) is not None
        and not message.is_test_data
    ):
        raise HTTPException(status_code=403, detail="이 메시지에 접근할 수 없습니다.")
    return message


def _interaction_is_test_data(
    user: User,
    *,
    room: Room | None = None,
    message: Message | None = None,
) -> bool:
    return (
        settings.environment != "production"
        or getattr(user, "_reviewer_experience", None) is not None
        or bool(room is not None and room.is_test_data)
        or bool(message is not None and message.is_test_data)
    )


def _require_processor(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin" and not user.can_process_records:
        raise HTTPException(status_code=403, detail="업무함 사용 권한이 없습니다.")
    return user


def _ensure_work_item(
    db: Session,
    message: Message,
    *,
    force: bool = False,
) -> WorkItem | None:
    if message.resident_id is None and not force:
        return None
    existing = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == message.id)
    )
    if existing is not None:
        return existing
    # SessionLocal은 의도적으로 autoflush=False다. 사진까지 같은 원문
    # 스냅샷에 포함하려면 아직 대기 중인 첨부 메타데이터를 먼저 반영한다.
    db.flush()
    room = db.get(Room, message.room_id)
    attachment_ids = db.scalars(
        select(MessageAttachment.id)
        .where(MessageAttachment.message_id == message.id)
        .order_by(MessageAttachment.created_at, MessageAttachment.id)
    ).all()
    confirmed_links = _confirmed_message_resident_links(db, message.id)
    resident_names = [link.resident.display_name for link in confirmed_links]
    primary_resident = message.resident
    work_item = WorkItem(
        organization_id=message.organization_id,
        source_message_id=message.id,
        resident_id=message.resident_id,
        status="pending",
        source_snapshot={
            "message_id": str(message.id),
            "room_id": str(message.room_id),
            "room_name": room.name if room is not None else "삭제된 채팅방",
            "sender_id": str(message.sender_id),
            "sender_name": message.sender.full_name,
            "resident_id": str(message.resident_id) if message.resident_id else None,
            "resident_name": (
                primary_resident.display_name if primary_resident is not None else None
            ),
            "resident_names": resident_names,
            "body": message.body,
            "message_type": message.message_type,
            "attachment_ids": [str(attachment_id) for attachment_id in attachment_ids],
            "created_at": _as_utc(message.created_at).isoformat(),
        },
        document_types=[],
        is_test_data=message.is_test_data,
    )
    db.add(work_item)
    return work_item


def _refresh_work_item_residents(db: Session, message: Message) -> WorkItem | None:
    confirmed_links = _confirmed_message_resident_links(db, message.id)
    confirmed_ids = {link.resident_id for link in confirmed_links}
    if message.resident_id not in confirmed_ids:
        message.resident_id = confirmed_links[0].resident_id if confirmed_links else None
    item = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == message.id)
    )
    if item is None:
        item = _ensure_work_item(
            db,
            message,
            force=bool(message.resident_links),
        )
    if item is None:
        return None
    item.resident_id = message.resident_id
    primary = (
        next(
            (
                link.resident
                for link in confirmed_links
                if link.resident_id == message.resident_id
            ),
            None,
        )
        if message.resident_id is not None
        else None
    )
    snapshot = dict(item.source_snapshot)
    snapshot["resident_id"] = str(message.resident_id) if message.resident_id else None
    snapshot["resident_name"] = primary.display_name if primary else None
    snapshot["resident_names"] = [
        link.resident.display_name for link in confirmed_links
    ]
    item.source_snapshot = snapshot
    if item.confirmed_at is None:
        item.ai_state = "not_requested"
        item.ai_payload = None
        item.ai_generator = None
        item.ai_generated_at = None
        item.document_types = []
        if item.status == "in_review":
            item.status = "pending"
    return item


def _create_action_item(
    db: Session,
    *,
    message: Message,
    creator: User,
    payload: ActionItemCreate | None,
) -> ActionItem | None:
    if payload is None:
        return None
    if payload.assignee_user_id is None and payload.assignee_unit_id is None:
        raise HTTPException(status_code=422, detail="담당 직원 또는 담당 팀을 지정해 주세요.")
    if payload.assignee_user_id is not None and payload.assignee_unit_id is not None:
        raise HTTPException(status_code=422, detail="담당 직원과 담당 팀 중 하나만 선택해 주세요.")
    assignee_user = None
    if payload.assignee_user_id is not None:
        assignee_user = db.get(User, payload.assignee_user_id)
        if (
            assignee_user is None
            or assignee_user.organization_id != creator.organization_id
            or not assignee_user.is_active
            or assignee_user.employment_status != "active"
        ):
            raise HTTPException(status_code=422, detail="담당 직원을 찾을 수 없습니다.")
        if active_membership(db, assignee_user.id, message.room_id) is None:
            raise HTTPException(
                status_code=422,
                detail="현재 채팅방에 참여한 직원만 담당자로 지정할 수 있습니다.",
            )
    assignee_unit = None
    if payload.assignee_unit_id is not None:
        assignee_unit = db.get(OrgUnit, payload.assignee_unit_id)
        if (
            assignee_unit is None
            or assignee_unit.organization_id != creator.organization_id
            or not assignee_unit.is_active
        ):
            raise HTTPException(status_code=422, detail="담당 팀을 찾을 수 없습니다.")
        member_ids = room_member_user_ids(db, message.room_id)
        member_users = (
            db.scalars(select(User).where(User.id.in_(member_ids))).all()
            if member_ids
            else []
        )
        visible_unit_ids = {
            unit.id
            for member in member_users
            for unit in (
                member.business,
                member.department,
                member.floor,
                member.team,
            )
            if unit is not None
        }
        if assignee_unit.id not in visible_unit_ids:
            raise HTTPException(
                status_code=422,
                detail="현재 채팅방 참여자의 소속 팀만 담당 팀으로 지정할 수 있습니다.",
            )
    action_item = ActionItem(
        organization_id=creator.organization_id,
        source_message_id=message.id,
        action_type=payload.action_type,
        assignee_user_id=payload.assignee_user_id,
        assignee_unit_id=payload.assignee_unit_id,
        priority=payload.priority,
        status="assigned",
        due_at=payload.due_at,
        created_by_id=creator.id,
        is_test_data=settings.environment != "production",
    )
    db.add(action_item)
    return action_item


def _work_item_comments(db: Session, item: WorkItem) -> list[MessageComment]:
    return db.scalars(
        select(MessageComment)
        .where(MessageComment.message_id == item.source_message_id)
        .order_by(MessageComment.created_at, MessageComment.id)
    ).all()


def _work_item_ai_snapshot(db: Session, item: WorkItem) -> dict:
    snapshot = dict(item.source_snapshot)
    extra_sections: list[str] = []
    extraction_attachment_ids: list[str] = []
    for attachment in item.source_message.attachments:
        extraction = attachment.text_extraction
        if extraction is None or extraction.status not in {"completed", "reviewed"}:
            continue
        extracted_text = extraction.reviewed_text or extraction.extracted_text
        if not extracted_text:
            continue
        extraction_label = (
            "음성파일 받아쓰기"
            if attachment.mime_type in AUDIO_MIME_TYPES
            else "보고서 이미지 판독"
        )
        extra_sections.append(
            f"[{extraction_label} · {attachment.original_name}]\n{extracted_text}"
        )
        extraction_attachment_ids.append(str(attachment.id))
    comments = _work_item_comments(db, item)
    if comments:
        extra_sections.append(
            "\n".join(
            f"[댓글 {comment.author.full_name}] {comment.body}"
            for comment in comments
            )
        )
        snapshot["comment_ids"] = [str(comment.id) for comment in comments]
    if extra_sections:
        snapshot["body"] = f"{snapshot['body']}\n" + "\n".join(extra_sections)
    if extraction_attachment_ids:
        snapshot["text_extraction_attachment_ids"] = extraction_attachment_ids
    return snapshot


def _merge_record_draft_with_prototype(
    current: RecordDraft,
    prototype: RecordDraft,
) -> RecordDraft:
    """오래된 초안에도 현재 일일서류 안전망을 보충한다."""
    current_payload = current.model_dump(mode="json")
    prototype_payload = prototype.model_dump(mode="json")

    proposal_by_type: dict[str, dict] = {
        proposal["document_type"]: proposal
        for proposal in prototype_payload["document_drafts"]
        if proposal["document_type"] in DAILY_DOCUMENT_TYPES
    }
    for proposal in current_payload.get("document_drafts", []):
        if proposal["document_type"] in DAILY_DOCUMENT_TYPES:
            proposal_by_type[proposal["document_type"]] = proposal

    requested_types = list(
        dict.fromkeys(
            [
                *prototype_payload["document_types"],
                *current_payload.get("document_types", []),
            ]
        )
    )
    current_payload["document_types"] = [
        document_type
        for document_type in requested_types
        if document_type in DAILY_DOCUMENT_TYPES
        and document_type in proposal_by_type
    ]
    current_payload["document_drafts"] = [
        proposal_by_type[document_type]
        for document_type in current_payload["document_types"]
    ]
    current_payload["verification_questions"] = list(
        dict.fromkeys(
            [
                *prototype_payload.get("verification_questions", []),
                *current_payload.get("verification_questions", []),
            ]
        )
    )[:20]
    for field in (
        "observation_details",
        "actions_taken",
        "resident_response",
        "handover_summary",
    ):
        if not current_payload.get(field):
            current_payload[field] = prototype_payload.get(field)
    return RecordDraft.model_validate(current_payload)


def _current_work_item_document_drafts(
    db: Session,
    item: WorkItem,
) -> list[WorkItemDocumentDraft]:
    return db.scalars(
        select(WorkItemDocumentDraft)
        .where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.is_current.is_(True),
        )
        .order_by(
            WorkItemDocumentDraft.created_at,
            WorkItemDocumentDraft.document_type,
        )
    ).all()


def _work_item_document_draft_response(
    draft: WorkItemDocumentDraft,
) -> WorkItemDocumentDraftResponse:
    return WorkItemDocumentDraftResponse(
        id=draft.id,
        document_type=draft.document_type,
        content=draft.content,
        verification_questions=draft.verification_questions or [],
        status=draft.status,
        version=draft.version,
        generator=draft.generator,
        change_request=draft.change_request,
        approved_by_name=(
            draft.approved_by.full_name if draft.approved_by is not None else None
        ),
        approved_at=_as_utc(draft.approved_at) if draft.approved_at else None,
        created_at=_as_utc(draft.created_at),
        updated_at=_as_utc(draft.updated_at),
    )


def _replace_work_item_document_draft(
    db: Session,
    *,
    item: WorkItem,
    document_type: str,
    content: str,
    verification_questions: list[str],
    generator: str,
    created_by_id: UUID | None,
    change_request: str | None = None,
    status_value: str = "draft",
) -> WorkItemDocumentDraft:
    current_drafts = db.scalars(
        select(WorkItemDocumentDraft).where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.document_type == document_type,
            WorkItemDocumentDraft.is_current.is_(True),
        )
    ).all()
    for current in current_drafts:
        current.is_current = False
    latest_version = db.scalar(
        select(func.max(WorkItemDocumentDraft.version)).where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.document_type == document_type,
        )
    )
    draft = WorkItemDocumentDraft(
        organization_id=item.organization_id,
        work_item_id=item.id,
        document_type=document_type,
        content=content.strip(),
        verification_questions=list(dict.fromkeys(verification_questions))[:12],
        status=status_value,
        version=int(latest_version or 0) + 1,
        is_current=True,
        generator=generator[:120],
        change_request=change_request,
        created_by_id=created_by_id,
    )
    db.add(draft)
    db.flush()
    return draft


def _sync_work_item_document_drafts(
    db: Session,
    *,
    item: WorkItem,
    suggestion: RecordDraft,
    generator: str,
    actor_id: UUID | None,
) -> None:
    current_by_type = {
        draft.document_type: draft
        for draft in _current_work_item_document_drafts(db, item)
    }
    proposed_types: set[str] = set()
    for proposal in suggestion.document_drafts:
        proposed_types.add(proposal.document_type)
        current = current_by_type.get(proposal.document_type)
        if (
            current is not None
            and current.status == "draft"
            and current.content.strip() == proposal.content.strip()
            and (current.verification_questions or [])
            == proposal.verification_questions
        ):
            continue
        _replace_work_item_document_draft(
            db,
            item=item,
            document_type=proposal.document_type,
            content=proposal.content,
            verification_questions=proposal.verification_questions,
            generator=generator,
            created_by_id=actor_id,
        )
    for document_type, current in current_by_type.items():
        if document_type not in proposed_types and current.status != "approved":
            current.is_current = False


def _approve_selected_document_drafts(
    db: Session,
    *,
    item: WorkItem,
    document_types: list[str],
    processor: User,
) -> None:
    approved_at = utcnow()
    selected_types = set(document_types)
    for draft in _current_work_item_document_drafts(db, item):
        if draft.document_type not in selected_types or draft.status == "not_used":
            continue
        draft.status = "approved"
        draft.approved_by_id = processor.id
        draft.approved_at = approved_at


def _refresh_work_item_suggestion(db: Session, item: WorkItem) -> bool:
    if (
        item.confirmed_at is not None
        or item.status == "dismissed"
        or item.resident_id is None
        or _has_pending_message_resident_candidates(
            db,
            item.source_message_id,
        )
    ):
        return False
    snapshot = _work_item_ai_snapshot(db, item)
    comment_ids = snapshot.get("comment_ids", [])
    previous_comment_ids = (item.ai_payload or {}).get("source_comment_ids", [])
    if item.ai_payload is not None and previous_comment_ids == comment_ids:
        return False
    suggestion = RecordDraft.model_validate(build_prototype_suggestion(snapshot))
    payload = suggestion.model_dump(mode="json")
    payload["source_comment_ids"] = comment_ids
    item.ai_state = "prototype_suggested"
    item.ai_payload = payload
    item.ai_generator = PROTOTYPE_GENERATOR
    item.ai_generated_at = utcnow()
    item.document_types = suggestion.document_types
    if item.status == "pending":
        item.status = "in_review"
    return True


def _work_item_response(
    db: Session,
    item: WorkItem,
    *,
    viewer_id: UUID | None = None,
) -> WorkItemResponse:
    room = db.get(Room, item.source_message.room_id)
    comments = _work_item_comments(db, item)
    return WorkItemResponse(
        id=item.id,
        status=item.status,
        source_snapshot=item.source_snapshot,
        message=message_response(item.source_message, db=db, viewer_id=viewer_id),
        comments=[
            MessageCommentResponse(
                id=comment.id,
                author_id=comment.author_id,
                author_name=comment.author.full_name,
                body=comment.body,
                created_at=_as_utc(comment.created_at),
            )
            for comment in comments
        ],
        room_name=room.name if room is not None else "삭제된 채팅방",
        resident=resident_response(item.resident),
        document_types=item.document_types or [],
        processing_notes=item.processing_notes,
        handled_by_name=item.handled_by.full_name if item.handled_by else None,
        ai_state=item.ai_state,
        ai_suggestion=item.ai_payload,
        ai_generator=item.ai_generator,
        ai_generated_at=_as_utc(item.ai_generated_at) if item.ai_generated_at else None,
        confirmed_record=item.confirmed_payload,
        confirmed_by_name=item.confirmed_by.full_name if item.confirmed_by else None,
        confirmed_at=_as_utc(item.confirmed_at) if item.confirmed_at else None,
        document_drafts=[
            _work_item_document_draft_response(draft)
            for draft in _current_work_item_document_drafts(db, item)
        ],
        created_at=_as_utc(item.created_at),
        updated_at=_as_utc(item.updated_at),
    )


def _work_item_for_processor(
    db: Session, processor: User, work_item_id: UUID
) -> WorkItem:
    item = db.get(WorkItem, work_item_id)
    if item is None:
        raise HTTPException(status_code=404, detail="업무 항목을 찾을 수 없습니다.")
    if item.organization_id != processor.organization_id:
        raise HTTPException(status_code=403, detail="이 업무 항목을 처리할 수 없습니다.")
    sender = item.source_message.sender
    if (
        processor.role != "admin"
        and (
            processor.business is None
            or sender.business is None
            or sender.business.id != processor.business.id
        )
    ):
        raise HTTPException(status_code=403, detail="이 업무 항목을 처리할 수 없습니다.")
    return item


def _visible_work_items_for_processor(
    db: Session,
    processor: User,
    *,
    status_filter: str | None = None,
) -> list[WorkItem]:
    query = (
        select(WorkItem)
        .join(Message, Message.id == WorkItem.source_message_id)
        .where(WorkItem.organization_id == processor.organization_id)
        .order_by(WorkItem.updated_at.desc(), WorkItem.created_at.desc())
    )
    if status_filter:
        query = query.where(WorkItem.status == status_filter)
    items = db.scalars(query).unique().all()
    if processor.role == "admin":
        return list(items)
    if processor.business is None:
        return []
    return [
        item
        for item in items
        if item.source_message.sender.business is not None
        and item.source_message.sender.business.id == processor.business.id
    ]


async def _store_attachment(
    db: Session,
    *,
    upload: UploadFile,
    message: Message,
    user: User,
) -> tuple[MessageAttachment, Path]:
    mime_type = (upload.content_type or "").lower()
    extension = ATTACHMENT_EXTENSIONS.get(mime_type)
    if extension is None:
        raise HTTPException(
            status_code=422,
            detail=(
                "파일은 JPG·PNG·WEBP 이미지, MP3·WAV·M4A·WEBM·OGG·AAC 음성, "
                "MP4·WEBM·MOV 동영상, PDF 형식만 첨부할 수 있습니다."
            ),
        )
    content = await upload.read(settings.max_attachment_bytes + 1)
    if len(content) > settings.max_attachment_bytes:
        raise HTTPException(
            status_code=413,
            detail=(
                f"파일 하나는 {settings.max_attachment_bytes // (1024 * 1024)}MB "
                "이하여야 합니다."
            ),
        )
    if not content:
        raise HTTPException(status_code=422, detail="빈 파일은 첨부할 수 없습니다.")
    if not _has_expected_signature(mime_type, content):
        raise HTTPException(
            status_code=422,
            detail="파일 내용과 표시된 형식이 일치하지 않습니다.",
        )
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    storage_key = f"{uuid4().hex}{extension}"
    target = (upload_dir / storage_key).resolve()
    if target.parent != upload_dir:
        raise HTTPException(status_code=422, detail="올바르지 않은 파일 경로입니다.")
    target.write_bytes(content)
    attachment = MessageAttachment(
        organization_id=message.organization_id,
        owner_module_code="staff_hub",
        entity_type="staff_hub_message",
        message_id=message.id,
        uploader_id=user.id,
        storage_key=storage_key,
        original_name=Path(upload.filename or f"attachment{extension}").name[:255],
        mime_type=mime_type,
        size_bytes=len(content),
        sha256=sha256(content).hexdigest(),
    )
    db.add(attachment)
    return attachment, target


def _queue_attachment_text_extraction(
    db: Session,
    *,
    attachment: MessageAttachment,
    requested_by: User,
) -> AttachmentTextExtraction:
    is_audio = attachment.mime_type in AUDIO_MIME_TYPES
    provider = settings.stt_provider if is_audio else settings.ocr_provider
    model_name = settings.stt_model if is_audio else settings.ocr_model
    if attachment.id is None:
        db.flush()
    extraction = db.scalar(
        select(AttachmentTextExtraction).where(
            AttachmentTextExtraction.attachment_id == attachment.id
        )
    )
    if extraction is None:
        extraction = AttachmentTextExtraction(
            organization_id=attachment.organization_id,
            attachment_id=attachment.id,
            status="pending",
            provider=provider,
            model_name=model_name,
            requested_by_id=requested_by.id,
        )
        db.add(extraction)
    else:
        extraction.status = "pending"
        extraction.provider = provider
        extraction.model_name = model_name
        extraction.extracted_text = None
        extraction.reviewed_text = None
        extraction.visual_signature = None
        extraction.error_message = None
        extraction.requested_by_id = requested_by.id
        extraction.reviewed_by_id = None
        extraction.started_at = None
        extraction.completed_at = None
        extraction.reviewed_at = None
    return extraction


def _run_attachment_text_extraction(attachment_id: UUID) -> None:
    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        extraction = db.scalar(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id == attachment_id
            )
        )
        if attachment is None or extraction is None:
            return
        extraction.status = "processing"
        extraction.started_at = utcnow()
        extraction.error_message = None
        db.commit()
        target = (Path(settings.upload_dir).resolve() / attachment.storage_key).resolve()
        try:
            if target.parent != Path(settings.upload_dir).resolve() or not target.is_file():
                raise OcrError("판독할 원본 첨부파일을 찾을 수 없습니다.")
            if attachment.mime_type in IMAGE_MIME_TYPES:
                result = extract_report_text(
                    target,
                    room_name=attachment.message.room.name,
                    resident_name=(
                        attachment.message.resident.display_name
                        if attachment.message.resident is not None
                        else None
                    ),
                )
                resident_link_source = "ocr_exact"
            elif attachment.mime_type in AUDIO_MIME_TYPES:
                result = transcribe_audio(
                    target,
                    mime_type=attachment.mime_type,
                )
                resident_link_source = "audio_transcript"
            else:
                raise OcrError("이미지 또는 음성파일만 판독할 수 있습니다.")
        except Exception as exc:  # 채팅 전송과 백그라운드 판독 실패를 분리한다.
            extraction.status = "failed"
            extraction.error_message = str(exc)[:2000]
            extraction.completed_at = utcnow()
        else:
            extraction.status = "completed"
            extraction.extracted_text = result[:12000]
            if extraction.original_extracted_text is None:
                extraction.original_extracted_text = extraction.extracted_text
            extraction.visual_signature = (
                extract_page_visual_signature(target)
                if attachment.mime_type in IMAGE_MIME_TYPES
                else None
            )
            extraction.completed_at = utcnow()
            _sync_message_resident_candidates(
                db,
                message=attachment.message,
                text=extraction.extracted_text,
                source=resident_link_source,
            )
            _ensure_work_item(db, attachment.message, force=True)
        db.commit()


def bootstrap_database() -> None:
    if settings.auto_create_schema:
        Base.metadata.create_all(bind=engine)
    Path(settings.upload_dir).resolve().mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        organization = ensure_reference_data(db)
        ensure_system_rooms(db, organization)
        if settings.bootstrap_admin_username and settings.bootstrap_admin_password:
            ensure_bootstrap_admin(
                db,
                username=settings.bootstrap_admin_username,
                password=settings.bootstrap_admin_password,
                display_name=settings.bootstrap_admin_name,
            )
        if settings.dev_launcher_active and settings.dev_launcher_password:
            ensure_developer_launcher_user(
                db,
                username=settings.dev_launcher_username,
                password=settings.dev_launcher_password.get_secret_value(),
                display_name=settings.dev_launcher_name,
            )
        active_users = db.scalars(
            select(User).where(
                User.organization_id == organization.id,
                User.is_active.is_(True),
            )
        ).all()
        for user in active_users:
            if user.staff is not None and user.employment_status == "active":
                sync_auto_memberships(db, user)
        db.commit()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    bootstrap_database()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.1.0",
    openapi_url="/api/openapi.json" if settings.environment == "development" else None,
    docs_url="/api/docs" if settings.environment == "development" else None,
    redoc_url=None,
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)

REVIEWER_UUID_PATH = (
    r"[0-9a-fA-F]{8}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{12}"
)
REVIEWER_CLIENT_COOKIE_NAME = "mesil_reviewer_client"
REVIEWER_CLIENT_COOKIE_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVIEWER_ALLOWED_READ_PATHS = (
    re.compile(r"^/api/auth/me$"),
    re.compile(r"^/api/rooms$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/messages$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/residents$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/message-search$"),
    re.compile(rf"^/api/messages/{REVIEWER_UUID_PATH}$"),
    re.compile(rf"^/api/attachments/{REVIEWER_UUID_PATH}$"),
    re.compile(rf"^/api/workdesk/attachments/{REVIEWER_UUID_PATH}$"),
    re.compile(r"^/api/workdesk/residents$"),
)
REVIEWER_ALLOWED_WRITE_PATHS = (
    re.compile(r"^/api/auth/reviewer-session$"),
    re.compile(r"^/api/auth/logout$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/message-search/summary$"),
    re.compile(
        rf"^/api/rooms/{REVIEWER_UUID_PATH}/messages(?:-with-(?:files|photos))?$"
    ),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/read$"),
    re.compile(rf"^/api/messages/{REVIEWER_UUID_PATH}/comments(?:/read)?$"),
    re.compile(r"^/api/workdesk/period-review$"),
    re.compile(r"^/api/workdesk/record-summary$"),
)


@app.middleware("http")
async def protect_unsafe_origins(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in settings.origin_list:
            return Response(status_code=403, content="허용되지 않은 요청 출처입니다.")
    return await call_next(request)


@app.middleware("http")
async def restrict_reviewer_writes(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    token = request.cookies.get(settings.session_cookie_name, "")
    if not token.startswith("rv1."):
        return await call_next(request)
    if request.method in {"GET", "HEAD"}:
        if any(
            pattern.fullmatch(request.url.path)
            for pattern in REVIEWER_ALLOWED_READ_PATHS
        ):
            return await call_next(request)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": (
                    "심사위원 체험에서는 기존 업무함 원자료를 열 수 없습니다. "
                    "지정된 심사방과 AI 돌봄 브리핑만 확인해 주세요."
                )
            },
        )
    if any(pattern.fullmatch(request.url.path) for pattern in REVIEWER_ALLOWED_WRITE_PATHS):
        return await call_next(request)
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
            "detail": (
                "심사위원 체험에서는 채팅·읽음·AI 브리핑만 사용할 수 있습니다. "
                "직원·조직·공식 기록은 변경되지 않습니다."
            )
        },
    )


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "smcodi-chat", "environment": settings.environment}


def _reviewer_account(
    db: Session,
    experience: str,
) -> User:
    usernames = {
        "care": settings.reviewer_care_username,
        "social_worker": settings.reviewer_social_username,
        "realtime_secondary": settings.reviewer_secondary_username,
    }
    username = usernames.get(experience)
    user = (
        db.scalar(select(User).where(User.username == username))
        if username
        else None
    )
    expected_processor_access = experience == "social_worker"
    valid = (
        user is not None
        and user.role == "staff"
        and user.is_active
        and user.employment_status == "active"
        and user.staff is not None
        and user.staff.is_test_data
        and user.staff.is_active
        and user.staff.deleted_at is None
        and not user.must_change_password
        and user.can_process_records == expected_processor_access
    )
    if not valid:
        record_audit(
            db,
            actor_id=None,
            action="auth.reviewer_session_blocked",
            target_type="user",
            target_id=user.id if user is not None else None,
            details={
                "experience": experience,
                "reason": "unsafe_account_configuration",
            },
        )
        db.commit()
        logger.error(
            "reviewer_session_blocked experience=%s reason=unsafe_account",
            experience,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="심사위원 체험 계정을 준비 중입니다.",
        )
    return user


def _block_reviewer_destination(
    db: Session,
    *,
    user: User,
    experience: str,
    reason: str,
    room_id: UUID | None = None,
) -> NoReturn:
    record_audit(
        db,
        actor_id=user.id,
        action="auth.reviewer_session_blocked",
        target_type="room",
        target_id=room_id,
        details={"experience": experience, "reason": reason},
    )
    db.commit()
    logger.error(
        "reviewer_session_blocked experience=%s reason=%s",
        experience,
        reason,
    )
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="심사위원 체험 자료를 안전하게 준비 중입니다.",
    )


def _reviewer_target_room(
    db: Session,
    *,
    user: User,
    experience: str,
) -> Room:
    rooms = list(
        db.scalars(
            select(Room).where(
                Room.organization_id == user.organization_id,
                Room.name == settings.reviewer_chat_room_name,
                Room.is_active.is_(True),
            )
        ).all()
    )
    if len(rooms) != 1:
        _block_reviewer_destination(
            db,
            user=user,
            experience=experience,
            reason="reviewer_room_not_unique",
        )
    room = rooms[0]
    if not room.is_test_data:
        _block_reviewer_destination(
            db,
            user=user,
            experience=experience,
            reason="reviewer_room_not_test_data",
            room_id=room.id,
        )
    membership = db.scalar(
        select(RoomMembership.id).where(
            RoomMembership.room_id == room.id,
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
    )
    if membership is None:
        _block_reviewer_destination(
            db,
            user=user,
            experience=experience,
            reason="reviewer_membership_unavailable",
            room_id=room.id,
        )
    unsafe_member_count = db.scalar(
        select(func.count(RoomMembership.id))
        .join(Staff, Staff.id == RoomMembership.staff_id)
        .where(
            RoomMembership.room_id == room.id,
            RoomMembership.left_at.is_(None),
            or_(
                Staff.is_test_data.is_(False),
                Staff.is_active.is_(False),
                Staff.deleted_at.is_not(None),
            ),
        )
    )
    unsafe_message_count = db.scalar(
        select(func.count(Message.id))
        .join(User, User.id == Message.sender_id)
        .join(Staff, Staff.id == User.staff_id)
        .where(
            Message.room_id == room.id,
            or_(
                Message.is_test_data.is_(False),
                Staff.is_test_data.is_(False),
            ),
        )
    )
    unsafe_direct_resident_count = db.scalar(
        select(func.count(Message.id))
        .join(Resident, Resident.id == Message.resident_id)
        .where(
            Message.room_id == room.id,
            Message.resident_id.is_not(None),
            Resident.is_test_data.is_(False),
        )
    )
    unsafe_linked_resident_count = db.scalar(
        select(func.count(MessageResidentLink.id))
        .join(Message, Message.id == MessageResidentLink.message_id)
        .join(Resident, Resident.id == MessageResidentLink.resident_id)
        .where(
            Message.room_id == room.id,
            Resident.is_test_data.is_(False),
        )
    )
    if any(
        int(count or 0) > 0
        for count in (
            unsafe_member_count,
            unsafe_message_count,
            unsafe_direct_resident_count,
            unsafe_linked_resident_count,
        )
    ):
        _block_reviewer_destination(
            db,
            user=user,
            experience=experience,
            reason="reviewer_scope_contains_non_test_data",
            room_id=room.id,
        )
    return room


def _reviewer_destination(
    db: Session,
    *,
    user: User,
    experience: str,
) -> tuple[str, UUID | None]:
    room = _reviewer_target_room(
        db,
        user=user,
        experience=experience,
    )
    if experience == "social_worker":
        return "care_briefing", None
    return "chat", room.id


def _reviewer_rate_retry_after(
    db: Session,
    *,
    client_key: str,
) -> int | None:
    usernames = [
        username
        for username in (
            settings.reviewer_care_username,
            settings.reviewer_social_username,
            settings.reviewer_secondary_username,
        )
        if username
    ]
    reviewer_user_ids = db.scalars(
        select(User.id).where(User.username.in_(usernames))
    ).all()
    if not reviewer_user_ids:
        return None
    now = utcnow()
    window = timedelta(minutes=settings.reviewer_rate_window_minutes)
    cutoff = now - window
    sessions = db.scalars(
        select(LoginSession)
        .where(
            LoginSession.user_id.in_(reviewer_user_ids),
            LoginSession.client_key == client_key,
            LoginSession.created_at >= cutoff,
            LoginSession.user_agent.like(
                f"{REVIEWER_SESSION_USER_AGENT_PREFIX}%"
            ),
        )
        .order_by(LoginSession.created_at)
    ).all()
    if len(sessions) < settings.reviewer_rate_limit:
        return None
    seconds = (
        _as_utc(sessions[0].created_at) + window - now
    ).total_seconds()
    return max(1, int(seconds) + 1)


def _reviewer_client_key(
    request: Request,
    response: Response,
    *,
    access_end: datetime,
    now: datetime,
) -> str:
    """브라우저별 익명 식별값으로 심사 체험 발급 제한을 분리합니다."""
    browser_token = request.cookies.get(REVIEWER_CLIENT_COOKIE_NAME, "")
    if not REVIEWER_CLIENT_COOKIE_PATTERN.fullmatch(browser_token):
        browser_token = f"{uuid4().hex}{uuid4().hex}"
    response.set_cookie(
        key=REVIEWER_CLIENT_COOKIE_NAME,
        value=browser_token,
        max_age=max(1, int((access_end - now).total_seconds())),
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite="lax",
        path="/",
    )
    return sha256(
        f"reviewer-browser:{browser_token}".encode("utf-8")
    ).hexdigest()


def _reviewer_response(
    *,
    user: User,
    login_session: LoginSession,
    experience: str,
    destination: str,
    room_id: UUID | None,
) -> ReviewerSessionResponse:
    return ReviewerSessionResponse(
        user=user_response(user, reviewer_experience=experience),
        expires_at=login_session.expires_at,
        destination=destination,
        room_id=room_id,
    )


def _block_reviewer_account_setting(login_session: LoginSession) -> None:
    if getattr(login_session, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="심사위원 체험 세션에서는 이 설정을 변경할 수 없습니다.",
        )


@app.post(
    "/api/auth/reviewer-session",
    response_model=ReviewerSessionResponse,
)
def create_reviewer_session(
    payload: ReviewerSessionRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> ReviewerSessionResponse:
    if not settings.reviewer_access_active:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="심사위원 체험 기간이 아닙니다.",
        )
    user = _reviewer_account(db, payload.experience)
    destination, room_id = _reviewer_destination(
        db,
        user=user,
        experience=payload.experience,
    )
    now = utcnow()
    configured_access_end = settings.reviewer_access_ends_at
    if configured_access_end is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="심사위원 체험 기간이 아닙니다.",
        )
    configured_end = _as_utc(configured_access_end)
    client_key = _reviewer_client_key(
        request,
        response,
        access_end=configured_end,
        now=now,
    )
    expires_at = min(
        now + timedelta(minutes=settings.reviewer_session_minutes),
        configured_end,
    )
    switching_session: LoginSession | None = None
    previous_experience: str | None = None
    current_token = request.cookies.get(settings.session_cookie_name)
    if current_token:
        current_session = db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == token_digest(current_token)
            )
        )
        if current_session is not None and current_session.revoked_at is None:
            current_user = db.get(User, current_session.user_id)
            try:
                current_context = (
                    validate_reviewer_session_user(
                        current_token,
                        current_user,
                        now=now,
                    )
                    if current_user is not None
                    else None
                )
            except InvalidReviewerSessionToken:
                current_context = None
            if (
                current_context is not None
                and current_context.experience == payload.experience
                and current_session.user_id == user.id
                and _as_utc(current_session.expires_at) > now
            ):
                current_session.last_seen_at = now
                current_session.client_key = client_key
                max_age = max(
                    1,
                    int(
                        (
                            _as_utc(current_session.expires_at) - now
                        ).total_seconds()
                    ),
                )
                response.set_cookie(
                    key=settings.session_cookie_name,
                    value=current_token,
                    max_age=max_age,
                    httponly=True,
                    secure=secure_cookie_for_request(request),
                    samesite="lax",
                    path="/",
                )
                response.delete_cookie(
                    settings.dev_launcher_cookie_name,
                    path="/",
                )
                record_audit(
                    db,
                    actor_id=user.id,
                    action="auth.reviewer_session_reused",
                    target_type="session",
                    target_id=current_session.id,
                    details={
                        "experience": payload.experience,
                        "client_key": client_key[:12],
                    },
                )
                db.commit()
                return _reviewer_response(
                    user=user,
                    login_session=current_session,
                    experience=payload.experience,
                    destination=destination,
                    room_id=room_id,
                )
            if (
                current_context is not None
                and is_reviewer_login_session(current_session)
                and _as_utc(current_session.expires_at) > now
            ):
                switching_session = current_session
                previous_experience = current_context.experience
            # 일반 직원 로그인을 체험 화면으로 전환할 때는 브라우저 쿠키만
            # 새 체험 세션으로 교체합니다. DB의 원래 직원 세션까지 종료하면
            # 사용자가 다른 기기/창에서 정상 업무를 이어갈 수 없게 됩니다.
            if (
                switching_session is None
                and is_reviewer_login_session(current_session)
            ):
                current_session.revoked_at = now

    retry_after = (
        None
        if switching_session is not None
        else _reviewer_rate_retry_after(db, client_key=client_key)
    )
    if retry_after is not None:
        record_audit(
            db,
            actor_id=None,
            action="auth.reviewer_session_rate_limited",
            target_type="user",
            target_id=user.id,
            details={
                "experience": payload.experience,
                "client_key": client_key[:12],
                "retry_after": retry_after,
            },
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="체험 화면 전환이 너무 잦습니다. 잠시 후 다시 시도해 주세요.",
            headers={"Retry-After": str(retry_after)},
        )

    if switching_session is not None:
        token = create_reviewer_session_token(payload.experience, expires_at)
        switching_session.user_id = user.id
        switching_session.token_hash = token_digest(token)
        switching_session.expires_at = expires_at
        switching_session.last_seen_at = now
        switching_session.revoked_at = None
        switching_session.user_agent = reviewer_session_user_agent(
            payload.experience,
            request.headers.get("user-agent"),
        )
        switching_session.client_key = client_key
        switching_session.impersonated_by_user_id = None
        user.last_login_at = now
        max_age = max(1, int((expires_at - now).total_seconds()))
        response.set_cookie(
            key=settings.session_cookie_name,
            value=token,
            max_age=max_age,
            httponly=True,
            secure=secure_cookie_for_request(request),
            samesite="lax",
            path="/",
        )
        response.delete_cookie(settings.dev_launcher_cookie_name, path="/")
        record_audit(
            db,
            actor_id=user.id,
            action="auth.reviewer_session_switched",
            target_type="session",
            target_id=switching_session.id,
            details={
                "from_experience": previous_experience,
                "to_experience": payload.experience,
                "client_key": client_key[:12],
                "expires_at": expires_at.isoformat(),
                "destination": destination,
                "room_id": str(room_id) if room_id is not None else None,
            },
        )
        db.commit()
        return _reviewer_response(
            user=user,
            login_session=switching_session,
            experience=payload.experience,
            destination=destination,
            room_id=room_id,
        )

    reviewer_usernames = [
        username
        for username in (
            settings.reviewer_care_username,
            settings.reviewer_social_username,
            settings.reviewer_secondary_username,
        )
        if username
    ]
    reviewer_user_ids = list(
        db.scalars(
            select(User.id).where(User.username.in_(reviewer_usernames))
        ).all()
    )
    active_reviewer_sessions = [
        item
        for item in db.scalars(
            select(LoginSession)
            .where(
                LoginSession.user_id.in_(reviewer_user_ids),
                LoginSession.client_key == client_key,
                LoginSession.revoked_at.is_(None),
                LoginSession.expires_at > now,
                LoginSession.user_agent.like(
                    f"{REVIEWER_SESSION_USER_AGENT_PREFIX}%"
                ),
            )
            .order_by(LoginSession.created_at.desc())
        ).all()
        if is_reviewer_login_session(item)
    ]
    keep_before_create = max(
        0,
        settings.reviewer_session_limit_per_client - 1,
    )
    for stale_session in active_reviewer_sessions[keep_before_create:]:
        stale_session.revoked_at = now

    token = create_reviewer_session_token(payload.experience, expires_at)
    user.last_login_at = now
    _, login_session = create_login_session(
        db,
        user,
        reviewer_session_user_agent(
            payload.experience,
            request.headers.get("user-agent"),
        ),
        client_key,
        session_token=token,
        expires_at_override=expires_at,
    )
    max_age = max(1, int((expires_at - now).total_seconds()))
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=max_age,
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite="lax",
        path="/",
    )
    response.delete_cookie(settings.dev_launcher_cookie_name, path="/")
    record_audit(
        db,
        actor_id=user.id,
        action="auth.reviewer_session_issued",
        target_type="session",
        target_id=login_session.id,
        details={
            "experience": payload.experience,
            "client_key": client_key[:12],
            "expires_at": expires_at.isoformat(),
            "destination": destination,
            "room_id": str(room_id) if room_id is not None else None,
        },
    )
    db.commit()
    return _reviewer_response(
        user=user,
        login_session=login_session,
        experience=payload.experience,
        destination=destination,
        room_id=room_id,
    )


@app.post("/api/auth/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    username = payload.username.strip()
    reviewer_usernames = {
        candidate
        for candidate in (
            settings.reviewer_care_username,
            settings.reviewer_social_username,
            settings.reviewer_secondary_username,
        )
        if candidate
    }
    if settings.reviewer_access_active and username in reviewer_usernames:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="심사 체험 계정은 심사위원 체험 화면에서만 이용할 수 있습니다.",
        )
    login_password = (
        payload.password.strip()
        if username == settings.dev_launcher_username
        else payload.password
    )
    if (
        username == settings.dev_launcher_username
        and (
            not settings.dev_launcher_active
            or not is_local_development_request(request)
        )
    ):
        raise HTTPException(
            status_code=404,
            detail="요청한 페이지를 찾을 수 없습니다.",
        )
    client_key = client_key_from_request(request)
    retry_after = login_retry_after(db, username, client_key)
    if retry_after is not None:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"로그인 시도가 너무 많습니다. {max(1, (retry_after + 59) // 60)}분 후 다시 시도해 주세요.",
            headers={"Retry-After": str(retry_after)},
        )
    user = db.scalar(select(User).where(User.username == username))
    password_hash = user.password_hash if user else dummy_password_hash
    password_matches = verify_password(login_password, password_hash)
    if user is None or not password_matches:
        record_failed_login(db, username, client_key)
        record_audit(
            db,
            actor_id=user.id if user else None,
            action="auth.login_failed",
            target_type="user",
            target_id=user.id if user else None,
            details={"username": username, "client_key": client_key[:12]},
        )
        db.commit()
        raise HTTPException(status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
    if not user.is_active or user.employment_status != "active":
        record_audit(
            db,
            actor_id=user.id,
            action="auth.login_blocked",
            target_type="user",
            target_id=user.id,
            details={"reason": "terminated"},
        )
        db.commit()
        raise HTTPException(status_code=403, detail="퇴사 처리되어 로그인할 수 없습니다.")
    clear_failed_logins(db, username, client_key)
    user.last_login_at = utcnow()
    token, login_session = create_login_session(
        db,
        user,
        request.headers.get("user-agent"),
        client_key,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.session_hours * 60 * 60,
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite="lax",
        path="/",
    )
    if settings.dev_launcher_active and user.username == settings.dev_launcher_username:
        response.set_cookie(
            key=settings.dev_launcher_cookie_name,
            value=token,
            max_age=settings.session_hours * 60 * 60,
            httponly=True,
            secure=secure_cookie_for_request(request),
            samesite="strict",
            path="/",
        )
    else:
        response.delete_cookie(settings.dev_launcher_cookie_name, path="/")
    record_audit(
        db,
        actor_id=user.id,
        action="auth.login",
        target_type="session",
        target_id=login_session.id,
    )
    db.commit()
    return LoginResponse(
        user=user_response(
            user,
            is_dev_launcher=(
                settings.dev_launcher_active
                and user.username == settings.dev_launcher_username
            ),
        ),
        expires_at=login_session.expires_at,
    )


@app.post("/api/auth/logout", status_code=204)
def logout(
    response: Response,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, user = auth
    login_session.revoked_at = utcnow()
    record_audit(
        db,
        actor_id=user.id,
        action="auth.logout",
        target_type="session",
        target_id=login_session.id,
    )
    db.commit()
    response.delete_cookie(settings.session_cookie_name, path="/")
    if user.username == settings.dev_launcher_username:
        response.delete_cookie(settings.dev_launcher_cookie_name, path="/")


@app.get("/api/auth/me", response_model=UserResponse)
def me(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
) -> UserResponse:
    login_session, user = auth
    return user_response(
        user,
        is_dev_launcher=(
            settings.dev_launcher_active
            and user.username == settings.dev_launcher_username
            and login_session.impersonated_by_user_id is None
        ),
        is_dev_impersonated=login_session.impersonated_by_user_id is not None,
        reviewer_experience=getattr(
            login_session,
            "_reviewer_experience",
            None,
        ),
    )


def _require_dev_controller(
    request: Request,
    db: Session,
) -> tuple[LoginSession, User, str]:
    if (
        not settings.dev_launcher_active
        or not is_local_development_request(request)
    ):
        raise HTTPException(status_code=404, detail="개발자 런처를 사용할 수 없습니다.")
    raw_token = request.cookies.get(settings.dev_launcher_cookie_name)
    if not raw_token:
        raise HTTPException(status_code=401, detail="개발자 런처 로그인이 필요합니다.")
    login_session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == token_digest(raw_token))
    )
    now = utcnow()
    if (
        login_session is None
        or login_session.revoked_at is not None
        or _as_utc(login_session.expires_at) <= now
        or login_session.impersonated_by_user_id is not None
    ):
        raise HTTPException(status_code=401, detail="개발자 런처 로그인이 만료되었습니다.")
    controller = db.get(User, login_session.user_id)
    if (
        controller is None
        or not controller.is_active
        or controller.username != settings.dev_launcher_username
    ):
        raise HTTPException(status_code=403, detail="개발자 런처 계정을 확인할 수 없습니다.")
    return login_session, controller, raw_token


@app.get("/api/dev/users", response_model=list[UserResponse])
def list_dev_launcher_users(
    request: Request,
    db: Session = Depends(get_db),
) -> list[UserResponse]:
    _, controller, _ = _require_dev_controller(request, db)
    users = db.scalars(
        select(User)
        .join(Staff, Staff.id == User.staff_id)
        .where(
            User.organization_id == controller.organization_id,
            User.id != controller.id,
            Staff.deleted_at.is_(None),
        )
        .order_by(User.display_name, User.username)
    ).all()
    return [user_response(user) for user in users]


@app.post("/api/dev/switch/{target_user_id}", response_model=LoginResponse)
def switch_dev_launcher_user(
    target_user_id: UUID,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    _, controller, _ = _require_dev_controller(request, db)
    target = db.get(User, target_user_id)
    if (
        target is None
        or target.organization_id != controller.organization_id
        or target.id == controller.id
    ):
        raise HTTPException(status_code=404, detail="시험할 사용자를 찾을 수 없습니다.")
    if not target.is_active or target.employment_status != "active":
        raise HTTPException(status_code=409, detail="퇴사·휴직 계정은 사용자 전환할 수 없습니다.")

    current_token = request.cookies.get(settings.session_cookie_name)
    if current_token:
        current_session = db.scalar(
            select(LoginSession).where(
                LoginSession.token_hash == token_digest(current_token)
            )
        )
        if (
            current_session is not None
            and current_session.impersonated_by_user_id == controller.id
            and current_session.revoked_at is None
        ):
            current_session.revoked_at = utcnow()

    token, target_session = create_login_session(
        db,
        target,
        f"SMCODI 개발자 런처: {request.headers.get('user-agent', '')}",
        client_key_from_request(request),
        impersonated_by_user_id=controller.id,
        expires_in_minutes=settings.dev_impersonation_minutes,
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=settings.dev_impersonation_minutes * 60,
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite="lax",
        path="/",
    )
    record_audit(
        db,
        actor_id=controller.id,
        action="dev_launcher.user_switched",
        target_type="user",
        target_id=target.id,
        details={
            "target_username": target.username,
            "session_id": str(target_session.id),
        },
    )
    db.commit()
    return LoginResponse(
        user=user_response(target, is_dev_impersonated=True),
        expires_at=target_session.expires_at,
    )


@app.post("/api/dev/return", response_model=LoginResponse)
def return_to_dev_launcher(
    request: Request,
    response: Response,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
) -> LoginResponse:
    current_session, current_user = auth
    controller_recovered = False
    try:
        controller_session, controller, raw_token = _require_dev_controller(request, db)
    except HTTPException as error:
        if (
            error.status_code != status.HTTP_401_UNAUTHORIZED
            or current_session.impersonated_by_user_id is None
        ):
            raise
        controller = db.get(User, current_session.impersonated_by_user_id)
        if (
            controller is None
            or not controller.is_active
            or controller.username != settings.dev_launcher_username
        ):
            raise HTTPException(
                status_code=403,
                detail="개발자 런처 계정을 확인할 수 없습니다.",
            ) from error
        raw_token, controller_session = create_login_session(
            db,
            controller,
            request.headers.get("user-agent"),
            client_key_from_request(request),
        )
        controller_recovered = True
        response.set_cookie(
            key=settings.dev_launcher_cookie_name,
            value=raw_token,
            max_age=settings.session_hours * 60 * 60,
            httponly=True,
            secure=secure_cookie_for_request(request),
            samesite="strict",
            path="/",
        )
        record_audit(
            db,
            actor_id=controller.id,
            action="dev_launcher.controller_recovered",
            target_type="session",
            target_id=controller_session.id,
            details={
                "impersonated_session_id": str(current_session.id),
                "impersonated_username": current_user.username,
            },
        )

    if (
        current_session.id != controller_session.id
        and current_session.impersonated_by_user_id == controller.id
        and current_session.revoked_at is None
    ):
        current_session.revoked_at = utcnow()
    remaining_seconds = max(
        1,
        int((_as_utc(controller_session.expires_at) - utcnow()).total_seconds()),
    )
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_token,
        max_age=remaining_seconds,
        httponly=True,
        secure=secure_cookie_for_request(request),
        samesite="lax",
        path="/",
    )
    record_audit(
        db,
        actor_id=controller.id,
        action="dev_launcher.returned",
        target_type="session",
        target_id=controller_session.id,
        details={"controller_recovered": controller_recovered},
    )
    db.commit()
    return LoginResponse(
        user=user_response(controller, is_dev_launcher=True),
        expires_at=controller_session.expires_at,
    )


@app.post("/api/auth/password", response_model=UserResponse)
async def change_password(
    payload: PasswordChangeRequest,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if verify_password(payload.new_password, user.password_hash):
        raise HTTPException(status_code=400, detail="새 비밀번호는 현재 비밀번호와 달라야 합니다.")
    now = utcnow()
    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    user.password_changed_at = now
    other_sessions = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.id != current_session.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    for login_session in other_sessions:
        login_session.revoked_at = now
    record_audit(
        db,
        actor_id=user.id,
        action="auth.password_changed",
        target_type="user",
        target_id=user.id,
        details={"revoked_other_sessions": len(other_sessions)},
    )
    db.commit()
    await manager.force_logout_sessions(
        user.id,
        {item.id for item in other_sessions},
        "비밀번호가 변경되어 이 기기의 접속이 종료되었습니다.",
    )
    return user_response(user)


@app.get("/api/auth/sessions", response_model=list[SessionResponse])
def list_sessions(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    now = utcnow()
    sessions = db.scalars(
        select(LoginSession)
        .where(
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
        .order_by(LoginSession.last_seen_at.desc())
    ).all()
    return [
        SessionResponse(
            id=item.id,
            created_at=_as_utc(item.created_at),
            expires_at=_as_utc(item.expires_at),
            last_seen_at=_as_utc(item.last_seen_at),
            user_agent=item.user_agent,
            is_current=item.id == current_session.id,
        )
        for item in sessions
        if _as_utc(item.expires_at) > now
    ]


@app.delete("/api/auth/sessions/{session_id}", status_code=204)
async def revoke_session(
    session_id: UUID,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    if session_id == current_session.id:
        raise HTTPException(status_code=409, detail="현재 기기는 로그아웃 버튼으로 종료해 주세요.")
    target = db.scalar(
        select(LoginSession).where(
            LoginSession.id == session_id,
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
    )
    if target is None:
        raise HTTPException(status_code=404, detail="종료할 로그인 기기를 찾을 수 없습니다.")
    target.revoked_at = utcnow()
    record_audit(
        db,
        actor_id=user.id,
        action="auth.session_revoked",
        target_type="session",
        target_id=target.id,
    )
    db.commit()
    await manager.force_logout_sessions(
        user.id,
        {target.id},
        "다른 기기에서 이 로그인을 종료했습니다.",
    )


@app.post("/api/auth/sessions/revoke-others", status_code=204)
async def revoke_other_sessions(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    now = utcnow()
    targets = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.id != current_session.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    for target in targets:
        target.revoked_at = now
    record_audit(
        db,
        actor_id=user.id,
        action="auth.other_sessions_revoked",
        target_type="user",
        target_id=user.id,
        details={"revoked_sessions": len(targets)},
    )
    db.commit()
    await manager.force_logout_sessions(
        user.id,
        {item.id for item in targets},
        "다른 기기에서 이 로그인을 종료했습니다.",
    )


def _org_unit_usage(
    db: Session,
    unit_ids: list[UUID],
) -> tuple[dict[UUID, int], dict[UUID, int]]:
    if not unit_ids:
        return {}, {}
    staff_counts = dict(
        db.execute(
            select(
                StaffOrganizationAssignment.unit_id,
                func.count(func.distinct(StaffOrganizationAssignment.staff_id)),
            )
            .join(Staff, Staff.id == StaffOrganizationAssignment.staff_id)
            .where(
                StaffOrganizationAssignment.unit_id.in_(unit_ids),
                StaffOrganizationAssignment.end_date.is_(None),
                Staff.is_active.is_(True),
            )
            .group_by(StaffOrganizationAssignment.unit_id)
        ).all()
    )
    room_counts = dict(
        db.execute(
            select(Room.scope_unit_id, func.count(Room.id))
            .where(
                Room.scope_unit_id.in_(unit_ids),
                Room.is_active.is_(True),
            )
            .group_by(Room.scope_unit_id)
        ).all()
    )
    return staff_counts, room_counts


def _org_unit_responses(db: Session, units: list[OrgUnit]) -> list[OrgUnitResponse]:
    staff_counts, room_counts = _org_unit_usage(db, [unit.id for unit in units])
    unit_ids = [unit.id for unit in units]
    assignment_counts: dict[UUID, int] = {}
    child_counts: dict[UUID, int] = {}
    recipient_room_counts: dict[UUID, int] = {}
    resident_scope_room_counts: dict[UUID, int] = {}
    action_item_counts: dict[UUID, int] = {}
    if unit_ids:
        assignment_counts = dict(
            db.execute(
                select(
                    StaffOrganizationAssignment.unit_id,
                    func.count(StaffOrganizationAssignment.id),
                )
                .where(StaffOrganizationAssignment.unit_id.in_(unit_ids))
                .group_by(StaffOrganizationAssignment.unit_id)
            ).all()
        )
        child_counts = dict(
            db.execute(
                select(OrgUnit.parent_unit_id, func.count(OrgUnit.id))
                .where(OrgUnit.parent_unit_id.in_(unit_ids))
                .group_by(OrgUnit.parent_unit_id)
            ).all()
        )
        recipient_room_counts = dict(
            db.execute(
                select(RecipientRoom.floor_unit_id, func.count(RecipientRoom.id))
                .where(RecipientRoom.floor_unit_id.in_(unit_ids))
                .group_by(RecipientRoom.floor_unit_id)
            ).all()
        )
        resident_scope_room_counts = dict(
            db.execute(
                select(Room.resident_scope_unit_id, func.count(Room.id))
                .where(Room.resident_scope_unit_id.in_(unit_ids))
                .group_by(Room.resident_scope_unit_id)
            ).all()
        )
        action_item_counts = dict(
            db.execute(
                select(ActionItem.assignee_unit_id, func.count(ActionItem.id))
                .where(ActionItem.assignee_unit_id.in_(unit_ids))
                .group_by(ActionItem.assignee_unit_id)
            ).all()
        )
    return [
        OrgUnitResponse.model_validate(unit).model_copy(
            update={
                "active_staff_count": int(staff_counts.get(unit.id, 0)),
                "active_room_count": int(room_counts.get(unit.id, 0)),
                "reference_count": (
                    int(assignment_counts.get(unit.id, 0))
                    + int(child_counts.get(unit.id, 0))
                    + int(recipient_room_counts.get(unit.id, 0))
                    + int(room_counts.get(unit.id, 0))
                    + int(resident_scope_room_counts.get(unit.id, 0))
                    + int(action_item_counts.get(unit.id, 0))
                ),
                "can_delete": (
                    not unit.is_active
                    and int(assignment_counts.get(unit.id, 0)) == 0
                    and int(child_counts.get(unit.id, 0)) == 0
                    and int(recipient_room_counts.get(unit.id, 0)) == 0
                    and int(room_counts.get(unit.id, 0)) == 0
                    and int(resident_scope_room_counts.get(unit.id, 0)) == 0
                    and int(action_item_counts.get(unit.id, 0)) == 0
                ),
            }
        )
        for unit in units
    ]


def _job_code_responses(
    db: Session,
    jobs: list[StaffJobCode],
    organization_id: str,
) -> list[JobCodeResponse]:
    codes = [job.code for job in jobs]
    if not codes:
        return []
    staff_counts = dict(
        db.execute(
            select(
                StaffJobAssignment.job_code,
                func.count(func.distinct(StaffJobAssignment.staff_id)),
            )
            .join(Staff, Staff.id == StaffJobAssignment.staff_id)
            .where(
                StaffJobAssignment.job_code.in_(codes),
                StaffJobAssignment.end_date.is_(None),
                Staff.organization_id == organization_id,
                Staff.is_active.is_(True),
            )
            .group_by(StaffJobAssignment.job_code)
        ).all()
    )
    room_counts = dict(
        db.execute(
            select(Room.job_code, func.count(Room.id))
            .where(
                Room.job_code.in_(codes),
                Room.organization_id == organization_id,
                Room.is_active.is_(True),
            )
            .group_by(Room.job_code)
        ).all()
    )
    assignment_reference_counts = dict(
        db.execute(
            select(
                StaffJobAssignment.job_code,
                func.count(StaffJobAssignment.id),
            )
            .where(StaffJobAssignment.job_code.in_(codes))
            .group_by(StaffJobAssignment.job_code)
        ).all()
    )
    room_reference_counts = dict(
        db.execute(
            select(Room.job_code, func.count(Room.id))
            .where(Room.job_code.in_(codes))
            .group_by(Room.job_code)
        ).all()
    )
    return [
        JobCodeResponse.model_validate(job).model_copy(
            update={
                "active_staff_count": int(staff_counts.get(job.code, 0)),
                "active_room_count": int(room_counts.get(job.code, 0)),
                "reference_count": (
                    int(assignment_reference_counts.get(job.code, 0))
                    + int(room_reference_counts.get(job.code, 0))
                ),
                "can_delete": (
                    not job.is_active
                    and int(assignment_reference_counts.get(job.code, 0)) == 0
                    and int(room_reference_counts.get(job.code, 0)) == 0
                ),
            }
        )
        for job in jobs
    ]


def _position_title_responses(
    db: Session,
    positions: list[StaffPositionCode],
    organization_id: UUID,
) -> list[PositionTitleResponse]:
    names = [position.name for position in positions]
    if not names:
        return []
    active_staff_counts = dict(
        db.execute(
            select(Staff.position_title, func.count(Staff.id))
            .where(
                Staff.organization_id == organization_id,
                Staff.position_title.in_(names),
                Staff.deleted_at.is_(None),
                Staff.is_active.is_(True),
                Staff.employment_status == "active",
            )
            .group_by(Staff.position_title)
        ).all()
    )
    staff_reference_counts = dict(
        db.execute(
            select(Staff.position_title, func.count(Staff.id))
            .where(
                Staff.organization_id == organization_id,
                Staff.position_title.in_(names),
            )
            .group_by(Staff.position_title)
        ).all()
    )
    assignment_reference_counts = dict(
        db.execute(
            select(
                StaffJobAssignment.position_title,
                func.count(StaffJobAssignment.id),
            )
            .join(Staff, Staff.id == StaffJobAssignment.staff_id)
            .where(
                Staff.organization_id == organization_id,
                StaffJobAssignment.position_title.in_(names),
            )
            .group_by(StaffJobAssignment.position_title)
        ).all()
    )
    return [
        PositionTitleResponse.model_validate(position).model_copy(
            update={
                "active_staff_count": int(
                    active_staff_counts.get(position.name, 0)
                ),
                "reference_count": (
                    int(staff_reference_counts.get(position.name, 0))
                    + int(assignment_reference_counts.get(position.name, 0))
                ),
                "can_delete": (
                    not position.is_active
                    and int(staff_reference_counts.get(position.name, 0)) == 0
                    and int(assignment_reference_counts.get(position.name, 0)) == 0
                ),
            }
        )
        for position in positions
    ]


@app.get("/api/org-units", response_model=list[OrgUnitResponse])
def list_org_units(
    unit_type: str | None = None,
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(OrgUnit).where(
        OrgUnit.organization_id == user.organization_id,
    )
    if include_inactive:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="관리자만 중지된 조직정보를 볼 수 있습니다.")
    else:
        query = query.where(OrgUnit.is_active.is_(True))
    query = query.order_by(OrgUnit.is_active.desc(), OrgUnit.unit_type, OrgUnit.name)
    if unit_type:
        query = query.where(OrgUnit.unit_type == unit_type)
    return _org_unit_responses(db, list(db.scalars(query).all()))


@app.post("/api/org-units", response_model=OrgUnitResponse, status_code=201)
def create_org_unit(
    payload: OrgUnitCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    duplicate = db.scalar(
        select(OrgUnit).where(
            OrgUnit.organization_id == admin.organization_id,
            OrgUnit.unit_type == payload.unit_type,
            OrgUnit.name == payload.name,
            OrgUnit.is_active.is_(True),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다.")
    unit = OrgUnit(
        organization_id=admin.organization_id,
        unit_type=payload.unit_type,
        internal_code=payload.code or f"{payload.unit_type}-{uuid4().hex[:8]}",
        name=payload.name,
        is_test_data=settings.environment != "production",
    )
    db.add(unit)
    try:
        db.flush()
        record_audit(
            db,
            actor_id=admin.id,
            action="org_unit.created",
            target_type="org_unit",
            target_id=unit.id,
            details={"unit_type": unit.unit_type, "name": unit.name},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다.")
    db.refresh(unit)
    return unit


@app.patch("/api/org-units/{unit_id}", response_model=OrgUnitResponse)
async def update_org_unit(
    unit_id: UUID,
    payload: OrgUnitUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    unit = db.get(OrgUnit, unit_id)
    if unit is None or unit.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="조직정보를 찾을 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 조직정보가 없습니다.")
    if values.get("is_active") is False and unit.is_active:
        active_assignments = int(
            db.scalar(
                select(func.count(StaffOrganizationAssignment.id))
                .join(Staff, Staff.id == StaffOrganizationAssignment.staff_id)
                .where(
                    StaffOrganizationAssignment.unit_id == unit.id,
                    StaffOrganizationAssignment.end_date.is_(None),
                    Staff.is_active.is_(True),
                )
            )
            or 0
        )
        active_rooms = int(
            db.scalar(
                select(func.count(Room.id)).where(
                    Room.scope_unit_id == unit.id,
                    Room.is_active.is_(True),
                )
            )
            or 0
        )
        if active_assignments or active_rooms:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"재직 직원 {active_assignments}명과 활성 채팅방 {active_rooms}개가 "
                    "사용 중입니다. 직원 이동과 방 종료 후 사용중지하세요."
                ),
            )
    if "name" in values:
        duplicate = db.scalar(
            select(OrgUnit).where(
                OrgUnit.organization_id == admin.organization_id,
                OrgUnit.unit_type == unit.unit_type,
                OrgUnit.name == values["name"],
                OrgUnit.id != unit.id,
                OrgUnit.is_active.is_(True),
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다.")
        unit.name = values["name"]
    if "is_active" in values:
        unit.is_active = values["is_active"]
    record_audit(
        db,
        actor_id=admin.id,
        action="org_unit.updated",
        target_type="org_unit",
        target_id=unit.id,
        details={"changed_fields": sorted(values)},
    )
    db.commit()
    db.refresh(unit)
    await manager.send_to_users(
        {admin.id},
        {"event": "organization_changed", "unit_id": str(unit.id)},
    )
    return unit


@app.delete("/api/org-units/{unit_id}", response_model=OrgUnitResponse)
async def deactivate_org_unit(
    unit_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return await update_org_unit(
        unit_id,
        OrgUnitUpdate(is_active=False),
        admin,
        db,
    )


@app.delete("/api/org-units/{unit_id}/purge", status_code=204)
async def purge_org_unit(
    unit_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    unit = db.get(OrgUnit, unit_id)
    if unit is None or unit.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="조직정보를 찾을 수 없습니다.")
    response = _org_unit_responses(db, [unit])[0]
    if unit.is_active:
        raise HTTPException(
            status_code=409,
            detail="먼저 이 조직정보를 사용중지한 뒤 완전 삭제해 주세요.",
        )
    if not response.can_delete:
        raise HTTPException(
            status_code=409,
            detail=(
                f"과거 직원 배치·채팅방·업무 기록 {response.reference_count}건과 연결되어 "
                "완전 삭제할 수 없습니다. 기록 보존을 위해 중지 상태로 보관됩니다."
            ),
        )
    unit_name = unit.name
    record_audit(
        db,
        actor_id=admin.id,
        action="org_unit.purged",
        target_type="org_unit",
        target_id=unit.id,
        details={"unit_type": unit.unit_type, "name": unit_name},
    )
    db.delete(unit)
    db.commit()
    await manager.send_to_users(
        {admin.id},
        {"event": "organization_changed", "unit_id": str(unit_id)},
    )
    return Response(status_code=204)


@app.get("/api/job-codes", response_model=list[JobCodeResponse])
def list_job_codes(
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(StaffJobCode)
    if include_inactive:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="관리자만 중지된 직종정보를 볼 수 있습니다.")
    else:
        query = query.where(StaffJobCode.is_active.is_(True))
    jobs = list(
        db.scalars(
            query.order_by(
                StaffJobCode.is_active.desc(),
                StaffJobCode.sort_order,
                StaffJobCode.name,
            )
        ).all()
    )
    return _job_code_responses(db, jobs, user.organization_id)


@app.post("/api/job-codes", response_model=JobCodeResponse, status_code=201)
def create_job_code(
    payload: JobCodeCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    code = payload.code or f"custom_{uuid4().hex[:16]}"
    if db.get(StaffJobCode, code) is not None:
        raise HTTPException(status_code=409, detail="같은 직종 코드가 이미 있습니다.")
    if db.scalar(select(StaffJobCode).where(StaffJobCode.name == payload.name)):
        raise HTTPException(status_code=409, detail="같은 직종 이름이 이미 있습니다.")
    next_sort = int(
        db.scalar(select(func.max(StaffJobCode.sort_order))) or 0
    ) + 10
    job = StaffJobCode(
        code=code,
        name=payload.name,
        sort_order=next_sort,
    )
    db.add(job)
    record_audit(
        db,
        actor_id=admin.id,
        action="job_code.created",
        target_type="staff_job_code",
        target_id=None,
        details={"code": job.code, "name": job.name},
    )
    db.commit()
    db.refresh(job)
    return job


@app.patch("/api/job-codes/{job_code}", response_model=JobCodeResponse)
def update_job_code(
    job_code: str,
    payload: JobCodeUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = db.get(StaffJobCode, job_code)
    if job is None:
        raise HTTPException(status_code=404, detail="직종정보를 찾을 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 직종정보가 없습니다.")
    if values.get("is_active") is False and job.is_active:
        active_assignments = int(
            db.scalar(
                select(func.count(StaffJobAssignment.id))
                .join(Staff, Staff.id == StaffJobAssignment.staff_id)
                .where(
                    StaffJobAssignment.job_code == job.code,
                    StaffJobAssignment.end_date.is_(None),
                    Staff.is_active.is_(True),
                )
            )
            or 0
        )
        active_rooms = int(
            db.scalar(
                select(func.count(Room.id)).where(
                    Room.job_code == job.code,
                    Room.is_active.is_(True),
                )
            )
            or 0
        )
        if active_assignments or active_rooms:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"재직 직원 {active_assignments}명과 활성 채팅방 {active_rooms}개가 "
                    "사용 중입니다. 직원 직종 변경과 방 종료 후 사용중지하세요."
                ),
            )
    if "name" in values:
        duplicate = db.scalar(
            select(StaffJobCode).where(
                StaffJobCode.name == values["name"],
                StaffJobCode.code != job.code,
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="같은 직종 이름이 이미 있습니다.")
        job.name = values["name"]
    if "is_active" in values:
        job.is_active = values["is_active"]
    record_audit(
        db,
        actor_id=admin.id,
        action="job_code.updated",
        target_type="staff_job_code",
        target_id=None,
        details={"job_code": job.code, "changed_fields": sorted(values)},
    )
    db.commit()
    db.refresh(job)
    return job


@app.delete("/api/job-codes/{job_code}", response_model=JobCodeResponse)
def deactivate_job_code(
    job_code: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return update_job_code(
        job_code,
        JobCodeUpdate(is_active=False),
        admin,
        db,
    )


@app.delete("/api/job-codes/{job_code}/purge", status_code=204)
def purge_job_code(
    job_code: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = db.get(StaffJobCode, job_code)
    if job is None:
        raise HTTPException(status_code=404, detail="직종정보를 찾을 수 없습니다.")
    response = _job_code_responses(db, [job], admin.organization_id)[0]
    if job.is_active:
        raise HTTPException(
            status_code=409,
            detail="먼저 이 직종을 사용중지한 뒤 완전 삭제해 주세요.",
        )
    if not response.can_delete:
        raise HTTPException(
            status_code=409,
            detail=(
                f"과거 직원 배치·채팅방 기록 {response.reference_count}건과 연결되어 "
                "완전 삭제할 수 없습니다. 기록 보존을 위해 중지 상태로 보관됩니다."
            ),
        )
    job_name = job.name
    record_audit(
        db,
        actor_id=admin.id,
        action="job_code.purged",
        target_type="staff_job_code",
        target_id=None,
        details={"job_code": job.code, "name": job_name},
    )
    db.delete(job)
    db.commit()
    return Response(status_code=204)


@app.get("/api/position-titles", response_model=list[PositionTitleResponse])
def list_position_titles(
    include_inactive: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = select(StaffPositionCode).where(
        StaffPositionCode.organization_id == user.organization_id,
    )
    if include_inactive:
        if user.role != "admin":
            raise HTTPException(status_code=403, detail="관리자만 중지된 직위를 볼 수 있습니다.")
    else:
        query = query.where(StaffPositionCode.is_active.is_(True))
    positions = list(
        db.scalars(
            query.order_by(
                StaffPositionCode.is_active.desc(),
                StaffPositionCode.sort_order,
                StaffPositionCode.name,
            )
        ).all()
    )
    return _position_title_responses(db, positions, user.organization_id)


@app.post(
    "/api/position-titles",
    response_model=PositionTitleResponse,
    status_code=201,
)
def create_position_title(
    payload: PositionTitleCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    duplicate = db.scalar(
        select(StaffPositionCode).where(
            StaffPositionCode.organization_id == admin.organization_id,
            StaffPositionCode.name == payload.name,
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="같은 직위 이름이 이미 있습니다.")
    next_sort = int(
        db.scalar(
            select(func.max(StaffPositionCode.sort_order)).where(
                StaffPositionCode.organization_id == admin.organization_id,
            )
        )
        or 0
    ) + 10
    position = StaffPositionCode(
        organization_id=admin.organization_id,
        internal_code=f"custom_{uuid4().hex[:16]}",
        name=payload.name,
        sort_order=next_sort,
    )
    db.add(position)
    db.flush()
    record_audit(
        db,
        actor_id=admin.id,
        action="position_title.created",
        target_type="staff_position_code",
        target_id=position.id,
        details={"name": position.name},
    )
    db.commit()
    db.refresh(position)
    return _position_title_responses(
        db,
        [position],
        admin.organization_id,
    )[0]


@app.patch(
    "/api/position-titles/{position_id}",
    response_model=PositionTitleResponse,
)
def update_position_title(
    position_id: UUID,
    payload: PositionTitleUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    position = db.get(StaffPositionCode, position_id)
    if (
        position is None
        or position.organization_id != admin.organization_id
    ):
        raise HTTPException(status_code=404, detail="직위를 찾을 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 직위정보가 없습니다.")
    current_response = _position_title_responses(
        db,
        [position],
        admin.organization_id,
    )[0]
    if (
        values.get("is_active") is False
        and position.is_active
        and current_response.active_staff_count > 0
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"재직 직원 {current_response.active_staff_count}명이 사용 중입니다. "
                "직원의 직위를 먼저 변경한 뒤 사용중지하세요."
            ),
        )
    if "name" in values and values["name"] != position.name:
        duplicate = db.scalar(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == admin.organization_id,
                StaffPositionCode.name == values["name"],
                StaffPositionCode.id != position.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(status_code=409, detail="같은 직위 이름이 이미 있습니다.")
        previous_name = position.name
        staff_members = db.scalars(
            select(Staff).where(
                Staff.organization_id == admin.organization_id,
                Staff.position_title == previous_name,
            )
        ).all()
        for staff_member in staff_members:
            staff_member.position_title = values["name"]
        assignments = db.scalars(
            select(StaffJobAssignment)
            .join(Staff, Staff.id == StaffJobAssignment.staff_id)
            .where(
                Staff.organization_id == admin.organization_id,
                StaffJobAssignment.position_title == previous_name,
            )
        ).all()
        for assignment in assignments:
            assignment.position_title = values["name"]
            assignment.updated_by = admin.id
        position.name = values["name"]
    if "is_active" in values:
        position.is_active = values["is_active"]
    record_audit(
        db,
        actor_id=admin.id,
        action="position_title.updated",
        target_type="staff_position_code",
        target_id=position.id,
        details={"changed_fields": sorted(values)},
    )
    db.commit()
    db.refresh(position)
    return _position_title_responses(
        db,
        [position],
        admin.organization_id,
    )[0]


@app.delete(
    "/api/position-titles/{position_id}",
    response_model=PositionTitleResponse,
)
def deactivate_position_title(
    position_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return update_position_title(
        position_id,
        PositionTitleUpdate(is_active=False),
        admin,
        db,
    )


@app.delete("/api/position-titles/{position_id}/purge", status_code=204)
def purge_position_title(
    position_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    position = db.get(StaffPositionCode, position_id)
    if (
        position is None
        or position.organization_id != admin.organization_id
    ):
        raise HTTPException(status_code=404, detail="직위를 찾을 수 없습니다.")
    response = _position_title_responses(
        db,
        [position],
        admin.organization_id,
    )[0]
    if position.is_active:
        raise HTTPException(
            status_code=409,
            detail="먼저 이 직위를 사용중지한 뒤 완전 삭제해 주세요.",
        )
    if not response.can_delete:
        raise HTTPException(
            status_code=409,
            detail=(
                f"직원·과거 인사기록 {response.reference_count}건과 연결되어 "
                "완전 삭제할 수 없습니다. 기록 보존을 위해 중지 상태로 보관됩니다."
            ),
        )
    record_audit(
        db,
        actor_id=admin.id,
        action="position_title.purged",
        target_type="staff_position_code",
        target_id=position.id,
        details={"name": position.name},
    )
    db.delete(position)
    db.commit()
    return Response(status_code=204)


@app.get("/api/employees", response_model=list[UserResponse])
def list_employees(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return [
        user_response(user)
        for user in db.scalars(
            select(User)
            .join(Staff, Staff.id == User.staff_id)
            .where(
                User.organization_id == admin.organization_id,
                User.username != settings.dev_launcher_username,
                Staff.deleted_at.is_(None),
            )
            .order_by(User.display_name, User.id)
        ).all()
    ]


@app.post("/api/employees", response_model=UserResponse, status_code=201)
async def add_employee(
    payload: EmployeeCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = create_employee(db, payload.model_dump(), admin.id)
    await manager.send_to_users({admin.id}, {"event": "employees_changed"})
    return user_response(user)


@app.patch("/api/employees/{employee_id}", response_model=UserResponse)
async def update_employee(
    employee_id: UUID,
    payload: EmployeeUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, employee_id)
    if (
        user is None
        or user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status != "active":
        raise HTTPException(status_code=409, detail="퇴사자의 정보를 변경할 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if user.organization_id != admin.organization_id or user.staff is None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    validate_unit_assignments(db, values, user.organization_id)
    if "employee_code" in values and values["employee_code"]:
        duplicate = db.scalar(
            select(Staff).where(
                Staff.organization_id == user.organization_id,
                Staff.internal_code == values["employee_code"],
                Staff.id != user.staff_id,
            )
        )
        if duplicate:
            raise HTTPException(status_code=409, detail="이미 사용 중인 직원번호입니다.")
    if "full_name" in values:
        user.display_name = values["full_name"]
        user.staff.display_name = values["full_name"]
    if "can_process_records" in values:
        user.can_process_records = values["can_process_records"]
    if "employee_code" in values and values["employee_code"]:
        user.staff.internal_code = values["employee_code"]
    if "role" in values:
        set_user_role(db, user, values["role"])
    if "job_code" in values:
        if values["job_code"] is None:
            clear_staff_job(db, user.staff, admin.id)
        else:
            set_staff_job(db, user.staff, values["job_code"], admin.id)
    if "position_title" in values:
        values["position_title"] = validate_position_title(
            db,
            user.organization_id,
            values["position_title"],
        )
        set_staff_position_title(
            user.staff,
            values["position_title"],
            admin.id,
        )
    set_staff_unit_assignments(db, user.staff, values, admin.id)
    sync_auto_memberships(db, user)
    record_audit(
        db,
        actor_id=admin.id,
        action="employee.updated",
        target_type="user",
        target_id=user.id,
        details={"changed_fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    db.refresh(user)
    await manager.send_to_users({user.id}, {"event": "rooms_changed"})
    return user_response(user)


@app.post(
    "/api/employees/{employee_id}/reset-password",
    response_model=UserResponse,
)
async def reset_employee_password(
    employee_id: UUID,
    payload: AdminPasswordResetRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, employee_id)
    if (
        user is None
        or user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status != "active":
        raise HTTPException(status_code=409, detail="퇴사자의 비밀번호를 초기화할 수 없습니다.")
    if user.id == admin.id:
        raise HTTPException(
            status_code=409,
            detail="현재 관리자 비밀번호는 보안 설정에서 직접 변경해 주세요.",
        )
    if verify_password(payload.temporary_password, user.password_hash):
        raise HTTPException(status_code=400, detail="기존 비밀번호와 다른 임시 비밀번호를 입력하세요.")
    now = utcnow()
    user.password_hash = hash_password(payload.temporary_password)
    user.must_change_password = True
    user.password_changed_at = now
    sessions = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    for login_session in sessions:
        login_session.revoked_at = now
    record_audit(
        db,
        actor_id=admin.id,
        action="employee.password_reset",
        target_type="user",
        target_id=user.id,
        details={"revoked_sessions": len(sessions)},
    )
    db.commit()
    await manager.force_logout(
        user.id,
        "관리자가 비밀번호를 초기화하여 접속이 종료되었습니다.",
    )
    return user_response(user)


@app.post("/api/employees/{employee_id}/terminate", response_model=UserResponse)
async def terminate_employee(
    employee_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, employee_id)
    if (
        user is None
        or user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="현재 로그인한 관리자 자신은 퇴사 처리할 수 없습니다.")
    if user.employment_status == "retired":
        return user_response(user)
    now = utcnow()
    user.is_active = False
    if user.staff is not None:
        user.staff.employment_status = "retired"
        user.staff.is_active = False
        user.staff.terminated_at = now
    sessions = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    for login_session in sessions:
        login_session.revoked_at = now
    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
    ).all()
    for membership in memberships:
        membership.left_at = now
    record_audit(
        db,
        actor_id=admin.id,
        action="employee.terminated",
        target_type="user",
        target_id=user.id,
        details={"revoked_sessions": len(sessions), "closed_memberships": len(memberships)},
    )
    db.commit()
    await manager.force_logout(user.id, "퇴사 처리되어 접속이 종료되었습니다.")
    return user_response(user)


@app.post("/api/employees/{employee_id}/restore", response_model=UserResponse)
async def restore_employee(
    employee_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, employee_id)
    if (
        user is None
        or user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status == "active":
        return user_response(user)
    if user.employment_status != "retired":
        raise HTTPException(status_code=409, detail="퇴사 상태의 직원만 재직으로 복구할 수 있습니다.")

    user.is_active = True
    user.staff.employment_status = "active"
    user.staff.is_active = True
    user.staff.terminated_at = None
    sync_auto_memberships(db, user)
    record_audit(
        db,
        actor_id=admin.id,
        action="employee.restored",
        target_type="user",
        target_id=user.id,
    )
    db.commit()
    db.refresh(user)
    await manager.send_to_users(
        {admin.id, user.id},
        {"event": "employees_changed"},
    )
    await manager.send_to_users({user.id}, {"event": "rooms_changed"})
    return user_response(user)


@app.delete("/api/employees/{employee_id}", status_code=204)
async def delete_employee_from_directory(
    employee_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    user = db.get(User, employee_id)
    if (
        user is None
        or user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.id == admin.id:
        raise HTTPException(status_code=409, detail="현재 로그인한 관리자 자신은 삭제할 수 없습니다.")
    if user.employment_status != "retired":
        raise HTTPException(
            status_code=409,
            detail="접속을 안전하게 종료하기 위해 먼저 퇴사 처리해 주세요.",
        )

    now = utcnow()
    previous_username = user.username
    previous_employee_code = user.staff.internal_code
    user.username = f"deleted-{user.id.hex}"
    user.password_hash = hash_password(uuid4().hex)
    user.is_active = False
    user.staff.internal_code = f"DELETED-{user.staff.id.hex}"
    user.staff.is_active = False
    user.staff.deleted_at = now

    sessions = db.scalars(
        select(LoginSession).where(
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
    ).all()
    for login_session in sessions:
        login_session.revoked_at = now
    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
    ).all()
    for membership in memberships:
        membership.left_at = now

    record_audit(
        db,
        actor_id=admin.id,
        action="employee.deleted_from_directory",
        target_type="user",
        target_id=user.id,
        details={
            "previous_username": previous_username,
            "previous_employee_code": previous_employee_code,
            "preserved_historical_author": True,
            "revoked_sessions": len(sessions),
            "closed_memberships": len(memberships),
        },
    )
    db.commit()
    await manager.force_logout(user.id, "직원 계정이 삭제되어 접속이 종료되었습니다.")
    await manager.send_to_users({admin.id}, {"event": "employees_changed"})
    return Response(status_code=204)


@app.get("/api/push/config", response_model=PushConfigResponse)
def web_push_config(user: User = Depends(get_current_user)):
    del user
    return PushConfigResponse(
        enabled=settings.web_push_active,
        public_key=(
            settings.web_push_vapid_public_key
            if settings.web_push_active
            else None
        ),
    )


@app.post(
    "/api/push/subscriptions",
    response_model=PushSubscriptionResponse,
    status_code=201,
)
def register_web_push_subscription(
    payload: PushSubscriptionCreate,
    request: Request,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, user = auth
    _block_reviewer_account_setting(login_session)
    if not settings.web_push_active:
        raise HTTPException(
            status_code=503,
            detail="휴대전화 알림 서버가 아직 준비되지 않았습니다.",
        )

    endpoint_hash = sha256(payload.endpoint.encode("utf-8")).hexdigest()
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint_hash == endpoint_hash
        )
    )
    if subscription is None:
        subscription = PushSubscription(
            organization_id=user.organization_id,
            user_id=user.id,
            login_session_id=login_session.id,
            endpoint=payload.endpoint,
            endpoint_hash=endpoint_hash,
            p256dh=payload.keys.p256dh,
            auth=payload.keys.auth,
            expiration_time=payload.expiration_time,
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
        db.add(subscription)
    else:
        if not subscription.is_active and subscription.failure_count > 0:
            return PushSubscriptionResponse(
                enabled=True,
                active=False,
                resubscribe_required=True,
                reason_code="endpoint_expired",
                message=(
                    "이 기기의 알림 주소가 만료되었습니다. "
                    "기존 알림을 해제하고 새 알림 주소를 만들어야 합니다."
                ),
            )
        subscription.organization_id = user.organization_id
        subscription.user_id = user.id
        subscription.login_session_id = login_session.id
        subscription.endpoint = payload.endpoint
        subscription.p256dh = payload.keys.p256dh
        subscription.auth = payload.keys.auth
        subscription.expiration_time = payload.expiration_time
        subscription.user_agent = (
            (request.headers.get("user-agent") or "")[:300] or None
        )
        subscription.is_active = True
        subscription.failure_count = 0
        subscription.disabled_at = None
    db.commit()
    return PushSubscriptionResponse(
        enabled=True,
        active=True,
        message="이 휴대전화의 잠금화면 알림을 켰습니다.",
    )


@app.delete(
    "/api/push/subscriptions",
    response_model=PushSubscriptionResponse,
)
def delete_web_push_subscription(
    payload: PushSubscriptionDelete,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, user = auth
    _block_reviewer_account_setting(login_session)
    endpoint_hash = sha256(payload.endpoint.strip().encode("utf-8")).hexdigest()
    subscription = db.scalar(
        select(PushSubscription).where(
            PushSubscription.endpoint_hash == endpoint_hash,
            PushSubscription.user_id == user.id,
        )
    )
    if subscription is not None:
        subscription.is_active = False
        subscription.disabled_at = utcnow()
        db.commit()
    return PushSubscriptionResponse(
        enabled=settings.web_push_active,
        active=False,
        message="이 휴대전화의 잠금화면 알림을 껐습니다.",
    )


@app.post(
    "/api/push/test",
    response_model=PushSubscriptionResponse,
    status_code=202,
)
def test_web_push_notification(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
):
    login_session, user = auth
    _block_reviewer_account_setting(login_session)
    if not settings.web_push_active:
        raise HTTPException(
            status_code=503,
            detail="휴대전화 알림 서버가 아직 준비되지 않았습니다.",
        )
    sent_count = send_web_push_to_users({user.id}, is_test=True)
    if sent_count == 0:
        raise HTTPException(
            status_code=502,
            detail=(
                "휴대전화 알림 전송에 실패했습니다. "
                "알림을 껐다가 다시 켠 뒤 다시 시험해 주세요."
            ),
        )
    return PushSubscriptionResponse(
        enabled=True,
        active=True,
        message="시험 알림을 보냈습니다. 잠금화면을 확인해 주세요.",
    )


@app.get("/api/rooms", response_model=list[RoomResponse])
def rooms(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return list_user_rooms(db, user.id)


def _managed_room_response(db: Session, room: Room) -> ManagedRoomResponse:
    member_query = (
        select(User.id)
        .join(Staff, Staff.id == User.staff_id)
        .join(RoomMembership, RoomMembership.staff_id == Staff.id)
        .where(
            RoomMembership.room_id == room.id,
            User.organization_id == room.organization_id,
            User.is_active.is_(True),
        )
        .distinct()
        .order_by(User.id)
    )
    if room.is_active:
        member_query = member_query.where(RoomMembership.left_at.is_(None))
    member_ids = list(db.scalars(member_query).all())
    return ManagedRoomResponse(
        id=room.id,
        name=room.name,
        kind=room.kind,
        is_active=room.is_active,
        scope_unit_id=room.scope_unit_id,
        scope_name=room.scope_unit.name if room.scope_unit else None,
        job_code=room.job_code,
        job_name=room.job.name if room.job else None,
        member_ids=member_ids,
        member_count=len(member_ids),
        resident_scope=room.resident_scope,
        resident_scope_unit_id=room.resident_scope_unit_id,
        resident_scope_name=(
            room.resident_scope_unit.name if room.resident_scope_unit else None
        ),
        message_count=int(
            db.scalar(
                select(func.count(Message.id)).where(Message.room_id == room.id)
            )
            or 0
        ),
        created_at=_as_utc(room.created_at),
    )


def _active_room_users(
    db: Session,
    organization_id: UUID,
    member_ids: set[UUID],
) -> list[User]:
    if not member_ids:
        return []
    users = db.scalars(
        select(User).where(
            User.id.in_(member_ids),
            User.organization_id == organization_id,
            User.is_active.is_(True),
        )
    ).all()
    if (
        {user.id for user in users} != member_ids
        or any(user.staff_id is None or user.employment_status != "active" for user in users)
    ):
        raise HTTPException(status_code=422, detail="참여자 중 존재하지 않거나 퇴사한 직원이 있습니다.")
    return list(users)


def _sync_all_rule_memberships(db: Session, organization_id: UUID) -> set[UUID]:
    users = db.scalars(
        select(User).where(
            User.organization_id == organization_id,
            User.is_active.is_(True),
        )
    ).all()
    changed_user_ids: set[UUID] = set()
    for user in users:
        if user.staff_id is None or user.employment_status != "active":
            continue
        sync_auto_memberships(db, user)
        changed_user_ids.add(user.id)
    db.flush()
    return changed_user_ids


def _set_room_member_selection(
    db: Session,
    room: Room,
    users: list[User],
    admin: User,
) -> set[UUID]:
    active_users = list(
        db.scalars(
            select(User).where(
                User.organization_id == room.organization_id,
                User.is_active.is_(True),
            )
        ).all()
    )
    active_users = [
        user
        for user in active_users
        if user.staff is not None and user.employment_status == "active"
    ]
    desired_staff_ids = {user.staff_id for user in users}
    overrides = {
        override.staff_id: override
        for override in db.scalars(
            select(RoomMembershipOverride).where(
                RoomMembershipOverride.room_id == room.id
            )
        ).all()
    }
    memberships = list(
        db.scalars(
            select(RoomMembership).where(RoomMembership.room_id == room.id)
        ).all()
    )
    memberships_by_staff: dict[UUID, list[RoomMembership]] = {}
    for membership in memberships:
        memberships_by_staff.setdefault(membership.staff_id, []).append(membership)

    now = utcnow()
    decisions: list[tuple[User, bool, bool, str | None]] = []
    for user in active_users:
        staff = user.staff
        baseline = room.kind != "custom" and staff_matches_room_rule(staff, room)
        selected = staff.id in desired_staff_ids
        action = (
            "include"
            if selected and not baseline
            else "exclude"
            if not selected and baseline
            else None
        )
        decisions.append((user, baseline, selected, action))

        for membership in memberships_by_staff.get(staff.id, []):
            if membership.left_at is not None:
                continue
            should_close = (
                not selected
                or (selected and baseline and membership.source == "manual")
                or (selected and not baseline and membership.source == "auto")
            )
            if should_close:
                membership.left_at = now
    db.flush()

    for user, _baseline, selected, action in decisions:
        staff = user.staff
        override = overrides.get(staff.id)
        if action is None:
            if override is not None:
                db.delete(override)
        elif override is None:
            override = RoomMembershipOverride(
                organization_id=room.organization_id,
                room_id=room.id,
                staff_id=staff.id,
                action=action,
                created_by=admin.id,
            )
            db.add(override)
        else:
            override.action = action
            override.created_by = admin.id

        if selected and action == "include":
            previous_manual = next(
                (
                    membership
                    for membership in sorted(
                        memberships_by_staff.get(staff.id, []),
                        key=lambda item: item.joined_at,
                        reverse=True,
                    )
                    if membership.source == "manual"
                ),
                None,
            )
            if previous_manual is None:
                db.add(
                    RoomMembership(
                        organization_id=room.organization_id,
                        room_id=room.id,
                        staff_id=staff.id,
                        source="manual",
                        joined_at=now,
                        created_by=admin.id,
                    )
                )
            else:
                previous_manual.left_at = None
                previous_manual.joined_at = now
                previous_manual.created_by = admin.id

    db.flush()
    for user in active_users:
        sync_auto_memberships(db, user)
    db.flush()
    return {user.id for user in active_users}


def _validate_managed_room(
    db: Session,
    organization_id: UUID,
    payload: ManagedRoomCreate,
) -> tuple[OrgUnit | None, StaffJobCode | None, list[User]]:
    scope_unit = None
    job = None
    users: list[User] = []
    if payload.kind in {"business", "department", "floor", "team"}:
        if payload.scope_unit_id is None:
            raise HTTPException(status_code=422, detail="조직 자동배정 방은 연결할 조직정보가 필요합니다.")
        scope_unit = db.get(OrgUnit, payload.scope_unit_id)
        if (
            scope_unit is None
            or scope_unit.organization_id != organization_id
            or scope_unit.unit_type != payload.kind
            or not scope_unit.is_active
        ):
            raise HTTPException(status_code=422, detail="채팅방의 조직 배정규칙이 올바르지 않습니다.")
    elif payload.kind == "job":
        if not payload.job_code:
            raise HTTPException(status_code=422, detail="직종 자동배정 방은 직종을 선택해야 합니다.")
        job = db.get(StaffJobCode, payload.job_code)
        if job is None or not job.is_active:
            raise HTTPException(status_code=422, detail="채팅방의 직종 배정규칙이 올바르지 않습니다.")
    elif payload.kind == "custom":
        if not payload.member_ids:
            raise HTTPException(status_code=422, detail="직접 선택 방은 참여 직원을 한 명 이상 선택해야 합니다.")
        users = _active_room_users(db, organization_id, set(payload.member_ids))
    elif payload.kind != "all":
        raise HTTPException(status_code=422, detail="지원하지 않는 채팅방 배정방식입니다.")
    return scope_unit, job, users


def _resident_scope_unit_id(
    db: Session,
    organization_id: UUID,
    *,
    room_kind: str,
    resident_scope: str,
    resident_scope_unit_id: UUID | None,
    scope_unit_id: UUID | None,
) -> UUID | None:
    if resident_scope != "floor":
        return None
    if room_kind == "floor":
        return scope_unit_id
    if resident_scope_unit_id is None:
        raise HTTPException(
            status_code=422,
            detail="층 어르신 우선표시를 사용하려면 기준 층을 선택해야 합니다.",
        )
    floor = db.get(OrgUnit, resident_scope_unit_id)
    if (
        floor is None
        or floor.organization_id != organization_id
        or floor.unit_type != "floor"
        or not floor.is_active
    ):
        raise HTTPException(status_code=422, detail="어르신 우선표시 기준 층이 올바르지 않습니다.")
    return floor.id


@app.get("/api/admin/rooms", response_model=list[ManagedRoomResponse])
def list_managed_rooms(
    include_inactive: bool = False,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = select(Room).where(
        Room.organization_id == admin.organization_id,
        Room.kind != "self",
    )
    if not include_inactive:
        query = query.where(Room.is_active.is_(True))
    rooms = db.scalars(
        query.order_by(
            Room.is_active.desc(),
            Room.sort_order,
            Room.name,
            Room.created_at,
        )
    ).all()
    return [_managed_room_response(db, room) for room in rooms]


@app.post("/api/admin/rooms", response_model=ManagedRoomResponse, status_code=201)
async def create_managed_room(
    payload: ManagedRoomCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    _scope_unit, _job, users = _validate_managed_room(
        db,
        admin.organization_id,
        payload,
    )
    resident_scope = "floor" if payload.kind == "floor" else payload.resident_scope
    resident_scope_unit_id = _resident_scope_unit_id(
        db,
        admin.organization_id,
        room_kind=payload.kind,
        resident_scope=resident_scope,
        resident_scope_unit_id=payload.resident_scope_unit_id,
        scope_unit_id=payload.scope_unit_id,
    )
    room = Room(
        organization_id=admin.organization_id,
        name=payload.name,
        kind=payload.kind,
        scope_unit_id=payload.scope_unit_id,
        job_code=payload.job_code,
        resident_scope=resident_scope,
        resident_scope_unit_id=resident_scope_unit_id,
        created_by_id=admin.id,
        is_test_data=settings.environment != "production",
    )
    db.add(room)
    try:
        db.flush()
        changed_user_ids: set[UUID]
        if room.kind == "custom":
            changed_user_ids = {user.id for user in users}
            for user in users:
                db.add(
                    RoomMembership(
                        organization_id=admin.organization_id,
                        room_id=room.id,
                        staff_id=user.staff_id,
                        source="manual",
                        created_by=admin.id,
                    )
                )
            db.flush()
        else:
            changed_user_ids = _sync_all_rule_memberships(db, admin.organization_id)
        record_audit(
            db,
            actor_id=admin.id,
            action="room.created",
            target_type="room",
            target_id=room.id,
            details={
                "kind": room.kind,
                "scope_unit_id": str(room.scope_unit_id) if room.scope_unit_id else None,
                "resident_scope": room.resident_scope,
                "resident_scope_unit_id": (
                    str(room.resident_scope_unit_id)
                    if room.resident_scope_unit_id
                    else None
                ),
                "job_code": room.job_code,
                "member_ids": sorted(str(user.id) for user in users),
            },
        )
        response = _managed_room_response(db, room)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="같은 자동배정 규칙의 채팅방이 이미 있습니다.")
    await manager.send_to_users(
        changed_user_ids | {admin.id},
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.patch("/api/admin/rooms/{room_id}", response_model=ManagedRoomResponse)
async def update_managed_room(
    room_id: UUID,
    payload: ManagedRoomUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    room = db.get(Room, room_id)
    if room is None or room.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    if not room.is_active:
        raise HTTPException(status_code=409, detail="종료된 채팅방은 먼저 복구해야 합니다.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 채팅방 정보가 없습니다.")
    before_user_ids = room_member_user_ids(db, room.id)
    if "name" in values:
        room.name = values["name"]
    if "resident_scope" in values or "resident_scope_unit_id" in values:
        resident_scope = (
            "floor"
            if room.kind == "floor"
            else values.get("resident_scope", room.resident_scope)
        )
        room.resident_scope_unit_id = _resident_scope_unit_id(
            db,
            admin.organization_id,
            room_kind=room.kind,
            resident_scope=resident_scope,
            resident_scope_unit_id=values.get(
                "resident_scope_unit_id",
                room.resident_scope_unit_id,
            ),
            scope_unit_id=room.scope_unit_id,
        )
        room.resident_scope = resident_scope
    if "member_ids" in values:
        users = _active_room_users(db, admin.organization_id, set(values["member_ids"]))
        _set_room_member_selection(db, room, users, admin)
    db.flush()
    after_user_ids = room_member_user_ids(db, room.id)
    record_audit(
        db,
        actor_id=admin.id,
        action="room.updated",
        target_type="room",
        target_id=room.id,
        details={"changed_fields": sorted(values)},
    )
    response = _managed_room_response(db, room)
    db.commit()
    await manager.send_to_users(
        before_user_ids | after_user_ids | {admin.id},
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.delete("/api/admin/rooms/{room_id}", status_code=204)
async def close_managed_room(
    room_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    room = db.get(Room, room_id)
    if room is None or room.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    if not room.is_active:
        raise HTTPException(status_code=409, detail="이미 종료된 채팅방입니다.")
    member_ids = room_member_user_ids(db, room.id)
    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.room_id == room.id,
            RoomMembership.left_at.is_(None),
        )
    ).all()
    now = utcnow()
    for membership in memberships:
        membership.left_at = now
    room.is_active = False
    record_audit(
        db,
        actor_id=admin.id,
        action="room.closed",
        target_type="room",
        target_id=room.id,
        details={"closed_memberships": len(memberships), "data_retained": True},
    )
    db.commit()
    await manager.send_to_users(
        member_ids | {admin.id},
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return Response(status_code=204)


@app.post("/api/admin/rooms/{room_id}/restore", response_model=ManagedRoomResponse)
async def restore_managed_room(
    room_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    room = db.get(Room, room_id)
    if room is None or room.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    if room.is_active:
        return _managed_room_response(db, room)
    room.is_active = True
    db.flush()
    changed_user_ids: set[UUID] = set()
    if room.kind == "custom":
        memberships = db.scalars(
            select(RoomMembership)
            .join(Staff, Staff.id == RoomMembership.staff_id)
            .join(User, User.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room.id,
                RoomMembership.source == "manual",
                User.is_active.is_(True),
                Staff.is_active.is_(True),
            )
        ).all()
        now = utcnow()
        for membership in memberships:
            membership.left_at = None
            membership.joined_at = now
            changed_user_ids.update(
                db.scalars(
                    select(User.id).where(User.staff_id == membership.staff_id)
                ).all()
            )
    else:
        changed_user_ids = _sync_all_rule_memberships(db, admin.organization_id)
    record_audit(
        db,
        actor_id=admin.id,
        action="room.restored",
        target_type="room",
        target_id=room.id,
    )
    db.flush()
    response = _managed_room_response(db, room)
    db.commit()
    await manager.send_to_users(
        changed_user_ids | {admin.id},
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.get("/api/rooms/{room_id}/members", response_model=list[RoomMemberResponse])
def room_members(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방의 참여자를 볼 수 없습니다.")
    member_query = (
        select(User)
        .join(Staff, Staff.id == User.staff_id)
        .join(RoomMembership, RoomMembership.staff_id == Staff.id)
        .where(
            RoomMembership.room_id == room_id,
            RoomMembership.left_at.is_(None),
            User.is_active.is_(True),
        )
        .order_by(Staff.display_name)
    )
    if getattr(user, "_reviewer_experience", None) is not None:
        member_query = member_query.where(Staff.is_test_data.is_(True))
    members = db.scalars(member_query).all()
    return [
        RoomMemberResponse(
            id=member.id,
            full_name=member.full_name,
            job_name=member.job_name,
            floor=unit_response(member.floor),
            team=unit_response(member.team),
        )
        for member in members
    ]


@app.get(
    "/api/rooms/{room_id}/action-assignees",
    response_model=list[ActionAssigneeResponse],
)
def room_action_assignees(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에서 담당자를 지정할 수 없습니다.")
    candidates = db.scalars(
        select(User)
        .join(Staff, Staff.id == User.staff_id)
        .join(RoomMembership, RoomMembership.staff_id == Staff.id)
        .where(
            RoomMembership.room_id == room_id,
            RoomMembership.left_at.is_(None),
            User.is_active.is_(True),
            Staff.is_active.is_(True),
            Staff.employment_status == "active",
        )
        .order_by(Staff.display_name)
    ).all()
    priority_job_codes = {
        "representative",
        "facility_director",
        "office_director",
        "social_worker",
        "registered_nurse",
        "nursing_assistant",
        "physical_therapist",
        "occupational_therapist",
    }
    candidates.sort(
        key=lambda candidate: (
            0
            if candidate.can_process_records
            or candidate.job_code in priority_job_codes
            else 1,
            candidate.job_name or "",
            candidate.full_name,
        )
    )
    return [
        ActionAssigneeResponse(
            id=candidate.id,
            full_name=candidate.full_name,
            job_code=candidate.job_code,
            job_name=candidate.job_name,
            business=unit_response(candidate.business),
            department=unit_response(candidate.department),
            floor=unit_response(candidate.floor),
            team=unit_response(candidate.team),
            can_process_records=candidate.can_process_records,
            is_room_member=True,
        )
        for candidate in candidates
    ]


@app.get("/api/rooms/{room_id}/residents", response_model=list[ResidentResponse])
def room_residents(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 접근할 수 없습니다.")
    room = db.get(Room, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    resident_query = select(Resident).where(
            Resident.organization_id == user.organization_id,
            Resident.is_active.is_(True),
        )
    if getattr(user, "_reviewer_experience", None) is not None:
        resident_query = resident_query.where(Resident.is_test_data.is_(True))
    residents = db.scalars(resident_query).all()
    if settings.environment != "test":
        carefor_services = {
            resident.service_type
            for resident in residents
            if resident.internal_code.startswith("SMCODI:carefor:")
        }
        residents = [
            resident
            for resident in residents
            if resident.service_type not in carefor_services
            or resident.internal_code.startswith("SMCODI:carefor:")
        ]

    def is_priority(resident: Resident) -> bool:
        if room.resident_scope == "floor":
            return resident.floor_id == (
                room.resident_scope_unit_id or room.scope_unit_id
            )
        if room.resident_scope in {"facility", "daycare", "homecare"}:
            return resident.service_type == room.resident_scope
        return False

    residents.sort(
        key=lambda resident: (
            0 if is_priority(resident) else 1,
            resident.sort_order,
            resident.display_name,
        )
    )
    return [
        resident_response(resident, is_priority=is_priority(resident))
        for resident in residents
    ]


@app.get("/api/admin/residents", response_model=list[ResidentResponse])
def list_residents_for_admin(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    residents = db.scalars(
        select(Resident)
        .where(
            Resident.organization_id == admin.organization_id,
            Resident.is_active.is_(True),
        )
        .order_by(
            Resident.service_type,
            Resident.sort_order,
            Resident.display_name,
        )
    ).all()
    return [resident_response(resident) for resident in residents]


@app.get("/api/workdesk/residents", response_model=list[ResidentResponse])
def list_residents_for_workdesk(
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident_query = (
        select(Resident)
        .where(
            Resident.organization_id == processor.organization_id,
            Resident.is_active.is_(True),
        )
        .order_by(
            Resident.service_type,
            Resident.sort_order,
            Resident.display_name,
        )
    )
    if getattr(processor, "_reviewer_experience", None) is not None:
        resident_query = resident_query.where(Resident.is_test_data.is_(True))
    residents = db.scalars(resident_query).all()
    if settings.environment != "test":
        carefor_services = {
            resident.service_type
            for resident in residents
            if resident.internal_code.startswith("SMCODI:carefor:")
        }
        residents = [
            resident
            for resident in residents
            if resident.service_type not in carefor_services
            or resident.internal_code.startswith("SMCODI:carefor:")
        ]
    return [resident_response(resident) for resident in residents]


def _room_for_manual_resident(
    db: Session,
    *,
    organization_id: UUID,
    service_type: str,
    floor_id: UUID | None,
) -> RecipientRoom:
    floor_unit = None
    if floor_id is not None:
        floor_unit = db.scalar(
            select(OrgUnit).where(
                OrgUnit.id == floor_id,
                OrgUnit.organization_id == organization_id,
                OrgUnit.unit_type == "floor",
                OrgUnit.is_active.is_(True),
            )
        )
        if floor_unit is None:
            raise HTTPException(status_code=422, detail="선택한 층을 찾을 수 없습니다.")
    if service_type in {"facility", "daycare"} and floor_unit is None:
        raise HTTPException(
            status_code=422,
            detail="시설·주간보호 어르신은 층을 선택해 주세요.",
        )
    floor_name = floor_unit.name if floor_unit else None
    room_name = (
        f"{floor_name} 생활구역"
        if floor_name
        else "방문요양"
    )
    room_key = f"{service_type}|{floor_name or ''}|{room_name}"
    internal_code = f"SMCODI-ROOM-{sha256(room_key.encode('utf-8')).hexdigest()[:24]}"
    room = db.scalar(
        select(RecipientRoom).where(
            RecipientRoom.organization_id == organization_id,
            RecipientRoom.internal_code == internal_code,
        )
    )
    if room is None:
        room = RecipientRoom(
            organization_id=organization_id,
            internal_code=internal_code,
            name=room_name,
            floor=floor_name,
            floor_unit_id=floor_unit.id if floor_unit else None,
            is_active=True,
        )
        db.add(room)
        db.flush()
    elif not room.is_active:
        room.is_active = True
    return room


@app.post(
    "/api/admin/residents",
    response_model=ResidentResponse,
    status_code=201,
)
def create_resident_for_admin(
    payload: ResidentAdminCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    duplicate = db.scalar(
        select(Resident).where(
            Resident.organization_id == admin.organization_id,
            Resident.service_type == payload.service_type,
            Resident.display_name == payload.display_name,
            Resident.is_active.is_(True),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail="같은 서비스의 이용 중 명단에 같은 표시 이름이 있습니다.",
        )
    room = _room_for_manual_resident(
        db,
        organization_id=admin.organization_id,
        service_type=payload.service_type,
        floor_id=payload.floor_id,
    )
    current_max = db.scalar(
        select(func.max(Resident.sort_order)).where(
            Resident.organization_id == admin.organization_id,
            Resident.service_type == payload.service_type,
        )
    )
    resident = Resident(
        organization_id=admin.organization_id,
        internal_code=f"MANUAL:{uuid4().hex}",
        display_name=payload.display_name,
        status="active",
        room_id=room.id,
        service_type=payload.service_type,
        sort_order=int(current_max or 0) + 10,
        is_test_data=settings.environment != "production",
        is_active=True,
    )
    db.add(resident)
    db.flush()
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.manual_created",
        target_type="recipient",
        target_id=resident.id,
        details={
            "display_name": resident.display_name,
            "service_type": resident.service_type,
            "floor_id": str(payload.floor_id) if payload.floor_id else None,
        },
    )
    db.commit()
    db.refresh(resident)
    return resident_response(resident)


@app.delete("/api/admin/residents/{resident_id}", status_code=204)
def deactivate_resident_for_admin(
    resident_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    resident = db.scalar(
        select(Resident).where(
            Resident.id == resident_id,
            Resident.organization_id == admin.organization_id,
            Resident.is_active.is_(True),
        )
    )
    if resident is None:
        raise HTTPException(status_code=404, detail="이용 중인 어르신을 찾을 수 없습니다.")
    resident.is_active = False
    resident.status = "inactive"
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.deactivated",
        target_type="recipient",
        target_id=resident.id,
        details={"reason": "admin_manual"},
    )
    db.commit()
    return Response(status_code=204)


@app.patch("/api/admin/residents/order", response_model=list[ResidentResponse])
def update_resident_order(
    payload: ResidentOrderUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    resident_ids = payload.resident_ids
    if len(set(resident_ids)) != len(resident_ids):
        raise HTTPException(status_code=422, detail="어르신 순서에 중복된 항목이 있습니다.")
    residents = db.scalars(
        select(Resident).where(
            Resident.id.in_(set(resident_ids)),
            Resident.organization_id == admin.organization_id,
            Resident.is_active.is_(True),
        )
    ).all()
    if {resident.id for resident in residents} != set(resident_ids):
        raise HTTPException(status_code=422, detail="순서를 변경할 수 없는 어르신이 포함되어 있습니다.")
    by_id = {resident.id: resident for resident in residents}
    for index, resident_id in enumerate(resident_ids, start=1):
        by_id[resident_id].sort_order = index * 10
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.reordered",
        target_type="recipient",
        target_id=None,
        details={"resident_ids": [str(resident_id) for resident_id in resident_ids]},
    )
    db.commit()
    return [
        resident_response(by_id[resident_id])
        for resident_id in resident_ids
    ]


def _parse_local_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


@app.get(
    "/api/admin/carefor-roster/status",
    response_model=CareforRosterStatusResponse,
)
def carefor_roster_status(
    admin: User = Depends(require_admin),
):
    del admin
    path = Path(settings.carefor_identity_map_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        payload = {}
    staff_path = Path(settings.carefor_staff_roster_path)
    try:
        staff_payload = json.loads(staff_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        staff_payload = {}
    raw_staff = (
        staff_payload.get("staff", [])
        if isinstance(staff_payload, dict)
        else []
    )
    staff_by_service: dict[str, list[CareforStaffAliasResponse]] = {
        "facility": [],
        "daycare": [],
        "homecare": [],
    }
    if isinstance(raw_staff, list):
        for row in raw_staff:
            if not isinstance(row, dict):
                continue
            service_type = str(row.get("service_type", "")).strip()
            display_name = str(row.get("display_name", "")).strip()
            if (
                service_type not in staff_by_service
                or "(가명)" not in display_name
            ):
                continue
            staff_by_service[service_type].append(
                CareforStaffAliasResponse(
                    display_name=display_name,
                    service_type=service_type,
                    status=str(row.get("status", "")).strip() or "상태 미확인",
                    job_name=str(row.get("job_name", "")).strip() or "직종 미확인",
                    is_active=bool(row.get("is_active", True)),
                )
            )
    for aliases in staff_by_service.values():
        aliases.sort(key=lambda item: item.display_name)
    raw_sources = payload.get("sources", {}) if isinstance(payload, dict) else {}
    sources: dict[str, CareforRosterSourceStatus] = {}
    for service_type in ("facility", "daycare", "homecare"):
        raw = raw_sources.get(service_type, {}) if isinstance(raw_sources, dict) else {}
        raw_status = str(raw.get("status", "missing"))
        source_status = (
            raw_status
            if raw_status in {"captured", "login_required", "missing"}
            else "missing"
        )
        sources[service_type] = CareforRosterSourceStatus(
            status=source_status,
            captured_at=_parse_local_timestamp(raw.get("captured_at")),
            resident_count=max(0, int(raw.get("resident_count", 0) or 0)),
            staff_count=max(0, int(raw.get("staff_count", 0) or 0)),
            staff_aliases=staff_by_service[service_type],
        )
    return CareforRosterStatusResponse(
        generated_at=_parse_local_timestamp(
            payload.get("generated_at") if isinstance(payload, dict) else None
        ),
        sources=sources,
    )


@app.post(
    "/api/admin/carefor-roster/preview",
    response_model=ResidentSyncBatchResponse,
    status_code=201,
)
def preview_carefor_resident_roster(
    service_type: Annotated[
        str,
        Form(pattern="^(facility|daycare|homecare)$"),
    ],
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    identity_path = Path(settings.carefor_identity_map_path)
    try:
        identity_payload = json.loads(identity_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        identity_payload = {}
    source_info = (
        identity_payload.get("sources", {}).get(service_type, {})
        if isinstance(identity_payload, dict)
        else {}
    )
    if source_info.get("status") != "captured":
        raise HTTPException(
            status_code=409,
            detail="이 서비스의 케어포 명단이 아직 준비되지 않았습니다.",
        )

    roster_path = Path(settings.carefor_resident_roster_path)
    try:
        content = roster_path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail="로컬 케어포 가명 명단 파일을 찾을 수 없습니다.",
        ) from exc
    try:
        source_generated_at, all_rows = parse_roster_file(
            content,
            roster_path.name,
        )
        rows = [
            row
            for row in all_rows
            if str(row.get("service_type", "")).strip() == service_type
        ]
        if not rows:
            raise ResidentSyncError("선택한 서비스의 어르신 명단이 비어 있습니다.")
        entries, summary = build_preview_entries(
            db,
            admin.organization_id,
            rows,
            include_missing_as_deactivate=True,
            managed_external_id_prefixes=(
                f"carefor:{service_type}:resident:",
            ),
        )
    except ResidentSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    original_name = f"carefor_{service_type}_residents.local.json"
    batch = ResidentSyncBatch(
        organization_id=admin.organization_id,
        source="carefor_read_only_capture",
        original_name=original_name,
        file_sha256=sha256(content).hexdigest(),
        source_generated_at=source_generated_at,
        status="preview",
        summary=summary,
        created_by_id=admin.id,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            ResidentSyncItem(
                batch_id=batch.id,
                organization_id=admin.organization_id,
                **entry,
            )
            for entry in entries
        ]
    )
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.carefor_preview_created",
        target_type="recipient_sync_batch",
        target_id=batch.id,
        details={
            "service_type": service_type,
            "source": batch.source,
            "original_name": original_name,
            "file_sha256": batch.file_sha256,
            "summary": summary,
        },
    )
    db.commit()
    db.refresh(batch)
    return _resident_sync_batch_response(batch)


def _resident_sync_batch_response(
    batch: ResidentSyncBatch,
    *,
    include_items: bool = True,
) -> ResidentSyncBatchResponse:
    items = batch.items if include_items else []
    return ResidentSyncBatchResponse(
        id=batch.id,
        source=batch.source,
        original_name=batch.original_name,
        file_sha256=batch.file_sha256,
        source_generated_at=_as_utc(batch.source_generated_at)
        if batch.source_generated_at
        else None,
        status=batch.status,
        summary=batch.summary,
        created_by_name=batch.created_by.full_name,
        applied_by_name=batch.applied_by.full_name if batch.applied_by else None,
        applied_at=_as_utc(batch.applied_at) if batch.applied_at else None,
        created_at=_as_utc(batch.created_at),
        updated_at=_as_utc(batch.updated_at),
        items=[
            ResidentSyncItemResponse(
                id=item.id,
                external_id=item.external_id,
                change_type=item.change_type,
                status=item.status,
                current_resident_id=item.current_resident_id,
                incoming_payload=item.incoming_payload,
                current_snapshot=item.current_snapshot,
                conflict_reason=item.conflict_reason,
                applied_at=_as_utc(item.applied_at) if item.applied_at else None,
            )
            for item in items
        ],
    )


@app.post(
    "/api/admin/resident-sync/preview",
    response_model=ResidentSyncBatchResponse,
    status_code=201,
)
async def preview_resident_sync(
    file: UploadFile = File(...),
    practice_mode: bool = Form(False),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    original_name = Path(file.filename or "resident_roster.json").name[:180]
    content = await file.read(MAX_RESIDENT_SYNC_FILE_BYTES + 1)
    await file.close()
    if not content:
        raise HTTPException(status_code=422, detail="빈 명단 파일은 올릴 수 없습니다.")
    if len(content) > MAX_RESIDENT_SYNC_FILE_BYTES:
        raise HTTPException(
            status_code=413,
            detail="명단 파일은 2MB 이하여야 합니다.",
        )
    try:
        source_generated_at, rows = parse_roster_file(content, original_name)
        entries, summary = build_preview_entries(
            db,
            admin.organization_id,
            rows,
            include_missing_as_deactivate=not practice_mode,
        )
    except ResidentSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    batch = ResidentSyncBatch(
        organization_id=admin.organization_id,
        source="practice_example" if practice_mode else "smcodi_read_only_export",
        original_name=original_name,
        file_sha256=sha256(content).hexdigest(),
        source_generated_at=source_generated_at,
        status="preview",
        summary=summary,
        created_by_id=admin.id,
    )
    db.add(batch)
    db.flush()
    db.add_all(
        [
            ResidentSyncItem(
                batch_id=batch.id,
                organization_id=admin.organization_id,
                **entry,
            )
            for entry in entries
        ]
    )
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.sync_preview_created",
        target_type="recipient_sync_batch",
        target_id=batch.id,
        details={
            "source": batch.source,
            "original_name": original_name,
            "file_sha256": batch.file_sha256,
            "summary": summary,
        },
    )
    db.commit()
    db.refresh(batch)
    return _resident_sync_batch_response(batch)


@app.get(
    "/api/admin/resident-sync/batches",
    response_model=list[ResidentSyncBatchResponse],
)
def list_resident_sync_batches(
    limit: int = 10,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    safe_limit = max(1, min(limit, 30))
    batches = db.scalars(
        select(ResidentSyncBatch)
        .where(ResidentSyncBatch.organization_id == admin.organization_id)
        .order_by(ResidentSyncBatch.created_at.desc())
        .limit(safe_limit)
    ).all()
    return [
        _resident_sync_batch_response(batch, include_items=False)
        for batch in batches
    ]


@app.get(
    "/api/admin/resident-sync/batches/{batch_id}",
    response_model=ResidentSyncBatchResponse,
)
def get_resident_sync_batch(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(ResidentSyncBatch).where(
            ResidentSyncBatch.id == batch_id,
            ResidentSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="동기화 미리보기를 찾을 수 없습니다.")
    return _resident_sync_batch_response(batch)


@app.post(
    "/api/admin/resident-sync/batches/{batch_id}/apply",
    response_model=ResidentSyncBatchResponse,
)
def apply_resident_sync(
    batch_id: UUID,
    payload: ResidentSyncApplyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(ResidentSyncBatch).where(
            ResidentSyncBatch.id == batch_id,
            ResidentSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(status_code=404, detail="동기화 미리보기를 찾을 수 없습니다.")
    requested_ids = set(payload.item_ids)
    items = db.scalars(
        select(ResidentSyncItem).where(
            ResidentSyncItem.batch_id == batch.id,
            ResidentSyncItem.organization_id == admin.organization_id,
            ResidentSyncItem.id.in_(requested_ids),
        )
    ).all()
    if {item.id for item in items} != requested_ids:
        raise HTTPException(
            status_code=422,
            detail="이 미리보기에 속하지 않는 승인 항목이 포함되어 있습니다.",
        )
    invalid_items = [
        item
        for item in items
        if item.status != "pending"
        or item.change_type not in {"new", "update", "deactivate"}
    ]
    if invalid_items:
        raise HTTPException(
            status_code=422,
            detail="이미 처리되었거나 승인할 수 없는 항목이 포함되어 있습니다.",
        )

    applied_at = utcnow()
    change_counts: dict[str, int] = {}
    try:
        for item in items:
            apply_sync_item(
                db,
                item,
                is_test_data=settings.environment != "production",
            )
            item.status = "applied"
            item.applied_at = applied_at
            change_counts[item.change_type] = change_counts.get(item.change_type, 0) + 1
        db.flush()
        remaining_count = db.scalar(
            select(func.count())
            .select_from(ResidentSyncItem)
            .where(
                ResidentSyncItem.batch_id == batch.id,
                ResidentSyncItem.status == "pending",
                ResidentSyncItem.change_type.in_(("new", "update", "deactivate")),
            )
        )
        applied_count = db.scalar(
            select(func.count())
            .select_from(ResidentSyncItem)
            .where(
                ResidentSyncItem.batch_id == batch.id,
                ResidentSyncItem.status == "applied",
            )
        )
        batch.status = "partially_applied" if remaining_count else "applied"
        batch.applied_by_id = admin.id
        batch.applied_at = applied_at
        batch.summary = {
            **batch.summary,
            "applied": int(applied_count or 0),
            "remaining": int(remaining_count or 0),
        }
        record_audit(
            db,
            actor_id=admin.id,
            action="recipients.sync_changes_applied",
            target_type="recipient_sync_batch",
            target_id=batch.id,
            details={
                "item_ids": [str(item.id) for item in items],
                "change_counts": change_counts,
                "remaining": int(remaining_count or 0),
            },
        )
        db.commit()
    except ResidentSyncStaleError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="다른 변경과 충돌했습니다. 명단 파일을 다시 올려 확인해 주세요.",
        ) from exc

    db.refresh(batch)
    return _resident_sync_batch_response(batch)


@app.post("/api/rooms/custom", response_model=RoomResponse, status_code=201)
async def create_custom_room(
    payload: CustomRoomCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    member_ids = set(payload.member_ids)
    users = db.scalars(
        select(User).where(
            User.id.in_(member_ids),
            User.organization_id == admin.organization_id,
            User.is_active.is_(True),
        )
    ).all()
    if (
        {user.id for user in users} != member_ids
        or any(user.employment_status != "active" or user.staff_id is None for user in users)
    ):
        raise HTTPException(status_code=422, detail="참여자 중 존재하지 않거나 퇴사한 직원이 있습니다.")
    room = Room(
        organization_id=admin.organization_id,
        name=payload.name.strip(),
        kind="custom",
        created_by_id=admin.id,
        is_test_data=settings.environment != "production",
    )
    db.add(room)
    db.flush()
    for member in users:
        db.add(
            RoomMembership(
                organization_id=admin.organization_id,
                room_id=room.id,
                staff_id=member.staff_id,
                source="manual",
                created_by=admin.id,
            )
        )
    record_audit(
        db,
        actor_id=admin.id,
        action="room.custom_created",
        target_type="room",
        target_id=room.id,
        details={"member_ids": sorted(str(member_id) for member_id in member_ids)},
    )
    db.commit()
    await manager.send_to_users(
        member_ids, {"event": "rooms_changed", "room_id": str(room.id)}
    )
    return RoomResponse(
        id=room.id,
        name=room.name,
        kind=room.kind,
        unread_count=0,
        last_message=None,
        last_message_at=None,
    )


def _managed_custom_room_response(
    db: Session,
    room: Room,
) -> ManagedCustomRoomResponse:
    member_ids = list(
        db.scalars(
            select(User.id)
            .join(Staff, Staff.id == User.staff_id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room.id,
                RoomMembership.left_at.is_(None),
            )
            .order_by(Staff.display_name, User.id)
        ).all()
    )
    return ManagedCustomRoomResponse(
        id=room.id,
        name=room.name,
        is_active=room.is_active,
        member_ids=member_ids,
        created_at=_as_utc(room.created_at),
    )


def _custom_room_for_admin(db: Session, admin: User, room_id: UUID) -> Room:
    room = db.get(Room, room_id)
    if (
        room is None
        or room.organization_id != admin.organization_id
        or room.kind != "custom"
    ):
        raise HTTPException(
            status_code=422,
            detail="기본 채팅방은 종료하거나 참여자를 직접 변경할 수 없습니다.",
        )
    return room


@app.get("/api/rooms/custom", response_model=list[ManagedCustomRoomResponse])
def list_custom_rooms_for_admin(
    include_inactive: bool = False,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = (
        select(Room)
        .where(
            Room.organization_id == admin.organization_id,
            Room.kind == "custom",
        )
        .order_by(Room.is_active.desc(), Room.created_at.desc())
    )
    if not include_inactive:
        query = query.where(Room.is_active.is_(True))
    return [
        _managed_custom_room_response(db, room)
        for room in db.scalars(query).all()
    ]


@app.patch("/api/rooms/custom/{room_id}", response_model=ManagedCustomRoomResponse)
async def update_custom_room(
    room_id: UUID,
    payload: CustomRoomUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.name is None and payload.member_ids is None:
        raise HTTPException(status_code=422, detail="변경할 방 정보가 없습니다.")
    room = _custom_room_for_admin(db, admin, room_id)
    if not room.is_active:
        raise HTTPException(status_code=409, detail="이미 종료된 채팅방입니다.")

    before_member_ids = room_member_user_ids(db, room.id)
    if payload.member_ids is not None:
        desired_user_ids = set(payload.member_ids)
        users = db.scalars(
            select(User).where(
                User.id.in_(desired_user_ids),
                User.organization_id == admin.organization_id,
                User.is_active.is_(True),
            )
        ).all()
        if (
            {user.id for user in users} != desired_user_ids
            or any(
                user.employment_status != "active" or user.staff_id is None
                for user in users
            )
        ):
            raise HTTPException(
                status_code=422,
                detail="참여자 중 존재하지 않거나 퇴사한 직원이 있습니다.",
            )

        active_memberships = db.scalars(
            select(RoomMembership).where(
                RoomMembership.room_id == room.id,
                RoomMembership.left_at.is_(None),
            )
        ).all()
        memberships_by_staff = {
            membership.staff_id: membership for membership in active_memberships
        }
        desired_staff_ids = {user.staff_id for user in users}
        now = utcnow()
        for staff_id, membership in memberships_by_staff.items():
            if staff_id not in desired_staff_ids:
                membership.left_at = now
        for user in users:
            if user.staff_id not in memberships_by_staff:
                db.add(
                    RoomMembership(
                        organization_id=admin.organization_id,
                        room_id=room.id,
                        staff_id=user.staff_id,
                        source="manual",
                        created_by=admin.id,
                    )
                )

    if payload.name is not None:
        room.name = payload.name.strip()

    db.flush()
    after_member_ids = room_member_user_ids(db, room.id)
    record_audit(
        db,
        actor_id=admin.id,
        action="room.custom_updated",
        target_type="room",
        target_id=room.id,
        details={
            "name": room.name,
            "member_ids": sorted(str(member_id) for member_id in after_member_ids),
        },
    )
    response = _managed_custom_room_response(db, room)
    db.commit()
    await manager.send_to_users(
        before_member_ids | after_member_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.delete("/api/rooms/custom/{room_id}", status_code=204)
async def close_custom_room(
    room_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    room = _custom_room_for_admin(db, admin, room_id)
    if not room.is_active:
        raise HTTPException(status_code=409, detail="이미 종료된 채팅방입니다.")
    member_ids = room_member_user_ids(db, room.id)
    now = utcnow()
    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.room_id == room.id,
            RoomMembership.left_at.is_(None),
        )
    ).all()
    for membership in memberships:
        membership.left_at = now
    room.is_active = False
    record_audit(
        db,
        actor_id=admin.id,
        action="room.custom_closed",
        target_type="room",
        target_id=room.id,
        details={
            "closed_memberships": len(memberships),
            "data_retained": True,
        },
    )
    db.commit()
    await manager.send_to_users(
        member_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return Response(status_code=204)


@app.get("/api/rooms/{room_id}/messages", response_model=list[MessageResponse])
def get_messages(
    room_id: UUID,
    after_id: UUID | None = None,
    limit: int = 60,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 접근할 수 없습니다.")
    limit = min(max(limit, 1), 100)
    query = select(Message).where(Message.room_id == room_id)
    if getattr(user, "_reviewer_experience", None) is not None:
        query = query.where(Message.is_test_data.is_(True))
    if after_id:
        cursor_message = db.get(Message, after_id)
        if cursor_message is None or cursor_message.room_id != room_id:
            raise HTTPException(status_code=422, detail="메시지 조회 기준이 올바르지 않습니다.")
        if (
            getattr(user, "_reviewer_experience", None) is not None
            and not cursor_message.is_test_data
        ):
            raise HTTPException(status_code=403, detail="이 메시지에 접근할 수 없습니다.")
        query = (
            query.where(Message.created_at > cursor_message.created_at)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        messages = db.scalars(query).all()
    else:
        messages = list(
            reversed(
                db.scalars(
                    query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit)
                ).all()
            )
        )
    read_counts, reply_user_counts = message_engagement_counts(
        db, [message.id for message in messages]
    )
    return [
        message_response(
            message,
            db=db,
            viewer_id=user.id,
            read_count=read_counts.get(message.id, 0),
            reply_user_count=reply_user_counts.get(message.id, 0),
        )
        for message in messages
    ]


@app.get(
    "/api/rooms/{room_id}/message-search",
    response_model=RoomMessageSearchResponse,
)
def search_room_messages(
    room_id: UUID,
    q: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    resident_id: UUID | None = None,
    message_type: str | None = None,
    action_status: str | None = None,
    limit: int = 100,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방을 검색할 수 없습니다.")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="검색 시작일이 종료일보다 늦습니다.")
    if message_type and message_type not in {
        "chat",
        "notice",
        "handover",
        "work_request",
        "report",
    }:
        raise HTTPException(status_code=422, detail="메시지 종류가 올바르지 않습니다.")
    if action_status and action_status not in {
        "none",
        "assigned",
        "acknowledged",
        "in_progress",
        "completed",
    }:
        raise HTTPException(status_code=422, detail="업무 상태가 올바르지 않습니다.")

    kst = timezone(timedelta(hours=9))
    message_query = select(Message).where(Message.room_id == room_id)
    if getattr(user, "_reviewer_experience", None) is not None:
        message_query = message_query.where(Message.is_test_data.is_(True))
    if date_from:
        from_utc = datetime.combine(date_from, time.min, tzinfo=kst).astimezone(
            timezone.utc
        )
        message_query = message_query.where(Message.created_at >= from_utc)
    if date_to:
        until_utc = datetime.combine(
            date_to + timedelta(days=1),
            time.min,
            tzinfo=kst,
        ).astimezone(timezone.utc)
        message_query = message_query.where(Message.created_at < until_utc)
    messages = db.scalars(
        message_query
        .order_by(Message.created_at.desc(), Message.id.desc())
        .limit(5001)
    ).all()
    truncated = len(messages) > 5000
    messages = messages[:5000]
    query_text = (q or "").strip().casefold()
    matched: list[Message] = []
    for message in messages:
        local_date = _as_utc(message.created_at).astimezone(kst).date()
        if date_from and local_date < date_from:
            continue
        if date_to and local_date > date_to:
            continue
        confirmed_resident_ids = {
            link.resident_id
            for link in message.resident_links
            if link.status == "confirmed"
        }
        if resident_id and (
            message.resident_id != resident_id and resident_id not in confirmed_resident_ids
        ):
            continue
        if message_type and message.message_type != message_type:
            continue
        action_item = message.action_item
        if action_status == "none" and action_item is not None:
            continue
        if action_status and action_status != "none" and (
            action_item is None or action_item.status != action_status
        ):
            continue
        if query_text:
            attachment_texts = [
                (
                    attachment.text_extraction.reviewed_text
                    or attachment.text_extraction.extracted_text
                    or ""
                )
                for attachment in message.attachments
                if attachment.text_extraction is not None
            ]
            search_text = "\n".join(
                [
                    message.body,
                    message.sender.full_name,
                    message.resident.display_name if message.resident else "",
                    *[
                        link.resident.display_name
                        for link in message.resident_links
                        if link.status != "rejected"
                    ],
                    *[comment.body for comment in message.comments],
                    *attachment_texts,
                ]
            ).casefold()
            if query_text not in search_text:
                continue
        matched.append(message)

    result_limit = min(max(limit, 1), 200)
    result_messages = matched[:result_limit]
    read_counts, reply_user_counts = message_engagement_counts(
        db, [message.id for message in result_messages]
    )
    return RoomMessageSearchResponse(
        matched_count=len(matched),
        truncated=truncated,
        messages=[
            message_response(
                message,
                db=db,
                viewer_id=user.id,
                read_count=read_counts.get(message.id, 0),
                reply_user_count=reply_user_counts.get(message.id, 0),
            )
            for message in result_messages
        ],
    )


@app.post(
    "/api/rooms/{room_id}/message-search/summary",
    response_model=RoomSearchSummaryResponse,
)
def summarize_room_search(
    room_id: UUID,
    payload: RoomSearchSummaryRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방을 요약할 수 없습니다.")
    selected_message_query = select(Message).where(
            Message.room_id == room_id,
            Message.id.in_(payload.message_ids),
        )
    if getattr(user, "_reviewer_experience", None) is not None:
        selected_message_query = selected_message_query.where(
            Message.is_test_data.is_(True)
        )
    selected_messages = db.scalars(selected_message_query).all()
    by_id = {message.id: message for message in selected_messages}
    ordered_messages = [
        by_id[message_id]
        for message_id in payload.message_ids
        if message_id in by_id
    ]
    if len(ordered_messages) != len(payload.message_ids):
        raise HTTPException(status_code=422, detail="검색 결과에 없는 메시지가 포함되어 있습니다.")

    entries: list[dict[str, Any]] = []
    for index, message in enumerate(ordered_messages, 1):
        extracted_text = "\n".join(
            (
                attachment.text_extraction.reviewed_text
                or attachment.text_extraction.extracted_text
                or ""
            )
            for attachment in message.attachments
            if attachment.text_extraction is not None
        ).strip()
        entries.append(
            {
                "number": index,
                "created_at": _as_utc(message.created_at).isoformat(),
                "sender": message.sender.full_name,
                "resident": (
                    message.resident.display_name if message.resident else None
                ),
                "body": message.body,
                "comments": [comment.body for comment in message.comments],
                "attachment_text": extracted_text,
                "action_status": (
                    message.action_item.status if message.action_item else None
                ),
            }
        )
    try:
        result = summarize_room_messages(
            entries=entries,
            external_allowed=all(message.is_test_data for message in ordered_messages),
        )
        summary = result.summary
        generator = f"{result.provider}:{result.model}"
    except LocalAiError:
        summary_lines = [
            f"- [{index}] {entry['resident'] or '일반 대화'}: {entry['body']}"
            for index, entry in enumerate(entries[:12], 1)
        ]
        summary = (
            "AI 연결이 지연되어 검색 결과를 간단히 정리했습니다.\n"
            + "\n".join(summary_lines)
        )
        generator = "prototype-search-summary-v1"
    return RoomSearchSummaryResponse(
        summary=summary,
        source_message_ids=payload.message_ids,
        generator=generator,
    )


@app.post("/api/rooms/{room_id}/messages", response_model=MessageResponse, status_code=201)
async def send_message(
    room_id: UUID,
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 메시지를 보낼 수 없습니다.")
    if payload.message_type == "notice" and user.role != "admin":
        raise HTTPException(status_code=403, detail="공지 작성은 관리자만 가능합니다.")
    room = db.get(Room, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    interaction_is_test_data = _interaction_is_test_data(user, room=room)
    resident = _resident_for_room(db, room, payload.resident_id)
    message = Message(
        organization_id=user.organization_id,
        room_id=room_id,
        sender_id=user.id,
        message_type=payload.message_type,
        body=payload.body,
        resident_id=resident.id if resident else None,
        resident_ref=payload.resident_ref,
        is_test_data=interaction_is_test_data,
    )
    db.add(message)
    db.flush()
    _confirm_manual_resident_link(db, message=message, resident=resident)
    detected_links = _sync_message_resident_candidates(
        db,
        message=message,
        text=message.body,
        source="text_exact",
    )
    _create_action_item(
        db,
        message=message,
        creator=user,
        payload=payload.action,
    )
    db.add(
        MessageReadReceipt(
            organization_id=user.organization_id,
            message_id=message.id,
            user_id=user.id,
            is_test_data=interaction_is_test_data,
        )
    )
    _ensure_work_item(
        db,
        message,
        force=resident is not None or bool(detected_links),
    )
    db.commit()
    db.refresh(message)
    response_payload = message_response(message, db=db, viewer_id=user.id)
    member_ids = room_member_user_ids(db, room_id)
    push_recipient_ids = member_ids - {user.id}
    if push_recipient_ids:
        background_tasks.add_task(
            send_web_push_to_users,
            push_recipient_ids,
            room_id=room_id,
        )
    await manager.send_to_users(
        member_ids,
        {"event": "message_created", "message": response_payload.model_dump(mode="json")},
    )
    return response_payload


@app.post(
    "/api/rooms/{room_id}/messages-with-files",
    response_model=MessageResponse,
    status_code=201,
)
@app.post(
    "/api/rooms/{room_id}/messages-with-photos",
    response_model=MessageResponse,
    status_code=201,
    include_in_schema=False,
)
async def send_message_with_files(
    room_id: UUID,
    background_tasks: BackgroundTasks,
    body: Annotated[str, Form(max_length=2000)] = "",
    message_type: Annotated[str, Form()] = "chat",
    resident_id: Annotated[UUID | None, Form()] = None,
    action_type: Annotated[str | None, Form()] = None,
    assignee_user_id: Annotated[UUID | None, Form()] = None,
    assignee_unit_id: Annotated[UUID | None, Form()] = None,
    action_priority: Annotated[str, Form()] = "normal",
    action_due_at: Annotated[datetime | None, Form()] = None,
    report_image: Annotated[bool, Form()] = False,
    files: Annotated[list[UploadFile], File()] = [],
    photos: Annotated[list[UploadFile], File()] = [],
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 메시지를 보낼 수 없습니다.")
    if message_type not in {
        "chat",
        "notice",
        "handover",
        "work_request",
        "report",
    }:
        raise HTTPException(status_code=422, detail="지원하지 않는 메시지 종류입니다.")
    if message_type == "notice" and user.role != "admin":
        raise HTTPException(status_code=403, detail="공지 작성은 관리자만 가능합니다.")
    uploads = [*files, *photos]
    if not uploads:
        raise HTTPException(status_code=422, detail="첨부할 파일을 선택해 주세요.")
    if len(uploads) > settings.max_attachments_per_message:
        raise HTTPException(
            status_code=422,
            detail=(
                f"파일은 한 메시지에 최대 {settings.max_attachments_per_message}개까지 "
                "첨부할 수 있습니다."
            ),
        )
    room = db.get(Room, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    interaction_is_test_data = _interaction_is_test_data(user, room=room)
    resident = _resident_for_room(db, room, resident_id)
    if report_image and not any(
        (upload.content_type or "").lower() in IMAGE_MIME_TYPES for upload in uploads
    ):
        raise HTTPException(
            status_code=422,
            detail="보고서 이미지 판독을 선택했지만 이미지 파일이 없습니다.",
        )
    normalized_body = body.strip()
    if not normalized_body:
        normalized_body = (
            "보고서 이미지를 첨부했습니다."
            if report_image
            else "파일을 첨부했습니다."
        )
    message = Message(
        organization_id=user.organization_id,
        room_id=room_id,
        sender_id=user.id,
        message_type=message_type,
        body=normalized_body,
        resident_id=resident.id if resident else None,
        is_test_data=interaction_is_test_data,
    )
    db.add(message)
    db.flush()
    _confirm_manual_resident_link(db, message=message, resident=resident)
    detected_links = _sync_message_resident_candidates(
        db,
        message=message,
        text=message.body,
        source="text_exact",
    )
    action_payload = None
    if action_type:
        try:
            action_payload = ActionItemCreate(
                action_type=action_type,
                assignee_user_id=assignee_user_id,
                assignee_unit_id=assignee_unit_id,
                priority=action_priority,
                due_at=action_due_at,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="업무 지정정보가 올바르지 않습니다.") from exc
    _create_action_item(
        db,
        message=message,
        creator=user,
        payload=action_payload,
    )
    stored_paths: list[Path] = []
    extraction_attachment_ids: list[UUID] = []
    try:
        for upload in uploads:
            attachment, stored_path = await _store_attachment(
                db,
                upload=upload,
                message=message,
                user=user,
            )
            stored_paths.append(stored_path)
            should_extract_image = (
                report_image and attachment.mime_type in IMAGE_MIME_TYPES
            )
            should_transcribe_audio = (
                settings.stt_enabled and attachment.mime_type in AUDIO_MIME_TYPES
            )
            if should_extract_image or should_transcribe_audio:
                _queue_attachment_text_extraction(
                    db,
                    attachment=attachment,
                    requested_by=user,
                )
                extraction_attachment_ids.append(attachment.id)
        db.add(
            MessageReadReceipt(
                organization_id=user.organization_id,
                message_id=message.id,
                user_id=user.id,
                is_test_data=interaction_is_test_data,
            )
        )
        _ensure_work_item(
            db,
            message,
            force=(
                report_image
                or resident is not None
                or bool(detected_links)
                or any(
                    (upload.content_type or "").lower() in AUDIO_MIME_TYPES
                    for upload in uploads
                )
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        for stored_path in stored_paths:
            stored_path.unlink(missing_ok=True)
        raise
    db.refresh(message)
    response_payload = message_response(message, db=db, viewer_id=user.id)
    for attachment_id in extraction_attachment_ids:
        background_tasks.add_task(_run_attachment_text_extraction, attachment_id)
    member_ids = room_member_user_ids(db, room_id)
    push_recipient_ids = member_ids - {user.id}
    if push_recipient_ids:
        background_tasks.add_task(
            send_web_push_to_users,
            push_recipient_ids,
            room_id=room_id,
        )
    await manager.send_to_users(
        member_ids,
        {"event": "message_created", "message": response_payload.model_dump(mode="json")},
    )
    return response_payload


def _copy_forwarded_attachments(
    db: Session,
    *,
    source_message: Message,
    target_message: Message,
    user: User,
) -> list[Path]:
    upload_dir = Path(settings.upload_dir).resolve()
    upload_dir.mkdir(parents=True, exist_ok=True)
    copied_paths: list[Path] = []
    for source_attachment in source_message.attachments:
        source_path = (upload_dir / source_attachment.storage_key).resolve()
        if source_path.parent != upload_dir or not source_path.is_file():
            raise HTTPException(
                status_code=409,
                detail=f"원본 첨부파일을 찾을 수 없습니다: {source_attachment.original_name}",
            )
        extension = Path(source_attachment.storage_key).suffix.lower()
        target_key = f"{uuid4().hex}{extension}"
        target_path = (upload_dir / target_key).resolve()
        if target_path.parent != upload_dir:
            raise HTTPException(status_code=422, detail="올바르지 않은 첨부파일 경로입니다.")
        copy2(source_path, target_path)
        copied_paths.append(target_path)
        db.add(
            MessageAttachment(
                organization_id=target_message.organization_id,
                owner_module_code="staff_hub",
                entity_type="staff_hub_message",
                message_id=target_message.id,
                uploader_id=user.id,
                storage_key=target_key,
                original_name=source_attachment.original_name,
                mime_type=source_attachment.mime_type,
                size_bytes=source_attachment.size_bytes,
                sha256=source_attachment.sha256,
            )
        )
    return copied_paths


@app.post(
    "/api/messages/{message_id}/forward",
    response_model=list[MessageResponse],
    status_code=201,
)
async def forward_message(
    message_id: UUID,
    payload: MessageForwardRequest,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    source_message = _message_for_member(db, user, message_id)
    memberships = db.scalars(
        select(RoomMembership)
        .join(Room, Room.id == RoomMembership.room_id)
        .where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
            Room.is_active.is_(True),
        )
    ).all()
    allowed_rooms = {membership.room_id: membership.room for membership in memberships}
    if payload.to_all_joined_rooms:
        if payload.room_ids:
            raise HTTPException(
                status_code=422,
                detail="전체 전달과 개별 방 선택을 동시에 사용할 수 없습니다.",
            )
        target_ids = {
            room_id
            for room_id, room in allowed_rooms.items()
            if room_id != source_message.room_id and room.kind != "self"
        }
    else:
        target_ids = set(payload.room_ids)
        target_ids.discard(source_message.room_id)
        unauthorized = target_ids - allowed_rooms.keys()
        if unauthorized:
            raise HTTPException(
                status_code=403,
                detail="참여하지 않은 채팅방에는 전달할 수 없습니다.",
            )
    if not target_ids:
        raise HTTPException(status_code=422, detail="전달할 다른 채팅방을 선택해 주세요.")
    if len(target_ids) > 50:
        raise HTTPException(status_code=422, detail="한 번에 최대 50개 방까지 전달할 수 있습니다.")

    source_info = {
        "message_id": str(source_message.id),
        "room_name": source_message.room.name,
        "sender_name": source_message.sender.full_name,
        "created_at": _as_utc(source_message.created_at).isoformat(),
    }
    forwarded_messages: list[Message] = []
    copied_paths: list[Path] = []
    try:
        for room_id in sorted(target_ids, key=str):
            target = Message(
                organization_id=user.organization_id,
                room_id=room_id,
                sender_id=user.id,
                message_type="chat",
                body=source_message.body,
                resident_id=source_message.resident_id,
                resident_ref=source_message.resident_ref,
                extra_data={"forwarded_from": source_info},
                is_test_data=settings.environment != "production",
            )
            db.add(target)
            db.flush()
            for source_link in source_message.resident_links:
                db.add(
                    MessageResidentLink(
                        organization_id=user.organization_id,
                        message_id=target.id,
                        resident_id=source_link.resident_id,
                        source=source_link.source,
                        status=source_link.status,
                        reviewed_by_id=source_link.reviewed_by_id,
                        reviewed_at=source_link.reviewed_at,
                    )
                )
            copied_paths.extend(
                _copy_forwarded_attachments(
                    db,
                    source_message=source_message,
                    target_message=target,
                    user=user,
                )
            )
            db.add(
                MessageReadReceipt(
                    organization_id=user.organization_id,
                    message_id=target.id,
                    user_id=user.id,
                    is_test_data=settings.environment != "production",
                )
            )
            forwarded_messages.append(target)
        record_audit(
            db,
            actor_id=user.id,
            action="message.forwarded",
            target_type="message",
            target_id=source_message.id,
            details={"target_room_ids": sorted(str(room_id) for room_id in target_ids)},
        )
        db.commit()
    except Exception:
        db.rollback()
        for copied_path in copied_paths:
            copied_path.unlink(missing_ok=True)
        raise

    responses: list[MessageResponse] = []
    for target in forwarded_messages:
        db.refresh(target)
        response_payload = message_response(target, db=db, viewer_id=user.id)
        responses.append(response_payload)
        member_ids = room_member_user_ids(db, target.room_id)
        push_recipient_ids = member_ids - {user.id}
        if push_recipient_ids:
            background_tasks.add_task(
                send_web_push_to_users,
                push_recipient_ids,
                room_id=target.room_id,
            )
        await manager.send_to_users(
            member_ids,
            {
                "event": "message_created",
                "message": response_payload.model_dump(mode="json"),
            },
        )
    return responses


@app.post("/api/rooms/{room_id}/read", status_code=204)
async def mark_read(
    room_id: UUID,
    payload: ReadRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    membership = active_membership(db, user.id, room_id)
    if membership is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 접근할 수 없습니다.")
    message = db.get(Message, payload.message_id)
    if message is None or message.room_id != room_id:
        raise HTTPException(status_code=422, detail="이 채팅방의 메시지가 아닙니다.")
    if (
        getattr(user, "_reviewer_experience", None) is not None
        and not message.is_test_data
    ):
        raise HTTPException(status_code=403, detail="이 메시지에 접근할 수 없습니다.")
    membership.last_read_message_id = payload.message_id
    message_ids = set(
        db.scalars(
            select(Message.id).where(
                Message.room_id == room_id,
                Message.created_at <= message.created_at,
                *(
                    [Message.is_test_data.is_(True)]
                    if getattr(user, "_reviewer_experience", None) is not None
                    else []
                ),
            )
        ).all()
    )
    existing_receipts = set(
        db.scalars(
            select(MessageReadReceipt.message_id).where(
                MessageReadReceipt.user_id == user.id,
                MessageReadReceipt.message_id.in_(message_ids),
            )
        ).all()
    )
    newly_read_message_ids = message_ids - existing_receipts
    for message_id in newly_read_message_ids:
        db.add(
            MessageReadReceipt(
                organization_id=user.organization_id,
                message_id=message_id,
                user_id=user.id,
                is_test_data=_interaction_is_test_data(user, message=message),
            )
        )
    db.commit()
    member_ids = room_member_user_ids(db, room_id)
    await manager.send_to_users(
        member_ids,
        {
            "event": "messages_read",
            "room_id": str(room_id),
            "message_id": str(payload.message_id),
            "message_ids": [
                str(message_id) for message_id in newly_read_message_ids
            ],
            "user_id": str(user.id),
        },
    )


@app.get("/api/messages/{message_id}", response_model=MessageDetailResponse)
def message_detail(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _message_for_member(db, user, message_id)
    receipts = db.scalars(
        select(MessageReadReceipt)
        .where(MessageReadReceipt.message_id == message.id)
        .order_by(MessageReadReceipt.read_at, MessageReadReceipt.id)
    ).all()
    comments = db.scalars(
        select(MessageComment)
        .where(MessageComment.message_id == message.id)
        .order_by(MessageComment.id)
    ).all()
    return MessageDetailResponse(
        message=message_response(message, db=db, viewer_id=user.id),
        read_receipts=[
            ReadReceiptResponse(
                user_id=receipt.user_id,
                user_name=receipt.user.full_name,
                read_at=_as_utc(receipt.read_at),
            )
            for receipt in receipts
        ],
        comments=[
            MessageCommentResponse(
                id=comment.id,
                author_id=comment.author_id,
                author_name=comment.author.full_name,
                body=comment.body,
                created_at=_as_utc(comment.created_at),
            )
            for comment in comments
        ],
    )


@app.post(
    "/api/messages/{message_id}/action-item",
    response_model=ActionItemResponse,
    status_code=201,
)
async def create_message_action_item(
    message_id: UUID,
    payload: ActionItemCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _message_for_member(db, user, message_id)
    existing = db.scalar(
        select(ActionItem).where(ActionItem.source_message_id == message.id)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="이미 담당자가 지정된 메시지입니다.")
    item = _create_action_item(
        db,
        message=message,
        creator=user,
        payload=payload,
    )
    if item is None:
        raise HTTPException(status_code=422, detail="업무 전달 내용을 확인해 주세요.")
    db.flush()
    assignee_name = (
        item.assignee_user.full_name
        if item.assignee_user is not None
        else item.assignee_unit.name
        if item.assignee_unit is not None
        else "담당자"
    )
    comment = MessageComment(
        organization_id=user.organization_id,
        message_id=message.id,
        author_id=user.id,
        body=f"업무로 전달했습니다. 담당: {assignee_name}",
        is_test_data=_interaction_is_test_data(user, message=message),
    )
    db.add(comment)
    record_audit(
        db,
        actor_id=user.id,
        action="action_item.created_from_message",
        target_type="action_item",
        target_id=item.id,
        details={
            "message_id": str(message.id),
            "action_type": item.action_type,
            "assignee_user_id": (
                str(item.assignee_user_id) if item.assignee_user_id else None
            ),
            "assignee_unit_id": (
                str(item.assignee_unit_id) if item.assignee_unit_id else None
            ),
        },
    )
    db.commit()
    db.refresh(item)
    member_ids = room_member_user_ids(db, message.room_id)
    response_payload = action_item_response(item)
    await manager.send_to_users(
        member_ids,
        {
            "event": "action_item_changed",
            "message_id": str(message.id),
            "room_id": str(message.room_id),
            "action_item": response_payload.model_dump(mode="json"),
        },
    )
    return response_payload


@app.patch(
    "/api/messages/{message_id}/resident-links/{resident_id}",
    response_model=MessageResponse,
)
def review_message_resident_link(
    message_id: UUID,
    resident_id: UUID,
    payload: MessageResidentLinkUpdate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    message = db.get(Message, message_id)
    if message is None or message.organization_id != processor.organization_id:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    item = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == message.id)
    )
    if item is None:
        raise HTTPException(status_code=404, detail="업무함 항목을 찾을 수 없습니다.")
    _work_item_for_processor(db, processor, item.id)
    if item.confirmed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="담당자가 확정한 기록의 어르신 연결은 변경할 수 없습니다.",
        )
    link = db.scalar(
        select(MessageResidentLink).where(
            MessageResidentLink.message_id == message.id,
            MessageResidentLink.resident_id == resident_id,
        )
    )
    if link is None:
        raise HTTPException(status_code=404, detail="어르신 연결 후보를 찾을 수 없습니다.")
    if link.source == "manual":
        raise HTTPException(
            status_code=409,
            detail="작성자가 직접 선택한 어르신은 이 화면에서 해제할 수 없습니다.",
        )
    link.status = payload.status
    link.reviewed_by_id = processor.id
    link.reviewed_at = utcnow()
    db.flush()
    _refresh_work_item_residents(db, message)
    record_audit(
        db,
        actor_id=processor.id,
        action="message_resident_link.reviewed",
        target_type="message",
        target_id=message.id,
        details={
            "resident_id": str(resident_id),
            "status": payload.status,
            "source": link.source,
        },
    )
    db.commit()
    db.refresh(message)
    return message_response(message, db=db, viewer_id=processor.id)


@app.post("/api/messages/{message_id}/comments/read", status_code=204)
def mark_message_comments_read(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _message_for_member(db, user, message_id)
    thread_view = db.scalar(
        select(MessageThreadView).where(
            MessageThreadView.message_id == message.id,
            MessageThreadView.user_id == user.id,
        )
    )
    if thread_view is None:
        db.add(
            MessageThreadView(
                organization_id=user.organization_id,
                message_id=message.id,
                user_id=user.id,
                last_viewed_at=utcnow(),
            )
        )
    else:
        thread_view.last_viewed_at = utcnow()
    db.commit()
    return Response(status_code=204)


@app.post(
    "/api/messages/{message_id}/comments",
    response_model=MessageCommentResponse,
    status_code=201,
)
async def add_message_comment(
    message_id: UUID,
    payload: MessageCommentCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _message_for_member(db, user, message_id)
    comment = MessageComment(
        organization_id=user.organization_id,
        message_id=message.id,
        author_id=user.id,
        body=payload.body,
        is_test_data=settings.environment != "production",
    )
    db.add(comment)
    db.flush()
    work_item = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == message.id)
    )
    if work_item is not None:
        _refresh_work_item_suggestion(db, work_item)
    db.commit()
    db.refresh(comment)
    response_payload = MessageCommentResponse(
        id=comment.id,
        author_id=user.id,
        author_name=user.full_name,
        body=comment.body,
        created_at=_as_utc(comment.created_at),
    )
    member_ids = room_member_user_ids(db, message.room_id)
    comment_count = int(
        db.scalar(
            select(func.count(MessageComment.id)).where(
                MessageComment.message_id == message.id
            )
        )
        or 0
    )
    reply_user_count = int(
        db.scalar(
            select(func.count(func.distinct(MessageComment.author_id))).where(
                MessageComment.message_id == message.id
            )
        )
        or 0
    )
    push_recipient_ids = member_ids - {user.id}
    if push_recipient_ids:
        background_tasks.add_task(
            send_web_push_to_users,
            push_recipient_ids,
            room_id=message.room_id,
            message_id=message.id,
            comment_id=comment.id,
            notification_kind="comment",
        )
    await manager.send_to_users(
        member_ids,
        {
            "event": "message_commented",
            "message_id": str(message.id),
            "room_id": str(message.room_id),
            "comment": response_payload.model_dump(mode="json"),
            "comment_count": comment_count,
            "reply_user_count": reply_user_count,
            "notification_user_ids": [
                str(recipient_id)
                for recipient_id in sorted(push_recipient_ids, key=str)
            ],
        },
    )
    return response_payload


@app.get("/api/attachments/{attachment_id}")
def download_attachment(
    attachment_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    _message_for_member(db, user, attachment.message_id)
    return _attachment_file_response(attachment)


def _attachment_file_response(attachment: MessageAttachment) -> FileResponse:
    upload_dir = Path(settings.upload_dir).resolve()
    target = (upload_dir / attachment.storage_key).resolve()
    if target.parent != upload_dir or not target.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    return FileResponse(target, media_type=attachment.mime_type)


def _attachment_for_processor(
    db: Session,
    processor: User,
    attachment_id: UUID,
) -> MessageAttachment:
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    if getattr(processor, "_reviewer_experience", None) is not None:
        _message_for_member(db, processor, attachment.message_id)
        return attachment
    item = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == attachment.message_id)
    )
    if item is None:
        message = db.get(Message, attachment.message_id)
        if (
            message is None
            or message.organization_id != processor.organization_id
        ):
            raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
        if processor.role != "admin" and active_membership(
            db,
            processor.id,
            message.room_id,
        ) is None:
            raise HTTPException(
                status_code=403,
                detail="이 채팅방의 첨부파일을 열 수 없습니다.",
            )
        return attachment
    _work_item_for_processor(db, processor, item.id)
    return attachment


def _attachment_for_text_editor(
    db: Session,
    editor: User,
    attachment_id: UUID,
) -> MessageAttachment:
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    if attachment.uploader_id == editor.id:
        _message_for_member(db, editor, attachment.message_id)
        return attachment
    if editor.role == "admin" or editor.can_process_records:
        return _attachment_for_processor(db, editor, attachment_id)
    raise HTTPException(
        status_code=403,
        detail="작성자 본인 또는 업무 담당자만 판독 내용을 수정할 수 있습니다.",
    )


@app.get("/api/workdesk/attachments/{attachment_id}")
def download_workdesk_attachment(
    attachment_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_processor(db, processor, attachment_id)
    return _attachment_file_response(attachment)


@app.post(
    "/api/attachments/{attachment_id}/text-extraction",
    response_model=AttachmentResponse,
)
def retry_attachment_text_extraction(
    attachment_id: UUID,
    background_tasks: BackgroundTasks,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    if attachment.mime_type not in IMAGE_MIME_TYPES | AUDIO_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail="이미지 글자 판독과 음성파일 받아쓰기만 지원합니다.",
        )
    if attachment.mime_type in AUDIO_MIME_TYPES and not settings.stt_enabled:
        raise HTTPException(
            status_code=503,
            detail="로컬 음성 판독 기능이 꺼져 있습니다.",
        )
    _queue_attachment_text_extraction(
        db,
        attachment=attachment,
        requested_by=editor,
    )
    record_audit(
        db,
        actor_id=editor.id,
        action="attachment_text_extraction.requested",
        target_type="attachment",
        target_id=attachment.id,
    )
    db.commit()
    db.expire(attachment, ["text_extraction"])
    response_payload = attachment_response(attachment, db=db)
    background_tasks.add_task(_run_attachment_text_extraction, attachment.id)
    return response_payload


@app.patch(
    "/api/attachments/{attachment_id}/text-extraction",
    response_model=AttachmentResponse,
)
def review_attachment_text_extraction(
    attachment_id: UUID,
    payload: AttachmentTextExtractionUpdate,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    extraction = attachment.text_extraction
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        raise HTTPException(
            status_code=409,
            detail="먼저 이미지 글자 판독을 완료해 주세요.",
        )
    original_extracted_text = (
        extraction.original_extracted_text or extraction.extracted_text or ""
    )
    is_image_extraction = attachment.mime_type in IMAGE_MIME_TYPES
    is_processor = editor.role == "admin" or editor.can_process_records
    if not is_processor and payload.decision != "direct_edit":
        raise HTTPException(
            status_code=422,
            detail="작성자는 판독문을 직접 확인하고 수정한 결과만 저장할 수 있습니다.",
        )
    if not is_image_extraction and payload.decision != "direct_edit":
        raise HTTPException(
            status_code=422,
            detail="음성 받아쓰기는 확인한 내용을 직접 저장해 주세요.",
        )
    resident_names = [
        resident.display_name
        for resident in db.scalars(
            select(Resident).where(
                Resident.organization_id == attachment.message.organization_id,
                Resident.is_active.is_(True),
            )
        ).all()
    ]
    if payload.decision == "apply_candidate":
        response_before_review = attachment_response(attachment, db=db)
        known_candidate_ids = {
            candidate.id
            for candidate in (
                response_before_review.text_extraction.spelling_candidates
                if response_before_review.text_extraction is not None
                else []
            )
        }
        if payload.selected_candidate_id not in known_candidate_ids:
            raise HTTPException(
                status_code=422,
                detail="현재 표시된 교정 후보를 다시 선택해 주세요.",
            )
    if payload.decision == "keep_raw":
        confirmed_text: str | None = original_extracted_text
    elif payload.decision == "needs_review":
        confirmed_text = payload.reviewed_text
    else:
        confirmed_text = payload.reviewed_text
    correction_pairs = build_correction_pairs(
        original_extracted_text,
        confirmed_text,
        resident_names=resident_names,
    )
    content_types = {
        str(pair.get("content_type", "general")) for pair in correction_pairs
    }
    event_content_type = (
        next(iter(content_types))
        if len(content_types) == 1
        else ("mixed" if content_types else "general")
    )
    confirmed = payload.decision != "needs_review"
    if is_image_extraction:
        db.add(
            OcrCorrectionEvent(
                organization_id=attachment.message.organization_id,
                extraction_id=extraction.id,
                attachment_id=attachment.id,
                source_message_id=attachment.message_id,
                source_writer_id=attachment.message.sender_id,
                reviewed_by_id=editor.id,
                decision=payload.decision,
                raw_text=original_extracted_text,
                corrected_text=confirmed_text,
                correction_pairs=correction_pairs if confirmed else [],
                content_type=event_content_type,
                context_text=original_extracted_text[:4000],
                provider=extraction.provider,
                model_name=extraction.model_name,
                visual_signature=extraction.visual_signature,
                selected_candidate_id=payload.selected_candidate_id,
                confirmed=confirmed,
            )
        )
    learned_corrections = [
        (
            str(pair["recognized_text"]),
            str(pair["corrected_text"]),
        )
        for pair in correction_pairs
        if confirmed and is_image_extraction
    ]
    if confirmed:
        extraction.reviewed_text = confirmed_text
        extraction.reviewed_by_id = editor.id
        extraction.reviewed_at = utcnow()
        extraction.status = "reviewed"
        _sync_message_resident_candidates(
            db,
            message=attachment.message,
            text=confirmed_text or original_extracted_text,
            source=(
                "ocr_exact"
                if is_image_extraction
                else "audio_transcript"
            ),
        )
    for recognized_text, corrected_text in learned_corrections:
        memory = db.scalar(
            select(OcrCorrectionMemory).where(
                OcrCorrectionMemory.organization_id
                == attachment.message.organization_id,
                OcrCorrectionMemory.recognized_text == recognized_text,
                OcrCorrectionMemory.corrected_text == corrected_text,
            )
        )
        if memory is None:
            db.add(
                OcrCorrectionMemory(
                    organization_id=attachment.message.organization_id,
                    recognized_text=recognized_text,
                    corrected_text=corrected_text,
                    last_reviewed_by_id=editor.id,
                )
            )
        else:
            memory.occurrence_count += 1
            memory.last_reviewed_by_id = editor.id
    item = db.scalar(
        select(WorkItem).where(WorkItem.source_message_id == attachment.message_id)
    )
    if confirmed and item is not None and item.confirmed_at is None:
        item.ai_state = "not_requested"
        item.ai_payload = None
        item.ai_generator = None
        item.ai_generated_at = None
    record_audit(
        db,
        actor_id=editor.id,
        action="attachment_text_extraction.reviewed",
        target_type="attachment",
        target_id=attachment.id,
        details={
            "decision": payload.decision,
            "confirmed": confirmed,
            "learned_correction_count": len(learned_corrections),
            "protected_candidate_count": sum(
                1
                for pair in correction_pairs
                if pair.get("content_type") in PROTECTED_CONTENT_TYPES
            ),
        },
    )
    db.commit()
    db.refresh(attachment)
    return attachment_response(attachment, db=db)


def _current_user_unit_ids(user: User) -> set[UUID]:
    if user.staff is None:
        return set()
    return {
        unit.id
        for unit in (
            user.business,
            user.department,
            user.floor,
            user.team,
        )
        if unit is not None
    }


def _can_access_action_item(user: User, item: ActionItem) -> bool:
    return (
        user.role == "admin"
        or item.created_by_id == user.id
        or item.assignee_user_id == user.id
        or (
            item.assignee_unit_id is not None
            and item.assignee_unit_id in _current_user_unit_ids(user)
        )
    )


@app.get("/api/action-items", response_model=list[ActionItemResponse])
def list_action_items(
    status_filter: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        select(ActionItem)
        .where(ActionItem.organization_id == user.organization_id)
        .order_by(ActionItem.updated_at.desc(), ActionItem.created_at.desc())
    )
    if status_filter:
        query = query.where(ActionItem.status == status_filter)
    items = db.scalars(query.limit(300)).all()
    return [
        action_item_response(item)
        for item in items
        if _can_access_action_item(user, item)
    ]


@app.patch("/api/action-items/{action_item_id}", response_model=ActionItemResponse)
async def update_action_item(
    action_item_id: UUID,
    payload: ActionItemUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = db.get(ActionItem, action_item_id)
    if (
        item is None
        or item.organization_id != user.organization_id
        or not _can_access_action_item(user, item)
    ):
        raise HTTPException(status_code=404, detail="업무 항목을 찾을 수 없습니다.")
    now = utcnow()
    previous_status = item.status
    item.status = payload.status
    if payload.status in {"acknowledged", "in_progress"} and item.acknowledged_at is None:
        item.acknowledged_at = now
    if payload.status == "completed":
        item.completed_at = now
        if item.acknowledged_at is None:
            item.acknowledged_at = now
    elif item.completed_at is not None:
        item.completed_at = None
    status_comment = {
        "acknowledged": "업무 내용을 확인했습니다.",
        "in_progress": "업무 처리를 시작했습니다.",
        "completed": "업무 처리를 완료했습니다.",
    }.get(payload.status)
    if previous_status != payload.status and status_comment:
        db.add(
            MessageComment(
                organization_id=user.organization_id,
                message_id=item.source_message_id,
                author_id=user.id,
                body=status_comment,
                is_test_data=settings.environment != "production",
            )
        )
    record_audit(
        db,
        actor_id=user.id,
        action="action_item.updated",
        target_type="action_item",
        target_id=item.id,
        details={"status": item.status},
    )
    db.commit()
    db.refresh(item)
    member_ids = room_member_user_ids(db, item.source_message.room_id)
    await manager.send_to_users(
        member_ids,
        {
            "event": "action_item_changed",
            "message_id": str(item.source_message_id),
            "room_id": str(item.source_message.room_id),
            "action_item": action_item_response(item).model_dump(mode="json"),
        },
    )
    return action_item_response(item)


def _digest_period(
    period: str,
    anchor: date | None,
) -> tuple[datetime, datetime]:
    if period not in {"day", "week", "month"}:
        raise HTTPException(status_code=422, detail="요약 기간은 day, week, month 중 하나여야 합니다.")
    kst = timezone(timedelta(hours=9))
    anchor_date = anchor or datetime.now(kst).date()
    if period == "week":
        start_date = anchor_date - timedelta(days=anchor_date.weekday())
        end_date = start_date + timedelta(days=7)
    elif period == "month":
        start_date = anchor_date.replace(day=1)
        if start_date.month == 12:
            end_date = start_date.replace(year=start_date.year + 1, month=1)
        else:
            end_date = start_date.replace(month=start_date.month + 1)
    else:
        start_date = anchor_date
        end_date = start_date + timedelta(days=1)
    start = datetime.combine(start_date, time.min, tzinfo=kst).astimezone(timezone.utc)
    end = datetime.combine(end_date, time.min, tzinfo=kst).astimezone(timezone.utc)
    return start, end


def _room_digest_response(digest: RoomDigest) -> RoomDigestResponse:
    return RoomDigestResponse(
        id=digest.id,
        room_id=digest.room_id,
        room_name=digest.room.name,
        period_start=_as_utc(digest.period_start),
        period_end=_as_utc(digest.period_end),
        message_count=digest.message_count,
        comment_count=digest.comment_count,
        resident_count=digest.resident_count,
        summary=digest.summary,
        major_points=[
            RoomDigestPoint.model_validate(point)
            for point in digest.major_points
        ],
        document_counts=digest.document_counts,
        risk_counts=digest.risk_counts,
        generator=digest.generator,
        generated_at=_as_utc(digest.generated_at),
    )


@app.get("/api/workdesk/room-digests", response_model=list[RoomDigestResponse])
def list_room_digests(
    period: str = "day",
    anchor: date | None = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    period_start, period_end = _digest_period(period, anchor)
    room_query = select(Room).where(Room.organization_id == processor.organization_id)
    if processor.role != "admin":
        if processor.staff_id is None:
            return []
        room_query = (
            room_query.join(RoomMembership, RoomMembership.room_id == Room.id)
            .where(
                RoomMembership.staff_id == processor.staff_id,
                RoomMembership.left_at.is_(None),
            )
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(
            Room.name == settings.reviewer_chat_room_name,
            Room.is_test_data.is_(True),
        )
    rooms = db.scalars(room_query.order_by(Room.name)).unique().all()
    digests: list[RoomDigest] = []
    suggestions_changed = False
    for room in rooms:
        messages = db.scalars(
            select(Message)
            .where(
                Message.room_id == room.id,
                Message.created_at >= period_start,
                Message.created_at < period_end,
            )
            .order_by(Message.created_at, Message.id)
        ).all()
        if not messages:
            continue
        message_ids = [message.id for message in messages]
        comments = db.scalars(
            select(MessageComment)
            .where(MessageComment.message_id.in_(message_ids))
            .order_by(MessageComment.created_at, MessageComment.id)
        ).all()
        comments_by_message: dict[UUID, list[MessageComment]] = {}
        for comment in comments:
            comments_by_message.setdefault(comment.message_id, []).append(comment)

        work_items = db.scalars(
            select(WorkItem).where(WorkItem.source_message_id.in_(message_ids))
        ).all()
        confirmed_resident_links = db.scalars(
            select(MessageResidentLink)
            .where(
                MessageResidentLink.message_id.in_(message_ids),
                MessageResidentLink.status == "confirmed",
            )
            .order_by(
                MessageResidentLink.message_id,
                MessageResidentLink.created_at,
                MessageResidentLink.id,
            )
        ).all()
        resident_names_by_message: dict[UUID, list[str]] = {}
        for link in confirmed_resident_links:
            resident_names_by_message.setdefault(link.message_id, []).append(
                link.resident.display_name
            )
        for message in messages:
            if (
                not resident_names_by_message.get(message.id)
                and message.resident is not None
            ):
                resident_names_by_message[message.id] = [
                    message.resident.display_name
                ]
        for item in work_items:
            suggestions_changed = _refresh_work_item_suggestion(db, item) or suggestions_changed
        document_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        for item in work_items:
            payload = item.confirmed_payload or item.ai_payload or {}
            for document_type in payload.get("document_types", []):
                document_counts[document_type] = document_counts.get(document_type, 0) + 1
            risk_level = payload.get("risk_level")
            if risk_level:
                risk_counts[risk_level] = risk_counts.get(risk_level, 0) + 1

        ranked_messages = sorted(
            messages,
            key=lambda message: (
                message.action_item is not None,
                message.message_type == "notice",
                bool(resident_names_by_message.get(message.id)),
                len(comments_by_message.get(message.id, [])),
                message.created_at,
            ),
            reverse=True,
        )
        major_points = [
            {
                "message_id": str(message.id),
                "resident_name": (
                    ", ".join(resident_names_by_message.get(message.id, []))
                    or None
                ),
                "body": message.body,
                "sender_name": message.sender.full_name,
                "created_at": _as_utc(message.created_at).isoformat(),
                "comment_count": len(comments_by_message.get(message.id, [])),
                "action_type": (
                    message.action_item.action_type if message.action_item else None
                ),
            }
            for message in ranked_messages[:10]
        ]
        resident_names = sorted(
            {
                resident_name
                for message in messages
                for resident_name in resident_names_by_message.get(message.id, [])
            }
        )
        resident_message_count = sum(
            1 for message in messages if resident_names_by_message.get(message.id)
        )
        summary_parts = [
            f"대화 {len(messages)}건",
            f"댓글 {len(comments)}건",
            f"어르신 관련 {resident_message_count}건",
            f"업무 지정 {sum(1 for message in messages if message.action_item is not None)}건",
        ]
        if resident_names:
            summary_parts.append(
                "관련 어르신 " + ", ".join(resident_names[:6])
                + (" 외" if len(resident_names) > 6 else "")
            )
        digest = db.scalar(
            select(RoomDigest).where(
                RoomDigest.room_id == room.id,
                RoomDigest.period_start == period_start,
                RoomDigest.period_end == period_end,
            )
        )
        if digest is None:
            digest = RoomDigest(
                organization_id=processor.organization_id,
                room_id=room.id,
                period_start=period_start,
                period_end=period_end,
            )
            db.add(digest)
        digest.message_count = len(messages)
        digest.comment_count = len(comments)
        digest.resident_count = len(resident_names)
        digest.summary = " · ".join(summary_parts)
        digest.major_points = major_points
        digest.document_counts = document_counts
        digest.risk_counts = risk_counts
        digest.source_message_ids = [str(message.id) for message in messages]
        digest.generator = "prototype-room-digest-v1"
        digest.generated_at = utcnow()
        digests.append(digest)
    if suggestions_changed or digests:
        db.commit()
    return [
        _room_digest_response(digest)
        for digest in sorted(
            digests,
            key=lambda item: (item.message_count, item.room.name),
            reverse=True,
        )
    ]


def _period_message_text(
    message: Message,
    comments: list[MessageComment],
) -> str:
    sections = [message.body.strip()]
    for attachment in message.attachments:
        extraction = attachment.text_extraction
        if extraction is None or extraction.status not in {"completed", "reviewed"}:
            continue
        extracted_text = extraction.reviewed_text or extraction.extracted_text
        if not extracted_text:
            continue
        label = (
            "음성 받아쓰기"
            if attachment.mime_type in AUDIO_MIME_TYPES
            else "이미지 글자 판독"
        )
        sections.append(f"[{label} · {attachment.original_name}]\n{extracted_text.strip()}")
    sections.extend(
        f"[답글 · {comment.author.full_name}] {comment.body.strip()}"
        for comment in comments
        if comment.body.strip()
    )
    return "\n".join(section for section in sections if section).strip()


BRIEFING_COMPARISON_DAYS = 3
BRIEFING_CATEGORY_LABELS = {
    "daily_care": "일상생활",
    "nutrition": "식사·영양",
    "health": "건강·간호",
    "safety": "이동·안전",
    "consultation": "보호자 상담",
    "rehabilitation": "재활·활동",
}
BRIEFING_ACTION_TERMS = (
    "도움드림",
    "도와드림",
    "제공",
    "안내",
    "전달",
    "연락",
    "말씀드",
    "말벗",
    "체위변경",
    "체위를 변경",
    "교체",
    "부축",
    "도포",
    "소독",
    "측정",
    "확인함",
    "완료",
)
BRIEFING_OBSERVATION_TERMS = (
    "비틀",
    "넘어",
    "낙상",
    "통증",
    "붉",
    "발적",
    "상처",
    "부종",
    "혈압",
    "혈당",
    "체온",
    "어지럼",
    "어지러",
    "어질",
    "구토",
    "기침",
    "배변",
    "식사",
    "거부",
    "불안",
    "배회",
    "수면",
    "피곤",
)
BRIEFING_PENDING_TERMS = (
    "확인 필요",
    "확인해",
    "불명확",
    "판독",
    "예정",
    "추후",
    "재측정",
    "관찰 중",
    "관찰중",
    "요청",
    "전달 후",
)
BRIEFING_RESOLVED_TERMS = (
    "확인 완료",
    "확인함",
    "재확인",
    "확인 시",
    "확인 결과",
    "안정됨",
    "안정을 찾",
    "안정 찾",
    "편안",
    "안내했습니다",
    "안내드렸",
    "어지럼 호소 없",
    "통증 호소 없",
    "정상",
    "완료함",
)
BRIEFING_SAFETY_TERMS = (
    "비틀",
    "넘어",
    "낙상",
    "주저앉",
    "균형을 잃",
    "미끄러",
    "어지럼",
    "어지러",
    "어질",
)
BRIEFING_SKIN_TERMS = (
    "피부",
    "붉",
    "발적",
    "상처",
    "부종",
    "욕창",
)
BRIEFING_VITAL_TERMS = (
    "혈압",
    "혈당",
    "체온",
    "맥박",
    "호흡",
)
BRIEFING_MEAL_TERMS = (
    "식사",
    "섭취",
    "식욕",
    "수분",
    "물",
)
BRIEFING_ACTIVITY_TERMS = (
    "활동 참여",
    "프로그램 거부",
    "피곤",
    "졸림",
    "졸고",
)


def _briefing_sentences(text: str) -> list[str]:
    """원문 표식을 걷어내고 브리핑에서 인용할 짧은 근거 문장만 만든다."""
    cleaned = re.sub(r"\[[^\]\r\n]{1,160}\]", " ", text)
    cleaned = re.sub(r"<[^>\r\n]{1,80}>", " ", cleaned)
    candidates = re.split(r"[\r\n]+|(?<=[.!?。])\s+", cleaned)
    sentences: list[str] = []
    for candidate in candidates:
        sentence = re.sub(r"\s+", " ", candidate).strip(" \t\r\n-·•:")
        if not sentence or len(sentence) < 3:
            continue
        if sentence.startswith(("audio-", "image-", "가명 음성보고", "가명 이미지")):
            continue
        if len(sentence) > 210:
            sentence = sentence[:207].rstrip() + "..."
        if sentence not in sentences:
            sentences.append(sentence)
    return sentences


def _briefing_observation(texts: list[str]) -> str:
    fallback = ""
    for text in reversed(texts):
        for sentence in _briefing_sentences(text):
            if not fallback:
                fallback = sentence
            if any(term in sentence for term in BRIEFING_OBSERVATION_TERMS):
                return sentence
    return fallback or "오늘 어르신 관련 새 보고가 등록되었습니다."


def _briefing_completed_actions(texts: list[str]) -> list[str]:
    actions: list[str] = []
    for text in texts:
        for sentence in _briefing_sentences(text):
            if not any(term in sentence for term in BRIEFING_ACTION_TERMS):
                continue
            if any(
                term in sentence
                for term in (
                    "예정",
                    "계획",
                    "하기로",
                    "기로 했",
                    "하겠습니다",
                    "해 주세요",
                    "해주세요",
                )
            ):
                continue
            if sentence not in actions:
                actions.append(sentence)
    return actions[:3]


def _briefing_meal_fractions(texts: list[str]) -> list[str]:
    """식사량 비교에 쓸 명시적 분수만 원문 순서대로 추출한다."""
    values: list[str] = []
    for text in texts:
        for sentence in _briefing_sentences(text):
            if not any(term in sentence for term in BRIEFING_MEAL_TERMS):
                continue
            sentence_values: list[str] = []
            for numerator, denominator in re.findall(r"(?<!\d)([0-9])\s*/\s*([0-9])(?!\d)", sentence):
                if denominator == "0":
                    continue
                value = f"{numerator}/{denominator}"
                values.append(value)
                sentence_values.append(value)
            if "절반" in sentence and "1/2" not in sentence_values:
                values.append("1/2")
    return values


def _briefing_has_resolution_after_signal(
    texts: list[str],
    signal_terms: tuple[str, ...],
) -> bool:
    signal_seen = False
    for text in texts:
        for sentence in _briefing_sentences(text):
            if not signal_seen and any(term in sentence for term in signal_terms):
                signal_seen = True
                continue
            if signal_seen and any(term in sentence for term in BRIEFING_RESOLVED_TERMS):
                return True
    return False


def _briefing_specific_followup(texts: list[str]) -> list[str]:
    joined = " ".join(texts)
    followups: list[str] = []
    if any(term in joined for term in ("불명확", "판독되지", "판독 확인")):
        if "약" in joined:
            followups.append(
                "약 이름 또는 복약 시간이 불명확합니다. "
                "입력자가 원본 음성·이미지를 확인한 뒤 확정해 주세요."
            )
        elif any(term in joined for term in BRIEFING_VITAL_TERMS):
            followups.append(
                "측정 수치가 불명확합니다. "
                "입력자 또는 간호팀이 원본에서 수치를 확인한 뒤 확정해 주세요."
            )
        elif any(term in joined for term in ("오른쪽", "왼쪽", "신체 부위", "좌우")):
            followups.append(
                "신체 부위의 좌우가 불명확합니다. "
                "입력자가 원본을 확인한 뒤 확정해 주세요."
            )
        else:
            followups.append(
                "판독이 불명확한 내용이 있습니다. "
                "입력자가 원본을 확인한 뒤 확정해 주세요."
            )
    if (
        any(term in joined for term in BRIEFING_SAFETY_TERMS)
        and any(term in joined for term in ("부축", "전달", "보고"))
        and not _briefing_has_resolution_after_signal(texts, BRIEFING_SAFETY_TERMS)
    ):
        followups.append(
            "간호팀 전달 이후 보행상태와 어지럼 여부가 기록되지 않았습니다. "
            "다음 이동 전 담당 요양보호사가 확인하고 결과를 남겨주세요."
        )
    if (
        any(term in joined for term in BRIEFING_SKIN_TERMS)
        and any(term in joined for term in ("도포", "소독", "체위", "전달", "관찰 중", "관찰중"))
        and not _briefing_has_resolution_after_signal(texts, BRIEFING_SKIN_TERMS)
    ):
        followups.append(
            "피부 상태 확인 이후의 변화가 기록되지 않았습니다. "
            "다음 돌봄 전에 담당 요양보호사 또는 간호팀이 같은 부위를 확인해 주세요."
        )
    if (
        any(term in joined for term in BRIEFING_VITAL_TERMS)
        and any(term in joined for term in ("재측정", "관찰 중", "관찰중", "전달"))
        and not _briefing_has_resolution_after_signal(texts, BRIEFING_VITAL_TERMS)
    ):
        followups.append(
            "측정값 전달 이후 재확인 결과가 기록되지 않았습니다. "
            "간호팀이 다음 측정값과 어르신 상태를 확인해 주세요."
        )
    if (
        any(term in joined for term in BRIEFING_MEAL_TERMS)
        and any(term in joined for term in ("확인하겠습니다", "확인 예정", "이어 확인", "추후 확인"))
        and not _briefing_has_resolution_after_signal(texts, BRIEFING_MEAL_TERMS)
    ):
        followups.append(
            "다음 식사·수분 섭취 결과가 아직 기록되지 않았습니다. "
            "담당 요양보호사가 다음 식사 후 섭취량을 남겨주세요."
        )
    return followups


def _briefing_pending_checks(
    texts: list[str],
    action_statuses: list[str],
) -> list[str]:
    pending = _briefing_specific_followup(texts)
    for text in texts:
        for sentence in _briefing_sentences(text):
            if not any(term in sentence for term in BRIEFING_PENDING_TERMS):
                continue
            if any(term in sentence for term in BRIEFING_RESOLVED_TERMS):
                continue
            if (
                any(
                    term in sentence
                    for term in (
                        "요청했습니다",
                        "요청드렸습니다",
                        "요청함",
                        "요청하였습니다",
                    )
                )
                and not any(
                    term in sentence
                    for term in (
                        "해 달라고",
                        "해달라고",
                        "달라고 요청",
                    )
                )
            ):
                # 이미 전달·요청한 사실은 완료 조치에 남긴다. 피부·안전 등
                # 후속 확인은 위의 구체적인 안내문으로 한 번만 제시한다.
                continue
            if (
                any(term in sentence for term in ("불명확", "판독"))
                and any("원본" in followup for followup in pending)
            ):
                continue
            if any(sentence in followup or followup in sentence for followup in pending):
                continue
            if sentence not in pending:
                pending.append(sentence)
    if any(status not in {"completed"} for status in action_statuses):
        pending.append(
            "담당자가 지정된 업무가 아직 완료되지 않았습니다. "
            "담당자가 확인 결과를 원문 댓글로 남겨주세요."
        )
    return list(dict.fromkeys(pending))[:3]


def _briefing_risk_reason(texts: list[str], risk_level: str) -> str | None:
    joined = " ".join(texts)
    if risk_level not in {"medium", "high", "urgent"}:
        return None
    safety_labels = (
        ("비틀", "비틀거림"),
        ("넘어", "넘어짐"),
        ("낙상", "낙상"),
        ("주저앉", "주저앉음"),
        ("균형을 잃", "균형을 잃음"),
        ("미끄러", "미끄러짐"),
        ("어지럼", "어지럼"),
        ("어지러", "어지럼"),
        ("어질", "어지럼"),
    )
    for term, label in safety_labels:
        if term in joined:
            return (
                f"이동 중 {label} 관련 원문이 있어 "
                "다음 이동 전 상태 확인이 필요합니다."
            )
    skin_labels = (
        ("발적", "발적"),
        ("붉", "붉게 보임"),
        ("상처", "상처"),
        ("부종", "부종"),
        ("욕창", "욕창"),
        ("피부", "피부 변화"),
    )
    for term, label in skin_labels:
        if term in joined:
            return (
                f"{label} 관련 관찰이 기록되어 "
                "같은 부위의 경과 확인이 필요합니다."
            )
    vital_labels = (
        ("혈압", "혈압"),
        ("혈당", "혈당"),
        ("체온", "체온"),
        ("맥박", "맥박"),
        ("호흡", "호흡"),
    )
    for term, label in vital_labels:
        if term in joined:
            return (
                f"{label} 관련 관찰이 있어 "
                "후속 확인 결과가 필요합니다."
            )
    if any(term in joined for term in BRIEFING_MEAL_TERMS):
        return "식사·수분 섭취 변화가 기록되어 다음 섭취 결과와 비교가 필요합니다."
    if "통증" in joined:
        return "통증 호소가 기록되어 이후 반응과 상태 확인이 필요합니다."
    return "평소와 다른 상태를 나타내는 원문 표현이 있어 경과 확인이 필요합니다."


def _briefing_daily_document_types(
    suggestion: RecordDraft,
    text: str,
) -> list[str]:
    """일일 기록 근거가 실제로 있는 서류만 보수적으로 제안한다."""
    if any(
        term in text
        for term in (
            "불명확",
            "판독되지",
            "판독 확인",
            "확인이 필요",
            "인지 확인",
            "인지 불가",
        )
    ) or re.search(r"\d\?", text):
        return []
    classification = suggestion.classification
    risk_level = suggestion.risk_level
    if classification == "safety":
        document_types = ["nursing_log", "care_service_record"]
    elif classification == "health":
        document_types = ["nursing_log"]
        if any(term in text for term in BRIEFING_ACTION_TERMS):
            document_types.append("care_service_record")
    elif classification == "nutrition":
        document_types = ["care_service_record"]
        if risk_level in {"medium", "high", "urgent"}:
            document_types.append("nursing_log")
    elif classification == "consultation":
        document_types = ["consultation_log"]
    elif classification == "rehabilitation":
        document_types = ["care_service_record"]
    else:
        document_types = ["care_service_record"]

    if any(term in text for term in ("보호자", "상담", "전화", "통화", "면담")):
        document_types.append("consultation_log")
    if any(
        term in text
        for term in ("신체제재", "신체 제재", "억제대", "안전벨트", "휠체어 벨트")
    ):
        document_types.append("physical_restraint_log")
    if any(
        term in text
        for term in ("프로그램", "참여", "활동", "체조", "노래", "미술", "레크리에이션")
    ):
        document_types.append("program_log")
    return [
        document_type
        for document_type in dict.fromkeys(document_types)
        if document_type in DAILY_DOCUMENT_TYPES
    ]


RECORD_USAGE_LABELS: dict[str, str] = {
    "nursing": "간호 기록",
    "care_service": "급여제공 기록",
    "consultation": "상담 기록",
    "program": "프로그램 기록",
    "general": "일반 업무",
    "needs_review": "확인 필요",
}
RECORD_UNCERTAIN_TERMS = (
    "불명확",
    "판독되지",
    "판독 확인",
    "확인이 필요",
    "확인 필요",
    "인지 불가",
    "알 수 없음",
)
RECORD_NURSING_TERMS = (
    "혈압",
    "혈당",
    "체온",
    "맥박",
    "호흡",
    "통증",
    "발적",
    "상처",
    "부종",
    "피부",
    "복약",
    "투약",
    "약 ",
    "낙상",
    "넘어",
    "비틀",
    "주저앉",
    "어지럼",
    "어지러",
    "어질",
    "배변",
    "소변",
    "간호",
)
RECORD_CARE_TERMS = (
    "식사",
    "수분",
    "물 ",
    "목욕",
    "세면",
    "위생",
    "기저귀",
    "화장실",
    "배변",
    "부축",
    "이동",
    "보행",
    "체위",
    "정서",
    "안정",
    "불안",
    "귀가",
    "집에",
    "엄마",
    "아버지",
    "보호자 찾",
    "도움",
    "지원",
)
RECORD_CARE_ACTION_TERMS = (
    "도와",
    "도움",
    "제공",
    "드림",
    "드렸",
    "부축",
    "안내",
    "확인",
    "교체",
    "변경",
    "도포",
    "전달",
    "말씀드",
    "안정",
    "관찰",
)
RECORD_CONTACT_PARTY_TERMS = (
    "보호자",
    "가족",
    "아드님",
    "따님",
    "아들",
    "딸",
)
RECORD_CONTACT_ACTION_TERMS = (
    "통화",
    "전화드",
    "전화함",
    "연락드",
    "연락함",
    "면담",
    "상담함",
    "상담 진행",
    "설명드",
    "알려드",
)
RECORD_PROGRAM_TERMS = (
    "프로그램",
    "체조",
    "노래",
    "미술",
    "레크리에이션",
    "독서",
    "산책",
    "활동",
)
RECORD_PROGRAM_ACTION_TERMS = (
    "참여",
    "거부",
    "진행",
    "수행",
    "따라",
    "반응",
    "웃",
    "즐거",
)
RECORD_EVENT_SIGNAL_TERMS = (
    "낙상",
    "넘어",
    "비틀",
    "통증",
    "발적",
    "상처",
    "혈압",
    "혈당",
    "체온",
    "복약",
    "식사",
    "수분",
    "배변",
    "화장실",
    "보호자",
    "통화",
    "프로그램",
    "체조",
    "산책",
    "귀가",
    "불안",
)


def _record_usage_tags(
    text: str,
    *,
    has_resident: bool,
    suggestion: RecordDraft,
) -> list[RecordUsageTag]:
    """서류를 자동 확정하지 않고, 원문 근거가 있는 사용처만 표시한다."""
    normalized = re.sub(r"\s+", " ", text).strip()
    tags: list[RecordUsageTag] = []
    uncertain = any(term in normalized for term in RECORD_UNCERTAIN_TERMS) or bool(
        re.search(r"(?:\d|\w)\?", normalized)
    )
    # 기록 종류는 반드시 특정 어르신과 연결된 근거에만 붙인다.
    # 어르신이 연결되지 않은 공지·시설 업무가 건강 용어를 포함하더라도
    # 간호·급여제공 기록 후보로 섞이지 않게 한다.
    if not has_resident:
        return ["general", *(["needs_review"] if uncertain else [])]
    actual_contact = (
        any(term in normalized for term in RECORD_CONTACT_PARTY_TERMS)
        and any(term in normalized for term in RECORD_CONTACT_ACTION_TERMS)
    )
    actual_program = (
        any(term in normalized for term in RECORD_PROGRAM_TERMS)
        and any(term in normalized for term in RECORD_PROGRAM_ACTION_TERMS)
    )
    nursing = (
        any(term in normalized for term in RECORD_NURSING_TERMS)
        or (
            has_resident
            and suggestion.classification in {"health", "safety"}
        )
    )
    care = (
        any(term in normalized for term in RECORD_CARE_TERMS)
        and (
            any(term in normalized for term in RECORD_CARE_ACTION_TERMS)
            or any(term in normalized for term in ("귀가", "집에", "엄마", "불안"))
        )
    ) or (
        has_resident
        and not actual_program
        and any(term in normalized for term in RECORD_CARE_TERMS)
        and suggestion.classification
        in {
            "daily_care",
            "nutrition",
            "rehabilitation",
        }
    )

    if nursing:
        tags.append("nursing")
    if care:
        tags.append("care_service")
    if actual_contact:
        tags.append("consultation")
    if actual_program:
        tags.append("program")
    if uncertain:
        tags.append("needs_review")
    if not tags:
        tags.append("general")
    return list(dict.fromkeys(tags))


def _record_event_tokens(text: str) -> set[str]:
    ignored = {
        "어르신",
        "시설",
        "가명",
        "오늘",
        "오전",
        "오후",
        "확인",
        "말씀",
        "보고",
        "관련",
    }
    return {
        token
        for token in re.findall(r"[가-힣A-Za-z0-9]+", text.casefold())
        if len(token) >= 2 and token not in ignored
    }


def _same_record_event(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["resident_id"] != right["resident_id"]:
        return False
    if _as_utc(left["created_at"]).astimezone(timezone(timedelta(hours=9))).date() != (
        _as_utc(right["created_at"]).astimezone(timezone(timedelta(hours=9))).date()
    ):
        return False
    if abs(
        (_as_utc(left["created_at"]) - _as_utc(right["created_at"])).total_seconds()
    ) > 7200:
        return False
    if not set(left["record_usage_tags"]) & set(right["record_usage_tags"]):
        return False
    left_signals = {
        term for term in RECORD_EVENT_SIGNAL_TERMS if term in left["text"]
    }
    right_signals = {
        term for term in RECORD_EVENT_SIGNAL_TERMS if term in right["text"]
    }
    if not left_signals or left_signals != right_signals:
        return False
    left_tokens = _record_event_tokens(left["text"])
    right_tokens = _record_event_tokens(right["text"])
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.45


def _group_record_events(
    candidates: list[dict[str, Any]],
) -> list[PeriodRecordEvent]:
    grouped: list[dict[str, Any]] = []
    for candidate in sorted(candidates, key=lambda value: value["created_at"]):
        target = next(
            (
                group
                for group in reversed(grouped)
                if _same_record_event(group["last_candidate"], candidate)
            ),
            None,
        )
        if target is None:
            grouped.append(
                {
                    "resident_id": candidate["resident_id"],
                    "resident_name": candidate["resident_name"],
                    "summary": candidate["summary"],
                    "record_usage_tags": list(candidate["record_usage_tags"]),
                    "evidence_ids": [candidate["message_id"]],
                    "room_names": [candidate["room_name"]],
                    "sender_names": [candidate["sender_name"]],
                    "latest_at": candidate["created_at"],
                    "last_candidate": candidate,
                }
            )
            continue
        target["record_usage_tags"] = list(
            dict.fromkeys(
                [*target["record_usage_tags"], *candidate["record_usage_tags"]]
            )
        )
        target["evidence_ids"].append(candidate["message_id"])
        target["room_names"].append(candidate["room_name"])
        target["sender_names"].append(candidate["sender_name"])
        target["latest_at"] = candidate["created_at"]
        target["last_candidate"] = candidate

    events: list[PeriodRecordEvent] = []
    for group in grouped:
        evidence_ids = list(dict.fromkeys(group["evidence_ids"]))
        events.append(
            PeriodRecordEvent(
                event_group_id=(
                    f"event-{evidence_ids[0].hex[:12]}-"
                    f"{group['resident_id'].hex[:12] if group['resident_id'] else 'general'}"
                ),
                resident_id=group["resident_id"],
                resident_name=group["resident_name"],
                summary=group["summary"],
                record_usage_tags=group["record_usage_tags"],
                evidence_ids=evidence_ids,
                room_names=list(dict.fromkeys(group["room_names"])),
                sender_names=list(dict.fromkeys(group["sender_names"])),
                latest_at=group["latest_at"],
            )
        )
    return sorted(events, key=lambda event: event.latest_at, reverse=True)


def _period_resident_name_aliases(display_name: str) -> set[str]:
    compact = re.sub(r"\s+", "", display_name)
    without_marker = compact.replace("(가명)", "")
    aliases = {display_name.strip(), compact, without_marker}
    numbered = re.fullmatch(r"(.+?)(\d{3})", without_marker)
    if numbered:
        prefix, number = numbered.groups()
        aliases.update(
            {
                f"{prefix} {number}",
                f"{prefix}(가명){number}",
                f"{prefix}(가명) {number}",
            }
        )
    return {alias for alias in aliases if alias}


def _resident_specific_period_text(
    text: str,
    *,
    target_name: str,
    resident_names: list[str],
) -> str:
    """여러 어르신이 함께 적힌 보고에서 해당 어르신 구간만 안전하게 분리한다."""
    if len(resident_names) <= 1:
        return text.strip()

    alias_owner: dict[str, str] = {}
    for resident_name in resident_names:
        for alias in _period_resident_name_aliases(resident_name):
            alias_owner.setdefault(alias.casefold(), resident_name)
    if not alias_owner:
        return ""

    alias_pattern = re.compile(
        "|".join(
            re.escape(alias)
            for alias in sorted(alias_owner, key=len, reverse=True)
        ),
        re.IGNORECASE,
    )
    segments: list[str] = []
    # 답글은 원문 뒤에 붙으므로, 어르신 이름이 없는 답글을 그대로 나누면
    # 마지막 어르신의 구간으로 잘못 들어간다. 답글 단위로 먼저 분리한 뒤
    # 어르신을 명시한 답글만 해당 어르신 근거에 포함한다.
    sections = re.split(
        r"(?=^\[답글(?:\s*·[^\]\r\n]+)?\])",
        text,
        flags=re.MULTILINE,
    )
    for section_index, section_text in enumerate(sections):
        matches = list(alias_pattern.finditer(section_text))
        if not matches:
            # 본문에 이름이 전혀 없으면 안전하게 분리할 수 없고, 이름 없는
            # 답글은 어느 어르신에게도 자동 귀속하지 않는다.
            continue
        comment_header = ""
        if section_index > 0:
            header_match = re.match(
                r"\[답글(?:\s*·[^\]\r\n]+)?\]",
                section_text,
            )
            if header_match is not None:
                comment_header = header_match.group(0)
        for index, match in enumerate(matches):
            if alias_owner.get(match.group(0).casefold()) != target_name:
                continue
            end = (
                matches[index + 1].start()
                if index + 1 < len(matches)
                else len(section_text)
            )
            segment = section_text[match.start() : end].strip(" \t\r\n-·•")
            if comment_header and segment:
                segment = f"{comment_header} {segment}"
            if segment and segment not in segments:
                segments.append(segment)
    return "\n".join(segments).strip()


@app.post(
    "/api/workdesk/period-review",
    response_model=PeriodWorkdeskResponse,
)
def create_period_workdesk_review(
    payload: PeriodWorkdeskRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    kst = timezone(timedelta(hours=9))
    period_start = datetime.combine(
        payload.start_date,
        time.min,
        tzinfo=kst,
    ).astimezone(timezone.utc)
    period_end = datetime.combine(
        payload.end_date + timedelta(days=1),
        time.min,
        tzinfo=kst,
    ).astimezone(timezone.utc)

    room_query = select(Room).where(
        Room.organization_id == processor.organization_id,
        Room.is_active.is_(True),
    )
    if processor.role != "admin":
        if processor.staff_id is None:
            return PeriodWorkdeskResponse(
                period_start=period_start,
                period_end=period_end,
                summary="확인할 수 있는 채팅방이 없습니다.",
                generator="empty",
                message_count=0,
                comment_count=0,
                resident_count=0,
                category_counts={},
                document_counts={},
                sources=[],
                document_drafts=[],
            )
        room_query = (
            room_query.join(RoomMembership, RoomMembership.room_id == Room.id)
            .where(
                RoomMembership.staff_id == processor.staff_id,
                RoomMembership.left_at.is_(None),
            )
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(
            Room.name == settings.reviewer_chat_room_name
        )
    rooms = list(db.scalars(room_query.order_by(Room.name)).unique().all())
    room_by_id = {room.id: room for room in rooms}
    if payload.room_id is not None:
        if payload.room_id not in room_by_id:
            raise HTTPException(status_code=403, detail="이 채팅방을 정리할 수 없습니다.")
        room_by_id = {payload.room_id: room_by_id[payload.room_id]}

    if payload.resident_id is not None:
        resident = db.get(Resident, payload.resident_id)
        if (
            resident is None
            or resident.organization_id != processor.organization_id
            or not resident.is_active
        ):
            raise HTTPException(status_code=422, detail="선택한 어르신을 찾을 수 없습니다.")

    if not room_by_id:
        messages: list[Message] = []
        truncated = False
    else:
        message_query = select(Message).where(
            Message.room_id.in_(list(room_by_id)),
            Message.created_at >= period_start,
            Message.created_at < period_end,
        )
        if getattr(processor, "_reviewer_experience", None) is not None:
            message_query = message_query.where(Message.is_test_data.is_(True))
        if payload.message_type is not None:
            message_query = message_query.where(
                Message.message_type == payload.message_type
            )
        message_query = message_query.order_by(
            Message.created_at,
            Message.id,
        ).limit(501)
        message_rows = list(db.scalars(message_query).unique().all())
        truncated = len(message_rows) > 500
        messages = message_rows[:500]

    if not messages:
        return PeriodWorkdeskResponse(
            period_start=period_start,
            period_end=period_end,
            summary="선택한 기간에 정리할 업무대화가 없습니다.",
            generator="empty",
            message_count=0,
            comment_count=0,
            resident_count=0,
            category_counts={},
            document_counts={},
            sources=[],
            document_drafts=[],
            truncated=truncated,
        )

    message_ids = [message.id for message in messages]
    comments = list(
        db.scalars(
            select(MessageComment)
            .where(MessageComment.message_id.in_(message_ids))
            .order_by(MessageComment.created_at, MessageComment.id)
        ).all()
    )
    comments_by_message: dict[UUID, list[MessageComment]] = {}
    for comment in comments:
        comments_by_message.setdefault(comment.message_id, []).append(comment)

    read_counts = {
        message_id: int(count)
        for message_id, count in db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id),
            )
            .where(MessageReadReceipt.message_id.in_(message_ids))
            .group_by(MessageReadReceipt.message_id)
        ).all()
    }
    confirmed_links = list(
        db.scalars(
            select(MessageResidentLink)
            .where(
                MessageResidentLink.message_id.in_(message_ids),
                MessageResidentLink.status == "confirmed",
            )
            .order_by(
                MessageResidentLink.message_id,
                MessageResidentLink.created_at,
                MessageResidentLink.id,
            )
        ).all()
    )
    residents_by_message: dict[UUID, list[Resident]] = {}
    for link in confirmed_links:
        residents_by_message.setdefault(link.message_id, []).append(link.resident)
    for message in messages:
        linked = residents_by_message.setdefault(message.id, [])
        if message.resident is not None and all(
            resident.id != message.resident.id for resident in linked
        ):
            linked.insert(0, message.resident)

    if payload.resident_id is not None:
        messages = [
            message
            for message in messages
            if any(
                resident.id == payload.resident_id
                for resident in residents_by_message.get(message.id, [])
            )
        ]
        message_ids = [message.id for message in messages]
        message_id_set = set(message_ids)
        comments = [
            comment for comment in comments if comment.message_id in message_id_set
        ]
        if not messages:
            return PeriodWorkdeskResponse(
                period_start=period_start,
                period_end=period_end,
                summary="선택한 기간에 이 어르신과 연결된 업무대화가 없습니다.",
                generator="empty",
                message_count=0,
                comment_count=0,
                resident_count=0,
                category_counts={},
                document_counts={},
                sources=[],
                document_drafts=[],
                truncated=truncated,
            )

    if payload.keyword:
        normalized_keyword = payload.keyword.casefold()
        messages = [
            message
            for message in messages
            if normalized_keyword
            in " ".join(
                [
                    _period_message_text(
                        message,
                        comments_by_message.get(message.id, []),
                    ),
                    message.sender.full_name,
                    room_by_id[message.room_id].name,
                    *[
                        resident.display_name
                        for resident in residents_by_message.get(message.id, [])
                    ],
                ]
            ).casefold()
        ]
        message_ids = [message.id for message in messages]
        message_id_set = set(message_ids)
        comments = [
            comment for comment in comments if comment.message_id in message_id_set
        ]
        if not messages:
            return PeriodWorkdeskResponse(
                period_start=period_start,
                period_end=period_end,
                summary="선택한 기간에 검색어와 맞는 업무대화가 없습니다.",
                generator="empty",
                message_count=0,
                comment_count=0,
                resident_count=0,
                category_counts={},
                document_counts={},
                sources=[],
                document_drafts=[],
                truncated=truncated,
            )

    work_items = {
        item.source_message_id: item
        for item in db.scalars(
            select(WorkItem).where(WorkItem.source_message_id.in_(message_ids))
        ).all()
    }
    category_counts: dict[str, int] = {}
    document_counts: dict[str, int] = {}
    source_models: list[PeriodWorkdeskSource] = []
    ai_entries: list[dict[str, Any]] = []
    resident_evidence: dict[
        UUID,
        dict[str, Any],
    ] = {}
    record_event_candidates: list[dict[str, Any]] = []

    for index, message in enumerate(messages, 1):
        message_comments = comments_by_message.get(message.id, [])
        text = _period_message_text(message, message_comments)
        linked_residents = residents_by_message.get(message.id, [])
        item = work_items.get(message.id)
        raw_suggestion = (
            (item.confirmed_payload or item.ai_payload)
            if item is not None
            else None
        )
        if not raw_suggestion:
            raw_suggestion = build_prototype_suggestion(
                {
                    "body": text,
                    "resident_name": (
                        linked_residents[0].display_name if linked_residents else None
                    ),
                    "resident_names": [
                        resident.display_name for resident in linked_residents
                    ],
                }
            )
        suggestion = RecordDraft.model_validate(raw_suggestion)
        category_counts[suggestion.classification] = (
            category_counts.get(suggestion.classification, 0) + 1
        )
        daily_document_types = _briefing_daily_document_types(suggestion, text)
        for document_type in daily_document_types:
            document_counts[document_type] = document_counts.get(document_type, 0) + 1

        for resident in linked_residents:
            resident_text = _resident_specific_period_text(
                text,
                target_name=resident.display_name,
                resident_names=[
                    linked_resident.display_name
                    for linked_resident in linked_residents
                ],
            )
            if not resident_text:
                continue
            resident_suggestion = RecordDraft.model_validate(
                build_prototype_suggestion(
                    {
                        "body": resident_text,
                        "resident_name": resident.display_name,
                        "resident_names": [resident.display_name],
                    }
                )
            )
            resident_document_types = _briefing_daily_document_types(
                resident_suggestion,
                resident_text,
            )
            resident_usage_tags = _record_usage_tags(
                resident_text,
                has_resident=True,
                suggestion=resident_suggestion,
            )
            record_event_candidates.append(
                {
                    "message_id": message.id,
                    "resident_id": resident.id,
                    "resident_name": resident.display_name,
                    "text": resident_text,
                    "summary": _briefing_observation([resident_text]),
                    "record_usage_tags": resident_usage_tags,
                    "room_name": room_by_id[message.room_id].name,
                    "sender_name": message.sender.full_name,
                    "created_at": _as_utc(message.created_at),
                }
            )
            evidence = resident_evidence.setdefault(
                resident.id,
                {
                    "resident": resident,
                    "texts": [],
                    "plain_texts": [],
                    "message_ids": [],
                    "classifications": [],
                    "risk_levels": [],
                    "document_types": [],
                    "suggestions": [],
                    "action_statuses": [],
                    "created_ats": [],
                },
            )
            evidence["texts"].append(
                f"[{_as_utc(message.created_at).astimezone(kst).strftime('%m/%d %H:%M')} · "
                f"{room_by_id[message.room_id].name}]\n{resident_text}"
            )
            evidence["plain_texts"].append(resident_text)
            evidence["message_ids"].append(message.id)
            evidence["classifications"].append(
                resident_suggestion.classification
            )
            evidence["risk_levels"].append(resident_suggestion.risk_level)
            evidence["document_types"].extend(resident_document_types)
            evidence["suggestions"].append(resident_suggestion)
            evidence["created_ats"].append(_as_utc(message.created_at))
            if message.action_item is not None:
                evidence["action_statuses"].append(message.action_item.status)

        if not linked_residents:
            general_tags = _record_usage_tags(
                text,
                has_resident=False,
                suggestion=suggestion,
            )
            record_event_candidates.append(
                {
                    "message_id": message.id,
                    "resident_id": None,
                    "resident_name": None,
                    "text": text,
                    "summary": _briefing_observation([text]),
                    "record_usage_tags": general_tags,
                    "room_name": room_by_id[message.room_id].name,
                    "sender_name": message.sender.full_name,
                    "created_at": _as_utc(message.created_at),
                }
            )

        source_models.append(
            PeriodWorkdeskSource(
                message=message_response(message, db=db, viewer_id=processor.id),
                room_name=room_by_id[message.room_id].name,
                resident_names=[
                    resident.display_name for resident in linked_residents
                ],
                read_count=read_counts.get(message.id, 0),
                reply_count=len(message_comments),
                reply_user_count=len(
                    {comment.author_id for comment in message_comments}
                ),
            )
        )
        if index <= 120:
            ai_entries.append(
                {
                    "number": index,
                    "room": room_by_id[message.room_id].name,
                    "sender": message.sender.full_name,
                    "resident": ", ".join(
                        resident.display_name for resident in linked_residents
                    ),
                    "type": message.message_type,
                    "body": text,
                }
            )

    summary = "[주요 내용]\n" + "\n".join(
        f"- [{index}] {source.room_name} · "
        f"{', '.join(source.resident_names) or '일반'} · "
        f"{source.message.body}"
        for index, source in enumerate(source_models[:12], 1)
    )
    generator = "quick-period-summary-v1"
    if payload.enhance_summary:
        try:
            ai_summary = summarize_room_messages(
                entries=ai_entries,
                external_allowed=all(message.is_test_data for message in messages),
            )
            summary = ai_summary.summary
            generator = f"{ai_summary.provider}:{ai_summary.model}"
        except LocalAiError:
            generator = "safe-period-summary-fallback-v1"

    record_events = _group_record_events(record_event_candidates)
    record_group_counts = {
        tag: sum(tag in event.record_usage_tags for event in record_events)
        for tag in RECORD_USAGE_LABELS
    }

    risk_order = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
    document_drafts: list[PeriodDocumentDraft] = []
    for resident_id, evidence in resident_evidence.items():
        resident = evidence["resident"]
        classifications = evidence["classifications"]
        classification = max(
            set(classifications),
            key=lambda value: (classifications.count(value), value),
        )
        risk_level = max(
            evidence["risk_levels"],
            key=lambda value: risk_order.get(value, 0),
        )
        combined_text = "\n\n".join(evidence["texts"])
        for document_type in dict.fromkeys(evidence["document_types"]):
            proposal = build_document_proposal(
                document_type,
                resident_label=resident.display_name,
                text=combined_text,
                classification=classification,
                risk_level=risk_level,
            )
            document_drafts.append(
                PeriodDocumentDraft(
                    key=f"{resident_id}:{document_type}",
                    resident_id=resident_id,
                    resident_name=resident.display_name,
                    document_type=document_type,
                    content=str(proposal["content"]),
                    verification_questions=list(
                        proposal.get("verification_questions", [])
                    ),
                    source_message_ids=list(dict.fromkeys(evidence["message_ids"])),
                )
            )

    baseline_start = period_start - timedelta(days=BRIEFING_COMPARISON_DAYS)
    baseline_query = (
        select(Message)
        .where(
            Message.room_id.in_(list(room_by_id)),
            Message.created_at >= baseline_start,
            Message.created_at < period_start,
        )
        .order_by(Message.created_at, Message.id)
        .limit(501)
    )
    if payload.message_type is not None:
        baseline_query = baseline_query.where(
            Message.message_type == payload.message_type
        )
    baseline_messages = list(db.scalars(baseline_query).unique().all())
    baseline_evidence: dict[UUID, dict[str, Any]] = {}
    current_resident_ids = set(resident_evidence)
    for baseline_message in baseline_messages[:500]:
        baseline_residents = [
            link.resident
            for link in baseline_message.resident_links
            if link.status == "confirmed" and link.resident_id in current_resident_ids
        ]
        if (
            baseline_message.resident is not None
            and baseline_message.resident.id in current_resident_ids
            and all(
                resident.id != baseline_message.resident.id
                for resident in baseline_residents
            )
        ):
            baseline_residents.insert(0, baseline_message.resident)
        if not baseline_residents:
            continue
        baseline_text = _period_message_text(
            baseline_message,
            list(baseline_message.comments),
        )
        for resident in baseline_residents:
            resident_text = _resident_specific_period_text(
                baseline_text,
                target_name=resident.display_name,
                resident_names=[
                    baseline_resident.display_name
                    for baseline_resident in baseline_residents
                ],
            )
            if not resident_text:
                continue
            suggestion = RecordDraft.model_validate(
                build_prototype_suggestion(
                    {
                        "body": resident_text,
                        "resident_name": resident.display_name,
                        "resident_names": [resident.display_name],
                    }
                )
            )
            baseline = baseline_evidence.setdefault(
                resident.id,
                {"count": 0, "classifications": set(), "plain_texts": []},
            )
            baseline["count"] += 1
            baseline["classifications"].add(suggestion.classification)
            baseline["plain_texts"].append(resident_text)

    events_by_resident: dict[UUID, list[PeriodRecordEvent]] = {}
    for record_event in record_events:
        if record_event.resident_id is not None:
            events_by_resident.setdefault(record_event.resident_id, []).append(
                record_event
            )
    briefing_cards: list[CareBriefingCard] = []
    risk_order = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
    for resident_id, evidence in resident_evidence.items():
        current_classifications = set(evidence["classifications"])
        baseline = baseline_evidence.get(
            resident_id,
            {"count": 0, "classifications": set(), "plain_texts": []},
        )
        baseline_classifications = set(baseline["classifications"])
        new_classifications = current_classifications - baseline_classifications
        current_labels = [
            BRIEFING_CATEGORY_LABELS.get(value, value)
            for value in sorted(current_classifications)
        ]
        new_labels = [
            BRIEFING_CATEGORY_LABELS.get(value, value)
            for value in sorted(new_classifications)
        ]
        observation = _briefing_observation(evidence["plain_texts"])
        baseline_meal_fractions = _briefing_meal_fractions(
            baseline.get("plain_texts", [])
        )
        current_meal_fractions = _briefing_meal_fractions(
            evidence["plain_texts"]
        )
        meal_fraction_sequence = (
            baseline_meal_fractions + current_meal_fractions
        )
        if len(meal_fraction_sequence) >= 3 and len(set(meal_fraction_sequence)) > 1:
            change_summary = (
                f"최근 기록의 식사량이 {' → '.join(meal_fraction_sequence[-4:])}로 "
                f"이어졌습니다. {observation}"
            )
        elif baseline["count"] == 0:
            change_summary = (
                f"비교할 최근 {BRIEFING_COMPARISON_DAYS}일 기록이 없어 "
                f"오늘 관찰을 첫 기준으로 표시합니다. {observation}"
            )
        elif new_labels:
            change_summary = (
                f"최근 {BRIEFING_COMPARISON_DAYS}일 기록에는 없던 "
                f"{'·'.join(new_labels)} 관련 보고가 오늘 새로 있습니다. "
                f"{observation}"
            )
        else:
            change_summary = (
                f"최근 {BRIEFING_COMPARISON_DAYS}일에도 "
                f"{'·'.join(current_labels) or '같은 주제'} 관련 기록이 있었고 "
                f"오늘 다시 보고되었습니다. {observation}"
            )

        risk_level = max(
            evidence["risk_levels"],
            key=lambda value: risk_order.get(value, 0),
        )
        completed_actions = _briefing_completed_actions(evidence["plain_texts"])
        pending_checks = _briefing_pending_checks(
            evidence["plain_texts"],
            evidence["action_statuses"],
        )
        if len(set(current_meal_fractions)) > 1:
            compared = "·".join(dict.fromkeys(current_meal_fractions))
            pending_checks.insert(
                0,
                f"같은 기간 식사량이 {compared}로 다르게 기록되었습니다. "
                "작성자가 실제 섭취량을 확인해 주세요.",
            )
            pending_checks = list(dict.fromkeys(pending_checks))[:3]
        reasons: list[str] = []
        risk_reason = _briefing_risk_reason(
            evidence["plain_texts"],
            risk_level,
        )
        if risk_reason:
            reasons.append(risk_reason)
        if (
            len(meal_fraction_sequence) >= 3
            and len(set(meal_fraction_sequence)) > 1
            and any(
                term in " ".join(evidence["plain_texts"])
                for term in BRIEFING_ACTIVITY_TERMS
            )
        ):
            reasons.append(
                "식사량 변화와 활동 저하·졸림 관련 관찰이 함께 기록되었습니다."
            )
            pending_checks.insert(
                0,
                "식사량 감소와 활동 저하·졸림이 함께 기록되었습니다. "
                "다음 식사 후 담당 요양보호사가 섭취량과 활동 상태를 확인해 주세요.",
            )
            pending_checks = list(dict.fromkeys(pending_checks))[:3]
        if new_labels and baseline["count"] > 0:
            reasons.append(
                f"최근 기록과 다른 {'·'.join(new_labels)} 주제가 새로 나타났습니다."
            )
        if pending_checks:
            reasons.append("조치 또는 확인 결과가 아직 기록되지 않은 항목이 있습니다.")
        if not reasons:
            reasons.append("오늘 새 관찰이 기록되어 경과 확인에 사용할 수 있습니다.")

        if risk_level in {"high", "urgent"}:
            priority = "first"
        elif risk_level == "medium" or pending_checks:
            priority = "check"
        else:
            priority = "observe"
        document_types = [
            document_type
            for document_type in dict.fromkeys(evidence["document_types"])
            if document_type in DAILY_DOCUMENT_TYPES
        ]
        briefing_cards.append(
            CareBriefingCard(
                event_group_id=(
                    events_by_resident.get(resident_id, [])[0].event_group_id
                    if events_by_resident.get(resident_id)
                    else None
                ),
                resident_id=resident_id,
                resident_name=evidence["resident"].display_name,
                priority=priority,
                change_summary=change_summary,
                check_reasons=reasons[:3],
                completed_actions=completed_actions,
                pending_checks=pending_checks,
                document_types=document_types,
                record_usage_tags=list(
                    dict.fromkeys(
                        tag
                        for event in events_by_resident.get(resident_id, [])
                        for tag in event.record_usage_tags
                    )
                ),
                source_message_ids=list(dict.fromkeys(evidence["message_ids"])),
                current_message_count=len(
                    set(evidence["message_ids"])
                ),
                baseline_message_count=int(baseline["count"]),
                latest_at=max(evidence["created_ats"]),
            )
        )
    priority_order = {"first": 0, "check": 1, "observe": 2}
    briefing_cards.sort(key=lambda card: card.latest_at, reverse=True)
    briefing_cards.sort(key=lambda card: priority_order[card.priority])
    briefing = CareBriefingSummary(
        comparison_days=BRIEFING_COMPARISON_DAYS,
        needs_attention_count=sum(
            card.priority in {"first", "check"} for card in briefing_cards
        ),
        pending_check_count=sum(
            len(card.pending_checks) for card in briefing_cards
        ),
        document_candidate_count=sum(
            len(card.document_types) for card in briefing_cards
        ),
        cards=briefing_cards,
    )

    return PeriodWorkdeskResponse(
        period_start=period_start,
        period_end=period_end,
        summary=summary,
        generator=generator,
        message_count=len(messages),
        comment_count=len(comments),
        resident_count=len(resident_evidence),
        category_counts=category_counts,
        document_counts=document_counts,
        sources=source_models,
        document_drafts=document_drafts,
        record_events=record_events,
        record_group_counts=record_group_counts,
        briefing=briefing,
        truncated=truncated,
    )


@app.post(
    "/api/workdesk/record-summary",
    response_model=PeriodRecordSummaryResponse,
)
def create_period_record_summary(
    payload: PeriodRecordSummaryRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    requested_selections = list(payload.selections)
    selected_evidence_ids = (
        list(
            dict.fromkeys(
                evidence_id
                for selection in requested_selections
                for evidence_id in selection.evidence_ids
            )
        )
        if requested_selections
        else list(payload.evidence_ids)
    )
    room_query = select(Room.id).where(
        Room.organization_id == processor.organization_id,
        Room.is_active.is_(True),
    )
    if processor.role != "admin":
        if processor.staff_id is None:
            raise HTTPException(status_code=403, detail="확인할 수 있는 채팅방이 없습니다.")
        room_query = (
            room_query.join(RoomMembership, RoomMembership.room_id == Room.id)
            .where(
                RoomMembership.staff_id == processor.staff_id,
                RoomMembership.left_at.is_(None),
            )
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(
            Room.name == settings.reviewer_chat_room_name,
            Room.is_test_data.is_(True),
        )
    visible_room_ids = list(db.scalars(room_query).unique().all())
    messages = list(
        db.scalars(
            select(Message)
            .where(
                Message.id.in_(selected_evidence_ids),
                Message.room_id.in_(visible_room_ids),
                *(
                    [Message.is_test_data.is_(True)]
                    if getattr(processor, "_reviewer_experience", None)
                    is not None
                    else []
                ),
            )
            .order_by(Message.created_at, Message.id)
        ).unique().all()
    )
    if len(messages) != len(selected_evidence_ids):
        raise HTTPException(
            status_code=403,
            detail="선택한 근거 중 확인할 수 없는 대화가 있습니다.",
        )
    comments = list(
        db.scalars(
            select(MessageComment)
            .where(MessageComment.message_id.in_(selected_evidence_ids))
            .order_by(MessageComment.created_at, MessageComment.id)
        ).all()
    )
    comments_by_message: dict[UUID, list[MessageComment]] = {}
    for comment in comments:
        comments_by_message.setdefault(comment.message_id, []).append(comment)
    confirmed_links = list(
        db.scalars(
            select(MessageResidentLink)
            .where(
                MessageResidentLink.message_id.in_(selected_evidence_ids),
                MessageResidentLink.status == "confirmed",
            )
            .order_by(
                MessageResidentLink.message_id,
                MessageResidentLink.created_at,
                MessageResidentLink.id,
            )
        ).all()
    )
    resident_ids_by_message: dict[UUID, list[UUID]] = {}
    all_resident_ids: set[UUID] = {
        selection.resident_id
        for selection in requested_selections
        if selection.resident_id is not None
    }
    for link in confirmed_links:
        linked_ids = resident_ids_by_message.setdefault(link.message_id, [])
        if link.resident_id not in linked_ids:
            linked_ids.append(link.resident_id)
        all_resident_ids.add(link.resident_id)
    for message in messages:
        if message.resident_id is None:
            continue
        linked_ids = resident_ids_by_message.setdefault(message.id, [])
        if message.resident_id not in linked_ids:
            linked_ids.insert(0, message.resident_id)
        all_resident_ids.add(message.resident_id)

    residents_by_id = {
        resident.id: resident
        for resident in db.scalars(
            select(Resident).where(
                Resident.id.in_(all_resident_ids),
                Resident.organization_id == processor.organization_id,
                Resident.is_active.is_(True),
            )
        ).all()
    }

    if not requested_selections:
        evidence_ids_by_resident: dict[UUID | None, list[UUID]] = {}
        resident_order: list[UUID | None] = []
        for evidence_id in selected_evidence_ids:
            linked_ids = resident_ids_by_message.get(evidence_id, [])
            unavailable_linked_ids = [
                resident_id
                for resident_id in linked_ids
                if resident_id not in residents_by_id
            ]
            if unavailable_linked_ids:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "연결된 어르신 중 현재 확인할 수 없는 명단이 있습니다. "
                        "어르신 연결을 정리한 뒤 다시 시도해 주세요."
                    ),
                )
            if len(linked_ids) > 1:
                raise HTTPException(
                    status_code=422,
                    detail=(
                        "여러 어르신이 연결된 근거는 이전 선택 형식으로 "
                        "안전하게 구분할 수 없습니다. 브리핑을 새로 불러온 뒤 "
                        "어르신별로 다시 선택해 주세요."
                    ),
                )
            resident_id = linked_ids[0] if linked_ids else None
            if resident_id not in evidence_ids_by_resident:
                evidence_ids_by_resident[resident_id] = []
                resident_order.append(resident_id)
            evidence_ids_by_resident[resident_id].append(evidence_id)
        requested_selections = [
            PeriodRecordSummarySelection(
                resident_id=resident_id,
                evidence_ids=evidence_ids_by_resident[resident_id],
            )
            for resident_id in resident_order
        ]

    selected_resident_ids = {
        selection.resident_id
        for selection in requested_selections
        if selection.resident_id is not None
    }
    if not selected_resident_ids.issubset(residents_by_id):
        raise HTTPException(
            status_code=422,
            detail="선택한 어르신 정보를 확인할 수 없습니다.",
        )

    messages_by_id = {message.id: message for message in messages}
    entries: list[dict[str, Any]] = []
    seen_selection_evidence: set[tuple[UUID | None, UUID]] = set()
    for selection in requested_selections:
        for evidence_id in selection.evidence_ids:
            selection_key = (selection.resident_id, evidence_id)
            if selection_key in seen_selection_evidence:
                continue
            seen_selection_evidence.add(selection_key)
            message = messages_by_id[evidence_id]
            linked_ids = resident_ids_by_message.get(message.id, [])
            if selection.resident_id is None:
                if linked_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "어르신이 연결된 근거는 해당 어르신 항목에서 "
                            "선택해 주세요."
                        ),
                    )
                resident_name = ""
                body = _period_message_text(
                    message,
                    comments_by_message.get(message.id, []),
                )
            else:
                if selection.resident_id not in linked_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "선택한 근거와 어르신 연결을 확인할 수 없습니다. "
                            "브리핑을 새로 불러온 뒤 다시 선택해 주세요."
                        ),
                    )
                resident = residents_by_id[selection.resident_id]
                resident_name = resident.display_name
                unavailable_linked_ids = [
                    resident_id
                    for resident_id in linked_ids
                    if resident_id not in residents_by_id
                ]
                if unavailable_linked_ids:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "연결된 어르신 중 현재 확인할 수 없는 명단이 있습니다. "
                            "어르신 연결을 정리한 뒤 다시 시도해 주세요."
                        ),
                    )
                linked_resident_names = [
                    residents_by_id[resident_id].display_name
                    for resident_id in linked_ids
                ]
                full_body = _period_message_text(
                    message,
                    comments_by_message.get(message.id, []),
                )
                if len(linked_ids) == 1:
                    body = full_body
                else:
                    body = _resident_specific_period_text(
                        full_body,
                        target_name=resident_name,
                        resident_names=linked_resident_names,
                    )
                if not body:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            "여러 어르신이 포함된 근거에서 선택한 어르신의 "
                            "문장을 안전하게 분리하지 못했습니다."
                        ),
                    )
            entries.append(
                {
                    "number": len(entries) + 1,
                    "room": message.room.name,
                    "sender": message.sender.full_name,
                    "resident": resident_name,
                    "type": message.message_type,
                    "body": body,
                }
            )
    selected_tags = payload.record_usage_tags
    purpose = " · ".join(RECORD_USAGE_LABELS[tag] for tag in selected_tags)
    try:
        result = summarize_room_messages(
            entries=entries,
            external_allowed=all(message.is_test_data for message in messages),
            purpose=purpose,
        )
    except LocalAiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info(
        "record_summary_completed generator=%s:%s elapsed_ms=%d "
        "evidence_count=%d selection_count=%d",
        result.provider,
        result.model,
        result.elapsed_ms,
        len(selected_evidence_ids),
        len(requested_selections),
    )
    return PeriodRecordSummaryResponse(
        record_usage_tag=selected_tags[0],
        record_usage_tags=selected_tags,
        summary=result.summary,
        evidence_ids=selected_evidence_ids,
        generator=f"{result.provider}:{result.model}",
        elapsed_ms=result.elapsed_ms,
    )


@app.get("/api/work-items", response_model=list[WorkItemResponse])
def list_work_items(
    status_filter: str | None = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    items = _visible_work_items_for_processor(
        db, processor, status_filter=status_filter
    )
    suggestions_changed = False
    for item in items:
        suggestions_changed = _refresh_work_item_suggestion(db, item) or suggestions_changed
    if suggestions_changed:
        db.commit()
    return [
        _work_item_response(db, item, viewer_id=processor.id)
        for item in items
    ]


@app.get(
    "/api/document-candidates",
    response_model=DocumentCandidateDashboardResponse,
)
def list_document_candidates(
    document_type: DocumentType | None = None,
    risk_level: RiskLevel | None = None,
    classification: RecordClassification | None = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    confirmed_items = [
        item
        for item in _visible_work_items_for_processor(db, processor)
        if item.status == "ready"
        and item.confirmed_at is not None
        and item.confirmed_payload is not None
    ]
    document_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    classification_counts: dict[str, int] = {}
    for item in confirmed_items:
        payload = item.confirmed_payload or {}
        for candidate_type in payload.get("document_types", []):
            document_counts[candidate_type] = document_counts.get(candidate_type, 0) + 1
        candidate_risk = payload.get("risk_level")
        if candidate_risk:
            risk_counts[candidate_risk] = risk_counts.get(candidate_risk, 0) + 1
        candidate_classification = payload.get("classification")
        if candidate_classification:
            classification_counts[candidate_classification] = (
                classification_counts.get(candidate_classification, 0) + 1
            )

    filtered_items = []
    for item in confirmed_items:
        payload = item.confirmed_payload or {}
        if document_type and document_type not in payload.get("document_types", []):
            continue
        if risk_level and payload.get("risk_level") != risk_level:
            continue
        if classification and payload.get("classification") != classification:
            continue
        filtered_items.append(item)

    return DocumentCandidateDashboardResponse(
        total_count=len(confirmed_items),
        filtered_count=len(filtered_items),
        document_counts=document_counts,
        risk_counts=risk_counts,
        classification_counts=classification_counts,
        items=[
            _work_item_response(db, item, viewer_id=processor.id)
            for item in filtered_items
        ],
    )


@app.patch("/api/work-items/{work_item_id}", response_model=WorkItemResponse)
def update_work_item(
    work_item_id: UUID,
    payload: WorkItemUpdate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if payload.status == "ready" and item.confirmed_at is None:
        raise HTTPException(
            status_code=422,
            detail="서류 후보 준비 상태는 담당자 확인·확정 후에만 사용할 수 있습니다.",
        )
    values = payload.model_dump(exclude_unset=True)
    for field_name, value in values.items():
        setattr(item, field_name, value)
    if (
        "status" in payload.model_fields_set
        and payload.status != "ready"
        and item.confirmed_at is not None
    ):
        item.confirmed_payload = None
        item.confirmed_by_id = None
        item.confirmed_at = None
    item.handled_by_id = processor.id
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.updated",
        target_type="work_item",
        target_id=item.id,
        details={"changed_fields": sorted(payload.model_fields_set)},
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.patch(
    "/api/work-items/{work_item_id}/resident",
    response_model=WorkItemResponse,
)
def replace_work_item_resident(
    work_item_id: UUID,
    payload: WorkItemResidentUpdate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.confirmed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="최종 승인한 기록의 어르신은 변경할 수 없습니다.",
        )
    resident = db.scalar(
        select(Resident).where(
            Resident.id == payload.resident_id,
            Resident.organization_id == processor.organization_id,
            Resident.is_active.is_(True),
        )
    )
    if resident is None:
        raise HTTPException(status_code=404, detail="선택한 어르신을 찾을 수 없습니다.")

    message = item.source_message
    previous_resident_id = message.resident_id
    if previous_resident_id == resident.id:
        return _work_item_response(db, item, viewer_id=processor.id)

    target_link = db.scalar(
        select(MessageResidentLink).where(
            MessageResidentLink.message_id == message.id,
            MessageResidentLink.resident_id == resident.id,
        )
    )
    target_was_confirmed = (
        target_link is not None and target_link.status == "confirmed"
    )
    if previous_resident_id is not None and not target_was_confirmed:
        previous_link = db.scalar(
            select(MessageResidentLink).where(
                MessageResidentLink.message_id == message.id,
                MessageResidentLink.resident_id == previous_resident_id,
            )
        )
        if previous_link is not None:
            previous_link.status = "rejected"
            previous_link.reviewed_by_id = processor.id
            previous_link.reviewed_at = utcnow()

    if target_link is None:
        target_link = MessageResidentLink(
            organization_id=message.organization_id,
            message_id=message.id,
            resident_id=resident.id,
            source="manual",
            status="confirmed",
            reviewed_by_id=processor.id,
            reviewed_at=utcnow(),
        )
        db.add(target_link)
    else:
        target_link.source = "manual"
        target_link.status = "confirmed"
        target_link.reviewed_by_id = processor.id
        target_link.reviewed_at = utcnow()

    message.resident_id = resident.id
    db.flush()
    _refresh_work_item_residents(db, message)
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.resident_replaced",
        target_type="work_item",
        target_id=item.id,
        details={
            "previous_resident_id": (
                str(previous_resident_id) if previous_resident_id else None
            ),
            "resident_id": str(resident.id),
            "preserved_previous_link": target_was_confirmed,
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.post(
    "/api/work-items/{work_item_id}/prototype-suggestion",
    response_model=WorkItemResponse,
)
def create_prototype_suggestion(
    work_item_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.resident_id is None:
        raise HTTPException(
            status_code=422,
            detail="먼저 판독문에서 찾은 어르신 후보를 확인해 주세요.",
        )
    if _has_pending_message_resident_candidates(db, item.source_message_id):
        raise HTTPException(
            status_code=422,
            detail="남아 있는 어르신 후보를 모두 확인하거나 제외해 주세요.",
        )
    if item.confirmed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="담당자가 확정한 항목은 시험 제안을 다시 만들 수 없습니다.",
        )
    if item.status == "dismissed":
        raise HTTPException(
            status_code=409,
            detail="사용 안 함 처리된 항목은 먼저 검토 상태로 되돌려야 합니다.",
        )
    suggestion = RecordDraft.model_validate(
        build_prototype_suggestion(_work_item_ai_snapshot(db, item))
    )
    item.ai_state = "prototype_suggested"
    item.ai_payload = {
        **suggestion.model_dump(mode="json"),
        "source_comment_ids": [
            str(comment.id) for comment in _work_item_comments(db, item)
        ],
    }
    item.ai_generator = PROTOTYPE_GENERATOR
    item.ai_generated_at = utcnow()
    item.document_types = suggestion.document_types
    item.status = "in_review"
    item.handled_by_id = processor.id
    _sync_work_item_document_drafts(
        db,
        item=item,
        suggestion=suggestion,
        generator=PROTOTYPE_GENERATOR,
        actor_id=processor.id,
    )
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.prototype_suggested",
        target_type="work_item",
        target_id=item.id,
        details={"generator": PROTOTYPE_GENERATOR},
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.post(
    "/api/work-items/{work_item_id}/ai-review",
    response_model=WorkItemResponse,
)
def review_work_item_with_ai(
    work_item_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.resident_id is None:
        raise HTTPException(
            status_code=422,
            detail="먼저 판독문에서 찾은 어르신 후보를 확인해 주세요.",
        )
    if _has_pending_message_resident_candidates(db, item.source_message_id):
        raise HTTPException(
            status_code=422,
            detail="남아 있는 어르신 후보를 모두 확인하거나 제외해 주세요.",
        )
    if item.confirmed_at is not None:
        raise HTTPException(
            status_code=409,
            detail="담당자가 확정한 항목은 AI 초안을 다시 만들 수 없습니다.",
        )
    if item.status == "dismissed":
        raise HTTPException(
            status_code=409,
            detail="사용 안 함 처리된 항목은 먼저 검토 상태로 되돌려야 합니다.",
        )
    unreviewed_image_names = [
        attachment.original_name
        for attachment in item.source_message.attachments
        if attachment.mime_type in IMAGE_MIME_TYPES
        and attachment.text_extraction is not None
        and attachment.text_extraction.status != "reviewed"
    ]
    if unreviewed_image_names:
        raise HTTPException(
            status_code=422,
            detail=(
                "AI 정리 전에 보고서 이미지의 OCR 원문을 확인해 주세요: "
                + ", ".join(unreviewed_image_names[:3])
            ),
        )
    snapshot = _work_item_ai_snapshot(db, item)
    prototype = RecordDraft.model_validate(build_prototype_suggestion(snapshot))
    current_record = RecordDraft.model_validate(item.ai_payload or prototype)
    current_draft = _merge_record_draft_with_prototype(
        current_record,
        prototype,
    ).model_dump(mode="json")
    try:
        result = refine_record_draft(
            source_snapshot=snapshot,
            current_draft=current_draft,
            lexicon_context=get_ai_lexicon_context(
                str(snapshot.get("body", "")),
            ),
            external_allowed=bool(item.is_test_data and item.source_message.is_test_data),
        )
        refined = RecordDraft.model_validate(result.draft)
    except LocalAiError as exc:
        fallback = RecordDraft.model_validate(current_draft)
        item.ai_state = "prototype_suggested"
        item.ai_payload = {
            **fallback.model_dump(mode="json"),
            "source_comment_ids": [
                str(comment.id) for comment in _work_item_comments(db, item)
            ],
            "_review_meta": {
                "provider": "rule",
                "model": PROTOTYPE_GENERATOR,
                "status": "ai_unavailable",
                "reason": str(exc)[:300],
            },
        }
        item.ai_generator = PROTOTYPE_GENERATOR
        item.ai_generated_at = utcnow()
        item.document_types = fallback.document_types
        item.status = "in_review"
        item.handled_by_id = processor.id
        _sync_work_item_document_drafts(
            db,
            item=item,
            suggestion=fallback,
            generator=PROTOTYPE_GENERATOR,
            actor_id=processor.id,
        )
        record_audit(
            db,
            actor_id=processor.id,
            action="work_item.ai_review_failed_fallback",
            target_type="work_item",
            target_id=item.id,
            details={
                "reason": str(exc)[:300],
                "correction_candidate_count": 0,
            },
        )
        db.commit()
        db.refresh(item)
        return _work_item_response(db, item, viewer_id=processor.id)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail="AI 결과 형식이 맞지 않아 기존 초안을 보존했습니다.",
        ) from exc

    item.ai_state = "ai_reviewed"
    item.ai_payload = {
        **refined.model_dump(mode="json"),
        "source_comment_ids": [
            str(comment.id) for comment in _work_item_comments(db, item)
        ],
        "_review_meta": {
            "provider": result.provider,
            "model": result.model,
            "elapsed_ms": result.elapsed_ms,
            "attempts": result.attempts,
        },
    }
    item.ai_generator = f"{result.provider}:{result.model}"[:80]
    item.ai_generated_at = utcnow()
    item.document_types = refined.document_types
    item.status = "in_review"
    item.handled_by_id = processor.id
    _sync_work_item_document_drafts(
        db,
        item=item,
        suggestion=refined,
        generator=f"{result.provider}:{result.model}",
        actor_id=processor.id,
    )
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.ai_reviewed",
        target_type="work_item",
        target_id=item.id,
        details={
            "provider": result.provider,
            "model": result.model,
            "elapsed_ms": result.elapsed_ms,
            "attempts": result.attempts,
            "correction_candidate_count": 0,
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.patch(
    "/api/work-items/{work_item_id}/document-drafts/{document_type}",
    response_model=WorkItemResponse,
)
def update_work_item_document_draft(
    work_item_id: UUID,
    document_type: DailyDocumentType,
    payload: WorkItemDocumentDraftActionRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    current = db.scalar(
        select(WorkItemDocumentDraft).where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.document_type == document_type,
            WorkItemDocumentDraft.is_current.is_(True),
        )
    )
    if current is None:
        raise HTTPException(status_code=404, detail="현재 서류 초안을 찾을 수 없습니다.")

    if payload.action == "direct_edit":
        if not payload.content:
            raise HTTPException(status_code=422, detail="수정한 서류 내용을 입력해 주세요.")
        current = _replace_work_item_document_draft(
            db,
            item=item,
            document_type=document_type,
            content=payload.content,
            verification_questions=current.verification_questions or [],
            generator="manual-review",
            created_by_id=processor.id,
        )
    elif payload.action in {"regenerate", "change_request"}:
        if payload.action == "change_request" and not payload.change_request:
            raise HTTPException(status_code=422, detail="AI에게 요청할 변경 내용을 입력해 주세요.")
        base_payload = item.confirmed_payload or item.ai_payload
        if base_payload is None:
            raise HTTPException(status_code=422, detail="먼저 AI 업무 정리를 실행해 주세요.")
        current_record_payload = RecordDraft.model_validate(base_payload).model_dump(
            mode="json"
        )
        current_record_payload["document_types"] = [document_type]
        current_record_payload["document_drafts"] = [
            {
                "document_type": document_type,
                "content": current.content,
                "verification_questions": current.verification_questions or [],
            }
        ]
        current_record = RecordDraft.model_validate(current_record_payload)
        snapshot = _work_item_ai_snapshot(db, item)
        snapshot["document_change_request"] = (
            payload.change_request
            if payload.action == "change_request"
            else "원문 사실을 유지하면서 더 간결하고 실제 서류에 옮기기 쉬운 문장으로 다시 작성"
        )
        try:
            result = refine_record_draft(
                source_snapshot=snapshot,
                current_draft=current_record.model_dump(mode="json"),
                lexicon_context=get_ai_lexicon_context(
                    str(snapshot.get("body", "")),
                ),
                external_allowed=bool(
                    item.is_test_data and item.source_message.is_test_data
                ),
            )
            regenerated = RecordDraft.model_validate(result.draft)
        except (LocalAiError, ValidationError) as exc:
            raise HTTPException(
                status_code=503,
                detail=f"AI가 서류 초안을 다시 만들지 못했습니다: {str(exc)[:240]}",
            ) from exc
        proposal = next(
            (
                candidate
                for candidate in regenerated.document_drafts
                if candidate.document_type == document_type
            ),
            None,
        )
        if proposal is None:
            raise HTTPException(
                status_code=422,
                detail="AI 응답에 요청한 서류 초안이 없어 기존 초안을 보존했습니다.",
            )
        current = _replace_work_item_document_draft(
            db,
            item=item,
            document_type=document_type,
            content=proposal.content,
            verification_questions=proposal.verification_questions,
            generator=f"{result.provider}:{result.model}",
            created_by_id=processor.id,
            change_request=snapshot["document_change_request"],
        )
    elif payload.action == "approve":
        if current.verification_questions and not payload.verification_acknowledged:
            raise HTTPException(
                status_code=422,
                detail="확인이 필요한 내용을 검토했다는 표시가 필요합니다.",
            )
        current.status = "approved"
        current.approved_by_id = processor.id
        current.approved_at = utcnow()
    else:
        current = _replace_work_item_document_draft(
            db,
            item=item,
            document_type=document_type,
            content=current.content,
            verification_questions=current.verification_questions or [],
            generator="manual-review",
            created_by_id=processor.id,
            status_value="not_used",
        )

    item.handled_by_id = processor.id
    record_audit(
        db,
        actor_id=processor.id,
        action=f"work_item.document_draft.{payload.action}",
        target_type="work_item_document_draft",
        target_id=current.id,
        details={
            "work_item_id": str(item.id),
            "document_type": document_type,
            "version": current.version,
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.post(
    "/api/work-items/{work_item_id}/document-drafts/{document_type}",
    response_model=WorkItemResponse,
)
def add_work_item_document_draft(
    work_item_id: UUID,
    document_type: DailyDocumentType,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.confirmed_at is not None:
        raise HTTPException(status_code=409, detail="이미 최종 승인된 자료입니다.")
    if item.status == "dismissed":
        raise HTTPException(
            status_code=409,
            detail="사용 안 함 자료를 먼저 다시 확인 상태로 바꿔 주세요.",
        )
    if item.ai_payload is None:
        raise HTTPException(status_code=422, detail="먼저 AI 업무 정리를 실행해 주세요.")

    current = db.scalar(
        select(WorkItemDocumentDraft).where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.document_type == document_type,
            WorkItemDocumentDraft.is_current.is_(True),
        )
    )
    suggestion = RecordDraft.model_validate(item.ai_payload)
    proposal_by_type = {
        proposal.document_type: proposal.model_dump(mode="json")
        for proposal in suggestion.document_drafts
    }

    if current is None:
        snapshot = _work_item_ai_snapshot(db, item)
        resident_names = [
            str(name).strip()
            for name in snapshot.get("resident_names", [])
            if str(name).strip()
        ]
        resident_name = str(snapshot.get("resident_name") or "").strip()
        if resident_name and resident_name not in resident_names:
            resident_names.insert(0, resident_name)
        proposal = build_document_proposal(
            document_type,
            resident_label=", ".join(resident_names) or "어르신 확인 필요",
            text=suggestion.corrected_text,
            classification=suggestion.classification,
            risk_level=suggestion.risk_level,
        )
        current = _replace_work_item_document_draft(
            db,
            item=item,
            document_type=document_type,
            content=proposal["content"],
            verification_questions=proposal["verification_questions"],
            generator="reviewer-added-rule-v1",
            created_by_id=processor.id,
        )
        proposal_by_type[document_type] = proposal
    elif current.status == "not_used":
        current = _replace_work_item_document_draft(
            db,
            item=item,
            document_type=document_type,
            content=current.content,
            verification_questions=current.verification_questions or [],
            generator="reviewer-restored",
            created_by_id=processor.id,
        )
        proposal_by_type[document_type] = {
            "document_type": document_type,
            "content": current.content,
            "verification_questions": current.verification_questions or [],
        }
    else:
        proposal_by_type[document_type] = {
            "document_type": document_type,
            "content": current.content,
            "verification_questions": current.verification_questions or [],
        }

    suggestion_payload = suggestion.model_dump(mode="json")
    document_types = list(
        dict.fromkeys([*suggestion_payload["document_types"], document_type])
    )
    suggestion_payload["document_types"] = document_types
    suggestion_payload["document_drafts"] = [
        proposal_by_type[candidate_type]
        for candidate_type in document_types
        if candidate_type in proposal_by_type
    ]
    updated_suggestion = RecordDraft.model_validate(suggestion_payload)
    extra_payload = {
        key: value
        for key, value in item.ai_payload.items()
        if key not in RecordDraft.model_fields
    }
    item.ai_payload = {
        **updated_suggestion.model_dump(mode="json"),
        **extra_payload,
    }
    item.document_types = updated_suggestion.document_types
    item.status = "in_review"
    item.handled_by_id = processor.id
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.document_draft.added",
        target_type="work_item_document_draft",
        target_id=current.id,
        details={
            "work_item_id": str(item.id),
            "document_type": document_type,
            "version": current.version,
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.post(
    "/api/work-items/{work_item_id}/confirm",
    response_model=WorkItemResponse,
)
def confirm_work_item(
    work_item_id: UUID,
    payload: WorkItemConfirmRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.ai_payload is None or item.ai_state not in {
        "prototype_suggested",
        "ai_reviewed",
    }:
        raise HTTPException(
            status_code=422,
            detail="먼저 시험용 판독 제안을 만들어야 합니다.",
        )
    if item.status == "dismissed":
        raise HTTPException(status_code=409, detail="사용 안 함 처리된 항목입니다.")
    selected_drafts = [
        draft
        for draft in _current_work_item_document_drafts(db, item)
        if draft.document_type in payload.document_types
        and draft.status != "not_used"
    ]
    pending_questions = list(payload.verification_questions)
    for draft in selected_drafts:
        pending_questions.extend(draft.verification_questions or [])
    if pending_questions and not payload.verification_acknowledged:
        raise HTTPException(
            status_code=422,
            detail="확인이 필요한 내용을 검토했다는 표시가 필요합니다.",
        )
    item.confirmed_payload = payload.model_dump(mode="json")
    item.confirmed_by_id = processor.id
    item.confirmed_at = utcnow()
    item.document_types = payload.document_types
    item.processing_notes = payload.reviewer_notes
    item.status = "ready"
    item.handled_by_id = processor.id
    _approve_selected_document_drafts(
        db,
        item=item,
        document_types=payload.document_types,
        processor=processor,
    )
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.confirmed",
        target_type="work_item",
        target_id=item.id,
        details={
            "classification": payload.classification,
            "risk_level": payload.risk_level,
            "document_types": payload.document_types,
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


@app.post(
    "/api/work-items/{work_item_id}/reopen",
    response_model=WorkItemResponse,
)
def reopen_work_item(
    work_item_id: UUID,
    payload: WorkItemReopenRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    item = _work_item_for_processor(db, processor, work_item_id)
    if item.status != "ready" or item.confirmed_payload is None:
        raise HTTPException(status_code=409, detail="승인 완료된 업무만 다시 수정할 수 있습니다.")
    if processor.role != "admin" and item.confirmed_by_id != processor.id:
        raise HTTPException(
            status_code=403,
            detail="최종 승인자 또는 관리자만 승인을 취소할 수 있습니다.",
        )

    previous_confirmed_at = item.confirmed_at
    previous_confirmed_by_id = item.confirmed_by_id
    previous_payload = dict(item.confirmed_payload)
    editable_payload = RecordDraft.model_validate(previous_payload).model_dump(mode="json")
    extra_payload = {
        key: value
        for key, value in (item.ai_payload or {}).items()
        if key not in RecordDraft.model_fields
    }
    item.ai_payload = {**editable_payload, **extra_payload}
    item.ai_state = "ai_reviewed"
    item.ai_generator = "reviewer-reopened"
    item.ai_generated_at = utcnow()
    item.document_types = editable_payload["document_types"]

    for draft in list(_current_work_item_document_drafts(db, item)):
        if draft.status != "approved":
            continue
        _replace_work_item_document_draft(
            db,
            item=item,
            document_type=draft.document_type,
            content=draft.content,
            verification_questions=draft.verification_questions or [],
            generator="reviewer-reopened",
            created_by_id=processor.id,
            change_request=payload.reason,
        )

    item.confirmed_payload = None
    item.confirmed_by_id = None
    item.confirmed_at = None
    item.status = "in_review"
    item.handled_by_id = processor.id
    record_audit(
        db,
        actor_id=processor.id,
        action="work_item.confirmation_reopened",
        target_type="work_item",
        target_id=item.id,
        details={
            "reason": payload.reason,
            "previous_confirmed_at": (
                _as_utc(previous_confirmed_at).isoformat()
                if previous_confirmed_at is not None
                else None
            ),
            "previous_confirmed_by_id": (
                str(previous_confirmed_by_id)
                if previous_confirmed_by_id is not None
                else None
            ),
            "document_types": editable_payload["document_types"],
        },
    )
    db.commit()
    db.refresh(item)
    return _work_item_response(db, item, viewer_id=processor.id)


def _websocket_auth(websocket: WebSocket, db: Session) -> tuple[LoginSession, User] | None:
    token = websocket.cookies.get(settings.session_cookie_name)
    if not token:
        return None
    login_session = db.scalar(
        select(LoginSession).where(LoginSession.token_hash == token_digest(token))
    )
    now = utcnow()
    if (
        login_session is None
        or login_session.revoked_at is not None
        or _as_utc(login_session.expires_at) <= now
    ):
        return None
    user = db.get(User, login_session.user_id)
    if (
        user is None
        or not user.is_active
        or user.employment_status != "active"
        or user.must_change_password
    ):
        return None
    try:
        reviewer_context = validate_reviewer_session_user(
            token,
            user,
            now=now,
        )
    except InvalidReviewerSessionToken:
        login_session.revoked_at = now
        db.commit()
        return None
    reviewer_experience = (
        reviewer_context.experience if reviewer_context is not None else None
    )
    login_session._reviewer_experience = reviewer_experience
    user._reviewer_experience = reviewer_experience
    return login_session, user


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    origin = websocket.headers.get("origin")
    if origin and origin.rstrip("/") not in settings.origin_list:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    db = SessionLocal()
    auth = _websocket_auth(websocket, db)
    if auth is None:
        db.close()
        await websocket.close(code=4001, reason="로그인이 필요합니다.")
        return
    login_session, user = auth
    user_id = user.id
    await manager.connect(user_id, login_session.id, websocket)
    await websocket.send_json({"event": "ready", "user_id": str(user_id)})
    try:
        while True:
            expires_in = (
                _as_utc(login_session.expires_at) - utcnow()
            ).total_seconds()
            if expires_in <= 0:
                await websocket.send_json(
                    {"event": "force_logout", "reason": "세션이 만료되었습니다."}
                )
                await websocket.close(code=4003)
                break
            try:
                payload = await asyncio.wait_for(
                    websocket.receive_json(),
                    timeout=expires_in,
                )
            except asyncio.TimeoutError:
                await websocket.send_json(
                    {"event": "force_logout", "reason": "세션이 만료되었습니다."}
                )
                await websocket.close(code=4003)
                break
            if payload.get("event") == "ping":
                db.expire_all()
                refreshed = _websocket_auth(websocket, db)
                if refreshed is None or refreshed[0].id != login_session.id:
                    await websocket.send_json(
                        {"event": "force_logout", "reason": "세션이 만료되었습니다."}
                    )
                    await websocket.close(code=4003)
                    break
                login_session.last_seen_at = utcnow()
                db.commit()
                await websocket.send_json({"event": "pong"})
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, login_session.id, websocket)
        db.close()
