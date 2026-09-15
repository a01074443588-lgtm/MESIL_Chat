"""Staff review retries must agree with the current message and locked job state."""
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import func, select

from app import main
from app.database import SessionLocal
from app.document_review_flow import save_staff_review
from app.models import AttachmentTextExtraction, AttachmentTextRevision, MessageAttachment, User, utcnow
from test_document_staff_review_flow import upload, review
from test_photo_resident_choice import context, HEADERS


@pytest.mark.parametrize('audio', [False, True])
def test_same_text_resave_reconciles_a_later_resident_link_change(context, monkeypatch, audio):
    monkeypatch.setattr(main, '_transcribe_attachment_audio', lambda *a, **k:
        SimpleNamespace(text='합성 최초 전사', audio_quality={}, processing_seconds=.01, model='synthetic-stt'))
    own, message, aid, raw, _ = upload(context, audio=audio)
    original_target, other_target = (row['id'] for row in context[4][:2])
    final = '직원이 확인한 합성 수정본'
    saved = review(own, aid, final, [original_target])
    assert saved.status_code == 200
    revision = saved.json()['text_extraction']['review_revision']
    changed = context[0].patch(f"/api/messages/{message['id']}/resident-review",
        json={'decision': 'set', 'resident_ids': [other_target]}, headers=HEADERS)
    assert changed.status_code == 200
    resaved = review(own, aid, final, [original_target], revision)
    assert resaved.status_code == 200
    current = own.get(f"/api/messages/{message['id']}").json()['message']
    assert {row['resident']['id'] for row in current['resident_links'] if row['status'] == 'confirmed'} == {original_target}
    assert resaved.json()['text_extraction']['review_revision'] == revision + 1
    duplicate = review(own, aid, final, [original_target], revision)
    assert duplicate.status_code == 200
    assert duplicate.json()['text_extraction']['review_revision'] == revision + 1
    assert own.get(f'/api/attachments/{aid}').content == raw


@pytest.mark.parametrize('audio', [False, True])
def test_review_rechecks_job_state_after_acquiring_lock(context, monkeypatch, audio):
    monkeypatch.setattr(main, '_transcribe_attachment_audio', lambda *a, **k:
        SimpleNamespace(text='합성 최초 전사', audio_quality={}, processing_seconds=.01, model='synthetic-stt'))
    own, _, aid, _, _ = upload(context, audio=audio)
    uid = UUID(own.get('/api/auth/me').json()['id'])
    with SessionLocal() as stale:
        attachment = stale.get(MessageAttachment, UUID(aid))
        assert attachment.text_extraction.status == 'completed'
        with SessionLocal() as current:
            job = current.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id == UUID(aid)))
            job.status = 'processing'
            job.started_at = utcnow()
            current.commit()
        payload = SimpleNamespace(decision='direct_edit', reviewed_text='합성 직원 수정', resident_ids=[], expected_revision=1)
        with pytest.raises(HTTPException) as denied:
            save_staff_review(stale, attachment, payload, stale.get(User, uid), BackgroundTasks())
        assert denied.value.status_code == 409
        stale.rollback()
    with SessionLocal() as db:
        job = db.scalar(select(AttachmentTextExtraction).where(AttachmentTextExtraction.attachment_id == UUID(aid)))
        assert job.status == 'processing'
        assert db.scalar(select(func.count()).select_from(AttachmentTextRevision).where(AttachmentTextRevision.attachment_id == UUID(aid))) == 1


def test_stale_editor_cannot_overwrite_newer_resident_selection(context):
    own, message, aid, _, _ = upload(context)
    target, changed_target = (row['id'] for row in context[4][:2])
    assert review(own, aid, '합성 확인문', [target]).status_code == 200
    state = own.get(f'/api/attachments/{aid}/staff-review-context').json()
    with SessionLocal() as db:
        from app.photo_reading import resident_review_metadata
        from app.models import Message
        selection_revision = resident_review_metadata(db.get(Message, UUID(message['id'])))['revision']
    changed = context[0].patch(f"/api/messages/{message['id']}/resident-review",
        json={'decision': 'set', 'resident_ids': [changed_target]}, headers=HEADERS)
    assert changed.status_code == 200
    response = own.patch(f'/api/attachments/{aid}/text-extraction', headers=HEADERS,
        json={'decision':'direct_edit', 'reviewed_text':'합성 확인문', 'resident_ids':[target],
            'expected_revision':state['revision'], 'expected_resident_revision':selection_revision})
    assert response.status_code == 409
    current = own.get(f"/api/messages/{message['id']}").json()['message']
    assert {row['resident']['id'] for row in current['resident_links'] if row['status']=='confirmed'} == {changed_target}
    latest = own.get(f'/api/attachments/{aid}/staff-review-context').json()
    assert latest['revision'] == state['revision']
    assert latest['resident_revision'] > selection_revision


@pytest.mark.parametrize('same_payload', [False, True])
def test_two_reviewers_serialize_without_duplicate_or_lost_versions(context, same_payload):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    own, _, aid, _, _ = upload(context)
    admin = context[0]
    state = own.get(f'/api/attachments/{aid}/staff-review-context').json()
    barrier = Barrier(2)
    def save(client, text):
        barrier.wait(timeout=5)
        return client.patch(f'/api/attachments/{aid}/text-extraction', headers=HEADERS,
            json={'decision':'direct_edit', 'reviewed_text':text, 'resident_ids':[context[4][0]['id']],
                'expected_revision':state['revision'], 'expected_resident_revision':state['resident_revision']}).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(save, own, '합성 동시 수정본 하나')
        second = pool.submit(save, admin, '합성 동시 수정본 하나' if same_payload else '합성 동시 수정본 둘')
        statuses = sorted([first.result(timeout=10), second.result(timeout=10)])
    assert statuses == ([200, 200] if same_payload else [200, 409])
    latest = own.get(f'/api/attachments/{aid}/staff-review-context').json()
    assert latest['revision'] == state['revision'] + 1
