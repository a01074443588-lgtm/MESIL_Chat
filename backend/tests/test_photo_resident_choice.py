"""Photo intent, resident decisions, audit and evidence on synthetic data only."""
from uuid import UUID, uuid4
from hashlib import sha256
from io import BytesIO
from datetime import datetime, timezone, timedelta
import json
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from app import main, ocr
from app.database import SessionLocal
from app.models import User, Resident, RecipientRoom, Message, MessageResidentLink, AttachmentTextExtraction, AuditEvent, WorkItem
from app.period_review_performance import briefing_sources

HEADERS={'origin':'http://testserver'}

def post(c,path,data):return c.post(path,json=data,headers=HEADERS)
def patch(c,path,data):return c.patch(path,json=data,headers=HEADERS)
def picture(blank=False):
    image=Image.new('RGB',(400,300),'white')
    if not blank:ImageDraw.Draw(image).text((30,80),'SYNTHETIC DOCUMENT 180 ml',fill='black')
    output=BytesIO();image.save(output,'PNG');return output.getvalue()

@pytest.fixture
def context(monkeypatch):
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:'합성 판독문을 확인했습니다.')
    monkeypatch.setattr(main,'extract_report_text',lambda *a,**k:'합성 판독문을 확인했습니다.')
    monkeypatch.setattr(main,'locate_report_text_regions',lambda *a,**k:[])
    with TestClient(main.app) as admin:
        assert post(admin,'/api/auth/login',{'username':'admin','password':'AdminPass!234'}).status_code==200
        username='choice_'+uuid4().hex[:10]
        users=[]
        for suffix in ('a','b'):
            result=post(admin,'/api/employees',{'username':username+suffix,'full_name':'합성 직원 '+suffix,'password':'SyntheticPass!234','role':'staff','can_process_records':False})
            assert result.status_code==201
            users.append(result.json()['id'])
        room=post(admin,'/api/admin/rooms',{'name':'합성 선택 '+username,'kind':'custom','member_ids':users+[admin.get('/api/auth/me').json()['id']],'resident_scope':'all'})
        assert room.status_code==201
        with SessionLocal() as db:
            org=db.get(User,UUID(users[0])).organization_id
            for uid in users:db.get(User,UUID(uid)).must_change_password=False
            spaces=[RecipientRoom(organization_id=org,internal_code=uuid4().hex,name='합성 공간 '+str(i)) for i in range(4)]
            db.add_all(spaces);db.flush()
            rows=[Resident(organization_id=org,internal_code=uuid4().hex,display_name=n,service_type='facility',is_test_data=True,room_id=spaces[i].id) for i,n in enumerate(['가온'+username,'나래'+username,'다솜'+username,'다솜'+username])]
            db.add_all(rows);db.commit();residents=[{'id':str(r.id),'name':r.display_name} for r in rows]
        with TestClient(main.app) as own, TestClient(main.app) as other:
            for c,suffix in [(own,'a'),(other,'b')]:assert post(c,'/api/auth/login',{'username':username+suffix,'password':'SyntheticPass!234'}).status_code==200
            yield admin,own,other,room.json()['id'],residents

def upload(c,room,read=False,blank=False,**data):
    raw=picture(blank)
    result=c.post(f'/api/rooms/{room}/messages-with-files',data={'body':'합성 사진','report_image':str(read).lower(),**data},files={'files':('합성사진.png',raw,'image/png')},headers=HEADERS)
    assert result.status_code==201
    return result.json(),sha256(raw).hexdigest()

def detail(c,mid):return c.get(f'/api/messages/{mid}').json()['message']
def extract(c,aid):return c.get(f'/api/attachments/{aid}/text-extraction?preview=true').json()
def decision(c,mid,kind,rid=None):return patch(c,f'/api/messages/{mid}/resident-review',{'decision':kind,'resident_id':rid})

def test_general_photo_no_worker_original_and_owner_permissions(context,monkeypatch):
    admin,own,other,room,_=context
    monkeypatch.setattr(main,'_run_attachment_text_extraction',lambda *a:pytest.fail('ordinary photo must not queue OCR'))
    message,digest=upload(own,room);attachment=detail(own,message['id'])['attachments'][0];aid=attachment['id']
    assert attachment['photo_reading_status']=='general' and attachment['text_extraction'] is None
    assert sha256(own.get(f'/api/attachments/{aid}').content).hexdigest()==digest
    assert patch(other,f'/api/attachments/{aid}/photo-reading',{'decision':'not_required'}).status_code==403
    assert patch(own,f'/api/attachments/{aid}/photo-reading',{'decision':'not_required'}).status_code==200
    assert detail(own,message['id'])['attachments'][0]['photo_reading_status']=='not_required'
    assert sha256(admin.get(f'/api/attachments/{aid}').content).hexdigest()==digest


def test_voice_correction_readiness_is_metadata_only_and_permissioned(context,monkeypatch):
    _,own,other,room,_=context
    message,_=upload(own,room)
    aid=message['attachments'][0]['id']
    monkeypatch.setattr(main,'stt_readiness',lambda **_kwargs:{
        'enabled':True,'ready':True,'status':'ready','timeout_seconds':2.0,
    })
    allowed=own.get(f'/api/attachments/{aid}/voice-correction-readiness')
    assert allowed.status_code==200,allowed.text
    assert allowed.json()=={
        'enabled':True,'ready':True,'status':'ready','timeout_seconds':2.0,
    }
    denied=other.get(f'/api/attachments/{aid}/voice-correction-readiness')
    assert denied.status_code==403

@pytest.mark.parametrize('blank,expected',[(False,'completed'),(True,'no_text')])
def test_explicit_read_and_neutral_blank(context,blank,expected):
    admin,own,_,room,_=context
    message,_=upload(own,room,blank=blank);aid=message['attachments'][0]['id']
    result=patch(admin,f'/api/attachments/{aid}/photo-reading',{'decision':'read'})
    assert result.status_code==200 and result.json()['text_extraction']['status']=='pending'
    saved=extract(own,aid)
    assert saved['photo_reading_status']==expected and saved['text_extraction']['status']==expected
    assert (saved['text_extraction']['result_char_count']>0) == (not blank)
    assert detail(own,message['id'])['resident_links']==[]

def test_opt_in_failure_retry_and_not_required_late_result(context,monkeypatch):
    admin,own,_,room,_=context
    def fail(*a,**k):raise RuntimeError('synthetic worker unavailable')
    monkeypatch.setattr(main,'extract_handwriting_text',fail)
    message,_=upload(own,room,read=True);aid=message['attachments'][0]['id']
    saved=extract(own,aid);assert saved['photo_reading_status']=='failed'
    def late(*a,**k):
        with SessionLocal() as db:
            row=db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id==UUID(aid)))
            assert row.status=='processing'
            row.status='not_required';db.commit()
        return '합성 늦은 결과'
    monkeypatch.setattr(main,'extract_handwriting_text',late)
    monkeypatch.setattr(main,'extract_report_text',late)
    assert patch(admin,f'/api/attachments/{aid}/photo-reading',{'decision':'read'}).status_code==200
    assert extract(own,aid)['photo_reading_status']=='not_required'
    assert not extract(own,aid)['text_extraction']['latest_confirmed_text']

def test_explicit_selection_precedes_ocr_and_text(context,monkeypatch):
    _,own,_,room,residents=context
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:residents[1]['name']+' 어르신 관찰')
    message,_=upload(own,room,read=True,resident_id=residents[0]['id'],body=residents[1]['name']+' 어르신')
    links=detail(own,message['id'])['resident_links']
    assert [(link['resident']['id'],link['status']) for link in links]==[(residents[0]['id'],'confirmed')]

def test_exact_unique_auto_link_and_duplicate_name_requires_choice(context):
    admin,own,other,room,residents=context
    unique=post(own,f'/api/rooms/{room}/messages',{'body':residents[0]['name']+' 어르신 물 180ml 제공'}).json()
    assert unique['resident_links'][0]['status']=='confirmed' and unique['resident_links'][0]['automatically_linked']
    assert decision(other,unique['id'],'change',residents[1]['id']).status_code==403
    assert decision(admin,unique['id'],'change',residents[1]['id']).status_code==200
    assert detail(own,unique['id'])['resident']['id']==residents[1]['id']
    dup=post(own,f'/api/rooms/{room}/messages',{'body':residents[2]['name']+' 어르신 관찰'}).json()
    assert len(dup['resident_links'])==2 and all(x['status']=='candidate' for x in dup['resident_links'])
    denied=own.get(f"/api/messages/{dup['id']}/resident-review/options")
    assert denied.status_code==403
    opts=admin.get(f"/api/messages/{dup['id']}/resident-review/options").json()
    same=[r for r in opts if r['id'] in [r['id'] for r in residents[2:]]]
    assert len(same)==2 and same[0]['room_name']!=same[1]['room_name']
    assert all(set(r)<={'id','display_name','room_name','floor_name','internal_code','service_type'} for r in opts)
    assert decision(admin,dup['id'],'change',residents[2]['id']).status_code==200
    assert detail(own,dup['id'])['resident']['id']==residents[2]['id']


def test_multiple_unique_names_in_message_are_confirmed_together(context):
    _,own,_,room,residents=context
    result=post(own,f'/api/rooms/{room}/messages',{
        'body':f"{residents[0]['name']} 어르신과 {residents[1]['name']} 어르신을 함께 확인했습니다."
    })
    assert result.status_code==201,result.text
    confirmed={link['resident']['id'] for link in result.json()['resident_links'] if link['status']=='confirmed'}
    assert confirmed=={residents[0]['id'],residents[1]['id']}


def test_batch_resident_management_adds_and_removes_in_one_audited_save(context):
    admin,own,_,room,residents=context
    created=post(own,f'/api/rooms/{room}/messages',{'body':residents[0]['name']+' 어르신 관찰'}).json()
    mid=created['id']
    first=patch(admin,f'/api/messages/{mid}/resident-review',{
        'decision':'set','resident_ids':[residents[0]['id'],residents[1]['id']],
    })
    assert first.status_code==200,first.text
    assert {link['resident']['id'] for link in first.json()['resident_links'] if link['status']=='confirmed'}=={
        residents[0]['id'],residents[1]['id'],
    }
    second=patch(admin,f'/api/messages/{mid}/resident-review',{
        'decision':'set','resident_ids':[residents[1]['id']],
    })
    assert second.status_code==200,second.text
    assert [link['resident']['id'] for link in second.json()['resident_links'] if link['status']=='confirmed']==[residents[1]['id']]
    repeated=patch(admin,f'/api/messages/{mid}/resident-review',{
        'decision':'set','resident_ids':[residents[1]['id']],
    })
    assert repeated.status_code==200,repeated.text
    with SessionLocal() as db:
        audits=list(db.scalars(select(AuditEvent).where(
            AuditEvent.target_id==UUID(mid),AuditEvent.action=='message_resident_links.set',
        )))
        assert len(audits)==2


def test_staff_final_text_confirms_all_unique_names_and_clears_stale_candidates(context,monkeypatch):
    _,own,_,room,residents=context
    raw_name=residents[2]['name']
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:raw_name+' 어르신')
    monkeypatch.setattr(main,'extract_report_text',lambda *a,**k:raw_name+' 어르신')
    message,_=upload(own,room,read=True,body='합성 최종문 검증')
    mid=message['id'];aid=message['attachments'][0]['id']
    before=detail(own,mid)
    assert len([link for link in before['resident_links'] if link['status']=='candidate'])==2
    final_text=f"{residents[0]['name']} 어르신과 {residents[1]['name']} 어르신을 확인했습니다."
    saved=patch(own,f'/api/attachments/{aid}/text-extraction',{
        'reviewed_text':final_text,'decision':'direct_edit',
    })
    assert saved.status_code==200,saved.text
    after=detail(own,mid)
    assert {link['resident']['id'] for link in after['resident_links'] if link['status']=='confirmed'}=={
        residents[0]['id'],residents[1]['id'],
    }
    assert not [link for link in after['resident_links'] if link['status']=='candidate']
    assert saved.json()['resident_link_revision']>=1


def test_staff_final_text_preserves_a_manually_selected_resident(context,monkeypatch):
    admin,own,_,room,residents=context
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:residents[2]['name']+' 어르신')
    monkeypatch.setattr(main,'extract_report_text',lambda *a,**k:residents[2]['name']+' 어르신')
    message,_=upload(own,room,read=True,body='합성 수동 연결 보존')
    mid=message['id'];aid=message['attachments'][0]['id']
    selected=patch(admin,f'/api/messages/{mid}/resident-review',{
        'decision':'set','resident_ids':[residents[2]['id']],
    })
    assert selected.status_code==200,selected.text
    selected_link=next(link for link in selected.json()['resident_links'] if link['status']=='confirmed')
    assert selected_link['source']=='manual'
    final_text=f"{residents[0]['name']} 어르신과 {residents[1]['name']} 어르신을 확인했습니다."
    saved=patch(own,f'/api/attachments/{aid}/text-extraction',{
        'reviewed_text':final_text,'decision':'direct_edit',
    })
    assert saved.status_code==200,saved.text
    confirmed={link['resident']['id'] for link in detail(own,mid)['resident_links'] if link['status']=='confirmed'}
    assert confirmed=={residents[0]['id'],residents[1]['id'],residents[2]['id']}


def test_historical_candidate_dry_run_returns_only_counts_and_does_not_write(context,monkeypatch):
    from app.resident_candidate_dry_run import collect_candidate_inventory

    _,own,_,room,residents=context
    raw=f"{residents[0]['name']} 어르신 합성 관찰"
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:raw)
    monkeypatch.setattr(main,'extract_report_text',lambda *a,**k:raw)
    message,_=upload(own,room,read=True,body='합성 점검')
    with SessionLocal() as db:
        before=db.scalar(select(func.count()).select_from(MessageResidentLink))
        result=collect_candidate_inventory(db)
        db.rollback()
    with SessionLocal() as db:
        after=db.scalar(select(func.count()).select_from(MessageResidentLink))
    serialized=json.dumps(result,ensure_ascii=False)
    assert result['ok'] and result['writes_performed']==0
    assert result['counts']['candidate_link_count']>=1
    assert result['counts']['raw_ocr_only_candidate_count']>=1
    assert before==after
    assert all(resident['name'] not in serialized for resident in residents)
    assert message['body'] not in serialized

def test_candidate_confirm_change_unrelated_audit_and_evidence(context,monkeypatch):
    admin,own,other,room,residents=context
    name=residents[0]['name'];monkeypatch.setattr(main,'extract_handwriting_text',lambda *a,**k:name+' 어르신 물 180ml 섭취 확인')
    message,_=upload(own,room,read=True,body='물 180ml를 제공했습니다.');mid=message['id'];aid=message['attachments'][0]['id']
    current=detail(own,mid);assert current['resident_links'][0]['status']=='candidate' and current['resident'] is None
    assert any(m['id']==mid for m in admin.get('/api/workdesk/resident-confirmations').json()['messages'])
    def targets():
        with SessionLocal() as db:
            source=briefing_sources(db,[db.get(Message,UUID(mid))])[0].message
            return {str(x.resident.id) for x in source.resident_links}
    def assert_scope(resident_id,expected):
        search=own.get(f'/api/rooms/{room}/message-search',params={'resident_id':resident_id})
        assert search.status_code==200
        assert (mid in {m['id'] for m in search.json()['messages']})==expected
        today=datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        period=post(admin,'/api/workdesk/period-review',{'start_date':today,'end_date':today,'room_id':room,'resident_id':resident_id,'response_mode':'briefing','enhance_summary':False,'save_history':False})
        assert period.status_code==200
        assert (mid in period.json()['source_ids'])==expected
    assert targets()==set()
    assert_scope(residents[0]['id'],False)
    assert decision(other,mid,'confirm',residents[0]['id']).status_code==403
    assert decision(own,mid,'confirm',residents[0]['id']).status_code==403
    assert decision(admin,mid,'confirm',residents[0]['id']).status_code==200
    assert targets()=={residents[0]['id']}
    assert_scope(residents[0]['id'],True)
    assert all(m['id']!=mid for m in admin.get('/api/workdesk/resident-confirmations').json()['messages'])
    assert decision(admin,mid,'change',residents[1]['id']).status_code==200
    assert targets()=={residents[1]['id']}
    assert_scope(residents[0]['id'],False);assert_scope(residents[1]['id'],True)
    assert decision(admin,mid,'unrelated').status_code==200
    assert targets()==set() and detail(own,mid)['resident'] is None
    assert_scope(residents[1]['id'],False)
    assert patch(own,f'/api/attachments/{aid}/photo-reading',{'decision':'read'}).status_code==200
    assert detail(own,mid)['resident_links']==[]
    with SessionLocal() as db:
        audits=list(db.scalars(select(AuditEvent).where(AuditEvent.target_id==UUID(mid),AuditEvent.action=='message_resident_link.reviewed')))
        assert len(audits)==3 and all(a.actor_id and a.created_at for a in audits)
        assert all(name not in str(a.details) for a in audits)


def test_single_resident_room_priority_and_current_search_scope(context):
    admin,own,_,_,residents=context
    with SessionLocal() as db:
        db.get(Resident,UUID(residents[0]['id'])).service_type='homecare';db.commit()
    room=post(admin,'/api/admin/rooms',{'name':'합성 전용방 '+uuid4().hex[:6],'kind':'custom','member_ids':[own.get('/api/auth/me').json()['id']],'resident_scope':'homecare'})
    assert room.status_code==201
    result=post(own,f"/api/rooms/{room.json()['id']}/messages",{'body':residents[1]['name']+' 어르신 물 180ml 제공'}).json()
    assert result['resident']['id']==residents[0]['id']
    assert [l['status'] for l in result['resident_links']]==['confirmed']
    with SessionLocal() as db:
        message=db.get(Message,UUID(result['id']))
        matches=main._message_search_matches(message,residents[0]['name'].casefold())
        assert any(m.source_type=='resident' for m in matches)


def test_reviewed_ocr_not_required_never_reuses_old_confirmed_text(context):
    _,own,_,room,_=context
    message,_=upload(own,room,read=True);aid=message['attachments'][0]['id']
    response=patch(own,f'/api/attachments/{aid}/text-extraction',{'reviewed_text':'합성 수정문 물 180ml 제공','decision':'direct_edit'})
    assert response.status_code==200 and response.json()['text_extraction']['status']=='reviewed'
    assert extract(own,aid)['text_extraction']['latest_confirmed_text']
    assert patch(own,f'/api/attachments/{aid}/photo-reading',{'decision':'not_required'}).status_code==200
    saved=extract(own,aid);assert saved['text_extraction']['latest_confirmed_text'] is None
    with SessionLocal() as db:
        source=briefing_sources(db,[db.get(Message,UUID(message['id']))])[0]
        assert source.message.attachments[0].text_extraction.latest_confirmed_text is None
    assert patch(own,f'/api/attachments/{aid}/photo-reading',{'decision':'read'}).status_code==200
    assert extract(own,aid)['text_extraction']['status']=='completed'


def test_ocr_arriving_after_unrelated_decision_cannot_recreate_candidate(context,monkeypatch):
    _,own,_,room,residents=context
    message,_=upload(own,room);mid=message['id'];aid=message['attachments'][0]['id']
    def read(*a,**k):
        assert decision(own,mid,'unrelated').status_code==200
        return residents[0]['name']+' 어르신 관찰'
    monkeypatch.setattr(main,'extract_handwriting_text',read);monkeypatch.setattr(main,'extract_report_text',read)
    assert patch(own,f'/api/attachments/{aid}/photo-reading',{'decision':'read'}).status_code==200
    assert detail(own,mid)['resident_links']==[]


def test_additive_status_migration_preserves_rows_and_refuses_lossy_downgrade(migration_fixture,monkeypatch):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    migration=Path(__file__).parents[1]/'migrations/postgres_versions/047_photo_reading_states.py'
    spec=importlib.util.spec_from_file_location('photo_status_migration',migration);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    conn = migration_fixture
    op=Operations(MigrationContext.configure(conn));monkeypatch.setattr(module,'op',op)
    before=conn.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all()
    assert len(before) == 1
    op.drop_constraint('attachment_text_extractions_status_check','attachment_text_extractions',type_='check')
    op.create_check_constraint('attachment_text_extractions_status_check','attachment_text_extractions',
        "status IN ('pending','processing','completed','failed','reviewed')")
    module.upgrade()
    assert conn.execute(text('SELECT to_jsonb(m)::text FROM staff_hub_messages m')).scalars().all()==before
    assert conn.execute(text('SELECT extracted_text FROM attachment_text_extractions')).scalar() == '합성 원문'
    conn.execute(text("UPDATE attachment_text_extractions SET status='not_required'"))
    with pytest.raises(RuntimeError,match='preserved'):module.downgrade()
    assert conn.execute(text('SELECT status FROM attachment_text_extractions')).scalar()=='not_required'


def test_duplicate_read_is_single_job_and_processor_can_review_in_scope(context,monkeypatch):
    admin,own,other,room,residents=context
    own_id=own.get('/api/auth/me').json()['id'];other_id=other.get('/api/auth/me').json()['id']
    business=post(admin,'/api/org-units',{'unit_type':'business','name':'합성 검증 사업부 '+uuid4().hex[:6]})
    assert business.status_code==201
    assert patch(admin,f'/api/employees/{own_id}',{'business_id':business.json()['id']}).status_code==200
    assert patch(admin,f'/api/employees/{other_id}',{'business_id':business.json()['id'],'can_process_records':True}).status_code==200
    message,_=upload(own,room);aid=message['attachments'][0]['id'];jobs=[]
    monkeypatch.setattr(main,'_run_attachment_text_extraction',lambda aid:jobs.append(aid))
    for _ in range(2):assert patch(other,f'/api/attachments/{aid}/photo-reading',{'decision':'read'}).status_code==200
    assert len(jobs)==1
    assert decision(other,message['id'],'change',residents[1]['id']).status_code==200
    assert detail(own,message['id'])['resident']['id']==residents[1]['id']
    assert patch(other,f'/api/attachments/{aid}/photo-reading',{'decision':'not_required'}).status_code==200
    assert extract(own,aid)['photo_reading_status']=='not_required'


def test_processor_room_membership_allows_resident_review_even_when_work_item_business_differs(context):
    admin,own,other,room,residents=context
    own_id=own.get('/api/auth/me').json()['id'];other_id=other.get('/api/auth/me').json()['id']
    sender_business=post(admin,'/api/org-units',{'unit_type':'business','name':'합성 작성부서 '+uuid4().hex[:6]})
    processor_business=post(admin,'/api/org-units',{'unit_type':'business','name':'합성 처리부서 '+uuid4().hex[:6]})
    assert sender_business.status_code==201 and processor_business.status_code==201
    assert patch(admin,f'/api/employees/{own_id}',{'business_id':sender_business.json()['id']}).status_code==200
    assert patch(admin,f'/api/employees/{other_id}',{
        'business_id':processor_business.json()['id'],'can_process_records':True,
    }).status_code==200
    created=post(own,f'/api/rooms/{room}/messages',{
        'body':'합성 업무항목이 있는 방 구성원 권한 확인',
        'message_type':'work_request',
        'action':{
            'action_type':'cooperation','assignee_user_id':other_id,'priority':'normal',
        },
    })
    assert created.status_code==201,created.text
    message_id=created.json()['id']
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(WorkItem).where(WorkItem.source_message_id==UUID(message_id)))==1
    options=other.get(f'/api/messages/{message_id}/resident-review/options')
    assert options.status_code==200,options.text
    changed=patch(other,f'/api/messages/{message_id}/resident-review',{
        'decision':'set','resident_ids':[residents[0]['id']],
    })
    assert changed.status_code==200,changed.text

    admin_id=admin.get('/api/auth/me').json()['id']
    private_room=post(admin,'/api/admin/rooms',{
        'name':'합성 접근 차단 방 '+uuid4().hex[:6],
        'kind':'custom','member_ids':[admin_id],'resident_scope':'all',
    })
    assert private_room.status_code==201,private_room.text
    private_message=post(admin,f"/api/rooms/{private_room.json()['id']}/messages",{
        'body':'합성 접근 차단 확인',
    })
    assert private_message.status_code==201,private_message.text
    inaccessible=other.get(f"/api/messages/{private_message.json()['id']}/resident-review/options")
    assert inaccessible.status_code==404,inaccessible.text


def test_bad_central_image_config_does_not_cancel_photo_upload(context,monkeypatch):
    from app import image_ocr_runtime
    _,own,_,room,_=context
    def bad():raise RuntimeError('synthetic configuration failure')
    monkeypatch.setattr(main,'requested_image_runtime',bad)
    monkeypatch.setattr(image_ocr_runtime,'requested_image_runtime',bad)
    message,digest=upload(own,room,read=True);aid=message['attachments'][0]['id']
    assert extract(own,aid)['photo_reading_status']=='failed'
    assert sha256(own.get(f'/api/attachments/{aid}').content).hexdigest()==digest
