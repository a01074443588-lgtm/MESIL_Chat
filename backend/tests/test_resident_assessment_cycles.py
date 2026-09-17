import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import (
    NewAdmissionDraftRecord,
    Organization,
    Resident,
    ResidentAssessmentCycle,
    ResidentAssessmentCycleRevision,
    ResidentCarePlanningState,
)
from app.resident_assessment_cycles import (
    ResidentAssessmentCyclePayload,
    build_assessment_cycle_comparison,
)


ORIGIN = {"origin": "http://testserver"}
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m21_k_existing_resident_cycle_v1.json"
)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _create_test_resident() -> str:
    with SessionLocal() as db:
        organization = db.scalar(select(Organization).limit(1))
        assert organization is not None
        resident = Resident(
            organization_id=organization.id,
            internal_code=f"M21-K-{uuid4().hex}",
            display_name="재평가 변화검증(가명)",
            service_type="facility",
            is_test_data=True,
        )
        db.add(resident)
        db.commit()
        return str(resident.id)


def test_comparison_covers_all_safe_change_states_without_duplicate_questions():
    payload = ResidentAssessmentCyclePayload.model_validate(_fixture())
    result = build_assessment_cycle_comparison(payload)
    by_key = {item["field_key"]: item for item in result["comparison_items"]}

    assert by_key["hydration_support"]["classification"] == "unchanged"
    assert by_key["sleep_status"]["classification"] == "changed"
    assert by_key["skin_redness"]["classification"] == "newly_confirmed"
    assert by_key["orientation_status"]["classification"] == (
        "stale_current_observation_required"
    )
    assert by_key["blood_pressure"]["classification"] == "material_conflict"
    assert by_key["blood_pressure"]["current_value"] is None
    assert by_key["fall_assessment_score"]["classification"] == (
        "basis_version_mismatch"
    )
    assert by_key["fall_assessment_score"]["current_value"] is None
    assert by_key["swallowing_review"]["classification"] == (
        "expert_review_required"
    )
    assert by_key["mobility_support"]["classification"] == (
        "care_plan_change_candidate"
    )
    assert by_key["mobility_support"]["staff_selection_allowed"] is True
    assert by_key["mobility_support"]["selected_for_plan_review"] is False
    assert result["duplicate_question_count"] == 0
    assert result["external_transfer_allowed"] is False
    assert result["official_record_write_count"] == 0
    assert result["signature_confirmation_count"] == 0
    assert result["public_insurer_transfer_count"] == 0
    assert result["auto_generated_restricted_value_count"] == 0


def test_cycle_and_revisions_are_append_only_and_previous_cycle_is_preserved():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        first_payload = _fixture()

        created = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles",
            headers=ORIGIN,
            json={"payload": first_payload},
        )
        assert created.status_code == 201, created.text
        first_cycle = created.json()
        cycle_id = first_cycle["id"]
        assert first_cycle["cycle_number"] == 1
        assert first_cycle["current_revision"] == 1
        assert first_cycle["reason"] == "periodic_reassessment"
        assert first_cycle["source_provider"] == "staff_manual"
        assert first_cycle["external_transfer_allowed"] is False
        assert len(first_cycle["revisions"]) == 1

        second_revision = deepcopy(first_payload)
        second_revision["revision"] = 2
        second_revision["supersedes_revision"] = 1
        second_revision["selected_plan_candidate_keys"] = ["mobility_support"]
        revised = client.post(
            (
                f"/api/workdesk/residents/{resident_id}/assessment-cycles/"
                f"{cycle_id}/revisions"
            ),
            headers=ORIGIN,
            json={"payload": second_revision},
        )
        assert revised.status_code == 201, revised.text
        revised_payload = revised.json()
        assert revised_payload["current_revision"] == 2
        assert len(revised_payload["revisions"]) == 2
        first_revision, latest_revision = revised_payload["revisions"]
        assert first_revision["selected_plan_candidate_keys"] == []
        assert latest_revision["selected_plan_candidate_keys"] == [
            "mobility_support"
        ]
        assert any(
            change["field_key"] == "mobility_support"
            and change["before_selected"] is False
            and change["after_selected"] is True
            for change in latest_revision["field_changes"]
        )

        state_change_payload = deepcopy(first_payload)
        state_change_payload["reason"] = "state_change"
        state_change_payload["previous_cycle_id"] = cycle_id
        created_state_change = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles",
            headers=ORIGIN,
            json={"payload": state_change_payload},
        )
        assert created_state_change.status_code == 201, created_state_change.text
        second_cycle = created_state_change.json()
        assert second_cycle["cycle_number"] == 2
        assert second_cycle["previous_cycle_id"] == cycle_id
        assert second_cycle["reason"] == "state_change"

        reloaded = client.get(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles/{cycle_id}"
        )
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json() == revised_payload

        listed = client.get(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles"
        )
        assert listed.status_code == 200, listed.text
        listed_payload = listed.json()
        assert [item["cycle_number"] for item in listed_payload["items"]] == [2, 1]
        assert "직원이 원본을 확인" in listed_payload["source_provider_notice"]
        assert "센터 운영 원칙" in listed_payload["operating_cycle_notice"]

        with SessionLocal() as db:
            assert db.scalar(
                select(func.count(ResidentAssessmentCycle.id)).where(
                    ResidentAssessmentCycle.resident_id == resident_id
                )
            ) == 2
            assert db.scalar(
                select(func.count(ResidentAssessmentCycleRevision.id))
                .join(
                    ResidentAssessmentCycle,
                    ResidentAssessmentCycle.id
                    == ResidentAssessmentCycleRevision.cycle_id,
                )
                .where(ResidentAssessmentCycle.resident_id == resident_id)
            ) == 3
            assert db.scalar(
                select(func.count(ResidentCarePlanningState.id)).where(
                    ResidentCarePlanningState.resident_id == resident_id
                )
            ) == 0
            assert db.scalar(
                select(func.count(NewAdmissionDraftRecord.id)).where(
                    NewAdmissionDraftRecord.resident_id == resident_id
                )
            ) == 0


def test_unconfirmed_candidate_and_future_provider_are_blocked():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        invalid_selection = _fixture()
        invalid_selection["selected_plan_candidate_keys"] = ["blood_pressure"]
        blocked_selection = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles",
            headers=ORIGIN,
            json={"payload": invalid_selection},
        )
        assert blocked_selection.status_code == 422
        assert "확인한 급여제공계획 변경 후보" in blocked_selection.json()["detail"]

        future_provider = _fixture()
        future_provider["source_provider"] = "carefor_readonly"
        blocked_provider = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-cycles",
            headers=ORIGIN,
            json={"payload": future_provider},
        )
        assert blocked_provider.status_code == 422
        assert "직원 직접입력 공급자만" in blocked_provider.json()["detail"]
