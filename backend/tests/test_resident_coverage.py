"""Actual question endpoint and model guards; only DB/model I/O is synthetic."""
import ast
import asyncio
from copy import deepcopy
from datetime import date, datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
import re
from time import perf_counter
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock
from uuid import UUID

from fastapi import HTTPException
import pytest
from app import record_narrative as narrative, record_question as questions
from app.schemas import RecordQuestionRequest, RecordQuestionResponse

QUESTION = '전체 어르신의 최근 7일 상태와 근거를 알려줘.'
DAY = date(2026, 9, 18)


def fact(number, person, text='보행 시 손잡이를 사용했고 불편 호소는 없었습니다.', day=13, event=None):
    return dict(message_id=UUID(int=number), resident_id=UUID(int=person) if person else None,
                resident_name=f'어르{person:04}' if person else '일반 대화', summary=text,
                occurred_at=datetime(2026, 9, day, 1, tzinfo=timezone.utc), kind='event',
                event_key=str(event or number), topic='돌봄')


def run_endpoint(monkeypatch, facts, *, covered=(1, 2, 3), question=QUESTION,
                 fail_model=False, deny_access=False):
    """Load the real candidate endpoint, with no copy of its response logic."""
    source = Path(questions.__file__).with_name('main.py')
    tree = ast.parse(source.read_text(encoding='utf-8'))
    nodes = [node for node in tree.body if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in ('ask_care_record_question', '_no_matching_record_result'))
        or (isinstance(node, ast.ImportFrom) and node.module == 'record_question')]
    for node in nodes:
        if hasattr(node, 'decorator_list'):
            node.decorator_list = []
    policy = NS(real_record_logging_verified=True, base_url='http://internal.invalid',
                timeout_seconds=5, max_input_chars=40000, context_tokens=8192)
    monkeypatch.setattr(narrative, 'load_ai_settings', lambda: (NS(), None))
    monkeypatch.setattr(narrative, 'effective_central_models', lambda _: policy)
    monkeypatch.setattr(narrative, 'central_feature_selection',
                        lambda *_: NS(provider='ollama', model='synthetic'))
    calls = []
    async def transport(client, base, path, body, timeout):
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic', 'context_length': 8192}]}
        if path == '/api/show':
            return {}
        calls.append(path)
        if fail_model:
            raise narrative.RecordModelError('model_connection_error')
        if 'supported' in body['format']['properties']:
            value = {'supported': [True] * len(covered), 'answers_question': True}
        else:
            records = json.loads(body['messages'][-1]['content'])['records']
            value = {'sentences': [
                {'text': f"{records[i-1]['person']}은 9월 13일 보행 시 손잡이를 사용했고 불편 호소는 없었습니다.",
                 'citations': [records[i-1]['id']], 'role': 'conclusion'} for i in covered]}
        return dict(model='synthetic', done=True, done_reason='stop',
                    message={'content': json.dumps(value, ensure_ascii=False)})
    monkeypatch.setattr(narrative, 'local_request', transport)
    payload = RecordQuestionRequest(question=question, start_date=date(2026, 9, 12), end_date=DAY)
    context = dict(payload=payload, result=questions.answer_facts(question, facts), facts=facts,
                   names=list(dict.fromkeys(f['resident_name'] for f in facts)), all_synthetic=True,
                   selected_ids={f['message_id'] for f in facts}, attachment_review_fingerprint='synthetic',
                   resolved_name=None, truncated=False, notes=[])
    recheck = Mock(side_effect=HTTPException(403, 'denied') if deny_access else None)
    namespace = dict(__package__='app', asyncio=asyncio, perf_counter=perf_counter, sha256=sha256, re=re,
        HTTPException=HTTPException, RecordQuestionResponse=RecordQuestionResponse,
        Depends=lambda *_: None, _require_processor=None, get_db=None, _record_question_progress={},
        _prepare_care_record_question=lambda *_: context,
        prepare_record_model=AsyncMock(return_value={'status': 'ready'}),
        generate_narrative=narrative.generate_narrative, await_connected=narrative.await_connected,
        message_review_fingerprint=lambda *_: 'synthetic', _recheck_record_text_access=recheck)
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    result = asyncio.run(namespace['ask_care_record_question'](
        payload, NS(is_disconnected=AsyncMock(return_value=False)), NS(id=UUID(int=99)), None))
    recheck.assert_called_once()
    return result, calls


def test_four_people_one_omitted_is_completed_without_replacing_verified_answer(monkeypatch):
    facts = [fact(i, i) for i in range(1, 5)]
    before = deepcopy(facts)
    result, calls = run_endpoint(monkeypatch, facts, covered=(1, 2, 3, 3))
    assert result.generation_verified and result.error_type is None
    assert '어르0004' in result.answer
    assert result.answer_sentences[0].text == '어르0001은 9월 13일 보행 시 손잡이를 사용했고 불편 호소는 없었습니다.'
    assert '기록 기반 보완' in result.answer_sentences[-1].text
    assert result.answer_sentences[-1].evidence_ids == [UUID(int=4)]
    assert set(result.evidence_ids) == {UUID(int=i) for i in range(1, 5)}
    assert '보완' in result.fallback_notice
    assert calls == ['/api/chat', '/api/chat']  # No extra generation for missing people.
    assert facts == before


def test_four_sentence_model_limit_does_not_hide_people_beyond_four(monkeypatch):
    result, _ = run_endpoint(monkeypatch, [fact(i, i) for i in range(1, 8)], covered=(1, 2, 3, 4))
    assert all(f'어르{i:04}' in result.answer for i in range(1, 8))
    assert len(result.answer_sentences) == 7
    assert len(result.evidence_ids) == 7


def test_same_message_id_is_not_proof_that_another_person_was_answered(monkeypatch):
    result, _ = run_endpoint(monkeypatch, [fact(1, 1), fact(2, 2), fact(1, 4)], covered=(1, 2))
    assert '어르0004' in result.answer
    assert result.answer_sentences[-1].evidence_ids == [UUID(int=1)]


def test_complete_answer_is_not_rewritten_or_marked_as_supplement(monkeypatch):
    result, _ = run_endpoint(monkeypatch, [fact(i, i) for i in range(1, 5)], covered=(1, 2, 3, 4))
    assert result.generation_verified and len(result.answer_sentences) == 4
    assert result.fallback_notice is None
    assert '기록 기반 보완' not in result.answer


@pytest.mark.parametrize('question', [
    '어르0001의 최근 상태 알려줘.',
    '어르0001의 2026년 9월 12일부터 9월 18일까지 상태 알려줘.',
])
def test_non_overview_answer_is_not_expanded(monkeypatch, question):
    result, _ = run_endpoint(monkeypatch, [fact(1, 1), fact(2, 2)], covered=(1,), question=question)
    assert result.generation_verified
    assert len(result.answer_sentences) == 1 and '어르0002' not in result.answer
    assert result.fallback_notice is None


def test_supplement_preserves_latest_event_chain_quantities_and_provision(monkeypatch):
    facts = [fact(i, i) for i in range(1, 4)] + [
        fact(40, 4, '이전 상태 기록.', day=12),
        fact(41, 4, '밥 1/2과 반찬 2~3숟가락 섭취함.', event=44),
        fact(42, 4, '추가 제공 후 밥 1/4 섭취함. 물 180mL 제공함.', event=44)]
    result, _ = run_endpoint(monkeypatch, facts)
    added = result.answer_sentences[-1]
    for value in ('어르0004', '9월 13일', '밥 1/2', '반찬 2~3숟가락', '추가 제공 후 밥 1/4', '물 180mL 제공함'):
        assert value in added.text
    assert '이전 상태' not in added.text and '180mL 섭취' not in added.text
    assert added.evidence_ids == [UUID(int=41), UUID(int=42)]


def test_model_failure_remains_honest_fallback(monkeypatch):
    result, _ = run_endpoint(monkeypatch, [fact(i, i) for i in range(1, 5)], fail_model=True)
    assert not result.generation_verified and result.processing_method == 'rules'
    assert result.performance['model_error_type'] == 'model_connection_error'
    assert 'AI 답변 검증을 완료하지 못해' in result.fallback_notice
    assert all(f'어르{i:04}' in result.answer for i in range(1, 5))


def test_supplement_cannot_bypass_post_generation_access_check(monkeypatch):
    with pytest.raises(HTTPException) as error:
        run_endpoint(monkeypatch, [fact(i, i) for i in range(1, 5)], deny_access=True)
    assert error.value.status_code == 403


def test_nutrition_question_keeps_its_existing_dedicated_answer_path():
    generated = dict(generation_verified=True, selected_facts=[fact(1, 1)],
                     sentences=[{'text': '기존 식사 답변', 'evidence_ids': [UUID(int=1)]}],
                     answer='기존 식사 답변')
    result = questions.complete_resident_overview(
        '전체 어르신의 식사 상태를 알려줘.', [fact(1, 1), fact(2, 2)], generated)
    assert result == generated


def test_no_unattributed_or_out_of_period_facts_added(monkeypatch):
    topics = []
    for entry in [fact(1, 1), fact(2, 2), fact(3, 3),
                  fact(4, 4, day=13), fact(5, 5, day=2), fact(6, None)]:
        topics.append(dict(resident_id=entry['resident_id'], resident_name=entry['resident_name'],
                           topic='돌봄', entries=[entry]))
    question = '전체 어르신의 2026년 9월 12일부터 9월 18일까지 상태와 근거를 알려줘.'
    plan = questions.plan_question(question, topics, today=DAY)
    result, _ = run_endpoint(monkeypatch, plan['facts'], question=question)
    assert all(f'어르{i:04}' in result.answer for i in range(1, 5))
    assert '어르0005' not in result.answer and '일반 대화' not in result.answer
    assert set(result.evidence_ids) == {UUID(int=i) for i in range(1, 5)}
    assert result.period_start == date(2026, 9, 12) and result.period_end == DAY
