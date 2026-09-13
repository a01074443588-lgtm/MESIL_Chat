from __future__ import annotations

import json
from pathlib import Path

from app.ocr_template_engine import VisualNameObservation
from app.ocr_versioned_pipeline import (
    CATALOG_PATH,
    EvaluationCase,
    FrozenContentOcr,
    evaluate_name_profile,
    load_pipeline_catalog,
    run_versioned_name_stage,
    run_versioned_structured_stage,
    serialize_versioned_name_run,
    serialize_versioned_structured_run,
)

FIXTURE_PATH = (
    Path(__file__).with_name("fixtures") / "ocr_profile_evaluation_matrix.v1.json"
)


def _cases() -> list[EvaluationCase]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return [
        EvaluationCase(
            case_id=item["id"],
            content_text=item["content_text"],
            roster=tuple(item["roster"]),
            expected_names=tuple(item["expected_names"]),
            observations=tuple(
                VisualNameObservation(
                    slot_index=value["slot_index"],
                    recognized=value["recognized"],
                    candidates=tuple(value["candidates"]),
                    normalized_bbox=tuple(value["bbox"]),
                )
                for value in item["observations"]
            ),
        )
        for item in payload["regression_cases"]
    ]


def test_catalog_is_additive_versioned_and_falls_back_to_content() -> None:
    catalog = load_pipeline_catalog()
    assert CATALOG_PATH.is_file()
    assert catalog["policy"] == {
        "current_public_pipeline_frozen": True,
        "content_text_must_be_immutable_in_name_stage": True,
        "existing_profiles_are_additive_only": True,
        "fallback": "content_ocr_only",
    }
    assert {item["source_id"] for item in catalog["preserved_sources"]} == {
        "long-term-care-lexicon",
        "legacy-roster-aware-corrections",
        "staff-confirmed-history",
    }


def test_name_stage_never_changes_status_time_or_narrative() -> None:
    content = FrozenContentOcr(
        "가나마: 흑인\n라마바: 교체\n\n"
        "<아침식사 하신 분>\n마바사: 라마바\n\n"
        "09:05 본문에서 흑인이라는 원문은 보존"
    )
    run = run_versioned_name_stage(
        content,
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
        observations=[VisualNameObservation(1, "가나다", ("가나다", "가나라"), None)],
    )
    assert run.content.text == content.text
    assert run.content.sha256 == content.sha256
    assert "가나다 (?)" in run.name_draft
    assert "흑인" in run.name_draft
    assert "09:05 본문에서 흑인이라는 원문은 보존" in run.name_draft
    assert run.safety["non_name_text_preserved"] is True
    assert run.safety["outside_roster_candidate_count"] == 0


def test_non_template_uses_content_only_fallback_without_name_slots() -> None:
    text = "아침에 설사를 하시고 09:40 상태를 확인함.\n첫 단어는 이름이 아님."
    run = run_versioned_name_stage(
        FrozenContentOcr(text), active_roster_names=["가나다", "라마바"]
    )
    assert run.matched is False
    assert run.fallback_used is True
    assert run.name_draft == text
    assert run.slot_outputs == ()


def test_structured_stage_changes_only_confirmed_status_and_name_slots() -> None:
    text = (
        "가나다: 문안, 교체\n"
        "라마바: 확인, 흑인\n\n"
        "<아침식사 하신 분>\n가나다: 라마바\n마바사: 가나다\n\n"
        "09:05 특이사항에는 문안과 흑인이라는 OCR 원문을 그대로 기록함"
    )
    content = FrozenContentOcr(text)
    run = run_versioned_structured_stage(
        content,
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

    assert run.content.text == text
    assert run.suggested_draft.startswith(
        "가나다: 확인, 교체\n라마바: 확인, 확인"
    )
    assert "09:05 특이사항에는 문안과 흑인이라는 OCR 원문을 그대로 기록함" in (
        run.suggested_draft
    )
    assert run.safety["outside_structured_slots_preserved"] is True
    assert run.safety["status_slot_count"] == 4
    assert run.safety["status_correction_count"] == 2

    details = serialize_versioned_structured_run(run)
    serialized = json.dumps(details, ensure_ascii=False)
    assert details[0]["status_stage_id"] == "confirmed-status-slot-correction"
    assert "문안" not in serialized
    assert "흑인" not in serialized
    assert text not in serialized


def test_structured_stage_keeps_generic_colon_and_prose_byte_identical() -> None:
    text = "담당자: 문안 필요\n09:05 본문에서 흑인이라는 OCR 원문은 보존"
    run = run_versioned_structured_stage(
        FrozenContentOcr(text),
        active_roster_names=["가나다", "라마바"],
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

    assert run.matched is False
    assert run.fallback_used is True
    assert run.suggested_draft == text
    assert run.name_outputs == ()
    assert run.status_outputs == ()


def test_structured_stage_recovers_all_misread_status_cells_without_touching_prose() -> (
    None
):
    text = (
        ". 가나다: 고세, 고레\n"
        ". 라마바: 락인, 고세\n"
        ". 마바사: 라이, 락인\n"
        ". 다라마: 고레, 라이\n\n"
        "09:05 일반 문장에는 락인과 고세를 그대로 보존함"
    )
    run = run_versioned_structured_stage(
        FrozenContentOcr(text),
        active_roster_names=["가나다", "라마바", "마바사", "다라마"],
    )

    assert run.matched is True
    assert run.name_outputs == ()
    assert run.safety["status_slot_count"] == 8
    assert run.safety["status_correction_count"] == 8
    assert "09:05 일반 문장에는 락인과 고세를 그대로 보존함" in (
        run.suggested_draft
    )
    assert run.content.text == text


def test_serialized_audit_has_versions_but_no_names_or_source_text() -> None:
    text = "가나마: 확인\n라마바: 교체\n\n<아침식사 관련>\n마바사: 라마바"
    run = run_versioned_name_stage(
        FrozenContentOcr(text),
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
    )
    details = serialize_versioned_name_run(run)
    serialized = json.dumps(details, ensure_ascii=False)
    assert details[0]["kind"] == "versioned_ocr_pipeline"
    assert details[0]["non_name_text_preserved"] is True
    assert details[0]["auto_apply_allowed"] is False
    assert "가나마" not in serialized
    assert "라마바" not in serialized
    assert text not in serialized


def test_ambiguous_confirmed_name_slot_becomes_marker_not_wrong_person() -> None:
    text = "가나마: 확인\n라마바: 교체\n\n<아침식사 관련>\n마바자: 라마바"
    run = run_versioned_name_stage(
        FrozenContentOcr(text),
        active_roster_names=["가나다", "가나라", "라마바", "마바사"],
        observations=[
            VisualNameObservation(1, "가나마", ("가나다", "가나라"), None),
            VisualNameObservation(3, "전혀다름", ("마바사", "라마바"), None),
        ],
    )
    assert run.slot_outputs[0].output == "(?)"
    assert run.slot_outputs[2].output == "(?)"
    assert not any(
        item.candidate not in {"가나다", "가나라", "라마바", "마바사"}
        for item in run.slot_outputs
        if item.candidate is not None
    )


def test_matrix_covers_all_reviewed_pages_and_reports_safety_metrics() -> None:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert not any(payload["privacy"].values())
    assert payload["source_scope"]["reviewed_image_count"] == 25
    assert sum(item["pages"] for item in payload["template_coverage"]) == 25
    assert sum(item["name_slots"] for item in payload["template_coverage"]) == 260
    metrics = evaluate_name_profile(_cases())
    assert metrics.case_count == 3
    assert metrics.non_name_text_changes == 0
    assert metrics.outside_roster_candidates == 0
    assert metrics.non_name_false_positives == 0
    assert metrics.wrong_substitutions == 0
    assert metrics.baseline_exact_name_slots == 5
    assert metrics.exact_name_slots == 6
    assert metrics.exact_name_slot_delta == 1
    assert metrics.regressed_cases == 0
    assert metrics.passes_regression_gate is True


def test_unknown_profile_cannot_silently_replace_fallback() -> None:
    try:
        run_versioned_name_stage(
            FrozenContentOcr("원문"),
            active_roster_names=[],
            profile_id="missing.profile",
        )
    except ValueError as exc:
        assert "Unknown OCR name profile" in str(exc)
    else:
        raise AssertionError("unknown profile must fail closed")
