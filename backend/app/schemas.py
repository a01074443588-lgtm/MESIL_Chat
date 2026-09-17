from calendar import monthrange
from datetime import date, datetime
from math import cos, radians, sin
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator

from .new_admission_contract import NewAdmissionDraftBundle
from .resident_assessment_cycles import ResidentAssessmentCyclePayload


UnitType = Literal["business", "department", "floor", "team"]
LOGIN_USERNAME_PATTERN = r"^[가-힣a-zA-Z0-9][가-힣a-zA-Z0-9._-]{1,79}$"


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
    parent_unit_id: UUID | None
    parent_unit_name: str | None = None
    unit_type: str
    name: str
    code: str | None
    is_active: bool
    is_test_data: bool
    active_staff_count: int = 0
    active_room_count: int = 0
    active_resident_count: int = 0
    active_participant_count: int = 0
    system_room_count: int = 0
    system_room_id: UUID | None = None
    reference_count: int = 0
    can_delete: bool = False


class LivingSpaceMembersUpdate(BaseModel):
    member_ids: list[UUID] = Field(default_factory=list, max_length=100)


class LivingSpaceRoomResponse(BaseModel):
    room_id: UUID
    room_name: str
    is_active: bool
    member_ids: list[UUID]
    member_count: int
    message_count: int
    attachment_count: int


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
    username: str = Field(
        pattern=LOGIN_USERNAME_PATTERN,
        min_length=2,
        max_length=80,
    )
    full_name: str = Field(min_length=2, max_length=100)
    password: str = Field(min_length=6, max_length=200)
    role: Literal["admin", "staff"] = "staff"
    can_process_records: bool = False
    employee_code: str | None = Field(default=None, max_length=80)
    job_code: str | None = Field(default=None, min_length=2, max_length=80)
    position_title: str | None = Field(default=None, max_length=100)
    business_id: UUID | None = None
    department_id: UUID | None = None
    floor_id: UUID | None = None
    team_id: UUID | None = None


class StaffDirectoryAdminCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=100)
    employee_code: str | None = Field(default=None, max_length=80)
    issue_login: bool = False
    username: str | None = Field(
        default=None,
        pattern=LOGIN_USERNAME_PATTERN,
        min_length=2,
        max_length=80,
    )
    temporary_password: str | None = Field(default=None, min_length=6, max_length=200)
    can_process_records: bool = False
    role: Literal["staff"] = "staff"

    @field_validator("full_name")
    @classmethod
    def strip_and_validate_full_name(cls, value: str) -> str:
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("직원 이름은 2자 이상 입력해 주세요.")
        return normalized

    @field_validator("employee_code")
    @classmethod
    def strip_optional_employee_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def require_login_credentials_when_issuing(self):
        if self.issue_login and (not self.username or not self.temporary_password):
            raise ValueError("로그인 아이디와 임시 비밀번호를 입력해 주세요.")
        return self


class StaffLoginIssueRequest(BaseModel):
    username: str = Field(
        pattern=LOGIN_USERNAME_PATTERN,
        min_length=2,
        max_length=80,
    )
    temporary_password: str = Field(min_length=6, max_length=200)
    can_process_records: bool = False
    role: Literal["staff"] = "staff"


class StaffDirectoryAdminUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=100)
    employee_code: str | None = Field(default=None, max_length=80)

    @field_validator("full_name")
    @classmethod
    def strip_and_validate_full_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if len(normalized) < 2:
            raise ValueError("직원 이름은 2자 이상 입력해 주세요.")
        return normalized

    @field_validator("employee_code")
    @classmethod
    def strip_and_validate_employee_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("직원번호를 비울 수 없습니다.")
        return normalized

    @model_validator(mode="after")
    def require_at_least_one_change(self):
        if self.full_name is None and self.employee_code is None:
            raise ValueError("수정할 직원 정보를 한 가지 이상 입력해 주세요.")
        return self


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
        Literal["care", "social_worker", "realtime_secondary", "mentor_full"]
        | None
    ) = None


class ActiveStaffDirectoryResponse(BaseModel):
    id: UUID
    staff_id: UUID
    full_name: str
    job_name: str | None
    position_title: str | None
    business_name: str | None = None
    department_name: str | None = None
    floor_name: str | None = None
    team_name: str | None = None


class StaffRoomMemberResponse(BaseModel):
    id: UUID
    staff_id: UUID
    full_name: str
    job_name: str | None
    position_title: str | None


class StaffRoomCreate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    member_ids: list[UUID] = Field(min_length=1, max_length=99)

    @field_validator("name")
    @classmethod
    def strip_optional_room_name(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("member_ids")
    @classmethod
    def unique_member_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class StaffRoomUpdate(BaseModel):
    name: str = Field(min_length=2, max_length=120)

    @field_validator("name")
    @classmethod
    def strip_room_name(cls, value: str) -> str:
        return value.strip()


class StaffRoomMembersRequest(BaseModel):
    member_ids: list[UUID] = Field(min_length=1, max_length=99)

    @field_validator("member_ids")
    @classmethod
    def unique_member_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class StaffRoomOwnerTransferRequest(BaseModel):
    new_owner_staff_id: UUID


class StaffRoomResponse(BaseModel):
    id: UUID
    name: str
    is_active: bool
    owner_staff_id: UUID
    owner_name: str
    member_ids: list[UUID]
    members: list[StaffRoomMemberResponse]
    is_owner: bool
    can_invite: bool
    can_manage: bool
    created_at: datetime


class StaffServiceAssignmentResponse(BaseModel):
    id: UUID
    service_type: Literal["facility", "daycare", "homecare"]
    source_account_id: UUID
    service_period_id: UUID
    employment_status: Literal["active", "leave", "retired"] | str
    business: OrgUnitResponse | None
    department: OrgUnitResponse | None
    floor: OrgUnitResponse | None
    team: OrgUnitResponse | None
    job_code: str | None
    job_name: str | None
    job_title_snapshot: str | None
    position_code_id: UUID | None
    position_title_snapshot: str | None
    start_date: date
    end_date: date | None
    assignment_basis: Literal[
        "carefor_auto", "admin_confirmed", "legacy_manual"
    ]
    is_current: bool


class StaffServiceAssignmentAdminInput(BaseModel):
    service_type: Literal["facility", "daycare", "homecare"]
    job_code: str = Field(min_length=2, max_length=80)
    position_title: str | None = Field(default=None, max_length=100)

    @field_validator("job_code", "position_title", mode="before")
    @classmethod
    def strip_assignment_text(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


class StaffServiceAssignmentsAdminUpdate(BaseModel):
    assignments: list[StaffServiceAssignmentAdminInput] = Field(
        min_length=1,
        max_length=3,
    )

    @model_validator(mode="after")
    def require_unique_service_types(self):
        service_types = [item.service_type for item in self.assignments]
        if len(service_types) != len(set(service_types)):
            raise ValueError("같은 서비스의 배정을 두 번 보낼 수 없습니다.")
        return self


class StaffLegacyAssignmentResponse(BaseModel):
    business: OrgUnitResponse | None
    department: OrgUnitResponse | None
    floor: OrgUnitResponse | None
    team: OrgUnitResponse | None
    job_code: str | None
    job_name: str | None
    position_title: str | None


class StaffDirectoryResponse(BaseModel):
    staff_id: UUID
    login_user_id: UUID | None
    display_name: str
    internal_code: str
    employment_status: Literal["active", "leave", "retired"]
    is_test_data: bool
    login_status: Literal["not_issued", "enabled", "disabled"]
    username: str | None
    role: Literal["admin", "staff"] | None
    can_process_records: bool
    must_change_password: bool
    terminated_at: datetime | None
    legacy_assignment: StaffLegacyAssignmentResponse
    service_assignments: list[StaffServiceAssignmentResponse] = Field(
        default_factory=list
    )


class CustomRoomCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    member_ids: list[UUID] = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name_before_length_check(cls, value: str) -> str:
        return value.strip() if isinstance(value, str) else value


class CustomRoomUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=120)
    member_ids: list[UUID] | None = Field(default=None, min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name_before_length_check(cls, value: str | None) -> str | None:
        return value.strip() if isinstance(value, str) else value


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
    attachment_count: int = 0
    owner_staff_id: UUID | None = None
    owner_name: str | None = None
    created_by_name: str | None = None
    last_message_at: datetime | None = None
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
    room_name: str | None
    sort_order: int
    is_priority: bool = False
    roster_source: Literal["carefor", "smcodi", "manual", "demo"]
    is_test_data: bool = False


class ResidentAdminCreate(BaseModel):
    display_name: str = Field(min_length=2, max_length=100)
    service_type: Literal["facility", "daycare", "homecare"]
    floor_id: UUID | None = None

    @field_validator("display_name")
    @classmethod
    def strip_resident_name(cls, value: str) -> str:
        return value.strip()


class ResidentAdminUpdate(BaseModel):
    floor_id: UUID | None = None


class BulkTransferApplyRequest(BaseModel):
    item_ids: list[UUID] = Field(min_length=1, max_length=1000)


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
    evidence: list[dict] = Field(default_factory=list)
    automatically_linked: bool = False


class MessageResidentLinkUpdate(BaseModel):
    status: Literal["confirmed", "rejected"]


class MessageResidentReviewRequest(BaseModel):
    decision: Literal["confirm", "change", "unrelated", "set"]
    resident_id: UUID | None = None
    previous_resident_id: UUID | None = None
    resident_ids: list[UUID] = Field(default_factory=list, max_length=100)


class PhotoReadingChoiceRequest(BaseModel):
    decision: Literal["read", "not_required"]


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


class CareforStaffSyncSourceResponse(BaseModel):
    status: Literal["ready", "missing", "invalid"]
    original_name: str
    generated_at: datetime | None = None
    row_count: int = 0
    unique_name_count: int = 0
    service_counts: dict[str, int] = Field(default_factory=dict)
    message: str


class StaffSyncItemResponse(BaseModel):
    id: UUID
    source_key: str
    service_type: Literal["facility", "daycare", "homecare"] | str
    external_id: str
    change_type: Literal[
        "new", "update", "leave", "retire", "unchanged", "conflict"
    ]
    status: Literal["pending", "not_required", "blocked"]
    current_staff_id: UUID | None
    incoming_payload: dict
    current_snapshot: dict | None
    conflict_reason: str | None


class StaffSyncBatchResponse(BaseModel):
    id: UUID
    source: str
    original_name: str
    file_sha256: str
    source_generated_at: datetime | None
    status: Literal["preview"]
    summary: dict[str, int]
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    items: list[StaffSyncItemResponse] = Field(default_factory=list)


class StaffOrganizationProposalResponse(BaseModel):
    unit_type: Literal["business", "department", "floor", "team"]
    proposed_name: str | None
    unit_id: UUID | None
    match_status: Literal[
        "matched", "reference_only", "not_proposed", "manual", "missing", "ambiguous"
    ]
    catalog_is_test_data: bool | None
    reason: str


class StaffJobProposalResponse(BaseModel):
    source_name: str
    code: str | None
    proposed_name: str | None
    match_status: Literal["matched", "manual", "missing"]
    required_for_review: bool
    reason: str


class StaffPositionProposalResponse(BaseModel):
    proposed_name: str | None
    position_id: UUID | None
    match_status: Literal["not_proposed", "suggested", "missing"]
    manual_confirmation: bool
    reason: str


class StaffAssignmentAccountResponse(BaseModel):
    source_key: str
    external_id: str
    service_type: Literal["facility", "daycare", "homecare"] | str
    employment_status: Literal["active", "leave", "retired", "unknown"] | str
    source_status: str
    source_job_name: str
    assignment_required: bool
    assignment_ready: bool
    organizations: list[StaffOrganizationProposalResponse]
    job: StaffJobProposalResponse
    position: StaffPositionProposalResponse
    notes: list[str] = Field(default_factory=list)


class StaffReviewPersonGroup(BaseModel):
    source_keys: list[str] = Field(min_length=1, max_length=20)
    primary_source_key: str | None = Field(default=None, max_length=140)
    primary_job_code: str | None = Field(default=None, max_length=80)
    primary_position_title: str | None = Field(default=None, max_length=100)

    @field_validator("source_keys")
    @classmethod
    def unique_source_keys(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value]
        if any(not item or len(item) > 140 for item in normalized):
            raise ValueError("사람 묶음의 계정 식별값이 올바르지 않습니다.")
        if len(set(normalized)) != len(normalized):
            raise ValueError("한 사람 묶음에 같은 계정이 중복되어 있습니다.")
        return normalized

    @field_validator(
        "primary_source_key",
        "primary_job_code",
        "primary_position_title",
    )
    @classmethod
    def strip_optional_group_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class StaffReviewAccountAssignment(BaseModel):
    source_key: str = Field(min_length=1, max_length=140)
    department_name: str | None = Field(default=None, max_length=100)
    job_code: str | None = Field(default=None, max_length=80)
    position_title: str | None = Field(default=None, max_length=100)

    @field_validator("source_key")
    @classmethod
    def strip_source_key(cls, value: str) -> str:
        return value.strip()

    @field_validator("department_name", "job_code", "position_title")
    @classmethod
    def strip_optional_assignment_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


StaffReviewIdentityDecision = Literal[
    "pending",
    "not_required",
    "same_person",
    "separate_people",
    "custom_groups",
]


class StaffSyncReviewDraftSaveRequest(BaseModel):
    expected_revision: int | None = Field(default=None, ge=1)
    identity_decision: StaffReviewIdentityDecision
    person_groups: list[StaffReviewPersonGroup] = Field(
        default_factory=list, max_length=20
    )
    account_assignments: list[StaffReviewAccountAssignment] = Field(
        default_factory=list, max_length=20
    )
    note: str = Field(default="", max_length=1000)

    @field_validator("note")
    @classmethod
    def strip_review_note(cls, value: str) -> str:
        return value.strip()

    @model_validator(mode="after")
    def unique_account_assignments(self):
        source_keys = [item.source_key for item in self.account_assignments]
        if len(set(source_keys)) != len(source_keys):
            raise ValueError("같은 계정의 배정 결정이 중복되어 있습니다.")
        return self


class StaffSyncReviewDraftResponse(BaseModel):
    id: UUID
    batch_id: UUID
    candidate_key: str
    identity_decision: StaffReviewIdentityDecision
    person_groups: list[StaffReviewPersonGroup] = Field(default_factory=list)
    account_assignments: list[StaffReviewAccountAssignment] = Field(
        default_factory=list
    )
    note: str
    completion_status: Literal["draft", "complete"]
    completion_issues: list[str] = Field(default_factory=list)
    revision: int
    updated_by_name: str
    created_at: datetime
    updated_at: datetime


class StaffPersonCandidateResponse(BaseModel):
    candidate_key: str
    display_name: str
    identity_status: Literal[
        "single_account",
        "linked_account",
        "linked_accounts",
        "verified_identity",
        "confirmation_required",
        "data_conflict",
    ]
    identity_evidence: Literal["name_birth", "name_only"]
    same_name_candidate_count: int = Field(ge=1)
    review_status: Literal[
        "proposal_ready", "identity_review", "assignment_review", "blocked"
    ]
    account_count: int
    requires_identity_confirmation: bool
    requires_assignment_confirmation: bool
    linked_staff_ids: list[UUID] = Field(default_factory=list)
    accounts: list[StaffAssignmentAccountResponse] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    review_draft: StaffSyncReviewDraftResponse | None = None


class StaffAssignmentPreviewResponse(BaseModel):
    batch_id: UUID
    source_generated_at: datetime | None
    summary: dict[str, int]
    candidates: list[StaffPersonCandidateResponse] = Field(default_factory=list)


class StaffApplicationPlanBlockerResponse(BaseModel):
    code: str
    message: str
    affected_people: int = Field(ge=0)
    affected_accounts: int = Field(ge=0)


class StaffApplicationPersonPlanResponse(BaseModel):
    plan_key: str
    source_keys: list[str] = Field(default_factory=list)
    planned_internal_code: str
    target_staff_id: UUID | None
    staff_action: Literal["create", "update", "no_change", "blocked"]
    employment_status: Literal["active", "leave", "retired"]
    service_types: list[str] = Field(default_factory=list)
    source_account_actions: dict[str, int]
    service_period_actions: dict[str, int]
    service_assignment_actions: dict[str, int]
    current_assignment_account_count: int = Field(ge=0)
    history_only_account_count: int = Field(ge=0)
    login_action: Literal["none"]
    access_policy: Literal["active_access", "leave_suspended"]
    blocker_codes: list[str] = Field(default_factory=list)


class StaffApplicationCandidatePlanResponse(BaseModel):
    candidate_key: str
    display_name: str
    decision_source: Literal[
        "auto_proposal", "saved_review", "missing_review", "blocked_source"
    ]
    outcome: Literal["prepared", "needs_design", "blocked"]
    account_count: int = Field(ge=0)
    history_only_account_count: int = Field(ge=0)
    person_plans: list[StaffApplicationPersonPlanResponse] = Field(default_factory=list)
    blocker_codes: list[str] = Field(default_factory=list)
    changes: list[str] = Field(default_factory=list)


class StaffApplicationPlanResponse(BaseModel):
    plan_schema_version: Literal[2]
    batch_id: UUID
    file_sha256: str
    read_only: Literal[True]
    apply_locked: Literal[True]
    can_apply: Literal[False]
    plan_fingerprint: str
    change_fingerprint: str
    change_manifest_supported: bool
    organization_catalog_version: str
    organization_catalog_fingerprint: str
    leave_access_policy_version: str
    leave_access_policy_fingerprint: str
    leave_access_policy: dict[str, str]
    summary: dict[str, int]
    blockers: list[StaffApplicationPlanBlockerResponse] = Field(default_factory=list)
    candidates: list[StaffApplicationCandidatePlanResponse] = Field(
        default_factory=list
    )
    safety_notes: list[str] = Field(default_factory=list)
    protected_tables: list[str] = Field(default_factory=list)


class StaffApplicationRunPrepareRequest(BaseModel):
    expected_plan_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_change_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")


class StaffApplicationEvidenceRequest(BaseModel):
    evidence_filename: str = Field(min_length=1, max_length=220)

    @field_validator("evidence_filename")
    @classmethod
    def strip_evidence_filename(cls, value: str) -> str:
        return value.strip()


class StaffApplicationRunResponse(BaseModel):
    id: UUID
    batch_id: UUID
    manifest_schema_version: Literal[1]
    plan_schema_version: Literal[2]
    plan_fingerprint: str
    change_fingerprint: str
    manifest_fingerprint: str
    organization_catalog_version: str
    leave_access_policy_version: str
    status: Literal[
        "prepared",
        "backup_verified",
        "restore_verified",
        "evidence_ready",
        "stale",
    ]
    entry_count: int = Field(ge=0)
    entry_summary: dict[str, int]
    plan_summary: dict[str, int]
    backup_verified: bool
    restore_verified: bool
    evidence_ready: bool
    apply_locked: Literal[True]
    can_apply: Literal[False]
    created_at: datetime
    updated_at: datetime


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
    rank: int | None = Field(default=None, ge=1)
    slot_index: int | None = Field(default=None, ge=1)
    applied_to_draft: bool = False
    application_reason: str | None = None
    detected_text: str | None = None
    resolved_in_draft: bool = False


class AttachmentTextExtractionAttemptResponse(BaseModel):
    attempt_number: int = Field(ge=1)
    status: Literal["completed", "failed", "reviewed", "no_text", "not_required", "unsupported"]
    provider: str
    model_name: str
    extracted_text: str | None
    reviewed_text: str | None
    error_message: str | None
    completed_at: datetime | None
    reviewed_at: datetime | None
    archived_at: datetime


class HandwritingPreprocessingResponse(BaseModel):
    applied: bool
    requires_staff_review: bool
    reason: str
    selected_variant: str
    input_label: str
    document_crop_applied: bool
    output_blocked: bool = False
    retry_count: int = Field(default=0, ge=0, le=1)
    model_call_limit: int = Field(default=1, ge=1, le=3)
    original_preserved: bool = True
    external_transfer: bool = False
    metrics: dict[str, int | float | None] = Field(default_factory=dict)


class AttachmentTextExtractionResponse(BaseModel):
    content_included: bool = True
    result_char_count: int | None = None
    processing_ms: int | None = None
    error_type: str | None = None
    status: Literal["pending", "processing", "completed", "failed", "reviewed", "no_text", "not_required", "unsupported"]
    review_revision: int = 0
    reviewed_by_id: UUID | None = None
    source_locations: list[dict[str, Any]] = Field(default_factory=list)
    provider: str
    model_name: str
    extracted_text: str | None
    original_extracted_text: str | None
    suggested_text: str | None = None
    suggested_service_context: (
        Literal["facility", "daycare", "homecare"] | None
    ) = None
    suggested_name_correction_count: int = Field(default=0, ge=0)
    auto_applied_name_count: int = Field(default=0, ge=0)
    unresolved_name_count: int = Field(default=0, ge=0)
    review_warnings: list[str] = Field(default_factory=list)
    reviewed_text: str | None
    reviewed_by_name: str | None = None
    latest_confirmed_text: str | None = None
    latest_confirmed_by_name: str | None = None
    latest_confirmed_at: datetime | None = None
    latest_confirmation_source: str | None = None
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
    attempt_number: int = Field(default=1, ge=1)
    previous_attempts: list[AttachmentTextExtractionAttemptResponse] = Field(
        default_factory=list
    )
    preprocessing: HandwritingPreprocessingResponse | None = None


class AttachmentTextExtractionRequest(BaseModel):
    force: bool = False


class AttachmentTextExtractionUpdate(BaseModel):
    reviewed_text: str | None = Field(default=None, max_length=100000)
    resident_ids: list[UUID] | None = Field(default=None, max_length=100)
    expected_revision: int | None = Field(default=None, ge=0)
    expected_resident_revision: int | None = Field(default=None, ge=0)
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


def normalized_rotated_box_corners(
    *,
    left: float,
    top: float,
    width: float,
    height: float,
    rotation_degrees: float = 0,
) -> tuple[tuple[float, float], ...]:
    """Return normalized image corners for a center-rotated rectangle.

    Keeping this calculation independent from the editor makes the exact saved
    geometry reusable by a later deskew/crop pipeline without duplicating the
    rotation convention.
    """

    center_x = left + width / 2
    center_y = top + height / 2
    angle = radians(rotation_degrees)
    cosine = cos(angle)
    sine = sin(angle)
    corners: list[tuple[float, float]] = []
    for local_x, local_y in (
        (-width / 2, -height / 2),
        (width / 2, -height / 2),
        (width / 2, height / 2),
        (-width / 2, height / 2),
    ):
        corners.append(
            (
                center_x + local_x * cosine - local_y * sine,
                center_y + local_x * sine + local_y * cosine,
            )
        )
    return tuple(corners)


class CoordinateBox(BaseModel):
    left: float = Field(ge=0, le=1)
    top: float = Field(ge=0, le=1)
    width: float = Field(gt=0, le=1)
    height: float = Field(gt=0, le=1)
    rotation_degrees: float = Field(default=0, ge=-30, le=30)

    @model_validator(mode="after")
    def validate_bounds(self):
        corners = normalized_rotated_box_corners(
            left=self.left,
            top=self.top,
            width=self.width,
            height=self.height,
            rotation_degrees=self.rotation_degrees,
        )
        tolerance = 0.000001
        if any(
            x < -tolerance
            or x > 1 + tolerance
            or y < -tolerance
            or y > 1 + tolerance
            for x, y in corners
        ):
            raise ValueError("글자 영역이 이미지 범위를 벗어났습니다.")
        return self


class HandwritingVoiceCorrectionStage(BaseModel):
    revision_no: int = Field(ge=1)
    stage: Literal[
        "initial_ocr",
        "audio_transcript",
        "ai_correction_suggestion",
        "staff_approved_final",
    ]
    status: Literal["completed", "needs_confirmation", "awaiting_staff_approval"]
    text: str | None
    provider: str | None
    model: str | None
    evidence_refs: list[str] = Field(default_factory=list)


class HandwritingVoiceCorrectionAlignment(BaseModel):
    row_no: int = Field(ge=1)
    image_text: str | None
    audio_text: str | None
    status: Literal["same", "different", "image_only", "audio_only"]
    similarity: float = Field(ge=0, le=1)


class HandwritingVoiceCorrectionFact(BaseModel):
    category: Literal[
        "date",
        "time",
        "quantity",
        "unit",
        "follow_up",
        "completion",
        "negation",
    ]
    image_values: list[str] = Field(default_factory=list)
    audio_values: list[str] = Field(default_factory=list)
    status: Literal["same", "different", "image_only", "audio_only", "not_found"]
    requires_staff_confirmation: bool


class HandwritingVoiceCorrectionChangedField(BaseModel):
    category: Literal[
        "narrative",
        "date",
        "time",
        "quantity",
        "unit",
        "follow_up",
        "completion",
        "negation",
        "name",
        "medication",
        "diagnosis",
    ]
    before_values: list[str] = Field(default_factory=list)
    proposed_values: list[str] = Field(default_factory=list)
    status: Literal["unchanged", "proposed", "needs_confirmation", "blocked"]
    reason: str = Field(min_length=1, max_length=500)
    evidence_refs: list[str] = Field(default_factory=list)


class HandwritingVoiceCorrectionResponse(BaseModel):
    schema_version: Literal["handwriting_voice_correction_v1"]
    status: Literal["awaiting_staff_approval"]
    mode: Literal["full_reading", "partial_correction", "story_hint"]
    stages: list[HandwritingVoiceCorrectionStage]
    alignments: list[HandwritingVoiceCorrectionAlignment]
    critical_facts: list[HandwritingVoiceCorrectionFact]
    changed_fields: list[HandwritingVoiceCorrectionChangedField]
    audio_quality: dict[str, Any]
    summary: dict[str, int]
    safety: dict[str, bool]
    ai_combination: dict[str, str] = Field(default_factory=dict)
    lexicon_applications: list[dict[str, Any]] = Field(default_factory=list)
    review_items: list[dict[str, Any]] = Field(default_factory=list)
    transcript_provenance: dict[str, Any] = Field(default_factory=dict)


class HandwritingCorrectionSentenceDecision(BaseModel):
    sentence_no: int = Field(ge=1)
    source_text: str = Field(min_length=1, max_length=12000)
    proposed_text: str = Field(min_length=1, max_length=12000)
    final_text: str = Field(min_length=1, max_length=12000)
    action: Literal["accepted", "edited"]
    approved: Literal[True]
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    coordinate_region_ids: list[str] = Field(default_factory=list, max_length=40)
    coordinate_evidence: list[dict[str, Any]] = Field(default_factory=list)

    @field_validator("source_text", "proposed_text", "final_text")
    @classmethod
    def strip_correction_sentence(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("승인 문장은 비워 둘 수 없습니다.")
        return stripped

    @field_validator("coordinate_region_ids")
    @classmethod
    def unique_coordinate_region_ids(cls, value: list[str]) -> list[str]:
        normalized = [item.strip() for item in value if item.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("같은 원본 위치가 한 문장에 두 번 포함되었습니다.")
        return normalized


class HandwritingCorrectionApprovalCreate(BaseModel):
    audio_attachment_id: UUID | None = None
    mode: Literal[
        "direct_typing",
        "full_reading",
        "partial_correction",
        "story_hint",
    ]
    idempotency_key: UUID
    sentences: list[HandwritingCorrectionSentenceDecision] = Field(
        min_length=1,
        max_length=200,
    )
    conflicts_confirmed: bool = False
    conflict_resolutions: list[dict[str, str]] = Field(default_factory=list, max_length=200)
    supersedes_approval_id: UUID | None = None

    @model_validator(mode="after")
    def validate_audio_for_mode(self):
        if self.mode == "direct_typing" and self.audio_attachment_id is not None:
            raise ValueError("직접 타이핑은 음성 근거를 연결하지 않습니다.")
        if self.mode != "direct_typing" and self.audio_attachment_id is None:
            raise ValueError("음성 또는 설명 근거를 선택해 주세요.")
        expected_numbers = list(range(1, len(self.sentences) + 1))
        if [item.sentence_no for item in self.sentences] != expected_numbers:
            raise ValueError("승인 문장 번호는 1부터 빠짐없이 이어져야 합니다.")
        return self


class HandwritingCorrectionApprovalResponse(BaseModel):
    id: UUID
    approval_group_id: UUID
    revision: int = Field(ge=1)
    supersedes_approval_id: UUID | None
    image_attachment_id: UUID
    audio_attachment_id: UUID | None
    mode: Literal[
        "direct_typing",
        "full_reading",
        "partial_correction",
        "story_hint",
    ]
    status: Literal["approved"]
    initial_ocr: str
    audio_or_explanation_text: str | None
    ai_suggestion: str | None
    approved_final_text: str
    sentence_decisions: list[dict[str, Any]]
    evidence_refs: list[str]
    confidence_state: dict[str, Any]
    conflicts_confirmed: bool
    approved_by_id: UUID
    approved_by_name: str
    approved_at: datetime
    is_test_data: bool
    official_record_saved: Literal[False]
    training_data_adopted: Literal[False]
    external_transfer_allowed: Literal[False]


class HandwritingCorrectionApprovalHistoryResponse(BaseModel):
    status: Literal["reviewing", "partially_approved", "approved"]
    versions: list[HandwritingCorrectionApprovalResponse]
    current_revision: int = Field(ge=0)
    resident_link_revision: int = Field(default=0, ge=0)
    safety: dict[str, bool]


class CoordinateRegion(BaseModel):
    client_id: str = Field(min_length=1, max_length=80)
    bbox: CoordinateBox
    raw_text: str = Field(default="", max_length=4000)
    corrected_text: str = Field(default="", max_length=4000)
    role: Literal["name", "status", "time", "general"] = "general"
    document_template: str | None = Field(default=None, max_length=80)
    section_role: str | None = Field(default=None, max_length=80)
    resident_id: UUID | None = None
    position_confidence: float = Field(default=0, ge=0, le=1)
    review_required: bool = False
    placement_status: Literal["auto", "confirmed", "needs_position"] = (
        "needs_position"
    )
    source: Literal[
        "ocr_bbox",
        "auto_locator",
        "confirmed_history",
        "legacy_alignment",
        "manual",
    ] = "manual"

    @field_validator("raw_text", "corrected_text", "document_template", "section_role")
    @classmethod
    def strip_coordinate_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_resident_link(self):
        if self.role != "name" and self.resident_id is not None:
            raise ValueError("어르신 연결은 이름 영역에서만 선택할 수 있습니다.")
        return self


class AttachmentCoordinateReviewCreate(BaseModel):
    image_width: int = Field(gt=0, le=30000)
    image_height: int = Field(gt=0, le=30000)
    editor_version: str = Field(default="coordinate-editor-mvp-1", max_length=40)
    document_template: str | None = Field(default=None, max_length=80)
    regions: list[CoordinateRegion] = Field(min_length=1, max_length=600)
    confirm_text: bool = False

    @model_validator(mode="after")
    def validate_unique_regions(self):
        ids = [region.client_id for region in self.regions]
        if len(ids) != len(set(ids)):
            raise ValueError("같은 글자 영역 식별자가 두 번 포함되었습니다.")
        return self


class CoordinateResidentOption(BaseModel):
    id: UUID
    display_name: str
    service_type: str


class AttachmentCoordinateReviewResponse(BaseModel):
    id: UUID | None = None
    version_number: int = Field(ge=0)
    image_width: int | None = None
    image_height: int | None = None
    editor_version: str
    document_template: str | None = None
    regions: list[CoordinateRegion]
    editor_name: str | None = None
    created_at: datetime | None = None
    text_confirmation_id: UUID | None = None
    text_confirmed_at: datetime | None = None
    is_bootstrap: bool = False
    raw_source_text: str
    confirmed_source_text: str
    resident_options: list[CoordinateResidentOption] = Field(default_factory=list)


class AttachmentResponse(BaseModel):
    correction_recording: dict[str, Any] | None = None
    photo_reading_status: Literal["general", "pending", "processing", "completed", "no_text", "not_required", "failed"] | None = None
    resident_candidate_notice: str | None = None
    resident_link_revision: int = Field(default=0, ge=0)
    can_download_original: bool = False
    can_view_reviewed_text: bool = False
    can_review_text: bool = False
    can_request_reading: bool = False
    id: UUID
    message_id: UUID
    uploader_id: UUID
    original_name: str
    mime_type: str
    size_bytes: int
    download_url: str
    thumbnail_url: str | None = None
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
    resident_ids: list[UUID] = Field(default_factory=list, max_length=50)
    resident_ref: str | None = Field(default=None, max_length=100)
    reply_to_message_id: UUID | None = None
    action: ActionItemCreate | None = None
    client_request_id: UUID | None = None

    @field_validator("body")
    @classmethod
    def strip_body(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("빈 메시지는 보낼 수 없습니다.")
        return value

    @field_validator("resident_ids")
    @classmethod
    def unique_resident_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


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


class RepliedMessageSource(BaseModel):
    message_id: UUID
    sender_name: str
    body: str
    created_at: datetime


class AiHelpMessageMeta(BaseModel):
    role: Literal["user", "assistant"]
    status: Literal[
        "queued", "preparing", "extracting", "reasoning", "validating",
        "completed", "failed", "cancelled"
    ]
    turn_id: UUID | None = None
    source_message_id: UUID | None = None
    question_type: Literal[
        "attachment_guidance", "general_guidance", "record_search", "clarification"
    ] | None = None
    provider: str | None = None
    model: str | None = None
    processing_location: Literal["rules", "local", "internal", "external", "unconfigured"] | None = None
    external_transmission: bool = False
    evidence: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    error_message: str | None = Field(default=None, max_length=500)


class MessageCommentResponse(BaseModel):
    id: UUID
    author_id: UUID
    author_name: str
    body: str
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
    latest_comment: MessageCommentResponse | None = None
    read_count: int = 0
    reply_user_count: int = 0
    action_item: ActionItemResponse | None = None
    forwarded_from: ForwardedMessageSource | None = None
    reply_to: RepliedMessageSource | None = None
    ai_help: AiHelpMessageMeta | None = None
    is_recalled: bool = False
    recalled_at: datetime | None = None
    created_at: datetime


class MessageRecallRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("reason")
    @classmethod
    def strip_recall_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class MessageRecallResponse(BaseModel):
    message_id: UUID
    recalled_message_ids: list[UUID]
    recalled_at: datetime


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


class MessageDetailResponse(BaseModel):
    message: MessageResponse
    read_receipts: list[ReadReceiptResponse]
    comments: list[MessageCommentResponse]


class RoomMessageSearchMatchResponse(BaseModel):
    message_id: UUID
    source_type: Literal[
        "message",
        "comment",
        "image",
        "audio",
        "resident",
        "sender",
    ]
    source_label: str
    excerpt: str
    attachment_id: UUID | None = None
    attachment_name: str | None = None


class RoomMessageSearchResponse(BaseModel):
    matched_count: int
    messages: list[MessageResponse]
    matches: list[RoomMessageSearchMatchResponse] = Field(default_factory=list)
    truncated: bool = False


class RoomSearchSummaryRequest(BaseModel):
    message_ids: list[UUID] = Field(min_length=1, max_length=200)
    provider: Literal["auto", "codex", "nvidia", "local", "rules"] = "auto"
    resident_id: UUID | None = None
    date_from: date | None = None
    date_to: date | None = None
    query: str | None = Field(default=None, max_length=200)
    message_type: Literal["chat", "notice", "handover", "work_request", "report"] | None = None
    action_status: Literal["none", "assigned", "acknowledged", "in_progress", "completed"] | None = None
    local_model_override: str | None = Field(default=None, max_length=200)

    @field_validator("message_ids")
    @classmethod
    def unique_message_ids(cls, value: list[UUID]) -> list[UUID]:
        return list(dict.fromkeys(value))


class RoomSearchSummaryCounts(BaseModel):
    total: int
    general: int
    attention: int
    unresolved: int


class RoomSearchSummaryTimelineItem(BaseModel):
    label: str
    message_count: int
    attention_count: int
    unresolved_count: int
    summary: str


class RoomSearchSummaryEvidence(BaseModel):
    number: int
    message_id: UUID
    created_at: datetime
    source_label: str
    excerpt: str


class RoomSearchResidentSummary(BaseModel):
    resident_id: UUID | None = None
    resident_name: str
    message_count: int
    attention_count: int
    unresolved_count: int
    summary: str
    evidence: list[RoomSearchSummaryEvidence] = Field(default_factory=list)


class RoomSearchSummarySentence(BaseModel):
    text: str
    summary_type: Literal["core", "change", "action", "follow_up", "latest", "handover", "unconfirmed"]
    resident_id: UUID | None = None
    evidence_ids: list[UUID] = Field(default_factory=list)
    first_event_id: UUID | None = None
    follow_up_ids: list[UUID] = Field(default_factory=list)
    latest_record_id: UUID | None = None
    needs_follow_up: bool = False
    unconfirmed_part: str = ""


class RoomSearchSummaryResponse(BaseModel):
    summary: str
    source_message_ids: list[UUID]
    generator: str
    provider_notice: str | None = None
    fallback_reason: str | None = None
    ai_elapsed_ms: int = 0
    request_elapsed_ms: int = 0
    cache_hit: bool = False
    processing_method: Literal["local_ai", "fallback_model", "rules"] = "rules"
    generation_verified: bool = False
    summary_sentences: list[RoomSearchSummarySentence] = Field(default_factory=list)
    summary_evidence: list[RoomSearchSummaryEvidence] = Field(default_factory=list)
    staged: bool = False
    performance: dict[str, int | float | str | bool | None] = Field(default_factory=dict)
    display_mode: Literal["overview", "direct"] = "overview"
    period_granularity: Literal["day", "week", "month"] = "day"
    counts: RoomSearchSummaryCounts
    highlights: list[str] = Field(default_factory=list)
    timeline: list[RoomSearchSummaryTimelineItem] = Field(default_factory=list)
    resident_summaries: list[RoomSearchResidentSummary] = Field(default_factory=list)
    general_evidence: list[RoomSearchSummaryEvidence] = Field(default_factory=list)


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
CareDocumentCandidateType = Literal[
    "care_service_record",
    "consultation_log",
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
]
CarePlanningDocumentType = Literal[
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
]
CarePlanningStatus = Literal["not_started", "draft", "needs_review", "confirmed"]


class ResidentCarePlanningStateUpdate(BaseModel):
    assessment_date: date | None = None
    valid_until: date | None = None
    next_due_date: date | None = None
    cycle_months: int = Field(default=6, ge=1, le=24)
    reassess_on_state_change: bool = True
    state_change_triggered: bool = False
    change_reason: str | None = Field(default=None, max_length=1000)
    status: CarePlanningStatus = "not_started"
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    known_facts: list[str] = Field(default_factory=list, max_length=50)
    questions_required: list[str] = Field(default_factory=list, max_length=50)
    professional_review_fields: list[str] = Field(default_factory=list, max_length=50)
    author_confirmed: bool = False

    @field_validator("change_reason")
    @classmethod
    def strip_change_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @field_validator(
        "evidence_refs",
        "known_facts",
        "questions_required",
        "professional_review_fields",
    )
    @classmethod
    def strip_planning_lines(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))


class ResidentCarePlanningStateResponse(BaseModel):
    id: UUID | None = None
    resident_id: UUID
    resident_name: str
    document_type: CarePlanningDocumentType
    assessment_date: date | None
    valid_until: date | None
    next_due_date: date | None
    cycle_months: int
    reassess_on_state_change: bool
    state_change_triggered: bool
    change_reason: str | None
    status: CarePlanningStatus
    evidence_refs: list[str]
    known_facts: list[str]
    questions_required: list[str]
    professional_review_fields: list[str]
    author_confirmed: bool
    author_verified_by_name: str | None
    author_verified_at: datetime | None
    version: int
    is_candidate: bool
    candidate_reasons: list[str]
    updated_at: datetime | None


class ResidentCarePlanningResponse(BaseModel):
    resident: ResidentResponse
    as_of: date
    operating_cycle_notice: str
    legal_standard_notice: str
    items: list[ResidentCarePlanningStateResponse]


class ResidentAssessmentCycleRevisionCreate(BaseModel):
    payload: ResidentAssessmentCyclePayload


class ResidentAssessmentCycleRevisionResponse(BaseModel):
    id: UUID
    revision: int
    supersedes_revision: int | None
    status: Literal["draft", "needs_confirmation"]
    baseline_sources: list[dict[str, Any]]
    current_facts: list[dict[str, Any]]
    comparison_items: list[dict[str, Any]]
    selected_plan_candidate_keys: list[str]
    confirmation_questions: list[dict[str, Any]]
    field_changes: list[dict[str, Any]]
    created_by_name: str
    created_at: datetime


class ResidentAssessmentCycleResponse(BaseModel):
    id: UUID
    resident_id: UUID
    resident_name: str
    reason: Literal[
        "new_admission",
        "periodic_reassessment",
        "state_change",
        "care_plan_change",
        "staff_review",
    ]
    cycle_number: int
    assessment_date: date
    period_start: date | None
    period_end: date | None
    source_provider: Literal["staff_manual", "carefor_readonly"]
    previous_cycle_id: UUID | None
    current_revision: int
    status: Literal["draft", "needs_confirmation"]
    external_transfer_allowed: Literal[False]
    is_test_data: bool
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    revisions: list[ResidentAssessmentCycleRevisionResponse]


class ResidentAssessmentCycleSummaryResponse(BaseModel):
    id: UUID
    reason: str
    cycle_number: int
    assessment_date: date
    current_revision: int
    status: Literal["draft", "needs_confirmation"]
    previous_cycle_id: UUID | None
    updated_at: datetime


class ResidentAssessmentCycleListResponse(BaseModel):
    resident_id: UUID
    resident_name: str
    source_provider_notice: str
    operating_cycle_notice: str
    items: list[ResidentAssessmentCycleSummaryResponse]


class NewAdmissionDraftRevisionCreate(BaseModel):
    bundle: NewAdmissionDraftBundle
    confirmed_field_keys: list[str] = Field(default_factory=list, max_length=500)
    reviewed_document_types: list[
        Literal[
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        ]
    ] = Field(default_factory=list, max_length=4)

    @field_validator("confirmed_field_keys")
    @classmethod
    def unique_confirmed_fields(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(value.strip() for value in values if value.strip()))

    @field_validator("reviewed_document_types")
    @classmethod
    def unique_reviewed_documents(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(values))


class NewAdmissionDraftFieldChangeResponse(BaseModel):
    field_key: str
    before_value: Any | None = None
    after_value: Any | None = None
    before_status: str | None = None
    after_status: str | None = None
    before_evidence_refs: list[str] = Field(default_factory=list)
    after_evidence_refs: list[str] = Field(default_factory=list)


class NewAdmissionDraftFieldConfirmationResponse(BaseModel):
    state: Literal["confirmed", "needs_confirmation"]
    confirmed_by_id: UUID | None = None
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None


class NewAdmissionDraftRevisionResponse(BaseModel):
    id: UUID
    revision: int
    supersedes_revision: int | None
    status: Literal["draft", "needs_confirmation"]
    bundle: NewAdmissionDraftBundle
    field_evidence_refs: dict[str, list[str]]
    field_changes: list[NewAdmissionDraftFieldChangeResponse]
    field_confirmations: dict[str, NewAdmissionDraftFieldConfirmationResponse]
    created_by_name: str
    created_at: datetime


class NewAdmissionDraftResponse(BaseModel):
    id: UUID
    resident_id: UUID
    resident_name: str
    case_ref: str
    processing_scope: Literal["internal_only", "deidentified_dev"]
    external_transfer_allowed: Literal[False]
    current_revision: int
    status: Literal["draft", "needs_confirmation"]
    is_test_data: bool
    created_by_name: str
    created_at: datetime
    updated_at: datetime
    revisions: list[NewAdmissionDraftRevisionResponse]


class NewAdmissionDraftSummaryResponse(BaseModel):
    id: UUID
    case_ref: str
    current_revision: int
    status: Literal["draft", "needs_confirmation"]
    reason: Literal[
        "new_admission",
        "periodic_reassessment",
        "state_change_reassessment",
        "staff_review",
    ]
    assessment_date: date | None
    reviewed_assessment_count: int = Field(ge=0, le=4)
    updated_at: datetime


class NewAdmissionDraftListResponse(BaseModel):
    resident_id: UUID
    resident_name: str
    items: list[NewAdmissionDraftSummaryResponse]


class AssessmentEvidenceFactResponse(BaseModel):
    field_key: str
    label: str
    value: Any
    status: str
    document_types: list[str] = Field(default_factory=list)


class AssessmentEvidenceLinkedFieldResponse(BaseModel):
    field_key: str
    document_types: list[str] = Field(default_factory=list)


class AssessmentEvidenceLedgerItemResponse(BaseModel):
    id: UUID
    source_ref: str
    document_kind: str
    document_date: date | None
    source_type: str
    organization_role: str
    reference_locator: str
    raw_extracted_text: str
    normalized_facts: list[AssessmentEvidenceFactResponse]
    verification_state: str
    conflict_groups: list[str]
    staff_review_required: bool
    linked_fields: list[AssessmentEvidenceLinkedFieldResponse]
    external_transfer_allowed: Literal[False]
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AssessmentEvidenceLedgerResponse(BaseModel):
    draft_id: UUID
    resident_id: UUID
    case_ref: str
    file_intake_complete: bool
    items: list[AssessmentEvidenceLedgerItemResponse]


class AssessmentEvidenceSummaryCounts(BaseModel):
    confirmed_facts: int
    conflicts: int
    missing_items: int


class AssessmentEvidenceConfirmedFactResponse(BaseModel):
    field_key: str
    label: str
    value: Any
    status: Literal["confirmed", "reusable", "derivable"]
    document_types: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AssessmentEvidenceConflictResponse(BaseModel):
    field_key: str
    label: str
    conflict_values: list[str]
    selected_value: None = None
    document_types: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AssessmentEvidenceMissingItemResponse(BaseModel):
    field_key: str
    label: str
    value: None = None
    reason: Literal[
        "evidence_missing",
        "current_observation_required",
        "staff_decision_required",
    ]
    document_types: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)


class AssessmentEvidenceSummaryResponse(BaseModel):
    draft_id: UUID
    resident_id: UUID
    case_ref: str
    file_intake_complete: bool
    status: Literal["draft", "needs_confirmation"]
    counts: AssessmentEvidenceSummaryCounts
    confirmed_facts: list[AssessmentEvidenceConfirmedFactResponse]
    conflicts: list[AssessmentEvidenceConflictResponse]
    missing_items: list[AssessmentEvidenceMissingItemResponse]
    external_transfer_allowed: Literal[False]


class AssessmentChatEvidencePreviewItemResponse(BaseModel):
    source_ref: str
    summary: str
    reference_locator: str


class AssessmentChatEvidencePreviewResponse(BaseModel):
    resident_id: UUID
    period_start: date
    period_end: date
    total_count: int
    items: list[AssessmentChatEvidencePreviewItemResponse]
    external_transfer_allowed: Literal[False]


class NewAdmissionQuestionSourceResponse(BaseModel):
    source_ref: str
    source_type: str
    document_kind: str
    organization_role: str
    verification_state: str
    usage: str
    usage_label: str


class NewAdmissionReusableFactResponse(BaseModel):
    field_key: str
    label: str
    status: Literal["confirmed", "reusable", "derivable"]
    status_label: str
    value_kind: str
    value: Any
    evidence_refs: list[str]
    source_usages: list[str]
    source_usage_labels: list[str]
    document_types: list[str]
    derivation_note: str | None = None
    staff_confirmation_state: Literal["confirmed", "needs_confirmation"]


class NewAdmissionQuestionResponse(BaseModel):
    question_key: str
    field_key: str
    label: str
    question_type: Literal[
        "material_conflict",
        "current_observation_required",
        "user_decision_required",
        "admin_missing",
    ]
    question_type_label: str
    prompt: str
    reason: str
    value_kind: str
    evidence_refs: list[str]
    source_usages: list[str]
    source_usage_labels: list[str]
    document_types: list[str]
    conflict_values: list[str]
    selected_value: None = None


class NewAdmissionAssessmentStepResponse(BaseModel):
    step_number: int
    step_count: int
    document_type: Literal[
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
    ]
    title: str
    local_form_alias: str
    official_basis_state: Literal[
        "official_reference_found_latest_adoption_required",
        "latest_official_source_required",
    ]
    official_basis_label: str
    official_basis_notice: str
    output_status: Literal["draft", "needs_confirmation"]
    reusable_facts: list[NewAdmissionReusableFactResponse]
    new_questions: list[NewAdmissionQuestionResponse]
    carried_pending_questions: list[NewAdmissionQuestionResponse]
    reusable_fact_count: int
    new_question_count: int
    carried_pending_count: int
    score_generation_allowed: Literal[False]
    score: None = None
    risk_grade: None = None
    diagnosis: None = None
    blocked_result_notice: str


class NewAdmissionAssessmentWorkflowResponse(BaseModel):
    schema_version: Literal["new_admission_assessment_flow_v1"]
    case_ref: str
    revision: int
    output_status: Literal["draft", "needs_confirmation"]
    allowed_output_statuses: list[Literal["draft", "needs_confirmation"]]
    processing_scope: Literal["internal_only", "deidentified_dev"]
    external_transfer_allowed: Literal[False]
    workflow_notice: str
    product_workflow_only: Literal[True]
    legal_requirement_order_claimed: Literal[False]
    steps: list[NewAdmissionAssessmentStepResponse]
    assigned_question_count: int
    assigned_question_keys: list[str]
    duplicate_assigned_question_count: int
    final_human_confirmation_questions: list[NewAdmissionQuestionResponse]
    deferred_care_plan_questions: list[NewAdmissionQuestionResponse]
    score_generation_attempt_count: Literal[0]
    risk_grade_generation_attempt_count: Literal[0]
    diagnosis_generation_attempt_count: Literal[0]
    official_record_write_count: Literal[0]
    signature_confirmation_count: Literal[0]
    public_insurer_transfer_count: Literal[0]


class NewAdmissionCarePlanEvidenceResponse(BaseModel):
    source_ref: str
    document_kind: str
    source_type: str
    organization_role: str
    verification_state: str
    usage_label: str


class NewAdmissionCarePlanRevisionLinkResponse(BaseModel):
    revision: int
    before_value: Any | None = None
    after_value: Any | None = None
    before_status: str | None = None
    after_status: str | None = None
    before_evidence_refs: list[str]
    after_evidence_refs: list[str]


class NewAdmissionCarePlanConfirmedItemResponse(BaseModel):
    field_key: str
    label: str
    value_kind: str
    value: Any
    draft_text: str
    source_assessment_types: list[str]
    evidence_refs: list[str]
    evidence_sources: list[NewAdmissionCarePlanEvidenceResponse]
    confirmation_state: Literal["confirmed"]
    confirmed_by_name: str | None = None
    confirmed_at: datetime | None = None
    revision_links: list[NewAdmissionCarePlanRevisionLinkResponse]


class NewAdmissionCarePlanPendingItemResponse(BaseModel):
    field_key: str
    label: str
    category: Literal[
        "staff_confirmation",
        "material_conflict",
        "current_observation_required",
        "user_decision_required",
        "admin_missing",
        "expert_confirmation",
        "official_basis",
    ]
    category_label: str
    reason: str
    value_kind: str
    evidence_refs: list[str]
    evidence_sources: list[NewAdmissionCarePlanEvidenceResponse]
    conflict_values: list[str]
    selected_value: None = None
    revision_links: list[NewAdmissionCarePlanRevisionLinkResponse]


class NewAdmissionCarePlanPreviewResponse(BaseModel):
    schema_version: Literal["new_admission_care_plan_preview_v1"]
    case_ref: str
    revision: int
    output_status: Literal["draft", "needs_confirmation"]
    allowed_output_statuses: list[Literal["draft", "needs_confirmation"]]
    processing_scope: Literal["internal_only", "deidentified_dev"]
    external_transfer_allowed: Literal[False]
    confirmed_assessment_results: list[NewAdmissionCarePlanConfirmedItemResponse]
    reflected_plan_items: list[NewAdmissionCarePlanConfirmedItemResponse]
    staff_questions: list[NewAdmissionCarePlanPendingItemResponse]
    conflicts_and_current_observations: list[
        NewAdmissionCarePlanPendingItemResponse
    ]
    expert_review_items: list[NewAdmissionCarePlanPendingItemResponse]
    confirmed_result_count: int
    reflected_item_count: int
    staff_question_count: int
    conflict_or_observation_count: int
    expert_review_count: int
    duplicate_question_count: Literal[0]
    blocked_auto_value_kinds: list[str]
    auto_generated_restricted_value_count: Literal[0]
    official_record_write_count: Literal[0]
    signature_confirmation_count: Literal[0]
    public_insurer_transfer_count: Literal[0]
    revision_mutation_count: Literal[0]
    safety_notice: str


class NewAdmissionFormEvidenceSourceResponse(BaseModel):
    source_ref: str
    document_kind: str
    verification_state: str
    organization_role: str


class NewAdmissionFormRevisionLinkResponse(BaseModel):
    revision: int
    before_value: Any | None = None
    after_value: Any | None = None
    before_status: str | None = None
    after_status: str | None = None


class NewAdmissionFormItemResponse(BaseModel):
    field_key: str
    label: str
    value_kind: str
    document_types: list[
        Literal[
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
            "long_term_care_service_plan",
        ]
    ] = Field(default_factory=list)
    state: Literal[
        "confirmed_fact",
        "evidence_found",
        "insufficient_evidence",
        "material_conflict",
        "current_observation_required",
        "expert_review_required",
        "staff_input_required",
        "staff_edited",
    ]
    state_label: str
    draft_text: str
    evidence_refs: list[str]
    evidence_sources: list[NewAdmissionFormEvidenceSourceResponse]
    conflict_values: list[str]
    revision_links: list[NewAdmissionFormRevisionLinkResponse]


class NewAdmissionFormSectionRowResponse(BaseModel):
    row_key: str
    field_key: str
    label: str
    value: str
    value_kind: str
    state: Literal[
        "confirmed_fact",
        "evidence_found",
        "insufficient_evidence",
        "material_conflict",
        "current_observation_required",
        "expert_review_required",
        "staff_input_required",
        "staff_edited",
    ]
    state_label: str
    review_state: Literal[
        "",
        "confirmed",
        "not_applicable",
        "not_confirmed",
        "follow_up",
    ] = ""
    evidence_refs: list[str]
    cells: dict[str, str] = Field(default_factory=dict)
    cell_states: dict[str, str] = Field(default_factory=dict)
    cell_evidence_refs: dict[str, list[str]] = Field(default_factory=dict)


class NewAdmissionFormSectionResponse(BaseModel):
    section_key: str
    title: str
    need_area: str | None = None
    columns: list[str] = Field(default_factory=list)
    rows: list[NewAdmissionFormSectionRowResponse]
    notice: str | None = None


class NewAdmissionFormDocumentResponse(BaseModel):
    document_type: Literal[
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
        "long_term_care_service_plan",
    ]
    title: str
    status: Literal["draft", "needs_confirmation"]
    items: list[NewAdmissionFormItemResponse]
    sections: list[NewAdmissionFormSectionResponse]
    draft_text: str
    confirmed_or_evidence_count: int
    staff_input_count: int


class NewAdmissionFormAnalysisSummaryResponse(BaseModel):
    source_count: int
    confirmed_fact_count: int
    evidence_found_count: int
    staff_input_count: int
    missing_state_counts: dict[str, int]


class NewAdmissionComprehensiveReportResponse(BaseModel):
    title: str
    summary: str
    evidence_items: list[NewAdmissionFormItemResponse]
    pending_items: list[NewAdmissionFormItemResponse]
    source_overview: list[NewAdmissionFormEvidenceSourceResponse]


class NewAdmissionFormSafetyResponse(BaseModel):
    official_record_saved: Literal[False]
    signature_confirmed: Literal[False]
    public_insurer_transferred: Literal[False]
    restricted_auto_generation_count: Literal[0]
    notice: str


class NewAdmissionCarePlanGateResponse(BaseModel):
    available: bool
    reviewed_assessment_count: int = Field(ge=0, le=4)
    required_assessment_count: Literal[4]
    reason: str


class NewAdmissionFormWorkspaceResponse(BaseModel):
    schema_version: Literal["new_admission_form_workspace_v1"]
    case_ref: str
    revision: int
    reason: Literal[
        "new_admission",
        "periodic_reassessment",
        "state_change_reassessment",
        "staff_review",
    ]
    assessment_date: str | None = None
    period_start: str | None = None
    period_end: str | None = None
    materials: list[dict[str, Any]] = Field(default_factory=list)
    output_status: Literal["draft", "needs_confirmation"]
    processing_scope: Literal["internal_only", "deidentified_dev"]
    external_transfer_allowed: Literal[False]
    analysis_summary: NewAdmissionFormAnalysisSummaryResponse
    comprehensive_report: NewAdmissionComprehensiveReportResponse
    documents: list[NewAdmissionFormDocumentResponse]
    care_plan_gate: NewAdmissionCarePlanGateResponse
    safety: NewAdmissionFormSafetyResponse


class NewAdmissionQuestionFlowResponse(BaseModel):
    draft_id: UUID
    resident_id: UUID
    case_ref: str
    revision: int
    output_status: Literal["draft", "needs_confirmation"]
    processing_scope: Literal["internal_only", "deidentified_dev"]
    external_transfer_allowed: Literal[False]
    sources: list[NewAdmissionQuestionSourceResponse]
    source_counts: dict[str, int]
    reusable_facts: list[NewAdmissionReusableFactResponse]
    questions: list[NewAdmissionQuestionResponse]
    reusable_fact_count: int
    question_count: int
    question_count_by_type: dict[str, int]
    duplicate_question_count: int
    assessment_workflow: NewAdmissionAssessmentWorkflowResponse
    care_plan_preview: NewAdmissionCarePlanPreviewResponse | None
    form_workspace: NewAdmissionFormWorkspaceResponse
    safety_notices: list[str]


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
    save_history: bool = False
    response_mode: Literal["full", "briefing"] = "full"
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
        month_index = self.start_date.month - 1 + 6
        limit_year = self.start_date.year + month_index // 12
        limit_month = month_index % 12 + 1
        six_month_boundary = date(
            limit_year,
            limit_month,
            min(
                self.start_date.day,
                monthrange(limit_year, limit_month)[1],
            ),
        )
        if self.end_date >= six_month_boundary:
            raise ValueError(
                "한 번에 최대 6개월까지 정리할 수 있습니다. "
                "시작일을 늦추거나 종료일을 앞당겨 주세요."
            )
        return self


class PeriodEvidenceRequest(PeriodWorkdeskRequest):
    message_ids: list[UUID] = Field(min_length=1, max_length=100)


class PeriodWorkdeskSource(BaseModel):
    message: MessageResponse
    room_name: str
    resident_names: list[str] = Field(default_factory=list)
    comments: list[MessageCommentResponse] = Field(default_factory=list)
    read_count: int = 0
    reply_count: int = 0
    reply_user_count: int = 0


class PeriodRecordEvent(BaseModel):
    event_group_id: str
    resident_id: UUID | None = None
    resident_name: str | None = None
    summary: str
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    document_candidate_types: list[CareDocumentCandidateType] = Field(
        default_factory=list
    )
    document_candidate_reasons: dict[CareDocumentCandidateType, str] = Field(
        default_factory=dict
    )
    document_candidate_review_types: list[CareDocumentCandidateType] = Field(
        default_factory=list
    )
    document_candidate_review_reasons: dict[CareDocumentCandidateType, str] = Field(
        default_factory=dict
    )
    evidence_ids: list[UUID] = Field(default_factory=list)
    room_names: list[str] = Field(default_factory=list)
    sender_names: list[str] = Field(default_factory=list)
    occurred_at: datetime
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
    document_candidate_types: list[CareDocumentCandidateType] = Field(
        default_factory=list,
        max_length=7,
    )
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
        document_candidate_types = list(dict.fromkeys(self.document_candidate_types))
        if self.record_usage_tag is not None and self.record_usage_tag not in tags:
            tags.insert(0, self.record_usage_tag)
        if not tags and not document_candidate_types:
            raise ValueError("정리할 기록 종류를 하나 이상 선택해 주세요.")
        self.record_usage_tags = tags
        self.record_usage_tag = tags[0] if tags else None
        self.document_candidate_types = document_candidate_types
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


class PeriodDocumentDraft(BaseModel):
    key: str
    event_group_id: str
    resident_id: UUID
    resident_name: str
    document_type: DailyDocumentType
    trigger: Literal["event"] = "event"
    status: Literal["draft", "needs_confirmation"] = "draft"
    content: str
    draft_fields: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)
    human_verification_fields: list[str] = Field(default_factory=list)
    verification_questions: list[str] = Field(default_factory=list)
    source_message_ids: list[UUID] = Field(default_factory=list)


class PeriodRecordSummaryResponse(BaseModel):
    record_usage_tag: RecordUsageTag | None = None
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    document_candidate_types: list[CareDocumentCandidateType] = Field(
        default_factory=list
    )
    summary: str
    evidence_ids: list[UUID]
    generator: str
    elapsed_ms: int = 0
    document_drafts: list[PeriodDocumentDraft] = Field(default_factory=list)


class SocialWorkerWritingGuidance(BaseModel):
    title: str
    previous_state: str | None = None
    current_state: str | None = None
    confirmation_questions: list[str] = Field(default_factory=list)
    suggested_wording: str
    related_document_types: list[CareDocumentCandidateType] = Field(
        default_factory=list
    )
    safety_notice: str
    plan_review_recommended: bool = False
    plan_review_notice: str | None = None
    based_on_confirmed_facts: bool = False


class CareBriefingCard(BaseModel):
    event_group_id: str | None = None
    resident_id: UUID
    resident_name: str
    priority: Literal["first", "check", "observe"]
    display_group: Literal[
        "today_schedule",
        "attention",
        "carryover",
        "background",
        "completed",
    ] = "background"
    importance_score: int = 0
    due_at: datetime | None = None
    change_summary: str
    final_status: Literal[
        "needs_confirmation",
        "in_progress",
        "completed",
        "monitoring",
    ]
    final_status_summary: str
    check_reasons: list[str] = Field(default_factory=list)
    completed_actions: list[str] = Field(default_factory=list)
    pending_checks: list[str] = Field(default_factory=list)
    document_types: list[DailyDocumentType] = Field(default_factory=list)
    record_usage_tags: list[RecordUsageTag] = Field(default_factory=list)
    document_candidate_types: list[CareDocumentCandidateType] = Field(
        default_factory=list
    )
    source_message_ids: list[UUID] = Field(default_factory=list)
    current_message_count: int = 0
    baseline_message_count: int = 0
    occurred_at: datetime
    latest_at: datetime
    social_worker_guidance: SocialWorkerWritingGuidance | None = None


class CareBriefingSummary(BaseModel):
    comparison_days: int = 7
    today_schedule_count: int = 0
    needs_attention_count: int = 0
    carryover_count: int = 0
    background_count: int = 0
    completed_count: int = 0
    pending_check_count: int = 0
    document_candidate_count: int = 0
    cards: list[CareBriefingCard] = Field(default_factory=list)


class FieldCareBriefingEntry(BaseModel):
    event_group_id: str
    occurred_at: datetime
    time_label: str
    summary: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    explicit_pending: bool = False
    conflict_note: str | None = None


class FieldCareBriefingResidentGroup(BaseModel):
    resident_id: UUID
    resident_name: str
    entries: list[FieldCareBriefingEntry] = Field(default_factory=list)


class FieldCareBriefingDay(BaseModel):
    date: date
    residents: list[FieldCareBriefingResidentGroup] = Field(default_factory=list)


class FieldCareConsultationReference(BaseModel):
    event_group_id: str
    resident_id: UUID
    resident_name: str
    occurred_at: datetime
    time_label: str
    summary: str
    evidence_ids: list[UUID] = Field(default_factory=list)


class FieldCareBriefingHistorySummary(BaseModel):
    id: UUID
    revision: int
    period_start: date
    period_end: date
    resident_id: UUID | None = None
    overall_summary: str
    care_reference_count: int
    consultation_reference_count: int
    created_at: datetime


class CareTopicEntry(BaseModel):
    message_id: UUID
    comment_id: UUID | None = None
    attachment_id: UUID | None = None
    source_kind: Literal["message", "comment", "attachment"] = "message"
    occurred_at: datetime
    summary: str
    kind: Literal["event", "repeated", "different", "followup", "conflict"]
    event_key: str


class CareTopic(BaseModel):
    key: str
    resident_id: UUID
    resident_name: str
    topic: str
    summary: str
    kinds: list[Literal["event", "repeated", "different", "followup", "conflict"]]
    evidence_ids: list[UUID]
    first_at: datetime
    latest_at: datetime
    entries: list[CareTopicEntry]


class RecordQuestionRequest(PeriodWorkdeskRequest):
    ai_phase: Literal["auto", "prepare"] = "auto"
    range_mode: Literal["default", "fixed"] = "fixed"
    default_resident_id: UUID | None = None
    question: str = Field(min_length=1, max_length=500)

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("질문을 입력해 주세요.")
        return value


class RecordQuestionTimeline(BaseModel):
    date: str
    fact: str
    evidence_ids: list[UUID]
    background: bool = False
    resident_name: str = ""


class RecordAnswerSentence(BaseModel):
    text: str
    evidence_ids: list[UUID] = Field(default_factory=list)


class RecordQuestionResponse(BaseModel):
    aggregate: dict[str, Any] | None = None
    structured_facts: list[dict[str, Any]] = Field(default_factory=list)
    ai_enhancement_available: bool = False
    answer_sentences: list[RecordAnswerSentence] = Field(default_factory=list)
    generation_verified: bool = False
    resolved_resident_name: str | None = None
    error_type: str | None = None
    performance: dict[str, float | int | bool | str | None] = Field(default_factory=dict)
    question: str
    # Echo the user's local calendar dates; the end date is inclusive.
    period_start: date
    period_end: date
    resident_id: UUID | None = None
    answer: str
    evidence_ids: list[UUID] = Field(default_factory=list)
    sources: list[PeriodWorkdeskSource] = Field(default_factory=list)
    matched_count: int = 0
    truncated: bool = False
    generator: str = "local-records-v1"
    limitation: str | None = None
    processing_method: Literal["local_ai", "fallback_model", "rules", "failed", "clarification", "no_records"] = "rules"
    timeline: list[RecordQuestionTimeline] = Field(default_factory=list)
    current_status: str | None = None
    unknowns: list[str] = Field(default_factory=list)
    fallback_notice: str | None = None
    ai_elapsed_ms: int = 0


class PeriodWorkdeskResponse(BaseModel):
    _question_sources: list[Any] = PrivateAttr(default_factory=list)
    source_ids: list[UUID] = Field(default_factory=list)
    attachment_count: int = 0
    response_mode: Literal["full", "briefing"] = "full"
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
    care_topics: list[CareTopic] = Field(default_factory=list)
    record_group_counts: dict[str, int] = Field(default_factory=dict)
    document_candidate_counts: dict[str, int] = Field(default_factory=dict)
    briefing: CareBriefingSummary = Field(default_factory=CareBriefingSummary)
    truncated: bool = False
    processed_periods: list[str] = Field(default_factory=list)
    truncated_periods: list[str] = Field(default_factory=list)
    scenario_label: str | None = None
    scenario_notice: str | None = None
    overall_summary: str = ""
    daily_care_references: list[FieldCareBriefingDay] = Field(default_factory=list)
    consultation_references: list[FieldCareConsultationReference] = Field(
        default_factory=list
    )
    care_reference_count: int = 0
    consultation_reference_count: int = 0
    history_id: UUID | None = None
    history_revision: int | None = None
    generated_at: datetime | None = None


class LoginResponse(BaseModel):
    user: UserResponse
    expires_at: datetime


class ReviewerSessionResponse(LoginResponse):
    destination: Literal["chat", "care_briefing"]
    room_id: UUID | None = None


class AdminConversationAccessRequest(BaseModel):
    password: str = Field(min_length=1, max_length=200)


class AdminConversationAccessResponse(BaseModel):
    active: bool
    expires_at: datetime | None


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str | None = Field(default=None, min_length=6, max_length=200)
    new_username: str | None = None


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool
    is_current: bool


class AdminPasswordResetRequest(BaseModel):
    temporary_password: str = Field(min_length=6, max_length=200)


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


class VoiceCallIceServerResponse(BaseModel):
    urls: list[str]
    username: str | None = None
    credential: str | None = None


class VoiceCallConfigResponse(BaseModel):
    enabled: bool
    max_participants: int
    max_video_participants: int
    ice_servers: list[VoiceCallIceServerResponse]


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


class PushTestRequest(PushSubscriptionDelete):
    @field_validator("endpoint")
    @classmethod
    def validate_push_endpoint(cls, value: str) -> str:
        endpoint = value.strip()
        if not endpoint.startswith("https://"):
            raise ValueError("푸시 알림 주소는 HTTPS여야 합니다.")
        return endpoint


class PushSubscriptionResponse(BaseModel):
    enabled: bool
    active: bool
    message: str
    resubscribe_required: bool = False
    reason_code: Literal["endpoint_expired"] | None = None


class MobilePushDeviceCreate(BaseModel):
    installation_id: str = Field(min_length=16, max_length=80)
    token: str = Field(min_length=32, max_length=4096)
    platform: Literal["android"] = "android"
    app_version: str | None = Field(default=None, max_length=40)

    @field_validator("installation_id", "token", "app_version")
    @classmethod
    def strip_mobile_push_values(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class MobilePushDeviceDelete(BaseModel):
    installation_id: str = Field(min_length=16, max_length=80)


class MobilePushDeviceResponse(BaseModel):
    enabled: bool
    active: bool
    message: str


class VoiceCallDeliveryReceiptRequest(BaseModel):
    stage: Literal[
        "device_received",
        "notification_posted",
        "ringtone_requested",
        "notification_cancelled",
    ]
    success: bool
    error_type: str | None = Field(default=None, max_length=80)
    elapsed_ms: int | None = Field(default=None, ge=0, le=120000)
    device_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
