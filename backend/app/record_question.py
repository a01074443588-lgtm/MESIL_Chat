"""Question retrieval over an already authorized record scope.

Lexical and conversational hints rank evidence; none is an admission rule.
The local model receives candidate records even when no literal term matches.
All answer text is copied from event-bound evidence by the server.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
import re
from typing import Any
from zoneinfo import ZoneInfo

from .care_record_journey import TOPIC_TERMS, _has_recorded_help
from .record_answer_quality import (
    HYDRATION_QUESTION,
    HYDRATION_RECORD,
    focused_rule_answer,
)
from .record_hydration import hydration_quantity_fact, hydration_quantity_intent, hydration_answer

KST = ZoneInfo("Asia/Seoul")
FIRST = re.compile(r"언제부터|처음|최초|시작")
FOLLOWUP = re.compile(r"그\s*뒤|이후|경과|후속|괜찮아|회복|호전")
REPEAT = re.compile(r"반복|비슷|또\s*있|다시|같은")
OVERVIEW = re.compile(
    r"요약|정리|중요|알아야|어떠|어때|어땠|컨디션|지내|어떤\s*상태|요즘|최근|달라|변화|말할\s*내용|전달할\s*내용"
)
# These are optional ranking hints, not accepted-question or accepted-topic lists.
HINTS = [
    (
        r'행동\s*양상|평소\s*모습|요즘\s*반응|어떻게\s*반응|행동.*달라',
        ('표정', '반응', '대화', '웃', '참여', '거부', '직원', '말씀', '소리', '짜증'),
    ),
    (
        r"병원|진료|검사|진단|치료|처방",
        ("병원", "진료", "검사", "진단", "귀원", "입원", "퇴원", "처방"),
    ),
    (r"거부|싫|안\s*하|원치|마지못", ("거부", "거절", "싫", "원치", "참여하지")),
    (
        r"달라|변화|전보다|평소보다",
        ("이전", "전날", "평소", "절반", "3/4", "늘", "줄", "다르"),
    ),
]
_REQUEST_WORDS = re.compile(
    r"최근에?|요즘은?|지난달|이번달|오늘|어제|전보다|달라진|달라졌|그\s*뒤에는?|이후|경과|후속|회복|처음|최초|언제부터|비슷한?|반복|기록에?|대화내용[을이은]?|내용[을이은]?|요약\w*|정리\w*|알려\w*|어떠\w*|어때\w*|어땠\w*|컨디션|지내\w*|어떤|상태[가를는은]?|궁금\w*|있나요|있어요|있어|없나요|없어|해주세요|해줘|주세요|이분[에의]?|그분[에의]?|관해|대해|알아야|중요한?|확인\w*|부탁\w*|선택된|선택한|어르신|최근|한번|좀|왜|원인[을이은]?|무엇\w*|어떻게|인지|나요|까요|하신|적이?|게|됐어|괜찮아졌\w*"
)


def at(value):
    parsed = datetime.fromisoformat(value) if isinstance(value, str) else value
    return (
        parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed
    ).astimezone(KST)


def event_id(fact):
    return (
        str(fact.get("resident_id", fact.get("resident_name", ""))),
        str(fact.get("event_key") or fact["message_id"]),
    )


def question_date_window(question: str, reference: date | None = None):
    """Resolve only explicit conversational periods; None means use the UI scope."""
    reference = reference or datetime.now(KST).date()
    compact = re.sub(r"\s+", "", question)
    if "지난달" in compact:
        last = reference.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if "이번달" in compact:
        return reference.replace(day=1), reference
    recent = re.search(r"최근(\d{1,3})일", compact)
    if recent:
        days=max(1,min(int(recent.group(1)),184))
        return reference-timedelta(days=days-1),reference
    if "이번주" in compact:
        return reference-timedelta(days=reference.weekday()),reference
    if "어제" in compact and "오늘" in compact:
        return reference-timedelta(days=1),reference
    if "어제" in compact:
        day=reference-timedelta(days=1);return day,day
    if "오늘" in compact:
        return reference,reference
    return None


def question_terms(question):
    text = _REQUEST_WORDS.sub(" ", question)
    words = re.findall(r"[가-힣A-Za-z0-9]{2,}", text)
    return [
        re.sub(r"(?:에서|에게|에는|으로|은|는|이|가|을|를|의|도|해|어)$", "", word)
        for word in words
    ]


def _similarity(terms, text):
    compact = re.sub(r"\s+", "", text)
    grams = {compact[i : i + 2] for i in range(len(compact) - 1)}
    score = 0.0
    for word in terms:
        if len(word) < 2:
            continue
        if word in compact:
            score += 2
        elif len(word) >= 3:
            pieces = {word[i : i + 2] for i in range(len(word) - 1)}
            overlap = len(pieces & grams) / len(pieces)
            if overlap >= 0.5:
                score += overlap
    return score


def plan_question(
    question: str,
    topics: list[dict[str, Any]],
    *,
    resident_id=None,
    today: date | None = None,
) -> dict:
    groups = defaultdict(list)
    names = defaultdict(set)
    for topic in topics:
        if resident_id is not None and str(topic["resident_id"]) != str(resident_id):
            continue
        names[topic["resident_name"]].add(str(topic["resident_id"]))
        for entry in topic["entries"]:
            fact = {
                **entry,
                "resident_id": topic["resident_id"],
                "resident_name": topic["resident_name"],
                "topic": topic["topic"],
            }
            if not any(
                old["summary"] == fact["summary"]
                and old.get("comment_id") == fact.get("comment_id")
                and old.get("attachment_id") == fact.get("attachment_id")
                for old in groups[event_id(fact)]
            ):
                groups[event_id(fact)].append(fact)
    scope_count = sum(len(items) for items in groups.values())
    # Internal only: aggregates consume the authorized scope before ranking,
    # never the bounded model candidates returned as `facts` below.
    scope_facts = [fact for items in groups.values() for fact in items]
    mentioned = [name for name in names if name and name in question]
    if mentioned:
        ids = set.union(*(names[name] for name in mentioned))
        groups = {key: facts for key, facts in groups.items() if key[0] in ids}
    elif re.search(r"어르\d+", question):
        groups = {}
    notes = []
    reference = today or datetime.now(KST).date()
    window = question_date_window(question,reference)
    if window:
        groups = {
            key: [
                fact
                for fact in facts
                if window[0] <= at(fact["occurred_at"]).date() <= window[1]
            ]
            for key, facts in groups.items()
        }
        groups = {key: facts for key, facts in groups.items() if facts}
        notes.append(
            f"화면의 선택 범위 안에서 {window[0]}~{window[1]} 기록을 확인했습니다."
        )
    text = question
    for name in mentioned:
        text = text.replace(name, " ")
    terms = question_terms(text)
    overview = bool(OVERVIEW.search(text)) and (
        not terms
        or bool(
            re.search(r"요약|정리|중요|알아야|말할\s*내용|전달할\s*내용|달라", text)
        )
    )
    specific_topic = any(label in text for label in TOPIC_TERMS if label != "돌봄")
    sharing_overview = bool(re.search(r"(?:말할|전달할)\s*내용", text))
    overview = overview and (not specific_topic or sharing_overview)
    if re.search(r'행동\s*양상|평소\s*모습|요즘\s*반응|어떻게\s*반응|행동.*달라', text):
        overview = False
    if overview:
        terms = []
    generic = overview or not terms
    hydration_focus = (bool(HYDRATION_QUESTION.search(text)) or hydration_quantity_intent(text)) and any(HYDRATION_RECORD.search(' '.join(f['summary'] for f in items)) for items in groups.values())
    ranking = []
    for key, facts in groups.items():
        facts.sort(key=lambda fact: (at(fact["occurred_at"]), str(fact["message_id"])))
        body = " ".join(fact["summary"] for fact in facts)
        score = _similarity(terms, body)
        if hydration_focus:
            score = score + 8 if HYDRATION_RECORD.search(body) else -100
        if not overview:
            for label, synonyms in TOPIC_TERMS.items():
                if label in text or any(term in text for term in synonyms):
                    score += 2 * any(fact["topic"] == label for fact in facts)
            for pattern, synonyms in HINTS:
                if re.search(pattern, text):
                    score += sum(term in body for term in synonyms)
        if (
            FIRST.search(text)
            and re.search(r"도움|부축", text)
            and not any(_has_recorded_help(fact["summary"]) for fact in facts)
        ):
            score = -1
        if REPEAT.search(text) and any(fact["kind"] == "repeated" for fact in facts):
            score += 4
        if FOLLOWUP.search(text) and any(
            fact["kind"] in {"followup", "different", "conflict"} for fact in facts
        ):
            score += 2
        ranking.append((key, facts, score))
    ranking.sort(
        key=lambda item: (item[2], at(item[1][-1]["occurred_at"])), reverse=True
    )
    matches = [item for item in ranking if item[2] > 0 or generic and item[2] >= 0]
    if REPEAT.search(text) and any(
        any(f["kind"] == "repeated" for f in item[1]) for item in matches
    ):
        matches = [
            item for item in matches if any(f["kind"] == "repeated" for f in item[1])
        ]
    if FIRST.search(text) and matches:
        matches = [min(matches, key=lambda item: at(item[1][0]["occurred_at"]))]
    # A water-volume question needs one completed, linked event. Choosing its
    # latest completion also prevents a broad model candidate set from mixing
    # older offers with a newer consumption confirmation.
    quantity = hydration_quantity_fact(text, [fact for item in matches for fact in item[1]])
    if quantity:
        matches = [(event_id(quantity['offered']), quantity['facts'], 100)]
    subject_ids = {item[0][0] for item in matches}
    ambiguous = (
        resident_id is None
        and len(subject_ids) > 1
        and (
            bool(re.search(r"이\s*분|그\s*분|이\s*어르신|그\s*어르신|그\s*뒤", text))
            or len(mentioned) == 1
        )
    )

    # Prefer matched whole events for an identified topic. If no event matches,
    # retain semantic discovery over the scope; this is not a question allowlist.
    focused_query = specific_topic or any(re.search(pattern, text) for pattern, _ in HINTS)
    def bounded(items, *, model_budget=False):
        picked, used_bytes = [], 0
        for _, facts, _ in items:
            size = sum(len(fact["summary"].encode("utf-8")) + 200 for fact in facts)
            if len(picked) + len(facts) <= (28 if model_budget else 128) and (
                not model_budget or used_bytes + size <= 6000
            ):
                picked.extend(facts)
                used_bytes += size
        return sorted(
            picked, key=lambda fact: (at(fact["occurred_at"]), str(fact["message_id"]))
        )

    candidates = bounded(
        matches
        if matches and (focused_query or hydration_focus or generic or FIRST.search(text) or REPEAT.search(text))
        else ranking,
        model_budget=True,
    )
    relevant = bounded(matches)
    if len(candidates) < sum(len(item[1]) for item in ranking):
        notes.append(
            f"확인 가능한 문장 {sum(len(item[1]) for item in ranking)}건 중 관련성·날짜 순으로 사건 전체가 담기는 {len(candidates)}건을 답변 후보로 확인했습니다."
        )
    return {
        "facts": [] if ambiguous else candidates,
        "rule_facts": [] if ambiguous else relevant,
        "scope_count": scope_count,
        "scope_facts": scope_facts,
        "ambiguous": ambiguous,
        "notes": notes,
        "question": question,
        "first": bool(FIRST.search(text)),
        "matched_event_count": len(matches),
    }


def answer_facts(
    question: str,
    facts: list[dict],
    *,
    notes=(),
    first=False,
    scope_count=0,
    ambiguous=False,
    focus=(),
) -> dict:
    limitations = list(notes)
    visible = []
    if ambiguous:
        answer = "선택 범위에서 여러 어르신의 관련 기록을 확인했지만 질문의 대상을 한 명으로 확정할 수 없습니다. 어르신을 선택하거나 비교할 대상을 알려 주세요."
    elif not facts:
        answer = (
            "선택 범위에 현재 접근 가능한 기록이 없습니다."
            if not scope_count
            else f"선택 범위의 접근 가능한 문장 {scope_count}건을 확인했지만 질문과 관련된 기록을 찾지 못했습니다."
        )
    else:
        focused = [fact for fact in focus if fact in facts]
        visible = (
            facts[:1]
            if first
            else focused[:3]
            if focused
            else facts
            if len(facts) <= 3
            else [facts[0], *facts[-2:]]
        )
        answer = "\n".join(
            f"{at(fact['occurred_at']):%m/%d %H:%M} {fact.get('resident_name') or '기록'}: {fact['summary']}"
            for fact in visible
        )
        # Rules preserve factual wording; the drawer contains only answer evidence.
        unique=[]
        for fact in visible:
            value=fact['summary'].strip()
            if value not in unique:unique.append(value)
        answer=' '.join(unique)
        if first:
            answer = f"선택 기간에서 처음 확인된 기록은 {at(visible[0]['occurred_at']):%m/%d %H:%M} " + answer
            limitations.append("실제 증상이나 도움의 시작일을 뜻하지 않습니다.")
        if len(facts) > len(visible):
            limitations.append(
                f"관련 문장 {len(facts)}건을 확인해 핵심 내용을 정리했습니다. 답변에 사용한 기록은 근거 보기에서 확인할 수 있습니다."
            )
    unknowns = question_unknowns(question, facts)
    timeline = [
        {
            "date": f"{at(fact['occurred_at']):%Y-%m-%d %H:%M}",
            "fact": fact["summary"],
            "resident_name": fact.get("resident_name", ""),
            "evidence_ids": [fact["message_id"]],
            "background": fact.get("background", False),
        }
        for fact in facts
    ]
    latest = {}
    for fact in facts:
        latest[fact.get("resident_id", fact.get("resident_name"))] = fact
    current = (
        "\n".join(
            f"{fact.get('resident_name') or '기록'}: {fact['summary']}"
            for fact in latest.values()
        )
        or None
    )
    result = {
        "answer": answer,
        # The evidence drawer must contain only records that formed the concise
        # answer, never the broader retrieval/timeline candidate set.
        "evidence_ids": list(dict.fromkeys(fact["message_id"] for fact in (visible if facts else []))),
        "matched_count": len(facts),
        "limitation": " ".join(limitations) or None,
        "timeline": timeline,
        "current_status": current,
        "unknowns": unknowns,
    }
    focused=focused_rule_answer(question,facts) if not ambiguous else None
    if focused:
        result['answer']=' '.join(sentence['text'] for sentence in focused)
        result['answer_sentences']=focused
        result['evidence_ids']=list(dict.fromkeys(
            evidence_id for sentence in focused for evidence_id in sentence['evidence_ids']
        ))
        quantity = hydration_answer(question, facts)
        if quantity:
            # This answer is derived from server-validated structured facts.
            # Keep it stable regardless of local-model warm/cold state.
            result['_deterministic_fact'] = True
            result['structured_facts'] = [quantity['fact']]
    return result


def question_unknowns(question, facts):
    text = " ".join(fact["summary"] for fact in facts)
    unknowns = []
    if (
        FOLLOWUP.search(question)
        and facts
        and not any(
            fact["kind"] in {"followup", "different", "repeated"} for fact in facts
        )
    ):
        unknowns.append(
            "확인된 기록은 위와 같으며, 그 뒤의 후속 결과는 선택 범위에서 확인되지 않습니다."
        )
    if any(fact["kind"] == "conflict" for fact in facts):
        unknowns.append("상충하는 값은 원문 확인 전까지 확정할 수 없습니다.")
    if re.search(r"왜|원인|이유", question):
        unknowns.append(
            "원인은 기록에서 확인되지 않습니다. 확인된 경과만 표시했으며 원인을 새로 추론하지 않았습니다."
            if not re.search(
                r"때문|원인.{0,20}(?:라고|으로|확인)|사유.{0,20}(?:으로|때문)", text
            )
            else "기록에 명시된 원인 설명만 인용하며 별도의 원인 판단은 하지 않습니다."
        )
    if "진단" in question and not re.search(r"진단|진료\s*결과|검사\s*결과", text):
        unknowns.append("이번 답변의 근거에서는 진단 내용을 확인할 수 없습니다.")
    if re.search(r"진단|치료|처방|점수|계획.*(?:만들|작성)", question):
        unknowns.append(
            "기록에 적힌 내용만 확인했으며 새로운 의학적 판단·치료·처방·점수를 만들지 않았습니다."
        )
    return unknowns
