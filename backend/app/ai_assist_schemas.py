from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


AiTaskType = Literal[
    "image_text",
    "image_explain",
    "audio_summary",
    "summary",
    "history_search",
    "risk_check",
    "question",
]
AiTurnStatus = Literal[
    "queued",
    "preparing",
    "extracting",
    "searching",
    "reasoning",
    "validating",
    "completed",
    "failed",
    "cancelled",
]
AiServiceContext = Literal["facility", "daycare", "homecare"]
AiProviderId = Literal[
    "rules",
    "ollama",
    "nvidia",
    "openai",
    "gemini",
    "anthropic",
    "openai_compatible",
]
AiProviderProcessingLocation = Literal["rules", "local", "external", "unconfigured"]
AiProviderConnectionState = Literal[
    "ready",
    "configured_unverified",
    "unconfigured",
    "unavailable",
    "not_implemented",
]
AiProviderEndpointScope = Literal[
    "rules",
    "local",
    "internal",
    "external",
    "invalid",
    "unconfigured",
]
AiProviderCheckName = Literal[
    "configuration",
    "authentication",
    "model_list",
    "capabilities",
    "structured_contract",
    "latency",
]
AiProviderCheckStatus = Literal["passed", "failed", "not_required", "not_run"]


class AiProviderCheckResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: AiProviderCheckName
    status: AiProviderCheckStatus
    latency_ms: int | None = Field(default=None, ge=0)
    detail: str = Field(min_length=1, max_length=240)


class AiProviderConnectionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: AiProviderId
    display_name: str = Field(min_length=1, max_length=80)
    adapter_available: bool
    configured: bool
    state: AiProviderConnectionState
    processing_location: AiProviderProcessingLocation
    endpoint_scope: AiProviderEndpointScope
    credential_configured: bool
    configured_model: str | None = Field(default=None, max_length=160)
    discovered_models: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=30)
    enabled_features: list[str] = Field(default_factory=list, max_length=30)
    external_transmission_default_allowed: Literal[False] = False
    requires_deidentified_approval: bool
    checks: list[AiProviderCheckResponse] = Field(default_factory=list, max_length=10)
    notice: str = Field(min_length=1, max_length=500)


class AiProviderStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["ai_provider_status_v1"] = "ai_provider_status_v1"
    providers: list[AiProviderConnectionResponse] = Field(min_length=1)
    secret_values_included: Literal[False] = False


class AiAssistTaskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_type: AiTaskType
    question: str | None = Field(default=None, max_length=4000)
    attachment_id: UUID | None = None
    service_context: AiServiceContext | None = None
    deidentified_confirmed_by_user: bool = False

    @field_validator("question")
    @classmethod
    def normalize_question(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class AiEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_no: int = Field(ge=1)
    statement: str = Field(min_length=1, max_length=1000)


class AiImageVisibleText(BaseModel):
    """Text read from one image in the ordered multi-image request."""

    model_config = ConfigDict(extra="forbid")

    image_no: int
    filename: str = Field(min_length=1, max_length=255)
    text: str = Field(min_length=1, max_length=12000)


class AiAssistResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=8000)
    visible_text: str | None = Field(default=None, max_length=12000)
    image_texts: list[AiImageVisibleText] = Field(default_factory=list, max_length=10)
    evidence: list[AiEvidence] = Field(default_factory=list, max_length=50)
    uncertainties: list[str] = Field(default_factory=list, max_length=20)
    recommended_actions: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("uncertainties", "recommended_actions")
    @classmethod
    def validate_short_text_lists(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if not item or len(item) > 1000:
                raise ValueError("AI 결과 목록의 각 항목은 1~1000자여야 합니다.")
            normalized.append(item)
        return normalized

class AiAssistWorkerEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ok: Literal[True]
    result: AiAssistResult
    provider: str = Field(min_length=1, max_length=50)
    model: str = Field(min_length=1, max_length=160)
    prompt_version: str = Field(min_length=1, max_length=80)
    elapsed_ms: int = Field(ge=0)


class AiResidentNameCandidate(BaseModel):
    """A roster-backed suggestion; it is never an automatic OCR correction."""

    model_config = ConfigDict(extra="forbid")

    recognized: str = Field(min_length=1, max_length=100)
    candidate: str = Field(min_length=1, max_length=100)
    resident_id: UUID
    service_type: AiServiceContext
    confidence: float = Field(ge=0, le=1)


class AiAssistTurnResponse(BaseModel):
    id: UUID
    status: AiTurnStatus
    task_type: AiTaskType
    question: str | None
    answer: str | None
    visible_text: str | None
    evidence: list[AiEvidence]
    uncertainties: list[str]
    recommended_actions: list[str]
    service_context: AiServiceContext | None = None
    service_context_source: str | None = None
    service_context_notice: str | None = None
    resident_name_candidates: list[AiResidentNameCandidate] = Field(
        default_factory=list
    )
    provider: str | None
    model: str | None
    processing_location: Literal[
        "rules",
        "local",
        "internal",
        "external",
        "unconfigured",
    ] = "unconfigured"
    external_transmission: bool = False
    deidentification_status: Literal[
        "confirmed_deidentified",
        "protected_local",
        "not_sent_external",
        "unknown",
    ] = "unknown"
    fallback_active: bool = False
    fallback_state: Literal[
        "primary",
        "fallback",
        "rules",
        "blocked",
        "unknown",
    ] = "unknown"
    fallback_reason: str | None = Field(default=None, max_length=500)
    enhancement_status: Literal[
        "pending",
        "completed",
        "baseline",
        "failed",
    ] = "pending"
    human_review_required: Literal[True] = True
    elapsed_ms: int | None
    error_message: str | None
    created_at: datetime
    completed_at: datetime | None


class AiConversationDetail(BaseModel):
    id: UUID
    message_id: UUID
    status: AiTurnStatus
    turns: list[AiAssistTurnResponse]


class AiAssistShareResponse(BaseModel):
    turn_id: UUID
    message_id: UUID
    shared_at: datetime
    created: bool = False


class AiAssistConfigResponse(BaseModel):
    enabled: bool = True
    codex_ready: bool
    whisper_ready: bool
    local_fallback_ready: bool = True
    primary_provider: Literal["codex_worker"] = "codex_worker"
    fallback_provider: Literal["local_deterministic"] = "local_deterministic"
    worker_configured: bool
    external_real_data_enabled: bool
    external_real_image_data_enabled: bool = False
    nemotron_ready: bool = False
    local_ai_ready: bool = False
    local_ai_status_message: str = ""
    task_types: list[AiTaskType]


class AiAssistSourceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_no: int = Field(ge=1)
    source_type: Literal[
        "message",
        "attachment",
        "confirmed_record",
        "manual_context",
    ]
    label: str = Field(min_length=1, max_length=240)
    text: str | None = Field(default=None, max_length=12000)
    metadata: dict[str, Any] = Field(default_factory=dict)
