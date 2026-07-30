from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


UnitType = Literal["business", "department", "floor", "team"]


class LoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=80)
    password: str = Field(min_length=1, max_length=200)


class ReviewerSessionRequest(BaseModel):
    experience: Literal["care", "social_worker", "realtime_secondary"]


class OrgUnitCreate(BaseModel):
    unit_type: UnitType
    name: str = Field(min_length=1, max_length=100)
    code: str | None = Field(default=None, max_length=80)

    @field_validator("name", "code")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class OrgUnitUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class OrgUnitResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    unit_type: str
    name: str
    code: str | None
    is_active: bool
    active_staff_count: int = 0
    active_room_count: int = 0
    reference_count: int = 0
    can_delete: bool = False


class JobCodeCreate(BaseModel):
    code: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]+$",
        min_length=2,
        max_length=80,
    )
    name: str = Field(min_length=1, max_length=100)

    @field_validator("code", "name")
    @classmethod
    def strip_job_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class JobCodeUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class JobCodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    code: str
    name: str
    sort_order: int
    is_active: bool
    active_staff_count: int = 0
    active_room_count: int = 0
    reference_count: int = 0
    can_delete: bool = False


class PositionTitleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_position_name(cls, value: str) -> str:
        return value.strip()


class PositionTitleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    is_active: bool | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class PositionTitleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    sort_order: int
    is_active: bool
    active_staff_count: int = 0
    reference_count: int = 0
    can_delete: bool = False


class EmployeeCreate(BaseModel):
    username: str = Field(pattern=r"^[a-zA-Z0-9._-]+$", min_length=3, max_length=80)
    full_name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=12, max_length=200)
    role: Literal["admin", "staff"] = "staff"
    can_process_records: bool = False
    employee_code: str | None = Field(default=None, max_length=80)
    job_code: str | None = Field(default=None, min_length=2, max_length=80)
    position_title: str | None = Field(default=None, max_length=100)
    business_id: UUID | None = None
    department_id: UUID | None = None
    floor_id: UUID | None = None
    team_id: UUID | None = None


class EmployeeUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=100)
    role: Literal["admin", "staff"] | None = None
    can_process_records: bool | None = None
    employee_code: str | None = Field(default=None, max_length=80)
    job_code: str | None = Field(default=None, min_length=2, max_length=80)
    position_title: str | None = Field(default=None, max_length=100)
    business_id: UUID | None = None
    department_id: UUID | None = None
    floor_id: UUID | None = None
    team_id: UUID | None = None


class UserResponse(BaseModel):
    id: UUID
    username: str
    full_name: str
    role: str
    can_process_records: bool
    employment_status: Literal["active", "leave", "retired"]
    must_change_password: bool
    employee_code: str | None
    business: OrgUnitResponse | None
    department: OrgUnitResponse | None
    job_code: str | None
    job_name: str | None
    position_title: str | None
    floor: OrgUnitResponse | None
    team: OrgUnitResponse | None
    terminated_at: datetime | None
    is_dev_launcher: bool = False
    is_dev_impersonated: bool = False
    is_reviewer_session: bool = False
    reviewer_experience: (
        Literal["care", "social_worker", "realtime_secondary"] | None
    ) = None


class CustomRoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    member_ids: list[UUID] = Field(min_length=1, max_length=100)


class CustomRoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    member_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)


class ManagedCustomRoomResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool
    member_ids: list[UUID]
    created_at: datetime


RoomKind = Literal["all", "business", "department", "floor", "team", "job", "custom"]
ResidentScope = Literal["all", "facility", "daycare", "homecare", "floor"]


class ManagedRoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    kind: RoomKind
    scope_unit_id: UUID | None = None
    job_code: str | None = Field(default=None, max_length=80)
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)
    resident_scope: ResidentScope = "all"
    resident_scope_unit_id: UUID | None = None

    @field_validator("name", "job_code")
    @classmethod
    def strip_room_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ManagedRoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    member_ids: list[UUID] | None = Field(default=None, max_length=100)
    resident_scope: ResidentScope | None = None
    resident_scope_unit_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ManagedRoomResponse(BaseModel):
    id: UUID
    name: str
    kind: str
    is_active: bool
    scope_unit_id: UUID | None
    scope_name: str | None
    job_code: str | None
    job_name: str | None
    member_ids: list[UUID]
    member_count: int
    resident_scope: str
    resident_scope_unit_id: UUID | None
    resident_scope_name: str | None
    message_count: int
    created_at: datetime


class RoomResponse(BaseModel):
    id: UUID
    name: str
    kind: str
    unread_count: int
    last_message: str | None
    last_message_at: datetime | None


class ResidentResponse(BaseModel):
    id: UUID
    display_name: str
    service_type: str
    floor: OrgUnitResponse | None
    sort_order: int
    is_priority: bool = False
    roster_source: Literal["carefor", "smcodi", "manual", "demo"]


class ResidentAdminCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=100)
    service_type: Literal["facility", "daycare", "homecare"]
    floor_id: UUID | None = None

    @field_validator("display_name")
    @classmethod
    def strip_resident_name(cls, value: str) -> str:
        return value.strip()


class CareforStaffAliasResponse(BaseModel):
    display_name: str
    service_type: Literal["facility", "daycare", "homecare"]
    status: str
    job_name: str
    is_active: bool


class CareforRosterSourceStatus(BaseModel):
    status: Literal["captured", "login_required", "missing"]
    captured_at: datetime | None = None
    resident_count: int = 0
    staff_count: int = 0
    staff_aliases: list[CareforStaffAliasResponse] = Field(default_factory=list)


class CareforRosterStatusResponse(BaseModel):
    generated_at: datetime | None = None
    sources: dict[
        Literal["facility", "daycare", "homecare"],
        CareforRosterSourceStatus,
    ]


class MessageResidentLinkResponse(BaseModel):
    resident: ResidentResponse
    source: Literal["manual", "text_exact", "ocr_exact", "audio_transcript"]
    status: Literal["candidate", "confirmed", "rejected"]
    reviewed_at: datetime | None


class MessageResidentLinkUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]


class WorkItemResidentUpdate(BaseModel):
    resident_id: UUID


class ResidentOrderUpdate(BaseModel):
    resident_ids: list[UUID] = Field(min_length=1, max_length=500)


class ResidentSyncItemResponse(BaseModel):
    id: UUID
    external_id: str
    change_type: Literal["new", "update", "deactivate", "unchanged", "conflict"]
    status: Literal["pending", "applied", "not_required", "blocked"]
    current_resident_id: UUID | None
    incoming_payload: dict
    current_snapshot: dict | None
    conflict_reason: str | None
    applied_at: datetime | None


class ResidentSyncBatchResponse(BaseModel):
    id: UUID
    source: str
    original_name: str
    file_sha256: str
    source_generated_at: datetime | None
    status: Literal["preview", "partially_applied", "applied"]
    summary: dict[str, int]
    created_by_name: str
    applied_by_name: str | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime
    items: list[ResidentSyncItemResponse] = Field(default_factory=list)


class ResidentSyncApplyRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=500)

    @field_validator("item_ids")
    @classmethod
    def unique_item_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("승인 항목에 중복된 값이 있습니다.")
        return value


class RoomMemberResponse(BaseModel):
    id: UUID
    full_name: str
    job_name: str | None
    floor: OrgUnitResponse | None
    team: OrgUnitResponse | None


class ActionAssigneeResponse(BaseModel):
    id: UUID
    full_name: str
    job_code: str | None
    job_name: str | None
    business: OrgUnitResponse | None
    department: OrgUnitResponse | None
    floor: OrgUnitResponse | None
    team: OrgUnitResponse | None
    can_process_records: bool
    is_room_member: bool


class OcrCorrectionCandidateResponse(BaseModel):
    id: str
    recognized: str
    candidate: str
    confidence: float = Field(ge=0, le=1)
    support_count: int = Field(ge=0)
    content_type: str
    is_protected: bool
    source: str
    reason: str
    source_event_ids: list[str] = Field(default_factory=list)
    auto_applicable: bool = False


class AttachmentTextExtractionResponse(BaseModel):
    status: Literal["pending", "processing", "completed", "failed", "reviewed"]
    provider: str
    model_name: str
    extracted_text: str | None
    original_extracted_text: str | None
    reviewed_text: str | None
    error_message: str | None
    completed_at: datetime | None
    reviewed_at: datetime | None
    review_decision: (
        Literal["keep_raw", "apply_candidate", "direct_edit", "needs_review"] | None
    ) = None
    correction_event_count: int = 0
    spelling_candidates: list[OcrCorrectionCandidateResponse] = Field(
        default_factory=list
    )


class AttachmentTextExtractionUpdate(BaseModel):
    reviewed_text: str | None = Field(default=None, max_length=12000)
    decision: Literal[
        "keep_raw",
        "apply_candidate",
        "direct_edit",
        "needs_review",
    ] = "direct_edit"
    selected_candidate_id: str | None = Field(default=None, max_length=120)

    @field_validator("reviewed_text")
    @classmethod
    def strip_reviewed_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_review_choice(self):
        if self.decision in {"apply_candidate", "direct_edit"} and not self.reviewed_text:
            raise ValueError("확인한 판독문을 입력해 주세요.")
        if self.decision == "apply_candidate" and not self.selected_candidate_id:
            raise ValueError("적용할 교정 후보를 선택해 주세요.")
        if self.decision != "apply_candidate" and self.selected_candidate_id is not None:
            raise ValueError("교정 후보 선택값은 후보 적용 시에만 사용할 수 있습니다.")
        return self


class AttachmentResponse(BaseModel):
    id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    download_url: str
    text_extraction: AttachmentTextExtractionResponse | None = None


class ActionItemCreate(BaseModel):
    action_type: Literal["handover", "cooperation", "confirmation"]
    assignee_user_id: UUID | None = None
    assignee_unit_id: UUID | None = None
    priority: Literal["normal", "important", "urgent"] = "normal"
    due_at: datetime | None = None


class ActionItemUpdate(BaseModel):
    status: Literal["assigned", "acknowledged", "in_progress", "completed"]


class ActionItemResponse(BaseModel):
    id: UUID
    source_message_id: UUID
    room_id: UUID
    room_name: str
    source_body: str
    sender_name: str
    resident_name: str | None
    comment_count: int
    action_type: str
    assignee_user_id: UUID | None
    assignee_user_name: str | None
    assignee_unit_id: UUID | None
    assignee_unit_name: str | None
    priority: str
    status: str
    due_at: datetime | None
    created_by_id: UUID
    created_by_name: str
    acknowledged_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class MessageCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)
    message_type: Literal[
        "chat",
        "notice",
        "handover",
        "work_request",
        "report",
    ] = "chat"
    resident_id: UUID | None = None
    resident_ref: str | None = Field(default=None, max_length=100)
    action: ActionItemCreate | None = None

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 메시지는 보낼 수 없습니다.")
        return value


class MessageForwardRequest(BaseModel):
    room_ids: list[UUID] = Field(default_factory=list, max_length=50)
    to_all_joined_rooms: bool = False

    @field_validator("room_ids")
    @classmethod
    def unique_room_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class ForwardedMessageSource(BaseModel):
    message_id: UUID
    room_name: str
    sender_name: str
    created_at: datetime


class MessageResponse(BaseModel):
    id: UUID
    room_id: UUID
    sender_id: UUID
    sender_name: str
    message_type: str
    body: str
    resident: ResidentResponse | None
    resident_links: list[MessageResidentLinkResponse] = Field(default_factory=list)
    resident_ref: str | None
    attachments: list[AttachmentResponse] = Field(default_factory=list)
    comment_count: int = 0
    unread_comment_count: int = 0
    read_count: int = 0
    reply_user_count: int = 0
    action_item: ActionItemResponse | None = None
    forwarded_from: ForwardedMessageSource | None = None
    created_at: datetime


class ReadRequest(BaseModel):
    message_id: UUID


class ReadReceiptResponse(BaseModel):
    user_id: UUID
    user_name: str
    read_at: datetime


class MessageCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=1000)

    @field_validator("body")
    @classmethod
    def strip_comment(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 댓글은 등록할 수 없습니다.")
        return value


class MessageCommentResponse(BaseModel):
    id: UUID
    author_id: UUID
    author_name: str
    body: str
    created_at: datetime


class MessageDetailResponse(BaseModel):
    message: MessageResponse
    read_receipts: list[ReadReceiptResponse]
    comments: list[MessageCommentResponse]


class RoomMessageSearchResponse(BaseModel):
    matched_count: int
    messages: list[MessageResponse]
    truncated: bool = False


class RoomSearchSummaryRequest(BaseModel):
    message_ids: list[UUID] = Field(min_length=1, max_length=200)

    @field_validator("message_ids")
    @classmethod
    def unique_message_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class RoomSearchSummaryResponse(BaseModel):
    summary: str
    source_message_ids: list[UUID]
    generator: str


DocumentType = Literal[
    "care_service_record",
    "integrated_assessment",
    "nursing_log",
    "care_plan",
    "care_plan_evaluation",
    "consultation_log",
    "physical_restraint_log",
    "program_log",
]
DailyDocumentType = Literal[
    "care_service_record",
    "nursing_log",
    "consultation_log",
    "physical_restraint_log",
    "program_log",
]
RecordUsageTag = Literal[
    "nursing",
    "care_service",
    "consultation",
    "program",
    "general",
    "needs_review",
]
WorkItemStatus = Literal["pending", "in_review", "ready", "dismissed"]
RecordClassification = Literal[
    "daily_care",
    "nutrition",
    "health",
    "safety",
    "consultation",
    "rehabilitation",
]
RiskLevel = Literal["low", "medium", "high", "urgent"]
TargetRole = Literal[
    "caregiver",
    "nurse",
    "social_worker",
    "director",
    "therapist",
    "nutritionist",
]


class WorkItemSourceSnapshot(BaseModel):
    message_id: UUID
    room_id: UUID
    room_name: str
    sender_id: UUID
    sender_name: str
    resident_id: UUID | None
    resident_name: str | None
    resident_names: list[str] = Field(default_factory=list)
    body: str
    message_type: str
    attachment_ids: list[UUID] = Field(default_factory=list)
    created_at: datetime


class DocumentDraftProposal(BaseModel):
    document_type: DailyDocumentType
    content: str = Field(min_length=1, max_length=8000)
    verification_questions: list[str] = Field(default_factory=list, max_length=12)


class RecordDraft(BaseModel):
    corrected_text: str = Field(min_length=1, max_length=2000)
    summary: str = Field(min_length=1, max_length=1000)
    observation_details: str = Field(default="", max_length=4000)
    actions_taken: list[str] = Field(default_factory=list, max_length=20)
    resident_response: str = Field(default="", max_length=2000)
    handover_summary: str = Field(default="", max_length=2000)
    verification_questions: list[str] = Field(default_factory=list, max_length=20)
    classification: RecordClassification
    risk_level: RiskLevel
    target_roles: list[TargetRole] = Field(min_length=1, max_length=6)
    document_types: list[DocumentType] = Field(min_length=1, max_length=8)
    keywords: list[str] = Field(default_factory=list, max_length=12)
    document_drafts: list[DocumentDraftProposal] = Field(
        default_factory=list,
        max_length=5,
    )


class WorkItemConfirmRequest(RecordDraft):
    reviewer_notes: str | None = Field(default=None, max_length=4000)
    verification_acknowledged: bool = False

    @field_validator(
        "corrected_text",
        "summary",
        "observation_details",
        "resident_response",
        "handover_summary",
        "reviewer_notes",
    )
    @classmethod
    def strip_record_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class WorkItemReopenRequest(BaseModel):
    reason: str = Field(default="수정이 필요하여 승인을 취소함", min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_reason(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("승인 취소 사유를 입력해 주세요.")
        return value


class WorkItemUpdate(BaseModel):
    status: WorkItemStatus | None = None
    document_types: list[DocumentType] | None = Field(default=None, max_length=8)
    processing_notes: str | None = Field(default=None, max_length=4000)

    @field_validator("processing_notes")
    @classmethod
    def strip_notes(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


DocumentDraftAction = Literal[
    "direct_edit",
    "regenerate",
    "change_request",
    "approve",
    "not_used",
]


class WorkItemDocumentDraftActionRequest(BaseModel):
    action: DocumentDraftAction
    content: str | None = Field(default=None, max_length=8000)
    change_request: str | None = Field(default=None, max_length=2000)
    verification_acknowledged: bool = False

    @field_validator("content", "change_request")
    @classmethod
    def strip_document_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class WorkItemDocumentDraftResponse(BaseModel):
    id: UUID
    document_type: DailyDocumentType
    content: str
    verification_questions: list[str]
    status: str
    version: int
    generator: str
    change_request: str | None
    approved_by_name: str | None
    approved_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkItemResponse(BaseModel):
    id: UUID
    status: str
    source_snapshot: WorkItemSourceSnapshot
    message: MessageResponse
    comments: list[MessageCommentResponse]
    room_name: str
    resident: ResidentResponse | None
    document_types: list[str]
    processing_notes: str | None
    handled_by_name: str | None
    ai_state: str
    ai_suggestion: RecordDraft | None
    ai_generator: str | None
    ai_generated_at: datetime | None
    confirmed_record: WorkItemConfirmRequest | None
    confirmed_by_name: str | None
    confirmed_at: datetime | None
    document_drafts: list[WorkItemDocumentDraftResponse]
    created_at: datetime
    updated_at: datetime


class DocumentCandidateDashboardResponse(BaseModel):
    total_count: int
    filtered_count: int
    document_counts: dict[str, int]
    risk_counts: dict[str, int]
    classification_counts: dict[str, int]
    items: list[WorkItemResponse]


class RoomDigestPoint(BaseModel):
    message_id: UUID
    resident_name: str | None
    body: str
    sender_name: str
    created_at: datetime
    comment_count: int
    action_type: str | None


class RoomDigestResponse(BaseModel):
    id: UUID
    room_id: UUID
    room_name: str
    period_start: datetime
    period_end: datetime
    message_count: int
    comment_count: int
    resident_count: int
    summary: str
    major_points: list[RoomDigestPoint]
    document_counts: dict[str, int]
    risk_counts: dict[str, int]
    generator: str
    generated_at: datetime


class PeriodWorkdeskRequest(BaseModel):
    start_date: date
    end_date: date
    room_id: UUID | None = None
    resident_id: UUID | None = None
    keyword: str | None = Field(default=None, max_length=100)
    enhance_summary: bool = False
    message_type: Literal[
        "chat",
        "notice",
        "handover",
        "work_request",
        "report",
    ] | None = None

    @field_validator("keyword")
    @classmethod
    def strip_period_keyword(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def validate_period(self):
        if self.end_date < self.start_date:
            raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
        if (self.end_date - self.start_date).days > 31:
            raise ValueError("한 번에 최대 32일까지만 정리할 수 있습니다.")
        return self


class PeriodWorkdeskSource(BaseModel):
    message: MessageResponse
    room_name: str
    resident_names: list[str] = Field(default_factory=list)
    read_count: int = 0
    reply_count: int = 0
    reply_user_count: int = 0


class PeriodRecordEvent(BaseModel):
    event_group_id: str
    resident_id: UUID | None = None
    resident_name: str | None = None
    summary: str
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    room_names: list[str] = Field(default_factory=list)
    sender_names: list[str] = Field(default_factory=list)
    latest_at: datetime


class PeriodRecordSummarySelection(BaseModel):
    resident_id: UUID | None = None
    evidence_ids: list[UUID] = Field(min_length=1, max_length=50)

    @field_validator("evidence_ids")
    @classmethod
    def unique_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class PeriodRecordSummaryRequest(BaseModel):
    record_usage_tag: RecordUsageTag | None = None
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list, max_length=6)
    selections: list[PeriodRecordSummarySelection] = Field(
        default_factory=list,
        max_length=50,
    )
    # 이전 PWA가 보내던 평면 근거 목록입니다. 새 클라이언트는 selections를
    # 사용하며, 전환 기간이 끝나면 이 필드는 제거할 수 있습니다.
    evidence_ids: list[UUID] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def normalize_record_usage_tags(self):
        if {"selections", "evidence_ids"}.issubset(self.model_fields_set):
            raise ValueError(
                "새 선택 형식과 이전 선택 형식을 동시에 사용할 수 없습니다."
            )
        if not self.selections and not self.evidence_ids:
            raise ValueError("근거 대화를 하나 이상 선택해 주세요.")
        tags = list(dict.fromkeys(self.record_usage_tags))
        if self.record_usage_tag is not None and self.record_usage_tag not in tags:
            tags.insert(0, self.record_usage_tag)
        if not tags:
            raise ValueError("정리할 기록 종류를 하나 이상 선택해 주세요.")
        self.record_usage_tags = tags
        self.record_usage_tag = tags[0]
        evidence_ids = {
            evidence_id
            for selection in self.selections
            for evidence_id in selection.evidence_ids
        }
        if len(evidence_ids) > 50:
            raise ValueError("근거 대화는 최대 50건까지 선택할 수 있습니다.")
        return self

    @field_validator("evidence_ids")
    @classmethod
    def unique_legacy_evidence_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class PeriodRecordSummaryResponse(BaseModel):
    record_usage_tag: RecordUsageTag
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    summary: str
    evidence_ids: list[UUID]
    generator: str
    elapsed_ms: int = 0


class PeriodDocumentDraft(BaseModel):
    key: str
    resident_id: UUID
    resident_name: str
    document_type: DailyDocumentType
    content: str
    verification_questions: list[str] = Field(default_factory=list)
    source_message_ids: list[UUID] = Field(default_factory=list)


class CareBriefingCard(BaseModel):
    event_group_id: str | None = None
    resident_id: UUID
    resident_name: str
    priority: Literal["first", "check", "observe"]
    change_summary: str
    check_reasons: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    document_types: list[DailyDocumentType] = Field(default_factory=list)
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    source_message_ids: list[UUID] = Field(default_factory=list)
    current_message_count: int = 0
    baseline_message_count: int = 0
    latest_at: datetime


class CareBriefingSummary(BaseModel):
    comparison_days: int = 3
    needs_attention_count: int = 0
    pending_check_count: int = 0
    document_candidate_count: int = 0
    cards: list[CareBriefingCard] = Field(default_factory=list)


class PeriodWorkdeskResponse(BaseModel):
    period_start: datetime
    period_end: datetime
    summary: str
    generator: str
    message_count: int
    comment_count: int
    resident_count: int
    category_counts: dict[str, int]
    document_counts: dict[str, int]
    sources: list[PeriodWorkdeskSource]
    document_drafts: list[PeriodDocumentDraft]
    record_events: list[PeriodRecordEvent] = Field(default_factory=list)
    record_group_counts: dict[str, int] = Field(default_factory=dict)
    briefing: CareBriefingSummary = Field(default_factory=CareBriefingSummary)
    truncated: bool = False


class LoginResponse(BaseModel):
    user: UserResponse
    expires_at: datetime


class ReviewerSessionResponse(LoginResponse):
    destination: Literal["chat", "care_briefing"]
    room_id: UUID | None = None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=12, max_length=200)


class AdminPasswordResetRequest(BaseModel):
    temporary_password: str = Field(min_length=12, max_length=200)


class SessionResponse(BaseModel):
    id: UUID
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    user_agent: str | None
    is_current: bool


class PushConfigResponse(BaseModel):
    enabled: bool
    public_key: str | None


class PushSubscriptionKeys(BaseModel):
    p256dh: str = Field(min_length=20, max_length=512)
    auth: str = Field(min_length=8, max_length=256)


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)
    expiration_time: int | None = Field(default=None, ge=0)
    keys: PushSubscriptionKeys

    @field_validator("endpoint")
    @classmethod
    def validate_push_endpoint(cls, value: str) -> str:
        endpoint = value.strip()
        if not endpoint.startswith("https://"):
            raise ValueError("푸시 알림 주소는 HTTPS여야 합니다.")
        return endpoint


class PushSubscriptionDelete(BaseModel):
    endpoint: str = Field(min_length=20, max_length=2048)


class PushSubscriptionResponse(BaseModel):
    enabled: bool
    active: bool
    message: str
    resubscribe_required: bool = False
    reason_code: Literal["endpoint_expired"] | None = None
