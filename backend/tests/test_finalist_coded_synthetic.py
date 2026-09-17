from __future__ import annotations

import pytest
from fastapi import HTTPException

from app import services
from app.finalist_coded_synthetic import (
    DISPLAY_NOTICE,
    GUARDIAN_CODE_RE,
    RESIDENT_CODE_RE,
    STAFF_CODE_RE,
    SOURCE_SHAPED_ROOM_KEYS,
    build_fixture,
    public_manifest,
)


def test_codes_are_unique_stable_and_never_embed_identity_details() -> None:
    manifest = public_manifest(build_fixture())
    assert len(manifest["staff_codes"]) == len(set(manifest["staff_codes"]))
    assert len(manifest["resident_codes"]) == len(set(manifest["resident_codes"]))
    assert len(manifest["guardian_codes"]) == len(set(manifest["guardian_codes"]))
    assert all(STAFF_CODE_RE.fullmatch(code) for code in manifest["staff_codes"])
    assert all(RESIDENT_CODE_RE.fullmatch(code) for code in manifest["resident_codes"])
    assert all(GUARDIAN_CODE_RE.fullmatch(code) for code in manifest["guardian_codes"])
    assert manifest["code_rules"]["contains_birthdate_floor_room_or_real_name"] is False
    assert manifest["code_rules"]["code_reuse_allowed"] is False
    assert manifest["code_rules"]["real_name_restoration_allowed"] is False


def test_fixture_is_longitudinal_multirole_and_fully_synthetic() -> None:
    fixture = build_fixture()
    manifest = public_manifest(fixture)
    assert manifest["message_count"] >= 100
    assert manifest["comment_count"] >= 10
    assert len(manifest["event_categories"]) >= 12
    assert manifest["attachment_count"] == 7
    assert manifest["source_shaped_room_count"] == 5
    assert manifest["source_shaped_message_count"] == 240
    assert fixture["safety_contract"] == {
        "synthetic_fixture": True,
        "official_record": False,
        "external_transfer_allowed": False,
        "contains_real_personal_data": False,
        "real_name_restoration_allowed": False,
        "display_notice": DISPLAY_NOTICE,
    }
    required = {
        "식사량과 수분 섭취",
        "바이탈",
        "충돌 수치",
        "배변·배뇨",
        "수면",
        "감정 변화·프로그램",
        "이동·부축",
        "낙상·병원·보호자 연락",
        "피부 상태",
        "투약 전달",
        "보호자 상담",
    }
    assert required.issubset(set(manifest["event_categories"]))


def test_five_source_profiles_create_five_independent_fully_synthetic_rooms() -> None:
    fixture = build_fixture()
    source_rooms = [item for item in fixture["rooms"] if item.get("source_profile_id")]
    assert len(source_rooms) == len(SOURCE_SHAPED_ROOM_KEYS) == 5
    assert len({item["key"] for item in source_rooms}) == 5
    assert len({item["name"] for item in source_rooms}) == 5
    for room in source_rooms:
        messages = [item for item in fixture["messages"] if item["room_key"] == room["key"]]
        assert len(messages) == 48
        assert all(item["source_profile_id"] == room["source_profile_id"] for item in messages)
        assert all(item["synthetic_origin"] == "newly_authored_from_structure_only" for item in messages)
        assert sum(bool(item["comments"]) for item in messages) >= 9


def test_each_source_shaped_room_has_one_code_named_handwriting_attachment() -> None:
    fixture = build_fixture()
    messages_by_event = {item["event_id"]: item for item in fixture["messages"]}
    source_images = [
        item
        for item in fixture["attachments"]
        if item["case_id"].startswith("coded_handwriting_source_")
    ]
    assert len(source_images) == 5
    assert len({item["case_id"] for item in source_images}) == 5
    assert len({messages_by_event[item["event_id"]]["room_key"] for item in source_images}) == 5
    assert all(item["original_name"].startswith("coded-handwriting-source-") for item in source_images)


def test_every_identity_reference_uses_a_known_code() -> None:
    fixture = build_fixture()
    staff_codes = {item["code"] for item in fixture["staff"]}
    resident_codes = {item["code"] for item in fixture["residents"]}
    for message in fixture["messages"]:
        assert message["sender_code"] in staff_codes
        assert message["resident_code"] in resident_codes
        assert all(comment["author_code"] in staff_codes for comment in message["comments"])
        action = message.get("action")
        if action and action.get("assignee_code"):
            assert action["assignee_code"] in staff_codes


def test_finalist_mode_accepts_only_matching_staff_codes(monkeypatch) -> None:
    monkeypatch.setattr(services.settings, "environment", "development")
    monkeypatch.setattr(services.settings, "database_schema", "smcodi_finalist")
    assert services._finalist_coded_staff_identity({"full_name": "요보03"}) == {
        "full_name": "요보03",
        "employee_code": "요보03",
    }
    with pytest.raises(HTTPException) as real_name_error:
        services._finalist_coded_staff_identity({"full_name": "실명 입력"})
    assert real_name_error.value.status_code == 422
    with pytest.raises(HTTPException) as mismatch_error:
        services._finalist_coded_staff_identity(
            {"full_name": "요보03", "employee_code": "요보04"}
        )
    assert mismatch_error.value.status_code == 422
