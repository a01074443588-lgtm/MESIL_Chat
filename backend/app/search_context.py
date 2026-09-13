from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Iterable
from uuid import UUID


_SOURCE_HEADER_PATTERN = re.compile(
    r"^\[(?:답글|이미지 판독문|음성 받아쓰기)(?:\s*·[^\]\r\n]+)?\]",
    re.MULTILINE,
)
_GENERIC_ATTACHMENT_BODIES = {
    "파일을 첨부했습니다.",
    "사진을 첨부했습니다.",
    "음성을 첨부했습니다.",
}


def _resident_name_aliases(display_name: str) -> set[str]:
    compact = re.sub(r"\s+", "", display_name.strip())
    without_marker = compact.replace("(가명)", "")
    aliases = {display_name.strip(), compact, without_marker}
    numbered = re.fullmatch(r"(.+?)(\d{3})", without_marker)
    if numbered:
        prefix, number = numbered.groups()
        aliases.update(
            {
                f"{prefix} {number}",
                f"{prefix}(가명){number}",
                f"{prefix}(가명) {number}",
            }
        )
    return {alias for alias in aliases if alias}


def _resident_alias_matcher(
    resident_names: Iterable[str],
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    alias_owner: dict[str, str] = {}
    for resident_name in resident_names:
        normalized_name = str(resident_name or "").strip()
        if not normalized_name:
            continue
        for alias in _resident_name_aliases(normalized_name):
            alias_owner.setdefault(alias.casefold(), normalized_name)
            # OCR/STT often removes the space before common honorifics.
            alias_owner.setdefault(f"{alias}어르신".casefold(), normalized_name)
            alias_owner.setdefault(f"{alias} 어르신".casefold(), normalized_name)
            alias_owner.setdefault(f"{alias}님".casefold(), normalized_name)
    if not alias_owner:
        return None, alias_owner
    aliases = sorted(alias_owner, key=len, reverse=True)
    # Korean names do not always have whitespace around them.  Nevertheless,
    # matching them inside a longer Hangul/ASCII token is more likely to be a
    # false resident boundary than a valid name label.
    matcher = re.compile(
        r"(?<![0-9A-Za-z가-힣])(?:"
        + "|".join(re.escape(alias) for alias in aliases)
        + r")(?![0-9A-Za-z가-힣])",
        re.IGNORECASE,
    )
    return matcher, alias_owner


def resident_specific_search_text(
    text: str,
    *,
    target_name: str,
    resident_names: list[str],
    allow_unscoped: bool = False,
) -> str:
    """Return only the target resident's spans from a shared search source.

    Each explicit active-roster name starts a span that ends at the next name,
    even when the message currently has only one confirmed resident link.
    Attachment/reply headers are retained for provenance.  Text with no explicit
    resident boundary is kept only when the caller opts into ``allow_unscoped``.
    """

    normalized_text = str(text or "").strip()
    if not normalized_text:
        return ""
    unique_names = list(
        dict.fromkeys(
            str(name or "").strip()
            for name in resident_names
            if str(name or "").strip()
        )
    )
    if len(unique_names) <= 1:
        return normalized_text

    matcher, alias_owner = _resident_alias_matcher(unique_names)
    if matcher is None:
        return normalized_text if allow_unscoped else ""

    source_starts = [
        match.start() for match in _SOURCE_HEADER_PATTERN.finditer(normalized_text)
    ]
    if not source_starts or source_starts[0] != 0:
        source_starts.insert(0, 0)
    source_starts.append(len(normalized_text))

    selected_segments: list[str] = []
    any_resident_match = False
    for source_index in range(len(source_starts) - 1):
        source = normalized_text[
            source_starts[source_index] : source_starts[source_index + 1]
        ]
        header_match = _SOURCE_HEADER_PATTERN.match(source)
        header = header_match.group(0) if header_match else ""
        content_offset = header_match.end() if header_match else 0
        content = source[content_offset:]
        matches = list(matcher.finditer(content))
        any_resident_match = any_resident_match or bool(matches)
        source_segments: list[str] = []
        for position, match in enumerate(matches):
            owner = alias_owner.get(match.group(0).casefold())
            if owner != target_name:
                continue
            end = (
                matches[position + 1].start()
                if position + 1 < len(matches)
                else len(content)
            )
            segment = content[match.start() : end].strip(" \t\r\n-·•")
            if segment and segment not in source_segments:
                source_segments.append(segment)
        if source_segments:
            selected = "\n".join(source_segments)
            if header:
                selected = f"{header}\n{selected}"
            if selected not in selected_segments:
                selected_segments.append(selected)

    if selected_segments:
        return "\n\n".join(selected_segments).strip()
    if not any_resident_match and allow_unscoped:
        return normalized_text
    return ""


def _entry_resident_refs(entry: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        ref
        for ref in entry.get("resident_refs") or []
        if isinstance(ref, dict)
        and str(ref.get("id") or "").strip()
        and str(ref.get("name") or "").strip()
    ]


def isolate_search_entries_for_resident(
    entries: list[dict[str, Any]],
    *,
    resident_id: UUID | str,
    known_resident_names: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Build provider-ready entries containing only one resident's context.

    ``resident_refs`` must contain only confirmed message links (plus the legacy
    primary resident link).  ``known_resident_names`` is the active roster used
    only to find text boundaries; roster membership by itself never links an
    entry to a resident.

    Unscoped message bodies are retained because they are the authored context
    of the confirmed links.  Unscoped OCR/STT text and comments are retained
    only for a sole confirmed resident.  Once any active resident name appears,
    every source is narrowed to the selected resident's explicit spans.  Generic
    attachment boilerplate never keeps an otherwise empty entry alive.

    Message identifiers and source timestamps remain unchanged so the UI can
    open the complete original evidence.  Only the derived summary context is
    narrowed; stored messages, OCR/STT text, and staff-reviewed text are never
    mutated.
    """

    target_id = str(resident_id)
    roster_names = list(
        dict.fromkeys(
            str(name or "").strip()
            for name in known_resident_names
            if str(name or "").strip()
        )
    )
    isolated_entries: list[dict[str, Any]] = []
    for entry in entries:
        resident_refs = _entry_resident_refs(entry)
        target_ref = next(
            (ref for ref in resident_refs if str(ref.get("id")) == target_id),
            None,
        )
        if target_ref is None:
            continue

        target_name = str(target_ref["name"]).strip()
        resident_names = list(
            dict.fromkeys(str(ref["name"]).strip() for ref in resident_refs)
        )
        shared_message = len(resident_names) > 1
        boundary_names = list(dict.fromkeys([*resident_names, *roster_names]))
        narrowed = deepcopy(entry)
        narrowed["body"] = resident_specific_search_text(
            str(entry.get("body") or ""),
            target_name=target_name,
            resident_names=boundary_names,
            allow_unscoped=True,
        )
        narrowed["attachment_text"] = resident_specific_search_text(
            str(entry.get("attachment_text") or ""),
            target_name=target_name,
            resident_names=boundary_names,
            allow_unscoped=not shared_message,
        )
        narrowed["comments"] = [
            selected
            for comment in entry.get("comments") or []
            if (
                selected := resident_specific_search_text(
                    str(comment or ""),
                    target_name=target_name,
                    resident_names=boundary_names,
                    allow_unscoped=not shared_message,
                )
            )
        ]

        context_changed = (
            str(narrowed.get("body") or "").strip()
            != str(entry.get("body") or "").strip()
            or str(narrowed.get("attachment_text") or "").strip()
            != str(entry.get("attachment_text") or "").strip()
            or narrowed.get("comments", []) != list(entry.get("comments") or [])
        )

        narrowed["resident"] = target_name
        narrowed["resident_names"] = [target_name]
        narrowed["resident_refs"] = [deepcopy(target_ref)]
        narrowed["selected_resident_id"] = target_id
        narrowed["context_isolated"] = shared_message or context_changed

        has_body = str(narrowed.get("body") or "").strip() not in (
            _GENERIC_ATTACHMENT_BODIES | {""}
        )
        has_derived_text = bool(
            str(narrowed.get("attachment_text") or "").strip()
            or any(
                str(comment or "").strip() for comment in narrowed.get("comments") or []
            )
        )
        if not has_body and not has_derived_text:
            # There is no safely attributable text to send to an AI provider.
            continue
        isolated_entries.append(narrowed)
    return isolated_entries
