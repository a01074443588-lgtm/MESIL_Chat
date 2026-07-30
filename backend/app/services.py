from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from .config import settings
from .ocr import find_spelling_candidates
from .ocr_corrections import (
    CorrectionEvidence,
    PROTECTED_CONTENT_TYPES,
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
    OcrCorrectionEvent,
    OcrCorrectionMemory,
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
    User,
    utcnow,
)
from .schemas import (
    ActionItemResponse,
    AttachmentResponse,
    AttachmentTextExtractionResponse,
    MessageResidentLinkResponse,
    MessageResponse,
    ForwardedMessageSource,
    OrgUnitResponse,
    ResidentResponse,
    RoomResponse,
    UserResponse,
)
from .security import hash_password


AUTO_UNIT_FIELDS = {
    "business": "business_id",
    "department": "department_id",
    "floor": "floor_id",
    "team": "team_id",
}
ROOM_LABELS = {
    "business": "전체방",
    "department": "전체방",
    "floor": "직원방",
    "team": "방",
}
USER_UNIT_FIELDS = {field_name: unit_type for unit_type, field_name in AUTO_UNIT_FIELDS.items()}

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
]

DEFAULT_POSITION_TITLES = [
    ("representative", "대표"),
    ("director", "원장"),
    ("office_director", "사무국장"),
    ("senior_social_worker", "선임사회복지사"),
    ("nursing_team_lead", "간호팀장"),
    ("care_team_lead", "요양팀장"),
]


def as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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
    return organization


def unit_response(unit: OrgUnit | None) -> OrgUnitResponse | None:
    if unit is None:
        return None
    return OrgUnitResponse.model_validate(unit)


def user_response(
    user: User,
    *,
    is_dev_launcher: bool = False,
    is_dev_impersonated: bool = False,
    reviewer_experience: str | None = None,
) -> UserResponse:
    reviewer_public_usernames = {
        "care": "reviewer-care",
        "social_worker": "reviewer-social",
        "realtime_secondary": "reviewer-realtime-secondary",
    }
    public_username = (
        reviewer_public_usernames.get(reviewer_experience, "reviewer")
        if reviewer_experience is not None
        else user.username
    )
    return UserResponse(
        id=user.id,
        username=public_username,
        full_name=user.full_name,
        role=user.role,
        can_process_records=user.can_process_records,
        employment_status=user.employment_status,
        must_change_password=user.must_change_password,
        employee_code=user.employee_code,
        business=unit_response(user.business),
        department=unit_response(user.department),
        job_code=user.job_code,
        job_name=user.job_name,
        position_title=user.position_title,
        floor=unit_response(user.floor),
        team=unit_response(user.team),
        terminated_at=as_utc(user.terminated_at),
        is_dev_launcher=is_dev_launcher,
        is_dev_impersonated=is_dev_impersonated,
        is_reviewer_session=reviewer_experience is not None,
        reviewer_experience=reviewer_experience,
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
        sort_order=resident.sort_order,
        is_priority=is_priority,
        roster_source=roster_source,
    )


def message_resident_link_response(
    link: MessageResidentLink,
) -> MessageResidentLinkResponse:
    return MessageResidentLinkResponse(
        resident=resident_response(link.resident),
        source=link.source,
        status=link.status,
        reviewed_at=as_utc(link.reviewed_at) if link.reviewed_at else None,
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
) -> MessageResponse:
    comments = list(message.comments)
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
    if read_count is None:
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
    if reply_user_count is None:
        reply_user_count = len({comment.author_id for comment in comments})
    forwarded_from = None
    if message.extra_data:
        raw_forwarded = message.extra_data.get("forwarded_from")
        if isinstance(raw_forwarded, dict):
            try:
                forwarded_from = ForwardedMessageSource.model_validate(raw_forwarded)
            except ValueError:
                forwarded_from = None
    return MessageResponse(
        id=message.id,
        room_id=message.room_id,
        sender_id=message.sender_id,
        sender_name=message.sender.full_name,
        message_type=message.message_type,
        body=message.body,
        resident=resident_response(message.resident),
        resident_links=[
            message_resident_link_response(link)
            for link in message.resident_links
            if link.status != "rejected"
        ],
        resident_ref=message.resident_ref,
        attachments=[
            attachment_response(attachment, db=db)
            for attachment in message.attachments
        ],
        comment_count=len(comments),
        unread_comment_count=unread_comment_count,
        read_count=read_count,
        reply_user_count=reply_user_count,
        action_item=action_item_response(message.action_item),
        forwarded_from=forwarded_from,
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
) -> AttachmentResponse:
    extraction = attachment.text_extraction
    correction_pairs: list[tuple[str, str]] = []
    correction_candidates: list[dict] = []
    latest_correction_event: OcrCorrectionEvent | None = None
    correction_event_count = 0
    if db is not None and extraction is not None:
        memories = db.scalars(
            select(OcrCorrectionMemory)
            .where(
                OcrCorrectionMemory.organization_id
                == attachment.message.organization_id
            )
            .order_by(
                OcrCorrectionMemory.occurrence_count.desc(),
                OcrCorrectionMemory.updated_at.desc(),
            )
            .limit(200)
        ).all()
        correction_pairs = [
            (memory.recognized_text, memory.corrected_text)
            for memory in memories
        ]
        events = db.scalars(
            select(OcrCorrectionEvent)
            .where(
                OcrCorrectionEvent.organization_id
                == attachment.message.organization_id,
                OcrCorrectionEvent.confirmed.is_(True),
            )
            .order_by(OcrCorrectionEvent.created_at.desc())
            .limit(300)
        ).all()
        evidences: list[CorrectionEvidence] = []
        for event in events:
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
                    )
                )
        correction_candidates = suggest_from_confirmed_events(
            extraction.extracted_text,
            context_text=extraction.extracted_text,
            source_writer_id=attachment.message.sender_id,
            visual_signature=extraction.visual_signature,
            evidences=evidences,
            resident_names=[
                resident.display_name
                for resident in db.scalars(
                    select(Resident).where(
                        Resident.organization_id
                        == attachment.message.organization_id,
                        Resident.is_active.is_(True),
                    )
                ).all()
            ],
        )
        latest_correction_event = db.scalar(
            select(OcrCorrectionEvent)
            .where(OcrCorrectionEvent.extraction_id == extraction.id)
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
    seen_candidates = {
        (candidate["recognized"], candidate["candidate"])
        for candidate in correction_candidates
    }
    for legacy_candidate in find_spelling_candidates(
        extraction.extracted_text if extraction is not None else None,
        preferred_terms=preferred_resident_names,
        correction_pairs=correction_pairs,
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
                "confidence": 0.55,
                "support_count": 0,
                "content_type": content_type,
                "is_protected": content_type in PROTECTED_CONTENT_TYPES,
                "source": "institution_lexicon",
                "reason": "기관 어휘와 유사",
                "source_event_ids": [],
                "auto_applicable": False,
            }
        )
        seen_candidates.add((recognized, candidate))
        if len(correction_candidates) >= 8:
            break
    return AttachmentResponse(
        id=attachment.id,
        original_name=attachment.original_name,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        download_url=f"/api/attachments/{attachment.id}",
        text_extraction=(
            AttachmentTextExtractionResponse(
                status=extraction.status,
                provider=extraction.provider,
                model_name=extraction.model_name,
                extracted_text=extraction.extracted_text,
                original_extracted_text=(
                    extraction.original_extracted_text or extraction.extracted_text
                ),
                reviewed_text=extraction.reviewed_text,
                error_message=extraction.error_message,
                completed_at=(
                    as_utc(extraction.completed_at)
                    if extraction.completed_at is not None
                    else None
                ),
                reviewed_at=(
                    as_utc(extraction.reviewed_at)
                    if extraction.reviewed_at is not None
                    else None
                ),
                review_decision=(
                    latest_correction_event.decision
                    if latest_correction_event is not None
                    else None
                ),
                correction_event_count=correction_event_count,
                spelling_candidates=correction_candidates[:8],
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
        return unit is not None and unit.is_active and unit.id == room.scope_unit_id
    if room.kind == "job":
        assignment = staff.current_job()
        return (
            assignment is not None
            and assignment.job.is_active
            and assignment.job_code == room.job_code
        )
    if room.kind == "self":
        return room.owner_staff_id == staff.id
    return False


def sync_auto_memberships(db: Session, user: User) -> None:
    if user.staff is None:
        return
    now = utcnow()
    staff = user.staff
    desired_room_ids: set[UUID] = set()
    if user.is_active and staff.employment_status == "active":
        self_room = ensure_self_room(db, user)
        if self_room is not None and self_room.is_active:
            desired_room_ids.add(self_room.id)
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
        allowed_room = db.scalar(
            select(Room.id).where(
                Room.id == room_id,
                Room.organization_id == user.organization_id,
                Room.is_active.is_(True),
                Room.is_test_data.is_(True),
                Room.name == settings.reviewer_chat_room_name,
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
            )
        ).all()
    )
    if settings.reviewer_access_active:
        room = db.get(Room, room_id)
        if room is None or room.name != settings.reviewer_chat_room_name:
            reviewer_usernames = {
                username
                for username in (
                    settings.reviewer_care_username,
                    settings.reviewer_social_username,
                    settings.reviewer_secondary_username,
                )
                if username
            }
            if reviewer_usernames:
                reviewer_user_ids = set(
                    db.scalars(
                        select(User.id).where(
                            User.username.in_(reviewer_usernames)
                        )
                    ).all()
                )
                user_ids.difference_update(reviewer_user_ids)
    return user_ids


def list_user_rooms(db: Session, user_id: UUID) -> list[RoomResponse]:
    user = db.get(User, user_id)
    if user is None or user.staff_id is None:
        return []
    membership_query = (
        select(RoomMembership)
        .join(Room, Room.id == RoomMembership.room_id)
        .where(
            RoomMembership.staff_id == user.staff_id,
            RoomMembership.left_at.is_(None),
            Room.is_active.is_(True),
        )
    )
    if getattr(user, "_reviewer_experience", None) is not None:
        membership_query = membership_query.where(
            Room.name == settings.reviewer_chat_room_name,
            Room.is_test_data.is_(True),
        )
    memberships = db.scalars(membership_query).all()
    result: list[RoomResponse] = []
    for membership in memberships:
        message_filters = [Message.room_id == membership.room_id]
        if getattr(user, "_reviewer_experience", None) is not None:
            message_filters.append(Message.is_test_data.is_(True))
        last_message = db.scalar(
            select(Message)
            .where(*message_filters)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(1)
        )
        read_exists = exists(
            select(MessageReadReceipt.id).where(
                MessageReadReceipt.message_id == Message.id,
                MessageReadReceipt.user_id == user_id,
            )
        )
        unread_count = db.scalar(
            select(func.count(Message.id)).where(
                *message_filters,
                Message.sender_id != user_id,
                ~read_exists,
            )
        )
        result.append(
            RoomResponse(
                id=membership.room.id,
                name=membership.room.name,
                kind=membership.room.kind,
                unread_count=int(unread_count or 0),
                last_message=last_message.body if last_message else None,
                last_message_at=as_utc(last_message.created_at) if last_message else None,
            )
        )
    return sorted(
        result,
        key=lambda item: (
            item.last_message_at is not None,
            item.last_message_at or datetime.min.replace(tzinfo=timezone.utc),
            str(item.id),
        ),
        reverse=True,
    )


def create_employee(db: Session, values: dict, actor_id: UUID) -> User:
    values = dict(values)
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
    set_staff_position_title(staff, "원장", user.id)
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
