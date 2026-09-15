"""Create one append-only DEV OCR quality-registry snapshot."""

from __future__ import annotations

from collections import Counter
from uuid import uuid4

from ..database import SessionLocal
from ..models import OcrQualityRegistryEntry
from ..ocr_quality_registry import QUALITY_POLICY_VERSION
from .report_ocr_quality_registry import collect_quality_registry_rows


def main() -> None:
    rows = collect_quality_registry_rows()
    run_id = uuid4()
    counts: Counter[str] = Counter()
    with SessionLocal() as db:
        for row in rows:
            assessment = row.assessment
            primary_status = row.primary_status
            db.add(
                OcrQualityRegistryEntry(
                    assessment_run_id=run_id,
                    organization_id=row.organization_id,
                    attachment_id=row.attachment_id,
                    extraction_id=row.extraction_id,
                    policy_version=QUALITY_POLICY_VERSION,
                    bundle_fingerprint=row.bundle_fingerprint,
                    bundle_size=row.bundle_size,
                    member_primary_status=row.member_primary_status,
                    primary_status=primary_status,
                    rules_usable=assessment.rules_usable,
                    visual_learning_candidate=assessment.visual_learning_candidate,
                    evaluation_candidate=assessment.evaluation_candidate,
                    holdout_eligible=assessment.holdout_eligible,
                    reasons=list(row.reasons),
                )
            )
            counts[primary_status] += 1
        db.commit()
    print(
        "mode=dev-append-only "
        f"entries={len(rows)} "
        f"policy={QUALITY_POLICY_VERSION} "
        f"rules_usable={counts['rules_usable']} "
        f"visual_learning_candidate={counts['visual_learning_candidate']} "
        f"evaluation_candidate={counts['evaluation_candidate']} "
        f"hold={counts['hold']} "
        f"excluded={counts['excluded']}"
    )


if __name__ == "__main__":
    main()
