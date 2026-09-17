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


def _workflow(payload: dict | None = None) -> dict:
    return build_new_admission_question_flow(payload or _fixture())[
        "assessment_workflow"
    ]


def _step(workflow: dict, document_type: str) -> dict:
    return next(
        item
        for item in workflow["steps"]
        if item["document_type"] == document_type
    )


def test_four_assessments_follow_product_order_without_legal_claim():
    workflow = _workflow()

    assert [step["document_type"] for step in workflow["steps"]] == [
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
    ]
    assert [step["step_number"] for step in workflow["steps"]] == [1, 2, 3, 4]
    assert workflow["product_workflow_only"] is True
    assert workflow["legal_requirement_order_claimed"] is False
    assert "법적 의무 순서" in workflow["workflow_notice"]


def test_confirmed_facts_are_reused_in_later_assessments():
    workflow = _workflow()
    needs = _step(workflow, "needs_assessment")

    assert {fact["field_key"] for fact in needs["reusable_facts"]} == {
        "long_term_care_grade",
        "hypertension_diagnosis",
        "hydration_support_goal",
        "mobility_support_need",
    }
    assert needs["reusable_fact_count"] == 4


def test_staff_questions_are_assigned_once_and_not_repeated():
    workflow = _workflow()
    assigned_keys = [
        question["question_key"]
        for step in workflow["steps"]
        for question in step["new_questions"]
    ]

    assert assigned_keys == [
        "recent_fall_count",
        "skin_moisture_current",
        "cognitive_assessment_decision",
        "admission_room",
    ]
    assert len(assigned_keys) == len(set(assigned_keys))
    assert workflow["duplicate_assigned_question_count"] == 0
    assert [item["field_key"] for item in workflow["deferred_care_plan_questions"]] == [
        "service_frequency"
    ]
    assert [
        item["field_key"]
        for item in workflow["final_human_confirmation_questions"]
    ] == ["staff_signature"]


def test_past_data_does_not_replace_current_observation():
    pressure = _step(_workflow(), "pressure_ulcer_risk_assessment")

    assert [item["field_key"] for item in pressure["new_questions"]] == [
        "skin_moisture_current"
    ]
    assert pressure["new_questions"][0]["selected_value"] is None
    assert "현재 상태를 관찰" in pressure["new_questions"][0]["prompt"]


def test_conflicting_sources_are_not_selected_and_are_carried_forward():
    workflow = _workflow()
    fall = _step(workflow, "fall_risk_assessment")
    needs = _step(workflow, "needs_assessment")

    conflict = fall["new_questions"][0]
    assert conflict["field_key"] == "recent_fall_count"
    assert conflict["selected_value"] is None
    assert conflict["conflict_values"] == ["최근 낙상 없음", "최근 낙상 1회"]
    assert [item["field_key"] for item in needs["carried_pending_questions"]] == [
        "skin_moisture_current",
        "cognitive_assessment_decision",
        "recent_fall_count",
    ]
    assert "recent_fall_count" not in {
        item["field_key"] for item in needs["new_questions"]
    }


def test_unverified_scores_grades_and_diagnoses_are_never_generated():
    workflow = _workflow()

    assert workflow["output_status"] == "needs_confirmation"
    assert workflow["allowed_output_statuses"] == ["draft", "needs_confirmation"]
    assert workflow["score_generation_attempt_count"] == 0
    assert workflow["risk_grade_generation_attempt_count"] == 0
    assert workflow["diagnosis_generation_attempt_count"] == 0
    for step in workflow["steps"]:
        assert step["score_generation_allowed"] is False
        assert step["score"] is None
        assert step["risk_grade"] is None
        assert step["diagnosis"] is None
        assert step["output_status"] == "needs_confirmation"
        assert "확정하지 않습니다" in step["blocked_result_notice"]


def test_official_write_signature_transfer_and_revision_mutation_are_zero():
    fixture = _fixture()
    original = deepcopy(fixture)
    workflow = _workflow(fixture)

    assert fixture == original
    assert fixture["revision"] == 1
    assert workflow["revision"] == 1
    assert workflow["official_record_write_count"] == 0
    assert workflow["signature_confirmation_count"] == 0
    assert workflow["public_insurer_transfer_count"] == 0
    assert workflow["external_transfer_allowed"] is False
    assert any(
        fact["evidence_refs"]
        for step in workflow["steps"]
        for fact in step["reusable_facts"]
    )


def test_only_huhn_has_official_reference_found_but_scoring_stays_blocked():
    workflow = _workflow()
    states = {
        step["document_type"]: step["official_basis_state"]
        for step in workflow["steps"]
    }

    assert states["fall_risk_assessment"] == (
        "official_reference_found_latest_adoption_required"
    )
    assert set(states.values()) == {
        "official_reference_found_latest_adoption_required",
        "latest_official_source_required",
    }
