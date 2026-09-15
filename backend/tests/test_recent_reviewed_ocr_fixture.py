from __future__ import annotations

import json
import re
from pathlib import Path


FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "recent_reviewed_ocr_structure_cases.json"
)


def _load_fixture() -> dict:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_recent_reviewed_fixture_is_anonymized_and_structurally_complete() -> None:
    payload = _load_fixture()

    assert payload["source_basis"] == "aggregate_only_recent_staff_reviewed_images"
    assert all(value is False for value in payload["privacy"].values())
    assert payload["observed_aggregate"]["reviewed_image_count"] == 12
    assert payload["observed_aggregate"]["repeated_name_row_image_count"] == 10
    assert payload["observed_aggregate"]["reviewed_roster_name_slots"] == 63
    assert payload["constraints"]["legacy_test_flag_must_not_exclude_active_roster"]

    serialized = json.dumps(payload, ensure_ascii=False)
    assert not re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        serialized,
        re.IGNORECASE,
    )
    assert "storage_key" not in serialized
    assert "original_name" not in serialized


def test_every_auto_draft_name_slot_is_roster_bound_or_explicitly_unresolved() -> None:
    payload = _load_fixture()
    sentinel = payload["constraints"]["unknown_name_sentinel"]

    for case in payload["cases"]:
        allowed = set(case["active_roster"]) | {sentinel}
        assert set(case["expected_draft_name_slots"]) <= allowed
        assert case["raw_text"]
        assert case["auto_draft"]
        assert case["staff_final"]


def test_fixture_covers_requested_section_writer_and_domain_phrase_patterns() -> None:
    payload = _load_fixture()
    cases = {case["id"]: case for case in payload["cases"]}

    meal_case = cases["meal-name-column-with-transport"]
    assert meal_case["repeated_name_row_pattern"] is True
    assert set(meal_case["detected_sections"]) == {"meal", "transport"}
    assert meal_case["writer_history_hints"]

    role_case = cases["role-phrase-is-not-a-resident-name"]
    assert "아침 송영시" in role_case["auto_draft"]
    assert "운전원 선생" in role_case["auto_draft"]
    assert role_case["expected_draft_name_slots"] == []

    ambiguous_case = cases["ambiguous-name-remains-unresolved"]
    assert "이름 확인 필요" in ambiguous_case["auto_draft"]
    assert "누구지" not in ambiguous_case["auto_draft"]


def test_aggregate_name_slot_outcomes_balance() -> None:
    aggregate = _load_fixture()["observed_aggregate"]
    expected = aggregate["reviewed_roster_name_slots"]

    assert (
        aggregate["raw_exact_roster_name_slots"]
        + aggregate["raw_out_of_roster_name_slots"]
        + aggregate["raw_missing_name_slots"]
        == expected
    )
    assert (
        aggregate["suggested_exact_roster_name_slots"]
        + aggregate["suggested_wrong_roster_name_slots"]
        + aggregate["suggested_out_of_roster_name_slots"]
        + aggregate["suggested_missing_name_slots"]
        == expected
    )
