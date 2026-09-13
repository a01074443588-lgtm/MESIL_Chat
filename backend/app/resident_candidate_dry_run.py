"""Read-only, content-free inventory for historical resident-link candidates.

The command intentionally returns only aggregate counts, elapsed time, and a
fingerprint made from row identifiers and states.  It may inspect final text in
process memory to classify exact-name matches, but it never serializes names or
message/attachment text.
"""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import re
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import select, text as sql_text
from sqlalchemy.orm import Session, selectinload

from . import main
from .database import SessionLocal
from .models import (
    AttachmentTextExtraction,
    AuditEvent,
    HandwritingCorrectionApproval,
    Message,
    MessageAttachment,
    MessageResidentLink,
    OcrCorrectionEvent,
    Resident,
    WorkItem,
)
from .photo_reading import resident_review_metadata
from .resident_candidate_evidence import candidate_evidence


_UUID_PATTERN = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _latest_staff_texts(
    db: Session,
    attachments: list[MessageAttachment],
) -> dict[UUID, str]:
    image_ids = [
        attachment.id
        for attachment in attachments
        if attachment.mime_type.startswith("image/")
    ]
    if not image_ids:
        return {}

    candidates: dict[UUID, tuple[Any, str]] = {}
    correction_events = db.scalars(
        select(OcrCorrectionEvent)
        .where(
            OcrCorrectionEvent.attachment_id.in_(image_ids),
            OcrCorrectionEvent.confirmed.is_(True),
        )
        .order_by(OcrCorrectionEvent.created_at.desc(), OcrCorrectionEvent.id.desc())
    ).all()
    for event in correction_events:
        if event.attachment_id is None or not (event.corrected_text or "").strip():
            continue
        candidates.setdefault(
            event.attachment_id,
            (event.created_at, event.corrected_text.strip()),
        )

    approvals = db.scalars(
        select(HandwritingCorrectionApproval)
        .where(HandwritingCorrectionApproval.image_attachment_id.in_(image_ids))
        .order_by(
            HandwritingCorrectionApproval.created_at.desc(),
            HandwritingCorrectionApproval.id.desc(),
        )
    ).all()
    for approval in approvals:
        value = (approval.approved_final_text or "").strip()
        if not value:
            continue
        current = candidates.get(approval.image_attachment_id)
        if current is None or approval.created_at > current[0]:
            candidates[approval.image_attachment_id] = (approval.created_at, value)

    for attachment in attachments:
        extraction = attachment.text_extraction
        value = (
            (extraction.reviewed_text or "").strip()
            if extraction is not None and extraction.status == "reviewed"
            else ""
        )
        if not value:
            continue
        timestamp = extraction.reviewed_at or extraction.updated_at
        current = candidates.get(attachment.id)
        if current is None or (timestamp is not None and timestamp > current[0]):
            candidates[attachment.id] = (timestamp, value)
    return {attachment_id: value for attachment_id, (_at, value) in candidates.items()}


def _alias_index(db: Session, message: Message, cache: dict[tuple[UUID, str | None], dict[str, list[Resident]]]):
    scope = main._message_resident_service_context(message)
    key = (message.organization_id, scope)
    cached = cache.get(key)
    if cached is not None:
        return cached
    statement = select(Resident).where(
        Resident.organization_id == message.organization_id,
        Resident.is_active.is_(True),
        Resident.status == "active",
        Resident.service_type.in_(main.VALID_RESIDENT_SERVICE_CONTEXTS),
    )
    if scope is not None:
        statement = statement.where(Resident.service_type == scope)
    aliases: dict[str, list[Resident]] = {}
    for resident in db.scalars(statement):
        for alias in main._resident_name_aliases(resident):
            aliases.setdefault(alias, []).append(resident)
    cache[key] = aliases
    return aliases


def collect_candidate_inventory(db: Session) -> dict[str, Any]:
    started = perf_counter()
    bind = db.get_bind()
    if bind.dialect.name == "postgresql":
        db.execute(sql_text("SET TRANSACTION READ ONLY"))

    messages = list(
        db.scalars(
            select(Message)
            .where(
                Message.lifecycle_status == "active",
                Message.resident_links.any(MessageResidentLink.status == "candidate"),
            )
            .options(
                selectinload(Message.room),
                selectinload(Message.resident),
                selectinload(Message.resident_links).selectinload(
                    MessageResidentLink.resident
                ),
                selectinload(Message.attachments)
                .selectinload(MessageAttachment.text_extraction)
                .selectinload(AttachmentTextExtraction.attempts),
            )
            .order_by(Message.created_at, Message.id)
        ).unique()
    )
    message_ids = [message.id for message in messages]
    work_items = {
        item.source_message_id: item
        for item in (
            db.scalars(
                select(WorkItem).where(WorkItem.source_message_id.in_(message_ids))
            ).all()
            if message_ids
            else []
        )
    }

    counts: Counter[str] = Counter()
    fingerprint_rows: list[str] = []
    alias_cache: dict[tuple[UUID, str | None], dict[str, list[Resident]]] = {}
    organizations: set[UUID] = set()

    for message in messages:
        organizations.add(message.organization_id)
        review = resident_review_metadata(message)
        stored_hashes = review.get("staff_final_text_hashes") or {}
        stored_hashes = stored_hashes if isinstance(stored_hashes, dict) else {}
        staff_texts = _latest_staff_texts(db, list(message.attachments))
        aliases = _alias_index(db, message, alias_cache)
        unique_ids: set[UUID] = set()
        ambiguous_ids: set[UUID] = set()

        for attachment_id, final_text in staff_texts.items():
            counts["staff_final_text_attachment_count"] += 1
            digest = sha256(final_text.encode("utf-8")).hexdigest()
            stored = stored_hashes.get(str(attachment_id))
            if stored == digest:
                counts["staff_final_text_hash_match_count"] += 1
            elif stored:
                counts["staff_final_text_hash_mismatch_count"] += 1
            else:
                counts["staff_final_text_hash_missing_count"] += 1
            for alias, owners in aliases.items():
                if not main._text_mentions_alias(final_text, alias):
                    continue
                if len(owners) == 1:
                    unique_ids.add(owners[0].id)
                else:
                    ambiguous_ids.update(owner.id for owner in owners)

        if staff_texts:
            counts["message_with_staff_final_text_count"] += 1
        if unique_ids:
            counts["message_with_unique_exact_match_count"] += 1
            counts["unique_exact_resident_match_count"] += len(unique_ids)
        if ambiguous_ids:
            counts["message_with_ambiguous_name_count"] += 1
            counts["ambiguous_resident_match_count"] += len(ambiguous_ids)

        manual_exclusions = {
            UUID(value)
            for value in review.get("manual_excluded_resident_ids", [])
            if isinstance(value, str) and _UUID_PATTERN.fullmatch(value)
        }
        if manual_exclusions:
            counts["message_with_manual_exclusion_count"] += 1
            counts["manual_excluded_resident_count"] += len(manual_exclusions)
        if review.get("unrelated") or review.get("decision") == "unrelated":
            counts["message_marked_unrelated_count"] += 1

        has_staff_final_text = bool(staff_texts)
        for link in message.resident_links:
            if link.status != "candidate":
                continue
            counts["candidate_link_count"] += 1
            current_evidence = candidate_evidence(link, message)
            if link.source == "ocr_exact" and current_evidence and not has_staff_final_text:
                counts["raw_ocr_only_candidate_count"] += 1
            if link.source == "audio_transcript" and not has_staff_final_text:
                counts["transcript_only_candidate_count"] += 1

            stale_binding = False
            if link.source == "ocr_exact":
                for attachment in message.attachments:
                    extraction = attachment.text_extraction
                    if extraction is None:
                        continue
                    binding = next(
                        (
                            row
                            for row in extraction.suggestion_details or []
                            if row.get("kind") == "resident_candidate_evidence"
                            and row.get("source") == "ocr_exact"
                        ),
                        None,
                    )
                    if not binding:
                        continue
                    latest_attempt = max(
                        (attempt.attempt_number for attempt in extraction.attempts),
                        default=0,
                    )
                    bound_attempt = int(binding.get("attempt_number") or 0)
                    if latest_attempt and bound_attempt and bound_attempt < latest_attempt:
                        stale_binding = True
                        break
            stale = not current_evidence or stale_binding
            if link.source == "ocr_exact" and stale:
                counts["stale_ocr_candidate_count"] += 1
            item = work_items.get(message.id)
            eligible_cleanup = (
                link.source in {"ocr_exact", "audio_transcript", "text_exact"}
                and stale
                and link.reviewed_by_id is None
                and link.resident_id not in manual_exclusions
                and (item is None or item.confirmed_at is None)
            )
            if eligible_cleanup:
                counts["strict_auto_cleanup_candidate_count"] += 1
            fingerprint_rows.append(
                f"{message.id}:{link.id}:{link.source}:{link.status}:"
                f"{int(bool(current_evidence))}:{int(stale_binding)}"
            )

        counts["manual_confirmed_link_count"] += sum(
            1
            for link in message.resident_links
            if link.status == "confirmed" and link.source == "manual"
        )

    audited_actions = {
        "message_resident_link.reviewed",
        "message_resident_links.set",
        "message_resident_links.finalized_from_text_review",
    }
    for action, details in db.execute(
        select(AuditEvent.action, AuditEvent.details).where(
            AuditEvent.action.in_(audited_actions)
        )
    ):
        payload = details if isinstance(details, dict) else {}
        if action == "message_resident_links.set":
            counts["manual_set_audit_count"] += 1
        elif action == "message_resident_links.finalized_from_text_review":
            counts["final_text_audit_count"] += 1
        elif action == "message_resident_link.reviewed":
            decision = str(payload.get("decision", ""))
            if decision == "confirm":
                counts["manual_confirm_audit_count"] += 1
            elif decision == "change":
                counts["manual_change_audit_count"] += 1
            elif decision in {"unrelated", "reject_one"}:
                counts["manual_exclude_audit_count"] += 1

    count_keys = (
        "staff_final_text_attachment_count",
        "staff_final_text_hash_match_count",
        "staff_final_text_hash_missing_count",
        "staff_final_text_hash_mismatch_count",
        "message_with_staff_final_text_count",
        "message_with_unique_exact_match_count",
        "unique_exact_resident_match_count",
        "message_with_ambiguous_name_count",
        "ambiguous_resident_match_count",
        "raw_ocr_only_candidate_count",
        "transcript_only_candidate_count",
        "stale_ocr_candidate_count",
        "strict_auto_cleanup_candidate_count",
        "manual_confirmed_link_count",
        "message_with_manual_exclusion_count",
        "manual_excluded_resident_count",
        "message_marked_unrelated_count",
        "manual_set_audit_count",
        "manual_confirm_audit_count",
        "manual_change_audit_count",
        "manual_exclude_audit_count",
        "final_text_audit_count",
        "candidate_link_count",
    )
    safe_counts = {key: int(counts[key]) for key in count_keys}
    return {
        "ok": True,
        "mode": "read_only_dry_run",
        "schema_version": 1,
        "read_only": True,
        "writes_performed": 0,
        "organizations_scanned": len(organizations),
        "candidate_messages_scanned": len(messages),
        "counts": safe_counts,
        "state_fingerprint_sha256": sha256(
            "\n".join(sorted(fingerprint_rows)).encode("utf-8")
        ).hexdigest(),
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        "contains_names_or_content": False,
    }


def main_cli() -> int:
    try:
        with SessionLocal() as db:
            result = collect_candidate_inventory(db)
            db.rollback()
    except Exception as exc:  # pragma: no cover - operational safety envelope
        print(json.dumps({"ok": False, "error_type": type(exc).__name__}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
