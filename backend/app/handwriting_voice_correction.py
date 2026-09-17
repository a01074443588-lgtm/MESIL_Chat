"""Read-only alignment for handwriting OCR and staff voice correction hints.

The comparison remains side-effect free. Explicit staff approvals are stored
through a separate append-only contract so OCR/STT evidence never becomes an
official record or a learning example merely because it was compared here.
"""

from __future__ import annotations

from difflib import SequenceMatcher
import hashlib
import json
import re
from typing import Any, Callable, Literal

from .handwriting_evidence import (
    normalize_spoken_numbers, source_rows, standardize_exact,
    validate_lexicon, voice_document,
)


_SPACE = re.compile(r"\s+")
_SEGMENT = re.compile(r"(?:\r?\n)+|(?<=[.!?])\s+")
_DATE = re.compile(r"\b\d{1,2}\s*월\s*\d{1,2}\s*일\b")
_TIME = re.compile(
    r"(?:(?:오전|오후)\s*)?\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?|"
    r"(?:오전|오후)\s*\d{1,2}\s*분"
)
_QUANTITY = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mL|ml|㎖)(?![A-Za-z0-9])|"
    r"\b\d+\s*/\s*\d+\b|\b\d+\s*분의\s*\d+\b",
    re.IGNORECASE,
)
_COMPLETION = re.compile(r"(?:섭취\s*)?완료|미완료|확인\s*완료")
_NEGATION = re.compile(
    r"(?:없|않|못)(?:음|다|았|었|함|함\.)|아님|미확인|미완료"
)
_NAME_CLAIM = re.compile(r"(?P<value>[가-힣]{2,4})\s*어르신")
_MEDICATION_CLAIM = re.compile(
    r"(?P<value>[가-힣A-Za-z0-9-]{2,20}(?:정|캡슐|시럽|약))"
)
_DIAGNOSIS_CLAIM = re.compile(
    r"(?P<value>[가-힣A-Za-z0-9-]{2,20})\s*(?:진단|진단받)"
)
_CLOCK_HOUR = re.compile(r"(?:(오전|오후)\s*)?(\d{1,2})\s*시")
_BROKEN_CONTEXT = (
    re.compile(r"확인\s*여자"),
    re.compile(r"(?:예정|확인|제공)\s*(?:예정|확인|제공){2,}"),
)
_FACT_CATEGORIES = (
    "date",
    "time",
    "quantity",
    "unit",
    "follow_up",
    "completion",
    "negation",
)
CorrectionMode = Literal["full_reading", "partial_correction", "story_hint"]
AiRefiner = Callable[..., dict[str, Any] | None]


def _normalized(value: str) -> str:
    normalized = _SPACE.sub("", value).lower().replace("㎖", "ml")
    return re.sub(
        r"(?P<denominator>\d+)분의(?P<numerator>\d+)",
        lambda match: f"{match.group('numerator')}/{match.group('denominator')}",
        normalized,
    )


def _segments(value: str) -> list[str]:
    return [item.strip() for item in _SEGMENT.split(value) if item.strip()]


def correction_sentences(value: str) -> list[str]:
    """Return stable review sentences used by both comparison and approval."""

    return _segments(value)


def _unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        key = _normalized(item)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _facts(value: str) -> dict[str, list[str]]:
    segments = _segments(value)
    quantities = _unique([match.group(0) for match in _QUANTITY.finditer(value)])
    completion_values = []
    for match in _COMPLETION.finditer(value):
        completion_values.append(
            "미완료" if "미완료" in match.group(0) else "완료"
        )
    units = _unique(
        ["mL" for _item in quantities]
    )
    return {
        "date": _unique([match.group(0) for match in _DATE.finditer(value)]),
        "time": _unique([match.group(0) for match in _TIME.finditer(value)]),
        "quantity": quantities,
        "unit": units,
        "follow_up": _unique(
            [
                item
                for item in segments
                if "다시 확인" in item or "재확인" in item or "확인 예정" in item
            ]
        ),
        "completion": _unique(completion_values),
        "negation": _unique([match.group(0) for match in _NEGATION.finditer(value)]),
    }


def _aligned_segments(initial_ocr: str, transcript: str) -> list[dict[str, Any]]:
    image_segments = _segments(initial_ocr)
    audio_segments = _segments(transcript)
    unmatched_audio = set(range(len(audio_segments)))
    rows: list[dict[str, Any]] = []
    for image_index, image_text in enumerate(image_segments, start=1):
        best_index: int | None = None
        best_ratio = 0.0
        for audio_index in unmatched_audio:
            ratio = SequenceMatcher(
                None,
                _normalized(image_text),
                _normalized(audio_segments[audio_index]),
            ).ratio()
            if ratio > best_ratio:
                best_index = audio_index
                best_ratio = ratio
        if best_index is None or best_ratio < 0.2:
            rows.append(
                {
                    "row_no": len(rows) + 1,
                    "image_text": image_text,
                    "audio_text": None,
                    "status": "image_only",
                    "similarity": round(best_ratio, 4),
                }
            )
            continue
        unmatched_audio.remove(best_index)
        rows.append(
            {
                "row_no": len(rows) + 1,
                "image_text": image_text,
                "audio_text": audio_segments[best_index],
                "status": "same" if best_ratio >= 0.85 else "different",
                "similarity": round(best_ratio, 4),
            }
        )
    for audio_index in sorted(unmatched_audio):
        rows.append(
            {
                "row_no": len(rows) + 1,
                "image_text": None,
                "audio_text": audio_segments[audio_index],
                "status": "audio_only",
                "similarity": 0.0,
            }
        )
    return rows


def _fact_rows(initial_ocr: str, transcript: str) -> list[dict[str, Any]]:
    image_facts = _facts(initial_ocr)
    audio_facts = _facts(transcript)
    rows: list[dict[str, Any]] = []
    for category in _FACT_CATEGORIES:
        image_values = image_facts[category]
        audio_values = audio_facts[category]
        image_keys = {_normalized(item) for item in image_values}
        audio_keys = {_normalized(item) for item in audio_values}
        if image_keys and audio_keys and image_keys == audio_keys:
            status = "same"
        elif image_keys and audio_keys:
            status = "different"
        elif image_keys:
            status = "image_only"
        elif audio_keys:
            status = "audio_only"
        else:
            status = "not_found"
        rows.append(
            {
                "category": category,
                "image_values": image_values,
                "audio_values": audio_values,
                "status": status,
                "requires_staff_confirmation": status not in {"same", "not_found"},
            }
        )
    return rows


def _replace_one(source: str, before: str, after: str) -> tuple[str, bool]:
    if not before or before not in source:
        return source, False
    return source.replace(before, after, 1), True


def _sensitive_claims(value: str) -> list[tuple[str, str]]:
    claims: list[tuple[str, str]] = []
    for category, pattern in (
        ("name", _NAME_CLAIM),
        ("medication", _MEDICATION_CLAIM),
        ("diagnosis", _DIAGNOSIS_CLAIM),
    ):
        for match in pattern.finditer(value):
            claims.append((category, match.group("value")))
    return claims


def _partial_correction(
    *,
    initial_ocr: str,
    transcript: str,
    image_evidence_ref: str,
    audio_evidence_ref: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply only a one-to-one value that the partial utterance explicitly says."""

    image_facts = _facts(initial_ocr)
    spoken_facts = _facts(transcript)
    suggestion = initial_ocr
    changed_fields: list[dict[str, Any]] = []
    for category in _FACT_CATEGORIES:
        proposed = spoken_facts[category]
        if not proposed:
            continue
        before = image_facts[category]
        before_keys = {_normalized(item) for item in before}
        proposed_keys = {_normalized(item) for item in proposed}
        if before_keys == proposed_keys:
            status = "unchanged"
            reason = "말한 값이 기존 OCR과 같아 변경하지 않았습니다."
        elif (
            category in {"follow_up", "completion", "negation"}
            or len(before) != 1
            or len(proposed) != 1
        ):
            status = "needs_confirmation"
            reason = (
                "어느 문장에 적용할지 또는 상태 의미를 확정할 수 없어 "
                "직원이 확인해야 합니다."
            )
        else:
            suggestion, replaced = _replace_one(suggestion, before[0], proposed[0])
            status = "proposed" if replaced else "needs_confirmation"
            reason = (
                "부분 음성에서 이 입력 항목을 명시해 해당 값만 바꿨습니다."
                if replaced
                else "기존 OCR에서 바꿀 위치를 하나로 확정하지 못했습니다."
            )
        changed_fields.append(
            {
                "category": category,
                "before_values": before,
                "proposed_values": proposed,
                "status": status,
                "reason": reason,
                "evidence_refs": [image_evidence_ref, audio_evidence_ref],
            }
        )

    for category, claimed_value in _sensitive_claims(transcript):
        if _normalized(claimed_value) in _normalized(initial_ocr):
            continue
        changed_fields.append(
            {
                "category": category,
                "before_values": [],
                "proposed_values": [claimed_value],
                "status": "blocked",
                "reason": "이미지에서 확인되지 않아 부분 음성만으로 추가하지 않았습니다.",
                "evidence_refs": [audio_evidence_ref],
            }
        )
    return suggestion, changed_fields


def _readable_ocr(value: str) -> str:
    """Improve line readability using only OCR text, without semantic completion."""

    segments = _segments(value)
    if not segments:
        return value.strip()
    sentences = [item.rstrip(". ") + "." for item in segments]
    return " ".join(sentences)


def _story_hint_suggestion(
    *,
    initial_ocr: str,
    transcript: str,
    image_evidence_ref: str,
    audio_evidence_ref: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Use an explanation as a hint while sourcing every proposed fact from OCR."""

    image_facts = _facts(initial_ocr)
    hint_facts = _facts(transcript)
    changed_fields: list[dict[str, Any]] = [
        {
            "category": "narrative",
            "before_values": _segments(initial_ocr),
            "proposed_values": [_readable_ocr(initial_ocr)],
            "status": "proposed",
            "reason": (
                "설명은 문맥 힌트로만 두고, 제안 문장은 기존 OCR 문구만 "
                "줄바꿈과 문장부호로 정리했습니다."
            ),
            "evidence_refs": [image_evidence_ref, audio_evidence_ref],
        }
    ]
    for category in _FACT_CATEGORIES:
        proposed = hint_facts[category]
        if not proposed:
            continue
        before = image_facts[category]
        before_keys = {_normalized(item) for item in before}
        unsupported = [
            item for item in proposed if _normalized(item) not in before_keys
        ]
        changed_fields.append(
            {
                "category": category,
                "before_values": before,
                "proposed_values": proposed,
                "status": "blocked" if unsupported else "unchanged",
                "reason": (
                    "설명에만 있어 새 사실로 추가하지 않았습니다."
                    if unsupported
                    else "OCR에서도 확인되는 값이며 설명만으로 변경하지 않았습니다."
                ),
                "evidence_refs": [image_evidence_ref, audio_evidence_ref],
            }
        )
    for category, claimed_value in _sensitive_claims(transcript):
        if _normalized(claimed_value) in _normalized(initial_ocr):
            continue
        changed_fields.append(
            {
                "category": category,
                "before_values": [],
                "proposed_values": [claimed_value],
                "status": "blocked",
                "reason": "설명에만 있어 새 사실로 추가하지 않았습니다.",
                "evidence_refs": [audio_evidence_ref],
            }
        )
    return _readable_ocr(initial_ocr), changed_fields


def _audio_quality_document(value: dict[str, Any] | None) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    status = source.get("status")
    if status not in {"good", "low", "unknown"}:
        status = "unknown"
    pace = source.get("reading_pace")
    if pace not in {"fast", "normal", "slow", "unknown"}:
        pace = "unknown"
    return {
        "status": status,
        "reading_pace": pace,
        "message": str(
            source.get("message")
            or (
                "음성이 작거나 불분명합니다. 원본과 다른 부분을 확인하거나 다시 녹음해 주세요."
                if status == "low"
                else "음성 전사가 준비됐습니다."
            )
        ),
        "reasons": [str(item) for item in source.get("reasons", []) if str(item)],
        "metrics": (
            dict(source.get("metrics") or {})
            if isinstance(source.get("metrics"), dict)
            else {}
        ),
    }


def _text_quality_penalty(value: str) -> int:
    """Detect only obvious transcript/OCR breakage; never infer care facts."""

    penalty = sum(bool(pattern.search(value)) for pattern in _BROKEN_CONTEXT) * 3
    for meridiem, raw_hour in _CLOCK_HOUR.findall(value):
        hour = int(raw_hour)
        if hour > 23 or (meridiem and hour > 12):
            penalty += 5
    tokens = re.findall(r"[가-힣A-Za-z0-9]+", value)
    if tokens:
        repeated = max(tokens.count(token) for token in set(tokens))
        if repeated >= 4:
            penalty += repeated - 3
    return penalty


def _segment_has_fact_conflict(
    image_text: str,
    audio_text: str,
) -> bool:
    for row in _fact_rows(image_text, audio_text):
        if row["status"] == "different":
            return True
    image_claims = {(category, _normalized(value)) for category, value in _sensitive_claims(image_text)}
    audio_claims = {(category, _normalized(value)) for category, value in _sensitive_claims(audio_text)}
    return bool(image_claims and audio_claims and image_claims != audio_claims)


def _replace_scalar_conflicts(
    suggestion: str,
    facts: list[dict[str, Any]],
) -> str:
    # Keep the best source-backed sentence intact. Conflicting scalar values
    # remain in critical_facts/changed_fields for staff confirmation instead of
    # degrading the final sentence with a generic placeholder.
    return suggestion


def _contains_review_placeholder(value: str) -> bool:
    return bool(
        re.search(
            r"\[[^\]\n]*(?:확인\s*필요|확인\s*항목)[^\]\n]*\]",
            value,
        )
    )


def _candidate_from_supported_evidence(
    *,
    candidate: str,
    initial_ocr: str,
    transcript: str,
    facts: list[dict[str, Any]],
) -> str | None:
    """Reject unsupported facts and keep conflicting scalar values unresolved."""

    result = candidate.strip()
    if not result or len(result) > max(12000, (len(initial_ocr) + len(transcript)) * 3):
        return None
    if _contains_review_placeholder(result):
        return None

    source = normalize_spoken_numbers(f"{initial_ocr}\n{transcript}")
    source_numbers = set(re.findall(r"\d+(?:\.\d+)?", source))
    candidate_numbers = set(re.findall(r"\d+(?:\.\d+)?", result))
    if candidate_numbers - source_numbers:
        return None

    source_claims = {
        (category, _normalized(value))
        for category, value in _sensitive_claims(source)
    }
    candidate_claims = {
        (category, _normalized(value))
        for category, value in _sensitive_claims(result)
    }
    if candidate_claims - source_claims:
        return None

    candidate_facts = _facts(result)
    source_facts = _facts(source)
    for category in ("date", "time", "quantity", "completion", "negation"):
        supported = {_normalized(item) for item in source_facts[category]}
        proposed = {_normalized(item) for item in candidate_facts[category]}
        if proposed - supported:
            return None

    result = _replace_scalar_conflicts(result, facts)
    return result.strip() or None


def _full_reading_suggestion(
    *,
    initial_ocr: str,
    transcript: str,
    alignments: list[dict[str, Any]],
    facts: list[dict[str, Any]],
    audio_quality: dict[str, Any],
    image_evidence_ref: str,
    audio_evidence_ref: str,
) -> tuple[str, list[dict[str, Any]]]:
    """Fuse image and voice evidence without treating either as ground truth."""

    selected_segments: list[str] = []
    low_quality = audio_quality["status"] == "low"
    voice_obviously_broken = (
        _text_quality_penalty(transcript) > _text_quality_penalty(initial_ocr)
    )
    for row in alignments:
        image_text = row["image_text"]
        audio_text = row["audio_text"]
        if image_text is None:
            # Reading-only text is not added as a new care fact.
            continue
        if audio_text is None or low_quality:
            selected_segments.append(image_text)
            continue
        if _normalized(image_text) == _normalized(audio_text):
            selected_segments.append(image_text)
            continue
        image_penalty = _text_quality_penalty(image_text)
        audio_penalty = _text_quality_penalty(audio_text)
        audio_is_better_supported = (
            row["similarity"] >= 0.45
            and not _segment_has_fact_conflict(image_text, audio_text)
            and audio_penalty < image_penalty
        )
        selected_segments.append(audio_text if audio_is_better_supported else image_text)

    suggestion = "\n".join(selected_segments).strip() or initial_ocr
    if low_quality:
        suggestion = initial_ocr
    if not low_quality and not voice_obviously_broken:
        # The full reading supplies content; OCR supplies document layout.
        # A low-quality or unrelated reading retains the original OCR path.
        natural_voice = voice_document(initial_ocr, transcript)
        if natural_voice is not None:
            suggestion = natural_voice
    if not low_quality:
        suggestion = "\n".join(standardize_exact(row) for row in suggestion.splitlines())
    if not low_quality and not voice_obviously_broken:
        suggestion = _replace_scalar_conflicts(suggestion, facts)
    changed_fields = [
        {
            "category": row["category"],
            "before_values": row["image_values"],
            "proposed_values": row["audio_values"],
            "status": (
                "unchanged"
                if row["status"] in {"same", "not_found"}
                else "needs_confirmation"
            ),
            "reason": (
                "OCR과 전체 읽기 음성이 같습니다."
                if row["status"] == "same"
                else (
                    "음성 품질이 낮아 OCR을 유지하고 직원 확인을 기다립니다."
                    if low_quality or voice_obviously_broken
                    else "OCR·음성·문맥을 함께 비교했으며 다른 값은 자동 확정하지 않았습니다."
                )
            ),
            "evidence_refs": [image_evidence_ref, audio_evidence_ref],
        }
        for row in facts
        if row["status"] != "not_found"
    ]
    for category, claimed_value in _sensitive_claims(transcript):
        if _normalized(claimed_value) in _normalized(initial_ocr):
            continue
        changed_fields.append(
            {
                "category": category,
                "before_values": [],
                "proposed_values": [claimed_value],
                "status": "blocked",
                "reason": "음성에만 있는 민감 사실은 최종문에 새로 추가하지 않았습니다.",
                "evidence_refs": [audio_evidence_ref],
            }
        )
    return suggestion, changed_fields


def build_review_items(initial_ocr: str, transcript: str, facts: list[dict]) -> list[dict]:
    rows = source_rows(initial_ocr, transcript)
    items = []
    review_facts = list(facts)
    for category in ("name", "medication", "diagnosis"):
        left = _unique([v for c, v in _sensitive_claims(initial_ocr) if c == category])
        right = _unique([v for c, v in _sensitive_claims(transcript) if c == category])
        if set(left) != set(right):
            review_facts.append({"category": category, "image_values": left, "audio_values": right,
                                 "requires_staff_confirmation": True})
    for fact in review_facts:
        if not fact["requires_staff_confirmation"]:
            continue
        left, right = fact["image_values"], fact["audio_values"]
        refs = []
        for source, values in (("ocr", left), ("whisper", right)):
            for number, raw in enumerate(rows[source], 1):
                if any(_normalized(normalize_spoken_numbers(v)) in _normalized(normalize_spoken_numbers(raw)) for v in values):
                    refs.append({"source": source, "source_row": number, "text": raw})
        identity = json.dumps([fact["category"], refs, left, right], ensure_ascii=False, sort_keys=True)
        items.append({"review_item_id": hashlib.sha256(identity.encode()).hexdigest()[:24],
                      "category": fact["category"], "kind": "conflict" if left and right else "one_sided_critical",
                      "ocr_values": left, "whisper_values": right, "source_rows": refs})
    return items


def validate_review_resolutions(items: list[dict], resolutions: list[dict]) -> list[dict]:
    """Recomputed source choices only; no AI call and no blanket-confirm bypass."""
    by_id = {item["review_item_id"]: item for item in items}
    if len(resolutions) != len(by_id):
        raise ValueError("중요 항목을 각각 확인해 주세요.")
    checked, seen = [], set()
    for resolution in resolutions:
        key, choice, value = (resolution.get(k) for k in ("review_item_id", "choice", "value"))
        if key not in by_id or key in seen or not isinstance(value, str) or not value.strip():
            raise ValueError("확인 항목 또는 직원 입력값이 현재 근거와 다릅니다.")
        seen.add(key)
        if choice in {"ocr", "whisper"}:
            allowed = by_id[key]["ocr_values" if choice == "ocr" else "whisper_values"]
            if value != " · ".join(allowed) or not allowed:
                raise ValueError("선택한 값이 현재 원문 근거와 다릅니다.")
        elif choice != "staff_manual":
            raise ValueError("허용되지 않은 확인 방법입니다.")
        checked.append({"review_item_id": key, "choice": choice, "value": value.strip()})
    return checked


def build_handwriting_voice_comparison(
    *,
    initial_ocr: str,
    transcript: str,
    image_evidence_ref: str,
    audio_evidence_ref: str,
    image_provider: str,
    image_model: str,
    audio_provider: str,
    audio_model: str,
    audio_quality: dict[str, Any] | None = None,
    mode: CorrectionMode = "full_reading",
    ai_refiner: AiRefiner | None = None,
) -> dict[str, Any]:
    """Build an approval-pending comparison without writing any record."""

    raw_ocr = initial_ocr.strip()
    raw_transcript = transcript.strip()
    if not raw_ocr:
        raise ValueError("비교할 최초 OCR 결과가 없습니다.")
    if not raw_transcript:
        raise ValueError("비교할 음성 전사 결과가 없습니다.")

    if mode not in {"full_reading", "partial_correction", "story_hint"}:
        raise ValueError("지원하지 않는 손글씨 교정 방식입니다.")

    alignments = _aligned_segments(raw_ocr, raw_transcript)
    facts = _fact_rows(normalize_spoken_numbers(raw_ocr), normalize_spoken_numbers(raw_transcript))
    quality = _audio_quality_document(audio_quality)
    different_count = sum(
        row["status"] != "same" for row in alignments
    )
    critical_review_count = sum(
        row["requires_staff_confirmation"] for row in facts
    )
    if mode == "partial_correction":
        suggestion, changed_fields = _partial_correction(
            initial_ocr=raw_ocr,
            transcript=raw_transcript,
            image_evidence_ref=image_evidence_ref,
            audio_evidence_ref=audio_evidence_ref,
        )
    elif mode == "story_hint":
        suggestion, changed_fields = _story_hint_suggestion(
            initial_ocr=raw_ocr,
            transcript=raw_transcript,
            image_evidence_ref=image_evidence_ref,
            audio_evidence_ref=audio_evidence_ref,
        )
    else:
        suggestion, changed_fields = _full_reading_suggestion(
            initial_ocr=raw_ocr,
            transcript=raw_transcript,
            alignments=alignments,
            facts=facts,
            audio_quality=quality,
            image_evidence_ref=image_evidence_ref,
            audio_evidence_ref=audio_evidence_ref,
        )

    suggestion_provider = "local_evidence_fusion"
    suggestion_model = "handwriting_voice_correction_v2"
    rows = source_rows(raw_ocr, raw_transcript)
    all_rows = {source: list(range(1, len(values) + 1)) for source, values in rows.items()}
    lexicon_applications = []
    try:
        lexicon_applications, _ = validate_lexicon(suggestion, rows, all_rows, [])
    except ValueError:
        suggestion = raw_ocr
    combination_status = "not_requested"
    if mode == "full_reading" and ai_refiner is not None:
        combination_status = "provider_unavailable"
        try:
            refined = ai_refiner(
                initial_ocr=raw_ocr,
                transcript=raw_transcript,
                deterministic_suggestion=suggestion,
                alignments=alignments,
                audio_quality=quality,
                mode=mode,
            )
        except (OSError, TimeoutError, TypeError, ValueError):
            refined = None
        if isinstance(refined, dict):
            combination_status = "evidence_validation_failed"
            validated = None
            if refined.get("status") in {"provider_unavailable", "invalid_response"}:
                combination_status = refined["status"]
            else:
                try:
                    applications, selected = validate_lexicon(
                        str(refined.get("final_text") or ""), rows,
                        refined.get("source_rows"), refined.get("lexicon_applications", []),
                    )
                    validated = _candidate_from_supported_evidence(
                        candidate=str(refined.get("final_text") or ""),
                        initial_ocr="\n".join(selected["ocr"]),
                        transcript="\n".join(selected["whisper"]), facts=facts,
                    )
                    if quality["status"] == "low" and validated != raw_ocr:
                        validated = None
                except (ValueError, TypeError):
                    validated = None
            if validated:
                suggestion = validated
                lexicon_applications = applications
                combination_status = "applied"
                suggestion_provider = str(refined.get("provider") or "internal_text_ai")
                suggestion_model = str(refined.get("model") or "internal_text_ai")

    blocked_count = sum(row["status"] == "blocked" for row in changed_fields)
    proposed_count = sum(row["status"] == "proposed" for row in changed_fields)
    return {
        "schema_version": "handwriting_voice_correction_v1",
        "status": "awaiting_staff_approval",
        "mode": mode,
        "ai_combination": {"status": combination_status},
        "lexicon_applications": lexicon_applications,
        "review_items": build_review_items(raw_ocr, raw_transcript, facts) if mode == "full_reading" else [],
        "stages": [
            {
                "revision_no": 1,
                "stage": "initial_ocr",
                "status": "completed",
                "text": raw_ocr,
                "provider": image_provider,
                "model": image_model,
                "evidence_refs": [image_evidence_ref],
            },
            {
                "revision_no": 2,
                "stage": "audio_transcript",
                "status": "completed",
                "text": raw_transcript,
                "provider": audio_provider,
                "model": audio_model,
                "evidence_refs": [audio_evidence_ref],
            },
            {
                "revision_no": 3,
                "stage": "ai_correction_suggestion",
                "status": "needs_confirmation",
                # Voice evidence is never authoritative. The suggestion applies
                # the selected mode's conservative, staff-reviewable contract.
                "text": suggestion,
                "provider": suggestion_provider,
                "model": suggestion_model,
                "evidence_refs": [image_evidence_ref, audio_evidence_ref],
            },
            {
                "revision_no": 4,
                "stage": "staff_approved_final",
                "status": "awaiting_staff_approval",
                "text": None,
                "provider": None,
                "model": None,
                "evidence_refs": [],
            },
        ],
        "alignments": alignments,
        "critical_facts": facts,
        "changed_fields": changed_fields,
        "audio_quality": quality,
        "summary": {
            "alignment_row_count": len(alignments),
            "different_row_count": different_count,
            "critical_review_count": critical_review_count,
            "changed_field_count": len(changed_fields),
            "proposed_field_count": proposed_count,
            "blocked_field_count": blocked_count,
        },
        "safety": {
            "saved_before_approval": False,
            "official_record_written": False,
            "training_candidate_created": False,
            "external_transfer": False,
            "direct_typing_supported": True,
            "unsupported_fact_generation": False,
            "unmentioned_fields_unchanged": True,
            "story_hint_is_not_ground_truth": True,
        },
    }
