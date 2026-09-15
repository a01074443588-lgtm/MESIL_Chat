from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .care_briefing_contract import (
    CareBriefingFailureCode,
    CareBriefingV1Envelope,
    CareBriefingV1Event,
)


class CareBriefingEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CareBriefingMeasuredScores(CareBriefingEvaluationModel):
    important_event_selection: float = Field(ge=0, le=25)
    no_unsupported_claims: float = Field(ge=0, le=20)
    reply_final_state: float = Field(ge=0, le=15)
    priority_judgement: float = Field(ge=0, le=10)
    evidence_linking: float = Field(ge=0, le=10)
    structured_output: float = Field(ge=0, le=8)
    privacy_policy: float = Field(ge=0, le=2)

    @property
    def total(self) -> float:
        return round(
            self.important_event_selection
            + self.no_unsupported_claims
            + self.reply_final_state
            + self.priority_judgement
            + self.evidence_linking
            + self.structured_output
            + self.privacy_policy,
            2,
        )


class CareBriefingCaseEvaluation(CareBriefingEvaluationModel):
    case_id: str
    provider: str
    model: str
    contract_ok: bool
    failure_code: CareBriefingFailureCode | None = None
    elapsed_ms: int = Field(ge=0)
    measured_score: float = Field(ge=0, le=90)
    measured_max: int = 90
    scores: CareBriefingMeasuredScores
    missing_requirements: list[str] = Field(default_factory=list)
    forbidden_claims_found: list[str] = Field(default_factory=list)
    critical_flaws: list[str] = Field(default_factory=list)
    unmeasured_dimensions: list[str] = Field(
        default_factory=lambda: ["처리시간 비교", "장애율 비교", "운영비용"]
    )


def _ratio_score(matched: int, required: int, maximum: float) -> float:
    if required <= 0:
        return maximum
    return round(maximum * min(matched, required) / required, 2)


def _event_evidence_ids(event: CareBriefingV1Event) -> set[str]:
    return {item.source_id for item in event.evidence}


def _match_expected_events(
    case_definition: dict[str, Any],
    actual_events: list[CareBriefingV1Event],
) -> dict[str, CareBriefingV1Event]:
    """Match by id first and then by evidence overlap, never model prose."""

    expected_events = case_definition.get("expected_result", {}).get("events", [])
    available = list(actual_events)
    matched: dict[str, CareBriefingV1Event] = {}
    for expected in expected_events:
        expected_id = str(expected.get("event_id", ""))
        direct = next(
            (event for event in available if event.event_id == expected_id),
            None,
        )
        if direct is not None:
            matched[expected_id] = direct
            available.remove(direct)
            continue

        expected_evidence = {
            str(item.get("source_id"))
            for item in expected.get("evidence", [])
            if item.get("source_id")
        }
        if not available:
            continue
        ranked = sorted(
            available,
            key=lambda event: len(
                expected_evidence.intersection(_event_evidence_ids(event))
            ),
            reverse=True,
        )
        best = ranked[0]
        if expected_evidence.intersection(_event_evidence_ids(best)):
            matched[expected_id] = best
            available.remove(best)
    return matched


def evaluate_care_briefing_case(
    case_definition: dict[str, Any],
    envelope: CareBriefingV1Envelope,
) -> CareBriefingCaseEvaluation:
    """Score only deterministic fixture requirements; leave human review explicit."""

    case_id = str(case_definition["case_id"])
    answer_key = case_definition.get("answer_key", {})
    required_event_count = int(answer_key.get("required_event_count", 0))
    input_policy = case_definition.get("input", {}).get("transmission_policy", {})
    privacy_ok = (
        input_policy.get("privacy_mode") == "external_deidentified"
        and input_policy.get("external_ai_allowed") is True
        and input_policy.get("contains_direct_identifiers") is False
    )

    if not envelope.ok or envelope.result is None:
        scores = CareBriefingMeasuredScores(
            important_event_selection=0,
            no_unsupported_claims=0,
            reply_final_state=0,
            priority_judgement=0,
            evidence_linking=0,
            structured_output=0,
            privacy_policy=2 if privacy_ok else 0,
        )
        failure_code = envelope.failure.code if envelope.failure else "internal_error"
        return CareBriefingCaseEvaluation(
            case_id=case_id,
            provider=envelope.provider,
            model=envelope.model,
            contract_ok=False,
            failure_code=failure_code,
            elapsed_ms=envelope.elapsed_ms,
            measured_score=scores.total,
            scores=scores,
            missing_requirements=["care_briefing_v1 계약 통과"],
            critical_flaws=[f"계약 실패: {failure_code}"],
        )

    result = envelope.result
    matched_events = _match_expected_events(case_definition, result.events)
    missing: list[str] = []

    if required_event_count == 0:
        event_selection = 20.0 if not result.events else 0.0
        if result.events:
            missing.append("완료된 일상 기록을 중요 사건에서 제외")
    else:
        event_selection = _ratio_score(
            len(matched_events), required_event_count, 20
        )
        if len(matched_events) < required_event_count:
            missing.append(
                f"필수 사건 {required_event_count - len(matched_events)}개"
            )

    required_document_types = set(answer_key.get("required_document_types", []))
    actual_document_types = {
        candidate.document_type
        for event in result.events
        for candidate in event.document_draft_candidates
    }
    matched_document_types = required_document_types.intersection(
        actual_document_types
    )
    document_score = _ratio_score(
        len(matched_document_types), len(required_document_types), 5
    )
    if required_document_types - actual_document_types:
        missing.append(
            "기록·서류 후보: "
            + ", ".join(sorted(required_document_types - actual_document_types))
        )

    required_statuses = answer_key.get("required_final_statuses", {})
    matched_statuses = sum(
        1
        for expected_id, expected_status in required_statuses.items()
        if expected_id in matched_events
        and matched_events[expected_id].final_status == expected_status
    )
    status_score = _ratio_score(matched_statuses, len(required_statuses), 10)
    if matched_statuses < len(required_statuses):
        missing.append("답글을 반영한 최종상태")

    required_verification_fields = set(
        answer_key.get("required_verification_fields", [])
    )
    actual_verification_fields = {
        item.field
        for event in result.events
        for item in event.verification_items
    }
    matched_verification_fields = required_verification_fields.intersection(
        actual_verification_fields
    )
    verification_score = _ratio_score(
        len(matched_verification_fields), len(required_verification_fields), 5
    )
    if required_verification_fields - actual_verification_fields:
        missing.append(
            "확인 필요 항목: "
            + ", ".join(
                sorted(required_verification_fields - actual_verification_fields)
            )
        )

    required_priorities = answer_key.get("required_priorities", {})
    matched_priorities = sum(
        1
        for expected_id, expected_priority in required_priorities.items()
        if expected_id in matched_events
        and matched_events[expected_id].priority == expected_priority
    )
    priority_score = _ratio_score(
        matched_priorities, len(required_priorities), 10
    )
    if matched_priorities < len(required_priorities):
        missing.append("중요도 등급")

    referenced_evidence = {
        source_id
        for event in result.events
        for source_id in _event_evidence_ids(event)
    }
    referenced_evidence.update(
        item.source_id for item in result.ignored_sources
    )
    required_evidence = set(answer_key.get("required_evidence_source_ids", []))
    required_ignored = set(answer_key.get("required_ignored_source_ids", []))
    evidence_requirements = required_evidence | required_ignored
    matched_evidence = evidence_requirements.intersection(referenced_evidence)
    evidence_score = _ratio_score(
        len(matched_evidence), len(evidence_requirements), 10
    )
    if evidence_requirements - referenced_evidence:
        missing.append(
            "근거 식별자: "
            + ", ".join(sorted(evidence_requirements - referenced_evidence))
        )

    result_text = json.dumps(
        result.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
    )
    forbidden_found = [
        str(claim)
        for claim in answer_key.get("forbidden_claims", [])
        if str(claim) in result_text
    ]
    unsupported_score = 20.0 if not forbidden_found else 0.0

    critical_flaws: list[str] = []
    if len(matched_events) < required_event_count:
        critical_flaws.append("중요 사건 누락")
    if forbidden_found:
        critical_flaws.append("금지된 추측 발견")

    scores = CareBriefingMeasuredScores(
        important_event_selection=round(event_selection + document_score, 2),
        no_unsupported_claims=unsupported_score,
        reply_final_state=round(status_score + verification_score, 2),
        priority_judgement=priority_score,
        evidence_linking=evidence_score,
        structured_output=8,
        privacy_policy=2 if privacy_ok else 0,
    )
    return CareBriefingCaseEvaluation(
        case_id=case_id,
        provider=envelope.provider,
        model=envelope.model,
        contract_ok=True,
        elapsed_ms=envelope.elapsed_ms,
        measured_score=scores.total,
        scores=scores,
        missing_requirements=missing,
        forbidden_claims_found=forbidden_found,
        critical_flaws=critical_flaws,
    )
