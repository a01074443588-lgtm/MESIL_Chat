from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.care_briefing_contract import CareBriefingV1Input
from app.care_briefing_nvidia_adapter import (
    NvidiaCompactResult,
    adapt_nvidia_compact_result,
    nvidia_compact_generation_schema,
)


FIXTURE_PATH = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "care_briefing_v1_cases.json"
)


def _input_data() -> CareBriefingV1Input:
    raw = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["cases"][0]["input"]
    return CareBriefingV1Input.model_validate(raw)


def _compact_event(**overrides) -> NvidiaCompactResult:
    event = {
        "source_ids": ["msg-safety-1", "reply-safety-1", "photo-safety-1"],
        "subject_id": "subject-a",
        "what_happened": "안전 관련 후속 확인이 필요합니다.",
        "final_status": "monitoring",
        "final_status_summary": "경과를 확인 중입니다.",
        "occurred_at": "2026-08-22T14:05:00+09:00",
        "scheduled_at": None,
        "priority": "first",
        "importance_score": 95,
        "importance_reason": "안전 위험의 현재 상태 확인이 필요합니다.",
        "status_source_id": "reply-safety-1",
        "completion_source_id": None,
        "verification_field": "follow_up",
        "verification_question": "후속 상태를 확인했나요?",
        "document_type": "nursing_log",
        "document_reason": "경과 기록 후보입니다.",
        "analysis_confidence": 0.9,
    }
    event.update(overrides)
    return NvidiaCompactResult.model_validate(
        {"events": [event], "ignored_sources": [], "warnings": []}
    )


def test_generation_schema_requires_complete_top_level_envelope() -> None:
    schema = nvidia_compact_generation_schema(_input_data())

    assert schema["required"] == ["events", "ignored_sources", "warnings"]


def test_adapter_builds_evidence_summaries_from_original_sources():
    result = adapt_nvidia_compact_result(_compact_event(), _input_data())

    assert result.events[0].event_id.startswith("evt-")
    assert result.events[0].subject.subject_id == "subject-a"
    assert len(result.events[0].evidence) == 3
    assert all(item.fact_summary for item in result.events[0].evidence)
    assert result.events[0].validation_status == "needs_staff_review"


def test_adapter_never_guesses_unknown_subject():
    result = adapt_nvidia_compact_result(
        _compact_event(subject_id=None),
        _input_data(),
    )

    event = result.events[0]
    assert event.subject.subject_id is None
    assert event.subject.display_label == "확인 필요"
    assert event.subject.verification_status == "needs_confirmation"
    assert any(item.field == "subject" for item in event.verification_items)


def test_adapter_rejects_unknown_source_id():
    compact = _compact_event(source_ids=["invented-source"])

    with pytest.raises(ValueError, match="입력에 없는 근거"):
        adapt_nvidia_compact_result(compact, _input_data())


def test_adapter_rejects_completed_without_direct_completion_source():
    compact = _compact_event(final_status="completed", completion_source_id=None)

    with pytest.raises(ValueError, match="명시적인 완료 근거"):
        adapt_nvidia_compact_result(compact, _input_data())


def test_compact_model_safely_completes_partial_review_pairs():
    compact = _compact_event(
        verification_field="follow_up",
        verification_question=None,
        document_type="nursing_log",
        document_reason=None,
    )

    event = compact.events[0]
    assert event.verification_question == "해당 내용을 원문에서 확인해 주세요."
    assert event.document_reason is not None
    assert "원문 근거" in event.document_reason
