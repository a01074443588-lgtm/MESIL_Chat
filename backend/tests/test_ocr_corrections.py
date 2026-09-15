from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageDraw

from app.ocr_corrections import (
    CorrectionEvidence,
    ResidentRosterEntry,
    apply_top_name_candidates_to_draft,
    build_correction_pairs,
    build_ocr_review_warnings,
    build_roster_aware_draft,
    detect_report_name_slots,
    extract_page_visual_signature,
    infer_resident_service_context,
    suggest_from_confirmed_events,
)


def test_top_name_candidates_are_applied_once_to_non_final_draft():
    raw = "가나타: 확인\n가나타: 교체\n나다라: 약 2회"
    candidates = [
        {
            "recognized": "가나타",
            "candidate": "가나다",
            "rank": 1,
            "slot_index": 1,
        },
        {
            "recognized": "가나타",
            "candidate": "가나마",
            "rank": 2,
            "slot_index": 1,
        },
        {
            "recognized": "가나타",
            "candidate": "가나라",
            "rank": 1,
            "slot_index": 2,
        },
        {
            "recognized": "이름 위치 3",
            "candidate": "다라마",
            "rank": 1,
            "slot_index": 3,
        },
    ]

    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=candidates,
    )

    assert raw == "가나타: 확인\n가나타: 교체\n나다라: 약 2회"
    assert draft == "가나다: 확인\n가나라: 교체\n나다라: 약 2회"
    assert [item["applied_to_draft"] for item in applications] == [True, True, False]


def test_replacement_does_not_retarget_an_earlier_candidate_value():
    raw = "가나타: 확인\n가나다: 교체"
    draft, _ = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "가나타",
                "candidate": "가나다",
                "rank": 1,
                "slot_index": 1,
            },
            {
                "recognized": "가나다",
                "candidate": "가나라",
                "rank": 1,
                "slot_index": 2,
            },
        ],
    )

    assert draft == "가나다: 확인\n가나라: 교체"


def test_near_equal_name_slot_counts_use_document_order_for_draft_only():
    raw = "가나타: 확인\n나다라: 교체"
    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "다른획",
                "candidate": "가나다",
                "rank": 1,
                "slot_index": 1,
            },
            {
                "recognized": "또다른획",
                "candidate": "나다마",
                "rank": 1,
                "slot_index": 2,
            },
            {
                "recognized": "추가이름",
                "candidate": "다라마",
                "rank": 1,
                "slot_index": 3,
            },
        ],
    )

    assert draft == "가나다: 확인\n나다마: 교체"
    assert [item["applied_to_draft"] for item in applications] == [True, True, False]


def test_image_region_candidate_stays_bound_to_its_explicit_name_slot():
    raw = (
        "가나타: 확인\n"
        "나다라: 확인\n"
        "다라머: 확인\n"
        "라마바: 확인\n"
        "본문에는 시각 판독 글자 모양이 따로 적혀 있음"
    )

    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "글자모양",
                "candidate": "다라마",
                "rank": 1,
                "slot_index": 3,
                "position_locked": True,
            }
        ],
    )

    assert draft == (
        "가나타: 확인\n"
        "나다라: 확인\n"
        "다라마: 확인\n"
        "라마바: 확인\n"
        "본문에는 시각 판독 글자 모양이 따로 적혀 있음"
    )
    assert applications[0]["recognized"] == "다라머"
    assert applications[0]["resolved_in_draft"] is True


def test_report_field_labels_are_not_detected_or_replaced_as_name_slots():
    raw = (
        "<아침식사 관련>\n"
        "식사량: 양호\n"
        "혈압: 120/80\n"
        "소변: 확인\n"
        "가나타: 확인\n"
        "나다라: 교체"
    )

    slots = detect_report_name_slots(raw)
    assert [slot.recognized for slot in slots] == ["가나타", "나다라"]

    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "흐린글자",
                "candidate": "가나다",
                "rank": 1,
                "slot_index": 1,
                "position_locked": True,
            }
        ],
        roster_names=["가나다"],
    )

    assert "식사량: 양호" in draft
    assert "혈압: 120/80" in draft
    assert "소변: 확인" in draft
    assert "가나다: 확인" in draft
    assert draft.endswith("나다라: 교체")
    assert applications[0]["resolved_in_draft"] is True


def test_name_candidate_outside_roster_never_enters_review_draft():
    raw = "가나타 어르신 확인"

    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "가나타",
                "candidate": "명단밖",
                "rank": 1,
                "slot_index": 1,
                "position_locked": True,
            }
        ],
        roster_names=["가나다"],
        unresolved_placeholder="이름 확인 필요",
    )

    assert draft == "이름 확인 필요 어르신 확인"
    assert "명단밖" not in draft
    assert applications[0]["application_reason"] == "unresolved_name_placeholder"


def test_invalid_locked_slot_never_retargets_matching_prose():
    raw = "가나타 어르신 확인\n본문의 글자모양은 그대로 유지"

    draft, applications = apply_top_name_candidates_to_draft(
        raw,
        candidates=[
            {
                "recognized": "글자모양",
                "candidate": "가나다",
                "rank": 1,
                "slot_index": 9,
                "position_locked": True,
            }
        ],
        roster_names=["가나다"],
        unresolved_placeholder="이름 확인 필요",
    )

    assert draft == "이름 확인 필요 어르신 확인\n본문의 글자모양은 그대로 유지"
    assert applications[0]["resolved_in_draft"] is False
    assert applications[0]["application_reason"] == "recognized_text_not_located"


def test_confirmed_event_returns_candidate_without_auto_applying():
    writer_id = uuid4()
    signature = [0.0, 0.6, 0.8]
    candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=writer_id,
        visual_signature=signature,
        evidences=[
            CorrectionEvidence(
                event_id=str(uuid4()),
                recognized_text="김성리",
                corrected_text="김성희",
                content_type="resident_name",
                context_text="김성리 어르신 오전 투약 확인",
                source_writer_id=str(writer_id),
                visual_signature=signature,
            )
        ],
        resident_names=["김성희"],
    )

    assert candidates
    assert candidates[0]["recognized"] == "김성리"
    assert candidates[0]["candidate"] == "김성희"
    assert candidates[0]["is_protected"] is True
    assert candidates[0]["auto_applicable"] is False
    assert "같은 작성자" in candidates[0]["reason"]
    assert "글씨 영역 유사" in candidates[0]["reason"]


def test_candidate_id_stays_stable_when_best_evidence_changes():
    first_event_id = str(uuid4())
    second_event_id = str(uuid4())
    common_evidence = {
        "recognized_text": "김성리",
        "corrected_text": "김성희",
        "content_type": "resident_name",
        "context_text": "김성리 어르신 오전 투약 확인",
        "source_writer_id": None,
        "visual_signature": None,
    }

    first_candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[
            CorrectionEvidence(event_id=first_event_id, **common_evidence),
        ],
        resident_names=["김성희"],
    )
    reinforced_candidates = suggest_from_confirmed_events(
        "김성리 어르신 오전 투약 확인",
        context_text="김성리 어르신 오전 투약 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[
            CorrectionEvidence(event_id=second_event_id, **common_evidence),
            CorrectionEvidence(event_id=first_event_id, **common_evidence),
        ],
        resident_names=["김성희"],
    )

    assert first_candidates[0]["id"] == reinforced_candidates[0]["id"]


def test_review_diff_classifies_name_time_and_keeps_raw_separate():
    raw_text = "김성리 어르신 9:05 투약 확인"
    corrected_text = "김성희 어르신 9:15 투약 확인"

    pairs = build_correction_pairs(
        raw_text,
        corrected_text,
        resident_names=["김성희"],
    )

    assert raw_text == "김성리 어르신 9:05 투약 확인"
    assert {
        (pair["recognized_text"], pair["corrected_text"], pair["content_type"])
        for pair in pairs
    } == {
        ("김성리", "김성희", "resident_name"),
        ("9:05", "9:15", "time"),
    }


def test_page_visual_signature_contains_only_small_numeric_feature(tmp_path: Path):
    image_path = tmp_path / "handwriting.png"
    image = Image.new("RGB", (240, 120), "white")
    draw = ImageDraw.Draw(image)
    draw.line((20, 40, 210, 80), fill="black", width=5)
    image.save(image_path)

    signature = extract_page_visual_signature(image_path)

    assert signature is not None
    assert len(signature) == 48 * 16
    assert all(isinstance(value, float) for value in signature)


def test_resident_name_history_is_limited_to_current_service_roster():
    evidence = CorrectionEvidence(
        event_id=str(uuid4()),
        recognized_text="김성리",
        corrected_text="김성희",
        content_type="resident_name",
        context_text="김성리 어르신 상태 확인",
        source_writer_id=None,
        visual_signature=None,
        service_context="daycare",
        source_kind="audio",
    )

    blocked = suggest_from_confirmed_events(
        "김성리 어르신 상태 확인",
        context_text="김성리 어르신 상태 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[evidence],
        resident_names=["김성희"],
        service_context="facility",
        source_kind="audio",
    )
    allowed = suggest_from_confirmed_events(
        "김성리 어르신 상태 확인",
        context_text="김성리 어르신 상태 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[evidence],
        resident_names=["김성희"],
        service_context="daycare",
        source_kind="audio",
    )

    assert blocked == []
    assert allowed[0]["candidate"] == "김성희"
    assert "같은 서비스" in allowed[0]["reason"]


def test_repeated_confirmed_candidate_is_sorted_first():
    repeated = {
        "recognized_text": "박수니",
        "corrected_text": "박순이",
        "content_type": "resident_name",
        "context_text": "박수니 어르신 상태 확인",
        "source_writer_id": None,
        "visual_signature": None,
        "service_context": "facility",
        "source_kind": "audio",
    }
    candidates = suggest_from_confirmed_events(
        "김성리 박수니 어르신 상태 확인",
        context_text="김성리 박수니 어르신 상태 확인",
        source_writer_id=None,
        visual_signature=None,
        evidences=[
            CorrectionEvidence(
                event_id=str(uuid4()),
                recognized_text="김성리",
                corrected_text="김성희",
                content_type="resident_name",
                context_text="김성리 어르신 상태 확인",
                source_writer_id=None,
                visual_signature=None,
                service_context="facility",
                source_kind="audio",
            ),
            CorrectionEvidence(event_id=str(uuid4()), **repeated),
            CorrectionEvidence(event_id=str(uuid4()), **repeated),
        ],
        resident_names=["김성희", "박순이"],
        service_context="facility",
        source_kind="audio",
    )

    assert candidates[0]["candidate"] == "박순이"
    assert candidates[0]["support_count"] == 2
    assert candidates[0]["reason"].startswith("반복 확인 2건")


def test_service_context_is_inferred_from_multiple_exact_roster_names():
    entries = [
        ResidentRosterEntry("가상갷", "daycare"),
        ResidentRosterEntry("가상걉", "daycare"),
        ResidentRosterEntry("김영수", "facility"),
        ResidentRosterEntry("가상건", "homecare"),
    ]

    service, confidence = infer_resident_service_context(
        "가상갷: 확인\n가상걉: 교체",
        roster_entries=entries,
    )

    assert service == "daycare"
    assert confidence >= 0.8


def test_shared_name_does_not_force_service_context():
    entries = [
        ResidentRosterEntry("가상걖", "facility"),
        ResidentRosterEntry("가상걖", "daycare"),
        ResidentRosterEntry("가상걖", "homecare"),
    ]

    assert infer_resident_service_context(
        "가상걖 어르신 상태 확인",
        roster_entries=entries,
    ) == (None, 0.0)


def test_roster_aware_draft_changes_only_unambiguous_name_slots():
    raw = (
        "시험갑: 교체, 교체\n"
        "검증을: 확인, 확인\n"
        "가상멍: 확인, 라인\n"
        "특이사항: 혈압 144, 72, 72"
    )
    entries = [
        ResidentRosterEntry("시험갑", "daycare"),
        ResidentRosterEntry("검증을", "daycare"),
        ResidentRosterEntry("가상명", "daycare"),
        ResidentRosterEntry("합성철", "daycare"),
        ResidentRosterEntry("가상면", "facility"),
    ]

    draft = build_roster_aware_draft(raw, roster_entries=entries)

    assert draft.service_context == "daycare"
    assert draft.text == raw
    assert "특이사항: 혈압 144, 72, 72" in draft.text
    assert draft.corrections[0]["recognized"] == "가상멍"
    assert draft.corrections[0]["candidate"] == "가상명"
    assert draft.corrections[0]["auto_applicable"] is False
    assert "daycare 범위" in draft.corrections[0]["reason"]


def test_roster_aware_draft_keeps_ambiguous_name_unchanged():
    raw = "가상객 어르신 확인"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("김순옥", "daycare"),
            ResidentRosterEntry("김순임", "daycare"),
        ],
        room_service_context="daycare",
    )

    assert draft.text == raw
    assert {item["candidate"] for item in draft.corrections} == {
        "김순옥",
        "김순임",
    }
    assert all(item["auto_applicable"] is False for item in draft.corrections)
    assert all("원본 확인 필요" in item["reason"] for item in draft.corrections)


def test_confirmed_same_handwriting_history_can_strengthen_name_draft():
    writer_id = uuid4()
    signature = [0.0, 0.6, 0.8]
    raw = "이우금 어르신 상태 확인"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[ResidentRosterEntry("가상걽", "daycare")],
        room_service_context="daycare",
        source_writer_id=writer_id,
        visual_signature=signature,
        evidences=[
            CorrectionEvidence(
                event_id=str(uuid4()),
                recognized_text="이우금",
                corrected_text="가상걽",
                content_type="resident_name",
                context_text=raw,
                source_writer_id=str(writer_id),
                visual_signature=signature,
                service_context="daycare",
                source_kind="image",
                section=None,
                layout_role="resident_suffix",
            )
        ],
    )

    assert draft.text == raw
    assert draft.corrections[0]["candidate"] == "가상걽"
    assert draft.corrections[0]["support_count"] == 1


def test_none_service_uses_full_roster_fallback_without_changing_raw():
    raw = "김종숙 어르신 상태 확인"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가상갯", "daycare"),
            ResidentRosterEntry("가상갬", "facility"),
            ResidentRosterEntry("김종순", "homecare"),
        ],
    )

    assert draft.service_context is None
    assert draft.text == raw
    assert draft.corrections
    assert "가상갯" in {item["candidate"] for item in draft.corrections}
    assert all(
        "전체 활성 명단 fallback" in item["reason"]
        for item in draft.corrections
    )


def test_bullet_delimited_report_name_is_treated_as_name_position():
    raw = "· 이애자 · 기저귀 확인"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가상걻", "daycare"),
            ResidentRosterEntry("가상걺", "facility"),
        ],
    )

    assert draft.text == raw
    assert draft.corrections
    assert all(item["recognized"] == "이애자" for item in draft.corrections)


def test_exact_staff_correction_is_reused_as_first_candidate_for_same_writer():
    writer_id = uuid4()
    signature = [0.1, 0.7, 0.2]
    raw = "가상걹: 확인\n가나다: 교체"
    draft = build_roster_aware_draft(
        raw,
        roster_entries=[
            ResidentRosterEntry("가상걻", "daycare"),
            ResidentRosterEntry("가상걺", "facility"),
            ResidentRosterEntry("가상늠", "homecare"),
        ],
        source_writer_id=writer_id,
        visual_signature=signature,
        evidences=[
            CorrectionEvidence(
                event_id=str(uuid4()),
                recognized_text="가상걹",
                corrected_text="가상걻",
                content_type="resident_name",
                context_text=raw,
                source_writer_id=str(writer_id),
                visual_signature=signature,
                source_kind="image",
                section=None,
                layout_role="label_value_row",
            )
        ],
    )

    assert draft.text == raw
    assert draft.corrections[0]["candidate"] == "가상걻"
    assert draft.corrections[0]["support_count"] == 1
    assert "직원 확정 이력 1건" in draft.corrections[0]["reason"]


def test_review_warnings_flag_risky_fields_without_rewriting_text():
    raw = "이애자: 기저귀 확인, 9:05 약 복용, 만두 정도 5됨."
    warnings = build_ocr_review_warnings(raw)

    assert raw == "이애자: 기저귀 확인, 9:05 약 복용, 만두 정도 5됨."
    assert any("숫자" in warning for warning in warnings)
    assert any("약" in warning for warning in warnings)
    assert any("기저귀" in warning for warning in warnings)
    assert any("어색한 숫자" in warning for warning in warnings)
