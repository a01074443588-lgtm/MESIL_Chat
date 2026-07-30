from __future__ import annotations

import base64
from difflib import SequenceMatcher
import json
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import settings


class OcrError(RuntimeError):
    """첨부 이미지 판독 실패를 사용자 데이터와 분리해 기록하기 위한 오류."""


KOREAN_TOKEN = re.compile(r"[가-힣]{2,8}")
CORRECTION_TOKEN = re.compile(r"^[가-힣]{2,12}$")
NO_TEXT_RESPONSES = {
    "이미지에는 한글 텍스트가 없습니다.",
    "이미지에 한글 텍스트가 없습니다.",
    "판독할 수 있는 한글이 없습니다.",
}
MODEL_EXPLANATION_MARKERS = (
    "이미지에 표시된 텍스트는 다음과 같습니다",
    "이미지의 텍스트는 다음과 같습니다",
    "이 텍스트는 ",
)


@dataclass(frozen=True)
class OcrLexicon:
    resident_names: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()
    corrections: tuple[tuple[str, str], ...] = ()


def _unique_terms(values: list[object], *, limit: int) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("term", "")
        term = str(value).strip()
        if term and term not in result:
            result.append(term)
        if len(result) >= limit:
            break
    return tuple(result)


def _load_resident_names() -> tuple[str, ...]:
    path = Path(settings.smcodi_resident_lexicon_path)
    if not path.is_file():
        return ()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ()
    records = payload.get("residents", []) if isinstance(payload, dict) else payload
    if not isinstance(records, list):
        return ()
    values = [
        record.get("display_name", "")
        for record in records
        if isinstance(record, dict) and record.get("is_active", True)
    ]
    return _unique_terms(values, limit=500)


def _load_local_lexicon() -> OcrLexicon:
    path = Path(settings.ocr_lexicon_path)
    if not path.is_file():
        return OcrLexicon(resident_names=_load_resident_names())
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return OcrLexicon(resident_names=_load_resident_names())
    if isinstance(payload, list):
        return OcrLexicon(
            resident_names=_load_resident_names(),
            terms=_unique_terms(payload, limit=200),
        )
    if not isinstance(payload, dict):
        return OcrLexicon(resident_names=_load_resident_names())
    correction_payload = payload.get("corrections", {})
    correction_items: list[tuple[str, str]] = []
    if isinstance(correction_payload, dict):
        for recognized, corrected in correction_payload.items():
            source = str(recognized).strip()
            target = str(corrected).strip()
            if (
                CORRECTION_TOKEN.fullmatch(source)
                and CORRECTION_TOKEN.fullmatch(target)
                and source != target
            ):
                correction_items.append((source, target))
    elif isinstance(correction_payload, list):
        for correction in correction_payload:
            if not isinstance(correction, dict):
                continue
            source = str(correction.get("recognized", "")).strip()
            target = str(correction.get("corrected", "")).strip()
            if (
                CORRECTION_TOKEN.fullmatch(source)
                and CORRECTION_TOKEN.fullmatch(target)
                and source != target
            ):
                correction_items.append((source, target))
    resident_names = _unique_terms(
        [
            *_load_resident_names(),
            *payload.get("resident_name_candidates", []),
            *payload.get("names", []),
        ],
        limit=500,
    )
    return OcrLexicon(
        resident_names=resident_names,
        terms=_unique_terms(
            [
                *payload.get("organization_terms", []),
                *payload.get("long_term_care_terms", []),
                *payload.get("terms", []),
            ],
            limit=300,
        ),
        corrections=tuple(dict.fromkeys(correction_items)),
    )


def extract_reviewed_corrections(
    extracted_text: str | None,
    reviewed_text: str | None,
    *,
    excluded_terms: list[str] | None = None,
) -> list[tuple[str, str]]:
    """담당자가 고친 문장에서 안전한 1:1 한글 토큰 교정만 추출합니다."""
    if not extracted_text or not reviewed_text or extracted_text == reviewed_text:
        return []
    original = KOREAN_TOKEN.findall(extracted_text)
    reviewed = KOREAN_TOKEN.findall(reviewed_text)
    excluded = set(excluded_terms or [])
    corrections: list[tuple[str, str]] = []
    matcher = SequenceMatcher(None, original, reviewed, autojunk=False)
    for operation, source_start, source_end, target_start, target_end in matcher.get_opcodes():
        if operation != "replace":
            continue
        source_tokens = original[source_start:source_end]
        target_tokens = reviewed[target_start:target_end]
        if (
            len(source_tokens) != len(target_tokens)
            or not source_tokens
            or len(source_tokens) > 4
        ):
            continue
        for source, target in zip(source_tokens, target_tokens, strict=True):
            if (
                source == target
                or source in excluded
                or target in excluded
                or not CORRECTION_TOKEN.fullmatch(source)
                or not CORRECTION_TOKEN.fullmatch(target)
                or abs(len(source) - len(target)) > 2
                or SequenceMatcher(None, source, target).ratio() < 0.3
            ):
                continue
            corrections.append((source, target))
            if len(corrections) >= 20:
                break
        if len(corrections) >= 20:
            break
    return list(dict.fromkeys(corrections))


def _clean_model_text(value: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = value.strip()
    if value.startswith("```") and value.endswith("```"):
        value = re.sub(r"^```(?:text)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    marker_positions = [
        value.find(marker)
        for marker in MODEL_EXPLANATION_MARKERS
        if value.find(marker) >= 0
    ]
    if marker_positions:
        # 손글씨 전사 뒤에 모델이 이미지 설명이나 예시 숫자를 만들어 붙이는 경우,
        # 설명이 시작되는 지점부터 버리고 실제 전사 부분만 담당자에게 보여준다.
        value = value[: min(marker_positions)]
    return value.strip()


def find_spelling_candidates(
    value: str | None,
    *,
    preferred_terms: list[str] | None = None,
    correction_pairs: list[tuple[str, str]] | None = None,
) -> list[dict[str, str]]:
    if not value:
        return []
    lexicon = _load_local_lexicon()
    preferred = [
        term
        for source in preferred_terms or []
        for term in KOREAN_TOKEN.findall(source)
    ]
    resident_names = list(dict.fromkeys([
        *preferred,
        *lexicon.resident_names,
    ]))
    terms = list(dict.fromkeys(lexicon.terms))
    corrections = dict(
        [
            *lexicon.corrections,
            *(correction_pairs or []),
        ]
    )
    candidates: list[dict[str, str]] = []
    for recognized in dict.fromkeys(KOREAN_TOKEN.findall(value)):
        exact_correction = corrections.get(recognized)
        if exact_correction and exact_correction != recognized:
            candidates.append(
                {"recognized": recognized, "candidate": exact_correction}
            )
            if len(candidates) >= 8:
                break
            continue
        if recognized in resident_names or recognized in terms:
            continue
        similar_resident_names = [
            term
            for term in resident_names
            if abs(len(term) - len(recognized)) <= 1
            and SequenceMatcher(None, recognized, term).ratio() >= 0.74
        ]
        similar_terms = [
            term
            for term in terms
            if len(term) == len(recognized)
            and SequenceMatcher(None, recognized, term).ratio() >= 0.86
        ]
        similar = [*similar_resident_names, *similar_terms]
        if not similar:
            continue
        candidate = max(
            similar,
            key=lambda term: SequenceMatcher(None, recognized, term).ratio(),
        )
        candidates.append({"recognized": recognized, "candidate": candidate})
        if len(candidates) >= 8:
            break
    return candidates


def get_ai_lexicon_context(
    value: str,
    *,
    correction_pairs: list[tuple[str, str]] | None = None,
) -> dict[str, object]:
    """AI 검토에는 기관용어만 전달하고, 확정 교정은 OCR 후보 단계에 둡니다."""
    lexicon = _load_local_lexicon()
    all_corrections = dict(correction_pairs or [])
    recognized_tokens = set(KOREAN_TOKEN.findall(value))
    relevant_corrections = {
        source: target
        for source, target in all_corrections.items()
        if source in recognized_tokens
    }
    return {
        "long_term_care_terms": list(lexicon.terms[:120]),
        "relevant_corrections": relevant_corrections,
    }


def _validate_model_text(value: str) -> str:
    result = _clean_model_text(value)
    if not result or result in NO_TEXT_RESPONSES:
        return ""
    lines = [line.strip() for line in result.splitlines() if line.strip()]
    if len(lines) >= 12:
        unique_ratio = len(set(lines)) / len(lines)
        if unique_ratio < 0.55:
            raise OcrError(
                "판독문에 같은 문장이 과도하게 반복되어 결과를 폐기했습니다."
            )
    return result


def _merge_ocr_band_texts(texts: list[str]) -> str:
    """겹쳐 촬영된 구간의 경계에서 완전히 같은 줄만 한 번으로 합친다."""
    merged_lines: list[str] = []
    for text in texts:
        incoming_lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not incoming_lines:
            continue

        max_overlap = min(len(merged_lines), len(incoming_lines), 8)
        overlap_size = 0
        for size in range(max_overlap, 0, -1):
            previous = [
                re.sub(r"\s+", "", line) for line in merged_lines[-size:]
            ]
            incoming = [
                re.sub(r"\s+", "", line) for line in incoming_lines[:size]
            ]
            if previous == incoming:
                overlap_size = size
                break
        merged_lines.extend(incoming_lines[overlap_size:])
    return "\n".join(merged_lines)


def _ollama_extract_single(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    del room_name, resident_name
    # Qwen3-VL은 긴 지시나 큰 어휘목록을 함께 주면 내부 추론이 길어져
    # 실제 판독문을 내기 전에 출력 한도를 소진할 수 있다. 1차 판독은
    # 짧고 결정적인 지시만 사용하고, 기관 어휘는 담당자 확인 단계에서
    # 철자 후보로 활용한다.
    prompt = (
        "이미지에 실제로 보이는 한글과 숫자를 위에서 아래 순서대로 한 번씩 그대로 "
        "옮겨 적으세요. 추측·교정·설명 없이 텍스트만 출력하세요."
    )
    request_payload = {
        "model": settings.ocr_model,
        "stream": False,
        "think": False,
        "messages": [
            {
                "role": "user",
                "content": prompt,
                "images": [base64.b64encode(image_path.read_bytes()).decode("ascii")],
            }
        ],
        "options": {
            "temperature": 0,
            "num_ctx": 8192,
            "num_predict": 4096,
        },
        "keep_alive": "10m",
    }
    request = Request(
        f"{settings.ocr_base_url.rstrip('/')}/api/chat",
        data=json.dumps(request_payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(request, timeout=settings.ocr_timeout_seconds) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise OcrError(f"로컬 OCR 서버 오류({exc.code}): {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise OcrError("로컬 OCR 서버에 연결할 수 없습니다.") from exc
    except json.JSONDecodeError as exc:
        raise OcrError("로컬 OCR 서버 응답을 해석할 수 없습니다.") from exc
    content = response_payload.get("message", {}).get("content", "")
    result = _validate_model_text(str(content))
    if not result:
        raise OcrError("이미지에서 판독된 글이 없습니다.")
    return result


def _extract_with_repetition_split(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
    depth: int = 0,
) -> str:
    """반복 출력이 생긴 구간만 더 작게 나눠 최대 두 단계 다시 판독한다."""
    try:
        return _ollama_extract_single(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    except OcrError as exc:
        if "과도하게 반복" not in str(exc) or depth >= 2:
            raise

    try:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("반복 판독 구간 이미지를 다시 열 수 없습니다.") from exc
    width, height = image.size
    if height < 260:
        raise OcrError(
            "로컬 AI가 같은 문장을 반복했습니다. 글씨가 더 크게 보이도록 다시 촬영하거나 "
            "원본을 보며 직접 입력해 주세요."
        )

    overlap = max(12, height // 80)
    texts: list[str] = []
    for index in range(2):
        top = max(0, (height * index // 2) - (overlap if index else 0))
        bottom = min(
            height,
            (height * (index + 1) // 2) + (overlap if index == 0 else 0),
        )
        retry_path = image_path.with_name(
            f"{image_path.stem}-retry-{depth + 1}-{index + 1}.jpg"
        )
        image.crop((0, top, width, bottom)).save(
            retry_path,
            format="JPEG",
            quality=94,
        )
        try:
            text = _extract_with_repetition_split(
                retry_path,
                room_name=room_name,
                resident_name=resident_name,
                depth=depth + 1,
            )
        except OcrError as exc:
            if str(exc) == "이미지에서 판독된 글이 없습니다.":
                continue
            raise
        texts.append(text)

    result = _validate_model_text(_merge_ocr_band_texts(texts))
    if not result:
        raise OcrError("이미지에서 판독된 글이 없습니다.")
    return result


def _ollama_extract(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    bands = settings.ocr_image_bands
    if bands <= 1:
        return _ollama_extract_single(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    try:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("보고서 이미지를 열 수 없습니다.") from exc
    width, height = image.size
    if height < width * 0.9:
        return _ollama_extract_single(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    overlap = max(24, height // 50)
    texts: list[str] = []
    with TemporaryDirectory(prefix="smcodi-ocr-") as temporary:
        for index in range(bands):
            top = max(0, (height * index // bands) - (overlap if index else 0))
            bottom = min(
                height,
                (height * (index + 1) // bands)
                + (overlap if index < bands - 1 else 0),
            )
            crop_path = Path(temporary) / f"band-{index + 1}.jpg"
            image.crop((0, top, width, bottom)).save(
                crop_path,
                format="JPEG",
                quality=92,
            )
            try:
                text = _extract_with_repetition_split(
                    crop_path,
                    room_name=room_name,
                    resident_name=resident_name,
                )
            except OcrError as exc:
                if str(exc) == "이미지에서 판독된 글이 없습니다.":
                    continue
                raise
            texts.append(text)
    result = _validate_model_text(_merge_ocr_band_texts(texts))
    if not result:
        raise OcrError("이미지에서 판독된 글이 없습니다.")
    return result


def extract_report_text(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    provider = settings.ocr_provider.strip().lower()
    if provider == "stub":
        return "시험용 손글씨 판독 결과"
    if provider == "ollama":
        return _ollama_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    if provider in {"", "disabled", "off", "none"}:
        raise OcrError("로컬 OCR 기능이 꺼져 있습니다.")
    raise OcrError(f"지원하지 않는 OCR 제공 방식입니다: {settings.ocr_provider}")
