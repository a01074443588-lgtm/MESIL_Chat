from copy import deepcopy
from datetime import datetime,timezone,timedelta
from uuid import UUID,uuid4
from fastapi.testclient import TestClient
from app import main
from app.database import SessionLocal
from app.models import Message,Resident,User
from app.record_question import answer_facts

def test_unique_name_is_filtered_before_retrieval_and_duplicate_names_need_selection(monkeypatch):
    async def ready(*,wait=True):return {'status':'ready'}
    monkeypatch.setattr(main,'prepare_record_model',ready)
    with TestClient(main.app) as client:
        headers={'origin':'http://testserver'}
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=headers).status_code==200
        uid=UUID(client.get('/api/auth/me').json()['id'])
        room=client.post('/api/admin/rooms',json={'name':'합성 질문 '+uuid4().hex,'kind':'custom','member_ids':[str(uid)],'resident_scope':'all'},headers=headers).json()
        with SessionLocal() as db:
            user=db.get(User,uid);org=user.organization_id
            resident=Resident(organization_id=org,internal_code='SYN-'+uuid4().hex,display_name='합성질문대상',service_type='facility',is_test_data=True)
            db.add(resident);db.flush();target=resident.id
            for i,text in enumerate(['식사 1/2 섭취함.','추가 관찰에서 불편 없음.']):
                db.add(Message(organization_id=org,room_id=UUID(room['id']),sender_id=uid,resident_id=target,body=text,created_at=datetime(2026,9,8,1,tzinfo=timezone.utc)+timedelta(hours=i),is_test_data=True))
            db.commit()
        original=main.create_period_workdesk_review;seen=[]
        def review(*args,**kwargs):seen.append(kwargs['payload'].resident_id);return original(*args,**kwargs)
        monkeypatch.setattr(main,'create_period_workdesk_review',review)
        phases=[];next_error=['model_not_ready']
        async def model(**kwargs):
            phases.append(kwargs['allow_cold_start'])
            return {'processing_method':'rules','generation_verified':False,'error_type':next_error[0],'ai_elapsed_ms':1,'load_ms':None,'generation_ms':None,'prefill_ms':None}
        monkeypatch.setattr(main,'generate_narrative',model)
        scope={'start_date':'2026-09-01','end_date':'2026-09-09','room_id':room['id'],'question':'합성질문대상 최근에 어떠셨어?'}
        response=client.post('/api/workdesk/record-question',json=scope,headers=headers)
        assert response.status_code==200
        data=response.json();assert data['resident_id']==str(target) and seen==[target]
        assert data['generation_verified'] is False and data['processing_method']=='failed'
        assert data['error_type']=='model_not_ready' and data['ai_enhancement_available'] is True
        assert data['answer']=='이번에는 AI 답변을 완성하지 못했습니다.'
        assert '설치되어 있지만 아직 준비되지 않았습니다' in data['fallback_notice']
        assert data['performance']['candidate_count']>0 and data['sources']==[]
        with SessionLocal() as db:
            db.add(Resident(organization_id=org,internal_code='SYN-'+uuid4().hex,display_name='합성질문대상',service_type='facility',is_test_data=True));db.commit()
        duplicate=client.post('/api/workdesk/record-question',json=scope,headers=headers)
        assert duplicate.status_code==200 and duplicate.json()['evidence_ids']==[]
        assert len(seen)==1 and '여러 명' in duplicate.json()['answer']
        selected=client.post('/api/workdesk/record-question',json={**scope,'resident_id':str(target),'question':'최근에 어떠셨어?'},headers=headers)
        assert selected.status_code==200 and selected.json()['processing_method']=='failed'
        assert selected.json()['evidence_ids']==[]
        enhanced=client.post('/api/workdesk/record-question',json={**scope,'resident_id':str(target),'question':'최근에 어떠셨어?','ai_phase':'prepare'},headers=headers)
        assert enhanced.status_code==200 and enhanced.json()['performance']['ai_phase']=='prepare'
        assert phases[-2:]==[True,True]
        next_error[0]='timeout'
        timed=client.post('/api/workdesk/record-question',json={**scope,'resident_id':str(target),'question':'요즘은 어떠셨어?'},headers=headers)
        assert timed.status_code==200 and timed.json()['ai_enhancement_available'] is True
        assert '제한시간을 초과' in timed.json()['fallback_notice']
        next_error[0]='context_limit'
        oversized=client.post('/api/workdesk/record-question',json={**scope,'resident_id':str(target),'question':'최근 기록을 요약해 주세요.'},headers=headers)
        assert oversized.status_code==200
        oversized_data=oversized.json()
        assert oversized_data['processing_method']=='failed' and not oversized_data['generation_verified']
        assert '기간' in oversized_data['fallback_notice'] and '줄여' in oversized_data['fallback_notice']
        assert '잠시 후' not in oversized_data['fallback_notice']
        assert oversized_data['evidence_ids']==[] and oversized_data['sources']==[]
        assert oversized_data['answer_sentences']==[]
        next_error[0]='timeout'
        today=datetime.now(timezone.utc).date()
        old_day=today-timedelta(days=20)
        with SessionLocal() as db:
            db.add(Message(organization_id=org,room_id=UUID(room['id']),sender_id=uid,
                resident_id=target,body='병원 방문 사유가 기록되어 있습니다.',
                created_at=datetime.combine(old_day,datetime.min.time(),tzinfo=timezone.utc),is_test_data=True))
            db.commit()
        expanded=client.post('/api/workdesk/record-question',json={
            'start_date':str(today-timedelta(days=6)),'end_date':str(today),
            'range_mode':'default','room_id':room['id'],'resident_id':str(target),
            'question':'마지막으로 병원에 다녀온 이유가 뭐야?'},headers=headers)
        assert expanded.status_code==200
        expanded_data=expanded.json()
        assert expanded_data['performance']['candidate_count']>0
        assert expanded_data['period_start']<=str(old_day)<=expanded_data['period_end']
        assert '넓혀 확인했습니다' in (expanded_data['limitation'] or '')


def test_completed_hydration_uses_the_same_server_fact_but_only_displays_verified_ai(monkeypatch):
    question='가장 최근에 드신 물의 양은?'
    resident_id=uuid4(); offered_id,consumed_id=uuid4(),uuid4()
    facts=[
        {'message_id':offered_id,'occurred_at':datetime(2026,8,18,5,tzinfo=timezone.utc),
         'summary':'물 200ml를 제공했습니다.','resident_id':resident_id,'resident_name':'합성대상','kind':'event','event_key':'synthetic-water'},
        {'message_id':consumed_id,'occurred_at':datetime(2026,8,18,7,tzinfo=timezone.utc),
         'summary':'제공한 물 200ml 전량 섭취를 확인했습니다.','resident_id':resident_id,'resident_name':'합성대상','kind':'followup','event_key':'synthetic-water'},
    ]
    deterministic=answer_facts(question,facts,scope_count=2)
    assert deterministic.get('_deterministic_fact') is True
    def prepare(payload, processor, db):
        return {'payload':payload,'result':deepcopy(deterministic),'facts':deepcopy(facts),
                'names':[],'notes':[],'selected_ids':{offered_id,consumed_id},
                'resolved_name':None,'truncated':False,'all_synthetic':True}
    monkeypatch.setattr(main,'_prepare_care_record_question',prepare)
    monkeypatch.setattr(main,'_recheck_record_text_access',lambda *args,**kwargs:None)
    async def generated(**kwargs):
        return {'generation_verified':True,'error_type':None,'ai_elapsed_ms':2,'load_ms':0,'generation_ms':1,'prefill_ms':1,
            'selected_facts':deepcopy(facts),'answer':deterministic['answer'],
            'sentences':deepcopy(deterministic['answer_sentences'])}
    monkeypatch.setattr(main,'generate_narrative',generated)
    results=[]
    with TestClient(main.app) as client:
        headers={'origin':'http://testserver'}
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=headers).status_code==200
        for prepared in (
            {'status':'unavailable','error_type':'model_not_ready','total_ms':1},
            {'status':'ready','status_ms':1,'total_ms':1},
        ):
            async def model_state(value=prepared,*,wait=True):
                return value
            monkeypatch.setattr(main,'prepare_record_model',model_state)
            response=client.post('/api/workdesk/record-question',json={
                'start_date':'2026-08-01','end_date':'2026-08-31','question':question,
                'resident_id':str(resident_id),
            },headers=headers)
            assert response.status_code==200
            results.append(response.json())
    assert results[0]['processing_method']=='failed'
    assert results[0]['answer']=='이번에는 AI 답변을 완성하지 못했습니다.'
    assert results[0]['evidence_ids']==[]
    assert results[1]['answer']==deterministic['answer']
    assert results[1]['evidence_ids']==[str(offered_id),str(consumed_id)]
    assert results[1]['answer_sentences']==[
        {**sentence,'evidence_ids':[str(value) for value in sentence['evidence_ids']]}
        for sentence in deterministic['answer_sentences']
    ]
    assert results[1]['processing_method']=='local_ai' and results[1]['generation_verified'] is True
    assert results[1]['timeline']==[] and results[1]['current_status'] is None
    assert results[1]['unknowns']==[]
def test_no_matching_records_is_normal_scope_answer_without_gpu_preparation(monkeypatch):
    async def forbidden(**kwargs):
        raise AssertionError('An empty record scope must not prepare or call a model')
    monkeypatch.setattr(main,'prepare_record_model',forbidden)
    monkeypatch.setattr(main,'generate_narrative',forbidden)
    with TestClient(main.app) as client:
        headers={'origin':'http://testserver'}
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=headers).status_code==200
        response=client.post('/api/workdesk/record-question',json={
            'start_date':'2001-01-01','end_date':'2001-01-02','range_mode':'fixed',
            'question':'합성 우주선 점검 기록은 무엇인가요?',
        },headers=headers)
        assert response.status_code==200
        result=response.json()
        assert result['processing_method']=='no_records'
        assert result['error_type'] is None
        assert result['evidence_ids']==[]
        assert result['generation_verified'] is False
        assert result['performance']['model_called'] is False
