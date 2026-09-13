from __future__ import annotations

import json
from pathlib import Path

from scripts.run_longitudinal_multimodal_validation import (
    ALIAS,
    _character_accuracy,
    _correction_prompt,
    _preservation,
    _redact,
    _unsupported_numeric_tokens,
)


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "longitudinal_multimodal_v1"


def _load(name: str) -> dict:
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_real_handwriting_fixture_is_internal_only_and_not_machine_font() -> None:
    manifest = _load("manifest.json")
    truth = _load("ground_truth.json")

    assert manifest["handwriting_evidence"]["case_count"] == 5
    assert manifest["audio_evidence"]["case_count"] == 2
    assert manifest["handwriting_evidence"]["source_kind"] == (
        "user_provided_actual_handwriting_photo"
    )
    assert manifest["handwriting_evidence"]["machine_font_accuracy_claim_allowed"] is False
    assert manifest["privacy"] == {
        "synthetic_fixture": True,
        "official_record": False,
        "external_transfer_allowed": False,
        "contains_direct_identifier_in_source_media": True,
        "tracked_results_must_be_deidentified": True,
        "processing_scope": [
            "x370d_internal_qwen3_vl",
            "isolated_dev_faster_whisper",
        ],
    }
    assert truth["ground_truth_is_model_input"] is False
    assert len(truth["cases"]) == 5


def test_revision_and_audio_modes_keep_staff_approval_pending() -> None:
    manifest = _load("manifest.json")

    assert manifest["revision_contract"]["append_only_stages"] == [
        "initial_ocr",
        "audio_transcript",
        "ai_correction_suggestion",
        "staff_approved_final",
    ]
    assert manifest["revision_contract"]["unapproved_final_must_be_null"] is True
    mappings = manifest["audio_evidence"]["mappings"]
    assert [item["correction_mode"] for item in mappings] == [
        "full_reading",
        "story_hint",
    ]


def test_redaction_removes_identity_header_and_direct_name() -> None:
    redacted = _redact(
        "검증용 가상자료 시험대상\n6월 25일 오전 9시 10분\n시 험 대 상 어르신",
        "시험대상",
        remove_identity_header=True,
    )

    assert "시험대상" not in redacted
    assert "시 험 대 상" not in redacted
    assert "검증용 가상자료" not in redacted
    assert ALIAS in redacted
    assert redacted.startswith("6월 25일")

    near_name = _redact(
        "김삼년 어르신 2월 3일 물을 제공했습니다.",
        "시험대상",
        remove_identity_header=False,
    )
    assert "김삼년" not in near_name
    assert near_name.startswith(f"{ALIAS} 어르신")


def test_character_and_separate_fact_preservation_metrics() -> None:
    candidate = "오후 2시 10분 물 200mL 제공함\n오후 4시 5분 확인함\n섭취 완료"
    reference = candidate
    terms = {
        "time": [["2시10분", "2시 10분"], ["4시5분", "4시 5분"]],
        "quantity": [["200mL"]],
        "unit": [["mL"]],
        "completion_status": [["섭취 완료"]],
        "negation": [],
    }

    assert _character_accuracy(candidate, reference) == 1.0
    preservation = _preservation(candidate, terms)
    assert preservation["time"]["rate"] == 1.0
    assert preservation["quantity"]["rate"] == 1.0
    assert preservation["unit"]["rate"] == 1.0
    assert preservation["completion_status"]["rate"] == 1.0
    assert preservation["negation"]["rate"] is None
    assert _unsupported_numeric_tokens(candidate + " 500mL", reference) == ["500"]


def test_story_hint_prompt_forbids_new_critical_facts() -> None:
    prompt = _correction_prompt("1차 판독", "간단한 설명", "story_hint")

    for forbidden in (
        "시간",
        "수량",
        "단위",
        "혈압",
        "배변 상태",
        "완료 여부",
        "부정 표현",
        "후속 확인",
    ):
        assert forbidden in prompt
    assert "정답이 아닙니다" in prompt
    assert "새 사실을 만들거나 요약하지 말고" in prompt




