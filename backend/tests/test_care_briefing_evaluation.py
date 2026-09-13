from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

from app.care_briefing_contract import CareBriefingV1Envelope, CareBriefingV1Result
from app.care_briefing_evaluation import evaluate_care_briefing_case


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "care_briefing_v1_cases.json"
)


def _case(case_id: str) -> dict:
    cases = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"]
    return next(case for case in cases if case["case_id"] == case_id)


def _success(case: dict) -> CareBriefingV1Envelope:
    return CareBriefingV1Envelope(
        ok=True,
        result=CareBriefingV1Result.model_validate(case["expected_result"]),
        provider="test",
        model="expected-result",
        elapsed_ms=123,
    )


def test_expected_result_receives_full_measured_score():
    case = _case("CBV1-MULTI-REPLY-FINAL-STATE")

    evaluation = evaluate_care_briefing_case(case, _success(case))

    assert evaluation.contract_ok is True
    assert evaluation.measured_score == 90
    assert evaluation.measured_max == 90
    assert evaluation.missing_requirements == []
    assert evaluation.critical_flaws == []


def test_routine_case_penalizes_unnecessary_event():
    routine = _case("CBV1-ROUTINE-COMPLETED")
    safety = _case("CBV1-SAFETY-FOLLOWUP")
    result = deepcopy(safety["expected_result"])
    result["as_of"] = routine["expected_result"]["as_of"]
    result["events"][0]["event_id"] = "invented-routine-event"
    result["events"][0]["evidence"] = [
        {
            "source_id": "msg-routine-1",
            "supports": ["event"],
            "fact_summary": "일상 기록",
        }
    ]
    result["events"][0]["importance_reasons"][0]["evidence_source_ids"] = [
        "msg-routine-1"
    ]
    result["events"][0]["verification_items"] = []
    result["events"][0]["document_draft_candidates"] = []
    envelope = CareBriefingV1Envelope(
        ok=True,
        result=CareBriefingV1Result.model_validate(result),
        provider="test",
        model="over-reporting-model",
        elapsed_ms=100,
    )

    evaluation = evaluate_care_briefing_case(routine, envelope)

    assert evaluation.scores.important_event_selection == 5
    assert "완료된 일상 기록을 중요 사건에서 제외" in evaluation.missing_requirements


def test_failure_envelope_is_measured_without_stopping_suite():
    case = _case("CBV1-SAFETY-FOLLOWUP")
    envelope = CareBriefingV1Envelope(
        ok=False,
        failure={
            "code": "timeout",
            "message": "시간 초과",
            "retryable": True,
            "offline_fallback_available": False,
        },
        provider="test",
        model="slow-model",
        elapsed_ms=180000,
    )

    evaluation = evaluate_care_briefing_case(case, envelope)

    assert evaluation.contract_ok is False
    assert evaluation.failure_code == "timeout"
    assert evaluation.measured_score == 2
    assert evaluation.critical_flaws == ["계약 실패: timeout"]


def test_forbidden_claim_is_reported_as_critical_flaw():
    case = _case("CBV1-SAFETY-FOLLOWUP")
    result = deepcopy(case["expected_result"])
    result["events"][0]["what_happened"] += " 낙상함"
    envelope = CareBriefingV1Envelope(
        ok=True,
        result=CareBriefingV1Result.model_validate(result),
        provider="test",
        model="guessing-model",
        elapsed_ms=100,
    )

    evaluation = evaluate_care_briefing_case(case, envelope)

    assert evaluation.scores.no_unsupported_claims == 0
    assert evaluation.forbidden_claims_found == ["낙상함"]
    assert "금지된 추측 발견" in evaluation.critical_flaws
