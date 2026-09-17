"""Real API/DB transactions using generated documents and isolated test users."""
from hashlib import sha256
from types import SimpleNamespace
from uuid import UUID, uuid4
from sqlalchemy import select, func
import pytest

from test_photo_resident_choice import context, HEADERS
from app import main
from app.database import SessionLocal
from app.models import Message, MessageAttachment, AttachmentTextRevision, WorkItem
from app.attachment_review_policy import attachment_evidence_text
from app.ai_assist import _attachment_extracted_text
from app.confirmed_records import _load_active_evidence


def upload(context, *, audio=False, body=None):
    _, own, _, room, residents=context
    text=f"{residents[0]['name']} · {residents[1]['name']} · 합성확인전용물량 200ml"
    raw=b'RIFF\x04\x00\x00\x00WAVE' if audio else text.encode()
    response=own.post(f'/api/rooms/{room}/messages-with-files',headers=HEADERS,
        data={'body':body or '합성 업무자료','message_type':'chat'},
        files={'files':('합성녹음.wav' if audio else '합성문서.txt',raw,'audio/wav' if audio else 'text/plain')})
    assert response.status_code==201
    message=response.json();aid=message['attachments'][0]['id']
    return own, message, aid, raw, text


def review(c, aid, text, residents, revision=None):
    payload={'decision':'direct_edit','reviewed_text':text,'resident_ids':residents}
    if revision is not None:payload['expected_revision']=revision
    return c.patch(f'/api/attachments/{aid}/text-extraction',json=payload,headers=HEADERS)


@pytest.mark.parametrize('audio',[False,True])
def test_raw_exclusion_staff_atomic_review_and_immutable_versions(context,monkeypatch,audio):
    monkeypatch.setattr(main,'_transcribe_attachment_audio',lambda *a,**k:SimpleNamespace(text='합성확인전용물량 200ml',audio_quality={},processing_seconds=.01,model='synthetic-stt'))
    admin,_,other,_,residents=context
    own,message,aid,raw,text=upload(context,audio=audio)
    saved=own.get(f'/api/attachments/{aid}/staff-review-context')
    assert saved.status_code==200
    initial=saved.json()['attachment']['text_extraction']
    assert initial['status']=='completed' and initial['extracted_text']
    first=initial['original_extracted_text']
    assert not any(link['status']=='confirmed' for link in own.get(f"/api/messages/{message['id']}").json()['message']['resident_links'])
    with SessionLocal() as db:
        attachment=db.get(MessageAttachment,UUID(aid));source=db.get(Message,UUID(message['id']))
        assert attachment_evidence_text(attachment)==''
        assert _attachment_extracted_text(db,attachment.id) is None
        assert main._message_search_matches(source,'합성확인전용물량')==[]
        assert '합성확인전용물량' not in main._period_message_text(source,[])
        item=db.scalar(select(WorkItem).where(WorkItem.source_message_id==source.id))
        assert '합성확인전용물량' not in main._work_item_ai_snapshot(db,item)['body']
        evidence=_load_active_evidence(db,organization_id=source.organization_id,primary_message_id=source.id,source_message_ids=[])
        assert all(row.attachment_id!=attachment.id for row in evidence)
    assert review(other,aid,'합성 확인문',[]).status_code==403
    invalid=review(own,aid,'저장되면 안 되는 합성문',[str(uuid4())])
    assert invalid.status_code==422
    assert own.get(f'/api/attachments/{aid}/staff-review-context').json()['attachment']['text_extraction']['status']=='completed'
    selected=[row['id'] for row in residents[:2]]
    final='직원확인완료 합성물량 200ml'
    response=review(own,aid,final,selected,saved.json()['revision'])
    assert response.status_code==200
    assert response.json()['text_extraction']['status']=='reviewed'
    assert response.json()['text_extraction']['original_extracted_text']==first
    final_revision=response.json()['text_extraction']['review_revision']
    assert review(own,aid,final,selected,saved.json()['revision']).status_code==200
    assert review(own,aid,'다른 합성 문장',selected,saved.json()['revision']).status_code==409
    with SessionLocal() as db:
        attachment=db.get(MessageAttachment,UUID(aid));source=db.get(Message,UUID(message['id']))
        assert attachment_evidence_text(attachment)==final
        assert _attachment_extracted_text(db,attachment.id)==final
        assert main._message_search_matches(source,'직원확인완료')
        assert final in main._period_message_text(source,[])
        assert {str(link.resident_id) for link in source.resident_links if link.status=='confirmed'}==set(selected)
        assert db.scalar(select(func.count()).select_from(AttachmentTextRevision).where(AttachmentTextRevision.attachment_id==attachment.id))==final_revision
    assert sha256(other.get(f'/api/attachments/{aid}').content).digest()==sha256(raw).digest()
    # Authorized re-read does not alter original file or first automatic text.
    assert own.post(f'/api/attachments/{aid}/text-extraction',json={},headers=HEADERS).status_code==200
    current=own.get(f'/api/attachments/{aid}/staff-review-context').json()
    assert current['attachment']['text_extraction']['status']=='completed'
    assert current['attachment']['text_extraction']['original_extracted_text']==first
    assert any(row['kind']=='staff_review' and row['text']==final for row in current['revisions'])


def test_unreviewed_audio_cannot_be_confirmed_by_legacy_link(context,monkeypatch):
    monkeypatch.setattr(main,'_transcribe_attachment_audio',lambda *a,**k:SimpleNamespace(text='합성확인전용물량 200ml',audio_quality={},processing_seconds=.01,model='synthetic-stt'))
    own,_,aid,_,_=upload(context,audio=True)
    # API clients must explicitly choose residents, including an explicit empty list.
    assert own.patch(f'/api/attachments/{aid}/text-extraction',json={'reviewed_text':'합성 확인문'},headers=HEADERS).status_code==422


def test_long_automatic_audio_original_is_not_cut_at_image_preview_limit(context,monkeypatch):
    original='합성 원본 전사 보존 확인. '*1000
    assert len(original)>12000
    monkeypatch.setattr(main,'_transcribe_attachment_audio',lambda *a,**k:SimpleNamespace(text=original,audio_quality={},processing_seconds=.01,model='synthetic-stt'))
    own,_,aid,_,_=upload(context,audio=True)
    data=own.get(f'/api/attachments/{aid}/staff-review-context').json()
    assert data['attachment']['text_extraction']['original_extracted_text']==original
    assert data['revisions'][0]['text']==original


def test_legacy_audio_resident_pointer_cannot_bypass_review(context,monkeypatch):
    from app.models import MessageResidentLink
    from app.period_review_performance import briefing_sources
    monkeypatch.setattr(main,'_transcribe_attachment_audio',lambda *a,**k:SimpleNamespace(text='합성 미검수 전사',audio_quality={},processing_seconds=.01,model='synthetic-stt'))
    own,message,_,_,_=upload(context,audio=True)
    with SessionLocal() as db:
        source=db.get(Message,UUID(message['id']))
        resident_id=UUID(context[4][0]['id'])
        source.resident_id=resident_id
        link=MessageResidentLink(organization_id=source.organization_id,message_id=source.id,
            resident_id=resident_id,source='audio_transcript',status='confirmed')
        db.add(link);db.commit()
        assert main._confirmed_message_resident_links(db,source.id)==[]
        compact=briefing_sources(db,[source])[0].message
        assert compact.resident is None and compact.resident_links==[]
    shown=own.get(f"/api/messages/{message['id']}").json()['message']
    assert shown['resident'] is None and shown['resident_links']==[]
    with SessionLocal() as db:
        link=db.scalar(select(MessageResidentLink).where(MessageResidentLink.message_id==UUID(message['id'])))
        link.source='manual';db.commit()
    assert own.get(f"/api/messages/{message['id']}").json()['message']['resident']['id']==str(resident_id)


def test_review_revocation_and_immutable_history(context):
    from app.models import RoomMembership, User, utcnow
    own,message,aid,raw,_=upload(context)
    selected=[row['id'] for row in context[4][:2]]
    assert review(own,aid,'직원 확인 합성문',selected).status_code==200
    with SessionLocal() as db:
        row=db.scalar(select(AttachmentTextRevision).where(AttachmentTextRevision.attachment_id==UUID(aid)).order_by(AttachmentTextRevision.revision.desc()))
        row.text_content='덮어쓰면 안 되는 합성문'
        with pytest.raises(RuntimeError,match='immutable'):db.commit()
        db.rollback()
        owner=db.get(User,UUID(own.get('/api/auth/me').json()['id']))
        membership=db.scalar(select(RoomMembership).where(RoomMembership.room_id==UUID(context[3]),RoomMembership.staff_id==owner.staff_id,RoomMembership.left_at.is_(None)))
        membership.left_at=utcnow();db.commit()
    assert own.get(f'/api/attachments/{aid}').status_code==403
    assert own.get(f'/api/attachments/{aid}/staff-review-context').status_code==403
    assert review(own,aid,'권한 취소 후 합성문',selected).status_code==403


def test_compact_briefing_projection_uses_only_verified_staff_text(context):
    from app.period_review_performance import briefing_sources
    from app.care_record_journey import build_care_topics
    from datetime import datetime, timedelta, timezone
    own,message,aid,_,_=upload(context)
    selected=[context[4][0]['id']]
    with SessionLocal() as db:
        source=db.get(Message,UUID(message['id']))
        assert attachment_evidence_text(briefing_sources(db,[source])[0].message.attachments[0])==''
    assert review(own,aid,'물 200ml 섭취를 확인했습니다.',selected).status_code==200
    with SessionLocal() as db:
        source=db.get(Message,UUID(message['id']))
        projection=briefing_sources(db,[source])[0].message.attachments[0]
        assert attachment_evidence_text(projection)=='물 200ml 섭취를 확인했습니다.'
