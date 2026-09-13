import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_handwriting_vision_comparison import (
    PROMPT,
    REQUIRED_KEYS,
    _evaluate,
    _load_safe_fixture,
    _nano_payload,
    _parse_content,
    _qwen_payload,
    _request_metadata,
)


def _parsed() -> dict:
    return {
        "event_time": "2:00 PM",
        "quantity_value": 200,
        "quantity_unit": "mL",
        "completion_status": "completed",
        "follow_up_time": "4:00 PM",
        "follow_up_status": "pending",
        "privacy_note": "synthetic",
        "unreadable_fields": [],
    }


def _expected() -> dict:
    return {
        "event_time": "14:00",
        "quantity_value": 200,
        "quantity_unit": "ml",
        "completion_status": "completed",
        "follow_up_time": "16:00",
        "follow_up_status": "pending",
    }


def test_strict_contract_and_fact_evaluation_pass() -> None:
    parsed, error = _parse_content(json.dumps(_parsed()))
    evaluation = _evaluate(parsed, _expected())

    assert error is None
    assert evaluation["quality_passed"] is True
    assert evaluation["passed_checks"] == evaluation["total_checks"] == 8


def test_contract_rejects_unexpected_key() -> None:
    content = _parsed()
    content["person_name"] = "invented"

    parsed, error = _parse_content(json.dumps(content))

    assert parsed is None
    assert error == "unexpected_json_keys"


def test_provider_payloads_use_same_prompt_and_do_not_need_logged_media() -> None:
    nano = _nano_payload(b"png")
    qwen = _qwen_payload(b"png", 0)
    nano_metadata = _request_metadata(nano, 3)
    qwen_metadata = _request_metadata(qwen, 3)

    assert nano["messages"][0]["content"][0]["text"] == PROMPT
    assert qwen["messages"][0]["content"] == PROMPT
    assert set(REQUIRED_KEYS) == set(_parsed())
    assert nano_metadata["asset_data_logged"] is False
    assert qwen_metadata["asset_data_logged"] is False
    assert "cG5n" not in json.dumps(nano_metadata)
    assert "cG5n" not in json.dumps(qwen_metadata)


def test_fixture_loader_blocks_non_deidentified_manifest(tmp_path: Path) -> None:
    image = b"synthetic"
    (tmp_path / "case.png").write_bytes(image)
    expected = _expected()
    case = {
        "case_id": "one",
        "file": "case.png",
        "media_type": "image/png",
        "sha256": hashlib.sha256(image).hexdigest(),
        "size_bytes": len(image),
        "expected": expected,
    }
    manifest = {
        "fixture_version": "handwriting_vision_compare_v1",
        "privacy": {
            "synthetic": True,
            "external_ai_allowed": True,
            "contains_direct_identifiers": True,
            "contains_real_person_or_record": False,
        },
        "cases": [case] * 6,
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="비식별"):
        _load_safe_fixture(tmp_path)
