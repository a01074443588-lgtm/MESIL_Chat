"""Safe routing policy for handwritten OCR and optional voice correction.

The policy never sees ground truth.  It only decides whether a second model
call has independent evidence and enough disagreement to justify its cost.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher


_SPACE = re.compile(r"\s+")
_CONTENT_TOKEN = re.compile(r"[가-힣]{2,}|\d+(?::\d+|/\d+)?|mL|ml", re.IGNORECASE)
_CRITICAL_TOKEN = re.compile(
    r"\d+(?::\d+|/\d+)?(?:\s*(?:분|시|회|일|mL|ml))?|"
    r"완료|미완료|없(?:음|다|었)|않(?:음|다|았)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CorrectionDecision:
    request_ai_correction: bool
    reason: str
    evidence_difference: float | None
    shared_content_token_count: int
    requires_reocr_or_staff_review: bool
    parallel_ocr_stt_allowed: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _normalized(text: str) -> str:
    return _SPACE.sub("", text).lower()


def _content_tokens(text: str) -> set[str]:
    return {token.lower() for token in _CONTENT_TOKEN.findall(text)}


def _critical_tokens(text: str) -> set[str]:
    return {_normalized(token) for token in _CRITICAL_TOKEN.findall(text)}


def _has_repetition_failure(text: str) -> bool:
    if len(text) > 2500:
        return True
    lines = [line.strip() for line in text.splitlines() if len(line.strip()) >= 8]
    if len(lines) < 4:
        return False
    return len(set(lines)) / len(lines) < 0.55


def decide_multimodal_correction(
    initial_ocr: str,
    transcript: str | None,
    correction_mode: str,
    *,
    difference_threshold: float = 0.25,
) -> CorrectionDecision:
    """Return a ground-truth-free decision for an optional correction call."""

    initial = initial_ocr.strip()
    audio = (transcript or "").strip()
    has_audio_mode = correction_mode in {"full_reading", "story_hint"}
    if not initial:
        return CorrectionDecision(
            request_ai_correction=False,
            reason="initial_ocr_missing",
            evidence_difference=None,
            shared_content_token_count=0,
            requires_reocr_or_staff_review=True,
            parallel_ocr_stt_allowed=has_audio_mode,
        )
    if _has_repetition_failure(initial):
        return CorrectionDecision(
            request_ai_correction=False,
            reason="ocr_repetition_requires_reocr_or_staff_review",
            evidence_difference=None,
            shared_content_token_count=0,
            requires_reocr_or_staff_review=True,
            parallel_ocr_stt_allowed=has_audio_mode,
        )
    if not has_audio_mode:
        return CorrectionDecision(
            request_ai_correction=False,
            reason="no_independent_audio_evidence",
            evidence_difference=None,
            shared_content_token_count=0,
            requires_reocr_or_staff_review=False,
            parallel_ocr_stt_allowed=False,
        )
    if not audio:
        return CorrectionDecision(
            request_ai_correction=False,
            reason="audio_transcript_missing",
            evidence_difference=None,
            shared_content_token_count=0,
            requires_reocr_or_staff_review=True,
            parallel_ocr_stt_allowed=True,
        )

    normalized_initial = _normalized(initial)
    normalized_audio = _normalized(audio)
    similarity = SequenceMatcher(None, normalized_initial, normalized_audio).ratio()
    difference = round(1 - similarity, 4)
    shared_tokens = _content_tokens(initial) & _content_tokens(audio)
    if not shared_tokens:
        return CorrectionDecision(
            request_ai_correction=False,
            reason="audio_has_no_shared_content_evidence",
            evidence_difference=difference,
            shared_content_token_count=0,
            requires_reocr_or_staff_review=True,
            parallel_ocr_stt_allowed=True,
        )
    if correction_mode == "full_reading" and _critical_tokens(initial) != _critical_tokens(audio):
        return CorrectionDecision(
            request_ai_correction=True,
            reason="critical_image_audio_difference",
            evidence_difference=difference,
            shared_content_token_count=len(shared_tokens),
            requires_reocr_or_staff_review=False,
            parallel_ocr_stt_allowed=True,
        )
    should_correct = difference >= difference_threshold
    return CorrectionDecision(
        request_ai_correction=should_correct,
        reason=(
            "material_image_audio_difference"
            if should_correct
            else "image_audio_difference_below_threshold"
        ),
        evidence_difference=difference,
        shared_content_token_count=len(shared_tokens),
        requires_reocr_or_staff_review=False,
        parallel_ocr_stt_allowed=True,
    )
