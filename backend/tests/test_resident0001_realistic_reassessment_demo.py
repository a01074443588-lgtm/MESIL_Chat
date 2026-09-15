from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "resident0001_realistic_reassessment_v1"
)
EXPECTED_PDFS = {
    "previous_fall_assessment.pdf",
    "previous_pressure_ulcer_assessment.pdf",
    "previous_cognitive_assessment.pdf",
    "previous_needs_assessment.pdf",
    "previous_care_plan.pdf",
    "previous_care_plan_evaluation.pdf",
}


def _read_json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_resident0001_demo_has_form_like_source_documents() -> None:
    manifest = _read_json("manifest.json")
    specs = _read_json("document_specs.json")

    assert manifest["fixture_version"] == "resident0001_realistic_reassessment_v1"
    assert manifest["resident_code"] == "어르0001"
    assert manifest["flow"] == "periodic_reassessment"
    assert manifest["safety"] == {
        "synthetic_fixture": True,
        "contains_real_personal_data": False,
        "official_record": False,
        "external_transfer_allowed": False,
    }
    assert set(manifest["pdf_files"]) == EXPECTED_PDFS
    assert set(specs) == EXPECTED_PDFS

    for filename, spec in specs.items():
        pdf_path = FIXTURE_DIR / filename
        assert pdf_path.stat().st_size >= 20_000
        assert len(spec["sections"]) >= 3
        assert sum(len(section["rows"]) for section in spec["sections"]) >= 10
        assert spec["display_notice"] == "DEV 비공식 자료 · 코드화된 합성 시험자료"
        assert len(PdfReader(str(pdf_path)).pages) >= 1


def test_resident0001_demo_messages_form_a_three_month_multirole_story() -> None:
    messages = _read_json("messages.json")
    assert len(messages) >= 24
    assert len({item["event_id"] for item in messages}) == len(messages)
    assert {item["resident_code"] for item in messages} == {"어르0001"}
    assert len({item["sender_code"] for item in messages}) >= 3
    assert sum(len(item.get("comments", [])) for item in messages) >= 16

    timestamps = [datetime.fromisoformat(item["created_at"]) for item in messages]
    assert (max(timestamps) - min(timestamps)).days >= 90

    states = {item["state"] for item in messages}
    assert {"completed", "pending", "needs_review"} <= states
    categories = {item["category"] for item in messages}
    assert {
        "mobility",
        "nutrition",
        "hydration",
        "skin",
        "cognition",
        "guardian_consultation",
        "vital_conflict",
    } <= categories
    assert any(item.get("duplicate_of") for item in messages)
    assert all(item["synthetic_fixture"] is True for item in messages)
    assert all(item["official_record"] is False for item in messages)
    assert all(item["external_transfer_allowed"] is False for item in messages)


def test_resident0001_demo_evidence_and_changes_are_referentially_complete() -> None:
    manifest = _read_json("manifest.json")
    messages = _read_json("messages.json")
    evidence = _read_json("evidence_ledger.json")
    changes = _read_json("expected_changes.json")

    valid_refs = {item["source_ref"] for item in manifest["documents"]}
    valid_refs.update(f"message:{item['event_id']}" for item in messages)
    valid_refs.update(
        f"comment:{item['event_id']}:{index}"
        for item in messages
        for index, _ in enumerate(item.get("comments", []), start=1)
    )
    assert len({item["evidence_id"] for item in evidence}) == len(evidence)
    assert all(item["source_ref"] in valid_refs for item in evidence)
    assert all(item["resident_code"] == "어르0001" for item in evidence)
    assert all(item["external_transfer_allowed"] is False for item in evidence)
    assert all(item["official_record"] is False for item in evidence)

    evidence_ids = {item["evidence_id"] for item in evidence}
    allowed_classifications = {
        "이전과 동일",
        "변경됨",
        "새로 확인됨",
        "오래된 자료라 현재 관찰 필요",
        "자료 충돌",
        "전문가 확인 필요",
        "급여제공계획 변경 검토 후보",
    }
    assert all(item["classification"] in allowed_classifications for item in changes)
    assert all(set(item["evidence_refs"]) <= evidence_ids for item in changes)
    assert any(item["classification"] == "자료 충돌" for item in changes)
    assert any(item["classification"] == "급여제공계획 변경 검토 후보" for item in changes)
    assert not any(item.get("generated_score") for item in changes)
    assert not any(item.get("generated_diagnosis") for item in changes)


def test_resident0001_demo_contains_no_obvious_personal_identifiers() -> None:
    combined = "\n".join(
        (FIXTURE_DIR / name).read_text(encoding="utf-8")
        for name in (
            "manifest.json",
            "document_specs.json",
            "messages.json",
            "evidence_ledger.json",
            "expected_changes.json",
        )
    )
    assert "가상갚" not in combined
    assert "가상갅" not in combined
    assert "이가상" not in combined
    assert "silvermedical.kr" not in combined
    assert "010-" not in combined
    assert "주민등록" not in combined
