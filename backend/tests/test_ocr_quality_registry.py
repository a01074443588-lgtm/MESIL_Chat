from dataclasses import dataclass

from app.ocr_quality_registry import (
    OcrEvidenceSnapshot,
    OcrQualityAssessment,
    assess_ocr_evidence,
    group_evidence_bundles,
)


def _complete(**overrides: bool) -> OcrEvidenceSnapshot:
    values = {
        "extraction_completed": True,
        "original_ocr_present": True,
        "integrity_hash_present": True,
        "source_message_active": True,
        "has_staff_confirmed_text": True,
        "has_confirmed_correction_event": True,
        "coordinate_version_present": True,
        "all_coordinate_regions_confirmed": True,
        "coordinate_regions_non_overlapping": True,
        "rotation_metadata_complete": True,
        "template_classified": True,
        "region_and_role_classified": True,
        "writer_identity_confirmed": True,
        "already_visible_to_candidate_rules": True,
        "belongs_to_existing_evaluation_group": False,
    }
    values.update(overrides)
    return OcrEvidenceSnapshot(**values)


def test_complete_review_is_usable_for_rules_and_visual_learning() -> None:
    assessment = assess_ocr_evidence(_complete())

    assert assessment.rules_usable is True
    assert assessment.visual_learning_candidate is True
    assert assessment.primary_status == "visual_learning_candidate"
    assert assessment.evaluation_candidate is False
    assert "candidate_history_leakage" in assessment.reasons


def test_missing_writer_blocks_visual_learning_but_not_structured_rules() -> None:
    assessment = assess_ocr_evidence(_complete(writer_identity_confirmed=False))

    assert assessment.rules_usable is True
    assert assessment.visual_learning_candidate is False
    assert "writer_identity_unknown" in assessment.reasons


def test_unconfirmed_or_overlapping_coordinates_fail_closed() -> None:
    assessment = assess_ocr_evidence(
        _complete(
            all_coordinate_regions_confirmed=False,
            coordinate_regions_non_overlapping=False,
        )
    )

    assert assessment.rules_usable is True
    assert assessment.visual_learning_candidate is False
    assert "coordinate_regions_unconfirmed" in assessment.reasons
    assert "coordinate_regions_overlap" in assessment.reasons


def test_clean_never_used_group_can_be_evaluation_candidate() -> None:
    assessment = assess_ocr_evidence(
        _complete(
            has_staff_confirmed_text=False,
            has_confirmed_correction_event=False,
            coordinate_version_present=False,
            all_coordinate_regions_confirmed=False,
            coordinate_regions_non_overlapping=False,
            rotation_metadata_complete=False,
            template_classified=False,
            region_and_role_classified=False,
            already_visible_to_candidate_rules=False,
            writer_identity_confirmed=False,
        )
    )

    assert assessment.evaluation_candidate is True
    assert assessment.holdout_eligible is True
    assert assessment.visual_learning_candidate is False


def test_unconfirmed_text_never_enters_any_path() -> None:
    assessment = assess_ocr_evidence(_complete(has_staff_confirmed_text=False))

    assert assessment.rules_usable is False
    assert assessment.visual_learning_candidate is False
    assert assessment.evaluation_candidate is False
    assert assessment.primary_status == "hold"


def test_invalid_source_is_excluded() -> None:
    assessment = assess_ocr_evidence(_complete(original_ocr_present=False))

    assert assessment.primary_status == "excluded"
    assert "original_ocr_missing" in assessment.reasons


@dataclass
class _Member:
    organization_id: str
    attachment_id: str
    message_id: str
    sha256: str | None
    assessment: OcrQualityAssessment


def _member(
    attachment_id: str,
    *,
    message_id: str,
    sha256: str | None,
    assessment: OcrQualityAssessment,
) -> _Member:
    return _Member("org", attachment_id, message_id, sha256, assessment)


def test_bundle_groups_same_message_and_exact_duplicates_without_leakage() -> None:
    rules = assess_ocr_evidence(_complete(writer_identity_confirmed=False))
    evaluation = assess_ocr_evidence(
        _complete(
            has_staff_confirmed_text=False,
            has_confirmed_correction_event=False,
            coordinate_version_present=False,
            all_coordinate_regions_confirmed=False,
            coordinate_regions_non_overlapping=False,
            rotation_metadata_complete=False,
            template_classified=False,
            region_and_role_classified=False,
            writer_identity_confirmed=False,
            already_visible_to_candidate_rules=False,
        )
    )
    bundles = group_evidence_bundles(
        [
            _member("a", message_id="m1", sha256="hash-a", assessment=rules),
            _member("b", message_id="m1", sha256="hash-b", assessment=rules),
            _member("c", message_id="m2", sha256="hash-b", assessment=evaluation),
        ]
    )

    assert len(bundles) == 1
    assert bundles[0].primary_status == "hold"
    assert len(bundles[0].members) == 3
    assert "bundle_member_status_mixed" in bundles[0].reasons


def test_bundle_fingerprint_is_deterministic_and_contains_no_source_id() -> None:
    rules = assess_ocr_evidence(_complete(writer_identity_confirmed=False))
    rows = [
        _member("attachment-secret", message_id="message-secret", sha256=None, assessment=rules)
    ]

    first = group_evidence_bundles(rows)[0]
    second = group_evidence_bundles(rows)[0]

    assert first.fingerprint == second.fingerprint
    assert len(first.fingerprint) == 64
    assert "attachment-secret" not in first.fingerprint
