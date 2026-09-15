from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CARE_BRIEFING_VERSION = "care_briefing_v1"

CareBriefingPrivacyMode = Literal["local_sensitive", "external_deidentified"]
CareBriefingSourceKind = Literal[
    "message",
    "reply",
    "photo",
    "image_ocr",
    "audio_transcript",
    "file_text",
]
CareBriefingSourceVerification = Literal[
    "source_record",
    "ai_extracted",
    "staff_corrected",
]
CareBriefingFinalStatus = Literal[
    "needs_confirmation",
    "in_progress",
    "completed",
    "monitoring",
]
CareBriefingPriority = Literal["first", "check", "observe"]
CareBriefingValidationStatus = Literal["needs_staff_review", "source_linked"]
CareBriefingFailureCode = Literal[
    "privacy_blocked",
    "provider_unavailable",
    "timeout",
    "invalid_json",
    "schema_validation_failed",
    "missing_required_output",
    "unsupported_evidence",
    "empty_result",
    "internal_error",
]


class CareBriefingStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareBriefingTransmissionPolicy(CareBriefingStrictModel):
    privacy_mode: CareBriefingPrivacyMode
    external_ai_allowed: bool = False
    contains_direct_identifiers: bool = False

    @model_validator(mode="after")
    def validate_external_policy(self):
        if self.external_ai_allowed and self.privacy_mode != "external_deidentified":
            raise ValueError(
                "외부 AI 전송은 external_deidentified 정책에서만 허용됩니다."
            )
        if self.privacy_mode == "external_deidentified" and self.contains_direct_identifiers:
            raise ValueError("비식별 외부 전송 입력에 직접 식별정보가 남아 있습니다.")
        return self


class CareBriefingAnalysisWindow(CareBriefingStrictModel):
    start_at: datetime
    end_at: datetime
    as_of: datetime
    timezone: str = Field(default="Asia/Seoul", min_length=1, max_length=80)
    includes_unresolved_carryover: bool = True

    @model_validator(mode="after")
    def validate_window(self):
        if self.start_at > self.end_at:
            raise ValueError("분석 시작일은 종료일보다 늦을 수 없습니다.")
        if self.end_at > self.as_of:
            raise ValueError("분석 종료일은 브리핑 기준시각보다 늦을 수 없습니다.")
        return self


class CareBriefingInputSubject(CareBriefingStrictModel):
    subject_id: str = Field(min_length=1, max_length=120)
    display_label: str = Field(min_length=1, max_length=120)
    verification_status: Literal["confirmed", "needs_confirmation"] = "confirmed"

    @field_validator("subject_id", "display_label")
    @classmethod
    def strip_subject_text(cls, value: str) -> str:
        return value.strip()


class CareBriefingInputSource(CareBriefingStrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    source_kind: CareBriefingSourceKind
    conversation_id: str = Field(min_length=1, max_length=160)
    parent_source_id: str | None = Field(default=None, max_length=160)
    subject_ids: list[str] = Field(default_factory=list, max_length=20)
    author_role: str | None = Field(default=None, max_length=120)
    occurred_at: datetime
    text: str | None = Field(default=None, max_length=16000)
    media_id: str | None = Field(default=None, max_length=160)
    verification_status: CareBriefingSourceVerification = "source_record"

    @field_validator(
        "source_id",
        "conversation_id",
        "parent_source_id",
        "author_role",
        "text",
        "media_id",
    )
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("subject_ids")
    @classmethod
    def validate_subject_ids(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values if value.strip()]
        if len(normalized) != len(set(normalized)):
            raise ValueError("한 근거에 같은 대상자 식별자를 중복할 수 없습니다.")
        return normalized

    @model_validator(mode="after")
    def validate_source_content(self):
        if self.text is None and self.media_id is None:
            raise ValueError("근거에는 text 또는 media_id가 하나 이상 필요합니다.")
        if self.source_kind == "reply" and not self.parent_source_id:
            raise ValueError("답글 근거에는 parent_source_id가 필요합니다.")
        return self


class CareBriefingV1Input(CareBriefingStrictModel):
    version: Literal["care_briefing_v1"] = CARE_BRIEFING_VERSION
    window: CareBriefingAnalysisWindow
    transmission_policy: CareBriefingTransmissionPolicy
    subjects: list[CareBriefingInputSubject] = Field(default_factory=list, max_length=500)
    sources: list[CareBriefingInputSource] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_references(self):
        subject_ids = [subject.subject_id for subject in self.subjects]
        if len(subject_ids) != len(set(subject_ids)):
            raise ValueError("대상자 식별자는 입력에서 고유해야 합니다.")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("근거 식별자는 입력에서 고유해야 합니다.")
        known_subject_ids = set(subject_ids)
        known_source_ids = set(source_ids)
        for source in self.sources:
            unknown_subjects = set(source.subject_ids) - known_subject_ids
            if unknown_subjects:
                raise ValueError(
                    f"근거 {source.source_id}가 알 수 없는 대상자를 참조합니다."
                )
            if source.parent_source_id and source.parent_source_id not in known_source_ids:
                raise ValueError(
                    f"근거 {source.source_id}의 원문 근거를 찾을 수 없습니다."
                )
        return self


class CareBriefingSubjectRef(CareBriefingStrictModel):
    # Always emit this key. Unknown subjects use null; confirmed subjects must
    # copy an identifier from the standard input without inventing one.
    subject_id: str | None = Field(max_length=120)
    display_label: str = Field(min_length=1, max_length=120)
    verification_status: Literal["confirmed", "needs_confirmation"]

    @model_validator(mode="after")
    def validate_unknown_subject(self):
        if self.verification_status == "confirmed" and self.subject_id is None:
            raise ValueError("확정 대상자에는 subject_id가 필요합니다.")
        if self.verification_status == "needs_confirmation" and self.subject_id is None:
            if self.display_label != "확인 필요":
                raise ValueError(
                    "대상자를 특정하지 못한 경우 표시명은 '확인 필요'여야 합니다."
                )
        return self


class CareBriefingEvidenceRef(CareBriefingStrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    supports: list[
        Literal["subject", "event", "status", "time", "importance", "completion"]
    ] = Field(min_length=1, max_length=6)
    fact_summary: str = Field(min_length=1, max_length=600)


class CareBriefingImportanceReason(CareBriefingStrictModel):
    reason: str = Field(min_length=1, max_length=600)
    evidence_source_ids: list[str] = Field(min_length=1, max_length=20)


class CareBriefingVerificationItem(CareBriefingStrictModel):
    field: Literal[
        "subject",
        "facts",
        "time",
        "assignee",
        "completion",
        "follow_up",
        "other",
    ]
    question: str = Field(min_length=1, max_length=600)
    evidence_source_ids: list[str] = Field(default_factory=list, max_length=20)


class CareBriefingDocumentCandidate(CareBriefingStrictModel):
    document_type: Literal[
        "care_service_record",
        "nursing_log",
        "consultation_log",
        "physical_restraint_log",
        "program_log",
        "other",
    ]
    reason: str = Field(min_length=1, max_length=600)
    evidence_source_ids: list[str] = Field(min_length=1, max_length=20)


class CareBriefingV1Event(CareBriefingStrictModel):
    event_id: str = Field(min_length=1, max_length=160)
    subject: CareBriefingSubjectRef
    what_happened: str = Field(min_length=1, max_length=1200)
    final_status: CareBriefingFinalStatus
    final_status_summary: str = Field(min_length=1, max_length=1000)
    occurred_at: datetime | None
    scheduled_at: datetime | None
    priority: CareBriefingPriority
    importance_score: int = Field(ge=0, le=100)
    importance_reasons: list[CareBriefingImportanceReason] = Field(
        min_length=1,
        max_length=10,
    )
    verification_items: list[CareBriefingVerificationItem] = Field(
        default_factory=list,
        max_length=20,
    )
    evidence: list[CareBriefingEvidenceRef] = Field(min_length=1, max_length=100)
    document_draft_candidates: list[CareBriefingDocumentCandidate] = Field(
        default_factory=list,
        max_length=10,
    )
    analysis_confidence: float = Field(ge=0, le=1)
    validation_status: CareBriefingValidationStatus

    @model_validator(mode="after")
    def validate_event_grounding(self):
        if self.occurred_at is None and self.scheduled_at is None:
            raise ValueError("사건에는 발생시각 또는 예정시각이 필요합니다.")
        evidence_ids = [item.source_id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("한 사건에서 같은 근거를 중복 참조할 수 없습니다.")
        known_evidence_ids = set(evidence_ids)
        referenced_ids = {
            source_id
            for reason in self.importance_reasons
            for source_id in reason.evidence_source_ids
        }
        referenced_ids.update(
            source_id
            for item in self.verification_items
            for source_id in item.evidence_source_ids
        )
        referenced_ids.update(
            source_id
            for item in self.document_draft_candidates
            for source_id in item.evidence_source_ids
        )
        if referenced_ids - known_evidence_ids:
            raise ValueError("판단 사유가 사건 근거 목록에 없는 식별자를 참조합니다.")
        if self.final_status == "completed" and not any(
            "completion" in item.supports for item in self.evidence
        ):
            raise ValueError("완료 판정에는 완료를 직접 뒷받침하는 근거가 필요합니다.")
        if self.subject.verification_status == "needs_confirmation" and not any(
            item.field == "subject" for item in self.verification_items
        ):
            raise ValueError("대상자 미확정 사건에는 대상자 확인 질문이 필요합니다.")
        if self.verification_items and self.validation_status != "needs_staff_review":
            raise ValueError("확인할 내용이 있으면 직원 검토 필요 상태여야 합니다.")
        return self


class CareBriefingIgnoredSource(CareBriefingStrictModel):
    source_id: str = Field(min_length=1, max_length=160)
    reason: Literal[
        "routine_completed",
        "insufficient_context",
        "unrelated",
        "duplicate",
        "outside_scope",
    ]
    explanation: str = Field(min_length=1, max_length=600)


class CareBriefingV1Result(CareBriefingStrictModel):
    version: Literal["care_briefing_v1"] = CARE_BRIEFING_VERSION
    as_of: datetime
    events: list[CareBriefingV1Event] = Field(default_factory=list, max_length=200)
    ignored_sources: list[CareBriefingIgnoredSource] = Field(
        default_factory=list,
        max_length=500,
    )
    warnings: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_result_uniqueness(self):
        if not self.events and not self.ignored_sources:
            raise ValueError(
                "사건이 없으면 모든 입력 근거에 대한 제외 사유가 필요합니다."
            )
        event_ids = [event.event_id for event in self.events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("사건 식별자는 출력에서 고유해야 합니다.")
        ignored_ids = [source.source_id for source in self.ignored_sources]
        if len(ignored_ids) != len(set(ignored_ids)):
            raise ValueError("제외 근거 식별자는 출력에서 고유해야 합니다.")
        return self


class CareBriefingV1Failure(CareBriefingStrictModel):
    code: CareBriefingFailureCode
    message: str = Field(min_length=1, max_length=500)
    retryable: bool
    offline_fallback_available: bool


class CareBriefingV1Envelope(CareBriefingStrictModel):
    ok: bool
    result: CareBriefingV1Result | None = None
    failure: CareBriefingV1Failure | None = None
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    elapsed_ms: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_outcome(self):
        if self.ok and (self.result is None or self.failure is not None):
            raise ValueError("성공 응답에는 result만 있어야 합니다.")
        if not self.ok and (self.failure is None or self.result is not None):
            raise ValueError("실패 응답에는 failure만 있어야 합니다.")
        return self


def validate_care_briefing_result(
    input_data: CareBriefingV1Input,
    result: CareBriefingV1Result,
) -> CareBriefingV1Result:
    """Validate model-neutral source and subject references against one input."""

    if result.as_of != input_data.window.as_of:
        raise ValueError("출력 기준시각이 입력 기준시각과 다릅니다.")
    known_source_ids = {source.source_id for source in input_data.sources}
    known_subject_ids = {subject.subject_id for subject in input_data.subjects}
    used_source_ids = {
        evidence.source_id
        for event in result.events
        for evidence in event.evidence
    }
    ignored_source_ids = {source.source_id for source in result.ignored_sources}
    referenced_source_ids = used_source_ids | ignored_source_ids
    unknown_source_ids = referenced_source_ids - known_source_ids
    if unknown_source_ids:
        raise ValueError("출력이 입력에 없는 근거 식별자를 참조합니다.")
    if used_source_ids & ignored_source_ids:
        raise ValueError("같은 근거를 사건 근거와 제외 근거로 동시에 분류할 수 없습니다.")
    if referenced_source_ids != known_source_ids:
        raise ValueError("모든 입력 근거는 사건에 사용되거나 제외 사유가 기록되어야 합니다.")
    for event in result.events:
        if event.subject.subject_id and event.subject.subject_id not in known_subject_ids:
            raise ValueError("출력이 입력에 없는 대상자 식별자를 참조합니다.")
    return result
