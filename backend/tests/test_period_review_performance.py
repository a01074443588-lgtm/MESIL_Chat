from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
from fastapi.testclient import TestClient
from sqlalchemy import select,event
from app import main
from app.database import SessionLocal,engine
from app.models import Message,MessageComment,Resident,User,RoomMembership

ORIGIN={'origin':'http://testserver'}

def test_compact_review_preserves_facts_avoids_detail_work_and_rechecks_access(monkeypatch):
    with TestClient(main.app) as client:
        assert client.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=ORIGIN).status_code==200
        uid=UUID(client.get('/api/auth/me').json()['id'])
        room=client.post('/api/admin/rooms',json={'name':'합성 성능 '+uuid4().hex,'kind':'custom','member_ids':[str(uid)],'resident_scope':'all'},headers=ORIGIN)
        assert room.status_code==201
        rid=UUID(room.json()['id']);at=datetime(2026,9,8,1,tzinfo=timezone.utc)
        with SessionLocal() as db:
            user=db.get(User,uid)
            resident=Resident(organization_id=user.organization_id,internal_code='SYN-PERF-'+uuid4().hex,display_name='합성대상',service_type='facility',is_test_data=True)
            db.add(resident);db.flush();target=str(resident.id)
            messages=[Message(organization_id=user.organization_id,room_id=rid,sender_id=uid,resident_id=resident.id,body=f'합성대상 09:00 식사 1/2 섭취함. 관찰 {i}회.',created_at=at,is_test_data=True) for i in range(24)]
            db.add_all(messages);db.flush();ids=[str(m.id) for m in messages]
            db.add(MessageComment(organization_id=user.organization_id,message_id=messages[0].id,author_id=uid,body='10:00 추가 섭취 후 불편 없음.',created_at=at+timedelta(hours=1),is_test_data=True));db.commit()
        body={'start_date':'2026-09-03','end_date':'2026-09-09','room_id':str(rid),'resident_id':target,'enhance_summary':False,'save_history':False}
        full=client.post('/api/workdesk/period-review',json=body,headers=ORIGIN);assert full.status_code==200
        queries=[]
        def counted(*args):queries.append(1)
        def forbidden(*args,**kwargs):raise AssertionError('Compact list must not build detail DTOs or call AI')
        with monkeypatch.context() as patch:
            patch.setattr(main,'message_response',forbidden);patch.setattr(main,'summarize_room_messages',forbidden)
            event.listen(engine,'before_cursor_execute',counted)
            try:compact=client.post('/api/workdesk/period-review',json={**body,'response_mode':'briefing'},headers=ORIGIN)
            finally:event.remove(engine,'before_cursor_execute',counted)
        assert compact.status_code==200
        value=compact.json();assert value['sources']==[] and value['record_events']==[]
        assert value['care_topics']==full.json()['care_topics']
        assert set(value['source_ids'])==set(ids) and value['comment_count']==1
        assert len(compact.content)<len(full.content)
        assert len(queries)<80
        assert 'backend_total;dur=' in compact.headers['server-timing']
        assert '합성' not in compact.headers['server-timing']
        evidence=client.post('/api/workdesk/period-review/evidence',json={**body,'message_ids':[ids[0]]},headers=ORIGIN)
        assert evidence.status_code==200 and evidence.json()[0]['comments'][0]['body']=='10:00 추가 섭취 후 불편 없음.'
        out_of_range=client.post('/api/workdesk/period-review/evidence',json={**body,'start_date':'2026-03-01','end_date':'2026-03-07','message_ids':[ids[0]]},headers=ORIGIN)
        assert out_of_range.status_code==403
        with SessionLocal() as db:
            db.get(Message,UUID(ids[0])).lifecycle_status='recalled';db.commit()
        assert client.post('/api/workdesk/period-review/evidence',json={**body,'message_ids':[ids[0]]},headers=ORIGIN).status_code==403
        # A fresh calculation sees changed records; no stale response cache.
        updated=client.post('/api/workdesk/period-review',json={**body,'response_mode':'briefing'},headers=ORIGIN).json()
        assert ids[0] not in updated['source_ids']
        with TestClient(main.app) as anonymous:
            assert anonymous.post('/api/workdesk/period-review/evidence',json={**body,'message_ids':[ids[1]]},headers=ORIGIN).status_code==401


def test_compact_evidence_denies_revoked_staff_membership():
    with TestClient(main.app) as admin:
        assert admin.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers=ORIGIN).status_code==200
        username='perf_'+uuid4().hex[:10]
        employee=admin.post('/api/employees',json={'username':username,'full_name':'합성 직원','password':'SyntheticPass!234','role':'staff','can_process_records':True},headers=ORIGIN)
        assert employee.status_code==201
        uid=UUID(employee.json()['id'])
        room=admin.post('/api/admin/rooms',json={'name':'합성 권한 '+username,'kind':'custom','member_ids':[str(uid)],'resident_scope':'all'},headers=ORIGIN)
        rid=UUID(room.json()['id'])
        with SessionLocal() as db:
            user=db.get(User,uid);user.must_change_password=False
            message=Message(organization_id=user.organization_id,room_id=rid,sender_id=uid,body='합성 기록',created_at=datetime(2026,9,8,tzinfo=timezone.utc),is_test_data=True)
            db.add(message);db.commit();mid=str(message.id)
        staff=TestClient(main.app)
        assert staff.post('/api/auth/login',json={'username':username,'password':'SyntheticPass!234'},headers=ORIGIN).status_code==200
        body={'start_date':'2026-09-03','end_date':'2026-09-09','room_id':str(rid),'response_mode':'briefing'}
        assert staff.post('/api/workdesk/period-review',json=body,headers=ORIGIN).status_code==200
        with SessionLocal() as db:
            membership=db.scalar(select(RoomMembership).where(RoomMembership.room_id==rid,RoomMembership.staff_id==db.get(User,uid).staff_id));membership.left_at=datetime.now(timezone.utc);db.commit()
        assert staff.post('/api/workdesk/period-review/evidence',json={**body,'message_ids':[mid]},headers=ORIGIN).status_code==403
