from uuid import uuid4

from app import main as main_module
from app.social_worker_guidance import build_social_worker_guidance


def test_synthetic_scenario_event_id_links_later_completion_without_cross_event_merge():
    resident_id = uuid4()
    left = {
        "resident_id": resident_id,
        "scenario_event_id": "EVT-BOWEL-001",
        "created_at": "2026-05-08T06:20:00+00:00",
    }
    completed_later = {
        "resident_id": resident_id,
        "scenario_event_id": "EVT-BOWEL-001",
        "created_at": "2026-05-09T00:15:00+00:00",
    }
    unrelated = {
        "resident_id": resident_id,
        "scenario_event_id": "EVT-ROUTINE-001",
        "created_at": "2026-05-09T00:15:00+00:00",
    }

    assert main_module._same_record_event(left, completed_later) is True
    assert main_module._same_record_event(left, unrelated) is False


def test_later_resolution_clears_only_explicit_synthetic_event_pending_state():
    texts = [
        "최근 3일간 배변 기록이 없어 상태 확인이 필요합니다.",
        "오늘 아침 배변을 확인해 이전 확인 요청을 완료했습니다.",
    ]

    assert main_module._briefing_pending_checks(texts, [])
    assert (
        main_module._briefing_pending_checks(
            texts,
            [],
            allow_later_resolution=True,
        )
        == []
    )


def test_guidance_preserves_hydration_time_amount_and_follow_up():
    guidance = build_social_worker_guidance(
        texts=[
            "14:00 물 200mL를 제공했고 16:00 섭취 상태를 다시 확인할 예정입니다.",
            "16:00 재확인 결과 섭취 상태가 양호했습니다.",
        ],
        pending_checks=[],
        document_candidates=["care_service_record"],
        final_status="completed",
    )

    assert guidance["suggested_wording"] == (
        "14:00 물 200mL 제공함. 16:00 재확인 결과 섭취 상태 양호함."
    )
    assert guidance["based_on_confirmed_facts"] is True
    assert guidance["related_document_types"] == ["care_service_record"]


def test_guidance_keeps_conflicting_blood_pressure_unresolved():
    guidance = build_social_worker_guidance(
        texts=[
            "혈압 기록이 120/70과 170/90으로 서로 달라 원본 확인이 필요합니다.",
            "어느 수치가 맞는지 선택하지 말고 재측정 후 확인해 주세요.",
        ],
        pending_checks=["원본 확인과 재측정이 필요합니다."],
        document_candidates=["care_service_record"],
        final_status="needs_confirmation",
    )

    assert "120/70" in guidance["suggested_wording"]
    assert "170/90" in guidance["suggested_wording"]
    assert "어느 값도 확정하지 않고" in guidance["suggested_wording"]
    assert guidance["based_on_confirmed_facts"] is False


def test_mobility_guidance_marks_plan_review_without_official_change():
    guidance = build_social_worker_guidance(
        texts=[
            "현재 실내에서 독립보행하는 모습을 확인했습니다.",
            "최근 보행 중 비틀거림이 있어 이동할 때 부축했습니다.",
        ],
        pending_checks=["지속되는 변화인지 확인해 주세요."],
        document_candidates=["care_service_record"],
        final_status="needs_confirmation",
    )

    assert guidance["previous_state"] == "이전 기록: 실내 독립보행"
    assert guidance["current_state"] == "현재 기록: 보행 시 부축 필요"
    assert "fall_risk_assessment" in guidance["related_document_types"]
    assert "long_term_care_service_plan" in guidance["related_document_types"]
    assert guidance["plan_review_notice"] == "아직 공식 계획에는 반영되지 않았습니다."


def test_mobility_guidance_preserves_previous_record_observation_date_and_exact_evidence():
    guidance = build_social_worker_guidance(
        texts=[
            "어르0001 복도 이동 중 몸이 한 차례 흔들려 옆에서 손을 받쳐드렸습니다.",
            "어르0001 프로그램실 이동 시 다시 몸이 흔들려 한 명이 팔을 잡고 이동을 도왔습니다.",
        ],
        baseline_texts=[
            "직전 욕구사정 이동 상태: 실내에서 지팡이를 사용해 독립보행함.",
        ],
        observed_at="2026-08-31 15:10",
        pending_checks=["지속되는 변화인지 확인해 주세요."],
        document_candidates=["care_service_record"],
        final_status="needs_confirmation",
    )

    wording = guidance["suggested_wording"]
    assert "이전 자료에는" in wording
    assert "지팡이를 사용해 독립보행" in wording
    assert "2026년 8월 31일 15:10 관찰" in wording
    assert "프로그램실 이동 시 다시 몸이 흔들려" in wording
    assert "근거:" in wording
    assert "낙상위험도의 이동·보행" in wording
    assert "욕구사정의 이동·일상생활" in wording
    assert "급여제공계획의 이동지원" in wording
    assert "pressure_ulcer_risk_assessment" not in guidance["related_document_types"]
    assert "cognitive_function_assessment" not in guidance["related_document_types"]


def test_mobility_guidance_uses_the_more_informative_observation_as_evidence():
    guidance = build_social_worker_guidance(
        texts=[
            "침상 옆에서 미끄러져 주저앉은 뒤 오른쪽 무릎 통증을 호소했습니다.",
            "멍과 보행 상태를 계속 확인해 주세요.",
        ],
        observed_at="2026-08-20 18:20",
        pending_checks=["무릎과 보행 상태 확인이 필요합니다."],
        document_candidates=["care_service_record"],
        final_status="needs_confirmation",
    )

    wording = guidance["suggested_wording"]
    assert "침상 옆에서 미끄러져 주저앉은 뒤" in wording
    assert '근거: "침상 옆에서 미끄러져 주저앉은 뒤' in wording
