from __future__ import annotations

from datetime import date, datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
import re
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .config import settings
from .attachment_review_policy import attachment_evidence_text, requires_staff_review, has_staff_review
from .resident_candidate_evidence import candidate_evidence, resident_link_is_current, visible_image_text, current_message_resident
from .photo_reading import photo_reading_status, resident_review_metadata
from .finalist_coded_synthetic import STAFF_CODE_RE
from .ocr import find_spelling_candidates, is_pathological_handwriting_output
from .ocr_corrections import (
    CorrectionEvidence,
    PROTECTED_CONTENT_TYPES,
    build_ocr_review_warnings,
    classify_content_type,
    correction_candidate_id,
    suggest_from_confirmed_events,
)
from .models import (
    ActionItem,
    AuditEvent,
    DomainModule,
    Message,
    MessageAttachment,
    MessageComment,
    MessageReadReceipt,
    MessageResidentLink,
    MessageThreadView,
    LoginSession,
    OcrCorrectionEvent,
    Organization,
    OrgUnit,
    Resident,
    Role,
    Room,
    RoomMembership,
    RoomMembershipOverride,
    Staff,
    StaffJobAssignment,
    StaffJobCode,
    StaffOrganizationAssignment,
    StaffPositionCode,
    StaffServiceAssignment,
    User,
    WorkItem,
    utcnow,
)
from .organization_catalog import ensure_approved_organization_catalog
from .schemas import (
    ActionItemResponse,
    AiHelpMessageMeta,
    AttachmentResponse,
    AttachmentTextExtractionAttemptResponse,
    AttachmentTextExtractionResponse,
    MessageCommentResponse,
    MessageResidentLinkResponse,
    MessageResponse,
    ForwardedMessageSource,
    RepliedMessageSource,
    LOGIN_USERNAME_PATTERN,
    OrgUnitResponse,
    ResidentResponse,
    RoomResponse,
    StaffDirectoryResponse,
    StaffLegacyAssignmentResponse,
    StaffServiceAssignmentResponse,
    UserResponse,
)
from .security import (
    MENTOR_FULL_REVIEW_EXPERIENCE,
    hash_password,
    is_mentor_full_reviewer,
)


AUTO_UNIT_FIELDS = {
    "business": "business_id",
    "department": "department_id",
    "floor": "floor_id",
    "team": "team_id",
}
SERVICE_ASSIGNMENT_UNIT_FIELDS = {
    "business": "business_unit_id",
    "department": "department_unit_id",
    "floor": "floor_unit_id",
    "team": "team_unit_id",
}
ROOM_LABELS = {
    "business": "전체방",
    "department": "전체방",
    "floor": "직원방",
    "team": "방",
}
USER_UNIT_FIELDS = {field_name: unit_type for unit_type, field_name in AUTO_UNIT_FIELDS.items()}


def _finalist_coded_staff_identity(values: dict) -> dict:
    """Keep every newly created finalist DEV staff identity code-only."""

    if settings.environment != "development" or settings.database_schema != "smcodi_finalist":
        return values
    normalized = dict(values)
    full_name = str(normalized.get("full_name") or "").strip()
    if not STAFF_CODE_RE.fullmatch(full_name):
        raise HTTPException(
            status_code=422,
            detail="본선 DEV에서는 직원 이름 대신 직군 코드와 두 자리 번호를 입력해 주세요.",
        )
    employee_code = str(normalized.get("employee_code") or full_name).strip()
    if employee_code != full_name:
        raise HTTPException(
            status_code=422,
            detail="본선 DEV의 직원 표시 코드와 직원번호는 같아야 합니다.",
        )
    normalized["full_name"] = full_name
    normalized["employee_code"] = full_name
    return normalized

DEFAULT_JOB_CODES = [
    ("social_worker", "사회복지사"),
    ("registered_nurse", "간호사"),
    ("nursing_assistant", "간호조무사"),
    ("physical_therapist", "물리치료사"),
    ("occupational_therapist", "작업치료사"),
    ("caregiver", "요양보호사"),
    ("dietitian", "영양사"),
    ("cook", "조리원"),
    ("office_worker", "사무원"),
    ("sanitation_worker", "위생원"),
    ("maintenance_worker", "관리인"),
    ("driver_assistant", "보조원(운전사)"),
    ("other", "기타"),
    ("contract_doctor", "계약의사"),
    ("facility_director", "시설장"),
]

DEFAULT_POSITION_TITLES = [
    ("representative", "대표"),
    ("office_director", "사무국장"),
    ("senior_social_worker", "선임사회복지사"),
    ("nursing_team_lead", "간호팀장"),
    ("care_team_lead", "요양팀장"),
]

AUTOMATIC_ATTACHMENT_MESSAGE_BODIES = frozenset(
    {
        "파일을 첨부했습니다.",
        "보고서 이미지를 첨부했습니다.",
    }
)
VALID_RESIDENT_SERVICE_CONTEXTS = frozenset({"facility", "daycare", "homecare"})


def _handwriting_preprocessing_summary(
    details: list[dict[str, object]] | None,
) -> dict[str, object] | None:
    for item in reversed(details or []):
        if item.get("kind") == "handwriting_preprocessing":
            return {
                key: value
                for key, value in item.items()
                if key != "kind"
            }
    return None


def _message_resident_service_context(message: Message) -> str | None:
    if (
        message.resident is not None
        and message.resident.service_type in VALID_RESIDENT_SERVICE_CONTEXTS
    ):
        return message.resident.service_type
    scope = message.room.resident_scope if message.room is not None else None
    if scope in VALID_RESIDENT_SERVICE_CONTEXTS:
        return scope
    return "facility" if scope == "floor" else None


def _active_resident_names_for_message(db: Session, message: Message) -> list[str]:
    """Return current room-scoped names as suggestions, never replacements."""

    statement = select(Resident.display_name).where(
        Resident.organization_id == message.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
        Resident.service_type.in_(VALID_RESIDENT_SERVICE_CONTEXTS),
    )
    service_context = _message_resident_service_context(message)
    if service_context is not None:
        statement = statement.where(Resident.service_type == service_context)
    return list(
        dict.fromkeys(
            db.scalars(
                statement.order_by(
                    Resident.service_type,
                    Resident.sort_order,
                    Resident.display_name,
                    Resident.id,
                )
            )
        )
    )


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _attachment_preview_text(mime_types: list[str]) -> str | None:
    counts = {
        "image": 0,
        "audio": 0,
        "video": 0,
        "pdf": 0,
        "file": 0,
    }
    for raw_mime_type in mime_types:
        mime_type = (raw_mime_type or "").strip().lower()
        if mime_type.startswith("image/"):
            counts["image"] += 1
        elif mime_type.startswith("audio/"):
            counts["audio"] += 1
        elif mime_type.startswith("video/"):
            counts["video"] += 1
        elif mime_type == "application/pdf":
            counts["pdf"] += 1
        else:
            counts["file"] += 1

    parts: list[str] = []
    if counts["image"]:
        parts.append(f"사진 {counts['image']}장")
    if counts["audio"]:
        parts.append(f"음성 {counts['audio']}개")
    if counts["video"]:
        parts.append(f"동영상 {counts['video']}개")
    if counts["pdf"]:
        parts.append(f"PDF {counts['pdf']}개")
    if counts["file"]:
        parts.append(f"파일 {counts['file']}개")
    return " · ".join(parts) or None


def _room_last_message_preview(db: Session, message: Message | None) -> str | None:
    if message is None:
        return None
    if message.lifecycle_status == "recalled":
        return "작성자가 회수한 메시지입니다."
    if message.body.strip() not in AUTOMATIC_ATTACHMENT_MESSAGE_BODIES:
        return message.body

    mime_types = [
        attachment.mime_type
        for attachment in sorted(
            message.attachments,
            key=lambda item: (item.upload_ordinal, item.created_at),
        )
    ]
    return _attachment_preview_text(mime_types) or message.body


def ensure_reference_data(db: Session) -> Organization:
    organization = db.scalar(
        select(Organization).where(
            Organization.internal_code == settings.organization_code
        )
    )
    if organization is None:
        organization = Organization(
            internal_code=settings.organization_code,
            name=settings.organization_name,
            service_type=settings.organization_service_type,
        )
        db.add(organization)
        db.flush()

    module = db.get(DomainModule, "staff_hub")
    if module is None:
        db.add(
            DomainModule(
                code="staff_hub",
                name="직원 소통·업무기록",
                data_owner="SMCODI StaffHub",
                status="prototype",
                sort_order=10,
                is_independently_deployable=True,
            )
        )

    for order, (code, name) in enumerate(
        [("admin", "관리자"), ("staff", "직원")], start=1
    ):
        role = db.scalar(select(Role).where(Role.code == code))
        if role is None:
            db.add(Role(code=code, name=name, sort_order=order * 10))

    for order, (code, name) in enumerate(DEFAULT_JOB_CODES, start=1):
        job = db.get(StaffJobCode, code)
        if job is None:
            db.add(
                StaffJobCode(
                    code=code,
                    name=name,
                    sort_order=order * 10,
                )
            )
    for order, (code, name) in enumerate(DEFAULT_POSITION_TITLES, start=1):
        position = db.scalar(
            select(StaffPositionCode).where(
                StaffPositionCode.organization_id == organization.id,
                or_(
                    StaffPositionCode.internal_code == code,
                    StaffPositionCode.name == name,
                ),
            )
        )
        if position is None:
            db.add(
                StaffPositionCode(
                    organization_id=organization.id,
                    internal_code=code,
                    name=name,
                    sort_order=order * 10,
                )
            )
    db.flush()
    ensure_approved_organization_catalog(db, organization)
    return organization


def unit_response(unit: OrgUnit | None) -> OrgUnitResponse | None:
    if unit is None:
        return None
    return OrgUnitResponse.model_validate(unit)


def owned_unit_response(
    unit: OrgUnit | None,
    organization_id: UUID,
    unit_type: str,
) -> OrgUnitResponse | None:
    if (
        unit is None
        or unit.organization_id != organization_id
        or unit.unit_type != unit_type
    ):
        return None
    return unit_response(unit)


def user_response(
    user: User,
    *,
    is_dev_launcher: bool = False,
    is_dev_impersonated: bool = False,
    reviewer_experience: str | None = None,
) -> UserResponse:
    staff = (
        user.staff
        if user.staff is not None
        and user.staff.organization_id == user.organization_id
        else None
    )
    current_job = staff.current_job() if staff is not None else None
    if current_job is not None and (
        current_job.organization_id != user.organization_id
        or current_job.staff_id != staff.id
    ):
        current_job = None
    position_title = None
    if staff is not None:
        position_title = staff.position_title or (
            current_job.position_title if current_job is not None else None
        )
    reviewer_public_usernames = {
        "care": "reviewer-care",
        "social_worker": "reviewer-social",
        "realtime_secondary": "reviewer-realtime-secondary",
        MENTOR_FULL_REVIEW_EXPERIENCE: "reviewer-mentor-full",
    }
    public_username = (
        reviewer_public_usernames.get(reviewer_experience, "reviewer")
        if reviewer_experience is not None
        else user.username
    )
    return UserResponse(
        id=user.id,
        username=public_username,
        full_name=staff.display_name if staff is not None else user.display_name,
        role=user.role,
        can_process_records=user.can_process_records,
        employment_status=(
            staff.employment_status
            if staff is not None
            else "active" if user.is_active else "retired"
        ),
        must_change_password=user.must_change_password,
        employee_code=staff.internal_code if staff is not None else None,
        business=owned_unit_response(
            staff.current_unit("business") if staff is not None else None,
            user.organization_id,
            "business",
        ),
        department=owned_unit_response(
            staff.current_unit("department") if staff is not None else None,
            user.organization_id,
            "department",
        ),
        job_code=current_job.job_code if current_job is not None else None,
        job_name=current_job.job.name if current_job is not None else None,
        position_title=position_title,
        floor=owned_unit_response(
            staff.current_unit("floor") if staff is not None else None,
            user.organization_id,
            "floor",
        ),
        team=owned_unit_response(
            staff.current_unit("team") if staff is not None else None,
            user.organization_id,
            "team",
        ),
        terminated_at=as_utc(staff.terminated_at if staff is not None else None),
        is_dev_launcher=is_dev_launcher,
        is_dev_impersonated=is_dev_impersonated,
        is_reviewer_session=reviewer_experience is not None,
        reviewer_experience=reviewer_experience,
    )


def staff_service_assignment_response(
    assignment: StaffServiceAssignment,
) -> StaffServiceAssignmentResponse:
    position_code_id = (
        assignment.position_code_id
        if assignment.position is not None
        and assignment.position.organization_id == assignment.organization_id
        else None
    )
    return StaffServiceAssignmentResponse(
        id=assignment.id,
        service_type=assignment.service_type,
        source_account_id=assignment.source_account_id,
        service_period_id=assignment.service_period_id,
        employment_status=assignment.service_period.employment_status,
        business=owned_unit_response(
            assignment.business, assignment.organization_id, "business"
        ),
        department=owned_unit_response(
            assignment.department, assignment.organization_id, "department"
        ),
        floor=owned_unit_response(
            assignment.floor, assignment.organization_id, "floor"
        ),
        team=owned_unit_response(
            assignment.team, assignment.organization_id, "team"
        ),
        job_code=assignment.job_code,
        job_name=assignment.job.name if assignment.job else None,
        job_title_snapshot=assignment.job_title_snapshot,
        position_code_id=position_code_id,
        position_title_snapshot=assignment.position_title_snapshot,
        start_date=assignment.start_date,
        end_date=assignment.end_date,
        assignment_basis=assignment.assignment_basis,
        is_current=assignment.end_date is None,
    )


def staff_directory_response(
    staff: Staff,
    login_user: User | None,
) -> StaffDirectoryResponse:
    if login_user is not None and (
        login_user.staff_id != staff.id
        or login_user.organization_id != staff.organization_id
    ):
        login_user = None
    current_job = staff.current_job()
    if current_job is not None and (
        current_job.organization_id != staff.organization_id
        or current_job.staff_id != staff.id
    ):
        current_job = None
    service_order = {"facility": 0, "daycare": 1, "homecare": 2}
    assignments = sorted(
        [
            assignment
            for assignment in staff.service_assignments
            if assignment.organization_id == staff.organization_id
            and assignment.staff_id == staff.id
            and assignment.source_account.organization_id == staff.organization_id
            and assignment.source_account.staff_id == staff.id
            and assignment.source_account.service_type == assignment.service_type
            and assignment.service_period.organization_id == staff.organization_id
            and assignment.service_period.staff_id == staff.id
            and assignment.service_period.source_account_id
            == assignment.source_account_id
        ],
        key=lambda row: (
            row.end_date is not None,
            service_order.get(row.service_type, 99),
            -row.start_date.toordinal(),
            str(row.id),
        ),
    )
    if login_user is None:
        login_status = "not_issued"
    elif login_user.is_active and staff.is_active and staff.employment_status == "active":
        login_status = "enabled"
    else:
        login_status = "disabled"
    return StaffDirectoryResponse(
        staff_id=staff.id,
        login_user_id=login_user.id if login_user else None,
        display_name=staff.display_name,
        internal_code=staff.internal_code,
        employment_status=staff.employment_status,
        is_test_data=staff.is_test_data,
        login_status=login_status,
        username=login_user.username if login_user else None,
        role=login_user.role if login_user else None,
        can_process_records=(login_user.can_process_records if login_user else False),
        must_change_password=(login_user.must_change_password if login_user else False),
        terminated_at=as_utc(staff.terminated_at),
        legacy_assignment=StaffLegacyAssignmentResponse(
            business=owned_unit_response(
                staff.current_unit("business"), staff.organization_id, "business"
            ),
            department=owned_unit_response(
                staff.current_unit("department"), staff.organization_id, "department"
            ),
            floor=owned_unit_response(
                staff.current_unit("floor"), staff.organization_id, "floor"
            ),
            team=owned_unit_response(
                staff.current_unit("team"), staff.organization_id, "team"
            ),
            job_code=current_job.job_code if current_job else None,
            job_name=current_job.job.name if current_job else None,
            position_title=staff.position_title,
        ),
        service_assignments=[
            staff_service_assignment_response(assignment)
            for assignment in assignments
        ],
    )


def resident_response(
    resident: Resident | None,
    *,
    is_priority: bool = False,
) -> ResidentResponse | None:
    if resident is None:
        return None
    if resident.internal_code.startswith("SMCODI:carefor:"):
        roster_source = "carefor"
    elif resident.internal_code.startswith("SMCODI:"):
        roster_source = "smcodi"
    elif resident.internal_code.startswith("MANUAL:"):
        roster_source = "manual"
    else:
        roster_source = "demo"
    return ResidentResponse(
        id=resident.id,
        display_name=resident.display_name,
        service_type=resident.service_type,
        floor=unit_response(resident.floor),
        room_name=resident.room.name if resident.room else None,
        sort_order=resident.sort_order,
        is_priority=is_priority,
        roster_source=roster_source,
        is_test_data=resident.is_test_data,
    )


def message_resident_link_response(
    link: MessageResidentLink,
    message: Message | None = None,
) -> MessageResidentLinkResponse:
    return MessageResidentLinkResponse(
        automatically_linked=link.status == "confirmed" and link.source == "text_exact" and link.reviewed_by_id is None,
        resident=resident_response(link.resident),
        source=link.source,
        status=link.status,
        reviewed_at=as_utc(link.reviewed_at) if link.reviewed_at else None,
        evidence=candidate_evidence(link, message) if message is not None and link.status == "candidate" else [],
    )


def action_item_response(item: ActionItem | None) -> ActionItemResponse | None:
    if item is None:
        return None
    return ActionItemResponse(
        id=item.id,
        source_message_id=item.source_message_id,
        room_id=item.source_message.room_id,
        room_name=item.source_message.room.name,
        source_body=item.source_message.body,
        sender_name=item.source_message.sender.full_name,
        resident_name=(
            item.source_message.resident.display_name
            if item.source_message.resident
            else None
        ),
        comment_count=len(item.source_message.comments),
        action_type=item.action_type,
        assignee_user_id=item.assignee_user_id,
        assignee_user_name=item.assignee_user.full_name if item.assignee_user else None,
        assignee_unit_id=item.assignee_unit_id,
        assignee_unit_name=item.assignee_unit.name if item.assignee_unit else None,
        priority=item.priority,
        status=item.status,
        due_at=as_utc(item.due_at),
        created_by_id=item.created_by_id,
        created_by_name=item.created_by.full_name,
        acknowledged_at=as_utc(item.acknowledged_at),
        completed_at=as_utc(item.completed_at),
        created_at=as_utc(item.created_at),
    )


def message_response(
    message: Message,
    *,
    db: Session | None = None,
    viewer_id: UUID | None = None,
    read_count: int | None = None,
    reply_user_count: int | None = None,
    include_recalled_content: bool = False,
    use_admin_attachment_urls: bool = False,
    include_attachment_review_details: bool = True,
) -> MessageResponse:
    redact_attachment_names = False
    can_view_message_comments = True
    if db is not None and viewer_id is not None:
        viewer = db.get(User, viewer_id)
        redact_attachment_names = bool(
            viewer is not None and is_mentor_full_reviewer(viewer)
        )
        can_view_message_comments = bool(
            viewer is not None and viewer.role == "admin"
        )
    is_recalled = message.lifecycle_status == "recalled"
    show_content = not is_recalled or include_recalled_content
    comments = (
        list(message.comments)
        if show_content and can_view_message_comments
        else []
    )
    latest_comment = (
        max(
            comments,
            key=lambda comment: (as_utc(comment.created_at), str(comment.id)),
        )
        if comments
        else None
    )
    unread_comment_count = 0
    if db is not None and viewer_id is not None and comments:
        thread_view = db.scalar(
            select(MessageThreadView).where(
                MessageThreadView.message_id == message.id,
                MessageThreadView.user_id == viewer_id,
            )
        )
        viewed_at = as_utc(thread_view.last_viewed_at) if thread_view else None
        unread_comment_count = sum(
            1
            for comment in comments
            if comment.author_id != viewer_id
            and (viewed_at is None or as_utc(comment.created_at) > viewed_at)
        )
    if not show_content:
        read_count = 0
    elif read_count is None:
        read_count = (
            int(
                db.scalar(
                    select(func.count(MessageReadReceipt.id)).where(
                        MessageReadReceipt.message_id == message.id
                    )
                )
                or 0
            )
            if db is not None
            else 0
        )
    if not show_content:
        reply_user_count = 0
    elif reply_user_count is None:
        reply_user_count = len({comment.author_id for comment in comments})
    forwarded_from = None
    reply_to = None
    ai_help = None
    if show_content and message.extra_data:
        raw_forwarded = message.extra_data.get("forwarded_from")
        if isinstance(raw_forwarded, dict):
            try:
                forwarded_from = ForwardedMessageSource.model_validate(raw_forwarded)
            except ValueError:
                forwarded_from = None
        raw_reply = message.extra_data.get("reply_to")
        if isinstance(raw_reply, dict):
            try:
                reply_to = RepliedMessageSource.model_validate(raw_reply)
            except ValueError:
                reply_to = None
        raw_ai_help = message.extra_data.get("ai_help")
        if isinstance(raw_ai_help, dict):
            try:
                ai_help = AiHelpMessageMeta.model_validate(raw_ai_help)
            except ValueError:
                ai_help = None
    return MessageResponse(
        id=message.id,
        room_id=message.room_id,
        sender_id=message.sender_id,
        sender_name=message.sender.full_name,
        message_type=message.message_type,
        body=message.body if show_content else "작성자가 회수한 메시지입니다.",
        resident=resident_response(current_message_resident(message)) if show_content else None,
        resident_links=[
            message_resident_link_response(link, message)
            for link in message.resident_links
            if show_content and resident_link_is_current(link, message)
        ],
        resident_ref=message.resident_ref if show_content else None,
        attachments=[
            attachment_response(
                attachment,
                db=db,
                viewer_id=viewer_id,
                use_admin_download_url=use_admin_attachment_urls,
                include_review_details=include_attachment_review_details,
                confirmed_text_only=not include_attachment_review_details,
                redact_original_name=redact_attachment_names,
            )
            for attachment in message.attachments
            if show_content
        ],
        comment_count=len(comments),
        unread_comment_count=unread_comment_count,
        latest_comment=(
            MessageCommentResponse(
                id=latest_comment.id,
                author_id=latest_comment.author_id,
                author_name=latest_comment.author.full_name,
                body=latest_comment.body,
                created_at=as_utc(latest_comment.created_at),
            )
            if latest_comment is not None
            else None
        ),
        read_count=read_count,
        reply_user_count=reply_user_count,
        action_item=action_item_response(message.action_item) if show_content else None,
        forwarded_from=forwarded_from,
        reply_to=reply_to,
        ai_help=ai_help,
        is_recalled=is_recalled,
        recalled_at=as_utc(message.recalled_at),
        created_at=as_utc(message.created_at),
    )


def message_engagement_counts(
    db: Session,
    message_ids: list[UUID],
) -> tuple[dict[UUID, int], dict[UUID, int]]:
    unique_message_ids = list(dict.fromkeys(message_ids))
    if not unique_message_ids:
        return {}, {}
    read_counts = {
        message_id: int(count)
        for message_id, count in db.execute(
            select(
                MessageReadReceipt.message_id,
                func.count(MessageReadReceipt.id),
            )
            .where(MessageReadReceipt.message_id.in_(unique_message_ids))
            .group_by(MessageReadReceipt.message_id)
        ).all()
    }
    reply_user_counts = {
        message_id: int(count)
        for message_id, count in db.execute(
            select(
                MessageComment.message_id,
                func.count(func.distinct(MessageComment.author_id)),
            )
            .where(MessageComment.message_id.in_(unique_message_ids))
            .group_by(MessageComment.message_id)
        ).all()
    }
    return read_counts, reply_user_counts


def attachment_response(
    attachment: MessageAttachment,
    *,
    db: Session | None = None,
    viewer_id: UUID | None = None,
    use_admin_download_url: bool = False,
    include_review_details: bool = True,
    confirmed_text_only: bool = False,
    redact_original_name: bool = False,
) -> AttachmentResponse:
    access = attachment_access_capabilities(db, attachment, viewer_id)
    extraction = attachment.text_extraction
    correction_candidates: list[dict] = []
    latest_correction_event: OcrCorrectionEvent | None = None
    latest_confirmed_event: OcrCorrectionEvent | None = None
    correction_event_count = 0
    active_resident_names: list[str] = []
    if db is not None and extraction is not None and include_review_details:
        active_resident_names = _active_resident_names_for_message(
            db,
            attachment.message,
        )
        events = db.scalars(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.organization_id
                == attachment.message.organization_id,
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(
                OcrCorrectionEvent.created_at.desc(),
                OcrCorrectionEvent.id.desc(),
            )
            .limit(300)
        ).all()
        latest_events: list[OcrCorrectionEvent] = []
        seen_extraction_ids: set[UUID] = set()
        for event in events:
            if event.extraction_id is not None:
                if event.extraction_id in seen_extraction_ids:
                    continue
                seen_extraction_ids.add(event.extraction_id)
            latest_events.append(event)
        events = latest_events
        event_message_ids = {
            event.source_message_id
            for event in events
            if event.source_message_id is not None
        }
        source_messages = {
            message.id: message
            for message in db.scalars(
                select(Message).where(Message.id.in_(event_message_ids))
            ).all()
        }
        event_attachment_ids = {
            event.attachment_id
            for event in events
            if event.attachment_id is not None
        }
        source_attachments = {
            source_attachment.id: source_attachment
            for source_attachment in db.scalars(
                select(MessageAttachment).where(
                    MessageAttachment.id.in_(event_attachment_ids)
                )
            ).all()
        }
        evidences: list[CorrectionEvidence] = []
        for event in events:
            source_message = source_messages.get(event.source_message_id)
            source_attachment = source_attachments.get(event.attachment_id)
            for pair in event.correction_pairs or []:
                recognized = str(pair.get("recognized_text", "")).strip()
                corrected = str(pair.get("corrected_text", "")).strip()
                if not recognized or not corrected or recognized == corrected:
                    continue
                evidences.append(
                    CorrectionEvidence(
                        event_id=str(event.id),
                        recognized_text=recognized,
                        corrected_text=corrected,
                        content_type=str(pair.get("content_type", "general")),
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
                        source_kind=(
                            "audio"
                            if source_attachment is not None
                            and source_attachment.mime_type.startswith("audio/")
                            else "image"
                            if source_attachment is not None
                            and source_attachment.mime_type.startswith("image/")
                            else None
                        ),
                    )
                )
        correction_candidates = suggest_from_confirmed_events(
            extraction.extracted_text,
            context_text=extraction.extracted_text,
            source_writer_id=attachment.message.sender_id,
            visual_signature=extraction.visual_signature,
            evidences=evidences,
            resident_names=active_resident_names,
            service_context=_message_resident_service_context(
                attachment.message
            ),
            source_kind=(
                "audio"
                if attachment.mime_type.startswith("audio/")
                else "image"
                if attachment.mime_type.startswith("image/")
                else None
            ),
        )
        seen_candidates = {
            (candidate["recognized"], candidate["candidate"])
            for candidate in correction_candidates
        }
        for stored_candidate in getattr(extraction, "suggestion_details", None) or []:
            recognized = str(stored_candidate.get("recognized", "")).strip()
            candidate = str(stored_candidate.get("candidate", "")).strip()
            is_name_region = str(stored_candidate.get("reason", "")).startswith(
                "이미지 이름 위치 전용 판독"
            )
            if (
                not recognized
                or not candidate
                or candidate not in active_resident_names
                or (
                    (recognized, candidate) in seen_candidates
                    and not is_name_region
                )
            ):
                continue
            correction_candidates.append(
                {
                    "id": correction_candidate_id(
                        "active_resident_roster",
                        recognized,
                        candidate,
                    ),
                    "recognized": recognized,
                    "candidate": candidate,
                    "confidence": max(
                        0.0,
                        min(1.0, float(stored_candidate.get("confidence", 0.0))),
                    ),
                    "support_count": max(
                        0,
                        int(stored_candidate.get("support_count", 0)),
                    ),
                    "content_type": "resident_name",
                    "is_protected": True,
                    "source": (
                        "image_name_region"
                        if is_name_region
                        else "active_resident_roster"
                    ),
                    "reason": str(
                        stored_candidate.get("reason")
                        or "이름 위치 · 현재 이용 어르신 명단 대조"
                    ),
                    "source_event_ids": [],
                    "auto_applicable": False,
                    "rank": (
                        max(1, int(stored_candidate.get("rank", 1)))
                        if stored_candidate.get("rank") is not None
                        else None
                    ),
                    "slot_index": (
                        max(1, int(stored_candidate.get("slot_index", 1)))
                        if stored_candidate.get("slot_index") is not None
                        else None
                    ),
                    "applied_to_draft": bool(
                        stored_candidate.get("applied_to_draft", False)
                    ),
                    "application_reason": (
                        str(stored_candidate.get("application_reason"))
                        if stored_candidate.get("application_reason")
                        else None
                    ),
                    "detected_text": (
                        str(stored_candidate.get("detected_text"))
                        if stored_candidate.get("detected_text")
                        else None
                    ),
                    "resolved_in_draft": bool(
                        stored_candidate.get("resolved_in_draft", False)
                    ),
                }
            )
            seen_candidates.add((recognized, candidate))
        correction_candidates.sort(
            key=lambda item: (
                item.get("source") != "image_name_region",
                -float(item.get("confidence", 0.0)),
            )
        )
        latest_correction_event = db.scalar(
            select(OcrCorrectionEvent)
            .where(OcrCorrectionEvent.extraction_id == extraction.id)
            .order_by(OcrCorrectionEvent.created_at.desc())
            .limit(1)
        )
        latest_confirmed_event = db.scalar(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.extraction_id == extraction.id,
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(OcrCorrectionEvent.created_at.desc())
            .limit(1)
        )
        correction_event_count = int(
            db.scalar(
                select(func.count(OcrCorrectionEvent.id)).where(
                    OcrCorrectionEvent.extraction_id == extraction.id
                )
            )
            or 0
        )
    elif db is not None and extraction is not None and confirmed_text_only:
        latest_confirmed_event = db.scalar(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.extraction_id == extraction.id,
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(OcrCorrectionEvent.created_at.desc())
            .limit(1)
        )
    # 채팅방·검색 목록은 판독문 표시까지만 필요합니다. 기관 사전 전체와
    # 어르신 명단을 비교하는 후보 계산은 상세 화면에서만 수행해야 방 진입 때
    # 첨부 수만큼 수십만 번의 한글 유사도 계산이 반복되지 않습니다.
    if include_review_details:
        preferred_resident_names = [
            link.resident.display_name
            for link in attachment.message.resident_links
            if link.status != "rejected"
        ]
        if (
            attachment.message.resident is not None
            and attachment.message.resident.display_name not in preferred_resident_names
        ):
            preferred_resident_names.insert(
                0,
                attachment.message.resident.display_name,
            )
        preferred_resident_names = list(
            dict.fromkeys([*preferred_resident_names, *active_resident_names])
        )
        seen_candidates = {
            (candidate["recognized"], candidate["candidate"])
            for candidate in correction_candidates
        }
        for legacy_candidate in find_spelling_candidates(
            extraction.extracted_text if extraction is not None else None,
            preferred_terms=preferred_resident_names,
            source_kind=(
                "audio"
                if attachment.mime_type.startswith("audio/")
                else "image"
                if attachment.mime_type.startswith("image/")
                else None
            ),
        ):
            recognized = legacy_candidate["recognized"]
            candidate = legacy_candidate["candidate"]
            if (recognized, candidate) in seen_candidates:
                continue
            content_type = classify_content_type(
                recognized,
                candidate,
                resident_names=preferred_resident_names,
            )
            correction_candidates.append(
                {
                    "id": correction_candidate_id(
                        "institution_lexicon",
                        recognized,
                        candidate,
                    ),
                    "recognized": recognized,
                    "candidate": candidate,
                    "confidence": round(
                        0.55
                        if candidate not in active_resident_names
                        else max(
                            0.55,
                            SequenceMatcher(None, recognized, candidate).ratio(),
                        ),
                        3,
                    ),
                    "support_count": 0,
                    "content_type": content_type,
                    "is_protected": content_type in PROTECTED_CONTENT_TYPES,
                    "source": (
                        "active_resident_roster"
                        if candidate in active_resident_names
                        else "institution_lexicon"
                    ),
                    "reason": (
                        "현재 이용 어르신 이름과 유사"
                        if candidate in active_resident_names
                        else "기관 어휘와 유사"
                    ),
                    "source_event_ids": [],
                    "auto_applicable": False,
                }
            )
            seen_candidates.add((recognized, candidate))
            if len(correction_candidates) >= 8:
                break
    preprocessing_summary = (
        _handwriting_preprocessing_summary(
            getattr(extraction, "suggestion_details", None)
        )
        if extraction is not None and attachment.mime_type.startswith("image/")
        else None
    )
    guard_handwriting_output = attachment.mime_type.startswith("image/")
    safe_extracted_text = (
        None
        if extraction is not None
        and guard_handwriting_output
        and is_pathological_handwriting_output(extraction.extracted_text)
        else extraction.extracted_text if extraction is not None else None
    )
    raw_original_text = (
        extraction.original_extracted_text or extraction.extracted_text
        if extraction is not None
        else None
    )
    safe_original_text = (
        None
        if guard_handwriting_output
        and is_pathological_handwriting_output(raw_original_text)
        else raw_original_text
    )
    output_blocked = bool(guard_handwriting_output and (
        (preprocessing_summary or {}).get("output_blocked")
        or (raw_original_text and safe_original_text is None)
        or (extraction is not None and extraction.extracted_text and safe_extracted_text is None)
    ))
    image_result_invalid = bool(extraction is not None and guard_handwriting_output and
        extraction.status == "completed" and (output_blocked or not visible_image_text(extraction)))
    if image_result_invalid:
        safe_extracted_text = safe_original_text = None
    photo_state = photo_reading_status(attachment)
    if image_result_invalid:
        photo_state = "failed"
    if photo_state in {"no_text", "not_required", "general"}:
        safe_extracted_text = safe_original_text = None
        confirmed_text_only = True
    runtime_detail = next((row for row in getattr(extraction, "suggestion_details", None) or [] if row.get("kind") in {"image_ocr_runtime", "document_reading", "audio_quality"}), {})
    image_error_type = "output_blocked" if output_blocked else "empty_result" if image_result_invalid else runtime_detail.get("error_type")
    confirmed_text = (
        latest_confirmed_event.corrected_text
        if latest_confirmed_event is not None
        else extraction.reviewed_text
        if extraction is not None
        and extraction.status == "reviewed"
        and extraction.reviewed_text
        else None
    )
    if attachment.mime_type.startswith("image/") and photo_state != "completed":
        confirmed_text = None
    if requires_staff_review(attachment):
        confirmed_text = attachment_evidence_text(attachment) or None
    safe_extension = Path(attachment.original_name).suffix.lower()
    if not re.fullmatch(r"\.[a-z0-9]{1,8}", safe_extension):
        safe_extension = ""
    upload_ordinal = int(getattr(attachment, "upload_ordinal", 0) or 0)
    if attachment.mime_type.startswith("image/"):
        review_name = f"가상 이미지 자료 {upload_ordinal + 1}{safe_extension}"
    elif attachment.mime_type.startswith("audio/"):
        review_name = f"가상 음성 자료 {upload_ordinal + 1}{safe_extension}"
    elif attachment.mime_type == "application/pdf":
        review_name = f"가상 문서 자료 {upload_ordinal + 1}.pdf"
    else:
        review_name = f"가상 첨부 자료 {upload_ordinal + 1}{safe_extension}"
    return AttachmentResponse(
        photo_reading_status=photo_state,
        can_download_original=(
            True if use_admin_download_url else access["can_download_original"]
        ),
        can_view_reviewed_text=access["can_view_reviewed_text"],
        can_review_text=access["can_review_text"],
        can_request_reading=access["can_request_reading"],
        resident_link_revision=int(
            resident_review_metadata(attachment.message).get("revision", 0) or 0
        ),
        id=attachment.id,
        message_id=attachment.message_id,
        resident_candidate_notice=("판독 결과를 먼저 확인해 주세요" if attachment.mime_type.startswith("image/") and photo_state not in {"general", "no_text", "not_required"} and any(link.source == "ocr_exact" and link.status == "candidate" and not resident_link_is_current(link, attachment.message) for link in attachment.message.resident_links) else None),
        uploader_id=attachment.uploader_id,
        original_name=review_name if redact_original_name else attachment.original_name,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        download_url=(
            f"/api/admin/conversations/attachments/{attachment.id}"
            if use_admin_download_url
            else f"/api/attachments/{attachment.id}"
        ),
        thumbnail_url=(
            f"/api/attachments/{attachment.id}/thumbnail"
            if attachment.mime_type.startswith("image/")
            and not use_admin_download_url
            else None
        ),
        text_extraction=(
            AttachmentTextExtractionResponse(
                reviewed_by_id=getattr(extraction, "reviewed_by_id", None),
                review_revision=next((int(row.get("revision",0)) for row in getattr(extraction,"suggestion_details",None) or [] if row.get("kind")=="staff_review_revision"),0),
                source_locations=runtime_detail.get("locations",[]) if not confirmed_text_only else [],
                status="failed" if image_result_invalid else "no_text" if photo_state == "no_text" else "completed" if requires_staff_review(attachment) and extraction.status == "reviewed" and not has_staff_review(extraction) else extraction.status,
                content_included=not confirmed_text_only,
                result_char_count=0 if photo_state in {"no_text", "not_required"} else None if confirmed_text_only else len(safe_extracted_text or safe_original_text or ""),
                processing_ms=runtime_detail.get("elapsed_ms") if not confirmed_text_only else None,
                error_type=image_error_type if not confirmed_text_only else None,
                provider="" if confirmed_text_only else extraction.provider,
                model_name="" if confirmed_text_only else extraction.model_name,
                extracted_text=(None if confirmed_text_only else safe_extracted_text),
                original_extracted_text=(
                    None if confirmed_text_only else safe_original_text
                ),
                suggested_text=(
                    None
                    if confirmed_text_only or output_blocked
                    else getattr(extraction, "suggested_text", None)
                ),
                suggested_service_context=(
                    getattr(extraction, "suggested_service_context", None)
                    if not confirmed_text_only
                    and getattr(extraction, "suggested_service_context", None)
                    in VALID_RESIDENT_SERVICE_CONTEXTS
                    else None
                ),
                suggested_name_correction_count=len(
                    {
                        str(item.get("recognized", "")).strip()
                        for item in (
                            getattr(extraction, "suggestion_details", None) or []
                        )
                        if str(item.get("recognized", "")).strip()
                    }
                ) if not confirmed_text_only else 0,
                auto_applied_name_count=sum(
                    1
                    for item in (getattr(extraction, "suggestion_details", None) or [])
                    if int(item.get("rank", 0) or 0) == 1
                    and bool(item.get("applied_to_draft", False))
                    and item.get("application_reason")
                    not in {"unresolved_name_placeholder", "safe_context_draft"}
                ) if not confirmed_text_only else 0,
                unresolved_name_count=sum(
                    1
                    for item in (getattr(extraction, "suggestion_details", None) or [])
                    if int(item.get("rank", 0) or 0) == 1
                    and item.get("application_reason") in {
                        "recognized_text_not_located",
                        "unresolved_name_placeholder",
                    }
                ) if not confirmed_text_only else 0,
                review_warnings=(
                    []
                    if confirmed_text_only
                    else build_ocr_review_warnings(safe_original_text)
                ),
                reviewed_text=(
                    None if confirmed_text_only else extraction.reviewed_text
                ),
                reviewed_by_name=(
                    extraction.reviewed_by.full_name
                    if include_review_details
                    and not confirmed_text_only
                    and extraction.reviewed_by is not None
                    else None
                ),
                latest_confirmed_text=confirmed_text,
                latest_confirmed_by_name=(
                    latest_confirmed_event.reviewed_by.full_name
                    if latest_confirmed_event is not None
                    and latest_confirmed_event.reviewed_by is not None
                    and not confirmed_text_only
                    else None
                ),
                latest_confirmed_at=(
                    as_utc(latest_confirmed_event.created_at)
                    if latest_confirmed_event is not None
                    else None
                ),
                latest_confirmation_source=(
                    latest_confirmed_event.provider
                    if latest_confirmed_event is not None and not confirmed_text_only
                    else None
                ),
                error_message=(None if confirmed_text_only else
                    "판독 결과가 비정상적으로 반복되어 표시하지 않았습니다. 다시 판독해 주세요." if output_blocked else
                    "판독 결과를 만들지 못했습니다. 글씨가 있다면 다시 판독해 주세요." if image_result_invalid else extraction.error_message),
                completed_at=(
                    as_utc(extraction.completed_at)
                    if extraction.completed_at is not None and not confirmed_text_only
                    else None
                ),
                reviewed_at=(
                    as_utc(extraction.reviewed_at)
                    if extraction.reviewed_at is not None and not confirmed_text_only
                    else None
                ),
                review_decision=(
                    latest_correction_event.decision
                    if latest_correction_event is not None and not confirmed_text_only
                    else None
                ),
                correction_event_count=(0 if confirmed_text_only else correction_event_count),
                spelling_candidates=([] if confirmed_text_only else correction_candidates[:40]),
                attempt_number=len(getattr(extraction, "attempts", []) or []) + 1,
                previous_attempts=(
                    [
                        AttachmentTextExtractionAttemptResponse(
                            attempt_number=attempt.attempt_number,
                            status=attempt.status,
                            provider=attempt.provider,
                            model_name=attempt.model_name,
                            extracted_text=(
                                None
                                if guard_handwriting_output
                                and is_pathological_handwriting_output(
                                    attempt.extracted_text
                                )
                                else attempt.extracted_text
                            ),
                            reviewed_text=attempt.reviewed_text,
                            error_message=attempt.error_message,
                            completed_at=(
                                as_utc(attempt.completed_at)
                                if attempt.completed_at is not None
                                else None
                            ),
                            reviewed_at=(
                                as_utc(attempt.reviewed_at)
                                if attempt.reviewed_at is not None
                                else None
                            ),
                            archived_at=as_utc(attempt.archived_at),
                        )
                        for attempt in reversed(
                            (getattr(extraction, "attempts", []) or [])[-5:]
                        )
                    ]
                    if include_review_details
                    else []
                ),
                preprocessing=(None if confirmed_text_only else preprocessing_summary),
            )
            if extraction is not None
            else None
        ),
    )


def record_audit(
    db: Session,
    *,
    actor_id: UUID | None,
    action: str,
    target_type: str,
    target_id: UUID | str | None,
    details: dict | None = None,
) -> None:
    actor = db.get(User, actor_id) if actor_id else None
    organization = actor.organization if actor else ensure_reference_data(db)
    entity_id: UUID | None
    if isinstance(target_id, UUID):
        entity_id = target_id
    else:
        try:
            entity_id = UUID(str(target_id)) if target_id else None
        except ValueError:
            entity_id = None
            details = {**(details or {}), "external_target_id": str(target_id)}
    db.add(
        AuditEvent(
            organization_id=organization.id,
            actor_id=actor_id,
            action=action,
            target_type=target_type,
            target_id=entity_id,
            details=details,
            is_test_data=settings.environment != "production",
        )
    )


def set_user_role(db: Session, user: User, role_code: str) -> None:
    role = db.scalar(select(Role).where(Role.code == role_code))
    if role is None:
        raise HTTPException(status_code=422, detail="지정한 권한을 찾을 수 없습니다.")
    user.roles[:] = [role]


def ensure_system_rooms(
    db: Session, organization: Organization | None = None
) -> Room:
    organization = organization or ensure_reference_data(db)
    room = db.scalar(
        select(Room).where(
            Room.organization_id == organization.id,
            Room.kind == "all",
            Room.is_active.is_(True),
        )
    )
    if room is None:
        room = Room(
            organization_id=organization.id,
            name="전체 직원방",
            kind="all",
            is_test_data=settings.environment != "production",
        )
        db.add(room)
        db.flush()
    return room


def ensure_scope_room(db: Session, unit: OrgUnit) -> Room | None:
    if unit.unit_type not in AUTO_UNIT_FIELDS:
        return None
    if unit.unit_type == "department":
        parent = unit.parent
        if (
            parent is not None
            and parent.organization_id == unit.organization_id
            and parent.unit_type == "business"
        ):
            expected_name = f"{parent.name} {unit.name}방"
        else:
            expected_name = f"{unit.name}방"
    else:
        suffix = ROOM_LABELS[unit.unit_type]
        separator = "" if unit.unit_type == "team" else " "
        expected_name = f"{unit.name}{separator}{suffix}"
    room = db.scalar(
        select(Room).where(
            Room.organization_id == unit.organization_id,
            Room.kind == unit.unit_type,
            Room.scope_unit_id == unit.id,
        )
    )
    if room is None:
        room = Room(
            organization_id=unit.organization_id,
            name=expected_name,
            kind=unit.unit_type,
            scope_unit_id=unit.id,
            resident_scope="floor" if unit.unit_type == "floor" else "all",
            is_test_data=unit.is_test_data,
        )
        db.add(room)
        db.flush()
    elif room.name != expected_name:
        room.name = expected_name
        db.flush()
    return room


def ensure_job_room(
    db: Session, organization_id: UUID, job: StaffJobCode
) -> Room:
    room = db.scalar(
        select(Room).where(
            Room.organization_id == organization_id,
            Room.kind == "job",
            Room.job_code == job.code,
        )
    )
    if room is None:
        room = Room(
            organization_id=organization_id,
            name=f"{job.name}방",
            kind="job",
            job_code=job.code,
            is_test_data=settings.environment != "production",
        )
        db.add(room)
        db.flush()
    return room


def ensure_self_room(db: Session, user: User) -> Room | None:
    """재직자 본인만 사용하는 '나와의 대화' 방을 멱등 생성합니다."""
    if not settings.self_chat_enabled:
        return None
    if user.staff is None:
        return None
    if settings.dev_launcher_active and user.username == settings.dev_launcher_username:
        return None
    room = db.scalar(
        select(Room).where(
            Room.organization_id == user.organization_id,
            Room.kind == "self",
            Room.owner_staff_id == user.staff.id,
        )
    )
    if room is None:
        room = Room(
            organization_id=user.organization_id,
            name="나와의 대화",
            kind="self",
            owner_staff_id=user.staff.id,
            resident_scope="all",
            sort_order=-100,
            is_test_data=settings.environment != "production",
        )
        db.add(room)
        db.flush()
    return room


def ensure_ai_help_room(db: Session, user: User) -> Room | None:
    """활성 직원에게만 개인 AI 도움방을 멱등 생성합니다."""
    if not settings.ai_help_room_enabled or user.staff is None:
        return None
    room = db.scalar(
        select(Room).where(
            Room.organization_id == user.organization_id,
            Room.kind == "ai",
            Room.owner_staff_id == user.staff.id,
        )
    )
    if room is None:
        room = Room(
            organization_id=user.organization_id,
            name="MESIL AI 도움방",
            kind="ai",
            owner_staff_id=user.staff.id,
            resident_scope="all",
            # 일반 업무방을 먼저 보여 주고 개인 도구방은 목록 하단에 둔다.
            sort_order=900,
            is_test_data=settings.environment != "production",
        )
        try:
            with db.begin_nested():
                db.add(room)
                db.flush()
        except Exception:
            room = db.scalar(
                select(Room).where(
                    Room.organization_id == user.organization_id,
                    Room.kind == "ai",
                    Room.owner_staff_id == user.staff.id,
                )
            )
            if room is None:
                raise
    elif not room.is_active:
        # 롤백은 대화 이력을 삭제하지 않고 방만 숨긴다. 기능을 다시 켜면
        # 같은 방을 되살려 직원별 유일 방과 기존 문맥을 모두 보존한다.
        room.is_active = True
        db.flush()
    return room


def ensure_mesil_ai_user(db: Session, organization_id: UUID) -> User:
    """로그인할 수 없는 조직별 시스템 AI 작성자를 반환합니다."""
    username = f"__mesil_ai__{organization_id.hex}"
    user = db.scalar(
        select(User).where(
            User.organization_id == organization_id,
            User.username == username,
        )
    )
    if user is None:
        user = User(
            organization_id=organization_id,
            username=username,
            display_name="MESIL AI",
            password_hash=hash_password(uuid4().hex + uuid4().hex),
            is_active=False,
            can_process_records=False,
            must_change_password=False,
        )
        try:
            with db.begin_nested():
                db.add(user)
                db.flush()
        except Exception:
            user = db.scalar(
                select(User).where(
                    User.organization_id == organization_id,
                    User.username == username,
                )
            )
            if user is None:
                raise
    return user


def validate_unit_assignments(
    db: Session, values: dict, organization_id: UUID | None = None
) -> None:
    for field_name, expected_type in USER_UNIT_FIELDS.items():
        if field_name not in values or values[field_name] is None:
            continue
        unit = db.get(OrgUnit, values[field_name])
        if (
            unit is None
            or not unit.is_active
            or unit.unit_type != expected_type
            or (organization_id is not None and unit.organization_id != organization_id)
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"{field_name}에 지정한 조직정보가 올바르지 않습니다.",
            )


def set_staff_unit_assignments(
    db: Session,
    staff: Staff,
    values: dict,
    actor_id: UUID,
) -> None:
    validate_unit_assignments(db, values, staff.organization_id)
    today = date.today()
    for field_name, unit_type in USER_UNIT_FIELDS.items():
        if field_name not in values:
            continue
        next_unit_id = values[field_name]
        current = next(
            (
                assignment
                for assignment in staff.organization_assignments
                if assignment.unit_type == unit_type and assignment.end_date is None
            ),
            None,
        )
        if current is not None and current.unit_id == next_unit_id:
            continue
        if current is not None:
            if current.start_date == today and next_unit_id is not None:
                current.unit_id = next_unit_id
                current.unit = db.get(OrgUnit, next_unit_id)
                current.updated_by = actor_id
                continue
            if current.start_date == today:
                staff.organization_assignments.remove(current)
                db.delete(current)
            else:
                current.end_date = today
                current.updated_by = actor_id
        if next_unit_id is not None:
            unit = db.get(OrgUnit, next_unit_id)
            staff.organization_assignments.append(
                StaffOrganizationAssignment(
                    organization_id=staff.organization_id,
                    unit_id=next_unit_id,
                    unit_type=unit_type,
                    start_date=today,
                    is_test_data=staff.is_test_data,
                    created_by=actor_id,
                    updated_by=actor_id,
                    unit=unit,
                )
            )


def set_staff_job(
    db: Session,
    staff: Staff,
    job_code: str,
    actor_id: UUID,
) -> None:
    job = db.get(StaffJobCode, job_code) if job_code else None
    if job_code and (job is None or not job.is_active):
        raise HTTPException(status_code=422, detail="지정한 직종을 찾을 수 없습니다.")
    current = staff.current_job()
    if current is not None and current.job_code == job_code:
        staff.job_title = job.name
        return
    today = date.today()
    if current is not None:
        if current.start_date == today:
            current.job_code = job.code
            current.job_title = job.name
            current.job = job
            current.updated_by = actor_id
            staff.job_title = job.name
            return
        current.end_date = today
        current.updated_by = actor_id
    staff.job_assignments.append(
        StaffJobAssignment(
            organization_id=staff.organization_id,
            job_code=job.code,
            job_title=job.name,
            start_date=today,
            is_primary=True,
            created_by=actor_id,
            updated_by=actor_id,
            job=job,
        )
    )
    staff.job_title = job.name


def clear_staff_job(
    db: Session,
    staff: Staff,
    actor_id: UUID,
) -> None:
    current = staff.current_job()
    if current is None:
        staff.job_title = "직종 미지정"
        return
    today = date.today()
    if current.start_date == today:
        staff.job_assignments.remove(current)
        db.delete(current)
    else:
        current.end_date = today
        current.updated_by = actor_id
    staff.job_title = "직종 미지정"


def set_staff_position_title(
    staff: Staff,
    position_title: str | None,
    actor_id: UUID,
) -> None:
    normalized = position_title.strip() if position_title else None
    staff.position_title = normalized or None
    current = staff.current_job()
    if current is not None:
        current.position_title = normalized or None
        current.updated_by = actor_id


def validate_position_title(
    db: Session,
    organization_id: UUID,
    position_title: str | None,
) -> str | None:
    normalized = position_title.strip() if position_title else None
    if not normalized:
        return None
    position = db.scalar(
        select(StaffPositionCode).where(
            StaffPositionCode.organization_id == organization_id,
            StaffPositionCode.name == normalized,
            StaffPositionCode.is_active.is_(True),
        )
    )
    if position is None:
        raise HTTPException(
            status_code=422,
            detail="관리자가 등록한 직위를 선택해 주세요.",
        )
    return normalized


def staff_matches_room_rule(staff: Staff, room: Room) -> bool:
    if room.kind == "all":
        return True
    if room.kind in AUTO_UNIT_FIELDS:
        unit = staff.current_unit(room.kind)
        if unit is not None and unit.is_active and unit.id == room.scope_unit_id:
            return True
        return any(
            assignment.end_date is None
            and assignment.organization_id == staff.organization_id
            and (service_unit := getattr(assignment, room.kind)) is not None
            and service_unit.organization_id == staff.organization_id
            and service_unit.unit_type == room.kind
            and service_unit.is_active
            and service_unit.id == room.scope_unit_id
            for assignment in staff.service_assignments
        )
    if room.kind == "job":
        assignment = staff.current_job()
        if (
            assignment is not None
            and assignment.job.is_active
            and assignment.job_code == room.job_code
        ):
            return True
        return any(
            service_assignment.end_date is None
            and service_assignment.organization_id == staff.organization_id
            and service_assignment.job_code == room.job_code
            and service_assignment.job is not None
            and service_assignment.job.is_active
            for service_assignment in staff.service_assignments
        )
    if room.kind in {"self", "ai"}:
        return room.owner_staff_id == staff.id
    return False


def sync_auto_memberships(db: Session, user: User) -> None:
    if user.staff is None:
        return
    staff = user.staff
    if (
        not user.is_active
        or not staff.is_active
        or staff.deleted_at is not None
        or staff.employment_status != "active"
    ):
        # 휴직 중에는 방 배정 이력을 닫거나 다시 만들지 않는다. 접근 계층에서
        # 일시 중지하고, 복직하면 보존된 이력과 현재 배정으로 다시 계산한다.
        return
    now = utcnow()
    desired_room_ids: set[UUID] = set()
    if user.is_active and staff.employment_status == "active":
        self_room = ensure_self_room(db, user)
        if self_room is not None and self_room.is_active:
            desired_room_ids.add(self_room.id)
        ai_room = ensure_ai_help_room(db, user)
        if ai_room is not None and ai_room.is_active:
            desired_room_ids.add(ai_room.id)
        desired_room_ids.update(
            db.scalars(
                select(Room.id).where(
                    Room.organization_id == user.organization_id,
                    Room.kind == "all",
                    Room.is_active.is_(True),
                )
            ).all()
        )
        for unit_type in AUTO_UNIT_FIELDS:
            unit = staff.current_unit(unit_type)
            if unit is not None and unit.is_active:
                room = db.scalar(
                    select(Room).where(
                        Room.organization_id == user.organization_id,
                        Room.kind == unit_type,
                        Room.scope_unit_id == unit.id,
                        Room.is_active.is_(True),
                    )
                )
                if room is not None:
                    desired_room_ids.add(room.id)
        job_assignment = staff.current_job()
        if job_assignment is not None and job_assignment.job.is_active:
            job_room = db.scalar(
                select(Room).where(
                    Room.organization_id == staff.organization_id,
                    Room.kind == "job",
                    Room.job_code == job_assignment.job.code,
                    Room.is_active.is_(True),
                )
            )
            if job_room is not None:
                desired_room_ids.add(job_room.id)

        current_service_assignments = [
            assignment
            for assignment in staff.service_assignments
            if assignment.end_date is None
            and assignment.organization_id == staff.organization_id
        ]
        for service_assignment in current_service_assignments:
            for unit_type, field_name in SERVICE_ASSIGNMENT_UNIT_FIELDS.items():
                unit_id = getattr(service_assignment, field_name)
                if unit_id is None:
                    continue
                unit = db.get(OrgUnit, unit_id)
                if (
                    unit is None
                    or not unit.is_active
                    or unit.organization_id != staff.organization_id
                    or unit.unit_type != unit_type
                ):
                    continue
                room = ensure_scope_room(db, unit)
                if room is not None and room.is_active:
                    desired_room_ids.add(room.id)
            if (
                service_assignment.job_code is not None
                and service_assignment.job is not None
                and service_assignment.job.is_active
            ):
                job_room = ensure_job_room(
                    db,
                    staff.organization_id,
                    service_assignment.job,
                )
                if job_room.is_active:
                    desired_room_ids.add(job_room.id)

    overrides = list(
        db.scalars(
            select(RoomMembershipOverride).where(
                RoomMembershipOverride.staff_id == staff.id
            )
        ).all()
    )
    excluded_room_ids = {
        override.room_id for override in overrides if override.action == "exclude"
    }
    included_room_ids = {
        override.room_id for override in overrides if override.action == "include"
    }
    if included_room_ids:
        included_room_ids = set(
            db.scalars(
                select(Room.id).where(
                    Room.id.in_(included_room_ids),
                    Room.is_active.is_(True),
                )
            ).all()
        )
    desired_room_ids.difference_update(excluded_room_ids)
    desired_room_ids.difference_update(included_room_ids)

    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.staff_id == staff.id,
            RoomMembership.source == "auto",
        )
    ).all()
    by_room = {membership.room_id: membership for membership in memberships}
    for room_id, membership in by_room.items():
        if room_id in desired_room_ids:
            membership.left_at = None
        elif membership.left_at is None:
            membership.left_at = now
    active_manual_room_ids = set(
        db.scalars(
            select(RoomMembership.room_id).where(
                RoomMembership.staff_id == staff.id,
                RoomMembership.source == "manual",
                RoomMembership.left_at.is_(None),
            )
        ).all()
    )
    for room_id in desired_room_ids - by_room.keys() - active_manual_room_ids:
        db.add(
            RoomMembership(
                organization_id=staff.organization_id,
                room_id=room_id,
                staff_id=staff.id,
                source="auto",
                joined_at=now,
            )
        )

    manual_memberships = list(
        db.scalars(
            select(RoomMembership)
            .where(
                RoomMembership.staff_id == staff.id,
                RoomMembership.source == "manual",
                RoomMembership.room_id.in_(included_room_ids or {UUID(int=0)}),
            )
            .order_by(RoomMembership.joined_at.desc())
        ).all()
    )
    manual_by_room: dict[UUID, RoomMembership] = {}
    for membership in manual_memberships:
        manual_by_room.setdefault(membership.room_id, membership)
    for room_id in included_room_ids:
        membership = manual_by_room.get(room_id)
        if membership is None:
            db.add(
                RoomMembership(
                    organization_id=staff.organization_id,
                    room_id=room_id,
                    staff_id=staff.id,
                    source="manual",
                    joined_at=now,
                )
            )
        else:
            membership.left_at = None
    db.flush()


def active_membership(
    db: Session, user_id: UUID, room_id: UUID
) -> RoomMembership | None:
    user = db.get(User, user_id)
    if user is None or user.staff_id is None:
        return None
    reviewer_experience = getattr(user, "_reviewer_experience", None)
    if reviewer_experience is not None:
        mentor_full_review = (
            reviewer_experience == MENTOR_FULL_REVIEW_EXPERIENCE
        )
        password_member_scope = bool(
            settings.reviewer_password_access_active
            and reviewer_experience == settings.reviewer_password_login_experience
            and (settings.mentor_reviewer_username or "").strip()
            and not mentor_full_review
        )
        allowed_room = db.scalar(
            select(Room.id).where(
                Room.id == room_id,
                Room.organization_id == user.organization_id,
                Room.is_active.is_(True),
                Room.is_test_data.is_(True),
                *(
                    [Room.kind == "custom"]
                    if password_member_scope
                    else [Room.kind.in_(("custom", "self"))]
                    if mentor_full_review
                    else [Room.name == settings.reviewer_chat_room_name]
                ),
            )
        )
        if allowed_room is None:
            return None
    return db.scalar(
        select(RoomMembership).where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.room_id == room_id,
            RoomMembership.left_at.is_(None),
        )
    )


def room_member_user_ids(db: Session, room_id: UUID) -> set[UUID]:
    user_ids = set(
        db.scalars(
            select(User.id)
            .join(Staff, Staff.id == User.staff_id)
            .join(RoomMembership, RoomMembership.staff_id == Staff.id)
            .where(
                RoomMembership.room_id == room_id,
                RoomMembership.left_at.is_(None),
                User.is_active.is_(True),
                User.organization_id == Staff.organization_id,
                Staff.is_active.is_(True),
                Staff.deleted_at.is_(None),
                Staff.employment_status == "active",
            )
        ).all()
    )
    if settings.reviewer_access_active:
        room = db.get(Room, room_id)
        allowed_reviewer_usernames: set[str] = set()
        if room is not None and room.is_test_data:
            if room.name == settings.reviewer_chat_room_name:
                allowed_reviewer_usernames.update(
                    username
                    for username in (
                        settings.reviewer_care_username,
                        settings.reviewer_social_username,
                        settings.reviewer_secondary_username,
                    )
                    if username
                )
            if room.kind == "custom" and settings.reviewer_password_access_active:
                password_username = settings.reviewer_username_for_experience(
                    settings.reviewer_password_login_experience
                )
                if password_username:
                    allowed_reviewer_usernames.add(password_username)
        blocked_reviewer_usernames = set(settings.reviewer_usernames).difference(
            allowed_reviewer_usernames
        )
        if blocked_reviewer_usernames:
            blocked_reviewer_user_ids = set(
                db.scalars(
                    select(User.id).where(User.username.in_(blocked_reviewer_usernames))
                ).all()
            )
            user_ids.difference_update(blocked_reviewer_user_ids)
    return user_ids


def list_user_rooms(db: Session, user_id: UUID) -> list[RoomResponse]:
    user = db.get(User, user_id)
    if user is None or user.staff_id is None:
        return []
    membership_query = (
        select(RoomMembership)
        .options(selectinload(RoomMembership.room))
        .join(Room, Room.id == RoomMembership.room_id)
        .where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
            Room.is_active.is_(True),
        )
    )
    if getattr(user, "_reviewer_experience", None) is not None:
        reviewer_experience = getattr(user, "_reviewer_experience")
        mentor_full_review = (
            reviewer_experience == MENTOR_FULL_REVIEW_EXPERIENCE
        )
        password_member_scope = bool(
            settings.reviewer_password_access_active
            and reviewer_experience == settings.reviewer_password_login_experience
            and (settings.mentor_reviewer_username or "").strip()
            and not mentor_full_review
        )
        membership_query = membership_query.where(
            Room.is_test_data.is_(True),
            *(
                [Room.kind == "custom"]
                if password_member_scope
                else [Room.kind.in_(("custom", "self"))]
                if mentor_full_review
                else [Room.name == settings.reviewer_chat_room_name]
            ),
        )
    memberships = db.scalars(membership_query).all()
    if not memberships:
        return []
    room_ids = [membership.room_id for membership in memberships]
    message_filters = [Message.room_id.in_(room_ids)]
    if getattr(user, "_reviewer_experience", None) is not None:
        message_filters.append(Message.is_test_data.is_(True))

    ranked_messages = (
        select(
            Message.id.label("message_id"),
            Message.room_id.label("room_id"),
            func.row_number()
            .over(
                partition_by=Message.room_id,
                order_by=(Message.created_at.desc(), Message.id.desc()),
            )
            .label("position"),
        )
        .where(*message_filters)
        .subquery()
    )
    last_messages = db.scalars(
        select(Message)
        .join(ranked_messages, ranked_messages.c.message_id == Message.id)
        .where(ranked_messages.c.position == 1)
        .options(selectinload(Message.attachments))
    ).unique().all()
    last_message_by_room = {message.room_id: message for message in last_messages}

    read_exists = exists(
        select(MessageReadReceipt.id).where(
            MessageReadReceipt.message_id == Message.id,
            MessageReadReceipt.user_id == user_id,
        )
    )
    unread_by_room = {
        room_id: int(count)
        for room_id, count in db.execute(
            select(Message.room_id, func.count(Message.id))
            .where(
                *message_filters,
                Message.sender_id != user_id,
                ~read_exists,
            )
            .group_by(Message.room_id)
        ).all()
    }
    result: list[RoomResponse] = []
    for membership in memberships:
        last_message = last_message_by_room.get(membership.room_id)
        result.append(
            RoomResponse(
                id=membership.room.id,
                name=membership.room.name,
                kind=membership.room.kind,
                unread_count=unread_by_room.get(membership.room_id, 0),
                last_message=_room_last_message_preview(db, last_message),
                last_message_at=as_utc(last_message.created_at) if last_message else None,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.kind != "ai",
            item.last_message_at is not None,
            item.last_message_at or datetime.min.replace(tzinfo=timezone.utc),
            str(item.id),
        ),
        reverse=True,
    )


def attachment_access_capabilities(
    db: Session | None,
    attachment: MessageAttachment,
    viewer_id: UUID | None,
) -> dict[str, bool]:
    """Return a single server-side decision for every attachment action."""

    denied = {
        "can_download_original": False,
        "can_view_reviewed_text": False,
        "can_review_text": False,
        "can_request_reading": False,
    }
    if db is None or viewer_id is None:
        return denied
    viewer = db.get(User, viewer_id)
    message = attachment.message
    if (
        viewer is None
        or message is None
        or attachment.organization_id != viewer.organization_id
        or message.organization_id != viewer.organization_id
        or message.lifecycle_status != "active"
    ):
        return denied

    reviewer_session = getattr(viewer, "_reviewer_experience", None) is not None
    admin_access = viewer.role == "admin" and not reviewer_session
    room_access = active_membership(db, viewer.id, message.room_id) is not None
    is_uploader = attachment.uploader_id == viewer.id
    work_access = False
    if (
        not admin_access
        and not room_access
        and viewer.can_process_records
        and not reviewer_session
    ):
        item = db.scalar(
            select(WorkItem).where(WorkItem.source_message_id == message.id)
        )
        sender = message.sender
        work_access = bool(
            item is not None
            and item.organization_id == viewer.organization_id
            and viewer.business is not None
            and sender.business is not None
            and sender.business.id == viewer.business.id
        )

    # A former room member must not regain their own upload through the old
    # work-item row after the room membership is revoked.
    message_access = admin_access or room_access or (work_access and not is_uploader)
    reviewable = not attachment.mime_type.startswith("video/")
    can_manage_text = bool(
        message_access
        and reviewable
        and (
            admin_access
            or (is_uploader and room_access)
            or (viewer.can_process_records and (room_access or work_access))
        )
    )
    return {
        "can_download_original": message_access,
        "can_view_reviewed_text": message_access,
        "can_review_text": can_manage_text,
        "can_request_reading": can_manage_text,
    }


def create_employee(db: Session, values: dict, actor_id: UUID) -> User:
    values = _finalist_coded_staff_identity(dict(values))
    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="관리자 계정을 확인할 수 없습니다.")
    validate_unit_assignments(db, values, actor.organization_id)
    if db.scalar(select(User).where(User.username == values["username"])):
        raise HTTPException(status_code=409, detail="이미 사용 중인 로그인 아이디입니다.")

    employee_code = values.get("employee_code") or f"CHAT-{uuid4().hex[:10].upper()}"
    if db.scalar(
        select(Staff).where(
            Staff.organization_id == actor.organization_id,
            Staff.internal_code == employee_code,
        )
    ):
        raise HTTPException(status_code=409, detail="이미 사용 중인 직원번호입니다.")

    password = values.pop("password")
    role_code = values.pop("role")
    job_code = values.pop("job_code")
    position_title = values.pop("position_title", None)
    can_process_records = values.pop("can_process_records")
    full_name = values.pop("full_name")
    username = values.pop("username")
    values.pop("employee_code", None)

    job = db.get(StaffJobCode, job_code) if job_code else None
    if job_code and (job is None or not job.is_active):
        raise HTTPException(status_code=422, detail="지정한 직종을 찾을 수 없습니다.")
    position_title = validate_position_title(
        db,
        actor.organization_id,
        position_title,
    )

    staff = Staff(
        organization_id=actor.organization_id,
        internal_code=employee_code,
        display_name=full_name,
        job_title=job.name if job is not None else "직종 미지정",
        employment_status="active",
        is_active=True,
        is_test_data=settings.environment != "production",
    )
    db.add(staff)
    db.flush()
    user = User(
        organization_id=actor.organization_id,
        staff_id=staff.id,
        staff=staff,
        username=username,
        display_name=full_name,
        password_hash=hash_password(password),
        must_change_password=True,
        can_process_records=can_process_records,
        is_active=True,
    )
    db.add(user)
    db.flush()
    set_user_role(db, user, role_code)
    if job_code:
        set_staff_job(db, staff, job_code, actor_id)
    set_staff_position_title(staff, position_title, actor_id)
    set_staff_unit_assignments(db, staff, values, actor_id)
    sync_auto_memberships(db, user)
    record_audit(
        db,
        actor_id=actor_id,
        action="employee.created",
        target_type="staff",
        target_id=staff.id,
        details={"username": user.username, "role": user.role},
    )
    db.commit()
    db.refresh(user)
    return user


def create_staff_directory_entry(
    db: Session,
    values: dict,
    actor_id: UUID,
) -> Staff:
    """Create a staff identity without issuing a login account."""

    values = _finalist_coded_staff_identity(dict(values))
    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="관리자 계정을 확인할 수 없습니다.")

    full_name = str(values.get("full_name") or "").strip()
    if len(full_name) < 2:
        raise HTTPException(status_code=422, detail="직원 이름은 2자 이상 입력해 주세요.")

    employee_code = str(values.get("employee_code") or "").strip()
    if not employee_code:
        employee_code = f"CHAT-{uuid4().hex[:10].upper()}"
    if db.scalar(
        select(Staff).where(
            Staff.organization_id == actor.organization_id,
            Staff.internal_code == employee_code,
        )
    ):
        raise HTTPException(status_code=409, detail="이미 사용 중인 직원번호입니다.")

    staff = Staff(
        organization_id=actor.organization_id,
        internal_code=employee_code,
        display_name=full_name,
        job_title="직종 미지정",
        employment_status="active",
        is_active=True,
        is_test_data=settings.environment != "production",
    )
    db.add(staff)
    db.flush()
    record_audit(
        db,
        actor_id=actor_id,
        action="staff.created",
        target_type="staff",
        target_id=staff.id,
        details={"source": "admin_direct", "login_issued": False},
    )
    db.commit()
    db.refresh(staff)
    return staff


def update_staff_directory_entry(
    db: Session,
    staff_id: UUID,
    values: dict,
    actor_id: UUID,
) -> tuple[Staff, User | None]:
    """Update staff identity fields without requiring a login account."""

    if (
        settings.environment == "development"
        and settings.database_schema == "smcodi_finalist"
        and ({"full_name", "employee_code"} & set(values))
    ):
        raise HTTPException(
            status_code=409,
            detail="본선 DEV에서 한 번 부여한 직원 코드는 변경하거나 재사용할 수 없습니다.",
        )
    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="관리자 계정을 확인할 수 없습니다.")

    staff = db.scalar(
        select(Staff)
        .where(
            Staff.id == staff_id,
            Staff.organization_id == actor.organization_id,
            Staff.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if staff is None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if not staff.is_active or staff.employment_status != "active":
        raise HTTPException(status_code=409, detail="재직 중인 직원만 수정할 수 있습니다.")

    login_user = db.scalar(
        select(User).where(
            User.staff_id == staff.id,
            User.organization_id == staff.organization_id,
        )
    )
    changed_fields: list[str] = []

    if "full_name" in values:
        full_name = str(values.get("full_name") or "").strip()
        if len(full_name) < 2:
            raise HTTPException(status_code=422, detail="직원 이름은 2자 이상 입력해 주세요.")
        if staff.display_name != full_name:
            staff.display_name = full_name
            if login_user is not None:
                login_user.display_name = full_name
            changed_fields.append("full_name")

    if "employee_code" in values:
        employee_code = str(values.get("employee_code") or "").strip()
        if not employee_code:
            raise HTTPException(status_code=422, detail="직원번호를 비울 수 없습니다.")
        duplicate_staff_id = db.scalar(
            select(Staff.id).where(
                Staff.organization_id == staff.organization_id,
                Staff.internal_code == employee_code,
                Staff.id != staff.id,
                Staff.deleted_at.is_(None),
            )
        )
        if duplicate_staff_id is not None:
            raise HTTPException(status_code=409, detail="이미 사용 중인 직원번호입니다.")
        if staff.internal_code != employee_code:
            staff.internal_code = employee_code
            changed_fields.append("employee_code")

    if changed_fields:
        record_audit(
            db,
            actor_id=actor_id,
            action="staff.updated",
            target_type="staff",
            target_id=staff.id,
            details={
                "source": "admin_direct",
                "changed_fields": sorted(changed_fields),
                "login_display_name_synced": (
                    "full_name" in changed_fields and login_user is not None
                ),
            },
        )
    db.commit()
    db.refresh(staff)
    if login_user is not None:
        db.refresh(login_user)
    return staff, login_user


def terminate_staff_directory_entry(
    db: Session,
    staff_id: UUID,
    actor_id: UUID,
) -> tuple[Staff, User | None, bool]:
    """Retire a staff identity while preserving historical records.

    A login account is optional. When one exists, active sessions are revoked;
    room memberships are closed by staff identity in either case.
    """

    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="관리자 계정을 확인할 수 없습니다.")

    staff = db.scalar(
        select(Staff)
        .where(
            Staff.id == staff_id,
            Staff.organization_id == actor.organization_id,
            Staff.deleted_at.is_(None),
        )
        .with_for_update()
    )
    if staff is None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")

    login_user = db.scalar(
        select(User).where(
            User.staff_id == staff.id,
            User.organization_id == staff.organization_id,
        )
    )
    if login_user is not None and login_user.id == actor.id:
        raise HTTPException(
            status_code=409,
            detail="현재 로그인한 관리자 자신은 퇴사 처리할 수 없습니다.",
        )
    if staff.employment_status == "retired":
        return staff, login_user, False
    if staff.employment_status != "active" or not staff.is_active:
        raise HTTPException(status_code=409, detail="재직 중인 직원만 퇴사 처리할 수 있습니다.")

    now = utcnow()
    staff.employment_status = "retired"
    staff.is_active = False
    staff.terminated_at = now
    if login_user is not None:
        login_user.is_active = False

    sessions = []
    if login_user is not None:
        sessions = db.scalars(
            select(LoginSession).where(
                LoginSession.user_id == login_user.id,
                LoginSession.revoked_at.is_(None),
            )
        ).all()
        for login_session in sessions:
            login_session.revoked_at = now

    memberships = db.scalars(
        select(RoomMembership).where(
            RoomMembership.staff_id == staff.id,
            RoomMembership.left_at.is_(None),
        )
    ).all()
    for membership in memberships:
        membership.left_at = now

    record_audit(
        db,
        actor_id=actor_id,
        action="staff.terminated",
        target_type="staff",
        target_id=staff.id,
        details={
            "source": "admin_direct",
            "login_disabled": login_user is not None,
            "revoked_sessions": len(sessions),
            "closed_memberships": len(memberships),
        },
    )
    db.commit()
    db.refresh(staff)
    if login_user is not None:
        db.refresh(login_user)
    return staff, login_user, True


def issue_existing_staff_login(
    db: Session,
    *,
    staff: Staff,
    username: str,
    temporary_password: str,
    actor_id: UUID,
    role_code: str = "staff",
    can_process_records: bool = False,
) -> User:
    """Attach a new login to an existing active staff row without committing.

    The caller owns the transaction so a batch issuer can roll back the whole
    operation if any selected staff account fails validation.
    """

    actor = db.get(User, actor_id)
    if actor is None:
        raise HTTPException(status_code=403, detail="관리자 계정을 확인할 수 없습니다.")

    target_staff = db.get(Staff, staff.id)
    if target_staff is None or target_staff.deleted_at is not None:
        raise HTTPException(status_code=404, detail="직원을 찾을 수 없습니다.")
    if target_staff.organization_id != actor.organization_id:
        raise HTTPException(status_code=404, detail="같은 기관의 직원만 처리할 수 있습니다.")
    if not target_staff.is_active or target_staff.employment_status != "active":
        raise HTTPException(status_code=409, detail="재직 중인 직원만 로그인할 수 있습니다.")
    if db.scalar(select(User.id).where(User.staff_id == target_staff.id)) is not None:
        raise HTTPException(status_code=409, detail="이미 로그인이 연결된 직원입니다.")

    normalized_username = username.strip()
    if not re.fullmatch(LOGIN_USERNAME_PATTERN, normalized_username):
        raise HTTPException(status_code=422, detail="로그인 아이디 형식이 올바르지 않습니다.")
    if db.scalar(select(User.id).where(User.username == normalized_username)) is not None:
        raise HTTPException(status_code=409, detail="이미 사용 중인 로그인 아이디입니다.")
    if not 6 <= len(temporary_password) <= 200:
        raise HTTPException(
            status_code=422,
            detail="임시 비밀번호는 6자 이상 200자 이하로 입력해 주세요.",
        )

    user = User(
        organization_id=target_staff.organization_id,
        staff_id=target_staff.id,
        staff=target_staff,
        username=normalized_username,
        display_name=target_staff.display_name,
        password_hash=hash_password(temporary_password),
        must_change_password=True,
        can_process_records=can_process_records,
        is_active=True,
    )
    db.add(user)
    db.flush()
    set_user_role(db, user, role_code)
    sync_auto_memberships(db, user)
    record_audit(
        db,
        actor_id=actor_id,
        action="staff.login_issued",
        target_type="staff",
        target_id=target_staff.id,
        details={
            "username": normalized_username,
            "role": role_code,
            "can_process_records": can_process_records,
        },
    )
    db.flush()
    return user


def ensure_bootstrap_admin(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str,
) -> User:
    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        return existing
    organization = ensure_reference_data(db)
    staff = Staff(
        organization_id=organization.id,
        internal_code="CHAT-ADMIN-001",
        display_name=display_name,
        job_title="직종 미지정",
        employment_status="active",
        is_active=True,
        is_test_data=settings.environment != "production",
    )
    db.add(staff)
    db.flush()
    user = User(
        organization_id=organization.id,
        staff_id=staff.id,
        staff=staff,
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        is_active=True,
        can_process_records=True,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    set_user_role(db, user, "admin")
    sync_auto_memberships(db, user)
    record_audit(
        db,
        actor_id=user.id,
        action="system.bootstrap_admin",
        target_type="user",
        target_id=user.id,
    )
    return user


def ensure_developer_launcher_user(
    db: Session,
    *,
    username: str,
    password: str,
    display_name: str,
) -> User:
    """개발환경에서만 쓰는 사용자 전환 전용 계정을 멱등 생성합니다."""

    existing = db.scalar(select(User).where(User.username == username))
    if existing is not None:
        existing.password_hash = hash_password(password)
        existing.is_active = True
        existing.must_change_password = False
        existing.can_process_records = False
        if existing.staff is not None:
            existing.staff.is_active = True
            existing.staff.employment_status = "active"
        set_user_role(db, existing, "admin")
        return existing

    organization = ensure_reference_data(db)
    staff = Staff(
        organization_id=organization.id,
        internal_code="CHAT-DEV-LAUNCHER",
        display_name=display_name,
        job_title="개발 시험 도구",
        employment_status="active",
        is_active=True,
        is_test_data=True,
    )
    db.add(staff)
    db.flush()
    user = User(
        organization_id=organization.id,
        staff_id=staff.id,
        staff=staff,
        username=username,
        display_name=display_name,
        password_hash=hash_password(password),
        is_active=True,
        can_process_records=False,
        must_change_password=False,
    )
    db.add(user)
    db.flush()
    set_user_role(db, user, "admin")
    record_audit(
        db,
        actor_id=user.id,
        action="system.dev_launcher_created",
        target_type="user",
        target_id=user.id,
        details={"development_only": True},
    )
    return user
