from __future__ import annotations

import json
from pathlib import Path

from scripts.analyze_kakao_exports_privacy_safe import analyze_exports


def test_structure_profile_never_contains_source_names_or_messages(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    private_name = "원문실명"
    private_message = "실제 사건 문장은 결과에 남으면 안 됩니다."
    source.write_text(
        "--------------- 2026년 8월 30일 일요일 ---------------\n"
        f"[{private_name}] [오전 9:10] {private_message}\n"
        "추가 멀티라인 원문\n",
        encoding="utf-8",
    )

    result = analyze_exports([source])
    rendered = json.dumps(result, ensure_ascii=False)

    assert result["source_count"] == 1
    assert result["sources"][0]["parsed_message_count"] == 1
    assert result["sources"][0]["participant_count"] == 1
    assert result["sources"][0]["continuation_line_count"] == 1
    assert private_name not in rendered
    assert private_message not in rendered
    assert str(source) not in rendered
