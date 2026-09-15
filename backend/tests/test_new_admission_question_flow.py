import json
from copy import deepcopy
from pathlib import Path

from app.new_admission_questions import build_new_admission_question_flow


FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m21_new_admission_contract_v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _field(payload: dict, field_key: str) -> dict:
    return next(field for field in payload["fields"] if field["field_key"] == field_key)


def test_reuses_resolved_facts_and_asks_each_unresolved_field_once():
    fixture = _fixture()
    flow = build_new_admission_question_flow(fixture)

    assert flow["external_transfer_allowed"] is False
    assert flow["output_status"] == "needs_confirmation"
    assert flow["reusable_fact_count"] == 4
    assert flow["question_count"] == 6
    assert flow["duplicate_question_count"] == 0
    assert flow["question_count_by_type"] == {
        "material_conflict": 1,
        "current_observation_required": 1,
        "user_decision_required": 3,
        "admin_missing": 1,
    }

    reusable_by_key = {
        item["field_key"]: item for item in flow["reusable_facts"]
    }
    assert set(reusable_by_key) == {
        "long_term_care_grade",
        "hypertension_diagnosis",
        "hydration_support_goal",
        "mobility_support_need",
    }
    assert reusable_by_key["long_term_care_grade"]["status_label"] == (
        "자료에서 확인된 사실"
    )
    assert reusable_by_key["hydration_support_goal"]["status_label"] == (
        "그대로 재사용 가능한 사실"
    )
    assert reusable_by_key["mobility_support_need"]["status_label"] == (
        "자료에서 안전하게 도출 가능한 내용"
    )
    assert reusable_by_key["hypertension_diagnosis"]["value"] == _field(
        fixture, "hypertension_diagnosis"
    )["value"]

    questions_by_key = {item["field_key"]: item for item in flow["questions"]}
    assert len(questions_by_key) == 6
    assert set(questions_by_key).isdisjoint(reusable_by_key)
    assert questions_by_key["recent_fall_count"]["conflict_values"] == [
        "최근 낙상 없음",
        "최근 낙상 1회",
    ]
    assert questions_by_key["recent_fall_count"]["selected_value"] is None
    assert "자동 선택하지 않습니다" in questions_by_key["recent_fall_count"][
        "reason"
    ]
    assert "현재 상태를 관찰" in questions_by_key["skin_moisture_current"]["prompt"]
    assert "과거 자료" in questions_by_key["skin_moisture_current"]["reason"]
    assert "직원이 결정" in questions_by_key["service_frequency"]["prompt"]
    assert "행정정보" in questions_by_key["admission_room"]["prompt"]
    assert questions_by_key["staff_signature"]["selected_value"] is None
    assert "자동 확정하지 않습니다" in questions_by_key["staff_signature"][
        "reason"
    ]
    assert all(question["selected_value"] is None for question in flow["questions"])
    assert flow["form_workspace"]["schema_version"] == (
        "new_admission_form_workspace_v1"
    )
    assert [
        item["document_type"]
        for item in flow["form_workspace"]["documents"]
    ] == [
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
    ]
    assert flow["form_workspace"]["care_plan_gate"]["available"] is False
    assert flow["care_plan_preview"] is None
    assert flow["form_workspace"]["safety"]["official_record_saved"] is False


def test_builds_care_plan_preview_only_after_all_four_assessments_are_reviewed():
    fixture = _fixture()
    fixture["document_reviews"] = {
        document_type: {"state": "reviewed"}
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }

    flow = build_new_admission_question_flow(fixture)

    assert flow["form_workspace"]["care_plan_gate"]["available"] is True
    assert flow["care_plan_preview"] is not None
    assert flow["form_workspace"]["documents"][-1]["document_type"] == (
        "long_term_care_service_plan"
    )


def test_distinguishes_authoring_reference_and_review_sources():
    flow = build_new_admission_question_flow(_fixture())
    usages = {source["source_ref"]: source for source in flow["sources"]}

    assert usages["staff-confirmation-syn-001"]["usage"] == (
        "authoring_direct_verified"
    )
    assert usages["current-observation-syn-001"]["usage"] == (
        "authoring_needs_review"
    )
    assert usages["medical-document-syn-001"]["usage"] == "reference_only"
    assert usages["public-insurer-syn-001"]["usage"] == (
        "public_insurer_reference"
    )
    assert usages["consultation-syn-001"]["usage"] == "consultation_evidence"
    assert flow["source_counts"] == {
        "public_insurer_reference": 1,
        "reference_only": 1,
        "consultation_evidence": 1,
        "authoring_direct_verified": 1,
        "authoring_needs_review": 1,
    }


def test_resolved_admin_fact_is_reused_without_repeating_the_question():
    fixture = deepcopy(_fixture())
    admin_field = _field(fixture, "admission_room")
    admin_field["status"] = "confirmed"
    admin_field["value"] = "합성 생활실 A"
    admin_field["evidence_refs"] = ["staff-confirmation-syn-001"]
    for draft in fixture["drafts"]:
        draft["missing_fields"] = [
            field_key
            for field_key in draft["missing_fields"]
            if field_key != "admission_room"
        ]

    flow = build_new_admission_question_flow(fixture)

    assert flow["reusable_fact_count"] == 5
    assert flow["question_count"] == 5
    assert flow["duplicate_question_count"] == 0
    assert "admission_room" in {
        item["field_key"] for item in flow["reusable_facts"]
    }
    assert "admission_room" not in {
        item["field_key"] for item in flow["questions"]
    }


def test_legacy_missing_fact_is_not_shown_as_missing_administrative_information():
    fixture = deepcopy(_fixture())
    clinical_field = _field(fixture, "skin_moisture_current")
    clinical_field["status"] = "admin_missing"
    for draft in fixture["drafts"]:
        if "skin_moisture_current" not in draft["field_keys"]:
            continue
        draft["missing_fields"].append("skin_moisture_current")
        draft["human_verification_fields"] = [
            field_key
            for field_key in draft["human_verification_fields"]
            if field_key != "skin_moisture_current"
        ]

    flow = build_new_admission_question_flow(fixture)
    question = next(
        item
        for item in flow["questions"]
        if item["field_key"] == "skin_moisture_current"
    )

    assert question["question_type"] == "current_observation_required"
    assert question["question_type_label"] == "현재 관찰 필요"
    assert "현재 상태를 관찰" in question["prompt"]
    assert "행정정보" not in question["prompt"]
