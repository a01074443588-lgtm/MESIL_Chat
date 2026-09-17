from app.handwriting_accuracy_evaluation import (
    aggregate_preservation,
    approval_reference_eligibility,
    character_metrics,
    evaluate_candidate,
    evaluation_fingerprint,
    preservation_metrics,
    staff_edit_metrics,
    strip_identity_header,
    unsupported_fact_additions,
)


TERMS = {
    "time": [["14시", "오후 2시"]],
    "quantity": [["200mL", "200 ml"]],
    "unit": [["mL", "ml"]],
    "completion_status": [["섭취 완료"]],
    "negation": [["통증 없음"]],
    "follow_up": [["16시 재확인"]],
}
REFERENCE = "오후 2시 물 200mL 제공, 16시 재확인 후 섭취 완료, 통증 없음"


def test_character_error_rate_and_accuracy_are_reproducible():
    exact = character_metrics("가나다", "가나다")
    deletion = character_metrics("가나", "가나다")

    assert exact["character_error_rate"] == 0.0
    assert exact["character_accuracy"] == 1.0
    assert deletion["edit_distance"] == 1
    assert deletion["character_error_rate"] == 0.3333
    assert deletion["character_accuracy"] == 0.6667


def test_identity_header_is_removed_when_readable_mode_joins_it_with_date():
    value = "검증용 가상자료 가명어르신. 2월 3일 오후 2시 물 200mL 제공."

    assert strip_identity_header(value).startswith("2월 3일")
    assert character_metrics(value, "2월 3일 오후 2시 물 200mL 제공")[
        "character_accuracy"
    ] == 1.0


def test_critical_facts_are_scored_separately_and_empty_categories_are_not_zeroed():
    preservation = preservation_metrics(REFERENCE, TERMS)

    assert preservation["time"]["rate"] == 1.0
    assert preservation["quantity"]["rate"] == 1.0
    assert preservation["follow_up"]["rate"] == 1.0
    assert preservation["medication"]["rate"] is None
    assert aggregate_preservation(preservation, ("name", "medication", "diagnosis"))[
        "rate"
    ] is None


def test_unsupported_structured_facts_are_counted_without_claiming_full_semantics():
    additions = unsupported_fact_additions(
        REFERENCE + " 18시 확인, 감기약 제공, 치매 진단",
        REFERENCE,
    )

    assert additions["unsupported_numeric_token_count"] == 1
    assert additions["unsupported_sensitive_claim_count"] == 2
    assert additions["count"] == 3


def test_missing_stage_stays_not_evaluable_instead_of_becoming_zero():
    result = evaluate_candidate(
        candidate=None,
        reference=REFERENCE,
        preservation_terms=TERMS,
        unavailable_reason="부분 수정 발화 결과가 없습니다.",
    )

    assert result["availability"] == "not_evaluable"
    assert result["character_accuracy"] is None
    assert result["verdict"] == "평가 불가"


def test_conflict_review_is_not_counted_as_wrong_auto_confirmation():
    result = evaluate_candidate(
        candidate="오후 2시 물 200mL 제공, 16시 재확인",
        reference=REFERENCE,
        preservation_terms=TERMS,
        initial_accuracy=0.5,
        conflict_review_count=2,
        incorrect_auto_confirmation_count=0,
    )

    assert result["conflict_review_count"] == 2
    assert result["incorrect_auto_confirmation_count"] == 0
    assert result["verdict"] == "보조교정"


def test_cross_case_audio_disqualifies_staff_approval_as_reference():
    same = approval_reference_eligibility(
        image_case_id="case-1", audio_case_id="case-1", mode="full_reading"
    )
    mismatch = approval_reference_eligibility(
        image_case_id="case-1", audio_case_id="case-2", mode="full_reading"
    )

    assert same == {"eligible": True, "reason": "image_audio_same_case"}
    assert mismatch == {
        "eligible": False,
        "reason": "image_audio_case_mismatch",
    }


def test_staff_edit_count_and_fingerprint_ignore_generation_time():
    edits = staff_edit_metrics(
        suggestion="오후 2시 물 100mL 제공",
        approved_final="오후 2시 물 200mL 제공",
        preservation_terms=TERMS,
    )
    base = {
        "schema_version": "v1",
        "case_count": 1,
        "cases": [{"case_id": "case-1", "accuracy": 1.0}],
        "summary": {"verdict": "직원확인 필수"},
        "data_quality": {"approved_reference_count": 0},
        "boundaries": {"external_api_calls": 0},
        "generated_at": "first",
    }
    changed_time = {**base, "generated_at": "second"}

    assert edits is not None
    assert edits["edited_character_count"] == 1
    assert edits["changed_critical_field_count"] == 1
    assert evaluation_fingerprint(base) == evaluation_fingerprint(changed_time)
