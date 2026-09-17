from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest


def _runtime(**overrides):
    values = {
        "environment": "presentation",
        "postgres_db": "mesil_presentation",
        "database_schema": "mesil_presentation",
        "session_cookie_name": "mesil_presentation_20260911_session",
        "upload_dir": "/data/presentation-candidate-20260911/uploads",
        "origin_list": ["http://127.0.0.1:18130"],
        "stt_enabled": False,
        "ai_review_external_enabled": False,
        "ai_assist_allow_external_real_image_data": False,
        "web_push_enabled": False,
        "voice_call_enabled": False,
        "self_chat_enabled": False,
        "firebase_project_id": None,
        "nvidia_api_key": None,
        "openai_api_key": None,
        "gemini_api_key": None,
        "anthropic_api_key": None,
        "ollama_cloud_api_key": None,
        "openai_compatible_api_key": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_runtime_contract_accepts_only_the_presentation_namespace() -> None:
    from scripts.seed_presentation_five_room_candidate import validate_runtime_contract

    validate_runtime_contract(_runtime())

    invalid_cases = (
        {"environment": "development"},
        {"postgres_db": "smcodi_chat"},
        {"database_schema": "smcodi"},
        {"session_cookie_name": "smcodi_chat_session"},
        {"upload_dir": "/data/uploads"},
        {"origin_list": ["https://talk.silvermedical.kr"]},
        {"stt_enabled": True},
        {"ai_review_external_enabled": True},
        {"web_push_enabled": True},
        {"voice_call_enabled": True},
        {"self_chat_enabled": True},
        {"openai_api_key": object()},
    )
    for overrides in invalid_cases:
        with pytest.raises(RuntimeError):
            validate_runtime_contract(_runtime(**overrides))


def test_fixture_contract_requires_exact_presentation_counts_and_safety_flags() -> None:
    from app.presentation_five_room_synthetic import build_fixture
    from scripts.seed_presentation_five_room_candidate import validate_seed_fixture

    fixture = build_fixture()
    plan = validate_seed_fixture(fixture)

    assert plan == {
        "fixture_version": "presentation_five_room_synthetic_v1",
        "staff_count": 12,
        "resident_count": 8,
        "room_count": 5,
        "message_count": 336,
        "attachment_count": 14,
        "scenario_count": 5,
        "external_transfer_count": 0,
        "official_record_write_count": 0,
        "contains_real_personal_data": False,
    }

    unsafe = deepcopy(fixture)
    unsafe["safety_contract"]["external_transfer_allowed"] = True
    with pytest.raises(ValueError):
        validate_seed_fixture(unsafe)


def test_fixture_asset_paths_cannot_escape_the_fixture_directory(tmp_path: Path) -> None:
    from scripts.seed_presentation_five_room_candidate import resolve_fixture_asset

    fixture_dir = tmp_path / "fixture"
    fixture_dir.mkdir()
    asset = fixture_dir / "safe.txt"
    asset.write_text("synthetic", encoding="utf-8")

    assert resolve_fixture_asset(fixture_dir, "safe.txt") == asset.resolve()
    with pytest.raises(RuntimeError):
        resolve_fixture_asset(fixture_dir, "../outside.txt")


def test_fixture_office_job_code_maps_to_the_current_application_catalog() -> None:
    from scripts.seed_presentation_five_room_candidate import application_job_code

    assert application_job_code("administrative_staff") == "office_worker"
    assert application_job_code("social_worker") == "social_worker"


def test_presentation_work_item_snapshot_satisfies_current_api_contract() -> None:
    from app.schemas import WorkItemSourceSnapshot
    from scripts.seed_presentation_five_room_candidate import (
        presentation_work_item_source_snapshot,
    )

    message = SimpleNamespace(
        id=uuid4(),
        room_id=uuid4(),
        sender_id=uuid4(),
        resident_id=uuid4(),
        body="합성 기록 후보 원문",
        message_type="chat",
        created_at=datetime(2026, 9, 10, 3, 4, 5, tzinfo=timezone.utc),
        extra_data={"fixture_message_id": "PRES-TEST-001"},
    )
    attachment_ids = [uuid4(), uuid4()]

    snapshot = presentation_work_item_source_snapshot(
        message,
        room_name="주간보호",
        sender_name="관리자01",
        resident_name="가상어르신01",
        attachment_ids=attachment_ids,
        scenario_id="SCN-TEST",
    )

    validated = WorkItemSourceSnapshot.model_validate(snapshot)
    assert validated.message_id == message.id
    assert validated.room_id == message.room_id
    assert validated.sender_id == message.sender_id
    assert validated.resident_id == message.resident_id
    assert validated.resident_names == ["가상어르신01"]
    assert validated.attachment_ids == attachment_ids
    assert validated.created_at == message.created_at
