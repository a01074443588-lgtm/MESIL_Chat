import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_nano_omni_multimodal_preflight import (
    DEFAULT_MODEL,
    _build_payload,
    _evaluate_output,
    _load_safe_fixture,
    _normalize_time,
    _parse_and_sanitize_content,
    _request_metadata,
)


def _manifest() -> dict:
    return {
        "fixture_version": "nano_omni_deidentified_v1",
        "privacy": {
            "synthetic": True,
            "external_ai_allowed": True,
            "contains_direct_identifiers": False,
            "contains_real_person_or_record": False,
        },
        "expected": {
            "time": "14:00",
            "amount_ml": 200,
            "follow_up_status": "pending",
            "image_audio_consistent": True,
        },
        "assets": {
            "image": {"media_type": "image/png"},
            "audio": {"media_type": "audio/wav"},
        },
    }


def test_build_combined_payload_uses_only_official_media_parts() -> None:
    payload = _build_payload(
        mode="combined",
        model=DEFAULT_MODEL,
        manifest=_manifest(),
        assets={"image": b"png", "audio": b"wav"},
    )

    content = payload["messages"][0]["content"]
    assert [part["type"] for part in content] == ["text", "image_url", "audio_url"]
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
    assert content[2]["audio_url"]["url"].startswith("data:audio/wav;base64,")
    assert payload["model"] == DEFAULT_MODEL
    assert payload["chat_template_kwargs"] == {"enable_thinking": False}


def test_request_metadata_never_contains_asset_data_urls() -> None:
    payload = _build_payload(
        mode="image",
        model=DEFAULT_MODEL,
        manifest=_manifest(),
        assets={"image": b"private-bytes", "audio": b"unused"},
    )

    metadata = _request_metadata(payload)

    assert metadata["media_types"] == ["image_url"]
    assert metadata["asset_data_urls_logged"] is False
    assert "private-bytes" not in json.dumps(metadata)


def test_parse_and_evaluate_combined_output_requires_all_expected_facts() -> None:
    content = json.dumps(
        {
            "image_audio_consistent": True,
            "time": "14:00",
            "amount_ml": 200,
            "follow_up_status": "pending",
            "contradictions": [],
            "privacy_note": "synthetic",
        }
    )

    parsed, parse_error = _parse_and_sanitize_content("combined", content)
    evaluation = _evaluate_output("combined", parsed, _manifest()["expected"])

    assert parse_error is None
    assert evaluation["passed"] is True
    assert evaluation["passed_checks"] == evaluation["total_checks"] == 6


def test_parse_rejects_unexpected_output_keys() -> None:
    parsed, parse_error = _parse_and_sanitize_content(
        "image",
        json.dumps(
            {
                "time": "14:00",
                "amount_ml": 200,
                "follow_up_status": "pending",
                "privacy_note": "synthetic",
                "invented_person": "not allowed",
            }
        ),
    )

    assert parsed is None
    assert parse_error == "unexpected_json_keys"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("14:00", "14:00"),
        ("2 pm", "14:00"),
        ("2:00 P.M.", "14:00"),
        ("12 am", "00:00"),
        ("12 pm", "12:00"),
        ("25:00", None),
        ("afternoon", None),
    ],
)
def test_normalize_time_accepts_only_bounded_clock_expressions(
    value: str,
    expected: str | None,
) -> None:
    assert _normalize_time(value) == expected


def test_evaluate_accepts_equivalent_12_and_24_hour_time() -> None:
    parsed = {
        "time": "2 pm",
        "amount_ml": 200,
        "follow_up_status": "pending",
        "privacy_note": "synthetic",
    }

    evaluation = _evaluate_output("audio", parsed, _manifest()["expected"])

    assert evaluation["passed"] is True
    assert evaluation["checks"]["time"] is True


def test_load_safe_fixture_verifies_privacy_hash_and_size(tmp_path: Path) -> None:
    image = b"synthetic-image"
    audio = b"synthetic-audio"
    (tmp_path / "image.png").write_bytes(image)
    (tmp_path / "audio.wav").write_bytes(audio)
    manifest = _manifest()
    manifest["assets"] = {
        "image": {
            "file": "image.png",
            "media_type": "image/png",
            "sha256": hashlib.sha256(image).hexdigest(),
            "size_bytes": len(image),
        },
        "audio": {
            "file": "audio.wav",
            "media_type": "audio/wav",
            "sha256": hashlib.sha256(audio).hexdigest(),
            "size_bytes": len(audio),
        },
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    loaded_manifest, assets = _load_safe_fixture(tmp_path)

    assert loaded_manifest["privacy"]["synthetic"] is True
    assert assets == {"image": image, "audio": audio}


def test_load_safe_fixture_blocks_non_deidentified_manifest(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["privacy"]["contains_direct_identifiers"] = True
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ValueError, match="비식별"):
        _load_safe_fixture(tmp_path)
