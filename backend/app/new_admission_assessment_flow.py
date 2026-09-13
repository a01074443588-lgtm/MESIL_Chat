from __future__ import annotations

from typing import Any

from .new_admission_contract import NewAdmissionDraftBundle


ASSESSMENT_WORKFLOW_ORDER = (
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "cognitive_function_assessment",
    "needs_assessment",
)

_STEP_POLICIES: dict[str, dict[str, str]] = {
    "fall_risk_assessment": {
        "title": "낙상위험도",
        "local_form_alias": "FORM-06",
        "official_basis_state": "official_reference_found_latest_adoption_required",
        "official_basis_label": "공식기관 참고 원문 확인·최신 적용 기준 확인 필요",
        "official_basis_notice": (
            "국민건강보험공단 발간자료에서 Huhn 도구 구조는 확인했지만, "
            "현재 제품의 최신 점수 기준으로 채택하기 전 별도 확인이 필요합니다."
        ),
    },
    "pressure_ulcer_risk_assessment": {
        "title": "욕창위험도",
        "local_form_alias": "FORM-05",
        "official_basis_state": "latest_official_source_required",
        "official_basis_label": "최신 공식 기준 확인 필요",
        "official_basis_notice": (
            "로컬 서식의 응전력 표기와 기존 제품 구조표의 전단력 표기가 달라 "
            "최신 공식 원문 대조 전 점수와 위험등급을 만들지 않습니다."
        ),
    },
    "cognitive_function_assessment": {
        "title": "인지기능검사",
        "local_form_alias": "FORM-04",
        "official_basis_state": "latest_official_source_required",
        "official_basis_label": "최신 공식 기준 확인 필요",
        "official_basis_notice": (
            "적법하게 실시된 기존 검사결과만 그대로 재사용하며, 검사 문항·점수·"
            "판정·진단은 다른 자료에서 추정하지 않습니다."
        ),
    },
    "needs_assessment": {
        "title": "욕구사정",
        "local_form_alias": "FORM-03",
        "official_basis_state": "latest_official_source_required",
        "official_basis_label": "최신 공식 기준 확인 필요",
        "official_basis_notice": (
            "자녀 선택 범위와 중풍 분류 등 불일치 후보가 있어 최신 공식 원문과 "
            "원본 서식 확인 전 선택지와 판정을 자동 생성하지 않습니다."
        ),
    },
}


def build_new_admission_assessment_workflow(
    bundle_payload: dict[str, Any],
    *,
    reusable_facts: list[dict[str, Any]],
    questions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a read-only four-step assessment flow without scoring anything."""

    bundle = NewAdmissionDraftBundle.model_validate(bundle_payload)
    question_step_by_key: dict[str, str] = {}
    final_human_confirmation_questions: list[dict[str, Any]] = []
    deferred_care_plan_questions: list[dict[str, Any]] = []

    for question in questions:
        if question["value_kind"] == "signature":
            final_human_confirmation_questions.append(question)
            continue
        matching_steps = [
            document_type
            for document_type in ASSESSMENT_WORKFLOW_ORDER
            if document_type in question["document_types"]
        ]
        if not matching_steps:
            deferred_care_plan_questions.append(question)
            continue
        question_step_by_key[question["question_key"]] = matching_steps[0]

    steps: list[dict[str, Any]] = []
    for index, document_type in enumerate(ASSESSMENT_WORKFLOW_ORDER, start=1):
        policy = _STEP_POLICIES[document_type]
        step_facts = [
            fact for fact in reusable_facts if document_type in fact["document_types"]
        ]
        step_questions = [
            question
            for question in questions
            if question_step_by_key.get(question["question_key"]) == document_type
        ]
        carried_pending = [
            question
            for question in questions
            if document_type in question["document_types"]
            and question["question_key"] in question_step_by_key
            and question_step_by_key[question["question_key"]] != document_type
        ]
        steps.append(
            {
                "step_number": index,
                "step_count": len(ASSESSMENT_WORKFLOW_ORDER),
                "document_type": document_type,
                **policy,
                "output_status": "needs_confirmation",
                "reusable_facts": step_facts,
                "new_questions": step_questions,
                "carried_pending_questions": carried_pending,
                "reusable_fact_count": len(step_facts),
                "new_question_count": len(step_questions),
                "carried_pending_count": len(carried_pending),
                "score_generation_allowed": False,
                "score": None,
                "risk_grade": None,
                "diagnosis": None,
                "blocked_result_notice": (
                    "직원이 근거와 현재 관찰을 확인하고 최신 공식 기준을 채택하기 전에는 "
                    "점수·위험등급·진단을 확정하지 않습니다."
                ),
            }
        )

    assigned_question_keys = list(question_step_by_key)
    return {
        "schema_version": "new_admission_assessment_flow_v1",
        "case_ref": bundle.case_ref,
        "revision": bundle.revision,
        "output_status": "needs_confirmation",
        "allowed_output_statuses": ["draft", "needs_confirmation"],
        "processing_scope": bundle.processing_scope,
        "external_transfer_allowed": False,
        "workflow_notice": (
            "낙상위험도 → 욕창위험도 → 인지기능검사 → 욕구사정 순서는 "
            "업무 누락과 중복 질문을 줄이기 위한 제품 흐름이며 법적 의무 순서를 "
            "뜻하지 않습니다."
        ),
        "product_workflow_only": True,
        "legal_requirement_order_claimed": False,
        "steps": steps,
        "assigned_question_count": len(assigned_question_keys),
        "assigned_question_keys": assigned_question_keys,
        "duplicate_assigned_question_count": (
            len(assigned_question_keys) - len(set(assigned_question_keys))
        ),
        "final_human_confirmation_questions": final_human_confirmation_questions,
        "deferred_care_plan_questions": deferred_care_plan_questions,
        "score_generation_attempt_count": 0,
        "risk_grade_generation_attempt_count": 0,
        "diagnosis_generation_attempt_count": 0,
        "official_record_write_count": 0,
        "signature_confirmation_count": 0,
        "public_insurer_transfer_count": 0,
    }
