import hashlib
import json
from pathlib import Path


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "longitudinal_care_scenario_v1"


def _load(name: str):
    return json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_longitudinal_fixture_is_deidentified_and_deterministic():
    fixture = _load("messages.json")
    manifest = _load("manifest.json")
    messages = fixture["messages"]

    assert len(messages) == manifest["message_count"] == 200
    assert manifest["event_count"] >= 25
    assert manifest["role_count"] == 9
    assert fixture["resident_alias"] == "검증어르신(가명)"
    assert manifest["contains_real_personal_data"] is False
    assert all(message["synthetic_fixture"] is True for message in messages)
    assert all(message["official_record"] is False for message in messages)
    assert all(message["external_transfer_allowed"] is False for message in messages)
    assert all(message["display_notice"] == "검증용 가상자료" for message in messages)
    assert all("scenario_event_id" in message for message in messages)
    assert all("fixture_message_id" in message for message in messages)


def test_ground_truth_is_separate_and_not_model_input():
    fixture = _load("messages.json")
    truth = _load("ground_truth.json")
    manifest = _load("manifest.json")
    canonical = (json.dumps(fixture, ensure_ascii=False, sort_keys=True) + "\n").encode()

    assert hashlib.sha256(canonical).hexdigest() == manifest["fixture_sha256"]
    assert truth["input_fixture_sha256"] == manifest["fixture_sha256"]
    assert truth["prohibited_as_model_input"] is True
    assert "events" not in fixture
    assert "expected_final_status" not in json.dumps(fixture, ensure_ascii=False)
    assert set(truth["focus_event_ids"]).issubset({event["event_id"] for event in truth["events"]})


def test_required_longitudinal_edge_cases_are_present():
    truth = _load("ground_truth.json")
    by_id = {event["event_id"]: event for event in truth["events"]}

    assert by_id["EVT-HOSPITAL-001"]["expected_final_status"] == "completed"
    assert by_id["EVT-HYDRATION-001"]["expected_final_status"] == "completed"
    assert by_id["EVT-BOWEL-001"]["expected_final_status"] == "completed"
    assert by_id["EVT-BP-CONFLICT-001"]["expected_final_status"] == "needs_review"
    assert by_id["EVT-MOBILITY-CHANGE-001"]["document_candidates"] == [
        "care_service_record",
        "fall_risk_assessment",
        "care_plan",
    ]
    assert "consultation_log" in by_id["EVT-CONSULT-001"]["document_candidates"]
    assert "consultation_log" in by_id["EVT-NONCONSULT-PHONE-001"]["forbidden_document_candidates"]
