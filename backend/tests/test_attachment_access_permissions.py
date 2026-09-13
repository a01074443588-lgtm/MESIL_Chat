"""Attachment document permissions use synthetic users and content only."""

from fastapi import HTTPException
import pytest
from sqlalchemy import select
from uuid import UUID

from app import main
from app.database import SessionLocal
from app.models import MessageAttachment, Organization, User
from app.services import attachment_access_capabilities
from test_document_staff_review_flow import review, upload
from test_photo_resident_choice import HEADERS, context  # noqa: F401


def _set_record_permission(client, enabled: bool) -> UUID:
    user_id = UUID(client.get("/api/auth/me").json()["id"])
    with SessionLocal() as db:
        user = db.get(User, user_id)
        user.can_process_records = enabled
        db.commit()
    return user_id


def test_record_processor_uploader_keeps_owner_review_path(context, monkeypatch):
    """A processor flag must not send an accessible own upload down a stricter path."""

    own, _message, attachment_id, _raw, _text = upload(context)
    owner_id = _set_record_permission(own, True)

    def reject_processor_path(*_args, **_kwargs):
        raise HTTPException(403, "이 업무 항목을 처리할 수 없습니다.")

    monkeypatch.setattr(main, "_attachment_for_processor", reject_processor_path)
    with SessionLocal() as db:
        owner = db.get(User, owner_id)
        attachment = main._attachment_for_text_editor(
            db, owner, UUID(attachment_id)
        )
        assert attachment.id == UUID(attachment_id)


def test_message_attachment_exposes_server_decided_capabilities(context):
    admin, own, other, _room_id, _residents = context
    own, message, attachment_id, _raw, _text = upload(context)

    owner_attachment = own.get(
        f"/api/messages/{message['id']}", headers=HEADERS
    ).json()["message"]["attachments"][0]
    peer_attachment = other.get(
        f"/api/messages/{message['id']}", headers=HEADERS
    ).json()["message"]["attachments"][0]
    admin_attachment = admin.get(
        f"/api/messages/{message['id']}", headers=HEADERS
    ).json()["message"]["attachments"][0]

    assert owner_attachment["can_download_original"] is True
    assert owner_attachment["can_view_reviewed_text"] is True
    assert owner_attachment["can_review_text"] is True
    assert owner_attachment["can_request_reading"] is True
    assert peer_attachment["can_download_original"] is True
    assert peer_attachment["can_view_reviewed_text"] is True
    assert peer_attachment["can_review_text"] is False
    assert peer_attachment["can_request_reading"] is False
    assert admin_attachment["can_review_text"] is True
    assert admin_attachment["can_request_reading"] is True

    denied = other.get(
        f"/api/attachments/{attachment_id}/staff-review-context", headers=HEADERS
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "이 문서의 내용을 확인·수정할 권한이 없습니다."


def test_read_only_room_member_sees_only_staff_confirmed_text(context):
    _admin, own, other, _room_id, residents = context
    own, message, attachment_id, _raw, automatic_text = upload(context)

    before = other.get(f"/api/messages/{message['id']}", headers=HEADERS).json()
    before_extraction = before["message"]["attachments"][0]["text_extraction"]
    assert before_extraction["content_included"] is False
    assert before_extraction["extracted_text"] is None
    assert automatic_text not in str(before_extraction)

    final_text = "직원이 확인한 합성 최종문 200ml"
    saved = review(own, attachment_id, final_text, [residents[0]["id"]])
    assert saved.status_code == 200, saved.text
    after_attachment = other.get(
        f"/api/messages/{message['id']}", headers=HEADERS
    ).json()["message"]["attachments"][0]
    assert after_attachment["can_view_reviewed_text"] is True
    assert after_attachment["can_review_text"] is False
    assert after_attachment["text_extraction"]["latest_confirmed_text"] == final_text


def test_revoked_uploader_cannot_download_or_review(context):
    from app.models import RoomMembership, utcnow

    _admin, own, _other, room_id, _residents = context
    own, _message, attachment_id, _raw, _text = upload(context)
    owner_id = _set_record_permission(own, True)
    with SessionLocal() as db:
        owner = db.get(User, owner_id)
        membership = db.scalar(
            select(RoomMembership).where(
                RoomMembership.room_id == UUID(room_id),
                RoomMembership.staff_id == owner.staff_id,
                RoomMembership.left_at.is_(None),
            )
        )
        membership.left_at = utcnow()
        # A mismatched work-item scope must not restore access to an uploader
        # removed from the room.
        owner.business_id = None
        db.commit()

    assert own.get(f"/api/attachments/{attachment_id}").status_code == 403
    denied = own.get(f"/api/attachments/{attachment_id}/staff-review-context")
    assert denied.status_code == 403


def test_record_permission_grant_and_revoke_is_rechecked_for_peer(context):
    _admin, own, other, _room_id, residents = context
    own, _message, attachment_id, _raw, _text = upload(context)

    peer_id = _set_record_permission(other, False)
    assert other.get(
        f"/api/attachments/{attachment_id}/staff-review-context"
    ).status_code == 403

    _set_record_permission(other, True)
    opened = other.get(f"/api/attachments/{attachment_id}/staff-review-context")
    assert opened.status_code == 200, opened.text
    assert review(
        other,
        attachment_id,
        "기록관리 담당자가 확인한 합성 최종문",
        [residents[0]["id"]],
        opened.json()["revision"],
    ).status_code == 200

    _set_record_permission(other, False)
    with SessionLocal() as db:
        assert db.get(User, peer_id).can_process_records is False
    denied_save = review(
        other,
        attachment_id,
        "권한 해제 뒤 저장되면 안 되는 합성문",
        [residents[0]["id"]],
    )
    assert denied_save.status_code == 403
    assert denied_save.json()["detail"] == "이 문서의 내용을 확인·수정할 권한이 없습니다."


def test_admin_can_review_peer_document_and_recalled_message_is_blocked(context):
    admin, own, _other, _room_id, residents = context
    own, message, attachment_id, _raw, _text = upload(context)

    opened = admin.get(f"/api/attachments/{attachment_id}/staff-review-context")
    assert opened.status_code == 200, opened.text
    assert review(
        admin,
        attachment_id,
        "관리자가 확인한 합성 최종문",
        [residents[0]["id"]],
        opened.json()["revision"],
    ).status_code == 200

    recalled = own.post(
        f"/api/messages/{message['id']}/recall", json={}, headers=HEADERS
    )
    assert recalled.status_code == 200, recalled.text
    assert own.get(
        f"/api/attachments/{attachment_id}/staff-review-context"
    ).status_code == 409
    assert own.get(f"/api/attachments/{attachment_id}").status_code == 409


def test_other_organization_cannot_discover_or_use_attachment(context):
    _admin, own, _other, _room_id, _residents = context
    own, _message, attachment_id, _raw, _text = upload(context)

    with SessionLocal() as db:
        attachment = db.get(MessageAttachment, UUID(attachment_id))
        other_organization = Organization(
            internal_code="SYNTHETIC-OTHER-ATTACHMENT",
            name="합성 타기관",
            service_type="facility",
        )
        db.add(other_organization)
        db.flush()
        outsider = User(
            organization_id=other_organization.id,
            username="synthetic_attachment_outsider",
            display_name="합성 외부 직원",
            password_hash="not-used",
            is_active=True,
        )
        db.add(outsider)
        db.flush()

        assert not any(
            attachment_access_capabilities(db, attachment, outsider.id).values()
        )
        with pytest.raises(HTTPException) as denied:
            main._attachment_for_text_editor(db, outsider, attachment.id)
        assert denied.value.status_code == 404
