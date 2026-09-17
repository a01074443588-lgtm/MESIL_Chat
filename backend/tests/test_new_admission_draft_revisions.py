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
    NewAdmissionDraftRevision,
    Organization,
    Resident,
    ResidentCarePlanningState,
)


ORIGIN = {"origin": "http://testserver"}
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m21_new_admission_contract_v1.json"
)


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
            internal_code=f"M21-JB-{uuid4().hex}",
            display_name="신규입소 개정검증(가명)",
            service_type="facility",
            is_test_data=True,
        )
        db.add(resident)
        db.commit()
        return str(resident.id)


def _fixture() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def _field(payload: dict, field_key: str) -> dict:
    return next(field for field in payload["fields"] if field["field_key"] == field_key)


def test_append_only_draft_revisions_preserve_evidence_changes_and_confirmer():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        revision_one = _fixture()

        created = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts",
            headers=ORIGIN,
            json={
                "bundle": revision_one,
                "confirmed_field_keys": [
                    "long_term_care_grade",
                    "hydration_support_goal",
                ],
            },
        )
        assert created.status_code == 201, created.text
        created_payload = created.json()
        draft_id = created_payload["id"]
        assert created_payload["current_revision"] == 1
        assert created_payload["status"] == "needs_confirmation"
        assert created_payload["external_transfer_allowed"] is False
        assert len(created_payload["revisions"]) == 1
        first = created_payload["revisions"][0]
        assert first["bundle"]["revision"] == 1
        assert first["field_confirmations"]["long_term_care_grade"]["state"] == (
            "confirmed"
        )
        assert first["field_confirmations"]["long_term_care_grade"][
            "confirmed_by_name"
        ] == "시험 관리자"
        assert first["field_evidence_refs"]["hydration_support_goal"] == [
            "consultation-syn-001"
        ]

        revision_two = deepcopy(revision_one)
        revision_two["revision"] = 2
        revision_two["supersedes_revision"] = 1
        revision_two["assessment_date"] = "2026-08-31"
        revision_two["comparison_items"] = [
            {
                "field_key": "mobility_support_need",
                "label": "이동 도움",
                "classification": "changed",
                "previous_value": "독립 보행",
                "current_value": "부축 필요",
                "baseline_evidence_refs": ["staff-confirmation-syn-001"],
                "current_evidence_refs": ["current-observation-syn-001"],
                "conflict_values": [],
                "reason": "이동 지원 방법 검토",
            }
        ]
        revision_two["selected_change_field_keys"] = ["mobility_support_need"]
        _field(revision_two, "hydration_support_goal")["value"] = (
            "매 식사 사이 수분 섭취 확인 요청"
        )
        updated = client.post(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft_id}/revisions"
            ),
            headers=ORIGIN,
            json={
                "bundle": revision_two,
                "confirmed_field_keys": ["hydration_support_goal"],
                "reviewed_document_types": [
                    "fall_risk_assessment",
                    "pressure_ulcer_risk_assessment",
                    "cognitive_function_assessment",
                    "needs_assessment",
                ],
            },
        )
        assert updated.status_code == 201, updated.text
        updated_payload = updated.json()
        assert updated_payload["current_revision"] == 2
        assert len(updated_payload["revisions"]) == 2
        preserved_first, second = updated_payload["revisions"]
        assert _field(preserved_first["bundle"], "hydration_support_goal")["value"] == (
            "수분 섭취를 자주 확인해 달라는 요청"
        )
        assert _field(second["bundle"], "hydration_support_goal")["value"] == (
            "매 식사 사이 수분 섭취 확인 요청"
        )
        assert second["supersedes_revision"] == 1
        assert second["bundle"]["selected_change_field_keys"] == [
            "mobility_support_need"
        ]
        assert second["field_changes"] == [
            {
                "field_key": "hydration_support_goal",
                "before_value": "수분 섭취를 자주 확인해 달라는 요청",
                "after_value": "매 식사 사이 수분 섭취 확인 요청",
                "before_status": "reusable",
                "after_status": "reusable",
                "before_evidence_refs": ["consultation-syn-001"],
                "after_evidence_refs": ["consultation-syn-001"],
            }
        ]
        assert second["field_confirmations"]["long_term_care_grade"] == first[
            "field_confirmations"
        ]["long_term_care_grade"]
        assert second["field_confirmations"]["hydration_support_goal"]["state"] == (
            "confirmed"
        )

        reloaded = client.get(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft_id}"
            )
        )
        assert reloaded.status_code == 200, reloaded.text
        assert reloaded.json() == updated_payload
        assert reloaded.json()["revisions"][1]["bundle"][
            "selected_change_field_keys"
        ] == ["mobility_support_need"]

        question_flow = client.get(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft_id}/questions"
            )
        )
        assert question_flow.status_code == 200, question_flow.text
        question_payload = question_flow.json()
        assert question_payload["draft_id"] == draft_id
        assert question_payload["revision"] == 2
        assert question_payload["reusable_fact_count"] == 4
        assert question_payload["question_count"] == 6
        assert question_payload["duplicate_question_count"] == 0
        assert question_payload["external_transfer_allowed"] is False
        plan_preview = question_payload["care_plan_preview"]
        assert plan_preview["output_status"] == "needs_confirmation"
        assert {
            item["field_key"] for item in plan_preview["reflected_plan_items"]
        } == {"long_term_care_grade", "hydration_support_goal"}
        assert plan_preview["official_record_write_count"] == 0
        assert plan_preview["signature_confirmation_count"] == 0
        assert plan_preview["public_insurer_transfer_count"] == 0
        assert plan_preview["revision_mutation_count"] == 0
        assert any(
            link["revision"] == 2
            for item in plan_preview["reflected_plan_items"]
            if item["field_key"] == "hydration_support_goal"
            for link in item["revision_links"]
        )
        assert {
            item["field_key"] for item in question_payload["questions"]
        } == {
            "skin_moisture_current",
            "cognitive_assessment_decision",
            "admission_room",
            "recent_fall_count",
            "service_frequency",
            "staff_signature",
        }

        stale = client.post(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft_id}/revisions"
            ),
            headers=ORIGIN,
            json={"bundle": revision_two, "confirmed_field_keys": []},
        )
        assert stale.status_code == 409

        invalid_revision = deepcopy(revision_two)
        invalid_revision["revision"] = 3
        invalid_revision["supersedes_revision"] = 2
        unresolved_confirmation = client.post(
            (
                f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
                f"{draft_id}/revisions"
            ),
            headers=ORIGIN,
            json={
                "bundle": invalid_revision,
                "confirmed_field_keys": ["recent_fall_count"],
            },
        )
        assert unresolved_confirmation.status_code == 422

        listed = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts"
        )
        assert listed.status_code == 200, listed.text
        listed_summary = listed.json()["items"][0]
        assert listed_summary["current_revision"] == 2
        assert listed_summary["assessment_date"] == "2026-08-31"
        assert listed_summary["reviewed_assessment_count"] == 4
        assert listed_summary["reason"] == "new_admission"

        with SessionLocal() as db:
            assert db.scalar(
                select(func.count(NewAdmissionDraftRecord.id)).where(
                    NewAdmissionDraftRecord.resident_id == resident_id
                )
            ) == 1
            assert db.scalar(
                select(func.count(NewAdmissionDraftRevision.id)).join(
                    NewAdmissionDraftRecord,
                    NewAdmissionDraftRecord.id == NewAdmissionDraftRevision.draft_id,
                ).where(NewAdmissionDraftRecord.resident_id == resident_id)
            ) == 2
            assert db.scalar(
                select(func.count(ResidentCarePlanningState.id)).where(
                    ResidentCarePlanningState.resident_id == resident_id
                )
            ) == 0

        assert "official" not in json.dumps(updated_payload)
        assert "signature_confirmed" not in json.dumps(updated_payload)


def test_deidentified_draft_rejects_non_test_resident_and_duplicate_case():
    with TestClient(app) as client:
        _login_admin(client)
        fixture = _fixture()
        with SessionLocal() as db:
            organization = db.scalar(select(Organization).limit(1))
            assert organization is not None
            resident = Resident(
                organization_id=organization.id,
                internal_code=f"M21-JB-REAL-{uuid4().hex}",
                display_name="일반 어르신 경계검증",
                service_type="facility",
                is_test_data=False,
            )
            db.add(resident)
            db.commit()
            resident_id = str(resident.id)
        blocked = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts",
            headers=ORIGIN,
            json={"bundle": fixture, "confirmed_field_keys": []},
        )
        assert blocked.status_code == 422
