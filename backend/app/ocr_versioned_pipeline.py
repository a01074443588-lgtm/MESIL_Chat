from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ocr_template_engine import (
    TemplateAnalysis,
    TemplateDecision,
    VisualNameObservation,
    analyze_combined_checklist,
    apply_visual_name_observations,
)

CATALOG_PATH = Path(__file__).with_name("data") / "ocr_pipeline_catalog.v1.json"
NAME_CANDIDATE_SUFFIX = " (?)"
UNCERTAIN_NAME = "(?)"


@dataclass(frozen=True)
class FrozenContentOcr:
    """Immutable hand-off from content OCR to a separately versioned name stage."""

    text: str
    stage_id: str = "full-page-content-ocr"
    stage_version: str = "1.0.0"

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class NameSlotOutput:
    section_id: str
    line_index: int
    row_index: int
    column_id: str
    original: str
    output: str
    candidate: str | None
    action: str


@dataclass(frozen=True)
class StatusSlotOutput:
    section_id: str
    line_index: int
    row_index: int
    column_id: str
    original: str
    output: str
    action: str
    reason: str


@dataclass(frozen=True)
class VersionedNameRun:
    content: FrozenContentOcr
    name_draft: str
    catalog_version: str
    profile_id: str | None
    profile_version: str | None
    rule_version: str | None
    matched: bool
    fallback_used: bool
    slot_outputs: tuple[NameSlotOutput, ...]
    safety: dict[str, int | bool | str]


@dataclass(frozen=True)
class VersionedStructuredRun:
    """One immutable content OCR plus independently versioned slot drafts."""

    content: FrozenContentOcr
    suggested_draft: str
    catalog_version: str
    profile_id: str | None
    profile_version: str | None
    rule_version: str | None
    matched: bool
    fallback_used: bool
    name_outputs: tuple[NameSlotOutput, ...]
    status_outputs: tuple[StatusSlotOutput, ...]
    safety: dict[str, int | bool | str]


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    content_text: str
    roster: tuple[str, ...]
    expected_names: tuple[str, ...]
    observations: tuple[VisualNameObservation, ...] = ()


@dataclass(frozen=True)
class EvaluationMetrics:
    case_count: int
    expected_name_slots: int
    baseline_exact_name_slots: int
    exact_name_slots: int
    exact_name_slot_delta: int
    regressed_cases: int
    wrong_substitutions: int
    outside_roster_candidates: int
    non_name_false_positives: int
    non_name_text_changes: int

    @property
    def passes_regression_gate(self) -> bool:
        return bool(
            self.regressed_cases == 0
            and self.wrong_substitutions == 0
            and self.outside_roster_candidates == 0
            and self.non_name_false_positives == 0
            and self.non_name_text_changes == 0
            and self.exact_name_slot_delta >= 0
        )


def load_pipeline_catalog() -> dict[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def _normalized_roster(names: Sequence[str]) -> frozenset[str]:
    normalized = {
        re.sub(r"\s*\((?:가명|시험[^)]*)\)\s*$", "", name).replace(" ", "").strip()
        for name in names
    }
    return frozenset(name for name in normalized if re.fullmatch(r"[가-힣]{2,4}", name))


def _catalog_profile(profile_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    catalog = load_pipeline_catalog()
    profile = next(
        (item for item in catalog["name_profiles"] if item["profile_id"] == profile_id),
        None,
    )
    if profile is None:
        raise ValueError(f"Unknown OCR name profile: {profile_id}")
    return catalog, profile


def _name_output(decision: TemplateDecision, roster: frozenset[str]) -> str:
    if decision.slot.raw in roster:
        return decision.slot.raw
    if decision.candidate in roster and decision.action in {
        "candidate",
        "visual_candidate",
    }:
        return f"{decision.candidate}{NAME_CANDIDATE_SUFFIX}"
    return UNCERTAIN_NAME


def _render_name_slots_only(
    content_text: str,
    analysis: TemplateAnalysis,
    roster: frozenset[str],
) -> tuple[str, tuple[NameSlotOutput, ...], bool]:
    """Render only confirmed name-slot ranges; every other character is immutable."""

    if not analysis.matched:
        return content_text, (), True
    decisions = sorted(
        (item for item in analysis.decisions if item.slot.role == "name"),
        key=lambda item: (item.slot.start, item.slot.end),
    )
    cursor = 0
    rendered: list[str] = []
    untouched_source: list[str] = []
    untouched_output: list[str] = []
    outputs: list[NameSlotOutput] = []
    for decision in decisions:
        slot = decision.slot
        if slot.start < cursor or content_text[slot.start : slot.end] != slot.raw:
            raise ValueError("Name slot ranges do not match the immutable content OCR")
        untouched = content_text[cursor : slot.start]
        rendered.append(untouched)
        untouched_source.append(untouched)
        untouched_output.append(untouched)
        output = _name_output(decision, roster)
        rendered.append(output)
        outputs.append(
            NameSlotOutput(
                section_id=slot.section_id,
                line_index=slot.line_index,
                row_index=slot.row_index,
                column_id=slot.column_id,
                original=slot.raw,
                output=output,
                candidate=decision.candidate if decision.candidate in roster else None,
                action=decision.action,
            )
        )
        cursor = slot.end
    tail = content_text[cursor:]
    rendered.append(tail)
    untouched_source.append(tail)
    untouched_output.append(tail)
    return (
        "".join(rendered),
        tuple(outputs),
        "".join(untouched_source) == "".join(untouched_output),
    )


def _render_confirmed_name_and_status_slots(
    content_text: str,
    analysis: TemplateAnalysis,
    roster: frozenset[str],
) -> tuple[
    str,
    tuple[NameSlotOutput, ...],
    tuple[StatusSlotOutput, ...],
    bool,
]:
    """Render only template-confirmed name/status ranges from immutable OCR."""

    if not analysis.matched:
        return content_text, (), (), True
    decisions = sorted(
        (
            item
            for item in analysis.decisions
            if item.slot.role in {"name", "status"}
        ),
        key=lambda item: (item.slot.start, item.slot.end),
    )
    cursor = 0
    rendered: list[str] = []
    name_outputs: list[NameSlotOutput] = []
    status_outputs: list[StatusSlotOutput] = []
    for decision in decisions:
        slot = decision.slot
        if slot.start < cursor or content_text[slot.start : slot.end] != slot.raw:
            raise ValueError("Structured slot ranges do not match immutable content OCR")
        rendered.append(content_text[cursor : slot.start])
        if slot.role == "name":
            output = _name_output(decision, roster)
            name_outputs.append(
                NameSlotOutput(
                    section_id=slot.section_id,
                    line_index=slot.line_index,
                    row_index=slot.row_index,
                    column_id=slot.column_id,
                    original=slot.raw,
                    output=output,
                    candidate=(
                        decision.candidate if decision.candidate in roster else None
                    ),
                    action=decision.action,
                )
            )
        else:
            output = decision.proposed
            status_outputs.append(
                StatusSlotOutput(
                    section_id=slot.section_id,
                    line_index=slot.line_index,
                    row_index=slot.row_index,
                    column_id=slot.column_id,
                    original=slot.raw,
                    output=output,
                    action=decision.action,
                    reason=decision.reason,
                )
            )
        rendered.append(output)
        cursor = slot.end
    rendered.append(content_text[cursor:])
    return "".join(rendered), tuple(name_outputs), tuple(status_outputs), True


def run_versioned_name_stage(
    content: FrozenContentOcr,
    *,
    active_roster_names: Sequence[str],
    profile_id: str = "silvermedical.combined_daily_checklist",
    confirmed_history: Sequence[Mapping[str, object]] = (),
    observations: Sequence[VisualNameObservation] = (),
) -> VersionedNameRun:
    """Run an additive name profile without changing content OCR or persistence."""

    catalog, catalog_profile = _catalog_profile(profile_id)
    roster = _normalized_roster(active_roster_names)
    analysis = analyze_combined_checklist(
        content.text,
        active_roster_names=tuple(roster),
        confirmed_history=confirmed_history,
    )
    if observations and analysis.matched:
        analysis = apply_visual_name_observations(
            analysis,
            active_roster_names=tuple(roster),
            observations=observations,
        )
    draft, slot_outputs, non_name_preserved = _render_name_slots_only(
        content.text, analysis, roster
    )
    outside = sum(
        1
        for item in slot_outputs
        if item.candidate is not None and item.candidate not in roster
    )
    return VersionedNameRun(
        content=content,
        name_draft=draft,
        catalog_version=str(catalog["catalog_version"]),
        profile_id=profile_id if analysis.matched else None,
        profile_version=(
            str(catalog_profile["profile_version"]) if analysis.matched else None
        ),
        rule_version=(
            str(catalog_profile["rule_version"]) if analysis.matched else None
        ),
        matched=analysis.matched,
        fallback_used=not analysis.matched,
        slot_outputs=slot_outputs,
        safety={
            "content_sha256": content.sha256,
            "non_name_text_preserved": non_name_preserved,
            "outside_roster_candidate_count": outside,
            "name_slot_count": len(slot_outputs),
        },
    )


def run_versioned_structured_stage(
    content: FrozenContentOcr,
    *,
    active_roster_names: Sequence[str],
    profile_id: str = "silvermedical.combined_daily_checklist",
    confirmed_history: Sequence[Mapping[str, object]] = (),
    observations: Sequence[VisualNameObservation] = (),
) -> VersionedStructuredRun:
    """Render only confirmed name and status slots; keep all other text byte-identical."""

    catalog, catalog_profile = _catalog_profile(profile_id)
    roster = _normalized_roster(active_roster_names)
    analysis = analyze_combined_checklist(
        content.text,
        active_roster_names=tuple(roster),
        confirmed_history=confirmed_history,
    )
    if observations and analysis.matched:
        analysis = apply_visual_name_observations(
            analysis,
            active_roster_names=tuple(roster),
            observations=observations,
        )
    draft, name_outputs, status_outputs, outside_preserved = (
        _render_confirmed_name_and_status_slots(content.text, analysis, roster)
    )
    outside = sum(
        1
        for item in name_outputs
        if item.candidate is not None and item.candidate not in roster
    )
    return VersionedStructuredRun(
        content=content,
        suggested_draft=draft,
        catalog_version=str(catalog["catalog_version"]),
        profile_id=profile_id if analysis.matched else None,
        profile_version=(
            str(catalog_profile["profile_version"]) if analysis.matched else None
        ),
        rule_version=(
            str(catalog_profile["rule_version"]) if analysis.matched else None
        ),
        matched=analysis.matched,
        fallback_used=not analysis.matched,
        name_outputs=name_outputs,
        status_outputs=status_outputs,
        safety={
            "content_sha256": content.sha256,
            "outside_structured_slots_preserved": outside_preserved,
            "outside_roster_candidate_count": outside,
            "name_slot_count": len(name_outputs),
            "status_slot_count": len(status_outputs),
            "status_correction_count": sum(
                item.output != item.original for item in status_outputs
            ),
        },
    )


def serialize_versioned_structured_run(
    run: VersionedStructuredRun,
) -> list[dict[str, Any]]:
    """Serialize slot decisions without exposing OCR text or resident names."""

    details: list[dict[str, Any]] = [
        {
            "kind": "versioned_ocr_pipeline",
            "catalog_version": run.catalog_version,
            "content_stage_id": run.content.stage_id,
            "content_stage_version": run.content.stage_version,
            "content_sha256": run.content.sha256,
            "profile_id": run.profile_id,
            "profile_version": run.profile_version,
            "rule_version": run.rule_version,
            "status_stage_id": "confirmed-status-slot-correction",
            "status_stage_version": "1.0.0",
            "matched": run.matched,
            "fallback_used": run.fallback_used,
            "outside_structured_slots_preserved": run.safety[
                "outside_structured_slots_preserved"
            ],
            "outside_roster_candidate_count": run.safety[
                "outside_roster_candidate_count"
            ],
            "name_slot_count": run.safety["name_slot_count"],
            "status_slot_count": run.safety["status_slot_count"],
            "status_correction_count": run.safety["status_correction_count"],
            "auto_apply_allowed": False,
        }
    ]
    for item in run.name_outputs:
        details.append(
            {
                "kind": "versioned_name_slot",
                "profile_id": run.profile_id,
                "profile_version": run.profile_version,
                "rule_version": run.rule_version,
                "section_id": item.section_id,
                "line_index": item.line_index,
                "row_index": item.row_index,
                "column_id": item.column_id,
                "action": item.action,
                "output_kind": (
                    "uncertain"
                    if item.output == UNCERTAIN_NAME
                    else "candidate"
                    if item.output.endswith(NAME_CANDIDATE_SUFFIX)
                    else "exact"
                ),
                "auto_apply_allowed": False,
            }
        )
    for item in run.status_outputs:
        details.append(
            {
                "kind": "versioned_status_slot",
                "profile_id": run.profile_id,
                "profile_version": run.profile_version,
                "rule_version": run.rule_version,
                "section_id": item.section_id,
                "line_index": item.line_index,
                "row_index": item.row_index,
                "column_id": item.column_id,
                "action": item.action,
                "reason": item.reason,
                "changed": item.output != item.original,
                "auto_apply_allowed": False,
            }
        )
    return details


def serialize_versioned_name_run(run: VersionedNameRun) -> list[dict[str, Any]]:
    """Serialize versions and decisions without copying names or source text."""

    details: list[dict[str, Any]] = [
        {
            "kind": "versioned_ocr_pipeline",
            "catalog_version": run.catalog_version,
            "content_stage_id": run.content.stage_id,
            "content_stage_version": run.content.stage_version,
            "content_sha256": run.content.sha256,
            "profile_id": run.profile_id,
            "profile_version": run.profile_version,
            "rule_version": run.rule_version,
            "matched": run.matched,
            "fallback_used": run.fallback_used,
            "non_name_text_preserved": run.safety["non_name_text_preserved"],
            "outside_roster_candidate_count": run.safety[
                "outside_roster_candidate_count"
            ],
            "name_slot_count": run.safety["name_slot_count"],
            "auto_apply_allowed": False,
        }
    ]
    for item in run.slot_outputs:
        details.append(
            {
                "kind": "versioned_name_slot",
                "profile_id": run.profile_id,
                "profile_version": run.profile_version,
                "rule_version": run.rule_version,
                "section_id": item.section_id,
                "line_index": item.line_index,
                "row_index": item.row_index,
                "column_id": item.column_id,
                "action": item.action,
                "output_kind": (
                    "uncertain"
                    if item.output == UNCERTAIN_NAME
                    else "candidate"
                    if item.output.endswith(NAME_CANDIDATE_SUFFIX)
                    else "exact"
                ),
                "auto_apply_allowed": False,
            }
        )
    return details


def _output_name(value: str) -> str | None:
    if value == UNCERTAIN_NAME:
        return None
    return value.removesuffix(NAME_CANDIDATE_SUFFIX)


def evaluate_name_profile(cases: Sequence[EvaluationCase]) -> EvaluationMetrics:
    baseline_exact = exact = regressions = 0
    wrong = outside = false_positive = changed = 0
    expected_total = 0
    for case in cases:
        run = run_versioned_name_stage(
            FrozenContentOcr(case.content_text),
            active_roster_names=case.roster,
            observations=case.observations,
        )
        expected_total += len(case.expected_names)
        false_positive += max(0, len(run.slot_outputs) - len(case.expected_names))
        changed += int(not bool(run.safety["non_name_text_preserved"]))
        case_baseline_exact = 0
        case_exact = 0
        for index, item in enumerate(run.slot_outputs):
            output_name = _output_name(item.output)
            expected = (
                case.expected_names[index] if index < len(case.expected_names) else None
            )
            if item.original == expected:
                baseline_exact += 1
                case_baseline_exact += 1
            if output_name == expected:
                exact += 1
                case_exact += 1
            elif output_name is not None and output_name in case.roster:
                wrong += 1
            if output_name is not None and output_name not in case.roster:
                outside += 1
        regressions += int(case_exact < case_baseline_exact)
    return EvaluationMetrics(
        case_count=len(cases),
        expected_name_slots=expected_total,
        baseline_exact_name_slots=baseline_exact,
        exact_name_slots=exact,
        exact_name_slot_delta=exact - baseline_exact,
        regressed_cases=regressions,
        wrong_substitutions=wrong,
        outside_roster_candidates=outside,
        non_name_false_positives=false_positive,
        non_name_text_changes=changed,
    )
