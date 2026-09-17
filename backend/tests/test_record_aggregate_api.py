"""Authorized aggregate answers must work even while the local AI is unavailable."""
from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from app import main
from app.database import SessionLocal
from app.models import Message, Resident, User
from app.models import RoomMembership,MessageComment
from sqlalchemy import select
import pytest
from app.models import MessageResidentLink, MessageAttachment, AttachmentTextExtraction

HEADERS={'origin':'http://testserver'}


@pytest.fixture
def aggregate_record_scope(monkeypatch):
    async def forbidden(**kwargs):
        raise AssertionError('a rule/coverage result must not require AI readiness')
    monkeypatch.setattr(main, 'prepare_record_model', forbidden)
    with TestClient(main.app) as client:
        assert client.post('/api/auth/login', json={'username':'admin','password':'AdminPass!234'}, headers=HEADERS).status_code == 200
        user_id = UUID(client.get('/api/auth/me').json()['id'])
        response = client.post('/api/admin/rooms', json={'name':'합성 범위 '+uuid4().hex,
            'kind':'custom','member_ids':[str(user_id)],'resident_scope':'all'}, headers=HEADERS)
        assert response.status_code == 201
        room_id = UUID(response.json()['id'])
        with SessionLocal() as db:
            org = db.get(User,user_id).organization_id
            resident = Resident(organization_id=org, internal_code='SYN-'+uuid4().hex,
                display_name='합성범위가', service_type='facility', is_test_data=True)
            db.add(resident); db.flush()
            message = Message(organization_id=org, room_id=room_id, sender_id=user_id,
                resident_id=resident.id, body='낙상 발생함.',
                created_at=datetime(2026,9,5,1,tzinfo=timezone.utc), is_test_data=True)
            db.add(message); db.flush()
            scope = dict(org=org, room_id=room_id, user_id=user_id, resident_id=resident.id, message_id=message.id)
            db.commit()
        query = {'start_date':'2026-09-01','end_date':'2026-09-09',
            'room_id':str(room_id),'question':'낙상 기록이 가장 많은 어르신은?'}
        yield client, query, scope


@pytest.mark.parametrize('mime,status', [
    ('application/pdf', None), ('application/pdf','completed'),
    ('audio/wav','failed'), ('image/png','pending'),
])
def test_unread_or_unreviewed_attachment_prevents_complete_ranking(aggregate_record_scope, mime, status):
    client, query, scope = aggregate_record_scope
    with SessionLocal() as db:
        attachment = MessageAttachment(organization_id=scope['org'], message_id=scope['message_id'],
            uploader_id=scope['user_id'], storage_key='synthetic-metadata-only-'+uuid4().hex,
            original_name='synthetic-coverage.bin', mime_type=mime, size_bytes=1)
        db.add(attachment); db.flush()
        if status is not None:
            db.add(AttachmentTextExtraction(organization_id=scope['org'], attachment_id=attachment.id,
                status=status, provider='synthetic', model_name='synthetic', requested_by_id=scope['user_id'],
                extracted_text='낙상 발생함.' if status == 'completed' else None))
        db.commit()
    response = client.post('/api/workdesk/record-question', json=query, headers=HEADERS)
    assert response.status_code == 200
    aggregate = response.json()['aggregate']
    assert aggregate['status'] == 'insufficient'
    assert aggregate['leaders'] == [] and aggregate['counts'] == []
    assert 'attachment_scope_incomplete' in aggregate['reason_codes']


@pytest.mark.parametrize('link_kind', ['chain', 'missing', 'cycle'])
def test_reply_graph_counts_one_chain_and_refuses_missing_or_cyclic_roots(aggregate_record_scope, link_kind):
    client, query, scope = aggregate_record_scope
    with SessionLocal() as db:
        root = db.get(Message, scope['message_id'])
        previous = root
        for index in range(2):
            parent_id = uuid4() if link_kind == 'missing' and index == 0 else previous.id
            reply = Message(organization_id=scope['org'], room_id=scope['room_id'], sender_id=scope['user_id'],
                resident_id=scope['resident_id'], body=f'낙상 후 안정됨. 합성 후속 관찰 {index}.',
                created_at=datetime(2026,9,5,2+index,tzinfo=timezone.utc), is_test_data=True,
                extra_data={'reply_to': {'message_id':str(parent_id), 'sender_name':'합성 발신자',
                    'body':'합성 원문', 'created_at':'2026-09-05T01:00:00+00:00'}})
            db.add(reply); db.flush(); previous = reply
        if link_kind == 'cycle':
            root.extra_data = {'reply_to': {'message_id':str(previous.id), 'sender_name':'합성 발신자',
                'body':'합성 원문', 'created_at':'2026-09-05T03:00:00+00:00'}}
        db.commit()
    response = client.post('/api/workdesk/record-question', json=query, headers=HEADERS)
    assert response.status_code == 200
    aggregate = response.json()['aggregate']
    if link_kind == 'chain':
        assert aggregate['status'] == 'complete'
        assert [row['record_count'] for row in aggregate['counts']] == [1]
        assert len(response.json()['evidence_ids']) == 3
    else:
        assert aggregate['status'] == 'insufficient'
        assert aggregate['leaders'] == [] and aggregate['counts'] == []
        assert 'event_link_incomplete' in aggregate['reason_codes']


@pytest.mark.parametrize('explicit', [False, True])
def test_reviewed_attachment_requires_complete_resident_attribution(aggregate_record_scope, explicit):
    client, query, scope = aggregate_record_scope
    with SessionLocal() as db:
        other = Resident(organization_id=scope['org'], internal_code='SYN-'+uuid4().hex,
            display_name='합성범위나', service_type='facility', is_test_data=True)
        db.add(other); db.flush()
        record = db.get(Message, scope['message_id'])
        record.body = '합성범위가 식사함. 합성범위나 식사함.'
        db.add(MessageResidentLink(organization_id=scope['org'], message_id=record.id,
            resident_id=other.id, source='manual', status='confirmed', reviewed_by_id=scope['user_id']))
        attachment = MessageAttachment(organization_id=scope['org'], message_id=record.id,
            uploader_id=scope['user_id'], storage_key='synthetic-metadata-only-'+uuid4().hex,
            original_name='synthetic-reviewed.pdf', mime_type='application/pdf', size_bytes=1)
        db.add(attachment); db.flush()
        text = '합성범위가 낙상 발생함. 합성범위나 낙상 발생함.' if explicit else '낙상 발생함.'
        db.add(AttachmentTextExtraction(organization_id=scope['org'], attachment_id=attachment.id,
            status='reviewed', provider='synthetic', model_name='synthetic', requested_by_id=scope['user_id'],
            reviewed_text=text, reviewed_by_id=scope['user_id'],
            reviewed_at=datetime(2026,9,6,1,tzinfo=timezone.utc)))
        db.commit()
    response = client.post('/api/workdesk/record-question', json=query, headers=HEADERS)
    assert response.status_code == 200
    aggregate = response.json()['aggregate']
    if explicit:
        assert aggregate['status'] == 'complete'
        assert [row['record_count'] for row in aggregate['counts']] == [1,1]
    else:
        assert aggregate['status'] == 'insufficient'
        assert 'source_attribution_incomplete' in aggregate['reason_codes']
        assert aggregate['leaders'] == []


def test_empty_aggregate_does_not_expand_scope_to_find_a_winner(aggregate_record_scope):
    client, query, scope = aggregate_record_scope
    with SessionLocal() as db:
        db.add(Message(organization_id=scope['org'], room_id=scope['room_id'], sender_id=scope['user_id'],
            resident_id=scope['resident_id'], body='폭언 발생함.',
            created_at=datetime(2026,8,20,1,tzinfo=timezone.utc), is_test_data=True))
        db.commit()
    response = client.post('/api/workdesk/record-question', json={**query,
        'start_date':'2026-09-06','end_date':'2026-09-09','range_mode':'default',
        'question':'폭언 기록이 가장 많은 어르신은?'}, headers=HEADERS)
    assert response.status_code == 200
    data = response.json()
    assert data['period_start'] == '2026-09-06'
    assert data['period_end'] == '2026-09-09'
    assert data['aggregate']['counts'] == [] and data['aggregate']['leaders'] == []


@pytest.mark.parametrize('source_kind', ['message', 'comment'])
def test_unattributed_multi_resident_source_cannot_be_silently_dropped_from_ranking(monkeypatch, source_kind):
    async def forbidden(**kwargs):
        raise AssertionError('a coverage result must not require AI readiness')
    monkeypatch.setattr(main, 'prepare_record_model', forbidden)
    with TestClient(main.app) as client:
        assert client.post('/api/auth/login', json={'username':'admin','password':'AdminPass!234'}, headers=HEADERS).status_code == 200
        user_id = UUID(client.get('/api/auth/me').json()['id'])
        room = client.post('/api/admin/rooms', json={'name':'합성 귀속 '+uuid4().hex,
            'kind':'custom','member_ids':[str(user_id)],'resident_scope':'all'}, headers=HEADERS)
        assert room.status_code == 201
        room_id = UUID(room.json()['id'])
        with SessionLocal() as db:
            org = db.get(User, user_id).organization_id
            residents = [Resident(organization_id=org, internal_code='SYN-'+uuid4().hex,
                display_name='합성귀속'+label, service_type='facility', is_test_data=True) for label in ['가','나']]
            db.add_all(residents); db.flush()
            record = Message(organization_id=org, room_id=room_id, sender_id=user_id,
                resident_id=residents[0].id,
                body='낙상 발생함.' if source_kind == 'message' else '합성귀속가 식사함. 합성귀속나 식사함.',
                created_at=datetime(2026,9,5,1,tzinfo=timezone.utc), is_test_data=True)
            db.add(record); db.flush()
            db.add(MessageResidentLink(organization_id=org, message_id=record.id,
                resident_id=residents[1].id, source='manual', status='confirmed', reviewed_by_id=user_id))
            if source_kind == 'comment':
                db.add(MessageComment(organization_id=org, message_id=record.id, author_id=user_id,
                    body='낙상 발생함.', created_at=datetime(2026,9,5,2,tzinfo=timezone.utc), is_test_data=True))
            selected = str(residents[0].id)
            db.commit()
        query = {'start_date':'2026-09-01','end_date':'2026-09-09',
            'room_id':str(room_id),'question':'낙상 기록이 가장 많은 어르신은?'}
        for selection in ({}, {'resident_id':selected}):
            response = client.post('/api/workdesk/record-question', json={**query, **selection}, headers=HEADERS)
            assert response.status_code == 200
            data = response.json()
            assert data['aggregate']['status'] == 'insufficient'
            assert 'source_attribution_incomplete' in data['aggregate']['reason_codes']
            assert data['aggregate']['counts'] == []
            assert data['aggregate']['leaders'] == []
        # Explicit attribution restores a complete result; do not reject all
        # multi-resident messages merely because they have two links.
        with SessionLocal() as db:
            if source_kind == 'message':
                db.get(Message, record.id).body = '합성귀속가 낙상 발생함. 합성귀속나 낙상 발생함.'
            else:
                comment = db.scalar(select(MessageComment).where(MessageComment.message_id == record.id))
                comment.body = '합성귀속가 낙상 발생함. 합성귀속나 낙상 발생함.'
            db.commit()
        for selection, expected in (({}, [1, 1]), ({'resident_id':selected}, [1])):
            response = client.post('/api/workdesk/record-question', json={**query, **selection}, headers=HEADERS)
            assert response.status_code == 200
            assert response.json()['aggregate']['status'] == 'complete'
            assert [row['record_count'] for row in response.json()['aggregate']['counts']] == expected


def test_full_scope_count_and_tie_do_not_depend_on_model_readiness(monkeypatch):
    async def unavailable(**kwargs):
        return {'status':'unavailable','error_type':'model_connection_error'}
    monkeypatch.setattr(main,'prepare_record_model',unavailable)
    with TestClient(main.app) as client:
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=HEADERS).status_code==200
        user_id=UUID(client.get('/api/auth/me').json()['id'])
        response=client.post('/api/admin/rooms',json={'name':'합성 집계 '+uuid4().hex,
            'kind':'custom','member_ids':[str(user_id)],'resident_scope':'all'},headers=HEADERS)
        assert response.status_code==201
        room_id=UUID(response.json()['id'])
        with SessionLocal() as db:
            organization_id=db.get(User,user_id).organization_id
            residents=[Resident(organization_id=organization_id,internal_code='SYN-AGG-'+uuid4().hex,
                display_name=name,service_type='facility',is_test_data=True)
                for name in ['합성집계가','합성집계나']]
            db.add_all(residents);db.flush()
            ids=[str(r.id) for r in residents]
            for i,resident in enumerate(residents):
                db.add(Message(organization_id=organization_id,room_id=room_id,sender_id=user_id,
                    resident_id=resident.id,body=f'낙상이 발생했습니다. 합성 관찰 위치 {i+1}.',
                    created_at=datetime(2026,9,5,1,tzinfo=timezone.utc),is_test_data=True))
            db.commit()
        result=client.post('/api/workdesk/record-question',json={'start_date':'2026-09-01',
            'end_date':'2026-09-09','room_id':str(room_id),'question':'낙상 기록이 가장 많은 어르신은 누구인가요?'},headers=HEADERS)
        assert result.status_code==200
        data=result.json()
        assert data['processing_method']=='rules'
        assert data['error_type'] is None
        assert data['aggregate']['metric']=='record_event_groups'
        assert data['aggregate']['status']=='complete'
        assert set(data['aggregate']['leaders'])==set(ids)
        assert [row['record_count'] for row in data['aggregate']['counts']]==[1,1]
        assert '공동' in data['answer']
        assert '실제 발생 횟수' in data['limitation']
        assert len(data['evidence_ids'])==2
        # A known later correction cannot silently leave an old period's winner
        # valid. The caller must expand/review the scope instead.
        with SessionLocal() as db:
            original_id=db.scalar(select(Message.id).where(Message.room_id==room_id,Message.resident_id==UUID(ids[0])))
            db.add(MessageComment(organization_id=organization_id,message_id=original_id,
                author_id=user_id,body='이전 낙상 기록은 오기로 취소합니다.',
                created_at=datetime(2026,9,10,1,tzinfo=timezone.utc),is_test_data=True));db.commit()
        corrected=client.post('/api/workdesk/record-question',json={'start_date':'2026-09-01',
            'end_date':'2026-09-09','room_id':str(room_id),'question':'낙상 기록이 가장 많은 어르신은 누구인가요?'},headers=HEADERS)
        assert corrected.status_code==200
        assert corrected.json()['aggregate']['status']=='insufficient'
        assert 'correction_outside_period' in corrected.json()['aggregate']['reason_codes']


def test_ambiguous_behavior_ranking_gives_a_normal_clarification(monkeypatch):
    async def unavailable(**kwargs):
        return {'status':'unavailable','error_type':'model_connection_error'}
    monkeypatch.setattr(main,'prepare_record_model',unavailable)
    with TestClient(main.app) as client:
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=HEADERS).status_code==200
        response=client.post('/api/workdesk/record-question',json={'start_date':'2026-09-01',
            'end_date':'2026-09-09','question':'가장 문제 행동이 많은 어르신은 누구인가?'},headers=HEADERS)
        assert response.status_code==200
        data=response.json()
        assert data['processing_method']=='clarification'
        assert data['period_start']=='2026-09-01'
        assert data['period_end']=='2026-09-09'
        assert data['error_type'] is None
        assert data['evidence_ids']==[]
        assert data['aggregate']['leaders']==[]
        assert '기준' in data['answer']
        assert 'AI 답변을 완성하지 못했습니다' not in data['answer']


def test_aggregate_period_room_permission_truncation_and_post_query_revocation(monkeypatch):
    async def forbidden(**kwargs):raise AssertionError('aggregate must not prepare an AI model')
    monkeypatch.setattr(main,'prepare_record_model',forbidden)
    with TestClient(main.app) as admin,TestClient(main.app) as staff:
        assert admin.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=HEADERS).status_code==200
        admin_id=UUID(admin.get('/api/auth/me').json()['id']);username='agg_'+uuid4().hex[:10]
        employee=admin.post('/api/employees',json={'username':username,'full_name':'합성 집계 직원',
            'password':'SyntheticPass!234','role':'staff','can_process_records':True},headers=HEADERS)
        assert employee.status_code==201
        staff_id=UUID(employee.json()['id'])
        def room(members):
            r=admin.post('/api/admin/rooms',json={'name':'합성 권한 '+uuid4().hex,'kind':'custom',
                'member_ids':[str(value) for value in members],'resident_scope':'all'},headers=HEADERS)
            assert r.status_code==201
            return UUID(r.json()['id'])
        allowed=room([staff_id]);hidden=room([admin_id])
        with SessionLocal() as db:
            account=db.get(User,staff_id);account.must_change_password=False;org=account.organization_id
            residents=[Resident(organization_id=org,internal_code='SYN-'+uuid4().hex,
                display_name='합성권한'+label,service_type='facility',is_test_data=True) for label in ['가','나']]
            db.add_all(residents);db.flush();rids=[str(row.id) for row in residents]
            visible_ids=set()
            for index,(who,where,day) in enumerate([(0,allowed,5),(1,allowed,5),(1,allowed,6),
                    (0,hidden,5),(0,hidden,6),(0,hidden,7),(0,allowed,10),(0,allowed,11)]):
                message=Message(organization_id=org,room_id=where,sender_id=staff_id,
                    resident_id=residents[who].id,body=f'관찰 위치 {index}에서 낙상 발생함.',
                    created_at=datetime(2026,9,day,1,tzinfo=timezone.utc),is_test_data=True)
                db.add(message);db.flush()
                if where==allowed and day<=9:visible_ids.add(str(message.id))
            actual_staff_id=account.staff_id;db.commit()
        assert staff.post('/api/auth/login',json={'username':username,'password':'SyntheticPass!234'},headers=HEADERS).status_code==200
        query={'start_date':'2026-09-01','end_date':'2026-09-09','question':'낙상 기록이 가장 많은 어르신은?'}
        response=staff.post('/api/workdesk/record-question',json=query,headers=HEADERS)
        assert response.status_code==200
        data=response.json()
        assert data['aggregate']['leaders']==[rids[1]]
        assert [row['record_count'] for row in data['aggregate']['counts']]==[2,1]
        assert set(data['evidence_ids'])==visible_ids
        assert staff.post('/api/workdesk/record-question',json={**query,'room_id':str(hidden)},headers=HEADERS).status_code==403
        original_review=main.create_period_workdesk_review
        def incomplete(*args,**kwargs):
            value=original_review(*args,**kwargs);value.truncated=True;return value
        monkeypatch.setattr(main,'create_period_workdesk_review',incomplete)
        response=staff.post('/api/workdesk/record-question',json=query,headers=HEADERS)
        assert response.status_code==200
        assert response.json()['aggregate']['status']=='insufficient'
        assert response.json()['aggregate']['leaders']==[]
        monkeypatch.setattr(main,'create_period_workdesk_review',original_review)
        original_aggregate=main.aggregate_question
        def revoked(*args,**kwargs):
            result=original_aggregate(*args,**kwargs)
            with SessionLocal() as db:
                membership=db.scalar(select(RoomMembership).where(RoomMembership.room_id==allowed,
                    RoomMembership.staff_id==actual_staff_id,RoomMembership.left_at.is_(None)))
                membership.left_at=datetime.now(timezone.utc);db.commit()
            return result
        monkeypatch.setattr(main,'aggregate_question',revoked)
        assert staff.post('/api/workdesk/record-question',json=query,headers=HEADERS).status_code==403
