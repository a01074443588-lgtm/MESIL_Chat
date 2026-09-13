from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any, Mapping, Sequence

from .schemas import (
    CoordinateBox,
    CoordinateRegion,
    normalized_rotated_box_corners,
)

UNCERTAINTY_MARKER = "(?)"
CONFIRMED_STATUS_TERMS = frozenset({"확인", "교체"})


def editable_coordinate_text(value: str) -> tuple[str, bool]:
    """Remove machine uncertainty markers from the employee's editing text.

    The marker remains represented by ``review_required``.  Original OCR and
    extraction suggestion metadata are not changed by this presentation step.
    """

    review_required = UNCERTAINTY_MARKER in value
    editable = value.replace(f" {UNCERTAINTY_MARKER}", "").replace(
        UNCERTAINTY_MARKER, ""
    )
    return editable.strip(), review_required


def prepare_coordinate_regions_for_editor(
    regions: list[CoordinateRegion] | list[dict[str, Any]],
) -> list[CoordinateRegion]:
    """Return editor-safe regions while retaining uncertainty as metadata."""

    prepared: list[CoordinateRegion] = []
    for value in regions:
        region = (
            value
            if isinstance(value, CoordinateRegion)
            else CoordinateRegion.model_validate(value)
        )
        editable, marker_found = editable_coordinate_text(region.corrected_text)
        prepared.append(
            region.model_copy(
                update={
                    "corrected_text": editable,
                    "review_required": region.review_required or marker_found,
                }
            )
        )
    return prepared


def enrich_status_correction_pairs(
    pairs: Sequence[Mapping[str, Any]],
    *,
    regions: Sequence[CoordinateRegion],
    document_template: str | None,
) -> list[dict[str, Any]]:
    """Attach structure only when a confirmed edit came from a status region."""

    enriched: list[dict[str, Any]] = []
    status_regions = [region for region in regions if region.role == "status"]
    for value in pairs:
        item = dict(value)
        recognized = str(item.get("recognized_text") or "").strip()
        corrected = str(item.get("corrected_text") or "").strip()
        matching = next(
            (
                region
                for region in status_regions
                if corrected in CONFIRMED_STATUS_TERMS
                and recognized
                and recognized in region.raw_text
                and corrected in region.corrected_text
            ),
            None,
        )
        if matching is not None:
            item.update(
                {
                    "content_type": "status",
                    "section": matching.section_role or "status_checklist",
                    "layout_role": "status_cell",
                    "column_role": "status",
                    "document_template": (
                        matching.document_template or document_template
                    ),
                }
            )
        enriched.append(item)
    return enriched


@dataclass(frozen=True)
class CoordinateLineLocation:
    line_index: int
    bbox: CoordinateBox
    confidence: float = 0.72


@dataclass(frozen=True)
class CoordinatePlacementEvidence:
    """A staff-confirmed placement that may be reused without rewriting text."""

    document_template: str
    section_role: str
    role: str
    corrected_text: str
    bbox: CoordinateBox


def coordinate_box_corners(
    box: CoordinateBox,
) -> tuple[tuple[float, float], ...]:
    """Return the four saved corners for future deskew/crop consumers."""

    return normalized_rotated_box_corners(
        left=box.left,
        top=box.top,
        width=box.width,
        height=box.height,
        rotation_degrees=box.rotation_degrees,
    )


def coordinate_template_key(
    suggestion_details: Sequence[Mapping[str, Any]] | None,
) -> str | None:
    """Return the matched, versioned template key without exposing OCR content."""

    for item in reversed(suggestion_details or []):
        if item.get("kind") != "versioned_ocr_pipeline" or not item.get("matched"):
            continue
        profile_id = str(item.get("profile_id") or "").strip()
        profile_version = str(item.get("profile_version") or "").strip()
        if not profile_id or not profile_version:
            return None
        return f"{profile_id}@{profile_version}"[:80]
    return None


def _coordinate_line_contexts(
    suggestion_details: Sequence[Mapping[str, Any]] | None,
) -> dict[int, tuple[str, str]]:
    """Map immutable OCR line numbers to a template section and slot role."""

    contexts: dict[int, tuple[str, str]] = {}
    for item in suggestion_details or []:
        kind = item.get("kind")
        if kind not in {"versioned_name_slot", "versioned_status_slot"}:
            continue
        try:
            line_index = int(item.get("line_index"))
        except (TypeError, ValueError):
            continue
        section_role = str(item.get("section_id") or "").strip()
        if line_index < 1 or not section_role:
            continue
        role = "status" if kind == "versioned_status_slot" else "name"
        # A checklist row contains a name label and one or more status values.
        # Its editable line is classified as a status row so future token
        # corrections can be reused only in the same right-hand column.
        if role == "status" or line_index not in contexts:
            contexts[line_index] = (section_role, role)
    return contexts


def _placement_evidence_for_region(
    region: CoordinateRegion,
    evidence: Sequence[CoordinatePlacementEvidence],
) -> CoordinatePlacementEvidence | None:
    if (
        not region.document_template
        or not region.section_role
        or not region.corrected_text.strip()
    ):
        return None
    matches = [
        item
        for item in evidence
        if item.document_template == region.document_template
        and item.section_role == region.section_role
        and item.role == region.role
        and item.corrected_text == region.corrected_text.strip()
    ]
    unique_positions = {
        (
            round(item.bbox.left, 4),
            round(item.bbox.top, 4),
            round(item.bbox.width, 4),
            round(item.bbox.height, 4),
            round(item.bbox.rotation_degrees, 3),
        )
        for item in matches
    }
    # Repeated confirmations at one position are safe. Conflicting historical
    # positions are not applied silently.
    if not matches or len(unique_positions) != 1:
        return None
    return matches[0]


def _clean_lines(value: str | None) -> list[str]:
    return [line.strip() for line in (value or "").splitlines() if line.strip()]


def _paired_lines(
    raw_text: str,
    confirmed_text: str,
) -> list[tuple[str, str, int | None]]:
    raw_lines = _clean_lines(raw_text)
    confirmed_lines = _clean_lines(confirmed_text) or list(raw_lines)
    if not raw_lines:
        return [(line, line, None) for line in confirmed_lines]

    pairs: list[tuple[str, str, int | None]] = []
    matcher = SequenceMatcher(a=raw_lines, b=confirmed_lines, autojunk=False)
    for tag, left_start, left_end, right_start, right_end in matcher.get_opcodes():
        left = raw_lines[left_start:left_end]
        right = confirmed_lines[right_start:right_end]
        if tag == "equal":
            pairs.extend(
                (raw, corrected, left_start + index + 1)
                for index, (raw, corrected) in enumerate(zip(left, right, strict=True))
            )
            continue
        size = max(len(left), len(right))
        for index in range(size):
            raw = left[index] if index < len(left) else ""
            corrected = right[index] if index < len(right) else raw
            raw_line_index = left_start + index + 1 if index < len(left) else None
            pairs.append((raw, corrected, raw_line_index))
    return pairs


def _normalized_bbox(value: object) -> CoordinateBox | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        left, top, right, bottom = [float(item) / 1000 for item in value]
    except (TypeError, ValueError):
        return None
    if not (0 <= left < right <= 1 and 0 <= top < bottom <= 1):
        return None
    return CoordinateBox(
        left=left,
        top=top,
        width=right - left,
        height=bottom - top,
    )


def build_coordinate_bootstrap_regions(
    *,
    raw_text: str,
    confirmed_text: str,
    suggestion_details: list[dict[str, Any]] | None,
    auto_locations: list[CoordinateLineLocation] | None = None,
    placement_evidence: Sequence[CoordinatePlacementEvidence] = (),
) -> list[CoordinateRegion]:
    """Build a conservative first placement without changing OCR/reviewed text."""

    suggestions: list[tuple[str, str, CoordinateBox]] = []
    for item in suggestion_details or []:
        bbox = _normalized_bbox(item.get("region_bbox_normalized"))
        if bbox is None:
            continue
        recognized = str(
            item.get("detected_text") or item.get("recognized") or ""
        ).strip()
        candidate = str(item.get("candidate") or recognized).strip()
        suggestions.append((recognized, candidate, bbox))

    pairs = _paired_lines(raw_text, confirmed_text)
    if not pairs:
        pairs = [("", "", None)]
    locations = {
        item.line_index: item for item in (auto_locations or []) if item.line_index >= 1
    }
    document_template = coordinate_template_key(suggestion_details)
    line_contexts = _coordinate_line_contexts(suggestion_details)
    count = len(pairs)
    line_height = max(0.035, min(0.09, 0.88 / max(count, 1)))
    regions: list[CoordinateRegion] = []
    used_suggestions: set[int] = set()
    for index, (raw, corrected, raw_line_index) in enumerate(pairs):
        editable_corrected, review_required = editable_coordinate_text(corrected)
        section_role, contextual_role = line_contexts.get(
            raw_line_index or -1,
            (None, "general"),
        )
        matched_index: int | None = None
        for suggestion_index, (recognized, candidate, _bbox) in enumerate(suggestions):
            if suggestion_index in used_suggestions:
                continue
            if recognized and (recognized in raw or raw in recognized):
                matched_index = suggestion_index
                break
            if candidate and candidate == corrected:
                matched_index = suggestion_index
                break
        if matched_index is not None:
            recognized, candidate, bbox = suggestions[matched_index]
            used_suggestions.add(matched_index)
            regions.append(
                CoordinateRegion(
                    client_id=f"bootstrap-{index + 1}",
                    bbox=bbox,
                    raw_text=recognized or raw,
                    corrected_text=(editable_corrected or candidate or recognized)
                    .replace(UNCERTAINTY_MARKER, "")
                    .strip(),
                    role=contextual_role if section_role else "name",
                    document_template=document_template,
                    section_role=section_role,
                    position_confidence=0.85,
                    review_required=review_required or UNCERTAINTY_MARKER in candidate,
                    placement_status="auto",
                    source="ocr_bbox",
                )
            )
            continue

        # Auto-location is requested with the exact list shown by the editor.
        # Use that displayed-region index instead of the original OCR line index:
        # reviewed text may insert, remove, or reorder lines.
        location = locations.get(index + 1)
        if location is not None:
            regions.append(
                CoordinateRegion(
                    client_id=f"bootstrap-{index + 1}",
                    bbox=location.bbox,
                    raw_text=raw,
                    corrected_text=editable_corrected,
                    role=contextual_role,
                    document_template=document_template,
                    section_role=section_role,
                    position_confidence=location.confidence,
                    review_required=review_required,
                    placement_status="auto",
                    source="auto_locator",
                )
            )
            continue

        history_candidate = CoordinateRegion(
            client_id=f"bootstrap-{index + 1}",
            bbox=CoordinateBox(left=0.05, top=0.04, width=0.9, height=line_height),
            raw_text=raw,
            corrected_text=editable_corrected,
            role=contextual_role,
            document_template=document_template,
            section_role=section_role,
            position_confidence=0,
            review_required=review_required,
            placement_status="needs_position",
            source="legacy_alignment",
        )
        historical = _placement_evidence_for_region(
            history_candidate,
            placement_evidence,
        )
        if historical is not None:
            regions.append(
                history_candidate.model_copy(
                    update={
                        "bbox": historical.bbox,
                        "position_confidence": 0.8,
                        "placement_status": "auto",
                        "source": "confirmed_history",
                    }
                )
            )
            continue

        top = min(0.94 - line_height, 0.04 + index * (0.9 / max(count, 1)))
        regions.append(
            CoordinateRegion(
                client_id=f"bootstrap-{index + 1}",
                bbox=CoordinateBox(
                    left=0.05,
                    top=max(0, top),
                    width=0.9,
                    height=line_height,
                ),
                raw_text=raw,
                corrected_text=editable_corrected,
                role=contextual_role,
                document_template=document_template,
                section_role=section_role,
                position_confidence=0.2,
                review_required=review_required,
                placement_status="needs_position",
                source="legacy_alignment",
            )
        )
    return regions
