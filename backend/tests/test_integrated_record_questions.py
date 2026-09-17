"""Synthetic acceptance cases plus adversarial hydration event boundaries."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import pytest
from app.record_question import plan_question, answer_facts
from app.record_hydration import hydration_answer, hydration_quantity_intent

QUESTIONS = [
    '어르0001의 행동 양상에 대해 말해줘', '어르0001 평소 모습은 어때?',
    '직원이 말하면 어떻게 반응했어?', '전보다 행동이 달라진 게 있어?',
    '어르신 가장 최근에 드신 물의 양은?', '최근 제공받은 물의 양은?',
    '잔량이 있었어?', '물을 얼마나 제공했고 얼마나 드셨어?',
]


def fixture():
    person = uuid4()
    start = datetime(2026, 8, 18, 5, tzinfo=timezone.utc)
    def fact(text, hour, event=None):
        identifier = uuid4()
        return dict(message_id=identifier, resident_id=person, resident_name='어르0001',
                    summary=text, occurred_at=start+timedelta(hours=hour), kind='event',
                    event_key=event or str(identifier))
    water = [fact('오후 2시 물 200ml를 제공했습니다.', 0),
             fact('오후 4시 제공한 물 200ml 전량 섭취를 확인했습니다.', 2)]
    behavior = [fact('활동 시작 시 표정이 굳어 있었습니다. 직원이 말을 걸자 대화하고 참여했습니다.', -6, 'behavior'),
                fact('이후 직원의 안내에 웃으며 반응했습니다. 활동을 끝까지 참여했습니다.', -5, 'behavior')]
    other = [fact('식사 후 식판을 정리했습니다.', -24-index) for index in range(52)]
    return person, water, behavior, other


@pytest.mark.parametrize('question', QUESTIONS)
def test_all_required_questions_keep_only_relevant_evidence(question):
    person, water, behavior, other = fixture()
    topics = [dict(resident_id=person, resident_name='어르0001', topic='기록', entries=water+behavior+other)]
    plan = plan_question(question, topics, resident_id=person)
    result = answer_facts(question, plan['rule_facts'], scope_count=plan['scope_count'])
    assert plan['scope_count'] == 56
    expected = water if question in QUESTIONS[4:] else behavior
    assert set(result['evidence_ids']) == {fact['message_id'] for fact in expected}
    assert {fact['message_id'] for fact in plan['facts']} == {fact['message_id'] for fact in expected}
    if question in QUESTIONS[4:]:
        core = result['structured_facts'][0]
        assert (core['provided_ml'], core['consumed_ml'], core['remaining_ml']) == ('200', '200', '0')
        assert core['remaining_basis'] == 'full_consumption'
        assert core['unknowns'] == []
        assert core['resident_id'] == person
        assert core['offered_at'].endswith('14:00:00+09:00')
        assert core['completed_at'].endswith('16:00:00+09:00')


@pytest.mark.parametrize('mutation', ['person', 'next_day', 'before', 'amount', 'negative', 'short_negative', 'ambiguous', 'partial'])
def test_full_consumption_cannot_be_inferred_across_uncertain_events(mutation):
    _, water, _, _ = fixture()
    if mutation == 'person': water[1]['resident_id'] = uuid4()
    elif mutation == 'next_day': water[1]['occurred_at'] += timedelta(days=1)
    elif mutation == 'before': water[1]['occurred_at'] -= timedelta(hours=4)
    elif mutation == 'amount': water[1]['summary'] = '제공한 물 150ml 전량 섭취를 확인했습니다.'
    elif mutation == 'negative': water[1]['summary'] += ' 확인하지 못했습니다.'
    elif mutation == 'short_negative': water[1]['summary'] = '제공한 물 200ml는 안 마셨습니다.'
    elif mutation == 'ambiguous': water.insert(1, {**water[0], 'message_id': uuid4(), 'event_key':'other-offer'})
    elif mutation == 'partial': water[1]['summary'] = '제공한 물 50ml 섭취를 확인했습니다.'
    result = hydration_answer(QUESTIONS[4], water)
    assert result is None or result['fact']['consumed_ml'] != '200'


def test_remaining_is_separate_and_does_not_imply_unrecorded_consumption():
    _, water, _, _ = fixture()
    water[1]['summary'] = '제공한 물의 잔량 50ml를 확인했습니다.'
    result = hydration_answer('잔량이 있었어?', water)
    assert result['fact']['remaining_ml'] == '50'
    assert result['fact']['consumed_ml'] is None
    assert result['fact']['unknowns'] == ['consumed_ml']


def test_bare_followup_is_linked_only_inside_the_same_event():
    _, water, _, _ = fixture()
    water[1]['summary'] = '전량 섭취를 확인했습니다.'
    assert hydration_answer(QUESTIONS[4], water)['fact']['consumed_ml'] is None
    water[1]['event_key'] = water[0]['event_key']
    assert hydration_answer(QUESTIONS[4], water)['fact']['consumed_ml'] == '200'


def test_new_offer_does_not_replace_latest_completed_consumption():
    _, water, _, _ = fixture()
    newer = {**water[0], 'message_id':uuid4(), 'event_key':'new-water',
             'occurred_at':water[1]['occurred_at']+timedelta(hours=1), 'summary':'물 100ml를 제공했습니다.'}
    assert hydration_answer(QUESTIONS[4], water+[newer])['fact']['consumed_ml'] == '200'
    offered = hydration_answer(QUESTIONS[5], water+[newer])['fact']
    assert offered['provided_ml'] == '100' and offered['consumed_ml'] is None


def test_aggregation_and_nonwater_questions_do_not_use_latest_water_shortcut():
    _, water, _, _ = fixture()
    assert hydration_answer('하루 총 수분 섭취량', water) is None
    assert not hydration_quantity_intent('약물을 얼마나 드셨나요?')
    assert not hydration_quantity_intent('행동 양상이 어때?')
