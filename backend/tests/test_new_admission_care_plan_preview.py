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


def _reviewed_fixture() -> dict:
    payload = _fixture()
    payload["document_reviews"] = {
        document_type: {"state": "reviewed"}
        for document_type in (
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
        )
    }
    return payload


def _confirmations(*confirmed_keys: str) -> dict[str, dict]:
    payload = _fixture()
    return {
        field["field_key"]: {
            "state": (
                "confirmed"
                if field["field_key"] in confirmed_keys
                else "needs_confirmation"
            ),
            "confirmed_by_id": "00000000-0000-0000-0000-000000000001"
            if field["field_key"] in confirmed_keys
            else None,
            "confirmed_by_name": "시험 직원"
            if field["field_key"] in confirmed_keys
            else None,
            "confirmed_at": "2026-08-26T12:00:00Z"
            if field["field_key"] in confirmed_keys
            else None,
        }
        for field in payload["fields"]
    }


def _preview(*confirmed_keys: str) -> dict:
    return build_new_admission_question_flow(
        _reviewed_fixture(),
        _confirmations(*confirmed_keys),
        revision_history=[],
    )["care_plan_preview"]


def test_only_staff_confirmed_assessment_facts_are_reflected_in_plan_draft():
    preview = _preview("long_term_care_grade", "hydration_support_goal")

    assert [
        item["field_key"] for item in preview["reflected_plan_items"]
    ] == ["long_term_care_grade", "hydration_support_goal"]
    assert [item["draft_text"] for item in preview["reflected_plan_items"]] == [
        "장기요양등급: 3",
        "수분 섭취 확인 요청: 수분 섭취를 자주 확인해 달라는 요청",
    ]
    assert all(
        item["confirmation_state"] == "confirmed"
        and item["evidence_refs"]
        and item["revision_links"]
        for item in preview["reflected_plan_items"]
    )
    assert preview["confirmed_result_count"] == 2
    assert preview["reflected_item_count"] == 2


def test_unconfirmed_resolved_values_are_questions_not_plan_sentences():
    preview = _preview("long_term_care_grade", "hydration_support_goal")
    reflected_keys = {
        item["field_key"] for item in preview["reflected_plan_items"]
    }
    pending_keys = {
        item["field_key"]
        for item in (
            preview["staff_questions"]
            + preview["expert_review_items"]
            + preview["conflicts_and_current_observations"]
        )
    }

    assert "hypertension_diagnosis" not in reflected_keys
    assert "mobility_support_need" not in reflected_keys
    assert "hypertension_diagnosis" in pending_keys
    assert "mobility_support_need" in pending_keys
    assert preview["duplicate_question_count"] == 0


def test_conflicts_current_observation_and_restricted_values_are_never_selected():
    preview = _preview("long_term_care_grade", "hydration_support_goal")
    pending = {
        item["field_key"]: item
        for item in (
            preview["staff_questions"]
            + preview["expert_review_items"]
            + preview["conflicts_and_current_observations"]
        )
    }

    assert pending["recent_fall_count"]["selected_value"] is None
    assert pending["recent_fall_count"]["conflict_values"] == [
        "최근 낙상 없음",
        "최근 낙상 1회",
    ]
    assert pending["service_frequency"]["selected_value"] is None
    assert pending["staff_signature"]["selected_value"] is None
    assert all(item["selected_value"] is None for item in pending.values())
    assert preview["auto_generated_restricted_value_count"] == 0


def test_official_basis_and_expert_review_stay_unconfirmed():
    preview = _preview("long_term_care_grade", "hydration_support_goal")
    official_items = [
        item
        for item in preview["expert_review_items"]
        if item["category"] == "official_basis"
    ]

    assert len(official_items) == 4
    assert all(item["selected_value"] is None for item in official_items)
    assert "diagnosis" in preview["blocked_auto_value_kinds"]
    assert "assessment_score" in preview["blocked_auto_value_kinds"]


def test_preview_is_read_only_and_does_not_mutate_revision_or_official_records():
    fixture = _reviewed_fixture()
    original = deepcopy(fixture)
    preview = build_new_admission_question_flow(
        fixture,
        _confirmations("long_term_care_grade", "hydration_support_goal"),
        revision_history=[
            {
                "revision": 1,
                "field_changes": [
                    {
                        "field_key": "hydration_support_goal",
                        "before_value": None,
                        "after_value": "수분 섭취를 자주 확인해 달라는 요청",
                        "before_status": None,
                        "after_status": "reusable",
                        "before_evidence_refs": [],
                        "after_evidence_refs": ["consultation-syn-001"],
                    }
                ],
            }
        ],
    )["care_plan_preview"]

    assert fixture == original
    assert preview["revision"] == 1
    assert preview["revision_mutation_count"] == 0
    assert preview["official_record_write_count"] == 0
    assert preview["signature_confirmation_count"] == 0
    assert preview["public_insurer_transfer_count"] == 0
    assert preview["external_transfer_allowed"] is False
    hydration = next(
        item
        for item in preview["reflected_plan_items"]
        if item["field_key"] == "hydration_support_goal"
    )
    assert hydration["revision_links"][0]["revision"] == 1
    assert hydration["revision_links"][0]["after_evidence_refs"] == [
        "consultation-syn-001"
    ]
