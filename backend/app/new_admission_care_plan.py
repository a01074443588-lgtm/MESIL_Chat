from __future__ import annotations

from typing import Any

from .new_admission_contract import NewAdmissionDraftBundle


_ASSESSMENT_DOCUMENT_TYPES = {
    "fall_risk_assessment",
    "pressure_ulcer_risk_assessment",
    "cognitive_function_assessment",
    "needs_assessment",
}
_RESOLVED_STATUSES = {"confirmed", "reusable", "derivable"}
_EXPERT_VALUE_KINDS = {"assessment_score", "diagnosis"}
_SOURCE_USAGE_LABELS = {
    "public_insurer": "공단자료·참고",
    "authoring_organization": "작성기관 자료",
    "reference_organization": "다른 기관 자료·참고",
    "not_applicable": "상담·기타 확인 근거",
}
_PENDING_CATEGORY_LABELS = {
    "staff_confirmation": "직원 확인 필요",
    "material_conflict": "자료 충돌",
    "current_observation_required": "현재 관찰 필요",
    "user_decision_required": "직원 결정 필요",
    "admin_missing": "행정정보 미확인",
    "expert_confirmation": "전문가 확인 필요",
    "official_basis": "최신 공식 기준 확인 필요",
}


def _source_summary(source) -> dict[str, Any]:
    label = _SOURCE_USAGE_LABELS[source.organization_role]
    if (
        source.organization_role == "authoring_organization"
        and source.verification_state == "verified"
    ):
        label = "작성기관 직접 확인 자료"
    elif source.organization_role == "authoring_organization":
        label = "작성기관 자료·현재 확인 필요"
    return {
        "source_ref": source.source_ref,
        "document_kind": source.document_kind,
        "source_type": source.source_type,
        "organization_role": source.organization_role,
        "verification_state": source.verification_state,
        "usage_label": label,
    }


def _revision_links(
    *,
    field_key: str,
    bundle_revision: int,
    value: Any,
    status: str,
    evidence_refs: list[str],
    revision_history: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for revision in revision_history:
        for change in revision.get("field_changes", []):
            if change.get("field_key") != field_key:
                continue
            links.append(
                {
                    "revision": int(revision["revision"]),
                    "before_value": change.get("before_value"),
                    "after_value": change.get("after_value"),
                    "before_status": change.get("before_status"),
                    "after_status": change.get("after_status"),
                    "before_evidence_refs": list(
                        change.get("before_evidence_refs", [])
                    ),
                    "after_evidence_refs": list(
                        change.get("after_evidence_refs", [])
                    ),
                }
            )
    if links:
        return links
    return [
        {
            "revision": bundle_revision,
            "before_value": None,
            "after_value": value,
            "before_status": None,
            "after_status": status,
            "before_evidence_refs": [],
            "after_evidence_refs": list(evidence_refs),
        }
    ]


def _pending_reason(field, *, staff_confirmed: bool) -> tuple[str, str]:
    if field.status == "material_conflict":
        return (
            "material_conflict",
            "충돌한 값 중 어느 것도 자동 선택하지 않고 원문과 현재 사실을 확인합니다.",
        )
    if field.status == "current_observation_required":
        return (
            "current_observation_required",
            "과거 자료만으로 현재 상태를 확정하지 않고 직원이 현재 관찰합니다.",
        )
    if field.status == "admin_missing":
        return (
            "admin_missing",
            "자료에 없는 행정정보는 직원이 확인해 입력합니다.",
        )
    if field.value_kind in _EXPERT_VALUE_KINDS:
        return (
            "expert_confirmation",
            "점수·진단은 확인된 원본과 전문가 확인 없이 초안에 반영하지 않습니다.",
        )
    if field.value_kind == "signature":
        return (
            "user_decision_required",
            "직원 확인 전에는 서명을 생성하거나 확정하지 않습니다.",
        )
    if field.value_kind in {"service_frequency", "service_duration"}:
        return (
            "user_decision_required",
            "급여 횟수·시간은 직원이 확인한 계획값 없이 만들지 않습니다.",
        )
    if field.status == "user_decision_required":
        return (
            "user_decision_required",
            "자료만으로 결정할 수 없어 직원의 선택을 기다립니다.",
        )
    if not staff_confirmed:
        return (
            "staff_confirmation",
            "자료에 값이 있지만 직원 확인 전에는 급여제공계획 초안에 반영하지 않습니다.",
        )
    return (
        "staff_confirmation",
        "직원이 근거를 확인한 뒤에만 급여제공계획 초안에 반영합니다.",
    )


def build_new_admission_care_plan_preview(
    bundle_payload: dict[str, Any],
    *,
    field_confirmations: dict[str, dict[str, Any]] | None = None,
    revision_history: list[dict[str, Any]] | None = None,
    assessment_workflow: dict[str, Any],
) -> dict[str, Any]:
    """Create a read-only, evidence-linked care-plan editing preview."""

    bundle = NewAdmissionDraftBundle.model_validate(bundle_payload)
    confirmations = field_confirmations or {}
    history = revision_history or []
    source_by_ref = {
        source.source_ref: source for source in bundle.evidence_sources
    }
    care_plan_draft = next(
        draft
        for draft in bundle.drafts
        if draft.document_type == "long_term_care_service_plan"
    )
    field_by_key = {field.field_key: field for field in bundle.fields}

    confirmed_results: list[dict[str, Any]] = []
    pending_items: list[dict[str, Any]] = []
    seen_pending_keys: set[str] = set()

    for field_key in care_plan_draft.field_keys:
        field = field_by_key[field_key]
        confirmation = confirmations.get(field.field_key, {})
        staff_confirmed = confirmation.get("state") == "confirmed"
        evidence_sources = [
            _source_summary(source_by_ref[source_ref])
            for source_ref in field.evidence_refs
            if source_ref in source_by_ref
        ]
        source_assessments = [
            document_type
            for document_type in field.document_types
            if document_type in _ASSESSMENT_DOCUMENT_TYPES
        ]
        links = _revision_links(
            field_key=field.field_key,
            bundle_revision=bundle.revision,
            value=field.value,
            status=field.status,
            evidence_refs=list(field.evidence_refs),
            revision_history=history,
        )

        if field.status in _RESOLVED_STATUSES and staff_confirmed:
            confirmed_results.append(
                {
                    "field_key": field.field_key,
                    "label": field.label,
                    "value_kind": field.value_kind,
                    "value": field.value,
                    "draft_text": f"{field.label}: {field.value}",
                    "source_assessment_types": source_assessments,
                    "evidence_refs": list(field.evidence_refs),
                    "evidence_sources": evidence_sources,
                    "confirmation_state": "confirmed",
                    "confirmed_by_name": confirmation.get("confirmed_by_name"),
                    "confirmed_at": confirmation.get("confirmed_at"),
                    "revision_links": links,
                }
            )
            continue

        if field.field_key in seen_pending_keys:
            continue
        seen_pending_keys.add(field.field_key)
        category, reason = _pending_reason(
            field,
            staff_confirmed=staff_confirmed,
        )
        pending_items.append(
            {
                "field_key": field.field_key,
                "label": field.label,
                "category": category,
                "category_label": _PENDING_CATEGORY_LABELS[category],
                "reason": reason,
                "value_kind": field.value_kind,
                "evidence_refs": list(field.evidence_refs),
                "evidence_sources": evidence_sources,
                "conflict_values": list(field.conflict_values),
                "selected_value": None,
                "revision_links": links,
            }
        )

    official_basis_items = [
        {
            "field_key": f"official_basis_{step['document_type']}",
            "label": f"{step['title']} 최신 공식 기준",
            "category": "official_basis",
            "category_label": _PENDING_CATEGORY_LABELS["official_basis"],
            "reason": step["official_basis_notice"],
            "value_kind": "fact",
            "evidence_refs": [],
            "evidence_sources": [],
            "conflict_values": [],
            "selected_value": None,
            "revision_links": [],
        }
        for step in assessment_workflow["steps"]
        if step["official_basis_state"]
        != "official_basis_confirmed_for_product_use"
    ]

    staff_questions = [
        item
        for item in pending_items
        if item["category"]
        in {
            "staff_confirmation",
            "user_decision_required",
            "admin_missing",
        }
    ]
    conflicts_and_current_observations = [
        item
        for item in pending_items
        if item["category"]
        in {"material_conflict", "current_observation_required"}
    ]
    expert_review_items = [
        item
        for item in pending_items
        if item["category"] == "expert_confirmation"
    ] + official_basis_items

    return {
        "schema_version": "new_admission_care_plan_preview_v1",
        "case_ref": bundle.case_ref,
        "revision": bundle.revision,
        "output_status": (
            "needs_confirmation"
            if pending_items or official_basis_items
            else "draft"
        ),
        "allowed_output_statuses": ["draft", "needs_confirmation"],
        "processing_scope": bundle.processing_scope,
        "external_transfer_allowed": False,
        "confirmed_assessment_results": confirmed_results,
        "reflected_plan_items": confirmed_results,
        "staff_questions": staff_questions,
        "conflicts_and_current_observations": conflicts_and_current_observations,
        "expert_review_items": expert_review_items,
        "confirmed_result_count": len(confirmed_results),
        "reflected_item_count": len(confirmed_results),
        "staff_question_count": len(staff_questions),
        "conflict_or_observation_count": len(
            conflicts_and_current_observations
        ),
        "expert_review_count": len(expert_review_items),
        "duplicate_question_count": (
            len(pending_items) - len({item["field_key"] for item in pending_items})
        ),
        "blocked_auto_value_kinds": [
            "service_type",
            "service_frequency",
            "service_duration",
            "assessment_score",
            "diagnosis",
            "care_goal",
            "signature",
        ],
        "auto_generated_restricted_value_count": 0,
        "official_record_write_count": 0,
        "signature_confirmation_count": 0,
        "public_insurer_transfer_count": 0,
        "revision_mutation_count": 0,
        "safety_notice": (
            "이 화면은 공식 저장 전 편집용 초안입니다. 직원이 근거를 확인하기 전에는 "
            "공식 기록 저장·서명 확정·공단 전송을 할 수 없습니다."
        ),
    }
