import json
from pathlib import Path

import pytest

from app.care_briefing_contract import (
    CareBriefingV1Input,
    CareBriefingV1Result,
    validate_care_briefing_result,
)


FIXTURE_PATH = Path(__file__).parent / "fixtures" / "care_briefing_v1_cases.json"
SKILL_PATH = (
    Path(__file__).parents[1]
    / "app"
    / "ai_skills"
    / "care-briefing-v1"
    / "SKILL.md"
)


@pytest.fixture(scope="module")
def representative_cases() -> list[dict]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return payload["cases"]


def test_representative_cases_cover_required_risks(
    representative_cases: list[dict],
) -> None:
    case_ids = [case["case_id"] for case in representative_cases]
    assert len(case_ids) == 6
    assert len(case_ids) == len(set(case_ids))

    covered_tags = {
        tag
        for case in representative_cases
        for tag in case["tags"]
    }
    assert {
        "important_event",
        "reply_final_state",
        "photo_evidence",
        "follow_up",
        "today_schedule",
        "unresolved_carryover",
        "routine_completed",
        "ocr_uncertain",
        "subject_unknown",
        "conflicting_facts",
    } <= covered_tags


def test_all_representative_results_satisfy_contract_and_answer_key(
    representative_cases: list[dict],
) -> None:
    for case in representative_cases:
        input_data = CareBriefingV1Input.model_validate(case["input"])
        result = CareBriefingV1Result.model_validate(case["expected_result"])
        validated = validate_care_briefing_result(input_data, result)
        answer_key = case["answer_key"]

        assert len(validated.events) == answer_key["required_event_count"], case[
            "case_id"
        ]

        events_by_id = {event.event_id: event for event in validated.events}
        for event_id, priority in answer_key.get("required_priorities", {}).items():
            assert events_by_id[event_id].priority == priority, case["case_id"]
        for event_id, status in answer_key.get(
            "required_final_statuses", {}
        ).items():
            assert events_by_id[event_id].final_status == status, case["case_id"]

        actual_evidence_ids = {
            evidence.source_id
            for event in validated.events
            for evidence in event.evidence
        }
        assert set(answer_key.get("required_evidence_source_ids", [])) <= (
            actual_evidence_ids
        ), case["case_id"]

        actual_ignored_ids = {
            ignored.source_id for ignored in validated.ignored_sources
        }
        assert set(answer_key.get("required_ignored_source_ids", [])) <= (
            actual_ignored_ids
        ), case["case_id"]

        actual_verification_fields = {
            item.field
            for event in validated.events
            for item in event.verification_items
        }
        assert set(answer_key.get("required_verification_fields", [])) <= (
            actual_verification_fields
        ), case["case_id"]

        actual_document_types = {
            candidate.document_type
            for event in validated.events
            for candidate in event.document_draft_candidates
        }
        assert set(answer_key.get("required_document_types", [])) <= (
            actual_document_types
        ), case["case_id"]


def test_external_comparison_cases_are_deidentified(
    representative_cases: list[dict],
) -> None:
    for case in representative_cases:
        policy = CareBriefingV1Input.model_validate(
            case["input"]
        ).transmission_policy
        assert policy.privacy_mode == "external_deidentified", case["case_id"]
        assert policy.external_ai_allowed is True, case["case_id"]
        assert policy.contains_direct_identifiers is False, case["case_id"]


def test_skill_defines_deterministic_safety_decisions() -> None:
    skill = SKILL_PATH.read_text(encoding="utf-8")

    assert '`subject_id: null`' in skill
    assert '`field: "subject"`' in skill
    assert '`final_status: "needs_confirmation"`' in skill
    assert '`field: "facts"`' in skill
    assert "`in_progress`" in skill
    assert "`monitoring`" in skill
    assert "`completed`" in skill
    assert "`first` / 80~100점" in skill
    assert "`check` / 50~79점" in skill
    assert "`observe` / 1~49점" in skill
    assert "`nursing_log`" in skill
    assert "`care_service_record`" in skill
    assert "`pending`" not in skill
