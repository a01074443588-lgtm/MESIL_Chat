from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyze_kakao_exports_privacy_safe import (
    DESKTOP_MESSAGE_RE,
    MOBILE_MESSAGE_RE,
    _read_text,
)


def _source_terms(paths: list[Path]) -> tuple[set[str], set[str]]:
    speakers: set[str] = set()
    message_bodies: set[str] = set()
    for path in paths:
        text, _ = _read_text(path)
        for line in text.splitlines():
            stripped = line.strip()
            match = DESKTOP_MESSAGE_RE.match(stripped) or MOBILE_MESSAGE_RE.match(stripped)
            if match is None:
                continue
            speaker = match.group("speaker").strip()
            body = match.group("body").strip()
            if len(speaker) >= 2:
                speakers.add(speaker)
            if len(body) >= 8:
                message_bodies.add(body)
    return speakers, message_bodies


def verify(source_paths: list[Path], target_paths: list[Path]) -> dict[str, object]:
    speakers, message_bodies = _source_terms(source_paths)
    target_payloads = [path.read_text(encoding="utf-8") for path in target_paths]

    speaker_collision_count = sum(
        any(speaker in payload for payload in target_payloads) for speaker in speakers
    )
    exact_message_collision_count = sum(
        any(body in payload for payload in target_payloads) for body in message_bodies
    )
    source_filename_collision_count = sum(
        any(path.name in payload for payload in target_payloads) for path in source_paths
    )
    passed = not (
        speaker_collision_count
        or exact_message_collision_count
        or source_filename_collision_count
    )
    return {
        "schema_version": "kakao_synthetic_privacy_boundary_v1",
        "source_count": len(source_paths),
        "target_file_count": len(target_paths),
        "source_speaker_term_count": len(speakers),
        "source_nontrivial_message_count": len(message_bodies),
        "speaker_name_collision_count": speaker_collision_count,
        "exact_message_collision_count": exact_message_collision_count,
        "source_filename_collision_count": source_filename_collision_count,
        "raw_values_included": False,
        "source_paths_included": False,
        "external_transfer_count": 0,
        "passed": passed,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source", action="append", required=True, type=Path)
    parser.add_argument("--target", action="append", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.source, args.target)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
