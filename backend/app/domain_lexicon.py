from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Iterable
import unicodedata
from difflib import SequenceMatcher


LEXICON_PATH = Path(__file__).with_name("data") / "long_term_care_lexicon.v1.json"
KOREAN_OR_LATIN_TOKEN = re.compile(r"[가-힣]{1,16}|[A-Za-z]{2,12}")
VALID_SERVICE_CONTEXTS = frozenset({"facility", "daycare", "homecare"})
MAX_PROMPT_CHARS = 6_000
MAX_HOTWORDS_CHARS = 4_000


@dataclass(frozen=True)
class DomainTerm:
    canonical: str
    aliases: tuple[str, ...]
    speech_aliases: tuple[str, ...]
    category: str
    priority: int


@dataclass(frozen=True)
class DomainSituation:
    id: str
    label: str
    triggers: tuple[str, ...]
    related_terms: tuple[str, ...]
    phrases: tuple[str, ...]


@dataclass(frozen=True)
class LongTermCareLexicon:
    schema_version: int
    safety_rule: str
    terms: tuple[DomainTerm, ...]
    situations: tuple[DomainSituation, ...]


@dataclass(frozen=True)
class SpeechContextPack:
    initial_prompt: str
    hotwords: str
    resident_names: tuple[str, ...]
    domain_terms: tuple[str, ...]
    phrases: tuple[str, ...]


def _unique(values: Iterable[str], *, limit: int | None = None) -> tuple[str, ...]:
    result: list[str] = []
    for raw in values:
        value = re.sub(r"\s+", " ", str(raw or "")).strip()
        if not value or value in result:
            continue
        result.append(value)
        if limit is not None and len(result) >= limit:
            break
    return tuple(result)


@lru_cache(maxsize=1)
def load_long_term_care_lexicon() -> LongTermCareLexicon:
    payload = json.loads(LEXICON_PATH.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise RuntimeError("지원하지 않는 장기요양 문맥 사전 버전입니다.")
    terms = tuple(
        DomainTerm(
            canonical=str(item["canonical"]).strip(),
            aliases=_unique(item.get("aliases", [])),
            speech_aliases=_unique(item.get("speech_aliases", [])),
            category=str(item["category"]).strip(),
            priority=max(0, min(100, int(item.get("priority", 50)))),
        )
        for item in payload.get("terms", [])
        if isinstance(item, dict)
        and str(item.get("canonical", "")).strip()
        and str(item.get("category", "")).strip()
    )
    situations = tuple(
        DomainSituation(
            id=str(item["id"]).strip(),
            label=str(item["label"]).strip(),
            triggers=_unique(item.get("triggers", [])),
            related_terms=_unique(item.get("related_terms", [])),
            phrases=_unique(item.get("phrases", [])),
        )
        for item in payload.get("situations", [])
        if isinstance(item, dict)
        and str(item.get("id", "")).strip()
        and str(item.get("label", "")).strip()
    )
    if not terms or not situations:
        raise RuntimeError("장기요양 문맥 사전이 비어 있습니다.")
    return LongTermCareLexicon(
        schema_version=1,
        safety_rule=str(payload.get("safety_rule", "")).strip(),
        terms=terms,
        situations=situations,
    )


def canonical_terms() -> tuple[str, ...]:
    lexicon = load_long_term_care_lexicon()
    return _unique(
        term.canonical
        for term in sorted(
            lexicon.terms,
            key=lambda item: (-item.priority, item.category, item.canonical),
        )
    )


def term_corrections() -> tuple[tuple[str, str], ...]:
    lexicon = load_long_term_care_lexicon()
    return tuple(
        (alias, term.canonical)
        for term in lexicon.terms
        for alias in (*term.aliases, *term.speech_aliases)
        if alias != term.canonical
    )


def hangul_similarity(left: str, right: str) -> float:
    """Compare both complete Hangul syllables and decomposed jamo."""

    normalized_left = re.sub(r"\s+", "", left).casefold()
    normalized_right = re.sub(r"\s+", "", right).casefold()
    if not normalized_left or not normalized_right:
        return 0.0
    syllable_score = SequenceMatcher(
        None,
        normalized_left,
        normalized_right,
    ).ratio()
    jamo_score = SequenceMatcher(
        None,
        unicodedata.normalize("NFD", normalized_left),
        unicodedata.normalize("NFD", normalized_right),
    ).ratio()
    return max(syllable_score, jamo_score)


def matching_situations(value: str | None, *, limit: int = 5) -> list[dict[str, object]]:
    if not value:
        return []
    compact = re.sub(r"\s+", "", value)
    results: list[dict[str, object]] = []
    for situation in load_long_term_care_lexicon().situations:
        matched = [
            trigger
            for trigger in situation.triggers
            if re.sub(r"\s+", "", trigger) in compact
        ]
        if not matched:
            continue
        results.append(
            {
                "id": situation.id,
                "label": situation.label,
                "matched_terms": matched,
                "related_terms": list(situation.related_terms),
                "phrases": list(situation.phrases),
                "safety_rule": "원문 근거가 있는 단어의 철자와 연결만 교정하고 새 사실은 추가하지 않음",
            }
        )
        if len(results) >= limit:
            break
    return results


def build_speech_context(
    *,
    resident_names: Iterable[str] = (),
    organization_terms: Iterable[str] = (),
    service_context: str | None = None,
    max_resident_names: int = 120,
    max_domain_terms: int = 120,
    max_phrases: int = 28,
) -> SpeechContextPack:
    names = _unique(resident_names, limit=max_resident_names)
    lexicon = load_long_term_care_lexicon()
    ordered_terms = sorted(
        lexicon.terms,
        key=lambda item: (-item.priority, item.category, item.canonical),
    )
    domain_terms = _unique(
        [
            *_unique(organization_terms, limit=80),
            *(
                value
                for term in ordered_terms
                for value in (term.canonical, *term.speech_aliases)
            ),
        ],
        limit=max_domain_terms,
    )
    phrases = _unique(
        (
            phrase
            for situation in lexicon.situations
            for phrase in situation.phrases
        ),
        limit=max_phrases,
    )
    service_label = {
        "facility": "시설",
        "daycare": "주간보호",
        "homecare": "방문요양",
    }.get(service_context or "", "시설·주간보호·방문요양")
    prompt_parts = [f"장기요양기관 {service_label} 한국어 업무보고."]
    if names:
        prompt_parts.append("현재 이용 어르신 이름: " + ", ".join(names) + ".")
    prompt_parts.append("주요 업무용어: " + ", ".join(domain_terms) + ".")
    prompt_parts.append("자주 쓰는 표현: " + " / ".join(phrases) + ".")
    initial_prompt = " ".join(prompt_parts)[:MAX_PROMPT_CHARS]
    hotwords = ", ".join(_unique([*names, *domain_terms]))[:MAX_HOTWORDS_CHARS]
    return SpeechContextPack(
        initial_prompt=initial_prompt,
        hotwords=hotwords,
        resident_names=names,
        domain_terms=domain_terms,
        phrases=phrases,
    )


def ai_context(value: str | None) -> dict[str, object]:
    lexicon = load_long_term_care_lexicon()
    return {
        "safety_rule": lexicon.safety_rule,
        "matched_situations": matching_situations(value),
        "standard_terms": list(canonical_terms()[:120]),
    }


def handwriting_lexicon_context() -> dict[str, object]:
    """Public vocabulary only; never accept names, rosters or historical records.

    Three legacy alias groups require separate curation. Preserve their source
    JSON but expose only spelling-equivalent aliases in this correction path.
    """
    quarantine = {"확인", "아침 송영 시", "운전원 선생"}
    terms = []
    for term in load_long_term_care_lexicon().terms:
        spelling = list(term.aliases)
        speech = list(term.speech_aliases)
        if term.canonical in quarantine:
            spelling = [v for v in spelling if v.replace(" ", "") == term.canonical.replace(" ", "")]
            speech = []
        terms.append({"canonical": term.canonical, "category": term.category,
                      "spelling_aliases": spelling, "speech_aliases": speech})
    return {"terms": terms, "rules": [
        "선택된 원문 행의 정확한 canonical, spelling_alias, speech_alias만 표준화 근거로 사용한다.",
        "Unicode 및 연속 공백만 정규화한다. 등록되지 않은 유사어·음운 유사도는 자동 승인 근거가 아니다.",
        "단어장은 새로운 사건·이름·시간·수량·약명·진단·완료 여부의 근거가 아니다.",
        "실제 사람 이름을 단어장으로 추정하지 않는다. 오수와 오후, 기간과 기관을 혼동하지 않는다.",
    ]}
