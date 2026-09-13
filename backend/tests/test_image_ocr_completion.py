from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from uuid import UUID
import pytest
from PIL import Image,ImageDraw
from fastapi.testclient import TestClient
from sqlalchemy import select
from app import main,ocr,image_ocr_runtime as routing
from app.ai_settings_store import load_ai_settings
from app.database import SessionLocal
from app.models import AttachmentTextExtraction, Message, MessageResidentLink, Resident, Organization, User
from app.image_ocr_geometry import prepare_image_geometry


def image_bytes():
    image=Image.new('RGB',(320,220),'white');ImageDraw.Draw(image).text((30,50),'SYNTHETIC 180 ml',fill='black')
    output=BytesIO();image.save(output,'PNG');return output.getvalue()


@contextmanager
def client():
    with TestClient(main.app) as c:
        assert c.post('/api/auth/login',json={'username':'admin','password':'AdminPass!234'},headers={'origin':'http://testserver'}).status_code==200
        yield c


@pytest.mark.parametrize('result,error_type', [('', 'empty_result'), ('repeat', 'output_blocked'), ('failure', 'image_processing_failed')])
def test_empty_blocked_and_failed_do_not_complete_or_cancel_upload(monkeypatch,result,error_type):
    def read(*args,**kwargs):
        with SessionLocal() as db:
            current = db.scalar(
                select(AttachmentTextExtraction)
                .where(AttachmentTextExtraction.status == 'processing')
                .order_by(AttachmentTextExtraction.started_at.desc())
            )
            assert current is not None and current.status == 'processing'
        if result=='repeat':raise ocr.OcrError(ocr.HANDWRITING_SAFE_FAILURE_MESSAGE)
        if result=='failure':raise RuntimeError('private synthetic source must not be logged')
        return result
    monkeypatch.setattr(main,'extract_handwriting_text',read)
    with client() as c:
        room=c.get('/api/rooms').json()[0]['id']
        response=c.post(f'/api/rooms/{room}/messages-with-files',data={'report_image':'true','body':'합성 자동 판독 검증'},files={'files':('synthetic.png',image_bytes(),'image/png')},headers={'origin':'http://testserver'})
        assert response.status_code==201
        attachment=response.json()['attachments'][0]
        assert attachment['text_extraction']['status']=='pending'
        detail=c.get(f"/api/attachments/{attachment['id']}/text-extraction?preview=true").json()['text_extraction']
        assert detail['status']=='failed' and detail['result_char_count']==0
        assert detail['error_type']==error_type
        assert 'private synthetic' not in str(detail)
        assert c.get(f"/api/messages/{response.json()['id']}").status_code==200


def test_preview_loads_authorized_text_without_unredacting_message_list(monkeypatch):
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *args,**kwargs:'합성 판독문: 180ml 제공')
    monkeypatch.setattr(main,'locate_report_text_regions',lambda *args,**kwargs:[])
    with client() as c:
        room=c.get('/api/rooms').json()[0]['id']
        r=c.post(f'/api/rooms/{room}/messages-with-files',data={'report_image':'true','body':'합성'},files={'files':('synthetic.png',image_bytes(),'image/png')},headers={'origin':'http://testserver'})
        aid=r.json()['attachments'][0]['id']
        listed=c.get(f"/api/messages/{r.json()['id']}").json()['message']['attachments'][0]['text_extraction']
        assert listed['content_included'] is False and listed['extracted_text'] is None
        detail=c.get(f'/api/attachments/{aid}/text-extraction?preview=true').json()['text_extraction']
        assert detail['status']=='completed' and detail['content_included'] is True
        assert detail['extracted_text']=='합성 판독문: 180ml 제공' and detail['result_char_count']>0
        with TestClient(main.app) as anonymous:assert anonymous.get(f'/api/attachments/{aid}/text-extraction?preview=true').status_code==401
        with SessionLocal() as db:
            row=db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id==UUID(aid)))
            row.extracted_text='[표식] '*80;row.original_extracted_text=row.extracted_text;db.commit()
        blocked=c.get(f'/api/attachments/{aid}/text-extraction?preview=true').json()['text_extraction']
        assert blocked['status']=='failed' and blocked['error_type']=='output_blocked'
        assert blocked['extracted_text'] is None and blocked['suggested_text'] is None
        with SessionLocal() as db:assert db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id==UUID(aid))).status=='completed'


def test_central_text_inheritance_uses_configured_vision_fallback(monkeypatch):
    document,_=load_ai_settings()
    document.central_models=document.central_models.model_copy(update={'inheritance_version':2,'default_provider':'ollama','text_default_model':'synthetic-text','vision_default_model':None,'vision_fallback_model':'synthetic-vision','base_url':'http://127.0.0.1:11434'})
    monkeypatch.setattr(routing.settings,'ocr_provider','ollama')
    monkeypatch.setattr(routing,'load_ai_settings',lambda:(document,True))
    calls=[]
    def request(base,path,body,timeout):
        calls.append(body['model'])
        return {'capabilities':['completion'] if body['model']=='synthetic-text' else ['completion','vision']}
    monkeypatch.setattr(routing,'local_json_request',request)
    metadata={}
    with routing.attachment_image_runtime(metadata) as runtime:assert runtime.model=='synthetic-vision'
    assert calls==['synthetic-text','synthetic-vision'] and metadata['fallback_used']


def test_blank_and_faint_text_discrimination_preserves_original(tmp_path):
    assert ocr.validate_handwriting_model_text('[[NO_TEXT]]', expected_line_count=12) == ''
    assert ocr._validate_model_text('[[NO_TEXT]]') == ''
    blank=tmp_path/'blank.png';Image.new('RGB',(1600,900),'white').save(blank)
    assert ocr.image_likely_contains_text(blank) is False
    image=Image.new('RGB',(1600,900),'white');draw=ImageDraw.Draw(image)
    draw.text((600,450),'SYNTHETIC',fill=(245,245,245))
    faint=tmp_path/'faint.png';image.save(faint)
    assert ocr.image_likely_contains_text(faint) is True
    before=sha256(faint.read_bytes()).hexdigest()
    metadata=prepare_image_geometry(faint,tmp_path/'derivative.png')
    assert metadata['original_preserved'] and sha256(faint.read_bytes()).hexdigest()==before


def test_blank_upload_skips_model_and_is_not_completed(monkeypatch):
    monkeypatch.setattr(main,'extract_handwriting_text',lambda *args,**kwargs:pytest.fail('blank image must not call model'))
    output=BytesIO();Image.new('RGB',(50,50),'white').save(output,'PNG')
    with client() as c:
        room=c.get('/api/rooms').json()[0]['id']
        response=c.post(f'/api/rooms/{room}/messages-with-files',data={'report_image':'true','body':'합성 빈 사진'},files={'files':('blank.png',output.getvalue(),'image/png')},headers={'origin':'http://testserver'})
        assert response.status_code==201
        data=c.get('/api/attachments/'+response.json()['attachments'][0]['id']+'/text-extraction?preview=true').json()['text_extraction']
        assert data['status']=='no_text' and data['result_char_count']==0


def test_optional_name_scan_failure_keeps_valid_ocr(monkeypatch):
    monkeypatch.setattr(main, 'extract_handwriting_text', lambda *a, **k: '합성 업무 내용을 확인했습니다.')
    def unavailable(*args, **kwargs): raise routing.ImageOcrFailure('image_retry_limit', '합성 호출 한도')
    monkeypatch.setattr(main, 'extract_report_name_candidates', unavailable)
    with client() as c:
        room=c.get('/api/rooms').json()[0]['id']
        r=c.post(f'/api/rooms/{room}/messages-with-files',data={'report_image':'true','body':'합성'},files={'files':('synthetic.png',image_bytes(),'image/png')},headers={'origin':'http://testserver'})
        assert r.status_code==201
        result=c.get(f"/api/attachments/{r.json()['attachments'][0]['id']}/text-extraction?preview=true").json()['text_extraction']
        assert result['status']=='completed' and result['result_char_count']>0


def test_resident_candidates_follow_current_attempt_and_preserve_confirmed(monkeypatch):
    result = ['가람샘 어르신 불편 호소 없음']
    monkeypatch.setattr(main, 'extract_handwriting_text', lambda *a, **k: result[0])
    monkeypatch.setattr(main, 'extract_report_text', lambda *a, **k: result[0])
    monkeypatch.setattr(main, 'locate_report_text_regions', lambda *a, **k: [])
    with client() as c:
        with SessionLocal() as db:
            org = db.scalar(select(User).where(User.username == 'admin')).organization
            residents = [Resident(organization_id=org.id, internal_code='OCR-SYNTH-'+str(i), display_name=name, service_type='facility', is_test_data=True) for i, name in enumerate(['가람샘', '누리샘', '다솜샘'])]
            db.add_all(residents); db.commit(); ids = [str(r.id) for r in residents]
        room = c.get('/api/rooms').json()[0]['id']
        r = c.post(f'/api/rooms/{room}/messages-with-files', data={'report_image':'true','body':'합성 후보 검증'}, files={'files':('synthetic.png', image_bytes(), 'image/png')}, headers={'origin':'http://testserver'})
        assert r.status_code == 201, r.json()
        mid = r.json()['id']; aid = r.json()['attachments'][0]['id']
        def detail(): return c.get(f'/api/messages/{mid}').json()['message']
        def candidates(): return [x for x in detail()['resident_links'] if x['status']=='candidate']
        assert [x['resident']['id'] for x in candidates()] == [ids[0]]
        assert candidates()[0]['evidence'][0]['attempt_number'] == 1
        with SessionLocal() as db:
            db.add(MessageResidentLink(organization_id=db.get(Message,UUID(mid)).organization_id,message_id=UUID(mid),resident_id=UUID(ids[2]),source='ocr_exact',status='confirmed'))
            db.commit()
        for text, expected in [('누리샘 어르신 관찰했습니다.', [ids[1]]), ('', []), ('[표식] '*80, [])]:
            result[0] = text
            retry = c.post(f'/api/messages/{mid}/image-text-extractions', json={'force':True}, headers={'origin':'http://testserver'})
            assert retry.status_code == 200
            assert [x['resident']['id'] for x in candidates()] == expected
            assert any(x['resident']['id']==ids[2] and x['status']=='confirmed' for x in detail()['resident_links'])
            if not expected:
                assert detail()['attachments'][0]['resident_candidate_notice']=='판독 결과를 먼저 확인해 주세요'
            with SessionLocal() as db:
                message=db.get(Message, UUID(mid))
                assert not any(x.source=='resident' for x in main._message_search_matches(message,'가람샘'))
                assert '가람샘' not in main._period_message_text(message, [])
        # A legacy orphan is hidden on read, without deleting or rewriting it.
        with SessionLocal() as db:
            extraction=db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id==UUID(aid)))
            extraction.status='completed'; extraction.extracted_text=''; extraction.original_extracted_text=''; extraction.suggestion_details=None; db.commit()
        assert candidates()==[]
        with SessionLocal() as db:
            assert len(db.scalars(select(MessageResidentLink).where(MessageResidentLink.message_id==UUID(mid))).all())==3
