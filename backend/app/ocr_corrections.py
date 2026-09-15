from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
from hashlib import sha256
from math import sqrt
from pathlib import Path
from typing import Any
from uuid import UUID

from PIL import Image, ImageOps, UnidentifiedImageError

from .domain_lexicon import canonical_terms, term_corrections

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
EXCRETION_TERMS = {
    "소변",
    "대변",
    "배변",
    "설사",
    "기저귀",
    "속패드",
    "패드",
    "변기",
}


def _normalize_roster_name(value: str) -> str:
    without_test_label = re.sub(
        r"\s*\((?:가명|시험[^)]*)\)\s*$",
        "",
        value,
    )
    return without_test_label.replace(" ", "").strip()


@dataclass(frozen=True)
class CorrectionEvidence:
    event_id: str
    recognized_text: str
    corrected_text: str
    content_type: str
    context_text: str
    source_writer_id: str | None
    visual_signature: list[float] | None
    service_context: str | None = None
    source_kind: str | None = None
    section: str | None = None
    layout_role: str | None = None
    column_role: str | None = None
    document_template: str | None = None


@dataclass(frozen=True)
class ResidentRosterEntry:
    name: str
    service_type: str


@dataclass(frozen=True)
class RosterAwareDraft:
    text: str
    service_context: str | None
    service_confidence: float
    corrections: list[dict[str, Any]]


@dataclass(frozen=True)
class ReportNameSlot:
    start: int
    end: int
    recognized: str
    line_index: int
    section: str | None
    layout_role: str
    column_role: str | None = None
    row_sequence: int | None = None
    row_group_size: int = 1


@dataclass(frozen=True)
class ReportStatusSlot:
    start: int
    end: int
    recognized: str
    line_index: int
    row_group_size: int


VALID_RESIDENT_SERVICE_CONTEXTS = frozenset({"facility", "daycare", "homecare"})
SERVICE_KEYWORDS: dict[str, tuple[str, ...]] = {
    "facility": ("요양원", "입소", "침상", "층 어르신", "시설"),
    "daycare": ("주간보호", "센터", "송영", "등원", "귀가"),
    "homecare": ("방문요양", "방문", "재가", "수급자 댁"),
}
NAME_SLOT_PATTERNS = (
    re.compile(r"(?P<name>[가-힣]{2,4})(?=\s*어르신(?:\b|\s))"),
    re.compile(
        r"(?m)(?:^|[\r\n])\s*[·•\-○◦]\s*"
        r"(?P<name>[가-힣]{2,4})(?=\s*[·•:：])"
    ),
    re.compile(
        r"(?m)^\s*[·•\-○◦*]\s*(?P<name>[가-힣]{2,4})"
        r"(?=\s*(?:[·•\-–—]|$))"
    ),
    re.compile(r"(?m)^\s*(?P<name>[가-힣]{2,4})(?=\s+[\-–—]\s+)"),
)

REPORT_SECTION_KEYWORDS = (
    "아침식사",
    "점심식사",
    "저녁식사",
    "식사",
    "기저귀",
    "배변",
    "투약",
    "특이사항",
    "송영",
    "목욕",
    "건강상태",
)
NON_NAME_ROW_LABELS = frozenset(
    {
        "아침식사",
        "점심식사",
        "저녁식사",
        "특이사항",
        "건강상태",
        "작성자",
        "운전원",
        "보호자",
        "요양보호사",
        "사회복지사",
        "간호조무사",
        "작업치료사",
        "식사량",
        "섭취량",
        "수분량",
        "배뇨량",
        "배변량",
        "혈압",
        "체온",
        "맥박",
        "기저귀",
        "소변",
        "대변",
        "투약",
        "복약",
        "송영",
        "아침송영",
        "오전송영",
        "오후송영",
    }
)
POSITION_LOCKED_NAME_SLOT_ROLES = frozenset(
    {
        "label_value_row",
        "resident_suffix",
        "trailing_name_row",
        "section_repeated_row",
        "meal_name_list",
        "inline_name",
    }
)
CONTEXT_ONLY_CORRECTIONS = {"흑인": "확인"}
STATUS_CELL_TERMS = frozenset(
    {
        "확인",
        "흑인",
        "교체",
        "라인",
        "개인",
        "양호",
        "불량",
        "완료",
        "미완료",
        "정상",
        "주의",
        "해당없음",
    }
)
GENERIC_STATUS_ROW_PATTERN = re.compile(
    r"^(?P<prefix>\s*(?:[·•\-○◦*]\s*)?)"
    r"(?P<name>[가-힣]{2,4})"
    r"(?:\s*\[[^\]\r\n]{0,10}\]\s*)?"
    r"(?P<colon>[:：])\s*(?P<right>.+?)\s*$"
)
MEAL_HEADING_MISREADS = {
    "아침설사": "아침식사",
    "점심설사": "점심식사",
    "저녁설사": "저녁식사",
}
MEAL_HEADING_SUFFIXES = frozenset(
    {"", "확인", "하신분", "드신분", "하신분확인", "드신분확인", "관련"}
)
MEAL_NAME_TOKEN_PATTERN = r"[가-힣*＊○●□?？]{2,4}"
MEAL_NAME_CELL_PATTERN = (
    r"[가-힣*＊○●□?？](?:[가-힣*＊○●□?？\s]{0,10}[가-힣*＊○●□?？])?"
)
SAFE_CONTEXT_CORRECTION_TARGETS = frozenset(canonical_terms())
SAFE_CONTEXT_PHRASE_CORRECTIONS = tuple(
    (source, target)
    for source, target in term_corrections()
    if source
    and target
    and source not in CONTEXT_ONLY_CORRECTIONS
    and not re.search(r"\d", f"{source}{target}")
)


def _normalized_roster_entries(
    entries: Iterable[ResidentRosterEntry],
) -> list[ResidentRosterEntry]:
    unique: dict[tuple[str, str], ResidentRosterEntry] = {}
    for entry in entries:
        name = _normalize_roster_name(entry.name)
        service_type = entry.service_type.strip().lower()
        if (
            2 <= len(name) <= 4
            and re.fullmatch(r"[가-힣]+", name)
            and service_type in VALID_RESIDENT_SERVICE_CONTEXTS
        ):
            unique[(name, service_type)] = ResidentRosterEntry(
                name=name,
                service_type=service_type,
            )
    return list(unique.values())


def infer_resident_service_context(
    text: str,
    *,
    roster_entries: Iterable[ResidentRosterEntry],
    room_service_context: str | None = None,
) -> tuple[str | None, float]:
    """Infer a safe roster scope from exact names and report vocabulary.

    A fixed room scope always wins.  In an all-resident room, names shared by
    multiple services are deliberately ignored because they do not narrow the
    comparison population.
    """

    if room_service_context in VALID_RESIDENT_SERVICE_CONTEXTS:
        return room_service_context, 1.0
    entries = _normalized_roster_entries(roster_entries)
    services_by_name: dict[str, set[str]] = {}
    for entry in entries:
        services_by_name.setdefault(entry.name, set()).add(entry.service_type)

    exact_hits: dict[str, set[str]] = {
        service: set() for service in VALID_RESIDENT_SERVICE_CONTEXTS
    }
    for name, services in services_by_name.items():
        if len(services) != 1:
            continue
        if re.search(
            rf"(?<![가-힣A-Za-z0-9]){re.escape(name)}"
            rf"(?:\s*(?:어르신|님))?(?![가-힣A-Za-z0-9])",
            text,
        ):
            exact_hits[next(iter(services))].add(name)

    keyword_scores = {
        service: sum(text.count(keyword) for keyword in keywords)
        for service, keywords in SERVICE_KEYWORDS.items()
    }
    ranked = sorted(
        VALID_RESIDENT_SERVICE_CONTEXTS,
        key=lambda service: (
            len(exact_hits[service]),
            keyword_scores[service],
            service,
        ),
        reverse=True,
    )
    best, second = ranked[0], ranked[1]
    best_exact = len(exact_hits[best])
    second_exact = len(exact_hits[second])
    if best_exact >= 2 and best_exact > second_exact:
        confidence = min(0.98, 0.78 + (0.05 * min(best_exact, 4)))
        return best, confidence
    if (
        best_exact == 1
        and second_exact == 0
        and keyword_scores[best] >= 1
    ):
        return best, 0.8
    keyword_ranked = sorted(
        VALID_RESIDENT_SERVICE_CONTEXTS,
        key=lambda service: (keyword_scores[service], service),
        reverse=True,
    )
    keyword_best, keyword_second = keyword_ranked[0], keyword_ranked[1]
    if (
        keyword_scores[keyword_best] >= 2
        and keyword_scores[keyword_best] >= keyword_scores[keyword_second] + 2
    ):
        return keyword_best, 0.68
    return None, 0.0


def _normalized_report_heading(value: str) -> str:
    compact = re.sub(r"\s+", "", value).strip("<>[]【】()（）〈〉《》·•-:：")
    for source, target in MEAL_HEADING_MISREADS.items():
        if compact.startswith(source) and compact[len(source) :] in MEAL_HEADING_SUFFIXES:
            return f"{target}{compact[len(source):]}"
    return compact


def _report_section_heading(value: str) -> str | None:
    compact = _normalized_report_heading(value)
    if not compact or len(compact) > 24:
        return None
    return next(
        (keyword for keyword in REPORT_SECTION_KEYWORDS if keyword in compact),
        None,
    )


def _meal_heading_has_table_evidence(
    text: str,
    *,
    content_start: int,
    roster_names: Iterable[str] = (),
) -> bool:
    roster = {
        _normalize_roster_name(name)
        for name in roster_names
        if _normalize_roster_name(name)
    }
    bare_name_lines = 0
    checked_lines = 0
    for raw_line in text[content_start:].splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith(("<", "[", "【", "(")):
            break
        checked_lines += 1
        if checked_lines > 4:
            break
        if "이름 확인 필요" in line:
            return True
        if any(
            re.search(rf"(?<![가-힣]){re.escape(name)}(?![가-힣])", line)
            for name in roster
        ):
            return True
        if re.search(r"[:：]", line) and re.search(r"확인|교체", line):
            return True
        meal_pair = re.fullmatch(
            rf"\s*(?:[·•\-○◦]\s*)?({MEAL_NAME_TOKEN_PATTERN})"
            rf"\s*[:：]\s*({MEAL_NAME_CELL_PATTERN})\s*",
            line,
        )
        if meal_pair is not None and all(
            (compact := re.sub(r"\s+", "", token)) not in NON_NAME_ROW_LABELS
            and compact not in SAFE_CONTEXT_CORRECTION_TARGETS
            and 2 <= len(compact) <= 4
            for token in meal_pair.groups()
        ):
            return True
        content = line.lstrip("·•-○◦ ")
        tokens = re.findall(r"[가-힣]{2,4}", content)
        remainder = re.sub(r"[가-힣]{2,4}", "", content)
        if (
            len(tokens) >= 2
            and re.search(r"[,，·ㆍ/]", remainder)
            and re.fullmatch(r"[\s,，·ㆍ/]*", remainder)
            and all(
                token not in NON_NAME_ROW_LABELS
                and token not in SAFE_CONTEXT_CORRECTION_TARGETS
                for token in tokens
            )
        ):
            return True
        if (
            re.fullmatch(r"[가-힣]{2,4}", content)
            and content not in NON_NAME_ROW_LABELS
            and content not in SAFE_CONTEXT_CORRECTION_TARGETS
        ):
            bare_name_lines += 1
            if bare_name_lines >= 2:
                return True
        else:
            bare_name_lines = 0
    return False


def _status_cells_in_line(
    line: str,
    *,
    right_start: int,
) -> list[tuple[int, int, str]]:
    """Return exact status cells, never arbitrary words in report prose."""

    cells: list[tuple[int, int, str]] = []
    right = line[right_start:]
    for match in re.finditer(r"[가-힣]{2,6}", right):
        value = match.group(0)
        if value not in STATUS_CELL_TERMS:
            continue
        cells.append(
            (
                right_start + match.start(),
                right_start + match.end(),
                value,
            )
        )
    return cells


def _qualified_generic_status_rows(
    lines: list[str],
) -> dict[int, tuple[re.Match[str], list[tuple[int, int, str]], int]]:
    """Find repeated ``name: status`` rows with the same table grammar.

    A lone colon-left value is commonly a field label or prose heading.  It is
    therefore not a resident-name slot.  At least two adjacent non-empty rows
    must share the same bullet/column shape and expose a known status cell.
    """

    candidates: list[
        tuple[
            int,
            int,
            bool,
            int,
            re.Match[str],
            list[tuple[int, int, str]],
            int,
        ]
    ] = []
    nonempty_index = 0
    for line_index, raw_line in enumerate(lines, 1):
        line = raw_line.rstrip("\r\n")
        if not line.strip():
            continue
        nonempty_index += 1
        match = GENERIC_STATUS_ROW_PATTERN.fullmatch(line)
        if match is None or match.group("name") in NON_NAME_ROW_LABELS:
            continue
        right = match.group("right")
        if re.fullmatch(
            r"\s*[가-힣]{2,6}(?:\s*[,，/]\s*[가-힣]{2,6}){0,2}\s*",
            right,
        ) is None:
            continue
        right_tokens = re.findall(r"[가-힣]{2,6}", right)
        status_cells = _status_cells_in_line(
            line,
            right_start=match.start("right"),
        )
        candidates.append(
            (
                line_index,
                nonempty_index,
                bool(match.group("prefix").strip()),
                match.start("colon"),
                match,
                status_cells,
                len(right_tokens),
            )
        )

    qualified: dict[
        int, tuple[re.Match[str], list[tuple[int, int, str]], int]
    ] = {}
    run: list[
        tuple[
            int,
            int,
            bool,
            int,
            re.Match[str],
            list[tuple[int, int, str]],
            int,
        ]
    ] = []

    def flush() -> None:
        if len(run) < 2:
            return
        known_count = sum(len(row[5]) for row in run)
        token_count = sum(row[6] for row in run)
        if known_count < 2 or known_count / max(1, token_count) < (1 / 3):
            return
        group_size = len(run)
        for line_index, _, _, _, match, status_cells, _ in run:
            qualified[line_index] = (match, status_cells, group_size)

    for row in candidates:
        if run and (
            row[1] != run[-1][1] + 1
            or row[2] != run[-1][2]
            or abs(row[3] - run[-1][3]) > 4
        ):
            flush()
            run = []
        run.append(row)
    flush()
    return qualified


def detect_report_status_slots(text: str) -> list[ReportStatusSlot]:
    """Detect status cells only inside qualified repeated report rows."""

    lines = text.splitlines(keepends=True) or [text]
    qualified = _qualified_generic_status_rows(lines)
    slots: list[ReportStatusSlot] = []
    offset = 0
    for line_index, line in enumerate(lines, 1):
        row = qualified.get(line_index)
        if row is not None:
            _, status_cells, group_size = row
            for start, end, recognized in status_cells:
                slots.append(
                    ReportStatusSlot(
                        start=offset + start,
                        end=offset + end,
                        recognized=recognized,
                        line_index=line_index,
                        row_group_size=group_size,
                    )
                )
        offset += len(line)
    return slots


def detect_report_name_slots(text: str) -> list[ReportNameSlot]:
    """Detect name-shaped rows together with their report section and order.

    Full OCR frequently misreads the letters inside a name while still keeping
    the repeated ``name: value`` layout.  The layout is therefore recorded
    separately and later constrained to the active roster.
    """

    slots: dict[tuple[int, int], ReportNameSlot] = {}
    section: str | None = None
    offset = 0
    lines = text.splitlines(keepends=True) or [text]
    qualified_generic_rows = _qualified_generic_status_rows(lines)
    for line_index, line in enumerate(lines, 1):
        stripped = line.strip()
        heading = _report_section_heading(stripped)
        normalized_heading = _normalized_report_heading(stripped)
        plain_heading_suffix_is_safe = bool(
            heading
            and (
                heading not in {"아침식사", "점심식사", "저녁식사", "식사"}
                or normalized_heading[len(heading) :] in MEAL_HEADING_SUFFIXES
            )
        )
        is_heading_line = bool(
            heading
            and (
                stripped.startswith(("<", "[", "【", "(", "〈", "《"))
                or stripped.endswith((">", "]", "】", ")", "〉", "》", "관련"))
                or (
                    ":" not in stripped
                    and "：" not in stripped
                    and len(stripped) <= 14
                    and plain_heading_suffix_is_safe
                )
            )
        )
        if is_heading_line:
            is_misread_meal_heading = any(
                re.sub(r"\s+", "", stripped).strip("<>[]【】()（）·•-:：").startswith(
                    source
                )
                for source in MEAL_HEADING_MISREADS
            )
            if is_misread_meal_heading and not _meal_heading_has_table_evidence(
                text,
                content_start=offset + len(line),
            ):
                is_heading_line = False
        if is_heading_line:
            section = heading

        line_patterns: list[tuple[re.Pattern[str], str]] = [
            (
                re.compile(r"(?P<name>[가-힣]{2,4})(?=\s*어르신(?:\b|\s))"),
                "resident_suffix",
            ),
            (
                re.compile(
                    r"(?:\s[\-–—]\s+)(?P<name>[가-힣]{2,4})\s*$"
                ),
                "trailing_name_row",
            ),
        ]
        generic_row = qualified_generic_rows.get(line_index)
        if generic_row is not None:
            line_patterns.insert(
                0,
                (
                    re.compile(
                        r"^\s*(?:[·•\-○◦*]\s*)?(?P<name>[가-힣]{2,4})"
                        r"(?=\s*(?:\[[^\]\r\n]{0,10}\]\s*)?[:：])"
                    ),
                    "label_value_row",
                ),
            )
        if section in {"아침식사", "점심식사", "저녁식사", "식사", "기저귀", "배변", "투약"}:
            line_patterns.append(
                (
                    re.compile(
                        r"^\s*(?:[·•\-○◦]\s*)?(?P<name>[가-힣]{2,4})"
                        r"(?=\s{1,}|[,，])"
                    ),
                    "section_repeated_row",
                )
            )
        for pattern, layout_role in line_patterns:
            for match in pattern.finditer(line):
                recognized = match.group("name")
                if recognized in NON_NAME_ROW_LABELS:
                    continue
                start = offset + match.start("name")
                end = offset + match.end("name")
                slots[(start, end)] = ReportNameSlot(
                    start=start,
                    end=end,
                    recognized=recognized,
                    line_index=line_index,
                    section=section,
                    layout_role=layout_role,
                    row_group_size=(
                        generic_row[2]
                        if layout_role == "label_value_row"
                        and generic_row is not None
                        else 1
                    ),
                )
        if section in {"아침식사", "점심식사", "저녁식사", "식사"}:
            # 식사 확인표는 한 행에 `이름 : 이름`처럼 두 분을 나란히
            # 적기도 한다. 이 구역에서만 콜론 양쪽을 이름 칸으로 잡는다.
            meal_pair = re.fullmatch(
                r"\s*(?:[·•\-○◦]\s*)?"
                rf"(?P<left>{MEAL_NAME_TOKEN_PATTERN})\s*[:：]\s*"
                rf"(?P<right>{MEAL_NAME_CELL_PATTERN})\s*",
                line,
            )
            if meal_pair is not None:
                pair_tokens = [
                    re.sub(r"\s+", "", meal_pair.group(name))
                    for name in ("left", "right")
                ]
                if all(
                    token not in NON_NAME_ROW_LABELS
                    and token not in SAFE_CONTEXT_CORRECTION_TARGETS
                    and 2 <= len(token) <= 4
                    for token in pair_tokens
                ):
                    for group_name in ("left", "right"):
                        token = re.sub(r"\s+", "", meal_pair.group(group_name))
                        start = offset + meal_pair.start(group_name)
                        end = offset + meal_pair.end(group_name)
                        slots[(start, end)] = ReportNameSlot(
                            start=start,
                            end=end,
                            recognized=token,
                            line_index=line_index,
                            section=section,
                            layout_role="meal_name_list",
                            column_role=group_name,
                        )
            # 현장 식사 보고는 제목 아래에 이름만 한 명 또는 여러 명씩
            # 적는 경우가 많다. 그 줄이 이름 모양 토큰과 구분자만으로
            # 구성됐고 업무 단어가 섞이지 않았을 때 모든 토큰을 이름
            # 칸으로 다시 기록한다.
            content = stripped.lstrip("·•-○◦ ")
            bare_names = list(re.finditer(MEAL_NAME_TOKEN_PATTERN, content))
            remainder = re.sub(MEAL_NAME_TOKEN_PATTERN, "", content)
            bare_tokens = [match.group(0) for match in bare_names]
            if (
                bare_names
                and re.fullmatch(r"[\s,，·ㆍ/]*", remainder)
                and all(
                    token not in NON_NAME_ROW_LABELS
                    and token not in SAFE_CONTEXT_CORRECTION_TARGETS
                    for token in bare_tokens
                )
            ):
                content_start = line.find(content)
                for token_index, match in enumerate(bare_names):
                    start = offset + content_start + match.start()
                    end = offset + content_start + match.end()
                    slots[(start, end)] = ReportNameSlot(
                        start=start,
                        end=end,
                        recognized=match.group(0),
                        line_index=line_index,
                        section=section,
                        layout_role="meal_name_list",
                        column_role=(
                            "left"
                            if token_index == 0
                            else "right"
                            if token_index == 1
                            else "list"
                        ),
                    )
        offset += len(line)

    # Preserve the older mid-line patterns for reports that do not have
    # reliable line breaks, while still excluding known field labels.
    for pattern in NAME_SLOT_PATTERNS:
        for match in pattern.finditer(text):
            start, end = match.span("name")
            recognized = match.group("name")
            if recognized in NON_NAME_ROW_LABELS or (start, end) in slots:
                continue
            line_index = text.count("\n", 0, start) + 1
            slots[(start, end)] = ReportNameSlot(
                start=start,
                end=end,
                recognized=recognized,
                line_index=line_index,
                section=None,
                layout_role="inline_name",
            )
    ordered = [slots[key] for key in sorted(slots)]
    meal_rows: dict[tuple[str, int], list[int]] = {}
    for index, slot in enumerate(ordered):
        if slot.layout_role == "meal_name_list" and slot.section:
            meal_rows.setdefault((slot.section, slot.line_index), []).append(index)

    def annotate_meal_run(section_key: str, row_lines: list[int]) -> None:
        group_size = len(row_lines)
        for sequence, row_line in enumerate(row_lines, 1):
            for slot_index in meal_rows[(section_key, row_line)]:
                ordered[slot_index] = replace(
                    ordered[slot_index],
                    row_sequence=sequence,
                    row_group_size=group_size,
                )

    for section_name in {
        section_name for section_name, _ in meal_rows
    }:
        section_lines = sorted(
            line_index
            for candidate_section, line_index in meal_rows
            if candidate_section == section_name
        )
        run: list[int] = []
        for row_line in section_lines:
            if run and row_line != run[-1] + 1:
                annotate_meal_run(section_name, run)
                run = []
            run.append(row_line)
        annotate_meal_run(section_name, run)
    return ordered


def _is_safe_position_locked_name_slot(slot: ReportNameSlot) -> bool:
    """Reject field labels before a visual candidate can bind by position."""

    if (
        slot.layout_role not in POSITION_LOCKED_NAME_SLOT_ROLES
        or slot.recognized in NON_NAME_ROW_LABELS
        or re.fullmatch(r"[가-힣]{2,4}", slot.recognized) is None
    ):
        return False
    if slot.layout_role == "section_repeated_row":
        return slot.section in {
            "아침식사",
            "점심식사",
            "저녁식사",
            "식사",
            "기저귀",
            "배변",
            "투약",
        }
    if slot.layout_role == "meal_name_list":
        return slot.section in {"아침식사", "점심식사", "저녁식사", "식사"}
    return True


def _name_slots(text: str) -> list[tuple[int, int, str]]:
    return [
        (slot.start, slot.end, slot.recognized)
        for slot in detect_report_name_slots(text)
    ]


def _roster_name_score(recognized: str, candidate: str) -> float:
    score = SequenceMatcher(None, recognized, candidate).ratio()
    if len(recognized) == len(candidate):
        score += 0.03
    if recognized[:1] == candidate[:1]:
        score += 0.06
    if recognized[-1:] == candidate[-1:]:
        score += 0.03
    return min(0.99, score)


def _meal_structure_strength(
    *,
    column_role: str | None,
    row_sequence: int | None,
    row_group_size: int,
    service_confidence: float,
) -> float:
    """Return a small layout confidence signal, never a name similarity."""

    score = 0.0
    if column_role in {"left", "right"}:
        score += 0.03
    if row_sequence is not None and row_group_size >= 2:
        score += min(0.04, 0.02 + (0.005 * min(row_group_size, 4)))
    if service_confidence >= 0.78:
        score += 0.02
    return min(0.09, score)


def rank_visual_name_candidates(
    recognized: str,
    *,
    roster_names: Iterable[str],
    visual_candidates: Iterable[str] = (),
    layout_role: str | None = None,
    column_role: str | None = None,
    row_sequence: int | None = None,
    row_group_size: int = 1,
    service_confidence: float = 0.0,
) -> list[dict[str, Any]]:
    """Rank a visual read against the full roster without forcing a person."""

    visible = _normalize_roster_name(recognized)
    roster = sorted(
        {
            normalized
            for name in roster_names
            if (normalized := _normalize_roster_name(name))
        }
    )
    visual_order = list(
        dict.fromkeys(
            normalized
            for name in visual_candidates
            if (normalized := _normalize_roster_name(name)) in roster
        )
    )
    if not visible or not roster:
        return []

    structure_strength = _meal_structure_strength(
        column_role=column_role,
        row_sequence=row_sequence,
        row_group_size=row_group_size,
        service_confidence=service_confidence,
    )
    scored: list[tuple[str, float, float, int | None]] = []
    for candidate in roster:
        surface_score = _roster_name_score(visible, candidate)
        try:
            visual_rank: int | None = visual_order.index(candidate) + 1
        except ValueError:
            visual_rank = None
        visual_boost = 0.08 if visual_rank == 1 else 0.03 if visual_rank == 2 else 0.0
        scored.append(
            (
                candidate,
                min(0.99, surface_score + visual_boost + structure_strength),
                surface_score,
                visual_rank,
            )
        )
    scored.sort(key=lambda item: (item[1], item[2], item[0]), reverse=True)
    best_score = scored[0][1]
    second_score = scored[1][1] if len(scored) > 1 else 0.0
    best_surface = scored[0][2]
    minimum_score = 0.83 if len(visible) >= 3 else 0.90
    has_meal_structure = (
        layout_role == "meal_name_list"
        and (column_role is not None or row_sequence is not None)
    )
    minimum_surface = (
        0.72 if len(visible) >= 3 else 0.84
    ) if has_meal_structure else 0.90
    required_margin = (
        0.10 if structure_strength >= 0.07 else 0.12
    ) if has_meal_structure else 0.10
    best_is_clear = (
        # A single wrong syllable in a typical three-syllable Korean name
        # produces a SequenceMatcher ratio of about 0.667.  Keep that useful
        # near-match, but still require the total score and top-two margin
        # below so a merely roster-ranked stranger is never forced.
        best_surface >= minimum_surface
        and best_score >= minimum_score
        and best_score - second_score >= required_margin
    )
    return [
        {
            "candidate": candidate,
            "confidence": round(score, 3),
            "surface_similarity": round(surface_score, 3),
            "visual_rank": visual_rank,
            "layout_role": layout_role,
            "column_role": column_role,
            "row_sequence": row_sequence,
            "row_group_size": row_group_size,
            "structure_strength": round(structure_strength, 3),
            "score_gap": round(best_score - second_score, 3),
            "rank": rank,
            "candidate_is_clear": best_is_clear if rank == 1 else False,
        }
        for rank, (candidate, score, surface_score, visual_rank) in enumerate(
            scored[:2], start=1
        )
    ]


def build_roster_aware_draft(
    raw_text: str,
    *,
    roster_entries: Iterable[ResidentRosterEntry],
    room_service_context: str | None = None,
    source_writer_id: UUID | str | None = None,
    visual_signature: list[float] | None = None,
    evidences: Iterable[CorrectionEvidence] = (),
) -> RosterAwareDraft:
    """Build conservative, non-applying resident-name candidates.

    Only Hangul tokens in a name-shaped position are eligible.  The raw text
    is never mutated.  A room/service scope narrows the comparison population;
    when no scope can be inferred, the full active roster remains available as
    an explicit fallback.  Candidates are evidence for staff review and are
    never an automatic correction.
    """

    entries = _normalized_roster_entries(roster_entries)
    service_context, service_confidence = infer_resident_service_context(
        raw_text,
        roster_entries=entries,
        room_service_context=room_service_context,
    )
    # A vocabulary-only guess (for example, a document containing "송영") is
    # useful as a hint but is not strong enough to exclude two thirds of the
    # active roster.  Keep a fixed room scope or a name-supported inference;
    # otherwise compare against every active resident as the safe fallback.
    if (
        room_service_context not in VALID_RESIDENT_SERVICE_CONTEXTS
        and service_confidence < 0.78
    ):
        service_context = None
        service_confidence = 0.0
    scoped_names = sorted(
        {
            entry.name
            for entry in entries
            if service_context is None or entry.service_type == service_context
        }
    )
    if not scoped_names:
        return RosterAwareDraft(
            text=raw_text,
            service_context=service_context,
            service_confidence=service_confidence,
            corrections=[],
        )

    current_writer = str(source_writer_id) if source_writer_id is not None else None
    evidence_rows = list(evidences)
    suggestions: list[dict[str, Any]] = []
    for slot_index, slot in enumerate(detect_report_name_slots(raw_text), 1):
        recognized = slot.recognized
        if recognized in scoped_names:
            continue
        candidate_scores = [
            (candidate, _roster_name_score(recognized, candidate))
            for candidate in scoped_names
        ]
        history_support: dict[str, dict[str, float | int]] = {}
        for evidence in evidence_rows:
            corrected = _normalize_roster_name(evidence.corrected_text)
            if (
                evidence.content_type != "resident_name"
                or corrected not in scoped_names
                or evidence.source_kind != "image"
                or evidence.section != slot.section
                or evidence.layout_role != slot.layout_role
            ):
                continue
            if (
                slot.layout_role == "meal_name_list"
                and evidence.column_role is not None
                and evidence.column_role != slot.column_role
            ):
                continue
            recognized_similarity = SequenceMatcher(
                None,
                recognized,
                evidence.recognized_text,
            ).ratio()
            if recognized_similarity < 0.8:
                continue
            row = history_support.setdefault(
                corrected,
                {"count": 0, "boost": 0.0},
            )
            row["count"] = int(row["count"]) + 1
            # The same OCR error corrected by staff is stronger evidence than
            # generic spelling similarity.  It still creates only a candidate.
            boost = (
                0.30
                if recognized_similarity >= 0.98
                else 0.10 * recognized_similarity
            )
            if (
                current_writer
                and evidence.source_writer_id
                and current_writer == evidence.source_writer_id
            ):
                boost += 0.04
            page_similarity = visual_similarity(
                visual_signature,
                evidence.visual_signature,
            )
            if page_similarity is not None and page_similarity >= 0.72:
                boost += 0.05 * page_similarity
            if service_context and evidence.service_context == service_context:
                boost += 0.03
            if slot.section and slot.section in evidence.context_text:
                boost += 0.06
            row["boost"] = max(float(row["boost"]), boost)

        structure_strength = (
            _meal_structure_strength(
                column_role=slot.column_role,
                row_sequence=slot.row_sequence,
                row_group_size=slot.row_group_size,
                service_confidence=service_confidence,
            )
            if slot.layout_role == "meal_name_list"
            else 0.0
        )
        scored: list[tuple[str, float, int, float]] = []
        for candidate, base_score in candidate_scores:
            support = history_support.get(candidate, {"count": 0, "boost": 0.0})
            support_count = int(support["count"])
            score = (
                base_score
                + float(support["boost"])
                + structure_strength
            )
            if support_count >= 2:
                score += min(0.08, 0.025 * support_count)
            scored.append(
                (candidate, min(0.99, score), support_count, base_score)
            )
        scored.sort(
            key=lambda item: (item[1], item[2], item[3], item[0]),
            reverse=True,
        )
        best_score = scored[0][1]
        second_score = scored[1][1] if len(scored) > 1 else 0.0
        best_support_count = scored[0][2]
        best_surface_score = scored[0][3]
        minimum_score = (
            0.80
            if best_support_count >= 1
            else 0.90
            if len(recognized) == 2
            else 0.84
        )
        if slot.layout_role == "meal_name_list":
            required_margin = (
                0.08
                if best_support_count >= 2
                else 0.10
                if structure_strength >= 0.07
                else 0.12
            )
            minimum_surface = 0.72 if len(recognized) >= 3 else 0.84
        else:
            required_margin = 0.08 if best_support_count >= 2 else 0.11
            minimum_surface = 0.0
        best_is_clear = (
            best_score >= minimum_score
            and best_surface_score >= minimum_surface
            and best_score - second_score >= required_margin
        )

        # A failed service inference must not silently remove a name-position
        # candidate.  Show several honest alternatives instead of forcing one
        # resident.  A fixed service scope is smaller, so two are sufficient.
        candidate_limit = 3 if service_context is None else 2
        for rank, (candidate, score, support_count, surface_score) in enumerate(
            scored[:candidate_limit],
            start=1,
        ):
            reason_parts = ["이름 위치", "현재 명단 대조"]
            if service_context:
                reason_parts.append(f"{service_context} 범위")
            else:
                reason_parts.append("전체 활성 명단 fallback")
            if support_count:
                reason_parts.append(f"직원 확정 이력 {support_count}건")
            if rank > 1 or not best_is_clear:
                reason_parts.append("원본 확인 필요")
            suggestions.append(
                {
                    "recognized": recognized,
                    "candidate": candidate,
                    "confidence": round(score, 3),
                    "support_count": support_count,
                    "surface_similarity": round(surface_score, 3),
                    "reason": " · ".join(reason_parts),
                    "rank": rank,
                    "slot_index": slot_index,
                    "section": slot.section,
                    "layout_role": slot.layout_role,
                    "column_role": slot.column_role,
                    "row_sequence": slot.row_sequence,
                    "row_group_size": slot.row_group_size,
                    "structure_strength": round(structure_strength, 3),
                    "score_gap": round(best_score - second_score, 3),
                    "candidate_is_clear": best_is_clear if rank == 1 else False,
                    "auto_applicable": False,
                }
            )

    return RosterAwareDraft(
        text=raw_text,
        service_context=service_context,
        service_confidence=service_confidence,
        corrections=suggestions,
    )


def apply_top_name_candidates_to_draft(
    raw_text: str,
    *,
    candidates: Iterable[dict[str, Any]],
    roster_names: Iterable[str] = (),
    unresolved_placeholder: str | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Apply only each name slot's top candidate to a non-final draft.

    Replacements are located against the immutable raw OCR text and applied
    once per slot.  Exact text is preferred; near-equal name-slot counts use
    document order and larger mismatches require spelling similarity.  This
    prevents an earlier replacement from becoming the target of a later one.
    """

    normalized_roster = {
        _normalize_roster_name(name)
        for name in roster_names
        if _normalize_roster_name(name)
    }
    top_rows: dict[int, dict[str, Any]] = {}
    for item in candidates:
        slot_index = int(item.get("slot_index", 0) or 0)
        rank = int(item.get("rank", 0) or 0)
        candidate = _normalize_roster_name(str(item.get("candidate", "")))
        if (
            slot_index <= 0
            or rank != 1
            or item.get("candidate_is_clear") is False
            or (normalized_roster and candidate not in normalized_roster)
            or slot_index in top_rows
        ):
            continue
        top_rows[slot_index] = dict(item)

    raw_slot_rows = detect_report_name_slots(raw_text)
    raw_slots = [(slot.start, slot.end, slot.recognized) for slot in raw_slot_rows]
    applications: list[dict[str, Any]] = []
    replacements: list[tuple[int, int, str]] = []
    used_ranges: list[tuple[int, int]] = []
    for slot_index in sorted(top_rows):
        row = top_rows[slot_index]
        recognized = str(row.get("recognized", "")).strip()
        candidate = str(row.get("candidate", "")).strip()
        applied = False
        resolved = False
        matched_recognized = recognized
        matched_range: tuple[int, int] | None = None
        position_locked = bool(row.get("position_locked"))
        if (
            position_locked
            and candidate
            and slot_index <= len(raw_slots)
        ):
            positional_row = raw_slot_rows[slot_index - 1]
            positional = raw_slots[slot_index - 1]
            if _is_safe_position_locked_name_slot(positional_row) and not any(
                positional[0] < used_end and positional[1] > used_start
                for used_start, used_end in used_ranges
            ):
                matched_range = (positional[0], positional[1])
                matched_recognized = positional[2]
        if (
            matched_range is None
            and not position_locked
            and recognized
            and candidate
            and not recognized.startswith("이름 위치 ")
        ):
            start = raw_text.find(recognized)
            while start >= 0:
                end = start + len(recognized)
                if not any(start < used_end and end > used_start for used_start, used_end in used_ranges):
                    matched_range = (start, end)
                    break
                start = raw_text.find(recognized, start + 1)
        if matched_range is None and not position_locked and recognized and candidate:
            available_slots = [
                (start, end, token)
                for start, end, token in raw_slots
                if not any(
                    start < used_end and end > used_start
                    for used_start, used_end in used_ranges
                )
            ]
            if (
                abs(len(raw_slots) - len(top_rows)) <= 2
                and slot_index <= len(raw_slots)
            ):
                positional = raw_slots[slot_index - 1]
                if positional in available_slots:
                    matched_range = (positional[0], positional[1])
                    matched_recognized = positional[2]
            elif available_slots:
                expected_position = (
                    (slot_index - 1) / max(1, len(top_rows) - 1)
                )
                scored_slots: list[tuple[float, int, int, str]] = []
                for raw_index, (start, end, token) in enumerate(raw_slots):
                    if (start, end, token) not in available_slots:
                        continue
                    visible_similarity = SequenceMatcher(
                        None,
                        recognized,
                        token,
                    ).ratio()
                    candidate_similarity = SequenceMatcher(
                        None,
                        candidate,
                        token,
                    ).ratio()
                    if max(visible_similarity, candidate_similarity) < 0.5:
                        continue
                    raw_position = raw_index / max(1, len(raw_slots) - 1)
                    position_score = max(0.0, 1.0 - abs(expected_position - raw_position))
                    scored_slots.append(
                        (
                            max(visible_similarity, candidate_similarity)
                            + (0.15 * position_score),
                            start,
                            end,
                            token,
                        )
                    )
                if scored_slots:
                    _, start, end, matched_recognized = max(scored_slots)
                    matched_range = (start, end)
        if matched_range is not None:
            start, end = matched_range
            used_ranges.append((start, end))
            resolved = True
            if matched_recognized != candidate:
                replacements.append((start, end, candidate))
                applied = True
        applications.append(
            {
                **row,
                "recognized": matched_recognized if resolved else recognized,
                "detected_text": recognized,
                "applied_to_draft": applied,
                "resolved_in_draft": resolved,
                "application_reason": (
                    "top_rank_name_slot"
                    if applied
                    else "already_matches_top_candidate"
                    if resolved
                    else "recognized_text_not_located"
                ),
            }
        )

    # Every detected name cell in the review draft must either be an active
    # roster name or be visibly unresolved.  Unknown OCR inventions must not
    # survive in a name-shaped row as though they were real residents.
    if normalized_roster and unresolved_placeholder:
        resolved_ranges = set(used_ranges)
        for slot_index, slot in enumerate(raw_slot_rows, 1):
            slot_range = (slot.start, slot.end)
            if slot_range in resolved_ranges or slot.recognized in normalized_roster:
                continue
            replacements.append((slot.start, slot.end, unresolved_placeholder))
            used_ranges.append(slot_range)
            applications.append(
                {
                    "recognized": slot.recognized,
                    "detected_text": slot.recognized,
                    "candidate": unresolved_placeholder,
                    "confidence": 0.0,
                    "support_count": 0,
                    "reason": "이름 위치 · 활성 명단에 없는 판독 · 직접 확인 필요",
                    "rank": 1,
                    "slot_index": slot_index,
                    "section": slot.section,
                    "layout_role": slot.layout_role,
                    "auto_applicable": False,
                    "applied_to_draft": True,
                    "resolved_in_draft": False,
                    "application_reason": "unresolved_name_placeholder",
                }
            )

    draft_text = raw_text
    for start, end, replacement in sorted(replacements, reverse=True):
        draft_text = f"{draft_text[:start]}{replacement}{draft_text[end:]}"
    return draft_text, applications


def apply_safe_context_corrections_to_draft(
    draft_text: str,
    *,
    source_writer_id: UUID | str | None = None,
    evidences: Iterable[CorrectionEvidence] = (),
    roster_names: Iterable[str] = (),
) -> tuple[str, list[dict[str, Any]]]:
    """Apply only allow-listed or repeatedly confirmed wording to a draft.

    This never touches the raw OCR.  Names, numbers, times, medication and
    incident facts are excluded; they remain staff-review items.
    """

    replacements: dict[str, tuple[str, str]] = {
        source: (target, "기관 업무 표현 사전")
        for source, target in SAFE_CONTEXT_PHRASE_CORRECTIONS
        if source != target and len(source) >= 2
    }
    del source_writer_id
    ignored_memory_pairs: dict[tuple[str, str], int] = {}
    for evidence in evidences:
        source = evidence.recognized_text.strip()
        target = evidence.corrected_text.strip()
        if (
            evidence.content_type in PROTECTED_CONTENT_TYPES
            or not source
            or not target
            or source == target
            or re.search(r"\d", f"{source}{target}")
            or target not in SAFE_CONTEXT_CORRECTION_TARGETS
        ):
            continue
        occurrence_count = draft_text.count(source)
        if occurrence_count:
            key = (source, target)
            ignored_memory_pairs[key] = (
                ignored_memory_pairs.get(key, 0) + occurrence_count
            )

    result = draft_text
    applied: list[dict[str, Any]] = []
    meal_heading_pattern = re.compile(
        r"(?m)^(?P<prefix>\s*[<\[【(（]?\s*)"
        r"(?P<meal>아침|점심|저녁)\s*설사"
        r"(?P<suffix>\s*(?:확인|하신\s*분|드신\s*분|관련)?"
        r"\s*[>\]】)）]?\s*)$"
    )

    meal_heading_changes: dict[tuple[str, str], int] = {}

    def replace_meal_heading(match: re.Match[str]) -> str:
        if not _meal_heading_has_table_evidence(
            draft_text,
            content_start=match.end(),
            roster_names=roster_names,
        ):
            return match.group(0)
        source = f"{match.group('meal')} 설사"
        target = f"{match.group('meal')}식사"
        key = (source, target)
        meal_heading_changes[key] = meal_heading_changes.get(key, 0) + 1
        return f"{match.group('prefix')}{target}{match.group('suffix')}"

    result = meal_heading_pattern.sub(replace_meal_heading, result)
    for (source, target), occurrence_count in meal_heading_changes.items():
        applied.append(
            {
                "recognized": source,
                "candidate": target,
                "content_type": "general",
                "source": "contextual_heading_lexicon",
                "reason": "식사 보고 제목·목록 머리말 문맥",
                "rule_id": "meal_heading_time_diarrhea_to_meal",
                "section": target,
                "layout_role": "report_heading",
                "confidence": 0.99,
                "occurrence_count": occurrence_count,
                "auto_applicable": True,
                "applied_to_draft": True,
                "review_required": True,
                "application_reason": "safe_context_draft",
            }
        )
    for (source, target), occurrence_count in ignored_memory_pairs.items():
        applied.append(
            {
                "recognized": source,
                "candidate": target,
                "content_type": "general",
                "source": "confirmed_memory_context_mismatch",
                "reason": "단독 토큰 수정 이력은 다른 문서에 자동 재사용하지 않음",
                "occurrence_count": occurrence_count,
                "auto_applicable": False,
                "applied_to_draft": False,
                "review_required": True,
                "application_reason": "context_mismatch_ignored",
            }
        )
    for source, target in CONTEXT_ONLY_CORRECTIONS.items():
        # '확인' 상태가 들어가는 표 칸에서만 흔한 OCR 오독을 고친다.
        # 일반 문장에 실제로 등장할 수 있는 같은 글자는 건드리지 않는다.
        status_slots = [
            slot
            for slot in detect_report_status_slots(result)
            if slot.recognized == source
        ]
        occurrence_count = len(status_slots)
        for slot in reversed(status_slots):
            result = f"{result[:slot.start]}{target}{result[slot.end:]}"
        if occurrence_count:
            applied.append(
                {
                    "recognized": source,
                    "candidate": target,
                    "content_type": "general",
                    "source": "contextual_lexicon",
                    "reason": "장기요양 상태 칸 문맥",
                    "occurrence_count": occurrence_count,
                    "auto_applicable": True,
                    "applied_to_draft": True,
                    "application_reason": "safe_context_draft",
                }
            )
    for source in sorted(replacements, key=len, reverse=True):
        target, reason = replacements[source]
        occurrence_count = result.count(source)
        if not occurrence_count:
            continue
        result = result.replace(source, target)
        applied.append(
            {
                "recognized": source,
                "candidate": target,
                "content_type": "general",
                "source": "confirmed_context_or_lexicon",
                "reason": reason,
                "occurrence_count": occurrence_count,
                "auto_applicable": True,
                "applied_to_draft": True,
                "application_reason": "safe_context_draft",
            }
        )
    return result, applied


def build_ocr_review_warnings(value: str | None) -> list[str]:
    """Return short staff-review warnings without rewriting OCR text."""

    if not value:
        return []
    warnings: list[str] = []
    if TIME_PATTERN.search(value) or re.search(r"\d", value):
        warnings.append("숫자·시간·횟수·수량은 원본과 확인해 주세요.")
    if any(term in value for term in MEDICATION_TERMS):
        warnings.append("약·투약·처방 내용은 원본과 확인해 주세요.")
    if any(term in value for term in EXCRETION_TERMS):
        warnings.append("배변·소변·기저귀 내용은 원본과 확인해 주세요.")
    if re.search(r"\d+\s*(?:됨|했음|하심|하셨음)(?:[.!。]|\s*$)", value):
        warnings.append("문맥상 어색한 숫자 결합이 있어 해당 줄을 확인해 주세요.")
    return warnings[:4]


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
) -> list[dict[str, str | None]]:
    """Extract conservative one-to-one token edits from a confirmed review."""
    if not raw_text or not corrected_text or raw_text == corrected_text:
        return []
    original = TOKEN_PATTERN.findall(raw_text)
    corrected = TOKEN_PATTERN.findall(corrected_text)
    pairs: list[dict[str, str | None]] = []
    name_slots = detect_report_name_slots(raw_text)
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
            matching_slot = next(
                (slot for slot in name_slots if slot.recognized == source),
                None,
            )
            pairs.append(
                {
                    "recognized_text": source,
                    "corrected_text": target,
                    "content_type": content_type,
                    "context_text": _context_excerpt(raw_text, source),
                    "section": (
                        matching_slot.section
                        if content_type == "resident_name" and matching_slot is not None
                        else None
                    ),
                    "layout_role": (
                        matching_slot.layout_role
                        if content_type == "resident_name" and matching_slot is not None
                        else None
                    ),
                }
            )
            if len(pairs) >= 24:
                break
        if len(pairs) >= 24:
            break
    unique: dict[tuple[str, str], dict[str, str | None]] = {}
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
    service_context: str | None = None,
    source_kind: str | None = None,
    limit: int = 8,
) -> list[dict[str, Any]]:
    if not value:
        return []
    current_context = context_text or value
    current_writer = str(source_writer_id) if source_writer_id is not None else None
    active_resident_names = {
        _normalize_roster_name(name)
        for name in resident_names or []
        if name.strip()
    }
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
            if evidence.content_type == "resident_name":
                corrected_name = _normalize_roster_name(
                    evidence.corrected_text
                )
                if corrected_name not in active_resident_names:
                    continue
                if (
                    service_context is not None
                    and evidence.service_context is not None
                    and service_context != evidence.service_context
                ):
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
            service_similarity = (
                1.0
                if service_context is not None
                and evidence.service_context == service_context
                else 0.0
            )
            source_kind_similarity = (
                1.0
                if source_kind is not None and evidence.source_kind == source_kind
                else 0.0
            )
            score = (
                (0.60 * raw_similarity)
                + (0.18 * context_similarity)
                + (0.08 * writer_similarity)
                + (0.06 * (page_visual_similarity or 0.0))
                + (0.05 * service_similarity)
                + (0.03 * source_kind_similarity)
            )
            reason_parts = [f"과거 오인 {raw_similarity:.0%} 유사"]
            if context_similarity >= 0.45:
                reason_parts.append("문맥 유사")
            if writer_similarity:
                reason_parts.append("같은 작성자")
            if page_visual_similarity is not None and page_visual_similarity >= 0.7:
                reason_parts.append("글씨 영역 유사")
            if service_similarity:
                reason_parts.append("같은 서비스")
            if source_kind_similarity:
                reason_parts.append("같은 자료 종류")
            key = (recognized, evidence.corrected_text)
            grouped.setdefault(key, []).append(
                (evidence, score, " · ".join(reason_parts))
            )

    candidates: list[dict[str, Any]] = []
    for (recognized, corrected), matches in grouped.items():
        matches.sort(key=lambda item: item[1], reverse=True)
        best_evidence, best_score, reason = matches[0]
        support_count = len({match[0].event_id for match in matches})
        if best_score < 0.58 and support_count < 2:
            continue
        confidence = min(0.99, best_score + min(0.15, 0.05 * (support_count - 1)))
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
                "reason": (
                    f"반복 확인 {support_count}건 · {reason}"
                    if support_count >= 2
                    else reason
                ),
                "source_event_ids": [
                    match[0].event_id for match in matches[:5]
                ],
                "auto_applicable": False,
            }
        )
    candidates.sort(
        key=lambda candidate: (
            candidate["support_count"] >= 2,
            candidate["support_count"],
            candidate["confidence"],
        ),
        reverse=True,
    )
    return candidates[:limit]
