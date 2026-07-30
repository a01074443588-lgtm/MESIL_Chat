from datetime import datetime, timedelta, timezone
from uuid import uuid4

from app.main import _group_record_events, _record_usage_tags
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
            {**base, "message_id": first_id, "created_at": created_at},
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
