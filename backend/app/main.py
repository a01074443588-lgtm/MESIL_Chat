from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, ExitStack
from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from hashlib import sha256
import hmac
from io import BytesIO
import json
import logging
from pathlib import Path
import re
import secrets
from shutil import copy2
import tempfile
from time import monotonic, perf_counter
from typing import Annotated, Any, Literal, NoReturn, Sequence
from uuid import UUID, uuid4

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    Response,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware
from sqlalchemy import and_, case, delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .attachment_review_policy import (
    ATTACHMENT_STAFF_REVIEW_REQUIRED_DETAIL,
    attachment_evidence_text,
    required_attachment_reviews_current,
    requires_staff_review,
)
from .attachment_review_policy import review_fingerprint, message_review_fingerprint, work_item_review_current
from .document_reading import DOCUMENT_MIME_TYPES, document_extension, READABLE_EXTENSIONS
from .attachment_validation import (
    AttachmentValidationError,
    DOCUMENT_MIME_BY_EXTENSION,
    is_download_only_document,
    validate_uploaded_attachment,
)
from .period_review_performance import PeriodTiming, PeriodTimingMiddleware, briefing_sources, prefetch_period_relations
from .record_narrative import generate_narrative, prepare_record_model, await_connected, natural_clause, source_clauses
from .search_summary_narrative import (
    build_search_fallback,
    choose_summary_mode,
    generate_search_summary,
    preprocess_search_facts,
    search_model_fingerprint,
    search_summary_cache_key,
)
from .schemas import PeriodEvidenceRequest
from .finalist_coded_synthetic import RESIDENT_CODE_RE
from .database import Base, SessionLocal, engine, get_db
from .dependencies import (
    admin_conversation_access_is_active,
    get_current_session_and_user,
    get_current_user,
    require_admin,
    require_admin_conversation_access,
)
from .confirmed_records import (
    ConfirmedRecordError,
    persist_confirmed_records_from_work_item,
    router as confirmed_records_router,
)
from .ai_analysis import (
    AiAnalysisEvidenceInput,
    append_ai_analysis_snapshot,
    content_fingerprint,
)
from .ai_assist import AiAssistRuntimeSettings, router as ai_assist_router
from .ai_assist_schemas import AiAssistSourceSnapshot
from .ai_system import router as ai_system_router
from .codex_worker_client import CodexWorkerClient, CodexWorkerError
from .smcodi_outbox import router as smcodi_outbox_router
from .models import (
    ActionItem,
    AttachmentCoordinateReview,
    AttachmentTextExtraction,
    AttachmentTextExtractionAttempt,
    AuditEvent,
    BulkTransferBatch,
    BulkTransferItem,
    FieldCareBriefingHistory,
    HandwritingCorrectionApproval,
    LoginSession,
    Message,
    MessageAttachment,
    MessageComment,
    MessageReadReceipt,
    MessageResidentLink,
    MessageThreadView,
    MobilePushDevice,
    VoiceCallInvitation,
    VoiceCallParticipant,
    AssessmentEvidenceLedgerEntry,
    NewAdmissionDraftRecord,
    NewAdmissionDraftRevision,
    OcrCorrectionEvent,
    OcrCorrectionMemory,
    OrgUnit,
    PushSubscription,
    Resident,
    ResidentAssessmentCycle,
    ResidentAssessmentCycleRevision,
    ResidentCarePlanningState,
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
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    StaffApplicationRun,
    StaffSyncBatch,
    StaffSyncItem,
    StaffSyncReviewDraft,
    User,
    WorkItem,
    WorkItemDocumentDraft,
    utcnow,
)
from .bulk_transfer import (
    XLSX_CONTENT_TYPE,
    apply_batch as apply_bulk_transfer_batch,
    available_choices as bulk_transfer_choices,
    create_preview as create_bulk_transfer_preview,
    current_workbook as bulk_transfer_current_workbook,
    error_workbook as bulk_transfer_error_workbook,
    get_batch as get_bulk_transfer_batch,
    workbook_template as bulk_transfer_workbook_template,
)
from .local_ai import (
    LocalAiError,
    RoomSummaryResult,
    deidentify_external_room_entries,
    local_summary_provider_status,
    nemotron_is_configured,
    refine_record_draft,
    restore_external_resident_names,
    summarize_room_messages,
)
from .field_care_briefing import KST, build_field_care_briefing
from .care_record_journey import build_care_topics
from .record_question import plan_question, answer_facts, event_id, question_date_window
from .record_aggregate import aggregate_question, is_aggregate_question, has_later_record_correction, has_unattributed_source, has_unread_aggregate_attachments
from .message_nature import MessageNatureDecision, classify_message_nature
from .image_variants import ensure_image_thumbnail, thumbnail_path
from .handwriting_preprocessing import prepare_handwriting_for_ocr
from .image_ocr_geometry import prepare_image_geometry
from .resident_candidate_evidence import resident_link_is_current, visible_image_text
from .photo_reading import photo_reading_status, resident_review_metadata
from .ocr import is_pathological_handwriting_output
from .image_ocr_runtime import attachment_image_runtime, requested_image_runtime, safe_image_failure, ImageOcrFailure
from .handwriting_voice_correction import (
    build_handwriting_voice_comparison,
    correction_sentences,
)
from .handwriting_voice_ai import refine_handwriting_with_internal_ai
from .ocr import (
    HANDWRITING_SAFE_FAILURE_MESSAGE,
    OcrError,
    extract_assessment_document_text,
    extract_handwriting_text,
    extract_report_name_candidates,
    extract_report_text,
    find_spelling_candidates,
    get_ai_lexicon_context,
    image_likely_contains_text,
    locate_report_text_regions,
)
from .ocr_corrections import (
    CorrectionEvidence,
    PROTECTED_CONTENT_TYPES,
    ResidentRosterEntry,
    apply_safe_context_corrections_to_draft,
    apply_top_name_candidates_to_draft,
    build_roster_aware_draft,
    build_correction_pairs,
    detect_report_name_slots,
    extract_page_visual_signature,
    infer_resident_service_context,
    rank_visual_name_candidates,
)
from .ocr_template_engine import (
    VisualNameObservation,
    analyze_combined_checklist,
)
from .ocr_versioned_pipeline import (
    FrozenContentOcr,
    run_versioned_structured_stage,
    serialize_versioned_structured_run,
)
from .new_admission_questions import build_new_admission_question_flow
from .new_admission_contract import (
    ASSESSMENT_DOCUMENT_TYPES,
    NewAdmissionDraftBundle,
)
from .assessment_file_intake import (
    DOCUMENT_KIND_LABELS,
    MAX_ASSESSMENT_FILES,
    ParsedAssessmentMaterial,
    build_assessment_evidence_entries,
    build_assessment_evidence_summary,
    build_parsed_materials,
    build_assessment_draft_bundle,
    extract_declared_target_codes,
    extract_pdf_page_texts,
    merge_assessment_materials_into_bundle,
    parse_document_kind_selection,
    protected_material_directory,
    render_pdf_pages,
)
from .assessment_chat_evidence import collect_resident_assessment_chat_evidence
from .resident_assessment_cycles import build_assessment_cycle_comparison
from .realtime import manager
from .push import (
    build_voice_call_mobile_payload,
    send_voice_call_web_push,
    send_web_push_to_users,
    voice_call_ttl_seconds,
)
from .mobile_push import send_voice_call_mobile_push
from .call_delivery import CallDeliveryCoordinator, DeliveryResult
from .voice_call_turn import VoiceCallTurnError, voice_call_ice_servers
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
    ActiveStaffDirectoryResponse,
    AdminConversationAccessRequest,
    AdminConversationAccessResponse,
    AdminPasswordResetRequest,
    AttachmentResponse,
    AttachmentCoordinateReviewCreate,
    AttachmentCoordinateReviewResponse,
    AttachmentTextExtractionRequest,
    AttachmentTextExtractionUpdate,
    HandwritingVoiceCorrectionResponse,
    HandwritingCorrectionApprovalCreate,
    HandwritingCorrectionApprovalHistoryResponse,
    HandwritingCorrectionApprovalResponse,
    CareBriefingCard,
    CareBriefingSummary,
    FieldCareBriefingHistorySummary,
    CareDocumentCandidateType,
    CareforRosterSourceStatus,
    CareforRosterStatusResponse,
    CareforStaffSyncSourceResponse,
    CareforStaffAliasResponse,
    BulkTransferApplyRequest,
    CoordinateBox,
    CoordinateRegion,
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
    MessageRecallRequest,
    MessageRecallResponse,
    MessageResidentLinkUpdate,
    MessageResponse,
    MobilePushDeviceCreate,
    MobilePushDeviceDelete,
    MobilePushDeviceResponse,
    VoiceCallDeliveryReceiptRequest,
    JobCodeCreate,
    JobCodeUpdate,
    JobCodeResponse,
    LOGIN_USERNAME_PATTERN,
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
    RecordQuestionRequest,
    RecordQuestionResponse,
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
    ResidentAdminUpdate,
    ResidentResponse,
    ResidentCarePlanningResponse,
    ResidentCarePlanningStateResponse,
    ResidentCarePlanningStateUpdate,
    ResidentAssessmentCycleListResponse,
    ResidentAssessmentCycleResponse,
    ResidentAssessmentCycleRevisionCreate,
    ResidentAssessmentCycleRevisionResponse,
    ResidentAssessmentCycleSummaryResponse,
    CarePlanningDocumentType,
    AssessmentChatEvidencePreviewResponse,
    AssessmentEvidenceLedgerItemResponse,
    AssessmentEvidenceLedgerResponse,
    AssessmentEvidenceSummaryResponse,
    NewAdmissionDraftListResponse,
    NewAdmissionQuestionFlowResponse,
    NewAdmissionDraftResponse,
    NewAdmissionDraftRevisionCreate,
    NewAdmissionDraftRevisionResponse,
    NewAdmissionDraftSummaryResponse,
    ResidentOrderUpdate,
    ResidentSyncApplyRequest,
    ResidentSyncBatchResponse,
    ResidentSyncItemResponse,
    StaffSyncBatchResponse,
    StaffSyncItemResponse,
    StaffAssignmentPreviewResponse,
    StaffApplicationPlanResponse,
    StaffApplicationEvidenceRequest,
    StaffApplicationRunPrepareRequest,
    StaffApplicationRunResponse,
    StaffDirectoryAdminCreate,
    StaffDirectoryAdminUpdate,
    StaffDirectoryResponse,
    StaffLoginIssueRequest,
    StaffServiceAssignmentsAdminUpdate,
    StaffRoomCreate,
    StaffRoomMembersRequest,
    StaffRoomOwnerTransferRequest,
    StaffRoomResponse,
    StaffRoomMemberResponse,
    StaffRoomUpdate,
    StaffSyncReviewDraftResponse,
    StaffSyncReviewDraftSaveRequest,
    ReviewerSessionRequest,
    UsernameAvailabilityResponse,
    ReviewerSessionResponse,
    RoomDigestPoint,
    RoomDigestResponse,
    RoomMessageSearchMatchResponse,
    RoomMessageSearchResponse,
    RoomMemberResponse,
    RoomResponse,
    RoomSearchSummaryRequest,
    RoomSearchSummaryResponse,
    RiskLevel,
    SessionResponse,
    UserResponse,
    VoiceCallConfigResponse,
    VoiceCallIceServerResponse,
    WorkItemResponse,
    WorkItemConfirmRequest,
    WorkItemReopenRequest,
    WorkItemDocumentDraftActionRequest,
    WorkItemDocumentDraftResponse,
    WorkItemResidentUpdate,
    WorkItemUpdate,
)
from .coordinate_reviews import (
    CoordinateLineLocation,
    CoordinatePlacementEvidence,
    build_coordinate_bootstrap_regions,
    coordinate_template_key,
    enrich_status_correction_pairs,
    prepare_coordinate_regions_for_editor,
)
from .resident_sync import (
    ResidentSyncError,
    ResidentSyncStaleError,
    apply_sync_item,
    build_preview_entries,
    parse_roster_file,
)
from .resident_care_planning import (
    CARE_PLANNING_DOCUMENT_TYPES,
    LEGAL_STANDARD_NOTICE,
    OPERATING_CYCLE_NOTICE,
    candidate_reasons as care_planning_candidate_reasons,
    normalized_due_dates,
)
from .social_worker_guidance import build_social_worker_guidance
from .search_context import isolate_search_entries_for_resident, resident_specific_search_text
from .staff_application_manifest import (
    StaffApplicationManifestError,
    application_run_response,
    attach_backup_proof,
    attach_restore_receipt,
    prepare_application_run,
)
from .staff_sync import (
    StaffSyncError,
    build_staff_application_plan,
    build_staff_assignment_preview,
    build_staff_preview_entries,
    normalize_staff_review_draft,
    parse_staff_roster_file,
)
from .security import (
    MENTOR_FULL_REVIEW_EXPERIENCE,
    REVIEWER_SESSION_USER_AGENT_PREFIX,
    InvalidReviewerSessionToken,
    clear_failed_logins,
    client_key_from_request,
    create_login_session,
    create_reviewer_session_token,
    dummy_password_hash,
    hash_password,
    is_local_development_request,
    is_mentor_full_reviewer,
    is_reviewer_login_session,
    login_retry_after,
    mentor_visible_resident_code,
    record_failed_login,
    reviewer_session_user_agent,
    reviewer_session_context,
    secure_cookie_for_request,
    token_digest,
    validate_reviewer_session_user,
    verify_password,
)
from .stt import (
    SttTranscriptionResult,
    stt_readiness,
    transcribe_audio,
    transcribe_audio_with_metadata,
)
from .services import (
    active_membership,
    action_item_response,
    attachment_access_capabilities,
    attachment_response,
    clear_staff_job,
    create_employee,
    create_staff_directory_entry,
    ensure_bootstrap_admin,
    ensure_developer_launcher_user,
    ensure_reference_data,
    ensure_system_rooms,
    list_user_rooms,
    message_engagement_counts,
    message_response,
    record_audit,
    resident_response,
    room_member_user_ids,
    set_staff_job,
    set_staff_position_title,
    set_staff_unit_assignments,
    set_user_role,
    staff_directory_response,
    staff_matches_room_rule,
    issue_existing_staff_login,
    sync_auto_memberships,
    terminate_staff_directory_entry,
    unit_response,
    update_staff_directory_entry,
    user_response,
    validate_position_title,
    validate_unit_assignments,
)


logger = logging.getLogger(__name__)


def _automatic_message_nature(
    *,
    requested_message_type: str,
    user: User,
    body: str,
    has_attachments: bool = False,
    report_image: bool = False,
    action_type: str | None = None,
) -> tuple[MessageNatureDecision, dict[str, Any]]:
    """Resolve message nature without making message delivery depend on AI/network."""

    if requested_message_type == "notice":
        decision = MessageNatureDecision(
            message_type="notice",
            confidence=1.0,
            reason_codes=("admin_notice",),
            scores={},
        )
        source = "admin_notice"
    elif action_type in {"handover", "cooperation", "confirmation"}:
        resolved_type = "handover" if action_type == "handover" else "work_request"
        decision = MessageNatureDecision(
            message_type=resolved_type,
            confidence=1.0,
            reason_codes=(f"linked_action:{action_type}",),
            scores={},
        )
        source = "linked_action"
    else:
        try:
            decision = classify_message_nature(
                body,
                speaker_job_code=user.job_code,
                speaker_job_name=user.job_name,
                speaker_position_title=user.position_title,
                has_attachments=has_attachments,
                report_image=report_image,
            )
            source = "automatic_local"
        except Exception:
            logger.exception(
                "Message nature classification failed; using chat fallback."
            )
            decision = MessageNatureDecision(
                message_type="chat",
                confidence=0.0,
                reason_codes=("classification_error_fallback",),
                scores={},
            )
            source = "automatic_local_fallback"

    metadata = decision.metadata(
        speaker_job_code=user.job_code,
        speaker_job_name=user.job_name,
        speaker_position_title=user.position_title,
        source=source,
        client_hint=requested_message_type,
    )
    return decision, metadata


def _record_message_nature_analysis(
    db: Session,
    *,
    message: Message,
    user: User,
    decision: MessageNatureDecision,
    metadata: dict[str, Any],
    has_attachments: bool = False,
    report_image: bool = False,
) -> None:
    """분류 결과를 원문과 분리해 추가 저장하되 전송 성공과 결합하지 않는다."""

    source = str(metadata.get("source") or "automatic_local_fallback")
    processor_kind = {
        "admin_notice": "admin_control",
        "linked_action": "linked_action",
        "automatic_local": "local_rules",
        "automatic_local_fallback": "system_fallback",
    }.get(source, "system_fallback")
    uncertainties = (
        ["분류 처리 중 오류가 발생하여 일반 대화로 저장했습니다. 확인이 필요합니다."]
        if source == "automatic_local_fallback"
        else []
    )
    try:
        with db.begin_nested():
            append_ai_analysis_snapshot(
                db,
                organization_id=message.organization_id,
                subject_type="message",
                subject_key=str(message.id),
                primary_message_id=message.id,
                analysis_kind="message_nature",
                processor_kind=processor_kind,
                processor_version=str(metadata.get("version") or "unknown"),
                input_payload={
                    "body": message.body,
                    "speaker_job_code": user.job_code,
                    "speaker_job_name": user.job_name,
                    "speaker_position_title": user.position_title,
                    "has_attachments": has_attachments,
                    "report_image": report_image,
                },
                result_payload=metadata,
                confidence=decision.confidence,
                reason_codes=decision.reason_codes,
                uncertainties=uncertainties,
                evidence=[
                    AiAnalysisEvidenceInput(
                        source_type="message",
                        source_message_id=message.id,
                        evidence_role="primary",
                        content_sha256=content_fingerprint(message.body),
                        included_in_prompt=False,
                    )
                ],
                transmitted_external=False,
                created_by_user_id=user.id,
                is_test_data=message.is_test_data,
            )
    except Exception:
        logger.exception(
            "Message nature analysis history could not be stored; "
            "message delivery will continue."
        )


def _message_list_query_options():
    """Batch-load only relationships needed by room and search result lists."""

    return (
        selectinload(Message.room),
        selectinload(Message.sender).selectinload(User.staff),
        selectinload(Message.resident)
        .selectinload(Resident.room)
        .selectinload(RecipientRoom.floor_unit),
        selectinload(Message.resident_links)
        .selectinload(MessageResidentLink.resident)
        .selectinload(Resident.room)
        .selectinload(RecipientRoom.floor_unit),
        selectinload(Message.attachments).selectinload(
            MessageAttachment.text_extraction
        ),
        selectinload(Message.comments).selectinload(MessageComment.author),
        selectinload(Message.action_item)
        .selectinload(ActionItem.assignee_user)
        .selectinload(User.staff),
        selectinload(Message.action_item).selectinload(ActionItem.assignee_unit),
        selectinload(Message.action_item)
        .selectinload(ActionItem.created_by)
        .selectinload(User.staff),
    )


def _search_excerpt(text: str, query: str, *, radius: int = 72) -> str:
    normalized_text = text.strip()
    if not normalized_text:
        return ""
    if not query:
        return normalized_text[: radius * 2].strip()
    offset = normalized_text.casefold().find(query.casefold())
    if offset < 0:
        return normalized_text[: radius * 2].strip()
    start = max(0, offset - radius)
    end = min(len(normalized_text), offset + len(query) + radius)
    excerpt = normalized_text[start:end].strip()
    if start:
        excerpt = f"…{excerpt}"
    if end < len(normalized_text):
        excerpt = f"{excerpt}…"
    return excerpt


def _message_search_matches(
    message: Message,
    query_text: str,
    *,
    include_comments: bool = True,
) -> list[RoomMessageSearchMatchResponse]:
    if not query_text:
        return []
    sources: list[tuple[str, str, str, UUID | None, str | None]] = [
        ("message", "대화 내용", message.body, None, None),
        ("sender", "작성자", message.sender.full_name, None, None),
    ]
    if message.action_item is not None:
        action_text = {
            "assigned": "업무가 지정되었습니다.",
            "acknowledged": "업무 내용을 확인했습니다.",
            "in_progress": "업무 처리를 시작했습니다.",
            "completed": "업무 처리를 완료했습니다.",
        }.get(message.action_item.status)
        if action_text:
            sources.append(("message", "업무 상태", action_text, None, None))
    if message.resident is not None:
        sources.append(
            ("resident", "어르신", message.resident.display_name, None, None)
        )
    sources.extend(
        (
            "resident",
            "어르신",
            link.resident.display_name,
            None,
            None,
        )
        for link in message.resident_links
        if link.status == "confirmed" and resident_link_is_current(link, message)
    )
    if include_comments:
        sources.extend(
            ("comment", "답글", comment.body, None, None) for comment in message.comments
        )
    for attachment in message.attachments:
        extraction = attachment.text_extraction
        if extraction is None:
            continue
        text = attachment_evidence_text(attachment)
        if not text.strip():
            continue
        is_audio = attachment.mime_type.startswith("audio/")
        sources.append(
            (
                "audio" if is_audio else "image",
                "음성 받아쓰기" if is_audio else "이미지 판독문",
                text,
                attachment.id,
                attachment.original_name,
            )
        )

    matches: list[RoomMessageSearchMatchResponse] = []
    seen: set[tuple[str, UUID | None, str]] = set()
    for source_type, label, text, attachment_id, attachment_name in sources:
        if query_text not in text.casefold():
            continue
        excerpt = _search_excerpt(text, query_text)
        key = (source_type, attachment_id, excerpt)
        if key in seen:
            continue
        seen.add(key)
        matches.append(
            RoomMessageSearchMatchResponse(
                message_id=message.id,
                source_type=source_type,
                source_label=label,
                excerpt=excerpt,
                attachment_id=attachment_id,
                attachment_name=attachment_name,
            )
        )
    return matches[:4]


def _as_utc(value):
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


MAX_RESIDENT_SYNC_FILE_BYTES = 2 * 1024 * 1024
FINALIST_CAREFOR_ROSTER_BLOCK_DETAIL = (
    "본선용 DEV에서는 코드화된 합성 어르신 자료만 사용하므로 "
    "케어포 명단을 불러올 수 없습니다."
)


def _finalist_carefor_roster_blocked() -> bool:
    return (
        settings.environment == "development"
        and settings.database_schema == "smcodi_finalist"
    )


def _require_carefor_roster_allowed() -> None:
    if _finalist_carefor_roster_blocked():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=FINALIST_CAREFOR_ROSTER_BLOCK_DETAIL,
        )


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
    **{mime_type: extension for extension, mime_type in DOCUMENT_MIME_BY_EXTENSION.items()},
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


def _residents_for_room(
    db: Session,
    room: Room,
    *,
    resident_id: UUID | None = None,
    resident_ids: Sequence[UUID] | None = None,
) -> list[Resident]:
    selected_ids = list(
        dict.fromkeys(
            [
                *([resident_id] if resident_id is not None else []),
                *(resident_ids or []),
            ]
        )
    )
    if len(selected_ids) > 50:
        raise HTTPException(
            status_code=422,
            detail="한 메시지에는 어르신을 최대 50명까지 선택할 수 있습니다.",
        )
    if selected_ids:
        return [
            resident
            for selected_id in selected_ids
            if (resident := _resident_for_room(db, room, selected_id)) is not None
        ]
    return []


def _single_scoped_resident_for_room(db: Session, room: Room) -> Resident | None:

    # 직원 대화방에 연결되는 활성 어르신이 정확히 한 명일 때만 기존
    # 범위 설정을 재사용한다. 전체방 또는 두 명 이상인 범위에서는 이름이나
    # 문맥을 추측하지 않고 관리자 검토 대상으로 남긴다.
    scoped_query = select(Resident).where(
        Resident.organization_id == room.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
    )
    if room.resident_scope == "floor":
        scope_unit_id = room.resident_scope_unit_id or room.scope_unit_id
        if scope_unit_id is None:
            return None
        scoped_query = scoped_query.join(
            RecipientRoom, Resident.room_id == RecipientRoom.id
        ).where(RecipientRoom.floor_unit_id == scope_unit_id)
    elif room.resident_scope in {"facility", "daycare", "homecare"}:
        scoped_query = scoped_query.where(
            Resident.service_type == room.resident_scope
        )
    else:
        return None
    candidates = db.scalars(scoped_query.order_by(Resident.id).limit(2)).all()
    return candidates[0] if len(candidates) == 1 else None


def _reply_snapshot_for_room(
    db: Session,
    *,
    room: Room,
    reply_to_message_id: UUID | None,
) -> dict[str, str] | None:
    if reply_to_message_id is None:
        return None
    source = db.get(Message, reply_to_message_id)
    if (
        source is None
        or source.organization_id != room.organization_id
        or source.room_id != room.id
    ):
        raise HTTPException(
            status_code=422,
            detail="같은 채팅방의 메시지에만 답장할 수 있습니다.",
        )
    if source.lifecycle_status == "recalled":
        raise HTTPException(
            status_code=422,
            detail="회수된 메시지에는 답장할 수 없습니다.",
        )
    return {
        "message_id": str(source.id),
        "sender_name": source.sender.full_name,
        "body": source.body,
        "created_at": _as_utc(source.created_at).isoformat(),
    }


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
    aliases = {alias for alias in (display_name, without_test_label) if len(alias) >= 2}
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


VALID_RESIDENT_SERVICE_CONTEXTS = frozenset({"facility", "daycare", "homecare"})


def _message_resident_service_context(message: Message) -> str | None:
    if (
        message.resident is not None
        and message.resident.service_type in VALID_RESIDENT_SERVICE_CONTEXTS
    ):
        return message.resident.service_type
    room_scope = message.room.resident_scope if message.room is not None else None
    if room_scope in VALID_RESIDENT_SERVICE_CONTEXTS:
        return room_scope
    if room_scope == "floor":
        return "facility"
    return None


def _active_message_residents(db: Session, message: Message) -> list[Resident]:
    statement = select(Resident).where(
        Resident.organization_id == message.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
        Resident.service_type.in_(VALID_RESIDENT_SERVICE_CONTEXTS),
    )
    service_context = _message_resident_service_context(message)
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


def _sync_message_resident_candidates(
    db: Session,
    *,
    message: Message,
    text: str,
    source: str,
    extraction: AttachmentTextExtraction | None = None,
    staff_reviewed: bool = False,
) -> list[MessageResidentLink]:
    # A staff decision may arrive while OCR is running. Read its current
    # suppression flag under the same message lock used by the review route.
    db.flush()
    db.execute(select(Message.id).where(Message.id == message.id).with_for_update())
    db.refresh(message, ["extra_data", "resident_id"])
    review = resident_review_metadata(message)
    if review.get("suppress_automatic"):
        return []
    if db.scalar(select(MessageResidentLink.id).where(MessageResidentLink.message_id == message.id,
            MessageResidentLink.source == "manual", MessageResidentLink.status == "confirmed")):
        return []
    if source == "text_exact" and message.resident_id is None and message.room.kind == "custom":
        # A system floor/team room with one current resident is still a staff
        # room. Only an explicitly scoped custom room gets resident priority.
        scoped = _single_scoped_resident_for_room(db, message.room)
        if scoped is not None:
            message.resident_id = scoped.id
            return [_confirm_manual_resident_link(db, message=message, resident=scoped, origin="room")]
    if source == "ocr_exact" and not staff_reviewed:
        text = visible_image_text(extraction)
    if (
        source not in {"text_exact", "ocr_exact", "audio_transcript"}
        or not text.strip()
    ):
        return []
    residents = _active_message_residents(db, message)
    aliases: dict[str, list[Resident]] = {}
    for resident in residents:
        for alias in _resident_name_aliases(resident):
            aliases.setdefault(alias, []).append(resident)

    matched = {
        owner.id: owner
        for alias, owners in aliases.items()
        if _text_mentions_alias(text, alias)
        for owner in owners
    }
    # A normal staff message may explicitly name several different residents.
    # Each exact, roster-unique display name is safe to confirm independently;
    # duplicate names remain candidates for a person to distinguish.
    auto_exact_ids = {
        resident.id
        for resident in matched.values()
        if source == "text_exact"
        and _text_mentions_alias(text, resident.display_name)
        and sum(
            candidate.display_name == resident.display_name
            for candidate in residents
        )
        == 1
    }
    if source == "audio_transcript":
        for spelling in find_spelling_candidates(
            text,
            preferred_terms=list(aliases),
        ):
            owners = aliases.get(spelling.get("candidate", ""), [])
            if len(owners) == 1:
                matched.setdefault(owners[0].id, owners[0])
    if extraction is not None:
        attempt_number = int(db.scalar(select(func.max(AttachmentTextExtractionAttempt.attempt_number)).where(AttachmentTextExtractionAttempt.extraction_id == extraction.id)) or 0) + 1
        extraction.suggestion_details = [
            *(row for row in extraction.suggestion_details or [] if row.get("kind") != "resident_candidate_evidence"),
            {"kind": "resident_candidate_evidence", "source": source, "attempt_number": attempt_number,
             **({"staff_reviewed_text": text.strip()} if staff_reviewed else {}),
             "text_sha256": sha256(text.strip().encode()).hexdigest(),
             "matches": [{"resident_id": str(resident_id), "matched_name": alias}
                         for resident_id in matched for alias, owners in aliases.items()
                         if any(owner.id == resident_id for owner in owners) and _text_mentions_alias(text, alias)]},
        ]
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
                status="confirmed" if resident.id in auto_exact_ids else "candidate",
            )
            db.add(link)
            if resident.id in auto_exact_ids:
                if message.resident_id is None:
                    message.resident_id = resident.id
                record_audit(db, actor_id=message.sender_id, action="message_resident_link.auto_connected",
                    target_type="message", target_id=message.id,
                    details={"resident_id": str(resident.id), "source": "text_exact"})
        elif resident.id in auto_exact_ids and link.status != "confirmed":
            link.status = "confirmed"
            link.reviewed_by_id = None
            link.reviewed_at = None
            if message.resident_id is None:
                message.resident_id = resident.id
        links.append(link)
    db.flush()
    return links


def _finalize_message_resident_links_from_reviewed_extractions(
    db: Session,
    *,
    message: Message,
    reviewer: User,
) -> dict[str, int | bool]:
    """Apply every staff-confirmed image text without waiting for unrelated files.

    Raw OCR and audio transcripts may create candidates, but only immutable
    message text and staff-confirmed image text can promote links here.  This
    keeps a late transcript from reintroducing a name after the final text was
    saved.
    """

    db.flush()
    db.execute(select(Message.id).where(Message.id == message.id).with_for_update())
    db.refresh(message, ["extra_data", "resident_id", "resident_links"])
    review = resident_review_metadata(message)

    authoritative_parts: list[tuple[str, str, UUID | None]] = []
    if message.body.strip():
        authoritative_parts.append((message.body, "text_exact", None))

    image_attachments = [
        attachment
        for attachment in message.attachments
        if attachment.mime_type in IMAGE_MIME_TYPES
        and attachment.text_extraction is not None
    ]
    image_attachment_ids = [attachment.id for attachment in image_attachments]
    latest_confirmations: dict[UUID, OcrCorrectionEvent] = {}
    if image_attachment_ids:
        events = db.scalars(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.attachment_id.in_(image_attachment_ids),
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(
                OcrCorrectionEvent.attachment_id,
                OcrCorrectionEvent.created_at.desc(),
                OcrCorrectionEvent.id.desc(),
            )
        ).all()
        for event in events:
            if event.attachment_id is not None:
                latest_confirmations.setdefault(event.attachment_id, event)

    staff_text_hashes = dict(review.get("staff_final_text_hashes") or {})
    for attachment in image_attachments:
        extraction = attachment.text_extraction
        latest = latest_confirmations.get(attachment.id)
        staff_text = (
            latest.corrected_text
            if latest is not None
            else extraction.reviewed_text
            if extraction.status == "reviewed"
            else None
        )
        if staff_text and staff_text.strip():
            authoritative_parts.append((staff_text.strip(), "ocr_exact", attachment.id))
            staff_text_hashes[str(attachment.id)] = sha256(
                staff_text.strip().encode("utf-8")
            ).hexdigest()

    aliases: dict[str, list[Resident]] = {}
    residents = _active_message_residents(db, message)
    for resident in residents:
        for alias in _resident_name_aliases(resident):
            aliases.setdefault(alias, []).append(resident)

    unique_matches: dict[UUID, tuple[Resident, str]] = {}
    ambiguous_matches: dict[UUID, tuple[Resident, str]] = {}
    for text, source, _attachment_id in authoritative_parts:
        for alias, owners in aliases.items():
            if not _text_mentions_alias(text, alias):
                continue
            if len(owners) == 1:
                current = unique_matches.get(owners[0].id)
                if current is None or current[1] != "text_exact":
                    unique_matches[owners[0].id] = (owners[0], source)
            else:
                for resident in owners:
                    ambiguous_matches.setdefault(resident.id, (resident, source))

    explicitly_excluded = {
        UUID(value)
        for value in review.get("manual_excluded_resident_ids", [])
        if isinstance(value, str)
        and re.fullmatch(
            r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}",
            value,
        )
    }
    unrelated = bool(review.get("unrelated")) or review.get("decision") == "unrelated"
    if unrelated:
        unique_matches.clear()
        ambiguous_matches.clear()

    existing = {
        link.resident_id: link
        for link in db.scalars(
            select(MessageResidentLink)
            .where(MessageResidentLink.message_id == message.id)
            .with_for_update()
        ).all()
    }
    before = {
        resident_id: (link.status, link.source, link.reviewed_by_id)
        for resident_id, link in existing.items()
    }
    reviewed_at = utcnow()

    for resident_id, (resident, source) in unique_matches.items():
        if resident_id in explicitly_excluded:
            continue
        link = existing.get(resident_id)
        if link is None:
            link = MessageResidentLink(
                organization_id=message.organization_id,
                message_id=message.id,
                resident_id=resident_id,
                source=source,
                status="confirmed",
                reviewed_by_id=reviewer.id,
                reviewed_at=reviewed_at,
            )
            db.add(link)
            existing[resident_id] = link
        elif not (link.source == "manual" and link.status == "confirmed"):
            link.source = source
            link.status = "confirmed"
            link.reviewed_by_id = reviewer.id
            link.reviewed_at = reviewed_at

    for resident_id, (resident, source) in ambiguous_matches.items():
        if resident_id in unique_matches or resident_id in explicitly_excluded:
            continue
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
            existing[resident_id] = link
        elif link.source != "manual" and link.status != "confirmed":
            link.source = source
            link.status = "candidate"
            link.reviewed_by_id = None
            link.reviewed_at = None

    current_match_ids = set(unique_matches) | set(ambiguous_matches)
    for resident_id, link in existing.items():
        if (
            resident_id not in current_match_ids
            and link.source != "manual"
            and link.status == "candidate"
        ):
            link.status = "rejected"
            link.reviewed_by_id = reviewer.id
            link.reviewed_at = reviewed_at

    db.flush()
    db.expire(message, ["resident_links"])
    _refresh_work_item_residents(db, message)
    after_links = list(message.resident_links)
    after = {
        link.resident_id: (link.status, link.source, link.reviewed_by_id)
        for link in after_links
    }
    link_state_changed = before != after
    hashes_changed = staff_text_hashes != dict(review.get("staff_final_text_hashes") or {})
    revision = int(review.get("revision", 0) or 0)
    if link_state_changed or hashes_changed:
        revision += 1
        message.extra_data = {
            **(message.extra_data or {}),
            "resident_review": {
                **review,
                "revision": revision,
                "staff_final_text_hashes": staff_text_hashes,
                "suppress_automatic": True,
                "staff_finalized_at": reviewed_at.isoformat(),
            },
        }
        confirmed_count = sum(
            1
            for resident_id, state in after.items()
            if state[0] == "confirmed" and before.get(resident_id, (None,))[0] != "confirmed"
        )
        rejected_count = sum(
            1
            for resident_id, state in after.items()
            if state[0] == "rejected" and before.get(resident_id, (None,))[0] != "rejected"
        )
        record_audit(
            db,
            actor_id=reviewer.id,
            action="message_resident_links.finalized_from_text_review",
            target_type="message",
            target_id=message.id,
            details={
                "revision": revision,
                "confirmed_count": confirmed_count,
                "rejected_count": rejected_count,
                "unique_match_count": len(unique_matches),
                "ambiguous_match_count": len(ambiguous_matches),
                "staff_text_count": len(staff_text_hashes),
            },
        )
    else:
        confirmed_count = 0
        rejected_count = 0
    _search_summary_cache.clear()
    _adaptive_search_summary_cache.clear()
    return {
        "finalized": bool(staff_text_hashes),
        "confirmed_count": confirmed_count,
        "rejected_count": rejected_count,
        "revision": revision,
    }


def _confirm_manual_resident_link(
    db: Session,
    *,
    message: Message,
    resident: Resident | None,
    origin: str = "uploader",
) -> MessageResidentLink | None:
    if resident is None:
        return None
    message.extra_data = {**(message.extra_data or {}), "resident_review": {
        **resident_review_metadata(message), "origin": origin, "suppress_automatic": True}}
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
    message = db.get(Message, message_id)
    if message is None:
        return []
    links = db.scalars(
        select(MessageResidentLink)
        .where(
            MessageResidentLink.message_id == message_id,
            MessageResidentLink.status == "confirmed",
        )
        .order_by(MessageResidentLink.created_at, MessageResidentLink.id)
    ).all()
    return [link for link in links if resident_link_is_current(link, message)]


def _has_pending_message_resident_candidates(
    db: Session,
    message_id: UUID,
) -> bool:
    message = db.get(Message, message_id)
    return bool(message and any(link.status == "candidate" and resident_link_is_current(link, message) for link in message.resident_links))


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


def _require_active_message(message: Message, action_label: str) -> None:
    if message.lifecycle_status == "recalled":
        raise HTTPException(
            status_code=409,
            detail=f"회수한 메시지는 {action_label}할 수 없습니다.",
        )


def _interaction_is_test_data(
    user: User,
    *,
    room: Room | None = None,
    message: Message | None = None,
) -> bool:
    """Classify an interaction from explicit test identities, not the environment.

    Older development databases marked ordinary rooms as test data simply because
    they were created outside production.  Those room flags cannot distinguish a
    real employee conversation from the reviewer demo, so new message content is
    classified from the acting staff account instead.  The actor also wins over
    a legacy message flag so a real employee's follow-up is never downgraded to
    test data by an old development row.
    """

    # Kept in the signature for existing call sites and future room/message policy.
    del room, message
    return getattr(user, "_reviewer_experience", None) is not None or bool(
        user.staff is not None and user.staff.is_test_data
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
        message.resident_id = (
            confirmed_links[0].resident_id if confirmed_links else None
        )
    item = db.scalar(select(WorkItem).where(WorkItem.source_message_id == message.id))
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
        raise HTTPException(
            status_code=422, detail="담당 직원 또는 담당 팀을 지정해 주세요."
        )
    if payload.assignee_user_id is not None and payload.assignee_unit_id is not None:
        raise HTTPException(
            status_code=422, detail="담당 직원과 담당 팀 중 하나만 선택해 주세요."
        )
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


def _require_work_item_attachment_reviews(db: Session, item: WorkItem) -> None:
    if required_attachment_reviews_current(db, item.source_message.attachments):
        return
    raise HTTPException(
        status_code=409,
        detail=ATTACHMENT_STAFF_REVIEW_REQUIRED_DETAIL,
    )


def _work_item_ai_snapshot(db: Session, item: WorkItem) -> dict:
    snapshot = dict(item.source_snapshot)
    # Rebuild attachment evidence from current review state, not a historical cache.
    snapshot["body"] = item.source_message.body
    snapshot.pop("text_extraction_attachment_ids", None)
    fingerprint = review_fingerprint(item.source_message.attachments)
    snapshot["attachment_review_fingerprint"] = fingerprint
    item.source_snapshot = {**item.source_snapshot, "attachment_review_fingerprint": fingerprint}
    extra_sections: list[str] = []
    extraction_attachment_ids: list[str] = []
    for attachment in item.source_message.attachments:
        extraction = attachment.text_extraction
        if extraction is None or extraction.status not in {"completed", "reviewed"}:
            continue
        extracted_text = attachment_evidence_text(attachment)
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
        if document_type in DAILY_DOCUMENT_TYPES and document_type in proposal_by_type
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
    if not work_item_review_current(item):
        return []
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
        or not required_attachment_reviews_current(
            db, item.source_message.attachments
        )
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
        source_snapshot={**item.source_snapshot, "body": item.source_message.body,
            "attachment_ids": [str(attachment.id) for attachment in item.source_message.attachments
                if not requires_staff_review(attachment) or attachment_evidence_text(attachment)],
            "text_extraction_attachment_ids": [str(attachment.id) for attachment in item.source_message.attachments
                if attachment_evidence_text(attachment)]},
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
        ai_state=item.ai_state if work_item_review_current(item) else "not_requested",
        ai_suggestion=item.ai_payload if work_item_review_current(item) else None,
        ai_generator=item.ai_generator,
        ai_generated_at=_as_utc(item.ai_generated_at) if item.ai_generated_at else None,
        confirmed_record=item.confirmed_payload if work_item_review_current(item) else None,
        confirmed_by_name=item.confirmed_by.full_name if item.confirmed_by else None,
        confirmed_at=_as_utc(item.confirmed_at) if item.confirmed_at else None,
        document_drafts=[
            _work_item_document_draft_response(draft)
            for draft in (_current_work_item_document_drafts(db, item) if work_item_review_current(item) else [])
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
        raise HTTPException(
            status_code=403, detail="이 업무 항목을 처리할 수 없습니다."
        )
    _require_active_message(item.source_message, "업무 처리")
    sender = item.source_message.sender
    if processor.role != "admin" and (
        processor.business is None
        or sender.business is None
        or sender.business.id != processor.business.id
    ):
        raise HTTPException(
            status_code=403, detail="이 업무 항목을 처리할 수 없습니다."
        )
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
        .where(
            WorkItem.organization_id == processor.organization_id,
            Message.lifecycle_status == "active",
        )
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


_PROCESSOR_REVIEW_DOCUMENT_TYPES = {
    *ASSESSMENT_DOCUMENT_TYPES,
    "care_plan",
    "care_plan_evaluation",
}


def _work_item_requires_processor_review(db: Session, item: WorkItem) -> bool:
    """Return only exceptions that need a record processor's attention.

    Every employee message keeps a WorkItem for lossless follow-up and audit,
    but ordinary resident-linked conversation must not inflate the visible
    administrator queue.
    """

    message = item.source_message
    if item.resident_id is None:
        return True
    if _has_pending_message_resident_candidates(db, item.source_message_id):
        return True
    if (
        message.action_item is not None
        and message.action_item.action_type == "confirmation"
        and message.action_item.status != "completed"
    ):
        return True
    if any(
        attachment.text_extraction is not None
        and attachment.text_extraction.status == "failed"
        and (not attachment.mime_type.startswith("image/") or photo_reading_status(attachment) == "failed")
        for attachment in message.attachments
    ):
        return True
    latest_review_decision = db.scalar(
        select(OcrCorrectionEvent.decision)
        .where(OcrCorrectionEvent.source_message_id == item.source_message_id)
        .order_by(OcrCorrectionEvent.created_at.desc(), OcrCorrectionEvent.id.desc())
        .limit(1)
    )
    if latest_review_decision == "needs_review":
        return True
    return bool(
        set(item.document_types or []).intersection(_PROCESSOR_REVIEW_DOCUMENT_TYPES)
    )


async def _store_attachment(
    db: Session,
    *,
    upload: UploadFile,
    upload_ordinal: int,
    message: Message,
    user: User,
) -> tuple[MessageAttachment, Path]:
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
    try:
        validated = validate_uploaded_attachment(
            upload.filename,
            upload.content_type,
            content,
        )
    except AttachmentValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    mime_type = validated.mime_type
    extension = validated.extension
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
        original_name=validated.original_name,
        mime_type=mime_type,
        size_bytes=len(content),
        upload_ordinal=upload_ordinal,
        sha256=sha256(content).hexdigest(),
        created_at=utcnow(),
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
    is_document = attachment.mime_type in DOCUMENT_MIME_TYPES
    provider = settings.stt_provider if is_audio else settings.ocr_provider
    model_name = settings.stt_model if is_audio else settings.ocr_model
    if is_document:
        provider, model_name = "internal_document", "local_document_reader"
    elif not is_audio:
        try:
            requested_image = requested_image_runtime()
            provider, model_name = requested_image.provider, requested_image.model
        except Exception as exc:
            # A broken model setting must not roll back the chat/photo upload.
            # The worker resolves the central configuration again and records
            # a sanitized failure; it never uses another hardcoded model.
            provider, model_name = "unavailable", ""
            logger.warning("Image model configuration unavailable: error_type=%s", type(exc).__name__)
    if attachment.id is None:
        db.flush()

    if requires_staff_review(attachment):
        item = db.scalar(
            select(WorkItem).where(WorkItem.source_message_id == attachment.message_id)
        )
        if item is not None and item.confirmed_at is None:
            item.ai_state = "not_requested"
            item.ai_payload = None
            item.ai_generator = None
            item.ai_generated_at = None
            item.document_types = []
            if item.status == "in_review":
                item.status = "pending"
            snapshot = dict(item.source_snapshot or {})
            snapshot.pop("attachment_review_fingerprint", None)
            item.source_snapshot = snapshot
            for draft in db.scalars(
                select(WorkItemDocumentDraft).where(
                    WorkItemDocumentDraft.work_item_id == item.id,
                    WorkItemDocumentDraft.is_current.is_(True),
                )
            ):
                draft.is_current = False
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
        if extraction.status in {"completed", "reviewed", "failed", "no_text", "not_required", "unsupported"}:
            previous_attempt_number = int(
                db.scalar(
                    select(
                        func.max(AttachmentTextExtractionAttempt.attempt_number)
                    ).where(
                        AttachmentTextExtractionAttempt.extraction_id == extraction.id
                    )
                )
                or 0
            )
            db.add(
                AttachmentTextExtractionAttempt(
                    organization_id=extraction.organization_id,
                    extraction_id=extraction.id,
                    attachment_id=attachment.id,
                    attempt_number=previous_attempt_number + 1,
                    status=extraction.status,
                    provider=extraction.provider,
                    model_name=extraction.model_name,
                    extracted_text=extraction.extracted_text,
                    suggested_service_context=extraction.suggested_service_context,
                    suggestion_details=extraction.suggestion_details,
                    reviewed_text=extraction.reviewed_text,
                    error_message=extraction.error_message,
                    requested_by_id=extraction.requested_by_id,
                    reviewed_by_id=extraction.reviewed_by_id,
                    started_at=extraction.started_at,
                    completed_at=extraction.completed_at,
                    reviewed_at=extraction.reviewed_at,
                )
            )
        extraction.status = "pending"
        extraction.provider = provider
        extraction.model_name = model_name
        extraction.extracted_text = None
        if not is_audio and not is_document:
            extraction.original_extracted_text = None
        extraction.suggested_text = None
        extraction.suggested_service_context = None
        extraction.suggestion_details = None
        extraction.reviewed_text = None
        extraction.visual_signature = None
        extraction.error_message = None
        extraction.requested_by_id = requested_by.id
        extraction.reviewed_by_id = None
        extraction.started_at = None
        extraction.completed_at = None
        extraction.reviewed_at = None
    return extraction


def _attachment_roster_evidence(
    db: Session,
    *,
    attachment: MessageAttachment,
) -> tuple[list[ResidentRosterEntry], list[CorrectionEvidence]]:
    residents = list(
        db.scalars(
            select(Resident).where(
                Resident.organization_id == attachment.organization_id,
                Resident.is_active.is_(True),
                Resident.status == "active",
                Resident.service_type.in_(VALID_RESIDENT_SERVICE_CONTEXTS),
            )
        )
    )
    roster_entries = [
        ResidentRosterEntry(
            name=resident.display_name,
            service_type=resident.service_type,
        )
        for resident in residents
    ]
    events = list(
        db.scalars(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.organization_id == attachment.organization_id,
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(
                OcrCorrectionEvent.created_at.desc(),
                OcrCorrectionEvent.id.desc(),
            )
            .limit(300)
        )
    )
    source_message_ids = {
        event.source_message_id
        for event in events
        if event.source_message_id is not None
    }
    source_messages = {
        message.id: message
        for message in db.scalars(
            select(Message).where(Message.id.in_(source_message_ids))
        ).all()
    }
    source_attachment_ids = {
        event.attachment_id for event in events if event.attachment_id is not None
    }
    source_attachments = {
        source_attachment.id: source_attachment
        for source_attachment in db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.id.in_(source_attachment_ids)
            )
        ).all()
    }
    coordinate_review_ids = {
        event.coordinate_review_id
        for event in events
        if event.coordinate_review_id is not None
    }
    coordinate_reviews = {
        review.id: review
        for review in db.scalars(
            select(AttachmentCoordinateReview).where(
                AttachmentCoordinateReview.id.in_(coordinate_review_ids)
            )
        ).all()
    }
    evidences: list[CorrectionEvidence] = []
    for event in events:
        source_message = source_messages.get(event.source_message_id)
        source_attachment = source_attachments.get(event.attachment_id)
        if (
            source_attachment is None
            or source_attachment.mime_type not in IMAGE_MIME_TYPES
        ):
            continue
        review = coordinate_reviews.get(event.coordinate_review_id)
        status_regions: list[CoordinateRegion] = []
        if review is not None:
            for raw_region in review.regions or []:
                try:
                    region = CoordinateRegion.model_validate(raw_region)
                except ValidationError:
                    continue
                if region.role == "status":
                    status_regions.append(region)
        for pair in event.correction_pairs or []:
            recognized = str(pair.get("recognized_text", "")).strip()
            corrected = str(pair.get("corrected_text", "")).strip()
            if not recognized or not corrected or recognized == corrected:
                continue
            section = (
                str(pair.get("section"))
                if pair.get("section") is not None
                else None
            )
            layout_role = (
                str(pair.get("layout_role"))
                if pair.get("layout_role") is not None
                else None
            )
            column_role = (
                str(pair.get("column_role"))
                if pair.get("column_role") is not None
                else None
            )
            document_template = (
                str(pair.get("document_template"))
                if pair.get("document_template") is not None
                else None
            )
            content_type = str(pair.get("content_type", "general"))
            matching_status_region = next(
                (
                    region
                    for region in status_regions
                    if corrected in {"확인", "교체"}
                    and recognized in region.raw_text
                    and corrected in region.corrected_text
                ),
                None,
            )
            if matching_status_region is not None:
                content_type = "status"
                section = matching_status_region.section_role or "status_checklist"
                layout_role = "status_cell"
                column_role = "status"
                document_template = (
                    matching_status_region.document_template
                    or (review.document_template if review is not None else None)
                )
            evidences.append(
                CorrectionEvidence(
                    event_id=str(event.id),
                    recognized_text=recognized,
                    corrected_text=corrected,
                    content_type=content_type,
                    context_text=str(
                        pair.get("context_text") or event.context_text or ""
                    ),
                    source_writer_id=(
                        str(event.source_writer_id)
                        if event.source_writer_id is not None
                        else None
                    ),
                    visual_signature=event.visual_signature,
                    service_context=(
                        _message_resident_service_context(source_message)
                        if source_message is not None
                        else None
                    ),
                    source_kind="image",
                    section=section,
                    layout_role=layout_role,
                    column_role=column_role,
                    document_template=document_template,
                )
            )
    return roster_entries, evidences


def _build_attachment_roster_aware_draft(
    db: Session,
    *,
    attachment: MessageAttachment,
    raw_text: str,
    visual_signature: list[float] | None,
):
    roster_entries, evidences = _attachment_roster_evidence(
        db,
        attachment=attachment,
    )
    draft = build_roster_aware_draft(
        raw_text,
        roster_entries=roster_entries,
        room_service_context=_message_resident_service_context(attachment.message),
        source_writer_id=attachment.message.sender_id,
        visual_signature=visual_signature,
        evidences=evidences,
    )
    scoped_names = [
        entry.name
        for entry in roster_entries
        if draft.service_context is None or entry.service_type == draft.service_context
    ]
    return draft, list(dict.fromkeys(scoped_names)), evidences


def _with_handwriting_preprocessing_details(
    details: list[dict[str, Any]] | None,
    summary: dict[str, object] | None,
) -> list[dict[str, Any]] | None:
    rows = [
        dict(item)
        for item in (details or [])
        if item.get("kind") != "handwriting_preprocessing"
    ]
    if summary is not None:
        rows.append({"kind": "handwriting_preprocessing", **summary})
    return rows or None


_DEFAULT_TRANSCRIBE_AUDIO = transcribe_audio


def _transcribe_attachment_audio(
    path: Path,
    *,
    mime_type: str,
) -> SttTranscriptionResult:
    # 기존 유지보수·회귀 코드가 text-only 함수를 교체하는 계약은 보존한다.
    # 일반 실행에서는 품질 메타데이터를 함께 받는 새 경로를 사용한다.
    if transcribe_audio is not _DEFAULT_TRANSCRIBE_AUDIO:
        return SttTranscriptionResult(
            text=transcribe_audio(path, mime_type=mime_type),
            model=settings.stt_model,
            processing_seconds=None,
            normalization={},
            audio_quality={
                "status": "unknown",
                "reading_pace": "unknown",
                "message": "음성 전사가 준비됐습니다.",
                "reasons": [],
                "metrics": {},
            },
        )
    return transcribe_audio_with_metadata(path, mime_type=mime_type)


def _audio_quality_from_details(
    details: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    for item in details or []:
        if item.get("kind") == "audio_quality":
            return {key: value for key, value in item.items() if key != "kind"}
    return None


def _run_attachment_text_extraction_impl(attachment_id: UUID, attempt_context: dict | None = None) -> None:
    with SessionLocal() as db, ExitStack() as image_context:
        def optional_name_candidates(*args, **kwargs):
            try:
                return extract_report_name_candidates(*args, **kwargs)
            except OcrError:
                # A bounded optional name scan must not discard valid OCR.
                # Resident links below use only exact names in displayed text.
                return []
        attachment = db.get(MessageAttachment, attachment_id)
        extraction = db.scalar(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id == attachment_id,
                AttachmentTextExtraction.status == "pending",
            ).with_for_update()
        )
        if attachment is None or extraction is None:
            return
        extraction.status = "processing"
        extraction.started_at = utcnow()
        attempt_started_at = extraction.started_at
        if attempt_context is not None:
            attempt_context["started_at"] = attempt_started_at
        extraction.error_message = None
        db.commit()
        target = (
            Path(settings.upload_dir).resolve() / attachment.storage_key
        ).resolve()
        image_has_text: bool | None = None
        preprocessing_summary: dict[str, object] | None = None
        audio_quality_detail: dict[str, Any] | None = None
        image_runtime_detail: dict[str, Any] = {"kind": "image_ocr_runtime", "model_call_count": 0}
        ocr_target = target
        def attempt_is_current():
            with db.no_autoflush:
                active = db.scalar(select(AttachmentTextExtraction.id).where(
                    AttachmentTextExtraction.id == extraction.id,
                    AttachmentTextExtraction.status == "processing",
                    AttachmentTextExtraction.started_at == attempt_started_at,
                ).with_for_update())
            if active is None:
                db.rollback()
                return False
            return True
        try:
            if (
                target.parent != Path(settings.upload_dir).resolve()
                or not target.is_file()
            ):
                raise OcrError("판독할 원본 첨부파일을 찾을 수 없습니다.")
            if attachment.mime_type in IMAGE_MIME_TYPES:
                image_has_text = image_likely_contains_text(target)
                if not image_has_text:
                    raise ImageOcrFailure("no_text_detected", "사진에서 판독할 글자를 확인하지 못해 건너뛰었습니다. 글씨가 있다면 다시 판독해 주세요.")
                retrying = bool(extraction.attempts)
                if (
                    image_has_text
                    and settings.ocr_bleed_through_preprocessing_enabled
                ):
                    preprocessing = prepare_handwriting_for_ocr(
                        target,
                        derivative_dir=(
                            Path(settings.upload_dir).resolve()
                            / ".ocr-preprocessed"
                        ),
                        derivative_name=(
                            f"{Path(attachment.storage_key).stem}.png"
                        ),
                    )
                    ocr_target = preprocessing.selected_path
                    preprocessing_summary = {
                        **preprocessing.public_summary(),
                        "output_blocked": False,
                        "retry_count": 1 if retrying else 0,
                        "model_call_limit": 3 if retrying else 1,
                    }
                geometry_target = Path(settings.upload_dir).resolve() / ".ocr-preprocessed" / f"{target.stem}.geometry.png"
                image_runtime_detail.update(prepare_image_geometry(target if retrying else ocr_target, geometry_target))
                ocr_target = geometry_target
                runtime = image_context.enter_context(attachment_image_runtime(image_runtime_detail, retry=retrying))
                if not attempt_is_current():
                    return
                extraction.provider, extraction.model_name = runtime.provider, runtime.model
                db.commit()
                if retrying or preprocessing_summary is None:
                    result = extract_report_text(ocr_target, room_name=attachment.message.room.name, resident_name=None)
                else:
                    result = extract_handwriting_text(
                        ocr_target,
                        room_name=attachment.message.room.name,
                        resident_name=None,
                        expected_line_count=preprocessing.metrics.estimated_front_line_count,
                    )
                if not result.strip():
                    raise ImageOcrFailure("empty_result", "판독 결과를 만들지 못했습니다. 보정된 사진이나 이미지 대체 모델로 다시 판독해 주세요.")
                if is_pathological_handwriting_output(result):
                    raise ImageOcrFailure("output_blocked", "반복되는 판독 결과를 제외했습니다. 보정된 사진으로 다시 판독해 주세요.")
                resident_link_source = "ocr_exact"
            elif attachment.mime_type in AUDIO_MIME_TYPES:
                # 전체 이용자 명단과 업무용어를 한 번에 Whisper에 주입하면
                # 실제 검증 음성에서 이름 회상률이 낮아졌다. 원 받아쓰기는
                # 편향 없이 보존하고 명단·기관 교정 기억은 응답 후보와 AI
                # 검토 단계에서만 사용한다.
                transcription = _transcribe_attachment_audio(
                    target,
                    mime_type=attachment.mime_type,
                )
                result = transcription.text
                audio_quality_detail = {
                    "kind": "audio_quality",
                    **transcription.audio_quality,
                    "processing_seconds": transcription.processing_seconds,
                    "model": transcription.model,
                    "processing_location": "internal_dev_stt",
                    "external_transfer": False,
                }
                resident_link_source = "audio_transcript"
            else:
                raise OcrError("이미지 또는 음성파일만 판독할 수 있습니다.")
        except Exception as exc:  # 채팅 전송과 백그라운드 판독 실패를 분리한다.
            if not attempt_is_current():
                return
            extraction.status = "failed"
            if attachment.mime_type in IMAGE_MIME_TYPES:
                error_code, message = safe_image_failure(exc)
                if error_code == "no_text_detected":
                    extraction.status = "no_text"
                image_runtime_detail["error_type"] = error_code
                extraction.error_message = message
                extraction.extracted_text = None
                extraction.suggested_text = None
            else:
                extraction.error_message = str(exc)[:2000]
            if preprocessing_summary is not None:
                preprocessing_summary["output_blocked"] = (
                    image_runtime_detail.get("error_type") == "output_blocked"
                )
                extraction.suggestion_details = [
                    {"kind": "handwriting_preprocessing", **preprocessing_summary}
                ]
            extraction.completed_at = utcnow()
        else:
            if not attempt_is_current():
                return
            extraction.status = "completed"
            # Audio is an immutable automatic transcript, not an image preview.
            extraction.extracted_text = result if attachment.mime_type in AUDIO_MIME_TYPES else result[:12000]
            extraction.error_message = (
                "이미지에서 판독할 글자를 찾지 못해 자동 판독을 건너뛰었습니다."
                if image_has_text is False
                else None
            )
            if extraction.original_extracted_text is None:
                extraction.original_extracted_text = extraction.extracted_text
            extraction.visual_signature = (
                extract_page_visual_signature(target)
                if attachment.mime_type in IMAGE_MIME_TYPES
                else None
            )
            if attachment.mime_type in IMAGE_MIME_TYPES and image_has_text:
                template_gate_enabled = settings.ocr_template_engine_enabled
                if template_gate_enabled:
                    roster_entries, evidences = _attachment_roster_evidence(
                        db,
                        attachment=attachment,
                    )
                    room_service_context = _message_resident_service_context(
                        attachment.message
                    )
                    service_context, service_confidence = (
                        infer_resident_service_context(
                            extraction.extracted_text,
                            roster_entries=roster_entries,
                            room_service_context=room_service_context,
                        )
                    )
                    if (
                        room_service_context not in VALID_RESIDENT_SERVICE_CONTEXTS
                        and service_confidence < 0.78
                    ):
                        service_context = None
                    roster_names = list(
                        dict.fromkeys(
                            entry.name
                            for entry in roster_entries
                            if service_context is None
                            or entry.service_type == service_context
                        )
                    )
                    confirmed_history = [
                        {
                            "recognized_text": evidence.recognized_text,
                            "corrected_text": evidence.corrected_text,
                            "content_type": evidence.content_type,
                            "context_text": evidence.context_text,
                            "section": evidence.section,
                            "layout_role": evidence.layout_role,
                            "column_role": evidence.column_role,
                            "document_template": evidence.document_template,
                        }
                        for evidence in evidences
                    ]
                    template_analysis = analyze_combined_checklist(
                        extraction.extracted_text,
                        active_roster_names=roster_names,
                        confirmed_history=confirmed_history,
                    )
                    visual_observations: list[VisualNameObservation] = []
                    if template_analysis.matched:
                        roster_names = list(
                            dict.fromkeys(
                                entry.name
                                for entry in roster_entries
                                if entry.service_type
                                == template_analysis.profile.resident_service_scope
                            )
                        )
                        template_analysis = analyze_combined_checklist(
                            extraction.extracted_text,
                            active_roster_names=roster_names,
                            confirmed_history=confirmed_history,
                        )
                        template_name_slots = [
                            slot
                            for slot in sorted(
                                template_analysis.slots,
                                key=lambda item: (item.start, item.end),
                            )
                            if slot.role == "name"
                        ]
                        layout_hints = [
                            (
                                f"슬롯{slot_index}={slot.line_index}행/"
                                f"{slot.section_id}/{slot.section_id}"
                            )
                            for slot_index, slot in enumerate(template_name_slots, 1)
                        ]
                        visual_rows = (
                            []
                            if preprocessing_summary is not None
                            and preprocessing_summary.get("selected_variant")
                            == "blue_ink_isolated"
                            else optional_name_candidates(
                                ocr_target,
                                roster_names=roster_names,
                                layout_hints=layout_hints,
                            )
                        )
                        visual_observations = [
                            VisualNameObservation(
                                slot_index=row.slot_index,
                                recognized=row.recognized,
                                candidates=row.candidates,
                                normalized_bbox=row.normalized_bbox,
                            )
                            for row in visual_rows
                        ]
                    versioned_run = run_versioned_structured_stage(
                        FrozenContentOcr(extraction.extracted_text),
                        active_roster_names=roster_names,
                        confirmed_history=confirmed_history,
                        observations=visual_observations,
                    )
                    extraction.suggested_text = (
                        versioned_run.suggested_draft[:12000]
                        if versioned_run.matched
                        and versioned_run.suggested_draft
                        != extraction.extracted_text
                        else None
                    )
                    extraction.suggested_service_context = service_context
                    extraction.suggestion_details = (
                        _with_handwriting_preprocessing_details(
                            serialize_versioned_structured_run(versioned_run),
                            preprocessing_summary,
                        )
                    )
                    extraction.completed_at = utcnow()
                    _sync_message_resident_candidates(
                        db,
                        message=attachment.message,
                        text=extraction.suggested_text or extraction.extracted_text,
                        source=resident_link_source,
                        extraction=extraction,
                    )
                    _ensure_work_item(
                        db,
                        attachment.message,
                        force=bool(result.strip()),
                    )
                    image_runtime_detail.update(result_char_count=len(extraction.extracted_text or ""), elapsed_ms=round((utcnow()-extraction.started_at).total_seconds()*1000))
                    extraction.suggestion_details = [*(extraction.suggestion_details or []), image_runtime_detail]
                    db.commit()
                    return
                draft, roster_names, evidences = _build_attachment_roster_aware_draft(
                    db,
                    attachment=attachment,
                    raw_text=extraction.extracted_text,
                    visual_signature=extraction.visual_signature,
                )
                name_slots = detect_report_name_slots(extraction.extracted_text)
                layout_hints = [
                    (
                        f"슬롯{slot_index}={slot.line_index}행/"
                        f"{slot.section or '일반'}/{slot.layout_role}"
                    )
                    for slot_index, slot in enumerate(name_slots, 1)
                ]
                name_region_rows = (
                    []
                    if preprocessing_summary is not None
                    and preprocessing_summary.get("selected_variant")
                    == "blue_ink_isolated"
                    else optional_name_candidates(
                        target,
                        roster_names=roster_names,
                        layout_hints=layout_hints,
                    )
                )
                if (
                    not (
                        preprocessing_summary is not None
                        and preprocessing_summary.get("selected_variant")
                        == "blue_ink_isolated"
                    )
                    and not name_region_rows
                    and roster_names
                ):
                    # Optional name-region reading can occasionally return an
                    # empty structured response even though full OCR succeeded.
                    # Retry once before leaving every name for manual entry.
                    name_region_rows = optional_name_candidates(
                        target,
                        roster_names=roster_names,
                        layout_hints=layout_hints,
                    )
                name_region_suggestions: list[dict[str, object]] = []
                for row in name_region_rows:
                    raw_slot = (
                        name_slots[row.slot_index - 1]
                        if row.slot_index <= len(name_slots)
                        else None
                    )
                    if raw_slot is not None and raw_slot.recognized in roster_names:
                        # An exact active-roster name in the immutable full OCR
                        # is safer than a conflicting optional second pass.
                        continue
                    if raw_slot is None:
                        continue
                    ranked_candidates = rank_visual_name_candidates(
                        raw_slot.recognized,
                        roster_names=roster_names,
                        visual_candidates=row.candidates,
                        layout_role=raw_slot.layout_role,
                        column_role=raw_slot.column_role,
                        row_sequence=raw_slot.row_sequence,
                        row_group_size=raw_slot.row_group_size,
                        service_confidence=draft.service_confidence,
                    )
                    for ranked in ranked_candidates:
                        rank = int(ranked["rank"])
                        candidate = str(ranked["candidate"])
                        name_region_suggestions.append(
                            {
                                "recognized": row.recognized,
                                "candidate": candidate,
                                "confidence": float(ranked["confidence"]),
                                "surface_similarity": float(
                                    ranked["surface_similarity"]
                                ),
                                "visual_rank": ranked["visual_rank"],
                                "support_count": 0,
                                "reason": (
                                    "이미지 이름 위치 전용 판독 · 활성 명단 제한 · "
                                    "원본 확인 필요"
                                ),
                                "rank": rank,
                                "slot_index": row.slot_index,
                                "region_bbox_normalized": (
                                    list(row.normalized_bbox)
                                    if row.normalized_bbox is not None
                                    else None
                                ),
                                # The optional image pass is explicitly asked
                                # to keep the first-pass layout slot number.
                                # Bind its candidate to that same detected
                                # name cell instead of searching the prose for
                                # the visually read letters.
                                "position_locked": True,
                                "candidate_is_clear": bool(
                                    ranked["candidate_is_clear"]
                                ),
                                "auto_applicable": False,
                            }
                        )
                region_slot_indexes = {
                    int(item["slot_index"]) for item in name_region_suggestions
                }
                auto_candidate_rows = [
                    *name_region_suggestions,
                    *(
                        item
                        for item in draft.corrections
                        if int(item.get("slot_index", 0) or 0)
                        not in region_slot_indexes
                    ),
                ]
                auto_name_draft, top_name_applications = (
                    apply_top_name_candidates_to_draft(
                        extraction.extracted_text,
                        candidates=auto_candidate_rows,
                        roster_names=roster_names,
                        unresolved_placeholder="이름확인필요",
                    )
                )
                application_by_slot = {
                    int(item["slot_index"]): item for item in top_name_applications
                }

                def annotate_application(
                    item: dict[str, object],
                ) -> dict[str, object]:
                    application = application_by_slot.get(
                        int(item.get("slot_index", 0) or 0),
                        {},
                    )
                    is_top = int(item.get("rank", 0) or 0) == 1
                    return {
                        **item,
                        "recognized": str(
                            application.get(
                                "recognized",
                                item.get("recognized", ""),
                            )
                        ),
                        "detected_text": str(
                            application.get(
                                "detected_text",
                                item.get("recognized", ""),
                            )
                        ),
                        "applied_to_draft": (
                            bool(application.get("applied_to_draft", False))
                            if is_top
                            else False
                        ),
                        "resolved_in_draft": bool(
                            application.get("resolved_in_draft", False)
                        ),
                        "application_reason": (
                            application.get("application_reason")
                            if is_top
                            else "alternate_candidate_not_applied"
                        ),
                    }

                name_region_suggestions = [
                    annotate_application(item) for item in name_region_suggestions
                ]
                draft_suggestions = [
                    annotate_application(item) for item in draft.corrections
                ]
                context_draft, context_applications = (
                    apply_safe_context_corrections_to_draft(
                        auto_name_draft,
                        source_writer_id=attachment.message.sender_id,
                        evidences=evidences,
                        roster_names=roster_names,
                    )
                )
                unresolved_applications = [
                    {
                        **item,
                        "rank": 1,
                    }
                    for item in top_name_applications
                    if item.get("application_reason") == "unresolved_name_placeholder"
                ]
                seen_region_pairs = {
                    (
                        int(item.get("slot_index", 0) or 0),
                        str(item["recognized"]),
                        str(item["candidate"]),
                    )
                    for item in name_region_suggestions
                }
                merged_suggestions = [
                    *name_region_suggestions,
                    *(
                        item
                        for item in draft_suggestions
                        if (
                            int(item.get("slot_index", 0) or 0),
                            str(item["recognized"]),
                            str(item["candidate"]),
                        )
                        not in seen_region_pairs
                    ),
                    *unresolved_applications,
                    *context_applications,
                ]
                extraction.suggested_text = (
                    context_draft[:12000]
                    if context_draft != extraction.extracted_text
                    else None
                )
                extraction.suggested_service_context = draft.service_context
                extraction.suggestion_details = (
                    _with_handwriting_preprocessing_details(
                        merged_suggestions or None,
                        preprocessing_summary,
                    )
                )
            else:
                extraction.suggested_text = None
                extraction.suggested_service_context = None
                extraction.suggestion_details = (
                    [audio_quality_detail] if audio_quality_detail is not None else None
                )
            extraction.completed_at = utcnow()
            _sync_message_resident_candidates(
                db,
                message=attachment.message,
                text=extraction.suggested_text or extraction.extracted_text,
                source=resident_link_source,
                extraction=extraction,
            )
            _ensure_work_item(
                db,
                attachment.message,
                force=(
                    attachment.mime_type in AUDIO_MIME_TYPES or bool(result.strip())
                ),
            )
        if attachment.mime_type in IMAGE_MIME_TYPES:
            image_runtime_detail.update(result_char_count=len(extraction.extracted_text or ""), elapsed_ms=round((utcnow()-extraction.started_at).total_seconds()*1000))
            extraction.suggestion_details = [*(extraction.suggestion_details or []), image_runtime_detail]
        elif attachment.mime_type in AUDIO_MIME_TYPES and extraction.status == "completed":
            from .document_review_flow import append_revision
            if not (extraction.extracted_text or "").strip():
                extraction.status = "failed"
                extraction.error_message = "녹음에서 들리는 말소리를 확인하지 못했습니다."
            else:
                append_revision(db, attachment, extraction, kind="automatic", text=extraction.extracted_text)
        db.commit()


def _run_attachment_text_extraction(attachment_id: UUID) -> None:
    from .document_review_flow import run_document_job
    if run_document_job(attachment_id):
        return
    attempt_context: dict = {}
    try:
        _run_attachment_text_extraction_impl(attachment_id, attempt_context)
    except Exception as exc:
        # Optional name/coordinate processing must not leave a job permanently
        # processing, or expose a raw exception containing source content.
        trace = exc.__traceback__
        while trace is not None and trace.tb_next is not None:
            trace = trace.tb_next
        logger.warning("Attachment postprocessing failed: error_type=%s code_line=%s", type(exc).__name__, trace.tb_lineno if trace else 0)
        with SessionLocal() as db:
            extraction = db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id == attachment_id))
            if (extraction is not None and extraction.status == "processing"
                and extraction.started_at == attempt_context.get("started_at")):
                extraction.status = "failed"
                extraction.error_message = "판독 결과를 만들지 못했습니다. 다시 판독하거나 관리자에게 알려 주세요."
                extraction.completed_at = utcnow()
                extraction.suggestion_details = [*(extraction.suggestion_details or []), {"kind": "image_ocr_runtime", "error_type": "postprocessing_failed"}]
                db.commit()


def _run_attachment_text_extractions(attachment_ids: Sequence[UUID]) -> None:
    """Process a message's queued attachments independently.

    A single damaged image must not prevent the remaining pages from being
    processed.  The single-attachment worker owns each status transaction, so
    completed pages remain available even when a later page fails.
    """

    for attachment_id in attachment_ids:
        try:
            _run_attachment_text_extraction(attachment_id)
        except Exception as exc:
            logger.warning("Queued attachment extraction failed: error_type=%s", type(exc).__name__)


def _run_new_attachment_processing(attachment_id: UUID) -> None:
    """Run OCR first, then prepare the coordinate-editor draft for new images.

    Coordinate discovery is deliberately a second, non-confirming stage.  A
    locator failure must never turn a successful OCR into a failed extraction,
    and it must not create a staff correction event or a confirmed work record.
    """

    _run_attachment_text_extraction(attachment_id)
    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        if attachment is None or attachment.mime_type not in IMAGE_MIME_TYPES:
            return
    try:
        _run_attachment_coordinate_auto_location(attachment_id)
    except Exception:
        logger.exception(
            "Automatic coordinate discovery failed unexpectedly: %s",
            attachment_id,
        )


def _run_new_attachment_processing_lane(attachment_ids: Sequence[UUID]) -> None:
    """Run one media lane sequentially while isolating each attachment failure."""

    for attachment_id in attachment_ids:
        try:
            _run_new_attachment_processing(attachment_id)
        except Exception:
            logger.exception(
                "Queued new attachment processing failed unexpectedly: %s",
                attachment_id,
            )


async def _run_new_attachment_processing_batch(
    queued_attachments: Sequence[tuple[UUID, str]],
) -> None:
    """Process image and audio lanes concurrently without parallel image OCR.

    CPU-bound Qwen image calls stay strictly sequential so the internal server
    is not overloaded.  Audio transcription uses a separate isolated service,
    so its sequential lane may progress alongside the image lane.
    """

    image_ids = [
        attachment_id
        for attachment_id, mime_type in queued_attachments
        if mime_type in IMAGE_MIME_TYPES | DOCUMENT_MIME_TYPES
    ]
    audio_ids = [
        attachment_id
        for attachment_id, mime_type in queued_attachments
        if mime_type in AUDIO_MIME_TYPES
    ]
    lanes = [ids for ids in (image_ids, audio_ids) if ids]
    if not lanes:
        return
    await asyncio.gather(
        *(asyncio.to_thread(_run_new_attachment_processing_lane, ids) for ids in lanes)
    )


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
app.add_middleware(PeriodTimingMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origin_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type"],
)
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.trusted_host_list,
)
app.include_router(confirmed_records_router)
app.include_router(ai_assist_router)
app.include_router(ai_system_router)
app.include_router(smcodi_outbox_router)

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
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/care-planning$"
    ),
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/assessment-cycles$"
    ),
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/assessment-cycles/"
        rf"{REVIEWER_UUID_PATH}$"
    ),
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/new-admission-drafts$"
    ),
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/new-admission-drafts/"
        rf"{REVIEWER_UUID_PATH}/questions$"
    ),
)
REVIEWER_ALLOWED_WRITE_PATHS = (
    re.compile(r"^/api/auth/reviewer-session$"),
    re.compile(r"^/api/auth/logout$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/message-search/summary$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/messages$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/read$"),
    re.compile(rf"^/api/messages/{REVIEWER_UUID_PATH}/comments(?:/read)?$"),
    re.compile(r"^/api/workdesk/period-review$"),
    re.compile(r"^/api/workdesk/period-review/evidence$"),
    re.compile(r"^/api/workdesk/record-summary$"),
    re.compile(
        rf"^/api/workdesk/residents/{REVIEWER_UUID_PATH}/care-planning/"
        r"(?:cognitive_function_assessment|fall_risk_assessment|"
        r"pressure_ulcer_risk_assessment|needs_assessment|"
        r"long_term_care_service_plan)$"
    ),
)
MENTOR_FULL_ALLOWED_READ_PATHS = (
    *REVIEWER_ALLOWED_READ_PATHS,
    re.compile(r"^/api/health$"),
    re.compile(r"^/api/push/config$"),
    re.compile(rf"^/api/attachments/{REVIEWER_UUID_PATH}/thumbnail$"),
    re.compile(r"^/api/workdesk/period-reviews$"),
    re.compile(rf"^/api/workdesk/period-reviews/{REVIEWER_UUID_PATH}$"),
    re.compile(r"^/api/org-units$"),
    re.compile(r"^/api/job-codes$"),
    re.compile(r"^/api/position-titles$"),
    re.compile(r"^/api/employees$"),
    re.compile(r"^/api/admin/staff-directory$"),
    re.compile(r"^/api/admin/rooms$"),
    re.compile(r"^/api/admin/residents$"),
    re.compile(r"^/api/ai/system/status$"),
    re.compile(r"^/api/ai/providers/status$"),
    re.compile(r"^/api/ai-assist/config$"),
    re.compile(
        rf"^/api/messages/{REVIEWER_UUID_PATH}/ai-conversations/latest$"
    ),
    re.compile(rf"^/api/ai-conversations/{REVIEWER_UUID_PATH}$"),
    re.compile(rf"^/api/ai-turns/{REVIEWER_UUID_PATH}$"),
)
MENTOR_FULL_ALLOWED_WRITE_PATHS = (
    re.compile(r"^/api/auth/logout$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/message-search/summary$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/messages$"),
    re.compile(rf"^/api/rooms/{REVIEWER_UUID_PATH}/read$"),
    re.compile(rf"^/api/messages/{REVIEWER_UUID_PATH}/comments(?:/read)?$"),
    re.compile(r"^/api/workdesk/period-review$"),
    re.compile(r"^/api/workdesk/period-review/evidence$"),
    re.compile(r"^/api/workdesk/record-summary$"),
    re.compile(rf"^/api/messages/{REVIEWER_UUID_PATH}/ai-conversations$"),
    re.compile(rf"^/api/ai-conversations/{REVIEWER_UUID_PATH}/turns$"),
    re.compile(rf"^/api/ai-turns/{REVIEWER_UUID_PATH}/cancel$"),
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
    try:
        context = reviewer_session_context(token)
    except InvalidReviewerSessionToken:
        context = None
    mentor_full_review = bool(
        context is not None
        and context.experience == MENTOR_FULL_REVIEW_EXPERIENCE
    )
    read_paths = (
        MENTOR_FULL_ALLOWED_READ_PATHS
        if mentor_full_review
        else REVIEWER_ALLOWED_READ_PATHS
    )
    write_paths = (
        MENTOR_FULL_ALLOWED_WRITE_PATHS
        if mentor_full_review
        else REVIEWER_ALLOWED_WRITE_PATHS
    )
    if request.method in {"GET", "HEAD"}:
        if any(
            pattern.fullmatch(request.url.path)
            for pattern in read_paths
        ):
            return await call_next(request)
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content={
                "detail": (
                    "멘토 검토 환경에서는 비식별 합성자료와 안전하게 마스킹된 "
                    "DEV 기능만 열 수 있습니다."
                    if mentor_full_review
                    else "심사위원 체험에서는 기존 업무함 원자료를 열 수 없습니다. "
                    "지정된 심사방과 AI 돌봄 브리핑만 확인해 주세요."
                )
            },
        )
    if any(
        pattern.fullmatch(request.url.path) for pattern in write_paths
    ):
        return await call_next(request)
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={
                "detail": (
                    "멘토 전체 기능 검토에서는 화면과 합성자료를 볼 수 있지만 "
                    "관리 변경·삭제·비밀 설정·공식 저장은 잠겨 있습니다."
                    if mentor_full_review
                    else "심사위원 체험에서는 채팅·읽음·AI 브리핑만 사용할 수 있습니다. "
                    "직원·조직·공식 기록은 변경되지 않습니다."
                )
            },
        )


@app.get("/api/health")
def health() -> dict:
    return {
        "status": "ok",
        "service": "smcodi-chat",
        "environment": settings.environment,
    }


def _reviewer_experience_for_username(username: str) -> str | None:
    for experience in (
        "care",
        "social_worker",
        "realtime_secondary",
        MENTOR_FULL_REVIEW_EXPERIENCE,
    ):
        configured_username = settings.reviewer_username_for_experience(experience)
        if configured_username and username == configured_username:
            return experience
    return None


def _reviewer_account(
    db: Session,
    experience: str,
) -> User:
    username = settings.reviewer_username_for_experience(experience)
    user = (
        db.scalar(select(User).where(User.username == username)) if username else None
    )
    expected_processor_access = experience in {
        "social_worker",
        MENTOR_FULL_REVIEW_EXPERIENCE,
    }
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
    if experience == MENTOR_FULL_REVIEW_EXPERIENCE:
        rooms = list(
            db.scalars(
                select(Room)
                .join(RoomMembership, RoomMembership.room_id == Room.id)
                .where(
                    Room.organization_id == user.organization_id,
                    RoomMembership.staff_id == user.staff_id,
                    RoomMembership.left_at.is_(None),
                    Room.is_active.is_(True),
                    Room.is_test_data.is_(True),
                    Room.kind.in_(("custom", "self")),
                )
                .order_by(Room.kind == "self", Room.name, Room.id)
            ).unique().all()
        )
        if len(rooms) < 2:
            _block_reviewer_destination(
                db,
                user=user,
                experience=experience,
                reason="mentor_full_room_scope_insufficient",
            )
        room_ids = [room.id for room in rooms]
        unsafe_member_count = int(
            db.scalar(
                select(func.count(RoomMembership.id))
                .join(Staff, Staff.id == RoomMembership.staff_id)
                .where(
                    RoomMembership.room_id.in_(room_ids),
                    RoomMembership.left_at.is_(None),
                    Staff.is_test_data.is_(False),
                )
            )
            or 0
        )
        unsafe_message_count = int(
            db.scalar(
                select(func.count(Message.id))
                .join(User, User.id == Message.sender_id)
                .join(Staff, Staff.id == User.staff_id)
                .where(
                    Message.room_id.in_(room_ids),
                    or_(
                        Message.is_test_data.is_(False),
                        Staff.is_test_data.is_(False),
                    ),
                )
            )
            or 0
        )
        direct_residents = db.scalars(
            select(Resident)
            .join(Message, Message.resident_id == Resident.id)
            .where(Message.room_id.in_(room_ids))
        ).unique().all()
        linked_residents = db.scalars(
            select(Resident)
            .join(MessageResidentLink, MessageResidentLink.resident_id == Resident.id)
            .join(Message, Message.id == MessageResidentLink.message_id)
            .where(Message.room_id.in_(room_ids))
        ).unique().all()
        visible_residents = {
            resident.id: resident
            for resident in [*direct_residents, *linked_residents]
        }
        unsafe_resident_count = sum(
            1
            for resident in visible_residents.values()
            if not resident.is_test_data
            or not mentor_visible_resident_code(resident.internal_code)
        )
        if unsafe_member_count or unsafe_message_count or unsafe_resident_count:
            _block_reviewer_destination(
                db,
                user=user,
                experience=experience,
                reason="mentor_full_scope_contains_unsafe_data",
            )
        return rooms[0]

    room_query = select(Room).where(
        Room.organization_id == user.organization_id,
        Room.is_active.is_(True),
    )
    if _password_reviewer_uses_member_scoped_room(experience):
        room_query = room_query.join(
            RoomMembership, RoomMembership.room_id == Room.id
        ).where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
            Room.kind == "custom",
            Room.is_test_data.is_(True),
        )
    else:
        room_query = room_query.where(Room.name == settings.reviewer_chat_room_name)
    rooms = list(db.scalars(room_query).unique().all())
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


def _password_reviewer_uses_member_scoped_room(experience: str) -> bool:
    return bool(
        settings.reviewer_password_access_active
        and experience == settings.reviewer_password_login_experience
        and experience != MENTOR_FULL_REVIEW_EXPERIENCE
        and (settings.mentor_reviewer_username or "").strip()
    )


def _reviewer_room_scope_filter(user: User):
    experience = getattr(user, "_reviewer_experience", None)
    if experience == MENTOR_FULL_REVIEW_EXPERIENCE:
        return and_(
            Room.is_test_data.is_(True),
            Room.kind.in_(("custom", "self")),
            Room.id.in_(
                select(RoomMembership.room_id).where(
                    RoomMembership.staff_id == user.staff_id,
                    RoomMembership.left_at.is_(None),
                )
            ),
        )
    if experience and _password_reviewer_uses_member_scoped_room(experience):
        return and_(Room.kind == "custom", Room.is_test_data.is_(True))
    return and_(
        Room.name == settings.reviewer_chat_room_name,
        Room.is_test_data.is_(True),
    )


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
    usernames = settings.reviewer_usernames
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
            LoginSession.user_agent.like(f"{REVIEWER_SESSION_USER_AGENT_PREFIX}%"),
        )
        .order_by(LoginSession.created_at)
    ).all()
    if len(sessions) < settings.reviewer_rate_limit:
        return None
    seconds = (_as_utc(sessions[0].created_at) + window - now).total_seconds()
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
    return sha256(f"reviewer-browser:{browser_token}".encode("utf-8")).hexdigest()


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
    if not settings.reviewer_experience_access_active:
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
                    int((_as_utc(current_session.expires_at) - now).total_seconds()),
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
            if switching_session is None and is_reviewer_login_session(current_session):
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

    reviewer_usernames = settings.reviewer_usernames
    reviewer_user_ids = list(
        db.scalars(select(User.id).where(User.username.in_(reviewer_usernames))).all()
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
                LoginSession.user_agent.like(f"{REVIEWER_SESSION_USER_AGENT_PREFIX}%"),
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
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    username = payload.username.strip()
    reviewer_experience = _reviewer_experience_for_username(username)
    mentor_admin_login = bool(
        reviewer_experience == MENTOR_FULL_REVIEW_EXPERIENCE
        and settings.reviewer_password_access_active
        and settings.reviewer_password_login_experience
        == MENTOR_FULL_REVIEW_EXPERIENCE
    )
    password_reviewer = (
        reviewer_experience is not None
        and settings.reviewer_password_access_active
        and reviewer_experience == settings.reviewer_password_login_experience
        and not mentor_admin_login
    )
    if reviewer_experience is not None and not (
        password_reviewer or mentor_admin_login
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="사용 기간이 끝났거나 로그인할 수 없는 검토 계정입니다.",
        )
    login_password = (
        payload.password.strip()
        if username == settings.dev_launcher_username
        else payload.password
    )
    if username == settings.dev_launcher_username and (
        not settings.dev_launcher_active or not is_local_development_request(request)
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
        raise HTTPException(
            status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )
    if (
        user.staff is None
        or user.staff.organization_id != user.organization_id
        or user.staff.deleted_at is not None
    ):
        record_audit(
            db,
            actor_id=user.id,
            action="auth.login_blocked",
            target_type="user",
            target_id=user.id,
            details={"reason": "invalid_staff_link"},
        )
        db.commit()
        raise HTTPException(
            status_code=403,
            detail="계정 소속정보를 확인할 수 없어 로그인할 수 없습니다.",
        )
    if (
        not user.is_active
        or not user.staff.is_active
        or user.staff.employment_status != "active"
    ):
        reason = "leave" if user.staff.employment_status == "leave" else "terminated"
        record_audit(
            db,
            actor_id=user.id,
            action="auth.login_blocked",
            target_type="user",
            target_id=user.id,
            details={"reason": reason},
        )
        db.commit()
        detail = (
            "휴직 중에는 로그인할 수 없습니다. 복직 처리 후 다시 로그인해 주세요."
            if reason == "leave"
            else "퇴사 처리되어 로그인할 수 없습니다."
        )
        raise HTTPException(status_code=403, detail=detail)
    clear_failed_logins(db, username, client_key)
    user.last_login_at = utcnow()
    if password_reviewer:
        safe_user = _reviewer_account(db, reviewer_experience)
        if safe_user.id != user.id:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="검토 계정의 안전 조건을 확인할 수 없습니다.",
            )
        _reviewer_destination(
            db,
            user=user,
            experience=reviewer_experience,
        )
        configured_end = settings.reviewer_access_ends_at
        if configured_end is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="검토 계정의 사용 기간이 끝났습니다.",
            )
        expires_at = min(
            utcnow() + timedelta(minutes=settings.reviewer_session_minutes),
            _as_utc(configured_end),
        )
        token = create_reviewer_session_token(reviewer_experience, expires_at)
        _, login_session = create_login_session(
            db,
            user,
            reviewer_session_user_agent(
                reviewer_experience,
                request.headers.get("user-agent"),
            ),
            client_key,
            session_token=token,
            expires_at_override=expires_at,
        )
        cookie_max_age = max(1, int((expires_at - utcnow()).total_seconds()))
    elif mentor_admin_login:
        if user.role != "admin":
            record_audit(
                db,
                actor_id=user.id,
                action="auth.login_blocked",
                target_type="user",
                target_id=user.id,
                details={"reason": "mentor_admin_role_required"},
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="멘토 DEV 계정의 개발자 권한을 관리자가 확인해 주세요.",
            )
        configured_end = settings.reviewer_access_ends_at
        if configured_end is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="멘토 DEV 계정의 사용 기간이 끝났습니다.",
            )
        expires_at = min(
            utcnow() + timedelta(hours=settings.session_hours),
            _as_utc(configured_end),
        )
        token, login_session = create_login_session(
            db,
            user,
            request.headers.get("user-agent"),
            client_key,
            expires_at_override=expires_at,
        )
        cookie_max_age = max(1, int((expires_at - utcnow()).total_seconds()))
    else:
        token, login_session = create_login_session(
            db,
            user,
            request.headers.get("user-agent"),
            client_key,
        )
        cookie_max_age = settings.session_hours * 60 * 60
    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=cookie_max_age,
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
            reviewer_experience=(
                reviewer_experience if password_reviewer else None
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
    request: Request,
    response: Response,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
) -> UserResponse:
    login_session, user = auth
    reviewer_experience = getattr(login_session, "_reviewer_experience", None)
    is_dev_launcher = (
        settings.dev_launcher_active
        and user.username == settings.dev_launcher_username
        and login_session.impersonated_by_user_id is None
    )
    if (
        reviewer_experience is None
        and not is_dev_launcher
        and login_session.impersonated_by_user_id is None
    ):
        now = utcnow()
        login_session.expires_at = now + timedelta(hours=settings.session_hours)
        login_session.last_seen_at = now
        db.commit()
        session_token = request.cookies.get(settings.session_cookie_name)
        if session_token:
            response.set_cookie(
                key=settings.session_cookie_name,
                value=session_token,
                max_age=settings.session_hours * 60 * 60,
                httponly=True,
                secure=secure_cookie_for_request(request),
                samesite="lax",
                path="/",
            )
    return user_response(
        user,
        is_dev_launcher=is_dev_launcher,
        is_dev_impersonated=login_session.impersonated_by_user_id is not None,
        reviewer_experience=reviewer_experience,
    )


def _require_dev_controller(
    request: Request,
    db: Session,
) -> tuple[LoginSession, User, str]:
    if not settings.dev_launcher_active or not is_local_development_request(request):
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
        raise HTTPException(
            status_code=401, detail="개발자 런처 로그인이 만료되었습니다."
        )
    controller = db.get(User, login_session.user_id)
    if (
        controller is None
        or not controller.is_active
        or controller.username != settings.dev_launcher_username
    ):
        raise HTTPException(
            status_code=403, detail="개발자 런처 계정을 확인할 수 없습니다."
        )
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
        raise HTTPException(
            status_code=409, detail="퇴사·휴직 계정은 사용자 전환할 수 없습니다."
        )

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


@app.get(
    "/api/auth/username-availability",
    response_model=UsernameAvailabilityResponse,
)
def check_username_availability(
    username: str,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    normalized_username = username.strip()
    if not re.fullmatch(LOGIN_USERNAME_PATTERN, normalized_username):
        raise HTTPException(
            status_code=422, detail="로그인 아이디 형식이 올바르지 않습니다."
        )
    is_current = normalized_username == user.username
    duplicate_exists = (
        db.scalar(
            select(User.id).where(
                User.username == normalized_username,
                User.id != user.id,
            )
        )
        is not None
    )
    return UsernameAvailabilityResponse(
        username=normalized_username,
        available=not duplicate_exists,
        is_current=is_current,
    )


@app.post("/api/auth/password", response_model=UserResponse)
async def change_login_credentials(
    payload: PasswordChangeRequest,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    current_session, user = auth
    _block_reviewer_account_setting(current_session)
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="현재 비밀번호가 올바르지 않습니다."
        )
    if payload.new_password is not None and verify_password(
        payload.new_password,
        user.password_hash,
    ):
        raise HTTPException(
            status_code=400, detail="새 비밀번호는 현재 비밀번호와 달라야 합니다."
        )
    old_username = user.username
    new_username = (
        payload.new_username.strip()
        if payload.new_username is not None
        else old_username
    )
    if not re.fullmatch(LOGIN_USERNAME_PATTERN, new_username):
        raise HTTPException(
            status_code=422, detail="로그인 아이디 형식이 올바르지 않습니다."
        )
    username_changed = new_username != old_username
    password_changed = payload.new_password is not None
    if not username_changed and not password_changed:
        raise HTTPException(
            status_code=422,
            detail="바꿀 로그인 아이디 또는 새 비밀번호를 입력해 주세요.",
        )
    if (
        username_changed
        and db.scalar(
            select(User.id).where(
                User.username == new_username,
                User.id != user.id,
            )
        )
        is not None
    ):
        raise HTTPException(
            status_code=409, detail="이미 사용 중인 로그인 아이디입니다."
        )
    now = utcnow()
    user.username = new_username
    user.must_change_password = False
    other_sessions: list[LoginSession] = []
    if password_changed:
        user.password_hash = hash_password(payload.new_password)
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
    audit_action = (
        "auth.login_credentials_changed"
        if username_changed and password_changed
        else "auth.username_changed"
        if username_changed
        else "auth.password_changed"
    )
    record_audit(
        db,
        actor_id=user.id,
        action=audit_action,
        target_type="user",
        target_id=user.id,
        details={
            "old_username": old_username,
            "new_username": new_username,
            "username_changed": username_changed,
            "password_changed": password_changed,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="이미 사용 중인 로그인 아이디입니다.",
        ) from exc
    if other_sessions:
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
        raise HTTPException(
            status_code=409, detail="현재 기기는 로그아웃 버튼으로 종료해 주세요."
        )
    target = db.scalar(
        select(LoginSession).where(
            LoginSession.id == session_id,
            LoginSession.user_id == user.id,
            LoginSession.revoked_at.is_(None),
        )
    )
    if target is None:
        raise HTTPException(
            status_code=404, detail="종료할 로그인 기기를 찾을 수 없습니다."
        )
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
    *,
    test_data_only: bool = False,
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
                *([Staff.is_test_data.is_(True)] if test_data_only else []),
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
                *([Room.is_test_data.is_(True)] if test_data_only else []),
            )
            .group_by(Room.scope_unit_id)
        ).all()
    )
    return staff_counts, room_counts


def _org_unit_responses(
    db: Session,
    units: list[OrgUnit],
    *,
    test_data_only: bool = False,
) -> list[OrgUnitResponse]:
    staff_counts, room_counts = _org_unit_usage(
        db,
        [unit.id for unit in units],
        test_data_only=test_data_only,
    )
    unit_ids = [unit.id for unit in units]
    parent_ids = {
        unit.parent_unit_id for unit in units if unit.parent_unit_id is not None
    }
    parent_names = (
        dict(
            db.execute(
                select(OrgUnit.id, OrgUnit.name).where(OrgUnit.id.in_(parent_ids))
            ).all()
        )
        if parent_ids
        else {}
    )
    assignment_counts: dict[UUID, int] = {}
    child_counts: dict[UUID, int] = {}
    recipient_room_counts: dict[UUID, int] = {}
    active_resident_counts: dict[UUID, int] = {}
    resident_scope_room_counts: dict[UUID, int] = {}
    action_item_counts: dict[UUID, int] = {}
    if unit_ids:
        assignment_counts = dict(
            db.execute(
                select(
                    StaffOrganizationAssignment.unit_id,
                    func.count(StaffOrganizationAssignment.id),
                )
                .join(Staff, Staff.id == StaffOrganizationAssignment.staff_id)
                .where(StaffOrganizationAssignment.unit_id.in_(unit_ids))
                .where(*([Staff.is_test_data.is_(True)] if test_data_only else []))
                .group_by(StaffOrganizationAssignment.unit_id)
            ).all()
        )
        child_counts = dict(
            db.execute(
                select(OrgUnit.parent_unit_id, func.count(OrgUnit.id))
                .where(OrgUnit.parent_unit_id.in_(unit_ids))
                .where(*([OrgUnit.is_test_data.is_(True)] if test_data_only else []))
                .group_by(OrgUnit.parent_unit_id)
            ).all()
        )
        recipient_room_counts = {} if test_data_only else dict(
            db.execute(
                select(RecipientRoom.floor_unit_id, func.count(RecipientRoom.id))
                .where(RecipientRoom.floor_unit_id.in_(unit_ids))
                .group_by(RecipientRoom.floor_unit_id)
            ).all()
        )
        active_resident_counts = dict(
            db.execute(
                select(RecipientRoom.floor_unit_id, func.count(Resident.id))
                .join(Resident, Resident.room_id == RecipientRoom.id)
                .where(
                    RecipientRoom.floor_unit_id.in_(unit_ids),
                    Resident.is_active.is_(True),
                    *([Resident.is_test_data.is_(True)] if test_data_only else []),
                )
                .group_by(RecipientRoom.floor_unit_id)
            ).all()
        )
        resident_scope_room_counts = dict(
            db.execute(
                select(Room.resident_scope_unit_id, func.count(Room.id))
                .where(Room.resident_scope_unit_id.in_(unit_ids))
                .where(*([Room.is_test_data.is_(True)] if test_data_only else []))
                .group_by(Room.resident_scope_unit_id)
            ).all()
        )
        action_item_counts = {} if test_data_only else dict(
            db.execute(
                select(ActionItem.assignee_unit_id, func.count(ActionItem.id))
                .where(ActionItem.assignee_unit_id.in_(unit_ids))
                .group_by(ActionItem.assignee_unit_id)
            ).all()
        )
    return [
        OrgUnitResponse.model_validate(unit).model_copy(
            update={
                "parent_unit_name": parent_names.get(unit.parent_unit_id),
                "active_staff_count": int(staff_counts.get(unit.id, 0)),
                "active_room_count": int(room_counts.get(unit.id, 0)),
                "active_resident_count": int(
                    active_resident_counts.get(unit.id, 0)
                ),
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
    *,
    test_data_only: bool = False,
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
                *([Staff.is_test_data.is_(True)] if test_data_only else []),
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
                *([Room.is_test_data.is_(True)] if test_data_only else []),
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
            .join(Staff, Staff.id == StaffJobAssignment.staff_id)
            .where(StaffJobAssignment.job_code.in_(codes))
            .where(*([Staff.is_test_data.is_(True)] if test_data_only else []))
            .group_by(StaffJobAssignment.job_code)
        ).all()
    )
    room_reference_counts = dict(
        db.execute(
            select(Room.job_code, func.count(Room.id))
            .where(Room.job_code.in_(codes))
            .where(*([Room.is_test_data.is_(True)] if test_data_only else []))
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
    *,
    test_data_only: bool = False,
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
                *([Staff.is_test_data.is_(True)] if test_data_only else []),
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
                *([Staff.is_test_data.is_(True)] if test_data_only else []),
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
                *([Staff.is_test_data.is_(True)] if test_data_only else []),
            )
            .group_by(StaffJobAssignment.position_title)
        ).all()
    )
    return [
        PositionTitleResponse.model_validate(position).model_copy(
            update={
                "active_staff_count": int(active_staff_counts.get(position.name, 0)),
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
        if user.role != "admin" and not is_mentor_full_reviewer(user):
            raise HTTPException(
                status_code=403, detail="관리자만 중지된 조직정보를 볼 수 있습니다."
            )
    else:
        query = query.where(OrgUnit.is_active.is_(True))
    query = query.order_by(OrgUnit.is_active.desc(), OrgUnit.unit_type, OrgUnit.name)
    if unit_type:
        query = query.where(OrgUnit.unit_type == unit_type)
    if is_mentor_full_reviewer(user):
        query = query.where(OrgUnit.is_test_data.is_(True))
    return _org_unit_responses(
        db,
        list(db.scalars(query).all()),
        test_data_only=is_mentor_full_reviewer(user),
    )


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
        raise HTTPException(
            status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다."
        )
    unit = OrgUnit(
        organization_id=admin.organization_id,
        unit_type=payload.unit_type,
        internal_code=payload.code or f"{payload.unit_type}-{uuid4().hex[:8]}",
        name=payload.name,
        # 생활공간은 기관이 직접 관리하는 기준정보다. 다른 조직 유형의 수동 추가는
        # 기존 계약대로 시험·예외 항목으로 격리한다.
        is_test_data=payload.unit_type != "floor",
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
        raise HTTPException(
            status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다."
        )
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
    if not unit.is_test_data and unit.unit_type != "floor":
        raise HTTPException(
            status_code=409,
            detail="승인된 실제 조직표는 이 화면에서 변경할 수 없습니다.",
        )
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 조직정보가 없습니다.")
    if (
        values.get("is_active") is False
        and unit.is_active
        and unit.unit_type != "floor"
    ):
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
            raise HTTPException(
                status_code=409, detail="같은 종류와 이름의 조직정보가 이미 있습니다."
            )
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
    return _org_unit_responses(db, [unit])[0]


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
    if not unit.is_test_data:
        raise HTTPException(
            status_code=409,
            detail="승인된 실제 조직표는 이 화면에서 삭제할 수 없습니다.",
        )
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
        if user.role != "admin" and not is_mentor_full_reviewer(user):
            raise HTTPException(
                status_code=403, detail="관리자만 중지된 직종정보를 볼 수 있습니다."
            )
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
    return _job_code_responses(
        db,
        jobs,
        user.organization_id,
        test_data_only=is_mentor_full_reviewer(user),
    )


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
    next_sort = int(db.scalar(select(func.max(StaffJobCode.sort_order))) or 0) + 10
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
    return _job_code_responses(db, [job], admin.organization_id)[0]


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
    # 현재 선택 목록에서 제거해도 기존 직원 배치·대화방 기록은 보존한다.
    # 연결된 현재 값은 직원/방 관리에서 활성 항목 또는 미지정으로 변경할 수 있다.
    if "name" in values:
        duplicate = db.scalar(
            select(StaffJobCode).where(
                StaffJobCode.name == values["name"],
                StaffJobCode.code != job.code,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail="같은 직종 이름이 이미 있습니다."
            )
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
    return _job_code_responses(db, [job], admin.organization_id)[0]


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
        if user.role != "admin" and not is_mentor_full_reviewer(user):
            raise HTTPException(
                status_code=403, detail="관리자만 중지된 직위를 볼 수 있습니다."
            )
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
    return _position_title_responses(
        db,
        positions,
        user.organization_id,
        test_data_only=is_mentor_full_reviewer(user),
    )


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
    next_sort = (
        int(
            db.scalar(
                select(func.max(StaffPositionCode.sort_order)).where(
                    StaffPositionCode.organization_id == admin.organization_id,
                )
            )
            or 0
        )
        + 10
    )
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
    if position is None or position.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="직위를 찾을 수 없습니다.")
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=422, detail="변경할 직위정보가 없습니다.")
    # 직위를 현재 선택 목록에서 제거해도 직원·과거 인사기록은 보존한다.
    # 현재 직원은 직원 관리에서 다른 직위 또는 미지정으로 변경할 수 있다.
    if "name" in values and values["name"] != position.name:
        duplicate = db.scalar(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == admin.organization_id,
                StaffPositionCode.name == values["name"],
                StaffPositionCode.id != position.id,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=409, detail="같은 직위 이름이 이미 있습니다."
            )
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
    if position is None or position.organization_id != admin.organization_id:
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


@app.get(
    "/api/admin/staff-directory",
    response_model=list[StaffDirectoryResponse],
)
def list_staff_directory(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = (
        select(Staff, User)
        .outerjoin(
            User,
            and_(
                User.staff_id == Staff.id,
                User.organization_id == Staff.organization_id,
            ),
        )
        .where(
            Staff.organization_id == admin.organization_id,
            Staff.deleted_at.is_(None),
            or_(User.id.is_(None), User.username != settings.dev_launcher_username),
        )
        .order_by(Staff.display_name, Staff.id)
    )
    if is_mentor_full_reviewer(admin):
        mentor_room_ids = select(RoomMembership.room_id).join(
            Room, Room.id == RoomMembership.room_id
        ).where(
            RoomMembership.staff_id == admin.staff_id,
            RoomMembership.left_at.is_(None),
            Room.is_test_data.is_(True),
            Room.kind.in_(("custom", "self")),
        )
        mentor_visible_staff_ids = select(RoomMembership.staff_id).where(
            RoomMembership.room_id.in_(mentor_room_ids),
            RoomMembership.left_at.is_(None),
        )
        query = query.where(
            Staff.is_test_data.is_(True),
            Staff.id.in_(mentor_visible_staff_ids),
        )
    rows = db.execute(query).all()
    return [staff_directory_response(staff, login_user) for staff, login_user in rows]


@app.post(
    "/api/admin/staff-directory",
    response_model=StaffDirectoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_staff_directory_record(
    payload: StaffDirectoryAdminCreate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    if payload.issue_login:
        user = create_employee(
            db,
            {
                "username": payload.username,
                "full_name": payload.full_name,
                "password": payload.temporary_password,
                "role": payload.role,
                "can_process_records": payload.can_process_records,
                "employee_code": payload.employee_code,
                "job_code": None,
                "position_title": None,
                "business_id": None,
                "department_id": None,
                "floor_id": None,
                "team_id": None,
            },
            admin.id,
        )
        await manager.send_to_users(
            [admin.id, user.id],
            {"event": "employees_changed"},
        )
        return staff_directory_response(user.staff, user)
    staff = create_staff_directory_entry(
        db,
        payload.model_dump(include={"full_name", "employee_code"}),
        admin.id,
    )
    await manager.send_to_users(
        [admin.id],
        {"event": "employees_changed"},
    )
    return staff_directory_response(staff, None)


@app.post(
    "/api/admin/staff-directory/{staff_id}/login",
    response_model=StaffDirectoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def issue_staff_directory_login(
    staff_id: UUID,
    payload: StaffLoginIssueRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff = db.scalar(
        select(Staff).where(
            Staff.id == staff_id,
            Staff.organization_id == admin.organization_id,
            Staff.deleted_at.is_(None),
        )
    )
    if staff is None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    login_user = issue_existing_staff_login(
        db,
        staff=staff,
        username=payload.username,
        temporary_password=payload.temporary_password,
        actor_id=admin.id,
        role_code=payload.role,
        can_process_records=payload.can_process_records,
    )
    db.commit()
    db.refresh(staff)
    db.refresh(login_user)
    await manager.send_to_users(
        [admin.id, login_user.id],
        {"event": "employees_changed"},
    )
    return staff_directory_response(staff, login_user)


@app.patch(
    "/api/admin/staff-directory/{staff_id}",
    response_model=StaffDirectoryResponse,
)
async def update_staff_directory_record(
    staff_id: UUID,
    payload: StaffDirectoryAdminUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff, login_user = update_staff_directory_entry(
        db,
        staff_id,
        payload.model_dump(exclude_unset=True),
        admin.id,
    )
    recipients = [admin.id]
    if login_user is not None and login_user.id != admin.id:
        recipients.append(login_user.id)
    await manager.send_to_users(
        recipients,
        {"event": "employees_changed"},
    )
    return staff_directory_response(staff, login_user)


@app.post(
    "/api/admin/staff-directory/{staff_id}/terminate",
    response_model=StaffDirectoryResponse,
)
async def terminate_staff_directory_record(
    staff_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff, login_user, transitioned = terminate_staff_directory_entry(
        db,
        staff_id,
        admin.id,
    )
    recipients = [admin.id]
    if login_user is not None and login_user.id != admin.id:
        recipients.append(login_user.id)
        if transitioned:
            await manager.force_logout(
                login_user.id,
                "퇴사 처리되어 접속이 종료되었습니다.",
            )
    await manager.send_to_users(
        recipients,
        {"event": "employees_changed"},
    )
    return staff_directory_response(staff, login_user)


def _bulk_transfer_response(batch: BulkTransferBatch) -> dict[str, Any]:
    return {
        "id": str(batch.id),
        "entity_type": batch.entity_type,
        "status": batch.status,
        "original_name": batch.original_name,
        "reference_at": batch.reference_at,
        "summary": batch.summary or {},
        "apply_result": batch.apply_result,
        "created_at": batch.created_at,
        "applied_at": batch.applied_at,
        "items": [
            {
                "id": str(item.id),
                "row_number": item.row_number,
                "entity_id": str(item.entity_id) if item.entity_id else None,
                "display_code": item.display_code,
                "change_type": item.change_type,
                "status": item.status,
                "current_snapshot": item.current_snapshot,
                "incoming_payload": item.incoming_payload,
                "issues": item.issues or [],
                "applied_at": item.applied_at,
            }
            for item in sorted(batch.items, key=lambda value: value.row_number)
        ],
    }


def _bulk_transfer_entity_or_404(entity_type: str) -> Literal["resident", "staff"]:
    if entity_type not in {"resident", "staff"}:
        raise HTTPException(status_code=404, detail="지원하지 않는 자료 종류입니다.")
    return entity_type  # type: ignore[return-value]


@app.get("/api/admin/bulk-transfer/{entity_type}/template.xlsx")
def download_bulk_transfer_template(
    entity_type: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entity = _bulk_transfer_entity_or_404(entity_type)
    choices = bulk_transfer_choices(db, organization_id=admin.organization_id, entity_type=entity)
    content = bulk_transfer_workbook_template(entity, choices=choices)
    label = "residents" if entity == "resident" else "staff"
    return StreamingResponse(
        BytesIO(content),
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{label}_input_template.xlsx"'},
    )


@app.get("/api/admin/bulk-transfer/{entity_type}/current.xlsx")
def download_bulk_transfer_current(
    entity_type: str,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entity = _bulk_transfer_entity_or_404(entity_type)
    choices = bulk_transfer_choices(db, organization_id=admin.organization_id, entity_type=entity)
    content = bulk_transfer_current_workbook(
        db,
        organization_id=admin.organization_id,
        entity_type=entity,
        choices=choices,
    )
    label = "residents" if entity == "resident" else "staff"
    return StreamingResponse(
        BytesIO(content),
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{label}_current.xlsx"'},
    )


@app.post("/api/admin/bulk-transfer/{entity_type}/preview", status_code=201)
async def preview_bulk_transfer(
    entity_type: str,
    file: UploadFile = File(...),
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    entity = _bulk_transfer_entity_or_404(entity_type)
    content = await file.read()
    batch = create_bulk_transfer_preview(
        db,
        actor=admin,
        entity_type=entity,
        original_name=file.filename or "upload.xlsx",
        content=content,
    )
    return _bulk_transfer_response(batch)


@app.get("/api/admin/bulk-transfer/batches/{batch_id}")
def read_bulk_transfer_batch(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    return _bulk_transfer_response(get_bulk_transfer_batch(db, actor=admin, batch_id=batch_id))


@app.post("/api/admin/bulk-transfer/batches/{batch_id}/apply")
async def apply_bulk_transfer(
    batch_id: UUID,
    payload: BulkTransferApplyRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch, revoked_user_ids = apply_bulk_transfer_batch(
        db,
        actor=admin,
        batch_id=batch_id,
        item_ids=payload.item_ids,
    )
    for user_id in revoked_user_ids:
        await manager.force_logout(user_id, "퇴사 처리되어 접속이 종료되었습니다.")
    await manager.send_to_users([admin.id], {"event": "employees_changed"})
    return _bulk_transfer_response(batch)


@app.get("/api/admin/bulk-transfer/batches/{batch_id}/errors.xlsx")
def download_bulk_transfer_errors(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = get_bulk_transfer_batch(db, actor=admin, batch_id=batch_id)
    content = bulk_transfer_error_workbook(batch)
    return StreamingResponse(
        BytesIO(content),
        media_type=XLSX_CONTENT_TYPE,
        headers={"Content-Disposition": 'attachment; filename="bulk_transfer_errors.xlsx"'},
    )


@app.patch(
    "/api/admin/staff-directory/{staff_id}/service-assignments",
    response_model=StaffDirectoryResponse,
)
async def update_staff_service_assignments(
    staff_id: UUID,
    payload: StaffServiceAssignmentsAdminUpdate,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    staff = db.scalar(
        select(Staff)
        .where(
            Staff.id == staff_id,
            Staff.organization_id == admin.organization_id,
            Staff.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if staff is None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if staff.employment_status != "active" or not staff.is_active:
        raise HTTPException(
            status_code=409,
            detail="재직 중인 직원의 서비스 배정만 변경할 수 있습니다.",
        )

    requested = {item.service_type: item for item in payload.assignments}
    job_codes = {item.job_code for item in payload.assignments}
    jobs = {
        job.code: job
        for job in db.scalars(
            select(StaffJobCode).where(
                StaffJobCode.code.in_(job_codes),
                StaffJobCode.is_active.is_(True),
            )
        ).all()
    }
    missing_job_codes = sorted(job_codes - jobs.keys())
    if missing_job_codes:
        raise HTTPException(
            status_code=422,
            detail="현재 사용할 수 있는 직종을 선택해 주세요.",
        )

    positions: dict[str, StaffPositionCode | None] = {}
    for item in payload.assignments:
        normalized_position = validate_position_title(
            db,
            admin.organization_id,
            item.position_title,
        )
        item.position_title = normalized_position
        if normalized_position and normalized_position not in positions:
            positions[normalized_position] = db.scalar(
                select(StaffPositionCode).where(
                    StaffPositionCode.organization_id == admin.organization_id,
                    StaffPositionCode.name == normalized_position,
                    StaffPositionCode.is_active.is_(True),
                )
            )

    current_assignments = {
        assignment.service_type: assignment
        for assignment in db.scalars(
            select(StaffServiceAssignment).where(
                StaffServiceAssignment.organization_id == admin.organization_id,
                StaffServiceAssignment.staff_id == staff.id,
                StaffServiceAssignment.end_date.is_(None),
            )
        ).all()
    }
    today = date.today()
    service_labels = {
        "facility": "시설",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }
    changes: list[dict[str, Any]] = []

    def assignment_unit_id(
        assignment: StaffServiceAssignment | None,
        field_name: str,
        unit_type: str,
    ) -> UUID | None:
        if assignment is None:
            return None
        unit = getattr(assignment, field_name)
        if (
            unit is None
            or unit.organization_id != admin.organization_id
            or unit.unit_type != unit_type
        ):
            return None
        return unit.id

    def default_business_id(service_type: str) -> UUID | None:
        unit = db.scalar(
            select(OrgUnit)
            .where(
                OrgUnit.organization_id == admin.organization_id,
                OrgUnit.unit_type == "business",
                OrgUnit.name == service_labels[service_type],
                OrgUnit.is_active.is_(True),
            )
            .order_by(OrgUnit.is_test_data, OrgUnit.internal_code, OrgUnit.id)
        )
        return unit.id if unit is not None else None

    for service_type, assignment in current_assignments.items():
        if service_type in requested:
            continue
        if assignment.start_date >= today:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"오늘 시작된 {service_labels.get(service_type, service_type)} "
                    "배정은 오늘 바로 종료할 수 없습니다. 내일부터 변경하거나 "
                    "관리자에게 확인해 주세요."
                ),
            )
        assignment.end_date = today
        assignment.updated_by = admin.id
        changes.append(
            {
                "service_type": service_type,
                "change": "closed",
                "previous_job_code": assignment.job_code,
                "previous_position_title": assignment.position_title_snapshot,
            }
        )

    for service_type, item in requested.items():
        current = current_assignments.get(service_type)
        position = positions.get(item.position_title) if item.position_title else None
        if current is not None and (
            current.job_code == item.job_code
            and (current.position_title_snapshot or None) == (item.position_title or None)
        ):
            continue

        previous_job_code = current.job_code if current is not None else None
        previous_position = (
            current.position_title_snapshot if current is not None else None
        )
        if current is not None and current.start_date >= today:
            if not (
                current.assignment_basis == "admin_confirmed"
                and current.source_account.source_system == "manual"
            ):
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"오늘 가져온 {service_labels[service_type]} 배정은 원본 보존을 "
                        "위해 오늘 바로 덮어쓸 수 없습니다. 내일부터 변경해 주세요."
                    ),
                )
            current.job_code = item.job_code
            current.job_title_snapshot = jobs[item.job_code].name
            current.position_code_id = position.id if position is not None else None
            current.position_title_snapshot = item.position_title
            current.updated_by = admin.id
            changes.append(
                {
                    "service_type": service_type,
                    "change": "same_day_manual_correction",
                    "previous_job_code": previous_job_code,
                    "job_code": item.job_code,
                    "previous_position_title": previous_position,
                    "position_title": item.position_title,
                }
            )
            continue

        if current is not None:
            current.end_date = today
            current.updated_by = admin.id

        external_id = f"admin:{staff.id}:{service_type}"
        source_account = db.scalar(
            select(StaffSourceAccount).where(
                StaffSourceAccount.organization_id == admin.organization_id,
                StaffSourceAccount.source_system == "manual",
                StaffSourceAccount.service_type == service_type,
                StaffSourceAccount.external_id == external_id,
            )
        )
        if source_account is None:
            source_account = StaffSourceAccount(
                organization_id=admin.organization_id,
                staff_id=staff.id,
                source_system="manual",
                service_type=service_type,
                external_id=external_id,
                display_name_snapshot=staff.display_name,
                job_name_snapshot=jobs[item.job_code].name,
                employment_status="active",
                source_status="관리자 직접 입력",
                latest_start_date=today,
                latest_end_date=None,
                is_active=True,
            )
            db.add(source_account)
            db.flush()
        else:
            source_account.staff_id = staff.id
            source_account.display_name_snapshot = staff.display_name
            source_account.job_name_snapshot = jobs[item.job_code].name
            source_account.employment_status = "active"
            source_account.source_status = "관리자 직접 입력"
            source_account.latest_end_date = None
            source_account.is_active = True
            source_account.last_seen_at = utcnow()

        service_period = db.scalar(
            select(StaffServicePeriod).where(
                StaffServicePeriod.organization_id == admin.organization_id,
                StaffServicePeriod.staff_id == staff.id,
                StaffServicePeriod.source_account_id == source_account.id,
                StaffServicePeriod.end_date.is_(None),
            )
        )
        if service_period is None:
            service_period = StaffServicePeriod(
                organization_id=admin.organization_id,
                staff_id=staff.id,
                source_account_id=source_account.id,
                start_date=today,
                end_date=None,
                employment_status="active",
            )
            db.add(service_period)
            db.flush()

        new_assignment = StaffServiceAssignment(
            organization_id=admin.organization_id,
            staff_id=staff.id,
            source_account_id=source_account.id,
            service_period_id=service_period.id,
            service_type=service_type,
            business_unit_id=(
                assignment_unit_id(current, "business", "business")
                or default_business_id(service_type)
            ),
            department_unit_id=assignment_unit_id(
                current,
                "department",
                "department",
            ),
            floor_unit_id=assignment_unit_id(current, "floor", "floor"),
            team_unit_id=assignment_unit_id(current, "team", "team"),
            job_code=item.job_code,
            job_title_snapshot=jobs[item.job_code].name,
            position_code_id=position.id if position is not None else None,
            position_title_snapshot=item.position_title,
            start_date=today,
            end_date=None,
            assignment_basis="admin_confirmed",
            is_test_data=staff.is_test_data,
            created_by=admin.id,
            updated_by=admin.id,
        )
        db.add(new_assignment)
        changes.append(
            {
                "service_type": service_type,
                "change": "reassigned" if current is not None else "added",
                "previous_job_code": previous_job_code,
                "job_code": item.job_code,
                "previous_position_title": previous_position,
                "position_title": item.position_title,
            }
        )

    login_user = db.scalar(
        select(User).where(
            User.organization_id == admin.organization_id,
            User.staff_id == staff.id,
        )
    )
    if changes:
        if login_user is not None:
            sync_auto_memberships(db, login_user)
        record_audit(
            db,
            actor_id=admin.id,
            action="staff.service_assignments.updated",
            target_type="staff",
            target_id=staff.id,
            details={"changes": changes},
        )
        db.commit()
        db.expire(staff, ["service_assignments"])

    if login_user is not None:
        db.refresh(login_user)
    response = staff_directory_response(staff, login_user)
    recipients = {admin.id}
    if login_user is not None:
        recipients.add(login_user.id)
    await manager.send_to_users(recipients, {"event": "employees_changed"})
    if login_user is not None and changes:
        await manager.send_to_users({login_user.id}, {"event": "rooms_changed"})
    return response


@app.get("/api/employees", response_model=list[UserResponse])
def list_employees(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = (
        select(User)
        .join(Staff, Staff.id == User.staff_id)
        .where(
            User.organization_id == admin.organization_id,
            Staff.organization_id == admin.organization_id,
            User.username != settings.dev_launcher_username,
            Staff.deleted_at.is_(None),
        )
        .order_by(User.display_name, User.id)
    )
    if is_mentor_full_reviewer(admin):
        mentor_room_ids = select(RoomMembership.room_id).join(
            Room, Room.id == RoomMembership.room_id
        ).where(
            RoomMembership.staff_id == admin.staff_id,
            RoomMembership.left_at.is_(None),
            Room.is_test_data.is_(True),
            Room.kind.in_(("custom", "self")),
        )
        mentor_visible_staff_ids = select(RoomMembership.staff_id).where(
            RoomMembership.room_id.in_(mentor_room_ids),
            RoomMembership.left_at.is_(None),
        )
        query = query.where(
            Staff.is_test_data.is_(True),
            Staff.id.in_(mentor_visible_staff_ids),
        )
    return [
        user_response(user)
        for user in db.scalars(query).all()
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
        or user.staff.organization_id != admin.organization_id
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status != "active":
        raise HTTPException(
            status_code=409, detail="퇴사자의 정보를 변경할 수 없습니다."
        )
    values = payload.model_dump(exclude_unset=True)
    if (
        settings.environment == "development"
        and settings.database_schema == "smcodi_finalist"
        and ({"full_name", "employee_code"} & set(values))
    ):
        raise HTTPException(
            status_code=409,
            detail="본선 DEV에서 한 번 부여한 직원 코드는 변경하거나 재사용할 수 없습니다.",
        )
    if (
        user.organization_id != admin.organization_id
        or user.staff is None
        or user.staff.organization_id != admin.organization_id
    ):
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
            raise HTTPException(
                status_code=409, detail="이미 사용 중인 직원번호입니다."
            )
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
        or user.staff.organization_id != admin.organization_id
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status != "active":
        raise HTTPException(
            status_code=409, detail="퇴사자의 비밀번호를 초기화할 수 없습니다."
        )
    if user.id == admin.id:
        raise HTTPException(
            status_code=409,
            detail="현재 관리자 비밀번호는 보안 설정에서 직접 변경해 주세요.",
        )
    if verify_password(payload.temporary_password, user.password_hash):
        raise HTTPException(
            status_code=400, detail="기존 비밀번호와 다른 임시 비밀번호를 입력하세요."
        )
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
        or user.staff.organization_id != admin.organization_id
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.id == admin.id:
        raise HTTPException(
            status_code=409,
            detail="현재 로그인한 관리자 자신은 퇴사 처리할 수 없습니다.",
        )
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
        details={
            "revoked_sessions": len(sessions),
            "closed_memberships": len(memberships),
        },
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
        or user.staff.organization_id != admin.organization_id
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.employment_status == "active":
        return user_response(user)
    if user.employment_status != "retired":
        raise HTTPException(
            status_code=409, detail="퇴사 상태의 직원만 재직으로 복구할 수 있습니다."
        )

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
        or user.staff.organization_id != admin.organization_id
        or user.staff.deleted_at is not None
    ):
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if user.id == admin.id:
        raise HTTPException(
            status_code=409, detail="현재 로그인한 관리자 자신은 삭제할 수 없습니다."
        )
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
            settings.web_push_vapid_public_key if settings.web_push_active else None
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
        select(PushSubscription).where(PushSubscription.endpoint_hash == endpoint_hash)
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
        subscription.user_agent = (request.headers.get("user-agent") or "")[
            :300
        ] or None
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
    "/api/mobile-push/devices",
    response_model=MobilePushDeviceResponse,
    status_code=201,
)
def register_mobile_push_device(
    payload: MobilePushDeviceCreate,
    request: Request,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, user = auth
    _block_reviewer_account_setting(login_session)
    token_hash = sha256(payload.token.encode("utf-8")).hexdigest()
    device = db.scalar(
        select(MobilePushDevice).where(
            MobilePushDevice.installation_id == payload.installation_id
        )
    )
    if device is None:
        device = MobilePushDevice(
            organization_id=user.organization_id,
            user_id=user.id,
            login_session_id=login_session.id,
            installation_id=payload.installation_id,
            platform=payload.platform,
            token=payload.token,
            token_hash=token_hash,
            app_version=payload.app_version,
            user_agent=(request.headers.get("user-agent") or "")[:300] or None,
        )
        db.add(device)
    else:
        device.organization_id = user.organization_id
        device.user_id = user.id
        device.login_session_id = login_session.id
        device.platform = payload.platform
        device.token = payload.token
        device.token_hash = token_hash
        device.app_version = payload.app_version
        device.user_agent = (request.headers.get("user-agent") or "")[:300] or None
        device.is_active = True
        device.failure_count = 0
        device.disabled_at = None
    db.commit()
    return MobilePushDeviceResponse(
        enabled=settings.mobile_push_active,
        active=settings.mobile_push_active,
        message=(
            "이 휴대전화의 앱 통화 알림을 연결했습니다."
            if settings.mobile_push_active
            else "휴대전화 등록을 저장했습니다. 푸시 서버 설정 후 자동으로 활성화됩니다."
        ),
    )


@app.delete(
    "/api/mobile-push/devices",
    response_model=MobilePushDeviceResponse,
)
def delete_mobile_push_device(
    payload: MobilePushDeviceDelete,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, user = auth
    _block_reviewer_account_setting(login_session)
    device = db.scalar(
        select(MobilePushDevice).where(
            MobilePushDevice.installation_id == payload.installation_id,
            MobilePushDevice.user_id == user.id,
        )
    )
    if device is not None:
        device.is_active = False
        device.disabled_at = utcnow()
        db.commit()
    return MobilePushDeviceResponse(
        enabled=settings.mobile_push_active,
        active=False,
        message="이 휴대전화의 앱 통화 알림을 해제했습니다.",
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


def _admin_session_for_conversation_access(
    auth: tuple[LoginSession, User],
) -> tuple[LoginSession, User]:
    login_session, user = auth
    if user.must_change_password and login_session.impersonated_by_user_id is None:
        raise HTTPException(
            status_code=403,
            detail="계속하려면 먼저 임시 비밀번호를 변경해야 합니다.",
        )
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="관리자 권한이 필요합니다.")
    if getattr(user, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="심사 체험 화면에서는 업무대화를 열람할 수 없습니다.",
        )
    return login_session, user


ADMIN_CONVERSATION_CACHE_CONTROL = "private, no-store"


def _prevent_admin_conversation_caching(response: Response) -> None:
    response.headers["Cache-Control"] = ADMIN_CONVERSATION_CACHE_CONTROL


@app.get(
    "/api/admin/conversation-access",
    response_model=AdminConversationAccessResponse,
)
def admin_conversation_access_status(
    response: Response,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
):
    _prevent_admin_conversation_caching(response)
    login_session, _admin = _admin_session_for_conversation_access(auth)
    active = admin_conversation_access_is_active(login_session)
    return AdminConversationAccessResponse(
        active=active,
        expires_at=(
            _as_utc(login_session.admin_conversation_access_expires_at)
            if active
            else None
        ),
    )


@app.get("/api/voice-calls/config", response_model=VoiceCallConfigResponse)
def voice_call_config(
    response: Response,
    user: User = Depends(get_current_user),
):
    response.headers["Cache-Control"] = "private, no-store"
    reviewer_session = getattr(user, "_reviewer_experience", None) is not None
    enabled = settings.voice_call_enabled and not reviewer_session
    try:
        ice_servers = voice_call_ice_servers(subject=str(user.id)) if enabled else []
    except VoiceCallTurnError as exc:
        logger.warning("통화 중계 설정 준비 실패: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="통화 중계 서버를 준비하지 못했습니다. 관리자에게 알려 주세요.",
        ) from exc
    return VoiceCallConfigResponse(
        enabled=enabled,
        max_participants=settings.voice_call_max_participants,
        max_video_participants=settings.voice_call_max_video_participants,
        ice_servers=(
            [VoiceCallIceServerResponse(**server) for server in ice_servers]
            if enabled
            else []
        ),
    )


@app.post(
    "/api/admin/conversation-access",
    response_model=AdminConversationAccessResponse,
)
def grant_admin_conversation_access(
    payload: AdminConversationAccessRequest,
    request: Request,
    response: Response,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    _prevent_admin_conversation_caching(response)
    login_session, admin = _admin_session_for_conversation_access(auth)
    client_key = client_key_from_request(request)
    retry_after = login_retry_after(db, admin.username, client_key)
    if retry_after is not None:
        db.commit()
        raise HTTPException(
            status_code=429,
            detail="비밀번호 확인 시도가 너무 많습니다. 잠시 후 다시 시도해 주세요.",
            headers={"Retry-After": str(retry_after)},
        )
    if not verify_password(payload.password, admin.password_hash):
        record_failed_login(db, admin.username, client_key)
        record_audit(
            db,
            actor_id=admin.id,
            action="admin.conversation_access_failed",
            target_type="session",
            target_id=login_session.id,
            details={"client_key": client_key[:12]},
        )
        db.commit()
        raise HTTPException(
            status_code=400, detail="관리자 비밀번호가 올바르지 않습니다."
        )
    clear_failed_logins(db, admin.username, client_key)
    expires_at = utcnow() + timedelta(
        minutes=settings.admin_conversation_access_minutes
    )
    login_session.admin_conversation_access_expires_at = expires_at
    record_audit(
        db,
        actor_id=admin.id,
        action="admin.conversation_access_granted",
        target_type="session",
        target_id=login_session.id,
        details={"expires_at": expires_at.isoformat()},
    )
    db.commit()
    return AdminConversationAccessResponse(active=True, expires_at=expires_at)


@app.delete("/api/admin/conversation-access", status_code=204)
def revoke_admin_conversation_access(
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    login_session, admin = _admin_session_for_conversation_access(auth)
    login_session.admin_conversation_access_expires_at = None
    record_audit(
        db,
        actor_id=admin.id,
        action="admin.conversation_access_revoked",
        target_type="session",
        target_id=login_session.id,
    )
    db.commit()
    return Response(
        status_code=204,
        headers={"Cache-Control": ADMIN_CONVERSATION_CACHE_CONTROL},
    )


def _admin_conversation_room(
    db: Session,
    admin: User,
    room_id: UUID,
) -> Room:
    room = db.get(Room, room_id)
    if room is None or room.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    return room


@app.get(
    "/api/admin/conversations/rooms/{room_id}/messages",
    response_model=list[MessageResponse],
)
def list_admin_conversation_messages(
    room_id: UUID,
    response: Response,
    after_id: UUID | None = None,
    limit: int = 60,
    auth: tuple[LoginSession, User] = Depends(require_admin_conversation_access),
    db: Session = Depends(get_db),
):
    _prevent_admin_conversation_caching(response)
    _login_session, admin = auth
    room = _admin_conversation_room(db, admin, room_id)
    safe_limit = max(1, min(limit, 200))
    query = select(Message).where(Message.room_id == room.id)
    if after_id is not None:
        cursor = db.get(Message, after_id)
        if cursor is None or cursor.room_id != room.id:
            raise HTTPException(
                status_code=422, detail="메시지 조회 기준이 올바르지 않습니다."
            )
        messages = db.scalars(
            query.where(Message.created_at > cursor.created_at)
            .order_by(Message.created_at, Message.id)
            .limit(safe_limit)
        ).all()
    else:
        messages = list(
            reversed(
                db.scalars(
                    query.order_by(Message.created_at.desc(), Message.id.desc()).limit(
                        safe_limit
                    )
                ).all()
            )
        )
    record_audit(
        db,
        actor_id=admin.id,
        action="admin.conversation_room_viewed",
        target_type="room",
        target_id=room.id,
        details={"message_count": len(messages)},
    )
    db.commit()
    return [
        message_response(
            message,
            db=db,
            viewer_id=admin.id,
            include_recalled_content=True,
            use_admin_attachment_urls=True,
        )
        for message in messages
    ]


@app.get(
    "/api/admin/conversations/messages/{message_id}",
    response_model=MessageDetailResponse,
)
def get_admin_conversation_message(
    message_id: UUID,
    response: Response,
    auth: tuple[LoginSession, User] = Depends(require_admin_conversation_access),
    db: Session = Depends(get_db),
):
    _prevent_admin_conversation_caching(response)
    _login_session, admin = auth
    message = db.get(Message, message_id)
    if message is None or message.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    receipts = db.scalars(
        select(MessageReadReceipt)
        .where(MessageReadReceipt.message_id == message.id)
        .order_by(MessageReadReceipt.read_at, MessageReadReceipt.id)
    ).all()
    comments = db.scalars(
        select(MessageComment)
        .where(MessageComment.message_id == message.id)
        .order_by(MessageComment.created_at, MessageComment.id)
    ).all()
    record_audit(
        db,
        actor_id=admin.id,
        action="admin.conversation_message_viewed",
        target_type="message",
        target_id=message.id,
    )
    db.commit()
    return MessageDetailResponse(
        message=message_response(
            message,
            db=db,
            viewer_id=admin.id,
            include_recalled_content=True,
            use_admin_attachment_urls=True,
        ),
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


@app.get("/api/admin/conversations/attachments/{attachment_id}")
def download_admin_conversation_attachment(
    attachment_id: UUID,
    auth: tuple[LoginSession, User] = Depends(require_admin_conversation_access),
    db: Session = Depends(get_db),
):
    _login_session, admin = auth
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    message = db.get(Message, attachment.message_id)
    if message is None or message.organization_id != admin.organization_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    record_audit(
        db,
        actor_id=admin.id,
        action="admin.conversation_attachment_viewed",
        target_type="attachment",
        target_id=attachment.id,
        details={"message_id": str(message.id), "room_id": str(message.room_id)},
    )
    db.commit()
    response = _attachment_file_response(attachment)
    _prevent_admin_conversation_caching(response)
    return response


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
    created_by = db.get(User, room.created_by_id) if room.created_by_id else None
    last_message_at = db.scalar(
        select(func.max(Message.created_at)).where(Message.room_id == room.id)
    )
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
            db.scalar(select(func.count(Message.id)).where(Message.room_id == room.id))
            or 0
        ),
        attachment_count=int(
            db.scalar(
                select(func.count(MessageAttachment.id))
                .join(Message, Message.id == MessageAttachment.message_id)
                .where(Message.room_id == room.id)
            )
            or 0
        ),
        owner_staff_id=room.owner_staff_id,
        owner_name=room.owner_staff.display_name if room.owner_staff else None,
        created_by_name=created_by.full_name if created_by else None,
        last_message_at=_as_utc(last_message_at) if last_message_at else None,
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
    if {user.id for user in users} != member_ids or any(
        user.staff_id is None or user.employment_status != "active" for user in users
    ):
        raise HTTPException(
            status_code=422, detail="참여자 중 존재하지 않거나 퇴사한 직원이 있습니다."
        )
    return list(users)


def _staff_room_member_response(user: User) -> StaffRoomMemberResponse:
    if user.staff_id is None:
        raise RuntimeError("직원 연결이 없는 사용자는 대화방 참여자가 될 수 없습니다.")
    return StaffRoomMemberResponse(
        id=user.id,
        staff_id=user.staff_id,
        full_name=user.full_name,
        job_name=user.job_name,
        position_title=user.position_title,
    )


def _active_custom_room_users(db: Session, room: Room) -> list[User]:
    return list(
        db.scalars(
            select(User)
            .join(Staff, Staff.id == User.staff_id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room.id,
                RoomMembership.left_at.is_(None),
                User.organization_id == room.organization_id,
                User.is_active.is_(True),
                Staff.is_active.is_(True),
                Staff.deleted_at.is_(None),
                Staff.employment_status == "active",
            )
            .order_by(Staff.display_name, User.id)
        ).all()
    )


def _matching_custom_room(
    db: Session,
    *,
    organization_id: UUID,
    current_staff_id: UUID,
    member_user_ids: set[UUID],
) -> Room | None:
    rooms = db.scalars(
        select(Room)
        .join(RoomMembership, RoomMembership.room_id == Room.id)
        .where(
            Room.organization_id == organization_id,
            Room.kind == "custom",
            Room.is_active.is_(True),
            RoomMembership.staff_id == current_staff_id,
            RoomMembership.left_at.is_(None),
        )
        .order_by(Room.created_at.desc(), Room.id)
    ).all()
    for room in rooms:
        if {
            member.id for member in _active_custom_room_users(db, room)
        } == member_user_ids:
            return room
    return None


def _effective_custom_room_owner(
    room: Room,
    members: list[User],
) -> tuple[UUID, str]:
    owner = next(
        (member for member in members if member.staff_id == room.owner_staff_id),
        None,
    )
    if owner is None and members:
        owner = members[0]
    if owner is None or owner.staff_id is None:
        raise HTTPException(status_code=409, detail="채팅방 방장을 확인할 수 없습니다.")
    return owner.staff_id, owner.full_name


def _staff_room_response(db: Session, room: Room, viewer: User) -> StaffRoomResponse:
    members = _active_custom_room_users(db, room)
    owner_staff_id, owner_name = _effective_custom_room_owner(room, members)
    is_owner = viewer.staff_id == owner_staff_id
    return StaffRoomResponse(
        id=room.id,
        name=room.name,
        is_active=room.is_active,
        owner_staff_id=owner_staff_id,
        owner_name=owner_name,
        member_ids=[member.id for member in members],
        members=[_staff_room_member_response(member) for member in members],
        is_owner=is_owner,
        can_invite=room.is_active and any(member.id == viewer.id for member in members),
        can_manage=room.is_active and is_owner,
        created_at=_as_utc(room.created_at),
    )


def _custom_room_for_active_member(
    db: Session,
    user: User,
    room_id: UUID,
) -> tuple[Room, RoomMembership]:
    room = db.get(Room, room_id)
    if (
        room is None
        or room.organization_id != user.organization_id
        or room.kind != "custom"
    ):
        raise HTTPException(status_code=404, detail="직원 대화방을 찾을 수 없습니다.")
    if not room.is_active:
        raise HTTPException(status_code=409, detail="종료된 직원 대화방입니다.")
    if user.staff_id is None:
        raise HTTPException(
            status_code=403, detail="직원 연결정보를 확인할 수 없습니다."
        )
    membership = db.scalar(
        select(RoomMembership).where(
            RoomMembership.room_id == room.id,
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
    )
    if membership is None:
        raise HTTPException(
            status_code=403, detail="이 직원 대화방에 참여하고 있지 않습니다."
        )
    return room, membership


def _require_custom_room_owner(room: Room, user: User, db: Session) -> None:
    members = _active_custom_room_users(db, room)
    owner_staff_id, _owner_name = _effective_custom_room_owner(room, members)
    if user.staff_id != owner_staff_id:
        raise HTTPException(status_code=403, detail="방장만 이 작업을 할 수 있습니다.")


def _default_staff_room_name(users: list[User]) -> str:
    names = sorted({user.full_name for user in users})
    if len(names) <= 3:
        return ", ".join(names)
    return f"{names[0]}, {names[1]} 외 {len(names) - 2}명"


@app.get(
    "/api/staff-directory/active",
    response_model=list[ActiveStaffDirectoryResponse],
)
def list_active_staff_directory(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if getattr(user, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="심사 체험 화면에서는 실제 직원 목록을 볼 수 없습니다.",
        )
    employees = db.scalars(
        select(User)
        .join(Staff, Staff.id == User.staff_id)
        .where(
            User.organization_id == user.organization_id,
            User.is_active.is_(True),
            User.username != settings.dev_launcher_username,
            Staff.is_active.is_(True),
            Staff.deleted_at.is_(None),
            Staff.employment_status == "active",
        )
        .order_by(Staff.display_name, User.id)
    ).all()
    return [
        ActiveStaffDirectoryResponse(
            id=employee.id,
            staff_id=employee.staff_id,
            full_name=employee.full_name,
            job_name=employee.job_name,
            position_title=employee.position_title,
            business_name=employee.business.name if employee.business else None,
            department_name=employee.department.name if employee.department else None,
            floor_name=employee.floor.name if employee.floor else None,
            team_name=employee.team.name if employee.team else None,
        )
        for employee in employees
        if employee.staff_id is not None
    ]


@app.get("/api/staff-rooms", response_model=list[StaffRoomResponse])
def list_staff_rooms(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if getattr(user, "_reviewer_experience", None) is not None:
        return []
    if user.staff_id is None:
        return []
    rooms = db.scalars(
        select(Room)
        .join(RoomMembership, RoomMembership.room_id == Room.id)
        .where(
            Room.organization_id == user.organization_id,
            Room.kind == "custom",
            Room.is_active.is_(True),
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
        )
        .order_by(Room.created_at.desc(), Room.id)
    ).all()
    return [_staff_room_response(db, room, user) for room in rooms]


@app.post("/api/staff-rooms", response_model=StaffRoomResponse, status_code=201)
async def create_staff_room(
    payload: StaffRoomCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if getattr(user, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=403, detail="심사 체험 화면에서는 대화방을 만들 수 없습니다."
        )
    if user.staff_id is None:
        raise HTTPException(
            status_code=403, detail="직원 연결정보를 확인할 수 없습니다."
        )
    requested_user_ids = set(payload.member_ids)
    requested_user_ids.discard(user.id)
    if not requested_user_ids:
        raise HTTPException(
            status_code=422, detail="대화할 직원을 한 명 이상 선택해 주세요."
        )
    invited_users = _active_room_users(db, user.organization_id, requested_user_ids)
    all_users = [user, *invited_users]
    if len(all_users) > 100:
        raise HTTPException(
            status_code=422, detail="한 채팅방에는 최대 100명까지 참여할 수 있습니다."
        )
    if payload.name is None:
        existing_room = _matching_custom_room(
            db,
            organization_id=user.organization_id,
            current_staff_id=user.staff_id,
            member_user_ids={member.id for member in all_users},
        )
        if existing_room is not None:
            return _staff_room_response(db, existing_room, user)
    room = Room(
        organization_id=user.organization_id,
        name=payload.name or _default_staff_room_name(all_users),
        kind="custom",
        owner_staff_id=user.staff_id,
        created_by_id=user.id,
        is_test_data=settings.environment != "production",
    )
    db.add(room)
    db.flush()
    for member in all_users:
        db.add(
            RoomMembership(
                organization_id=user.organization_id,
                room_id=room.id,
                staff_id=member.staff_id,
                source="manual",
                created_by=user.id,
            )
        )
    db.flush()
    record_audit(
        db,
        actor_id=user.id,
        action="room.staff_created",
        target_type="room",
        target_id=room.id,
        details={
            "owner_staff_id": str(user.staff_id),
            "member_ids": sorted(str(member.id) for member in all_users),
        },
    )
    response = _staff_room_response(db, room, user)
    db.commit()
    await manager.send_to_users(
        {member.id for member in all_users},
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.get("/api/staff-rooms/{room_id}", response_model=StaffRoomResponse)
def get_staff_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    return _staff_room_response(db, room, user)


@app.patch("/api/staff-rooms/{room_id}", response_model=StaffRoomResponse)
async def update_staff_room(
    room_id: UUID,
    payload: StaffRoomUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    _require_custom_room_owner(room, user, db)
    before_name = room.name
    room.name = payload.name
    record_audit(
        db,
        actor_id=user.id,
        action="room.staff_renamed",
        target_type="room",
        target_id=room.id,
        details={"before_name": before_name, "name": room.name},
    )
    response = _staff_room_response(db, room, user)
    member_user_ids = room_member_user_ids(db, room.id)
    db.commit()
    await manager.send_to_users(
        member_user_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.post(
    "/api/staff-rooms/{room_id}/members",
    response_model=StaffRoomResponse,
)
async def invite_staff_room_members(
    room_id: UUID,
    payload: StaffRoomMembersRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    existing_users = _active_custom_room_users(db, room)
    existing_user_ids = {member.id for member in existing_users}
    requested_ids = set(payload.member_ids) - existing_user_ids
    invited_users = _active_room_users(db, user.organization_id, requested_ids)
    if len(existing_users) + len(invited_users) > 100:
        raise HTTPException(
            status_code=422, detail="한 채팅방에는 최대 100명까지 참여할 수 있습니다."
        )
    for invited in invited_users:
        db.add(
            RoomMembership(
                organization_id=user.organization_id,
                room_id=room.id,
                staff_id=invited.staff_id,
                source="manual",
                created_by=user.id,
            )
        )
    if invited_users:
        db.flush()
        record_audit(
            db,
            actor_id=user.id,
            action="room.staff_members_invited",
            target_type="room",
            target_id=room.id,
            details={"member_ids": sorted(str(member.id) for member in invited_users)},
        )
    response = _staff_room_response(db, room, user)
    notification_ids = existing_user_ids | {member.id for member in invited_users}
    db.commit()
    if invited_users:
        await manager.send_to_users(
            notification_ids,
            {"event": "rooms_changed", "room_id": str(room.id)},
        )
    return response


@app.delete(
    "/api/staff-rooms/{room_id}/members/{staff_id}",
    response_model=StaffRoomResponse,
)
async def remove_staff_room_member(
    room_id: UUID,
    staff_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    _require_custom_room_owner(room, user, db)
    if staff_id == user.staff_id:
        raise HTTPException(
            status_code=409, detail="방장은 먼저 다른 직원에게 방장을 넘겨야 합니다."
        )
    target_membership = db.scalar(
        select(RoomMembership).where(
            RoomMembership.room_id == room.id,
            RoomMembership.staff_id == staff_id,
            RoomMembership.left_at.is_(None),
        )
    )
    if target_membership is None:
        raise HTTPException(
            status_code=404, detail="현재 참여 중인 직원을 찾을 수 없습니다."
        )
    target_user = db.scalar(
        select(User).where(
            User.staff_id == staff_id,
            User.organization_id == user.organization_id,
        )
    )
    target_membership.left_at = utcnow()
    record_audit(
        db,
        actor_id=user.id,
        action="room.staff_member_removed",
        target_type="room",
        target_id=room.id,
        details={"staff_id": str(staff_id)},
    )
    db.flush()
    response = _staff_room_response(db, room, user)
    notification_ids = room_member_user_ids(db, room.id)
    if target_user is not None:
        notification_ids.add(target_user.id)
    db.commit()
    await manager.send_to_users(
        notification_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.post("/api/staff-rooms/{room_id}/leave", status_code=204)
async def leave_staff_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, membership = _custom_room_for_active_member(db, user, room_id)
    owner_staff_id, _owner_name = _effective_custom_room_owner(
        room,
        _active_custom_room_users(db, room),
    )
    if owner_staff_id == user.staff_id:
        raise HTTPException(
            status_code=409, detail="방장은 먼저 다른 직원에게 방장을 넘겨야 합니다."
        )
    membership.left_at = utcnow()
    record_audit(
        db,
        actor_id=user.id,
        action="room.staff_member_left",
        target_type="room",
        target_id=room.id,
        details={"staff_id": str(user.staff_id)},
    )
    notification_ids = room_member_user_ids(db, room.id) | {user.id}
    db.commit()
    await manager.send_to_users(
        notification_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return Response(status_code=204)


@app.post(
    "/api/staff-rooms/{room_id}/transfer-owner",
    response_model=StaffRoomResponse,
)
async def transfer_staff_room_owner(
    room_id: UUID,
    payload: StaffRoomOwnerTransferRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    _require_custom_room_owner(room, user, db)
    if payload.new_owner_staff_id == user.staff_id:
        return _staff_room_response(db, room, user)
    target = db.scalar(
        select(RoomMembership).where(
            RoomMembership.room_id == room.id,
            RoomMembership.staff_id == payload.new_owner_staff_id,
            RoomMembership.left_at.is_(None),
        )
    )
    if target is None:
        raise HTTPException(
            status_code=422, detail="현재 참여 중인 직원에게만 방장을 넘길 수 있습니다."
        )
    previous_owner_staff_id = room.owner_staff_id
    room.owner_staff_id = payload.new_owner_staff_id
    record_audit(
        db,
        actor_id=user.id,
        action="room.staff_owner_transferred",
        target_type="room",
        target_id=room.id,
        details={
            "previous_owner_staff_id": str(previous_owner_staff_id),
            "owner_staff_id": str(room.owner_staff_id),
        },
    )
    db.flush()
    response = _staff_room_response(db, room, user)
    member_user_ids = room_member_user_ids(db, room.id)
    db.commit()
    await manager.send_to_users(
        member_user_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return response


@app.post("/api/staff-rooms/{room_id}/close", status_code=204)
async def close_staff_room(
    room_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    room, _membership = _custom_room_for_active_member(db, user, room_id)
    _require_custom_room_owner(room, user, db)
    notification_ids = room_member_user_ids(db, room.id)
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
        actor_id=user.id,
        action="room.staff_closed",
        target_type="room",
        target_id=room.id,
        details={"closed_memberships": len(memberships), "data_retained": True},
    )
    db.commit()
    await manager.send_to_users(
        notification_ids,
        {"event": "rooms_changed", "room_id": str(room.id)},
    )
    return Response(status_code=204)


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

    if room.kind == "custom" and room.owner_staff_id not in desired_staff_ids:
        room.owner_staff_id = (
            min(desired_staff_ids, key=str) if desired_staff_ids else None
        )
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
            raise HTTPException(
                status_code=422,
                detail="조직 자동배정 방은 연결할 조직정보가 필요합니다.",
            )
        scope_unit = db.get(OrgUnit, payload.scope_unit_id)
        if (
            scope_unit is None
            or scope_unit.organization_id != organization_id
            or scope_unit.unit_type != payload.kind
            or not scope_unit.is_active
        ):
            raise HTTPException(
                status_code=422, detail="채팅방의 조직 배정규칙이 올바르지 않습니다."
            )
    elif payload.kind == "job":
        if not payload.job_code:
            raise HTTPException(
                status_code=422, detail="직종 자동배정 방은 직종을 선택해야 합니다."
            )
        job = db.get(StaffJobCode, payload.job_code)
        if job is None or not job.is_active:
            raise HTTPException(
                status_code=422, detail="채팅방의 직종 배정규칙이 올바르지 않습니다."
            )
    elif payload.kind == "custom":
        if not payload.member_ids:
            raise HTTPException(
                status_code=422,
                detail="직접 선택 방은 참여 직원을 한 명 이상 선택해야 합니다.",
            )
        users = _active_room_users(db, organization_id, set(payload.member_ids))
    elif payload.kind != "all":
        raise HTTPException(
            status_code=422, detail="지원하지 않는 채팅방 배정방식입니다."
        )
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
            detail="어르신 우선표시를 사용하려면 생활실·방·구역을 선택해야 합니다.",
        )
    floor = db.get(OrgUnit, resident_scope_unit_id)
    if (
        floor is None
        or floor.organization_id != organization_id
        or floor.unit_type != "floor"
        or not floor.is_active
    ):
        raise HTTPException(
            status_code=422,
            detail="어르신 우선표시의 생활실·방·구역이 올바르지 않습니다.",
        )
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
    if is_mentor_full_reviewer(admin):
        query = query.join(
            RoomMembership, RoomMembership.room_id == Room.id
        ).where(
            Room.is_test_data.is_(True),
            Room.kind == "custom",
            RoomMembership.staff_id == admin.staff_id,
            RoomMembership.left_at.is_(None),
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
        owner_staff_id=(
            admin.staff_id
            if payload.kind == "custom" and any(user.id == admin.id for user in users)
            else min(
                (user.staff_id for user in users if user.staff_id is not None),
                key=str,
                default=None,
            )
            if payload.kind == "custom"
            else None
        ),
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
                "scope_unit_id": str(room.scope_unit_id)
                if room.scope_unit_id
                else None,
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
        raise HTTPException(
            status_code=409, detail="같은 자동배정 규칙의 채팅방이 이미 있습니다."
        )
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
        raise HTTPException(
            status_code=409, detail="종료된 채팅방은 먼저 복구해야 합니다."
        )
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
        if room.kind == "custom" and not values["member_ids"]:
            raise HTTPException(
                status_code=422,
                detail="직원 직접 선택 방에는 참여 직원이 1명 이상 필요합니다.",
            )
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
    if room.kind == "custom":
        raise HTTPException(
            status_code=409,
            detail=(
                "직원을 직접 선택한 종료 방은 과거 참여자가 잘못 복구되지 않도록 "
                "다시 열 수 없습니다. 참여자를 확인해 새 채팅방을 만들어 주세요."
            ),
        )
    room.is_active = True
    db.flush()
    changed_user_ids: set[UUID] = set()
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
        raise HTTPException(
            status_code=403, detail="이 채팅방의 참여자를 볼 수 없습니다."
        )
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
        raise HTTPException(
            status_code=403, detail="이 채팅방에서 담당자를 지정할 수 없습니다."
        )
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
            if candidate.can_process_records or candidate.job_code in priority_job_codes
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

    service_order = {"facility": 0, "daycare": 1, "homecare": 2}
    residents.sort(
        key=lambda resident: (
            0 if is_priority(resident) else 1,
            service_order.get(resident.service_type, 99),
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
    query = (
        select(Resident)
        .where(
            Resident.organization_id == admin.organization_id,
            Resident.is_active.is_(True),
        )
        .order_by(
            case(
                (Resident.service_type == "facility", 0),
                (Resident.service_type == "daycare", 1),
                (Resident.service_type == "homecare", 2),
                else_=99,
            ),
            Resident.sort_order,
            Resident.display_name,
        )
    )
    if is_mentor_full_reviewer(admin):
        query = query.where(
            Resident.is_test_data.is_(True),
            or_(
                *[
                    Resident.internal_code.startswith(prefix)
                    for prefix in (
                        "DEV-",
                        "SYNTHETIC-",
                        "LONGITUDINAL-SYNTHETIC-",
                        "M21-",
                        "M22-",
                        "MENTOR-",
                    )
                ]
            ),
        )
    residents = db.scalars(query).all()
    return [resident_response(resident) for resident in residents]


def _filter_workdesk_resident_sources(
    residents: Sequence[Resident],
) -> list[Resident]:
    """Prefer Carefor sources while retaining isolated DEV/test residents."""

    carefor_services = {
        resident.service_type
        for resident in residents
        if resident.internal_code.startswith("SMCODI:carefor:")
    }
    return [
        resident
        for resident in residents
        if resident.is_test_data is True
        or resident.service_type not in carefor_services
        or resident.internal_code.startswith("SMCODI:carefor:")
    ]


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
            case(
                (Resident.service_type == "facility", 0),
                (Resident.service_type == "daycare", 1),
                (Resident.service_type == "homecare", 2),
                else_=99,
            ),
            Resident.sort_order,
            Resident.display_name,
        )
    )
    if getattr(processor, "_reviewer_experience", None) is not None:
        resident_query = resident_query.where(Resident.is_test_data.is_(True))
    if is_mentor_full_reviewer(processor):
        resident_query = resident_query.where(
            or_(
                *[
                    Resident.internal_code.startswith(prefix)
                    for prefix in (
                        "DEV-",
                        "SYNTHETIC-",
                        "LONGITUDINAL-SYNTHETIC-",
                        "M21-",
                        "M22-",
                        "MENTOR-",
                    )
                ]
            )
        )
    residents = db.scalars(resident_query).all()
    if settings.environment != "test":
        residents = _filter_workdesk_resident_sources(residents)
    return [resident_response(resident) for resident in residents]


def _care_planning_resident(
    db: Session,
    *,
    processor: User,
    resident_id: UUID,
) -> Resident:
    resident = db.get(Resident, resident_id)
    if (
        resident is None
        or resident.organization_id != processor.organization_id
        or not resident.is_active
    ):
        raise HTTPException(status_code=404, detail="어르신을 찾을 수 없습니다.")
    if (
        getattr(processor, "_reviewer_experience", None) is not None
        and not resident.is_test_data
    ):
        raise HTTPException(
            status_code=403,
            detail="심사 체험에서는 비식별 시험 어르신만 사용할 수 있습니다.",
        )
    if is_mentor_full_reviewer(processor) and not mentor_visible_resident_code(
        resident.internal_code
    ):
        raise HTTPException(
            status_code=403,
            detail="멘토 검토 환경에서는 명시적으로 승인된 합성 어르신만 볼 수 있습니다.",
        )
    return resident


def _care_planning_state_response(
    *,
    resident: Resident,
    document_type: str,
    state: ResidentCarePlanningState | None,
    as_of: date,
) -> ResidentCarePlanningStateResponse:
    assessment_date = state.assessment_date if state else None
    valid_until = state.valid_until if state else None
    next_due_date = state.next_due_date if state else None
    cycle_months = state.cycle_months if state else 6
    reasons = care_planning_candidate_reasons(
        as_of=as_of,
        assessment_date=assessment_date,
        next_due_date=next_due_date,
        reassess_on_state_change=(
            state.reassess_on_state_change if state else True
        ),
        state_change_triggered=state.state_change_triggered if state else False,
        status=state.status if state else "not_started",
    )
    return ResidentCarePlanningStateResponse(
        id=state.id if state else None,
        resident_id=resident.id,
        resident_name=resident.display_name,
        document_type=document_type,
        assessment_date=assessment_date,
        valid_until=valid_until,
        next_due_date=next_due_date,
        cycle_months=cycle_months,
        reassess_on_state_change=(
            state.reassess_on_state_change if state else True
        ),
        state_change_triggered=state.state_change_triggered if state else False,
        change_reason=state.change_reason if state else None,
        status=state.status if state else "not_started",
        evidence_refs=list(state.evidence_refs or []) if state else [],
        known_facts=list(state.known_facts or []) if state else [],
        questions_required=list(state.questions_required or []) if state else [],
        professional_review_fields=(
            list(state.professional_review_fields or []) if state else []
        ),
        author_confirmed=state.author_confirmed if state else False,
        author_verified_by_name=(
            state.author_verified_by.full_name
            if state is not None and state.author_verified_by is not None
            else None
        ),
        author_verified_at=state.author_verified_at if state else None,
        version=state.version if state else 0,
        is_candidate=bool(reasons),
        candidate_reasons=reasons,
        updated_at=state.updated_at if state else None,
    )


@app.get(
    "/api/workdesk/residents/{resident_id}/care-planning",
    response_model=ResidentCarePlanningResponse,
)
def get_resident_care_planning(
    resident_id: UUID,
    as_of: date | None = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    states = db.scalars(
        select(ResidentCarePlanningState).where(
            ResidentCarePlanningState.organization_id == processor.organization_id,
            ResidentCarePlanningState.resident_id == resident.id,
        )
    ).all()
    by_type = {state.document_type: state for state in states}
    reference_date = as_of or date.today()
    return ResidentCarePlanningResponse(
        resident=resident_response(resident),
        as_of=reference_date,
        operating_cycle_notice=OPERATING_CYCLE_NOTICE,
        legal_standard_notice=LEGAL_STANDARD_NOTICE,
        items=[
            _care_planning_state_response(
                resident=resident,
                document_type=document_type,
                state=by_type.get(document_type),
                as_of=reference_date,
            )
            for document_type in CARE_PLANNING_DOCUMENT_TYPES
        ],
    )


@app.put(
    "/api/workdesk/residents/{resident_id}/care-planning/{document_type}",
    response_model=ResidentCarePlanningStateResponse,
)
def update_resident_care_planning(
    resident_id: UUID,
    document_type: CarePlanningDocumentType,
    payload: ResidentCarePlanningStateUpdate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    if payload.author_confirmed and payload.status != "confirmed":
        raise HTTPException(
            status_code=422,
            detail="작성자 확인을 완료하려면 상태도 '확정'으로 선택해 주세요.",
        )
    if payload.status == "confirmed" and not payload.author_confirmed:
        raise HTTPException(
            status_code=422,
            detail="확정 상태에는 작성자 확인이 필요합니다.",
        )
    if payload.author_confirmed and payload.assessment_date is None:
        raise HTTPException(
            status_code=422,
            detail="작성자 확인 전 평가 기준일을 입력해 주세요.",
        )
    if payload.state_change_triggered and not payload.change_reason:
        raise HTTPException(
            status_code=422,
            detail="상태 변경 후보에는 변경 사유를 입력해 주세요.",
        )

    valid_until, next_due_date = normalized_due_dates(
        assessment_date=payload.assessment_date,
        valid_until=payload.valid_until,
        next_due_date=payload.next_due_date,
        cycle_months=payload.cycle_months,
    )
    state = db.scalar(
        select(ResidentCarePlanningState).where(
            ResidentCarePlanningState.organization_id == processor.organization_id,
            ResidentCarePlanningState.resident_id == resident.id,
            ResidentCarePlanningState.document_type == document_type,
        )
    )
    if state is None:
        state = ResidentCarePlanningState(
            organization_id=processor.organization_id,
            resident_id=resident.id,
            document_type=document_type,
            is_test_data=resident.is_test_data,
        )
        db.add(state)
    else:
        state.version += 1

    state.assessment_date = payload.assessment_date
    state.valid_until = valid_until
    state.next_due_date = next_due_date
    state.cycle_months = payload.cycle_months
    state.reassess_on_state_change = payload.reassess_on_state_change
    state.state_change_triggered = payload.state_change_triggered
    state.change_reason = payload.change_reason
    state.status = payload.status
    state.evidence_refs = list(payload.evidence_refs)
    state.known_facts = list(payload.known_facts)
    state.questions_required = list(payload.questions_required)
    state.professional_review_fields = list(payload.professional_review_fields)
    state.author_confirmed = payload.author_confirmed
    state.author_verified_by_id = processor.id if payload.author_confirmed else None
    state.author_verified_at = utcnow() if payload.author_confirmed else None
    db.commit()
    db.refresh(state)
    return _care_planning_state_response(
        resident=resident,
        document_type=document_type,
        state=state,
        as_of=date.today(),
    )


def _new_admission_field_map(bundle_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        field["field_key"]: field
        for field in bundle_payload.get("fields", [])
        if isinstance(field, dict) and isinstance(field.get("field_key"), str)
    }


def _new_admission_field_changes(
    previous_payload: dict[str, Any] | None,
    current_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    previous_fields = _new_admission_field_map(previous_payload or {})
    current_fields = _new_admission_field_map(current_payload)
    changes: list[dict[str, Any]] = []
    for field_key in sorted(previous_fields.keys() | current_fields.keys()):
        before = previous_fields.get(field_key)
        after = current_fields.get(field_key)
        if before == after:
            continue
        changes.append(
            {
                "field_key": field_key,
                "before_value": before.get("value") if before else None,
                "after_value": after.get("value") if after else None,
                "before_status": before.get("status") if before else None,
                "after_status": after.get("status") if after else None,
                "before_evidence_refs": list(before.get("evidence_refs", []))
                if before
                else [],
                "after_evidence_refs": list(after.get("evidence_refs", []))
                if after
                else [],
            }
        )
    previous_documents = (previous_payload or {}).get("document_texts", {})
    current_documents = current_payload.get("document_texts", {})
    for document_type in sorted(set(previous_documents) | set(current_documents)):
        before_text = previous_documents.get(document_type)
        after_text = current_documents.get(document_type)
        if before_text == after_text:
            continue
        changes.append(
            {
                "field_key": f"document_text.{document_type}",
                "before_value": before_text,
                "after_value": after_text,
                "before_status": "saved_draft" if before_text else None,
                "after_status": "saved_draft" if after_text else None,
                "before_evidence_refs": [],
                "after_evidence_refs": [],
            }
        )
    previous_form_values = (previous_payload or {}).get("form_values", {})
    current_form_values = current_payload.get("form_values", {})
    for document_type in sorted(
        set(previous_form_values) | set(current_form_values)
    ):
        before_values = previous_form_values.get(document_type, {})
        after_values = current_form_values.get(document_type, {})
        for value_key in sorted(set(before_values) | set(after_values)):
            before_value = before_values.get(value_key)
            after_value = after_values.get(value_key)
            if before_value == after_value:
                continue
            changes.append(
                {
                    "field_key": f"form_value.{document_type}.{value_key}",
                    "before_value": before_value,
                    "after_value": after_value,
                    "before_status": "saved_form_value" if before_value is not None else None,
                    "after_status": "saved_form_value" if after_value is not None else None,
                    "before_evidence_refs": [],
                    "after_evidence_refs": [],
                }
            )
    return changes


def _new_admission_document_reviews(
    *,
    bundle_payload: dict[str, Any],
    reviewed_document_types: list[str],
    processor: User,
    previous_payload: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """Authenticate four-assessment review state and reset edited reviews."""

    requested = set(reviewed_document_types)
    unknown = requested - set(ASSESSMENT_DOCUMENT_TYPES)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail="기초사정 4종만 검토 완료로 저장할 수 있습니다.",
        )
    previous_payload = previous_payload or {}
    previous_reviews = previous_payload.get("document_reviews", {})
    previous_values = previous_payload.get("form_values", {})
    current_values = bundle_payload.get("form_values", {})
    now = utcnow().isoformat()
    reviews: dict[str, dict[str, Any]] = {}
    for document_type in ASSESSMENT_DOCUMENT_TYPES:
        if document_type in requested:
            reviews[document_type] = {
                "state": "reviewed",
                "reviewed_by_id": str(processor.id),
                "reviewed_by_name": processor.full_name,
                "reviewed_at": now,
            }
            continue
        previous_review = previous_reviews.get(document_type, {})
        if (
            previous_review.get("state") == "reviewed"
            and previous_values.get(document_type, {})
            == current_values.get(document_type, {})
        ):
            reviews[document_type] = dict(previous_review)
            continue
        reviews[document_type] = {
            "state": "pending",
            "reviewed_by_id": None,
            "reviewed_by_name": None,
            "reviewed_at": None,
        }
    return reviews


def _new_admission_confirmations(
    *,
    bundle_payload: dict[str, Any],
    confirmed_field_keys: list[str],
    processor: User,
    previous_payload: dict[str, Any] | None = None,
    previous_confirmations: dict[str, dict[str, Any]] | None = None,
) -> dict[str, dict[str, Any]]:
    current_fields = _new_admission_field_map(bundle_payload)
    previous_fields = _new_admission_field_map(previous_payload or {})
    requested = set(confirmed_field_keys)
    unknown = requested - current_fields.keys()
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"존재하지 않는 필드는 확인할 수 없습니다: {', '.join(sorted(unknown))}",
        )

    resolved_statuses = {"confirmed", "reusable", "derivable"}
    unresolved_requested = sorted(
        field_key
        for field_key in requested
        if current_fields[field_key].get("status") not in resolved_statuses
        or current_fields[field_key].get("value") in (None, "")
    )
    if unresolved_requested:
        raise HTTPException(
            status_code=422,
            detail=(
                "값과 근거가 확정되지 않은 필드는 확인 완료로 저장할 수 없습니다: "
                + ", ".join(unresolved_requested)
            ),
        )

    now = utcnow()
    previous_confirmations = previous_confirmations or {}
    confirmations: dict[str, dict[str, Any]] = {}
    for field_key, field in current_fields.items():
        if field_key in requested:
            confirmations[field_key] = {
                "state": "confirmed",
                "confirmed_by_id": str(processor.id),
                "confirmed_by_name": processor.full_name,
                "confirmed_at": now.isoformat(),
            }
            continue
        previous_confirmation = previous_confirmations.get(field_key, {})
        if (
            previous_fields.get(field_key) == field
            and previous_confirmation.get("state") == "confirmed"
        ):
            confirmations[field_key] = dict(previous_confirmation)
            continue
        confirmations[field_key] = {
            "state": "needs_confirmation",
            "confirmed_by_id": None,
            "confirmed_by_name": None,
            "confirmed_at": None,
        }
    return confirmations


def _assessment_cycle_field_changes(
    previous_items: list[dict[str, Any]] | None,
    current_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    before = {
        item.get("field_key"): item
        for item in (previous_items or [])
        if item.get("field_key")
    }
    after = {
        item.get("field_key"): item
        for item in current_items
        if item.get("field_key")
    }
    changes: list[dict[str, Any]] = []
    for field_key in sorted(set(before) | set(after)):
        previous = before.get(field_key)
        current = after.get(field_key)
        if previous == current:
            continue
        changes.append(
            {
                "field_key": field_key,
                "before_value": previous.get("current_value") if previous else None,
                "after_value": current.get("current_value") if current else None,
                "before_classification": (
                    previous.get("classification") if previous else None
                ),
                "after_classification": (
                    current.get("classification") if current else None
                ),
                "before_evidence_refs": (
                    list(previous.get("evidence_refs", [])) if previous else []
                ),
                "after_evidence_refs": (
                    list(current.get("evidence_refs", [])) if current else []
                ),
                "before_selected": bool(
                    previous and previous.get("selected_for_plan_review")
                ),
                "after_selected": bool(
                    current and current.get("selected_for_plan_review")
                ),
            }
        )
    return changes


def _assessment_cycle_revision_response(
    revision: ResidentAssessmentCycleRevision,
) -> ResidentAssessmentCycleRevisionResponse:
    return ResidentAssessmentCycleRevisionResponse(
        id=revision.id,
        revision=revision.revision,
        supersedes_revision=revision.supersedes_revision,
        status=revision.status,
        baseline_sources=revision.baseline_sources,
        current_facts=revision.current_facts,
        comparison_items=revision.comparison_items,
        selected_plan_candidate_keys=revision.selected_plan_candidate_keys,
        confirmation_questions=revision.confirmation_questions,
        field_changes=revision.field_changes,
        created_by_name=revision.created_by.full_name,
        created_at=revision.created_at,
    )


def _assessment_cycle_response(
    db: Session,
    *,
    cycle: ResidentAssessmentCycle,
    resident: Resident,
) -> ResidentAssessmentCycleResponse:
    revisions = db.scalars(
        select(ResidentAssessmentCycleRevision)
        .options(selectinload(ResidentAssessmentCycleRevision.created_by))
        .where(ResidentAssessmentCycleRevision.cycle_id == cycle.id)
        .order_by(ResidentAssessmentCycleRevision.revision)
    ).all()
    return ResidentAssessmentCycleResponse(
        id=cycle.id,
        resident_id=resident.id,
        resident_name=resident.display_name,
        reason=cycle.reason,
        cycle_number=cycle.cycle_number,
        assessment_date=cycle.assessment_date,
        period_start=cycle.period_start,
        period_end=cycle.period_end,
        source_provider=cycle.source_provider,
        previous_cycle_id=cycle.previous_cycle_id,
        current_revision=cycle.current_revision,
        status=cycle.status,
        external_transfer_allowed=False,
        is_test_data=cycle.is_test_data,
        created_by_name=cycle.created_by.full_name,
        created_at=cycle.created_at,
        updated_at=cycle.updated_at,
        revisions=[_assessment_cycle_revision_response(item) for item in revisions],
    )


def _assessment_cycle_record(
    db: Session,
    *,
    processor: User,
    resident_id: UUID,
    cycle_id: UUID,
) -> tuple[Resident, ResidentAssessmentCycle]:
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    cycle = db.scalar(
        select(ResidentAssessmentCycle)
        .options(selectinload(ResidentAssessmentCycle.created_by))
        .where(
            ResidentAssessmentCycle.id == cycle_id,
            ResidentAssessmentCycle.organization_id == processor.organization_id,
            ResidentAssessmentCycle.resident_id == resident.id,
        )
    )
    if cycle is None:
        raise HTTPException(status_code=404, detail="평가 회차를 찾을 수 없습니다.")
    return resident, cycle


@app.get(
    "/api/workdesk/residents/{resident_id}/assessment-cycles",
    response_model=ResidentAssessmentCycleListResponse,
)
def list_resident_assessment_cycles(
    resident_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    cycles = db.scalars(
        select(ResidentAssessmentCycle)
        .where(
            ResidentAssessmentCycle.organization_id == processor.organization_id,
            ResidentAssessmentCycle.resident_id == resident.id,
        )
        .order_by(ResidentAssessmentCycle.cycle_number.desc())
    ).all()
    return ResidentAssessmentCycleListResponse(
        resident_id=resident.id,
        resident_name=resident.display_name,
        source_provider_notice=(
            "본선에서는 직원이 원본을 확인해 항목별로 입력합니다. "
            "Carefor 읽기 전용 공급자는 별도 승인 전 연결하지 않습니다."
        ),
        operating_cycle_notice=(
            "6개월 기본 주기는 센터 운영 원칙이며 법령·공단 의무로 단정하지 않습니다."
        ),
        items=[
            ResidentAssessmentCycleSummaryResponse(
                id=cycle.id,
                reason=cycle.reason,
                cycle_number=cycle.cycle_number,
                assessment_date=cycle.assessment_date,
                current_revision=cycle.current_revision,
                status=cycle.status,
                previous_cycle_id=cycle.previous_cycle_id,
                updated_at=cycle.updated_at,
            )
            for cycle in cycles
        ],
    )


@app.post(
    "/api/workdesk/residents/{resident_id}/assessment-cycles",
    response_model=ResidentAssessmentCycleResponse,
    status_code=201,
)
def create_resident_assessment_cycle(
    resident_id: UUID,
    request_payload: ResidentAssessmentCycleRevisionCreate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    payload = request_payload.payload
    if payload.revision != 1 or payload.supersedes_revision is not None:
        raise HTTPException(
            status_code=422,
            detail="첫 평가 회차는 수정본 1이며 이전 수정본 번호가 없어야 합니다.",
        )
    if payload.source_provider != "staff_manual":
        raise HTTPException(
            status_code=422,
            detail="현재는 직원 직접입력 공급자만 사용할 수 있습니다.",
        )
    previous_cycle = None
    if payload.previous_cycle_id is not None:
        previous_cycle = db.scalar(
            select(ResidentAssessmentCycle).where(
                ResidentAssessmentCycle.id == payload.previous_cycle_id,
                ResidentAssessmentCycle.organization_id == processor.organization_id,
                ResidentAssessmentCycle.resident_id == resident.id,
            )
        )
        if previous_cycle is None:
            raise HTTPException(
                status_code=422,
                detail="같은 어르신의 이전 평가 회차만 연결할 수 있습니다.",
            )
    try:
        comparison = build_assessment_cycle_comparison(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    status_value = (
        "needs_confirmation"
        if comparison["confirmation_questions"]
        else payload.output_status
    )
    cycle_number = (
        db.scalar(
            select(func.max(ResidentAssessmentCycle.cycle_number)).where(
                ResidentAssessmentCycle.organization_id == processor.organization_id,
                ResidentAssessmentCycle.resident_id == resident.id,
            )
        )
        or 0
    ) + 1
    cycle = ResidentAssessmentCycle(
        organization_id=processor.organization_id,
        resident_id=resident.id,
        reason=payload.reason,
        cycle_number=cycle_number,
        assessment_date=payload.assessment_date,
        period_start=payload.period_start,
        period_end=payload.period_end,
        source_provider="staff_manual",
        previous_cycle_id=previous_cycle.id if previous_cycle else None,
        current_revision=1,
        status=status_value,
        external_transfer_allowed=False,
        is_test_data=resident.is_test_data,
        created_by_id=processor.id,
    )
    db.add(cycle)
    try:
        db.flush()
        db.add(
            ResidentAssessmentCycleRevision(
                cycle_id=cycle.id,
                revision=1,
                supersedes_revision=None,
                status=status_value,
                baseline_sources=[
                    source.model_dump(mode="json")
                    for source in payload.baseline_sources
                ],
                current_facts=[
                    fact.model_dump(mode="json") for fact in payload.current_facts
                ],
                comparison_items=comparison["comparison_items"],
                selected_plan_candidate_keys=comparison[
                    "selected_plan_candidate_keys"
                ],
                confirmation_questions=comparison["confirmation_questions"],
                field_changes=_assessment_cycle_field_changes(
                    None, comparison["comparison_items"]
                ),
                created_by_id=processor.id,
            )
        )
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="평가 회차 번호가 겹쳤습니다. 목록을 다시 불러와 주세요.",
        ) from error
    cycle = db.scalar(
        select(ResidentAssessmentCycle)
        .options(selectinload(ResidentAssessmentCycle.created_by))
        .where(ResidentAssessmentCycle.id == cycle.id)
    )
    assert cycle is not None
    return _assessment_cycle_response(db, cycle=cycle, resident=resident)


@app.get(
    "/api/workdesk/residents/{resident_id}/assessment-cycles/{cycle_id}",
    response_model=ResidentAssessmentCycleResponse,
)
def get_resident_assessment_cycle(
    resident_id: UUID,
    cycle_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, cycle = _assessment_cycle_record(
        db,
        processor=processor,
        resident_id=resident_id,
        cycle_id=cycle_id,
    )
    return _assessment_cycle_response(db, cycle=cycle, resident=resident)


@app.post(
    "/api/workdesk/residents/{resident_id}/assessment-cycles/{cycle_id}/revisions",
    response_model=ResidentAssessmentCycleResponse,
    status_code=201,
)
def create_resident_assessment_cycle_revision(
    resident_id: UUID,
    cycle_id: UUID,
    request_payload: ResidentAssessmentCycleRevisionCreate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    cycle = db.scalar(
        select(ResidentAssessmentCycle)
        .where(
            ResidentAssessmentCycle.id == cycle_id,
            ResidentAssessmentCycle.organization_id == processor.organization_id,
            ResidentAssessmentCycle.resident_id == resident.id,
        )
        .with_for_update()
    )
    if cycle is None:
        raise HTTPException(status_code=404, detail="평가 회차를 찾을 수 없습니다.")
    payload = request_payload.payload
    expected_revision = cycle.current_revision + 1
    if (
        payload.revision != expected_revision
        or payload.supersedes_revision != cycle.current_revision
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"현재 수정본 {cycle.current_revision}을 기준으로 "
                f"수정본 {expected_revision}을 저장해 주세요."
            ),
        )
    if payload.source_provider != "staff_manual":
        raise HTTPException(
            status_code=422,
            detail="현재는 직원 직접입력 공급자만 사용할 수 있습니다.",
        )
    if (
        payload.reason != cycle.reason
        or payload.assessment_date != cycle.assessment_date
        or payload.period_start != cycle.period_start
        or payload.period_end != cycle.period_end
        or payload.previous_cycle_id != cycle.previous_cycle_id
    ):
        raise HTTPException(
            status_code=422,
            detail="작성 사유·기준일·기간·이전 회차는 수정본에서 변경할 수 없습니다.",
        )
    previous = db.scalar(
        select(ResidentAssessmentCycleRevision)
        .where(
            ResidentAssessmentCycleRevision.cycle_id == cycle.id,
            ResidentAssessmentCycleRevision.revision == cycle.current_revision,
        )
        .with_for_update()
    )
    if previous is None:
        raise HTTPException(status_code=409, detail="현재 수정 이력을 찾을 수 없습니다.")
    try:
        comparison = build_assessment_cycle_comparison(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    status_value = (
        "needs_confirmation"
        if comparison["confirmation_questions"]
        else payload.output_status
    )
    db.add(
        ResidentAssessmentCycleRevision(
            cycle_id=cycle.id,
            revision=payload.revision,
            supersedes_revision=payload.supersedes_revision,
            status=status_value,
            baseline_sources=[
                source.model_dump(mode="json") for source in payload.baseline_sources
            ],
            current_facts=[
                fact.model_dump(mode="json") for fact in payload.current_facts
            ],
            comparison_items=comparison["comparison_items"],
            selected_plan_candidate_keys=comparison[
                "selected_plan_candidate_keys"
            ],
            confirmation_questions=comparison["confirmation_questions"],
            field_changes=_assessment_cycle_field_changes(
                previous.comparison_items, comparison["comparison_items"]
            ),
            created_by_id=processor.id,
        )
    )
    cycle.current_revision = payload.revision
    cycle.status = status_value
    cycle.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="같은 수정본이 이미 저장되어 평가 회차를 다시 불러와야 합니다.",
        ) from error
    cycle = db.scalar(
        select(ResidentAssessmentCycle)
        .options(selectinload(ResidentAssessmentCycle.created_by))
        .where(ResidentAssessmentCycle.id == cycle.id)
    )
    assert cycle is not None
    return _assessment_cycle_response(db, cycle=cycle, resident=resident)


def _new_admission_revision_status(
    confirmations: dict[str, dict[str, Any]],
) -> str:
    return (
        "needs_confirmation"
        if any(value.get("state") != "confirmed" for value in confirmations.values())
        else "draft"
    )


def _new_admission_revision_response(
    revision: NewAdmissionDraftRevision,
) -> NewAdmissionDraftRevisionResponse:
    return NewAdmissionDraftRevisionResponse(
        id=revision.id,
        revision=revision.revision,
        supersedes_revision=revision.supersedes_revision,
        status=revision.status,
        bundle=revision.bundle_payload,
        field_evidence_refs=revision.field_evidence_refs,
        field_changes=revision.field_changes,
        field_confirmations=revision.field_confirmations,
        created_by_name=revision.created_by.full_name,
        created_at=revision.created_at,
    )


def _new_admission_draft_response(
    db: Session,
    *,
    draft: NewAdmissionDraftRecord,
    resident: Resident,
) -> NewAdmissionDraftResponse:
    revisions = db.scalars(
        select(NewAdmissionDraftRevision)
        .options(selectinload(NewAdmissionDraftRevision.created_by))
        .where(NewAdmissionDraftRevision.draft_id == draft.id)
        .order_by(NewAdmissionDraftRevision.revision)
    ).all()
    return NewAdmissionDraftResponse(
        id=draft.id,
        resident_id=resident.id,
        resident_name=resident.display_name,
        case_ref=draft.case_ref,
        processing_scope=draft.processing_scope,
        external_transfer_allowed=False,
        current_revision=draft.current_revision,
        status=draft.status,
        is_test_data=draft.is_test_data,
        created_by_name=draft.created_by.full_name,
        created_at=draft.created_at,
        updated_at=draft.updated_at,
        revisions=[_new_admission_revision_response(item) for item in revisions],
    )


def _new_admission_draft_record(
    db: Session,
    *,
    processor: User,
    resident_id: UUID,
    draft_id: UUID,
) -> tuple[Resident, NewAdmissionDraftRecord]:
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    draft = db.scalar(
        select(NewAdmissionDraftRecord)
        .options(selectinload(NewAdmissionDraftRecord.created_by))
        .where(
            NewAdmissionDraftRecord.id == draft_id,
            NewAdmissionDraftRecord.organization_id == processor.organization_id,
            NewAdmissionDraftRecord.resident_id == resident.id,
        )
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="신규입소자 초안을 찾을 수 없습니다.")
    return resident, draft


@app.get(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts",
    response_model=NewAdmissionDraftListResponse,
)
def list_new_admission_drafts(
    resident_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    drafts = db.scalars(
        select(NewAdmissionDraftRecord)
        .where(
            NewAdmissionDraftRecord.organization_id == processor.organization_id,
            NewAdmissionDraftRecord.resident_id == resident.id,
        )
        .order_by(NewAdmissionDraftRecord.updated_at.desc())
    ).all()
    current_revisions = db.scalars(
        select(NewAdmissionDraftRevision)
        .join(
            NewAdmissionDraftRecord,
            NewAdmissionDraftRecord.id == NewAdmissionDraftRevision.draft_id,
        )
        .where(
            NewAdmissionDraftRecord.id.in_([draft.id for draft in drafts]),
            NewAdmissionDraftRevision.revision
            == NewAdmissionDraftRecord.current_revision,
        )
    ).all() if drafts else []
    revision_by_draft_id = {
        revision.draft_id: revision for revision in current_revisions
    }

    def summary_payload(draft: NewAdmissionDraftRecord) -> dict[str, Any]:
        revision = revision_by_draft_id.get(draft.id)
        bundle_payload = revision.bundle_payload if revision is not None else {}
        reviews = bundle_payload.get("document_reviews", {})
        return {
            "id": draft.id,
            "case_ref": draft.case_ref,
            "current_revision": draft.current_revision,
            "status": draft.status,
            "reason": bundle_payload.get("reason", "new_admission"),
            "assessment_date": bundle_payload.get("assessment_date"),
            "reviewed_assessment_count": sum(
                1
                for document_type in ASSESSMENT_DOCUMENT_TYPES
                if reviews.get(document_type, {}).get("state") == "reviewed"
            ),
            "updated_at": draft.updated_at,
        }

    return NewAdmissionDraftListResponse(
        resident_id=resident.id,
        resident_name=resident.display_name,
        items=[
            NewAdmissionDraftSummaryResponse(**summary_payload(draft))
            for draft in drafts
        ],
    )


@app.post(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts",
    response_model=NewAdmissionDraftResponse,
    status_code=201,
)
def create_new_admission_draft(
    resident_id: UUID,
    payload: NewAdmissionDraftRevisionCreate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    draft = _add_new_admission_draft(
        db,
        resident=resident,
        processor=processor,
        payload=payload,
    )
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="같은 신규입소 사례 초안이 이미 있습니다.",
        ) from error
    draft = db.scalar(
        select(NewAdmissionDraftRecord)
        .options(selectinload(NewAdmissionDraftRecord.created_by))
        .where(NewAdmissionDraftRecord.id == draft.id)
    )
    assert draft is not None
    return _new_admission_draft_response(db, draft=draft, resident=resident)


def _add_new_admission_draft(
    db: Session,
    *,
    resident: Resident,
    processor: User,
    payload: NewAdmissionDraftRevisionCreate,
) -> NewAdmissionDraftRecord:
    """초안과 첫 개정본을 현재 트랜잭션에 추가하고 커밋은 호출자에게 맡긴다."""
    bundle = payload.bundle
    if bundle.revision != 1 or bundle.supersedes_revision is not None:
        raise HTTPException(
            status_code=422,
            detail="첫 초안은 revision 1이며 supersedes_revision이 없어야 합니다.",
        )
    if bundle.processing_scope == "deidentified_dev" and not resident.is_test_data:
        raise HTTPException(
            status_code=422,
            detail="비식별 DEV 초안은 시험 어르신에게만 저장할 수 있습니다.",
        )
    bundle_payload = bundle.model_dump(mode="json")
    bundle_payload["document_reviews"] = _new_admission_document_reviews(
        bundle_payload=bundle_payload,
        reviewed_document_types=payload.reviewed_document_types,
        processor=processor,
    )
    bundle_payload = NewAdmissionDraftBundle.model_validate(
        bundle_payload
    ).model_dump(mode="json")
    confirmations = _new_admission_confirmations(
        bundle_payload=bundle_payload,
        confirmed_field_keys=payload.confirmed_field_keys,
        processor=processor,
    )
    revision_status = _new_admission_revision_status(confirmations)
    draft = NewAdmissionDraftRecord(
        organization_id=processor.organization_id,
        resident_id=resident.id,
        case_ref=bundle.case_ref,
        processing_scope=bundle.processing_scope,
        external_transfer_allowed=False,
        current_revision=1,
        status=revision_status,
        is_test_data=resident.is_test_data,
        created_by_id=processor.id,
    )
    db.add(draft)
    db.flush()
    db.add(
        NewAdmissionDraftRevision(
            draft_id=draft.id,
            revision=1,
            supersedes_revision=None,
            status=revision_status,
            bundle_payload=bundle_payload,
            field_evidence_refs={
                field["field_key"]: list(field.get("evidence_refs", []))
                for field in bundle_payload["fields"]
            },
            field_changes=_new_admission_field_changes(None, bundle_payload),
            field_confirmations=confirmations,
            created_by_id=processor.id,
        )
    )
    return draft


async def _parse_assessment_material_uploads(
    *,
    resident: Resident,
    reason: str,
    document_kinds: list[str],
    files: list[UploadFile],
    source_prefix: str,
) -> tuple[list[ParsedAssessmentMaterial], list[tuple[bytes, str]]]:
    """Parse assessment uploads locally without saving files or changing the DB."""

    if not files or len(files) > MAX_ASSESSMENT_FILES:
        raise HTTPException(
            status_code=422,
            detail=f"PDF·이미지 자료는 1개 이상 {MAX_ASSESSMENT_FILES}개 이하로 제출해 주세요.",
        )
    if len(document_kinds) != len(files):
        raise HTTPException(status_code=422, detail="자료 종류와 파일 개수가 맞지 않습니다.")

    parsed_materials: list[ParsedAssessmentMaterial] = []
    stored_contents: list[tuple[bytes, str]] = []
    for index, (upload, document_kind) in enumerate(
        zip(files, document_kinds, strict=True), start=1
    ):
        try:
            selected_kinds = parse_document_kind_selection(
                document_kind,
                reason=reason,
            )
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail=f"제출 자료 {index}: {error}",
            ) from error
        mime_type = (upload.content_type or "").lower()
        if mime_type not in {
            "application/pdf",
            "application/octet-stream",
            "image/jpeg",
            "image/png",
        }:
            raise HTTPException(
                status_code=422,
                detail=f"제출 자료 {index}은 PDF·JPG·PNG 파일만 사용할 수 있습니다.",
            )
        content = await upload.read(settings.max_attachment_bytes + 1)
        if not content or len(content) > settings.max_attachment_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"제출 자료 {index}의 크기를 확인해 주세요.",
            )
        material_source_prefix = f"{source_prefix}-{index:02d}"
        try:
            if content.startswith(b"%PDF"):
                _, page_texts = extract_pdf_page_texts(content)
                missing_page_indexes = [
                    page_index
                    for page_index, text in enumerate(page_texts)
                    if not text.strip()
                ]
                if missing_page_indexes:
                    with tempfile.TemporaryDirectory(
                        prefix="assessment-pdf-"
                    ) as temporary_directory:
                        rendered_pages = await asyncio.to_thread(
                            render_pdf_pages,
                            content,
                            output_directory=Path(temporary_directory),
                            page_indexes=missing_page_indexes,
                        )
                        for page_index in missing_page_indexes:
                            try:
                                page_texts[page_index] = await asyncio.to_thread(
                                    extract_assessment_document_text,
                                    rendered_pages[page_index],
                                    room_name="기초사정 제출 자료",
                                    resident_name=resident.display_name,
                                )
                            except OcrError as error:
                                raise ValueError(
                                    f"{page_index + 1}쪽 스캔 글자를 판독하지 못했습니다. "
                                    "원본을 확인하거나 다시 제출해 주세요."
                                ) from error
                parsed_materials.extend(
                    build_parsed_materials(
                        page_texts=page_texts,
                        source_prefix=material_source_prefix,
                        selected_kinds=selected_kinds,
                        reason=reason,
                        mime_type="application/pdf",
                        size_bytes=len(content),
                    )
                )
                extension = ".pdf"
            else:
                normalized_mime = (
                    "image/png" if content.startswith(b"\x89PNG") else "image/jpeg"
                )
                if not _has_expected_signature(normalized_mime, content):
                    raise ValueError("이미지 파일 형식을 확인해 주세요.")
                extension = ".png" if normalized_mime == "image/png" else ".jpg"
                temporary_path: Path | None = None
                try:
                    with tempfile.NamedTemporaryFile(
                        delete=False, suffix=extension
                    ) as temporary:
                        temporary.write(content)
                        temporary_path = Path(temporary.name)
                    extracted_text = await asyncio.to_thread(
                        extract_assessment_document_text,
                        temporary_path,
                        room_name="기초사정 제출 자료",
                        resident_name=resident.display_name,
                    )
                finally:
                    if temporary_path is not None:
                        temporary_path.unlink(missing_ok=True)
                if not extracted_text.strip():
                    raise ValueError("이미지에서 글자를 확인할 수 없습니다.")
                parsed_materials.extend(
                    build_parsed_materials(
                        page_texts=[extracted_text],
                        source_prefix=material_source_prefix,
                        selected_kinds=selected_kinds,
                        reason=reason,
                        mime_type=normalized_mime,
                        size_bytes=len(content),
                    )
                )
        except (ValueError, OcrError) as error:
            raise HTTPException(
                status_code=422,
                detail=f"제출 자료 {index}: {error}",
            ) from error
        stored_contents.append((content, extension))
    return parsed_materials, stored_contents


@app.get(
    "/api/workdesk/residents/{resident_id}/assessment-chat-evidence/preview",
    response_model=AssessmentChatEvidencePreviewResponse,
)
def preview_assessment_chat_evidence(
    resident_id: UUID,
    reason: str,
    assessment_date: str,
    period_start: str | None = None,
    period_end: str | None = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    if not resident.is_test_data:
        raise HTTPException(
            status_code=422,
            detail="현재 DEV에서는 코드화된 합성 어르신 자료만 분석할 수 있습니다.",
        )
    if reason not in {
        "new_admission",
        "periodic_reassessment",
        "state_change_reassessment",
        "staff_review",
    }:
        raise HTTPException(status_code=422, detail="작성 사유를 다시 선택해 주세요.")
    try:
        assessment_day = date.fromisoformat(assessment_date)
        start_day = date.fromisoformat(period_start) if period_start else None
        end_day = date.fromisoformat(period_end) if period_end else None
    except ValueError as error:
        raise HTTPException(status_code=422, detail="날짜 형식을 확인해 주세요.") from error
    if reason == "new_admission":
        if start_day is None:
            start_day = min(resident.created_at.date(), assessment_day)
        if end_day is None:
            end_day = assessment_day
    elif start_day is None or end_day is None:
        raise HTTPException(
            status_code=422,
            detail="재평가는 이전 작성일 이후의 확인 기간을 입력해 주세요.",
        )
    assert start_day is not None and end_day is not None
    if start_day > end_day:
        raise HTTPException(status_code=422, detail="확인 기간의 시작일이 종료일보다 늦습니다.")
    items = collect_resident_assessment_chat_evidence(
        db,
        organization_id=processor.organization_id,
        resident_id=resident.id,
        start_day=start_day,
        end_day=end_day,
    )
    return AssessmentChatEvidencePreviewResponse(
        resident_id=resident.id,
        period_start=start_day,
        period_end=end_day,
        total_count=len(items),
        items=[
            {
                "source_ref": item["source_ref"],
                "summary": item["summary"],
                "reference_locator": item["reference_locator"],
            }
            for item in items
        ],
        external_transfer_allowed=False,
    )


@app.post(
    "/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
    response_model=NewAdmissionDraftResponse,
    status_code=201,
)
async def analyze_assessment_materials(
    resident_id: UUID,
    reason: Annotated[str, Form()],
    assessment_date: Annotated[str, Form()],
    document_kinds: Annotated[list[str], Form()],
    files: Annotated[list[UploadFile], File()],
    period_start: Annotated[str | None, Form()] = None,
    period_end: Annotated[str | None, Form()] = None,
    excluded_chat_source_refs: Annotated[list[str] | None, Form()] = None,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    if not resident.is_test_data:
        raise HTTPException(
            status_code=422,
            detail="현재 DEV에서는 코드화된 합성 어르신 자료만 분석할 수 있습니다.",
        )
    if reason not in {
        "new_admission",
        "periodic_reassessment",
        "state_change_reassessment",
        "staff_review",
    }:
        raise HTTPException(status_code=422, detail="작성 사유를 다시 선택해 주세요.")
    try:
        assessment_day = date.fromisoformat(assessment_date)
        start_day = date.fromisoformat(period_start) if period_start else None
        end_day = date.fromisoformat(period_end) if period_end else None
    except ValueError as error:
        raise HTTPException(status_code=422, detail="날짜 형식을 확인해 주세요.") from error
    if reason != "new_admission" and (start_day is None or end_day is None):
        raise HTTPException(
            status_code=422,
            detail="재평가는 이전 작성일 이후의 확인 기간을 입력해 주세요.",
        )
    if reason == "new_admission":
        if start_day is None:
            registered_day = resident.created_at.date()
            start_day = min(registered_day, assessment_day)
        if end_day is None:
            end_day = assessment_day
        period_start = start_day.isoformat()
        period_end = end_day.isoformat()
    if start_day and end_day and start_day > end_day:
        raise HTTPException(status_code=422, detail="확인 기간의 시작일이 종료일보다 늦습니다.")
    case_ref = f"assessment-{assessment_day:%Y%m%d}-{uuid4().hex[:12]}"
    parsed_materials, stored_contents = await _parse_assessment_material_uploads(
        resident=resident,
        reason=reason,
        document_kinds=document_kinds,
        files=files,
        source_prefix="material",
    )
    declared_target_codes = extract_declared_target_codes(parsed_materials)
    if declared_target_codes and declared_target_codes != (resident.display_name,):
        raise HTTPException(
            status_code=409,
            detail=(
                "선택한 어르신과 제출 자료에 표시된 대상 코드가 다릅니다. "
                "어르신과 파일을 다시 확인해 주세요."
            ),
        )

    linked_evidence: list[dict[str, str]] = []
    if start_day and end_day:
        linked_evidence = collect_resident_assessment_chat_evidence(
            db,
            organization_id=processor.organization_id,
            resident_id=resident.id,
            start_day=start_day,
            end_day=end_day,
            excluded_source_refs=set(excluded_chat_source_refs or []),
        )

    bundle = build_assessment_draft_bundle(
        parsed_materials,
        case_ref=case_ref,
        reason=reason,
        assessment_date=assessment_date,
        period_start=period_start,
        period_end=period_end,
        linked_evidence=linked_evidence,
    )
    ledger_entries = build_assessment_evidence_entries(parsed_materials, bundle)
    material_directory = protected_material_directory(settings.upload_dir, case_ref)
    material_directory.mkdir(parents=True, exist_ok=False)
    saved_paths: list[Path] = []
    try:
        for index, (content, extension) in enumerate(stored_contents, start=1):
            target = material_directory / f"material-{index:02d}{extension}"
            target.write_bytes(content)
            saved_paths.append(target)
        draft = _add_new_admission_draft(
            db,
            resident=resident,
            processor=processor,
            payload=NewAdmissionDraftRevisionCreate(bundle=bundle),
        )
        for entry in ledger_entries:
            db.add(
                AssessmentEvidenceLedgerEntry(
                    organization_id=processor.organization_id,
                    resident_id=resident.id,
                    draft_id=draft.id,
                    created_by_id=processor.id,
                    is_test_data=resident.is_test_data,
                    **entry,
                )
            )
        db.commit()
        draft = db.scalar(
            select(NewAdmissionDraftRecord)
            .options(selectinload(NewAdmissionDraftRecord.created_by))
            .where(NewAdmissionDraftRecord.id == draft.id)
        )
        assert draft is not None
        return _new_admission_draft_response(db, draft=draft, resident=resident)
    except IntegrityError as error:
        db.rollback()
        for target in saved_paths:
            target.unlink(missing_ok=True)
        material_directory.rmdir()
        raise HTTPException(
            status_code=409,
            detail="같은 신규입소 사례 초안이 이미 있습니다.",
        ) from error
    except Exception:
        db.rollback()
        for target in saved_paths:
            target.unlink(missing_ok=True)
        material_directory.rmdir()
        raise


@app.get(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}",
    response_model=NewAdmissionDraftResponse,
)
def get_new_admission_draft(
    resident_id: UUID,
    draft_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, draft = _new_admission_draft_record(
        db,
        processor=processor,
        resident_id=resident_id,
        draft_id=draft_id,
    )
    return _new_admission_draft_response(db, draft=draft, resident=resident)


@app.post(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}/materials",
    response_model=NewAdmissionDraftResponse,
    status_code=201,
)
async def add_new_admission_draft_materials(
    resident_id: UUID,
    draft_id: UUID,
    document_kinds: Annotated[list[str], Form()],
    files: Annotated[list[UploadFile], File()],
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, snapshot_draft = _new_admission_draft_record(
        db,
        processor=processor,
        resident_id=resident_id,
        draft_id=draft_id,
    )
    if not resident.is_test_data:
        raise HTTPException(
            status_code=422,
            detail="현재 DEV에서는 코드화된 합성 어르신 자료만 분석할 수 있습니다.",
        )
    snapshot_revision = snapshot_draft.current_revision
    snapshot_row = db.scalar(
        select(NewAdmissionDraftRevision).where(
            NewAdmissionDraftRevision.draft_id == snapshot_draft.id,
            NewAdmissionDraftRevision.revision == snapshot_revision,
        )
    )
    if snapshot_row is None:
        raise HTTPException(status_code=409, detail="현재 수정본을 다시 불러와 주세요.")
    current_bundle = NewAdmissionDraftBundle.model_validate(
        snapshot_row.bundle_payload
    )
    next_revision = snapshot_revision + 1
    parsed_materials, stored_contents = await _parse_assessment_material_uploads(
        resident=resident,
        reason=current_bundle.reason,
        document_kinds=document_kinds,
        files=files,
        source_prefix=f"material-r{next_revision:03d}",
    )
    declared_target_codes = extract_declared_target_codes(parsed_materials)
    if declared_target_codes and declared_target_codes != (resident.display_name,):
        raise HTTPException(
            status_code=409,
            detail=(
                "선택한 어르신과 제출 자료에 표시된 대상 코드가 다릅니다. "
                "어르신과 파일을 다시 확인해 주세요."
            ),
        )

    draft = db.scalar(
        select(NewAdmissionDraftRecord)
        .where(
            NewAdmissionDraftRecord.id == draft_id,
            NewAdmissionDraftRecord.organization_id == processor.organization_id,
            NewAdmissionDraftRecord.resident_id == resident.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="기초사정 초안을 찾을 수 없습니다.")
    if draft.current_revision != snapshot_revision:
        raise HTTPException(
            status_code=409,
            detail="다른 수정본이 먼저 저장되었습니다. 최신 초안을 다시 열어 주세요.",
        )
    previous = db.scalar(
        select(NewAdmissionDraftRevision)
        .where(
            NewAdmissionDraftRevision.draft_id == draft.id,
            NewAdmissionDraftRevision.revision == draft.current_revision,
        )
        .with_for_update()
    )
    if previous is None:
        raise HTTPException(status_code=409, detail="현재 수정본을 다시 불러와 주세요.")
    previous_bundle = NewAdmissionDraftBundle.model_validate(previous.bundle_payload)
    try:
        merged_bundle = merge_assessment_materials_into_bundle(
            previous_bundle,
            parsed_materials,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if (
        merged_bundle.revision != next_revision
        or merged_bundle.supersedes_revision != snapshot_revision
    ):
        raise HTTPException(
            status_code=409,
            detail="추가자료 수정본 번호가 맞지 않아 최신 초안을 다시 불러와야 합니다.",
        )

    bundle_payload = merged_bundle.model_dump(mode="json")
    confirmations = _new_admission_confirmations(
        bundle_payload=bundle_payload,
        confirmed_field_keys=[],
        processor=processor,
        previous_payload=previous.bundle_payload,
        previous_confirmations=previous.field_confirmations,
    )
    revision_status = _new_admission_revision_status(confirmations)
    ledger_entries = build_assessment_evidence_entries(
        parsed_materials,
        merged_bundle,
    )
    material_directory = protected_material_directory(
        settings.upload_dir,
        draft.case_ref,
    )
    directory_already_existed = material_directory.exists()
    material_directory.mkdir(parents=True, exist_ok=True)
    saved_paths: list[Path] = []
    try:
        for index, (content, extension) in enumerate(stored_contents, start=1):
            target = material_directory / (
                f"material-r{next_revision:03d}-{index:02d}{extension}"
            )
            try:
                with target.open("xb") as output:
                    output.write(content)
            except FileExistsError as error:
                raise HTTPException(
                    status_code=409,
                    detail="같은 추가자료 수정본 파일이 이미 있어 최신 초안을 다시 열어야 합니다.",
                ) from error
            saved_paths.append(target)

        db.add(
            NewAdmissionDraftRevision(
                draft_id=draft.id,
                revision=merged_bundle.revision,
                supersedes_revision=merged_bundle.supersedes_revision,
                status=revision_status,
                bundle_payload=bundle_payload,
                field_evidence_refs={
                    field["field_key"]: list(field.get("evidence_refs", []))
                    for field in bundle_payload["fields"]
                },
                field_changes=_new_admission_field_changes(
                    previous.bundle_payload,
                    bundle_payload,
                ),
                field_confirmations=confirmations,
                created_by_id=processor.id,
            )
        )
        for entry in ledger_entries:
            db.add(
                AssessmentEvidenceLedgerEntry(
                    organization_id=processor.organization_id,
                    resident_id=resident.id,
                    draft_id=draft.id,
                    created_by_id=processor.id,
                    is_test_data=resident.is_test_data,
                    **entry,
                )
            )
        draft.current_revision = merged_bundle.revision
        draft.status = revision_status
        draft.updated_at = utcnow()
        db.commit()
    except HTTPException:
        db.rollback()
        for target in saved_paths:
            target.unlink(missing_ok=True)
        if not directory_already_existed:
            material_directory.rmdir()
        raise
    except IntegrityError as error:
        db.rollback()
        for target in saved_paths:
            target.unlink(missing_ok=True)
        if not directory_already_existed:
            material_directory.rmdir()
        raise HTTPException(
            status_code=409,
            detail="같은 추가자료 수정본이 이미 저장되어 최신 초안을 다시 열어야 합니다.",
        ) from error
    except Exception:
        db.rollback()
        for target in saved_paths:
            target.unlink(missing_ok=True)
        if not directory_already_existed:
            material_directory.rmdir()
        raise

    refreshed = db.scalar(
        select(NewAdmissionDraftRecord)
        .options(selectinload(NewAdmissionDraftRecord.created_by))
        .where(NewAdmissionDraftRecord.id == draft.id)
    )
    assert refreshed is not None
    return _new_admission_draft_response(
        db,
        draft=refreshed,
        resident=resident,
    )


@app.get(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}/evidence-ledger",
    response_model=AssessmentEvidenceLedgerResponse,
)
def get_assessment_evidence_ledger(
    resident_id: UUID,
    draft_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, draft = _new_admission_draft_record(
        db,
        processor=processor,
        resident_id=resident_id,
        draft_id=draft_id,
    )
    entries = db.scalars(
        select(AssessmentEvidenceLedgerEntry)
        .where(
            AssessmentEvidenceLedgerEntry.organization_id
            == processor.organization_id,
            AssessmentEvidenceLedgerEntry.resident_id == resident.id,
            AssessmentEvidenceLedgerEntry.draft_id == draft.id,
        )
        .order_by(
            AssessmentEvidenceLedgerEntry.document_date,
            AssessmentEvidenceLedgerEntry.created_at,
            AssessmentEvidenceLedgerEntry.id,
        )
    ).all()
    return AssessmentEvidenceLedgerResponse(
        draft_id=draft.id,
        resident_id=resident.id,
        case_ref=draft.case_ref,
        file_intake_complete=bool(entries),
        items=[
            AssessmentEvidenceLedgerItemResponse.model_validate(entry)
            for entry in entries
        ],
    )


@app.get(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}/evidence-summary",
    response_model=AssessmentEvidenceSummaryResponse,
)
def get_assessment_evidence_summary(
    resident_id: UUID,
    draft_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, draft = _new_admission_draft_record(
        db,
        processor=processor,
        resident_id=resident_id,
        draft_id=draft_id,
    )
    revision = db.scalar(
        select(NewAdmissionDraftRevision).where(
            NewAdmissionDraftRevision.draft_id == draft.id,
            NewAdmissionDraftRevision.revision == draft.current_revision,
        )
    )
    if revision is None:
        raise HTTPException(status_code=404, detail="저장된 수정본을 찾을 수 없습니다.")
    entries = db.scalars(
        select(AssessmentEvidenceLedgerEntry).where(
            AssessmentEvidenceLedgerEntry.organization_id
            == processor.organization_id,
            AssessmentEvidenceLedgerEntry.resident_id == resident.id,
            AssessmentEvidenceLedgerEntry.draft_id == draft.id,
        )
    ).all()
    bundle = NewAdmissionDraftBundle.model_validate(revision.bundle_payload)
    available_source_refs = {
        source.source_ref for source in bundle.evidence_sources
    } | {entry.source_ref for entry in entries}
    summary = build_assessment_evidence_summary(
        bundle,
        available_source_refs=available_source_refs,
    )
    return AssessmentEvidenceSummaryResponse(
        draft_id=draft.id,
        resident_id=resident.id,
        case_ref=draft.case_ref,
        file_intake_complete=bool(entries),
        **summary,
    )


@app.get(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}/questions",
    response_model=NewAdmissionQuestionFlowResponse,
)
def get_new_admission_draft_questions(
    resident_id: UUID,
    draft_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident, draft = _new_admission_draft_record(
        db,
        processor=processor,
        resident_id=resident_id,
        draft_id=draft_id,
    )
    revision = db.scalar(
        select(NewAdmissionDraftRevision).where(
            NewAdmissionDraftRevision.draft_id == draft.id,
            NewAdmissionDraftRevision.revision == draft.current_revision,
        )
    )
    if revision is None:
        raise HTTPException(status_code=409, detail="현재 개정 이력을 찾을 수 없습니다.")
    revision_rows = db.scalars(
        select(NewAdmissionDraftRevision)
        .where(NewAdmissionDraftRevision.draft_id == draft.id)
        .order_by(NewAdmissionDraftRevision.revision)
    ).all()
    flow = build_new_admission_question_flow(
        revision.bundle_payload,
        revision.field_confirmations,
        revision_history=[
            {
                "revision": item.revision,
                "field_changes": item.field_changes,
            }
            for item in revision_rows
        ],
    )
    flow["output_status"] = revision.status
    return NewAdmissionQuestionFlowResponse(
        draft_id=draft.id,
        resident_id=resident.id,
        **flow,
    )


@app.post(
    "/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}/revisions",
    response_model=NewAdmissionDraftResponse,
    status_code=201,
)
def create_new_admission_draft_revision(
    resident_id: UUID,
    draft_id: UUID,
    payload: NewAdmissionDraftRevisionCreate,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resident = _care_planning_resident(
        db, processor=processor, resident_id=resident_id
    )
    draft = db.scalar(
        select(NewAdmissionDraftRecord)
        .where(
            NewAdmissionDraftRecord.id == draft_id,
            NewAdmissionDraftRecord.organization_id == processor.organization_id,
            NewAdmissionDraftRecord.resident_id == resident.id,
        )
        .with_for_update()
    )
    if draft is None:
        raise HTTPException(status_code=404, detail="신규입소자 초안을 찾을 수 없습니다.")
    bundle = payload.bundle
    if bundle.case_ref != draft.case_ref:
        raise HTTPException(status_code=422, detail="초안의 사례 참조값은 변경할 수 없습니다.")
    if (
        bundle.processing_scope != draft.processing_scope
        or bundle.external_transfer_allowed
    ):
        raise HTTPException(
            status_code=422,
            detail="처리 범위와 외부전송 차단 계약은 개정에서 변경할 수 없습니다.",
        )
    expected_revision = draft.current_revision + 1
    if (
        bundle.revision != expected_revision
        or bundle.supersedes_revision != draft.current_revision
    ):
        raise HTTPException(
            status_code=409,
            detail=(
                f"현재 revision {draft.current_revision}을 기준으로 "
                f"revision {expected_revision}을 새로 저장해 주세요."
            ),
        )
    previous = db.scalar(
        select(NewAdmissionDraftRevision)
        .where(
            NewAdmissionDraftRevision.draft_id == draft.id,
            NewAdmissionDraftRevision.revision == draft.current_revision,
        )
        .with_for_update()
    )
    if previous is None:
        raise HTTPException(status_code=409, detail="현재 개정 이력을 찾을 수 없습니다.")
    bundle_payload = bundle.model_dump(mode="json")
    bundle_payload["document_reviews"] = _new_admission_document_reviews(
        bundle_payload=bundle_payload,
        reviewed_document_types=payload.reviewed_document_types,
        processor=processor,
        previous_payload=previous.bundle_payload,
    )
    bundle_payload = NewAdmissionDraftBundle.model_validate(
        bundle_payload
    ).model_dump(mode="json")
    confirmations = _new_admission_confirmations(
        bundle_payload=bundle_payload,
        confirmed_field_keys=payload.confirmed_field_keys,
        processor=processor,
        previous_payload=previous.bundle_payload,
        previous_confirmations=previous.field_confirmations,
    )
    revision_status = _new_admission_revision_status(confirmations)
    revision = NewAdmissionDraftRevision(
        draft_id=draft.id,
        revision=bundle.revision,
        supersedes_revision=bundle.supersedes_revision,
        status=revision_status,
        bundle_payload=bundle_payload,
        field_evidence_refs={
            field["field_key"]: list(field.get("evidence_refs", []))
            for field in bundle_payload["fields"]
        },
        field_changes=_new_admission_field_changes(
            previous.bundle_payload, bundle_payload
        ),
        field_confirmations=confirmations,
        created_by_id=processor.id,
    )
    db.add(revision)
    draft.current_revision = bundle.revision
    draft.status = revision_status
    draft.updated_at = utcnow()
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="같은 revision이 이미 저장되어 최신 초안을 다시 불러와야 합니다.",
        ) from error
    draft = db.scalar(
        select(NewAdmissionDraftRecord)
        .options(selectinload(NewAdmissionDraftRecord.created_by))
        .where(NewAdmissionDraftRecord.id == draft.id)
    )
    assert draft is not None
    return _new_admission_draft_response(db, draft=draft, resident=resident)


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
            raise HTTPException(
                status_code=422,
                detail="선택한 생활실·방·구역을 찾을 수 없습니다.",
            )
    if service_type == "facility" and floor_unit is None:
        raise HTTPException(
            status_code=422,
            detail="시설 어르신은 생활실·방·구역을 선택해 주세요.",
        )
    floor_name = floor_unit.name if floor_unit else None
    room_name = (
        floor_name
        if floor_name
        else "주간보호 미지정"
        if service_type == "daycare"
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
    if (
        settings.environment == "development"
        and settings.database_schema == "smcodi_finalist"
        and not RESIDENT_CODE_RE.fullmatch(payload.display_name.strip())
    ):
        raise HTTPException(
            status_code=422,
            detail="본선 DEV에서는 어르신 코드 네 자리 형식으로 입력해 주세요.",
        )
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
        internal_code=(
            f"DEV-FINALIST-RESIDENT-{payload.display_name.removeprefix('어르')}"
            if settings.environment == "development"
            and settings.database_schema == "smcodi_finalist"
            else f"MANUAL:{uuid4().hex}"
        ),
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


@app.patch(
    "/api/admin/residents/{resident_id}",
    response_model=ResidentResponse,
)
def update_resident_for_admin(
    resident_id: UUID,
    payload: ResidentAdminUpdate,
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
        raise HTTPException(
            status_code=404, detail="이용 중인 어르신을 찾을 수 없습니다."
        )
    if resident.service_type == "homecare" and payload.floor_id is not None:
        raise HTTPException(
            status_code=422,
            detail="방문요양 어르신에게는 생활실·방·구역을 지정하지 않습니다.",
        )

    previous_floor_id = resident.floor_id
    if payload.floor_id is None:
        resident.room_id = None
    else:
        room = _room_for_manual_resident(
            db,
            organization_id=admin.organization_id,
            service_type=resident.service_type,
            floor_id=payload.floor_id,
        )
        resident.room_id = room.id
    record_audit(
        db,
        actor_id=admin.id,
        action="recipients.living_space_updated",
        target_type="recipient",
        target_id=resident.id,
        details={
            "had_previous_space": previous_floor_id is not None,
            "has_current_space": payload.floor_id is not None,
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
        raise HTTPException(
            status_code=404, detail="이용 중인 어르신을 찾을 수 없습니다."
        )
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
        raise HTTPException(
            status_code=422, detail="어르신 순서에 중복된 항목이 있습니다."
        )
    residents = db.scalars(
        select(Resident).where(
            Resident.id.in_(set(resident_ids)),
            Resident.organization_id == admin.organization_id,
            Resident.is_active.is_(True),
        )
    ).all()
    if {resident.id for resident in residents} != set(resident_ids):
        raise HTTPException(
            status_code=422, detail="순서를 변경할 수 없는 어르신이 포함되어 있습니다."
        )
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
    return [resident_response(by_id[resident_id]) for resident_id in resident_ids]


def _parse_local_timestamp(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _carefor_staff_sync_source() -> CareforStaffSyncSourceResponse:
    path = Path(settings.carefor_staff_roster_path)
    original_name = path.name[:180] or "carefor_staff.local.json"
    if not path.is_file():
        return CareforStaffSyncSourceResponse(
            status="missing",
            original_name=original_name,
            message="개발본에 케어포 직원 명단이 아직 준비되지 않았습니다.",
        )
    try:
        content = path.read_bytes()
        if not content:
            raise StaffSyncError("직원 명단 파일이 비어 있습니다.")
        if len(content) > MAX_RESIDENT_SYNC_FILE_BYTES:
            raise StaffSyncError("직원 명단 파일은 2MB 이하여야 합니다.")
        generated_at, rows = parse_staff_roster_file(content)
    except (OSError, StaffSyncError) as exc:
        return CareforStaffSyncSourceResponse(
            status="invalid",
            original_name=original_name,
            message=str(exc),
        )
    service_counts = {"facility": 0, "daycare": 0, "homecare": 0}
    unique_names: set[str] = set()
    for row in rows:
        display_name = (
            str(
                row.get("display_name")
                or row.get("staff_name")
                or row.get("employee_name")
                or row.get("name")
                or ""
            )
            .replace(" ", "")
            .strip()
        )
        if display_name:
            unique_names.add(display_name)
        raw = str(row.get("service_type", "")).strip().lower()
        service_type = {
            "facility_care": "facility",
            "시설": "facility",
            "요양원": "facility",
            "day_care": "daycare",
            "주간": "daycare",
            "주간보호": "daycare",
            "home_care": "homecare",
            "방문": "homecare",
            "방문요양": "homecare",
        }.get(raw, raw)
        if service_type in service_counts:
            service_counts[service_type] += 1
    return CareforStaffSyncSourceResponse(
        status="ready",
        original_name=original_name,
        generated_at=generated_at,
        row_count=len(rows),
        unique_name_count=len(unique_names),
        service_counts=service_counts,
        message="읽기 전용 직원 명단을 안전하게 미리볼 수 있습니다.",
    )


def _staff_sync_batch_response(
    batch: StaffSyncBatch,
    *,
    include_items: bool = True,
) -> StaffSyncBatchResponse:
    items = batch.items if include_items else []
    return StaffSyncBatchResponse(
        id=batch.id,
        source=batch.source,
        original_name=batch.original_name,
        file_sha256=batch.file_sha256,
        source_generated_at=(
            _as_utc(batch.source_generated_at) if batch.source_generated_at else None
        ),
        status="preview",
        summary=batch.summary,
        created_by_name=batch.created_by.full_name,
        created_at=_as_utc(batch.created_at),
        updated_at=_as_utc(batch.updated_at),
        items=[
            StaffSyncItemResponse(
                id=item.id,
                source_key=item.source_key,
                service_type=item.service_type,
                external_id=item.external_id,
                change_type=item.change_type,
                status=item.status,
                current_staff_id=item.current_staff_id,
                incoming_payload=item.incoming_payload,
                current_snapshot=item.current_snapshot,
                conflict_reason=item.conflict_reason,
            )
            for item in items
        ],
    )


def _staff_sync_review_draft_response(
    draft: StaffSyncReviewDraft,
) -> StaffSyncReviewDraftResponse:
    payload = draft.decision_payload or {}
    return StaffSyncReviewDraftResponse(
        id=draft.id,
        batch_id=draft.batch_id,
        candidate_key=draft.candidate_key,
        identity_decision=payload.get("identity_decision", "pending"),
        person_groups=payload.get("person_groups") or [],
        account_assignments=payload.get("account_assignments") or [],
        note=str(payload.get("note") or ""),
        completion_status=draft.completion_status,
        completion_issues=draft.completion_issues or [],
        revision=draft.revision,
        updated_by_name=draft.updated_by.full_name,
        created_at=_as_utc(draft.created_at),
        updated_at=_as_utc(draft.updated_at),
    )


def _staff_assignment_preview_for_batch(
    db: Session,
    batch: StaffSyncBatch,
    *,
    include_review_drafts: bool = True,
) -> dict[str, Any]:
    entries = [
        {
            "source_key": item.source_key,
            "service_type": item.service_type,
            "external_id": item.external_id,
            "change_type": item.change_type,
            "status": item.status,
            "current_staff_id": item.current_staff_id,
            "incoming_payload": item.incoming_payload,
            "conflict_reason": item.conflict_reason,
        }
        for item in batch.items
    ]
    preview = build_staff_assignment_preview(
        db,
        batch.organization_id,
        entries,
    )
    drafts_by_candidate = (
        {
            draft.candidate_key: draft
            for draft in db.scalars(
                select(StaffSyncReviewDraft).where(
                    StaffSyncReviewDraft.batch_id == batch.id,
                    StaffSyncReviewDraft.organization_id == batch.organization_id,
                )
            ).all()
        }
        if include_review_drafts
        else {}
    )
    for candidate in preview["candidates"]:
        draft = drafts_by_candidate.get(candidate["candidate_key"])
        candidate["review_draft"] = (
            _staff_sync_review_draft_response(draft) if draft is not None else None
        )
    review_required = [
        candidate
        for candidate in preview["candidates"]
        if candidate["review_status"] in {"identity_review", "assignment_review"}
    ]
    review_drafts = [
        drafts_by_candidate[candidate["candidate_key"]]
        for candidate in review_required
        if candidate["candidate_key"] in drafts_by_candidate
    ]
    preview["summary"].update(
        {
            "review_required_total": len(review_required),
            "decision_saved": len(review_drafts),
            "decision_complete": sum(
                draft.completion_status == "complete" for draft in review_drafts
            ),
            "decision_draft": sum(
                draft.completion_status == "draft" for draft in review_drafts
            ),
        }
    )
    return preview


@app.get(
    "/api/admin/carefor-staff-sync/status",
    response_model=CareforStaffSyncSourceResponse,
)
def carefor_staff_sync_status(admin: User = Depends(require_admin)):
    del admin
    return _carefor_staff_sync_source()


@app.post(
    "/api/admin/carefor-staff-sync/preview",
    response_model=StaffSyncBatchResponse,
    status_code=201,
)
def preview_carefor_staff_sync(
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    path = Path(settings.carefor_staff_roster_path)
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise HTTPException(
            status_code=422,
            detail="개발본에 케어포 직원 명단이 아직 준비되지 않았습니다.",
        ) from exc
    if not content:
        raise HTTPException(
            status_code=422, detail="빈 직원 명단은 미리볼 수 없습니다."
        )
    if len(content) > MAX_RESIDENT_SYNC_FILE_BYTES:
        raise HTTPException(
            status_code=413, detail="직원 명단 파일은 2MB 이하여야 합니다."
        )
    try:
        source_generated_at, rows = parse_staff_roster_file(content)
        entries, summary = build_staff_preview_entries(
            db,
            admin.organization_id,
            rows,
        )
    except StaffSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    batch = StaffSyncBatch(
        organization_id=admin.organization_id,
        source="carefor_read_only_capture",
        original_name=path.name[:180] or "carefor_staff.local.json",
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
            StaffSyncItem(
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
        action="staff.carefor_sync_preview_created",
        target_type="staff_sync_batch",
        target_id=batch.id,
        details={
            "source": batch.source,
            "original_name": batch.original_name,
            "file_sha256": batch.file_sha256,
            "summary": summary,
            "safety": "preview_only_missing_rows_not_retired",
        },
    )
    db.commit()
    db.refresh(batch)
    return _staff_sync_batch_response(batch)


@app.get(
    "/api/admin/carefor-staff-sync/batches",
    response_model=list[StaffSyncBatchResponse],
)
def list_carefor_staff_sync_batches(
    limit: int = 10,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    safe_limit = max(1, min(limit, 30))
    batches = db.scalars(
        select(StaffSyncBatch)
        .where(StaffSyncBatch.organization_id == admin.organization_id)
        .order_by(StaffSyncBatch.created_at.desc())
        .limit(safe_limit)
    ).all()
    return [_staff_sync_batch_response(batch, include_items=False) for batch in batches]


@app.get(
    "/api/admin/carefor-staff-sync/batches/{batch_id}",
    response_model=StaffSyncBatchResponse,
)
def get_carefor_staff_sync_batch(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == batch_id,
            StaffSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail="직원 동기화 미리보기를 찾을 수 없습니다."
        )
    return _staff_sync_batch_response(batch)


@app.get(
    "/api/admin/carefor-staff-sync/batches/{batch_id}/assignment-preview",
    response_model=StaffAssignmentPreviewResponse,
)
def get_carefor_staff_assignment_preview(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == batch_id,
            StaffSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail="직원 동기화 미리보기를 찾을 수 없습니다."
        )
    preview = _staff_assignment_preview_for_batch(db, batch)
    return StaffAssignmentPreviewResponse(
        batch_id=batch.id,
        source_generated_at=(
            _as_utc(batch.source_generated_at) if batch.source_generated_at else None
        ),
        summary=preview["summary"],
        candidates=preview["candidates"],
    )


@app.get(
    "/api/admin/carefor-staff-sync/batches/{batch_id}/application-plan",
    response_model=StaffApplicationPlanResponse,
)
def get_carefor_staff_application_plan(
    batch_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == batch_id,
            StaffSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail="직원 동기화 미리보기를 찾을 수 없습니다."
        )

    plan = _staff_application_plan_for_batch(db, batch)
    return StaffApplicationPlanResponse(**plan)


def _staff_application_plan_for_batch(
    db: Session,
    batch: StaffSyncBatch,
) -> dict[str, Any]:
    preview = _staff_assignment_preview_for_batch(db, batch)
    entries = [
        {
            "source_key": item.source_key,
            "service_type": item.service_type,
            "external_id": item.external_id,
            "change_type": item.change_type,
            "status": item.status,
            "current_staff_id": item.current_staff_id,
            "incoming_payload": item.incoming_payload,
            "current_snapshot": item.current_snapshot,
            "conflict_reason": item.conflict_reason,
        }
        for item in batch.items
    ]
    plan = build_staff_application_plan(
        db,
        batch.organization_id,
        batch_id=batch.id,
        file_sha256=batch.file_sha256,
        batch_status=batch.status,
        entries=entries,
        candidates=preview["candidates"],
    )
    return plan


def _staff_application_run_for_admin(
    db: Session,
    admin: User,
    run_id: UUID,
) -> StaffApplicationRun:
    run = db.scalar(
        select(StaffApplicationRun).where(
            StaffApplicationRun.id == run_id,
            StaffApplicationRun.organization_id == admin.organization_id,
        )
    )
    if run is None:
        raise HTTPException(
            status_code=404, detail="직원 반영 실행기록을 찾을 수 없습니다."
        )
    return run


def _revalidate_staff_application_run(
    db: Session,
    run: StaffApplicationRun,
) -> dict[str, Any]:
    if run.status == "stale":
        raise HTTPException(
            status_code=409, detail="현재 실행기록은 자료 변경으로 만료되었습니다."
        )
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == run.batch_id,
            StaffSyncBatch.organization_id == run.organization_id,
        )
    )
    if batch is None:
        run.status = "stale"
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="원본 직원 배치를 찾을 수 없어 실행기록이 만료됐습니다.",
        )
    plan = _staff_application_plan_for_batch(db, batch)
    matches = (
        plan["plan_fingerprint"] == run.plan_fingerprint
        and plan["change_fingerprint"] == run.change_fingerprint
        and plan["organization_catalog_fingerprint"]
        == run.organization_catalog_fingerprint
        and plan["leave_access_policy_fingerprint"]
        == run.leave_access_policy_fingerprint
    )
    if not matches:
        run.status = "stale"
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="원본·결정안·조직표 또는 현재 직원 상태가 달라져 실행기록이 만료됐습니다.",
        )
    return plan


@app.post(
    "/api/admin/carefor-staff-sync/batches/{batch_id}/application-runs",
    response_model=StaffApplicationRunResponse,
    status_code=201,
)
def prepare_carefor_staff_application_run(
    batch_id: UUID,
    payload: StaffApplicationRunPrepareRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == batch_id,
            StaffSyncBatch.organization_id == admin.organization_id,
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail="직원 동기화 미리보기를 찾을 수 없습니다."
        )
    plan = _staff_application_plan_for_batch(db, batch)
    if (
        plan["plan_fingerprint"] != payload.expected_plan_fingerprint
        or plan["change_fingerprint"] != payload.expected_change_fingerprint
    ):
        raise HTTPException(
            status_code=409,
            detail="화면에서 확인한 계획이 달라졌습니다. 계획을 새로 조회해 주세요.",
        )
    try:
        run = prepare_application_run(
            db,
            organization_id=admin.organization_id,
            batch=batch,
            created_by_id=admin.id,
            plan=plan,
        )
    except StaffApplicationManifestError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(run)
    return StaffApplicationRunResponse(**application_run_response(run))


@app.get(
    "/api/admin/carefor-staff-sync/application-runs/{run_id}",
    response_model=StaffApplicationRunResponse,
)
def get_carefor_staff_application_run(
    run_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = _staff_application_run_for_admin(db, admin, run_id)
    return StaffApplicationRunResponse(**application_run_response(run))


@app.post(
    "/api/admin/carefor-staff-sync/application-runs/{run_id}/revalidate",
    response_model=StaffApplicationRunResponse,
)
def revalidate_carefor_staff_application_run(
    run_id: UUID,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = _staff_application_run_for_admin(db, admin, run_id)
    _revalidate_staff_application_run(db, run)
    return StaffApplicationRunResponse(**application_run_response(run))


@app.post(
    "/api/admin/carefor-staff-sync/application-runs/{run_id}/backup-proof",
    response_model=StaffApplicationRunResponse,
)
def verify_carefor_staff_application_backup(
    run_id: UUID,
    payload: StaffApplicationEvidenceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = _staff_application_run_for_admin(db, admin, run_id)
    _revalidate_staff_application_run(db, run)
    try:
        attach_backup_proof(
            db,
            run=run,
            evidence_filename=payload.evidence_filename,
            verified_by_id=admin.id,
        )
    except StaffApplicationManifestError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(run)
    return StaffApplicationRunResponse(**application_run_response(run))


@app.post(
    "/api/admin/carefor-staff-sync/application-runs/{run_id}/restore-receipt",
    response_model=StaffApplicationRunResponse,
)
def verify_carefor_staff_application_restore(
    run_id: UUID,
    payload: StaffApplicationEvidenceRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    run = _staff_application_run_for_admin(db, admin, run_id)
    _revalidate_staff_application_run(db, run)
    try:
        attach_restore_receipt(
            db,
            run=run,
            receipt_filename=payload.evidence_filename,
            verified_by_id=admin.id,
        )
    except StaffApplicationManifestError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    db.refresh(run)
    return StaffApplicationRunResponse(**application_run_response(run))


@app.put(
    "/api/admin/carefor-staff-sync/batches/{batch_id}/review-drafts/{candidate_key}",
    response_model=StaffSyncReviewDraftResponse,
)
def save_carefor_staff_review_draft(
    batch_id: UUID,
    candidate_key: str,
    payload: StaffSyncReviewDraftSaveRequest,
    admin: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    batch = db.scalar(
        select(StaffSyncBatch).where(
            StaffSyncBatch.id == batch_id,
            StaffSyncBatch.organization_id == admin.organization_id,
            StaffSyncBatch.status == "preview",
        )
    )
    if batch is None:
        raise HTTPException(
            status_code=404, detail="직원 동기화 미리보기를 찾을 수 없습니다."
        )

    preview = _staff_assignment_preview_for_batch(
        db,
        batch,
        include_review_drafts=False,
    )
    candidate = next(
        (
            item
            for item in preview["candidates"]
            if item["candidate_key"] == candidate_key
        ),
        None,
    )
    if candidate is None:
        raise HTTPException(
            status_code=404, detail="검토할 사람 후보를 찾을 수 없습니다."
        )
    if candidate["review_status"] == "blocked":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="자료 충돌을 먼저 해결해야 결정안을 저장할 수 있습니다.",
        )
    if candidate["review_status"] == "proposal_ready":
        raise HTTPException(
            status_code=422,
            detail="이 후보는 별도 관리자 결정이 필요한 대상이 아닙니다.",
        )

    try:
        normalized_payload, completion_issues = normalize_staff_review_draft(
            db,
            admin.organization_id,
            candidate,
            payload.model_dump(exclude={"expected_revision"}),
        )
    except StaffSyncError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    draft = db.scalar(
        select(StaffSyncReviewDraft)
        .where(
            StaffSyncReviewDraft.batch_id == batch.id,
            StaffSyncReviewDraft.candidate_key == candidate_key,
            StaffSyncReviewDraft.organization_id == admin.organization_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    try:
        if draft is None:
            if payload.expected_revision is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="다른 화면에서 결정안 상태가 바뀌었습니다. 새로고침 후 다시 저장해 주세요.",
                )
            draft = StaffSyncReviewDraft(
                organization_id=admin.organization_id,
                batch_id=batch.id,
                candidate_key=candidate_key,
                decision_payload=normalized_payload,
                completion_status="complete" if not completion_issues else "draft",
                completion_issues=completion_issues,
                revision=1,
                created_by_id=admin.id,
                updated_by_id=admin.id,
            )
            db.add(draft)
            db.flush()
        else:
            if payload.expected_revision != draft.revision:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="다른 화면에서 결정안이 수정되었습니다. 새로고침 후 다시 저장해 주세요.",
                )
            draft.decision_payload = normalized_payload
            draft.completion_status = "complete" if not completion_issues else "draft"
            draft.completion_issues = completion_issues
            draft.revision += 1
            draft.updated_by_id = admin.id
            draft.updated_at = utcnow()

        record_audit(
            db,
            actor_id=admin.id,
            action="staff.carefor_review_draft_saved",
            target_type="staff_sync_review_draft",
            target_id=draft.id,
            details={
                "batch_id": str(batch.id),
                "candidate_key": candidate_key,
                "revision": draft.revision,
                "identity_decision": normalized_payload["identity_decision"],
                "group_count": len(normalized_payload["person_groups"]),
                "completion_status": draft.completion_status,
                "safety": "review_draft_only_no_staff_mutation",
            },
        )
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="다른 화면에서 결정안이 먼저 저장되었습니다. 새로고침 후 다시 확인해 주세요.",
        ) from exc
    db.refresh(draft)
    return _staff_sync_review_draft_response(draft)


@app.get(
    "/api/admin/carefor-roster/status",
    response_model=CareforRosterStatusResponse,
)
def carefor_roster_status(
    admin: User = Depends(require_admin),
):
    del admin
    _require_carefor_roster_allowed()
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
        staff_payload.get("staff", []) if isinstance(staff_payload, dict) else []
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
            if service_type not in staff_by_service or "(가명)" not in display_name:
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
    _require_carefor_roster_allowed()
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
            managed_external_id_prefixes=(f"carefor:{service_type}:resident:",),
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
    query = select(ResidentSyncBatch).where(
        ResidentSyncBatch.organization_id == admin.organization_id
    )
    if _finalist_carefor_roster_blocked():
        query = query.where(
            ResidentSyncBatch.source != "carefor_read_only_capture"
        )
    batches = db.scalars(
        query.order_by(ResidentSyncBatch.created_at.desc()).limit(safe_limit)
    ).all()
    return [
        _resident_sync_batch_response(batch, include_items=False) for batch in batches
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
        raise HTTPException(
            status_code=404, detail="동기화 미리보기를 찾을 수 없습니다."
        )
    if (
        _finalist_carefor_roster_blocked()
        and batch.source == "carefor_read_only_capture"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=FINALIST_CAREFOR_ROSTER_BLOCK_DETAIL,
        )
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
        raise HTTPException(
            status_code=404, detail="동기화 미리보기를 찾을 수 없습니다."
        )
    if (
        _finalist_carefor_roster_blocked()
        and batch.source == "carefor_read_only_capture"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=FINALIST_CAREFOR_ROSTER_BLOCK_DETAIL,
        )
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
    if {user.id for user in users} != member_ids or any(
        user.employment_status != "active" or user.staff_id is None for user in users
    ):
        raise HTTPException(
            status_code=422, detail="참여자 중 존재하지 않거나 퇴사한 직원이 있습니다."
        )
    room = Room(
        organization_id=admin.organization_id,
        name=payload.name.strip(),
        kind="custom",
        owner_staff_id=(
            admin.staff_id
            if any(member.id == admin.id for member in users)
            else min(
                users, key=lambda member: (member.full_name, str(member.id))
            ).staff_id
        ),
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
    return [_managed_custom_room_response(db, room) for room in db.scalars(query).all()]


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
        if {user.id for user in users} != desired_user_ids or any(
            user.employment_status != "active" or user.staff_id is None
            for user in users
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

        if room.owner_staff_id not in desired_staff_ids:
            previous_owner_staff_id = room.owner_staff_id
            new_owner = min(
                users,
                key=lambda member: (member.full_name, str(member.id)),
            )
            room.owner_staff_id = new_owner.staff_id
            record_audit(
                db,
                actor_id=admin.id,
                action="room.staff_owner_transferred",
                target_type="room",
                target_id=room.id,
                details={
                    "previous_owner_staff_id": str(previous_owner_staff_id),
                    "owner_staff_id": str(room.owner_staff_id),
                    "source": "legacy_admin_update",
                },
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
    before_id: UUID | None = None,
    limit: int = 60,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방에 접근할 수 없습니다.")
    limit = min(max(limit, 1), 100)
    if after_id is not None and before_id is not None:
        raise HTTPException(
            status_code=422,
            detail="이전·이후 메시지 기준을 동시에 지정할 수 없습니다.",
        )
    query = (
        select(Message)
        .options(*_message_list_query_options())
        .where(Message.room_id == room_id)
    )
    if getattr(user, "_reviewer_experience", None) is not None:
        query = query.where(Message.is_test_data.is_(True))
    if after_id:
        cursor_message = db.get(Message, after_id)
        if cursor_message is None or cursor_message.room_id != room_id:
            raise HTTPException(
                status_code=422, detail="메시지 조회 기준이 올바르지 않습니다."
            )
        if (
            getattr(user, "_reviewer_experience", None) is not None
            and not cursor_message.is_test_data
        ):
            raise HTTPException(
                status_code=403, detail="이 메시지에 접근할 수 없습니다."
            )
        query = (
            query.where(
                or_(
                    Message.created_at > cursor_message.created_at,
                    and_(
                        Message.created_at == cursor_message.created_at,
                        Message.id > cursor_message.id,
                    ),
                )
            )
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
        )
        messages = db.scalars(query).all()
    elif before_id:
        cursor_message = db.get(Message, before_id)
        if cursor_message is None or cursor_message.room_id != room_id:
            raise HTTPException(
                status_code=422, detail="메시지 조회 기준이 올바르지 않습니다."
            )
        if (
            getattr(user, "_reviewer_experience", None) is not None
            and not cursor_message.is_test_data
        ):
            raise HTTPException(
                status_code=403, detail="이 메시지에 접근할 수 없습니다."
            )
        messages = list(
            reversed(
                db.scalars(
                    query.where(
                        or_(
                            Message.created_at < cursor_message.created_at,
                            and_(
                                Message.created_at == cursor_message.created_at,
                                Message.id < cursor_message.id,
                            ),
                        )
                    )
                    .order_by(Message.created_at.desc(), Message.id.desc())
                    .limit(limit)
                ).all()
            )
        )
    else:
        messages = list(
            reversed(
                db.scalars(
                    query.order_by(Message.created_at.desc(), Message.id.desc()).limit(
                        limit
                    )
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
            include_attachment_review_details=False,
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
        raise HTTPException(
            status_code=422, detail="검색 시작일이 종료일보다 늦습니다."
        )
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
    message_query = (
        select(Message)
        .options(*_message_list_query_options())
        .where(
            Message.room_id == room_id,
            Message.lifecycle_status == "active",
        )
    )
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
        message_query.order_by(Message.created_at.desc(), Message.id.desc()).limit(5001)
    ).all()
    truncated = len(messages) > 5000
    messages = messages[:5000]
    query_text = (q or "").strip().casefold()
    matched: list[Message] = []
    matches_by_message_id: dict[UUID, list[RoomMessageSearchMatchResponse]] = {}
    for message in messages:
        local_date = _as_utc(message.created_at).astimezone(kst).date()
        if date_from and local_date < date_from:
            continue
        if date_to and local_date > date_to:
            continue
        confirmed_resident_ids = {
            link.resident_id
            for link in message.resident_links
            if link.status == "confirmed" and resident_link_is_current(link, message)
        }
        if resident_id and (
            message.resident_id != resident_id
            and resident_id not in confirmed_resident_ids
        ):
            continue
        if message_type and message.message_type != message_type:
            continue
        action_item = message.action_item
        if action_status == "none" and action_item is not None:
            continue
        if (
            action_status
            and action_status != "none"
            and (action_item is None or action_item.status != action_status)
        ):
            continue
        if query_text:
            source_matches = _message_search_matches(
                message, query_text, include_comments=user.role == "admin"
            )
            if not source_matches:
                continue
            matches_by_message_id[message.id] = source_matches
        matched.append(message)

    result_limit = min(max(limit, 1), 200)
    result_messages = matched[:result_limit]
    read_counts, reply_user_counts = message_engagement_counts(
        db, [message.id for message in result_messages]
    )
    return RoomMessageSearchResponse(
        matched_count=len(matched),
        truncated=truncated,
        matches=[
            match
            for message in result_messages
            for match in matches_by_message_id.get(message.id, [])
        ],
        messages=[
            message_response(
                message,
                db=db,
                viewer_id=user.id,
                read_count=read_counts.get(message.id, 0),
                reply_user_count=reply_user_counts.get(message.id, 0),
                include_attachment_review_details=False,
            )
            for message in result_messages
        ],
    )


_SEARCH_SUMMARY_CACHE_TTL_SECONDS = 600
_SEARCH_SUMMARY_CACHE_MAX_ITEMS = 64
_search_summary_cache: dict[
    str,
    tuple[float, RoomSummaryResult, str | None],
] = {}
_adaptive_search_summary_cache: dict[
    str,
    tuple[float, RoomSearchSummaryResponse],
] = {}


def _adaptive_search_cache_get(key: str) -> RoomSearchSummaryResponse | None:
    cached = _adaptive_search_summary_cache.get(key)
    if cached is None:
        return None
    created_at, response = cached
    if monotonic() - created_at > _SEARCH_SUMMARY_CACHE_TTL_SECONDS:
        _adaptive_search_summary_cache.pop(key, None)
        return None
    return response


def _adaptive_search_cache_put(key: str, response: RoomSearchSummaryResponse) -> None:
    if len(_adaptive_search_summary_cache) >= _SEARCH_SUMMARY_CACHE_MAX_ITEMS:
        oldest = min(_adaptive_search_summary_cache, key=lambda item: _adaptive_search_summary_cache[item][0])
        _adaptive_search_summary_cache.pop(oldest, None)
    _adaptive_search_summary_cache[key] = (monotonic(), response)


def _search_entry_text(entry: dict[str, Any]) -> str:
    parts = [
        str(entry.get("body") or "").strip(),
        str(entry.get("attachment_text") or "").strip(),
    ]
    parts.extend(
        str(comment).strip()
        for comment in entry.get("comments") or []
        if str(comment).strip()
    )
    generic_bodies = {
        "파일을 첨부했습니다.",
        "사진을 첨부했습니다.",
        "음성을 첨부했습니다.",
    }
    return "\n".join(
        part
        for part in parts
        if part and (part not in generic_bodies or len(parts) == 1)
    ).strip()


def _search_summary_excerpt(value: str, limit: int = 180) -> str:
    normalized = re.sub(r"\s+", " ", value).strip()
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def _search_entry_category(entry: dict[str, Any], content: str) -> str:
    action_status = str(entry.get("action_status") or "")
    if action_status in {"assigned", "acknowledged", "in_progress"}:
        return "unresolved"
    unresolved_terms = (
        "확인 필요",
        "추후 확인",
        "재확인",
        "연락 필요",
        "처리 필요",
        "조치 필요",
        "요청함",
        "요청드립니다",
    )
    if any(term in content for term in unresolved_terms):
        return "unresolved"
    attention_terms = (
        "낙상",
        "통증",
        "상처",
        "발진",
        "설사",
        "구토",
        "출혈",
        "응급",
        "병원",
        "처방",
        "혈압",
        "체온",
        "복약",
        "기저귀",
        "거부",
        "변화",
        "주의",
    )
    return (
        "attention" if any(term in content for term in attention_terms) else "general"
    )


def _search_period_bounds(
    entries: list[dict[str, Any]],
    *,
    requested_from: date | None,
    requested_to: date | None,
) -> tuple[date, date]:
    parsed_dates: list[date] = []
    for entry in entries:
        try:
            parsed_dates.append(
                datetime.fromisoformat(str(entry.get("created_at") or ""))
                .astimezone(timezone(timedelta(hours=9)))
                .date()
            )
        except (ValueError, TypeError):
            continue
    today = datetime.now(timezone(timedelta(hours=9))).date()
    start = requested_from or (min(parsed_dates) if parsed_dates else today)
    end = requested_to or (max(parsed_dates) if parsed_dates else start)
    return (start, end) if start <= end else (end, start)


def _search_period_granularity(start: date, end: date) -> str:
    day_span = (end - start).days
    if day_span <= 14:
        return "day"
    if day_span <= 90:
        return "week"
    return "month"


def _search_period_label(value: datetime, granularity: str) -> tuple[str, date]:
    local_date = value.astimezone(timezone(timedelta(hours=9))).date()
    if granularity == "month":
        key = local_date.replace(day=1)
        return f"{key.year}년 {key.month}월", key
    if granularity == "week":
        key = local_date - timedelta(days=local_date.weekday())
        return f"{key.month}월 {key.day}일 주간", key
    return f"{local_date.month}월 {local_date.day}일", local_date


def _build_search_summary_structure(
    entries: list[dict[str, Any]],
    *,
    resident_id: UUID | None,
    requested_from: date | None,
    requested_to: date | None,
) -> dict[str, Any]:
    start, end = _search_period_bounds(
        entries,
        requested_from=requested_from,
        requested_to=requested_to,
    )
    granularity = _search_period_granularity(start, end)
    display_mode = (
        "direct" if resident_id is not None or (end - start).days <= 7 else "overview"
    )
    counts = {"total": len(entries), "general": 0, "attention": 0, "unresolved": 0}
    timeline_groups: dict[date, dict[str, Any]] = {}
    resident_groups: dict[str, dict[str, Any]] = {}
    general_evidence: list[dict[str, Any]] = []

    for position, entry in enumerate(entries, 1):
        content = _search_entry_text(entry)
        category = _search_entry_category(entry, content)
        counts[category] += 1
        try:
            created_at = datetime.fromisoformat(str(entry.get("created_at") or ""))
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
        except (ValueError, TypeError):
            created_at = datetime.now(timezone.utc)
        label, period_key = _search_period_label(created_at, granularity)
        timeline = timeline_groups.setdefault(
            period_key,
            {
                "label": label,
                "message_count": 0,
                "attention_count": 0,
                "unresolved_count": 0,
                "excerpts": [],
            },
        )
        timeline["message_count"] += 1
        if category == "attention":
            timeline["attention_count"] += 1
        elif category == "unresolved":
            timeline["unresolved_count"] += 1
        excerpt = _search_summary_excerpt(content) or "본문 없이 첨부만 등록된 대화"
        if excerpt not in timeline["excerpts"]:
            timeline["excerpts"].append(excerpt)

        evidence = {
            "number": int(entry.get("number") or position),
            "message_id": entry["message_id"],
            "created_at": created_at,
            "source_label": str(entry.get("source_label") or "대화"),
            "excerpt": excerpt,
        }
        resident_refs = [
            ref
            for ref in entry.get("resident_refs") or []
            if isinstance(ref, dict) and str(ref.get("name") or "").strip()
        ]
        if resident_id is not None:
            resident_refs = [
                ref
                for ref in resident_refs
                if str(ref.get("id") or "") == str(resident_id)
            ]
        if not resident_refs:
            general_evidence.append(evidence)
            continue
        linked_names = [str(ref["name"]).strip() for ref in resident_refs]
        for ref in resident_refs:
            name = str(ref["name"]).strip()
            resident_key = str(ref.get("id") or name)
            group = resident_groups.setdefault(
                resident_key,
                {
                    "resident_id": ref.get("id"),
                    "resident_name": name,
                    "message_count": 0,
                    "attention_count": 0,
                    "unresolved_count": 0,
                    "evidence": [],
                },
            )
            group["message_count"] += 1
            if category == "attention":
                group["attention_count"] += 1
            elif category == "unresolved":
                group["unresolved_count"] += 1
            resident_excerpt = _resident_specific_period_text(
                content,
                target_name=name,
                resident_names=linked_names,
            )
            resident_evidence = {
                **evidence,
                "excerpt": (
                    _search_summary_excerpt(resident_excerpt)
                    if resident_excerpt
                    else "여러 어르신이 함께 언급된 원문에서 확인"
                ),
            }
            group["evidence"].append(resident_evidence)

    timeline = []
    for _, group in sorted(timeline_groups.items()):
        summary_parts = [f"보고 {group['message_count']}건"]
        if group["attention_count"]:
            summary_parts.append(f"주의·변화 {group['attention_count']}건")
        if group["unresolved_count"]:
            summary_parts.append(f"미해결 {group['unresolved_count']}건")
        timeline.append(
            {
                "label": group["label"],
                "message_count": group["message_count"],
                "attention_count": group["attention_count"],
                "unresolved_count": group["unresolved_count"],
                "summary": " · ".join(summary_parts),
            }
        )

    resident_summaries = []
    for group in sorted(
        resident_groups.values(),
        key=lambda item: (
            -item["unresolved_count"],
            -item["attention_count"],
            item["resident_name"],
        ),
    ):
        summary_parts = [f"관련 보고 {group['message_count']}건"]
        if group["attention_count"]:
            summary_parts.append(f"주의·변화 {group['attention_count']}건")
        if group["unresolved_count"]:
            summary_parts.append(f"미해결 {group['unresolved_count']}건")
        resident_summaries.append(
            {
                **{key: value for key, value in group.items() if key != "evidence"},
                "summary": " · ".join(summary_parts),
                "evidence": group["evidence"],
            }
        )

    highlights = []
    if counts["unresolved"]:
        highlights.append(f"미해결 업무 {counts['unresolved']}건을 먼저 확인해 주세요.")
    if counts["attention"]:
        highlights.append(f"주의·변화 보고 {counts['attention']}건이 있습니다.")
    if resident_summaries:
        highlights.append(
            f"관련 어르신 {len(resident_summaries)}명의 기록이 포함됐습니다."
        )
    elif counts["total"]:
        highlights.append("특정 어르신과 연결되지 않은 일반 대화입니다.")

    return {
        "display_mode": display_mode,
        "period_granularity": granularity,
        "counts": counts,
        "highlights": highlights,
        "timeline": timeline,
        "resident_summaries": resident_summaries,
        "general_evidence": general_evidence,
    }


def _compact_search_overview(structure: dict[str, Any]) -> str:
    counts = structure["counts"]
    lines = [
        (
            f"검색 결과 {counts['total']}건 중 일반 보고 {counts['general']}건, "
            f"주의·변화 {counts['attention']}건, 미해결 {counts['unresolved']}건입니다."
        )
    ]
    lines.extend(f"- {item}" for item in structure["highlights"][:3])
    return "\n".join(lines)


def _search_summary_cache_key(
    entries: list[dict[str, Any]],
    *,
    preferred_provider: str,
) -> str:
    serialized = json.dumps(
        {"provider": preferred_provider, "entries": entries},
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return sha256(serialized.encode("utf-8")).hexdigest()


def _search_summary_cache_get(
    key: str,
) -> tuple[RoomSummaryResult, str | None] | None:
    cached = _search_summary_cache.get(key)
    if cached is None:
        return None
    created_at, result, fallback_reason = cached
    if monotonic() - created_at > _SEARCH_SUMMARY_CACHE_TTL_SECONDS:
        _search_summary_cache.pop(key, None)
        return None
    return result, fallback_reason


def _search_summary_cache_put(
    key: str,
    result: RoomSummaryResult,
    fallback_reason: str | None,
) -> None:
    if len(_search_summary_cache) >= _SEARCH_SUMMARY_CACHE_MAX_ITEMS:
        oldest_key = min(
            _search_summary_cache, key=lambda item: _search_summary_cache[item][0]
        )
        _search_summary_cache.pop(oldest_key, None)
    _search_summary_cache[key] = (monotonic(), result, fallback_reason)


def _codex_search_summary(
    entries: list[dict[str, Any]],
    *,
    resident_names: list[str],
) -> RoomSummaryResult:
    runtime = AiAssistRuntimeSettings.from_env()
    worker = CodexWorkerClient(
        base_url=runtime.worker_url,
        shared_token=runtime.worker_token,
        timeout_seconds=min(runtime.worker_timeout_seconds, 20),
    )
    health = worker.health()
    if not health.credential_ready:
        raise LocalAiError("Codex 인증이 준비되지 않았습니다.")
    safe_entries, resident_aliases = deidentify_external_room_entries(
        entries,
        resident_names=resident_names,
    )
    sources: list[AiAssistSourceSnapshot] = []
    for entry_index, entry in enumerate(safe_entries, 1):
        # Search entries include UUID metadata. Keep the internal structure intact,
        # but serialize identifiers as strings when building the external AI context.
        serialized = json.dumps(entry, ensure_ascii=False, default=str)
        chunks = [
            serialized[offset : offset + 11_500]
            for offset in range(0, len(serialized), 11_500)
        ] or [""]
        for chunk_index, chunk in enumerate(chunks, 1):
            suffix = f" · {chunk_index}/{len(chunks)}" if len(chunks) > 1 else ""
            sources.append(
                AiAssistSourceSnapshot(
                    source_no=len(sources) + 1,
                    source_type="manual_context",
                    label=f"검색 근거 {entry_index}{suffix}",
                    text=chunk,
                    metadata={"number": entry_index, "part": chunk_index},
                )
            )
    question = (
        "다음 검색 근거의 큰 흐름만 한국어로 3~5개 항목으로 요약하세요. "
        "받아쓰기나 메시지 전체 나열은 하지 말고, 반복 변화·주의점·미해결 업무를 우선하세요. "
        "확인되는 사실만 쓰고 각 항목 끝에 근거 번호를 [1]처럼 표시하세요. "
        "RESIDENT_001 같은 표기는 철자와 번호를 바꾸지 마세요."
    )
    try:
        analysis = worker.analyze(
            task_type="summary",
            question=question,
            sources=sources,
        )
    except CodexWorkerError as exc:
        raise LocalAiError(exc.public_message) from exc
    summary = restore_external_resident_names(
        analysis.result.answer.strip(),
        resident_aliases,
    )
    if not summary:
        raise LocalAiError("Codex 요약 결과가 비어 있습니다.")
    return RoomSummaryResult(
        summary=summary,
        provider="codex_worker",
        model=analysis.model,
        elapsed_ms=analysis.elapsed_ms,
    )


def _search_summary_with_fallback(
    entries: list[dict[str, Any]],
    *,
    preferred_provider: str,
    resident_names: list[str],
    fallback_summary: str,
) -> tuple[RoomSummaryResult, str | None, bool]:
    if preferred_provider == "codex":
        provider_order = ["codex", "nvidia", "local", "rules"]
    elif preferred_provider == "nvidia":
        provider_order = ["nvidia", "codex", "local", "rules"]
    elif preferred_provider == "local":
        provider_order = ["local", "rules"]
    elif preferred_provider == "rules":
        provider_order = ["rules"]
    else:
        provider_order = ["configured", "rules"]

    cache_key = _search_summary_cache_key(
        entries,
        preferred_provider=preferred_provider,
    )
    cached = _search_summary_cache_get(cache_key)
    if cached is not None:
        return cached[0], cached[1], True

    local_ready, local_status = local_summary_provider_status()
    provider_ready = {
        "nvidia": nemotron_is_configured(),
        "local": local_ready,
    }
    failures: list[str] = []
    for provider in provider_order:
        if provider == "nvidia" and not provider_ready["nvidia"]:
            failures.append(
                "Nemotron: 운영 등록 파일에서 사용할 설정을 확인하지 못했습니다."
            )
            continue
        if provider in {"local", "configured"} and not provider_ready["local"]:
            failures.append(f"로컬 AI: {local_status}")
            continue
        try:
            if provider == "codex":
                result = _codex_search_summary(
                    entries,
                    resident_names=resident_names,
                )
            elif provider == "nvidia":
                result = summarize_room_messages(
                    entries=entries,
                    external_allowed=True,
                    preferred_provider="nvidia",
                    resident_names=resident_names,
                    timeout_seconds=20,
                )
            elif provider == "local":
                result = summarize_room_messages(
                    entries=entries,
                    external_allowed=False,
                    preferred_provider="local",
                    timeout_seconds=settings.ai_review_timeout_seconds,
                )
            elif provider == "configured":
                result = summarize_room_messages(
                    entries=entries,
                    external_allowed=False,
                    resident_names=resident_names,
                    timeout_seconds=settings.ai_review_timeout_seconds,
                )
            else:
                result = RoomSummaryResult(
                    summary=fallback_summary,
                    provider="rules",
                    model="hierarchical-search-summary-v1",
                    elapsed_ms=0,
                )
            fallback_reason = "; ".join(failures) or None
            if result.provider != "rules":
                _search_summary_cache_put(cache_key, result, fallback_reason)
            return result, fallback_reason, False
        except LocalAiError as exc:
            label = {
                "codex": "Codex",
                "nvidia": "Nemotron",
                "local": "로컬 AI",
                "configured": "기본 AI",
            }.get(provider, provider)
            failures.append(f"{label}: {str(exc)[:160]}")
        except Exception:
            label = {
                "codex": "Codex",
                "nvidia": "Nemotron",
                "local": "로컬 AI",
                "configured": "기본 AI",
            }.get(provider, provider)
            logger.exception("Search summary provider failed: %s", provider)
            failures.append(f"{label}: internal summary error")
    return (
        RoomSummaryResult(
            summary=fallback_summary,
            provider="rules",
            model="hierarchical-search-summary-v1",
            elapsed_ms=0,
        ),
        "; ".join(failures) or None,
        False,
    )


def _adaptive_search_facts(
    entries: list[dict[str, Any]],
    *,
    by_id: dict[UUID, Message],
    resident_names: list[str],
    include_comments: bool,
) -> list[dict[str, Any]]:
    """Build resident-safe facts from already authorized search entries."""

    generic_bodies = {
        "파일을 첨부했습니다.",
        "사진을 첨부했습니다.",
        "음성을 첨부했습니다.",
    }
    facts: list[dict[str, Any]] = []
    for entry in entries:
        message = by_id[entry["message_id"]]
        reply = (message.extra_data or {}).get("reply_to") or {}
        event_key = str(reply.get("message_id") or message.id)
        source_rows: list[tuple[str, datetime, str, bool]] = []
        body = str(entry.get("body") or "").strip()
        if body and body not in generic_bodies:
            source_rows.append((body, message.created_at, "event", message.is_test_data is True))
        if include_comments:
            allowed_comments = list(entry.get("comments") or [])
            for index, text in enumerate(allowed_comments):
                comment = message.comments[index] if index < len(message.comments) else None
                source_rows.append(
                    (
                        str(text),
                        comment.created_at if comment is not None else message.created_at,
                        "followup",
                        comment.is_test_data is True if comment is not None else message.is_test_data is True,
                    )
                )
        attachment_text = str(entry.get("attachment_text") or "").strip()
        if attachment_text:
            source_rows.append(
                (attachment_text, message.created_at, "event", message.is_test_data is True)
            )
        action_text = {
            "assigned": "업무가 지정되었습니다.",
            "acknowledged": "업무 내용을 확인했습니다.",
            "in_progress": "업무 처리를 시작했습니다.",
            "completed": "업무 처리를 완료했습니다.",
        }.get(str(entry.get("action_status") or ""))
        if action_text:
            source_rows.append(
                (action_text, message.created_at, "followup", message.is_test_data is True)
            )

        resident_refs = [
            ref
            for ref in entry.get("resident_refs") or []
            if isinstance(ref, dict) and ref.get("id") and str(ref.get("name") or "").strip()
        ]
        for text, occurred_at, kind, is_test_data in source_rows:
            common = {
                "message_id": message.id,
                "occurred_at": occurred_at,
                "kind": kind,
                "event_key": event_key,
                "is_test_data": is_test_data,
            }
            if not resident_refs:
                facts.append({**common, "resident_id": None, "resident_name": "", "summary": text})
                continue
            if len(resident_refs) == 1:
                ref = resident_refs[0]
                facts.append(
                    {
                        **common,
                        "resident_id": ref["id"],
                        "resident_name": str(ref["name"]),
                        "summary": text,
                    }
                )
                continue

            narrowed_any = False
            for ref in resident_refs:
                narrowed = resident_specific_search_text(
                    text,
                    target_name=str(ref["name"]),
                    resident_names=resident_names,
                    allow_unscoped=False,
                )
                if not narrowed:
                    continue
                narrowed_any = True
                facts.append(
                    {
                        **common,
                        "resident_id": ref["id"],
                        "resident_name": str(ref["name"]),
                        "summary": narrowed,
                    }
                )
            if not narrowed_any:
                facts.append({**common, "resident_id": None, "resident_name": "", "summary": text})
    facts.sort(key=lambda item: (_as_utc(item["occurred_at"]), str(item["message_id"])))
    return facts


@app.post(
    "/api/rooms/{room_id}/message-search/summary",
    response_model=RoomSearchSummaryResponse,
)
async def summarize_room_search(
    room_id: UUID,
    payload: RoomSearchSummaryRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    request_started = monotonic()
    permission_started = monotonic()
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(status_code=403, detail="이 채팅방을 요약할 수 없습니다.")
    permission_ms = int((monotonic() - permission_started) * 1000)
    if payload.local_model_override and user.role != "admin":
        raise HTTPException(status_code=403, detail="모델 시험은 관리자만 사용할 수 있습니다.")
    query_started = monotonic()
    selected_message_query = (
        select(Message)
        .options(*_message_list_query_options())
        .where(
            Message.room_id == room_id,
            Message.id.in_(payload.message_ids),
            Message.lifecycle_status == "active",
        )
    )
    if getattr(user, "_reviewer_experience", None) is not None:
        selected_message_query = selected_message_query.where(
            Message.is_test_data.is_(True)
        )
    selected_messages = db.scalars(selected_message_query).all()
    by_id = {message.id: message for message in selected_messages}
    ordered_messages = [
        by_id[message_id] for message_id in payload.message_ids if message_id in by_id
    ]
    if len(ordered_messages) != len(payload.message_ids):
        raise HTTPException(
            status_code=422, detail="검색 결과에 없는 메시지가 포함되어 있습니다."
        )

    kst = timezone(timedelta(hours=9))
    query_text = (payload.query or "").strip().casefold()
    for message in ordered_messages:
        local_date = _as_utc(message.created_at).astimezone(kst).date()
        confirmed_ids = {
            link.resident_id
            for link in message.resident_links
            if link.status == "confirmed" and resident_link_is_current(link, message)
        }
        action_item = message.action_item
        action_matches = (
            payload.action_status is None
            or payload.action_status == "none" and action_item is None
            or payload.action_status != "none" and action_item is not None and action_item.status == payload.action_status
        )
        scope_matches = (
            (payload.date_from is None or local_date >= payload.date_from)
            and (payload.date_to is None or local_date <= payload.date_to)
            and (payload.resident_id is None or message.resident_id == payload.resident_id or payload.resident_id in confirmed_ids)
            and (payload.message_type is None or message.message_type == payload.message_type)
            and action_matches
            and (not query_text or bool(_message_search_matches(message, query_text, include_comments=user.role == "admin")))
        )
        if not scope_matches:
            raise HTTPException(status_code=422, detail="현재 검색 조건에 포함되지 않은 메시지가 있습니다. 다시 검색해 주세요.")
    query_ms = int((monotonic() - query_started) * 1000)

    entries: list[dict[str, Any]] = []
    # 첨부 판독문 안의 이름은 메시지에 아직 연결되지 않았을 수 있다.
    # 외부 AI 전송 전에 현재 조직의 현원 이름 전체를 가명 처리 후보로 사용한다.
    resident_names: set[str] = set(
        db.scalars(
            select(Resident.display_name).where(
                Resident.organization_id == user.organization_id,
                Resident.is_active.is_(True),
            )
        ).all()
    )
    for index, message in enumerate(ordered_messages, 1):
        attachment_sources: list[str] = []
        attachment_source_labels: list[str] = []
        for attachment in message.attachments:
            extraction = attachment.text_extraction
            if extraction is None:
                continue
            text = attachment_evidence_text(attachment)
            if not text.strip():
                continue
            source_label = (
                "음성 받아쓰기"
                if attachment.mime_type.startswith("audio/")
                else "이미지 판독문"
            )
            attachment_source_labels.append(source_label)
            attachment_sources.append(
                f"[{source_label} · {attachment.original_name}]\n{text.strip()}"
            )
        message_resident_refs = [
            {"id": link.resident.id, "name": link.resident.display_name}
            for link in message.resident_links
            if link.status == "confirmed" and resident_link_is_current(link, message)
        ]
        if message.resident is not None and all(
            ref["id"] != message.resident.id for ref in message_resident_refs
        ):
            message_resident_refs.insert(
                0,
                {"id": message.resident.id, "name": message.resident.display_name},
            )
        message_resident_names = [str(ref["name"]) for ref in message_resident_refs]
        resident_names.update(message_resident_names)
        source_labels = list(dict.fromkeys(attachment_source_labels))
        message_body = (message.body or "").strip()
        if message_body and message_body not in {
            "파일을 첨부했습니다.",
            "사진을 첨부했습니다.",
            "음성을 첨부했습니다.",
        }:
            source_labels.insert(0, "대화")
        entries.append(
            {
                "number": index,
                "message_id": message.id,
                "created_at": _as_utc(message.created_at).isoformat(),
                "sender": message.sender.full_name,
                "resident": (
                    message.resident.display_name if message.resident else None
                ),
                "resident_names": list(dict.fromkeys(message_resident_names)),
                "resident_refs": message_resident_refs,
                "body": message.body or "",
                "comments": [comment.body for comment in message.comments] if user.role == "admin" else [],
                "attachment_text": "\n\n".join(attachment_sources),
                "source_label": " · ".join(source_labels) or "대화",
                "action_status": (
                    message.action_item.status if message.action_item else None
                ),
            }
        )
    if payload.resident_id is not None:
        entries = isolate_search_entries_for_resident(
            entries,
            resident_id=payload.resident_id,
            known_resident_names=sorted(resident_names),
        )
        if not entries:
            raise HTTPException(
                status_code=422,
                detail="선택한 어르신에게 안전하게 연결할 수 있는 검색 근거가 없습니다.",
            )
    structure = _build_search_summary_structure(
        entries,
        resident_id=payload.resident_id,
        requested_from=payload.date_from,
        requested_to=payload.date_to,
    )
    preprocess_started = monotonic()
    facts = _adaptive_search_facts(
        entries,
        by_id=by_id,
        resident_names=sorted(resident_names),
        include_comments=user.role == "admin",
    )
    period_start, period_end = _search_period_bounds(
        entries,
        requested_from=payload.date_from,
        requested_to=payload.date_to,
    )
    summary_mode = choose_summary_mode(
        facts,
        date_span_days=(period_end - period_start).days,
        selected_resident_id=payload.resident_id,
    )
    prepared = preprocess_search_facts(facts, mode=summary_mode, max_facts=32)
    preprocess_ms = int((monotonic() - preprocess_started) * 1000)
    evidence_message_ids = {entry["message_id"] for entry in entries}
    names = _record_identity_names(db, user.organization_id)
    before_review_state = message_review_fingerprint(db, evidence_message_ids)

    result_fingerprint = sha256(
        json.dumps(
            [
                {
                    "message_id": str(row["message_id"]),
                    "resident_id": str(row.get("resident_id") or ""),
                    "occurred_at": _as_utc(row["occurred_at"]).isoformat(),
                    "kind": row.get("kind"),
                    "event_key": row.get("event_key"),
                    "text_sha256": sha256(str(row.get("summary") or "").encode("utf-8")).hexdigest(),
                }
                for row in facts
            ],
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    model_fingerprint = search_model_fingerprint()
    if payload.local_model_override:
        model_fingerprint = sha256(
            f"{model_fingerprint}:{payload.local_model_override}".encode("utf-8")
        ).hexdigest()
    cache_key = search_summary_cache_key(
        organization_id=user.organization_id,
        user_id=user.id,
        room_id=room_id,
        filters={
            "query": payload.query or "",
            "date_from": payload.date_from,
            "date_to": payload.date_to,
            "resident_id": payload.resident_id,
            "message_type": payload.message_type,
            "action_status": payload.action_status,
            "message_ids": [str(value) for value in payload.message_ids],
        },
        result_fingerprint=result_fingerprint,
        model_fingerprint=model_fingerprint,
    )
    cached = _adaptive_search_cache_get(cache_key)
    if cached is not None:
        _recheck_record_text_access(
            db, user, evidence_message_ids, require_membership=True
        )
        if before_review_state != message_review_fingerprint(db, evidence_message_ids):
            raise HTTPException(
                409, "근거의 직원 확인 상태가 변경되었습니다. 다시 조회해 주세요."
            )
        elapsed = int((monotonic() - request_started) * 1000)
        return cached.model_copy(
            update={
                "cache_hit": True,
                "request_elapsed_ms": elapsed,
                "performance": {
                    **cached.performance,
                    "permission_ms": permission_ms,
                    "record_query_ms": query_ms,
                    "cache_hit": True,
                    "total_ms": elapsed,
                },
            }
        )

    generated = await await_connected(
        asyncio.create_task(
            generate_search_summary(
                facts=facts,
                names=names,
                mode=summary_mode,
                all_synthetic=bool(facts) and all(row.get("is_test_data") is True for row in facts),
                request_key=cache_key,
                deadline=perf_counter() + settings.ai_review_timeout_seconds,
                model_override=payload.local_model_override,
            )
        ),
        request,
    )
    _recheck_record_text_access(
        db, user, evidence_message_ids, require_membership=True
    )
    if before_review_state != message_review_fingerprint(db, evidence_message_ids):
        raise HTTPException(
            409, "근거의 직원 확인 상태가 변경되었습니다. 다시 조회해 주세요."
        )

    used_model = bool(
        generated.get("processing_method") == "local_ai"
        and generated.get("generation_verified") is True
        and generated.get("sentences")
    )
    fallback = None if used_model else build_search_fallback(prepared["facts"], mode=summary_mode)
    sentence_rows = generated.get("sentences") if used_model else fallback["sentences"]
    summary_sentences = []
    for row in sentence_rows:
        summary_sentences.append(
            {
                "text": row["text"],
                "summary_type": row.get("summary_type", "core"),
                "resident_id": row.get("resident_id"),
                "evidence_ids": row.get("evidence_ids", []),
                "first_event_id": row.get("first_event_id"),
                "follow_up_ids": row.get("follow_up_ids", []),
                "latest_record_id": row.get("latest_record_id"),
                "needs_follow_up": row.get("needs_follow_up", False),
                "unconfirmed_part": row.get("unconfirmed_part", ""),
            }
        )
    final_evidence_ids = list(
        dict.fromkeys(
            identifier
            for row in summary_sentences
            for identifier in row["evidence_ids"]
        )
    )
    evidence_by_id: dict[UUID, dict[str, Any]] = {}
    for row in [
        *structure["general_evidence"],
        *[
            evidence
            for resident in structure["resident_summaries"]
            for evidence in resident["evidence"]
        ],
    ]:
        evidence_by_id.setdefault(row["message_id"], row)
    summary_evidence = [
        evidence_by_id[identifier]
        for identifier in final_evidence_ids
        if identifier in evidence_by_id
    ]
    response_started = monotonic()
    elapsed = int((monotonic() - request_started) * 1000)
    response = RoomSearchSummaryResponse(
        summary=generated["answer"] if used_model else fallback["summary"],
        source_message_ids=list(dict.fromkeys(entry["message_id"] for entry in entries)),
        generator=(
            f"ollama:{generated.get('model_used')}"
            if used_model
            else "rules:adaptive-search-summary-v1"
        ),
        provider_notice="중앙 텍스트 기본 모델을 사용하며 외부 AI로 전환하지 않습니다.",
        fallback_reason=(
            None
            if used_model
            else "AI 요약을 만들지 못해 확인된 기록으로 정리했습니다."
        ),
        ai_elapsed_ms=int(generated.get("ai_elapsed_ms") or 0),
        request_elapsed_ms=elapsed,
        cache_hit=False,
        processing_method="local_ai" if used_model else "rules",
        generation_verified=used_model,
        summary_sentences=summary_sentences,
        summary_evidence=summary_evidence,
        staged=bool(prepared["staged"]),
        performance={
            "permission_ms": permission_ms,
            "record_query_ms": query_ms,
            "preprocess_ms": preprocess_ms,
            "model_status_ms": generated.get("model_status_ms") or 0,
            "model_load_ms": generated.get("load_ms"),
            "prompt_ms": generated.get("prefill_ms"),
            "generation_ms": generated.get("generation_ms"),
            "draft_ms": generated.get("draft_ms") or 0,
            "review_ms": generated.get("review_ms") or 0,
            "validation_ms": generated.get("validation_ms") or 0,
            "accepted_sentence_count": generated.get("accepted_sentence_count") or 0,
            "rejected_sentence_count": generated.get("rejected_sentence_count") or 0,
            "rejection_types": generated.get("rejection_types"),
            "candidate_count": generated.get("candidate_count", len(prepared["facts"])),
            "deduplicated_count": generated.get("deduplicated_count", prepared["deduplicated_count"]),
            "response_build_ms": int((monotonic() - response_started) * 1000),
            "cache_hit": False,
            "error_type": generated.get("error_type"),
            "total_ms": elapsed,
        },
        display_mode="overview" if summary_mode == "overview" else "direct",
        period_granularity=structure["period_granularity"],
        counts=structure["counts"],
        highlights=structure["highlights"],
        timeline=structure["timeline"],
        resident_summaries=structure["resident_summaries"],
        general_evidence=structure["general_evidence"],
    )
    response.request_elapsed_ms = int((monotonic() - request_started) * 1000)
    response.performance["response_build_ms"] = int(
        (monotonic() - response_started) * 1000
    )
    response.performance["total_ms"] = response.request_elapsed_ms
    if used_model:
        _adaptive_search_cache_put(cache_key, response)
    return response


@app.post(
    "/api/rooms/{room_id}/messages", response_model=MessageResponse, status_code=201
)
async def send_message(
    room_id: UUID,
    payload: MessageCreate,
    background_tasks: BackgroundTasks,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if active_membership(db, user.id, room_id) is None:
        raise HTTPException(
            status_code=403, detail="이 채팅방에 메시지를 보낼 수 없습니다."
        )
    if payload.message_type == "notice" and user.role != "admin":
        raise HTTPException(status_code=403, detail="공지 작성은 관리자만 가능합니다.")
    room = db.get(Room, room_id)
    if room is None or not room.is_active:
        raise HTTPException(status_code=404, detail="채팅방을 찾을 수 없습니다.")
    interaction_is_test_data = _interaction_is_test_data(user, room=room)
    reply_snapshot = _reply_snapshot_for_room(
        db,
        room=room,
        reply_to_message_id=payload.reply_to_message_id,
    )
    selected_residents = _residents_for_room(
        db,
        room,
        resident_id=payload.resident_id,
        resident_ids=payload.resident_ids,
    )
    resident = selected_residents[0] if selected_residents else None
    classification, classification_metadata = _automatic_message_nature(
        requested_message_type=payload.message_type,
        user=user,
        body=payload.body,
        action_type=payload.action.action_type if payload.action else None,
    )
    message = Message(
        organization_id=user.organization_id,
        room_id=room_id,
        sender_id=user.id,
        message_type=classification.message_type,
        body=payload.body,
        resident_id=resident.id if resident else None,
        resident_ref=payload.resident_ref,
        extra_data={"reply_to": reply_snapshot} if reply_snapshot else None,
        is_test_data=interaction_is_test_data,
    )
    db.add(message)
    db.flush()
    _record_message_nature_analysis(
        db,
        message=message,
        user=user,
        decision=classification,
        metadata=classification_metadata,
    )
    for selected_resident in selected_residents:
        _confirm_manual_resident_link(
            db,
            message=message,
            resident=selected_resident,
        )
    detected_links = _sync_message_resident_candidates(
        db,
        message=message,
        text=message.body,
        source="text_exact",
    )
    if not selected_residents and not detected_links:
        scoped_resident = _single_scoped_resident_for_room(db, room)
        if scoped_resident is not None:
            message.resident_id = scoped_resident.id
            selected_residents = [scoped_resident]
            _confirm_manual_resident_link(
                db,
                message=message,
                resident=scoped_resident,
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
        # 직원은 어르신을 먼저 고르지 않고 대화를 남긴다. 대상이 아직
        # 없더라도 원문 메시지는 관리자 검토 목록에 남겨 나중에 안전하게
        # 연결하며, 이 단계에서는 공식 기록이나 서류 초안을 만들지 않는다.
        force=True,
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
        {
            "event": "message_created",
            "message": response_payload.model_dump(mode="json"),
        },
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
    resident_ids: Annotated[list[UUID] | None, Form()] = None,
    reply_to_message_id: Annotated[UUID | None, Form()] = None,
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
        raise HTTPException(
            status_code=403, detail="이 채팅방에 메시지를 보낼 수 없습니다."
        )
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
    reply_snapshot = _reply_snapshot_for_room(
        db,
        room=room,
        reply_to_message_id=reply_to_message_id,
    )
    selected_residents = _residents_for_room(
        db,
        room,
        resident_id=resident_id,
        resident_ids=resident_ids,
    )
    resident = selected_residents[0] if selected_residents else None
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
            "보고서 이미지를 첨부했습니다." if report_image else "파일을 첨부했습니다."
        )
    classification, classification_metadata = _automatic_message_nature(
        requested_message_type=message_type,
        user=user,
        body=normalized_body,
        has_attachments=True,
        report_image=report_image,
        action_type=action_type,
    )
    message = Message(
        organization_id=user.organization_id,
        room_id=room_id,
        sender_id=user.id,
        message_type=classification.message_type,
        body=normalized_body,
        resident_id=resident.id if resident else None,
        extra_data={"reply_to": reply_snapshot} if reply_snapshot else None,
        is_test_data=interaction_is_test_data,
    )
    db.add(message)
    db.flush()
    _record_message_nature_analysis(
        db,
        message=message,
        user=user,
        decision=classification,
        metadata=classification_metadata,
        has_attachments=True,
        report_image=report_image,
    )
    for selected_resident in selected_residents:
        _confirm_manual_resident_link(
            db,
            message=message,
            resident=selected_resident,
        )
    detected_links = _sync_message_resident_candidates(
        db,
        message=message,
        text=message.body,
        source="text_exact",
    )
    if not selected_residents and not detected_links:
        scoped_resident = _single_scoped_resident_for_room(db, room)
        if scoped_resident is not None:
            message.resident_id = scoped_resident.id
            selected_residents = [scoped_resident]
            _confirm_manual_resident_link(
                db,
                message=message,
                resident=scoped_resident,
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
            raise HTTPException(
                status_code=422, detail="업무 지정정보가 올바르지 않습니다."
            ) from exc
    _create_action_item(
        db,
        message=message,
        creator=user,
        payload=action_payload,
    )
    stored_paths: list[Path] = []
    stored_total_bytes = 0
    extraction_attachments: list[tuple[UUID, str]] = []
    try:
        for upload_ordinal, upload in enumerate(uploads):
            attachment, stored_path = await _store_attachment(
                db,
                upload=upload,
                upload_ordinal=upload_ordinal,
                message=message,
                user=user,
            )
            stored_paths.append(stored_path)
            stored_total_bytes += attachment.size_bytes
            if stored_total_bytes > settings.max_attachments_total_bytes:
                raise HTTPException(
                    status_code=413,
                    detail=(
                        "한 메시지의 파일 전체 용량은 "
                        f"{settings.max_attachments_total_bytes // (1024 * 1024)}MB "
                        "이하여야 합니다."
                    ),
                )
            should_extract_image = (
                attachment.mime_type in IMAGE_MIME_TYPES
                and report_image
            )
            should_transcribe_audio = (
                settings.stt_enabled and attachment.mime_type in AUDIO_MIME_TYPES
            )
            if should_extract_image or should_transcribe_audio or attachment.mime_type in DOCUMENT_MIME_TYPES:
                _queue_attachment_text_extraction(
                    db,
                    attachment=attachment,
                    requested_by=user,
                )
                extraction_attachments.append((attachment.id, attachment.mime_type))
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
            # PDF처럼 자동 판독하지 않는 파일과 판독 실패 파일도 원문은
            # 이미 저장됐다. 대상 미지정 자료를 잃지 않도록 모든 첨부
            # 메시지를 관리자 검토 목록에 먼저 남긴다.
            force=True,
        )
        db.commit()
    except Exception:
        db.rollback()
        for stored_path in stored_paths:
            stored_path.unlink(missing_ok=True)
        raise
    db.refresh(message)
    response_payload = message_response(message, db=db, viewer_id=user.id)
    if extraction_attachments:
        background_tasks.add_task(
            _run_new_attachment_processing_batch,
            extraction_attachments,
        )
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
        {
            "event": "message_created",
            "message": response_payload.model_dump(mode="json"),
        },
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
    source_attachments = sorted(
        source_message.attachments,
        key=lambda item: (item.upload_ordinal, item.created_at, str(item.id)),
    )
    for upload_ordinal, source_attachment in enumerate(source_attachments):
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
            raise HTTPException(
                status_code=422, detail="올바르지 않은 첨부파일 경로입니다."
            )
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
                upload_ordinal=upload_ordinal,
                sha256=source_attachment.sha256,
                created_at=utcnow(),
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
    _require_active_message(source_message, "전달")
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
        raise HTTPException(
            status_code=422, detail="전달할 다른 채팅방을 선택해 주세요."
        )
    if len(target_ids) > 50:
        raise HTTPException(
            status_code=422, detail="한 번에 최대 50개 방까지 전달할 수 있습니다."
        )

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
            target_is_test_data = bool(
                source_message.is_test_data
                and _interaction_is_test_data(user, room=allowed_rooms[room_id])
            )
            target = Message(
                organization_id=user.organization_id,
                room_id=room_id,
                sender_id=user.id,
                message_type="chat",
                body=source_message.body,
                resident_id=source_message.resident_id,
                resident_ref=source_message.resident_ref,
                extra_data={"forwarded_from": source_info},
                is_test_data=target_is_test_data,
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
                    is_test_data=target_is_test_data,
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
            "message_ids": [str(message_id) for message_id in newly_read_message_ids],
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
    _require_active_message(message, "상세 조회")
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
    if message.lifecycle_status == "recalled" or user.role != "admin":
        comments = []
    return MessageDetailResponse(
        # 답글·상세의 첫 화면은 본문, 첨부, 판독문만 먼저 반환한다. 첨부별
        # 교정 이력/이름 후보는 이미지마다 최근 이력을 다시 계산하므로 실제
        # 수정 버튼을 누를 때 해당 첨부 한 건에 대해서만 지연 조회한다.
        message=message_response(
            message,
            db=db,
            viewer_id=user.id,
            include_attachment_review_details=False,
        ),
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


async def _recall_message(
    message_id: UUID,
    reason: str | None,
    auth: tuple[LoginSession, User],
    db: Session,
) -> MessageRecallResponse:
    login_session, user = auth
    if user.must_change_password and login_session.impersonated_by_user_id is None:
        raise HTTPException(
            status_code=403,
            detail="계속하려면 먼저 임시 비밀번호를 변경해야 합니다.",
        )
    message = db.get(Message, message_id)
    if message is None or message.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="메시지를 찾을 수 없습니다.")
    if getattr(user, "_reviewer_experience", None) is not None:
        raise HTTPException(
            status_code=403,
            detail="심사 체험 화면에서는 메시지를 회수할 수 없습니다.",
        )
    if message.lifecycle_status == "recalled":
        raise HTTPException(status_code=409, detail="이미 회수한 메시지입니다.")
    if message.sender_id == user.id:
        _message_for_member(db, user, message_id)
    elif user.role == "admin":
        if not admin_conversation_access_is_active(login_session):
            raise HTTPException(
                status_code=403,
                detail="다른 직원의 메시지를 회수하려면 관리자 비밀번호를 다시 입력해 주세요.",
            )
        if not reason:
            raise HTTPException(
                status_code=422,
                detail="다른 직원의 메시지를 회수하는 사유를 입력해 주세요.",
            )
    else:
        raise HTTPException(
            status_code=403,
            detail="본인이 작성한 메시지만 회수할 수 있습니다.",
        )

    organization_messages = db.scalars(
        select(Message).where(Message.organization_id == user.organization_id)
    ).all()
    messages_by_id = {candidate.id: candidate for candidate in organization_messages}
    affected_ids = {message.id}
    changed = True
    while changed:
        changed = False
        for candidate in organization_messages:
            if candidate.id in affected_ids or not candidate.extra_data:
                continue
            forwarded_from = candidate.extra_data.get("forwarded_from")
            if not isinstance(forwarded_from, dict):
                continue
            try:
                source_id = UUID(str(forwarded_from.get("message_id")))
            except (TypeError, ValueError):
                continue
            if source_id in affected_ids:
                affected_ids.add(candidate.id)
                changed = True

    affected_messages = [
        messages_by_id[target_id]
        for target_id in affected_ids
        if messages_by_id[target_id].lifecycle_status != "recalled"
    ]
    room_ids = {candidate.room_id for candidate in affected_messages}
    attachment_ids = {
        attachment.id
        for candidate in affected_messages
        for attachment in candidate.attachments
    }
    if room_ids:
        db.execute(delete(RoomDigest).where(RoomDigest.room_id.in_(room_ids)))

    recalled_at = utcnow()
    recall_group_id = message.id
    for candidate in affected_messages:
        candidate.lifecycle_status = "recalled"
        candidate.recalled_at = recalled_at
        candidate.recalled_by_id = user.id
        candidate.recall_reason = reason
        candidate.recall_group_id = recall_group_id
    record_audit(
        db,
        actor_id=user.id,
        action="message.recalled",
        target_type="message",
        target_id=message.id,
        details={
            "room_ids": sorted(str(room_id) for room_id in room_ids),
            "recalled_message_count": len(affected_messages),
            "forwarded_copy_count": max(0, len(affected_messages) - 1),
            "attachment_count": len(attachment_ids),
            "reason_provided": bool(reason),
            "data_retained": True,
        },
    )
    db.commit()

    notification_user_ids: set[UUID] = set()
    for room_id in room_ids:
        notification_user_ids.update(room_member_user_ids(db, room_id))
    if notification_user_ids:
        await manager.send_to_users(
            notification_user_ids,
            {
                "event": "message_recalled",
                "message_ids": sorted(str(target_id) for target_id in affected_ids),
                "room_ids": sorted(str(room_id) for room_id in room_ids),
            },
        )
    return MessageRecallResponse(
        message_id=message.id,
        recalled_message_ids=sorted(affected_ids, key=str),
        recalled_at=recalled_at,
    )


@app.post(
    "/api/messages/{message_id}/recall",
    response_model=MessageRecallResponse,
)
async def recall_message(
    message_id: UUID,
    payload: MessageRecallRequest,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    return await _recall_message(message_id, payload.reason, auth, db)


@app.delete("/api/messages/{message_id}", status_code=204)
async def recall_message_legacy_delete(
    message_id: UUID,
    auth: tuple[LoginSession, User] = Depends(get_current_session_and_user),
    db: Session = Depends(get_db),
):
    await _recall_message(message_id, None, auth, db)
    return Response(status_code=204)


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
    _require_active_message(message, "업무 지정")
    existing = db.scalar(
        select(ActionItem).where(ActionItem.source_message_id == message.id)
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail="이미 담당자가 지정된 메시지입니다."
        )
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
    processor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Older clients share the same author/processor checks and audit/cache path.
    from .resident_review_flow import apply_review
    message = apply_review(db, processor, message_id,
        "confirm" if payload.status == "confirmed" else "reject_one", resident_id)
    return message_response(message, db=db, viewer_id=processor.id)


@app.post("/api/messages/{message_id}/comments/read", status_code=204)
def mark_message_comments_read(
    message_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="코멘트는 관리자만 확인할 수 있습니다.",
        )
    message = _message_for_member(db, user, message_id)
    _require_active_message(message, "댓글 작성")
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
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="코멘트는 관리자만 등록할 수 있습니다.",
        )
    message = _message_for_member(db, user, message_id)
    _require_active_message(message, "댓글 작성")
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
    message = db.get(Message, attachment.message_id)
    if message is None or message.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    _require_active_message(message, "첨부파일 열기")
    access = attachment_access_capabilities(db, attachment, user.id)
    if not access["can_download_original"]:
        raise HTTPException(
            status_code=403, detail="현재 이 대화방에 접근할 수 없습니다."
        )
    response = _attachment_file_response(attachment)
    # FileResponse가 원본 파일을 전송하는 동안 yield 의존성의 Session을
    # 계속 보유하면 여러 사진을 동시에 여는 채팅방에서 DB 풀이 고갈된다.
    # 권한 확인과 파일 경로 결정을 끝낸 뒤 연결을 먼저 반환한다.
    db.close()
    return response


@app.get("/api/attachments/{attachment_id}/thumbnail")
def download_attachment_thumbnail(
    attachment_id: UUID,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None or attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=404, detail="사진 미리보기를 찾을 수 없습니다.")
    message = _message_for_member(db, user, attachment.message_id)
    _require_active_message(message, "사진 미리보기")
    upload_dir = Path(settings.upload_dir).resolve()
    source = (upload_dir / attachment.storage_key).resolve()
    if source.parent != upload_dir or not source.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    try:
        target = ensure_image_thumbnail(
            source,
            thumbnail_path(upload_dir, attachment.storage_key),
        )
    except (OSError, ValueError):
        raise HTTPException(
            status_code=422,
            detail="사진 미리보기를 만들 수 없습니다.",
        ) from None
    db.close()
    return FileResponse(
        target,
        media_type="image/webp",
        headers={
            "Cache-Control": "private, max-age=604800, immutable",
            "Vary": "Cookie",
        },
    )


def _attachment_file_response(attachment: MessageAttachment) -> FileResponse:
    upload_dir = Path(settings.upload_dir).resolve()
    target = (upload_dir / attachment.storage_key).resolve()
    if target.parent != upload_dir or not target.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    headers = {
        "Cache-Control": "private, max-age=86400",
        "Vary": "Cookie",
        "X-Content-Type-Options": "nosniff",
    }
    if is_download_only_document(attachment.mime_type):
        return FileResponse(
            target,
            media_type=attachment.mime_type,
            filename=attachment.original_name,
            content_disposition_type="attachment",
            headers=headers,
        )
    return FileResponse(target, media_type=attachment.mime_type, headers=headers)


def _attachment_for_processor(
    db: Session,
    processor: User,
    attachment_id: UUID,
) -> MessageAttachment:
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    message = db.get(Message, attachment.message_id)
    if message is None or message.organization_id != processor.organization_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    _require_active_message(message, "첨부파일 열기")
    access = attachment_access_capabilities(db, attachment, processor.id)
    if access["can_download_original"]:
        return attachment
    raise HTTPException(
        status_code=403,
        detail="현재 이 대화방에 접근할 수 없습니다.",
    )


def _attachment_for_text_editor(
    db: Session,
    editor: User,
    attachment_id: UUID,
) -> MessageAttachment:
    attachment = db.get(MessageAttachment, attachment_id)
    if attachment is None or attachment.organization_id != editor.organization_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    message = db.get(Message, attachment.message_id)
    if message is None or message.organization_id != editor.organization_id:
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    _require_active_message(message, "첨부 내용 확인·수정")
    access = attachment_access_capabilities(db, attachment, editor.id)
    if access["can_review_text"]:
        return attachment
    raise HTTPException(
        status_code=403,
        detail="이 문서의 내용을 확인·수정할 권한이 없습니다.",
    )


@app.get("/api/workdesk/attachments/{attachment_id}")
def download_workdesk_attachment(
    attachment_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_processor(db, processor, attachment_id)
    return _attachment_file_response(attachment)


_AUTO_COORDINATE_DETAIL_KIND = "coordinate_auto_location"
_AUTO_COORDINATE_DETAIL_VERSION = "report-line-locator-2-angle"


def _coordinate_line_location_from_report_region(
    item: Any,
) -> CoordinateLineLocation:
    box_values = {
        "left": item.normalized_bbox[0] / 1000,
        "top": item.normalized_bbox[1] / 1000,
        "width": (item.normalized_bbox[2] - item.normalized_bbox[0]) / 1000,
        "height": (item.normalized_bbox[3] - item.normalized_bbox[1]) / 1000,
    }
    try:
        bbox = CoordinateBox(
            **box_values,
            rotation_degrees=item.rotation_degrees,
        )
    except ValidationError:
        # Keep a useful location even when rotating a box near the image edge
        # would leave the canvas.  Automatic location is only a draft.
        bbox = CoordinateBox(**box_values, rotation_degrees=0)
    return CoordinateLineLocation(line_index=item.line_index, bbox=bbox)


def _saved_coordinate_auto_locations(
    suggestion_details: list[dict[str, Any]] | None,
) -> list[CoordinateLineLocation]:
    for item in reversed(suggestion_details or []):
        if (
            item.get("kind") != _AUTO_COORDINATE_DETAIL_KIND
            or item.get("status") != "completed"
        ):
            continue
        locations: list[CoordinateLineLocation] = []
        for raw_location in item.get("locations", []):
            if not isinstance(raw_location, dict):
                continue
            raw_bbox = raw_location.get("bbox")
            if not isinstance(raw_bbox, dict):
                continue
            try:
                locations.append(
                    CoordinateLineLocation(
                        line_index=int(raw_location["line_index"]),
                        bbox=CoordinateBox.model_validate(raw_bbox),
                        confidence=float(raw_location.get("confidence", 0.72)),
                    )
                )
            except (KeyError, TypeError, ValueError, ValidationError):
                continue
        return locations
    return []


def _store_coordinate_auto_location_detail(
    extraction: AttachmentTextExtraction,
    *,
    status: str,
    locations: list[CoordinateLineLocation] | None = None,
) -> None:
    retained = [
        item
        for item in (extraction.suggestion_details or [])
        if item.get("kind") != _AUTO_COORDINATE_DETAIL_KIND
    ]
    retained.append(
        {
            "kind": _AUTO_COORDINATE_DETAIL_KIND,
            "version": _AUTO_COORDINATE_DETAIL_VERSION,
            "status": status,
            "generated_at": utcnow().isoformat(),
            "locations": [
                {
                    "line_index": item.line_index,
                    "bbox": item.bbox.model_dump(mode="json"),
                    "confidence": item.confidence,
                }
                for item in (locations or [])
            ],
        }
    )
    extraction.suggestion_details = retained


def _run_attachment_coordinate_auto_location(attachment_id: UUID) -> None:
    """Persist a non-confirming line-location draft after successful image OCR."""

    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        if attachment is None or attachment.mime_type not in IMAGE_MIME_TYPES:
            return
        extraction = attachment.text_extraction
        if extraction is None or extraction.status not in {"completed", "reviewed"}:
            return
        if (
            db.scalar(
                select(AttachmentCoordinateReview.id)
                .where(AttachmentCoordinateReview.attachment_id == attachment.id)
                .limit(1)
            )
            is not None
        ):
            return
        existing_detail = next(
            (
                item
                for item in reversed(extraction.suggestion_details or [])
                if item.get("kind") == _AUTO_COORDINATE_DETAIL_KIND
                and item.get("status") == "completed"
            ),
            None,
        )
        if existing_detail is not None:
            return

        raw_text = extraction.original_extracted_text or extraction.extracted_text or ""
        confirmed_text = (
            extraction.reviewed_text
            or extraction.suggested_text
            or extraction.extracted_text
            or raw_text
        )
        displayed_regions = build_coordinate_bootstrap_regions(
            raw_text=raw_text,
            confirmed_text=confirmed_text,
            suggestion_details=extraction.suggestion_details,
        )
        displayed_lines = [
            (region.corrected_text or region.raw_text).strip()
            for region in displayed_regions
            if (region.corrected_text or region.raw_text).strip()
        ]
        if not displayed_lines:
            return
        upload_dir = Path(settings.upload_dir).resolve()
        image_path = (upload_dir / attachment.storage_key).resolve()
        if image_path.parent != upload_dir or not image_path.is_file():
            _store_coordinate_auto_location_detail(extraction, status="failed")
            db.commit()
            return
        try:
            located = locate_report_text_regions(image_path, lines=displayed_lines)
        except OcrError:
            _store_coordinate_auto_location_detail(extraction, status="failed")
            db.commit()
            return
        locations = [
            _coordinate_line_location_from_report_region(item) for item in located
        ]
        _store_coordinate_auto_location_detail(
            extraction,
            status="completed",
            locations=locations,
        )
        db.commit()


def _confirmed_coordinate_placement_evidence(
    db: Session,
    *,
    organization_id: UUID,
    document_template: str | None,
    exclude_attachment_id: UUID,
) -> list[CoordinatePlacementEvidence]:
    """Load only staff-confirmed positions from the same template version."""

    if not document_template:
        return []
    rows = db.execute(
        select(AttachmentCoordinateReview, OcrCorrectionEvent)
        .join(
            OcrCorrectionEvent,
            OcrCorrectionEvent.coordinate_review_id == AttachmentCoordinateReview.id,
        )
        .where(
            AttachmentCoordinateReview.organization_id == organization_id,
            AttachmentCoordinateReview.attachment_id != exclude_attachment_id,
            AttachmentCoordinateReview.document_template == document_template,
            OcrCorrectionEvent.confirmed.is_(True),
        )
        .order_by(
            AttachmentCoordinateReview.created_at.desc(),
            AttachmentCoordinateReview.id.desc(),
        )
        .limit(200)
    ).all()
    evidence: list[CoordinatePlacementEvidence] = []
    for review, _event in rows:
        for raw_region in review.regions or []:
            try:
                region = CoordinateRegion.model_validate(raw_region)
            except ValidationError:
                continue
            region_template = region.document_template or review.document_template
            if (
                region_template != document_template
                or region.placement_status != "confirmed"
                or not region.section_role
                or not region.corrected_text.strip()
            ):
                continue
            evidence.append(
                CoordinatePlacementEvidence(
                    document_template=document_template,
                    section_role=region.section_role,
                    role=region.role,
                    corrected_text=region.corrected_text.strip(),
                    bbox=region.bbox,
                )
            )
    return evidence


def _coordinate_review_response(
    db: Session,
    attachment: MessageAttachment,
    review: AttachmentCoordinateReview | None,
    *,
    auto_locations: list[CoordinateLineLocation] | None = None,
) -> AttachmentCoordinateReviewResponse:
    extraction = attachment.text_extraction
    if extraction is None:
        raise HTTPException(
            status_code=409, detail="먼저 이미지 글자 판독을 완료해 주세요."
        )
    raw_text = extraction.original_extracted_text or extraction.extracted_text or ""
    confirmed_text = (
        extraction.reviewed_text
        or extraction.suggested_text
        or extraction.extracted_text
        or raw_text
    )
    resident_query = select(Resident).where(
        Resident.organization_id == attachment.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
    )
    if extraction.suggested_service_context:
        resident_query = resident_query.where(
            Resident.service_type == extraction.suggested_service_context
        )
    resident_options = [
        {
            "id": resident.id,
            "display_name": resident.display_name,
            "service_type": resident.service_type,
        }
        for resident in db.scalars(
            resident_query.order_by(Resident.sort_order, Resident.display_name)
        ).all()
    ]
    if review is None:
        document_template = coordinate_template_key(extraction.suggestion_details)
        placement_evidence = _confirmed_coordinate_placement_evidence(
            db,
            organization_id=attachment.organization_id,
            document_template=document_template,
            exclude_attachment_id=attachment.id,
        )
        if auto_locations is None:
            auto_locations = _saved_coordinate_auto_locations(
                extraction.suggestion_details
            )
        return AttachmentCoordinateReviewResponse(
            version_number=0,
            editor_version="coordinate-editor-mvp-1",
            document_template=document_template,
            regions=build_coordinate_bootstrap_regions(
                raw_text=raw_text,
                confirmed_text=confirmed_text,
                suggestion_details=extraction.suggestion_details,
                auto_locations=auto_locations,
                placement_evidence=placement_evidence,
            ),
            is_bootstrap=True,
            raw_source_text=raw_text,
            confirmed_source_text=confirmed_text,
            resident_options=resident_options,
        )
    text_confirmation = db.scalar(
        select(OcrCorrectionEvent).where(
            OcrCorrectionEvent.coordinate_review_id == review.id
        )
    )
    if text_confirmation is not None and text_confirmation.corrected_text is not None:
        confirmed_text = text_confirmation.corrected_text
    return AttachmentCoordinateReviewResponse(
        id=review.id,
        version_number=review.version_number,
        image_width=review.image_width,
        image_height=review.image_height,
        editor_version=review.editor_version,
        document_template=review.document_template,
        regions=prepare_coordinate_regions_for_editor(review.regions),
        editor_name=review.editor.full_name,
        created_at=review.created_at,
        text_confirmation_id=(
            text_confirmation.id if text_confirmation is not None else None
        ),
        text_confirmed_at=(
            text_confirmation.created_at if text_confirmation is not None else None
        ),
        is_bootstrap=False,
        raw_source_text=raw_text,
        confirmed_source_text=confirmed_text,
        resident_options=resident_options,
    )


@app.get(
    "/api/attachments/{attachment_id}/coordinate-review",
    response_model=AttachmentCoordinateReviewResponse,
)
def get_attachment_coordinate_review(
    attachment_id: UUID,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    if attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=422, detail="이미지 첨부만 좌표 편집할 수 있습니다."
        )
    if attachment.text_extraction is None:
        raise HTTPException(
            status_code=409, detail="먼저 이미지 글자 판독을 완료해 주세요."
        )
    review = db.scalar(
        select(AttachmentCoordinateReview)
        .where(
            AttachmentCoordinateReview.attachment_id == attachment.id,
            AttachmentCoordinateReview.organization_id == editor.organization_id,
        )
        .order_by(
            AttachmentCoordinateReview.version_number.desc(),
            AttachmentCoordinateReview.created_at.desc(),
        )
        .limit(1)
    )
    return _coordinate_review_response(db, attachment, review)


@app.post(
    "/api/attachments/{attachment_id}/coordinate-review/auto-locate",
    response_model=AttachmentCoordinateReviewResponse,
)
def auto_locate_attachment_coordinate_review(
    attachment_id: UUID,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Find visual positions for existing OCR lines without saving a review."""

    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    if attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=422, detail="이미지 첨부만 좌표 편집할 수 있습니다."
        )
    extraction = attachment.text_extraction
    if extraction is None:
        raise HTTPException(
            status_code=409, detail="먼저 이미지 글자 판독을 완료해 주세요."
        )
    existing_review = db.scalar(
        select(AttachmentCoordinateReview)
        .where(
            AttachmentCoordinateReview.attachment_id == attachment.id,
            AttachmentCoordinateReview.organization_id == editor.organization_id,
        )
        .order_by(
            AttachmentCoordinateReview.version_number.desc(),
            AttachmentCoordinateReview.created_at.desc(),
        )
        .limit(1)
    )
    if existing_review is not None:
        return _coordinate_review_response(db, attachment, existing_review)

    saved_locations = _saved_coordinate_auto_locations(extraction.suggestion_details)
    if saved_locations:
        return _coordinate_review_response(
            db,
            attachment,
            None,
            auto_locations=saved_locations,
        )

    raw_text = extraction.original_extracted_text or extraction.extracted_text or ""
    confirmed_text = (
        extraction.reviewed_text
        or extraction.suggested_text
        or extraction.extracted_text
        or raw_text
    )
    displayed_regions = build_coordinate_bootstrap_regions(
        raw_text=raw_text,
        confirmed_text=confirmed_text,
        suggestion_details=extraction.suggestion_details,
    )
    displayed_lines = [
        (region.corrected_text or region.raw_text).strip()
        for region in displayed_regions
        if (region.corrected_text or region.raw_text).strip()
    ]
    upload_dir = Path(settings.upload_dir).resolve()
    image_path = (upload_dir / attachment.storage_key).resolve()
    if image_path.parent != upload_dir or not image_path.is_file():
        raise HTTPException(status_code=404, detail="첨부파일을 찾을 수 없습니다.")
    try:
        located = locate_report_text_regions(image_path, lines=displayed_lines)
    except OcrError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    locations = [_coordinate_line_location_from_report_region(item) for item in located]
    _store_coordinate_auto_location_detail(
        extraction,
        status="completed",
        locations=locations,
    )
    db.commit()
    return _coordinate_review_response(
        db,
        attachment,
        None,
        auto_locations=locations,
    )


@app.post(
    "/api/attachments/{attachment_id}/coordinate-review",
    response_model=AttachmentCoordinateReviewResponse,
    status_code=201,
)
def create_attachment_coordinate_review(
    attachment_id: UUID,
    payload: AttachmentCoordinateReviewCreate,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    if attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=422, detail="이미지 첨부만 좌표 편집할 수 있습니다."
        )
    extraction = attachment.text_extraction
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        raise HTTPException(
            status_code=409, detail="먼저 이미지 글자 판독을 완료해 주세요."
        )

    resident_ids = {
        region.resident_id
        for region in payload.regions
        if region.resident_id is not None
    }
    residents = (
        {
            resident.id: resident
            for resident in db.scalars(
                select(Resident).where(
                    Resident.id.in_(resident_ids),
                    Resident.organization_id == editor.organization_id,
                    Resident.is_active.is_(True),
                    Resident.status == "active",
                )
            ).all()
        }
        if resident_ids
        else {}
    )
    if set(residents) != resident_ids:
        raise HTTPException(
            status_code=422,
            detail="현재 이용 중인 어르신만 이름 영역에 연결할 수 있습니다.",
        )
    service_context = extraction.suggested_service_context
    for region in payload.regions:
        if region.resident_id is None:
            continue
        resident = residents[region.resident_id]
        if service_context and resident.service_type != service_context:
            raise HTTPException(
                status_code=422,
                detail="해당 판독의 서비스 명단에 속한 어르신만 연결할 수 있습니다.",
            )
        candidate_label = region.corrected_text.removesuffix(" (?)").strip()
        if candidate_label != resident.display_name:
            raise HTTPException(
                status_code=422,
                detail="이름 영역의 표시값과 선택한 어르신이 일치하지 않습니다.",
            )

    next_version = (
        db.scalar(
            select(func.max(AttachmentCoordinateReview.version_number)).where(
                AttachmentCoordinateReview.attachment_id == attachment.id
            )
        )
        or 0
    ) + 1
    review = AttachmentCoordinateReview(
        organization_id=editor.organization_id,
        attachment_id=attachment.id,
        extraction_id=extraction.id,
        version_number=next_version,
        image_width=payload.image_width,
        image_height=payload.image_height,
        editor_version=payload.editor_version,
        document_template=(
            payload.document_template
            or coordinate_template_key(extraction.suggestion_details)
        ),
        regions=[region.model_dump(mode="json") for region in payload.regions],
        editor_id=editor.id,
    )
    db.add(review)
    db.flush()
    if payload.confirm_text:
        confirmed_text = "\n".join(
            text
            for region in payload.regions
            if (text := region.corrected_text.strip())
        )
        if not confirmed_text:
            raise HTTPException(
                status_code=422,
                detail="확정할 판독문 내용을 한 글자 이상 입력해 주세요.",
            )
        if len(confirmed_text) > 12000:
            raise HTTPException(
                status_code=422,
                detail="확정 판독문은 12,000자 이하로 저장해 주세요.",
            )
        previous_coordinate_confirmation = db.scalar(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.extraction_id == extraction.id,
                OcrCorrectionEvent.coordinate_review_id.is_not(None),
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(
                OcrCorrectionEvent.created_at.desc(),
                OcrCorrectionEvent.id.desc(),
            )
            .limit(1)
        )
        previous_text = (
            previous_coordinate_confirmation.corrected_text
            if previous_coordinate_confirmation is not None
            and previous_coordinate_confirmation.corrected_text is not None
            else (
                extraction.reviewed_text
                or extraction.suggested_text
                or extraction.extracted_text
                or extraction.original_extracted_text
                or ""
            )
        )
        resident_names = [resident.display_name for resident in residents.values()]
        if not resident_names:
            resident_names = [
                resident.display_name
                for resident in db.scalars(
                    select(Resident).where(
                        Resident.organization_id == editor.organization_id,
                        Resident.is_active.is_(True),
                        Resident.status == "active",
                    )
                ).all()
            ]
        correction_pairs = build_correction_pairs(
            previous_text,
            confirmed_text,
            resident_names=resident_names,
        )
        correction_pairs = enrich_status_correction_pairs(
            correction_pairs,
            regions=payload.regions,
            document_template=review.document_template,
        )
        content_types = {
            str(pair.get("content_type", "general")) for pair in correction_pairs
        }
        event_content_type = (
            next(iter(content_types))
            if len(content_types) == 1
            else ("mixed" if content_types else "general")
        )
        text_confirmation = OcrCorrectionEvent(
            organization_id=editor.organization_id,
            extraction_id=extraction.id,
            attachment_id=attachment.id,
            coordinate_review_id=review.id,
            source_message_id=attachment.message_id,
            source_writer_id=attachment.message.sender_id,
            reviewed_by_id=editor.id,
            decision="direct_edit",
            raw_text=previous_text,
            corrected_text=confirmed_text,
            correction_pairs=correction_pairs,
            content_type=event_content_type,
            context_text=(
                extraction.original_extracted_text or extraction.extracted_text or ""
            )[:4000],
            provider="coordinate_editor",
            model_name=payload.editor_version,
            visual_signature=extraction.visual_signature,
            confirmed=True,
        )
        db.add(text_confirmation)
        # A coordinate confirmation is an authoritative staff reading.  It must
        # participate in the same resident-link finalization as the ordinary
        # text-review path, otherwise a saved name can remain a candidate.
        db.flush()
        _sync_message_resident_candidates(
            db,
            message=attachment.message,
            text=confirmed_text,
            source="ocr_exact",
            extraction=extraction,
            staff_reviewed=True,
        )
        _finalize_message_resident_links_from_reviewed_extractions(
            db,
            message=attachment.message,
            reviewer=editor,
        )
        record_audit(
            db,
            actor_id=editor.id,
            action="attachment_coordinate_review.text_confirmed",
            target_type="attachment",
            target_id=attachment.id,
            details={
                "coordinate_review_id": str(review.id),
                "version_number": next_version,
                "text_length": len(confirmed_text),
                "correction_pair_count": len(correction_pairs),
                "editor_version": payload.editor_version,
            },
        )
    record_audit(
        db,
        actor_id=editor.id,
        action="attachment_coordinate_review.created",
        target_type="attachment",
        target_id=attachment.id,
        details={
            "version_number": next_version,
            "region_count": len(payload.regions),
            "changed_region_count": sum(
                region.raw_text != region.corrected_text for region in payload.regions
            ),
            "confirmed_position_count": sum(
                region.placement_status == "confirmed" for region in payload.regions
            ),
            "resident_link_count": len(resident_ids),
            "editor_version": payload.editor_version,
        },
    )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="다른 사용자가 먼저 저장했습니다. 최신 좌표를 다시 열어 주세요.",
        ) from exc
    db.refresh(review)
    return _coordinate_review_response(db, attachment, review)


@app.post(
    "/api/messages/{message_id}/image-text-extractions",
    response_model=list[AttachmentResponse],
)
def request_message_image_text_extractions(
    message_id: UUID,
    payload: AttachmentTextExtractionRequest,
    background_tasks: BackgroundTasks,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    message = _message_for_member(db, editor, message_id)
    _require_active_message(message, "이미지 판독")
    image_attachments = [
        attachment
        for attachment in message.attachments
        if attachment.mime_type in IMAGE_MIME_TYPES
    ]
    if not image_attachments:
        raise HTTPException(
            status_code=422,
            detail="판독할 이미지가 없습니다.",
        )

    queued_ids: list[UUID] = []
    for attachment in image_attachments:
        extraction_status = (
            attachment.text_extraction.status
            if attachment.text_extraction is not None
            else None
        )
        if extraction_status in {"pending", "processing"}:
            continue
        if not payload.force and extraction_status in {"completed", "reviewed"}:
            continue
        editable_attachment = _attachment_for_text_editor(db, editor, attachment.id)
        _queue_attachment_text_extraction(
            db,
            attachment=editable_attachment,
            requested_by=editor,
        )
        queued_ids.append(editable_attachment.id)
        record_audit(
            db,
            actor_id=editor.id,
            action="attachment_text_extraction.requested",
            target_type="attachment",
            target_id=editable_attachment.id,
            details={
                "request_scope": "message_images",
                "force": payload.force,
            },
        )

    db.commit()
    for attachment in image_attachments:
        db.expire(attachment, ["text_extraction"])
    response_payload = [
        attachment_response(attachment, db=db, viewer_id=editor.id)
        for attachment in image_attachments
    ]
    if queued_ids:
        background_tasks.add_task(_run_attachment_text_extractions, queued_ids)
    return response_payload


@app.post(
    "/api/attachments/{attachment_id}/text-extraction",
    response_model=AttachmentResponse,
)
def retry_attachment_text_extraction(
    attachment_id: UUID,
    payload: AttachmentTextExtractionRequest,
    background_tasks: BackgroundTasks,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    if attachment.mime_type not in IMAGE_MIME_TYPES | AUDIO_MIME_TYPES | DOCUMENT_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail="이미지 글자 판독과 음성파일 받아쓰기만 지원합니다.",
        )
    if attachment.mime_type in AUDIO_MIME_TYPES and not settings.stt_enabled:
        raise HTTPException(
            status_code=503,
            detail="로컬 음성 판독 기능이 꺼져 있습니다.",
        )
    if attachment.text_extraction is not None and attachment.text_extraction.status in {
        "pending",
        "processing",
    }:
        raise HTTPException(
            status_code=409,
            detail="이미 판독 중입니다. 완료된 뒤 다시 시도해 주세요.",
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
        details={"force": payload.force},
    )
    db.commit()
    db.expire(attachment, ["text_extraction"])
    response_payload = attachment_response(attachment, db=db, viewer_id=editor.id)
    background_tasks.add_task(_run_attachment_text_extraction, attachment.id)
    return response_payload


@app.get(
    "/api/attachments/{attachment_id}/text-extraction",
    response_model=AttachmentResponse,
)
def get_attachment_text_extraction_review_details(
    attachment_id: UUID,
    preview: bool = False,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Load expensive OCR review context only when an editor actually needs it."""

    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    return attachment_response(
        attachment,
        db=db,
        viewer_id=editor.id,
        include_review_details=not preview,
    )


@app.get("/api/attachments/{attachment_id}/staff-review-context")
def get_staff_review_context(attachment_id: UUID, editor: User = Depends(get_current_user), db: Session = Depends(get_db)):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    # Keep the displayed selection and its revision from the same message state.
    message = db.scalar(select(Message).where(Message.id == attachment.message_id)
        .execution_options(populate_existing=True).with_for_update(read=True))
    if message is None or message.organization_id != editor.organization_id:
        raise HTTPException(404, "메시지를 찾을 수 없습니다.")
    _require_active_message(message, "첨부 내용 확인")
    db.expire(message, ["resident_links"])
    from .document_review_flow import current_revision
    from .models import AttachmentTextRevision
    from .photo_reading import resident_review_metadata
    extraction = attachment.text_extraction
    revisions = list(db.scalars(select(AttachmentTextRevision).where(
        AttachmentTextRevision.attachment_id == attachment.id).order_by(AttachmentTextRevision.revision.desc()).limit(30)))
    return {
        "attachment": attachment_response(attachment, db=db, viewer_id=editor.id),
        "revision": current_revision(db, extraction) if extraction is not None else 0,
        "resident_revision": int(resident_review_metadata(message).get("revision", 0) or 0),
        "selected_resident_ids": [str(link.resident_id) for link in message.resident_links if link.status == "confirmed" and resident_link_is_current(link, message)],
        "choices": [{"id":str(resident.id), "display_name":resident.display_name,
            "room_name":resident.room.name if resident.room else None, "floor_name":resident.floor.name if resident.floor else None,
            "internal_code":resident.internal_code} for resident in _active_message_residents(db, message)],
        "revisions": [{"revision":row.revision,"kind":row.kind,"text":row.text_content,
            "text_sha256":row.text_sha256,"reviewed_by_id":str(row.reviewed_by_id) if row.reviewed_by_id else None,
            "created_at":row.created_at} for row in revisions],
    }


@app.get(
    "/api/attachments/{image_attachment_id}/voice-correction-comparison",
    response_model=HandwritingVoiceCorrectionResponse,
)
def get_handwriting_voice_correction_comparison(
    image_attachment_id: UUID,
    audio_attachment_id: UUID,
    mode: Literal["full_reading", "partial_correction", "story_hint"] = "full_reading",
    refine: bool = False,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Compare existing internal OCR/STT evidence without persisting a final text."""

    image_attachment = _attachment_for_text_editor(
        db, editor, image_attachment_id
    )
    audio_attachment = _attachment_for_text_editor(
        db, editor, audio_attachment_id
    )
    if image_attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="손글씨 이미지 파일을 선택해 주세요.")
    if audio_attachment.mime_type not in AUDIO_MIME_TYPES:
        raise HTTPException(status_code=422, detail="전체를 읽어 준 음성파일을 선택해 주세요.")
    if image_attachment.message_id != audio_attachment.message_id:
        raise HTTPException(
            status_code=422,
            detail="같은 대화에 첨부된 이미지와 음성만 비교할 수 있습니다.",
        )
    image_extraction = image_attachment.text_extraction
    audio_extraction = audio_attachment.text_extraction
    for extraction, pending_message in (
        (image_extraction, "이미지 글자 판독이 끝난 뒤 비교할 수 있습니다."),
        (audio_extraction, "음성 받아쓰기가 끝난 뒤 비교할 수 있습니다."),
    ):
        if extraction is None or extraction.status in {"pending", "processing"}:
            raise HTTPException(status_code=409, detail=pending_message)
        if extraction.status == "failed":
            raise HTTPException(
                status_code=409,
                detail=extraction.error_message or pending_message,
            )
    initial_ocr = (
        image_extraction.original_extracted_text
        or image_extraction.extracted_text
        or ""
    )
    transcript = (
        audio_extraction.original_extracted_text
        or audio_extraction.extracted_text
        or ""
    )
    try:
        comparison = build_handwriting_voice_comparison(
            initial_ocr=initial_ocr,
            transcript=transcript,
            image_evidence_ref=f"attachment:{image_attachment.id}",
            audio_evidence_ref=f"attachment:{audio_attachment.id}",
            image_provider=image_extraction.provider,
            image_model=image_extraction.model_name,
            audio_provider=audio_extraction.provider,
            audio_model=audio_extraction.model_name,
            audio_quality=_audio_quality_from_details(
                audio_extraction.suggestion_details
            ),
            mode=mode,
            ai_refiner=(
                refine_handwriting_with_internal_ai
                if refine and mode == "full_reading"
                else None
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return HandwritingVoiceCorrectionResponse.model_validate(comparison)


@app.post(
    "/api/attachments/{image_attachment_id}/voice-correction-recordings",
    response_model=AttachmentResponse,
    status_code=201,
)
async def create_handwriting_voice_correction_recording(
    image_attachment_id: UUID,
    background_tasks: BackgroundTasks,
    mode: Annotated[
        Literal["full_reading", "partial_correction", "story_hint"], Form()
    ],
    file: Annotated[UploadFile, File()],
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Store one browser recording beside its image and transcribe it internally."""

    image_attachment = _attachment_for_text_editor(
        db, editor, image_attachment_id
    )
    if image_attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="손글씨 이미지 파일을 선택해 주세요.")
    if not settings.stt_enabled:
        raise HTTPException(
            status_code=503,
            detail="내부 음성 받아쓰기 기능이 꺼져 있습니다.",
        )
    if (file.content_type or "").lower() not in AUDIO_MIME_TYPES:
        raise HTTPException(
            status_code=422,
            detail="브라우저에서 녹음한 음성파일만 사용할 수 있습니다.",
        )

    max_ordinal = db.scalar(
        select(func.max(MessageAttachment.upload_ordinal)).where(
            MessageAttachment.message_id == image_attachment.message_id
        )
    )
    stored_path: Path | None = None
    try:
        recording, stored_path = await _store_attachment(
            db,
            upload=file,
            upload_ordinal=int(max_ordinal if max_ordinal is not None else -1) + 1,
            message=image_attachment.message,
            user=editor,
        )
        _queue_attachment_text_extraction(
            db,
            attachment=recording,
            requested_by=editor,
        )
        record_audit(
            db,
            actor_id=editor.id,
            action="handwriting_correction.recording_requested",
            target_type="attachment",
            target_id=recording.id,
            details={
                "image_attachment_id": str(image_attachment.id),
                "mode": mode,
                "processing_location": "internal_stt",
                "external_transfer": False,
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        if stored_path is not None:
            stored_path.unlink(missing_ok=True)
        raise

    db.refresh(recording)
    db.expire(recording, ["text_extraction"])
    response_payload = attachment_response(recording, db=db, viewer_id=editor.id)
    background_tasks.add_task(_run_attachment_text_extraction, recording.id)
    return response_payload


@app.get("/api/attachments/{image_attachment_id}/voice-correction-readiness")
def get_handwriting_voice_correction_readiness(
    image_attachment_id: UUID,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Authorize first, then make a short metadata-only STT health probe."""

    image_attachment = _attachment_for_text_editor(db, editor, image_attachment_id)
    if image_attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="손글씨 이미지 파일을 선택해 주세요.")
    return stt_readiness(timeout_seconds=2.0)


def _handwriting_approval_response(
    approval: HandwritingCorrectionApproval,
) -> HandwritingCorrectionApprovalResponse:
    return HandwritingCorrectionApprovalResponse(
        id=approval.id,
        approval_group_id=approval.approval_group_id,
        revision=approval.revision,
        supersedes_approval_id=approval.supersedes_approval_id,
        image_attachment_id=approval.image_attachment_id,
        audio_attachment_id=approval.audio_attachment_id,
        mode=approval.mode,
        status="approved",
        initial_ocr=approval.initial_ocr,
        audio_or_explanation_text=approval.audio_or_explanation_text,
        ai_suggestion=approval.ai_suggestion,
        approved_final_text=approval.approved_final_text,
        sentence_decisions=list(approval.sentence_decisions or []),
        evidence_refs=list(approval.evidence_refs or []),
        confidence_state=dict(approval.confidence_state or {}),
        conflicts_confirmed=approval.conflicts_confirmed,
        approved_by_id=approval.approved_by_id,
        approved_by_name=approval.approved_by.full_name,
        approved_at=approval.approved_at,
        is_test_data=approval.is_test_data,
        official_record_saved=False,
        training_data_adopted=False,
        external_transfer_allowed=False,
    )


def _handwriting_approval_history(
    db: Session,
    *,
    editor: User,
    image_attachment_id: UUID,
    audio_attachment_id: UUID | None,
    mode: str,
) -> HandwritingCorrectionApprovalHistoryResponse:
    conditions = [
        HandwritingCorrectionApproval.organization_id == editor.organization_id,
        HandwritingCorrectionApproval.image_attachment_id == image_attachment_id,
        HandwritingCorrectionApproval.mode == mode,
    ]
    if audio_attachment_id is None:
        conditions.append(HandwritingCorrectionApproval.audio_attachment_id.is_(None))
    else:
        conditions.append(
            HandwritingCorrectionApproval.audio_attachment_id == audio_attachment_id
        )
    versions = list(
        db.scalars(
            select(HandwritingCorrectionApproval)
            .where(*conditions)
            .order_by(
                HandwritingCorrectionApproval.approval_group_id,
                HandwritingCorrectionApproval.revision,
            )
        )
    )
    image_attachment = db.get(MessageAttachment, image_attachment_id)
    link_revision = (
        int(resident_review_metadata(image_attachment.message).get("revision", 0) or 0)
        if image_attachment is not None
        else 0
    )
    return HandwritingCorrectionApprovalHistoryResponse(
        status="approved" if versions else "reviewing",
        versions=[_handwriting_approval_response(item) for item in versions],
        current_revision=max((item.revision for item in versions), default=0),
        resident_link_revision=link_revision,
        safety={
            "partial_state_persisted": False,
            "official_record_written": False,
            "training_candidate_created": False,
            "external_transfer": False,
            "append_only_versions": True,
        },
    )


@app.get(
    "/api/attachments/{image_attachment_id}/voice-correction-approvals",
    response_model=HandwritingCorrectionApprovalHistoryResponse,
)
def get_handwriting_correction_approvals(
    image_attachment_id: UUID,
    mode: Literal[
        "direct_typing",
        "full_reading",
        "partial_correction",
        "story_hint",
    ],
    audio_attachment_id: UUID | None = None,
    editor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    _attachment_for_text_editor(db, editor, image_attachment_id)
    if audio_attachment_id is not None:
        _attachment_for_text_editor(db, editor, audio_attachment_id)
    return _handwriting_approval_history(
        db,
        editor=editor,
        image_attachment_id=image_attachment_id,
        audio_attachment_id=audio_attachment_id,
        mode=mode,
    )


@app.post(
    "/api/attachments/{image_attachment_id}/voice-correction-approvals",
    response_model=HandwritingCorrectionApprovalHistoryResponse,
    status_code=201,
)
def create_handwriting_correction_approval(
    image_attachment_id: UUID,
    payload: HandwritingCorrectionApprovalCreate,
    background_tasks: BackgroundTasks,
    editor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    """Persist one fully approved correction without touching official records."""

    image_attachment = _attachment_for_text_editor(db, editor, image_attachment_id)
    if image_attachment.mime_type not in IMAGE_MIME_TYPES:
        raise HTTPException(status_code=422, detail="손글씨 이미지 파일을 선택해 주세요.")
    image_extraction = image_attachment.text_extraction
    if image_extraction is None or image_extraction.status not in {
        "completed",
        "reviewed",
    }:
        raise HTTPException(
            status_code=409,
            detail="이미지 글자 판독이 끝난 뒤 승인할 수 있습니다.",
        )
    initial_ocr = (
        image_extraction.original_extracted_text
        or image_extraction.extracted_text
        or ""
    ).strip()
    if not initial_ocr:
        raise HTTPException(status_code=409, detail="승인할 최초 OCR 결과가 없습니다.")

    existing_idempotent = db.scalar(
        select(HandwritingCorrectionApproval).where(
            HandwritingCorrectionApproval.organization_id == editor.organization_id,
            HandwritingCorrectionApproval.idempotency_key
            == str(payload.idempotency_key),
        )
    )
    if existing_idempotent is not None:
        if existing_idempotent.approved_by_id != editor.id:
            raise HTTPException(status_code=409, detail="이미 사용된 승인 요청입니다.")
        return _handwriting_approval_history(
            db,
            editor=editor,
            image_attachment_id=existing_idempotent.image_attachment_id,
            audio_attachment_id=existing_idempotent.audio_attachment_id,
            mode=existing_idempotent.mode,
        )

    audio_attachment: MessageAttachment | None = None
    audio_text: str | None = None
    ai_suggestion: str | None = None
    confidence_state: dict[str, Any]
    evidence_refs = [f"attachment:{image_attachment.id}"]
    if payload.mode == "direct_typing":
        proposed_sentences = correction_sentences(initial_ocr)
        source_sentences = list(proposed_sentences)
        confidence_state = {
            "requires_confirmation": False,
            "critical_review_count": 0,
            "blocked_field_count": 0,
            "source": "staff_direct_typing",
        }
    else:
        assert payload.audio_attachment_id is not None
        audio_attachment = _attachment_for_text_editor(
            db, editor, payload.audio_attachment_id
        )
        if audio_attachment.mime_type not in AUDIO_MIME_TYPES:
            raise HTTPException(
                status_code=422,
                detail="음성 또는 설명 파일을 선택해 주세요.",
            )
        if image_attachment.message_id != audio_attachment.message_id:
            raise HTTPException(
                status_code=422,
                detail="같은 대화에 첨부된 이미지와 음성만 승인할 수 있습니다.",
            )
        audio_extraction = audio_attachment.text_extraction
        if audio_extraction is None or audio_extraction.status not in {
            "completed",
            "reviewed",
        }:
            raise HTTPException(
                status_code=409,
                detail="음성 받아쓰기가 끝난 뒤 승인할 수 있습니다.",
            )
        audio_text = (
            audio_extraction.original_extracted_text
            or audio_extraction.extracted_text
            or ""
        ).strip()
        try:
            comparison = build_handwriting_voice_comparison(
                initial_ocr=initial_ocr,
                transcript=audio_text,
                image_evidence_ref=f"attachment:{image_attachment.id}",
                audio_evidence_ref=f"attachment:{audio_attachment.id}",
                image_provider=image_extraction.provider,
                image_model=image_extraction.model_name,
                audio_provider=audio_extraction.provider,
                audio_model=audio_extraction.model_name,
                audio_quality=_audio_quality_from_details(
                    audio_extraction.suggestion_details
                ),
                mode=payload.mode,
            )
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        ai_suggestion = next(
            item["text"]
            for item in comparison["stages"]
            if item["stage"] == "ai_correction_suggestion"
        )
        proposed_sentences = correction_sentences(ai_suggestion or "")
        source_sentences = correction_sentences(initial_ocr)
        requires_confirmation = any(
            item["requires_staff_confirmation"]
            for item in comparison["critical_facts"]
        ) or any(
            item["status"] in {"needs_confirmation", "blocked"}
            for item in comparison["changed_fields"]
        )
        confidence_state = {
            "requires_confirmation": requires_confirmation,
            "critical_review_count": comparison["summary"]["critical_review_count"],
            "blocked_field_count": comparison["summary"]["blocked_field_count"],
            "source": "internal_evidence_comparison",
        }
        evidence_refs.append(f"attachment:{audio_attachment.id}")

    whole_document_proposal = (ai_suggestion or initial_ocr).strip()
    whole_document_approval = bool(
        len(payload.sentences) == 1
        and payload.sentences[0].sentence_no == 1
        and payload.sentences[0].proposed_text.strip()
    )
    if whole_document_approval:
        whole_document_proposal = payload.sentences[0].proposed_text.strip()
        ai_suggestion = whole_document_proposal
    if not whole_document_approval and len(payload.sentences) != len(proposed_sentences):
        raise HTTPException(
            status_code=409,
            detail="모든 교정 문장을 승인해야 최종 교정자료로 저장할 수 있습니다.",
        )
    if confidence_state["requires_confirmation"] and not payload.conflicts_confirmed:
        raise HTTPException(
            status_code=409,
            detail="충돌·미확인 항목을 원본과 대조해 직원이 직접 확인해 주세요.",
        )

    requested_coordinate_ids = {
        region_id
        for decision in payload.sentences
        for region_id in decision.coordinate_region_ids
    }
    coordinate_review: AttachmentCoordinateReview | None = None
    confirmed_coordinate_regions: dict[str, CoordinateRegion] = {}
    if requested_coordinate_ids:
        coordinate_review = db.scalar(
            select(AttachmentCoordinateReview)
            .where(
                AttachmentCoordinateReview.attachment_id == image_attachment.id,
                AttachmentCoordinateReview.organization_id == editor.organization_id,
            )
            .order_by(
                AttachmentCoordinateReview.version_number.desc(),
                AttachmentCoordinateReview.created_at.desc(),
            )
            .limit(1)
        )
        if coordinate_review is None:
            raise HTTPException(
                status_code=409,
                detail="확인된 원본 위치 정보를 찾지 못했습니다.",
            )
        for raw_region in coordinate_review.regions or []:
            region = CoordinateRegion.model_validate(raw_region)
            if region.placement_status == "confirmed":
                confirmed_coordinate_regions[region.client_id] = region
        if requested_coordinate_ids - set(confirmed_coordinate_regions):
            raise HTTPException(
                status_code=409,
                detail="확인되지 않은 위치는 교정 근거로 저장할 수 없습니다.",
            )

    sentence_decisions: list[dict[str, Any]] = []
    approval_rows = (
        [(payload.sentences[0], whole_document_proposal)]
        if whole_document_approval
        else list(zip(payload.sentences, proposed_sentences, strict=True))
    )
    for index, (decision, expected_proposal) in enumerate(approval_rows, start=1):
        if decision.proposed_text != expected_proposal:
            raise HTTPException(
                status_code=409,
                detail=f"{index}번 교정 제안이 현재 근거와 다릅니다. 다시 비교해 주세요.",
            )
        if decision.action == "accepted" and decision.final_text != expected_proposal:
            raise HTTPException(
                status_code=422,
                detail=f"{index}번 문장은 직접 수정으로 표시해 주세요.",
            )
        source_text = (
            initial_ocr
            if whole_document_approval
            else (
                source_sentences[index - 1]
                if index <= len(source_sentences)
                else "원본 대응 문장 없음"
            )
        )
        coordinate_region_ids = list(decision.coordinate_region_ids)
        coordinate_evidence = (
            [
                {
                    "coordinate_review_id": str(coordinate_review.id),
                    "region_id": region_id,
                    "bbox": confirmed_coordinate_regions[region_id].bbox.model_dump(),
                    "placement_status": "confirmed",
                    "source": confirmed_coordinate_regions[region_id].source,
                    "raw_text": confirmed_coordinate_regions[region_id].raw_text,
                }
                for region_id in coordinate_region_ids
            ]
            if coordinate_review is not None
            else []
        )
        sentence_decisions.append(
            {
                "sentence_no": index,
                "source_text": source_text,
                "proposed_text": expected_proposal,
                "final_text": decision.final_text,
                "action": (
                    "accepted"
                    if decision.final_text == expected_proposal
                    else "edited"
                ),
                "approved": True,
                "evidence_refs": list(evidence_refs),
                "coordinate_region_ids": coordinate_region_ids,
                "coordinate_evidence": coordinate_evidence,
            }
        )
    approved_final_text = (
        sentence_decisions[0]["final_text"]
        if whole_document_approval
        else "\n".join(item["final_text"] for item in sentence_decisions)
    )
    if coordinate_review is not None:
        evidence_refs.append(f"coordinate_review:{coordinate_review.id}")
        confidence_state["confirmed_coordinate_region_count"] = len(
            requested_coordinate_ids
        )
    else:
        confidence_state["confirmed_coordinate_region_count"] = 0
    confidence_state["approval_scope"] = (
        "whole_document" if whole_document_approval else "sentence_by_sentence"
    )

    supersedes: HandwritingCorrectionApproval | None = None
    if payload.supersedes_approval_id is not None:
        supersedes = db.get(
            HandwritingCorrectionApproval,
            payload.supersedes_approval_id,
        )
        if (
            supersedes is None
            or supersedes.organization_id != editor.organization_id
            or supersedes.image_attachment_id != image_attachment.id
            or supersedes.audio_attachment_id != payload.audio_attachment_id
            or supersedes.mode != payload.mode
        ):
            raise HTTPException(status_code=404, detail="이전 승인본을 찾을 수 없습니다.")
        if supersedes.approved_by_id != editor.id:
            raise HTTPException(
                status_code=403,
                detail="승인본은 처음 승인한 직원만 새 수정본으로 이어갈 수 있습니다.",
            )
        latest_revision = db.scalar(
            select(func.max(HandwritingCorrectionApproval.revision)).where(
                HandwritingCorrectionApproval.approval_group_id
                == supersedes.approval_group_id
            )
        )
        if supersedes.revision != latest_revision:
            raise HTTPException(
                status_code=409,
                detail="가장 최근 승인본에서만 새 수정본을 만들 수 있습니다.",
            )

    # The approved whole-document text becomes the one authoritative image
    # reading.  Resident links are updated in this same transaction; the audio
    # transcript remains supporting evidence and cannot create a late link.
    image_extraction.reviewed_text = approved_final_text
    image_extraction.reviewed_by_id = editor.id
    image_extraction.reviewed_at = utcnow()
    image_extraction.status = "reviewed"
    resident_link_review = _finalize_message_resident_links_from_reviewed_extractions(
        db,
        message=image_attachment.message,
        reviewer=editor,
    )
    confidence_state = {
        **confidence_state,
        "resident_link_revision": resident_link_review.get("revision", 0),
    }

    approval = HandwritingCorrectionApproval(
        organization_id=editor.organization_id,
        approval_group_id=(supersedes.approval_group_id if supersedes else uuid4()),
        revision=(supersedes.revision + 1 if supersedes else 1),
        supersedes_approval_id=(supersedes.id if supersedes else None),
        image_attachment_id=image_attachment.id,
        audio_attachment_id=(audio_attachment.id if audio_attachment else None),
        mode=payload.mode,
        status="approved",
        initial_ocr=initial_ocr,
        audio_or_explanation_text=audio_text,
        ai_suggestion=ai_suggestion,
        approved_final_text=approved_final_text,
        sentence_decisions=sentence_decisions,
        evidence_refs=evidence_refs,
        confidence_state=confidence_state,
        conflicts_confirmed=payload.conflicts_confirmed,
        approved_by_id=editor.id,
        approved_at=utcnow(),
        idempotency_key=str(payload.idempotency_key),
        is_test_data=bool(image_attachment.message.is_test_data),
        official_record_saved=False,
        training_data_adopted=False,
        external_transfer_allowed=False,
    )
    db.add(approval)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        repeated = db.scalar(
            select(HandwritingCorrectionApproval).where(
                HandwritingCorrectionApproval.organization_id
                == editor.organization_id,
                HandwritingCorrectionApproval.idempotency_key
                == str(payload.idempotency_key),
            )
        )
        if repeated is None or repeated.approved_by_id != editor.id:
            raise HTTPException(status_code=409, detail="승인본 저장이 충돌했습니다.")
        return _handwriting_approval_history(
            db,
            editor=editor,
            image_attachment_id=repeated.image_attachment_id,
            audio_attachment_id=repeated.audio_attachment_id,
            mode=repeated.mode,
        )
    record_audit(
        db,
        actor_id=editor.id,
        action="handwriting_correction.approved",
        target_type="handwriting_correction_approval",
        target_id=approval.id,
        details={
            "revision": approval.revision,
            "mode": approval.mode,
            "sentence_count": len(sentence_decisions),
            "official_record_saved": False,
            "training_data_adopted": False,
            "external_transfer_allowed": False,
        },
    )
    db.commit()
    background_tasks.add_task(
        manager.send_to_users,
        room_member_user_ids(db, image_attachment.message.room_id),
        {
            "event": "message_metadata_changed",
            "message_id": str(image_attachment.message_id),
            "room_id": str(image_attachment.message.room_id),
        },
    )
    return _handwriting_approval_history(
        db,
        editor=editor,
        image_attachment_id=image_attachment.id,
        audio_attachment_id=(audio_attachment.id if audio_attachment else None),
        mode=payload.mode,
    )


@app.patch(
    "/api/attachments/{attachment_id}/text-extraction",
    response_model=AttachmentResponse,
)
def review_attachment_text_extraction(
    attachment_id: UUID,
    payload: AttachmentTextExtractionUpdate,
    background_tasks: BackgroundTasks,
    editor: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    attachment = _attachment_for_text_editor(db, editor, attachment_id)
    extraction = attachment.text_extraction
    if requires_staff_review(attachment):
        from .document_review_flow import save_staff_review
        return save_staff_review(db, attachment, payload, editor, background_tasks)
    if extraction is None or extraction.status not in {"completed", "reviewed"}:
        raise HTTPException(
            status_code=409,
            detail="먼저 이미지 또는 음성 판독을 완료해 주세요.",
        )
    original_extracted_text = (
        extraction.original_extracted_text or extraction.extracted_text or ""
    )
    previous_confirmed_text = (
        extraction.reviewed_text
        if extraction.status == "reviewed" and extraction.reviewed_text is not None
        else original_extracted_text
    )
    previous_confirmed_event = db.scalar(
        select(OcrCorrectionEvent)
        .where(
            OcrCorrectionEvent.extraction_id == extraction.id,
            OcrCorrectionEvent.confirmed.is_(True),
            OcrCorrectionEvent.coordinate_review_id.is_(None),
        )
        .order_by(
            OcrCorrectionEvent.created_at.desc(),
            OcrCorrectionEvent.id.desc(),
        )
        .limit(1)
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
    previous_correction_pairs = (
        previous_confirmed_event.correction_pairs or []
        if previous_confirmed_event is not None
        else build_correction_pairs(
            original_extracted_text,
            previous_confirmed_text,
            resident_names=resident_names,
        )
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
    db.add(
        OcrCorrectionEvent(
            organization_id=attachment.message.organization_id,
            extraction_id=extraction.id,
            attachment_id=attachment.id,
            source_message_id=attachment.message_id,
            source_writer_id=attachment.message.sender_id,
            reviewed_by_id=editor.id,
            decision=payload.decision,
            raw_text=previous_confirmed_text,
            corrected_text=confirmed_text,
            correction_pairs=correction_pairs if confirmed else [],
            content_type=event_content_type,
            context_text=original_extracted_text[:4000],
            provider=extraction.provider,
            model_name=extraction.model_name,
            visual_signature=(
                extraction.visual_signature if is_image_extraction else None
            ),
            selected_candidate_id=payload.selected_candidate_id,
            confirmed=confirmed,
        )
    )
    previous_correction_keys = {
        (str(pair["recognized_text"]), str(pair["corrected_text"]))
        for pair in previous_correction_pairs
    }
    current_correction_keys = {
        (str(pair["recognized_text"]), str(pair["corrected_text"]))
        for pair in correction_pairs
    }
    learned_corrections = (
        current_correction_keys - previous_correction_keys if confirmed else set()
    )
    retired_corrections = (
        previous_correction_keys - current_correction_keys if confirmed else set()
    )
    resident_link_review = {
        "finalized": False,
        "confirmed_count": 0,
        "rejected_count": 0,
    }
    if confirmed:
        extraction.reviewed_text = confirmed_text
        extraction.reviewed_by_id = editor.id
        extraction.reviewed_at = utcnow()
        extraction.status = "reviewed"
        if is_image_extraction:
            resident_link_review = _finalize_message_resident_links_from_reviewed_extractions(
                db,
                message=attachment.message,
                reviewer=editor,
            )
        else:
            # A reviewed transcript is still supporting evidence.  It remains a
            # candidate until the employee includes it in the image final text.
            _sync_message_resident_candidates(
                db,
                message=attachment.message,
                text=confirmed_text or original_extracted_text,
                source="audio_transcript",
                extraction=extraction,
                staff_reviewed=True,
            )
    for recognized_text, corrected_text in retired_corrections:
        memory = db.scalar(
            select(OcrCorrectionMemory).where(
                OcrCorrectionMemory.organization_id
                == attachment.message.organization_id,
                OcrCorrectionMemory.recognized_text == recognized_text,
                OcrCorrectionMemory.corrected_text == corrected_text,
            )
        )
        if memory is None:
            continue
        if memory.occurrence_count <= 1:
            db.delete(memory)
        else:
            memory.occurrence_count -= 1
            memory.last_reviewed_by_id = editor.id
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
            "retired_correction_count": len(retired_corrections),
            "resident_links_finalized": resident_link_review["finalized"],
            "resident_links_confirmed_count": resident_link_review["confirmed_count"],
            "resident_links_rejected_count": resident_link_review["rejected_count"],
            "previous_text_sha256": sha256(
                previous_confirmed_text.encode("utf-8")
            ).hexdigest(),
            "confirmed_text_sha256": (
                sha256(confirmed_text.encode("utf-8")).hexdigest()
                if confirmed_text is not None
                else None
            ),
            "protected_candidate_count": sum(
                1
                for pair in correction_pairs
                if pair.get("content_type") in PROTECTED_CONTENT_TYPES
            ),
        },
    )
    db.commit()
    db.refresh(attachment)
    if confirmed:
        background_tasks.add_task(
            manager.send_to_users,
            room_member_user_ids(db, attachment.message.room_id),
            {
                "event": "message_metadata_changed",
                "message_id": str(attachment.message_id),
                "room_id": str(attachment.message.room_id),
            },
        )
    return attachment_response(attachment, db=db, viewer_id=editor.id)


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
        .join(Message, Message.id == ActionItem.source_message_id)
        .where(
            ActionItem.organization_id == user.organization_id,
            Message.lifecycle_status == "active",
        )
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
    _require_active_message(item.source_message, "업무 상태 변경")
    now = utcnow()
    previous_status = item.status
    item.status = payload.status
    if (
        payload.status in {"acknowledged", "in_progress"}
        and item.acknowledged_at is None
    ):
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
        raise HTTPException(
            status_code=422, detail="요약 기간은 day, week, month 중 하나여야 합니다."
        )
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
            RoomDigestPoint.model_validate(point) for point in digest.major_points
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
        room_query = room_query.join(
            RoomMembership, RoomMembership.room_id == Room.id
        ).where(
            RoomMembership.staff_id == processor.staff_id,
            RoomMembership.left_at.is_(None),
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(_reviewer_room_scope_filter(processor))
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
                Message.lifecycle_status == "active",
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
                resident_names_by_message[message.id] = [message.resident.display_name]
        for item in work_items:
            suggestions_changed = (
                _refresh_work_item_suggestion(db, item) or suggestions_changed
            )
        document_counts: dict[str, int] = {}
        risk_counts: dict[str, int] = {}
        for item in work_items:
            payload = item.confirmed_payload or item.ai_payload or {}
            for document_type in payload.get("document_types", []):
                document_counts[document_type] = (
                    document_counts.get(document_type, 0) + 1
                )
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
                    ", ".join(resident_names_by_message.get(message.id, [])) or None
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
                "관련 어르신 "
                + ", ".join(resident_names[:6])
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


def _period_date_chunks(
    start_date: date,
    end_date: date,
    *,
    max_days: int = 31,
) -> list[tuple[date, date]]:
    if end_date < start_date or max_days < 1:
        return []
    chunks: list[tuple[date, date]] = []
    chunk_start = start_date
    while chunk_start <= end_date:
        chunk_end = min(end_date, chunk_start + timedelta(days=max_days - 1))
        chunks.append((chunk_start, chunk_end))
        chunk_start = chunk_end + timedelta(days=1)
    return chunks


def _period_label(start_date: date, end_date: date) -> str:
    if start_date == end_date:
        return start_date.isoformat()
    return f"{start_date.isoformat()} ~ {end_date.isoformat()}"


def _period_message_text(
    message: Message,
    comments: list[MessageComment],
) -> str:
    # RecordDraft.corrected_text is intentionally bounded to keep a single
    # message from turning the briefing into a full attachment dump. Long PDF
    # OCR is still preserved on the attachment; the period review only needs a
    # compact excerpt plus the message/reply context.
    max_text_length = 1_900
    core_text_limit = 1_150
    attachment_text_budget = 700

    core_sections = [message.body.strip()]
    core_sections.extend(
        f"[답글 · {comment.author.full_name}] {comment.body.strip()}"
        for comment in comments
        if comment.body.strip()
    )
    core_text = "\n".join(section for section in core_sections if section).strip()
    if len(core_text) > core_text_limit:
        core_text = core_text[: core_text_limit - 3].rstrip() + "..."

    attachment_sources: list[tuple[str, str]] = []
    for attachment in message.attachments:
        extraction = attachment.text_extraction
        if extraction is None or extraction.status not in {"completed", "reviewed"}:
            continue
        extracted_text = attachment_evidence_text(attachment)
        if not extracted_text:
            continue
        label = (
            "음성 받아쓰기"
            if attachment.mime_type in AUDIO_MIME_TYPES
            else "이미지 글자 판독"
        )
        safe_name = attachment.original_name.strip()
        if len(safe_name) > 120:
            safe_name = safe_name[:117].rstrip() + "..."
        attachment_sources.append(
            (f"[{label} · {safe_name}]", extracted_text.strip())
        )

    attachment_sections: list[str] = []
    if attachment_sources:
        header_length = sum(len(header) + 1 for header, _ in attachment_sources)
        excerpt_budget = max(0, attachment_text_budget - header_length)
        per_attachment = excerpt_budget // len(attachment_sources)
        for header, extracted_text in attachment_sources:
            excerpt = extracted_text[:per_attachment].rstrip()
            if excerpt and len(extracted_text) > per_attachment:
                excerpt = excerpt[:-3].rstrip() + "..." if len(excerpt) > 3 else "..."
            attachment_sections.append(
                f"{header}\n{excerpt}" if excerpt else header
            )

    text = "\n".join(
        section for section in [core_text, *attachment_sections] if section
    ).strip()
    if len(text) > max_text_length:
        text = text[: max_text_length - 3].rstrip() + "..."
    return text


BRIEFING_COMPARISON_DAYS = 7
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
    "확인했",
    "확인됐",
    "확인되었",
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
    "불편감이 없",
    "회복했",
    "회복됐",
    "회복되었",
    "정상",
    "완료함",
    "완료했",
    "완료됐",
    "완료되었",
    "마치고",
    "귀원",
)
BRIEFING_FUTURE_OR_PENDING_TERMS = (
    "예정",
    "추후",
    "계획",
    "필요",
    "해야",
    "요청",
    "관찰 중",
    "관찰중",
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
BRIEFING_TODAY_SCHEDULE_TERMS = (
    "예정",
    "예약",
    "방문",
    "진료",
    "검사",
    "외출",
    "귀가",
    "면담",
    "회의",
    "통화",
    "복약",
    "투약",
    "재측정",
    "확인하기",
    "확인 예정",
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
            for numerator, denominator in re.findall(
                r"(?<!\d)([0-9])\s*/\s*([0-9])(?!\d)", sentence
            ):
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
            if any(term in sentence for term in signal_terms):
                signal_seen = True
            if signal_seen and any(
                term in sentence for term in BRIEFING_RESOLVED_TERMS
            ):
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
        and any(
            term in joined
            for term in ("도포", "소독", "체위", "전달", "관찰 중", "관찰중")
        )
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
        and any(
            term in joined
            for term in ("확인하겠습니다", "확인 예정", "이어 확인", "추후 확인")
        )
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
    *,
    allow_later_resolution: bool = False,
) -> list[str]:
    pending = _briefing_specific_followup(texts)
    ordered_sentences = [
        sentence for text in texts for sentence in _briefing_sentences(text)
    ]
    for sentence_index, sentence in enumerate(ordered_sentences):
        if not any(term in sentence for term in BRIEFING_PENDING_TERMS):
            continue
        if any(
            term in sentence for term in BRIEFING_RESOLVED_TERMS
        ) and not any(
            term in sentence for term in BRIEFING_FUTURE_OR_PENDING_TERMS
        ):
            continue
        if allow_later_resolution and "완료" in sentence:
            # 같은 검증 사건의 완료 문장에 과거 '요청'이라는 단어가
            # 남아 있어도 새 미완료 업무로 되살리지 않는다.
            continue
        sentence_clocks = _record_event_clock_tokens(sentence)
        if sentence_clocks and any(
            sentence_clocks & _record_event_clock_tokens(later_sentence)
            and any(
                term in later_sentence for term in BRIEFING_RESOLVED_TERMS
            )
            for later_sentence in ordered_sentences[sentence_index + 1 :]
        ):
            continue
        if allow_later_resolution and any(
            any(term in later_sentence for term in BRIEFING_RESOLVED_TERMS)
            for later_sentence in ordered_sentences[sentence_index + 1 :]
        ):
            # 안정된 검증 사건 식별자로 같은 사건임이 확인된 경우에만
            # 며칠 뒤 완료 기록이 앞선 예정 문장을 종료한다.
            continue
        if any(
            term in sentence
            for term in (
                "요청했습니다",
                "요청드렸습니다",
                "요청함",
                "요청하였습니다",
            )
        ) and not any(
            term in sentence
            for term in (
                "해 달라고",
                "해달라고",
                "달라고 요청",
            )
        ):
            # 이미 전달·요청한 사실은 완료 조치에 남긴다. 피부·안전 등
            # 후속 확인은 위의 구체적인 안내문으로 한 번만 제시한다.
            continue
        if any(term in sentence for term in ("불명확", "판독")) and any(
            "원본" in followup for followup in pending
        ):
            continue
        if any(
            sentence in followup or followup in sentence for followup in pending
        ):
            continue
        if sentence not in pending:
            pending.append(sentence)
    if any(status not in {"completed"} for status in action_statuses):
        pending.append(
            "담당자가 지정된 업무가 아직 완료되지 않았습니다. "
            "담당자가 확인 결과를 원문 댓글로 남겨주세요."
        )
    return list(dict.fromkeys(pending))[:3]


def _briefing_final_status(
    texts: list[str],
    action_statuses: list[str],
    pending_checks: list[str] | None = None,
) -> tuple[str, str]:
    """근거 대화에 명시된 범위 안에서만 사건의 현재 상태를 표현한다."""
    pending = (
        list(pending_checks)
        if pending_checks is not None
        else _briefing_pending_checks(texts, action_statuses)
    )
    if pending:
        return "needs_confirmation", pending[0]

    if any(status != "completed" for status in action_statuses):
        return (
            "in_progress",
            "연결된 업무가 진행 중이며, 완료 결과는 아직 기록되지 않았습니다.",
        )

    final_state_terms = (
        "확인 완료",
        "확인했",
        "확인됐",
        "확인되었",
        "재확인 시",
        "재확인 결과",
        "재확인함",
        "확인 결과",
        "안정됨",
        "안정을 찾",
        "안정 찾",
        "편안",
        "어지럼 호소 없",
        "통증 호소 없",
        "불편감이 없",
        "회복했",
        "회복됐",
        "회복되었",
        "정상",
        "완료함",
        "완료했",
        "완료됐",
        "완료되었",
    )
    resolved_sentence = ""
    for text in texts:
        for sentence in _briefing_sentences(text):
            if any(term in sentence for term in final_state_terms):
                resolved_sentence = sentence
    if resolved_sentence:
        return "completed", resolved_sentence

    if action_statuses and all(status == "completed" for status in action_statuses):
        return "completed", "연결된 업무가 완료로 기록되었습니다."

    if _briefing_completed_actions(texts):
        return (
            "monitoring",
            "조치 내용은 기록되었지만, 이후 최종 결과는 대화에서 확인되지 않습니다.",
        )

    return (
        "monitoring",
        "현재 상태의 담당자와 완료 여부는 대화에서 확인되지 않습니다.",
    )


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
                f"이동 중 {label} 관련 원문이 있어 다음 이동 전 상태 확인이 필요합니다."
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
            return f"{label} 관련 관찰이 기록되어 같은 부위의 경과 확인이 필요합니다."
    vital_labels = (
        ("혈압", "혈압"),
        ("혈당", "혈당"),
        ("체온", "체온"),
        ("맥박", "맥박"),
        ("호흡", "호흡"),
    )
    for term, label in vital_labels:
        if term in joined:
            return f"{label} 관련 관찰이 있어 후속 확인 결과가 필요합니다."
    if any(term in joined for term in BRIEFING_MEAL_TERMS):
        return "식사·수분 섭취 변화가 기록되어 다음 섭취 결과와 비교가 필요합니다."
    if "통증" in joined:
        return "통증 호소가 기록되어 이후 반응과 상태 확인이 필요합니다."
    return "평소와 다른 상태를 나타내는 원문 표현이 있어 경과 확인이 필요합니다."


def _briefing_today_schedule(
    candidates: list[dict[str, Any]],
    *,
    current_date: date,
) -> tuple[bool, datetime | None]:
    """구조화된 기한 또는 오늘 작성된 명시적 일정만 오늘 일정으로 분류한다."""
    due_ats = sorted(
        {
            _as_utc(candidate["action_due_at"])
            for candidate in candidates
            if candidate.get("action_due_at") is not None
        }
    )
    due_at = due_ats[0] if due_ats else None
    today_due_ats = [
        value
        for value in due_ats
        if value.astimezone(timezone(timedelta(hours=9))).date() == current_date
    ]
    if today_due_ats:
        return True, today_due_ats[0]

    for candidate in candidates:
        created_date = _as_utc(candidate["created_at"]).astimezone(
            timezone(timedelta(hours=9))
        ).date()
        if created_date != current_date:
            continue
        text = candidate["text"]
        if any(term in text for term in ("오늘", "금일")) and any(
            term in text for term in BRIEFING_TODAY_SCHEDULE_TERMS
        ):
            return True, due_at
    return False, due_at


def _briefing_importance_score(
    *,
    risk_level: str,
    pending_checks: list[str],
    has_new_topic: bool,
    action_priorities: list[str],
    action_statuses: list[str],
    due_at: datetime | None,
    is_today_schedule: bool,
    is_carryover: bool,
    final_status: str,
    latest_at: datetime,
    current_date: date,
) -> int:
    """종류를 고정 순위로 두지 않고 놓쳤을 때의 손실 신호를 합산한다."""
    score = {
        "low": 0,
        "medium": 25,
        "high": 45,
        "urgent": 60,
    }.get(risk_level, 0)
    if pending_checks:
        score += 25
    if len(pending_checks) >= 2:
        score += 5
    if has_new_topic:
        score += 15
    if "urgent" in action_priorities:
        score += 20
    elif "important" in action_priorities:
        score += 10
    if is_today_schedule:
        score += 35
    if due_at is not None and any(status != "completed" for status in action_statuses):
        due_date = _as_utc(due_at).astimezone(
            timezone(timedelta(hours=9))
        ).date()
        if due_date < current_date:
            score += 45
    if is_carryover:
        score += 20
    latest_date = _as_utc(latest_at).astimezone(
        timezone(timedelta(hours=9))
    ).date()
    age_days = max((current_date - latest_date).days, 0)
    if age_days == 0:
        score += 10
    elif age_days <= 2:
        score += 5
    if final_status == "completed":
        score -= 40
    return max(score, 0)


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
CARE_DOCUMENT_CANDIDATE_LABELS: dict[CareDocumentCandidateType, str] = {
    "care_service_record": "급여제공기록지",
    "consultation_log": "상담일지",
    "cognitive_function_assessment": "인지기능검사",
    "fall_risk_assessment": "낙상위험도",
    "pressure_ulcer_risk_assessment": "욕창위험도",
    "needs_assessment": "욕구사정",
    "long_term_care_service_plan": "장기요양급여 제공계획서",
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
RECORD_CONTACT_MEANS_TERMS = (
    "통화",
    "전화",
    "연락",
    "면담",
    "상담",
)
RECORD_CONSULTATION_CONTENT_TERMS = (
    "설명",
    "안내",
    "요청",
    "문의",
    "동의",
    "확인",
    "공유",
    "전달",
    "결과",
    "응답",
    "답변",
    "상의",
    "협의",
)
RECORD_DIRECT_CONSULTATION_TERMS = (
    "설명드",
    "안내드",
    "알려드",
    "공유드",
    "전달드",
    "말씀드",
)
RECORD_CONTACT_PARTY_INVOLVEMENT_PATTERN = re.compile(
    r"(?<![가-힣A-Za-z0-9])"
    r"(?P<party>보호자|가족|아드님|따님|아들|딸|며느리|사위|배우자|손녀|손자)"
    r"(?:님)?(?:과|와|랑|이랑|에게|께|한테|로부터|이|가|은|는|의|도|을|를)"
)
RECORD_CONTACT_PARTY_COMPACT_PATTERN = re.compile(
    r"(?<![가-힣A-Za-z0-9])"
    r"(?P<party>보호자|가족|아드님|따님|아들|딸|며느리|사위|배우자|손녀|손자)"
    r"(?:님)?\s+(?:(?:오전|오후)?\s*\d{1,2}(?::\d{2}|시)?\s*)?"
    r"(?:통화|전화|연락|면담|상담)"
)
RECORD_RESIDENT_CONTACT_PATTERN = re.compile(
    r"(?:어르신|대상자|본인)(?:에게|께|한테|와|과|랑|이랑)"
    r"[^.!?\n]{0,24}(?:통화|전화|연락|면담|상담)"
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


def _record_contact_clauses(text: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", text).strip()
    return [
        clause.strip(" -")
        for clause in re.split(r"(?:\r?\n)+|(?<=[.!?])\s+", normalized)
        if clause.strip(" -")
    ]


def _record_contact_reason_excerpt(clause: str) -> str:
    compact = re.sub(r"\[[A-Z0-9_-]+:[^\]]+\]\s*", "", clause, flags=re.I)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) > 120:
        compact = f"{compact[:117].rstrip()}…"
    return compact


def _consultation_candidate_context(text: str) -> tuple[str, str | None]:
    """상담 상대·연락 행위·상담 내용/결과가 같은 문맥에 있을 때만 추천한다."""

    review_reason: str | None = None
    for clause in _record_contact_clauses(text):
        has_contact_means = any(term in clause for term in RECORD_CONTACT_MEANS_TERMS)
        has_direct_consultation = any(
            term in clause for term in RECORD_DIRECT_CONSULTATION_TERMS
        )
        if not has_contact_means and not has_direct_consultation:
            continue

        party_match = RECORD_CONTACT_PARTY_INVOLVEMENT_PATTERN.search(
            clause
        ) or RECORD_CONTACT_PARTY_COMPACT_PATTERN.search(clause)
        explicit_resident_contact = bool(RECORD_RESIDENT_CONTACT_PATTERN.search(clause))
        if party_match is not None:
            has_content_or_result = has_direct_consultation or any(
                term in clause for term in RECORD_CONSULTATION_CONTENT_TERMS
            )
            if has_content_or_result:
                return (
                    "candidate",
                    "보호자 연락·설명·확인 결과",
                )
            review_reason = (
                "보호자·가족 연락 사실은 있으나 상담 내용 또는 결과가 "
                "명확하지 않습니다."
            )
            continue

        if explicit_resident_contact:
            # 어르신에게 전화한 문맥과 별개의 가족 행사·귀가 사유를 합쳐
            # 보호자 상담으로 해석하지 않는다.
            continue
        review_reason = (
            "전화·연락 사실은 있으나 상대가 보호자·가족인지 명확하지 않습니다."
        )

    if review_reason:
        return "review", review_reason
    return "none", None


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
    consultation_status, _ = _consultation_candidate_context(normalized)
    actual_contact = consultation_status == "candidate"
    contact_needs_review = consultation_status == "review"
    actual_program = any(term in normalized for term in RECORD_PROGRAM_TERMS) and any(
        term in normalized for term in RECORD_PROGRAM_ACTION_TERMS
    )
    nursing = any(term in normalized for term in RECORD_NURSING_TERMS) or (
        has_resident and suggestion.classification in {"health", "safety"}
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
    if uncertain or contact_needs_review:
        tags.append("needs_review")
    if not tags:
        tags.append("general")
    return list(dict.fromkeys(tags))


def _record_document_candidate_types(
    record_usage_tags: list[RecordUsageTag],
) -> list[CareDocumentCandidateType]:
    """기존 사건 성격 태그를 승인된 최종 서류 후보로 호환 매핑한다."""
    candidates: list[CareDocumentCandidateType] = []
    if any(
        tag in {"nursing", "care_service", "program"}
        for tag in record_usage_tags
    ):
        candidates.append("care_service_record")
    if "consultation" in record_usage_tags:
        candidates.append("consultation_log")
    return candidates


def _record_document_candidate_metadata(
    text: str,
    record_usage_tags: list[RecordUsageTag],
    *,
    has_resident: bool,
) -> tuple[
    list[CareDocumentCandidateType],
    dict[CareDocumentCandidateType, str],
    list[CareDocumentCandidateType],
    dict[CareDocumentCandidateType, str],
]:
    candidates = _record_document_candidate_types(record_usage_tags)
    reasons: dict[CareDocumentCandidateType, str] = {}
    review_types: list[CareDocumentCandidateType] = []
    review_reasons: dict[CareDocumentCandidateType, str] = {}
    if not has_resident:
        return candidates, reasons, review_types, review_reasons
    if "care_service_record" in candidates:
        if "needs_review" in record_usage_tags and re.search(
            r"(?<!\d)\d{2,3}\s*/\s*\d{2,3}(?!\d)", text
        ):
            reasons["care_service_record"] = "확인 필요 · 혈압 수치 충돌 원문 확인"
        elif any(term in text for term in ("물", "수분")) and any(
            term in text for term in ("제공", "섭취", "권유", "재확인", "후속")
        ):
            reasons["care_service_record"] = "수분 제공 및 후속 확인 기록"
        elif "program" in record_usage_tags:
            reasons["care_service_record"] = "프로그램 참여·반응 기록"
        elif "nursing" in record_usage_tags:
            reasons["care_service_record"] = "건강·바이탈 관찰 기록"
        else:
            reasons["care_service_record"] = "돌봄 제공·관찰 기록"
    consultation_status, consultation_reason = _consultation_candidate_context(text)
    if (
        consultation_status == "candidate"
        and "consultation_log" in candidates
        and consultation_reason
    ):
        reasons["consultation_log"] = consultation_reason
    elif consultation_status == "review" and consultation_reason:
        review_types.append("consultation_log")
        review_reasons["consultation_log"] = consultation_reason
    return candidates, reasons, review_types, review_reasons


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


def _record_event_clock_tokens(text: str) -> set[str]:
    return {
        re.sub(r"\s+", "", value)
        for value in re.findall(
            r"(?<!\d)(?:[01]?\d|2[0-3]):[0-5]\d(?!\d)"
            r"|(?:오전|오후)?\s*(?:[1-9]|1[0-2])시(?:\s*[0-5]?\d분)?",
            text,
        )
    }


def _same_record_event(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left["resident_id"] != right["resident_id"]:
        return False
    left_scenario_event_id = left.get("scenario_event_id")
    right_scenario_event_id = right.get("scenario_event_id")
    if left_scenario_event_id or right_scenario_event_id:
        return bool(left_scenario_event_id) and (
            left_scenario_event_id == right_scenario_event_id
        )
    if _as_utc(left["created_at"]).astimezone(timezone(timedelta(hours=9))).date() != (
        _as_utc(right["created_at"]).astimezone(timezone(timedelta(hours=9))).date()
    ):
        return False
    if (
        abs(
            (_as_utc(left["created_at"]) - _as_utc(right["created_at"])).total_seconds()
        )
        > 7200
    ):
        return False
    if not set(left["record_usage_tags"]) & set(right["record_usage_tags"]):
        return False
    left_signals = {term for term in RECORD_EVENT_SIGNAL_TERMS if term in left["text"]}
    right_signals = {
        term for term in RECORD_EVENT_SIGNAL_TERMS if term in right["text"]
    }
    if not left_signals or left_signals != right_signals:
        return False
    if _record_event_clock_tokens(left["text"]) & _record_event_clock_tokens(
        right["text"]
    ):
        return True
    left_tokens = _record_event_tokens(left["text"])
    right_tokens = _record_event_tokens(right["text"])
    union = left_tokens | right_tokens
    return bool(union) and len(left_tokens & right_tokens) / len(union) >= 0.45


def _safe_synthetic_scenario_event_id(message: Message) -> str | None:
    metadata = message.extra_data or {}
    event_id = metadata.get("scenario_event_id")
    if not (
        message.is_test_data is True
        and metadata.get("synthetic_fixture") is True
        and metadata.get("official_record") is False
        and metadata.get("external_transfer_allowed") is False
        and metadata.get("display_notice") == "검증용 가상자료"
        and isinstance(event_id, str)
        and re.fullmatch(r"[A-Z0-9_-]{4,80}", event_id)
    ):
        return None
    return event_id


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
            candidate_latest_at = candidate.get(
                "latest_at",
                candidate["created_at"],
            )
            grouped.append(
                {
                    "resident_id": candidate["resident_id"],
                    "resident_name": candidate["resident_name"],
                    "summary": candidate["summary"],
                    "record_usage_tags": list(candidate["record_usage_tags"]),
                    "document_candidate_types": list(
                        candidate.get("document_candidate_types", [])
                    ),
                    "document_candidate_reasons": dict(
                        candidate.get("document_candidate_reasons", {})
                    ),
                    "document_candidate_review_types": list(
                        candidate.get("document_candidate_review_types", [])
                    ),
                    "document_candidate_review_reasons": dict(
                        candidate.get("document_candidate_review_reasons", {})
                    ),
                    "evidence_ids": [candidate["message_id"]],
                    "room_names": [candidate["room_name"]],
                    "sender_names": [candidate["sender_name"]],
                    "occurred_at": candidate["created_at"],
                    "latest_at": candidate_latest_at,
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
        target["summary"] = candidate["summary"]
        target["occurred_at"] = min(
            target["occurred_at"],
            candidate["created_at"],
        )
        target["document_candidate_types"] = list(
            dict.fromkeys(
                [
                    *target["document_candidate_types"],
                    *candidate.get("document_candidate_types", []),
                ]
            )
        )
        target["document_candidate_reasons"].update(
            candidate.get("document_candidate_reasons", {})
        )
        target["document_candidate_review_types"] = list(
            dict.fromkeys(
                [
                    *target["document_candidate_review_types"],
                    *candidate.get("document_candidate_review_types", []),
                ]
            )
        )
        target["document_candidate_review_reasons"].update(
            candidate.get("document_candidate_review_reasons", {})
        )
        target["latest_at"] = max(
            target["latest_at"],
            candidate.get("latest_at", candidate["created_at"]),
        )
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
                document_candidate_types=group["document_candidate_types"],
                document_candidate_reasons=group["document_candidate_reasons"],
                document_candidate_review_types=group[
                    "document_candidate_review_types"
                ],
                document_candidate_review_reasons=group[
                    "document_candidate_review_reasons"
                ],
                evidence_ids=evidence_ids,
                room_names=list(dict.fromkeys(group["room_names"])),
                sender_names=list(dict.fromkeys(group["sender_names"])),
                occurred_at=group["occurred_at"],
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
            re.escape(alias) for alias in sorted(alias_owner, key=len, reverse=True)
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


def _save_field_care_briefing_history(
    *,
    db: Session,
    processor: User,
    payload: PeriodWorkdeskRequest,
    response_payload: PeriodWorkdeskResponse,
    is_test_data: bool,
) -> None:
    """브리핑 이력을 공식 기록과 분리해 추가형으로만 저장한다."""

    filters = {
        "start_date": payload.start_date.isoformat(),
        "end_date": payload.end_date.isoformat(),
        "room_id": str(payload.room_id) if payload.room_id else None,
        "resident_id": str(payload.resident_id) if payload.resident_id else None,
        "keyword": payload.keyword,
        "message_type": payload.message_type,
    }
    scope_key = sha256(
        json.dumps(filters, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    next_revision = int(
        db.scalar(
            select(func.max(FieldCareBriefingHistory.revision)).where(
                FieldCareBriefingHistory.organization_id
                == processor.organization_id,
                FieldCareBriefingHistory.scope_key == scope_key,
            )
        )
        or 0
    ) + 1
    evidence_refs = list(
        dict.fromkeys(
            str(evidence_id)
            for day in response_payload.daily_care_references
            for resident_group in day.residents
            for entry in resident_group.entries
            for evidence_id in entry.evidence_ids
        )
    )
    history = FieldCareBriefingHistory(
        organization_id=processor.organization_id,
        created_by_id=processor.id,
        scope_key=scope_key,
        revision=next_revision,
        period_start=payload.start_date,
        period_end=payload.end_date,
        room_id=payload.room_id,
        resident_id=payload.resident_id,
        filters=filters,
        overall_summary=response_payload.overall_summary,
        daily_care_references=[
            item.model_dump(mode="json")
            for item in response_payload.daily_care_references
        ],
        consultation_references=[
            item.model_dump(mode="json")
            for item in response_payload.consultation_references
        ],
        evidence_refs=evidence_refs,
        response_payload={},
        care_reference_count=response_payload.care_reference_count,
        consultation_reference_count=response_payload.consultation_reference_count,
        generator=response_payload.generator,
        is_test_data=is_test_data,
        official_record_saved=False,
        created_at=response_payload.generated_at or utcnow(),
    )
    db.add(history)
    db.flush()
    response_payload.history_id = history.id
    response_payload.history_revision = next_revision
    history.response_payload = {**response_payload.model_dump(mode="json"),
        "attachment_review_fingerprint": message_review_fingerprint(db, [source.message.id for source in response_payload.sources])}
    db.commit()


@app.post(
    "/api/workdesk/period-review",
    response_model=PeriodWorkdeskResponse,
)
def create_period_workdesk_review(
    payload: PeriodWorkdeskRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
    request: Request = None,
):
    timing = PeriodTiming(request)
    compact = payload.response_mode == "briefing" and not payload.enhance_summary and not payload.save_history
    resident_message_filter = or_(Message.resident_id == payload.resident_id, Message.resident_links.any(and_(MessageResidentLink.resident_id == payload.resident_id, MessageResidentLink.status == "confirmed"))) if compact and payload.resident_id is not None else True
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
    period_chunks = _period_date_chunks(payload.start_date, payload.end_date)
    processed_periods = [
        _period_label(chunk_start, chunk_end)
        for chunk_start, chunk_end in period_chunks
    ]
    truncated_periods: list[str] = []

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
                processed_periods=processed_periods,
            )
        room_query = room_query.join(
            RoomMembership, RoomMembership.room_id == Room.id
        ).where(
            RoomMembership.staff_id == processor.staff_id,
            RoomMembership.left_at.is_(None),
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(_reviewer_room_scope_filter(processor))
    rooms = list(db.scalars(room_query.order_by(Room.name)).unique().all())
    room_by_id = {room.id: room for room in rooms}
    if payload.room_id is not None:
        if payload.room_id not in room_by_id:
            raise HTTPException(
                status_code=403, detail="이 채팅방을 정리할 수 없습니다."
            )
        room_by_id = {payload.room_id: room_by_id[payload.room_id]}

    timing.phase("residents")
    if payload.resident_id is not None:
        resident = db.get(Resident, payload.resident_id)
        if (
            resident is None
            or resident.organization_id != processor.organization_id
            or not resident.is_active
        ):
            raise HTTPException(
                status_code=422, detail="선택한 어르신을 찾을 수 없습니다."
            )

    timing.phase("messages")
    today_date = datetime.now(kst).date()
    today_start = datetime.combine(today_date, time.min, tzinfo=kst).astimezone(
        timezone.utc
    )
    today_end = datetime.combine(
        today_date + timedelta(days=1), time.min, tzinfo=kst
    ).astimezone(timezone.utc)
    selected_message_ids: set[UUID] = set()
    carryover_message_ids: set[UUID] = set()
    today_schedule_message_ids: set[UUID] = set()
    if not room_by_id:
        messages: list[Message] = []
        truncated = False
    else:
        selected_message_by_id: dict[UUID, Message] = {}
        truncated = False
        for chunk_start_date, chunk_end_date in period_chunks:
            chunk_start = datetime.combine(
                chunk_start_date,
                time.min,
                tzinfo=kst,
            ).astimezone(timezone.utc)
            chunk_end = datetime.combine(
                chunk_end_date + timedelta(days=1),
                time.min,
                tzinfo=kst,
            ).astimezone(timezone.utc)
            message_query = select(Message).where(
                resident_message_filter,
                Message.room_id.in_(list(room_by_id)),
                Message.created_at >= chunk_start,
                Message.created_at < chunk_end,
                Message.lifecycle_status == "active",
            )
            if getattr(processor, "_reviewer_experience", None) is not None:
                message_query = message_query.where(Message.is_test_data.is_(True))
            if payload.message_type is not None:
                message_query = message_query.where(
                    Message.message_type == payload.message_type
                )
            message_rows = list(
                db.scalars(
                    message_query.order_by(
                        Message.created_at,
                        Message.id,
                    ).limit(501)
                )
                .unique()
                .all()
            )
            if len(message_rows) > 500:
                truncated = True
                truncated_periods.append(
                    _period_label(chunk_start_date, chunk_end_date)
                )
            for message in message_rows[:500]:
                selected_message_by_id[message.id] = message
            # A recovery reply posted this period remains evidence even when
            # its parent was posted earlier and is no longer an open task.
            reply_parent_query = (
                select(Message)
                .join(MessageComment, MessageComment.message_id == Message.id)
                .where(
                    resident_message_filter,
                    Message.room_id.in_(list(room_by_id)),
                    Message.lifecycle_status == "active",
                    MessageComment.created_at >= chunk_start,
                    MessageComment.created_at < chunk_end,
                )
                .distinct()
            )
            if getattr(processor, "_reviewer_experience", None) is not None:
                reply_parent_query = reply_parent_query.where(Message.is_test_data.is_(True))
            if payload.message_type is not None:
                reply_parent_query = reply_parent_query.where(Message.message_type == payload.message_type)
            reply_parents = list(db.scalars(reply_parent_query.order_by(Message.created_at, Message.id).limit(501)).all())
            if len(reply_parents) > 500:
                truncated = True
                truncated_periods.append(_period_label(chunk_start_date, chunk_end_date))
            for message in reply_parents[:500]:
                selected_message_by_id[message.id] = message
        selected_messages = sorted(
            selected_message_by_id.values(),
            key=lambda message: (_as_utc(message.created_at), str(message.id)),
        )
        selected_message_ids = {message.id for message in selected_messages}

        carryover_query = select(Message).where(
            resident_message_filter,
            Message.room_id.in_(list(room_by_id)),
            Message.created_at < period_start,
            Message.lifecycle_status == "active",
        )
        if getattr(processor, "_reviewer_experience", None) is not None:
            carryover_query = carryover_query.where(Message.is_test_data.is_(True))
        if payload.message_type is not None:
            carryover_query = carryover_query.where(
                Message.message_type == payload.message_type
            )
        carryover_rows = list(
            db.scalars(
                carryover_query.order_by(
                    Message.created_at.desc(),
                    Message.id.desc(),
                ).limit(501)
            )
            .unique()
            .all()
        )
        if len(carryover_rows) > 500:
            truncated = True
            truncated_periods.append(
                f"{payload.start_date.isoformat()} 이전 이월 기록"
            )

        # 최근 대화 500건 밖에 있더라도 완료되지 않은 구조화 업무는
        # 브리핑에서 사라지면 안 된다. 일반 과거 대화 후보와 별도로 조회해
        # 7일 이전 미완료 업무를 계속 이월한다.
        incomplete_action_query = (
            select(Message)
            .join(ActionItem, ActionItem.source_message_id == Message.id)
            .where(
                resident_message_filter,
                Message.room_id.in_(list(room_by_id)),
                Message.created_at < period_start,
                Message.lifecycle_status == "active",
                ActionItem.status != "completed",
            )
        )
        if getattr(processor, "_reviewer_experience", None) is not None:
            incomplete_action_query = incomplete_action_query.where(
                Message.is_test_data.is_(True)
            )
        if payload.message_type is not None:
            incomplete_action_query = incomplete_action_query.where(
                Message.message_type == payload.message_type
            )
        incomplete_action_rows = list(
            db.scalars(
                incomplete_action_query.order_by(
                    Message.created_at.desc(),
                    Message.id.desc(),
                ).limit(501)
            )
            .unique()
            .all()
        )
        if len(incomplete_action_rows) > 500:
            truncated = True
            carryover_label = f"{payload.start_date.isoformat()} 이전 미완료 업무"
            if carryover_label not in truncated_periods:
                truncated_periods.append(carryover_label)

        today_schedule_query = (
            select(Message)
            .join(ActionItem, ActionItem.source_message_id == Message.id)
            .where(
                resident_message_filter,
                Message.room_id.in_(list(room_by_id)),
                Message.lifecycle_status == "active",
                ActionItem.due_at >= today_start,
                ActionItem.due_at < today_end,
            )
        )
        if getattr(processor, "_reviewer_experience", None) is not None:
            today_schedule_query = today_schedule_query.where(
                Message.is_test_data.is_(True)
            )
        if payload.message_type is not None:
            today_schedule_query = today_schedule_query.where(
                Message.message_type == payload.message_type
            )
        today_schedule_messages = list(
            db.scalars(today_schedule_query).unique().all()
        )
        today_schedule_message_ids = {
            message.id for message in today_schedule_messages
        }

        combined_by_id = {
            message.id: message
            for message in [
                *selected_messages,
                *carryover_rows[:500],
                *incomplete_action_rows[:500],
                *today_schedule_messages,
            ]
        }
        messages = sorted(
            combined_by_id.values(),
            key=lambda message: (_as_utc(message.created_at), str(message.id)),
        )

    timing.phase("relations")
    prefetch_period_relations(db, messages)
    message_ids = [message.id for message in messages]
    comments = (
        list(
            db.scalars(
                select(MessageComment)
                .where(MessageComment.message_id.in_(message_ids))
                .order_by(MessageComment.created_at, MessageComment.id)
            ).all()
        )
        if message_ids
        else []
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
    } if message_ids else {}
    confirmed_links = (
        list(
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
        if message_ids
        else []
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

    timing.phase("composition")
    retained_messages: list[Message] = []
    for message in messages:
        if (
            message.id in selected_message_ids
            or message.id in today_schedule_message_ids
        ):
            retained_messages.append(message)
            continue
        if not residents_by_message.get(message.id):
            continue
        message_text = _period_message_text(
            message,
            comments_by_message.get(message.id, []),
        )
        action_statuses = (
            [message.action_item.status] if message.action_item is not None else []
        )
        if (
            any(status != "completed" for status in action_statuses)
            or _briefing_pending_checks(
                [message_text] if message_text else [],
                action_statuses,
            )
        ):
            retained_messages.append(message)
            carryover_message_ids.add(message.id)
    messages = retained_messages

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

    message_ids = [message.id for message in messages]
    message_id_set = set(message_ids)
    comments = [
        comment for comment in comments if comment.message_id in message_id_set
    ]
    carryover_message_ids &= message_id_set
    today_schedule_message_ids &= message_id_set
    if not messages:
        empty_response = PeriodWorkdeskResponse(
            period_start=period_start,
            period_end=period_end,
            summary="선택한 범위와 이어서 확인할 미완료 업무가 없습니다.",
            generator="empty",
            message_count=0,
            comment_count=0,
            resident_count=0,
            category_counts={},
            document_counts={},
            sources=[],
            document_drafts=[],
            truncated=truncated,
            processed_periods=processed_periods,
            truncated_periods=list(dict.fromkeys(truncated_periods)),
            overall_summary=(
                "선택한 기간과 범위에는 급여제공기록지 작성에 참고할 "
                "돌봄 내용이 없습니다."
            ),
            generated_at=utcnow(),
        )
        if payload.save_history:
            _save_field_care_briefing_history(
                db=db,
                processor=processor,
                payload=payload,
                response_payload=empty_response,
                is_test_data=(
                    getattr(processor, "_reviewer_experience", None) is not None
                ),
            )
        return timing.finish(empty_response)

    if compact:
        timing.phase("relations")
        compact_sources = briefing_sources(db, messages)
        attachment_count = sum(len(source.message.attachments) for source in compact_sources)
        timing.phase("topics")
        care_topics = build_care_topics(
            sources=compact_sources, period_start=period_start, period_end=period_end,
            resident_id=payload.resident_id, split_resident_text=_resident_specific_period_text,
        )
        if payload.resident_id is None:
            care_topics = [{**topic, "entries": []} for topic in care_topics]
        timing.phase("response_build")
        result = PeriodWorkdeskResponse(
            period_start=period_start, period_end=period_end, summary="",
            generator="local-care-topics-v1", message_count=len(messages),
            comment_count=len(comments), attachment_count=attachment_count,
            resident_count=len({resident.id for message in messages for resident in residents_by_message.get(message.id, [])}),
            category_counts={}, document_counts={}, sources=[], source_ids=message_ids,
            document_drafts=[], care_topics=care_topics, response_mode="briefing",
            truncated=truncated, processed_periods=processed_periods,
            truncated_periods=list(dict.fromkeys(truncated_periods)), generated_at=utcnow(),
        )
        timing.phase("permission")
        result._question_sources = compact_sources
        _recheck_record_text_access(db, processor, set(message_ids), processor_required=True)
        return timing.finish(result)

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
            (item.confirmed_payload or item.ai_payload) if item is not None else None
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
                    linked_resident.display_name for linked_resident in linked_residents
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
            (
                resident_document_candidate_types,
                resident_document_candidate_reasons,
                resident_document_candidate_review_types,
                resident_document_candidate_review_reasons,
            ) = _record_document_candidate_metadata(
                resident_text,
                resident_usage_tags,
                has_resident=True,
            )
            if len(linked_residents) == 1:
                resident_comment_times = [
                    _as_utc(comment.created_at) for comment in message_comments
                ]
            else:
                resident_aliases = {
                    alias.casefold()
                    for alias in _period_resident_name_aliases(
                        resident.display_name
                    )
                }
                resident_comment_times = [
                    _as_utc(comment.created_at)
                    for comment in message_comments
                    if any(
                        alias in comment.body.casefold()
                        for alias in resident_aliases
                    )
                ]
            resident_action_statuses = (
                [message.action_item.status]
                if message.action_item is not None
                else []
            )
            record_event_candidates.append(
                {
                    "message_id": message.id,
                    "resident_id": resident.id,
                    "resident_name": resident.display_name,
                    "text": resident_text,
                    "summary": _briefing_observation([resident_text]),
                    "record_usage_tags": resident_usage_tags,
                    "document_candidate_types": resident_document_candidate_types,
                    "document_candidate_reasons": resident_document_candidate_reasons,
                    "document_candidate_review_types": (
                        resident_document_candidate_review_types
                    ),
                    "document_candidate_review_reasons": (
                        resident_document_candidate_review_reasons
                    ),
                    "room_name": room_by_id[message.room_id].name,
                    "sender_name": message.sender.full_name,
                    "created_at": _as_utc(message.created_at),
                    "latest_at": max(
                        [_as_utc(message.created_at), *resident_comment_times]
                    ),
                    "classification": resident_suggestion.classification,
                    "risk_level": resident_suggestion.risk_level,
                    "document_types": [
                        document_type
                        for document_type in resident_document_candidate_types
                        if document_type in DAILY_DOCUMENT_TYPES
                    ],
                    "action_statuses": resident_action_statuses,
                    "action_priorities": (
                        [message.action_item.priority]
                        if message.action_item is not None
                        else []
                    ),
                    "action_due_at": (
                        message.action_item.due_at
                        if message.action_item is not None
                        else None
                    ),
                    "is_carryover": message.id in carryover_message_ids,
                    "is_today_schedule_source": (
                        message.id in today_schedule_message_ids
                    ),
                    "scenario_event_id": _safe_synthetic_scenario_event_id(message),
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
            evidence["classifications"].append(resident_suggestion.classification)
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
            (
                general_document_candidate_types,
                general_document_candidate_reasons,
                general_document_candidate_review_types,
                general_document_candidate_review_reasons,
            ) = _record_document_candidate_metadata(
                text,
                general_tags,
                has_resident=False,
            )
            record_event_candidates.append(
                {
                    "message_id": message.id,
                    "resident_id": None,
                    "resident_name": None,
                    "text": text,
                    "summary": _briefing_observation([text]),
                    "record_usage_tags": general_tags,
                    "document_candidate_types": general_document_candidate_types,
                    "document_candidate_reasons": general_document_candidate_reasons,
                    "document_candidate_review_types": (
                        general_document_candidate_review_types
                    ),
                    "document_candidate_review_reasons": (
                        general_document_candidate_review_reasons
                    ),
                    "room_name": room_by_id[message.room_id].name,
                    "sender_name": message.sender.full_name,
                    "created_at": _as_utc(message.created_at),
                    "latest_at": max(
                        [
                            _as_utc(message.created_at),
                            *[
                                _as_utc(comment.created_at)
                                for comment in message_comments
                            ],
                        ]
                    ),
                    "classification": suggestion.classification,
                    "risk_level": suggestion.risk_level,
                    "document_types": [],
                    "action_statuses": (
                        [message.action_item.status]
                        if message.action_item is not None
                        else []
                    ),
                    "action_priorities": (
                        [message.action_item.priority]
                        if message.action_item is not None
                        else []
                    ),
                    "action_due_at": (
                        message.action_item.due_at
                        if message.action_item is not None
                        else None
                    ),
                    "is_carryover": message.id in carryover_message_ids,
                    "is_today_schedule_source": (
                        message.id in today_schedule_message_ids
                    ),
                    "scenario_event_id": _safe_synthetic_scenario_event_id(message),
                }
            )

        source_models.append(
            PeriodWorkdeskSource(
                message=message_response(message, db=db, viewer_id=processor.id),
                room_name=room_by_id[message.room_id].name,
                resident_names=[resident.display_name for resident in linked_residents],
                comments=[
                    MessageCommentResponse(
                        id=comment.id,
                        author_id=comment.author_id,
                        author_name=comment.author.full_name,
                        body=comment.body,
                        created_at=_as_utc(comment.created_at),
                    )
                    for comment in message_comments
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
    if payload.enhance_summary and len(period_chunks) == 1:
        try:
            ai_summary = summarize_room_messages(
                entries=ai_entries,
                external_allowed=all(message.is_test_data for message in messages),
                central_feature="search_summary",
            )
            summary = ai_summary.summary
            generator = f"{ai_summary.provider}:{ai_summary.model}"
        except LocalAiError:
            generator = "safe-period-summary-fallback-v1"
    elif payload.enhance_summary:
        # 장기 범위의 원문을 한 번에 외부/대형 모델로 보내지 않는다.
        # 구간별 규칙 추출과 중복 제거 결과를 그대로 사용한다.
        generator = "quick-period-summary-v1:chunked-safe"

    record_events = _group_record_events(record_event_candidates)
    record_group_counts = {
        tag: sum(tag in event.record_usage_tags for event in record_events)
        for tag in RECORD_USAGE_LABELS
    }
    document_candidate_counts = {
        candidate_type: sum(
            candidate_type in event.document_candidate_types
            for event in record_events
        )
        for candidate_type in CARE_DOCUMENT_CANDIDATE_LABELS
    }

    document_drafts: list[PeriodDocumentDraft] = []

    baseline_start = period_start - timedelta(days=BRIEFING_COMPARISON_DAYS)
    baseline_query = (
        select(Message)
        .where(
            Message.room_id.in_(list(room_by_id)),
            Message.created_at >= baseline_start,
            Message.created_at < period_start,
            Message.lifecycle_status == "active",
        )
        .order_by(Message.created_at, Message.id)
        .limit(501)
    )
    if payload.message_type is not None:
        baseline_query = baseline_query.where(
            Message.message_type == payload.message_type
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        baseline_query = baseline_query.where(Message.is_test_data.is_(True))
    if message_ids:
        # 이월 또는 오늘 일정으로 현재 카드에 포함한 근거를 비교 기준에
        # 다시 넣으면 자기 자신과 비교하게 되므로 제외한다.
        baseline_query = baseline_query.where(~Message.id.in_(message_ids))
    baseline_messages = list(db.scalars(baseline_query).unique().all())
    if len(baseline_messages) > 500:
        truncated = True
        truncated_periods.append(
            f"{(payload.start_date - timedelta(days=BRIEFING_COMPARISON_DAYS)).isoformat()}"
            f" ~ {(payload.start_date - timedelta(days=1)).isoformat()} 비교 기준"
        )
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

    briefing_cards: list[CareBriefingCard] = []
    risk_order = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
    for record_event in record_events:
        resident_id = record_event.resident_id
        if resident_id is None or resident_id not in resident_evidence:
            continue
        evidence = resident_evidence[resident_id]
        event_candidates = sorted(
            (
                candidate
                for candidate in record_event_candidates
                if candidate["resident_id"] == resident_id
                and candidate["message_id"] in record_event.evidence_ids
            ),
            key=lambda candidate: (
                candidate["created_at"],
                candidate.get("latest_at", candidate["created_at"]),
            ),
        )
        if not event_candidates:
            continue
        event_texts = [candidate["text"] for candidate in event_candidates]
        current_classifications = {
            candidate["classification"] for candidate in event_candidates
        }
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
        observation = _briefing_observation(event_texts)
        baseline_meal_fractions = _briefing_meal_fractions(
            baseline.get("plain_texts", [])
        )
        current_meal_fractions = _briefing_meal_fractions(event_texts)
        meal_fraction_sequence = baseline_meal_fractions + current_meal_fractions
        if len(meal_fraction_sequence) >= 3 and len(set(meal_fraction_sequence)) > 1:
            change_summary = (
                f"최근 기록의 식사량이 {' → '.join(meal_fraction_sequence[-4:])}로 "
                f"이어졌습니다. {observation}"
            )
        elif baseline["count"] == 0:
            change_summary = (
                f"비교할 최근 {BRIEFING_COMPARISON_DAYS}일 기록이 없어 "
                f"이번 관찰을 첫 기준으로 표시합니다. {observation}"
            )
        elif new_labels:
            change_summary = (
                f"최근 {BRIEFING_COMPARISON_DAYS}일 기록에는 없던 "
                f"{'·'.join(new_labels)} 관련 보고가 선택 범위에 새로 있습니다. "
                f"{observation}"
            )
        else:
            change_summary = (
                f"최근 {BRIEFING_COMPARISON_DAYS}일에도 "
                f"{'·'.join(current_labels) or '같은 주제'} 관련 기록이 있었고 "
                f"선택 범위에 다시 보고되었습니다. {observation}"
            )

        risk_level = max(
            (candidate["risk_level"] for candidate in event_candidates),
            key=lambda value: risk_order.get(value, 0),
        )
        action_statuses = [
            status
            for candidate in event_candidates
            for status in candidate["action_statuses"]
        ]
        action_priorities = [
            priority
            for candidate in event_candidates
            for priority in candidate["action_priorities"]
        ]
        action_due_ats = [
            candidate["action_due_at"]
            for candidate in event_candidates
            if candidate["action_due_at"] is not None
        ]
        earliest_due_at = min(action_due_ats) if action_due_ats else None
        is_carryover = any(
            candidate["is_carryover"] for candidate in event_candidates
        )
        is_today_schedule, due_at = _briefing_today_schedule(
            event_candidates,
            current_date=today_date,
        )
        is_today_schedule = is_today_schedule or any(
            candidate["is_today_schedule_source"]
            for candidate in event_candidates
        )
        due_at = due_at or earliest_due_at
        completed_actions = _briefing_completed_actions(event_texts)
        pending_checks = _briefing_pending_checks(
            event_texts,
            action_statuses,
            allow_later_resolution=bool(event_candidates[0].get("scenario_event_id"))
            and all(
                candidate.get("scenario_event_id")
                == event_candidates[0].get("scenario_event_id")
                for candidate in event_candidates
            ),
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
            event_texts,
            risk_level,
        )
        if risk_reason:
            reasons.append(risk_reason)
        if (
            len(meal_fraction_sequence) >= 3
            and len(set(meal_fraction_sequence)) > 1
            and any(
                term in " ".join(event_texts)
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
        if is_today_schedule:
            reasons.append("오늘 예정됐거나 오늘까지 확인해야 하는 일정입니다.")
        if due_at is not None and any(
            status != "completed" for status in action_statuses
        ):
            due_date = _as_utc(due_at).astimezone(kst).date()
            if due_date < today_date:
                reasons.append("기한이 지났지만 완료 기록이 없어 계속 확인해야 합니다.")
        if is_carryover:
            reasons.append("최근 7일 이전 기록이지만 후속 확인이 끝나지 않았습니다.")
        if not reasons:
            reasons.append("선택 범위에 새 관찰이 기록되어 경과 확인에 사용할 수 있습니다.")

        final_status, final_status_summary = _briefing_final_status(
            event_texts,
            action_statuses,
            pending_checks,
        )
        importance_score = _briefing_importance_score(
            risk_level=risk_level,
            pending_checks=pending_checks,
            has_new_topic=bool(new_labels and baseline["count"] > 0),
            action_priorities=action_priorities,
            action_statuses=action_statuses,
            due_at=due_at,
            is_today_schedule=is_today_schedule,
            is_carryover=is_carryover,
            final_status=final_status,
            latest_at=record_event.latest_at,
            current_date=today_date,
        )
        if importance_score >= 55:
            priority = "first"
        elif importance_score >= 25:
            priority = "check"
        else:
            priority = "observe"
        if is_today_schedule:
            display_group = "today_schedule"
        elif is_carryover and final_status != "completed":
            display_group = "carryover"
        elif final_status == "completed":
            display_group = "completed"
        elif importance_score >= 25:
            display_group = "attention"
        else:
            display_group = "background"
        document_types = [
            document_type
            for document_type in dict.fromkeys(
                document_type
                for candidate in event_candidates
                for document_type in candidate["document_types"]
            )
            if document_type in DAILY_DOCUMENT_TYPES
        ]
        event_classifications = [
            candidate["classification"] for candidate in event_candidates
        ]
        event_classification = max(
            set(event_classifications),
            key=lambda value: (event_classifications.count(value), value),
        )
        event_text = "\n\n".join(event_texts)
        for document_type in document_types:
            proposal = build_document_proposal(
                document_type,
                resident_label=evidence["resident"].display_name,
                text=event_text,
                classification=event_classification,
                risk_level=risk_level,
                recorded_at=record_event.occurred_at.astimezone(kst).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                evidence_count=len(record_event.evidence_ids),
            )
            document_drafts.append(
                PeriodDocumentDraft(
                    key=f"{record_event.event_group_id}:{document_type}",
                    event_group_id=record_event.event_group_id,
                    resident_id=resident_id,
                    resident_name=evidence["resident"].display_name,
                    document_type=document_type,
                    content=str(proposal["content"]),
                    draft_fields=dict(proposal.get("draft_fields", {})),
                    missing_fields=list(proposal.get("missing_fields", [])),
                    human_verification_fields=list(
                        proposal.get("human_verification_fields", [])
                    ),
                    verification_questions=list(
                        proposal.get("verification_questions", [])
                    ),
                    source_message_ids=list(record_event.evidence_ids),
                )
            )
        briefing_cards.append(
            CareBriefingCard(
                event_group_id=record_event.event_group_id,
                resident_id=resident_id,
                resident_name=evidence["resident"].display_name,
                priority=priority,
                display_group=display_group,
                importance_score=importance_score,
                due_at=due_at,
                change_summary=change_summary,
                final_status=final_status,
                final_status_summary=final_status_summary,
                check_reasons=reasons[:3],
                completed_actions=completed_actions,
                pending_checks=pending_checks,
                document_types=document_types,
                record_usage_tags=record_event.record_usage_tags,
                document_candidate_types=record_event.document_candidate_types,
                source_message_ids=record_event.evidence_ids,
                current_message_count=len(record_event.evidence_ids),
                baseline_message_count=int(baseline["count"]),
                occurred_at=record_event.occurred_at,
                latest_at=record_event.latest_at,
                social_worker_guidance=build_social_worker_guidance(
                    texts=event_texts,
                    baseline_texts=list(baseline.get("plain_texts", [])),
                    observed_at=record_event.occurred_at.astimezone(kst).strftime(
                        "%Y-%m-%d %H:%M"
                    ),
                    pending_checks=pending_checks,
                    document_candidates=list(
                        record_event.document_candidate_types
                    ),
                    final_status=final_status,
                ),
            )
        )
    display_group_order = {
        "today_schedule": 0,
        "attention": 1,
        "carryover": 2,
        "background": 3,
        "completed": 4,
    }
    briefing_cards.sort(
        key=lambda card: (
            display_group_order[card.display_group],
            -card.importance_score,
            -_as_utc(card.latest_at).timestamp(),
        )
    )
    briefing = CareBriefingSummary(
        comparison_days=BRIEFING_COMPARISON_DAYS,
        today_schedule_count=sum(
            card.display_group == "today_schedule" for card in briefing_cards
        ),
        needs_attention_count=sum(
            card.display_group in {"today_schedule", "attention", "carryover"}
            for card in briefing_cards
        ),
        carryover_count=sum(
            card.display_group == "carryover" for card in briefing_cards
        ),
        background_count=sum(
            card.display_group == "background" for card in briefing_cards
        ),
        completed_count=sum(
            card.display_group == "completed" for card in briefing_cards
        ),
        pending_check_count=sum(len(card.pending_checks) for card in briefing_cards),
        document_candidate_count=sum(
            len(card.document_types) for card in briefing_cards
        ),
        cards=briefing_cards,
    )

    scenario_resident = next(
        (
            evidence["resident"]
            for evidence in resident_evidence.values()
            if evidence["resident"].is_test_data is True
            and evidence["resident"].internal_code
            == "LONGITUDINAL-SYNTHETIC-V1"
        ),
        None,
    )
    candidate_texts_by_evidence: dict[UUID, list[str]] = {}
    for candidate in record_event_candidates:
        candidate_texts_by_evidence.setdefault(candidate["message_id"], []).append(
            candidate["text"]
        )
    field_briefing = build_field_care_briefing(
        record_events=record_events,
        candidate_texts_by_evidence=candidate_texts_by_evidence,
        selected_message_ids=selected_message_ids,
        period_start=period_start,
        period_end=period_end,
    )
    care_topics = build_care_topics(
        sources=source_models,
        period_start=period_start,
        period_end=period_end,
        resident_id=payload.resident_id,
        split_resident_text=_resident_specific_period_text,
    )
    generated_at = utcnow()
    response_payload = PeriodWorkdeskResponse(
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
        care_topics=care_topics,
        record_group_counts=record_group_counts,
        document_candidate_counts=document_candidate_counts,
        briefing=briefing,
        truncated=truncated,
        processed_periods=processed_periods,
        truncated_periods=list(dict.fromkeys(truncated_periods)),
        scenario_label=(
            f"{scenario_resident.display_name} · 검증용 가상 시나리오"
            if scenario_resident is not None
            else None
        ),
        scenario_notice=(
            "아래 대화와 사건은 기능 검증을 위해 만든 가상자료이며 "
            "공식 돌봄기록이 아닙니다."
            if scenario_resident is not None
            else None
        ),
        overall_summary=field_briefing["overall_summary"],
        daily_care_references=field_briefing["daily_care_references"],
        consultation_references=field_briefing["consultation_references"],
        care_reference_count=field_briefing["care_reference_count"],
        consultation_reference_count=field_briefing[
            "consultation_reference_count"
        ],
        generated_at=generated_at,
    )
    if payload.save_history:
        _save_field_care_briefing_history(
            db=db,
            processor=processor,
            payload=payload,
            response_payload=response_payload,
            is_test_data=all(message.is_test_data is True for message in messages),
        )
    return timing.finish(response_payload)


@app.post("/api/workdesk/period-review/evidence", response_model=list[PeriodWorkdeskSource])
def load_period_review_evidence(
    payload: PeriodEvidenceRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    # Recompute the current authorized scope; client IDs are never authority.
    review = create_period_workdesk_review(
        PeriodWorkdeskRequest(**{**payload.model_dump(exclude={"message_ids"}), "response_mode": "briefing", "enhance_summary": False, "save_history": False}),
        processor, db,
    )
    ids=set(payload.message_ids)
    if not ids <= set(review.source_ids):
        raise HTTPException(status_code=403, detail="현재 선택 범위에서 근거를 확인할 수 없습니다.")
    messages=list(db.scalars(select(Message).where(Message.id.in_(ids))).all())
    prefetch_period_relations(db,messages)
    by_id={message.id:message for message in messages}
    result=[]
    for identifier in dict.fromkeys(payload.message_ids):
        message=by_id[identifier]
        result.append(PeriodWorkdeskSource(
            message=message_response(message,db=db,viewer_id=processor.id),
            room_name=message.room.name,
            resident_names=[resident.display_name for resident in ([message.resident] if message.resident else [])],
            comments=[MessageCommentResponse(id=c.id,author_id=c.author_id,author_name=c.author.full_name,body=c.body,created_at=_as_utc(c.created_at)) for c in message.comments],
            read_count=0,reply_count=len(message.comments),reply_user_count=len({c.author_id for c in message.comments}),
        ))
    _recheck_record_text_access(db,processor,ids,processor_required=True)
    return result


def _record_identity_names(db: Session, organization_id: UUID) -> list[str]:
    return list(dict.fromkeys(
        name for model in (Resident, Staff, User)
        for name in db.scalars(select(model.display_name).where(model.organization_id == organization_id)).all()
        if name
    ))


def _recheck_record_text_access(db: Session, user: User, message_ids: set[UUID], *, require_membership: bool = False, processor_required: bool = False) -> None:
    db.expire_all()
    if not user.is_active or (user.staff is not None and (not user.staff.is_active or user.staff.deleted_at is not None)):
        raise HTTPException(status_code=403, detail="현재 기록 조회 권한을 확인할 수 없습니다.")
    if processor_required:
        _require_processor(user)
    query = select(Message.id).join(Room, Room.id == Message.room_id).where(Message.id.in_(message_ids), Message.organization_id == user.organization_id, Message.lifecycle_status == "active", Room.organization_id == user.organization_id, Room.is_active.is_(True))
    if require_membership or user.role != "admin":
        query = query.join(RoomMembership, RoomMembership.room_id == Room.id).where(RoomMembership.staff_id == user.staff_id, RoomMembership.left_at.is_(None))
    if getattr(user, "_reviewer_experience", None) is not None:
        query = query.where(_reviewer_room_scope_filter(user), Message.is_test_data.is_(True))
    if set(db.scalars(query).all()) != message_ids:
        raise HTTPException(status_code=403, detail="기록 접근 권한이 변경되어 결과를 표시할 수 없습니다.")


def _prepare_care_record_question(
    payload: RecordQuestionRequest,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    resolved_name = None
    if payload.resident_id is None:
        roster = list_residents_for_workdesk(processor=processor, db=db)
        matches = [resident for resident in roster if re.search(r"(?<![가-힣A-Za-z0-9])" + re.escape(resident.display_name) + r"(?=$|[\s,.!?]|어르신|님|은|는|이|가|의|께|에|을|를)", payload.question)]
        if len(matches) > 1 and not (is_aggregate_question(payload.question) and len({resident.display_name for resident in matches}) == len(matches)):
            return {"clarification": True, "payload": payload}
        if len(matches) == 1:
            payload = payload.model_copy(update={"resident_id": matches[0].id})
            resolved_name = matches[0].display_name
    period_notes=[]
    explicit_window=question_date_window(payload.question,datetime.now(KST).date())
    if explicit_window and payload.range_mode=="default":
        payload=payload.model_copy(update={"start_date":explicit_window[0],"end_date":explicit_window[1]})
        period_notes.append(f"질문의 기간 표현에 따라 {explicit_window[0]}~{explicit_window[1]} 기록을 확인했습니다.")
    elif explicit_window and not (payload.start_date<=explicit_window[0] and explicit_window[1]<=payload.end_date):
        period_notes.append(f"질문은 {explicit_window[0]}~{explicit_window[1]} 기간을 가리키지만 현재 고정 범위 {payload.start_date}~{payload.end_date} 안에서 확인했습니다. 기간 선택에서 범위를 바꿀 수 있습니다.")

    def retrieve(active_payload):
        # Reuse the authenticated room/resident/type/ACL filters on every pass.
        review = create_period_workdesk_review(
            payload=PeriodWorkdeskRequest(
                start_date=active_payload.start_date, end_date=active_payload.end_date,
                room_id=active_payload.room_id, resident_id=active_payload.resident_id,
                message_type=active_payload.message_type,
                enhance_summary=False, save_history=False, response_mode="briefing",
            ), processor=processor, db=db,
        )
        topics = build_care_topics(
            sources=review._question_sources, period_start=review.period_start,
            period_end=review.period_end, resident_id=active_payload.resident_id,
            split_resident_text=_resident_specific_period_text, include_unclassified=True,
        )
        return review,plan_question(active_payload.question,topics,resident_id=active_payload.resident_id)

    review,plan=retrieve(payload)
    # A ranking must keep its declared denominator. An empty aggregate is not
    # permission to search older periods until a winner can be found.
    if not is_aggregate_question(payload.question) and payload.range_mode=="default" and explicit_window is None and plan.get("matched_event_count",0)==0:
        original_start=payload.start_date
        for days in (30,90,180):
            expanded_start=payload.end_date-timedelta(days=days-1)
            if expanded_start>=payload.start_date:continue
            expanded=payload.model_copy(update={"start_date":expanded_start})
            next_review,next_plan=retrieve(expanded)
            review,plan,payload=next_review,next_plan,expanded
            if next_plan.get("matched_event_count",0)>0:break
        if payload.start_date<original_start:
            period_notes.append(f"초기 범위에서 관련 기록을 찾지 못해 권한 안에서 {payload.start_date}~{payload.end_date}까지 넓혀 확인했습니다.")
    plan["notes"]=[*period_notes,*plan["notes"]]
    if is_aggregate_question(payload.question):
        aggregate_facts=[dict(fact) for fact in plan["scope_facts"]]
        parents={source.message.id: (source.message.reply_to.message_id if source.message.reply_to is not None else None)
                 for source in review._question_sources}
        roots={};incomplete_links=False
        for identifier in parents:
            current=identifier;visited=set()
            while current in parents and parents[current] is not None and current not in visited:
                visited.add(current);current=parents[current]
            if current not in parents or current in visited:
                incomplete_links=True
            roots[identifier]=current
        for fact in aggregate_facts:
            fact["event_key"]=f"{fact.get('resident_id')}:{roots.get(fact['message_id'],fact['message_id'])}"
        complete=not review.truncated and not (explicit_window and not (
            payload.start_date<=explicit_window[0] and explicit_window[1]<=payload.end_date))
        aggregate=aggregate_question(payload.question,aggregate_facts,
            start_date=payload.start_date,end_date=payload.end_date,scope_complete=complete,
            scope_reason_codes=[*(['event_link_incomplete'] if incomplete_links else []),
                *(['attachment_scope_incomplete'] if has_unread_aggregate_attachments(review._question_sources,
                    start_date=payload.start_date,end_date=payload.end_date) else []),
                *(['source_attribution_incomplete'] if has_unattributed_source(payload.question,review._question_sources,
                    start_date=payload.start_date,end_date=payload.end_date,split_resident_text=_resident_specific_period_text) else []),
                *(['correction_outside_period'] if has_later_record_correction(payload.question,review._question_sources,payload.end_date) else [])])
        selected_ids={source.message.id for source in review._question_sources}
        return dict(payload=payload,terminal_result=aggregate,selected_ids=selected_ids,
            attachment_review_fingerprint=message_review_fingerprint(db,selected_ids),
            resolved_name=resolved_name,truncated=review.truncated)
    facts = plan["facts"]
    names = _record_identity_names(db, processor.organization_id)
    selected_ids = {fact["message_id"] for fact in facts}
    # A pre-period parent provides context for an in-period reply, and remains
    # visibly marked as background rather than a new event in this period.
    for source in review._question_sources:
        if source.message.id not in selected_ids:
            continue
        if any(fact["message_id"] == source.message.id and fact.get("source_kind") == "message" and _as_utc(fact["occurred_at"]) == _as_utc(source.message.created_at) for fact in facts):
            continue
        selected_names = {fact["resident_name"] for fact in facts if fact["message_id"] == source.message.id}
        for name in selected_names:
            text = resident_specific_search_text(source.message.body, target_name=name, resident_names=names, allow_unscoped=len(selected_names) == 1)
            if text:
                origin = next(fact for fact in facts if fact["message_id"] == source.message.id and fact["resident_name"] == name)
                background = {"message_id": source.message.id, "occurred_at": source.message.created_at, "resident_name": name, "resident_id": origin.get("resident_id"), "event_key": origin.get("event_key"), "summary": text, "kind": "event", "background": True}
                facts.append(background)
                if any(fact["message_id"] == source.message.id and fact["resident_name"] == name for fact in plan["rule_facts"]):
                    plan["rule_facts"].append(background)
    facts.sort(key=lambda fact: _as_utc(fact["occurred_at"]))
    synthetic_count = db.scalar(select(func.count()).select_from(Message).where(Message.id.in_(selected_ids), Message.is_test_data.is_(True)))
    non_synthetic_comments = db.scalar(select(func.count()).select_from(MessageComment).where(MessageComment.message_id.in_(selected_ids), MessageComment.is_test_data.is_not(True)))
    plan["rule_facts"].sort(key=lambda fact: _as_utc(fact["occurred_at"]))
    result = answer_facts(payload.question, plan["rule_facts"], notes=plan["notes"], first=plan["first"], scope_count=plan["scope_count"], ambiguous=plan["ambiguous"])
    if review.truncated:
        result["limitation"] = " ".join(filter(None, [result["limitation"], "일부 기록은 조회 건수 제한으로 포함되지 않았습니다."]))
    return dict(payload=payload, result=result, facts=facts, names=names,
                notes=plan["notes"],
                attachment_review_fingerprint=message_review_fingerprint(db, selected_ids),
                selected_ids=selected_ids, resolved_name=resolved_name, truncated=review.truncated,
                all_synthetic=bool(selected_ids) and synthetic_count == len(selected_ids) and not non_synthetic_comments)


_record_question_progress: dict[UUID, tuple[str, float]] = {}


@app.post("/api/workdesk/record-question/model-ready")
async def prepare_care_question_model(request: Request, processor: User = Depends(_require_processor),
    feature: Literal['care_record_question','search_summary','document_text'] = 'care_record_question'):
    # No request payload or record query. The authenticated processor role is
    # the same authorization boundary as the briefing/question endpoints.
    return await await_connected(asyncio.create_task(prepare_record_model(wait=False, feature=feature)), request)


@app.get("/api/workdesk/record-question/progress")
def care_question_progress(processor: User = Depends(_require_processor)):
    phase,at=_record_question_progress.get(processor.id,('idle',0))
    return {'phase':phase if perf_counter()-at<40 else 'idle'}


@app.post("/api/workdesk/record-question", response_model=RecordQuestionResponse)
async def ask_care_record_question(
    payload: RecordQuestionRequest, request: Request,
    processor: User = Depends(_require_processor), db: Session = Depends(get_db),
):
    started = perf_counter()
    # Model-independent answers return first. Model-dependent callers receive a
    # retryable pending response, not a long cold-load HTTP request.
    for identifier,(_,at) in list(_record_question_progress.items()):
        if started-at>60:_record_question_progress.pop(identifier,None)
    def progress(phase):_record_question_progress[processor.id]=(phase,perf_counter())
    progress('retrieving')
    # Sequential worker use: the SQLAlchemy session is never used concurrently.
    try:
        context = await asyncio.to_thread(_prepare_care_record_question, payload, processor, db)
    except BaseException:
        progress('idle')
        raise
    retrieval_ms = round((perf_counter() - started) * 1000)
    payload = context["payload"]
    if context.get("clarification"):
        progress('complete')
        return RecordQuestionResponse(question=payload.question, period_start=payload.start_date,
            period_end=payload.end_date, answer="질문에 일치하는 어르신이 여러 명입니다. 어르신을 선택한 뒤 다시 질문해 주세요.",
            processing_method="clarification", performance={"record_retrieval_ms": retrieval_ms, "candidate_count": 0})
    terminal_result = context.get("terminal_result")
    if terminal_result is None and not context["facts"]:
        # No retrieved record means there is nothing to send to a model. This
        # is a normal scope result, not a GPU or real-data logging-policy error.
        terminal_result = dict(context["result"])
        terminal_result.pop("_deterministic_fact", None)
        terminal_result.update(processing_method="no_records", error_type=None,
            generation_verified=False, answer_sentences=[], evidence_ids=[],
            structured_facts=[], timeline=[], current_status=None, unknowns=[],
            generator="record-scope-check-v1", ai_enhancement_available=False)
    if terminal_result is not None:
        await asyncio.to_thread(_recheck_record_text_access,db,processor,context["selected_ids"],processor_required=True)
        if context["attachment_review_fingerprint"] != message_review_fingerprint(db,context["selected_ids"]):
            raise HTTPException(409,"근거의 직원 확인 상태가 변경되었습니다. 질문을 다시 실행해 주세요.")
        if await request.is_disconnected():raise HTTPException(499,"요청이 취소되었습니다.")
        progress('complete')
        return RecordQuestionResponse(question=payload.question,period_start=payload.start_date,
            period_end=payload.end_date,resident_id=payload.resident_id,
            resolved_resident_name=context["resolved_name"],truncated=context["truncated"],
            performance={"record_retrieval_ms":retrieval_ms,"total_ms":round((perf_counter()-started)*1000),
                         "model_called":False},**terminal_result)
    progress('preparing')
    preparation=asyncio.create_task(prepare_record_model(wait=False))
    try:
        async with asyncio.timeout(max(.01,33.0-(perf_counter()-started))):
            prepared=await await_connected(preparation,request)
    except TimeoutError:
        prepared={'status':'unavailable','error_type':'timeout'}
    finally:
        if not preparation.done():preparation.cancel()
    result = context["result"]
    expected_review_state = context.get("attachment_review_fingerprint") or message_review_fingerprint(db, context["selected_ids"])
    result.pop("_deterministic_fact", False)
    if await request.is_disconnected():
        raise HTTPException(status_code=499, detail="요청이 취소되었습니다.")
    # The key remains in memory only and includes user, scope and source revision.
    key = sha256((str(processor.id) + payload.model_dump_json(exclude={"ai_phase"}) + repr(context["facts"])).encode()).hexdigest()
    if prepared['status']=='ready':
        task = asyncio.create_task(generate_narrative(question=payload.question, facts=context["facts"],
            names=context["names"], all_synthetic=context["all_synthetic"], request_key=key,
            deadline=started + 33.0, allow_cold_start=True,progress=progress))
        generated = await await_connected(task, request)
    else:
        generated={'generation_verified':False,'error_type':prepared.get('error_type') or ('model_preparing' if prepared['status']=='preparing' else 'model_not_ready'),
            'ai_elapsed_ms':prepared.get('total_ms',0),'load_ms':None,'generation_ms':None,'prefill_ms':None}
    enhancement_retryable = generated["error_type"] in {"model_cold", "model_not_ready", "model_prepare_busy", "model_preparing", "model_prepare_timeout", "timeout"}
    result.update(processing_method="rules", generation_verified=False,
                  ai_elapsed_ms=generated["ai_elapsed_ms"], error_type=generated["error_type"],
                  ai_enhancement_available=enhancement_retryable)
    if generated["generation_verified"]:
        selected_events = {event_id(fact) for fact in generated["selected_facts"]}
        chain = [fact for fact in context["facts"] if event_id(fact) in selected_events]
        scope_limitation=result.get("limitation")
        structured=answer_facts(payload.question,chain).get("structured_facts",[])
        result.update(answer=generated["answer"], answer_sentences=generated["sentences"],
                      processing_method="local_ai",
                      generation_verified=True, generator="local-narrative-v1",
                      structured_facts=structured, timeline=[], current_status=None,
                      unknowns=[], limitation=scope_limitation)
        # The compact answer's drawer may expose only records cited by a
        # validated final sentence, not all selected retrieval context.
        result["evidence_ids"] = list(dict.fromkeys(
            evidence_id
            for sentence in generated["sentences"]
            for evidence_id in sentence["evidence_ids"]
        ))
    elif not context["facts"]:
        result.update(processing_method="no_records", answer_sentences=[], evidence_ids=[],
            structured_facts=[], timeline=[], current_status=None, unknowns=[],
            generator="record-scope-check-v1")
    elif generated["error_type"]:
        notices = {
            "model_cold": "로컬 AI 모델 준비가 끝나지 않았습니다. 잠시 후 다시 시도해 주세요.",
            "model_not_ready": "선택된 로컬 AI 모델이 설치되어 있지만 아직 준비되지 않았습니다. 잠시 후 다시 시도해 주세요.",
            "model_missing": "중앙 AI 설정에서 선택한 모델을 찾지 못했습니다. 관리자에게 알려 주세요.",
            "model_prepare_busy": "다른 AI 작업이 진행 중입니다. 잠시 후 다시 시도해 주세요.",
            "model_preparing": "로컬 AI 모델을 준비하고 있습니다. 준비가 끝난 뒤 다시 질문해 주세요.",
            "model_prepare_timeout": "로컬 AI 모델 준비 제한시간을 초과했습니다. 관리자에게 모델 적재 상태를 확인해 주세요.",
            "timeout": "AI 답변 제한시간을 초과했습니다. 잠시 후 다시 시도해 주세요.",
            "gpu_capacity_unverified": "다른 AI 작업과 함께 실행할 메모리 여유를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요.",
            "gpu_memory_insufficient": "AI 처리에 필요한 메모리가 부족합니다. 관리자에게 알려 주세요.",
            "model_connection_error": "로컬 AI 서버에 연결하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.",
            "model_server_error": "로컬 AI 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해 주세요.",
            "context_limit": "질문과 기록이 AI가 한 번에 처리할 수 있는 범위를 넘었습니다. 조회 기간이나 대상을 줄여 다시 질문해 주세요.",
            "response_truncated": "AI 응답이 끝까지 생성되지 않았습니다. 다시 질문해 주세요.",
            "response_format_invalid": "AI 답변 형식을 확인하지 못했습니다. 다시 질문해 주세요.",
            "review_format_invalid": "AI 근거 확인 결과를 읽지 못했습니다. 다시 질문해 주세요.",
            "question_answer_not_supported": "AI 답변이 질문에 직접 답하는지 확인하지 못했습니다. 다시 질문해 주세요.",
            "narrative_validation_failed": "AI 답변의 사실과 근거를 확인하지 못했습니다. 다시 질문해 주세요.",
            "no_relevant_records": "AI가 질문과 직접 관련된 답변을 만들지 못했습니다. 질문이나 기간을 바꾸어 다시 시도해 주세요.",
            "duplicate_in_progress": "같은 질문이 이미 처리 중입니다. 진행 중인 답변을 기다려 주세요.",
            "busy": "AI가 다른 질문을 처리 중입니다. 잠시 후 다시 시도해 주세요.",
        }
        result.update(
            answer="이번에는 AI 답변을 완성하지 못했습니다.",
            answer_sentences=[], evidence_ids=[], structured_facts=[], timeline=[],
            current_status=None, unknowns=[],
            processing_method="failed", generator="local-ai-unavailable-v1",
            fallback_notice=notices.get(generated["error_type"], "AI 답변과 근거를 확인하지 못했습니다. 잠시 후 다시 시도해 주세요."),
        )
        # The rules preview was not delivered as this answer. Preserve only
        # retrieval coverage, never its claims of a summary/evidence drawer.
        result["limitation"] = " ".join(filter(None, [
            *context.get("notes", []),
            "일부 기록은 조회 건수 제한으로 포함되지 않았습니다." if context["truncated"] else None,
        ])) or None
    # Re-read permissions after generation; a stale request cannot disclose data.
    await asyncio.to_thread(_recheck_record_text_access, db, processor,
        set(result["evidence_ids"]) | context["selected_ids"], processor_required=True)
    if expected_review_state != message_review_fingerprint(db, context["selected_ids"]):
        raise HTTPException(409, "근거의 직원 확인 상태가 변경되었습니다. 질문을 다시 실행해 주세요.")
    result["performance"] = {"record_retrieval_ms": retrieval_ms, "candidate_count": len(context["facts"]),
        "model_status_ms":prepared.get('status_ms',0)+(generated.get('model_status_ms') or 0),
        "cold_load_ms":prepared.get('cold_load_ms',0),
        "model_preparation_call_ms":prepared.get('preparation_call_ms'),
        "model_preparation_total_ms":prepared.get('total_ms'),
        "draft_ms":generated.get('draft_ms'),"evidence_review_ms":generated.get('evidence_review_ms'),
        "draft_generation_ms":generated.get('draft_generation_ms'),
        "draft_prefill_ms":generated.get('draft_prefill_ms'),
        "review_generation_ms":generated.get('review_generation_ms'),
        "keep_alive_seconds":prepared.get('keep_alive_seconds'),
        "model_load_ms": generated["load_ms"], "ai_generation_ms": generated["generation_ms"],
        "model_prefill_ms": generated["prefill_ms"], "total_ms": round((perf_counter()-started)*1000),
        "loaded_model_count": generated.get("loaded_model_count"),
        "loaded_vram_bytes": generated.get("loaded_vram_bytes"), "ai_phase": payload.ai_phase,
        "validation_stage": generated.get("validation_stage"),
        "draft_sentence_count": generated.get("draft_sentence_count"),
        "guarded_sentence_count": generated.get("guarded_sentence_count"),
        "rejected_sentence_count": generated.get("rejected_sentence_count"),
        "correction_attempted": generated.get("correction_attempted"),
        "guard_rejection_types": generated.get("guard_rejection_types"),
        "model_candidate_count": generated.get("model_candidate_count"),
        "evidence_verified": generated["generation_verified"], "processing_method": result["processing_method"]}
    progress('complete')
    return RecordQuestionResponse(
        question=payload.question, period_start=payload.start_date,
        period_end=payload.end_date, resident_id=payload.resident_id,
        sources=[], truncated=context["truncated"], resolved_resident_name=context["resolved_name"], **result,
    )


def _can_read_care_briefing_history(history: FieldCareBriefingHistory, processor: User, db: Session) -> bool:
    """A saved summary must not bypass a later room access revocation."""
    sources = (history.response_payload or {}).get("sources", [])
    try:
        source_ids = {UUID(str(source["message"]["id"])) for source in sources}
    except (KeyError, TypeError, ValueError):
        return False
    if not source_ids:
        return not history.evidence_refs
    affected = list(db.scalars(select(MessageAttachment).where(MessageAttachment.message_id.in_(source_ids))))
    if any(requires_staff_review(row) for row in affected):
        if (history.response_payload or {}).get("attachment_review_fingerprint") != review_fingerprint(affected):
            return False
    query = (
        select(Message.id).join(Room, Room.id == Message.room_id)
        .where(
            Message.id.in_(source_ids), Message.organization_id == processor.organization_id,
            Message.lifecycle_status == "active", Room.organization_id == processor.organization_id,
            Room.is_active.is_(True),
        )
    )
    if processor.role != "admin":
        if processor.staff_id is None:
            return False
        query = query.join(RoomMembership, RoomMembership.room_id == Room.id).where(
            RoomMembership.staff_id == processor.staff_id, RoomMembership.left_at.is_(None),
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        query = query.where(_reviewer_room_scope_filter(processor), Message.is_test_data.is_(True))
    return set(db.scalars(query).all()) == source_ids


@app.get(
    "/api/workdesk/period-reviews",
    response_model=list[FieldCareBriefingHistorySummary],
)
def list_period_workdesk_reviews(
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    query = select(FieldCareBriefingHistory).where(
        FieldCareBriefingHistory.organization_id == processor.organization_id
    )
    if processor.role != "admin":
        query = query.where(FieldCareBriefingHistory.created_by_id == processor.id)
    if getattr(processor, "_reviewer_experience", None) is not None:
        query = query.where(FieldCareBriefingHistory.is_test_data.is_(True))
    histories = list(
        db.scalars(
            query.order_by(
                FieldCareBriefingHistory.created_at.desc(),
                FieldCareBriefingHistory.id.desc(),
            ).limit(30)
        ).all()
    )
    return [
        FieldCareBriefingHistorySummary(
            id=history.id,
            revision=history.revision,
            period_start=history.period_start,
            period_end=history.period_end,
            resident_id=history.resident_id,
            overall_summary=history.overall_summary,
            care_reference_count=history.care_reference_count,
            consultation_reference_count=history.consultation_reference_count,
            created_at=history.created_at,
        )
        for history in histories
        if _can_read_care_briefing_history(history, processor, db)
    ]


@app.get(
    "/api/workdesk/period-reviews/{history_id}",
    response_model=PeriodWorkdeskResponse,
)
def get_period_workdesk_review(
    history_id: UUID,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    history = db.get(FieldCareBriefingHistory, history_id)
    if (
        history is None
        or history.organization_id != processor.organization_id
        or (processor.role != "admin" and history.created_by_id != processor.id)
        or (
            getattr(processor, "_reviewer_experience", None) is not None
            and history.is_test_data is not True
        )
        or not _can_read_care_briefing_history(history, processor, db)
    ):
        raise HTTPException(status_code=404, detail="저장된 브리핑을 찾을 수 없습니다.")
    return PeriodWorkdeskResponse.model_validate(history.response_payload)


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
            raise HTTPException(
                status_code=403, detail="확인할 수 있는 채팅방이 없습니다."
            )
        room_query = room_query.join(
            RoomMembership, RoomMembership.room_id == Room.id
        ).where(
            RoomMembership.staff_id == processor.staff_id,
            RoomMembership.left_at.is_(None),
        )
    if getattr(processor, "_reviewer_experience", None) is not None:
        room_query = room_query.where(_reviewer_room_scope_filter(processor))
    visible_room_ids = list(db.scalars(room_query).unique().all())
    messages = list(
        db.scalars(
            select(Message)
            .where(
                Message.id.in_(selected_evidence_ids),
                Message.room_id.in_(visible_room_ids),
                Message.lifecycle_status == "active",
                *(
                    [Message.is_test_data.is_(True)]
                    if getattr(processor, "_reviewer_experience", None) is not None
                    else []
                ),
            )
            .order_by(Message.created_at, Message.id)
        )
        .unique()
        .all()
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
    selected_bodies_by_resident: dict[UUID, list[str]] = {}
    selected_draft_bodies_by_resident: dict[UUID, list[str]] = {}
    selected_evidence_by_resident: dict[UUID, list[UUID]] = {}
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
                            "어르신이 연결된 근거는 해당 어르신 항목에서 선택해 주세요."
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
                    draft_body = message.body.strip()
                else:
                    body = _resident_specific_period_text(
                        full_body,
                        target_name=resident_name,
                        resident_names=linked_resident_names,
                    )
                    draft_body = _resident_specific_period_text(
                        message.body.strip(),
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
            if selection.resident_id is not None:
                selected_bodies_by_resident.setdefault(selection.resident_id, []).append(
                    body
                )
                if draft_body:
                    selected_draft_bodies_by_resident.setdefault(
                        selection.resident_id, []
                    ).append(draft_body)
                selected_evidence_by_resident.setdefault(
                    selection.resident_id, []
                ).append(evidence_id)
    selected_tags = payload.record_usage_tags
    selected_document_candidate_types = payload.document_candidate_types
    purpose = " · ".join(
        CARE_DOCUMENT_CANDIDATE_LABELS[candidate_type]
        for candidate_type in selected_document_candidate_types
    ) or " · ".join(RECORD_USAGE_LABELS[tag] for tag in selected_tags)
    try:
        result = summarize_room_messages(
            entries=entries,
            external_allowed=all(message.is_test_data for message in messages),
            purpose=purpose,
            central_feature="document_text",
        )
    except LocalAiError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    summary_document_drafts: list[PeriodDocumentDraft] = []
    selected_daily_document_types = [
        candidate_type
        for candidate_type in selected_document_candidate_types
        if candidate_type in {"care_service_record", "consultation_log"}
    ]
    for selected_resident_id, selected_bodies in selected_bodies_by_resident.items():
        resident = residents_by_id[selected_resident_id]
        selected_source_ids = list(
            dict.fromkeys(selected_evidence_by_resident[selected_resident_id])
        )
        selected_text = "\n\n".join(
            selected_draft_bodies_by_resident.get(selected_resident_id, [])
        )
        selected_suggestion = RecordDraft.model_validate(
            build_prototype_suggestion(
                {
                    "body": selected_text,
                    "resident_name": resident.display_name,
                    "resident_names": [resident.display_name],
                }
            )
        )
        recorded_at = min(
            _as_utc(messages_by_id[source_id].created_at)
            for source_id in selected_source_ids
        ).astimezone(timezone(timedelta(hours=9))).strftime("%Y-%m-%d %H:%M")
        event_group_id = f"record-summary-{selected_resident_id.hex[:12]}"
        for document_type in selected_daily_document_types:
            proposal = build_document_proposal(
                document_type,
                resident_label=resident.display_name,
                text=selected_text,
                classification=selected_suggestion.classification,
                risk_level=selected_suggestion.risk_level,
                recorded_at=recorded_at,
                evidence_count=len(selected_source_ids),
            )
            summary_document_drafts.append(
                PeriodDocumentDraft(
                    key=f"{event_group_id}:{document_type}",
                    event_group_id=event_group_id,
                    resident_id=selected_resident_id,
                    resident_name=resident.display_name,
                    document_type=document_type,
                    status="needs_confirmation",
                    content=str(proposal["content"]),
                    draft_fields=dict(proposal.get("draft_fields", {})),
                    missing_fields=list(proposal.get("missing_fields", [])),
                    human_verification_fields=list(
                        proposal.get("human_verification_fields", [])
                    ),
                    verification_questions=list(
                        proposal.get("verification_questions", [])
                    ),
                    source_message_ids=selected_source_ids,
                )
            )
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
        record_usage_tag=selected_tags[0] if selected_tags else None,
        record_usage_tags=selected_tags,
        document_candidate_types=selected_document_candidate_types,
        summary=result.summary,
        evidence_ids=selected_evidence_ids,
        generator=f"{result.provider}:{result.model}",
        elapsed_ms=result.elapsed_ms,
        document_drafts=summary_document_drafts,
    )


@app.get("/api/work-items", response_model=list[WorkItemResponse])
def list_work_items(
    status_filter: str | None = None,
    review_queue_only: bool = False,
    processor: User = Depends(_require_processor),
    db: Session = Depends(get_db),
):
    items = _visible_work_items_for_processor(
        db, processor, status_filter=status_filter
    )
    suggestions_changed = False
    for item in items:
        suggestions_changed = (
            _refresh_work_item_suggestion(db, item) or suggestions_changed
        )
    if suggestions_changed:
        db.commit()
    if review_queue_only:
        items = [
            item
            for item in items
            if _work_item_requires_processor_review(db, item)
        ]
    return [_work_item_response(db, item, viewer_id=processor.id) for item in items]


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
    target_was_confirmed = target_link is not None and target_link.status == "confirmed"
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
    _require_work_item_attachment_reviews(db, item)
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
    _require_work_item_attachment_reviews(db, item)
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
            external_allowed=bool(
                item.is_test_data and item.source_message.is_test_data
            ),
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
    _require_work_item_attachment_reviews(db, item)
    current = db.scalar(
        select(WorkItemDocumentDraft).where(
            WorkItemDocumentDraft.work_item_id == item.id,
            WorkItemDocumentDraft.document_type == document_type,
            WorkItemDocumentDraft.is_current.is_(True),
        )
    )
    if current is None:
        raise HTTPException(
            status_code=404, detail="현재 서류 초안을 찾을 수 없습니다."
        )

    if payload.action == "direct_edit":
        if not payload.content:
            raise HTTPException(
                status_code=422, detail="수정한 서류 내용을 입력해 주세요."
            )
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
            raise HTTPException(
                status_code=422, detail="AI에게 요청할 변경 내용을 입력해 주세요."
            )
        base_payload = item.confirmed_payload or item.ai_payload
        if base_payload is None:
            raise HTTPException(
                status_code=422, detail="먼저 AI 업무 정리를 실행해 주세요."
            )
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
    _require_work_item_attachment_reviews(db, item)
    if item.confirmed_at is not None:
        raise HTTPException(status_code=409, detail="이미 최종 승인된 자료입니다.")
    if item.status == "dismissed":
        raise HTTPException(
            status_code=409,
            detail="사용 안 함 자료를 먼저 다시 확인 상태로 바꿔 주세요.",
        )
    if item.ai_payload is None:
        raise HTTPException(
            status_code=422, detail="먼저 AI 업무 정리를 실행해 주세요."
        )

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
    _require_work_item_attachment_reviews(db, item)
    if not work_item_review_current(item):
        raise HTTPException(409, "근거의 직원 확인 상태가 변경되었습니다. 확인한 자료로 업무초안을 다시 만들어 주세요.")
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
        if draft.document_type in payload.document_types and draft.status != "not_used"
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
    try:
        confirmed_records = persist_confirmed_records_from_work_item(
            db,
            work_item=item,
            confirmed_payload=item.confirmed_payload,
            confirmed_by_user_id=processor.id,
        )
    except ConfirmedRecordError as exc:
        raise HTTPException(
            status_code=(
                409
                if exc.code
                in {"recalled_source_message", "attachment_staff_review_required"}
                else 422
            ),
            detail=str(exc),
        ) from exc
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
            "confirmed_record_ids": [
                str(result.record.id) for result in confirmed_records
            ],
            "confirmed_record_versions": [
                result.version.version for result in confirmed_records
            ],
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
        raise HTTPException(
            status_code=409, detail="승인 완료된 업무만 다시 수정할 수 있습니다."
        )
    if processor.role != "admin" and item.confirmed_by_id != processor.id:
        raise HTTPException(
            status_code=403,
            detail="최종 승인자 또는 관리자만 승인을 취소할 수 있습니다.",
        )

    previous_confirmed_at = item.confirmed_at
    previous_confirmed_by_id = item.confirmed_by_id
    previous_payload = dict(item.confirmed_payload)
    editable_payload = RecordDraft.model_validate(previous_payload).model_dump(
        mode="json"
    )
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


def _websocket_auth(
    websocket: WebSocket, db: Session
) -> tuple[LoginSession, User] | None:
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
        or user.staff is None
        or not user.staff.is_active
        or user.employment_status != "active"
        or user.must_change_password
    ):
        if (
            login_session is not None
            and user is not None
            and user.employment_status != "active"
        ):
            login_session.revoked_at = now
            db.commit()
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


VOICE_CALL_CLIENT_EVENTS = {
    "voice_call_invite",
    "voice_call_join",
    "voice_call_signal",
    "voice_call_decline",
    "voice_call_cancel",
    "voice_call_leave",
    "voice_call_mode",
    "voice_call_delivery_ack",
}
VOICE_CALL_SIGNAL_MAX_BYTES = 128 * 1024
VOICE_CALL_MODES = {"audio", "video"}
# WebSocket 연결 중 빠른 조회를 위한 보조 캐시다. 정답 상태는 DB에 두어
# Backend 프로세스가 재시작되거나 Android 앱이 종료돼도 미응답 초대를
# 다시 조회할 수 있게 한다.
VOICE_CALL_PARTICIPANTS: dict[UUID, set[UUID]] = {}
VOICE_CALL_INVITE_TTL = timedelta(seconds=50)
VOICE_CALL_WEBSOCKET_TIMEOUT_SECONDS = 2.0
VOICE_CALL_BROWSER_PUSH_TIMEOUT_SECONDS = 12.0
VOICE_CALL_ANDROID_PUSH_TIMEOUT_SECONDS = 8.0
VOICE_CALL_DELIVERY = CallDeliveryCoordinator()


async def _voice_call_error(
    websocket: WebSocket,
    code: str,
    message: str,
    *,
    source_event: str | None = None,
) -> None:
    payload = {
        "event": "voice_call_error",
        "code": code,
        "message": message,
    }
    if source_event:
        payload["source_event"] = source_event
    await websocket.send_json(payload)


def _record_voice_call_delivery_result(
    *,
    call_id: UUID,
    actor_id: UUID,
    result: DeliveryResult,
) -> None:
    path = str(result.key[2]) if len(result.key) > 2 else "unknown"
    event_kind = str(result.key[3]) if len(result.key) > 3 else "unknown"
    with SessionLocal() as audit_db:
        if audit_db.get(VoiceCallInvitation, call_id) is None:
            return
        record_audit(
            audit_db,
            actor_id=actor_id,
            action="voice_call.delivery",
            target_type="voice_call",
            target_id=call_id,
            details={
                "path": path,
                "event_kind": event_kind,
                "status": result.status,
                "elapsed_ms": result.elapsed_ms,
                "delivered_count": result.delivered_count,
                "error_type": result.error_type,
            },
        )
        audit_db.commit()


def _schedule_voice_call_delivery(
    *,
    call_id: UUID,
    room_id: UUID,
    caller_user_id: UUID,
    caller_name: str,
    recipient_user_id: UUID,
    call_mode: str,
    member_count: int,
    mobile_action_token: str,
    expires_at_ms: int,
    websocket_payload: dict[str, Any] | None,
    cancelled: bool = False,
) -> None:
    event_kind = "cancel" if cancelled else "invite"
    common = {
        "call_id": call_id,
        "room_id": room_id,
        "caller_user_id": caller_user_id,
        "caller_name": caller_name,
        "call_mode": call_mode,
        "member_count": member_count,
        "cancelled": cancelled,
        "expires_at_ms": expires_at_ms,
    }
    mobile_payload = build_voice_call_mobile_payload(
        **common,
        mobile_action_token=mobile_action_token,
    )
    remaining_ttl = (
        60 if cancelled else voice_call_ttl_seconds(expires_at_ms)
    )

    path_results: dict[str, DeliveryResult] = {}
    expected_paths = 3 if websocket_payload is not None else 2

    def result_handler(result: DeliveryResult) -> None:
        _record_voice_call_delivery_result(
            call_id=call_id,
            actor_id=caller_user_id,
            result=result,
        )
        path_results[str(result.key[2])] = result
        if cancelled or len(path_results) != expected_paths:
            return
        delivered = any(item.delivered_count > 0 for item in path_results.values())
        VOICE_CALL_DELIVERY.schedule(
            (call_id, caller_user_id, "status", recipient_user_id),
            lambda: manager.send_to_users(
                {caller_user_id},
                {
                    "event": "voice_call_delivery_status",
                    "call_id": str(call_id),
                    "delivery_complete": True,
                    "delivered": delivered,
                    "path_statuses": {
                        name: item.status
                        for name, item in sorted(path_results.items())
                    },
                },
            ),
            timeout_seconds=VOICE_CALL_WEBSOCKET_TIMEOUT_SECONDS,
        )

    if websocket_payload is not None:
        VOICE_CALL_DELIVERY.schedule(
            (call_id, recipient_user_id, "websocket", event_kind),
            lambda: manager.send_to_users({recipient_user_id}, websocket_payload),
            timeout_seconds=VOICE_CALL_WEBSOCKET_TIMEOUT_SECONDS,
            expires_at_ms=None if cancelled else expires_at_ms,
            on_result=result_handler,
        )
    VOICE_CALL_DELIVERY.schedule(
        (call_id, recipient_user_id, "android", event_kind),
        lambda: asyncio.to_thread(
            send_voice_call_mobile_push,
            {recipient_user_id},
            payload=mobile_payload,
            ttl_seconds=remaining_ttl,
        ),
        timeout_seconds=VOICE_CALL_ANDROID_PUSH_TIMEOUT_SECONDS,
        expires_at_ms=None if cancelled else expires_at_ms,
        on_result=result_handler,
    )
    VOICE_CALL_DELIVERY.schedule(
        (call_id, recipient_user_id, "browser", event_kind),
        lambda: asyncio.to_thread(
            send_voice_call_web_push,
            {recipient_user_id},
            **common,
        ),
        timeout_seconds=VOICE_CALL_BROWSER_PUSH_TIMEOUT_SECONDS,
        expires_at_ms=None if cancelled else expires_at_ms,
        on_result=result_handler,
    )


def _voice_call_uuid(payload: dict[str, Any], field: str) -> UUID:
    try:
        return UUID(str(payload.get(field, "")))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError(f"{field} 값이 올바르지 않습니다.") from exc


def _voice_call_mode(payload: dict[str, Any]) -> str:
    call_mode = payload.get("call_mode", "audio")
    if call_mode not in VOICE_CALL_MODES:
        raise ValueError("통화 종류가 올바르지 않습니다.")
    return str(call_mode)


def _voice_call_signal(payload: dict[str, Any]) -> dict[str, Any]:
    signal = payload.get("signal")
    if not isinstance(signal, dict):
        raise ValueError("통화 연결정보가 없습니다.")
    try:
        encoded = json.dumps(signal, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("통화 연결정보 형식이 올바르지 않습니다.") from exc
    if len(encoded) > VOICE_CALL_SIGNAL_MAX_BYTES:
        raise ValueError("통화 연결정보가 너무 큽니다.")

    kind = signal.get("kind")
    if kind in {"offer", "answer"}:
        sdp = signal.get("sdp")
        if not isinstance(sdp, str) or not sdp or len(sdp) > 120_000:
            raise ValueError("통화 설명정보가 올바르지 않습니다.")
        return {"kind": kind, "sdp": sdp}
    if kind == "ice":
        candidate = signal.get("candidate")
        if not isinstance(candidate, dict):
            raise ValueError("통화 경로정보가 올바르지 않습니다.")
        candidate_text = candidate.get("candidate")
        if not isinstance(candidate_text, str) or len(candidate_text) > 8_000:
            raise ValueError("통화 경로정보가 올바르지 않습니다.")
        normalized: dict[str, Any] = {"candidate": candidate_text}
        sdp_mid = candidate.get("sdpMid")
        if sdp_mid is not None:
            if not isinstance(sdp_mid, str) or len(sdp_mid) > 200:
                raise ValueError("통화 경로정보가 올바르지 않습니다.")
            normalized["sdpMid"] = sdp_mid
        sdp_line = candidate.get("sdpMLineIndex")
        if sdp_line is not None:
            if not isinstance(sdp_line, int) or not 0 <= sdp_line <= 1_000:
                raise ValueError("통화 경로정보가 올바르지 않습니다.")
            normalized["sdpMLineIndex"] = sdp_line
        username_fragment = candidate.get("usernameFragment")
        if username_fragment is not None:
            if not isinstance(username_fragment, str) or len(username_fragment) > 300:
                raise ValueError("통화 경로정보가 올바르지 않습니다.")
            normalized["usernameFragment"] = username_fragment
        return {"kind": "ice", "candidate": normalized}
    raise ValueError("지원하지 않는 통화 연결정보입니다.")


def _voice_call_room_members(
    db: Session,
    *,
    room_id: UUID,
    user: User,
    call_mode: str,
) -> tuple[Room, set[UUID]]:
    if active_membership(db, user.id, room_id) is None:
        raise ValueError("이 대화방에서는 통화할 수 없습니다.")
    room = db.get(Room, room_id)
    if room is None or not room.is_active or room.kind != "custom":
        raise ValueError("이 대화방에서는 통화할 수 없습니다.")
    member_ids = set(
        db.scalars(
            select(User.id)
            .join(Staff, Staff.id == User.staff_id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room_id,
                RoomMembership.left_at.is_(None),
                User.is_active.is_(True),
                Staff.is_active.is_(True),
                Staff.employment_status == "active",
            )
        ).all()
    )
    if user.id not in member_ids:
        raise ValueError("이 대화방에서는 통화할 수 없습니다.")
    if len(member_ids) < 2:
        raise ValueError("통화할 다른 직원이 없습니다.")
    return room, member_ids


def _voice_call_selected_participants(
    payload: dict[str, Any],
    *,
    caller_id: UUID,
    room_member_ids: set[UUID],
    call_mode: str,
) -> set[UUID]:
    raw_recipient_ids = payload.get("recipient_user_ids")
    if raw_recipient_ids is None:
        recipient_ids = room_member_ids - {caller_id}
    else:
        if not isinstance(raw_recipient_ids, list):
            raise ValueError("통화 대상을 다시 선택해 주세요.")
        if len(raw_recipient_ids) > 50:
            raise ValueError("한 번에 선택할 수 있는 통화 대상이 너무 많습니다.")
        try:
            recipient_ids = {
                UUID(str(recipient_id)) for recipient_id in raw_recipient_ids
            }
        except (TypeError, ValueError, AttributeError) as exc:
            raise ValueError("통화 대상이 올바르지 않습니다.") from exc
    if not recipient_ids:
        raise ValueError("통화할 직원을 한 명 이상 선택해 주세요.")
    if caller_id in recipient_ids or not recipient_ids.issubset(room_member_ids):
        raise ValueError("이 대화방의 직원만 통화 대상으로 선택할 수 있습니다.")
    participant_ids = recipient_ids | {caller_id}
    max_participants = (
        settings.voice_call_max_video_participants
        if call_mode == "video"
        else settings.voice_call_max_participants
    )
    if len(participant_ids) > max_participants:
        call_label = "영상통화" if call_mode == "video" else "음성통화"
        raise ValueError(
            f"{call_label}는 발신자를 포함해 {max_participants}명까지 가능합니다. "
            "통화할 대상을 줄여 주세요."
        )
    return participant_ids


def _voice_call_token_hash(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def _voice_call_participant_for_action_token(
    db: Session,
    *,
    call_id: UUID,
    action_token: str | None,
) -> VoiceCallParticipant | None:
    if not action_token:
        return None
    supplied_hash = _voice_call_token_hash(action_token)
    return next(
        (
            candidate
            for candidate in db.scalars(
                select(VoiceCallParticipant).where(
                    VoiceCallParticipant.call_id == call_id,
                    VoiceCallParticipant.action_token_hash.is_not(None),
                )
            ).all()
            if candidate.action_token_hash is not None
            and hmac.compare_digest(candidate.action_token_hash, supplied_hash)
        ),
        None,
    )


def _expire_voice_call_invites(db: Session, *, now: datetime | None = None) -> None:
    checked_at = _as_utc(now or utcnow())
    invitations = db.scalars(
        select(VoiceCallInvitation).where(
            VoiceCallInvitation.state == "ringing",
            VoiceCallInvitation.expires_at <= checked_at,
        )
    ).all()
    if not invitations:
        return
    call_ids = {invitation.id for invitation in invitations}
    participants = db.scalars(
        select(VoiceCallParticipant).where(
            VoiceCallParticipant.call_id.in_(call_ids),
            VoiceCallParticipant.state == "ringing",
        )
    ).all()
    for invitation in invitations:
        invitation.state = "timeout"
        invitation.ended_at = checked_at
    for participant in participants:
        participant.state = "timeout"
        participant.responded_at = checked_at
    db.commit()


def _persist_voice_call_invitation(
    db: Session,
    *,
    call_id: UUID,
    room: Room,
    caller: User,
    call_mode: str,
    participant_ids: set[UUID],
) -> dict[UUID, str]:
    if db.get(VoiceCallInvitation, call_id) is not None:
        raise ValueError("이미 시작한 통화 요청입니다.")
    now = utcnow()
    invitation = VoiceCallInvitation(
        id=call_id,
        organization_id=caller.organization_id,
        room_id=room.id,
        caller_user_id=caller.id,
        call_mode=call_mode,
        state="ringing",
        member_count=len(participant_ids),
        expires_at=now + VOICE_CALL_INVITE_TTL,
    )
    db.add(invitation)
    action_tokens: dict[UUID, str] = {}
    for participant_id in participant_ids:
        is_caller = participant_id == caller.id
        action_token = None if is_caller else secrets.token_urlsafe(32)
        if action_token is not None:
            action_tokens[participant_id] = action_token
        db.add(
            VoiceCallParticipant(
                organization_id=caller.organization_id,
                call_id=call_id,
                user_id=participant_id,
                participant_role="caller" if is_caller else "recipient",
                state="joined" if is_caller else "ringing",
                action_token_hash=(
                    _voice_call_token_hash(action_token)
                    if action_token is not None
                    else None
                ),
            )
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise ValueError("이미 시작한 통화 요청입니다.") from exc
    return action_tokens


def _voice_call_db_context(
    db: Session,
    call_id: UUID,
) -> tuple[VoiceCallInvitation, list[VoiceCallParticipant], set[UUID]]:
    _expire_voice_call_invites(db)
    invitation = db.get(VoiceCallInvitation, call_id)
    if invitation is None:
        raise ValueError("통화 요청을 찾을 수 없습니다.")
    participants = db.scalars(
        select(VoiceCallParticipant).where(
            VoiceCallParticipant.call_id == call_id
        )
    ).all()
    participant_ids = {participant.user_id for participant in participants}
    return invitation, participants, participant_ids


@app.get("/api/voice-calls/pending")
def pending_voice_calls(
    response: Response,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    response.headers["Cache-Control"] = "private, no-store"
    _expire_voice_call_invites(db)
    rows = db.execute(
        select(VoiceCallInvitation, VoiceCallParticipant, User, Room)
        .join(
            VoiceCallParticipant,
            VoiceCallParticipant.call_id == VoiceCallInvitation.id,
        )
        .join(User, User.id == VoiceCallInvitation.caller_user_id)
        .join(Room, Room.id == VoiceCallInvitation.room_id)
        .where(
            VoiceCallInvitation.organization_id == user.organization_id,
            VoiceCallInvitation.state == "ringing",
            VoiceCallParticipant.user_id == user.id,
            VoiceCallParticipant.state == "ringing",
            VoiceCallInvitation.expires_at > utcnow(),
        )
        .order_by(VoiceCallInvitation.created_at.desc())
    ).all()
    return [
        {
            "call_id": str(invitation.id),
            "call_state": invitation.state,
            "room_id": str(invitation.room_id),
            "room_name": room.name,
            "caller_user_id": str(invitation.caller_user_id),
            "caller_name": caller.full_name,
            "call_mode": invitation.call_mode,
            "member_count": invitation.member_count,
            "expires_at": _as_utc(invitation.expires_at).isoformat(),
        }
        for invitation, _participant, caller, room in rows
    ]


@app.post("/api/voice-calls/{call_id}/native-decline", status_code=204)
def native_decline_voice_call(
    call_id: UUID,
    background_tasks: BackgroundTasks,
    action_token: Annotated[
        str | None,
        Header(alias="X-Mesil-Call-Action"),
    ] = None,
    db: Session = Depends(get_db),
):
    participant = _voice_call_participant_for_action_token(
        db, call_id=call_id, action_token=action_token
    )
    if participant is None:
        raise HTTPException(status_code=403, detail="통화 거절 요청을 확인할 수 없습니다.")
    invitation = db.get(VoiceCallInvitation, call_id)
    if invitation is None:
        raise HTTPException(status_code=403, detail="통화 거절 요청을 확인할 수 없습니다.")
    if participant.state in {"declined", "cancelled", "timeout", "ended"}:
        # Android 알림을 연속해서 누르거나 네트워크가 재전송해도 이미 끝난
        # 거절을 다시 중계하지 않는다.
        return Response(status_code=204)
    now = utcnow()
    participant.state = "declined"
    participant.responded_at = now
    # SessionLocal은 autoflush=False다. 방금 바꾼 수신자 상태를 먼저 DB에
    # 반영하지 않으면 아래 조회가 본인을 여전히 ringing으로 읽어 초대가
    # 영구히 울리는 상태로 남는다.
    db.flush()
    active_recipient = db.scalar(
        select(VoiceCallParticipant.id).where(
            VoiceCallParticipant.call_id == call_id,
            VoiceCallParticipant.participant_role == "recipient",
            VoiceCallParticipant.state.in_({"ringing", "accepted"}),
        )
    )
    if active_recipient is None:
        invitation.state = "declined"
        invitation.ended_at = now
    db.commit()
    caller = db.get(User, invitation.caller_user_id)
    participant_user = db.get(User, participant.user_id)
    notify_user_ids = {
        user_id
        for user_id in db.scalars(
            select(VoiceCallParticipant.user_id).where(
                VoiceCallParticipant.call_id == call_id,
                VoiceCallParticipant.user_id != participant.user_id,
            )
        ).all()
    }
    if notify_user_ids:
        room = db.get(Room, invitation.room_id)
        background_tasks.add_task(
            manager.send_to_users,
            notify_user_ids,
            {
                "event": "voice_call_decline",
                "call_id": str(invitation.id),
                "room_id": str(invitation.room_id),
                "room_name": room.name if room is not None else "대화방",
                "call_mode": invitation.call_mode,
                "participant_user_id": str(participant.user_id),
                "participant_name": (
                    participant_user.full_name
                    if participant_user is not None
                    else "직원"
                ),
            },
        )
    cancel_kwargs = {
        "call_id": invitation.id,
        "room_id": invitation.room_id,
        "caller_user_id": invitation.caller_user_id,
        "caller_name": caller.full_name if caller is not None else "직원",
        "call_mode": invitation.call_mode,
        "member_count": invitation.member_count,
        "cancelled": True,
        "expires_at_ms": int(_as_utc(invitation.expires_at).timestamp() * 1000),
    }
    background_tasks.add_task(
        send_voice_call_mobile_push,
        {participant.user_id},
        payload=build_voice_call_mobile_payload(
            **cancel_kwargs, mobile_action_token=action_token
        ),
    )
    background_tasks.add_task(
        send_voice_call_web_push,
        {participant.user_id},
        **cancel_kwargs,
    )
    return Response(status_code=204)


@app.post("/api/voice-calls/{call_id}/delivery-receipt", status_code=204)
def record_native_voice_call_delivery_receipt(
    call_id: UUID,
    payload: VoiceCallDeliveryReceiptRequest,
    action_token: Annotated[str | None, Header(alias="X-Mesil-Call-Action")] = None,
    db: Session = Depends(get_db),
):
    participant = _voice_call_participant_for_action_token(
        db, call_id=call_id, action_token=action_token
    )
    invitation = db.get(VoiceCallInvitation, call_id)
    if participant is None or invitation is None:
        raise HTTPException(status_code=403, detail="통화 수신 확인 요청을 확인할 수 없습니다.")
    if _as_utc(invitation.expires_at) + timedelta(minutes=2) <= utcnow():
        raise HTTPException(status_code=410, detail="통화 수신 확인 시간이 지났습니다.")
    existing = list(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "voice_call.delivery_receipt",
                AuditEvent.target_id == call_id,
                AuditEvent.actor_id == participant.user_id,
            )
        )
    )
    if any(
        (row.details or {}).get("stage") == payload.stage
        and (row.details or {}).get("device_hash") == payload.device_hash
        for row in existing
    ):
        return Response(status_code=204)
    # One invite creates at most four Android stages per device. Keep a small
    # hard cap so a leaked short-lived action token cannot grow audit storage.
    if len(existing) >= 20:
        return Response(status_code=204)
    record_audit(
        db,
        actor_id=participant.user_id,
        action="voice_call.delivery_receipt",
        target_type="voice_call",
        target_id=call_id,
        details={
            "stage": payload.stage,
            "success": payload.success,
            "error_type": payload.error_type,
            "elapsed_ms": payload.elapsed_ms,
            "device_hash": payload.device_hash,
        },
    )
    db.commit()
    return Response(status_code=204)


async def _handle_voice_call_websocket(
    *,
    websocket: WebSocket,
    payload: dict[str, Any],
    user: User,
    db: Session,
) -> bool:
    event = payload.get("event")
    if event not in VOICE_CALL_CLIENT_EVENTS:
        return False
    if not settings.voice_call_enabled:
        await _voice_call_error(
            websocket, "disabled", "통화 서버가 준비되지 않았습니다."
        )
        return True
    if getattr(user, "_reviewer_experience", None) is not None:
        await _voice_call_error(
            websocket, "reviewer_blocked", "심사 체험에서는 통화를 사용할 수 없습니다."
        )
        return True

    try:
        call_id = _voice_call_uuid(payload, "call_id")
        room_id = _voice_call_uuid(payload, "room_id")
        call_mode = _voice_call_mode(payload)
        db.expire_all()
        room, member_ids = _voice_call_room_members(
            db,
            room_id=room_id,
            user=user,
            call_mode=call_mode,
        )
        action_tokens: dict[UUID, str] = {}
        invitation: VoiceCallInvitation | None = None
        participant_rows: list[VoiceCallParticipant] = []
        if event == "voice_call_invite":
            participant_ids = _voice_call_selected_participants(
                payload,
                caller_id=user.id,
                room_member_ids=member_ids,
                call_mode=call_mode,
            )
            action_tokens = _persist_voice_call_invitation(
                db,
                call_id=call_id,
                room=room,
                caller=user,
                call_mode=call_mode,
                participant_ids=participant_ids,
            )
            VOICE_CALL_PARTICIPANTS[call_id] = participant_ids
            invitation = db.get(VoiceCallInvitation, call_id)
            if invitation is None:
                raise ValueError("통화 요청을 저장하지 못했습니다.")
            record_audit(
                db,
                actor_id=user.id,
                action="voice_call.created",
                target_type="voice_call",
                target_id=call_id,
                details={
                    "call_mode": call_mode,
                    "recipient_count": len(participant_ids - {user.id}),
                    "expires_at": _as_utc(invitation.expires_at).isoformat(),
                },
            )
            db.commit()
        else:
            invitation, participant_rows, participant_ids = _voice_call_db_context(
                db, call_id
            )
            if invitation.organization_id != user.organization_id:
                raise ValueError("통화 요청을 찾을 수 없습니다.")
            if invitation.room_id != room_id:
                raise ValueError("통화 대화방이 올바르지 않습니다.")
            allow_caller_cancel_after_leave = (
                event == "voice_call_cancel"
                and invitation.state == "ended"
                and invitation.caller_user_id == user.id
            )
            if (
                invitation.state in {"cancelled", "declined", "timeout", "ended"}
                and not allow_caller_cancel_after_leave
            ):
                raise ValueError("이미 종료된 통화 요청입니다.")
            if user.id not in participant_ids:
                raise ValueError("선택된 통화 참여자가 아닙니다.")
            if event != "voice_call_mode":
                call_mode = invitation.call_mode
            VOICE_CALL_PARTICIPANTS[call_id] = participant_ids
        common = {
            "call_id": str(call_id),
            "room_id": str(room_id),
            "room_name": room.name,
            "call_mode": call_mode,
        }
        if event == "voice_call_delivery_ack":
            participant_row = next(
                (row for row in participant_rows if row.user_id == user.id),
                None,
            )
            if participant_row is None or participant_row.participant_role != "recipient":
                raise ValueError("통화 수신 확인 권한이 없습니다.")
            duplicate_web_receipt = db.scalar(
                select(AuditEvent.id).where(
                    AuditEvent.action == "voice_call.delivery_receipt",
                    AuditEvent.target_id == call_id,
                    AuditEvent.actor_id == user.id,
                    AuditEvent.details["stage"].as_string() == "web_ready",
                )
            )
            if duplicate_web_receipt is None:
                record_audit(
                    db,
                    actor_id=user.id,
                    action="voice_call.delivery_receipt",
                    target_type="voice_call",
                    target_id=call_id,
                    details={"stage": "web_ready", "success": True},
                )
                db.commit()
            return True
        if event == "voice_call_invite":
            recipient_ids = participant_ids - {user.id}
            expires_at_ms = int(_as_utc(invitation.expires_at).timestamp() * 1000)
            for recipient_id in recipient_ids:
                _schedule_voice_call_delivery(
                    call_id=call_id,
                    room_id=room_id,
                    caller_user_id=user.id,
                    caller_name=user.full_name,
                    recipient_user_id=recipient_id,
                    call_mode=call_mode,
                    member_count=len(participant_ids),
                    mobile_action_token=action_tokens[recipient_id],
                    expires_at_ms=expires_at_ms,
                    websocket_payload={
                    "event": "voice_call_invite",
                    **common,
                    "caller_user_id": str(user.id),
                    "caller_name": user.full_name,
                    "member_count": len(participant_ids),
                    "expires_at": expires_at_ms,
                    },
                )
            await websocket.send_json(
                {
                    "event": "voice_call_started",
                    **common,
                    "member_count": len(participant_ids),
                }
            )
            return True
        if event == "voice_call_signal":
            target_user_id = _voice_call_uuid(payload, "target_user_id")
            if target_user_id == user.id or target_user_id not in participant_ids:
                raise ValueError("통화 연결대상이 올바르지 않습니다.")
            await manager.send_to_users(
                {target_user_id},
                {
                    "event": "voice_call_signal",
                    **common,
                    "sender_user_id": str(user.id),
                    "sender_name": user.full_name,
                    "signal": _voice_call_signal(payload),
                },
            )
            return True

        now = utcnow()
        participant_row = next(
            (row for row in participant_rows if row.user_id == user.id),
            None,
        )
        if invitation is None or participant_row is None:
            raise ValueError("통화 참여 상태를 확인할 수 없습니다.")
        if event == "voice_call_join":
            cancel_action_token = secrets.token_urlsafe(32)
            participant_row.action_token_hash = _voice_call_token_hash(
                cancel_action_token
            )
            participant_row.state = "accepted"
            participant_row.responded_at = now
            invitation.state = "accepted"
            invitation.accepted_at = invitation.accepted_at or now
            db.commit()
            # 같은 사용자의 다른 Android 기기에 남아 있는 초대 알림도 닫는다.
            _schedule_voice_call_delivery(
                call_id=call_id,
                room_id=room_id,
                caller_user_id=invitation.caller_user_id,
                caller_name="직원",
                recipient_user_id=user.id,
                call_mode=call_mode,
                member_count=len(participant_ids),
                mobile_action_token=cancel_action_token,
                expires_at_ms=int(_as_utc(invitation.expires_at).timestamp() * 1000),
                websocket_payload=None,
                cancelled=True,
            )
        elif event == "voice_call_decline":
            participant_row.state = "declined"
            participant_row.responded_at = now
            if not any(
                row.participant_role == "recipient"
                and row.id != participant_row.id
                and row.state in {"ringing", "accepted"}
                for row in participant_rows
            ):
                invitation.state = "declined"
                invitation.ended_at = now
            db.commit()
        elif event == "voice_call_cancel":
            if invitation.caller_user_id != user.id:
                raise ValueError("통화 발신자만 요청을 취소할 수 있습니다.")
            cancel_action_tokens: dict[UUID, str] = {}
            invitation.state = "cancelled"
            invitation.ended_at = now
            for row in participant_rows:
                if row.user_id != user.id:
                    cancel_action_token = secrets.token_urlsafe(32)
                    cancel_action_tokens[row.user_id] = cancel_action_token
                    row.action_token_hash = _voice_call_token_hash(
                        cancel_action_token
                    )
                if row.state == "ringing":
                    row.state = "cancelled"
                    row.responded_at = now
            db.commit()
        elif event == "voice_call_leave":
            participant_row.state = "ended"
            participant_row.responded_at = now
            if invitation.caller_user_id == user.id or not any(
                row.id != participant_row.id
                and row.state in {"ringing", "accepted", "joined"}
                for row in participant_rows
            ):
                invitation.state = "ended"
                invitation.ended_at = now
            db.commit()
        elif event == "voice_call_mode":
            invitation.call_mode = call_mode
            db.commit()

        outbound_event = {
            "voice_call_join": "voice_call_join",
            "voice_call_decline": "voice_call_decline",
            "voice_call_cancel": "voice_call_cancel",
            "voice_call_leave": "voice_call_leave",
            "voice_call_mode": "voice_call_mode",
        }[event]
        outbound_payload = {
            "event": outbound_event,
            **common,
            "participant_user_id": str(user.id),
            "participant_name": user.full_name,
        }
        if event == "voice_call_cancel":
            for recipient_id in participant_ids - {user.id}:
                _schedule_voice_call_delivery(
                    call_id=call_id,
                    room_id=room_id,
                    caller_user_id=user.id,
                    caller_name=user.full_name,
                    recipient_user_id=recipient_id,
                    call_mode=call_mode,
                    member_count=len(participant_ids),
                    mobile_action_token=cancel_action_tokens[recipient_id],
                    expires_at_ms=int(_as_utc(invitation.expires_at).timestamp() * 1000),
                    websocket_payload=outbound_payload,
                    cancelled=True,
                )
            VOICE_CALL_PARTICIPANTS.pop(call_id, None)
        else:
            await manager.send_to_users(participant_ids - {user.id}, outbound_payload)
        return True
    except ValueError as exc:
        await _voice_call_error(
            websocket,
            "invalid_call",
            str(exc),
            source_event=str(event),
        )
        return True


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
            expires_in = (_as_utc(login_session.expires_at) - utcnow()).total_seconds()
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
                continue
            await _handle_voice_call_websocket(
                websocket=websocket,
                payload=payload,
                user=user,
                db=db,
            )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(user_id, login_session.id, websocket)
        db.close()


# Explicit photo and resident decisions share the existing permission and audit contracts.
from .resident_review_flow import router as resident_review_router
app.include_router(resident_review_router)
