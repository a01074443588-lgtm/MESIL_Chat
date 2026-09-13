from __future__ import annotations

from typing import Any

from .new_admission_assessment_flow import build_new_admission_assessment_workflow
from .new_admission_care_plan import build_new_admission_care_plan_preview
from .new_admission_contract import NewAdmissionDraftBundle
from .new_admission_form_workspace import build_new_admission_form_workspace


_REUSABLE_STATUS_LABELS = {
    "confirmed": "자료에서 확인된 사실",
    "reusable": "그대로 재사용 가능한 사실",
    "derivable": "자료에서 안전하게 도출 가능한 내용",
}

_QUESTION_STATUS_LABELS = {
    "material_conflict": "자료끼리 충돌",
    "current_observation_required": "현재 관찰 필요",
    "user_decision_required": "사용자 결정 필요",
    "admin_missing": "행정정보 미확인",
}


def _source_usage(source) -> tuple[str, str]:
    if source.organization_role == "authoring_organization":
        if source.verification_state == "verified":
            return "authoring_direct_verified", "작성기관 직접 확인"
        return "authoring_needs_review", "작성기관 자료·확인 필요"
    if source.organization_role == "reference_organization":
        return "reference_only", "다른 기관 자료·참고 전용"
    if source.organization_role == "public_insurer":
        return "public_insurer_reference", "공단자료·참고"
    if source.source_type == "consultation":
        return "consultation_evidence", "상담 근거"
    return "contextual_evidence", "기타 확인 근거"


def _effective_question_status(field) -> str:
    """Repair the display classification of drafts saved before the boundary fix."""

    if field.status != "admin_missing":
        return field.status
    if field.value_kind == "administrative":
        return "admin_missing"
    if field.value_kind == "signature":
        return "user_decision_required"
    return "current_observation_required"


def _question_prompt(field, question_status: str) -> tuple[str, str]:
    if question_status == "material_conflict":
        return (
            f"{field.label} 자료가 서로 다릅니다. 원문과 현재 사실을 확인해 주세요.",
            "충돌한 값 중 어느 것도 자동 선택하지 않습니다.",
        )
    if question_status == "current_observation_required":
        return (
            f"현재 상태를 관찰해 확인해 주세요: {field.label}",
            "과거 자료만으로 현재 상태를 확정하지 않습니다.",
        )
    if question_status == "admin_missing":
        return (
            f"행정정보를 확인해 입력해 주세요: {field.label}",
            "자료에 없는 행정정보를 임의로 채우지 않습니다.",
        )
    if field.value_kind == "signature":
        return (
            f"최종 검토자가 직접 결정해 주세요: {field.label}",
            "직원 서명은 자동 확정하지 않습니다.",
        )
    return (
        f"직원이 결정해 주세요: {field.label}",
        "자료만으로 선택할 수 없어 사용자 결정을 기다립니다.",
    )


def build_new_admission_question_flow(
    bundle_payload: dict[str, Any],
    field_confirmations: dict[str, dict[str, Any]] | None = None,
    revision_history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a read-only question flow from one immutable draft revision."""

    bundle = NewAdmissionDraftBundle.model_validate(bundle_payload)
    confirmations = field_confirmations or {}
    source_by_ref = {source.source_ref: source for source in bundle.evidence_sources}
    sources: list[dict[str, Any]] = []
    source_usage_by_ref: dict[str, tuple[str, str]] = {}
    source_counts: dict[str, int] = {}

    for source in bundle.evidence_sources:
        usage, usage_label = _source_usage(source)
        source_usage_by_ref[source.source_ref] = (usage, usage_label)
        source_counts[usage] = source_counts.get(usage, 0) + 1
        sources.append(
            {
                "source_ref": source.source_ref,
                "source_type": source.source_type,
                "document_kind": source.document_kind,
                "organization_role": source.organization_role,
                "verification_state": source.verification_state,
                "usage": usage,
                "usage_label": usage_label,
            }
        )

    reusable_facts: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    seen_question_keys: set[str] = set()
    duplicate_question_count = 0

    for field in bundle.fields:
        usages = [
            source_usage_by_ref[reference][0]
            for reference in field.evidence_refs
            if reference in source_by_ref
        ]
        usage_labels = [
            source_usage_by_ref[reference][1]
            for reference in field.evidence_refs
            if reference in source_by_ref
        ]
        if field.status in _REUSABLE_STATUS_LABELS:
            reusable_facts.append(
                {
                    "field_key": field.field_key,
                    "label": field.label,
                    "status": field.status,
                    "status_label": _REUSABLE_STATUS_LABELS[field.status],
                    "value_kind": field.value_kind,
                    "value": field.value,
                    "evidence_refs": list(field.evidence_refs),
                    "source_usages": list(dict.fromkeys(usages)),
                    "source_usage_labels": list(dict.fromkeys(usage_labels)),
                    "document_types": list(field.document_types),
                    "derivation_note": field.derivation_note,
                    "staff_confirmation_state": confirmations.get(
                        field.field_key, {}
                    ).get("state", "needs_confirmation"),
                }
            )
            continue

        question_status = _effective_question_status(field)
        if question_status not in _QUESTION_STATUS_LABELS:
            continue
        if field.field_key in seen_question_keys:
            duplicate_question_count += 1
            continue
        seen_question_keys.add(field.field_key)
        prompt, reason = _question_prompt(field, question_status)
        questions.append(
            {
                "question_key": field.field_key,
                "field_key": field.field_key,
                "label": field.label,
                "question_type": question_status,
                "question_type_label": _QUESTION_STATUS_LABELS[question_status],
                "prompt": prompt,
                "reason": reason,
                "value_kind": field.value_kind,
                "evidence_refs": list(field.evidence_refs),
                "source_usages": list(dict.fromkeys(usages)),
                "source_usage_labels": list(dict.fromkeys(usage_labels)),
                "document_types": list(field.document_types),
                "conflict_values": list(field.conflict_values),
                "selected_value": None,
            }
        )

    question_count_by_type = {
        question_type: sum(
            question["question_type"] == question_type for question in questions
        )
        for question_type in _QUESTION_STATUS_LABELS
    }

    assessment_workflow = build_new_admission_assessment_workflow(
        bundle_payload,
        reusable_facts=reusable_facts,
        questions=questions,
    )
    form_workspace = build_new_admission_form_workspace(
        bundle_payload,
        field_confirmations=confirmations,
        revision_history=revision_history,
    )
    care_plan_preview = None
    if form_workspace["care_plan_gate"]["available"]:
        care_plan_preview = build_new_admission_care_plan_preview(
            bundle_payload,
            field_confirmations=confirmations,
            revision_history=revision_history,
            assessment_workflow=assessment_workflow,
        )

    return {
        "case_ref": bundle.case_ref,
        "revision": bundle.revision,
        "output_status": (
            "needs_confirmation" if questions else "draft"
        ),
        "processing_scope": bundle.processing_scope,
        "external_transfer_allowed": False,
        "sources": sources,
        "source_counts": source_counts,
        "reusable_facts": reusable_facts,
        "questions": questions,
        "reusable_fact_count": len(reusable_facts),
        "question_count": len(questions),
        "question_count_by_type": question_count_by_type,
        "duplicate_question_count": duplicate_question_count,
        "assessment_workflow": assessment_workflow,
        "care_plan_preview": care_plan_preview,
        "form_workspace": form_workspace,
        "safety_notices": [
            "이미 확인된 동일 항목은 다시 묻지 않습니다.",
            "충돌 자료와 현재 관찰 항목은 자동 확정하지 않습니다.",
            "낙상·욕창·인지 점수와 진단을 임의 생성하지 않습니다.",
            "공식 기록 자동저장·공단 전송·서명 자동확정은 하지 않습니다.",
        ],
    }
