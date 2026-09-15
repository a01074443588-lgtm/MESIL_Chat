from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from functools import lru_cache
from pathlib import Path
from typing import Any

PROFILE_PATH = Path(__file__).with_name("data") / "ocr_template_profiles.v1.json"
MEAL_HEADER_PATTERN = re.compile(
    r"^[\s<>{}\[\]【】()（）〈〉《》]*"
    r"(?P<meal>아침|점심|저녁)?\s*식사\s*(?:하신\s*분|드신\s*분|관련|확인)?"
    r"[\s<>{}\[\]【】()（）〈〉《》:：]*$"
)
STATUS_ROW_PATTERN = re.compile(
    r"^\s*(?:[.·•\-○◦*]\s*)?(?P<left>[가-힣]{2,4})"
    r"\s*[:：]\s*(?P<right>[가-힣]{2,6}(?:\s*[,，/]\s*[가-힣]{2,6}){0,2})\s*$"
)
STATUS_STRUCTURE_ROW_PATTERN = re.compile(
    r"^\s*(?:[.·•\-○◦*]\s*)?(?P<left>[가-힣]{2,4})"
    r"\s*[:：]\s*(?P<right>[가-힣0-9()（）·,，/\-\s]{1,24})\s*$"
)
MEAL_PAIR_PATTERN = re.compile(
    r"^\s*(?:[·•\-○◦*]\s*)?(?P<left>[가-힣]{2,4})"
    r"\s*[:：]\s*(?P<right>[가-힣]{2,4})\s*$"
)
BARE_NAME_LIST_PATTERN = re.compile(
    r"^\s*(?:[·•\-○◦*]\s*)?(?P<body>[가-힣]{2,4}(?:\s*[,，·ㆍ/]\s*[가-힣]{2,4})*)\s*$"
)
TIME_PATTERN = re.compile(r"(?:\d{1,2}:\d{2}|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?)")


@dataclass(frozen=True)
class TemplateProfile:
    profile_id: str
    profile_version: str
    rule_version: str
    operating_mode: str
    document_role: str
    page_role: str
    resident_service_scope: str
    minimum_status_rows: int
    minimum_meal_name_slots: int
    minimum_name_similarity: float
    minimum_name_gap: float
    status_terms: frozenset[str]
    status_aliases: dict[str, str]
    context_terms: tuple[str, ...]
    mapped_terms: frozenset[str]


@dataclass(frozen=True)
class TemplateSlot:
    start: int
    end: int
    raw: str
    line_index: int
    section_id: str
    table_id: str | None
    row_index: int
    column_id: str
    role: str
    evidence: tuple[str, ...]


@dataclass(frozen=True)
class TemplateDecision:
    slot: TemplateSlot
    action: str
    proposed: str
    candidate: str | None
    top_score: float | None
    second_score: float | None
    reason: str


@dataclass(frozen=True)
class TemplateAnalysis:
    matched: bool
    profile: TemplateProfile
    match_score: float
    slots: tuple[TemplateSlot, ...]
    decisions: tuple[TemplateDecision, ...]
    proposed_draft: str
    audit_metadata: dict[str, Any]
    source_text: str = ""


@dataclass(frozen=True)
class VisualNameObservation:
    """One independently cropped name-cell reading.

    The crop coordinates and candidate ranks are evidence for a non-final
    draft only.  They never replace the immutable full-page OCR or a staff
    confirmed value.
    """

    slot_index: int
    recognized: str
    candidates: tuple[str, ...]
    normalized_bbox: tuple[int, int, int, int] | None = None


@lru_cache(maxsize=1)
def load_combined_checklist_profile() -> TemplateProfile:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    raw = payload["profiles"][0]
    slots = raw["slots"]
    mapped_terms = {
        *slots["section_header"]["terms"],
        *slots["status"]["allowed_terms"],
        *slots["status"]["aliases"].keys(),
        *slots["narrative"]["context_terms"],
    }
    return TemplateProfile(
        profile_id=raw["profile_id"],
        profile_version=raw["profile_version"],
        rule_version=raw["rule_version"],
        operating_mode=raw["operating_mode"],
        document_role=raw["document_role"],
        page_role=raw["page_role"],
        resident_service_scope=raw["resident_service_scope"],
        minimum_status_rows=int(raw["match"]["minimum_status_rows"]),
        minimum_meal_name_slots=int(raw["match"]["minimum_meal_name_slots"]),
        minimum_name_similarity=float(slots["name"]["minimum_similarity"]),
        minimum_name_gap=float(slots["name"]["minimum_top_gap"]),
        status_terms=frozenset(slots["status"]["allowed_terms"]),
        status_aliases=dict(slots["status"]["aliases"]),
        context_terms=tuple(slots["narrative"]["context_terms"]),
        mapped_terms=frozenset(mapped_terms),
    )


def _line_offsets(text: str) -> list[tuple[int, str]]:
    result: list[tuple[int, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True) or [text]:
        result.append((offset, line.rstrip("\r\n")))
        offset += len(line)
    return result


def _normalized_roster(roster_names: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    names = {
        re.sub(r"\s*\((?:가명|시험[^)]*)\)\s*$", "", name).replace(" ", "").strip()
        for name in roster_names
    }
    return tuple(sorted(name for name in names if re.fullmatch(r"[가-힣]{2,4}", name)))


def _status_run(
    lines: list[tuple[int, str]], profile: TemplateProfile
) -> list[tuple[int, re.Match[str]]]:
    candidates: list[tuple[int, re.Match[str]]] = []
    best: list[tuple[int, re.Match[str]]] = []
    for line_index, (_, line) in enumerate(lines, 1):
        match = STATUS_STRUCTURE_ROW_PATTERN.fullmatch(line)
        if match:
            candidates.append((line_index, match))
            continue
        if len(candidates) > len(best):
            best = candidates
        candidates = []
    if len(candidates) > len(best):
        best = candidates
    return best


def _meal_slots(
    lines: list[tuple[int, str]], profile: TemplateProfile
) -> list[TemplateSlot]:
    header_index: int | None = None
    canonical_headers = (
        "아침식사하신분",
        "점심식사하신분",
        "저녁식사하신분",
        "아침식사관련",
        "점심식사관련",
        "저녁식사관련",
    )
    for line_index, (_, line) in enumerate(lines, 1):
        compact = re.sub(r"[^가-힣]", "", line)
        exact_header = MEAL_HEADER_PATTERN.fullmatch(line.strip()) is not None
        fuzzy_header = bool(
            4 <= len(compact) <= 12
            and max(
                (
                    SequenceMatcher(None, compact, value).ratio()
                    for value in canonical_headers
                ),
                default=0.0,
            )
            >= 0.70
        )
        if exact_header or fuzzy_header:
            header_index = line_index
            break
    if header_index is None:
        return []

    rows: list[list[tuple[int, int, str, str]]] = []
    for line_index in range(header_index + 1, min(len(lines), header_index + 8) + 1):
        offset, line = lines[line_index - 1]
        if not line.strip():
            continue
        if MEAL_HEADER_PATTERN.fullmatch(line.strip()):
            break
        pair = MEAL_PAIR_PATTERN.fullmatch(line)
        row: list[tuple[int, int, str, str]] = []
        if pair:
            for column in ("left", "right"):
                value = pair.group(column)
                row.append(
                    (
                        offset + pair.start(column),
                        offset + pair.end(column),
                        value,
                        column,
                    )
                )
        else:
            bare = BARE_NAME_LIST_PATTERN.fullmatch(line)
            if bare:
                body_start = bare.start("body")
                for token_index, token in enumerate(
                    re.finditer(r"[가-힣]{2,4}", bare.group("body"))
                ):
                    row.append(
                        (
                            offset + body_start + token.start(),
                            offset + body_start + token.end(),
                            token.group(0),
                            "left"
                            if token_index == 0
                            else "right"
                            if token_index == 1
                            else "list",
                        )
                    )
        if not row:
            break
        if any(value in profile.mapped_terms for _, _, value, _ in row):
            break
        rows.append(row)

    if sum(len(row) for row in rows) < profile.minimum_meal_name_slots:
        return []
    slots: list[TemplateSlot] = []
    for row_index, row in enumerate(rows, 1):
        for start, end, raw, column in row:
            slots.append(
                TemplateSlot(
                    start=start,
                    end=end,
                    raw=raw,
                    line_index=header_index + row_index,
                    section_id="meal_name_list",
                    table_id="meal_roster_table",
                    row_index=row_index,
                    column_id=column,
                    role="name",
                    evidence=("confirmed_slot", "header", "repeated"),
                )
            )
    return slots


def _status_slots(
    lines: list[tuple[int, str]],
    profile: TemplateProfile,
    *,
    roster_names: Sequence[str] = (),
    has_companion_meal_section: bool = False,
) -> list[TemplateSlot]:
    run = _status_run(lines, profile)
    if len(run) < profile.minimum_status_rows:
        return []

    # A repeated ``label: value`` shape alone is not enough to call a column a
    # care-status table.  An exact canonical status plus active-roster labels,
    # or an independently strong repeated layout, must confirm the table before
    # its short right-hand cells become status slots.  Aliases can contribute
    # only when the companion meal section independently confirms the template;
    # without that section they never bootstrap the structure they will change.
    status_signal_count = sum(
        1
        for _line_index, match in run
        for token in re.finditer(r"[가-힣]{2,6}", match.group("right"))
        if token.group(0) in profile.status_terms
        or token.group(0) in profile.status_aliases
    )
    # Real handwriting OCR can miss every status value at once.  Requiring an
    # already-correct ``확인/교체`` value then creates a circular failure: the
    # table is not recognized, so its scoped aliases/history can never run.
    # Permit that seedless case only when independent layout evidence is
    # strong: at least four consecutive short ``name: status[, status]`` rows,
    # aligned colon positions, and at least two exact active-roster labels.
    # This keeps generic prose and isolated colon rows outside the status path.
    roster = set(_normalized_roster(tuple(roster_names)))
    compact_right_cells = []
    colon_positions = []
    exact_roster_labels = 0
    for line_index, match in run:
        right = match.group("right")
        tokens = re.findall(r"[가-힣]{2,6}", right)
        compact_right_cells.append(
            bool(
                1 <= len(tokens) <= 2
                and re.fullmatch(r"[가-힣\s,，/]+", right) is not None
            )
        )
        colon_positions.append(match.start("right"))
        exact_roster_labels += int(match.group("left") in roster)
    strong_seedless_table = bool(
        len(run) >= max(4, profile.minimum_status_rows + 2)
        and all(compact_right_cells)
        and colon_positions
        and max(colon_positions) - min(colon_positions) <= 4
        and exact_roster_labels >= 2
    )
    seeded_table = bool(
        status_signal_count >= profile.minimum_status_rows
        and (
            has_companion_meal_section
            or exact_roster_labels >= profile.minimum_status_rows
        )
    )
    if (
        not seeded_table
        and not strong_seedless_table
    ):
        return []

    slots: list[TemplateSlot] = []
    for row_index, (line_index, match) in enumerate(run, 1):
        offset = lines[line_index - 1][0]
        slots.append(
            TemplateSlot(
                start=offset + match.start("left"),
                end=offset + match.end("left"),
                raw=match.group("left"),
                line_index=line_index,
                section_id="status_checklist",
                table_id="status_table",
                row_index=row_index,
                column_id="name",
                role="name",
                evidence=("confirmed_slot", "label", "repeated"),
            )
        )
        right = match.group("right")
        for token in re.finditer(r"[가-힣]{2,6}", right):
            value = token.group(0)
            value_is_known = (
                value in profile.status_terms or value in profile.status_aliases
            )
            slots.append(
                TemplateSlot(
                    start=offset + match.start("right") + token.start(),
                    end=offset + match.start("right") + token.end(),
                    raw=value,
                    line_index=line_index,
                    section_id="status_checklist",
                    table_id="status_table",
                    row_index=row_index,
                    column_id="status",
                    role="status",
                    evidence=(
                        "confirmed_slot",
                        "repeated",
                        "allowed_value" if value_is_known else "status_column",
                    ),
                )
            )
    return slots


def _history_context_confirms_status_slot(
    item: Mapping[str, object],
    *,
    raw: str,
    profile: TemplateProfile,
) -> bool:
    document_template = str(item.get("document_template") or "").strip()
    if document_template and document_template.split("@", 1)[0] != profile.profile_id:
        return False
    section = str(item.get("section") or "").strip()
    layout_role = str(item.get("layout_role") or "").strip()
    column_role = str(item.get("column_role") or "").strip()
    content_type = str(item.get("content_type") or "").strip()
    if section == "status_checklist" and (
        layout_role in {"status", "status_cell", "status_checklist"}
        or column_role == "status"
        or content_type == "status"
    ):
        return True

    # Older confirmed events did not persist column_role.  They may be reused
    # only when their own immutable context independently proves that the
    # source token occupied the right side of the same repeated status table.
    context_text = str(item.get("context_text") or "")
    if not context_text:
        return False
    historical_slots = _status_slots(_line_offsets(context_text), profile)
    return any(
        slot.role == "status" and slot.raw == raw for slot in historical_slots
    )


def _confirmed_status_history_candidate(
    raw: str,
    *,
    profile: TemplateProfile,
    confirmed_history: Sequence[Mapping[str, object]],
) -> tuple[str | None, int]:
    targets: list[str] = []
    for item in confirmed_history:
        recognized = str(item.get("recognized_text") or "").strip()
        corrected = str(item.get("corrected_text") or "").strip()
        if recognized != raw or corrected not in profile.status_terms:
            continue
        if not _history_context_confirms_status_slot(
            item,
            raw=raw,
            profile=profile,
        ):
            continue
        targets.append(corrected)
    unique_targets = set(targets)
    if len(unique_targets) != 1:
        return None, len(targets)
    return next(iter(unique_targets)), len(targets)


def _rank_name(
    raw: str,
    roster: tuple[str, ...],
    *,
    section_id: str,
    confirmed_history: Sequence[Mapping[str, object]],
) -> tuple[str | None, float, float]:
    expected_layout = (
        "meal_name_list" if section_id == "meal_name_list" else "label_value_row"
    )

    def history_bonus(name: str) -> float:
        for item in confirmed_history:
            if (
                str(item.get("recognized_text", "")).strip() == raw
                and str(item.get("corrected_text", "")).strip() == name
                and str(item.get("layout_role", "")).strip() == expected_layout
                and str(item.get("section", "")).strip()
            ):
                return 0.04
        return 0.0

    ranked = sorted(
        (
            (
                name,
                min(
                    1.0, SequenceMatcher(None, raw, name).ratio() + history_bonus(name)
                ),
            )
            for name in roster
        ),
        key=lambda item: (-item[1], item[0]),
    )
    if not ranked:
        return None, 0.0, 0.0
    top_name, top_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0
    return top_name, top_score, second_score


def analyze_combined_checklist(
    raw_text: str,
    *,
    active_roster_names: list[str] | tuple[str, ...],
    confirmed_history: Sequence[Mapping[str, object]] = (),
) -> TemplateAnalysis:
    """Build an offline, non-final draft for one confirmed template family.

    This function never mutates the source text or persistence objects.  The
    profile is audit-only until a fixed evaluation set and a separate release
    gate approve operational use.
    """

    profile = load_combined_checklist_profile()
    lines = _line_offsets(raw_text)
    meal_slots = _meal_slots(lines, profile)
    roster = _normalized_roster(active_roster_names)
    status_slots = _status_slots(
        lines,
        profile,
        roster_names=roster,
        has_companion_meal_section=bool(meal_slots),
    )
    status_row_count = len(
        {slot.row_index for slot in status_slots if slot.role == "status"}
    )
    full_template_match = bool(status_slots and meal_slots)
    status_only_match = bool(status_slots and status_row_count >= 4)
    matched = bool(full_template_match or status_only_match)
    if status_only_match and not full_template_match:
        # The limited recovery path exists only to repair confirmed status
        # cells.  It must not turn uncertain row labels into resident names.
        status_slots = [slot for slot in status_slots if slot.role == "status"]
    slots: list[TemplateSlot] = [*status_slots, *meal_slots]

    structured_ranges = [(slot.start, slot.end) for slot in slots]
    for line_index, (offset, line) in enumerate(lines, 1):
        for time_match in TIME_PATTERN.finditer(line):
            start, end = offset + time_match.start(), offset + time_match.end()
            if any(
                start < existing_end and end > existing_start
                for existing_start, existing_end in structured_ranges
            ):
                continue
            slots.append(
                TemplateSlot(
                    start=start,
                    end=end,
                    raw=time_match.group(0),
                    line_index=line_index,
                    section_id="narrative",
                    table_id=None,
                    row_index=line_index,
                    column_id="time",
                    role="time",
                    evidence=("pattern", "preserve_raw"),
                )
            )

    decisions: list[TemplateDecision] = []
    replacements: list[tuple[int, int, str]] = []
    for slot in sorted(slots, key=lambda item: (item.start, item.end)):
        if slot.role == "name":
            required_structure = (
                matched
                and "confirmed_slot" in slot.evidence
                and ("repeated" in slot.evidence)
            )
            if slot.raw in roster:
                decisions.append(
                    TemplateDecision(
                        slot,
                        "preserve_exact",
                        slot.raw,
                        slot.raw,
                        1.0,
                        None,
                        "active_roster_exact",
                    )
                )
                continue
            candidate, top_score, second_score = _rank_name(
                slot.raw,
                roster,
                section_id=slot.section_id,
                confirmed_history=confirmed_history,
            )
            gap = top_score - second_score
            if (
                required_structure
                and candidate is not None
                and top_score >= profile.minimum_name_similarity
                and gap >= profile.minimum_name_gap
            ):
                proposed = f"{candidate} (?)"
                replacements.append((slot.start, slot.end, proposed))
                decisions.append(
                    TemplateDecision(
                        slot,
                        "candidate",
                        proposed,
                        candidate,
                        top_score,
                        second_score,
                        "structure_roster_score_pass",
                    )
                )
            else:
                decisions.append(
                    TemplateDecision(
                        slot,
                        "preserve_uncertain",
                        slot.raw,
                        candidate,
                        top_score,
                        second_score,
                        "insufficient_structure_or_score",
                    )
                )
        elif slot.role == "status":
            canonical = profile.status_aliases.get(slot.raw, slot.raw)
            if canonical != slot.raw:
                replacements.append((slot.start, slot.end, canonical))
                action, reason = "context_correction", "status_slot_alias"
            elif canonical in profile.status_terms:
                action, reason = "preserve_exact", "allowed_status_exact"
            else:
                historical, support_count = _confirmed_status_history_candidate(
                    slot.raw,
                    profile=profile,
                    confirmed_history=confirmed_history,
                )
                if historical is not None:
                    canonical = historical
                    replacements.append((slot.start, slot.end, canonical))
                    action = "history_context_correction"
                    reason = f"confirmed_status_history:{support_count}"
                else:
                    action = "preserve_uncertain_status"
                    reason = "no_safe_status_candidate"
            decisions.append(
                TemplateDecision(
                    slot,
                    action,
                    canonical,
                    canonical if canonical in profile.status_terms else None,
                    1.0 if canonical in profile.status_terms else None,
                    None,
                    reason,
                )
            )
        else:
            decisions.append(
                TemplateDecision(
                    slot, "preserve_raw", slot.raw, None, None, None, "protected_time"
                )
            )

    proposed_draft = raw_text
    if matched:
        for start, end, value in sorted(replacements, reverse=True):
            proposed_draft = f"{proposed_draft[:start]}{value}{proposed_draft[end:]}"

    section_ids = sorted({slot.section_id for slot in slots})
    match_score = (
        min(1.0, 0.5 + 0.1 * len(status_slots) + 0.05 * len(meal_slots))
        if matched
        else 0.0
    )
    audit_metadata = {
        "kind": "template_audit",
        "profile_id": profile.profile_id,
        "profile_version": profile.profile_version,
        "rule_version": profile.rule_version,
        "operating_mode": profile.operating_mode,
        "matched": matched,
        "match_mode": (
            "full_template"
            if full_template_match
            else "status_table_only"
            if status_only_match
            else "none"
        ),
        "match_score": round(match_score, 3),
        "document_role": profile.document_role,
        "page_role": profile.page_role,
        "hierarchy": {
            "sections": section_ids,
            "tables": sorted({slot.table_id for slot in slots if slot.table_id}),
            "row_count": len({(slot.section_id, slot.row_index) for slot in slots}),
            "slot_count": len(slots),
        },
        "auto_apply_allowed": False,
        "confirmed_history": {
            "available": len(confirmed_history),
            "structure_eligible": sum(
                1
                for item in confirmed_history
                if item.get("section") and item.get("layout_role")
            ),
            "policy": "same_section_and_layout_weak_signal_only",
        },
    }
    return TemplateAnalysis(
        matched=matched,
        profile=profile,
        match_score=match_score,
        slots=tuple(slots),
        decisions=tuple(decisions),
        proposed_draft=proposed_draft,
        audit_metadata=audit_metadata,
        source_text=raw_text,
    )


def apply_visual_name_observations(
    analysis: TemplateAnalysis,
    *,
    active_roster_names: list[str] | tuple[str, ...],
    observations: Sequence[VisualNameObservation],
) -> TemplateAnalysis:
    """Re-rank only confirmed name slots using independent image crops.

    A candidate must be supported by the crop reading, local string
    similarity, a clear first/second gap, and the already-confirmed template
    structure.  Missing or ambiguous evidence becomes ``(?)`` in the draft;
    no out-of-roster surface form is copied into the draft.
    """

    if not analysis.matched:
        return analysis
    roster = _normalized_roster(active_roster_names)
    name_decisions = [
        decision for decision in analysis.decisions if decision.slot.role == "name"
    ]
    by_slot = {
        item.slot_index: item
        for item in observations
        if 1 <= item.slot_index <= len(name_decisions)
    }
    updated_by_range: dict[tuple[int, int], TemplateDecision] = {}
    visual_audit: list[dict[str, Any]] = []
    for slot_index, original in enumerate(name_decisions, 1):
        slot = original.slot
        structure_ok = "confirmed_slot" in slot.evidence and "repeated" in slot.evidence
        if slot.raw in roster:
            updated = replace(
                original,
                action="preserve_exact",
                proposed=slot.raw,
                candidate=slot.raw,
                top_score=1.0,
                second_score=None,
                reason="active_roster_exact",
            )
            updated_by_range[(slot.start, slot.end)] = updated
            continue
        observation = by_slot.get(slot_index)
        ranked: list[tuple[str, float, float, int | None]] = []
        if observation is not None:
            visual_ranks = {
                candidate: rank
                for rank, candidate in enumerate(observation.candidates, 1)
                if candidate in roster
            }
            for name in roster:
                crop_similarity = SequenceMatcher(
                    None, observation.recognized, name
                ).ratio()
                page_similarity = SequenceMatcher(None, slot.raw, name).ratio()
                visual_rank = visual_ranks.get(name)
                visual_support = (
                    0.22 if visual_rank == 1 else 0.07 if visual_rank == 2 else 0.0
                )
                combined = min(
                    1.0,
                    0.65 * crop_similarity + 0.13 * page_similarity + visual_support,
                )
                ranked.append((name, combined, crop_similarity, visual_rank))
            ranked.sort(key=lambda item: (-item[1], item[0]))
        top = ranked[0] if ranked else None
        second_score = ranked[1][1] if len(ranked) > 1 else 0.0
        surface_gap = (
            top[2] - ranked[1][2]
            if top is not None and len(ranked) > 1
            else (top[2] if top is not None else 0.0)
        )
        candidate_ok = bool(
            structure_ok
            and top is not None
            and top[3] == 1
            and top[2] >= 0.64
            and top[1] >= max(0.72, analysis.profile.minimum_name_similarity)
            and top[1] - second_score >= max(0.10, analysis.profile.minimum_name_gap)
            and surface_gap >= 0.10
        )
        if candidate_ok and top is not None:
            proposed = f"{top[0]} (?)"
            updated = TemplateDecision(
                slot,
                "visual_candidate",
                proposed,
                top[0],
                top[1],
                second_score,
                "crop_roster_similarity_gap_structure_pass",
            )
        else:
            updated = TemplateDecision(
                slot,
                "visual_uncertain",
                "(?)",
                top[0] if top is not None else None,
                top[1] if top is not None else None,
                second_score if ranked else None,
                "missing_or_ambiguous_visual_evidence",
            )
        updated_by_range[(slot.start, slot.end)] = updated
        visual_audit.append(
            {
                "slot_index": slot_index,
                "section_id": slot.section_id,
                "row_index": slot.row_index,
                "column_id": slot.column_id,
                "bbox_normalized": (
                    list(observation.normalized_bbox)
                    if observation is not None
                    and observation.normalized_bbox is not None
                    else None
                ),
                "candidate_accepted": candidate_ok,
                "top_score": round(top[1], 3) if top is not None else None,
                "second_score": round(second_score, 3) if ranked else None,
                "surface_similarity": round(top[2], 3) if top is not None else None,
                "reason": updated.reason,
            }
        )

    decisions = tuple(
        updated_by_range.get((item.slot.start, item.slot.end), item)
        for item in analysis.decisions
    )
    replacements = [
        (item.slot.start, item.slot.end, item.proposed)
        for item in decisions
        if item.proposed != item.slot.raw
        and item.action
        in {"visual_candidate", "visual_uncertain", "context_correction"}
    ]
    # Rebuild from the immutable OCR so older text-only name candidates cannot
    # leak into the coordinate-based result.
    raw_text = analysis.source_text or analysis.proposed_draft
    for start, end, value in sorted(replacements, reverse=True):
        raw_text = f"{raw_text[:start]}{value}{raw_text[end:]}"
    audit = dict(analysis.audit_metadata)
    audit["visual_name_pass"] = {
        "version": "coordinate-name-slots-1.0.0",
        "observation_count": len(observations),
        "accepted_count": sum(1 for item in visual_audit if item["candidate_accepted"]),
        "slots": visual_audit,
    }
    return replace(
        analysis,
        decisions=decisions,
        proposed_draft=raw_text,
        audit_metadata=audit,
    )


def serialize_template_audit(analysis: TemplateAnalysis) -> list[dict[str, Any]]:
    """Return metadata suitable for the existing suggestion_details JSON field."""

    details: list[dict[str, Any]] = [dict(analysis.audit_metadata)]
    for decision in analysis.decisions:
        details.append(
            {
                "kind": "template_slot_decision",
                "profile_id": analysis.profile.profile_id,
                "profile_version": analysis.profile.profile_version,
                "rule_version": analysis.profile.rule_version,
                "section_id": decision.slot.section_id,
                "table_id": decision.slot.table_id,
                "row_index": decision.slot.row_index,
                "column_id": decision.slot.column_id,
                "slot_role": decision.slot.role,
                "line_index": decision.slot.line_index,
                "action": decision.action,
                "reason": decision.reason,
                "top_score": round(decision.top_score, 3)
                if decision.top_score is not None
                else None,
                "second_score": round(decision.second_score, 3)
                if decision.second_score is not None
                else None,
                "auto_apply_allowed": False,
            }
        )
    return details
