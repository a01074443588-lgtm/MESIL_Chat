from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader


FIXTURE_DIR = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "resident0002_to_0005_two_cycle_v1"
)
RESIDENT_CODES = {f"어르{number:04d}" for number in range(2, 6)}
NEW_ADMISSION_FILES = {
    "long_term_care_certificate.pdf",
    "individual_long_term_care_plan.pdf",
    "prescription.pdf",
    "pre_admission_health_check.pdf",
}
BASELINE_FILES = {
    "initial_fall_assessment.pdf",
    "initial_pressure_ulcer_assessment.pdf",
    "initial_cognitive_assessment.pdf",
    "initial_needs_assessment.pdf",
    "initial_care_plan.pdf",
    "initial_care_plan_evaluation.pdf",
}
SAFETY = {
    "synthetic_fixture": True,
    "contains_real_personal_data": False,
    "official_record": False,
    "external_transfer_allowed": False,
}


def _read(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def test_each_resident_has_separate_admission_baseline_and_reassessment_inputs() -> None:
    manifest = _read(FIXTURE_DIR / "manifest.json")
    assert manifest["fixture_version"] == "resident0002_to_0005_two_cycle_v1"
    assert set(manifest["resident_codes"]) == RESIDENT_CODES
    assert manifest["safety"] == SAFETY

    for code in sorted(RESIDENT_CODES):
        resident_dir = FIXTURE_DIR / code
        admission_dir = resident_dir / "01_new_admission_sources"
        baseline_dir = resident_dir / "02_initial_staff_confirmed_baseline"
        assert {item.name for item in admission_dir.glob("*.pdf")} == NEW_ADMISSION_FILES
        assert {item.name for item in baseline_dir.glob("*.pdf")} == BASELINE_FILES

        cycle = _read(resident_dir / "cycle_manifest.json")
        assert cycle["resident_code"] == code
        assert cycle["flow"] == [
            "new_admission_sources",
            "initial_staff_confirmed_baseline",
            "post_baseline_chat",
            "reassessment_ground_truth",
        ]
        assert cycle["safety"] == SAFETY
        assert cycle["initial_baseline_status"] == "synthetic_staff_confirmed_reference"
        assert cycle["reassessment_output_status"] == "editing_draft_only"
        assert cycle["new_admission_source_count"] == 4
        assert cycle["initial_baseline_document_count"] == 6

        for path in (*admission_dir.glob("*.pdf"), *baseline_dir.glob("*.pdf")):
            assert path.stat().st_size >= 15_000
            assert len(PdfReader(str(path)).pages) >= 1


def test_each_resident_has_a_detailed_initial_care_plan_and_safe_assessments() -> None:
    for code in sorted(RESIDENT_CODES):
        resident_dir = FIXTURE_DIR / code
        facts = _read(resident_dir / "initial_confirmed_facts.json")
        rows = _read(resident_dir / "initial_care_plan_rows.json")

        assert facts["resident_code"] == code
        assert facts["source_cycle"] == "new_admission"
        assert facts["staff_confirmed"] is True
        assert facts["generated_score"] is False
        assert facts["generated_diagnosis"] is False
        assert len(facts["confirmed_facts"]) >= 12
        assert all(item["evidence_refs"] for item in facts["confirmed_facts"])

        assert len(rows) == 50
        assert len({row["row_key"] for row in rows}) == 50
        assert len({row["need_area"] for row in rows}) == 7
        assert all(row["resident_code"] == code for row in rows)
        assert all(row["official_record"] is False for row in rows)
        assert all(row["external_transfer_allowed"] is False for row in rows)
        assert all(row["frequency_status"] in {"confirmed", "staff_decision_required"} for row in rows)
        assert all(row["duration_status"] in {"confirmed", "staff_decision_required"} for row in rows)


def test_post_baseline_chat_and_reassessment_truth_are_linked_without_guessing() -> None:
    required_classifications = {
        "이전과 동일",
        "변경됨",
        "새로 확인됨",
        "오래된 자료라 현재 관찰 필요",
        "자료 충돌",
        "급여제공계획 변경 검토 후보",
    }
    for code in sorted(RESIDENT_CODES):
        resident_dir = FIXTURE_DIR / code
        cycle = _read(resident_dir / "cycle_manifest.json")
        messages = _read(resident_dir / "post_baseline_messages.json")
        evidence = _read(resident_dir / "reassessment_evidence_ledger.json")
        truth = _read(resident_dir / "reassessment_ground_truth.json")

        assert len(messages) >= 50
        assert {item["resident_code"] for item in messages} == {code}
        assert len({item["event_id"] for item in messages}) == len(messages)
        assert all(datetime.fromisoformat(item["created_at"]) > datetime.fromisoformat(cycle["baseline_reference_at"]) for item in messages)
        assert (datetime.fromisoformat(messages[-1]["created_at"]) - datetime.fromisoformat(messages[0]["created_at"])).days >= 90
        assert sum(len(item.get("comments", [])) for item in messages) >= 20

        evidence_ids = {item["evidence_id"] for item in evidence}
        source_refs = {f"message:{item['event_id']}" for item in messages}
        source_refs.update(
            f"comment:{item['event_id']}:{index}"
            for item in messages
            for index, _ in enumerate(item.get("comments", []), start=1)
        )
        assert all(item["resident_code"] == code for item in evidence)
        assert all(item["source_ref"] in source_refs or item["source_ref"].startswith("baseline:") for item in evidence)
        assert all(item["official_record"] is False for item in evidence)
        assert all(item["external_transfer_allowed"] is False for item in evidence)

        classifications = {item["classification"] for item in truth["changes"]}
        assert required_classifications <= classifications
        assert all(set(item["evidence_refs"]) <= evidence_ids for item in truth["changes"])
        assert all(item["generated_score"] is False for item in truth["changes"])
        assert all(item["generated_diagnosis"] is False for item in truth["changes"])
        assert truth["official_record_saved"] is False
        assert truth["external_transfer_count"] == 0


def test_fixture_contains_only_coded_synthetic_people() -> None:
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in FIXTURE_DIR.rglob("*.json")
    )
    assert "가상갚" not in combined
    assert "가상갅" not in combined
    assert "이가상" not in combined
    assert "010-" not in combined
    assert "silvermedical.kr" not in combined
    assert "synthetic_fixture" in combined
