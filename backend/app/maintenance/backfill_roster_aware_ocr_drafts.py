"""Build safe roster-aware drafts for existing unreviewed image OCR results.

Dry-run is the default.  Output contains aggregate counts only; resident names,
transcripts and correction pairs are never printed.
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..database import SessionLocal
from ..main import _build_attachment_roster_aware_draft
from ..models import AttachmentTextExtraction, Message, MessageAttachment


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="기존 미확정 이미지 판독문에 명단 기반 수정 초안을 만듭니다."
    )
    parser.add_argument("--apply", action="store_true", help="수정 초안을 저장합니다.")
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="이미 생성된 초안도 다시 계산합니다.",
    )
    parser.add_argument("--limit", type=int, default=500, help="최대 처리 건수")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    limit = max(1, min(args.limit, 5000))
    counts: Counter[str] = Counter()
    with SessionLocal() as db:
        statement = (
            select(AttachmentTextExtraction)
            .where(
                AttachmentTextExtraction.status == "completed",
                AttachmentTextExtraction.reviewed_text.is_(None),
                AttachmentTextExtraction.extracted_text.is_not(None),
            )
            .order_by(AttachmentTextExtraction.created_at.desc())
            .limit(limit)
        )
        for extraction in db.scalars(statement):
            attachment = db.scalar(
                select(MessageAttachment)
                .where(MessageAttachment.id == extraction.attachment_id)
                .options(
                    selectinload(MessageAttachment.message).selectinload(Message.room),
                    selectinload(MessageAttachment.message).selectinload(
                        Message.resident
                    ),
                )
            )
            if attachment is None or not attachment.mime_type.startswith("image/"):
                counts["unsupported"] += 1
                continue
            if extraction.suggested_text and not args.refresh:
                counts["already_suggested"] += 1
                continue
            draft = _build_attachment_roster_aware_draft(
                db,
                attachment=attachment,
                raw_text=extraction.extracted_text or "",
                visual_signature=extraction.visual_signature,
            )
            counts["examined"] += 1
            if draft.service_context:
                counts[f"scope_{draft.service_context}"] += 1
            if not draft.corrections or draft.text == extraction.extracted_text:
                counts["unchanged"] += 1
                if args.apply and args.refresh:
                    extraction.suggested_text = None
                    extraction.suggestion_details = None
                    extraction.suggested_service_context = draft.service_context
                continue
            counts["suggested_extractions"] += 1
            counts["suggested_name_corrections"] += len(draft.corrections)
            if args.apply:
                extraction.suggested_text = draft.text[:12000]
                extraction.suggested_service_context = draft.service_context
                extraction.suggestion_details = draft.corrections
                counts["applied"] += 1
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
