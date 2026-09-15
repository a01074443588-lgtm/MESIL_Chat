"""Single read-time boundary between automatic text and staff-reviewed evidence.

Raw text remains accessible in the permission-checked attachment editor only.
Legacy completed rows require an explicit reviewer, timestamp and final text;
this compatibility rule never changes historical database rows.
"""


ATTACHMENT_STAFF_REVIEW_REQUIRED_DETAIL = (
    "첨부된 문서 또는 음성 내용을 먼저 확인해 주세요. 원본과 비교하여 내용을 "
    "수정한 뒤 ‘확인 완료’를 눌러야 업무기록을 만들 수 있습니다."
)


def has_staff_review(extraction) -> bool:
    return bool(extraction is not None
        and getattr(extraction, "status", None) in {"reviewed", "completed"}
        and getattr(extraction, "reviewed_by_id", None)
        and getattr(extraction, "reviewed_at", None)
        and (getattr(extraction, "reviewed_text", None) or "").strip())


def requires_staff_review(attachment) -> bool:
    mime = getattr(attachment, "mime_type", "") or ""
    return mime.startswith("audio/") or (not mime.startswith(("image/", "video/")))


def has_current_staff_review(db, attachment) -> bool:
    """Return whether the live extraction matches its latest immutable review."""

    if not requires_staff_review(attachment):
        return True
    extraction = getattr(attachment, "text_extraction", None)
    reviewed_text = (getattr(extraction, "reviewed_text", None) or "").strip()
    attachment_sha256 = getattr(attachment, "sha256", None)
    if not (
        extraction is not None
        and getattr(extraction, "status", None) == "reviewed"
        and getattr(extraction, "reviewed_by_id", None)
        and getattr(extraction, "reviewed_at", None)
        and reviewed_text
        and attachment_sha256
    ):
        return False

    from hashlib import sha256
    from sqlalchemy import select
    from .models import AttachmentTextRevision

    latest = db.scalar(
        select(AttachmentTextRevision)
        .where(
            AttachmentTextRevision.attachment_id == attachment.id,
            AttachmentTextRevision.extraction_id == extraction.id,
        )
        .order_by(AttachmentTextRevision.revision.desc())
        .limit(1)
    )
    return bool(
        latest is not None
        and latest.kind == "staff_review"
        and latest.reviewed_by_id == extraction.reviewed_by_id
        and latest.original_file_sha256 == attachment_sha256
        and latest.text_content == reviewed_text
        and latest.text_sha256 == sha256(reviewed_text.encode()).hexdigest()
    )


def required_attachment_reviews_current(db, attachments) -> bool:
    return all(
        not requires_staff_review(attachment)
        or has_current_staff_review(db, attachment)
        for attachment in attachments
    )


def attachment_evidence_text(attachment, extraction=None) -> str:
    extraction = extraction if extraction is not None else getattr(attachment, "text_extraction", None)
    if requires_staff_review(attachment):
        return (extraction.reviewed_text or "").strip() if has_staff_review(extraction) else ""
    from .resident_candidate_evidence import visible_image_text
    latest = getattr(extraction, "latest_confirmed_text", None)
    if latest and getattr(extraction, "status", None) in {"completed", "reviewed"}:
        from .ocr import is_pathological_handwriting_output
        return latest.strip() if not is_pathological_handwriting_output(latest) else ""
    return visible_image_text(extraction)


def review_fingerprint(attachments) -> str:
    from hashlib import sha256
    import json
    rows = []
    for attachment in attachments:
        if not requires_staff_review(attachment):
            continue
        extraction = getattr(attachment, "text_extraction", None)
        rows.append((str(attachment.id), getattr(extraction, "status", None),
            str(getattr(extraction, "reviewed_at", None)),
            sha256(attachment_evidence_text(attachment).encode()).hexdigest()))
    return sha256(json.dumps(sorted(rows)).encode()).hexdigest()


def message_review_fingerprint(db, message_ids):
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload
    from .models import MessageAttachment
    attachments = list(db.scalars(select(MessageAttachment).where(MessageAttachment.message_id.in_(message_ids))
        .options(selectinload(MessageAttachment.text_extraction)).execution_options(populate_existing=True)))
    return review_fingerprint(attachments)


def work_item_review_current(item):
    attachments = item.source_message.attachments
    if not any(requires_staff_review(row) for row in attachments):
        return True
    return (item.source_snapshot or {}).get("attachment_review_fingerprint") == review_fingerprint(attachments)
