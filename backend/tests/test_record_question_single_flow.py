"""Single-request orchestration and content-free preparation authorization."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from uuid import uuid4
import pytest
from fastapi.testclient import TestClient
from app import main
from app import record_narrative
from app.record_question import answer_facts
from app.schemas import RecordQuestionRequest


@pytest.mark.parametrize('failure', [None, 'model_connection_error', 'model_missing',
    'timeout', 'narrative_validation_failed', 'gpu_capacity_unverified', 'model_preparing'])
def test_one_request_retrieves_once_and_returns_verified_ai_or_honest_failure(monkeypatch,failure):
    user=SimpleNamespace(id=uuid4());identifier=uuid4();seen=[]
    resident_id=uuid4()
    facts=[{'message_id':identifier,'occurred_at':datetime(2026,9,8,tzinfo=timezone.utc),
        'resident_id':resident_id,'summary':f'합성 관찰 내용 {index}입니다.',
        'kind':'event','event_key':'synthetic'} for index in range(5)]
    notes=['접근 가능한 기록에서 답변 후보를 확인했습니다.']
    def retrieve(payload,*args):
        seen.append(payload)
        return dict(payload=payload,result=answer_facts(payload.question,facts,notes=notes),
            facts=facts,names=[],all_synthetic=True,notes=notes,selected_ids={identifier},
            truncated=False,resolved_name=None)
    async def ready(**kwargs):
        return {'status':'preparing' if failure=='model_preparing' else 'ready','status_ms':1,'cold_load_ms':0}
    async def generate(**kwargs):
        assert failure!='model_preparing', 'Do not send records to a model still preparing'
        assert kwargs['allow_cold_start'] is True
        kwargs['progress']('verifying')
        result=dict(generation_verified=failure is None,error_type=failure,ai_elapsed_ms=1,
            load_ms=0,generation_ms=1,prefill_ms=0)
        if failure is None:
            result.update(selected_facts=facts,answer='합성 AI 답변',
                sentences=[{'text':'합성 AI 답변','evidence_ids':[identifier]}])
        return result
    monkeypatch.setattr(main,'_prepare_care_record_question',retrieve)
    monkeypatch.setattr(main,'prepare_record_model',ready)
    monkeypatch.setattr(main,'generate_narrative',generate)
    rechecks=[]
    monkeypatch.setattr(main,'_recheck_record_text_access',lambda db,user,ids,**kw:rechecks.append(ids))
    main.app.dependency_overrides[main._require_processor]=lambda:user
    try:
        with TestClient(main.app) as client:
            response=client.post('/api/workdesk/record-question',json={
                'start_date':'2026-09-01','end_date':'2026-09-09','question':'최근에 어떠셨어?'},
                headers={'origin':'http://testserver'})
            assert response.status_code==200
            data=response.json()
            assert len(seen)==1 and rechecks==[{identifier}]
            assert data['generation_verified'] is (failure is None)
            assert data['error_type']==failure
            assert data['processing_method']==('local_ai' if failure is None else 'failed')
            assert data['evidence_ids']==([str(identifier)] if failure is None else [])
            assert data['answer']==('합성 AI 답변' if failure is None else '이번에는 AI 답변을 완성하지 못했습니다.')
            if failure is not None:
                # A failed answer must not inherit the rules preview's claim
                # that it summarized records and provides an evidence drawer.
                assert '핵심 내용을 정리했습니다' not in (data['limitation'] or '')
                assert '근거 보기' not in (data['limitation'] or '')
                assert data['processing_method'] != 'no_records'
                assert notes[0] in (data['limitation'] or '')
            assert bool(data['fallback_notice']) is (failure is not None)
    finally:main.app.dependency_overrides.pop(main._require_processor,None)


def test_unauthenticated_prewarm_and_progress_are_denied(monkeypatch):
    called=[]
    async def ready():called.append(True);return {'status':'ready'}
    monkeypatch.setattr(main,'prepare_record_model',ready)
    with TestClient(main.app) as client:
        assert client.post('/api/workdesk/record-question/model-ready',headers={'origin':'http://testserver'}).status_code in (401,403)
        assert client.get('/api/workdesk/record-question/progress').status_code in (401,403)
    assert called==[]
    main.app.dependency_overrides[main.get_current_user]=lambda:SimpleNamespace(role='staff',can_process_records=False)
    try:
        with TestClient(main.app) as client:
            assert client.post('/api/workdesk/record-question/model-ready',headers={'origin':'http://testserver'}).status_code==403
            assert client.get('/api/workdesk/record-question/progress').status_code==403
        assert called==[]
    finally:main.app.dependency_overrides.pop(main.get_current_user,None)


def test_content_free_preparation_selects_the_requested_briefing_feature(monkeypatch):
    policy=SimpleNamespace(base_url='http://127.0.0.1:11434',timeout_seconds=30,context_tokens=4096)
    monkeypatch.setattr(record_narrative,'load_ai_settings',lambda:(SimpleNamespace(),None))
    monkeypatch.setattr(record_narrative,'effective_central_models',lambda _:policy)
    monkeypatch.setattr(record_narrative,'central_feature_selection',
        lambda _,feature:SimpleNamespace(provider='ollama',model='briefing-model' if feature=='document_text' else 'question-model'))
    async def transport(client,base,path,body,timeout):
        if path=='/api/show':return {}
        assert path=='/api/ps'
        return {'models':[{'name':model,'context_length':4096} for model in ['briefing-model','question-model']]}
    monkeypatch.setattr(record_narrative,'local_request',transport)
    main.app.dependency_overrides[main._require_processor]=lambda:SimpleNamespace(id=uuid4())
    try:
        with TestClient(main.app) as client:
            response=client.post('/api/workdesk/record-question/model-ready?feature=document_text',headers={'origin':'http://testserver'})
            assert response.status_code==200
            assert response.json()['status']=='ready'
            assert response.json()['model']=='briefing-model'
            assert client.post('/api/workdesk/record-question/model-ready?feature=image_reading',headers={'origin':'http://testserver'}).status_code==422
    finally:main.app.dependency_overrides.pop(main._require_processor,None)


def test_model_ready_endpoint_returns_pending_without_waiting_for_cold_load(monkeypatch):
    policy=SimpleNamespace(base_url='http://127.0.0.1:11434',timeout_seconds=30,context_tokens=4096)
    monkeypatch.setattr(record_narrative,'load_ai_settings',lambda:(SimpleNamespace(),None))
    monkeypatch.setattr(record_narrative,'effective_central_models',lambda _:policy)
    monkeypatch.setattr(record_narrative,'central_feature_selection',
        lambda *_:SimpleNamespace(provider='ollama',model='synthetic-model'))
    async def transport(client,base,path,body,timeout):
        if path=='/api/ps':return {'models':[]}
        if path=='/api/show':return {}
        await asyncio.sleep(.3)
        return {'model':'synthetic-model','done':True}
    monkeypatch.setattr(record_narrative,'local_request',transport)
    main.app.dependency_overrides[main._require_processor]=lambda:SimpleNamespace(id=uuid4())
    try:
        with TestClient(main.app) as client:
            response=client.post('/api/workdesk/record-question/model-ready',
                headers={'origin':'http://testserver'})
            assert response.status_code==200
            assert response.json()['status']=='preparing'
    finally:main.app.dependency_overrides.pop(main._require_processor,None)
