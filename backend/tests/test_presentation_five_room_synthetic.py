from __future__ import annotations

from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path


def test_fixture_exposes_only_the_five_presentation_rooms_and_safe_service_scopes() -> None:
    from app.presentation_five_room_synthetic import build_fixture

    fixture = build_fixture()

    assert [room["name"] for room in fixture["rooms"]] == [
        "주간보호 현장케어",
        "실버메디컬 전체소통",
        "전문직 케어협업",
        "사무행정 지원실",
        "방문요양 현장지원",
    ]
    assert fixture["window"] == {
        "start": "2026-08-28T07:30:00+09:00",
        "end": "2026-09-10T18:30:00+09:00",
    }
    assert {resident["service_type"] for resident in fixture["residents"]} == {
        "daycare",
        "homecare",
    }
    assert sum(
        resident["service_type"] == "daycare" for resident in fixture["residents"]
    ) == 5
    assert sum(
        resident["service_type"] == "homecare" for resident in fixture["residents"]
    ) == 3
    assert fixture["safety_contract"] == {
        "synthetic_fixture": True,
        "official_record": False,
        "external_transfer_allowed": False,
        "contains_real_personal_data": False,
        "real_name_restoration_allowed": False,
        "automatic_signature_or_nhis_transfer": False,
    }
    assert fixture["display_notice"] == (
        "결선 시연용 합성자료 · 실제 인물 및 기록과 무관"
    )


def test_fixture_builds_a_two_week_cross_room_story_with_valid_replies() -> None:
    from app.presentation_five_room_synthetic import (
        build_fixture,
        public_manifest,
        validate_fixture,
    )

    fixture = build_fixture()
    validate_fixture(fixture)
    manifest = public_manifest(fixture)

    assert manifest["message_count"] == 336
    assert manifest["room_message_counts"] == {
        "all_staff": 42,
        "daycare_field": 112,
        "homecare_field": 56,
        "office_admin": 56,
        "professional_collab": 70,
    }
    assert manifest["scenario_count"] == 5
    assert manifest["service_type_counts"] == {
        "administrative": 56,
        "all": 42,
        "daycare": 182,
        "homecare": 56,
    }

    staff_codes = {item["code"] for item in fixture["staff"]}
    rooms = {item["key"]: item for item in fixture["rooms"]}
    residents = {item["code"]: item for item in fixture["residents"]}
    messages = {item["message_id"]: item for item in fixture["messages"]}
    assert len(messages) == 336
    assert Counter(item["room_key"] for item in messages.values()) == {
        "daycare_field": 112,
        "professional_collab": 70,
        "office_admin": 56,
        "homecare_field": 56,
        "all_staff": 42,
    }

    for message in messages.values():
        assert message["sender_code"] in staff_codes
        assert message["sender_code"] in rooms[message["room_key"]]["member_codes"]
        resident_code = message.get("resident_code")
        if message["room_key"] == "daycare_field":
            assert resident_code is not None
            assert residents[resident_code]["service_type"] == "daycare"
        if message["room_key"] == "homecare_field":
            assert resident_code is None or residents[resident_code]["service_type"] == "homecare"
        reply_id = message.get("reply_to_message_id")
        if reply_id:
            parent = messages[reply_id]
            assert parent["room_key"] == message["room_key"]
            assert datetime.fromisoformat(parent["created_at"]) < datetime.fromisoformat(
                message["created_at"]
            )

    for scenario in fixture["scenarios"]:
        scenario_messages = [messages[item] for item in scenario["message_ids"]]
        assert len(scenario_messages) >= 3
        assert scenario_messages == sorted(
            scenario_messages, key=lambda item: datetime.fromisoformat(item["created_at"])
        )
        assert all(
            item.get("scenario_id") == scenario["scenario_id"]
            for item in scenario_messages
        )


def test_fixture_links_fourteen_coded_synthetic_attachments_to_existing_messages(public_media_root, monkeypatch) -> None:
    from app import presentation_five_room_synthetic as module
    monkeypatch.setattr(module, "ASSET_SOURCE_DIR", public_media_root / "backend/tests/fixtures/finalist_coded_synthetic_v1")
    from app.presentation_five_room_synthetic import (
        ASSET_SOURCE_DIR,
        build_fixture,
        public_manifest,
        validate_fixture,
    )

    fixture = build_fixture()
    validate_fixture(fixture)
    manifest = public_manifest(fixture)
    messages = {item["message_id"]: item for item in fixture["messages"]}
    attachments = fixture["attachments"]

    assert len(attachments) == manifest["attachment_count"] == 14
    assert len({item["attachment_id"] for item in attachments}) == 14
    assert len({item["original_name"] for item in attachments}) == 14
    assert Counter(messages[item["message_id"]]["room_key"] for item in attachments) == {
        "daycare_field": 5,
        "professional_collab": 3,
        "all_staff": 2,
        "office_admin": 2,
        "homecare_field": 2,
    }
    assert all(item["original_name"].startswith("presentation-coded-") for item in attachments)
    assert all(item["synthetic_origin"] == "newly_authored_from_aggregate_patterns_only" for item in attachments)
    assert all(item["message_id"] in messages for item in attachments)
    for item in attachments:
        if item.get("source_asset_name"):
            assert (Path(ASSET_SOURCE_DIR) / item["source_asset_name"]).is_file()
            assert "inline_content" not in item
        else:
            assert item["inline_content"].strip()
    assert {item["mime_type"] for item in attachments} == {
        "audio/wav",
        "image/jpeg",
        "image/png",
        "text/plain",
    }


def test_export_is_byte_deterministic_and_records_every_asset_hash(tmp_path: Path, public_media_root, monkeypatch) -> None:
    from app import presentation_five_room_synthetic as module
    monkeypatch.setattr(module, "ASSET_SOURCE_DIR", public_media_root / "backend/tests/fixtures/finalist_coded_synthetic_v1")
    from app.presentation_five_room_synthetic import export_fixture

    first = export_fixture(tmp_path / "first")
    second = export_fixture(tmp_path / "second")

    assert first["fixture_path"].read_bytes() == second["fixture_path"].read_bytes()
    assert first["manifest_path"].read_bytes() == second["manifest_path"].read_bytes()

    fixture_bytes = first["fixture_path"].read_bytes()
    manifest = json.loads(first["manifest_path"].read_text(encoding="utf-8"))
    assert manifest["fixture_sha256"] == hashlib.sha256(fixture_bytes).hexdigest()
    assert len(manifest["assets"]) == 14

    for asset in manifest["assets"]:
        asset_path = first["output_dir"] / "assets" / asset["name"]
        content = asset_path.read_bytes()
        assert len(content) == asset["size_bytes"]
        assert hashlib.sha256(content).hexdigest() == asset["sha256"]

    exported = json.loads(fixture_bytes)
    for item in exported["attachments"]:
        if item.get("source_asset_name"):
            source = module.ASSET_SOURCE_DIR / item["source_asset_name"]
            assert (first["output_dir"] / "assets" / item["original_name"]).read_bytes() == source.read_bytes()
