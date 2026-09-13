from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from app.ocr import image_likely_contains_text
from app.ocr_corrections import (
    CorrectionEvidence,
    ResidentRosterEntry,
    apply_safe_context_corrections_to_draft,
    apply_top_name_candidates_to_draft,
    build_roster_aware_draft,
    detect_report_name_slots,
    rank_visual_name_candidates,
)


def test_low_confidence_service_hint_keeps_full_active_roster_fallback() -> None:
    raw = "센터 송영\n가나타 어르신 확인"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가나다", "daycare"),
            ResidentRosterEntry("가나마", "facility"),
            ResidentRosterEntry("가나라", "homecare"),
        ],
    )

    assert draft.service_context is None
    assert draft.service_confidence == 0.0
    assert {row["candidate"] for row in draft.corrections} == {
        "가나다",
        "가나마",
        "가나라",
    }
    assert all("전체 활성 명단 fallback" in row["reason"] for row in draft.corrections)


def test_meal_section_repeated_rows_use_roster_or_unresolved_placeholder() -> None:
    raw = (
        "[아침식사]\n가너다  확인\n라미바  확인\n누구지  확인\n[특이사항]\n혈압: 120/80"
    )
    roster_names = ["가나다", "라마바"]
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가나다", "daycare"),
            ResidentRosterEntry("라마바", "daycare"),
        ],
        room_service_context="daycare",
        source_writer_id="writer-a",
        evidences=[
            CorrectionEvidence(
                event_id="event-a",
                recognized_text="가너다",
                corrected_text="가나다",
                content_type="resident_name",
                context_text="아침식사",
                source_writer_id="writer-a",
                visual_signature=None,
                source_kind="image",
                section="아침식사",
                layout_role="section_repeated_row",
            ),
            CorrectionEvidence(
                event_id="event-b",
                recognized_text="라미바",
                corrected_text="라마바",
                content_type="resident_name",
                context_text="아침식사",
                source_writer_id="writer-a",
                visual_signature=None,
                source_kind="image",
                section="아침식사",
                layout_role="section_repeated_row",
            ),
        ],
    )

    slots = detect_report_name_slots(raw)
    meal_slots = [slot for slot in slots if slot.section == "아침식사"]
    assert [slot.recognized for slot in meal_slots] == ["가너다", "라미바", "누구지"]
    assert all(slot.layout_role == "section_repeated_row" for slot in meal_slots)

    auto_draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=draft.corrections,
        roster_names=roster_names,
        unresolved_placeholder="이름 확인 필요",
    )

    assert "가나다  확인" in auto_draft
    assert "라마바  확인" in auto_draft
    assert "이름 확인 필요  확인" in auto_draft
    assert "누구지" not in auto_draft
    assert "혈압: 120/80" in auto_draft
    assert any(
        row["application_reason"] == "unresolved_name_placeholder"
        for row in applications
    )


def test_only_allowlisted_context_phrases_change_and_numbers_stay_exact() -> None:
    raw = "수동식 전차 윤전길 선생 혈압 120/80, 체온 36.5, 9:05"

    draft, applications = apply_safe_context_corrections_to_draft(
        raw,
        roster_names=["가나다", "라미바", "다라머"],
    )

    assert raw == "수동식 전차 윤전길 선생 혈압 120/80, 체온 36.5, 9:05"
    assert draft == "아침 송영 시 운전원 선생 혈압 120/80, 체온 36.5, 9:05"
    assert {row["recognized"] for row in applications} == {
        "수동식 전차",
        "윤전길 선생",
    }
    assert "120/80" in draft
    assert "36.5" in draft
    assert "9:05" in draft


def test_quick_image_text_detection_distinguishes_blank_and_text(
    tmp_path: Path,
) -> None:
    blank_path = tmp_path / "blank.png"
    text_path = tmp_path / "text.png"
    Image.new("RGB", (640, 480), "white").save(blank_path)

    text_image = Image.new("RGB", (640, 480), "white")
    draw = ImageDraw.Draw(text_image)
    for line_no in range(8):
        draw.text(
            (40, 30 + (line_no * 45)),
            f"REPORT LINE {line_no + 1}: 1234567890",
            fill="black",
        )
    text_image.save(text_path)

    assert image_likely_contains_text(blank_path) is False
    assert image_likely_contains_text(text_path) is True


def test_name_rows_accept_bullets_hyphens_and_short_bracket_notes() -> None:
    slots = detect_report_name_slots(
        "* 가나다\n• 라마바- 상태 확인\n다라마[오전]: 교체\n라마바사 - 사아자"
    )

    assert [slot.recognized for slot in slots] == [
        "가나다",
        "라마바",
        "라마바사",
        "사아자",
    ]


def test_breakfast_heading_treats_bare_name_pairs_as_name_slots() -> None:
    raw = "<아침식사 하신분>\n가너다, 라미바\n다라머  사아자\n<특이사항>\n상태 확인"

    slots = detect_report_name_slots(raw)
    breakfast = [slot for slot in slots if slot.section == "아침식사"]

    assert [slot.recognized for slot in breakfast] == [
        "가너다",
        "라미바",
        "다라머",
        "사아자",
    ]
    assert all(slot.layout_role == "meal_name_list" for slot in breakfast)


def test_angle_bracket_meal_heading_treats_both_colon_columns_as_names() -> None:
    raw = (
        "〈아침식사 관련〉\n"
        "가너다 : 라미바\n"
        "다라머 : 사아자\n"
        "〈특이사항〉\n"
        "혈압: 120/80"
    )

    breakfast = [
        slot for slot in detect_report_name_slots(raw) if slot.section == "아침식사"
    ]

    assert [slot.recognized for slot in breakfast] == [
        "가너다", "라미바", "다라머", "사아자"
    ]
    assert all(slot.layout_role == "meal_name_list" for slot in breakfast)


def test_meal_name_cell_recovers_a_spaced_food_like_ocr_surface() -> None:
    raw = "〈아침식사 관련〉\n가너다 : 흰 밥죽\n다라머 : 사아자"

    breakfast = [
        slot for slot in detect_report_name_slots(raw) if slot.section == "아침식사"
    ]

    assert [slot.recognized for slot in breakfast] == [
        "가너다",
        "흰밥죽",
        "다라머",
        "사아자",
    ]
    assert all(slot.layout_role == "meal_name_list" for slot in breakfast)


def test_meal_name_columns_leave_no_outside_or_masked_name_in_draft() -> None:
    raw = "〈아침식사 하신 분〉\n가너다 : 김*순\n누구지 : 라미바"
    roster_names = ["가나다", "김마순", "라마바"]
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[ResidentRosterEntry(name, "daycare") for name in roster_names],
        room_service_context="daycare",
    )

    auto_draft, _ = apply_top_name_candidates_to_draft(
        raw,
        candidates=draft.corrections,
        roster_names=roster_names,
        unresolved_placeholder="이름 확인 필요",
    )

    assert "가너다" not in auto_draft
    assert "김*순" not in auto_draft
    assert "누구지" not in auto_draft
    assert "이름 확인 필요" in auto_draft


def test_visual_name_ranking_prefers_close_roster_name_only_when_clear() -> None:
    ranked = rank_visual_name_candidates(
        "고상렬",
        roster_names=["고승렬", "가상갱", "고영숙"],
        visual_candidates=["고승렬", "가상갱"],
        layout_role="meal_name_list",
        column_role="left",
        row_sequence=1,
        row_group_size=3,
        service_confidence=0.9,
    )

    assert ranked[0]["candidate"] == "고승렬"
    assert ranked[0]["candidate_is_clear"] is True

    unstructured = rank_visual_name_candidates(
        "고상렬",
        roster_names=["고승렬", "가상갱", "고영숙"],
        visual_candidates=["고승렬", "가상갱"],
        layout_role="inline_name",
    )
    assert unstructured[0]["candidate"] == "고승렬"
    assert unstructured[0]["candidate_is_clear"] is False
    assert ranked[0]["surface_similarity"] > ranked[1]["surface_similarity"]


def test_visual_name_ranking_marks_ambiguous_or_dissimilar_read_unresolved() -> None:
    ambiguous = rank_visual_name_candidates(
        "가나마",
        roster_names=["가나다", "가나라"],
        visual_candidates=["가나다", "가나라"],
    )
    dissimilar = rank_visual_name_candidates(
        "흰쌀밥",
        roster_names=["가나다", "라마바"],
        visual_candidates=["라마바"],
    )

    assert ambiguous[0]["candidate_is_clear"] is False
    assert dissimilar[0]["candidate_is_clear"] is False


def test_name_history_is_only_weak_evidence_in_same_section_and_row_grammar() -> None:
    raw = "〈아침식사 하신 분〉\n가너다 : 라마바"
    common = {
        "event_id": "event-a",
        "recognized_text": "가너다",
        "corrected_text": "가나다",
        "content_type": "resident_name",
        "context_text": raw,
        "source_writer_id": "writer-a",
        "visual_signature": None,
        "source_kind": "image",
        "section": "아침식사",
    }
    entries = [
        ResidentRosterEntry("가나다", "daycare"),
        ResidentRosterEntry("가나라", "daycare"),
    ]
    same_context = build_roster_aware_draft(
        raw,
        roster_entries=entries,
        room_service_context="daycare",
        source_writer_id="writer-a",
        evidences=[CorrectionEvidence(**common, layout_role="meal_name_list")],
    )
    other_context = build_roster_aware_draft(
        raw,
        roster_entries=entries,
        room_service_context="daycare",
        source_writer_id="writer-a",
        evidences=[CorrectionEvidence(**common, layout_role="label_value_row")],
    )

    assert same_context.corrections[0]["support_count"] == 1
    assert all(row["support_count"] == 0 for row in other_context.corrections)


def test_general_label_value_row_never_treats_right_value_as_name() -> None:
    slots = detect_report_name_slots("〈특이사항〉\n가나다 : 라마바")

    assert slots == []


def test_repeated_status_table_keeps_unknown_cells_without_treating_prose_as_rows() -> None:
    raw = (
        "가나다: 흑인, 교체\n"
        "라마바: 흐림\n"
        "다라마: 확인, 개인\n"
        "사아자: 미상\n"
        "설명문: 오늘 상태를 길게 기록함"
    )

    slots = detect_report_name_slots(raw)
    draft, _ = apply_safe_context_corrections_to_draft(raw)

    assert [slot.recognized for slot in slots] == [
        "가나다", "라마바", "다라마", "사아자"
    ]
    assert all(slot.row_group_size == 4 for slot in slots)
    assert "가나다: 확인, 교체" in draft
    assert "라마바: 흐림" in draft
    assert "설명문: 오늘 상태를 길게 기록함" in draft


def test_meal_name_list_records_columns_continuity_and_keeps_ties_unresolved() -> None:
    raw = (
        "〈아침식사 관련〉\n"
        "가나마 : 라미바\n"
        "다라머 : 사아자\n"
        "마바서 : 자차카"
    )
    slots = detect_report_name_slots(raw)
    meal_slots = [slot for slot in slots if slot.layout_role == "meal_name_list"]

    assert [slot.column_role for slot in meal_slots] == [
        "left", "right", "left", "right", "left", "right"
    ]
    assert [slot.row_sequence for slot in meal_slots] == [1, 1, 2, 2, 3, 3]
    assert all(slot.row_group_size == 3 for slot in meal_slots)

    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가나다", "daycare"),
            ResidentRosterEntry("가나라", "daycare"),
            ResidentRosterEntry("라마바", "daycare"),
        ],
        room_service_context="daycare",
    )
    first_slot = [
        row for row in draft.corrections if row["slot_index"] == 1
    ]
    assert first_slot
    assert first_slot[0]["column_role"] == "left"
    assert first_slot[0]["row_group_size"] == 3
    assert first_slot[0]["candidate_is_clear"] is False


def test_meal_heading_misread_restores_section_and_keeps_real_diarrhea_prose() -> None:
    raw = (
        "<아침 설사 확인>\n"
        "가나다, 라미바\n"
        "다라머\n"
        "<특이사항>\n"
        "아침식사 후 설사를 하심\n"
        "아침에 설사를 하시고 휴식함"
    )

    slots = detect_report_name_slots(raw)
    breakfast = [slot for slot in slots if slot.section == "아침식사"]
    draft, applications = apply_safe_context_corrections_to_draft(raw)

    assert [slot.recognized for slot in breakfast] == ["가나다", "라미바", "다라머"]
    assert all(slot.layout_role == "meal_name_list" for slot in breakfast)
    assert "<아침식사 확인>" in draft
    assert "아침식사 후 설사를 하심" in draft
    assert "아침에 설사를 하시고 휴식함" in draft
    heading_application = next(
        row for row in applications if row["source"] == "contextual_heading_lexicon"
    )
    assert heading_application["recognized"] == "아침 설사"
    assert heading_application["candidate"] == "아침식사"
    assert heading_application["section"] == "아침식사"
    assert heading_application["layout_role"] == "report_heading"
    assert heading_application["review_required"] is True


def test_lunch_and_dinner_heading_misreads_only_change_on_heading_lines() -> None:
    raw = (
        "점심 설사 확인\n"
        "가나다, 라미바\n"
        "저녁설사 하신분\n"
        "다라머, 사아자\n"
        "점심식사 후 설사를 하심\n"
        "저녁에 설사를 하시고 휴식함"
    )

    draft, _ = apply_safe_context_corrections_to_draft(
        raw,
        roster_names=["가나다", "라미바", "다라머", "사아자"],
    )

    assert draft.splitlines() == [
        "점심식사 확인",
        "가나다, 라미바",
        "저녁식사 하신분",
        "다라머, 사아자",
        "점심식사 후 설사를 하심",
        "저녁에 설사를 하시고 휴식함",
    ]


def test_awkward_diarrhea_heading_without_table_structure_stays_unconfirmed() -> None:
    raw = "아침 설사 확인\n아침에 설사를 하시고 휴식함"

    slots = detect_report_name_slots(raw)
    draft, applications = apply_safe_context_corrections_to_draft(raw)

    assert draft == raw
    assert not [slot for slot in slots if slot.section == "아침식사"]
    assert not any(
        row.get("rule_id") == "meal_heading_time_diarrhea_to_meal"
        for row in applications
    )


def test_confirmed_memory_never_changes_one_canonical_care_fact_into_another() -> None:
    raw = "<아침식사 관련>\n아침에 설사를 하시고 휴식함"

    draft, applications = apply_safe_context_corrections_to_draft(
        raw,
        source_writer_id="writer-a",
        evidences=[
            CorrectionEvidence(
                event_id="event-a",
                recognized_text="식사",
                corrected_text="설사",
                content_type="general",
                context_text="과거 단일 보고 수정",
                source_writer_id="writer-a",
                visual_signature=None,
            )
        ],
    )

    assert draft == raw
    assert not any(
        row.get("recognized") == "식사"
        and row.get("candidate") == "설사"
        and row.get("applied_to_draft") is True
        for row in applications
    )
    assert any(
        row.get("recognized") == "식사"
        and row.get("candidate") == "설사"
        and row.get("application_reason") == "context_mismatch_ignored"
        for row in applications
    )


def test_confirmed_token_memory_is_not_reused_in_a_different_report_context() -> None:
    raw = "<특이사항>\n문안 상태를 기록함"

    draft, applications = apply_safe_context_corrections_to_draft(
        raw,
        source_writer_id="writer-a",
        evidences=[
            CorrectionEvidence(
                event_id="event-a",
                recognized_text="문안",
                corrected_text="확인",
                content_type="general",
                context_text="과거 기저귀 확인표",
                source_writer_id="writer-a",
                visual_signature=None,
            )
        ],
    )

    assert draft == raw
    assert any(
        row.get("recognized") == "문안"
        and row.get("candidate") == "확인"
        and row.get("application_reason") == "context_mismatch_ignored"
        for row in applications
    )


def test_status_cell_corrects_common_check_misread_without_touching_prose() -> None:
    raw = (
        "가나다: 흑인, 교체\n"
        "라마바: 확인, 개인\n"
        "안내문에는 흑인 어르신이라는 표현이 있음"
    )

    draft, applications = apply_safe_context_corrections_to_draft(raw)

    assert draft == (
        "가나다: 확인, 교체\n"
        "라마바: 확인, 개인\n"
        "안내문에는 흑인 어르신이라는 표현이 있음"
    )
    assert any(
        row["recognized"] == "흑인"
        and row["candidate"] == "확인"
        and row["source"] == "contextual_lexicon"
        for row in applications
    )
