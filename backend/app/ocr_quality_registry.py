"""Fail-closed classification for OCR improvement evidence.

The classifier receives metadata only. It does not read image bytes, run OCR,
or mutate the source extraction/review records.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol


QUALITY_POLICY_VERSION = "quality-v2"
PRIMARY_STATUSES = (
    "rules_usable",
    "visual_learning_candidate",
    "evaluation_candidate",
    "hold",
    "excluded",
)


@dataclass(frozen=True)
class OcrEvidenceSnapshot:
    """Non-content metadata required to judge one source image."""

    extraction_completed: bool
    original_ocr_present: bool
    integrity_hash_present: bool
    source_message_active: bool
    has_staff_confirmed_text: bool
    has_confirmed_correction_event: bool
    coordinate_version_present: bool
    all_coordinate_regions_confirmed: bool
    coordinate_regions_non_overlapping: bool
    rotation_metadata_complete: bool
    template_classified: bool
    region_and_role_classified: bool
    writer_identity_confirmed: bool
    already_visible_to_candidate_rules: bool
    belongs_to_existing_evaluation_group: bool = False


@dataclass(frozen=True)
class OcrQualityAssessment:
    """Explainable member assessment containing no OCR text or PII."""

    primary_status: str
    rules_usable: bool
    visual_learning_candidate: bool
    evaluation_candidate: bool
    holdout_eligible: bool
    reasons: tuple[str, ...]


class BundleMember(Protocol):
    organization_id: object
    attachment_id: object
    message_id: object
    sha256: str | None
    assessment: OcrQualityAssessment


@dataclass(frozen=True)
class OcrEvidenceBundle:
    """One privacy-safe group of message images and exact duplicates."""

    fingerprint: str
    members: tuple[BundleMember, ...]
    primary_status: str
    reasons: tuple[str, ...]


def assess_ocr_evidence(snapshot: OcrEvidenceSnapshot) -> OcrQualityAssessment:
    """Classify one image without modifying source data.

    Text/context rules can use an explicit staff correction without requiring
    crop-quality geometry. Visual learning is deliberately stricter. A clean
    evaluation candidate is an untouched completed source awaiting future
    independent ground-truth review.
    """

    reasons: list[str] = []

    hard_failures = {
        "extraction_not_completed": not snapshot.extraction_completed,
        "original_ocr_missing": not snapshot.original_ocr_present,
        "integrity_hash_missing": not snapshot.integrity_hash_present,
        "source_message_inactive": not snapshot.source_message_active,
    }
    reasons.extend(key for key, failed in hard_failures.items() if failed)

    if not snapshot.has_staff_confirmed_text:
        reasons.append("staff_confirmation_missing")
    if not snapshot.has_confirmed_correction_event:
        reasons.append("confirmed_correction_event_missing")
    if not snapshot.coordinate_version_present:
        reasons.append("coordinate_version_missing")
    if not snapshot.all_coordinate_regions_confirmed:
        reasons.append("coordinate_regions_unconfirmed")
    if not snapshot.coordinate_regions_non_overlapping:
        reasons.append("coordinate_regions_overlap")
    if not snapshot.rotation_metadata_complete:
        reasons.append("rotation_metadata_incomplete")
    if not snapshot.template_classified:
        reasons.append("template_unclassified")
    if not snapshot.region_and_role_classified:
        reasons.append("region_or_role_unclassified")
    if not snapshot.writer_identity_confirmed:
        reasons.append("writer_identity_unknown")

    source_valid = not any(hard_failures.values())
    rules_usable = (
        source_valid
        and snapshot.has_staff_confirmed_text
        and snapshot.has_confirmed_correction_event
    )
    visual_learning_candidate = (
        rules_usable
        and snapshot.coordinate_version_present
        and snapshot.all_coordinate_regions_confirmed
        and snapshot.coordinate_regions_non_overlapping
        and snapshot.rotation_metadata_complete
        and snapshot.template_classified
        and snapshot.region_and_role_classified
        and snapshot.writer_identity_confirmed
    )
    holdout_eligible = (
        source_valid
        and not snapshot.has_staff_confirmed_text
        and not snapshot.has_confirmed_correction_event
        and not snapshot.coordinate_version_present
        and not snapshot.already_visible_to_candidate_rules
        and not snapshot.belongs_to_existing_evaluation_group
    )
    if snapshot.already_visible_to_candidate_rules:
        reasons.append("candidate_history_leakage")
    if snapshot.belongs_to_existing_evaluation_group:
        reasons.append("evaluation_group_already_reserved")

    evaluation_candidate = holdout_eligible
    if any(hard_failures.values()):
        primary_status = "excluded"
    elif visual_learning_candidate:
        primary_status = "visual_learning_candidate"
    elif rules_usable:
        primary_status = "rules_usable"
    elif evaluation_candidate:
        primary_status = "evaluation_candidate"
    else:
        primary_status = "hold"
    return OcrQualityAssessment(
        primary_status=primary_status,
        rules_usable=rules_usable,
        visual_learning_candidate=visual_learning_candidate,
        evaluation_candidate=evaluation_candidate,
        holdout_eligible=holdout_eligible,
        reasons=tuple(dict.fromkeys(reasons)),
    )


class _DisjointSet:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))

    def find(self, index: int) -> int:
        while self.parents[index] != index:
            self.parents[index] = self.parents[self.parents[index]]
            index = self.parents[index]
        return index

    def union(self, left: int, right: int) -> None:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root != right_root:
            self.parents[right_root] = left_root


def group_evidence_bundles(rows: list[BundleMember]) -> list[OcrEvidenceBundle]:
    """Group same-message pages and exact SHA duplicates without exposing IDs."""

    if not rows:
        return []
    sets = _DisjointSet(len(rows))
    seen_messages: dict[tuple[str, str], int] = {}
    seen_hashes: dict[tuple[str, str], int] = {}
    for index, row in enumerate(rows):
        organization_key = str(row.organization_id)
        message_key = (organization_key, str(row.message_id))
        if message_key in seen_messages:
            sets.union(index, seen_messages[message_key])
        else:
            seen_messages[message_key] = index
        if row.sha256:
            hash_key = (organization_key, row.sha256.lower())
            if hash_key in seen_hashes:
                sets.union(index, seen_hashes[hash_key])
            else:
                seen_hashes[hash_key] = index

    grouped: dict[int, list[BundleMember]] = {}
    for index, row in enumerate(rows):
        grouped.setdefault(sets.find(index), []).append(row)

    bundles: list[OcrEvidenceBundle] = []
    for members in grouped.values():
        members.sort(key=lambda item: str(item.attachment_id))
        statuses = [member.assessment.primary_status for member in members]
        if all(status == "excluded" for status in statuses):
            status = "excluded"
        elif all(status == "visual_learning_candidate" for status in statuses):
            status = "visual_learning_candidate"
        elif all(
            status in {"rules_usable", "visual_learning_candidate"}
            for status in statuses
        ):
            status = "rules_usable"
        elif all(status == "evaluation_candidate" for status in statuses):
            status = "evaluation_candidate"
        else:
            status = "hold"
        reasons = sorted(
            {reason for member in members for reason in member.assessment.reasons}
        )
        if len(set(statuses)) > 1:
            reasons.append("bundle_member_status_mixed")
        identity = "|".join(
            f"{member.organization_id}:{member.attachment_id}" for member in members
        )
        bundles.append(
            OcrEvidenceBundle(
                fingerprint=sha256(identity.encode("ascii")).hexdigest(),
                members=tuple(members),
                primary_status=status,
                reasons=tuple(reasons),
            )
        )
    return sorted(bundles, key=lambda bundle: bundle.fingerprint)
