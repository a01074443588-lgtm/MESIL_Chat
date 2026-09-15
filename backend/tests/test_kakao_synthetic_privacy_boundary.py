from __future__ import annotations

import json
import sys
from pathlib import Path


SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from verify_kakao_synthetic_privacy_boundary import verify  # noqa: E402


def test_privacy_report_never_emits_source_values(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text(
        "[테스트화자] [오전 9:00] 이 문장은 원문 충돌 검사용으로만 사용합니다.\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.json"
    target.write_text(json.dumps({"content": "새로 작성한 합성 문장입니다."}), encoding="utf-8")

    result = verify([source], [target])

    assert result["passed"] is True
    assert result["speaker_name_collision_count"] == 0
    assert result["exact_message_collision_count"] == 0
    assert "테스트화자" not in json.dumps(result, ensure_ascii=False)
    assert str(source) not in json.dumps(result, ensure_ascii=False)


def test_privacy_report_counts_collisions_without_emitting_values(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    source.write_text(
        "[테스트화자] [오전 9:00] 이 문장은 원문 충돌 검사용으로만 사용합니다.\n",
        encoding="utf-8",
    )
    target = tmp_path / "target.json"
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    result = verify([source], [target])

    assert result["passed"] is False
    assert result["speaker_name_collision_count"] == 1
    assert result["exact_message_collision_count"] == 1
    assert "테스트화자" not in json.dumps(result, ensure_ascii=False)
