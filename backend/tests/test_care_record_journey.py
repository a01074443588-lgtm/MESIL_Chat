from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
from uuid import uuid4
from pathlib import Path
import json

from app.care_record_journey import answer_record_question, build_care_topics


START = datetime(2026, 9, 1, tzinfo=timezone.utc)
END = START + timedelta(days=7)


def _source(resident, body, *, day=0, comments=(), attachments=(), message_id=None):
    return NS(
        message=NS(
            id=message_id or uuid4(),
            created_at=START + timedelta(days=day),
            resident=resident,
            resident_links=[],
            body=body,
            attachments=list(attachments),
            reply_to=None,
            is_recalled=False,
        ),
        comments=[NS(id=uuid4(), body=body, created_at=at) for at, body in comments],
    )


def _build(sources, **kwargs):
    return build_care_topics(
        sources=sources,
        period_start=START,
        period_end=END,
        split_resident_text=lambda text, **_: text,
        **kwargs,
    )


def test_recovery_and_numeric_negation_remain_linked_to_actual_reply():
    resident = NS(id=uuid4(), display_name="어르0003")
    source = _source(
        resident,
        "09:00 이동 중 비틀거려 부축함.",
        comments=[
            (
                START + timedelta(hours=1),
                "09:30 휴식 후 어지럼 없음. 보행 안정되어 이동 완료함.",
            ),
        ],
    )
    topics = _build([source])
    assert len(topics) == 1
    topic = topics[0]
    assert "09:00" in topic["summary"] and "09:30" in topic["summary"]
    assert "어지럼 없음" in topic["summary"] and "이동 완료함" in topic["summary"]
    assert topic["evidence_ids"] == [source.message.id]
    assert topic["entries"][1]["comment_id"] == source.comments[0].id
    assert topic["entries"][1]["kind"] == "followup"


def test_same_observation_over_days_is_repeated_not_deterioration():
    resident = NS(id=uuid4(), display_name="어르0003")
    sources = [_source(resident, "점심 식사 1/2 섭취함.", day=day) for day in [0, 2, 4]]
    topic = _build(sources)[0]
    assert topic["kinds"] == ["repeated"]
    assert "3일" in topic["summary"] and "1/2" in topic["summary"]
    assert len(topic["entries"]) == 3 and len(topic["evidence_ids"]) == 3
    assert "악화" not in topic["summary"]


def test_different_residents_and_independent_events_are_not_merged():
    resident = NS(id=uuid4(), display_name="어르0003")
    other = NS(id=uuid4(), display_name="어르0004")
    sources = [
        _source(resident, "이동 중 부축함."),
        _source(resident, "이동 중 부축함.", day=2),
        _source(other, "이동 중 부축함."),
    ]
    topics = _build(sources)
    assert len(topics) == 2
    selected = _build(sources, resident_id=resident.id)
    assert len(selected) == 1
    assert len({entry["event_key"] for entry in selected[0]["entries"]}) == 2


def test_changed_quantities_are_not_repeated_or_automatically_conflicting():
    resident = NS(id=uuid4(), display_name="어르0003")
    topics = _build(
        [
            _source(resident, "점심 식사 1/2 섭취함."),
            _source(resident, "점심 식사 3/4 섭취함.", day=1),
        ]
    )
    assert len(topics) == 2
    assert all(topic["kinds"] == ["event"] for topic in topics)
    assert "1/2" in str(topics) and "3/4" in str(topics)
    conflict = _build(
        [
            _source(
                resident, "혈압 120/70과 170/90으로 서로 다른 기록이며 원문 확인 필요."
            )
        ]
    )[0]
    assert conflict["kinds"] == ["conflict"]
    assert "120/70" in conflict["summary"] and "170/90" in conflict["summary"]


def test_explicit_difference_is_distinguished_without_inventing_diagnosis():
    resident = NS(id=uuid4(), display_name="어르0003")
    topic = _build([_source(resident, "평소보다 식사 섭취량이 줄어 점심 1/2 섭취함.")])[
        0
    ]
    assert topic["kinds"] == ["different"]
    assert topic["summary"] == "평소보다 식사 섭취량이 줄어 점심 1/2 섭취함."


def test_period_boundaries_include_in_period_reply_without_claiming_old_onset():
    resident = NS(id=uuid4(), display_name="어르0003")
    source = _source(
        resident,
        "08:00 이동 중 넘어짐.",
        day=-2,
        comments=[
            (START + timedelta(days=1), "이동 부축 후 보행 안정됨."),
            (END, "새로 이동 중 넘어짐."),
        ],
    )
    topic = _build([source])[0]
    assert len(topic["entries"]) == 1
    assert "넘어짐" not in topic["summary"]
    assert topic["entries"][0]["comment_id"] == source.comments[0].id


def test_full_attachment_uses_staff_confirmation_and_preserves_origin():
    resident = NS(id=uuid4(), display_name="어르0003")
    attachment = NS(
        id=uuid4(),
        mime_type="image/png",
        text_extraction=NS(
            status="reviewed",
            latest_confirmed_text="14:00 물 200mL 섭취함.",
            reviewed_text="물 20mL 섭취함.",
            extracted_text="물 2mL 섭취함.",
        ),
    )
    topic = _build([_source(resident, "사진 첨부", attachments=[attachment])])[0]
    assert "200mL" in topic["summary"]
    assert topic["entries"][0]["attachment_id"] == attachment.id
    assert topic["entries"][0]["source_kind"] == "attachment"


def test_long_parent_does_not_truncate_recovery_reply():
    resident = NS(id=uuid4(), display_name="어르0003")
    source = _source(
        resident,
        "이동 중 비틀거림. " + "일반 전달 사항. " * 160,
        comments=[
            (START + timedelta(hours=1), "이동 부축 후 어지럼 없음. 보행 안정됨."),
        ],
    )
    topic = _build([source])[0]
    assert "어지럼 없음" in topic["summary"]


def test_first_found_answer_is_scoped_and_does_not_claim_true_onset():
    resident = NS(id=uuid4(), display_name="어르0003")
    topics = _build(
        [
            _source(resident, "이동 자립함."),
            _source(resident, "이동 도움 받아 부축함.", day=2),
        ]
    )
    answer = answer_record_question(
        question="이동 도움을 받은 기록은 언제부터 있나요?", topics=topics
    )
    assert "09/03" in answer["answer"] and "09/01" not in answer["answer"]
    assert "선택 기간에서 처음" in answer["answer"]
    assert "시작일을 뜻하지" in answer["limitation"]


def test_questions_retrieve_followups_repetitions_and_no_unfounded_causes():
    resident = NS(id=uuid4(), display_name="어르0003")
    topics = _build(
        [
            _source(
                resident,
                "이동 부축함.",
                comments=[(START + timedelta(hours=1), "이동 안정되어 완료함.")],
            ),
            _source(resident, "점심 1/2 섭취함."),
            _source(resident, "점심 1/2 섭취함.", day=2),
        ]
    )
    assert (
        "완료함"
        in answer_record_question(question="이후 경과는 어땠나요?", topics=topics)[
            "answer"
        ]
    )
    repeated = answer_record_question(
        question="비슷한 기록이 또 있나요?", topics=topics
    )
    assert repeated["matched_count"] == 2
    partial = answer_record_question(question="넘어진 원인이 무엇인가요?", topics=topics)
    assert partial["evidence_ids"]
    assert "부축함" in partial["answer"]
    assert any("원인은 기록에서 확인되지" in text for text in partial["unknowns"])
    assert (
        answer_record_question(question="수면은 어땠나요?", topics=topics)[
            "matched_count"
        ]
        == 0
    )


def test_missing_topics_are_not_filled_and_no_source_means_no_answer():
    resident = NS(id=uuid4(), display_name="어르0003")
    assert _build([_source(resident, "감사합니다.")]) == []
    assert (
        answer_record_question(question="이후 경과는 어땠나요?", topics=[])[
            "matched_count"
        ]
        == 0
    )


def test_single_stable_observation_is_not_claimed_to_be_a_followup():
    resident = NS(id=uuid4(), display_name="어르0003")
    topics = _build([_source(resident, "보행 안정됨. 통증 없음.")])
    assert all(topic["kinds"] == ["event"] for topic in topics)
    answer = answer_record_question(question="이후 경과는 어땠나요?", topics=topics)
    assert answer["matched_count"] == 1 and "통증 없음" in answer["answer"]
    assert any("후속 결과" in text for text in answer["unknowns"])


def test_ordinary_injury_and_symptom_words_are_not_numeric_record_conflicts():
    resident = NS(id=uuid4(), display_name="어르0001")
    for text in (
        "보행 시 한 명의 부축이 계속 필요했고 외상이나 통증은 없었습니다.",
        "이동 시 특이사항 없음.",
        "어지럼 증상이 없음.",
        "피부 이상이 없었습니다.",
    ):
        topics = _build([_source(resident, text)])
        assert topics and all("conflict" not in topic["kinds"] for topic in topics), text
    for text in (
        "혈압 기록이 서로 상이함. 120/70과 170/90으로 기록됨.",
        "체온 37.2도와 37.8도 기록은 상이합니다.",
        "혈압 기록이 상이하여 원문 확인이 필요합니다.",
    ):
        topics = _build([_source(resident, text)])
        assert topics and any("conflict" in topic["kinds"] for topic in topics), text


def test_earliest_help_does_not_treat_no_help_or_future_help_as_actual_help():
    resident = NS(id=uuid4(), display_name="어르0003")
    topics = _build(
        [
            _source(resident, "도움 없이 이동함."),
            _source(resident, "이동 부축 필요 없음.", day=1),
            _source(resident, "내일 이동 부축 예정.", day=2),
            _source(resident, "이동 도움을 받아 생활실로 이동함.", day=3),
        ]
    )
    answer = answer_record_question(
        question="이동 도움을 받은 기록은 언제부터 있나요?", topics=topics
    )
    assert "09/04" in answer["answer"]
    assert "09/01" not in answer["answer"] and "09/03" not in answer["answer"]


def test_existing_synthetic_cases_keep_incidents_outcomes_repetition_and_conflicts():
    fixture = json.loads(
        (
            Path(__file__).parent / "fixtures/finalist_coded_synthetic_v1/fixture.json"
        ).read_text(encoding="utf-8-sig")
    )
    targets = {
        "SYN-EVT-008",
        "SYN-EVT-013",
        "SYN-EVT-003",
        "SYN-EVT-012",
        "SYN-SOURCE-01-003",
        "SYN-SOURCE-01-027",
    }
    residents = {}
    sources, ids = [], {}
    for record in fixture["messages"]:
        if record["event_id"] not in targets:
            continue
        resident = residents.setdefault(
            record["resident_code"],
            NS(id=uuid4(), display_name=record["resident_code"]),
        )
        source = _source(resident, record["body"])
        source.message.created_at = datetime.fromisoformat(record["created_at"])
        source.comments = [
            NS(
                id=uuid4(),
                body=comment["body"],
                created_at=datetime.fromisoformat(comment["created_at"]),
            )
            for comment in record["comments"]
        ]
        ids[record["event_id"]] = source.message.id
        sources.append(source)
    # Surround the important event with unrelated routine records for the same
    # resident and topic: neither may hide the incident and its recovery.
    for at in (
        datetime(2026, 5, 1, tzinfo=timezone.utc),
        datetime(2026, 8, 25, tzinfo=timezone.utc),
    ):
        source = _source(residents["어르0002"], "이동 보행 안정됨.")
        source.message.created_at = at
        sources.append(source)
    topics = build_care_topics(
        sources=sources,
        period_start=datetime(2026, 5, 1, tzinfo=timezone.utc),
        period_end=datetime(2026, 9, 1, tzinfo=timezone.utc),
        split_resident_text=lambda text, **_: text,
    )

    def by_event(event):
        return next(topic for topic in topics if ids[event] in topic["evidence_ids"])

    recovery = by_event("SYN-EVT-008")
    assert recovery["topic"] == "이동"
    assert all(
        fact in recovery["summary"]
        for fact in ("주저앉", "골절은 없고", "염좌", "귀원")
    )
    assert len(recovery["entries"]) == 4
    nutrition = by_event("SYN-EVT-013")
    assert "절반" in nutrition["summary"] and "3/4" in nutrition["summary"]
    assert "different" in nutrition["kinds"]
    conflict = by_event("SYN-EVT-003")
    assert "conflict" in conflict["kinds"]
    assert all(
        fact in conflict["summary"] for fact in ("37.2", "37.8", "측정 시각이 달라")
    )
    sleep = by_event("SYN-SOURCE-01-003")
    assert sleep["topic"] == "수면" and "repeated" in sleep["kinds"]
    assert ids["SYN-SOURCE-01-027"] in sleep["evidence_ids"]
    assert "피로 호소 없이" in sleep["summary"]
    pending = by_event("SYN-EVT-012")
    assert pending["topic"] == "보호자 상담"
    assert (
        "회신을 기다리고" in pending["summary"] and "followup" not in pending["kinds"]
    )
