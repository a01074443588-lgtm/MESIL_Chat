"""Print a non-PII, read-only OCR quality-registry summary.

This report never writes a registry row.  It is the first live application of
the conservative quality rules, so the institution can inspect counts and
missing evidence before approving any persisted registry or model work.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from ..database import SessionLocal
from ..models import (
    AttachmentCoordinateReview,
    AttachmentTextExtraction,
    Message,
    MessageAttachment,
    OcrCorrectionEvent,
)
from ..ocr_quality_registry import (
    OcrEvidenceSnapshot,
    OcrQualityAssessment,
    assess_ocr_evidence,
    group_evidence_bundles,
)


@dataclass(frozen=True)
class QualityRegistryRow:
    organization_id: object
    attachment_id: object
    extraction_id: object
    message_id: object
    sha256: str | None
    assessment: OcrQualityAssessment


@dataclass(frozen=True)
class MaterializedQualityRegistryRow:
    organization_id: object
    attachment_id: object
    extraction_id: object
    bundle_fingerprint: str
    bundle_size: int
    member_primary_status: str
    primary_status: str
    assessment: OcrQualityAssessment
    reasons: tuple[str, ...]


def _latest_coordinate_reviews(
    db: object,
    attachment_ids: set[object],
) -> dict[object, AttachmentCoordinateReview]:
    reviews = list(
        db.scalars(  # type: ignore[attr-defined]
            select(AttachmentCoordinateReview)
            .where(AttachmentCoordinateReview.attachment_id.in_(attachment_ids))
            .order_by(
                AttachmentCoordinateReview.attachment_id,
                AttachmentCoordinateReview.version_number.desc(),
            )
        )
    )
    latest: dict[object, AttachmentCoordinateReview] = {}
    for review in reviews:
        latest.setdefault(review.attachment_id, review)
    return latest


def _regions_overlap(regions: list[dict[str, object]]) -> bool:
    """Fail closed on malformed bounds; rotation needs human confirmation."""

    boxes: list[tuple[float, float, float, float]] = []
    for region in regions:
        bbox = region.get("bbox")
        if not isinstance(bbox, dict):
            return True
        try:
            left = float(bbox["left"])
            top = float(bbox["top"])
            right = left + float(bbox["width"])
            bottom = top + float(bbox["height"])
        except (KeyError, TypeError, ValueError):
            return True
        if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
            return True
        for other_left, other_top, other_right, other_bottom in boxes:
            if max(left, other_left) < min(right, other_right) and max(
                top, other_top
            ) < min(bottom, other_bottom):
                return True
        boxes.append((left, top, right, bottom))
    return False


def _has_complete_rotation_metadata(regions: list[dict[str, object]]) -> bool:
    if not regions:
        return False
    for region in regions:
        bbox = region.get("bbox")
        if not isinstance(bbox, dict) or "rotation_degrees" not in bbox:
            return False
        try:
            rotation = float(bbox["rotation_degrees"])
        except (TypeError, ValueError):
            return False
        if not -30 <= rotation <= 30:
            return False
    return True


def collect_quality_registry_rows() -> list[MaterializedQualityRegistryRow]:
    """Read DEV metadata and return privacy-safe image-bundle assessments."""

    with SessionLocal() as db:
        rows = list(
            db.execute(
                select(MessageAttachment, AttachmentTextExtraction, Message)
                .join(
                    AttachmentTextExtraction,
                    AttachmentTextExtraction.attachment_id == MessageAttachment.id,
                )
                .join(Message, Message.id == MessageAttachment.message_id)
                .where(MessageAttachment.mime_type.like("image/%"))
            )
        )
        attachment_ids = {attachment.id for attachment, _extraction, _message in rows}
        latest_reviews = _latest_coordinate_reviews(db, attachment_ids)
        confirmed_events = list(
            db.scalars(
                select(OcrCorrectionEvent).where(
                    OcrCorrectionEvent.confirmed.is_(True),
                    OcrCorrectionEvent.attachment_id.in_(attachment_ids),
                )
            )
        )
        confirmed_event_attachment_ids = {
            event.attachment_id for event in confirmed_events
        }
        confirmed_text_attachment_ids = {
            event.attachment_id
            for event in confirmed_events
            if event.corrected_text and event.corrected_text.strip()
        }

    result: list[QualityRegistryRow] = []
    for attachment, extraction, message in rows:
        review = latest_reviews.get(attachment.id)
        regions = list(review.regions) if review is not None else []
        all_confirmed = bool(regions) and all(
            region.get("placement_status") == "confirmed"
            and not region.get("review_required", False)
            for region in regions
        )
        template_classified = bool(review is not None and review.document_template)
        roles_classified = bool(
            regions
            and all(
                region.get("section_role")
                and region.get("role") in {"name", "status", "time", "general"}
                for region in regions
            )
        )
        assessment = assess_ocr_evidence(
            OcrEvidenceSnapshot(
                extraction_completed=extraction.status in {"completed", "reviewed"},
                original_ocr_present=bool(
                    (extraction.original_extracted_text or extraction.extracted_text or "").strip()
                ),
                integrity_hash_present=bool(attachment.sha256),
                source_message_active=message.lifecycle_status == "active",
                has_staff_confirmed_text=bool(
                    (extraction.reviewed_text or "").strip()
                    or attachment.id in confirmed_text_attachment_ids
                ),
                has_confirmed_correction_event=(
                    attachment.id in confirmed_event_attachment_ids
                ),
                coordinate_version_present=review is not None,
                all_coordinate_regions_confirmed=all_confirmed,
                coordinate_regions_non_overlapping=not _regions_overlap(regions),
                rotation_metadata_complete=_has_complete_rotation_metadata(regions),
                template_classified=template_classified,
                region_and_role_classified=roles_classified,
                # An uploader account is not evidence of the actual handwriting
                # author, so this stays fail-closed until that evidence exists.
                writer_identity_confirmed=False,
                already_visible_to_candidate_rules=(
                    attachment.id in confirmed_event_attachment_ids
                ),
            )
        )
        result.append(
            QualityRegistryRow(
                organization_id=attachment.organization_id,
                attachment_id=attachment.id,
                extraction_id=extraction.id,
                message_id=attachment.message_id,
                sha256=attachment.sha256,
                assessment=assessment,
            )
        )
    materialized: list[MaterializedQualityRegistryRow] = []
    for bundle in group_evidence_bundles(result):
        for member in bundle.members:
            materialized.append(
                MaterializedQualityRegistryRow(
                    organization_id=member.organization_id,
                    attachment_id=member.attachment_id,
                    extraction_id=member.extraction_id,
                    bundle_fingerprint=bundle.fingerprint,
                    bundle_size=len(bundle.members),
                    member_primary_status=member.assessment.primary_status,
                    primary_status=bundle.primary_status,
                    assessment=member.assessment,
                    reasons=tuple(
                        dict.fromkeys((*member.assessment.reasons, *bundle.reasons))
                    ),
                )
            )
    return materialized


def main() -> None:
    rows = collect_quality_registry_rows()
    member_status_counts: Counter[str] = Counter()
    bundle_status_by_fingerprint: dict[str, str] = {}
    reason_counts: Counter[str] = Counter()
    for row in rows:
        member_status_counts[row.member_primary_status] += 1
        bundle_status_by_fingerprint[row.bundle_fingerprint] = row.primary_status
        reason_counts.update(row.reasons)
    bundle_status_counts = Counter(bundle_status_by_fingerprint.values())

    print("mode=read-only quality_registry_version=2")
    print(f"image_extractions={len(rows)}")
    print(f"source_bundles={len(bundle_status_by_fingerprint)}")
    for key in ("rules_usable", "visual_learning_candidate", "evaluation_candidate", "hold", "excluded"):
        print(f"bundle_status={key} count={bundle_status_counts[key]}")
        print(f"image_status={key} count={member_status_counts[key]}")
    for reason, count in sorted(reason_counts.items()):
        print(f"reason={reason} count={count}")


if __name__ == "__main__":
    main()
