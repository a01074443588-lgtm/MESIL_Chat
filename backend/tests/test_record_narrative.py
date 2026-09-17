import asyncio
from datetime import datetime, timezone, timedelta
from time import perf_counter
from types import SimpleNamespace
from uuid import uuid4
import pytest
import httpx
from app import record_narrative as n

RECORDS=[{'id':'S1','date':'2026-09-01 09:00','person':'인물1','text':'식사 1/2 섭취함. 불편 없음.'},
         {'id':'S2','date':'2026-09-02 09:00','person':'인물1','text':'식사 3/4 섭취함. 불편 없음.'}]
GOOD={'sentences':[{'text':'이전 기록에는 식사 1/2 섭취했습니다.','citations':['S1']},
                   {'text':'가장 최근 기록에는 식사 3/4 섭취했습니다.','citations':['S2']}]}

def test_complete_clauses_and_latest_evidence_are_required():
    assert len(n.validate_narrative(GOOD,RECORDS)[0])==2
    for text,citation in [('가장 최근 기록에는 식사 4/4 섭취했습니다.','S2'),
                          ('가장 최근 기록에는 불편 있었습니다.','S2'),
                          ('가장 최근 기록에는 9월 3일 호전되었습니다.','S2'),
                          ('가장 최근 기록에는 당뇨 진단을 받았습니다.','S2'),
                          ('가장 최근 기록에는 약 때문에 호전되었습니다.','S2'),
                          ('가장 최근 기록에는 식사 3/4 섭취했습니다.','S99')]:
        with pytest.raises(n.RecordModelError):
            n.validate_narrative({'sentences':[GOOD['sentences'][0],{'text':text,'citations':[citation]}]},RECORDS)
    valid,rejected=n.validate_narrative({'sentences':[*GOOD['sentences'],{'text':'최근 기록에는 완치했습니다.','citations':['S2']}]},RECORDS)
    assert len(valid)==2 and rejected==1

def test_no_duplicate_or_cross_person_status():
    with pytest.raises(n.RecordModelError):n.validate_narrative({'sentences':[GOOD['sentences'][0]]*2},RECORDS)
    other=[RECORDS[0],{**RECORDS[1],'person':'인물2'}]
    with pytest.raises(n.RecordModelError):n.validate_narrative(GOOD,other)

def test_exact_repeated_model_facts_keep_only_first_and_latest_without_changing_sources():
    facts=[{'resident_id':'r1','summary':'같은 관찰','message_id':str(index)} for index in range(5)]
    compact=n.compact_repeated_facts(facts)
    assert [row['message_id'] for row in compact]==['0','4']
    assert len(facts)==5


def test_cold_preparation_has_its_own_budget_not_the_generation_budget(monkeypatch):
    async def request(client,base,path,body,timeout):
        if path=='/api/ps':return {'models':[]}
        if path=='/api/show':return {}
        await asyncio.sleep(.08)
        return {'model':'synthetic-model','done':True,'load_duration':80000000}
    setup_model(monkeypatch,request)
    policy=n.effective_central_models(None)
    policy.timeout_seconds=.02  # Deliberately shorter than a controlled cold load.
    result=asyncio.run(n.prepare_record_model())
    assert result['status']=='ready'
    assert result['cold_load_ms']==80


def test_nonblocking_preparation_delivers_shared_completion_without_reloading(monkeypatch):
    async def run():
        entered=asyncio.Event();release=asyncio.Event();loads=[]
        async def request(client,base,path,body,timeout):
            if path=='/api/ps':return {'models':[]}
            if path=='/api/show':return {}
            loads.append(body)
            entered.set()
            await release.wait()
            return {'model':'synthetic-model','done':True}
        setup_model(monkeypatch,request)
        first=await n.prepare_record_model(wait=False)
        assert first['status']=='preparing'
        assert entered.is_set()
        second=await n.prepare_record_model(wait=False)
        assert second['status']=='preparing'
        release.set()
        result=await n.prepare_record_model()
        assert result['status']=='ready'
        assert len(loads)==1
        assert set(loads[0])=={'model','stream','keep_alive','options'}
    asyncio.run(run())


def test_cancelled_waiter_does_not_cancel_other_waiters_preparation(monkeypatch):
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def request(client,base,path,body,timeout):
            if path=='/api/ps':return {'models':[]}
            if path=='/api/show':return {}
            entered.set();await release.wait()
            return {'model':'synthetic-model','done':True}
        setup_model(monkeypatch,request)
        one=asyncio.create_task(n.prepare_record_model())
        await entered.wait()
        two=asyncio.create_task(n.prepare_record_model())
        one.cancel()
        with pytest.raises(asyncio.CancelledError):await one
        release.set()
        assert (await two)['status']=='ready'
    asyncio.run(run())


def test_ready_poll_hands_off_to_question_without_starting_another_slow_status_check(monkeypatch):
    async def run():
        calls=[]
        async def request(client,base,path,body,timeout):
            calls.append(path)
            if path=='/api/ps':
                await asyncio.sleep(.08)
                return {'models':[{'name':'synthetic-model','context_length':4096}]}
            return {}
        setup_model(monkeypatch,request)
        assert (await n.prepare_record_model())['status']=='ready'
        assert (await n.prepare_record_model(wait=False))['status']=='ready'
        assert calls==['/api/ps','/api/show']
    asyncio.run(run())

def setup_model(monkeypatch,request):
    policy=SimpleNamespace(real_record_logging_verified=True,base_url='http://127.0.0.1:11434',timeout_seconds=30,max_input_chars=20000,context_tokens=4096)
    monkeypatch.setattr(n,'load_ai_settings',lambda:(SimpleNamespace(),None))
    monkeypatch.setattr(n,'effective_central_models',lambda _:policy)
    monkeypatch.setattr(n,'central_feature_selection',lambda *_:SimpleNamespace(provider='ollama',model='synthetic-model'))
    monkeypatch.setattr(n,'local_request',request)
    return dict(question='전보다 달라진 게 있어?',facts=[{'message_id':uuid4(),'occurred_at':datetime(2026,9,1,tzinfo=timezone.utc)+timedelta(days=i),'resident_name':'합성대상','summary':r['text']} for i,r in enumerate(RECORDS)],names=['합성대상'],all_synthetic=True)

def test_installed_model_not_loaded_while_other_model_runs_is_retryable(monkeypatch):
    calls=[]
    async def request(client,base,path,body,timeout):
        calls.append(path)
        return {'models':[{'name':'synthetic-vision','size_vram':123}]} if path=='/api/ps' else {}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='cold',deadline=perf_counter()+8))
    assert result['error_type']=='model_not_ready' and result['generation_verified'] is False
    assert calls==['/api/ps','/api/show'] and result['loaded_vram_bytes']==123 and result['load_ms'] is None

def test_duplicate_cancellation_and_slots_released(monkeypatch):
    async def run():
        entered=asyncio.Event();cancelled=asyncio.Event()
        async def request(client,base,path,body,timeout):
            if path=='/api/ps':return {'models':[{'name':'synthetic-model'}]}
            if path=='/api/show':return {}
            entered.set()
            try:await asyncio.sleep(30)
            finally:cancelled.set()
        args=setup_model(monkeypatch,request)
        first=asyncio.create_task(n.generate_narrative(**args,request_key='same',deadline=perf_counter()+8))
        await entered.wait()
        duplicate=await n.generate_narrative(**args,request_key='same',deadline=perf_counter()+8)
        assert duplicate['error_type']=='duplicate_in_progress'
        class Gone:
            async def is_disconnected(self):return True
        with pytest.raises(asyncio.CancelledError):await n.await_connected(first,Gone())
        assert cancelled.is_set() and 'same' not in n._ACTIVE
        assert n._SLOTS.acquire(False) and n._SLOTS.acquire(False)
        n._SLOTS.release();n._SLOTS.release()
    asyncio.run(run())

def test_deadline_returns_rules_before_client_timeout(monkeypatch):
    async def request(*args):await asyncio.sleep(30)
    args=setup_model(monkeypatch,request);start=perf_counter()
    result=asyncio.run(n.generate_narrative(**args,request_key='timeout',deadline=start+.3))
    assert result['error_type']=='timeout' and perf_counter()-start<1

def test_cold_preview_does_not_load_and_followup_owns_preparation(monkeypatch):
    calls=[]
    async def request(client,base,path,body,timeout):
        calls.append(path)
        if path=='/api/ps':return {'models':[]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            return {'model':'synthetic-model','done':True,'message':{'content':'{"supported":[true,true],"answers_question":true}'},'load_duration':0,'eval_duration':100000000}
        return {'model':'synthetic-model','done':True,'message':{'content':__import__('json').dumps(GOOD)},'load_duration':8000000000,'eval_duration':900000000}
    args=setup_model(monkeypatch,request)
    first=asyncio.run(n.generate_narrative(**args,request_key='preview',deadline=perf_counter()+8))
    assert first['error_type']=='model_cold' and calls==['/api/ps','/api/show']
    followup=asyncio.run(n.generate_narrative(**args,request_key='preview',deadline=perf_counter()+18,allow_cold_start=True))
    assert followup['generation_verified'] and followup['load_ms']==8000
    assert calls.count('/api/chat')==2  # generation, then independent verification

def test_prepare_can_load_selected_model_without_stopping_other_loaded_model(monkeypatch):
    monkeypatch.setattr(n.settings,'record_ai_coexistence_verified',True)
    calls=[]
    async def request(client,base,path,body,timeout):
        calls.append((path,body))
        if path=='/api/ps':return {'models':[{'name':'synthetic-vision','size_vram':456}]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            return {'model':'synthetic-model','done':True,'message':{'content':'{"supported":[true,true],"answers_question":true}'},'load_duration':0,'eval_duration':100000000}
        return {'model':'synthetic-model','done':True,'message':{'content':__import__('json').dumps(GOOD)},'load_duration':7000000000,'eval_duration':900000000}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='other-loaded-prepare',deadline=perf_counter()+18,allow_cold_start=True))
    assert not result['generation_verified'] and result['error_type']=='gpu_capacity_unverified'
    assert [path for path,_ in calls]==['/api/ps','/api/show']
    assert not any(path in {'/api/generate/unload','/api/stop','/api/delete'} for path,_ in calls)


@pytest.mark.parametrize('load_reported',[True,False])
def test_preparation_is_shared_content_free_and_does_not_evict(monkeypatch,load_reported):
    calls=[];loaded=[]
    async def request(client,base,path,body,timeout):
        calls.append((path,body))
        if path=='/api/ps':return {'models':loaded}
        if path=='/api/show':return {}
        assert set(body)=={'model','stream','keep_alive','options'}
        await asyncio.sleep(.05)
        return {'model':'synthetic-model','done':True,**({'load_duration':1000000} if load_reported else {})}
    setup_model(monkeypatch,request)
    async def run():return await asyncio.gather(n.prepare_record_model(),n.prepare_record_model())
    results=asyncio.run(run())
    assert all(row['status']=='ready' for row in results)
    assert all(row['cold_load_ms']==(1 if load_reported else None) for row in results)
    assert all(row['preparation_call_ms']>=40 for row in results)
    assert sum(path=='/api/generate' for path,_ in calls)==1
    loaded.append({'name':'synthetic-vision'})
    calls.clear()
    assert asyncio.run(n.prepare_record_model())['error_type']=='gpu_capacity_unverified'
    assert all(path!='/api/generate' for path,_ in calls)


def test_retention_requires_coexistence_qualification(monkeypatch):
    monkeypatch.setattr(n.settings,'record_ai_keep_alive_seconds',600)
    monkeypatch.setattr(n.settings,'record_ai_coexistence_verified',False)
    assert n.model_retention()==180
    monkeypatch.setattr(n.settings,'record_ai_coexistence_verified',True)
    monkeypatch.setattr(n.settings,'record_ai_qualified_text_model','synthetic-model')
    assert n.model_retention('synthetic-model')==600
    assert n.model_retention('unqualified-model')==180
    assert n.model_retention('synthetic-model',16384)==180

def test_selected_model_already_loaded_generates_in_auto_phase(monkeypatch):
    calls=[]
    async def request(client,base,path,body,timeout):
        calls.append(path)
        if path=='/api/ps':return {'models':[{'name':'synthetic-model','context_length':4096,'size_vram':789}]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            return {'model':'synthetic-model','done':True,'message':{'content':'{"supported":[true,true],"answers_question":true}'},'load_duration':0,'eval_duration':100000000}
        return {'model':'synthetic-model','done':True,'message':{'content':__import__('json').dumps(GOOD)},'load_duration':0,'eval_duration':900000000}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='warm',deadline=perf_counter()+8))
    assert result['generation_verified'] and result['load_ms']==0 and result['loaded_vram_bytes']==789
    assert calls==['/api/ps','/api/show','/api/chat','/api/chat']

def test_one_verified_core_sentence_is_kept_when_an_extra_sentence_is_rejected(monkeypatch):
    calls=[]
    draft={'sentences':[
        {'text':'식사량은 이전보다 늘었습니다.','citations':['S1_S2'],'role':'conclusion'},
        {'text':'9월 3일에는 식사 4/4 섭취했습니다.','citations':['S2'],'role':'latest'},
    ]}
    async def request(client,base,path,body,timeout):
        calls.append(path)
        if path=='/api/ps':return {'models':[{'name':'synthetic-model','context_length':4096}]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            return {'model':'synthetic-model','done':True,'message':{'content':'{"supported":[true,true],"answers_question":true}'},'load_duration':0,'eval_duration':100000000}
        return {'model':'synthetic-model','done':True,'message':{'content':__import__('json').dumps(draft)},'load_duration':0,'eval_duration':100000000}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='one-grounded',deadline=perf_counter()+8))
    assert result['generation_verified'] is True
    assert result['answer']=='식사량은 이전보다 늘었습니다.'
    assert len(result['sentences'])==1
    assert result['rejected_sentence_count']==1
    assert 'review_count_normalized' in result['guard_rejection_types']
    assert calls==['/api/ps','/api/show','/api/chat','/api/chat']

def test_one_bounded_correction_rewrites_an_entirely_invalid_draft(monkeypatch):
    draft_calls=0
    async def request(client,base,path,body,timeout):
        nonlocal draft_calls
        if path=='/api/ps':return {'models':[{'name':'synthetic-model','context_length':4096}]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            return {'model':'synthetic-model','done':True,'message':{'content':'{"supported":[true],"answers_question":true}'},'load_duration':0,'eval_duration':100000000}
        draft_calls+=1
        draft=({'sentences':[{'text':'9월 3일에는 식사 4/4 섭취했습니다.','citations':['S2'],'role':'conclusion'}]}
            if draft_calls==1 else
            {'sentences':[{'text':'식사량은 이전보다 늘었습니다.','citations':['S1','S2'],'role':'conclusion'}]})
        return {'model':'synthetic-model','done':True,'message':{'content':__import__('json').dumps(draft)},'load_duration':0,'eval_duration':100000000}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='correct-once',deadline=perf_counter()+8))
    assert result['generation_verified'] is True
    assert result['correction_attempted'] is True
    assert result['answer']=='식사량은 이전보다 늘었습니다.'
    assert draft_calls==2

def test_missing_selected_model_tag_is_not_reported_as_not_ready(monkeypatch):
    async def request(client,base,path,body,timeout):
        if path=='/api/ps':return {'models':[{'name':'synthetic-vision'}]}
        response=httpx.Response(404,request=httpx.Request('POST',base+path))
        raise httpx.HTTPStatusError('missing',request=response.request,response=response)
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='missing',deadline=perf_counter()+8))
    assert result['error_type']=='model_missing' and result['generation_verified'] is False

def test_ollama_connection_failure_is_distinct(monkeypatch):
    async def request(client,base,path,body,timeout):
        raise httpx.ConnectError('offline',request=httpx.Request('GET',base+path))
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='offline',deadline=perf_counter()+8))
    assert result['error_type']=='model_connection_error' and result['generation_verified'] is False


def test_question_focus_rejection_reaches_correction_without_weakening_review(monkeypatch):
    """A factual source list must be rewritten into a supported answer/limitation."""
    import json
    drafts=0;reviews=0
    listed={'sentences':[{'text':'9월 1일에는 식사 1/2 섭취했습니다.',
        'citations':['S1'],'role':'conclusion'}]}
    answered={'sentences':[{'text':'식사량은 이전보다 늘었습니다.',
        'citations':['S1','S2'],'role':'conclusion'}]}
    async def request(client,base,path,body,timeout):
        nonlocal drafts,reviews
        if path=='/api/ps':return {'models':[{'name':'synthetic-model','context_length':4096}]}
        if path=='/api/show':return {}
        payload=json.loads(body['messages'][1]['content'])
        if 'supported' in body['format']['properties']:
            reviews+=1
            # Both drafts are factual; only the second answers the comparison.
            data={'supported':[True],'answers_question':payload['sentences'][0]['text']=='식사량은 이전보다 늘었습니다.'}
        else:
            drafts+=1
            data=answered if 'question_not_answered' in payload.get('validation_feedback',[]) else listed
        return {'model':'synthetic-model','done':True,'message':{'content':json.dumps(data)}}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='focus-correction',deadline=perf_counter()+8))
    assert result['generation_verified'] is True
    assert result['answer']=='식사량은 이전보다 늘었습니다.'
    assert result['correction_attempted'] is True
    assert 'question_not_answered' in result['guard_rejection_types']
    assert drafts==reviews==2


def test_persistent_question_focus_failure_keeps_reason_without_publishing_answer(monkeypatch):
    import json
    reviews=0
    async def request(client,base,path,body,timeout):
        nonlocal reviews
        if path=='/api/ps':return {'models':[{'name':'synthetic-model','context_length':4096}]}
        if path=='/api/show':return {}
        if 'supported' in body['format']['properties']:
            reviews+=1
            data={'supported':[True,True],'answers_question':False}
        else:data=GOOD
        return {'model':'synthetic-model','done':True,'message':{'content':json.dumps(data)}}
    args=setup_model(monkeypatch,request)
    result=asyncio.run(n.generate_narrative(**args,request_key='focus-still-failed',deadline=perf_counter()+8))
    assert result['generation_verified'] is False
    assert result['error_type']=='question_answer_not_supported'
    assert not result.get('answer') and not result.get('evidence_ids')
    assert 'question_not_answered' in (result.get('guard_rejection_types') or '')
    assert reviews==2
