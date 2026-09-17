import csv
import json
from collections import Counter
from pathlib import Path


FIXTURE_DIR = (
    Path(__file__).parent
    / "fixtures"
    / "finalist_five_care_planning_v1"
)
REPO_ROOT = FIXTURE_DIR.parents[3]


def load_json(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_finalist_five_care_planning_fixture_contract(public_media_root):
    required_files = {
        "manifest.json",
        "people.json",
        "rooms.json",
        "baseline_documents.json",
        "messages.json",
        "evidence_ledger.json",
        "expected_changes.json",
        "ground_truth.json",
        "ground_truth_table.csv",
    }
    assert required_files <= {path.name for path in FIXTURE_DIR.iterdir()}

    manifest = load_json("manifest.json")
    people = load_json("people.json")
    rooms = load_json("rooms.json")
    baseline_documents = load_json("baseline_documents.json")
    messages = load_json("messages.json")
    evidence_ledger = load_json("evidence_ledger.json")
    expected_changes = load_json("expected_changes.json")
    ground_truth = load_json("ground_truth.json")

    assert manifest["schema_version"] == "finalist_five_care_planning_manifest_v1"
    assert manifest["resident_count"] == 5
    assert manifest["message_count"] == 400
    assert manifest["attachment_count"] >= 3
    assert manifest["duplicate_report_count"] >= 1
    assert manifest["room_count"] == 25
    assert manifest["baseline_document_count"] == 25
    assert manifest["period"]["months"] == 6
    assert manifest["safety_contract"] == {
        "synthetic_fixture": True,
        "contains_real_personal_data": False,
        "official_record": False,
        "external_transfer_allowed": False,
    }

    residents = people["residents"]
    resident_codes = {resident["resident_code"] for resident in residents}
    assert len(residents) == len(resident_codes) == 5
    assert all(code.startswith("어르9") for code in resident_codes)
    assert all(resident["synthetic_fixture"] for resident in residents)

    room_codes = {room["room_code"] for room in rooms["rooms"]}
    assert len(room_codes) == 25
    assert Counter(room["resident_code"] for room in rooms["rooms"]) == Counter(
        {code: 5 for code in resident_codes}
    )

    documents = baseline_documents["documents"]
    assert len(documents) == 25
    assert Counter(document["resident_code"] for document in documents) == Counter(
        {code: 5 for code in resident_codes}
    )
    assert {
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
        "long_term_care_service_plan",
    } == {document["document_type"] for document in documents}
    assert all(document["official_record"] is False for document in documents)
    assert all(document["external_transfer_allowed"] is False for document in documents)
    assert all(document["score"] is None for document in documents)
    assert all(document["risk_level"] is None for document in documents)
    assert all(document["criteria_status"] == "criteria_unverified" for document in documents)

    message_rows = messages["messages"]
    assert len(message_rows) == 400
    assert len({row["message_id"] for row in message_rows}) == 400
    assert Counter(row["resident_code"] for row in message_rows) == Counter(
        {code: 80 for code in resident_codes}
    )
    assert all(row["room_code"] in room_codes for row in message_rows)
    assert all(row["synthetic_fixture"] is True for row in message_rows)
    assert all(row["official_record"] is False for row in message_rows)
    assert all(row["external_transfer_allowed"] is False for row in message_rows)
    assert not any("ground_truth" in row or "expected_change" in row for row in message_rows)

    attachments = [
        attachment
        for row in message_rows
        for attachment in row.get("attachments", [])
    ]
    assert len(attachments) >= 3
    assert {attachment["kind"] for attachment in attachments} >= {
        "image",
        "audio",
        "document",
    }
    assert all((public_media_root / attachment["fixture_path"]).is_file() for attachment in attachments)
    assert all(attachment["synthetic_fixture"] is True for attachment in attachments)
    assert all(attachment["official_record"] is False for attachment in attachments)
    assert all(attachment["external_transfer_allowed"] is False for attachment in attachments)

    duplicate_rows = [row for row in message_rows if row.get("duplicate_of_message_id")]
    assert duplicate_rows
    assert all(row["duplicate_of_message_id"] in {item["message_id"] for item in message_rows} for row in duplicate_rows)

    evidence_rows = evidence_ledger["entries"]
    evidence_ids = {row["evidence_id"] for row in evidence_rows}
    message_ids = {row["message_id"] for row in message_rows}
    required_ledger_fields = {
        "resident_id",
        "occurred_at",
        "author_role",
        "source_kind",
        "source_id",
        "category",
        "atomic_fact",
        "quantity",
        "unit",
        "observation",
        "action",
        "follow_up",
        "completion_state",
        "baseline_relation",
        "evidence_refs",
        "staff_confirmation",
        "revision",
    }
    assert len(evidence_ids) == len(evidence_rows)
    assert all(required_ledger_fields <= row.keys() for row in evidence_rows)
    assert all(row["resident_id"] == row["resident_code"] for row in evidence_rows)
    assert all(row["source_id"] and row["occurred_at"] for row in evidence_rows)
    assert all(row["atomic_fact"] for row in evidence_rows)
    assert all(row["evidence_refs"] == row["message_refs"] for row in evidence_rows)
    assert all(set(row["message_refs"]) <= message_ids for row in evidence_rows)
    assert not ({row["message_id"] for row in duplicate_rows} & {
        message_id
        for row in evidence_rows
        for message_id in row["message_refs"]
    })
    assert all(row["status"] in {
        "confirmed",
        "reusable",
        "derivable",
        "current_observation_required",
        "user_decision_required",
        "material_conflict",
        "admin_missing",
        "expert_review_required",
        "criteria_unverified",
        "excluded",
    } for row in evidence_rows)
    conflict_rows = [row for row in evidence_rows if row["status"] == "material_conflict"]
    assert conflict_rows
    assert all(row["completion_state"] == "needs_review" for row in conflict_rows)
    assert all(row["staff_confirmation"] == "required" for row in conflict_rows)
    resolved_rows = [row for row in evidence_rows if row["completion_state"] == "resolved"]
    assert resolved_rows
    assert any(row["follow_up"] for row in resolved_rows)

    changes = expected_changes["residents"]
    assert {row["resident_code"] for row in changes} == resident_codes
    assert all(row["assessment_order"] == [
        "fall_risk_assessment",
        "pressure_ulcer_risk_assessment",
        "cognitive_function_assessment",
        "needs_assessment",
        "long_term_care_service_plan",
    ] for row in changes)
    assert any(row["scenario_key"] == "stable_no_change" for row in changes)
    assert any(row["scenario_key"] == "mobility_persistent_change" for row in changes)
    assert any(row["scenario_key"] == "nutrition_skin_followup" for row in changes)
    assert any(row["scenario_key"] == "cognition_mood_observation" for row in changes)
    assert any(row["scenario_key"] == "hospital_medication_conflict" for row in changes)

    assert ground_truth["excluded_from_model_input"] is True
    assert ground_truth["staff_confirmation_required"] is True
    assert ground_truth["auto_generated_scores_allowed"] is False
    assert ground_truth["auto_generated_diagnoses_allowed"] is False
    assert ground_truth["auto_generated_service_frequency_allowed"] is False

    with (FIXTURE_DIR / "ground_truth_table.csv").open(
        encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert rows
    assert {row["resident_code"] for row in rows} == resident_codes
