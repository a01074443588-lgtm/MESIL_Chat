from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable


MOBILE_MESSAGE_RE = re.compile(
    r"^\[(?P<speaker>[^\]]+)\]\s+\[(?P<ampm>오전|오후)\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\]\s?(?P<body>.*)$"
)
DESKTOP_MESSAGE_RE = re.compile(
    r"^(?P<date>\d{4}년\s+\d{1,2}월\s+\d{1,2}일)\s+(?P<ampm>오전|오후)\s+(?P<hour>\d{1,2}):(?P<minute>\d{2}),\s*(?P<speaker>.+?)\s*:\s*(?P<body>.*)$"
)
DATE_SEPARATOR_RE = re.compile(
    r"^-+\s*(?P<date>\d{4}년\s+\d{1,2}월\s+\d{1,2}일)(?:\s+\S+요일)?\s*-+$"
)
MEDIA_MARKER_RE = re.compile(r"사진|동영상|파일|음성메시지|이모티콘")


@dataclass(frozen=True)
class ParsedMessage:
    speaker_token: str
    occurred_at: datetime | None
    body_length: int
    media_marker: bool


def _read_text(path: Path) -> tuple[str, str]:
    payload = path.read_bytes()
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return payload.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise ValueError("지원하는 로컬 문자 인코딩으로 읽을 수 없습니다.")


def _parse_hour(ampm: str, hour: str) -> int:
    value = int(hour) % 12
    return value + (12 if ampm == "오후" else 0)


def _parse_date(value: str) -> datetime:
    return datetime.strptime(value.replace(" ", ""), "%Y년%m월%d일")


def _speaker_token(source_id: str, speaker: str) -> str:
    digest = hashlib.sha256(f"{source_id}\0{speaker}".encode("utf-8")).hexdigest()
    return digest[:16]


def _quantiles(values: list[int]) -> dict[str, int]:
    if not values:
        return {"min": 0, "p50": 0, "p90": 0, "max": 0}
    ordered = sorted(values)

    def pick(fraction: float) -> int:
        index = min(len(ordered) - 1, max(0, math.ceil(len(ordered) * fraction) - 1))
        return ordered[index]

    return {"min": ordered[0], "p50": pick(0.5), "p90": pick(0.9), "max": ordered[-1]}


def analyze_export(path: Path, source_id: str) -> dict[str, object]:
    text, encoding = _read_text(path)
    lines = text.splitlines()
    current_date: datetime | None = None
    messages: list[ParsedMessage] = []
    continuation_lines = 0
    system_lines = 0
    format_counts: Counter[str] = Counter()

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        separator = DATE_SEPARATOR_RE.match(stripped)
        if separator:
            current_date = _parse_date(separator.group("date"))
            continue
        match = DESKTOP_MESSAGE_RE.match(stripped)
        if match:
            message_date = _parse_date(match.group("date"))
            occurred_at = message_date.replace(
                hour=_parse_hour(match.group("ampm"), match.group("hour")),
                minute=int(match.group("minute")),
            )
            body = match.group("body")
            messages.append(
                ParsedMessage(
                    speaker_token=_speaker_token(source_id, match.group("speaker")),
                    occurred_at=occurred_at,
                    body_length=len(body),
                    media_marker=bool(MEDIA_MARKER_RE.search(body)),
                )
            )
            format_counts["desktop"] += 1
            continue
        match = MOBILE_MESSAGE_RE.match(stripped)
        if match:
            occurred_at = None
            if current_date is not None:
                occurred_at = current_date.replace(
                    hour=_parse_hour(match.group("ampm"), match.group("hour")),
                    minute=int(match.group("minute")),
                )
            body = match.group("body")
            messages.append(
                ParsedMessage(
                    speaker_token=_speaker_token(source_id, match.group("speaker")),
                    occurred_at=occurred_at,
                    body_length=len(body),
                    media_marker=bool(MEDIA_MARKER_RE.search(body)),
                )
            )
            format_counts["mobile"] += 1
            continue
        if messages and not stripped.startswith(("저장한 날짜", "카카오톡 대화")):
            continuation_lines += 1
        else:
            system_lines += 1

    speakers = Counter(message.speaker_token for message in messages)
    timestamps = [message.occurred_at for message in messages if message.occurred_at is not None]
    hour_buckets = {"00_05": 0, "06_11": 0, "12_17": 0, "18_23": 0}
    for timestamp in timestamps:
        if timestamp.hour < 6:
            hour_buckets["00_05"] += 1
        elif timestamp.hour < 12:
            hour_buckets["06_11"] += 1
        elif timestamp.hour < 18:
            hour_buckets["12_17"] += 1
        else:
            hour_buckets["18_23"] += 1

    speaker_switches = sum(
        first.speaker_token != second.speaker_token
        for first, second in zip(messages, messages[1:])
    )
    dated_span_days = 0
    if timestamps:
        dated_span_days = max(0, (max(timestamps).date() - min(timestamps).date()).days)

    target_message_count = min(48, max(24, round(len(messages) ** 0.5 * 2.2)))
    return {
        "source_id": source_id,
        "input_fingerprint": hashlib.sha256(path.read_bytes()).hexdigest()[:16],
        "byte_size": path.stat().st_size,
        "encoding": encoding,
        "detected_format": format_counts.most_common(1)[0][0] if format_counts else "unknown",
        "line_count": len(lines),
        "parsed_message_count": len(messages),
        "participant_count": len(speakers),
        "participant_activity_share_percent": [
            round(count * 100 / len(messages), 1) for _, count in speakers.most_common()
        ] if messages else [],
        "dated_span_days": dated_span_days,
        "message_length": _quantiles([message.body_length for message in messages]),
        "continuation_line_count": continuation_lines,
        "system_line_count": system_lines,
        "media_marker_count": sum(message.media_marker for message in messages),
        "speaker_switch_rate": round(speaker_switches / max(1, len(messages) - 1), 4),
        "time_bucket_counts": hour_buckets,
        "synthetic_generation_target": {
            "room_count": 1,
            "message_count": target_message_count,
            "minimum_follow_up_threads": max(5, target_message_count // 5),
        },
    }


def analyze_exports(paths: Iterable[Path]) -> dict[str, object]:
    sources = [analyze_export(path, f"source_{index:02d}") for index, path in enumerate(paths, 1)]
    return {
        "schema_version": "privacy_safe_kakao_structure_profile_v1",
        "privacy_contract": {
            "raw_text_included": False,
            "speaker_names_included": False,
            "room_names_included": False,
            "exact_source_dates_included": False,
            "source_paths_included": False,
            "external_transfer_used": False,
            "profile_use": "fully_synthetic_room_shape_only",
        },
        "source_count": len(sources),
        "sources": sources,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    result = analyze_exports(args.inputs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"source_count": result["source_count"], "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
