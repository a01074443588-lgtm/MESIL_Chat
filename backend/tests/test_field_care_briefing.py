from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.field_care_briefing import build_field_care_briefing


KST = timezone(timedelta(hours=9))


def _event(
    *,
    resident_id,
    resident_name: str,
    at: datetime,
    summary: str,
    evidence_id,
    candidate_types: list[str],
    suffix: str,
):
    return SimpleNamespace(
        event_group_id=f"event-{suffix}",
        resident_id=resident_id,
        resident_name=resident_name,
        occurred_at=at,
        summary=summary,
        evidence_ids=[evidence_id],
        document_candidate_types=candidate_types,
    )


def test_builds_date_resident_time_ordered_factual_references_only():
    resident_id = uuid4()
    evidence_09 = uuid4()
    evidence_14 = uuid4()
    old_evidence = uuid4()
    start = datetime(2026, 3, 3, tzinfo=KST).astimezone(timezone.utc)
    end = datetime(2026, 3, 4, tzinfo=KST).astimezone(timezone.utc)
    events = [
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 14, 0, tzinfo=KST),
            summary="검증어르신에게 [SYNTHETIC:WATER] 14:00 물 200mL 제공함.",
            evidence_id=evidence_14,
            candidate_types=["care_service_record"],
            suffix="water",
        ),
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 9, 0, tzinfo=KST),
            summary="검증어르신 혈압 170/90 측정함.",
            evidence_id=evidence_09,
            candidate_types=["care_service_record"],
            suffix="bp",
        ),
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 2, 20, 9, 0, tzinfo=KST),
            summary="오래된 미완료 업무를 다시 확인해 주세요.",
            evidence_id=old_evidence,
            candidate_types=["care_service_record"],
            suffix="old",
        ),
    ]
    result = build_field_care_briefing(
        record_events=events,
        candidate_texts_by_evidence={
            evidence_09: ["혈압 170/90 측정함."],
            evidence_14: ["14:00 물 200mL 제공함."],
            old_evidence: ["미완료 업무"],
        },
        selected_message_ids={evidence_09, evidence_14},
        period_start=start,
        period_end=end,
    )

    entries = result["daily_care_references"][0]["residents"][0]["entries"]
    assert [entry["time_label"] for entry in entries] == ["09:00", "14:00"]
    assert [entry["summary"] for entry in entries] == [
        "혈압 170/90 측정함.",
        "14:00 물 200mL 제공함.",
    ]
    assert result["care_reference_count"] == 2
    assert result["consultation_references"] == []
    assert "미완료" not in result["overall_summary"]
    assert "SYNTHETIC" not in str(result)


def test_consultation_requires_confirmed_candidate_and_conflict_is_not_invented():
    resident_id = uuid4()
    care_evidence = uuid4()
    consultation_evidence = uuid4()
    start = datetime(2026, 3, 3, tzinfo=KST).astimezone(timezone.utc)
    end = datetime(2026, 3, 4, tzinfo=KST).astimezone(timezone.utc)
    events = [
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 10, 0, tzinfo=KST),
            summary="어르신에게 전화하고 가족 행사 일정을 들음.",
            evidence_id=care_evidence,
            candidate_types=["care_service_record"],
            suffix="call",
        ),
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 11, 0, tzinfo=KST),
            summary="보호자에게 점심 섭취 감소를 설명하고 확인받음.",
            evidence_id=consultation_evidence,
            candidate_types=["consultation_log"],
            suffix="consult",
        ),
    ]
    result = build_field_care_briefing(
        record_events=events,
        candidate_texts_by_evidence={
            care_evidence: ["어르신에게 전화하고 가족 행사 일정을 들음."],
            consultation_evidence: [
                "보호자에게 점심 섭취 감소를 설명하고 확인받음."
            ],
        },
        selected_message_ids={care_evidence, consultation_evidence},
        period_start=start,
        period_end=end,
    )

    assert result["consultation_reference_count"] == 1
    assert result["consultation_references"][0]["summary"].startswith("보호자에게")
    care_entry = result["daily_care_references"][0]["residents"][0]["entries"][0]
    assert care_entry["conflict_note"] is None
    assert care_entry["explicit_pending"] is False


def test_preserves_explicit_pending_and_only_marks_explicit_numeric_conflict():
    resident_id = uuid4()
    evidence_id = uuid4()
    start = datetime(2026, 3, 3, tzinfo=KST).astimezone(timezone.utc)
    end = datetime(2026, 3, 4, tzinfo=KST).astimezone(timezone.utc)
    event = _event(
        resident_id=resident_id,
        resident_name="검증어르신",
        at=datetime(2026, 3, 3, 9, 0, tzinfo=KST),
        summary="혈압 120/70 또는 170/90으로 불명확하여 원문 재확인 예정.",
        evidence_id=evidence_id,
        candidate_types=["care_service_record"],
        suffix="conflict",
    )
    result = build_field_care_briefing(
        record_events=[event],
        candidate_texts_by_evidence={evidence_id: [event.summary]},
        selected_message_ids={evidence_id},
        period_start=start,
        period_end=end,
    )

    entry = result["daily_care_references"][0]["residents"][0]["entries"][0]
    assert entry["explicit_pending"] is True
    assert entry["conflict_note"] == (
        "혈압 120/70과 170/90이 함께 기록되어 원문 확인이 필요합니다."
    )


def test_prefers_factual_evidence_and_drops_instruction_only_event():
    resident_id = uuid4()
    conflict_evidence = uuid4()
    instruction_evidence = uuid4()
    plan_request_evidence = uuid4()
    start = datetime(2026, 3, 3, tzinfo=KST).astimezone(timezone.utc)
    end = datetime(2026, 3, 4, tzinfo=KST).astimezone(timezone.utc)
    events = [
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 9, 0, tzinfo=KST),
            summary="어느 수치가 맞는지 선택하지 말고 재측정 후 확인해 주세요.",
            evidence_id=conflict_evidence,
            candidate_types=["care_service_record"],
            suffix="conflict-instruction",
        ),
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 16, 0, tzinfo=KST),
            summary="직원 확인 전에는 공식 계획을 변경하지 않습니다.",
            evidence_id=instruction_evidence,
            candidate_types=["care_service_record"],
            suffix="plan-instruction",
        ),
        _event(
            resident_id=resident_id,
            resident_name="검증어르신",
            at=datetime(2026, 3, 3, 17, 0, tzinfo=KST),
            summary="이동지원 방법을 급여제공계획 검토 목록에 담아 주세요.",
            evidence_id=plan_request_evidence,
            candidate_types=["care_service_record"],
            suffix="plan-request",
        ),
    ]
    result = build_field_care_briefing(
        record_events=events,
        candidate_texts_by_evidence={
            conflict_evidence: [
                "혈압 기록이 120/70과 170/90으로 서로 달라 원본 확인이 필요합니다."
            ],
            instruction_evidence: [
                "직원 확인 전에는 공식 계획을 변경하지 않습니다."
            ],
            plan_request_evidence: [
                "이동지원 방법을 급여제공계획 검토 목록에 담아 주세요."
            ],
        },
        selected_message_ids={
            conflict_evidence,
            instruction_evidence,
            plan_request_evidence,
        },
        period_start=start,
        period_end=end,
    )

    entries = result["daily_care_references"][0]["residents"][0]["entries"]
    assert len(entries) == 1
    assert entries[0]["summary"].startswith("혈압 기록이 120/70과 170/90")
    assert result["care_reference_count"] == 1


def test_initial_problem_does_not_hide_recorded_recovery_reply():
    resident_id, evidence_id = uuid4(), uuid4()
    start = datetime(2026, 9, 1, tzinfo=KST).astimezone(timezone.utc)
    end = start + timedelta(days=1)
    initial = "09:00 이동 중 비틀거려 부축함."
    recovery = "09:30 휴식 후 어지럼 없음. 보행 안정되어 이동 완료함."
    event = _event(
        resident_id=resident_id, resident_name="어르0003", at=start,
        summary=initial, evidence_id=evidence_id,
        candidate_types=["care_service_record"], suffix="recovery",
    )
    result = build_field_care_briefing(
        record_events=[event], candidate_texts_by_evidence={
            evidence_id: [f"{initial}\n[답글 · 직원0002] {recovery}"]
        }, selected_message_ids={evidence_id}, period_start=start, period_end=end,
    )
    summary = result["daily_care_references"][0]["residents"][0]["entries"][0]["summary"]
    assert "비틀거려" in summary
    assert "어지럼 없음" in summary
    assert "이동 완료함" in summary
