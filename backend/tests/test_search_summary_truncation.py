import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app import search_summary_narrative as module


@pytest.mark.parametrize('first_truncated', [False, True])
def test_homecare_32_finishes_three_grounded_sentences_after_deduplication(monkeypatch, first_truncated):
    """32 real candidate messages collapse to 23 facts, formerly selecting 300 tokens.

    A forced truncation must change the output contract before the sole retry.
    The external model double simulates its finite output limit; parsing, source
    validation, deduplication and response construction remain real.
    """
    fixture=json.loads((Path(__file__).parent/'fixtures/search_summary_homecare32_20260914.json').read_text(encoding='utf-8'))
    assert fixture['message_count']==32
    assert len(module.preprocess_search_facts(fixture['facts'], mode='overview')['records'])==23
    calls=[]
    async def model(_client, _base, path, payload, _timeout):
        if path=='/api/show': return {}
        calls.append(payload)
        if len(calls)>2:
            pytest.fail('only one correction is allowed')
        schema=payload['format']
        props=next(iter(schema['$defs'].values()))['properties']
        compact_retry=(props['s']['maxLength']<=44 and props['x']['maxLength']<=20 and props['e']['maxItems']<=3)
        truncated=payload['options']['num_predict']<420 or (first_truncated and (len(calls)==1 or not compact_retry))
        content={'a':[
            {'s':'복약 확인을 요청한 뒤 보호자와 복용 여부를 확인했습니다.','t':'follow_up','r':'P4','e':['S11','S13','S14'],'n':False,'x':''},
            {'s':'보호자 확인 아래 복약을 안내했고 다음 방문에도 확인하기로 했습니다.','t':'follow_up','r':'P4','e':['S14','S15','S16'],'n':True,'x':''},
            {'s':'통로 장애물을 인계한 뒤 보호자와 정리하고 안전한 이동을 확인했습니다.','t':'follow_up','r':'P4','e':['S19','S21','S23'],'n':False,'x':''},
        ]}
        return {'model':'local-test','done':True,'done_reason':'length' if truncated else 'stop',
                'message':{'content':'{"a":[' if truncated else json.dumps(content,ensure_ascii=False)},
                'eval_count':payload['options']['num_predict'] if truncated else 360}
    policy=SimpleNamespace(base_url='http://local.test',context_tokens=8192,max_input_chars=18000,timeout_seconds=30,real_record_logging_verified=True)
    monkeypatch.setattr(module,'load_ai_settings',lambda:(object(),None))
    monkeypatch.setattr(module,'effective_central_models',lambda _:policy)
    monkeypatch.setattr(module,'central_feature_selection',lambda *_:SimpleNamespace(provider='ollama',model='local-test'))
    monkeypatch.setattr(module,'model_retention',lambda *_:'3m')
    monkeypatch.setattr(module,'local_request',model)
    result=asyncio.run(module.generate_search_summary(facts=fixture['facts'],names=fixture['names'],mode='overview',all_synthetic=True,request_key=uuid4().hex,deadline=module.perf_counter()+75))
    assert result['generation_verified'] is True, result
    assert result['processing_method']=='local_ai'
    assert len(result['sentences'])==3
    assert result['accepted_sentence_count']==3
    assert set(result['evidence_ids'])<=set(fixture['message_ids'])
    assert len(calls)==(2 if first_truncated else 1)
    if first_truncated:
        assert calls[0]['format']!=calls[1]['format']
        assert calls[0]['messages'][0]['content']!=calls[1]['messages'][0]['content']


def test_twice_truncated_response_is_never_verified(monkeypatch):
    # Reuse the fixture contract but always exhaust the model budget.
    fixture=json.loads((Path(__file__).parent/'fixtures/search_summary_homecare32_20260914.json').read_text(encoding='utf-8'))
    calls=[]
    async def model(_client,_base,path,payload,_timeout):
        if path=='/api/show': return {}
        calls.append(payload)
        return {'model':'local-test','done':True,'done_reason':'length','message':{'content':'{"a":['}}
    policy=SimpleNamespace(base_url='http://local.test',context_tokens=8192,max_input_chars=18000,timeout_seconds=30,real_record_logging_verified=True)
    monkeypatch.setattr(module,'load_ai_settings',lambda:(object(),None))
    monkeypatch.setattr(module,'effective_central_models',lambda _:policy)
    monkeypatch.setattr(module,'central_feature_selection',lambda *_:SimpleNamespace(provider='ollama',model='local-test'))
    monkeypatch.setattr(module,'model_retention',lambda *_:'3m')
    monkeypatch.setattr(module,'local_request',model)
    result=asyncio.run(module.generate_search_summary(facts=fixture['facts'],names=fixture['names'],mode='overview',all_synthetic=True,request_key=uuid4().hex,deadline=module.perf_counter()+75))
    assert len(calls)==2
    assert result['processing_method']=='rules'
    assert result['generation_verified'] is False
    assert result['error_type']=='response_truncated'
