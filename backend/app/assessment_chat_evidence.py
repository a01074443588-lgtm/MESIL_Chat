from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
import re
from uuid import UUID
from .attachment_review_policy import attachment_evidence_text

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    HandwritingCorrectionApproval,
    Message,
    MessageAttachment,
    MessageResidentLink,
)


MAX_CHAT_EVIDENCE_ITEMS = 100
CHAT_QUERY_CHUNK_DAYS = 31
MESSAGE_QUERY_LIMIT_PER_CHUNK = 500


def _utc_range(day_start: date, day_end: date) -> tuple[datetime, datetime]:
    return (
        datetime.combine(day_start, time.min).replace(tzinfo=timezone.utc),
        datetime.combine(day_end + timedelta(days=1), time.min).replace(
            tzinfo=timezone.utc
        ),
    )


def _date_chunks(start_day: date, end_day: date):
    cursor = start_day
    while cursor <= end_day:
        chunk_end = min(
            cursor + timedelta(days=CHAT_QUERY_CHUNK_DAYS - 1),
            end_day,
        )
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _compact_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _latest_approvals_by_attachment(
    db: Session,
    *,
    organization_id: UUID,
    attachment_ids: list[UUID],
) -> dict[UUID, HandwritingCorrectionApproval]:
    if not attachment_ids:
        return {}
    approvals = db.scalars(
        select(HandwritingCorrectionApproval)
        .where(
            HandwritingCorrectionApproval.organization_id == organization_id,
            HandwritingCorrectionApproval.image_attachment_id.in_(attachment_ids),
            HandwritingCorrectionApproval.status == "approved",
            HandwritingCorrectionApproval.external_transfer_allowed.is_(False),
        )
        .order_by(
            HandwritingCorrectionApproval.image_attachment_id,
            HandwritingCorrectionApproval.revision.desc(),
            HandwritingCorrectionApproval.created_at.desc(),
        )
    ).all()
    latest: dict[UUID, HandwritingCorrectionApproval] = {}
    for approval in approvals:
        latest.setdefault(approval.image_attachment_id, approval)
    return latest


def collect_resident_assessment_chat_evidence(
    db: Session,
    *,
    organization_id: UUID,
    resident_id: UUID,
    start_day: date,
    end_day: date,
    excluded_source_refs: set[str] | None = None,
) -> list[dict[str, str]]:
    """Collect resident-isolated, staff-confirmed MesilChat evidence.

    The result is intentionally compact. Full message/comment/attachment records
    remain in their source tables; assessment drafts only receive a short source
    summary and stable human-readable locator. Exact duplicate events are merged.
    """

    excluded_source_refs = excluded_source_refs or set()
    confirmed_message_ids = select(MessageResidentLink.message_id).where(
        MessageResidentLink.organization_id == organization_id,
        MessageResidentLink.resident_id == resident_id,
        MessageResidentLink.status == "confirmed",
    )
    messages_by_id: dict[UUID, Message] = {}
    for chunk_start, chunk_end in _date_chunks(start_day, end_day):
        range_start, range_end = _utc_range(chunk_start, chunk_end)
        chunk_messages = db.scalars(
            select(Message)
            .options(
                selectinload(Message.comments),
                selectinload(Message.attachments).selectinload(
                    MessageAttachment.text_extraction
                ),
            )
            .where(
                Message.organization_id == organization_id,
                Message.lifecycle_status == "active",
                Message.is_test_data.is_(True),
                Message.message_type != "notice",
                Message.created_at >= range_start,
                Message.created_at < range_end,
                or_(
                    Message.resident_id == resident_id,
                    Message.id.in_(confirmed_message_ids),
                ),
            )
            .order_by(Message.created_at, Message.id)
            .limit(MESSAGE_QUERY_LIMIT_PER_CHUNK)
        ).all()
        for message in chunk_messages:
            messages_by_id.setdefault(message.id, message)

    messages = sorted(messages_by_id.values(), key=lambda item: (item.created_at, item.id))
    attachment_ids = [
        attachment.id for message in messages for attachment in message.attachments
    ]
    approvals = _latest_approvals_by_attachment(
        db,
        organization_id=organization_id,
        attachment_ids=attachment_ids,
    )

    evidence: list[dict[str, str]] = []
    seen_events: set[str] = set()
    for message in messages:
        parts = [message.body]
        parts.extend(comment.body for comment in message.comments)
        for attachment in message.attachments:
            approval = approvals.get(attachment.id)
            if approval is not None:
                parts.append(approval.approved_final_text)
                continue
            extraction = attachment.text_extraction
            reviewed_text = attachment_evidence_text(attachment)
            if extraction is not None and extraction.status == "reviewed" and reviewed_text:
                parts.append(reviewed_text)

        cleaned_parts = [_compact_text(part) for part in parts if _compact_text(part)]
        canonical = "\n".join(cleaned_parts).casefold()
        if not canonical or canonical in seen_events:
            continue
        seen_events.add(canonical)
        source_ref = f"chat-{sha256(canonical.encode('utf-8')).hexdigest()[:32]}"
        if source_ref in excluded_source_refs:
            continue
        local_created = message.created_at.astimezone(timezone.utc)
        evidence.append(
            {
                "source_ref": source_ref,
                "text": "\n".join(cleaned_parts),
                "summary": " · ".join(cleaned_parts)[:1000],
                "reference_locator": (
                    "매실챗 확인 기록 "
                    f"{local_created.date().isoformat()} "
                    f"{local_created.strftime('%H:%M')}"
                ),
            }
        )
        if len(evidence) >= MAX_CHAT_EVIDENCE_ITEMS:
            break
    return evidence
