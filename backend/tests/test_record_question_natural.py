"""Natural wording, semantic evidence selection and unchanged safety boundaries."""

import json
from datetime import date, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from app import record_text_ai as ai
from app.record_question import plan_question, answer_facts, question_date_window
from test_care_record_journey import _source, _build, START
import test_record_text_ai as record_test_helpers

policy = record_test_helpers.policy
run = record_test_helpers.run

QUESTIONS = [
    "최근에 어떠셨어?",
    "전보다 달라진 게 있어?",
    "보호자에게 말할 내용 있어?",
    "병원은 왜 다녀왔어?",
    "그 뒤에는 괜찮아졌어?",
    "거부하신 적 있어?",
    "지난달 중요한 내용 알려줘.",
    "이분에 관해 알아야 할 것 정리해줘.",
    "요즘은 어떤 상태야?",
    "기록에 나온 진단이 있어?",
]


def fixture_topics():
    person = NS(id=uuid4(), display_name="어르0003")
    sources = [
        _source(
            person,
            "이동 중 비틀거려 병원 진료를 받음. 진단서에 손목 염좌 진단이 적혀 있음.",
            comments=[(START + timedelta(hours=1), "귀원 후 통증 없고 보행 안정됨.")],
        ),
        _source(
            person,
            "점심 1/2 섭취함.",
            day=1,
            comments=[(START + timedelta(days=1, hours=1), "이후 점심 3/4 섭취함.")],
        ),
        _source(person, "체조 참여를 거부함. 보호자에게 안내함.", day=2),
        _source(person, "빨간 양말을 파란 양말로 갈아 신었음.", day=3),
    ]
    return person, _build(sources, include_unclassified=True)


@pytest.mark.parametrize(
    "question",
    QUESTIONS
    + [
        "어르0003의 대화내용을 요약해주세요",
        "좀 알려주실래요?",
        "컨디션 어때요?",
        "양말은 바꿨나요?",
        "식사는 어떠셨습니까?",
    ],
)
def test_natural_questions_reach_evidence_without_required_topic(question):
    person, topics = fixture_topics()
    plan = plan_question(
        question, topics, resident_id=person.id, today=date(2026, 10, 9)
    )
    assert plan["facts"], question
    result = answer_facts(
        question,
        plan["rule_facts"],
        scope_count=plan["scope_count"],
        notes=plan["notes"],
    )
    # A novel paraphrase may need the model, but the ten required conversational
    # questions must also produce useful exact-source material offline.
    if question in QUESTIONS or "요약" in question or "양말" in question:
        assert result["evidence_ids"] and result["timeline"], question
    assert "질문의 주제" not in result["answer"]
    by_id = {fact["message_id"]: [] for fact in plan["rule_facts"]}
    for fact in plan["rule_facts"]:
        by_id[fact["message_id"]].append(fact["summary"])
    assert all(
        row["fact"] in by_id[row["evidence_ids"][0]] for row in result["timeline"]
    )


def test_unclassified_record_is_searchable_without_changing_briefing_display():
    person = NS(id=uuid4(), display_name="어르0003")
    source = _source(person, "빨간 양말을 파란 양말로 갈아 신었음.")
    assert not _build([source])
    topics = _build([source], include_unclassified=True)
    plan = plan_question("양말 바꾸셨나요?", topics, resident_id=person.id)
    assert plan["rule_facts"][0]["summary"] == source.message.body


def test_rule_answer_evidence_excludes_unshown_candidate_timeline_records():
    facts = [
        {
            "message_id": uuid4(), "summary": summary,
            "occurred_at": START + timedelta(days=index),
            "resident_id": "synthetic-resident", "resident_name": "합성대상",
            "kind": "event", "event_key": f"event-{index}",
        }
        for index, summary in enumerate([
            "첫 기록입니다.", "중간 기록 하나입니다.", "중간 기록 둘입니다.",
            "최근 전 기록입니다.", "가장 최근 기록입니다.",
        ])
    ]
    result = answer_facts("최근에 어떠셨어?", facts, scope_count=len(facts))
    assert len(result["timeline"]) == 5
    assert len(result["evidence_ids"]) == 3
    visible_text = result["answer"]
    evidence_by_id = {fact["message_id"]: fact["summary"] for fact in facts}
    assert all(evidence_by_id[evidence_id] in visible_text for evidence_id in result["evidence_ids"])


def test_all_residents_name_filter_ambiguity_and_selected_scope():
    person, topics = fixture_topics()
    other = NS(id=uuid4(), display_name="어르0004")
    topics += _build(
        [_source(other, "병원 진료 후 어지럼 없음.")], include_unclassified=True
    )
    named = plan_question("어르0003의 대화내용을 요약해주세요", topics)
    assert {f["resident_id"] for f in named["facts"]} == {person.id}
    assert not plan_question("전체 중요한 내용을 정리해줘", topics)["ambiguous"]
    assert plan_question("그 뒤에는 괜찮아졌어?", topics)["ambiguous"]
    assert not plan_question("그 뒤에는 괜찮아졌어?", topics, resident_id=person.id)[
        "ambiguous"
    ]
    assert not plan_question("어르0004 이야기 요약해줘", topics, resident_id=person.id)[
        "facts"
    ]
    unique = plan_question("양말을 바꾸셨나?", topics)
    assert {f["resident_id"] for f in unique["rule_facts"]} == {person.id}


def test_no_medical_word_block_and_unknowns_are_separate_from_recorded_facts():
    person, topics = fixture_topics()
    plan = plan_question(
        "병원은 왜 다녀왔고 기록에 진단이나 처방이 있어?", topics, resident_id=person.id
    )
    answer = answer_facts(
        plan["question"], plan["rule_facts"], scope_count=plan["scope_count"]
    )
    assert "염좌 진단" in str(answer["timeline"])
    assert "통증 없고" in str(answer["timeline"])
    assert any("원인은 기록에서 확인되지" in item for item in answer["unknowns"])
    assert not any(
        "진단 내용을 확인할 수 없습니다" in item for item in answer["unknowns"]
    )
    short = _build([_source(person, "보행 안정됨. 통증 없음.")])
    plan = plan_question("기록에 진단이 있어?", short, resident_id=person.id)
    answer = answer_facts(
        plan["question"], plan["rule_facts"], scope_count=plan["scope_count"]
    )
    assert any("진단 내용을 확인할 수 없습니다" in item for item in answer["unknowns"])


def test_relative_month_only_narrows_the_authorized_scope():
    person, topics = fixture_topics()
    assert plan_question(
        "지난달 중요한 내용", topics, resident_id=person.id, today=date(2026, 10, 9)
    )["facts"]
    none = plan_question(
        "지난달 중요한 내용", topics, resident_id=person.id, today=date(2026, 9, 9)
    )
    assert not none["facts"] and "2026-08-01~2026-08-31" in none["notes"][0]


def test_conversational_relative_dates_resolve_in_the_institution_timezone():
    reference=date(2026,9,11)
    assert question_date_window("어제 오늘 중요한 일 정리해줘",reference)==(
        date(2026,9,10),date(2026,9,11))
    assert question_date_window("최근 3일 동안 어떠셨어?",reference)==(
        date(2026,9,9),date(2026,9,11))
    assert question_date_window("이번 주 일정 알려줘",reference)==(
        date(2026,9,7),date(2026,9,11))
    assert question_date_window("지난달 중요한 내용",reference)==(
        date(2026,8,1),date(2026,8,31))
    assert question_date_window("최근에 어떠셨어?",reference) is None


def test_unrelated_question_and_empty_scope_have_distinct_explanations():
    _, topics = fixture_topics()
    plan = plan_question("달의 공전주기가 얼마인가요?", topics)
    assert plan["facts"]  # Semantic selection remains possible without a literal match.
    assert not plan["rule_facts"]
    result = answer_facts(plan["question"], [], scope_count=plan["scope_count"])
    assert "관련된 기록을 찾지 못" in result["answer"]
    assert "현재 접근 가능한 기록이 없습니다" in answer_facts("어때?", [])["answer"]


def test_model_interprets_unmatched_phrase_and_server_expands_whole_event(
    policy, monkeypatch
):
    person, topics = fixture_topics()
    plan = plan_question("발에 신으시는 건 달라졌나?", topics, resident_id=person.id)
    captured = []

    def transport(base, path, body, timeout):
        if path == "/api/show":
            return {"capabilities": ["completion"]}
        data = json.loads(body["messages"][1]["content"])
        captured.append(data)
        chosen = next(row["id"] for row in data["records"] if "양말" in row["text"])
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"relevant_sources": [chosen]})},
        }

    monkeypatch.setattr(ai, "local_json_request", transport)
    result = run(
        question=plan["question"],
        facts=plan["facts"],
        names=[person.display_name],
        semantic_selection=True,
    )
    assert result["processing_method"] == "local_ai" and "양말" in result["answer"]
    assert all(f["resident_id"] == person.id for f in result["selected_facts"])
    assert person.display_name not in json.dumps(captured, ensure_ascii=False)
    assert all("event" in row for row in captured[0]["records"])


@pytest.mark.parametrize("invented", [False, True])
def test_semantic_ids_do_not_bypass_event_negation_or_permission_evidence(
    policy, monkeypatch, invented
):
    person, topics = fixture_topics()
    plan = plan_question("병원 왜 다녀왔어", topics, resident_id=person.id)

    def transport(base, path, body, timeout):
        if path == "/api/show":
            return {}
        data = json.loads(body["messages"][1]["content"])
        chosen = (
            "S999"
            if invented
            else next(row["id"] for row in data["records"] if "염좌" in row["text"])
        )
        return {
            "model": body["model"],
            "done": True,
            "message": {"content": json.dumps({"relevant_sources": [chosen]})},
        }

    monkeypatch.setattr(ai, "local_json_request", transport)
    result = run(facts=plan["facts"], semantic_selection=True)
    if invented:
        assert result["processing_method"] == "rules" and "answer" not in result
    else:
        assert result["processing_method"] == "local_ai"
        assert any("통증 없고" in row["fact"] for row in result["timeline"])
        assert any("염좌" in row["fact"] for row in result["timeline"])


def test_summary_focus_is_still_exact_evidence_and_bare_name_is_not_a_fact():
    person = NS(id=uuid4(), display_name="어르0003")
    assert not _build([_source(person, person.display_name)], include_unclassified=True)
    _, topics = fixture_topics()
    plan = plan_question("요약해줘", topics)
    facts = plan["rule_facts"]
    focus = next(fact for fact in facts if "거부" in fact["summary"])
    answer = answer_facts("거부하신 적 있어?", facts, focus=[focus])
    assert focus["summary"] in answer["answer"]
    assert len(answer["timeline"]) == len(facts)
    invented = {**focus, "summary": "새 진단을 만들었습니다."}
    assert "새 진단" not in answer_facts("알려줘", facts, focus=[invented])["answer"]
