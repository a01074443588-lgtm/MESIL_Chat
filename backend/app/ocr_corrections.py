from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from hashlib import sha256
from math import sqrt
from pathlib import Path
import re
from typing import Any
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError


TOKEN_PATTERN = re.compile(r"[가-힣]{2,}|(?:\d{1,2}:\d{2}|\d+(?:\.\d+)?)")
TIME_PATTERN = re.compile(r"(?:\d{1,2}:\d{2}|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?)")
PROTECTED_CONTENT_TYPES = {
    "resident_name",
    "name",
    "time",
    "number",
    "medication",
    "body_part",
    "incident",
}
MEDICATION_TERMS = {
    "약",
    "투약",
    "복약",
    "처방",
    "인슐린",
    "연고",
    "안약",
    "진통제",
    "해열제",
    "항생제",
}
BODY_PART_TERMS = {
    "머리",
    "얼굴",
    "눈",
    "코",
    "입",
    "목",
    "어깨",
    "가슴",
    "배",
    "등",
    "허리",
    "팔",
    "손",
    "손가락",
    "다리",
    "무릎",
    "발",
    "발가락",
    "엉덩이",
    "피부",
}
INCIDENT_TERMS = {
    "낙상",
    "욕창",
    "상처",
    "출혈",
    "멍",
    "통증",
    "골절",
    "화상",
    "실종",
    "흡인",
    "질식",
    "사고",
    "응급",
}


@dataclass(frozen=True)
class CorrectionEvidence:
    event_id: str
    recognized_text: str
    corrected_text: str
    content_type: str
    context_text: str
    source_writer_id: str | None
    visual_signature: list[float] | None


def classify_content_type(
    recognized_text: str,
    corrected_text: str,
    *,
    resident_names: list[str] | None = None,
) -> str:
    combined = f"{recognized_text} {corrected_text}"
    normalized_names = {
        name.replace(" ", "")
        for name in resident_names or []
        if name.strip()
    }
    if any(
        any(
            token.replace(" ", "") == name
            or (
                len(token.replace(" ", "")) >= 2
                and token.replace(" ", "") in name
            )
            for name in normalized_names
        )
        for token in (recognized_text, corrected_text)
    ):
        return "resident_name"
    if TIME_PATTERN.search(combined):
        return "time"
    if re.search(r"\d", combined):
        return "number"
    if any(term in combined for term in MEDICATION_TERMS):
        return "medication"
    if any(term in combined for term in BODY_PART_TERMS):
        return "body_part"
    if any(term in combined for term in INCIDENT_TERMS):
        return "incident"
    return "general"


def _context_excerpt(value: str, token: str, *, radius: int = 80) -> str:
    index = value.find(token)
    if index < 0:
        return value[: radius * 2]
    return value[max(0, index - radius) : index + len(token) + radius]


def build_correction_pairs(
    raw_text: str | None,
    corrected_text: str | None,
    *,
    resident_names: list[str] | None = None,
) -> list[dict[str, str]]:
    """Extract conservative one-to-one token edits from a confirmed review."""
    if not raw_text or not corrected_text or raw_text == corrected_text:
        return []
    original = TOKEN_PATTERN.findall(raw_text)
    corrected = TOKEN_PATTERN.findall(corrected_text)
    pairs: list[dict[str, str]] = []
    matcher = SequenceMatcher(None, original, corrected, autojunk=False)
    for operation, source_start, source_end, target_start, target_end in matcher.get_opcodes():
        if operation != "replace":
            continue
        source_tokens = original[source_start:source_end]
        target_tokens = corrected[target_start:target_end]
        if (
            not source_tokens
            or len(source_tokens) != len(target_tokens)
            or len(source_tokens) > 4
        ):
            continue
        for source, target in zip(source_tokens, target_tokens, strict=True):
            if source == target or abs(len(source) - len(target)) > 3:
                continue
            content_type = classify_content_type(
                source,
                target,
                resident_names=resident_names,
            )
            pairs.append(
                {
                    "recognized_text": source,
                    "corrected_text": target,
                    "content_type": content_type,
                    "context_text": _context_excerpt(raw_text, source),
                }
            )
            if len(pairs) >= 24:
                break
        if len(pairs) >= 24:
            break
    unique: dict[tuple[str, str], dict[str, str]] = {}
    for pair in pairs:
        unique[(pair["recognized_text"], pair["corrected_text"])] = pair
    return list(unique.values())


def extract_page_visual_signature(image_path: str | Path) -> list[float] | None:
    """Store a small numeric handwriting-region signature, never image bytes."""
    try:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("L")
    except (OSError, UnidentifiedImageError):
        return None
    image.thumbnail((1600, 1600))
    image = ImageOps.autocontrast(image)
    ink_mask = image.point(lambda pixel: 255 if pixel < 205 else 0)
    bounding_box = ink_mask.getbbox()
    if bounding_box is None:
        return None
    ink_region = image.crop(bounding_box)
    canvas = Image.new("L", (96, 32), color=255)
    ink_region.thumbnail((92, 28))
    x = (canvas.width - ink_region.width) // 2
    y = (canvas.height - ink_region.height) // 2
    canvas.paste(ink_region, (x, y))
    reduced = canvas.resize((48, 16))
    values = [(255.0 - float(pixel)) / 255.0 for pixel in reduced.getdata()]
    norm = sqrt(sum(value * value for value in values))
    if norm == 0:
        return None
    return [round(value / norm, 6) for value in values]


def visual_similarity(
    left: list[float] | None,
    right: list[float] | None,
) -> float | None:
    if not left or not right or len(left) != len(right):
        return None
    left_norm = sqrt(sum(value * value for value in left))
    right_norm = sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return None
    similarity = sum(a * b for a, b in zip(left, right, strict=True))
    return max(0.0, min(1.0, similarity / (left_norm * right_norm)))


def correction_candidate_id(
    source: str,
    recognized_text: str,
    corrected_text: str,
) -> str:
    digest = sha256(
        f"{source}\0{recognized_text}\0{corrected_text}".encode("utf-8")
    ).hexdigest()[:24]
    return f"{source}:{digest}"


def suggest_from_confirmed_events(
    value: str | None,
    *,
    context_text: str | None,
    source_writer_id: UUID | str | None,
    visual_signature: list[float] | None,
    evidences: list[CorrectionEvidence],
    resident_names: list[str] | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not value:
        return []
    current_context = context_text or value
    current_writer = str(source_writer_id) if source_writer_id is not None else None
    grouped: dict[tuple[str, str], list[tuple[CorrectionEvidence, float, str]]] = {}
    for recognized in dict.fromkeys(TOKEN_PATTERN.findall(value)):
        for evidence in evidences:
            raw_similarity = SequenceMatcher(
                None,
                recognized,
                evidence.recognized_text,
            ).ratio()
            if raw_similarity < 0.62:
                continue
            context_similarity = SequenceMatcher(
                None,
                current_context[:1200],
                evidence.context_text[:1200],
            ).ratio()
            writer_similarity = (
                1.0
                if current_writer
                and evidence.source_writer_id
                and current_writer == evidence.source_writer_id
                else 0.0
            )
            page_visual_similarity = visual_similarity(
                visual_signature,
                evidence.visual_signature,
            )
            score = (
                (0.62 * raw_similarity)
                + (0.23 * context_similarity)
                + (0.08 * writer_similarity)
                + (0.07 * (page_visual_similarity or 0.0))
            )
            reason_parts = [f"과거 오인 {raw_similarity:.0%} 유사"]
            if context_similarity >= 0.45:
                reason_parts.append("문맥 유사")
            if writer_similarity:
                reason_parts.append("같은 작성자")
            if page_visual_similarity is not None and page_visual_similarity >= 0.7:
                reason_parts.append("글씨 영역 유사")
            key = (recognized, evidence.corrected_text)
            grouped.setdefault(key, []).append(
                (evidence, score, " · ".join(reason_parts))
            )

    candidates: list[dict[str, Any]] = []
    for (recognized, corrected), matches in grouped.items():
        matches.sort(key=lambda item: item[1], reverse=True)
        best_evidence, best_score, reason = matches[0]
        support_count = len({match[0].event_id for match in matches})
        confidence = min(0.99, best_score + min(0.12, 0.04 * (support_count - 1)))
        content_type = best_evidence.content_type or classify_content_type(
            recognized,
            corrected,
            resident_names=resident_names,
        )
        candidates.append(
            {
                "id": correction_candidate_id(
                    "confirmed_history",
                    recognized,
                    corrected,
                ),
                "recognized": recognized,
                "candidate": corrected,
                "confidence": round(confidence, 3),
                "support_count": support_count,
                "content_type": content_type,
                "is_protected": content_type in PROTECTED_CONTENT_TYPES,
                "source": "confirmed_history",
                "reason": reason,
                "source_event_ids": [
                    match[0].event_id for match in matches[:5]
                ],
                "auto_applicable": False,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            candidate["confidence"],
            candidate["support_count"],
        ),
        reverse=True,
    )
    return candidates[:limit]
