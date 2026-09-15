from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone, timedelta
import re
from typing import Any, Iterable
from uuid import UUID


KST = timezone(timedelta(hours=9))

_TECHNICAL_PATTERNS = (
    re.compile(r"\[[A-Z0-9_-]+:[^\]]+\]\s*", re.IGNORECASE),
    re.compile(r"\bSYNTHETIC(?:_[A-Z0-9_-]+)?\b[^.!?\n]*[.!?]?", re.IGNORECASE),
    re.compile(r"\bDEV(?:_[A-Z0-9_-]+)?\b[^.!?\n]*[.!?]?", re.IGNORECASE),
)
_TECHNICAL_PHRASES = (
    "비교할 최근 7일 기록이 없어 이번 관찰을 첫 기준으로 표시합니다.",
    "최근 7일 기록에는 없던",
    "선택 범위에 다시 보고되었습니다.",
    "모든 내용은 실제 인물과 무관한 합성 시험자료입니다.",
    "모든 내용은 합성 시험자료입니다.",
)
_EXPLICIT_PENDING_TERMS = ("예정", "대기", "미완료", "재확인", "회신 필요")
_EXPLICIT_CONFLICT_TERMS = (
    "불명확",
    "충돌",
    "서로 다",
    "인지 확인",
    "또는",
    "원문 확인",
)
_NON_RECORD_INSTRUCTION_TERMS = (
    "주세요",
    "확인해 주세요",
    "선택하지 말",
    "변경하지 않습니다",
    "추정하지",
    "직원 확인 전",
    "공식 계획",
    "공식 기록",
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def clean_field_record_sentence(value: str, resident_name: str | None = None) -> str:
    """Remove fixture/implementation noise without inventing replacement facts."""

    cleaned = value
    for pattern in _TECHNICAL_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    for phrase in _TECHNICAL_PHRASES:
        cleaned = cleaned.replace(phrase, "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" \t\r\n-·•")
    if resident_name:
        for prefix in (
            f"{resident_name}님의 ",
            f"{resident_name}님이 ",
            f"{resident_name}님에게 ",
            f"{resident_name}에게 ",
            f"{resident_name}이 ",
            f"{resident_name} ",
        ):
            if cleaned.startswith(prefix):
                cleaned = cleaned[len(prefix) :].strip()
                break
    return cleaned


def _has_explicit_pending(texts: Iterable[str]) -> bool:
    combined = " ".join(texts)
    return any(term in combined for term in _EXPLICIT_PENDING_TERMS)


def _is_non_record_instruction(value: str) -> bool:
    return any(term in value for term in _NON_RECORD_INSTRUCTION_TERMS)


def _select_record_summary(
    event_summary: str,
    texts: Iterable[str],
    resident_name: str,
) -> str:
    """Prefer observed facts and suppress instruction-only fixture/safety prose."""

    candidates = [event_summary, *texts]
    seen: set[str] = set()
    for candidate in candidates:
        cleaned = clean_field_record_sentence(candidate, resident_name)
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        if _is_non_record_instruction(cleaned):
            continue
        # The first observation is not the whole event. Keep the last factual
        # reply with it so a reported problem cannot hide its recorded outcome.
        replies = [
            clean_field_record_sentence(part, resident_name)
            for text in texts
            for part in re.split(r"\[답글(?:\s*·[^\]]+)?\]", text)[1:]
        ]
        replies = [
            reply for reply in replies
            if reply and reply not in cleaned and not _is_non_record_instruction(reply)
        ]
        if replies:
            return f"{cleaned} 이후 기록: {replies[-1]}"
        return cleaned
    return ""


def _conflict_note(texts: Iterable[str]) -> str | None:
    combined = " ".join(texts)
    if not any(term in combined for term in _EXPLICIT_CONFLICT_TERMS):
        return None
    blood_pressures = list(
        dict.fromkeys(re.findall(r"(?<!\d)(\d{2,3}\s*/\s*\d{2,3})(?!\d)", combined))
    )
    if len(blood_pressures) > 1:
        compared = "과 ".join(value.replace(" ", "") for value in blood_pressures)
        return f"혈압 {compared}이 함께 기록되어 원문 확인이 필요합니다."
    return "서로 다른 근거 내용이 있어 원문 확인이 필요합니다."


def build_field_care_briefing(
    *,
    record_events: Iterable[Any],
    candidate_texts_by_evidence: dict[UUID, list[str]],
    selected_message_ids: set[UUID],
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Build the factual, date/resident/time ordered briefing presentation.

    The input can contain legacy carry-over events.  Only evidence inside the
    requested period is eligible here.  This prevents an old unfinished task
    from silently becoming a new care-record reference.
    """

    day_groups: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    consultations: list[dict[str, Any]] = []
    seen_care: set[tuple[str, str, str, str]] = set()
    seen_consultations: set[tuple[str, str, str]] = set()

    for event in record_events:
        resident_id = getattr(event, "resident_id", None)
        resident_name = getattr(event, "resident_name", None)
        evidence_ids = [
            evidence_id
            for evidence_id in getattr(event, "evidence_ids", [])
            if evidence_id in selected_message_ids
        ]
        if resident_id is None or not resident_name or not evidence_ids:
            continue
        occurred_at = _as_utc(getattr(event, "occurred_at"))
        if not (period_start <= occurred_at < period_end):
            continue
        texts = [
            text
            for evidence_id in evidence_ids
            for text in candidate_texts_by_evidence.get(evidence_id, [])
            if text.strip()
        ]
        summary = _select_record_summary(
            getattr(event, "summary", ""), texts, resident_name
        )
        if not summary:
            continue
        local_at = occurred_at.astimezone(KST)
        day_key = local_at.date().isoformat()
        time_label = local_at.strftime("%H:%M")
        # 원본 OCR 내부 후보 숫자만으로 서로 다른 근거의 충돌을 만들지 않는다.
        # 대표 사실문에 실제로 둘 이상의 값이 함께 남은 경우에만 표시한다.
        conflict_note = _conflict_note([summary])
        entry = {
            "event_group_id": getattr(event, "event_group_id"),
            "occurred_at": occurred_at,
            "time_label": time_label,
            "summary": summary,
            "evidence_ids": evidence_ids,
            "explicit_pending": _has_explicit_pending(texts or [summary]),
            "conflict_note": conflict_note,
        }
        candidate_types = set(getattr(event, "document_candidate_types", []))
        if "care_service_record" in candidate_types:
            care_key = (day_key, str(resident_id), time_label, summary)
            if care_key not in seen_care:
                seen_care.add(care_key)
                resident_group = day_groups[day_key].setdefault(
                    str(resident_id),
                    {
                        "resident_id": resident_id,
                        "resident_name": resident_name,
                        "entries": [],
                    },
                )
                resident_group["entries"].append(entry)
        if "consultation_log" in candidate_types:
            consultation_key = (str(resident_id), time_label, summary)
            if consultation_key not in seen_consultations:
                seen_consultations.add(consultation_key)
                consultations.append(
                    {
                        "event_group_id": getattr(event, "event_group_id"),
                        "resident_id": resident_id,
                        "resident_name": resident_name,
                        "occurred_at": occurred_at,
                        "time_label": time_label,
                        "summary": summary,
                        "evidence_ids": evidence_ids,
                    }
                )

    days: list[dict[str, Any]] = []
    for day_key in sorted(day_groups):
        residents = list(day_groups[day_key].values())
        residents.sort(key=lambda item: (item["resident_name"], str(item["resident_id"])))
        for resident in residents:
            resident["entries"].sort(
                key=lambda item: (_as_utc(item["occurred_at"]), item["event_group_id"])
            )
        days.append({"date": day_key, "residents": residents})
    consultations.sort(
        key=lambda item: (_as_utc(item["occurred_at"]), item["resident_name"])
    )
    care_entry_count = sum(
        len(resident["entries"])
        for day in days
        for resident in day["residents"]
    )
    resident_count = len(
        {
            str(resident["resident_id"])
            for day in days
            for resident in day["residents"]
        }
    )
    if care_entry_count:
        overall_summary = (
            f"선택 기간에 {resident_count}명의 급여제공기록지 참고 내용 "
            f"{care_entry_count}건을 날짜와 시간순으로 정리했습니다."
        )
        if consultations:
            overall_summary += f" 보호자 상담 참고 내용은 {len(consultations)}건입니다."
    else:
        overall_summary = "선택 기간에 급여제공기록지에 참고할 돌봄 내용이 없습니다."

    return {
        "overall_summary": overall_summary,
        "daily_care_references": days,
        "consultation_references": consultations,
        "care_reference_count": care_entry_count,
        "consultation_reference_count": len(consultations),
    }
