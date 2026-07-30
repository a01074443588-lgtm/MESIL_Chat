from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import ValidationError

from .config import settings
from .prototype_ai import DAILY_DOCUMENT_TYPES
from .schemas import RecordDraft

logger = logging.getLogger(__name__)


class LocalAiError(RuntimeError):
    """AI 연결·응답 오류를 채팅 원문과 분리합니다."""


@dataclass(frozen=True)
class AiReviewResult:
    draft: dict[str, Any]
    provider: str
    model: str
    elapsed_ms: int
    attempts: list[dict[str, Any]]


@dataclass(frozen=True)
class RoomSummaryResult:
    summary: str
    provider: str
    model: str
    elapsed_ms: int


def _clean_json_text(value: str) -> str:
    value = re.sub(r"<think>.*?</think>", "", value, flags=re.DOTALL | re.IGNORECASE)
    value = value.strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    value = value.strip()
    if not value.startswith("{"):
        start = value.find("{")
        end = value.rfind("}")
        if start >= 0 and end > start:
            value = value[start : end + 1]
    return value.strip()


def _stub_refine(current_draft: dict[str, Any]) -> dict[str, Any]:
    # 자동화 시험에서는 네트워크 없이도 담당자 확인 경계를 검증합니다.
    return {
        **current_draft,
        "corrected_text": str(current_draft["corrected_text"]).strip(),
        "summary": str(current_draft["summary"]).strip(),
    }


def _validated_draft(content: str) -> dict[str, Any]:
    try:
        result = json.loads(_clean_json_text(content))
    except json.JSONDecodeError as exc:
        raise LocalAiError("AI가 올바른 JSON 초안을 반환하지 않았습니다.") from exc
    if not isinstance(result, dict):
        raise LocalAiError("AI가 업무기록 객체를 반환하지 않았습니다.")
    try:
        return RecordDraft.model_validate(result).model_dump(mode="json")
    except ValidationError as exc:
        raise LocalAiError("AI 결과가 업무기록 형식과 맞지 않습니다.") from exc


def _validated_room_summary(
    content: str,
    *,
    source_count: int | None = None,
    require_all_references: bool = False,
) -> str:
    try:
        result = json.loads(_clean_json_text(content))
    except json.JSONDecodeError as exc:
        raise LocalAiError("AI가 올바른 대화 요약 형식을 반환하지 않았습니다.") from exc
    summary = str(result.get("summary", "")).strip() if isinstance(result, dict) else ""
    if not summary:
        raise LocalAiError("AI 대화 요약 내용이 비어 있습니다.")
    if len(re.sub(r"\s+", "", summary)) < 30:
        raise LocalAiError("AI 대화 요약이 너무 짧아 사용할 수 없습니다.")
    if len(summary) > 6000:
        raise LocalAiError("AI 대화 요약이 너무 길어 안전하게 사용할 수 없습니다.")
    if source_count:
        embedded_references = {
            int(reference)
            for reference in re.findall(r"\[(\d+)\]", summary)
        }
        declared_references = {
            int(reference)
            for reference in result.get("references", [])
            if isinstance(reference, int)
        }
        valid_embedded_references = {
            reference
            for reference in embedded_references
            if 1 <= reference <= source_count
        }
        valid_declared_references = {
            reference
            for reference in declared_references
            if 1 <= reference <= source_count
        }
        valid_references = sorted(
            reference
            for reference in valid_embedded_references | valid_declared_references
        )
        if not valid_references:
            raise LocalAiError("AI 대화 요약에 확인 가능한 근거 번호가 없습니다.")
        if require_all_references:
            missing_references = sorted(
                set(range(1, source_count + 1))
                - (valid_embedded_references | valid_declared_references)
            )
            if missing_references:
                missing_text = ", ".join(
                    f"[{reference}]" for reference in missing_references[:10]
                )
                raise LocalAiError(
                    "AI 정리에서 선택한 근거가 빠졌습니다: "
                    f"{missing_text}"
                )
            missing_inline_references = sorted(
                valid_declared_references - valid_embedded_references
            )
            if missing_inline_references:
                summary += "\n\n참고한 근거: " + ", ".join(
                    f"[{reference}]" for reference in missing_inline_references
                )
        elif not valid_embedded_references and valid_declared_references:
            summary += "\n\n근거 대화: " + ", ".join(
                f"[{reference}]" for reference in sorted(valid_declared_references)
            )
    return summary


def _safe_room_summary_references(summary: str, source_count: int) -> str:
    """근거 목록에 없는 번호를 사실 근거처럼 표시하지 않습니다."""

    def replace_reference(match: re.Match[str]) -> str:
        reference = int(match.group(1))
        if 1 <= reference <= source_count:
            return match.group(0)
        return "[근거 확인 필요]"

    return re.sub(r"\[(\d+)\]", replace_reference, summary)


RECORD_SUMMARY_FACT_TERMS = (
    "식사",
    "섭취",
    "물",
    "수분",
    "기저귀",
    "엉치",
    "붉",
    "발적",
    "체위",
    "간호",
    "전달",
    "비틀",
    "넘어",
    "낙상",
    "통증",
    "부축",
    "보행",
    "귀가",
    "집에",
    "말벗",
    "안정",
    "화장실",
    "배변",
    "소변",
    "대변",
    "목욕",
    "세면",
    "상처",
    "부종",
    "혈압",
    "혈당",
    "체온",
    "맥박",
    "호흡",
    "복약",
    "투약",
    "구토",
    "설사",
    "기침",
    "프로그램",
    "참여",
    "거부",
    "보호자",
    "통화",
    "연락",
    "면담",
    "상담",
    "불편",
    "양치",
    "구강",
    "변비",
    "배출",
    "욕창",
    "골절",
    "감염",
    "탈수",
    "폐렴",
    "당뇨",
    "고혈압",
    "치매",
    "뇌졸중",
)
# 같은 관찰을 현장에서 흔히 바꾸어 말하는 최소 동의어만 허용합니다.
# 진단·수치·사람·신체 부위는 이 목록에 넣지 않아 문자 그대로 검증합니다.
RECORD_SUMMARY_FACT_EQUIVALENT_GROUPS = (
    ("물", "수분"),
    ("붉", "발적"),
)
RECORD_SUMMARY_MEASUREMENT_PATTERN = re.compile(
    r"(?:"
    r"\d{1,3}\s*/\s*\d{1,3}"
    r"|"
    r"\d+(?:[.,]\d+)?\s*"
    r"(?:ml|cc|cm|kg|%|회|분|시간|시|도|일|월|mmhg|mg/dl|bpm)"
    r")",
    re.IGNORECASE,
)
RECORD_SUMMARY_RESIDENT_PATTERN = re.compile(
    r"(?:"
    r"(?:시설|주간|방문)\(가명\)\d+"
    r"|"
    r"(?:시설|주간|방문)-어르신-\d+\(가명\)"
    r")"
)
RECORD_SUMMARY_REQUIRED_HEADINGS = (
    "[한눈에 보기]",
    "[먼저 확인]",
    "[이미 한 일]",
    "[다음 업무 제안]",
)
RECORD_SUMMARY_NEGATED_CRITICAL_FACTS = (
    (
        "통증",
        re.compile(r"(?:통증|아프).{0,16}?(?:없|않|아니|부인)"),
        re.compile(r"(?:통증|아프)"),
    ),
    (
        "넘어짐",
        re.compile(r"(?:넘어지|넘어졌|넘어짐|낙상).{0,16}?(?:없|않|아니|미발생)"),
        re.compile(r"(?:넘어지|넘어졌|넘어짐|낙상)"),
    ),
)


def _record_summary_source_text(entries: list[dict[str, Any]]) -> str:
    return "\n".join(
        " ".join(
            str(entry.get(key, "")).strip()
            for key in ("resident", "type", "body")
            if str(entry.get(key, "")).strip()
        )
        for entry in entries
    )


def _validate_record_summary_faithfulness(
    summary: str,
    entries: list[dict[str, Any]],
    *,
    require_headings: bool = True,
) -> None:
    """함축은 허용하되 선택 근거에 없는 사람·위험 사실·수치는 막습니다."""

    source_text = _record_summary_source_text(entries)
    source_compact = re.sub(r"\s+", "", source_text).casefold()
    summary_without_references = re.sub(r"\[\d+\]", "", summary)
    summary_compact = re.sub(r"\s+", "", summary_without_references).casefold()

    if require_headings:
        missing_headings = [
            heading
            for heading in RECORD_SUMMARY_REQUIRED_HEADINGS
            if heading not in summary
        ]
        if missing_headings:
            raise LocalAiError(
                "AI 정리가 한눈에 보는 업무 브리핑 형식을 따르지 않았습니다: "
                + ", ".join(missing_headings)
            )

    source_residents = set(RECORD_SUMMARY_RESIDENT_PATTERN.findall(source_text))
    summary_residents = set(RECORD_SUMMARY_RESIDENT_PATTERN.findall(summary))
    invented_residents = sorted(summary_residents - source_residents)
    if invented_residents:
        raise LocalAiError(
            "AI 정리에 원문에 없는 어르신이 생겼습니다: "
            + ", ".join(invented_residents[:5])
        )

    equivalent_terms = {
        term.casefold(): tuple(member.casefold() for member in group)
        for group in RECORD_SUMMARY_FACT_EQUIVALENT_GROUPS
        for term in group
    }
    invented_facts = []
    for term in RECORD_SUMMARY_FACT_TERMS:
        folded_term = term.casefold()
        if folded_term not in summary_compact:
            continue
        source_terms = equivalent_terms.get(folded_term, (folded_term,))
        if not any(source_term in source_compact for source_term in source_terms):
            invented_facts.append(term)
    if invented_facts:
        raise LocalAiError(
            "AI 정리에 원문에 없는 내용이 생겼습니다: "
            + ", ".join(invented_facts[:8])
        )

    source_measurements = {
        re.sub(r"\s+", "", value).casefold()
        for value in RECORD_SUMMARY_MEASUREMENT_PATTERN.findall(source_text)
    }
    summary_measurements = {
        re.sub(r"\s+", "", value).casefold()
        for value in RECORD_SUMMARY_MEASUREMENT_PATTERN.findall(
            summary_without_references
        )
    }
    invented_measurements = sorted(summary_measurements - source_measurements)
    if invented_measurements:
        raise LocalAiError(
            "AI 정리에 원문에 없는 수치·시간이 생겼습니다: "
            + ", ".join(invented_measurements[:8])
        )

    reversed_negative_facts = [
        name
        for name, negative_pattern, mention_pattern in (
            RECORD_SUMMARY_NEGATED_CRITICAL_FACTS
        )
        if negative_pattern.search(source_text)
        and mention_pattern.search(summary_without_references)
        and not negative_pattern.search(summary_without_references)
    ]
    if reversed_negative_facts:
        raise LocalAiError(
            "AI 정리가 원문의 부정 표현을 반대로 바꿨습니다: "
            + ", ".join(reversed_negative_facts)
        )


def _clean_safe_record_body(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\[SUBMISSION-MEDIA[^\]]*\]\s*", "", text)
    text = re.sub(
        r"^\[(?:음성 받아쓰기|이미지 글자 판독)\s*·[^\]]+\]\s*$",
        "",
        text,
        flags=re.MULTILINE,
    )
    text = re.sub(r"^\[답글\s*·[^\]]+\]\s*", "", text, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", text).strip()


def _safe_selected_record_summary(
    entries: list[dict[str, Any]],
    *,
    purpose: str,
) -> str:
    """AI가 실패해도 원문 복사 대신 현장용 안전 브리핑을 만듭니다."""

    topic_terms = {
        "식사·수분": ("식사", "섭취", "물", "수분", "삼킴"),
        "건강·간호": ("혈압", "혈당", "체온", "통증", "기침", "복약", "투약"),
        "피부": ("피부", "발적", "붉", "엉치", "상처", "체위"),
        "이동·안전": ("보행", "부축", "비틀", "낙상", "넘어", "휠체어"),
        "배변·위생": ("배변", "소변", "대변", "기저귀", "목욕", "세면", "화장실"),
        "정서·인지": ("불안", "귀가", "집에", "배회", "말벗", "안정"),
        "보호자": ("보호자", "가족", "통화", "면담", "상담"),
        "프로그램": ("프로그램", "활동", "참여", "거부"),
    }
    priority_terms = (
        "비틀",
        "낙상",
        "넘어",
        "통증",
        "발적",
        "붉",
        "상처",
        "혈압",
        "혈당",
        "체온",
        "기침",
        "복약",
        "투약",
        "불안",
    )
    action_terms = (
        "도와",
        "도왔",
        "지원",
        "제공",
        "전달",
        "말씀드",
        "부축",
        "변경",
        "도포",
        "안내",
        "확인",
        "말벗",
        "연락",
        "통화",
        "설명",
    )
    pending_terms = (
        "예정",
        "계획",
        "요청",
        "기로 했",
        "하겠습니다",
        "할 예정",
        "해 주세요",
        "해주세요",
        "확인 필요",
        "필요합니다",
        "다시 확인",
        "이어 확인",
        "계속 확인",
        "관찰 중",
        "알려",
        "재확인",
        "다음 ",
        "추후",
    )
    completed_markers = (
        "했습니다",
        "하였습니다",
        "드렸",
        "도왔",
        "확인함",
        "전달함",
        "부축함",
        "변경함",
        "도포함",
        "제공함",
        "안내함",
        "말씀드림",
        "지원함",
        "완료함",
        "하심",
    )

    cleaned_entries: list[tuple[int, str, str]] = []
    topic_counts: dict[str, int] = {}
    for index, entry in enumerate(entries, 1):
        resident = str(entry.get("resident", "")).strip() or "일반 업무"
        body = _clean_safe_record_body(entry.get("body", ""))
        cleaned_entries.append((index, resident, body))
        for topic, terms in topic_terms.items():
            if any(term in body for term in terms):
                topic_counts[topic] = topic_counts.get(topic, 0) + 1

    def evidence_sentences(body: str) -> list[str]:
        return [
            re.sub(r"\s+", " ", sentence).strip(" -")
            for sentence in re.split(r"[\r\n]+|(?<=[.!?。])\s+", body)
            if sentence.strip(" -")
        ]

    sections: dict[str, list[tuple[str, int]]] = {
        "priority": [],
        "completed": [],
        "pending": [],
    }
    used_sentences: dict[str, set[tuple[str, str]]] = {
        "priority": set(),
        "completed": set(),
        "pending": set(),
    }
    for index, resident, body in cleaned_entries:
        for sentence in evidence_sentences(body):
            sentence = sentence[:220].rstrip()
            sentence_key = (resident, re.sub(r"\s+", "", sentence).casefold())
            has_pending = any(term in sentence for term in pending_terms)
            has_priority = any(term in sentence for term in priority_terms)
            has_completed = (
                any(term in sentence for term in action_terms)
                and any(marker in sentence for marker in completed_markers)
            )
            target_sections = tuple(
                section
                for section, matches in (
                    ("priority", has_priority),
                    ("completed", has_completed),
                    ("pending", has_pending),
                )
                if matches
            )
            for section in target_sections:
                if sentence_key in used_sentences[section]:
                    continue
                sections[section].append(
                    (f"- {resident}: {sentence} [{index}]", index)
                )
                used_sentences[section].add(sentence_key)

    def rendered_section(
        items: list[tuple[str, int]],
        *,
        empty_text: str,
    ) -> list[str]:
        visible = [text for text, _ in items[:6]]
        omitted = items[6:]
        if omitted:
            references = "".join(
                f"[{number}]" for number in dict.fromkeys(number for _, number in omitted)
            )
            visible.append(
                f"- 추가 {len(omitted)}건이 있습니다. 근거 {references}"
            )
        return visible or [empty_text]

    topic_text = ", ".join(
        f"{topic} {count}건"
        for topic, count in sorted(
            topic_counts.items(),
            key=lambda item: (-item[1], item[0]),
        )
    ) or "일반 업무 기록"
    residents = {
        resident for _, resident, _ in cleaned_entries if resident != "일반 업무"
    }
    resident_text = ", ".join(sorted(residents)) or "일반 업무"
    all_references = "".join(f"[{index}]" for index, _, _ in cleaned_entries)

    return "\n".join(
        [
            "[한눈에 보기]",
            f"{purpose} 근거 {len(entries)}건, 어르신 {len(residents)}명의 현재 기록입니다. "
            f"대상은 {resident_text}이며 확인된 주제는 {topic_text}입니다. "
            f"근거 {all_references}",
            "",
            "[먼저 확인]",
            *rendered_section(
                sections["priority"],
                empty_text="- 원문에 즉시 확인이 명시된 위험 변화는 없습니다.",
            ),
            "",
            "[이미 한 일]",
            *rendered_section(
                sections["completed"],
                empty_text="- 원문에 완료된 지원·전달 내용이 명확하지 않습니다.",
            ),
            "",
            "[다음 업무 제안]",
            *rendered_section(
                sections["pending"],
                empty_text=(
                    "- 원문에 명시된 후속 요청은 없습니다. 새 사실·진단·수치를 "
                    "만들지 말고 필요할 때 담당자가 원문을 확인하세요."
                ),
            ),
        ]
    ).strip()


def _ensure_record_briefing_sections(
    summary: str,
    entries: list[dict[str, Any]],
    *,
    purpose: str,
) -> str:
    """모델이 유용한 함축문만 반환해도 안전 항목을 붙여 현장 화면으로 정규화합니다."""

    if all(heading in summary for heading in RECORD_SUMMARY_REQUIRED_HEADINGS):
        return summary
    overview = re.sub(
        r"(?:즉시\s*)?확인할 변화 없음[.\s]*$",
        "",
        summary.strip(),
    ).strip()
    safe_briefing = _safe_selected_record_summary(entries, purpose=purpose)
    first_detail_heading = safe_briefing.find("[먼저 확인]")
    safe_details = (
        safe_briefing[first_detail_heading:]
        if first_detail_heading >= 0
        else (
            "[먼저 확인]\n- 원문 근거를 확인해 주세요.\n\n"
            "[이미 한 일]\n- 원문 근거를 확인해 주세요.\n\n"
            "[다음 업무 제안]\n- 원문 근거를 확인해 주세요."
        )
    )
    return f"[한눈에 보기]\n{overview}\n\n{safe_details}".strip()


def _room_summary_prompt(purpose: str | None = None) -> str:
    if purpose:
        purpose_instruction = f"""
이번 정리 목적은 '{purpose}' 업무 브리핑입니다.
사용자가 직접 고른 모든 근거를 검토하되 원문을 사람별로 길게 다시 쓰지 마세요.
반복되는 내용은 하나로 합치고, 정상적인 일상 경과는 한 문장으로 묶으세요.

정리 방법:
- 반드시 아래 네 제목만 사용하세요.
  [한눈에 보기]
  선택 범위의 핵심 변화와 반복 경향을 3~5문장으로 함축합니다.
  [먼저 확인]
  위험 신호, 아직 결과가 없거나 재확인이 명시된 어르신만 우선순위 순으로
  한 줄씩 씁니다. 없으면 '즉시 확인할 변화 없음'이라고 씁니다.
  [이미 한 일]
  직원이 실제로 시행한 지원·조치·전달을 중복 없이 한 줄씩 씁니다.
  [다음 업무 제안]
  원문에 근거가 있는 재확인·경과관찰·인수인계만 제안합니다. 진단, 치료,
  공식 평가점수 또는 원문에 없는 업무는 만들지 않습니다.
- 같은 어르신의 같은 사건은 하나로 합치고, 서로 다른 중요 변화는 보존하세요.
- 이름·시간·수치·약·신체 부위는 원문에 있는 표현을 정확히 유지하세요.
- 방 이름, 작성자, 파일명, SUBMISSION 표식 같은 시스템 정보는 기록 문장에
  넣지 마세요.
- 중요한 판단과 조치 문장 끝에는 관련 근거번호를 [1]처럼 표시하세요.
  반복되는 정상 경과는 문장마다 번호를 되풀이하지 말고 references 배열에 빠짐없이
  넣으세요. 1번부터 마지막 번호까지 모든 번호를 references 배열에 포함해야 합니다.
- 측정 수치만으로 진단명을 만들지 마세요. 예를 들어 혈압 168/91은 '고혈압'으로
  바꾸지 말고 원래 수치를 유지하며 '혈압 수치 재확인 필요'처럼 정리하세요.
- 원문에 '안정', '양호', '호전'이 없으면 상태가 안정적이거나 양호하다고
  판단하지 마세요. 증상이 없다는 기록은 해당 증상이 없었다는 사실만 유지하세요.
- 원문이 불명확하면 내용을 버리지 말고 '[확인 필요] ...'로 남기세요.
"""
    else:
        purpose_instruction = """
검색 조건과 직접 관련된 핵심만 고르되, 서로 다른 주요 사실은 생략하지 마세요.
"""
    return f"""
당신은 장기요양기관 내부 채팅의 검색 결과를 요약하는 보조자입니다.
제공된 대화, 댓글, 판독문에 없는 사실·진단·조치·사람·수치·시간을 만들지 마세요.
요약은 원문의 문장을 일부 건너뛰거나 그대로 이어 붙이는 일이 아닙니다.
같은 뜻은 합치고 반복은 제거하되 서로 다른 핵심 사실은 보존하세요.
중요한 사실 끝에 근거 메시지 번호를 [1]처럼 표시하세요.
공식 평가점수나 진단을 확정하지 마세요.
간호 기록은 건강 상태·증상·수치·실제 간호 확인과 조치만,
급여제공 기록은 실제 제공한 돌봄과 어르신 반응만,
상담 기록은 실제 보호자·가족과의 연락·면담 내용과 결과만,
프로그램 기록은 실제 활동 참여 내용과 반응만 골라 쓰세요.
일반 업무는 공지·시설 업무·직원 협조 내용을 간결히 정리하세요.
확인 필요는 불명확한 이름·시간·수치·약·신체 부위와 서로 충돌하는 내용을
없애거나 임의로 확정하지 말고 확인 문장으로 정리하세요.
{purpose_instruction}
반드시 {{"summary":"요약문","references":[1,2]}} JSON 객체 하나만 반환하세요.
references에는 실제 검토한 모든 근거번호를 넣으세요. 선택 범위 정리에서는
1번부터 마지막 번호까지 하나도 빠뜨리지 마세요.
""".strip()


def _numbered_room_entries(entries: list[dict[str, Any]]) -> str:
    lines = [
        "아래 대화는 각 줄의 [번호]가 근거번호입니다.",
        "references 배열에는 요약에 실제 사용한 번호만 넣으세요.",
        "",
    ]
    for index, entry in enumerate(entries, 1):
        number = entry.get("number")
        if not isinstance(number, int):
            number = index
        details = {key: value for key, value in entry.items() if key != "number"}
        lines.append(
            f"[{number}] {json.dumps(details, ensure_ascii=False)}"
        )
    return "\n".join(lines)


_RESIDENT_ALIAS_PATTERN = re.compile(
    r"(?:"
    r"(?:시설|주간|방문)\(가명\)\d+"
    r"|"
    r"(?:시설|주간|방문)-어르신-\d+\(가명\)"
    r")"
)
_UNRESOLVED_RESIDENT_ALIAS_PATTERN = re.compile(r"\bRESIDENT_\d{3}\b")


def _replace_nested_text(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, str):
        result = value
        for source, replacement in replacements.items():
            result = result.replace(source, replacement)
        return result
    if isinstance(value, list):
        return [_replace_nested_text(item, replacements) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_nested_text(item, replacements)
            for key, item in value.items()
        }
    return value


def _alias_resident_names(value: Any) -> tuple[Any, dict[str, str]]:
    serialized = json.dumps(value, ensure_ascii=False)
    names = sorted(set(_RESIDENT_ALIAS_PATTERN.findall(serialized)))
    original_to_alias = {
        name: f"RESIDENT_{index:03d}"
        for index, name in enumerate(names, 1)
    }
    return _replace_nested_text(value, original_to_alias), original_to_alias


def _restore_resident_names(value: str, original_to_alias: dict[str, str]) -> str:
    restored = value
    for original, alias in original_to_alias.items():
        restored = restored.replace(alias, original)
    if _UNRESOLVED_RESIDENT_ALIAS_PATTERN.search(restored):
        raise LocalAiError("AI가 어르신 식별코드를 정확히 보존하지 못했습니다.")
    return restored


def _room_summary_response_format(source_count: int) -> dict[str, Any]:
    # NVIDIA Ultra의 공식 JSON 모드를 사용한 뒤 아래 Pydantic·근거번호
    # 검증으로 스키마와 원문 충실도를 다시 확인한다.
    del source_count
    return {"type": "json_object"}


def _room_summary_with_nvidia(
    entries: list[dict[str, Any]],
    *,
    purpose: str | None = None,
) -> str:
    api_key = (
        settings.nvidia_api_key.get_secret_value().strip()
        if settings.nvidia_api_key is not None
        else ""
    )
    if not api_key:
        key_path = Path(settings.nvidia_api_key_file)
        if key_path.is_file():
            api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key or api_key == "PASTE_NVIDIA_API_KEY_HERE":
        raise LocalAiError("Nemotron API 키가 설정되지 않았습니다.")
    aliased_entries, resident_aliases = _alias_resident_names(entries)
    system_prompt = _room_summary_prompt(purpose)
    if resident_aliases:
        system_prompt += (
            "\nRESIDENT_001 같은 표기는 어르신 식별코드입니다. "
            "철자와 번호를 절대 번역하거나 바꾸지 마세요. "
            "원문에 없는 낙상·상처·통증·진단·처치·수치를 추론하지 마세요."
        )
    is_selected_record_summary = purpose is not None
    max_tokens = (
        min(1200, max(700, 480 + len(entries) * 45))
        if is_selected_record_summary
        else 1800
    )
    timeout_seconds = (
        min(
            settings.nvidia_api_timeout_seconds,
            settings.nvidia_summary_timeout_seconds,
        )
        if is_selected_record_summary
        else settings.nvidia_api_timeout_seconds
    )
    response = _request_json(
        url=f"{settings.nvidia_api_base_url.rstrip('/')}/chat/completions",
        payload={
            "model": settings.nvidia_nemotron_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": _numbered_room_entries(aliased_entries),
                },
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": max_tokens,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": _room_summary_response_format(len(entries)),
            "stream": False,
        },
        timeout_seconds=timeout_seconds,
        headers={"Authorization": f"Bearer {api_key}"},
        retry_transient=not is_selected_record_summary,
    )
    try:
        content = str(response["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalAiError("Nemotron API 응답에 대화 요약이 없습니다.") from exc
    content = _restore_resident_names(content, resident_aliases)
    summary = _validated_room_summary(
        content,
        source_count=len(entries),
        require_all_references=purpose is not None,
    )
    if purpose is not None:
        summary = _ensure_record_briefing_sections(
            summary,
            entries,
            purpose=purpose,
        )
    _validate_record_summary_faithfulness(
        summary,
        entries,
        require_headings=purpose is not None,
    )
    return summary


def _room_summary_with_ollama(
    entries: list[dict[str, Any]],
    *,
    model: str,
    purpose: str | None = None,
) -> str:
    response = _request_json(
        url=f"{settings.ai_review_base_url.rstrip('/')}/api/chat",
        payload={
            "model": model,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _room_summary_prompt(purpose)},
                {
                    "role": "user",
                    "content": _numbered_room_entries(entries),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
                "num_predict": 3000 if purpose else 1800,
            },
            "keep_alive": "10m",
        },
        timeout_seconds=settings.ai_review_timeout_seconds,
    )
    try:
        content = str(response["message"]["content"])
    except (KeyError, TypeError) as exc:
        raise LocalAiError("로컬 AI 응답에 대화 요약이 없습니다.") from exc
    summary = _validated_room_summary(
        content,
        source_count=len(entries),
        require_all_references=purpose is not None,
    )
    if purpose is not None:
        summary = _ensure_record_briefing_sections(
            summary,
            entries,
            purpose=purpose,
        )
    _validate_record_summary_faithfulness(
        summary,
        entries,
        require_headings=purpose is not None,
    )
    return summary


def summarize_room_messages(
    *,
    entries: list[dict[str, Any]],
    external_allowed: bool = False,
    purpose: str | None = None,
) -> RoomSummaryResult:
    provider = settings.ai_review_provider.strip().lower()
    if provider == "stub":
        lines = [
            f"- [{index}] {entry.get('resident') or '일반'}: {str(entry.get('body', '')).strip()}"
            for index, entry in enumerate(entries[:8], 1)
        ]
        return RoomSummaryResult(
            summary=(
                f"[{purpose or '주요 내용'}]\n"
                + "\n".join(lines)
                + "\n\n※ 시험 모드에서는 선택한 원문을 그대로 모아 보여줍니다."
            ),
            provider="stub",
            model=settings.ai_review_model,
            elapsed_ms=0,
        )
    if provider in {"", "disabled", "off", "none"}:
        raise LocalAiError("대화 요약 AI 기능이 꺼져 있습니다.")

    if provider in {"chain", "nvidia", "nemotron"}:
        if external_allowed and settings.ai_review_external_enabled:
            started = perf_counter()
            try:
                summary = _safe_room_summary_references(
                    _room_summary_with_nvidia(entries, purpose=purpose),
                    len(entries),
                )
                return RoomSummaryResult(
                    summary=summary,
                    provider="nvidia",
                    model=settings.nvidia_nemotron_model,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                )
            except LocalAiError as exc:
                if provider in {"nvidia", "nemotron"}:
                    raise
                if purpose is not None:
                    elapsed_ms = int((perf_counter() - started) * 1000)
                    logger.warning(
                        "selected_record_summary_fallback model=%s "
                        "elapsed_ms=%d evidence_count=%d reason=%s",
                        settings.nvidia_nemotron_model,
                        elapsed_ms,
                        len(entries),
                        str(exc)[:160],
                    )
                    return RoomSummaryResult(
                        summary=_safe_selected_record_summary(
                            entries,
                            purpose=purpose,
                        ),
                        provider="safe",
                        model="safe-briefing-v2",
                        elapsed_ms=elapsed_ms,
                    )
        elif provider in {"nvidia", "nemotron"}:
            raise LocalAiError("가명 시험자료가 아니므로 외부 API 요약을 차단했습니다.")

    if provider in {"chain", "ollama", "local"}:
        models = (
            _model_list(settings.ai_review_local_models)
            if provider == "chain"
            else [settings.ai_review_model]
        )
        last_error: LocalAiError | None = None
        for model in models:
            started = perf_counter()
            try:
                summary = _safe_room_summary_references(
                    _room_summary_with_ollama(
                        entries,
                        model=model,
                        purpose=purpose,
                    ),
                    len(entries),
                )
                return RoomSummaryResult(
                    summary=summary,
                    provider="ollama",
                    model=model,
                    elapsed_ms=int((perf_counter() - started) * 1000),
                )
            except LocalAiError as exc:
                last_error = exc
        if last_error is not None:
            if purpose is not None:
                return RoomSummaryResult(
                    summary=_safe_selected_record_summary(
                        entries,
                        purpose=purpose,
                    ),
                    provider="safe",
                    model="safe-briefing-v2",
                    elapsed_ms=0,
                )
            raise last_error
    raise LocalAiError("대화 요약 AI 경로를 사용할 수 없습니다.")


def _system_prompt() -> str:
    return """
당신은 장기요양기관 내부 업무기록 초안을 교정하고 분류하는 보조자입니다.
원문과 현재 초안에 없는 사실, 진단, 조치, 이름, 날짜, 시간, 수치를 만들지 마세요.
어르신이 여러 명이면 각 어르신을 빠뜨리거나 서로 섞지 마세요.
원문에 있는 사람 이름, 가명 식별번호, 날짜, 시간, 수량, 횟수와
"넘어지지 않음" 같은 부정 표현은 생략하거나 다른 표현으로 바꾸지 마세요.
철자와 띄어쓰기를 다듬고 업무기록으로 읽기 쉽게 요약하되 원문의 불확실성은 유지하세요.
자연스럽지 않거나 현장에서 쓰지 않는 용어를 새로 만들지 마세요.
낙상위험도, 욕창위험도, 인지기능, 욕구사정의 점수를 확정하지 마세요.
기관 교정사전은 후보일 뿐이며 문맥이 맞을 때만 사용하세요.
현재 초안의 분류, 위험도, 전달대상, 서류 후보는 보수적으로 만든 안전 후보입니다.
원문에 명백히 반대되는 근거가 없는 한 위험도를 낮추거나 전달대상·서류 후보를 삭제하지 마세요.
원문 근거가 분명하면 분류를 바로잡거나 전달대상·서류 후보를 추가할 수 있습니다.
분류할 때 다음 장기요양 업무 우선순위를 적용하세요.
- 넘어짐, 균형 상실, 주저앉음, 부딪힘은 safety 후보입니다.
- 피부 발적, 붉은 부위, 압박, 진물, 체위변경은 health 후보입니다.
- 반복적인 귀가 요구, 배회, 출입문 이동, 불안·안정지원은 daily_care 후보입니다.
- 보호자에게 추후 설명한다는 문장만으로 핵심 관찰을 consultation으로 바꾸지 마세요.
낙상·욕창·인지기능의 공식 평가점수는 만들지 말고, 관련 서류의 기초자료 후보만 제안하세요.
개별 대화에서는 급여제공기록지, 간호일지, 상담일지, 신체제재 기록지,
프로그램 운영기록지처럼 사건 당일 작성할 수 있는 서류만 제안하세요.
통합사정, 급여제공계획, 급여제공결과평가, 낙상·욕창·인지·욕구사정은
일정 기간 누적자료로 작성하므로 개별 대화의 document_types에 넣지 마세요.
신체제재 기록지는 명시적인 신체제재 근거가 있을 때만 제안하고,
사유·대체 불가능성·방법·시간·상태 관찰·해제 조건·보호자 통지가 부족하면
verification_questions에 확인할 내용을 남기세요.
프로그램 운영기록지는 프로그램명·일시·장소·목표·진행 내용·총평과
참석자별 참여 여부·반응을 구분하세요.
서류별 초안은 서류의 목적에 맞는 원문 근거만 골라 작성하세요.
같은 원문 전체를 서류 제목만 바꾸어 여러 서류에 반복하지 마세요.
- 급여제공기록지는 관찰한 욕구, 실제 제공한 지원, 어르신 반응을 구분하세요.
- 간호일지는 건강 상태·증상·수치와 실제 간호 확인·조치만 기록하세요.
- 상담일지는 상담 상대·방법·내용·결과·후속 조치를 구분하세요.
- 신체제재 기록지는 명시적인 제재 근거와 필수 확인항목을 구분하세요.
- 프로그램 운영기록지는 프로그램 내용과 참석자별 참여·반응을 구분하세요.
해당 서류를 작성할 원문 근거가 부족하면 다른 서류의 문장을 복사하지 말고
"확인 필요: 작성 근거 부족"이라고 표시하고 verification_questions에 남기세요.
source.document_change_request가 있으면 현재 서류 초안의 사실은 보존하면서
요청받은 서류 하나만 고쳐 document_drafts에 반환하세요.
반드시 아래 키를 가진 JSON 객체 하나만 반환하세요.
corrected_text, summary, observation_details, actions_taken, resident_response,
handover_summary, verification_questions, classification, risk_level,
target_roles, document_types, keywords, document_drafts
document_drafts의 각 항목은 document_type, content, verification_questions를 가집니다.
분류값과 코드값은 번역하지 말고 허용값 중 하나만 사용하세요.
""".strip()


def _user_payload(
    *,
    source_snapshot: dict[str, Any],
    current_draft: dict[str, Any],
    lexicon_context: dict[str, object],
) -> dict[str, Any]:
    return {
        "source": source_snapshot,
        "current_draft": current_draft,
        "organization_lexicon": lexicon_context,
        "allowed_values": {
            "classification": [
                "daily_care",
                "nutrition",
                "health",
                "safety",
                "consultation",
                "rehabilitation",
            ],
            "risk_level": ["low", "medium", "high", "urgent"],
            "target_roles": [
                "caregiver",
                "nurse",
                "social_worker",
                "director",
                "therapist",
                "nutritionist",
            ],
            "document_types": [
                "care_service_record",
                "nursing_log",
                "consultation_log",
                "physical_restraint_log",
                "program_log",
            ],
        },
        "required_output_keys": [
            "corrected_text",
            "summary",
            "observation_details",
            "actions_taken",
            "resident_response",
            "handover_summary",
            "verification_questions",
            "classification",
            "risk_level",
            "target_roles",
            "document_types",
            "keywords",
            "document_drafts",
        ],
    }


def _request_json(
    *,
    url: str,
    payload: dict[str, Any],
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
    retry_transient: bool = False,
) -> dict[str, Any]:
    request_headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    max_attempts = 2 if retry_transient else 1
    for attempt in range(max_attempts):
        request = Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=request_headers,
            method="POST",
        )
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            is_transient = exc.code in {408, 429, 500, 502, 503, 504}
            if retry_transient and is_transient and attempt + 1 < max_attempts:
                response_headers = exc.headers or {}
                retry_after = response_headers.get("Retry-After", "").strip()
                delay_seconds = (
                    min(8.0, max(1.0, float(retry_after)))
                    if retry_after.replace(".", "", 1).isdigit()
                    else 3.0
                )
                sleep(delay_seconds)
                continue
            raise LocalAiError(f"AI 서버 오류({exc.code}): {detail}") from exc
        except (URLError, TimeoutError, OSError) as exc:
            if retry_transient and attempt + 1 < max_attempts:
                sleep(1.0)
                continue
            raise LocalAiError("AI 서버 연결이 지연되거나 끊어졌습니다.") from exc
        except json.JSONDecodeError as exc:
            raise LocalAiError("AI 서버 응답을 해석할 수 없습니다.") from exc
    raise LocalAiError("AI 서버 연결을 다시 시도했지만 응답이 없습니다.")


def _review_with_nvidia(
    *,
    model: str,
    user_payload: dict[str, Any],
) -> dict[str, Any]:
    api_key = (
        settings.nvidia_api_key.get_secret_value().strip()
        if settings.nvidia_api_key is not None
        else ""
    )
    if not api_key:
        key_path = Path(settings.nvidia_api_key_file)
        if key_path.is_file():
            api_key = key_path.read_text(encoding="utf-8").strip()
    if not api_key or api_key == "PASTE_NVIDIA_API_KEY_HERE":
        raise LocalAiError("Nemotron API 키가 설정되지 않았습니다.")
    aliased_payload, resident_aliases = _alias_resident_names(user_payload)
    system_prompt = _system_prompt()
    if resident_aliases:
        system_prompt += (
            "\nRESIDENT_001 같은 표기는 어르신 식별코드입니다. "
            "철자와 번호를 절대 번역하거나 바꾸지 마세요."
        )
    response = _request_json(
        url=f"{settings.nvidia_api_base_url.rstrip('/')}/chat/completions",
        payload={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(aliased_payload, ensure_ascii=False),
                },
            ],
            "temperature": 0.0,
            "max_tokens": 3600,
            "chat_template_kwargs": {"enable_thinking": False},
            "response_format": {"type": "json_object"},
            "stream": False,
        },
        timeout_seconds=settings.nvidia_api_timeout_seconds,
        headers={"Authorization": f"Bearer {api_key}"},
        retry_transient=True,
    )
    try:
        content = str(response["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise LocalAiError("Nemotron API 응답에 업무기록 내용이 없습니다.") from exc
    content = _restore_resident_names(content, resident_aliases)
    return _validated_draft(content)


def _review_with_ollama(
    *,
    model: str,
    user_payload: dict[str, Any],
) -> dict[str, Any]:
    response = _request_json(
        url=f"{settings.ai_review_base_url.rstrip('/')}/api/chat",
        payload={
            "model": model,
            "stream": False,
            "think": False,
            "format": "json",
            "messages": [
                {"role": "system", "content": _system_prompt()},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "options": {
                "temperature": 0,
                "num_ctx": 8192,
                "num_predict": 3600,
            },
            "keep_alive": "10m",
        },
        timeout_seconds=settings.ai_review_timeout_seconds,
    )
    try:
        content = str(response["message"]["content"])
    except (KeyError, TypeError) as exc:
        raise LocalAiError("로컬 AI 응답에 업무기록 내용이 없습니다.") from exc
    return _validated_draft(content)


def _model_list(value: str) -> list[str]:
    return list(dict.fromkeys(model.strip() for model in value.split(",") if model.strip()))


def _protect_safety_candidates(
    current_draft: dict[str, Any],
    reviewed_draft: dict[str, Any],
) -> dict[str, Any]:
    protected = dict(reviewed_draft)
    protected["target_roles"] = list(
        dict.fromkeys(
            [
                *current_draft.get("target_roles", []),
                *reviewed_draft.get("target_roles", []),
            ]
        )
    )
    proposal_by_type: dict[str, dict[str, Any]] = {}
    for proposal in [
        *current_draft.get("document_drafts", []),
        *reviewed_draft.get("document_drafts", []),
    ]:
        if not isinstance(proposal, dict):
            continue
        document_type = str(proposal.get("document_type", ""))
        if document_type in DAILY_DOCUMENT_TYPES:
            proposal_by_type[document_type] = proposal
    requested_document_types = [
        document_type
        for document_type in dict.fromkeys(
            [
                *current_draft.get("document_types", []),
                *reviewed_draft.get("document_types", []),
            ]
        )
        if document_type in DAILY_DOCUMENT_TYPES
    ]
    if proposal_by_type:
        protected["document_types"] = [
            document_type
            for document_type in requested_document_types
            if document_type in proposal_by_type
        ]
        protected["document_drafts"] = [
            proposal_by_type[document_type]
            for document_type in protected["document_types"]
        ]
    else:
        # 과거 저장자료와 단위시험처럼 아직 서류 본문이 없는 초안도
        # 유효하게 읽되, 실제 업무함에서는 서버의 규칙 초안이 먼저 보충된다.
        protected["document_types"] = requested_document_types
        protected["document_drafts"] = []
    protected["verification_questions"] = list(
        dict.fromkeys(
            [
                *current_draft.get("verification_questions", []),
                *reviewed_draft.get("verification_questions", []),
            ]
        )
    )[:20]
    if not protected.get("observation_details"):
        protected["observation_details"] = current_draft.get(
            "observation_details",
            current_draft.get("corrected_text", ""),
        )
    if not protected.get("handover_summary"):
        protected["handover_summary"] = current_draft.get(
            "handover_summary",
            current_draft.get("summary", ""),
        )
    risk_order = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
    current_risk = str(current_draft.get("risk_level", "low"))
    reviewed_risk = str(reviewed_draft.get("risk_level", "low"))
    if risk_order.get(current_risk, 0) > risk_order.get(reviewed_risk, 0):
        protected["risk_level"] = current_risk
    current_classification = str(current_draft.get("classification", ""))
    current_keywords = {
        str(keyword) for keyword in current_draft.get("keywords", [])
    }
    if current_classification == "safety" or current_keywords.intersection(
        {"skin_integrity", "cognition"}
    ):
        protected["classification"] = current_classification
    return RecordDraft.model_validate(protected).model_dump(mode="json")


def _attempt(
    *,
    provider: str,
    model: str,
    call,
    attempts: list[dict[str, Any]],
) -> AiReviewResult | None:
    started = perf_counter()
    try:
        draft = call()
    except LocalAiError as exc:
        attempts.append(
            {
                "provider": provider,
                "model": model,
                "status": "failed",
                "elapsed_ms": int((perf_counter() - started) * 1000),
                "reason": str(exc)[:200],
            }
        )
        return None
    elapsed_ms = int((perf_counter() - started) * 1000)
    attempts.append(
        {
            "provider": provider,
            "model": model,
            "status": "completed",
            "elapsed_ms": elapsed_ms,
        }
    )
    return AiReviewResult(
        draft=draft,
        provider=provider,
        model=model,
        elapsed_ms=elapsed_ms,
        attempts=attempts,
    )


def refine_record_draft(
    *,
    source_snapshot: dict[str, Any],
    current_draft: dict[str, Any],
    lexicon_context: dict[str, object],
    external_allowed: bool = False,
) -> AiReviewResult:
    provider = settings.ai_review_provider.strip().lower()
    user_payload = _user_payload(
        source_snapshot=source_snapshot,
        current_draft=current_draft,
        lexicon_context=lexicon_context,
    )
    if provider == "stub":
        return AiReviewResult(
            draft=RecordDraft.model_validate(
                _stub_refine(current_draft)
            ).model_dump(mode="json"),
            provider="stub",
            model=settings.ai_review_model,
            elapsed_ms=0,
            attempts=[
                {
                    "provider": "stub",
                    "model": settings.ai_review_model,
                    "status": "completed",
                    "elapsed_ms": 0,
                }
            ],
        )
    if provider in {"", "disabled", "off", "none"}:
        raise LocalAiError("업무함 AI 정리 기능이 꺼져 있습니다.")

    attempts: list[dict[str, Any]] = []
    if provider in {"chain", "nvidia", "nemotron"}:
        if not external_allowed:
            attempts.append(
                {
                    "provider": "nvidia",
                    "model": settings.nvidia_nemotron_model,
                    "status": "skipped",
                    "elapsed_ms": 0,
                    "reason": "가명 시험자료가 아니므로 외부 API 전송을 차단했습니다.",
                }
            )
        elif not settings.ai_review_external_enabled:
            attempts.append(
                {
                    "provider": "nvidia",
                    "model": settings.nvidia_nemotron_model,
                    "status": "skipped",
                    "elapsed_ms": 0,
                    "reason": "외부 API 사용 설정이 꺼져 있습니다.",
                }
            )
        else:
            result = _attempt(
                provider="nvidia",
                model=settings.nvidia_nemotron_model,
                call=lambda: _review_with_nvidia(
                    model=settings.nvidia_nemotron_model, user_payload=user_payload
                ),
                attempts=attempts,
            )
            if result is not None:
                return AiReviewResult(
                    draft=_protect_safety_candidates(current_draft, result.draft),
                    provider=result.provider,
                    model=result.model,
                    elapsed_ms=result.elapsed_ms,
                    attempts=result.attempts,
                )
        if provider in {"nvidia", "nemotron"}:
            raise LocalAiError(str(attempts[-1]["reason"]))

    if provider in {"chain", "ollama", "local"}:
        local_models = (
            _model_list(settings.ai_review_local_models)
            if provider == "chain"
            else [settings.ai_review_model]
        )
        for model in local_models:
            result = _attempt(
                provider="ollama",
                model=model,
                call=lambda selected_model=model: _review_with_ollama(
                    model=selected_model,
                    user_payload=user_payload,
                ),
                attempts=attempts,
            )
            if result is not None:
                return AiReviewResult(
                    draft=_protect_safety_candidates(current_draft, result.draft),
                    provider=result.provider,
                    model=result.model,
                    elapsed_ms=result.elapsed_ms,
                    attempts=result.attempts,
                )
        reasons = [
            str(attempt.get("reason", "AI 처리 실패"))
            for attempt in attempts
            if attempt["status"] == "failed"
        ]
        raise LocalAiError(
            "모든 AI 정리 경로가 실패했습니다. "
            + (" / ".join(reasons[-3:]) if reasons else "AI 설정을 확인해 주세요.")
        )

    raise LocalAiError(f"지원하지 않는 AI 검토 방식입니다: {settings.ai_review_provider}")
