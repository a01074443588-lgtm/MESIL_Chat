"""Backfill confirmed image/audio review diffs into correction learning.

The default mode is read-only. Use ``--apply`` only after a database backup.
No transcript, resident name, or correction text is printed.
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select

from ..database import SessionLocal
from ..models import (
    AttachmentTextExtraction,
    MessageAttachment,
    OcrCorrectionEvent,
    OcrCorrectionMemory,
    Resident,
)
from ..ocr_corrections import build_correction_pairs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="직원이 확인한 이미지·음성 수정 이력을 교정학습에 반영합니다."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="실제로 교정 이벤트와 교정 메모리를 저장합니다.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    counts: Counter[str] = Counter()
    with SessionLocal() as db:
        extractions = list(
            db.scalars(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.status == "reviewed",
                    AttachmentTextExtraction.reviewed_text.is_not(None),
                    AttachmentTextExtraction.reviewed_by_id.is_not(None),
                )
            )
        )
        resident_cache: dict[object, list[str]] = {}
        for extraction in extractions:
            attachment = db.get(MessageAttachment, extraction.attachment_id)
            if attachment is None or not attachment.mime_type.startswith(
                ("image/", "audio/")
            ):
                counts["unsupported"] += 1
                continue
            if db.scalar(
                select(OcrCorrectionEvent.id).where(
                    OcrCorrectionEvent.extraction_id == extraction.id
                )
            ) is not None:
                counts["already_recorded"] += 1
                continue
            raw_text = (
                extraction.original_extracted_text
                or extraction.extracted_text
                or ""
            ).strip()
            reviewed_text = (extraction.reviewed_text or "").strip()
            if not raw_text or not reviewed_text or raw_text == reviewed_text:
                counts["unchanged"] += 1
                continue
            organization_id = attachment.message.organization_id
            if organization_id not in resident_cache:
                resident_cache[organization_id] = list(
                    db.scalars(
                        select(Resident.display_name).where(
                            Resident.organization_id == organization_id,
                            Resident.is_active.is_(True),
                        )
                    )
                )
            pairs = build_correction_pairs(
                raw_text,
                reviewed_text,
                resident_names=resident_cache[organization_id],
            )
            if not pairs:
                counts["no_safe_pairs"] += 1
                continue
            counts["eligible_extractions"] += 1
            counts["eligible_pairs"] += len(pairs)
            counts[
                "audio_extractions"
                if attachment.mime_type.startswith("audio/")
                else "image_extractions"
            ] += 1
            if not args.apply:
                continue
            content_types = {
                str(pair.get("content_type", "general")) for pair in pairs
            }
            event_content_type = (
                next(iter(content_types))
                if len(content_types) == 1
                else "mixed"
            )
            db.add(
                OcrCorrectionEvent(
                    organization_id=organization_id,
                    extraction_id=extraction.id,
                    attachment_id=attachment.id,
                    source_message_id=attachment.message_id,
                    source_writer_id=attachment.message.sender_id,
                    reviewed_by_id=extraction.reviewed_by_id,
                    decision="direct_edit",
                    raw_text=raw_text,
                    corrected_text=reviewed_text,
                    correction_pairs=pairs,
                    content_type=event_content_type,
                    context_text=raw_text[:4000],
                    provider=extraction.provider,
                    model_name=extraction.model_name,
                    visual_signature=(
                        extraction.visual_signature
                        if attachment.mime_type.startswith("image/")
                        else None
                    ),
                    confirmed=True,
                )
            )
            for pair in pairs:
                recognized = str(pair["recognized_text"])
                corrected = str(pair["corrected_text"])
                memory = db.scalar(
                    select(OcrCorrectionMemory).where(
                        OcrCorrectionMemory.organization_id == organization_id,
                        OcrCorrectionMemory.recognized_text == recognized,
                        OcrCorrectionMemory.corrected_text == corrected,
                    )
                )
                if memory is None:
                    db.add(
                        OcrCorrectionMemory(
                            organization_id=organization_id,
                            recognized_text=recognized,
                            corrected_text=corrected,
                            last_reviewed_by_id=extraction.reviewed_by_id,
                        )
                    )
                else:
                    memory.occurrence_count += 1
                    memory.last_reviewed_by_id = extraction.reviewed_by_id
            counts["applied_extractions"] += 1
        if args.apply:
            db.commit()
        else:
            db.rollback()
    mode = "apply" if args.apply else "dry-run"
    print(
        f"mode={mode} "
        + " ".join(f"{key}={counts[key]}" for key in sorted(counts))
    )


if __name__ == "__main__":
    main()
