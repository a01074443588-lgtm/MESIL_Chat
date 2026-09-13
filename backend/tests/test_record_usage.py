import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from app.main import (
    _consultation_candidate_context,
    _group_record_events,
    _record_document_candidate_metadata,
    _record_document_candidate_types,
    _record_usage_tags,
)
from app.prototype_ai import build_prototype_suggestion
from app.schemas import RecordDraft


def suggestion(text: str) -> RecordDraft:
    return RecordDraft.model_validate(
        build_prototype_suggestion(
            {
                "body": text,
                "resident_name": "시설(가명)001",
                "resident_names": ["시설(가명)001"],
            }
        )
    )


def tags(text: str, *, has_resident: bool = True) -> set[str]:
    return set(
        _record_usage_tags(
            text,
            has_resident=has_resident,
            suggestion=suggestion(text),
        )
    )


def document_candidates(text: str, *, has_resident: bool = True) -> set[str]:
    return set(
        _record_document_candidate_types(
            list(tags(text, has_resident=has_resident))
        )
    )


def document_metadata(text: str):
    record_tags = list(tags(text))
    return _record_document_candidate_metadata(
        text,
        record_tags,
        has_resident=True,
    )


def test_record_usage_examples_are_conservative():
    assert tags(
        "시설(가명)012 어르신이 복도에서 비틀거려 부축했습니다. "
        "넘어지지는 않았고 간호팀에 전달했습니다."
    ) >= {"nursing", "care_service"}

    family_request = tags(
        "시설(가명)027 어르신이 집에 가야 한다며 보호자를 찾으셨습니다. "
        "말벗을 제공하자 안정되셨습니다."
    )
    assert "care_service" in family_request
    assert "consultation" not in family_request

    guardian_call = tags(
        "시설(가명)007 어르신 보호자와 전화 통화하여 최근 수면 상태를 설명드렸습니다."
    )
    assert "consultation" in guardian_call

    program = tags(
        "시설(가명)008 어르신이 오전 독서 프로그램에 참여하셨고 대화 반응이 또렷했습니다."
    )
    assert program == {"program"}

    blood_pressure = tags(
        "시설(가명)036 어르신 혈압 168/92 확인 후 20분 뒤 154/86으로 재측정했습니다."
    )
    assert "nursing" in blood_pressure

    general = tags(
        "내일 소방 점검으로 2층 복도 통행을 잠시 제한합니다.",
        has_resident=False,
    )
    assert general == {"general"}

    unlinked_nursing_notice = tags(
        "간호팀은 오전 회의 후 혈압계 점검 결과를 공유해 주세요.",
        has_resident=False,
    )
    assert unlinked_nursing_notice == {"general"}

    uncertain = tags(
        "시설(가명)001 어르신 오른쪽인지 왼쪽인지 판독되지 않아 신체 부위 확인 필요."
    )
    assert "needs_review" in uncertain


def test_consultation_candidate_requires_one_connected_contact_event():
    confirmed = (
        "시설(가명)001 어르신 보호자와 11:00 통화하여 식사량 감소와 "
        "물 제공 내용을 설명드렸고 보호자가 확인했습니다."
    )
    resident_call_with_family_event = (
        "시설(가명)001 어르신에게 전화해 등원 시간을 확인했습니다. "
        "오후에는 손녀딸 가족 행사로 귀가할 예정입니다."
    )
    unclear_call = "시설(가명)001 어르신 관련 10:30 전화 후 다시 확인하기로 했습니다."
    transport_only = (
        "시설(가명)001 어르신의 등원을 도왔고 오후 귀가 송영을 지원했습니다."
    )
    granddaughter_substring = (
        "시설(가명)001 어르신이 손녀딸 가족 행사에 참석해 귀가를 지원했습니다."
    )

    assert _consultation_candidate_context(confirmed)[0] == "candidate"
    assert "consultation" in tags(confirmed)
    confirmed_candidates, reasons, review_types, _ = document_metadata(confirmed)
    assert "consultation_log" in confirmed_candidates
    assert reasons["consultation_log"] == "보호자 연락·설명·확인 결과"
    assert review_types == []

    assert _consultation_candidate_context(resident_call_with_family_event)[0] == "none"
    assert "consultation" not in tags(resident_call_with_family_event)
    assert "consultation_log" not in document_candidates(
        resident_call_with_family_event
    )

    assert _consultation_candidate_context(unclear_call)[0] == "review"
    assert tags(unclear_call) >= {"needs_review"}
    unclear_candidates, _, unclear_review_types, unclear_review_reasons = (
        document_metadata(unclear_call)
    )
    assert "consultation_log" not in unclear_candidates
    assert unclear_review_types == ["consultation_log"]
    assert "상대" in unclear_review_reasons["consultation_log"]

    assert "care_service_record" in document_candidates(transport_only)
    assert "consultation_log" not in document_candidates(transport_only)
    assert "consultation_log" not in document_candidates(granddaughter_substring)

    voice_unrelated = (
        "[음성 받아쓰기 · synthetic.wav] 시설(가명)001 어르신에게 전화해 "
        "등원 시간을 확인했습니다. 뒤 문장의 손녀딸 가족 행사는 귀가 사유입니다."
    )
    typed_unrelated = (
        "시설(가명)001 어르신에게 전화했습니다. 손녀딸 가족 행사로 귀가합니다."
    )
    assert "consultation_log" not in document_candidates(voice_unrelated)
    assert "consultation_log" not in document_candidates(typed_unrelated)


def test_document_candidate_reasons_are_concise_and_uncertainty_is_visible():
    water = "시설(가명)001 어르신께 14:00 물 200mL를 제공하고 16:00 재확인 예정입니다."
    water_candidates, water_reasons, _, _ = document_metadata(water)
    assert "care_service_record" in water_candidates
    assert water_reasons["care_service_record"] == "수분 제공 및 후속 확인 기록"

    conflict = "시설(가명)001 어르신 혈압이 120/70인지 170/90인지 불명확합니다."
    conflict_candidates, conflict_reasons, _, _ = document_metadata(conflict)
    assert "care_service_record" in conflict_candidates
    assert conflict_reasons["care_service_record"] == (
        "확인 필요 · 혈압 수치 충돌 원문 확인"
    )


def test_consultation_candidate_selection_metadata_keeps_manual_review_separate():
    unclear_call = "시설(가명)001 어르신 관련 전화 후 상태를 다시 확인했습니다."
    candidates, reasons, review_types, review_reasons = document_metadata(unclear_call)

    assert candidates == []
    assert reasons == {}
    assert review_types == ["consultation_log"]
    assert review_reasons["consultation_log"]


def test_record_usage_maps_to_only_the_seven_approved_document_candidates():
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "m21_document_candidate_mapping_v1.json"
        ).read_text(encoding="utf-8")
    )
    approved = set(fixture["approved_document_candidate_types"])
    assert len(approved) == 7
    mapped: set[str] = set()
    for case in fixture["cases"]:
        actual = document_candidates(
            case["body"],
            has_resident=case["has_resident"],
        )
        assert actual == set(case["expected_document_candidate_types"]), case[
            "case_id"
        ]
        mapped.update(actual)
    assert mapped <= approved
    assert {"nursing_log", "program_log", "general", "needs_review"}.isdisjoint(
        mapped
    )


def test_duplicate_reports_form_one_event_and_keep_all_evidence():
    resident_id = uuid4()
    first_id = uuid4()
    second_id = uuid4()
    created_at = datetime(2026, 7, 29, 1, 10, tzinfo=timezone.utc)
    text = "시설(가명)012 어르신이 복도에서 비틀거려 부축했습니다."
    base = {
        "resident_id": resident_id,
        "resident_name": "시설(가명)012",
        "text": text,
        "summary": text,
        "record_usage_tags": ["nursing", "care_service"],
        "room_name": "시설 전체방",
        "sender_name": "요양보호사 01",
    }
    events = _group_record_events(
        [
            {
                **base,
                "message_id": first_id,
                "created_at": created_at,
                "latest_at": created_at + timedelta(minutes=40),
            },
            {
                **base,
                "message_id": second_id,
                "created_at": created_at + timedelta(minutes=8),
                "sender_name": "요양보호사 02",
            },
        ]
    )
    assert len(events) == 1
    assert events[0].evidence_ids == [first_id, second_id]
    assert set(events[0].sender_names) == {"요양보호사 01", "요양보호사 02"}
    assert events[0].occurred_at == created_at
    assert events[0].latest_at == created_at + timedelta(minutes=40)


def test_followup_with_shared_clock_and_same_signals_forms_one_event():
    resident_id = uuid4()
    created_at = datetime(2026, 8, 25, 4, 20, tzinfo=timezone.utc)
    base = {
        "resident_id": resident_id,
        "resident_name": "검증어르신(가명)001",
        "record_usage_tags": ["nursing", "care_service"],
        "room_name": "합성 검증방",
        "sender_name": "합성 직원",
    }
    initial = (
        "검증어르신(가명)001님이 13:20 복도 이동 중 비틀거림이 있어 "
        "부축하고 간호팀에 전달했습니다. 통증 호소는 없었으며 "
        "14:00 비틀거림·통증 재확인 예정입니다."
    )
    followup = (
        "검증어르신(가명)001님의 14:00 비틀거림·통증 재확인 결과, "
        "비틀거림과 통증 호소 없이 보행 안정 확인을 완료했습니다."
    )

    events = _group_record_events(
        [
            {
                **base,
                "message_id": uuid4(),
                "text": initial,
                "summary": initial,
                "created_at": created_at,
            },
            {
                **base,
                "message_id": uuid4(),
                "text": followup,
                "summary": followup,
                "created_at": created_at + timedelta(minutes=45),
            },
        ]
    )

    assert len(events) == 1
    assert len(events[0].evidence_ids) == 2


def test_one_multi_resident_source_can_back_separate_events():
    shared_id = uuid4()
    created_at = datetime(2026, 7, 29, 1, 10, tzinfo=timezone.utc)
    candidates = [
        {
            "message_id": shared_id,
            "resident_id": uuid4(),
            "resident_name": f"시설(가명){number:03d}",
            "text": f"시설(가명){number:03d} 어르신 식사 상태 확인",
            "summary": "식사 상태 확인",
            "record_usage_tags": ["care_service"],
            "room_name": "시설 전체방",
            "sender_name": "요양보호사 01",
            "created_at": created_at,
        }
        for number in range(1, 6)
    ]
    events = _group_record_events(candidates)
    assert len(events) == 5
    assert all(event.evidence_ids == [shared_id] for event in events)
    assert len({event.event_group_id for event in events}) == 5


def test_linked_general_chat_is_not_forced_into_care_service_record():
    assert tags("오늘 직원 회의는 오후 4시에 시작합니다.") == {"general"}


def test_record_usage_does_not_treat_reservation_as_medication():
    reservation = tags(
        "시설(가명)001 어르신 보호자와 다음 주 면회를 예약했습니다."
    )
    medication = tags(
        "시설(가명)001 어르신 저녁 약을 드리고 삼키신 것을 확인했습니다."
    )

    assert "nursing" not in reservation
    assert "nursing" in medication
