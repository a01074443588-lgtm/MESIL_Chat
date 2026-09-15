from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.care_briefing_contract import (
    CareBriefingV1Envelope,
    CareBriefingV1Event,
    CareBriefingV1Failure,
    CareBriefingV1Input,
    CareBriefingV1Result,
    validate_care_briefing_result,
)


NOW = datetime(2026, 8, 23, 9, 0, tzinfo=timezone.utc)


def sample_input() -> CareBriefingV1Input:
    return CareBriefingV1Input.model_validate(
        {
            "version": "care_briefing_v1",
            "window": {
                "start_at": "2026-08-16T00:00:00Z",
                "end_at": "2026-08-23T08:59:59Z",
                "as_of": NOW,
                "timezone": "Asia/Seoul",
                "includes_unresolved_carryover": True,
            },
            "transmission_policy": {
                "privacy_mode": "external_deidentified",
                "external_ai_allowed": True,
                "contains_direct_identifiers": False,
            },
            "subjects": [
                {
                    "subject_id": "RESIDENT_001",
                    "display_label": "어르신 A",
                    "verification_status": "confirmed",
                }
            ],
            "sources": [
                {
                    "source_id": "MSG_001",
                    "source_kind": "message",
                    "conversation_id": "ROOM_001",
                    "subject_ids": ["RESIDENT_001"],
                    "author_role": "요양보호사",
                    "occurred_at": "2026-08-22T01:00:00Z",
                    "text": "아침 식사량이 평소보다 줄어 절반 정도 드셨습니다.",
                    "verification_status": "source_record",
                },
                {
                    "source_id": "REPLY_001",
                    "source_kind": "reply",
                    "conversation_id": "ROOM_001",
                    "parent_source_id": "MSG_001",
                    "subject_ids": ["RESIDENT_001"],
                    "author_role": "사회복지사",
                    "occurred_at": "2026-08-22T02:00:00Z",
                    "text": "점심 식사 후 섭취량을 다시 확인하겠습니다.",
                    "verification_status": "source_record",
                },
                {
                    "source_id": "PHOTO_001",
                    "source_kind": "photo",
                    "conversation_id": "ROOM_001",
                    "subject_ids": ["RESIDENT_001"],
                    "occurred_at": "2026-08-22T01:00:00Z",
                    "media_id": "ATTACHMENT_001",
                    "verification_status": "source_record",
                },
            ],
        }
    )


def sample_event() -> CareBriefingV1Event:
    return CareBriefingV1Event.model_validate(
        {
            "event_id": "EVENT_001",
            "subject": {
                "subject_id": "RESIDENT_001",
                "display_label": "어르신 A",
                "verification_status": "confirmed",
            },
            "what_happened": "아침 식사량이 평소보다 줄어 절반 정도 드셨습니다.",
            "final_status": "in_progress",
            "final_status_summary": "점심 식사 후 섭취량 재확인이 예정되어 있습니다.",
            "occurred_at": "2026-08-22T01:00:00Z",
            "scheduled_at": "2026-08-22T03:00:00Z",
            "priority": "check",
            "importance_score": 65,
            "importance_reasons": [
                {
                    "reason": "평소와 다른 식사량 변화이며 후속 확인이 끝나지 않았습니다.",
                    "evidence_source_ids": ["MSG_001", "REPLY_001"],
                }
            ],
            "verification_items": [
                {
                    "field": "follow_up",
                    "question": "점심 식사 후 실제 섭취량을 확인해 주세요.",
                    "evidence_source_ids": ["REPLY_001"],
                }
            ],
            "evidence": [
                {
                    "source_id": "MSG_001",
                    "supports": ["event", "status", "importance"],
                    "fact_summary": "아침 식사량 감소가 기록됨",
                },
                {
                    "source_id": "REPLY_001",
                    "supports": ["status", "time"],
                    "fact_summary": "점심 후 재확인 계획이 기록됨",
                },
            ],
            "document_draft_candidates": [
                {
                    "document_type": "care_service_record",
                    "reason": "식사 관찰 및 후속 확인 기록 후보입니다.",
                    "evidence_source_ids": ["MSG_001", "REPLY_001"],
                }
            ],
            "analysis_confidence": 0.88,
            "validation_status": "needs_staff_review",
        }
    )


def test_contract_accepts_grounded_result_and_accounts_for_all_sources():
    input_data = sample_input()
    result = CareBriefingV1Result(
        as_of=NOW,
        events=[sample_event()],
        ignored_sources=[
            {
                "source_id": "PHOTO_001",
                "reason": "insufficient_context",
                "explanation": "사진만으로는 추가 사실을 판정하지 않았습니다.",
            }
        ],
    )

    assert validate_care_briefing_result(input_data, result) is result


def test_output_schema_requires_nullable_subject_id_key():
    schema = CareBriefingV1Result.model_json_schema()
    event_required = schema["$defs"]["CareBriefingV1Event"]["required"]

    assert "subject_id" in schema["$defs"]["CareBriefingSubjectRef"]["required"]
    assert "occurred_at" in event_required
    assert "scheduled_at" in event_required


def test_contract_rejects_unknown_evidence_reference():
    input_data = sample_input()
    event_payload = sample_event().model_dump(mode="json")
    event_payload["evidence"].append(
        {
            "source_id": "INVENTED_999",
            "supports": ["event"],
            "fact_summary": "입력에 존재하지 않는 가짜 근거",
        }
    )
    event = CareBriefingV1Event.model_validate(event_payload)
    result = CareBriefingV1Result(
        as_of=NOW,
        events=[event],
        ignored_sources=[
            {
                "source_id": source_id,
                "reason": "insufficient_context",
                "explanation": "사건 판단에 사용하지 않은 근거입니다.",
            }
            for source_id in ["PHOTO_001"]
        ],
    )

    with pytest.raises(ValueError, match="입력에 없는 근거"):
        validate_care_briefing_result(input_data, result)


def test_contract_rejects_completed_status_without_completion_evidence():
    payload = sample_event().model_dump(mode="json")
    payload["final_status"] = "completed"
    payload["final_status_summary"] = "확인이 완료되었습니다."
    payload["verification_items"] = []
    payload["validation_status"] = "source_linked"

    with pytest.raises(ValidationError, match="완료를 직접 뒷받침"):
        CareBriefingV1Event.model_validate(payload)


def test_contract_rejects_unaccounted_input_source():
    input_data = sample_input()
    result = CareBriefingV1Result(
        as_of=NOW,
        events=[sample_event()],
        ignored_sources=[],
    )

    with pytest.raises(ValueError, match="모든 입력 근거"):
        validate_care_briefing_result(input_data, result)


def test_contract_rejects_direct_identifiers_in_external_input():
    payload = sample_input().model_dump(mode="json")
    payload["transmission_policy"]["contains_direct_identifiers"] = True

    with pytest.raises(ValidationError, match="직접 식별정보"):
        CareBriefingV1Input.model_validate(payload)


def test_contract_exposes_standard_timeout_failure():
    envelope = CareBriefingV1Envelope(
        ok=False,
        failure=CareBriefingV1Failure(
            code="timeout",
            message="AI 분석 시간이 초과되었습니다.",
            retryable=True,
            offline_fallback_available=True,
        ),
        provider="test-provider",
        model="test-model",
        elapsed_ms=30000,
    )

    assert envelope.result is None
    assert envelope.failure is not None
    assert envelope.failure.code == "timeout"


def test_contract_forbids_unknown_fields():
    payload = sample_event().model_dump(mode="json")
    payload["guessed_assignee"] = "담당자 A"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CareBriefingV1Event.model_validate(payload)


def test_contract_rejects_empty_result_without_exclusion_reasons():
    with pytest.raises(ValidationError, match="제외 사유"):
        CareBriefingV1Result(
            as_of=NOW,
            events=[],
            ignored_sources=[],
        )


def test_contract_rejects_missing_required_event_output():
    payload = sample_event().model_dump(mode="json")
    payload.pop("final_status_summary")

    with pytest.raises(ValidationError, match="Field required"):
        CareBriefingV1Event.model_validate(payload)
