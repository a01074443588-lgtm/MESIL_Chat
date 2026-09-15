from __future__ import annotations

import argparse
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from .database import SessionLocal
from .main import (
    _AUTO_COORDINATE_DETAIL_KIND,
    IMAGE_MIME_TYPES,
    _run_attachment_coordinate_auto_location,
    _store_coordinate_auto_location_detail,
)
from .models import (
    AttachmentCoordinateReview,
    AttachmentTextExtraction,
    MessageAttachment,
)


@dataclass
class CoordinateBackfillResult:
    eligible: int = 0
    selected: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0


def _has_auto_location_detail(extraction: AttachmentTextExtraction) -> bool:
    return any(
        item.get("kind") == _AUTO_COORDINATE_DETAIL_KIND
        for item in (extraction.suggestion_details or [])
    )


def _eligible_attachment_ids() -> list[UUID]:
    with SessionLocal() as db:
        manual_review_ids = set(
            db.scalars(select(AttachmentCoordinateReview.attachment_id)).all()
        )
        rows = db.execute(
            select(MessageAttachment.id, AttachmentTextExtraction)
            .join(
                AttachmentTextExtraction,
                AttachmentTextExtraction.attachment_id == MessageAttachment.id,
            )
            .where(
                MessageAttachment.mime_type.in_(IMAGE_MIME_TYPES),
                AttachmentTextExtraction.status.in_({"completed", "reviewed"}),
            )
            .order_by(MessageAttachment.created_at, MessageAttachment.id)
        ).all()
        return [
            attachment_id
            for attachment_id, extraction in rows
            if attachment_id not in manual_review_ids
            and not _has_auto_location_detail(extraction)
        ]


def run_backfill(
    *, limit: int | None = None, dry_run: bool = False
) -> CoordinateBackfillResult:
    attachment_ids = _eligible_attachment_ids()
    selected_ids = attachment_ids[:limit] if limit is not None else attachment_ids
    result = CoordinateBackfillResult(
        eligible=len(attachment_ids),
        selected=len(selected_ids),
    )
    if dry_run:
        return result

    for attachment_id in selected_ids:
        try:
            _run_attachment_coordinate_auto_location(attachment_id)
        except Exception:
            with SessionLocal() as db:
                extraction = db.scalar(
                    select(AttachmentTextExtraction).where(
                        AttachmentTextExtraction.attachment_id == attachment_id
                    )
                )
                if extraction is not None:
                    _store_coordinate_auto_location_detail(extraction, status="failed")
                    db.commit()
            result.failed += 1
            continue

        with SessionLocal() as db:
            extraction = db.scalar(
                select(AttachmentTextExtraction).where(
                    AttachmentTextExtraction.attachment_id == attachment_id
                )
            )
            detail = next(
                (
                    item
                    for item in (extraction.suggestion_details or [])
                    if item.get("kind") == _AUTO_COORDINATE_DETAIL_KIND
                ),
                None,
            )
        if detail is None:
            result.skipped += 1
        elif detail.get("status") == "completed":
            result.completed += 1
        else:
            result.failed += 1
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prepare non-confirming coordinate drafts for existing OCR images."
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    result = run_backfill(limit=args.limit, dry_run=args.dry_run)
    print(f"coordinate_backfill_eligible={result.eligible}")
    print(f"coordinate_backfill_selected={result.selected}")
    if not args.dry_run:
        print(f"coordinate_backfill_completed={result.completed}")
        print(f"coordinate_backfill_failed={result.failed}")
        print(f"coordinate_backfill_skipped={result.skipped}")
    return 1 if result.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
