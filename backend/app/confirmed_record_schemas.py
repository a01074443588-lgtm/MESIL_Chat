from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


ServiceContext = Literal[
    "facility_care",
    "day_care",
    "home_visit_care",
]
WorkCategory = Literal[
    "daily_care",
    "nutrition",
    "health",
    "safety",
    "consultation",
    "rehabilitation",
]
RecordUrgency = Literal["low", "medium", "high", "urgent"]
ConfirmedRecordLifecycle = Literal["active", "withdrawn"]


class ConfirmedRecordContent(BaseModel):
    """확정 업무기록 한 버전에 포함되는 정규화된 업무 내용."""

    occurred_at: datetime
    service_context: ServiceContext
    work_category: WorkCategory
    observation_text: str = Field(min_length=1, max_length=4000)
    action_text: str = Field(default="", max_length=4000)
    result_text: str = Field(default="", max_length=4000)
    measurement_text: str = Field(default="", max_length=2000)
    urgency: RecordUrgency = "low"
    needs_follow_up: bool = False

    @field_validator(
        "observation_text",
        "action_text",
        "result_text",
        "measurement_text",
    )
    @classmethod
    def strip_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("업무 발생 시각에는 시간대 정보가 필요합니다.")
        return value


class ConfirmedRecordSearchFilters(BaseModel):
    q: str | None = Field(default=None, max_length=200)
    resident_id: UUID | None = None
    service_context: ServiceContext | None = None
    work_category: WorkCategory | None = None
    urgency: RecordUrgency | None = None
    needs_follow_up: bool | None = None
    lifecycle_status: ConfirmedRecordLifecycle | None = "active"
    occurred_from: datetime | None = None
    occurred_to: datetime | None = None
    is_test_data: bool | None = None
    limit: int = Field(default=100, ge=1, le=200)
    offset: int = Field(default=0, ge=0)

    @field_validator("q")
    @classmethod
    def strip_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @field_validator("occurred_from", "occurred_to")
    @classmethod
    def require_filter_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("검색 시각에는 시간대 정보가 필요합니다.")
        return value

    @model_validator(mode="after")
    def validate_period(self) -> "ConfirmedRecordSearchFilters":
        if (
            self.occurred_from is not None
            and self.occurred_to is not None
            and self.occurred_from > self.occurred_to
        ):
            raise ValueError("검색 시작 시각은 종료 시각보다 늦을 수 없습니다.")
        return self


class ConfirmedRecordResponse(BaseModel):
    id: UUID
    organization_id: UUID
    work_item_id: UUID
    resident_id: UUID
    resident_name: str
    occurred_at: datetime
    service_context: ServiceContext
    work_category: WorkCategory
    observation_text: str
    action_text: str
    result_text: str
    measurement_text: str
    urgency: RecordUrgency
    needs_follow_up: bool
    lifecycle_status: ConfirmedRecordLifecycle
    current_version: int
    current_content_hash: str
    confirmed_by_user_id: UUID
    confirmed_at: datetime
    is_test_data: bool
    created_at: datetime
    updated_at: datetime


class ConfirmedRecordSourceResponse(BaseModel):
    id: UUID
    record_version_id: UUID
    source_message_id: UUID
    attachment_id: UUID | None
    text_extraction_id: UUID | None
    evidence_role: Literal["primary", "supporting"]
    source_key: str
    source_content_hash: str
    excerpt: str | None
    created_at: datetime


class ConfirmedRecordVersionResponse(BaseModel):
    id: UUID
    version: int
    content_hash: str
    snapshot: dict[str, Any]
    change_reason: str | None
    created_by_user_id: UUID
    created_at: datetime
    sources: list[ConfirmedRecordSourceResponse] = Field(default_factory=list)


class ConfirmedRecordDetailResponse(ConfirmedRecordResponse):
    versions: list[ConfirmedRecordVersionResponse] = Field(default_factory=list)


class ConfirmedRecordSearchResponse(BaseModel):
    items: list[ConfirmedRecordResponse]
    total: int
    limit: int
    offset: int
