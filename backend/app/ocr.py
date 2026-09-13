from __future__ import annotations

import base64
from collections import Counter
from io import BytesIO
from difflib import SequenceMatcher
import json
import re
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from PIL import (
    Image,
    ImageEnhance,
    ImageFilter,
    ImageOps,
    ImageStat,
    UnidentifiedImageError,
)

from .ai_assist_schemas import AiAssistSourceSnapshot
from .image_ocr_runtime import OcrError, current_image_runtime, image_chat, image_provider
from .codex_worker_client import (
    CodexWorkerAnalysis,
    CodexWorkerClient,
    CodexWorkerError,
    CodexWorkerImage,
)
from .config import settings
from .domain_lexicon import (
    ai_context as long_term_care_ai_context,
    canonical_terms as default_long_term_care_terms,
    hangul_similarity,
    term_corrections as default_term_corrections,
)


ASSESSMENT_DOCUMENT_OCR_PROMPT = (
    "서류 페이지에 실제로 보이는 문서 제목, 표 머리글, 항목명과 값을 "
    "위에서 아래 순서대로 옮겨 적으세요. 표의 한 행은 가능한 한 "
    "`항목명: 보이는 값` 한 줄로 쓰세요. 서로 다른 칸을 합치거나 추측하지 말고, "
    "읽을 수 없는 값은 `[판독불명]`으로 적으세요. 안내문은 그대로 옮기되 대상자의 "
    "현재 사실처럼 바꾸지 마세요. 설명 없이 판독문만 출력하세요."
)


HANDWRITING_SAFE_FAILURE_MESSAGE = (
    "이면지 글자 간섭 또는 반복 출력으로 판독하지 못했습니다. "
    "원본을 보며 직접 입력하거나 마이크로 읽어 주세요."
)
HANDWRITING_VIEWER_PHRASES = (
    "마우스 휠",
    "버튼으로 확대",
    "확대 축소",
    "화면을 탭",
    "사진을 확대",
)


def _handwriting_output_rejection_reason(
    value: str,
    *,
    expected_line_count: int | None = None,
) -> str | None:
    result = _clean_model_text(value)
    if not result or result in NO_TEXT_RESPONSES or result == '[[NO_TEXT]]':
        return None
    compact = re.sub(r"\s+", " ", result).strip()
    lines = [line.strip() for line in result.splitlines() if line.strip()]
    tokens = re.findall(
        r"\[[^\]\n]{1,12}\]|[가-힣A-Za-z0-9]+(?:[/.:%-][가-힣A-Za-z0-9]+)*",
        result,
    )
    counts = Counter(tokens)
    dominant_count = max(counts.values(), default=0)
    if dominant_count >= 8 and dominant_count / max(1, len(tokens)) >= 0.18:
        return "repeated_token"
    if re.search(r"(.{1,18}?)(?:\s*\1){5,}", compact):
        return "looping_fragment"
    normalized_lines = [re.sub(r"\s+", "", line) for line in lines]
    line_counts = Counter(normalized_lines)
    if max(line_counts.values(), default=0) >= 3:
        return "repeated_line"
    if len(lines) >= 12 and len(set(normalized_lines)) / len(lines) < 0.72:
        return "low_line_diversity"
    dates = [
        re.sub(r"\s+", "", item)
        for item in re.findall(
            r"(?:\d{2,4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일",
            result,
        )
    ]
    if max(Counter(dates).values(), default=0) >= 2:
        return "repeated_date"
    normalized_compact = re.sub(r"[·\-\s]+", "", compact)
    if any(
        re.sub(r"[·\-\s]+", "", phrase) in normalized_compact
        for phrase in HANDWRITING_VIEWER_PHRASES
    ):
        return "viewer_ui_text"
    if expected_line_count is not None and expected_line_count > 0:
        max_characters = max(900, expected_line_count * 210)
        max_lines = max(12, expected_line_count * 3)
        if len(result) > max_characters:
            return "too_long_for_detected_lines"
        if len(lines) > max_lines:
            return "too_many_lines"
    elif len(result) > 4000:
        return "unbounded_output"
    return None


def is_pathological_handwriting_output(value: str | None) -> bool:
    return bool(value and _handwriting_output_rejection_reason(value))


def validate_handwriting_model_text(
    value: str,
    *,
    expected_line_count: int | None,
) -> str:
    result = _clean_model_text(value)
    if not result or result in NO_TEXT_RESPONSES:
        return ""
    if _handwriting_output_rejection_reason(
        result,
        expected_line_count=expected_line_count,
    ):
        raise OcrError(HANDWRITING_SAFE_FAILURE_MESSAGE)
    return result


@dataclass(frozen=True)
class NameRegionCandidate:
    recognized: str
    candidates: tuple[str, ...]
    slot_index: int
    normalized_bbox: tuple[int, int, int, int] | None = None


@dataclass(frozen=True)
class MealNameRegion:
    slot_index: int
    normalized_bbox: tuple[int, int, int, int]


@dataclass(frozen=True)
class ReportTextRegion:
    line_index: int
    normalized_bbox: tuple[int, int, int, int]
    rotation_degrees: float = 0


_CODEX_OCR_LOCK = Lock()
_CODEX_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
NAME_REGION_LINE_PATTERN = re.compile(
    r"^(?:"
    r"이름\s*후보\s*(?:(?:슬롯|slot)(?:\s*번호)?\s*)?"
    r"(?P<named_slot>\d{1,2})?"
    r"|(?:슬롯|slot)(?:\s*번호)?\s*(?P<slot_only>\d{1,2})"
    r"(?:\s*이름\s*후보)?"
    r")\s*:\s*(?P<recognized>[^|\n]+)\|(?P<candidates>[^\n]+)$",
    re.IGNORECASE,
)
NAME_REGION_MARKDOWN_PREFIX_PATTERN = re.compile(
    r"^\s*(?:(?:[-+*•·]\s+)|(?:\d{1,2}[.)]\s+))"
)
MEAL_NAME_REGION_PATTERN = re.compile(
    r"^(?:이름\s*)?영역\s*(?:슬롯|slot)(?:\s*번호)?\s*"
    r"(?P<slot>\d{1,2})\s*:\s*"
    r"(?P<x1>\d{1,4})\s*[,，]\s*(?P<y1>\d{1,4})\s*[,，]\s*"
    r"(?P<x2>\d{1,4})\s*[,，]\s*(?P<y2>\d{1,4})$",
    re.IGNORECASE,
)
REPORT_TEXT_REGION_PATTERN = re.compile(
    r"^(?:텍스트\s*영역\s*)?(?:줄|line)(?:\s*번호)?\s*"
    r"(?P<line>\d{1,3})\s*:\s*"
    r"(?P<x1>\d{1,4})\s*[,，]\s*(?P<y1>\d{1,4})\s*[,，]\s*"
    r"(?P<x2>\d{1,4})\s*[,，]\s*(?P<y2>\d{1,4})"
    r"(?:\s*[;；|]\s*(?:각도|angle)\s*=?\s*"
    r"(?P<angle>-?\d{1,2}(?:\.\d{1,2})?))?$",
    re.IGNORECASE,
)
STRUCTURED_NAME_LAYOUT_HINT_PATTERN = re.compile(
    r"^슬롯(?P<slot>\d{1,2})=.*?/(?P<role>meal_name_list|status_checklist)$"
)


KOREAN_TOKEN = re.compile(r"[가-힣]{2,8}")
CORRECTION_TOKEN = re.compile(r"^[가-힣]{2,12}$")
CORRECTION_SUFFIXES = frozenset(
    {
        "이",
        "가",
        "은",
        "는",
        "을",
        "를",
        "에",
        "에서",
        "에게",
        "께",
        "과",
        "와",
        "도",
        "만",
        "로",
        "으로",
    }
)
RESIDENT_LABEL_SUFFIXES = ("어르신", "선생님", "님", "씨")
NO_TEXT_RESPONSES = {
    "[[NO_TEXT]]",
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
        return OcrLexicon(
            resident_names=_load_resident_names(),
            terms=default_long_term_care_terms(),
            corrections=default_term_corrections(),
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return OcrLexicon(
            resident_names=_load_resident_names(),
            terms=default_long_term_care_terms(),
            corrections=default_term_corrections(),
        )
    if isinstance(payload, list):
        return OcrLexicon(
            resident_names=_load_resident_names(),
            terms=_unique_terms(
                [*default_long_term_care_terms(), *payload],
                limit=400,
            ),
            corrections=default_term_corrections(),
        )
    if not isinstance(payload, dict):
        return OcrLexicon(
            resident_names=_load_resident_names(),
            terms=default_long_term_care_terms(),
            corrections=default_term_corrections(),
        )
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
                *default_long_term_care_terms(),
                *payload.get("organization_terms", []),
                *payload.get("long_term_care_terms", []),
                *payload.get("terms", []),
            ],
            limit=500,
        ),
        corrections=tuple(
            dict.fromkeys([*default_term_corrections(), *correction_items])
        ),
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
    for (
        operation,
        source_start,
        source_end,
        target_start,
        target_end,
    ) in matcher.get_opcodes():
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
    source_kind: str | None = None,
) -> list[dict[str, str]]:
    if not value:
        return []
    lexicon = _load_local_lexicon()
    preferred = [
        term
        for source in preferred_terms or []
        for term in KOREAN_TOKEN.findall(source)
    ]
    resident_names = list(
        dict.fromkeys(
            [
                *preferred,
                *lexicon.resident_names,
            ]
        )
    )
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
        if exact_correction is None:
            for source, target in sorted(
                corrections.items(),
                key=lambda item: len(item[0]),
                reverse=True,
            ):
                if not recognized.startswith(source):
                    continue
                suffix = recognized[len(source) :]
                if suffix in CORRECTION_SUFFIXES:
                    exact_correction = f"{target}{suffix}"
                    break
        if exact_correction and exact_correction != recognized:
            candidates.append({"recognized": recognized, "candidate": exact_correction})
            if len(candidates) >= 8:
                break
            continue
        if recognized in resident_names or recognized in terms:
            continue
        resident_token = recognized
        resident_suffix = ""
        for suffix in (
            *RESIDENT_LABEL_SUFFIXES,
            *sorted(CORRECTION_SUFFIXES, key=len, reverse=True),
        ):
            if recognized.endswith(suffix) and len(recognized) - len(suffix) >= 2:
                resident_token = recognized[: -len(suffix)]
                resident_suffix = suffix
                break
        if resident_token in resident_names:
            continue
        name_context = bool(
            resident_suffix in RESIDENT_LABEL_SUFFIXES
            or re.search(
                rf"(?:^|[\n,;])\s*{re.escape(resident_token)}"
                rf"(?:은|는|이|가|께서)?\s*(?:어르신|선생님|님|씨|[:：])",
                value,
            )
            or re.search(
                rf"(?:어르신|대상자)\s*{re.escape(resident_token)}",
                value,
            )
        )
        resident_threshold = (
            0.68
            if source_kind == "audio" and name_context
            else 0.82
            if source_kind == "audio"
            else 0.74
        )
        similar_resident_names = [
            term
            for term in resident_names
            if abs(len(term) - len(resident_token)) <= 1
            and hangul_similarity(resident_token, term) >= resident_threshold
        ]
        similar_terms = [
            term
            for term in terms
            if abs(len(term) - len(recognized)) <= 1
            and hangul_similarity(recognized, term) >= 0.84
        ]
        similar = [
            *(f"{term}{resident_suffix}" for term in similar_resident_names),
            *similar_terms,
        ]
        if not similar:
            continue
        candidate = max(
            similar,
            key=lambda term: hangul_similarity(recognized, term),
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
    all_corrections = dict([*lexicon.corrections, *(correction_pairs or [])])
    recognized_tokens = set(KOREAN_TOKEN.findall(value))
    relevant_corrections = {
        source: target
        for source, target in all_corrections.items()
        if source in recognized_tokens
    }
    return {
        "long_term_care_terms": list(lexicon.terms[:120]),
        "relevant_corrections": relevant_corrections,
        **long_term_care_ai_context(value),
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
            previous = [re.sub(r"\s+", "", line) for line in merged_lines[-size:]]
            incoming = [re.sub(r"\s+", "", line) for line in incoming_lines[:size]]
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
    handwriting_expected_lines: int | None = None,
    prompt_override: str | None = None,
) -> str:
    del room_name, resident_name
    # Qwen3-VL은 긴 지시나 큰 어휘목록을 함께 주면 내부 추론이 길어져
    # 실제 판독문을 내기 전에 출력 한도를 소진할 수 있다. 1차 판독은
    # 짧고 결정적인 지시만 사용하고, 기관 어휘는 담당자 확인 단계에서
    # 철자 후보로 활용한다.
    prompt = prompt_override or (
        "이미지에 실제로 보이는 한글과 숫자를 위에서 아래 순서대로 한 번씩 그대로 "
        "옮겨 적으세요. 추측·교정·설명 없이 텍스트만 출력하세요. 글자가 없으면 [[NO_TEXT]]만 출력하세요."
    )
    request_payload = {
        "model": current_image_runtime().model,
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
            "num_predict": (
                1800 if handwriting_expected_lines is not None else 4096
            ),
        },
        "keep_alive": "10m",
    }
    response_payload = image_chat(request_payload)
    content = response_payload.get("message", {}).get("content", "")
    result = _validate_model_text(str(content))
    if handwriting_expected_lines is not None:
        result = validate_handwriting_model_text(
            result,
            expected_line_count=handwriting_expected_lines,
        )
    if not result:
        raise OcrError("이미지에서 판독된 글이 없습니다.")
    return result


def _extract_with_repetition_split(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
    depth: int = 0,
    prompt_override: str | None = None,
) -> str:
    """반복 출력이 생긴 구간만 더 작게 나눠 최대 두 단계 다시 판독한다."""
    try:
        call_kwargs = {
            "room_name": room_name,
            "resident_name": resident_name,
        }
        if prompt_override is not None:
            call_kwargs["prompt_override"] = prompt_override
        return _ollama_extract_single(image_path, **call_kwargs)
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
                prompt_override=prompt_override,
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
    prompt_override: str | None = None,
) -> str:
    bands = settings.ocr_image_bands
    if bands <= 1:
        call_kwargs = {
            "room_name": room_name,
            "resident_name": resident_name,
        }
        if prompt_override is not None:
            call_kwargs["prompt_override"] = prompt_override
        return _ollama_extract_single(image_path, **call_kwargs)
    try:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("보고서 이미지를 열 수 없습니다.") from exc
    width, height = image.size
    # 가로 촬영된 실제 손글씨도 뒷면 인쇄물이나 표가 함께 보여 전체
    # 호출이 제한시간 안에 반복 감지까지 도달하지 못할 수 있다. 세로
    # 보고서와 같은 구간 판독을 먼저 적용하고 정확히 겹친 줄만 합친다.
    overlap = max(24, height // 50)
    texts: list[str] = []
    with TemporaryDirectory(prefix="smcodi-ocr-") as temporary:
        for index in range(bands):
            top = max(0, (height * index // bands) - (overlap if index else 0))
            bottom = min(
                height,
                (height * (index + 1) // bands) + (overlap if index < bands - 1 else 0),
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
                    prompt_override=prompt_override,
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


def _codex_worker_extract(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    """Read one report image through the isolated Codex worker.

    Automatic attachment extraction is serialized because the worker accepts
    one image job at a time.  Resident and room labels are deliberately not
    injected into the first reading so the stored source remains raw-first.
    """

    del room_name, resident_name
    if _CODEX_IMAGE_MIME_TYPES.get(image_path.suffix.lower()) is None:
        raise OcrError("지원하지 않는 이미지 형식입니다.")
    try:
        prepared_bytes = _prepare_codex_report_image(image_path)
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("보고서 이미지를 열 수 없습니다.") from exc
    shared_token = (
        settings.codex_worker_shared_token.get_secret_value()
        if settings.codex_worker_shared_token is not None
        else ""
    )
    try:
        client = CodexWorkerClient(
            base_url=settings.ocr_base_url,
            shared_token=shared_token,
            timeout_seconds=min(settings.ocr_timeout_seconds, 610),
        )
        with _CODEX_OCR_LOCK:
            analysis = client.analyze(
                task_type="image_text",
                question=(
                    "이미지에 실제로 보이는 한글과 숫자를 추측이나 교정 없이 그대로 "
                    "판독해 주세요. 문서의 왼쪽 본문만 읽지 말고 오른쪽 위·여백의 "
                    "작은 표와 메모도 빠뜨리지 마세요. 특히 `아침식사 하신 분`, "
                    "`점심식사 하신 분`, `저녁식사 하신 분`처럼 보이는 제목과 그 "
                    "아래의 연속된 좌우 이름 칸은 위치와 줄바꿈을 유지해 모두 적으세요. "
                    "보이지 않는 글자는 만들지 말고 `[판독불명]`으로 남기세요."
                ),
                sources=[
                    AiAssistSourceSnapshot(
                        source_no=1,
                        source_type="attachment",
                        label="첨부 이미지 원본",
                        text=None,
                    )
                ],
                images=[
                    CodexWorkerImage(
                        content=prepared_bytes,
                        mime_type="image/jpeg",
                        filename="report-enhanced.jpg",
                        image_no=1,
                    )
                ],
            )
    except CodexWorkerError as exc:
        raise OcrError(exc.public_message) from exc
    except ValueError as exc:
        raise OcrError("Codex 이미지 판독 설정이 올바르지 않습니다.") from exc
    except OSError as exc:
        raise OcrError("판독할 이미지 파일을 읽을 수 없습니다.") from exc

    matching_texts = [
        item.text
        for item in analysis.result.image_texts
        if item.image_no == 1 and item.text.strip()
    ]
    extracted_text = (
        matching_texts[0] if matching_texts else (analysis.result.visible_text or "")
    )
    result = _validate_model_text(extracted_text)
    if not result:
        raise OcrError("이미지에서 판독된 글이 없습니다.")
    return result


def _prepare_codex_report_image(image_path: Path) -> bytes:
    """Create a conservative OCR derivative without touching the original.

    Phone photos are often larger than the worker needs and faint pencil text
    loses contrast.  EXIF rotation, a modest contrast lift and light sharpening
    improve legibility while avoiding thresholding that could erase strokes.
    """

    image = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    longest_edge = max(image.size)
    if longest_edge > 2800:
        scale = 2800 / longest_edge
        image = image.resize(
            (
                max(1, round(image.width * scale)),
                max(1, round(image.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    image = ImageOps.autocontrast(image, cutoff=0.5)
    image = ImageEnhance.Contrast(image).enhance(1.08)
    image = image.filter(ImageFilter.UnsharpMask(radius=1.0, percent=75, threshold=3))
    output = BytesIO()
    image.save(output, format="JPEG", quality=95, optimize=True)
    return output.getvalue()


def parse_name_region_candidates(
    value: str,
    *,
    roster_names: list[str],
) -> list[NameRegionCandidate]:
    """Parse only roster-backed name-slot candidates from the second pass."""

    roster = {
        re.sub(r"\s+", "", name).strip()
        for name in roster_names
        if re.fullmatch(r"[가-힣]{2,4}", re.sub(r"\s+", "", name).strip())
    }
    parsed: list[NameRegionCandidate] = []
    seen: set[tuple[int, str, tuple[str, ...]]] = set()
    try:
        json_payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        json_payload = None
    if isinstance(json_payload, dict):
        json_slot = json_payload.get("slot", 1)
        json_recognized = json_payload.get("recognized")
        json_candidates = json_payload.get("candidates")
        if (
            isinstance(json_slot, int)
            and isinstance(json_recognized, str)
            and isinstance(json_candidates, list)
        ):
            source_lines = [
                f"이름후보 슬롯번호 {json_slot}: {json_recognized}|"
                + ",".join(item for item in json_candidates if isinstance(item, str))
            ]
        else:
            source_lines = []
    else:
        source_lines = value.splitlines()
    for raw_line in source_lines:
        line = NAME_REGION_MARKDOWN_PREFIX_PATTERN.sub("", raw_line, count=1).strip()
        # The worker occasionally wraps the requested one-line response in
        # Markdown emphasis/code spans.  Removing only formatting markers and
        # normalising full-width separators keeps parsing strict while accepting
        # those harmless presentation variants.
        line = line.replace("`", "").replace("**", "").replace("__", "")
        line = line.replace("：", ":").replace("｜", "|")
        match = NAME_REGION_LINE_PATTERN.fullmatch(line.strip())
        if match is None:
            continue
        recognized = re.sub(r"\s+", "", match.group("recognized")).strip("·•:：- ")
        if not recognized or len(recognized) > 12:
            continue
        candidates = tuple(
            dict.fromkeys(
                item
                for raw_item in re.split(r"[,，]", match.group("candidates"))
                if (item := re.sub(r"\s+", "", raw_item).strip()) in roster
            )
        )[:2]
        explicit_slot = match.group("named_slot") or match.group("slot_only")
        requested_slot_index = int(explicit_slot) if explicit_slot else len(parsed) + 1
        key = (requested_slot_index, recognized, candidates)
        if (
            not candidates
            or key in seen
            or requested_slot_index < 1
            or requested_slot_index > 40
            or any(item.slot_index == requested_slot_index for item in parsed)
        ):
            continue
        seen.add(key)
        parsed.append(
            NameRegionCandidate(
                recognized=recognized,
                candidates=candidates,
                slot_index=requested_slot_index,
            )
        )
    return parsed[:40]


def parse_meal_name_regions(
    value: str,
    *,
    expected_slot_indexes: list[int],
) -> list[MealNameRegion]:
    """Accept only bounded, normalized boxes for known meal-name slots."""

    expected = set(expected_slot_indexes)
    parsed: list[MealNameRegion] = []
    seen: set[int] = set()
    try:
        json_payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        json_payload = None
    if isinstance(json_payload, dict) and isinstance(json_payload.get("regions"), list):
        source_lines = []
        for item in json_payload["regions"]:
            if not isinstance(item, dict):
                continue
            slot = item.get("slot")
            bbox = item.get("bbox")
            if (
                not isinstance(slot, int)
                or not isinstance(bbox, list)
                or len(bbox) != 4
            ):
                continue
            if not all(isinstance(coordinate, int) for coordinate in bbox):
                continue
            source_lines.append(
                f"이름영역 슬롯번호 {slot}: " + ",".join(str(value) for value in bbox)
            )
    else:
        source_lines = value.splitlines()
    for raw_line in source_lines:
        line = NAME_REGION_MARKDOWN_PREFIX_PATTERN.sub("", raw_line, count=1).strip()
        line = line.replace("`", "").replace("**", "").replace("__", "")
        line = line.replace("：", ":")
        match = MEAL_NAME_REGION_PATTERN.fullmatch(line)
        if match is None:
            continue
        slot_index = int(match.group("slot"))
        bbox = tuple(int(match.group(name)) for name in ("x1", "y1", "x2", "y2"))
        x1, y1, x2, y2 = bbox
        width = x2 - x1
        height = y2 - y1
        if (
            slot_index not in expected
            or slot_index in seen
            or min(bbox) < 0
            or max(bbox) > 1000
            or width < 20
            or height < 10
            or width > 700
            or height > 350
        ):
            continue
        seen.add(slot_index)
        parsed.append(
            MealNameRegion(
                slot_index=slot_index,
                normalized_bbox=bbox,
            )
        )
    return sorted(parsed, key=lambda item: item.slot_index)


def parse_report_text_regions(
    value: str,
    *,
    expected_line_indexes: list[int],
) -> list[ReportTextRegion]:
    """Parse bounded full-page text-line locations without accepting new text."""

    expected = set(expected_line_indexes)
    source_lines: list[str]
    try:
        payload = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        payload = None
    if isinstance(payload, dict) and isinstance(payload.get("regions"), list):
        source_lines = []
        for item in payload["regions"]:
            if not isinstance(item, dict):
                continue
            line_index = item.get("line")
            bbox = item.get("bbox")
            angle = item.get("angle", 0)
            if (
                not isinstance(line_index, int)
                or not isinstance(bbox, list)
                or len(bbox) != 4
                or not all(isinstance(coordinate, int) for coordinate in bbox)
                or not isinstance(angle, (int, float))
            ):
                continue
            source_lines.append(
                f"텍스트영역 줄번호 {line_index}: "
                + ",".join(str(coordinate) for coordinate in bbox)
                + f"; 각도={angle}"
            )
    else:
        source_lines = value.splitlines()

    parsed: list[ReportTextRegion] = []
    seen: set[int] = set()
    for raw_line in source_lines:
        line = NAME_REGION_MARKDOWN_PREFIX_PATTERN.sub("", raw_line, count=1).strip()
        line = line.replace("`", "").replace("**", "").replace("__", "")
        line = line.replace("：", ":")
        match = REPORT_TEXT_REGION_PATTERN.fullmatch(line)
        if match is None:
            continue
        line_index = int(match.group("line"))
        bbox = tuple(int(match.group(name)) for name in ("x1", "y1", "x2", "y2"))
        x1, y1, x2, y2 = bbox
        width = x2 - x1
        height = y2 - y1
        raw_angle = match.group("angle")
        rotation_degrees = float(raw_angle) if raw_angle is not None else 0
        if (
            line_index not in expected
            or line_index in seen
            or min(bbox) < 0
            or max(bbox) > 1000
            or width < 15
            or height < 6
            or width > 980
            # A locator item represents one displayed editor line. Reject a
            # tall multi-line block so one coarse box cannot swallow several
            # independently editable lines.
            or height > 140
        ):
            continue
        # Automatic angles are only a non-confirming draft.  Reject an
        # implausibly steep estimate instead of rotating a valid location.
        if not -15 <= rotation_degrees <= 15:
            rotation_degrees = 0
        seen.add(line_index)
        parsed.append(
            ReportTextRegion(
                line_index=line_index,
                normalized_bbox=bbox,
                rotation_degrees=rotation_degrees,
            )
        )
    return sorted(parsed, key=lambda item: item.line_index)


def _worker_result_text(analysis: CodexWorkerAnalysis) -> str:
    result = analysis.result
    return "\n".join(
        [
            *(item.text for item in result.image_texts),
            result.visible_text or "",
            result.answer or "",
        ]
    )


def _analyze_name_image(
    client: CodexWorkerClient,
    *,
    question: str,
    content: bytes,
    filename: str,
    source_label: str,
) -> str:
    with _CODEX_OCR_LOCK:
        analysis = client.analyze(
            task_type="image_text",
            question=question,
            sources=[
                AiAssistSourceSnapshot(
                    source_no=1,
                    source_type="attachment",
                    label=source_label,
                    text=None,
                )
            ],
            images=[
                CodexWorkerImage(
                    content=content,
                    mime_type="image/jpeg",
                    filename=filename,
                    image_no=1,
                )
            ],
        )
    return _worker_result_text(analysis)


def _analyze_name_images(
    client: CodexWorkerClient,
    *,
    question: str,
    images: list[CodexWorkerImage],
    source_label: str,
) -> CodexWorkerAnalysis:
    with _CODEX_OCR_LOCK:
        return client.analyze(
            task_type="image_text",
            question=question,
            sources=[
                AiAssistSourceSnapshot(
                    source_no=1,
                    source_type="attachment",
                    label=source_label,
                    text=None,
                )
            ],
            images=images,
        )


def _ollama_analyze_name_image(
    *,
    question: str,
    content: bytes,
    format_schema: dict[str, object] | None = None,
) -> str:
    """Run one bounded name-region request against the configured local VLM."""

    request_payload = {
        "model": current_image_runtime().model,
        "stream": False,
        "think": False,
        "messages": [
            {
                "role": "user",
                "content": question,
                "images": [base64.b64encode(content).decode("ascii")],
            }
        ],
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": 2048},
        "keep_alive": "10m",
    }
    if format_schema is not None:
        request_payload["format"] = format_schema
    payload = image_chat(request_payload)
    return str(payload.get("message", {}).get("content", ""))


def _structured_name_slot_indexes(layout_hints: list[str] | None) -> list[int]:
    indexes: list[int] = []
    for hint in layout_hints or []:
        match = STRUCTURED_NAME_LAYOUT_HINT_PATTERN.fullmatch(hint.strip())
        if match is not None:
            indexes.append(int(match.group("slot")))
    return list(dict.fromkeys(indexes))


def _prepare_codex_region_image(
    image: Image.Image,
    normalized_bbox: tuple[int, int, int, int],
) -> bytes:
    x1, y1, x2, y2 = normalized_bbox
    # Small padding keeps handwriting strokes that touch a model-proposed edge.
    padding_x = max(2, round(image.width * 0.008))
    padding_y = max(2, round(image.height * 0.008))
    pixel_box = (
        max(0, image.width * x1 // 1000 - padding_x),
        max(0, image.height * y1 // 1000 - padding_y),
        min(image.width, (image.width * x2 + 999) // 1000 + padding_x),
        min(image.height, (image.height * y2 + 999) // 1000 + padding_y),
    )
    region = image.crop(pixel_box).convert("RGB")
    longest_edge = max(region.size)
    if longest_edge < 1200:
        scale = min(4.0, 1200 / max(1, longest_edge))
        region = region.resize(
            (
                max(1, round(region.width * scale)),
                max(1, round(region.height * scale)),
            ),
            Image.Resampling.LANCZOS,
        )
    region = ImageOps.autocontrast(region, cutoff=0.5)
    region = ImageEnhance.Contrast(region).enhance(1.08)
    region = region.filter(ImageFilter.UnsharpMask(radius=1.0, percent=75, threshold=3))
    output = BytesIO()
    region.save(output, format="JPEG", quality=95, optimize=True)
    return output.getvalue()


def locate_report_text_regions(
    image_path: Path,
    *,
    lines: list[str],
) -> list[ReportTextRegion]:
    """Locate existing OCR lines on the image without rewriting their content."""

    bounded_lines = [line.strip()[:500] for line in lines[:120] if line.strip()]
    if not bounded_lines:
        return []
    provider = image_provider()
    if provider not in {"codex_worker", "ollama"}:
        return []
    try:
        prepared_page = _prepare_codex_report_image(image_path)
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("보고서 이미지를 열 수 없습니다.") from exc

    indexed_lines = list(enumerate(bounded_lines, 1))
    question_prefix = (
        "아래 목록은 이 이미지에서 이미 판독된 줄입니다. 글자를 다시 쓰거나 교정하지 말고, "
        "각 줄이 실제 이미지에 보이는 위치만 찾으세요. 좌표는 이미지 왼쪽 위 0,0, "
        "오른쪽 아래 1000,1000의 정규화 좌표입니다. 줄 전체의 손글씨 영역을 감싸되 "
        "서로 다른 줄을 한 상자로 합치지 마세요. 확실히 찾은 줄만 "
        "`텍스트영역 줄번호 N: x1,y1,x2,y2; 각도=-3.5` 형식으로 출력하세요. "
        "각도는 글줄이 오른쪽으로 내려가면 양수, 올라가면 음수이며 -15~15도입니다. "
        "수평이면 0으로 쓰고, 찾지 못한 줄은 "
        "출력하지 마세요. 목록의 문구가 실제 글씨와 조금 다르더라도 줄 순서와 시각적 "
        "형태를 함께 보되 임의 위치를 만들지 마세요.\n\n판독 줄 목록:\n"
    )
    format_schema = {
        "type": "object",
        "properties": {
            "regions": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "line": {"type": "integer"},
                        "bbox": {
                            "type": "array",
                            "items": {"type": "integer"},
                            "minItems": 4,
                            "maxItems": 4,
                        },
                        "angle": {"type": "number", "minimum": -15, "maximum": 15},
                    },
                    "required": ["line", "bbox", "angle"],
                },
            }
        },
        "required": ["regions"],
    }
    client: CodexWorkerClient | None = None
    if provider == "codex_worker":
        shared_token = (
            settings.codex_worker_shared_token.get_secret_value()
            if settings.codex_worker_shared_token is not None
            else ""
        )
        try:
            client = CodexWorkerClient(
                base_url=settings.ocr_base_url,
                shared_token=shared_token,
                timeout_seconds=min(settings.ocr_timeout_seconds, 610),
            )
        except ValueError as exc:
            raise OcrError("Codex 위치 탐지 설정이 올바르지 않습니다.") from exc

    parsed_by_line: dict[int, ReportTextRegion] = {}
    successful_calls = 0
    last_error: OcrError | None = None
    for chunk_start in range(0, len(indexed_lines), 8):
        chunk = indexed_lines[chunk_start : chunk_start + 8]
        expected_indexes = [line_index for line_index, _line in chunk]
        numbered_lines = "\n".join(
            f"{line_index}. {line}" for line_index, line in chunk
        )
        question = question_prefix + numbered_lines
        try:
            if provider == "ollama":
                output = _ollama_analyze_name_image(
                    question=question,
                    content=prepared_page,
                    format_schema=format_schema,
                )
            else:
                assert client is not None
                output = _analyze_name_image(
                    client,
                    question=question,
                    content=prepared_page,
                    filename="report-text-line-locator.jpg",
                    source_label="판독문 줄 위치 탐지",
                )
            successful_calls += 1
        except CodexWorkerError as exc:
            last_error = OcrError(exc.public_message)
            continue
        except OcrError as exc:
            last_error = exc
            continue
        for region in parse_report_text_regions(
            output,
            expected_line_indexes=expected_indexes,
        ):
            parsed_by_line.setdefault(region.line_index, region)

    if successful_calls == 0 and last_error is not None:
        raise last_error
    return [parsed_by_line[index] for index in sorted(parsed_by_line)]


def _extract_structured_name_candidates_by_region(
    client: CodexWorkerClient | None,
    *,
    image_path: Path,
    prepared_page: bytes,
    roster_names: list[str],
    structured_slot_indexes: list[int],
    layout_hints: list[str] | None,
) -> list[NameRegionCandidate]:
    if not structured_slot_indexes:
        return []
    locator_prompt = (
        "업무 보고서에서 아래 확정된 status_checklist 또는 meal_name_list 슬롯 각각에 "
        "대응하는 손글씨 사람 이름 칸의 "
        "경계상자를 찾으세요. 좌표는 원본 이미지의 왼쪽 위가 0,0이고 오른쪽 아래가 "
        "1000,1000인 정규화 좌표입니다. 이름을 판독하지 말고 각 슬롯마다 "
        "`이름영역 슬롯번호: x1,y1,x2,y2` 한 줄만 출력하세요. 칸 전체가 아니라 "
        "한 사람의 이름 글씨만 포함하세요. 이미지의 회전·기울기·해상도와 무관하게 "
        "실제 표·제목·반복 행을 기준으로 찾고, 찾지 못한 슬롯은 출력하지 마세요. "
        "대상 슬롯: "
        + ", ".join(str(index) for index in structured_slot_indexes)
        + ". 1차 행 구조: "
        + "; ".join((layout_hints or [])[:40])
    )
    if client is None:
        locator_output = _ollama_analyze_name_image(
            question=locator_prompt,
            content=prepared_page,
            format_schema={
                "type": "object",
                "properties": {
                    "regions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "slot": {"type": "integer"},
                                "bbox": {
                                    "type": "array",
                                    "items": {"type": "integer"},
                                    "minItems": 4,
                                    "maxItems": 4,
                                },
                            },
                            "required": ["slot", "bbox"],
                        },
                    }
                },
                "required": ["regions"],
            },
        )
    else:
        locator_output = _analyze_name_image(
            client,
            question=locator_prompt,
            content=prepared_page,
            filename="structured-name-locator.jpg",
            source_label="업무표 이름 칸 위치 탐지",
        )
    regions = parse_meal_name_regions(
        locator_output,
        expected_slot_indexes=structured_slot_indexes,
    )
    if not regions:
        return []
    page = ImageOps.exif_transpose(Image.open(image_path)).convert("RGB")
    candidates: list[NameRegionCandidate] = []
    roster_text = ", ".join(roster_names)
    prepared_regions: list[tuple[MealNameRegion, bytes]] = []
    for region in regions:
        try:
            prepared_regions.append(
                (
                    region,
                    _prepare_codex_region_image(page, region.normalized_bbox),
                )
            )
        except (ValueError, OSError):
            continue
    for chunk_start in range(0, len(prepared_regions), 8):
        chunk = prepared_regions[chunk_start : chunk_start + 8]
        image_to_region = {
            image_no: region for image_no, (region, _content) in enumerate(chunk, 1)
        }
        mapping_text = ", ".join(
            f"이미지{image_no}=슬롯{region.slot_index}"
            for image_no, region in image_to_region.items()
        )
        question = (
            "각 이미지는 확정된 업무표 구조에서 한 사람의 이름 칸만 독립적으로 자른 것입니다. "
            f"이미지와 원래 슬롯의 대응은 {mapping_text}입니다. "
            "각 이미지의 손글씨 획을 현재 이용자 명단에서만 대조하고 문맥으로 추측하지 마세요. "
            "각 이미지 결과는 해당 슬롯 번호를 유지해 "
            "`이름후보 슬롯번호: 보이는글자|후보1,후보2` 한 줄로 출력하세요. "
            "후보1은 획이 가장 가까운 이름이고 후보2는 다음 후보입니다. "
            "명단 밖 이름은 후보에 쓰지 마세요.\n현재 이용자 명단: " + roster_text
        )
        if client is None:
            for region, content in chunk:
                try:
                    output = _ollama_analyze_name_image(
                        question=(
                            question + f"\n이번 한 장은 슬롯{region.slot_index}입니다. "
                            "결과는 한 줄만 출력하세요."
                        ),
                        content=content,
                        format_schema={
                            "type": "object",
                            "properties": {
                                "slot": {"type": "integer"},
                                "recognized": {"type": "string"},
                                "candidates": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "maxItems": 2,
                                },
                            },
                            "required": ["slot", "recognized", "candidates"],
                        },
                    )
                except OcrError:
                    continue
                parsed = parse_name_region_candidates(
                    output,
                    roster_names=roster_names,
                )
                if not parsed:
                    continue
                first = parsed[0]
                candidates.append(
                    NameRegionCandidate(
                        recognized=first.recognized,
                        candidates=first.candidates,
                        slot_index=region.slot_index,
                        normalized_bbox=region.normalized_bbox,
                    )
                )
            continue
        try:
            analysis = _analyze_name_images(
                client,
                question=question,
                images=[
                    CodexWorkerImage(
                        content=content,
                        mime_type="image/jpeg",
                        filename=f"structured-name-slot-{region.slot_index}.jpg",
                        image_no=image_no,
                    )
                    for image_no, (region, content) in enumerate(chunk, 1)
                ],
                source_label="업무표 이름 칸 묶음 판독",
            )
        except (CodexWorkerError, ValueError, OSError):
            continue
        for item in analysis.result.image_texts:
            region = image_to_region.get(item.image_no)
            if region is None:
                continue
            parsed = parse_name_region_candidates(
                item.text,
                roster_names=roster_names,
            )
            if not parsed:
                continue
            first = parsed[0]
            candidates.append(
                NameRegionCandidate(
                    recognized=first.recognized,
                    candidates=first.candidates,
                    slot_index=region.slot_index,
                    normalized_bbox=region.normalized_bbox,
                )
            )
    return candidates


def extract_report_name_candidates(
    image_path: Path,
    *,
    roster_names: list[str],
    layout_hints: list[str] | None = None,
) -> list[NameRegionCandidate]:
    """Read name-shaped image regions separately and restrict output to the roster.

    This is an additional candidate-only pass.  It does not rewrite or confirm
    the raw full-page OCR result.
    """

    normalized_roster = list(
        dict.fromkeys(
            re.sub(r"\s+", "", name).strip()
            for name in roster_names
            if re.fullmatch(r"[가-힣]{2,4}", re.sub(r"\s+", "", name).strip())
        )
    )
    provider = image_provider()
    if not normalized_roster or provider not in {"codex_worker", "ollama"}:
        return []
    shared_token = (
        settings.codex_worker_shared_token.get_secret_value()
        if settings.codex_worker_shared_token is not None
        else ""
    )
    prompt = (
        "이 작업은 전체 문장 받아쓰기가 아닙니다. 먼저 제목·구역·표의 행 구조를 보고 사람 이름이 적히는 위치만 찾으세요. "
        "이름은 콜론(:) 바로 앞, 글머리표 뒤의 2~4글자, 표의 이름 칸, 또는 각 기록 문단의 첫 항목입니다. "
        "특히 아침식사·점심식사·저녁식사 같은 제목 아래에서 같은 모양으로 반복되는 행의 첫 칸은 다음 제목이 나오기 전까지 모두 이름 칸입니다. "
        "그 위치의 손글씨 획을 아래 현재 이용자 명단 안에서만 대조하세요. 문장 내용으로 이름을 추측하지 마세요. "
        "각 이름 위치마다 1차 판독의 슬롯 번호를 유지해 정확히 한 줄씩 "
        "`이름후보 슬롯번호: 보이는글자|후보1,후보2` 형식만 출력하세요. "
        "후보1은 획이 가장 가까운 이름, 후보2는 다음 후보입니다. "
        "확실하지 않아도 명단 밖 이름은 쓰지 마세요. "
        + (
            "1차 판독에서 감지한 행 구조는 다음과 같습니다: "
            + "; ".join((layout_hints or [])[:40])
            + ". "
            if layout_hints
            else ""
        )
        + "\n현재 이용자 명단: "
        + ", ".join(normalized_roster)
    )
    try:
        client = (
            CodexWorkerClient(
                base_url=settings.ocr_base_url,
                shared_token=shared_token,
                timeout_seconds=min(settings.ocr_timeout_seconds, 610),
            )
            if provider == "codex_worker"
            else None
        )
        prepared_page = _prepare_codex_report_image(image_path)
    except (ValueError, OSError, UnidentifiedImageError):
        return []

    structured_slot_indexes = _structured_name_slot_indexes(layout_hints)
    region_candidates: list[NameRegionCandidate] = []
    if structured_slot_indexes:
        try:
            region_candidates = _extract_structured_name_candidates_by_region(
                client,
                image_path=image_path,
                prepared_page=prepared_page,
                roster_names=normalized_roster,
                structured_slot_indexes=structured_slot_indexes,
                layout_hints=layout_hints,
            )
        except (CodexWorkerError, ValueError, OSError, UnidentifiedImageError):
            # A locator or one crop may be unavailable. The established full-page
            # candidate pass below remains the safe fallback.
            region_candidates = []

    hinted_slot_indexes = {
        int(match.group(1))
        for hint in (layout_hints or [])
        if (match := re.match(r"^슬롯(\d{1,2})=", hint.strip())) is not None
    }
    region_slot_indexes = {item.slot_index for item in region_candidates}
    needs_full_page = (
        not region_candidates
        or bool(hinted_slot_indexes - region_slot_indexes)
        or not hinted_slot_indexes
    )
    full_page_candidates: list[NameRegionCandidate] = []
    if needs_full_page:
        try:
            output = (
                _analyze_name_image(
                    client,
                    question=prompt,
                    content=prepared_page,
                    filename="name-region.jpg",
                    source_label="이름 위치 판독 대상",
                )
                if client is not None
                else _ollama_analyze_name_image(
                    question=prompt,
                    content=prepared_page,
                )
            )
            full_page_candidates = parse_name_region_candidates(
                output,
                roster_names=normalized_roster,
            )
        except (CodexWorkerError, ValueError, OSError):
            # Keep successfully read crops even if the fallback pass fails.
            full_page_candidates = []

    merged = [
        *region_candidates,
        *(
            item
            for item in full_page_candidates
            if item.slot_index not in region_slot_indexes
        ),
    ]
    return sorted(merged, key=lambda item: item.slot_index)[:40]


def image_likely_contains_text(image_path: Path) -> bool:
    """Conservatively skip only near-blank images before expensive OCR.

    The check intentionally favors false positives so faint pencil reports are
    still analyzed. Ordinary photos may proceed to OCR, while blank scans and
    solid-color images are completed without consuming an AI job.
    """

    try:
        image = ImageOps.exif_transpose(Image.open(image_path)).convert("L")
    except (OSError, UnidentifiedImageError) as exc:
        raise OcrError("보고서 이미지를 열 수 없습니다.") from exc
    # Full resolution avoids losing faint strokes during downsampling.
    extrema = image.getextrema()
    contrast_range = float(extrema[1] - extrema[0])
    return contrast_range > 2


def extract_report_text(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    provider = image_provider()
    if provider == "stub":
        return "시험용 손글씨 판독 결과"
    if provider == "ollama":
        return _ollama_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    if provider == "codex_worker":
        return _codex_worker_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    if provider in {"", "disabled", "off", "none"}:
        raise OcrError("로컬 OCR 기능이 꺼져 있습니다.")
    raise OcrError(f"지원하지 않는 OCR 제공 방식입니다: {settings.ocr_provider}")


def extract_assessment_document_text(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
) -> str:
    """Read an assessment/form page as labeled fields without inventing facts."""

    provider = image_provider()
    if provider == "stub":
        return "시험용 서류 판독 결과"
    if provider == "ollama":
        return _ollama_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
            prompt_override=ASSESSMENT_DOCUMENT_OCR_PROMPT,
        )
    if provider == "codex_worker":
        return _codex_worker_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
    if provider in {"", "disabled", "off", "none"}:
        raise OcrError("로컬 OCR 기능이 꺼져 있습니다.")
    raise OcrError(f"지원하지 않는 OCR 제공 방식입니다: {settings.ocr_provider}")


def extract_handwriting_text(
    image_path: Path,
    *,
    room_name: str,
    resident_name: str | None,
    expected_line_count: int | None,
) -> str:
    """Read one safely prepared handwriting image without recursive retries."""

    bounded_lines = max(1, min(80, expected_line_count or 12))
    provider = image_provider()
    if provider == "stub":
        return validate_handwriting_model_text(
            "시험용 손글씨 판독 결과",
            expected_line_count=bounded_lines,
        )
    if provider == "ollama":
        return _ollama_extract_single(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
            handwriting_expected_lines=bounded_lines,
        )
    if provider == "codex_worker":
        result = _codex_worker_extract(
            image_path,
            room_name=room_name,
            resident_name=resident_name,
        )
        return validate_handwriting_model_text(
            result,
            expected_line_count=bounded_lines,
        )
    if provider in {"", "disabled", "off", "none"}:
        raise OcrError("로컬 OCR 기능이 꺼져 있습니다.")
    raise OcrError(f"지원하지 않는 OCR 제공 방식입니다: {settings.ocr_provider}")
