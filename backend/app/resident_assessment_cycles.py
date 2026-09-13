from __future__ import annotations

import json
from datetime import date
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


AssessmentReason = Literal[
    "new_admission",
    "periodic_reassessment",
    "state_change",
    "care_plan_change",
    "staff_review",
]
AssessmentDocumentType = Literal[
    "cognitive_function_assessment",
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "needs_assessment",
    "long_term_care_service_plan",
]
AssessmentOrganizationRole = Literal[
    "authoring_organization",
    "reference_organization",
]
AssessmentSourceProvider = Literal["staff_manual", "carefor_readonly"]
AssessmentOutputStatus = Literal["draft", "needs_confirmation"]
AssessmentChangeClassification = Literal[
    "unchanged",
    "changed",
    "newly_confirmed",
    "stale_current_observation_required",
    "material_conflict",
    "basis_version_mismatch",
    "expert_review_required",
    "care_plan_change_candidate",
]
AssessmentValueKind = Literal[
    "fact",
    "administrative",
    "assessment_score",
    "risk_grade",
    "diagnosis",
    "benefit_type",
    "service_frequency",
    "service_duration",
    "care_goal",
    "signature",
]
CurrentFactState = Literal[
    "confirmed",
    "current_observation_required",
    "material_conflict",
    "expert_review_required",
]


_RESTRICTED_VALUE_KINDS = {
    "assessment_score",
    "risk_grade",
    "diagnosis",
    "benefit_type",
    "service_frequency",
    "service_duration",
    "care_goal",
    "signature",
}


def _strip_unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value.strip() for value in values if value.strip()))


def _stable_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


class AssessmentBaselineFact(BaseModel):
    field_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$", max_length=80)
    label: str = Field(min_length=1, max_length=120)
    value_kind: AssessmentValueKind = "fact"
    value: str | int | float | bool | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    staff_confirmed: bool = False

    @field_validator("label")
    @classmethod
    def strip_label(cls, value: str) -> str:
        return value.strip()

    @field_validator("evidence_refs")
    @classmethod
    def strip_evidence_refs(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @model_validator(mode="after")
    def enforce_baseline_evidence(self):
        if self.staff_confirmed and (self.value in (None, "") or not self.evidence_refs):
            raise ValueError("직원이 확인한 기존 사실에는 값과 근거가 필요합니다.")
        if self.value_kind == "signature" and self.value not in (None, ""):
            raise ValueError("서명은 기존 기준자료에서도 자동 입력할 수 없습니다.")
        return self


class AssessmentBaselineSource(BaseModel):
    source_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{2,79}$", max_length=80)
    document_type: AssessmentDocumentType
    assessment_date: date
    period_start: date | None = None
    period_end: date | None = None
    source_label: str = Field(min_length=1, max_length=120)
    organization_role: AssessmentOrganizationRole
    original_verified_by_staff: bool = False
    current_reconfirmation_required: bool = True
    form_version: str | None = Field(default=None, max_length=80)
    facts: list[AssessmentBaselineFact] = Field(min_length=1, max_length=200)

    @field_validator("source_label", "form_version")
    @classmethod
    def strip_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_period_and_facts(self):
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("기존 자료 적용 기간의 시작일이 종료일보다 늦을 수 없습니다.")
        keys = [fact.field_key for fact in self.facts]
        if len(keys) != len(set(keys)):
            raise ValueError("한 기준자료 안에서 입력 항목 이름은 중복될 수 없습니다.")
        if not self.original_verified_by_staff and any(
            fact.staff_confirmed for fact in self.facts
        ):
            raise ValueError("원본을 확인하지 않은 자료의 항목을 직원 확인 사실로 표시할 수 없습니다.")
        return self


class AssessmentCurrentFact(BaseModel):
    field_key: str = Field(pattern=r"^[a-z][a-z0-9_]{2,79}$", max_length=80)
    label: str = Field(min_length=1, max_length=120)
    value_kind: AssessmentValueKind = "fact"
    value: str | int | float | bool | None = None
    state: CurrentFactState
    source_type: Literal["mesil_chat_confirmed", "staff_confirmation", "current_observation"]
    observed_at: date | None = None
    evidence_refs: list[str] = Field(default_factory=list, max_length=30)
    conflict_values: list[str] = Field(default_factory=list, max_length=10)
    basis_version: str | None = Field(default=None, max_length=80)
    plan_impact: str | None = Field(default=None, max_length=500)
    staff_confirmed: bool = False

    @field_validator("label", "basis_version", "plan_impact")
    @classmethod
    def strip_current_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("evidence_refs", "conflict_values")
    @classmethod
    def strip_current_lists(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @model_validator(mode="after")
    def enforce_current_fact_safety(self):
        has_value = self.value not in (None, "")
        if self.state == "confirmed":
            if not has_value or not self.evidence_refs or not self.staff_confirmed:
                raise ValueError("현재 확인 사실에는 직원 확인 값과 근거가 필요합니다.")
        elif has_value:
            raise ValueError("확인 전·충돌·전문가 확인 항목은 값을 자동 선택할 수 없습니다.")
        if self.state == "material_conflict" and (
            len(self.conflict_values) < 2 or len(self.evidence_refs) < 2
        ):
            raise ValueError("자료 충돌에는 둘 이상의 값과 근거가 필요합니다.")
        if self.state != "material_conflict" and self.conflict_values:
            raise ValueError("충돌 값은 자료 충돌 상태에서만 사용할 수 있습니다.")
        if self.value_kind == "signature" and has_value:
            raise ValueError("서명은 자동 입력할 수 없습니다.")
        if self.value_kind in _RESTRICTED_VALUE_KINDS and has_value:
            if self.source_type != "staff_confirmation" or not self.staff_confirmed:
                raise ValueError("제한 항목은 직원이 확인한 입력만 사용할 수 있습니다.")
        return self


class ResidentAssessmentCyclePayload(BaseModel):
    schema_version: Literal["resident_assessment_cycle_v1"] = "resident_assessment_cycle_v1"
    revision: int = Field(ge=1)
    supersedes_revision: int | None = Field(default=None, ge=1)
    reason: AssessmentReason
    assessment_date: date
    period_start: date | None = None
    period_end: date | None = None
    source_provider: AssessmentSourceProvider = "staff_manual"
    previous_cycle_id: UUID | None = None
    output_status: AssessmentOutputStatus = "needs_confirmation"
    external_transfer_allowed: Literal[False] = False
    baseline_sources: list[AssessmentBaselineSource] = Field(default_factory=list, max_length=50)
    current_facts: list[AssessmentCurrentFact] = Field(default_factory=list, max_length=500)
    selected_plan_candidate_keys: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("selected_plan_candidate_keys")
    @classmethod
    def strip_selected_keys(cls, values: list[str]) -> list[str]:
        return _strip_unique(values)

    @model_validator(mode="after")
    def enforce_cycle_contract(self):
        if self.supersedes_revision is not None and self.supersedes_revision >= self.revision:
            raise ValueError("이전 수정본 번호는 새 수정본보다 작아야 합니다.")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("평가 기간의 시작일이 종료일보다 늦을 수 없습니다.")
        refs = [source.source_ref for source in self.baseline_sources]
        if len(refs) != len(set(refs)):
            raise ValueError("기존 기준자료 참조값은 중복될 수 없습니다.")
        return self


def build_assessment_cycle_comparison(
    payload: ResidentAssessmentCyclePayload,
) -> dict[str, Any]:
    baseline_by_key: dict[str, list[tuple[AssessmentBaselineSource, AssessmentBaselineFact]]] = {}
    for source in payload.baseline_sources:
        for fact in source.facts:
            baseline_by_key.setdefault(fact.field_key, []).append((source, fact))

    current_by_key: dict[str, list[AssessmentCurrentFact]] = {}
    for fact in payload.current_facts:
        current_by_key.setdefault(fact.field_key, []).append(fact)

    comparisons: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    plan_candidate_keys: set[str] = set()
    all_keys = sorted(set(baseline_by_key) | set(current_by_key))

    for field_key in all_keys:
        baseline_rows = baseline_by_key.get(field_key, [])
        current_rows = current_by_key.get(field_key, [])
        label = (
            current_rows[0].label
            if current_rows
            else baseline_rows[0][1].label
        )
        previous_values = list(
            dict.fromkeys(
                _stable_value(fact.value)
                for source, fact in baseline_rows
                if source.original_verified_by_staff and fact.staff_confirmed
            )
        )
        previous_value = (
            baseline_rows[0][1].value
            if len(previous_values) == 1 and baseline_rows
            else None
        )
        current_confirmed = [
            fact for fact in current_rows if fact.state == "confirmed" and fact.staff_confirmed
        ]
        current_values = list(dict.fromkeys(_stable_value(fact.value) for fact in current_confirmed))
        current_value = current_confirmed[0].value if len(current_values) == 1 else None
        evidence_refs = _strip_unique(
            [
                *[
                    ref
                    for _source, fact in baseline_rows
                    for ref in fact.evidence_refs
                ],
                *[ref for fact in current_rows for ref in fact.evidence_refs],
            ]
        )
        baseline_versions = {
            source.form_version for source, _fact in baseline_rows if source.form_version
        }
        current_versions = {fact.basis_version for fact in current_rows if fact.basis_version}
        conflict_values = _strip_unique(
            [
                *[value for fact in current_rows for value in fact.conflict_values],
                *([str(row[1].value) for row in baseline_rows] if len(previous_values) > 1 else []),
            ]
        )

        if any(fact.state == "material_conflict" for fact in current_rows) or len(current_values) > 1 or len(previous_values) > 1:
            classification: AssessmentChangeClassification = "material_conflict"
            current_value = None
            reason = "자료끼리 값이 달라 어느 값도 자동 선택하지 않습니다."
        elif baseline_versions and current_versions and baseline_versions != current_versions:
            classification = "basis_version_mismatch"
            current_value = None
            reason = "서식 또는 평가기준 버전이 달라 점수·판정을 직접 비교하지 않습니다."
        elif any(fact.state == "expert_review_required" for fact in current_rows):
            classification = "expert_review_required"
            current_value = None
            reason = "전문가 확인 전 값을 확정하지 않습니다."
        elif not current_rows or any(
            fact.state == "current_observation_required" for fact in current_rows
        ):
            classification = "stale_current_observation_required"
            current_value = None
            reason = "과거 기준자료만으로 현재 상태를 확정하지 않고 현재 관찰을 요청합니다."
        elif not baseline_rows and current_confirmed:
            classification = "newly_confirmed"
            reason = "이전 기준자료에는 없고 현재 직원이 새로 확인한 사실입니다."
        elif previous_value == current_value:
            classification = "unchanged"
            reason = "직원이 확인한 이전 값과 현재 값이 같습니다."
        elif any(fact.plan_impact for fact in current_confirmed):
            classification = "care_plan_change_candidate"
            reason = "확인된 변화가 있어 급여제공계획 반영 여부를 직원이 검토합니다."
            plan_candidate_keys.add(field_key)
        else:
            classification = "changed"
            reason = "직원이 확인한 현재 값이 이전 값과 달라졌습니다."

        item = {
            "field_key": field_key,
            "label": label,
            "classification": classification,
            "previous_value": previous_value,
            "current_value": current_value,
            "conflict_values": conflict_values,
            "evidence_refs": evidence_refs,
            "reason": reason,
            "plan_impact": next(
                (fact.plan_impact for fact in current_confirmed if fact.plan_impact),
                None,
            ),
            "staff_selection_allowed": classification == "care_plan_change_candidate",
            "selected_for_plan_review": False,
        }
        comparisons.append(item)

        if classification in {
            "stale_current_observation_required",
            "material_conflict",
            "basis_version_mismatch",
            "expert_review_required",
            "care_plan_change_candidate",
        }:
            questions.append(
                {
                    "field_key": field_key,
                    "label": label,
                    "classification": classification,
                    "prompt": (
                        "급여제공계획 변경 검토에 반영할까요?"
                        if classification == "care_plan_change_candidate"
                        else reason
                    ),
                    "evidence_refs": evidence_refs,
                }
            )

    selected = set(payload.selected_plan_candidate_keys)
    unknown_selected = selected - plan_candidate_keys
    if unknown_selected:
        raise ValueError("직원이 확인한 급여제공계획 변경 후보만 선택할 수 있습니다.")
    for item in comparisons:
        item["selected_for_plan_review"] = item["field_key"] in selected

    return {
        "comparison_items": comparisons,
        "confirmation_questions": questions,
        "selected_plan_candidate_keys": sorted(selected),
        "duplicate_question_count": len(questions)
        - len({question["field_key"] for question in questions}),
        "external_transfer_allowed": False,
        "official_record_write_count": 0,
        "signature_confirmation_count": 0,
        "public_insurer_transfer_count": 0,
        "auto_generated_restricted_value_count": 0,
    }
