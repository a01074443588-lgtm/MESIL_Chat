from app.multimodal_correction_policy import decide_multimodal_correction


def test_image_only_does_not_spend_a_second_model_call_without_new_evidence():
    decision = decide_multimodal_correction(
        "오전 9시 배변 없음. 물을 제공하고 10분 정도 걸으심.",
        None,
        "image_only",
    )

    assert decision.request_ai_correction is False
    assert decision.reason == "no_independent_audio_evidence"
    assert decision.parallel_ocr_stt_allowed is False


def test_full_reading_requests_correction_only_for_material_difference():
    same = decide_multimodal_correction(
        "오후 2시 10분 물 200mL 제공함",
        "오후 2시 10분 물 200mL 제공함",
        "full_reading",
    )
    different = decide_multimodal_correction(
        "오후 2시 물 200mL 제공함. 오후 4시 확인함.",
        "오후 2시 10분 물 200mL 제공했고 오후 4시 5분 확인함.",
        "full_reading",
    )

    assert same.request_ai_correction is False
    assert same.reason == "image_audio_difference_below_threshold"
    assert different.request_ai_correction is True
    assert different.reason == "critical_image_audio_difference"


def test_unrelated_story_hint_is_not_allowed_to_rewrite_the_image():
    decision = decide_multimodal_correction(
        "혈압 170/90 측정됨. 10분간 앉아서 쉰 후 다시 측정함.",
        "점심 식사 후 기분이 좋았다고 설명함.",
        "story_hint",
    )

    assert decision.request_ai_correction is False
    assert decision.reason == "audio_has_no_shared_content_evidence"
    assert decision.requires_reocr_or_staff_review is True


def test_repeated_ocr_is_routed_to_reocr_or_staff_review_not_text_correction():
    repeated = "\n".join(["오후 2시 물 200mL 제공함"] * 8)
    decision = decide_multimodal_correction(
        repeated,
        "오후 2시 물 200mL 제공함",
        "full_reading",
    )

    assert decision.request_ai_correction is False
    assert decision.reason == "ocr_repetition_requires_reocr_or_staff_review"
    assert decision.requires_reocr_or_staff_review is True
