from __future__ import annotations

import json
from pathlib import Path

from app.domain_lexicon import canonical_terms
from app.ocr_template_engine import (
    PROFILE_PATH,
    VisualNameObservation,
    analyze_combined_checklist,
    apply_visual_name_observations,
    load_combined_checklist_profile,
    serialize_template_audit,
)


def test_coordinate_name_pass_accepts_only_clear_roster_candidate() -> None:
    raw = (
        "가나마: 확인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n마바자: 라마바\n바가다: 가나다"
    )
    roster = ["가나다", "가나라", "라마바", "마바사"]
    baseline = analyze_combined_checklist(raw, active_roster_names=roster)
    name_slots = [slot for slot in baseline.slots if slot.role == "name"]
    assert len(name_slots) == 6

    result = apply_visual_name_observations(
        baseline,
        active_roster_names=roster,
        observations=[
            VisualNameObservation(
                1, "가나마", ("가나다", "가나라"), (50, 80, 180, 120)
            ),
            VisualNameObservation(
                3, "마바사", ("마바사", "라마바"), (500, 300, 620, 340)
            ),
            VisualNameObservation(
                5, "전혀다름", ("가나다", "가나라"), (500, 360, 620, 400)
            ),
        ],
    )

    # The exact crop/roster match is usable, while an ambiguous reading never
    # becomes an arbitrary resident name.
    assert "마바사 (?)" in result.proposed_draft
    assert "전혀다름" not in result.proposed_draft
    assert "(?)" in result.proposed_draft
    assert not any(
        decision.candidate not in roster
        for decision in result.decisions
        if decision.slot.role == "name" and decision.candidate is not None
    )


def test_coordinate_name_pass_does_not_touch_non_name_prose() -> None:
    raw = (
        "가나다: 확인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n\n"
        "09:05 아침식사 후 설사 증상을 기록함"
    )
    baseline = analyze_combined_checklist(raw, active_roster_names=["가나다", "라마바"])
    result = apply_visual_name_observations(
        baseline,
        active_roster_names=["가나다", "라마바"],
        observations=[],
    )
    assert "09:05 아침식사 후 설사 증상을 기록함" in result.proposed_draft
    assert result.proposed_draft.count("(?)") == 0


def test_template_structure_survives_fuzzy_meal_header_and_noisy_status_values() -> (
    None
):
    raw = (
        "가나마: 교체1, 교체\n라마바: 회인, 확인\n\n"
        "〈아침식사 드신븐〉\n마바자: 라마바\n바가다: 가나다"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
    )
    assert analysis.matched is True
    assert len([slot for slot in analysis.slots if slot.role == "name"]) == 6


FIXTURE_PATH = (
    Path(__file__).with_name("fixtures") / "combined_checklist_template_cases.json"
)
COORDINATE_FIXTURE_PATH = (
    Path(__file__).with_name("fixtures") / "coordinate_name_slot_cases.json"
)


def test_profile_is_versioned_and_audit_only() -> None:
    profile = load_combined_checklist_profile()
    assert profile.profile_id == "silvermedical.combined_daily_checklist"
    assert profile.profile_version == "1.1.0"
    assert profile.rule_version == "combined-checklist-rules-1.1.0"
    assert profile.operating_mode == "dev_suggested_draft_only"
    assert profile.resident_service_scope == "daycare"
    assert PROFILE_PATH.is_file()


def test_combined_structure_is_hierarchical_and_role_specific() -> None:
    raw = (
        "가나다: 흑인\n"
        "라마바: 교체\n\n"
        "〈아침식사 하신 분〉\n"
        "가나디 : 라마바\n"
        "마바사 : 가나다\n\n"
        "09:05 특이사항을 기록함"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
    )
    assert analysis.matched is True
    assert {slot.section_id for slot in analysis.slots} >= {
        "status_checklist",
        "meal_name_list",
        "narrative",
    }
    assert {slot.role for slot in analysis.slots} >= {"name", "status", "time"}
    assert analysis.audit_metadata["hierarchy"]["tables"] == [
        "meal_roster_table",
        "status_table",
    ]


def test_name_candidate_requires_template_structure_roster_score_and_gap() -> None:
    raw = (
        "가나다: 확인\n라마바: 교체\n\n<아침식사 관련>\n가나디: 라마바\n마바사: 가나다"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
    )
    assert "가나다 (?)" in analysis.proposed_draft
    candidate = next(
        decision for decision in analysis.decisions if decision.slot.raw == "가나디"
    )
    assert candidate.action == "candidate"
    assert candidate.candidate == "가나다"

    ambiguous = analyze_combined_checklist(
        raw.replace("가나디", "가나마"),
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
    )
    uncertain = next(
        decision for decision in ambiguous.decisions if decision.slot.raw == "가나마"
    )
    assert uncertain.action == "preserve_uncertain"
    assert "가나마" in ambiguous.proposed_draft


def test_status_alias_only_changes_confirmed_status_slot_and_time_is_preserved() -> (
    None
):
    raw = (
        "가나다: 흑인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n마바사: 가나다\n\n"
        "09:05 본문에서 흑인이라는 OCR 원문은 보존"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
    )
    assert analysis.proposed_draft.count("확인") == 1
    assert "본문에서 흑인이라는 OCR 원문은 보존" in analysis.proposed_draft
    assert "09:05" in analysis.proposed_draft


def test_seedless_repeated_status_table_uses_scoped_aliases_only() -> None:
    raw = (
        ". 가나다: 고세, 고레\n"
        ". 라마바: 락인, 고세\n"
        ". 마바사: 라이, 락인\n"
        ". 다라마: 고레, 라이\n"
        "\n"
        "특이사항에는 락인과 고세라는 원문을 그대로 기록함"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사", "다라마"],
    )

    assert analysis.matched is True
    assert analysis.audit_metadata["match_mode"] == "status_table_only"
    assert analysis.proposed_draft.startswith(
        ". 가나다: 교체, 교체\n"
        ". 라마바: 확인, 교체\n"
        ". 마바사: 확인, 확인\n"
        ". 다라마: 교체, 확인"
    )
    assert "특이사항에는 락인과 고세라는 원문을 그대로 기록함" in (
        analysis.proposed_draft
    )
    assert not [slot for slot in analysis.slots if slot.role == "name"]


def test_seedless_colon_rows_without_roster_evidence_remain_raw() -> None:
    raw = (
        ". 항목가: 고세, 고레\n"
        ". 항목나: 락인, 고세\n"
        ". 항목다: 라이, 락인\n"
        ". 항목라: 고레, 라이\n"
        "본문에서 락인과 고세를 그대로 기록함"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바"],
    )

    assert analysis.matched is False
    assert analysis.proposed_draft == raw


def test_confirmed_status_history_changes_only_confirmed_status_column() -> None:
    raw = (
        "가나다: 문안, 교체\n"
        "라마바: 확인, 문안\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n마바사: 가나다\n\n"
        "특이사항에는 문안이라는 OCR 원문을 그대로 기록함"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
        confirmed_history=[
            {
                "recognized_text": "문안",
                "corrected_text": "확인",
                "content_type": "status",
                "section": "status_checklist",
                "layout_role": "status_cell",
                "column_role": "status",
            }
        ],
    )

    assert analysis.matched is True
    assert analysis.proposed_draft.startswith(
        "가나다: 확인, 교체\n라마바: 확인, 확인"
    )
    assert "특이사항에는 문안이라는 OCR 원문을 그대로 기록함" in (
        analysis.proposed_draft
    )
    corrected = [
        item
        for item in analysis.decisions
        if item.action == "history_context_correction"
    ]
    assert len(corrected) == 2
    assert all(item.slot.role == "status" for item in corrected)


def test_unstructured_history_never_changes_status_or_general_prose() -> None:
    raw = (
        "가나다: 문안, 교체\n"
        "라마바: 확인, 개인\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n마바사: 가나다\n\n"
        "특이사항에는 문안과 개인이라는 OCR 원문을 그대로 기록함"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
        confirmed_history=[
            {
                "recognized_text": "문안",
                "corrected_text": "확인",
                "content_type": "general",
                "section": "특이사항",
                "layout_role": "general",
            }
        ],
    )

    assert "가나다: 문안, 교체" in analysis.proposed_draft
    assert "라마바: 확인, 개인" in analysis.proposed_draft
    assert "특이사항에는 문안과 개인이라는 OCR 원문을 그대로 기록함" in (
        analysis.proposed_draft
    )
    assert not any(
        item.action == "history_context_correction" for item in analysis.decisions
    )


def test_status_history_from_another_document_template_is_not_reused() -> None:
    raw = (
        "가나다: 문안, 교체\n"
        "라마바: 확인, 개인\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n마바사: 가나다"
    )
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바", "마바사"],
        confirmed_history=[
            {
                "recognized_text": "문안",
                "corrected_text": "확인",
                "content_type": "status",
                "section": "status_checklist",
                "layout_role": "status_cell",
                "column_role": "status",
                "document_template": "another.template@1.0.0",
            }
        ],
    )

    assert "가나다: 문안, 교체" in analysis.proposed_draft
    assert not any(
        item.action == "history_context_correction" for item in analysis.decisions
    )


def test_non_template_prose_and_input_are_unchanged() -> None:
    raw = "아침식사 후 설사 증상이 있었음.\n담당자: 확인 필요\n10:20 상태 확인."
    analysis = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "라마바"],
    )
    assert analysis.matched is False
    assert analysis.proposed_draft == raw
    assert not [slot for slot in analysis.slots if slot.role == "name"]


def test_audit_metadata_keeps_versions_roles_and_never_marks_auto_apply() -> None:
    analysis = analyze_combined_checklist(
        "가나다: 확인\n라마바: 교체\n\n<아침식사 하신 분>\n가나다: 라마바",
        active_roster_names=["가나다", "라마바"],
    )
    details = serialize_template_audit(analysis)
    assert details[0]["kind"] == "template_audit"
    assert details[0]["auto_apply_allowed"] is False
    assert all(item["profile_version"] == "1.1.0" for item in details)
    assert all(
        item["rule_version"] == "combined-checklist-rules-1.1.0" for item in details
    )
    assert all("recognized" not in item and "candidate" not in item for item in details)


def test_fixture_is_deidentified_and_offline_comparison_passes() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert not any(payload["privacy"].values())
    for case in payload["cases"]:
        analysis = analyze_combined_checklist(
            case["raw"],
            active_roster_names=case["roster"],
        )
        assert analysis.matched is case["expected"]["matched"]
        if case["expected"].get("unchanged"):
            assert analysis.proposed_draft == case["raw"]
        else:
            assert case["expected"]["candidate_marker"] in analysis.proposed_draft
            assert case["expected"]["status_marker"] in analysis.proposed_draft
            assert case["expected"]["preserved_time"] in analysis.proposed_draft


def test_coordinate_fixture_has_no_outsider_wrong_auto_or_non_name_false_positive() -> (
    None
):
    payload = json.loads(COORDINATE_FIXTURE_PATH.read_text(encoding="utf-8"))
    assert not any(
        value
        for key, value in payload["privacy"].items()
        if key != "derived_from_structure_only"
    )
    total_accepted = 0
    total_uncertain = 0
    for case in payload["cases"]:
        baseline = analyze_combined_checklist(
            case["raw"], active_roster_names=case["roster"]
        )
        result = apply_visual_name_observations(
            baseline,
            active_roster_names=case["roster"],
            observations=[
                VisualNameObservation(
                    item["slot_index"],
                    item["recognized"],
                    tuple(item["candidates"]),
                    tuple(item["bbox"]),
                )
                for item in case["observations"]
            ],
        )
        visual = result.audit_metadata.get("visual_name_pass", {})
        accepted = int(visual.get("accepted_count", 0))
        uncertain = sum(
            1 for decision in result.decisions if decision.action == "visual_uncertain"
        )
        assert accepted == case["expected"]["accepted"]
        assert uncertain == case["expected"]["uncertain"]
        assert not any(
            decision.candidate not in case["roster"]
            for decision in result.decisions
            if decision.slot.role == "name" and decision.candidate is not None
        )
        assert not [
            decision
            for decision in result.decisions
            if decision.slot.role == "name"
            and decision.slot.section_id not in {"status_checklist", "meal_name_list"}
        ]
        if case["expected"].get("unchanged"):
            assert result.proposed_draft == case["raw"]
        total_accepted += accepted
        total_uncertain += uncertain

    # The text-only baseline had no independent visual confirmations.  The
    # coordinate pass safely recovers some names while keeping ambiguous cells
    # explicit instead of substituting another roster member.
    assert total_accepted == 2
    assert total_uncertain == 3


def test_recent_three_page_structure_has_no_new_placeholders_or_false_names() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    cases = [
        case
        for case in payload["cases"]
        if case["id"].startswith("three_page_regression_shape_")
    ]
    assert len(cases) == 3
    for case in cases:
        analysis = analyze_combined_checklist(
            case["raw"],
            active_roster_names=case["roster"],
        )
        assert analysis.proposed_draft.count("이름확인필요") == 0
        assert analysis.proposed_draft.count("이름 확인 필요") == 0
        assert not [
            slot
            for slot in analysis.slots
            if slot.role == "name"
            and slot.section_id not in {"status_checklist", "meal_name_list"}
        ]
        for decision in analysis.decisions:
            if decision.slot.role == "name" and decision.candidate is not None:
                assert decision.candidate in case["roster"]
        if case["expected"].get("unchanged"):
            assert analysis.proposed_draft == case["raw"]


def test_profile_maps_only_an_explicit_subset_of_domain_lexicon() -> None:
    profile = load_combined_checklist_profile()
    canonical = set(canonical_terms())
    mapped_canonical = profile.mapped_terms & canonical
    assert mapped_canonical
    assert mapped_canonical < canonical
    assert {"확인", "교체", "아침식사", "송영"} <= mapped_canonical


def test_confirmed_history_is_only_a_weak_same_layout_signal() -> None:
    raw = (
        "가나다: 확인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n가나마: 라마바\n마바사: 가나다"
    )
    unrelated = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
        confirmed_history=[
            {
                "recognized_text": "가나마",
                "corrected_text": "가나다",
                "section": "특이사항",
                "layout_role": "inline_name",
            }
        ],
    )
    decision = next(item for item in unrelated.decisions if item.slot.raw == "가나마")
    assert decision.action == "preserve_uncertain"

    same_layout = analyze_combined_checklist(
        raw,
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
        confirmed_history=[
            {
                "recognized_text": "가나마",
                "corrected_text": "가나다",
                "section": "아침식사",
                "layout_role": "meal_name_list",
            }
        ],
    )
    promoted = next(item for item in same_layout.decisions if item.slot.raw == "가나마")
    assert promoted.action == "preserve_uncertain"
    assert promoted.candidate == "가나다"
    assert promoted.top_score is not None
    assert promoted.second_score is not None
    assert promoted.top_score > promoted.second_score
