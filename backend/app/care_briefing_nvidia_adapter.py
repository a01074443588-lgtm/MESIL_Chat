from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .care_briefing_contract import (
    CARE_BRIEFING_VERSION,
    CareBriefingDocumentCandidate,
    CareBriefingEvidenceRef,
    CareBriefingImportanceReason,
    CareBriefingIgnoredSource,
    CareBriefingV1Event,
    CareBriefingV1Result,
    CareBriefingSubjectRef,
    CareBriefingVerificationItem,
    CareBriefingV1Input,
    validate_care_briefing_result,
)


FinalStatus = Literal["needs_confirmation", "in_progress", "completed", "monitoring"]
Priority = Literal["first", "check", "observe"]
VerificationField = Literal[
    "subject",
    "facts",
    "time",
    "assignee",
    "completion",
    "follow_up",
    "other",
]
DocumentType = Literal[
    "care_service_record",
    "nursing_log",
    "consultation_log",
    "physical_restraint_log",
    "program_log",
    "other",
]


class NvidiaCompactEvent(BaseModel):
    """NVIDIA가 생성하는 최소 사건 판단 형식.

    근거 문장과 중첩 출력은 MESIL_Chat이 원문에서 재구성한다. 모델은 사건 판단과
    근거 식별자만 반환하므로, 긴 JSON이 잘리는 위험과 원문을 재서술하며 생기는
    환각을 함께 줄인다.
    """

    model_config = ConfigDict(extra="forbid")

    source_ids: list[str] = Field(min_length=1, max_length=40)
    subject_id: str | None = None
    what_happened: str = Field(min_length=1, max_length=220)
    final_status: FinalStatus
    final_status_summary: str = Field(min_length=1, max_length=180)
    occurred_at: str | None = None
    scheduled_at: str | None = None
    priority: Priority
    importance_score: int = Field(ge=0, le=100)
    importance_reason: str = Field(min_length=1, max_length=180)
    status_source_id: str | None = None
    completion_source_id: str | None = None
    verification_field: VerificationField | None = None
    verification_question: str | None = Field(default=None, max_length=180)
    document_type: DocumentType | None = None
    document_reason: str | None = Field(default=None, max_length=180)
    analysis_confidence: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def validate_pairs_and_time(self) -> "NvidiaCompactEvent":
        if self.occurred_at is None and self.scheduled_at is None:
            raise ValueError("occurred_at 또는 scheduled_at 중 하나는 필요합니다.")
        if self.verification_field is None and self.verification_question is not None:
            self.verification_field = "other"
        elif self.verification_field is not None and self.verification_question is None:
            self.verification_question = "해당 내용을 원문에서 확인해 주세요."
        if self.document_type is None and self.document_reason is not None:
            self.document_type = "other"
        elif self.document_type is not None and self.document_reason is None:
            self.document_reason = (
                "원문 근거를 확인하여 기록·서류 초안 후보로 검토해 주세요."
            )
        return self


class NvidiaCompactIgnoredSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str
    reason: Literal[
        "routine_completed",
        "insufficient_context",
        "unrelated",
        "duplicate",
        "outside_scope",
    ]
    explanation: str = Field(min_length=1, max_length=140)


class NvidiaCompactResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    events: list[NvidiaCompactEvent] = Field(default_factory=list, max_length=50)
    ignored_sources: list[NvidiaCompactIgnoredSource] = Field(default_factory=list, max_length=200)
    warnings: list[str] = Field(default_factory=list, max_length=5)


def nvidia_compact_generation_schema(input_data: CareBriefingV1Input) -> dict:
    """입력 크기에 맞춰 NVIDIA guided JSON의 최대 배열 길이를 제한한다."""

    schema = deepcopy(NvidiaCompactResult.model_json_schema())
    source_count = max(1, len(input_data.sources))
    schema["properties"]["events"]["maxItems"] = min(50, max(2, source_count * 2))
    schema["properties"]["ignored_sources"]["maxItems"] = source_count
    schema["properties"]["warnings"]["maxItems"] = min(5, source_count)
    schema["$defs"]["NvidiaCompactEvent"]["properties"]["source_ids"]["maxItems"] = source_count
    # Guided JSON must require the complete transport envelope. Pydantic's
    # default_factory keeps these fields optional in its generated schema,
    # which allowed GPT-OSS-20B to omit `warnings` while still satisfying the
    # provider-side schema.
    schema["required"] = ["events", "ignored_sources", "warnings"]
    return schema


def _event_id(source_ids: list[str]) -> str:
    joined = "\n".join(sorted(source_ids)).encode("utf-8")
    return f"evt-{hashlib.sha256(joined).hexdigest()[:16]}"


def _fact_summary(source) -> str:
    text = " ".join((source.text or "").split())
    if text:
        return text[:240]
    if source.source_kind == "photo":
        return "사진 근거가 첨부되어 있습니다."
    if source.source_kind == "file_text":
        return "파일 근거가 첨부되어 있습니다."
    if source.source_kind == "audio_transcript":
        return "음성 근거가 첨부되어 있습니다."
    return "원문 근거를 확인해야 합니다."


def adapt_nvidia_compact_result(
    compact: NvidiaCompactResult,
    input_data: CareBriefingV1Input,
) -> CareBriefingV1Result:
    """짧은 NVIDIA 출력을 근거가 연결된 care_briefing_v1 결과로 변환한다."""

    source_by_id = {source.source_id: source for source in input_data.sources}
    subject_by_id = {subject.subject_id: subject for subject in input_data.subjects}
    used_source_ids: set[str] = set()
    events: list[CareBriefingV1Event] = []

    for compact_event in compact.events:
        if len(set(compact_event.source_ids)) != len(compact_event.source_ids):
            raise ValueError("사건의 source_ids에 중복이 있습니다.")
        unknown_source_ids = sorted(set(compact_event.source_ids) - source_by_id.keys())
        if unknown_source_ids:
            raise ValueError("입력에 없는 근거 식별자가 포함되어 있습니다.")
        overlapping = used_source_ids.intersection(compact_event.source_ids)
        if overlapping:
            raise ValueError("하나의 원문 근거가 여러 사건에 중복 사용되었습니다.")
        if compact_event.status_source_id not in {None, *compact_event.source_ids}:
            raise ValueError("최종상태 근거는 사건 근거에 포함되어야 합니다.")
        if compact_event.completion_source_id not in {None, *compact_event.source_ids}:
            raise ValueError("완료 근거는 사건 근거에 포함되어야 합니다.")
        if compact_event.final_status == "completed" and compact_event.completion_source_id is None:
            raise ValueError("완료 상태에는 명시적인 완료 근거가 필요합니다.")

        if compact_event.subject_id is None:
            subject_ref = CareBriefingSubjectRef(
                subject_id=None,
                display_label="확인 필요",
                verification_status="needs_confirmation",
            )
        else:
            subject_input = subject_by_id.get(compact_event.subject_id)
            if subject_input is None:
                raise ValueError("입력에 없는 대상자 식별자가 포함되어 있습니다.")
            subject_ref = CareBriefingSubjectRef(
                subject_id=subject_input.subject_id,
                display_label=subject_input.display_label,
                verification_status=subject_input.verification_status,
            )

        verification_items: list[CareBriefingVerificationItem] = []
        if compact_event.verification_field and compact_event.verification_question:
            verification_items.append(
                CareBriefingVerificationItem(
                    field=compact_event.verification_field,
                    question=compact_event.verification_question,
                    evidence_source_ids=compact_event.source_ids,
                )
            )
        if subject_ref.subject_id is None and not any(
            item.field == "subject" for item in verification_items
        ):
            verification_items.append(
                CareBriefingVerificationItem(
                    field="subject",
                    question="대상자가 누구인지 원문에서 확인해 주세요.",
                    evidence_source_ids=compact_event.source_ids,
                )
            )

        evidence: list[CareBriefingEvidenceRef] = []
        for source_id in compact_event.source_ids:
            source = source_by_id[source_id]
            supports = ["event", "time", "importance"]
            if compact_event.subject_id and compact_event.subject_id in source.subject_ids:
                supports.append("subject")
            if source_id == compact_event.status_source_id:
                supports.append("status")
            if source_id == compact_event.completion_source_id:
                supports.append("completion")
            evidence.append(
                CareBriefingEvidenceRef(
                    source_id=source_id,
                    supports=supports,
                    fact_summary=_fact_summary(source),
                )
            )

        draft_candidates: list[CareBriefingDocumentCandidate] = []
        if compact_event.document_type and compact_event.document_reason:
            draft_candidates.append(
                CareBriefingDocumentCandidate(
                    document_type=compact_event.document_type,
                    reason=compact_event.document_reason,
                    evidence_source_ids=compact_event.source_ids,
                )
            )

        events.append(
            CareBriefingV1Event(
                event_id=_event_id(compact_event.source_ids),
                subject=subject_ref,
                what_happened=compact_event.what_happened,
                final_status=compact_event.final_status,
                final_status_summary=compact_event.final_status_summary,
                occurred_at=compact_event.occurred_at,
                scheduled_at=compact_event.scheduled_at,
                priority=compact_event.priority,
                importance_score=compact_event.importance_score,
                importance_reasons=[
                    CareBriefingImportanceReason(
                        reason=compact_event.importance_reason,
                        evidence_source_ids=compact_event.source_ids,
                    )
                ],
                verification_items=verification_items,
                evidence=evidence,
                document_draft_candidates=draft_candidates,
                analysis_confidence=compact_event.analysis_confidence,
                validation_status=(
                    "needs_staff_review" if verification_items else "source_linked"
                ),
            )
        )
        used_source_ids.update(compact_event.source_ids)

    ignored_source_ids: set[str] = set()
    ignored_sources: list[CareBriefingIgnoredSource] = []
    for ignored in compact.ignored_sources:
        if ignored.source_id not in source_by_id:
            raise ValueError("입력에 없는 제외 근거 식별자가 포함되어 있습니다.")
        if ignored.source_id in used_source_ids or ignored.source_id in ignored_source_ids:
            raise ValueError("근거 식별자가 사용·제외 목록에 중복되었습니다.")
        ignored_sources.append(
            CareBriefingIgnoredSource.model_validate(ignored.model_dump())
        )
        ignored_source_ids.add(ignored.source_id)

    uncovered = source_by_id.keys() - used_source_ids - ignored_source_ids
    if uncovered:
        raise ValueError("모델 출력에서 사용 또는 제외되지 않은 원문 근거가 있습니다.")

    result = CareBriefingV1Result(
        version=CARE_BRIEFING_VERSION,
        as_of=input_data.window.as_of,
        events=events,
        ignored_sources=ignored_sources,
        warnings=compact.warnings,
    )
    validate_care_briefing_result(input_data, result)
    return result
