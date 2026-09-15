"""Synthetic API regression tests for the document/audio staff-review gate."""

# The imported pytest fixture is discovered by name and then injected as an
# argument, which static analysis otherwise reports as an unused/redefined name.
# ruff: noqa: F811

from types import SimpleNamespace
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app import main
from app.confirmed_records import (
    ConfirmedRecordError,
    ConfirmedWorkRecord,
    persist_confirmed_records_from_work_item,
)
from app.database import SessionLocal
from app.models import MessageAttachment, WorkItem, WorkItemDocumentDraft
from test_photo_resident_choice import HEADERS, context, picture  # noqa: F401


REVIEW_REQUIRED_MESSAGE = (
    "첨부된 문서 또는 음성 내용을 먼저 확인해 주세요. 원본과 비교하여 내용을 "
    "수정한 뒤 ‘확인 완료’를 눌러야 업무기록을 만들 수 있습니다."
)


def _stub_audio(monkeypatch):
    monkeypatch.setattr(
        main,
        "_transcribe_attachment_audio",
        lambda *args, **kwargs: SimpleNamespace(
            text="합성 음성 판독문 200ml",
            audio_quality={},
            processing_seconds=0.01,
            model="synthetic-stt",
        ),
    )


def _upload_required_attachment(context, monkeypatch, *, kind):
    admin, owner, _, room_id, residents = context
    resident_id = residents[0]["id"]
    if kind == "audio":
        _stub_audio(monkeypatch)
        filename, raw, mime = "합성녹음.wav", b"RIFF\x04\x00\x00\x00WAVE", "audio/wav"
    else:
        filename = "합성문서.txt"
        raw = "합성 문서 판독문 200ml".encode()
        mime = "text/plain"
    sent = owner.post(
        f"/api/rooms/{room_id}/messages-with-files",
        headers=HEADERS,
        data={
            "body": "합성 본문만으로도 업무초안이 만들어지면 안 됩니다.",
            "message_type": "chat",
            "resident_ids": [resident_id],
        },
        files={"files": (filename, raw, mime)},
    )
    assert sent.status_code == 201, sent.text
    message = sent.json()
    item = next(
        row
        for row in admin.get("/api/work-items").json()
        if row["source_snapshot"]["message_id"] == message["id"]
    )
    return admin, owner, residents, message, item


def _review(owner, message, resident_id, *, text="직원이 원본과 비교한 합성 확인문 200ml"):
    return owner.patch(
        f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
        headers=HEADERS,
        json={
            "decision": "direct_edit",
            "reviewed_text": text,
            "resident_ids": [resident_id],
        },
    )


def _confirmation_payload(prototype):
    return {
        **prototype["ai_suggestion"],
        "summary": "직원이 확인한 합성 업무기록",
        "reviewer_notes": "합성자료 검수 완료",
        "verification_acknowledged": True,
    }


@pytest.mark.parametrize("kind", ["document", "audio"])
@pytest.mark.parametrize("endpoint", ["prototype-suggestion", "ai-review"])
def test_unreviewed_required_attachment_blocks_work_suggestion(
    context, monkeypatch, kind, endpoint
):
    admin, _, _, _, item = _upload_required_attachment(context, monkeypatch, kind=kind)
    assert item["ai_state"] == "not_requested"
    assert item["ai_suggestion"] is None

    response = admin.post(
        f"/api/work-items/{item['id']}/{endpoint}", json={}, headers=HEADERS
    )

    assert response.status_code == 409
    assert response.json()["detail"] == REVIEW_REQUIRED_MESSAGE

@pytest.mark.parametrize("kind", ["document", "audio"])
def test_staff_reviewed_required_attachment_allows_suggestion_and_confirmation(
    context, monkeypatch, kind
):
    admin, owner, residents, message, item = _upload_required_attachment(
        context, monkeypatch, kind=kind
    )
    reviewed = _review(owner, message, residents[0]["id"])
    assert reviewed.status_code == 200, reviewed.text

    suggestion = admin.post(
        f"/api/work-items/{item['id']}/prototype-suggestion", json={}, headers=HEADERS
    )
    assert suggestion.status_code == 200, suggestion.text
    ai_suggestion = admin.post(
        f"/api/work-items/{item['id']}/ai-review", json={}, headers=HEADERS
    )
    assert ai_suggestion.status_code == 200, ai_suggestion.text

    confirmed = admin.post(
        f"/api/work-items/{item['id']}/confirm",
        json=_confirmation_payload(ai_suggestion.json()),
        headers=HEADERS,
    )
    assert confirmed.status_code == 200, confirmed.text


def test_existing_unreviewed_draft_is_blocked_at_confirm_and_persistence(
    context, monkeypatch
):
    admin, _, _, message, item_payload = _upload_required_attachment(
        context, monkeypatch, kind="document"
    )
    with SessionLocal() as db:
        item = db.get(WorkItem, UUID(item_payload["id"]))
        draft = main.build_prototype_suggestion(main._work_item_ai_snapshot(db, item))
        item.ai_state = "prototype_suggested"
        item.ai_payload = draft
        item.ai_generator = "synthetic-legacy-draft"
        item.status = "in_review"
        db.commit()

    add_draft = admin.post(
        f"/api/work-items/{item_payload['id']}/document-drafts/care_service_record",
        json={},
        headers=HEADERS,
    )
    assert add_draft.status_code == 409
    assert add_draft.json()["detail"] == REVIEW_REQUIRED_MESSAGE

    response = admin.post(
        f"/api/work-items/{item_payload['id']}/confirm",
        json={**draft, "reviewer_notes": "합성 미검수", "verification_acknowledged": True},
        headers=HEADERS,
    )
    assert response.status_code == 409
    assert response.json()["detail"] == REVIEW_REQUIRED_MESSAGE

    with SessionLocal() as db:
        item = db.get(WorkItem, UUID(item_payload["id"]))
        before_records = db.scalar(
            select(func.count())
            .select_from(ConfirmedWorkRecord)
            .where(ConfirmedWorkRecord.organization_id == item.organization_id)
        )
        with pytest.raises(ConfirmedRecordError, match=REVIEW_REQUIRED_MESSAGE) as caught:
            persist_confirmed_records_from_work_item(
                db,
                work_item=item,
                confirmed_payload=draft,
                confirmed_by_user_id=item.source_message.sender_id,
            )
        assert caught.value.code == "attachment_staff_review_required"
        assert db.scalar(
            select(func.count())
            .select_from(ConfirmedWorkRecord)
            .where(ConfirmedWorkRecord.organization_id == item.organization_id)
        ) == before_records


@pytest.mark.parametrize("kind", ["document", "audio"])
def test_reread_invalidates_existing_draft_and_blocks_until_reviewed_again(
    context, monkeypatch, kind
):
    admin, owner, residents, message, item = _upload_required_attachment(
        context, monkeypatch, kind=kind
    )
    assert _review(owner, message, residents[0]["id"]).status_code == 200
    suggestion = admin.post(
        f"/api/work-items/{item['id']}/prototype-suggestion", json={}, headers=HEADERS
    )
    assert suggestion.status_code == 200, suggestion.text
    old_confirmation = _confirmation_payload(suggestion.json())

    reread = owner.post(
        f"/api/attachments/{message['attachments'][0]['id']}/text-extraction",
        json={"force": True},
        headers=HEADERS,
    )
    assert reread.status_code == 200, reread.text

    with SessionLocal() as db:
        stored = db.get(WorkItem, UUID(item["id"]))
        assert stored.ai_state == "not_requested"
        assert stored.ai_payload is None
        assert stored.ai_generator is None
        assert stored.ai_generated_at is None
        assert db.scalar(
            select(func.count())
            .select_from(WorkItemDocumentDraft)
            .where(
                WorkItemDocumentDraft.work_item_id == stored.id,
                WorkItemDocumentDraft.is_current.is_(True),
            )
        ) == 0

    for endpoint, payload in (
        ("prototype-suggestion", {}),
        ("confirm", old_confirmation),
    ):
        blocked = admin.post(
            f"/api/work-items/{item['id']}/{endpoint}", json=payload, headers=HEADERS
        )
        assert blocked.status_code == 409
        assert blocked.json()["detail"] == REVIEW_REQUIRED_MESSAGE

    reviewed_again = _review(owner, message, residents[0]["id"])
    assert reviewed_again.status_code == 200, reviewed_again.text
    regenerated = admin.post(
        f"/api/work-items/{item['id']}/prototype-suggestion", json={}, headers=HEADERS
    )
    assert regenerated.status_code == 200, regenerated.text
    confirmed = admin.post(
        f"/api/work-items/{item['id']}/confirm",
        json=_confirmation_payload(regenerated.json()),
        headers=HEADERS,
    )
    assert confirmed.status_code == 200, confirmed.text


def test_text_only_and_general_image_messages_keep_existing_workflow(context):
    admin, owner, _, room_id, residents = context
    resident_id = residents[0]["id"]
    text_message = owner.post(
        f"/api/rooms/{room_id}/messages",
        json={"body": "합성 일반 텍스트 업무보고", "resident_id": resident_id},
        headers=HEADERS,
    )
    assert text_message.status_code == 201, text_message.text
    image_message = owner.post(
        f"/api/rooms/{room_id}/messages-with-files",
        data={
            "body": "합성 일반 사진 업무보고",
            "message_type": "chat",
            "resident_ids": [resident_id],
        },
        files={"files": ("합성사진.png", picture(), "image/png")},
        headers=HEADERS,
    )
    assert image_message.status_code == 201, image_message.text

    items = admin.get("/api/work-items").json()
    item_by_message = {
        row["source_snapshot"]["message_id"]: row for row in items
    }
    for message in (text_message.json(), image_message.json()):
        item = item_by_message[message["id"]]
        suggestion = admin.post(
            f"/api/work-items/{item['id']}/prototype-suggestion",
            json={},
            headers=HEADERS,
        )
        assert suggestion.status_code == 200, suggestion.text


def test_current_review_must_match_latest_revision_and_attachment(context, monkeypatch):
    admin, owner, residents, message, item = _upload_required_attachment(
        context, monkeypatch, kind="document"
    )
    assert _review(owner, message, residents[0]["id"]).status_code == 200
    attachment_id = UUID(message["attachments"][0]["id"])
    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, attachment_id)
        attachment.text_extraction.reviewed_text = "이력과 다른 합성 확인문"
        db.commit()

    blocked = admin.post(
        f"/api/work-items/{item['id']}/prototype-suggestion", json={}, headers=HEADERS
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == REVIEW_REQUIRED_MESSAGE
