"""Context refusals must not become retryable generic errors or fake answers."""
import asyncio
import json
from time import perf_counter

import httpx
import pytest

from app import record_narrative as n
from test_record_narrative import GOOD, setup_model


CONTEXT_ERROR = 'request (8807 tokens) exceeds the available context size (8192 tokens), try increasing it'


@pytest.mark.parametrize('status,error,expected', [
    (400, CONTEXT_ERROR, 'context_limit'),
    (400, {'message': CONTEXT_ERROR, 'type': 'exceed_context_size_error'}, 'context_limit'),
    (400, {'message': 'invalid request', 'echo': CONTEXT_ERROR}, None),
    (400, [CONTEXT_ERROR], None),
    (400, 'invalid option: context_length', None),
    (500, 'internal error while checking context', None),
    (400, 'CUDA out of memory', 'gpu_memory_insufficient'),
])
def test_local_http_error_is_classified_without_exposing_server_body(status, error, expected):
    async def run():
        transport = httpx.MockTransport(lambda request: httpx.Response(status, json={'error': error}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(n.RecordModelError if expected else httpx.HTTPStatusError) as caught:
                await n.local_request(client, 'http://127.0.0.1:11434', '/api/chat', {}, 1)
        if expected:
            assert str(caught.value) == expected
    asyncio.run(run())


def test_upstream_context_refusal_ends_generation_without_retry_or_answer(monkeypatch):
    actual_request = n.local_request
    args = setup_model(monkeypatch, actual_request)
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': [{'name': 'synthetic-model'}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={})
        return httpx.Response(400, json={'error': CONTEXT_ERROR})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(n.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    result = asyncio.run(n.generate_narrative(**args, request_key='upstream-context', deadline=perf_counter()+8))
    assert result['error_type'] == 'context_limit' and not result['generation_verified']
    assert 'answer' not in result and 'sentences' not in result
    assert paths == ['/api/ps', '/api/show', '/api/chat']


@pytest.mark.parametrize('stage', ['system_prompt', 'schema', 'correction', 'review'])
def test_every_generation_stage_checks_complete_request_without_silent_trimming(monkeypatch, stage):
    chats = []
    async def request(client, base, path, body, timeout):
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic-model'}]}
        if path == '/api/show':
            return {}
        chats.append(body)
        if stage == 'correction':
            value = {'sentences': [], 'invalid_extra': '합성' * 11000}
        elif 'supported' in body['format'].get('properties', {}):
            value = {'supported': [True, True], 'answers_question': True}
        else:
            value = GOOD
        return {'model': 'synthetic-model', 'done': True, 'message': {'content': json.dumps(value)}}
    args = setup_model(monkeypatch, request)
    if stage == 'system_prompt':
        monkeypatch.setattr(n, 'DRAFT_INSTRUCTION', '합성' * 11000)
    elif stage == 'schema':
        monkeypatch.setattr(n.GroundedDraft, 'model_json_schema', lambda: {'description': '합성' * 11000})
    elif stage == 'review':
        monkeypatch.setattr(n, 'REVIEW_INSTRUCTION', '합성' * 11000)
    result = asyncio.run(n.generate_narrative(**args, request_key='context-' + stage, deadline=perf_counter()+8))
    assert result['error_type'] == 'context_limit'
    assert result['generation_verified'] is False
    assert 'answer' not in result and 'sentences' not in result
    assert len(chats) == (1 if stage in {'correction', 'review'} else 0)


def test_small_complete_request_preserves_sources_and_generates_verified_answer(monkeypatch):
    chats = []
    async def request(client, base, path, body, timeout):
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic-model'}]}
        if path == '/api/show':
            return {}
        chats.append(body)
        value = ({'supported': [True, True], 'answers_question': True}
                 if 'supported' in body['format']['properties'] else GOOD)
        return {'model': 'synthetic-model', 'done': True, 'message': {'content': json.dumps(value)}}
    args = setup_model(monkeypatch, request)
    result = asyncio.run(n.generate_narrative(**args, request_key='small-context', deadline=perf_counter()+8))
    assert result['generation_verified'] is True and result['processing_method'] == 'local_ai'
    assert len(chats) == 2
    records = json.loads(chats[0]['messages'][1]['content'])['records']
    assert [r['text'] for r in records] == ['식사 1/2 섭취함. 불편 없음.', '식사 3/4 섭취함. 불편 없음.']
    assert chats[0]['options']['num_predict'] == 512 and chats[1]['options']['num_predict'] == 80


@pytest.mark.parametrize('profile', [None, '{"unqualified":true}'])
def test_cold_permission_cannot_resize_an_already_loaded_model(monkeypatch, profile):
    """A cold-start flag must not bypass the existing no-resize admission rule."""
    args = setup_model(monkeypatch, n.local_request)
    policy = n.effective_central_models(None)
    policy.context_tokens = 16384
    monkeypatch.setattr(n.settings, 'record_ai_gpu_capacity_profile_json', profile)
    paths = []
    def handler(request):
        paths.append(request.url.path)
        if request.url.path == '/api/ps':
            return httpx.Response(200, json={'models': [
                {'name': 'synthetic-model', 'context_length': 8192, 'size_vram': 20000000000}]})
        if request.url.path == '/api/show':
            return httpx.Response(200, json={})
        body = json.loads(request.content)
        value = ({'supported': [True, True], 'answers_question': True}
                 if 'supported' in body['format']['properties'] else GOOD)
        return httpx.Response(200, json={'model': 'synthetic-model', 'done': True,
            'done_reason': 'stop', 'message': {'content': json.dumps(value)}})
    original_client = httpx.AsyncClient
    monkeypatch.setattr(n.httpx, 'AsyncClient', lambda **kwargs: original_client(
        **kwargs, transport=httpx.MockTransport(handler)))
    result = asyncio.run(n.generate_narrative(**args, request_key='context-resize',
        deadline=perf_counter()+8, allow_cold_start=True))
    assert result['error_type'] == 'model_prepare_busy'
    assert result['generation_verified'] is False and 'answer' not in result
    assert paths == ['/api/ps', '/api/show']


def test_same_context_loaded_model_remains_usable_with_cold_permission(monkeypatch):
    chats = []
    async def request(client, base, path, body, timeout):
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic-model', 'context_length': 16384}]}
        if path == '/api/show':
            return {}
        chats.append(body)
        value = ({'supported': [True, True], 'answers_question': True}
                 if 'supported' in body['format']['properties'] else GOOD)
        return {'model': 'synthetic-model', 'done': True, 'done_reason': 'stop',
                'message': {'content': json.dumps(value)}}
    args = setup_model(monkeypatch, request)
    n.effective_central_models(None).context_tokens = 16384
    result = asyncio.run(n.generate_narrative(**args, request_key='context-warm',
        deadline=perf_counter()+8, allow_cold_start=True))
    assert result['generation_verified'] is True
    assert len(chats) == 2 and all(body['options']['num_ctx'] == 16384 for body in chats)
    assert [r['text'] for r in json.loads(chats[0]['messages'][1]['content'])['records']] == [
        '식사 1/2 섭취함. 불편 없음.', '식사 3/4 섭취함. 불편 없음.']


@pytest.mark.parametrize('corrected_role,review_supported,expected_success', [
    ('conclusion', True, True),
    ('conclusion', False, False),
    ('latest', True, False),
])
def test_nonreturnable_draft_is_corrected_before_spending_review_call(
        monkeypatch, corrected_role, review_supported, expected_success):
    """No review can add a missing conclusion; final answers still need review."""
    stages = []
    drafts = 0
    async def request(client, base, path, body, timeout):
        nonlocal drafts
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic-model', 'context_length': 4096}]}
        if path == '/api/show':
            return {}
        stage = body['format']['title']
        stages.append(stage)
        if stage == 'GroundedDraft':
            drafts += 1
            value = {'sentences': [{**GOOD['sentences'][1],
                'role': 'latest' if drafts == 1 else corrected_role}]}
        else:
            value = {'supported': [review_supported], 'answers_question': True}
        return {'model': 'synthetic-model', 'done': True, 'done_reason': 'stop',
                'message': {'content': json.dumps(value)}}
    args = setup_model(monkeypatch, request)
    result = asyncio.run(n.generate_narrative(**args, request_key='review-short-circuit',
        deadline=perf_counter()+8))
    assert stages == (['GroundedDraft', 'GroundedDraft', 'GroundingReview']
                      if corrected_role == 'conclusion' else ['GroundedDraft', 'GroundedDraft'])
    assert result['generation_verified'] is expected_success
    assert result['correction_attempted'] is True
    if expected_success:
        assert result['answer'] == '가장 최근 기록에는 식사 3/4 섭취했습니다.'
        assert result['evidence_ids'] == [args['facts'][1]['message_id']]
    else:
        assert result['error_type'] == 'question_answer_not_supported'
        assert 'answer' not in result and 'sentences' not in result


@pytest.mark.parametrize('review_result,expected_rejected,expected_error', [
    ('partial', 2, None),
    ('all_rejected', 3, 'question_answer_not_supported'),
    ('timeout', 1, 'timeout'),
])
def test_rejections_accumulate_across_drafts_and_review_even_on_failure(
        monkeypatch, review_result, expected_rejected, expected_error):
    """Final guard counts must not hide prior draft or independent review losses."""
    draft_count = 0

    async def request(client, base, path, body, timeout):
        nonlocal draft_count
        if path == '/api/ps':
            return {'models': [{'name': 'synthetic-model', 'context_length': 4096}]}
        if path == '/api/show':
            return {}
        if body['format']['title'] == 'GroundedDraft':
            draft_count += 1
            value = ({'sentences': [
                {'text': '식사 9/9 섭취했습니다.', 'citations': ['S1'], 'role': 'conclusion'},
                {**GOOD['sentences'][1], 'role': 'latest'},
            ]} if draft_count == 1 else GOOD)
        else:
            if review_result == 'timeout':
                raise httpx.ReadTimeout('synthetic bounded review timeout')
            value = {'supported': [review_result == 'partial', False], 'answers_question': True}
        return {'model': 'synthetic-model', 'done': True, 'done_reason': 'stop',
                'message': {'content': json.dumps(value)}}

    args = setup_model(monkeypatch, request)
    result = asyncio.run(n.generate_narrative(**args, request_key='cumulative-rejections',
        deadline=perf_counter()+8))
    assert result['error_type'] == expected_error
    assert result['generation_verified'] is (expected_error is None)
    if expected_error is None:
        assert result['answer'] == '이전 기록에는 식사 1/2 섭취했습니다.'
        assert result['evidence_ids'] == [args['facts'][0]['message_id']]
    else:
        assert 'answer' not in result and 'sentences' not in result
    assert result['rejected_sentence_count'] == expected_rejected
