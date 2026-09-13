"""Deterministic, privacy-safe handwriting correction accuracy metrics.

The evaluator never calls OCR, STT, or a language model.  It compares existing
texts only and deliberately keeps staff-approved text separate from official
care records and model-training data.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from hashlib import sha256
import json
import re
import unicodedata
from typing import Any


CRITICAL_CATEGORIES = (
    "time",
    "quantity",
    "unit",
    "name",
    "medication",
    "diagnosis",
    "completion_status",
    "negation",
    "follow_up",
)

_DATE_LINE = re.compile(r"\d{1,2}\s*월\s*\d{1,2}\s*일")
_NUMERIC_TOKEN = re.compile(r"\d+(?:[:/.]\d+)?", re.IGNORECASE)
_NAME_CLAIM = re.compile(r"(?P<value>[가-힣]{2,4})\s*어르신")
_MEDICATION_CLAIM = re.compile(
    r"(?P<value>[가-힣A-Za-z0-9-]{2,20}(?:정|캡슐|시럽|약))"
)
_DIAGNOSIS_CLAIM = re.compile(
    r"(?P<value>[가-힣A-Za-z0-9-]{2,20})\s*(?:진단|진단받)"
)


def strip_identity_header(text: str) -> str:
    """Remove an unscored identity header while preserving the first dated row."""

    clean = text.replace("\r", "\n")
    first_date_match = _DATE_LINE.search(clean)
    if first_date_match is not None:
        clean = clean[first_date_match.start() :]
    lines = [line.strip() for line in clean.splitlines() if line.strip()]
    first_date_index = next(
        (index for index, line in enumerate(lines) if _DATE_LINE.search(line)),
        None,
    )
    if first_date_index is not None:
        lines = lines[first_date_index:]
    return "\n".join(line for line in lines if "가상자료" not in line).strip()


def normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", strip_identity_header(text)).lower()
    value = value.replace("㎖", "ml")
    return "".join(re.findall(r"[0-9a-z가-힣%/]", value))


def levenshtein_distance(left: str, right: str) -> int:
    if len(left) < len(right):
        left, right = right, left
    previous = list(range(len(right) + 1))
    for row, left_char in enumerate(left, start=1):
        current = [row]
        for column, right_char in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[column] + 1,
                    previous[column - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def character_metrics(candidate: str, reference: str) -> dict[str, Any]:
    normalized_candidate = normalize_text(candidate)
    normalized_reference = normalize_text(reference)
    if not normalized_reference:
        distance = len(normalized_candidate)
        error_rate = 0.0 if not normalized_candidate else 1.0
    else:
        distance = levenshtein_distance(normalized_candidate, normalized_reference)
        error_rate = distance / len(normalized_reference)
    return {
        "reference_character_count": len(normalized_reference),
        "candidate_character_count": len(normalized_candidate),
        "edit_distance": distance,
        "character_error_rate": round(error_rate, 4),
        "character_accuracy": round(max(0.0, 1.0 - error_rate), 4),
    }


def preservation_metrics(
    candidate: str,
    preservation_terms: Mapping[str, Sequence[Sequence[str]]],
) -> dict[str, dict[str, Any]]:
    compact = normalize_text(candidate)
    categories: dict[str, dict[str, Any]] = {}
    for category in CRITICAL_CATEGORIES:
        groups = preservation_terms.get(category, ())
        checks = [
            any(normalize_text(alternative) in compact for alternative in alternatives)
            for alternatives in groups
        ]
        categories[category] = {
            "preserved": sum(checks),
            "total": len(checks),
            "rate": round(sum(checks) / len(checks), 4) if checks else None,
            "checks": checks,
        }
    return categories


def _sensitive_claims(text: str) -> set[tuple[str, str]]:
    claims: set[tuple[str, str]] = set()
    cleaned = strip_identity_header(text)
    for category, pattern in (
        ("name", _NAME_CLAIM),
        ("medication", _MEDICATION_CLAIM),
        ("diagnosis", _DIAGNOSIS_CLAIM),
    ):
        for match in pattern.finditer(cleaned):
            claims.add((category, normalize_text(match.group("value"))))
    return claims


def unsupported_fact_additions(candidate: str, reference: str) -> dict[str, Any]:
    candidate_clean = strip_identity_header(candidate)
    reference_clean = strip_identity_header(reference)
    reference_numbers = set(_NUMERIC_TOKEN.findall(reference_clean))
    unsupported_numbers = sorted(
        set(_NUMERIC_TOKEN.findall(candidate_clean)) - reference_numbers
    )
    unsupported_claims = sorted(_sensitive_claims(candidate_clean) - _sensitive_claims(reference_clean))
    return {
        "count": len(unsupported_numbers) + len(unsupported_claims),
        "unsupported_numeric_token_count": len(unsupported_numbers),
        "unsupported_sensitive_claim_count": len(unsupported_claims),
        "scope_note": (
            "구조화해 검출 가능한 숫자·이름·약명·진단 표현의 하한값이며 "
            "모든 의미상 환각을 자동 판정하지는 않습니다."
        ),
    }


def aggregate_preservation(
    preservation: Mapping[str, Mapping[str, Any]], categories: Sequence[str]
) -> dict[str, Any]:
    preserved = sum(int(preservation[name]["preserved"]) for name in categories)
    total = sum(int(preservation[name]["total"]) for name in categories)
    return {
        "preserved": preserved,
        "total": total,
        "rate": round(preserved / total, 4) if total else None,
    }


def unavailable_stage(reason: str) -> dict[str, Any]:
    return {
        "availability": "not_evaluable",
        "reason": reason,
        "character_error_rate": None,
        "character_accuracy": None,
        "critical_preservation": None,
        "unsupported_fact_additions": None,
        "missing_critical_fact_count": None,
        "delta_accuracy_vs_initial": None,
        "verdict": "평가 불가",
    }


def evaluate_candidate(
    *,
    candidate: str | None,
    reference: str,
    preservation_terms: Mapping[str, Sequence[Sequence[str]]],
    initial_accuracy: float | None = None,
    conflict_review_count: int = 0,
    incorrect_auto_confirmation_count: int = 0,
    unavailable_reason: str = "평가할 기존 결과가 없습니다.",
) -> dict[str, Any]:
    if not candidate:
        return unavailable_stage(unavailable_reason)
    characters = character_metrics(candidate, reference)
    preservation = preservation_metrics(candidate, preservation_terms)
    missing_count = sum(
        item["total"] - item["preserved"] for item in preservation.values()
    )
    additions = unsupported_fact_additions(candidate, reference)
    applicable_rates = [
        item["rate"] for item in preservation.values() if item["rate"] is not None
    ]
    all_critical_preserved = bool(applicable_rates) and all(
        rate == 1.0 for rate in applicable_rates
    )
    delta = (
        round(characters["character_accuracy"] - initial_accuracy, 4)
        if initial_accuracy is not None
        else None
    )
    if (
        characters["character_accuracy"] >= 0.95
        and all_critical_preserved
        and additions["count"] == 0
        and incorrect_auto_confirmation_count == 0
    ):
        verdict = "자동교정 후보"
    elif (
        delta is not None
        and delta > 0
        and additions["count"] == 0
        and incorrect_auto_confirmation_count == 0
    ):
        verdict = "보조교정"
    else:
        verdict = "직원확인 필수"
    return {
        "availability": "evaluated",
        **characters,
        "critical_preservation": preservation,
        "time": aggregate_preservation(preservation, ("time",)),
        "quantity_and_unit": aggregate_preservation(
            preservation, ("quantity", "unit")
        ),
        "identity_medical": aggregate_preservation(
            preservation, ("name", "medication", "diagnosis")
        ),
        "completion_and_follow_up": aggregate_preservation(
            preservation, ("completion_status", "negation", "follow_up")
        ),
        "unsupported_fact_additions": additions,
        "missing_critical_fact_count": missing_count,
        "delta_accuracy_vs_initial": delta,
        "conflict_review_count": conflict_review_count,
        "incorrect_auto_confirmation_count": incorrect_auto_confirmation_count,
        "verdict": verdict,
    }


def approval_reference_eligibility(
    *, image_case_id: str | None, audio_case_id: str | None, mode: str
) -> dict[str, Any]:
    if not image_case_id:
        return {"eligible": False, "reason": "image_case_unmapped"}
    if mode == "direct_typing":
        return {"eligible": True, "reason": "same_image_direct_typing"}
    if mode in {"full_reading", "partial_correction", "story_hint"}:
        if not audio_case_id:
            return {"eligible": False, "reason": "audio_case_unmapped"}
        if audio_case_id != image_case_id:
            return {"eligible": False, "reason": "image_audio_case_mismatch"}
        return {"eligible": True, "reason": "image_audio_same_case"}
    return {"eligible": False, "reason": "unsupported_mode"}


def staff_edit_metrics(
    *, suggestion: str | None, approved_final: str | None,
    preservation_terms: Mapping[str, Sequence[Sequence[str]]],
) -> dict[str, Any] | None:
    if not suggestion or not approved_final:
        return None
    suggestion_preservation = preservation_metrics(suggestion, preservation_terms)
    final_preservation = preservation_metrics(approved_final, preservation_terms)
    changed_critical_fields = sum(
        suggestion_preservation[name]["checks"] != final_preservation[name]["checks"]
        for name in CRITICAL_CATEGORIES
    )
    return {
        "edited_character_count": character_metrics(approved_final, suggestion)[
            "edit_distance"
        ],
        "changed_critical_field_count": changed_critical_fields,
    }


def evaluation_fingerprint(report: Mapping[str, Any]) -> str:
    stable = {
        "schema_version": report["schema_version"],
        "case_count": report["case_count"],
        "cases": report["cases"],
        "summary": report["summary"],
        "data_quality": report["data_quality"],
        "boundaries": report["boundaries"],
    }
    encoded = json.dumps(
        stable, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
