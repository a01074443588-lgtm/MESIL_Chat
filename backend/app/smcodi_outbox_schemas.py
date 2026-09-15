from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


SMCODI_HANDOVER_CONTRACT = "smcodi_staff_hub_handover_v1"

SmcodiOutboxStatus = Literal["ready", "blocked"]
SmcodiUrgency = Literal["normal", "caution", "urgent"]


class SmcodiOutboxPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_record_id: UUID
    source_version: int | None = Field(default=None, ge=1)
    target_mapping_id: UUID | None = None


class SmcodiValidationIssue(BaseModel):
    code: str
    field: str
    message: str


class SmcodiStaffHubHandoverV1(BaseModel):
    contract: Literal["smcodi_staff_hub_handover_v1"] = SMCODI_HANDOVER_CONTRACT
    recipient_id: UUID | None
    occurred_at: datetime
    observation_text: str = Field(min_length=1, max_length=10000)
    action_text: str = Field(default="", max_length=10000)
    result_text: str = Field(default="", max_length=10000)
    measurement_text: str = Field(default="", max_length=2000)
    target_unit_id: UUID | None
    assigned_user_id: UUID | None
    urgency: SmcodiUrgency
    needs_follow_up: bool
    source_record_id: UUID
    source_version_id: UUID
    source_version: int = Field(ge=1)
    source_content_hash: str = Field(min_length=64, max_length=64)
    idempotency_key: str = Field(min_length=64, max_length=64)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("업무 발생 시각에는 시간대 정보가 필요합니다.")
        return value


class SmcodiOutboxResponse(BaseModel):
    id: UUID
    organization_id: UUID
    source_record_id: UUID
    source_version_id: UUID
    source_version: int
    source_content_hash: str
    contract_name: Literal["smcodi_staff_hub_handover_v1"]
    target_mapping_id: UUID | None
    target_recipient_id: UUID | None
    target_unit_id: UUID | None
    assigned_user_id: UUID | None
    status: SmcodiOutboxStatus
    validation_issues: list[SmcodiValidationIssue]
    payload: SmcodiStaffHubHandoverV1
    payload_hash: str
    idempotency_key: str
    created_by_user_id: UUID
    is_test_data: bool
    created_at: datetime
    updated_at: datetime


class SmcodiOutboxPreviewResponse(BaseModel):
    created: bool
    item: SmcodiOutboxResponse


class SmcodiOutboxListResponse(BaseModel):
    items: list[SmcodiOutboxResponse]
    total: int
    limit: int
    offset: int
