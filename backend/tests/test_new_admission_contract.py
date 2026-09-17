from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.new_admission_contract import (
    NEW_ADMISSION_DOCUMENT_TYPES,
    NewAdmissionDraftBundle,
)


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "m21_new_admission_contract_v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def _field(payload: dict, field_key: str) -> dict:
    return next(field for field in payload["fields"] if field["field_key"] == field_key)


def test_synthetic_new_admission_bundle_preserves_contract_and_boundaries():
    bundle = NewAdmissionDraftBundle.model_validate(_fixture())

    assert bundle.schema_version == "new_admission_draft_v1"
    assert bundle.processing_scope == "deidentified_dev"
    assert bundle.external_transfer_allowed is False
    assert bundle.revision == 1
    assert bundle.supersedes_revision is None
    assert {source.source_type for source in bundle.evidence_sources} == {
        "public_insurer_data",
        "medical_or_submitted_document",
        "consultation",
        "staff_confirmation",
        "current_observation",
    }
    assert {field.status for field in bundle.fields} == {
        "confirmed",
        "reusable",
        "derivable",
        "current_observation_required",
        "user_decision_required",
        "material_conflict",
        "admin_missing",
    }
    assert {draft.document_type for draft in bundle.drafts} == set(
        NEW_ADMISSION_DOCUMENT_TYPES
    )
    assert {draft.status for draft in bundle.drafts} == {"needs_confirmation"}

    restricted_values = {
        field.value_kind: field.value
        for field in bundle.fields
        if field.value_kind
        in {"assessment_score", "service_frequency", "service_duration", "signature"}
    }
    assert "assessment_score" not in restricted_values
    assert restricted_values == {"service_frequency": None, "signature": None}
    assert all(source.contains_sensitive_data is False for source in bundle.evidence_sources)


def test_unknown_evidence_reference_is_rejected():
    payload = _fixture()
    _field(payload, "long_term_care_grade")["evidence_refs"] = ["unknown-source"]

    with pytest.raises(ValidationError, match="unknown evidence_refs"):
        NewAdmissionDraftBundle.model_validate(payload)


def test_material_conflict_cannot_choose_a_value_or_hide_a_source():
    payload = _fixture()
    conflict = _field(payload, "recent_fall_count")
    conflict["value"] = "최근 낙상 없음"
    conflict["evidence_refs"] = ["staff-confirmation-syn-001"]
    conflict["conflict_values"] = ["최근 낙상 없음"]

    with pytest.raises(ValidationError, match="unresolved fields must not"):
        NewAdmissionDraftBundle.model_validate(payload)


@pytest.mark.parametrize(
    "value_kind",
    ["assessment_score", "diagnosis", "service_frequency", "service_duration"],
)
def test_restricted_values_cannot_be_derived_before_authority_check(value_kind: str):
    payload = _fixture()
    field = _field(payload, "mobility_support_need")
    field["value_kind"] = value_kind

    with pytest.raises(ValidationError, match="cannot be derived"):
        NewAdmissionDraftBundle.model_validate(payload)


def test_signature_cannot_be_auto_filled_in_a_draft():
    payload = _fixture()
    signature = _field(payload, "staff_signature")
    signature["status"] = "confirmed"
    signature["value"] = "합성 서명"
    signature["evidence_refs"] = ["staff-confirmation-syn-001"]

    with pytest.raises(ValidationError, match="never auto-fill a signature"):
        NewAdmissionDraftBundle.model_validate(payload)


def test_draft_status_and_pending_field_lists_must_match_field_states():
    payload = _fixture()
    needs_draft = next(
        draft
        for draft in payload["drafts"]
        if draft["document_type"] == "needs_assessment"
    )
    needs_draft["status"] = "draft"
    needs_draft["missing_fields"] = []

    with pytest.raises(ValidationError, match="missing_fields do not match"):
        NewAdmissionDraftBundle.model_validate(payload)


def test_all_four_assessments_and_care_plan_are_required():
    payload = _fixture()
    payload["drafts"].pop()

    with pytest.raises(ValidationError):
        NewAdmissionDraftBundle.model_validate(payload)


def test_full_eight_column_service_plan_can_be_saved_as_one_revision():
    payload = _fixture()
    columns = (
        "장기요양 필요영역",
        "장기요양 세부목표",
        "장기요양 필요내용",
        "세부 제공내용",
        "제공방법",
        "횟수",
        "시간(분)",
        "작성자",
    )
    # The actual form persists a row value and review state in addition to the
    # eight visible cells.  One complete 50-row plan plus header/opinion fields
    # must fit in one append-only staff revision.
    values = {
        f"service_plan.row_{row_index:02d}::{column}": "합성 검증값"
        for row_index in range(50)
        for column in columns
    }
    values.update(
        {
            f"service_plan.row_{row_index:02d}": "합성 검증값"
            for row_index in range(50)
        }
    )
    values.update(
        {
            f"service_plan.row_{row_index:02d}::review_state": ""
            for row_index in range(50)
        }
    )
    values.update(
        {f"service_plan.summary_{index:02d}": "합성 검증값" for index in range(12)}
    )
    payload["form_values"] = {"long_term_care_service_plan": values}

    bundle = NewAdmissionDraftBundle.model_validate(payload)

    assert len(bundle.form_values["long_term_care_service_plan"]) == 512


def test_excessive_saved_form_values_are_still_rejected():
    payload = _fixture()
    payload["form_values"] = {
        "long_term_care_service_plan": {
            f"service_plan.unexpected_{index:03d}": "합성 검증값"
            for index in range(601)
        }
    }

    with pytest.raises(ValidationError, match="too many saved form values"):
        NewAdmissionDraftBundle.model_validate(payload)


def test_deidentified_dev_rejects_sensitive_source_and_revision_overwrite():
    sensitive_payload = _fixture()
    sensitive_payload["evidence_sources"][0]["contains_sensitive_data"] = True
    with pytest.raises(ValidationError, match="deidentified_dev"):
        NewAdmissionDraftBundle.model_validate(sensitive_payload)

    revision_payload = deepcopy(_fixture())
    revision_payload["revision"] = 2
    revision_payload["supersedes_revision"] = 2
    with pytest.raises(ValidationError, match="supersedes_revision"):
        NewAdmissionDraftBundle.model_validate(revision_payload)


def test_change_review_selection_is_saved_but_conflicts_cannot_be_selected():
    payload = _fixture()
    payload["comparison_items"] = [
        {
            "field_key": "mobility_support_need",
            "label": "이동 도움",
            "classification": "changed",
            "previous_value": "독립 보행",
            "current_value": "부축 필요",
            "baseline_evidence_refs": ["staff-confirmation-syn-001"],
            "current_evidence_refs": ["current-observation-syn-001"],
            "conflict_values": [],
            "reason": "이동 지원 방법 검토",
        },
        {
            "field_key": "recent_fall_count",
            "label": "최근 낙상",
            "classification": "material_conflict",
            "previous_value": None,
            "current_value": None,
            "baseline_evidence_refs": [],
            "current_evidence_refs": [
                "staff-confirmation-syn-001",
                "current-observation-syn-001",
            ],
            "conflict_values": ["없음", "1회"],
            "reason": "서로 다른 기록 확인 필요",
        },
    ]
    selectable = next(
        item
        for item in payload["comparison_items"]
        if item["classification"] != "material_conflict"
    )
    conflict = next(
        item
        for item in payload["comparison_items"]
        if item["classification"] == "material_conflict"
    )

    payload["selected_change_field_keys"] = [selectable["field_key"]]
    bundle = NewAdmissionDraftBundle.model_validate(payload)
    assert bundle.selected_change_field_keys == [selectable["field_key"]]

    payload["selected_change_field_keys"] = [conflict["field_key"]]
    with pytest.raises(ValidationError, match="충돌 항목은 반영 대상으로 선택할 수 없습니다"):
        NewAdmissionDraftBundle.model_validate(payload)
