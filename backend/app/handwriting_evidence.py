"""Exact, source-owned vocabulary evidence. No model, database or network calls."""
from __future__ import annotations

from difflib import SequenceMatcher
from functools import lru_cache
import re
import unicodedata

from .domain_lexicon import handwriting_lexicon_context


def normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def source_rows(ocr: str, whisper: str) -> dict[str, list[str]]:
    return {source: [row.strip() for row in value.splitlines() if row.strip()]
            for source, value in (("ocr", ocr), ("whisper", whisper))}


@lru_cache(maxsize=512)
def term_pattern(term: str) -> re.Pattern:
    # Korean particles/verb endings may follow a whole lexicon expression.
    # Never match inside a preceding word or silently remove internal spaces.
    return re.compile(r"(?<![가-힣A-Za-z0-9])" + re.escape(normalized_text(term))
                      + r"(?=$|[^가-힣A-Za-z0-9]|은|는|이|가|을|를|에|의|과|와|도|로|으로|하|함|한|했|중)")


def occurrences(value: str, *, aliases: bool = True) -> list[dict]:
    text = normalized_text(value)
    matches = []
    for term in handwriting_lexicon_context()["terms"]:
        variants = [(term["canonical"], "canonical")]
        if aliases:
            variants += [(v, "spelling_alias") for v in term["spelling_aliases"]]
            variants += [(v, "speech_alias") for v in term["speech_aliases"]]
        for value, kind in variants:
            for match in term_pattern(value).finditer(text):
                matches.append({"canonical": term["canonical"], "source_value": match.group(),
                                "match_type": kind, "start": match.start(), "end": match.end()})
    # Prefer the complete expression to a nested word, e.g. 오수 시간 over 오수.
    selected = []
    for item in sorted(matches, key=lambda x: (-(x["end"] - x["start"]), x["start"], x["canonical"])):
        if not any(item["start"] < old["end"] and old["start"] < item["end"] for old in selected):
            selected.append(item)
    return sorted(selected, key=lambda x: x["start"])


def standardize_exact(value: str) -> str:
    text = normalized_text(value)
    for item in reversed(occurrences(text)):
        text = text[:item["start"]] + item["canonical"] + text[item["end"]:]
    return text


def validate_lexicon(candidate: str, rows: dict[str, list[str]], selection: object,
                     claims: object) -> tuple[list[dict], dict[str, list[str]]]:
    if not isinstance(selection, dict) or set(selection) != {"ocr", "whisper"}:
        raise ValueError("source_rows")
    selected: dict[str, list[str]] = {"ocr": [], "whisper": []}
    available = []
    for source in ("ocr", "whisper"):
        numbers = selection[source]
        if not isinstance(numbers, list) or any(type(n) is not int or n < 1 or n > len(rows[source]) for n in numbers):
            raise ValueError("source_rows")
        if len(numbers) != len(set(numbers)):
            raise ValueError("duplicate_rows")
        for number in sorted(numbers):
            row = rows[source][number - 1]
            selected[source].append(row)
            for match in occurrences(row):
                available.append({k: v for k, v in match.items() if k not in {"start", "end"}} | {
                    "source": source, "source_row": number})
    if not available and not any(selected.values()):
        raise ValueError("empty_rows")
    proposed = {item["canonical"] for item in occurrences(candidate, aliases=False)}
    applications = []
    priority = {"canonical": 0, "spelling_alias": 1, "speech_alias": 2}
    for canonical in sorted(proposed):
        matches = [item for item in available if item["canonical"] == canonical]
        if not matches:
            raise ValueError("unsupported_canonical")
        applications.append(min(matches, key=lambda x: (priority[x["match_type"]], x["source"] != "ocr", x["source_row"], x["source_value"])))
    support = "\n".join(row for values in selected.values() for row in values)
    for before, after in (("오수", "오후"), ("오후", "오수"), ("기간", "기관")):
        if term_pattern(before).search(normalized_text(support)) and term_pattern(after).search(normalized_text(candidate)) and not term_pattern(after).search(normalized_text(support)):
            raise ValueError("forbidden_substitution")
    if not isinstance(claims, list):
        raise ValueError("applications")
    seen = set()
    for claim in claims:
        if not isinstance(claim, dict):
            raise ValueError("application")
        keys = ("canonical", "source", "source_row", "source_value")
        if not all(key in claim for key in keys) or type(claim["source_row"]) is not int:
            raise ValueError("application")
        token = tuple(claim[key] for key in keys)
        if token in seen:
            raise ValueError("duplicate_application")
        seen.add(token)
        if not any(all(claim[key] == item[key] for key in keys)
                   and ("match_type" not in claim or claim["match_type"] == item["match_type"])
                   for item in applications):
            raise ValueError("application_mismatch")
    return applications, selected


def _korean_number(value: str) -> int:
    native = {"한": 1, "두": 2, "세": 3, "네": 4, "다섯": 5, "여섯": 6,
              "일곱": 7, "여덟": 8, "아홉": 9, "열": 10, "열한": 11, "열두": 12}
    if value in native:
        return native[value]
    digits = {c: i for i, c in enumerate("영일이삼사오육칠팔구")}
    total = current = 0
    for char in value:
        if char in digits:
            current = digits[char]
        else:
            total += (current or 1) * {"십": 10, "백": 100, "천": 1000}[char]
            current = 0
    return total + current


def normalize_spoken_numbers(value: str) -> str:
    """Lossless numeral/unit notation conversion, only immediately before units."""
    text = unicodedata.normalize("NFKC", value)
    number = r"(?:열두|열한|다섯|여섯|일곱|여덟|아홉|한|두|세|네|열|[영일이삼사오육칠팔구십백천]+)"
    text = re.sub(r"(?<![가-힣])(" + number + r")\s*(?=밀리리터|월|일|시|분)",
                  lambda m: str(_korean_number(m.group(1))), text)
    text = re.sub(r"(\d+)\s*밀리리터", r"\1mL", text)
    text = re.sub(r"(\d+)\s*(월|일|시|분|mL|ml)", lambda m: m.group(1) + ("mL" if m.group(2).lower() == "ml" else m.group(2)), text)
    return text


def voice_document(ocr: str, voice: str) -> str | None:
    """Align contiguous voice word spans to OCR lines, retaining every voice word.

    Similarity here selects layout only, never authorizes lexicon substitutions.
    """
    image_rows = [normalized_text(normalize_spoken_numbers(x)) for x in ocr.splitlines() if x.strip()]
    spoken = normalized_text(normalize_spoken_numbers(voice))
    words = spoken.split()
    if not image_rows or len(words) < len(image_rows) or len(words) > 350 or len(image_rows) > 60:
        return None
    def compact(value: str) -> str:
        return re.sub(r"\s+", "", value)
    if SequenceMatcher(None, compact(" ".join(image_rows)), compact(spoken), autojunk=False).ratio() < 0.60:
        return None
    # Dynamic programming covers all words in original order, without inventing
    # or dropping facts. Penalize large line-length mismatch.
    costs = {0: (0.0, [])}
    for index, row in enumerate(image_rows):
        next_costs = {}
        remaining = len(image_rows) - index - 1
        for start, (cost, lines) in costs.items():
            for end in range(start + 1, len(words) - remaining + 1):
                segment = " ".join(words[start:end])
                ratio = SequenceMatcher(None, compact(row), compact(segment), autojunk=False).ratio()
                score = cost + (1 - ratio) * max(len(compact(row)), len(compact(segment)))
                if end not in next_costs or score < next_costs[end][0]:
                    next_costs[end] = (score, [*lines, segment])
        costs = next_costs
    if len(words) not in costs:
        return None
    result = []
    for template, line in zip(image_rows, costs[len(words)][1], strict=True):
        if ". " in template and ". " not in line:
            prefix, suffix = template.rsplit(". ", 1)
            if suffix and line.endswith(" " + suffix):
                line = line[:-len(suffix)].rstrip() + ". " + suffix
        # Case marker insertion changes grammar only, not the recorded time.
        line = re.sub(r"(\d+분)(?= 확인함)", r"\1에", line)
        result.append(standardize_exact(line))
    return "\n".join(result)
