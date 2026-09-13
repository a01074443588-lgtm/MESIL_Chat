"""Report correction-learning coverage without printing names or report text."""

from __future__ import annotations

from collections import Counter, defaultdict

from sqlalchemy import select

from ..database import SessionLocal
from ..models import Message, MessageAttachment, OcrCorrectionEvent, Resident


VALID_SERVICES = frozenset({"facility", "daycare", "homecare"})


def message_service_context(message: Message | None) -> str:
    if message is None:
        return "all"
    if (
        message.resident is not None
        and message.resident.service_type in VALID_SERVICES
    ):
        return message.resident.service_type
    scope = message.room.resident_scope if message.room is not None else None
    if scope in VALID_SERVICES:
        return scope
    return "facility" if scope == "floor" else "all"


def attachment_source_kind(attachment: MessageAttachment | None) -> str:
    if attachment is None:
        return "unknown"
    if attachment.mime_type.startswith("audio/"):
        return "audio"
    if attachment.mime_type.startswith("image/"):
        return "image"
    return "other"


def main() -> None:
    with SessionLocal() as db:
        events = list(
            db.scalars(
                select(OcrCorrectionEvent)
                .where(OcrCorrectionEvent.confirmed.is_(True))
                .order_by(OcrCorrectionEvent.created_at, OcrCorrectionEvent.id)
            )
        )
        message_ids = {
            event.source_message_id
            for event in events
            if event.source_message_id is not None
        }
        attachment_ids = {
            event.attachment_id
            for event in events
            if event.attachment_id is not None
        }
        messages = {
            message.id: message
            for message in db.scalars(
                select(Message).where(Message.id.in_(message_ids))
            )
        }
        attachments = {
            attachment.id: attachment
            for attachment in db.scalars(
                select(MessageAttachment).where(
                    MessageAttachment.id.in_(attachment_ids)
                )
            )
        }
        resident_services: dict[tuple[object, str], set[str]] = defaultdict(set)
        for organization_id, name, service_type in db.execute(
            select(
                Resident.organization_id,
                Resident.display_name,
                Resident.service_type,
            ).where(
                Resident.service_type.in_(VALID_SERVICES)
            )
        ):
            resident_services[
                (organization_id, name.replace(" ", ""))
            ].add(service_type)

        group_counts: Counter[tuple[str, str, str]] = Counter()
        pair_occurrences: Counter[tuple[str, str, str, str]] = Counter()
        for event in events:
            message = messages.get(event.source_message_id)
            source_kind = attachment_source_kind(
                attachments.get(event.attachment_id)
            )
            event_service = message_service_context(message)
            group_counts[(event_service, source_kind, "events")] += 1
            for pair in event.correction_pairs or []:
                recognized = str(pair.get("recognized_text", "")).strip()
                corrected = str(pair.get("corrected_text", "")).strip()
                if not recognized or not corrected or recognized == corrected:
                    continue
                pair_service = event_service
                if pair_service == "all":
                    matched_services = resident_services.get(
                        (event.organization_id, corrected.replace(" ", "")),
                        set(),
                    )
                    if len(matched_services) == 1:
                        pair_service = next(iter(matched_services))
                content_type = str(pair.get("content_type", "general"))
                group_counts[(pair_service, source_kind, "pairs")] += 1
                if content_type == "resident_name":
                    group_counts[
                        (pair_service, source_kind, "resident_name_pairs")
                    ] += 1
                pair_occurrences[
                    (pair_service, source_kind, recognized, corrected)
                ] += 1

        repeated_by_group: Counter[tuple[str, str]] = Counter()
        for (
            service,
            source_kind,
            _recognized,
            _corrected,
        ), count in pair_occurrences.items():
            if count >= 2:
                repeated_by_group[(service, source_kind)] += 1

    groups = sorted(
        {(service, source_kind) for service, source_kind, _ in group_counts},
        key=lambda item: (item[0], item[1]),
    )
    print(
        "mode=read-only "
        f"confirmed_events={len(events)} "
        f"unique_pairs={len(pair_occurrences)} "
        f"repeated_pairs={sum(repeated_by_group.values())}"
    )
    for service, source_kind in groups:
        print(
            f"service={service} source={source_kind} "
            f"events={group_counts[(service, source_kind, 'events')]} "
            f"pairs={group_counts[(service, source_kind, 'pairs')]} "
            "resident_name_pairs="
            f"{group_counts[(service, source_kind, 'resident_name_pairs')]} "
            f"repeated_pairs={repeated_by_group[(service, source_kind)]}"
        )


if __name__ == "__main__":
    main()
