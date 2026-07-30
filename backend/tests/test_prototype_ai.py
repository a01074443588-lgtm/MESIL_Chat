from app.prototype_ai import build_prototype_suggestion


def _draft(body: str) -> dict:
    return build_prototype_suggestion(
        {
            "body": body,
            "resident_name": "시설(가명)001",
            "resident_names": ["시설(가명)001"],
        }
    )


def test_fall_like_expression_is_prioritized_as_safety():
    draft = _draft(
        "화장실로 가시다가 균형을 잃어 주저앉으셨고 오른쪽 무릎 통증을 말씀하셨습니다."
    )

    assert draft["classification"] == "safety"
    assert draft["risk_level"] == "high"
    assert "nursing_log" in draft["document_types"]
    assert "integrated_assessment" not in draft["document_types"]
    assert draft["verification_questions"]


def test_skin_redness_is_health_and_requires_observation():
    draft = _draft(
        "엉치 피부가 동전 크기로 붉게 보여 체위변경 후 압박이 생기지 않도록 했습니다."
    )

    assert draft["classification"] == "health"
    assert draft["risk_level"] == "medium"
    assert "skin_integrity" in draft["keywords"]
    assert "nursing_log" in draft["document_types"]


def test_repeated_exit_seeking_is_cognition_related_daily_care():
    draft = _draft(
        "집에 가야 한다는 말씀을 반복하며 출입문 쪽으로 이동하여 달력을 함께 보고 안정 지원했습니다."
    )

    assert draft["classification"] == "daily_care"
    assert draft["risk_level"] == "medium"
    assert "cognition" in draft["keywords"]
    assert "care_plan_evaluation" not in draft["document_types"]
    assert "care_service_record" in draft["document_types"]


def test_explicit_no_oral_pain_does_not_hide_nutrition_observation():
    draft = _draft(
        "점심을 평소의 절반만 드셨고 물도 두 모금만 드셨습니다. 입안 통증은 없다고 하셨습니다."
    )

    assert draft["classification"] == "nutrition"
    assert draft["risk_level"] == "medium"
    assert "integrated_assessment" not in draft["document_types"]
    assert {item["document_type"] for item in draft["document_drafts"]} == {
        "care_service_record",
        "nursing_log",
    }


def test_meal_time_word_requires_actual_meal_context():
    movement = _draft(
        "현재까지 통증이나 붓기는 없으며 저녁 이동 때 보행 상태를 다시 확인하겠습니다."
    )
    physical_therapy = _draft(
        "오후 물리치료를 다녀오신 뒤 보행 시 한 명이 부축했습니다."
    )
    meal = _draft("점심을 절반만 드셨고 물을 100ml 드렸습니다.")

    assert movement["classification"] != "nutrition"
    assert "nutrition" not in movement["keywords"]
    assert physical_therapy["classification"] == "rehabilitation"
    assert "nutrition" not in physical_therapy["keywords"]
    assert meal["classification"] == "nutrition"
    assert {"점심", "물", "nutrition"} <= set(meal["keywords"])


def test_physical_restraint_draft_keeps_missing_fields_as_questions():
    draft = _draft("휠체어 안전벨트 사용이 필요하다고 보고했습니다.")

    restraint = next(
        item
        for item in draft["document_drafts"]
        if item["document_type"] == "physical_restraint_log"
    )
    assert "확인 필요" in restraint["content"]
    assert len(restraint["verification_questions"]) >= 3


def test_program_log_is_a_daily_document_candidate():
    draft = _draft("오후 음악 프로그램에 참여하여 노래를 따라 하셨습니다.")

    assert "program_log" in draft["document_types"]


def test_mixed_care_and_guardian_contact_create_distinct_document_drafts():
    draft = _draft(
        "점심 식사는 절반 드셨습니다. 보호자와 전화 통화하여 최근 수면 상태를 "
        "설명했고 다음 주 다시 연락하기로 했습니다."
    )

    drafts = {
        item["document_type"]: item["content"]
        for item in draft["document_drafts"]
    }
    assert {"care_service_record", "nursing_log", "consultation_log"} <= set(drafts)
    assert "[관찰·욕구]" in drafts["care_service_record"]
    assert "[건강 관찰]" in drafts["nursing_log"]
    assert "[상담·연락 내용]" in drafts["consultation_log"]
    assert len(set(drafts.values())) == len(drafts)


def test_reservation_words_are_not_mistaken_for_medication():
    reservation = _draft("보호자와 다음 주 면회를 예약했습니다.")
    promise = _draft("보호자와 다음 주 다시 연락하기로 약속했습니다.")
    hypothetical = _draft("만약 변화가 있으면 보호자에게 연락하겠습니다.")

    for draft in (reservation, promise, hypothetical):
        assert "약" not in draft["keywords"]
        assert "nursing_log" not in draft["document_types"]

    medication = _draft("저녁 약을 드리고 삼키신 것을 확인했습니다.")
    assert medication["classification"] == "health"
    assert "약" in medication["keywords"]
    assert "nursing_log" in medication["document_types"]


def test_after_meal_bathroom_use_is_not_mistaken_for_nutrition():
    draft = _draft(
        "점심 식사 후 화장실을 두 차례 이용하셨습니다. "
        "배변 양상은 보통이었습니다."
    )

    assert draft["classification"] == "daily_care"
    assert "nutrition" not in draft["keywords"]
