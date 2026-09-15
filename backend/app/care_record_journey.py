"""Local, extractive care timelines. No provider, network or database writes.

The caller supplies currently authorized message sources. Dates are record
dates, topic grouping never changes an event's identity, and source text is
kept separately from the short display summary.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
import re
from typing import Any, Callable, Iterable
from uuid import UUID
from types import SimpleNamespace

from .field_care_briefing import KST, _as_utc, clean_field_record_sentence
from .attachment_review_policy import attachment_evidence_text


TOPIC_TERMS = {
    "이동": (
        "이동",
        "보행",
        "부축",
        "낙상",
        "넘어",
        "비틀",
        "휠체어",
        "보행기",
        "기립",
        "주저앉",
        "균형",
        "일어서",
    ),
    "식사": (
        "식사",
        "섭취",
        "점심",
        "아침",
        "저녁",
        "간식",
        "수분",
        "음수",
        "삼킴",
        "연하",
        "물 ",
    ),
    "수면": ("수면", "잠", "야간", "불면", "기상"),
    "배설": ("배변", "배뇨", "소변", "대변", "변비", "설사", "기저귀", "화장실"),
    "기분": ("불안", "우울", "기분", "웃음", "초조", "배회", "울음", "화냄", "거부"),
    "활동": ("프로그램", "활동", "체조", "운동", "산책", "노래", "참여"),
    "보호자 상담": ("보호자", "보호-", "가족", "상담", "면회", "회신", "외출"),
    "건강": (
        "혈압",
        "혈당",
        "체온",
        "통증",
        "발적",
        "욕창",
        "피부",
        "상처",
        "어지",
        "기침",
        "발열",
        "투약",
        "구토",
        "부종",
        "골절",
        "염좌",
        "귀원",
        "진료",
        "붓기",
        "불편",
    ),
    "돌봄": (
        "세면",
        "목욕",
        "옷",
        "도움",
        "제공",
        "상태",
        "관찰",
        "확인",
        "안정",
        "회복",
    ),
}
_CONFLICT = re.compile(
    # '상이' is a comparison word only at a word boundary. It also occurs
    # inside ordinary observations such as 외상이나, 증상이 and 이상이.
    r"충돌|불일치|서로\s*다|서로\s*달|(?<![가-힣A-Za-z0-9_])상이(?=함|하|합|한|했|\s|[.!?]|$)|불명확|어느\s*(?:수치|기록|값).*(?:맞|확인)|기록했으나.{0,100}(?:원본|원문)\s*확인"
)
_DIFFERENCE = re.compile(
    r"(?:이전|전날|어제|평소|지난).{0,40}(?:달라|다르|늘|줄|증가|감소|대신)|(?:달라졌|변경됨|변경함|새로\s*관찰)"
)
_OUTCOME = re.compile(r"회복|호전|귀원|(?:휴식|조치|부축|제공|재측정)\s*후")
_INSTRUCTION = re.compile(
    r"(?:주세요|추정하지|선택하지\s*말|직원\s*확인\s*전|변경하지\s*않습니다)"
)


def _sentences(text: str, resident_name: str) -> list[str]:
    # Split only at a real sentence/line boundary, never a decimal or time.
    return list(
        dict.fromkeys(
            value
            for part in re.split(r"\n+|(?<=[.!?])\s+", text)
            if (value := clean_field_record_sentence(part, resident_name))
            and not _INSTRUCTION.search(value)
        )
    )


def _topic(text: str) -> str | None:
    # One sentence has one display home; it is never duplicated across topics.
    matches = [
        (label, sum(term in text for term in terms))
        for label, terms in TOPIC_TERMS.items()
    ]
    label, score = max(matches, key=lambda item: item[1])
    return label if score else None


def _kind(text: str, is_reply: bool) -> str:
    if _CONFLICT.search(text):
        return "conflict"
    if _DIFFERENCE.search(text):
        return "different"
    if is_reply or _OUTCOME.search(text):
        return "followup"
    return "event"


def _date(at: datetime) -> str:
    return _as_utc(at).astimezone(KST).strftime("%m/%d %H:%M")


def _signature(text: str) -> str:
    # Keep all quantities and negations. Ignore only explicit record timestamps.
    return re.sub(r"\s+", "", re.sub(r"(?<!\d)\d{1,2}:\d{2}(?!\d)", "", text)).strip(
        " ."
    )


def _meal_amount(text: str) -> tuple[str, str] | None:
    meal = next((value for value in ("아침", "점심", "저녁") if value in text), None)
    amount = re.search(r"(?<!\d)(?:1/2|3/4|1/4)(?!\d)|절반", text)
    if meal and amount:
        return meal, "1/2" if amount.group() == "절반" else amount.group()
    return None


def _has_recorded_help(text: str) -> bool:
    # A keyword in a negation or future plan is not evidence that help occurred.
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        if re.search(
            r"(?:도움|부축|보조|지원)(?:이|은|가|도|을)?\s*(?:없이|없|불필요|필요\s*없)|(?:도움|부축|지원).{0,15}(?:하지\s*않|받지\s*않|예정|계획|필요)",
            sentence,
        ):
            continue
        if re.search(
            r"부축|도와|(?:도움|지원|보조)(?:을)?\s*(?:받|드|제공)|\d인\s*도움",
            sentence,
        ):
            return True
    return False


def _short_summary(entries: list[dict[str, Any]]) -> str:
    if not entries:
        return ""
    first, last = entries[0], entries[-1]
    if len(entries) == 1:
        return first["summary"]
    repeated = len({_signature(entry["summary"]) for entry in entries}) == 1
    if repeated:
        days = len(
            {_as_utc(entry["occurred_at"]).astimezone(KST).date() for entry in entries}
        )
        return f"{first['summary']} {_date(first['occurred_at'])}~{_date(last['occurred_at'])}, {days}일에 기록됨."
    # The first event and its actual last follow-up take precedence over an
    # unrelated later event. All other events remain distinct in the timeline.
    followups = [
        entry for entry in entries[1:] if entry["event_key"] == first["event_key"]
    ]
    if followups:
        last = followups[-1]
    return f"{_date(first['occurred_at'])} {first['summary']} {_date(last['occurred_at'])} {last['summary']}"


def build_care_topics(
    *,
    sources: Iterable[Any],
    period_start: datetime,
    period_end: datetime,
    resident_id: UUID | None = None,
    split_resident_text: Callable[..., str],
    include_unclassified: bool = False,
) -> list[dict[str, Any]]:
    """Extract source sentences, including full saved replies and extractions.

    No legacy AI summary is used as a quote, and there is no hidden item cap.
    Multi-resident sections use the existing conservative attribution contract.
    """
    sources = list(sources)
    available_ids = {source.message.id for source in sources}
    grouped: dict[tuple[UUID, str, str], dict[str, Any]] = {}
    for source in sources:
        message = source.message
        if getattr(message, "is_recalled", False):
            continue
        residents = {}
        primary = getattr(message, "resident", None)
        if primary is not None:
            residents[primary.id] = primary
        for link in getattr(message, "resident_links", []):
            if link.status == "confirmed":
                residents[link.resident.id] = link.resident
        if include_unclassified and not residents and resident_id is None:
            residents[None] = SimpleNamespace(id=None, display_name="대상 미지정")
        reply_to = getattr(message, "reply_to", None)
        root_id = (
            reply_to.message_id
            if reply_to is not None and reply_to.message_id in available_ids
            else message.id
        )
        for resident in residents.values():
            if resident_id is not None and resident.id != resident_id:
                continue
            names = [item.display_name for item in residents.values()]
            event_key = f"{resident.id}:{root_id}"
            body = split_resident_text(
                message.body, target_name=resident.display_name, resident_names=names
            )
            body_sentences = _sentences(body, resident.display_name)
            body_topics = list(
                dict.fromkeys(
                    topic for text in body_sentences if (topic := _topic(text))
                )
            )
            default_topic = body_topics[0] if len(body_topics) == 1 else None
            if "이동" in body_topics and set(body_topics) <= {"이동", "건강", "돌봄"}:
                default_topic = "이동"
            parts: list[tuple[datetime, str, str, Any, Any]] = [
                (message.created_at, body, "message", None, None)
            ]
            parts.extend(
                (
                    comment.created_at,
                    split_resident_text(
                        comment.body,
                        target_name=resident.display_name,
                        resident_names=names,
                    ),
                    "comment",
                    comment.id,
                    None,
                )
                for comment in source.comments
            )
            for attachment in message.attachments:
                extraction = attachment.text_extraction
                if extraction is None or extraction.status not in {
                    "completed",
                    "reviewed",
                }:
                    continue
                # Confirmed staff wording wins; the original and full correction
                # history remain available through the attachment source.
                text = attachment_evidence_text(attachment)
                if not text:
                    continue
                parts.append(
                    (
                        message.created_at,
                        split_resident_text(
                            text,
                            target_name=resident.display_name,
                            resident_names=names,
                        ),
                        "attachment",
                        None,
                        attachment.id,
                    )
                )
            for at, text, source_kind, comment_id, attachment_id in parts:
                at = _as_utc(at)
                if not period_start <= at < period_end:
                    continue
                sentences = _sentences(text, resident.display_name)
                topic_sentences: dict[str, list[str]] = defaultdict(list)
                for sentence in sentences:
                    if include_unclassified and sentence.strip(" .:：") == resident.display_name:
                        continue
                    topic = _topic(sentence)
                    if topic is None and include_unclassified:
                        topic = "기록"
                    # An explicit reply can resolve a parent observation without
                    # repeating its topic word (e.g. "휴식 후 어지럼 없음").
                    if source_kind == "comment" and default_topic:
                        topic = default_topic
                    elif (
                        source_kind == "message"
                        and default_topic == "이동"
                        and topic in {"건강", "돌봄"}
                    ):
                        topic = default_topic
                    if topic is not None:
                        topic_sentences[topic].append(sentence)
                for topic, facts in topic_sentences.items():
                    key = resident.id, topic, event_key
                    group = grouped.setdefault(
                        key,
                        {
                            "key": f"{resident.id}:{topic}:{root_id}",
                            "resident_id": resident.id,
                            "resident_name": resident.display_name,
                            "topic": topic,
                            "entries": [],
                        },
                    )
                    summary = " ".join(facts)
                    group["entries"].append(
                        {
                            "message_id": message.id,
                            "comment_id": comment_id,
                            "attachment_id": attachment_id,
                            "source_kind": source_kind,
                            "occurred_at": at,
                            "summary": summary,
                            "kind": _kind(
                                summary,
                                source_kind == "comment" or reply_to is not None,
                            ),
                            "event_key": event_key,
                        }
                    )
    # Only identical complete observations can share a display item. Independent
    # events with different facts never disappear behind an unrelated first/last.
    clusters: dict[tuple[Any, ...], dict[str, Any]] = {}
    for group in grouped.values():
        group["entries"].sort(
            key=lambda entry: (entry["occurred_at"], entry["source_kind"])
        )
        signature = tuple(_signature(entry["summary"]) for entry in group["entries"])
        key = group["resident_id"], group["topic"], signature
        previous = clusters.get(key)
        day = group["entries"][0]["occurred_at"].astimezone(KST).date()
        if previous is not None and any(
            entry["occurred_at"].astimezone(KST).date() != day
            for entry in previous["entries"]
        ):
            previous["entries"].extend(group["entries"])
        else:
            clusters[key if previous is None else (*key, group["key"])] = group
    topics = []
    for group in clusters.values():
        entries = sorted(
            group["entries"],
            key=lambda entry: (
                entry["occurred_at"],
                str(entry["message_id"]),
                entry["source_kind"],
            ),
        )
        initial_meal = _meal_amount(entries[0]["summary"])
        if group["topic"] == "식사" and initial_meal:
            for entry in entries[1:]:
                meal = _meal_amount(entry["summary"])
                if (
                    entry["event_key"] == entries[0]["event_key"]
                    and meal
                    and meal[0] == initial_meal[0]
                    and meal[1] != initial_meal[1]
                    and entry["kind"] != "conflict"
                ):
                    entry["kind"] = "different"
        signatures: dict[str, set[Any]] = defaultdict(set)
        for entry in entries:
            signatures[_signature(entry["summary"])].add(
                entry["occurred_at"].astimezone(KST).date()
            )
        for entry in entries:
            if (
                entry["kind"] == "event"
                and len(signatures[_signature(entry["summary"])]) > 1
            ):
                entry["kind"] = "repeated"
        group.update(
            entries=entries,
            summary=_short_summary(entries),
            evidence_ids=list(dict.fromkeys(entry["message_id"] for entry in entries)),
            kinds=list(dict.fromkeys(entry["kind"] for entry in entries)),
            first_at=entries[0]["occurred_at"],
            latest_at=entries[-1]["occurred_at"],
        )
        topics.append(group)

    def order(topic):
        recorded_signal = any(
            re.search(
                r"주저앉|넘어|비틀|골절|염좌|거부|불편|통증|충돌|기록했으나|절반|1/2|회신.*기다",
                entry["summary"],
            )
            for entry in topic["entries"]
        )
        return recorded_signal, topic["latest_at"], topic["key"]

    return sorted(topics, key=order, reverse=True)


def answer_record_question(*, question: str, topics: list[dict[str, Any]]) -> dict[str, Any]:
    """Compatibility entry point using the shared natural-question retrieval."""
    from .record_question import answer_facts, plan_question

    plan = plan_question(question, topics)
    return answer_facts(question, plan["rule_facts"], notes=plan["notes"], first=plan["first"], scope_count=plan["scope_count"], ambiguous=plan["ambiguous"])
