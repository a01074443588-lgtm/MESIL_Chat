from app.prototype_ai import build_document_proposal, build_prototype_suggestion


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
    assert "[제공·관찰 내용과 수량]" in drafts["care_service_record"]
    assert "[건강 관찰]" in drafts["nursing_log"]
    assert "[주요 상담 내용]" in drafts["consultation_log"]
    assert len(set(drafts.values())) == len(drafts)


def test_event_document_drafts_match_the_verified_form_field_minimums():
    care = build_document_proposal(
        "care_service_record",
        resident_label="검증어르신(가명)001",
        text=(
            "[BRIEFING-VERIFY-V1:nutrition-pending] 14:00 점심 식사 1/2을 "
            "섭취하여 물 200mL를 제공했습니다. 15:00 수분 섭취와 상태를 "
            "다시 확인할 예정입니다. 모든 내용은 합성 시험자료입니다."
        ),
        classification="nutrition",
        risk_level="medium",
        recorded_at="2026-08-25 14:00",
        evidence_count=2,
    )
    assert {
        "일시",
        "서비스·관찰 구분",
        "제공·관찰 내용과 수량",
        "대상 상태·반응",
        "수행한 조치",
        "다음 확인·기한·결과",
        "특이사항",
        "작성 근거",
        "작성자 최종 확인",
    } <= set(care["draft_fields"])
    assert care["draft_fields"]["일시"] == "2026-08-25 14:00"
    assert "1/2" in care["content"]
    assert "200mL" in care["content"]
    assert "15:00" in care["content"]
    assert "BRIEFING-VERIFY" not in care["content"]
    assert "합성 시험자료" not in care["content"]
    assert "1/2" in care["draft_fields"]["제공·관찰 내용과 수량"]
    assert "작성자 최종 확인" in care["missing_fields"]
    assert care["human_verification_fields"]

    consultation = build_document_proposal(
        "consultation_log",
        resident_label="검증어르신(가명)001",
        text=(
            "11:00 보호자와 전화 통화하여 점심 식사량 감소와 물 제공을 "
            "설명드렸고 보호자 확인을 완료했습니다."
        ),
        classification="consultation",
        risk_level="low",
        recorded_at="2026-08-25 11:00",
        evidence_count=1,
    )
    assert {
        "상담 일시",
        "상담 방식",
        "상담 상대 관계",
        "상담 사유",
        "주요 상담 내용",
        "설명·요청·동의·반응",
        "수행한 조치와 결과",
        "추후 계획·재확인",
        "작성 근거",
        "상담 직원 최종 확인",
    } <= set(consultation["draft_fields"])
    assert consultation["draft_fields"]["상담 방식"] == "전화"
    assert consultation["draft_fields"]["상담 일시"] == "2026-08-25 11:00"
    assert "보호자" in consultation["content"]
    assert "상담 직원 최종 확인" in consultation["missing_fields"]
    assert consultation["human_verification_fields"]


def test_care_service_draft_keeps_only_confirmed_primary_facts():
    proposal = build_document_proposal(
        "care_service_record",
        resident_label="검증어르신(가명)001",
        text=(
            "[합성 비식별 통합 브리핑 시험] 14:00 물 200mL 제공을 완료했습니다. "
            "후속 확인은 아직 미완료이며 16:00 다시 확인할 예정입니다. "
            "모든 내용은 실제 인물과 무관한 합성 시험자료입니다.\n"
            "[이미지 글자 판독 · synthetic_care_card.png]\n"
            "SYNTHETIC CARE TEST TIME 14:00 WATER OFFERED 200 ML FOLLOW-UP PENDING\n"
            "[음성 받아쓰기 · synthetic_care_message.wav]\n"
            "신세티케어 테스트 투 피엠 워터 투헌드리드 밀리리터\n"
            "[답글 · 개발 관리자] 개발 검증용 댓글입니다."
        ),
        classification="nutrition",
        risk_level="medium",
        recorded_at="2026-08-25 14:00",
        evidence_count=1,
    )

    assert proposal["draft_fields"]["제공·관찰 내용과 수량"] == (
        "14:00 물 200mL 제공함."
    )
    assert proposal["draft_fields"]["대상 상태·반응"] == "확인 필요"
    assert proposal["draft_fields"]["수행한 조치"] == "물 200mL 제공"
    assert proposal["draft_fields"]["다음 확인·기한·결과"] == (
        "16:00 섭취 상태 재확인 예정"
    )
    assert proposal["draft_fields"]["특이사항"] == "후속 확인 미완료"
    serialized = " ".join(
        [proposal["content"], *proposal["draft_fields"].values()]
    )
    for contaminant in (
        "SYNTHETIC",
        "WATER OFFERED",
        "신세티케어",
        "개발 검증용 댓글",
        "합성 시험자료",
    ):
        assert contaminant not in serialized


def test_care_service_draft_never_chooses_a_conflicting_blood_pressure():
    proposal = build_document_proposal(
        "care_service_record",
        resident_label="검증어르신(가명)001",
        text=(
            "혈압이 120/70인지 170/90인지 불명확합니다. "
            "어느 수치도 확정하지 말고 원문 확인 후 재측정할 예정입니다."
        ),
        classification="health",
        risk_level="medium",
        recorded_at="2026-08-25 12:30",
        evidence_count=1,
    )

    assert proposal["draft_fields"]["제공·관찰 내용과 수량"] == (
        "혈압 120/70 또는 170/90 · 원문 확인 필요"
    )
    assert proposal["draft_fields"]["대상 상태·반응"] == "확인 필요"
    assert proposal["draft_fields"]["수행한 조치"] == (
        "원문 확인 후 혈압 재측정 필요"
    )
    assert "120/70 또는 170/90" in proposal["content"]
    assert "어느 값도 확정하지 않음" in proposal["content"]


def test_consultation_draft_excludes_attachment_and_development_reply_text():
    proposal = build_document_proposal(
        "consultation_log",
        resident_label="검증어르신(가명)001",
        text=(
            "보호자와 11:00 통화하여 식사량 감소를 설명했고 보호자가 확인했습니다.\n"
            "[음성 받아쓰기 · synthetic.wav]\n"
            "신세틱 오디오 테스트 문장\n"
            "[답글 · 개발 관리자] 개발 확인 댓글"
        ),
        classification="consultation",
        risk_level="low",
        recorded_at="2026-08-25 11:00",
        evidence_count=1,
    )

    serialized = " ".join(
        [proposal["content"], *proposal["draft_fields"].values()]
    )
    assert "보호자" in serialized
    assert "보호자에게 설명하고 확인받음" in serialized
    assert "신세틱 오디오" not in serialized
    assert "개발 확인 댓글" not in serialized


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
