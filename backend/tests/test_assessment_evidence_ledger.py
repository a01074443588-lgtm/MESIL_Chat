from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from app.assessment_file_intake import (
    build_assessment_draft_bundle,
    extract_pdf_material,
)
from app.database import SessionLocal
from app.main import app
from app.models import NewAdmissionDraftRecord, Organization, Resident


ORIGIN = {"origin": "http://testserver"}
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "m21_tdd_file_ledger_v1"
    / "new_admission_packet.pdf"
)
FIXTURE_TARGET_CODE = "어르0199"


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
            internal_code=f"LEDGER-{uuid4().hex}",
            # The synthetic PDF explicitly declares this target code.  Keep the
            # selected synthetic resident aligned so the test exercises ledger
            # persistence rather than the separate resident-mismatch guard.
            display_name=FIXTURE_TARGET_CODE,
            service_type="facility",
            is_test_data=True,
        )
        db.add(resident)
        db.commit()
        return str(resident.id)


def _field(bundle: dict, key: str) -> dict:
    return next(item for item in bundle["fields"] if item["field_key"] == key)


def test_actual_single_pdf_upload_persists_and_reopens_evidence_ledger():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with FIXTURE.open("rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["auto"],
                },
                files=[
                    (
                        "files",
                        (FIXTURE.name, handle, "application/pdf"),
                    )
                ],
            )

        assert created.status_code == 201, created.text
        draft = created.json()
        bundle = draft["revisions"][0]["bundle"]
        assert [item["document_kind"] for item in bundle["material_receipts"]] == [
            "장기요양인정서",
            "개인별장기요양이용계획서",
            "상담일지",
        ]
        mobility = _field(bundle, "mobility_support_need")
        assert mobility["status"] == "material_conflict"
        assert mobility["value"] is None
        assert set(mobility["conflict_values"]) == {
            "실내 독립보행",
            "보행 시 부축 필요",
        }
        assert _field(bundle, "pressure_ulcer_score")["value"] is None

        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft['id']}/evidence-ledger"
        )
        assert reopened.status_code == 200, reopened.text
        ledger = reopened.json()
        assert ledger["file_intake_complete"] is True
        assert ledger["resident_id"] == resident_id
        assert len(ledger["items"]) == 3
        assert {item["document_date"] for item in ledger["items"]} == {
            "2026-08-30"
        }
        assert {item["document_kind"] for item in ledger["items"]} == {
            "장기요양인정서",
            "개인별장기요양이용계획서",
            "상담일지",
        }
        assert all(item["reference_locator"].endswith("쪽") for item in ledger["items"])
        assert all(item["raw_extracted_text"].strip() for item in ledger["items"])
        assert all(item["external_transfer_allowed"] is False for item in ledger["items"])
        assert all(item["created_at"] and item["updated_at"] for item in ledger["items"])

        mobility_entries = [
            item
            for item in ledger["items"]
            if "mobility_support_need" in item["conflict_groups"]
        ]
        assert len(mobility_entries) == 2
        assert all(item["staff_review_required"] is True for item in mobility_entries)
        assert {
            fact["value"]
            for item in mobility_entries
            for fact in item["normalized_facts"]
            if fact["field_key"] == "mobility_support_need"
        } == {"실내 독립보행", "보행 시 부축 필요"}
        assert all(
            fact["status"] == "material_conflict"
            for item in mobility_entries
            for fact in item["normalized_facts"]
            if fact["field_key"] == "mobility_support_need"
        )

        with SessionLocal() as db:
            row_count = db.scalar(
                text(
                    "SELECT count(*) FROM assessment_evidence_ledger_entries "
                    "WHERE draft_id = :draft_id"
                ),
                {"draft_id": draft["id"]},
            )
            assert row_count == 3


def test_saved_evidence_summary_separates_confirmed_conflict_and_missing_without_guessing():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with FIXTURE.open("rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["auto"],
                },
                files=[("files", (FIXTURE.name, handle, "application/pdf"))],
            )
        assert created.status_code == 201, created.text
        draft_id = created.json()["id"]

        summary_response = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/evidence-summary"
        )
        assert summary_response.status_code == 200, summary_response.text
        summary = summary_response.json()

        assert summary["file_intake_complete"] is True
        assert summary["external_transfer_allowed"] is False
        assert summary["status"] == "needs_confirmation"
        assert summary["counts"] == {
            "confirmed_facts": len(summary["confirmed_facts"]),
            "conflicts": len(summary["conflicts"]),
            "missing_items": len(summary["missing_items"]),
        }
        assert summary["counts"]["confirmed_facts"] == 2
        assert summary["counts"]["conflicts"] == 1
        assert summary["counts"]["missing_items"] > 0

        confirmed_by_key = {
            item["field_key"]: item for item in summary["confirmed_facts"]
        }
        assert confirmed_by_key["long_term_care_grade"]["value"] == "3등급"
        assert confirmed_by_key["nutrition_state"]["value"] == "일반식 2/3 섭취"
        assert all(item["evidence_refs"] for item in summary["confirmed_facts"])

        assert len(summary["conflicts"]) == 1
        mobility = summary["conflicts"][0]
        assert mobility["field_key"] == "mobility_support_need"
        assert set(mobility["conflict_values"]) == {
            "실내 독립보행",
            "보행 시 부축 필요",
        }
        assert mobility["selected_value"] is None
        assert len(mobility["evidence_refs"]) == 2

        missing_by_key = {
            item["field_key"]: item for item in summary["missing_items"]
        }
        assert missing_by_key["pressure_ulcer_score"]["value"] is None
        assert missing_by_key["pressure_ulcer_score"]["reason"] == "current_observation_required"
        assert missing_by_key["staff_signature"]["value"] is None
        assert missing_by_key["staff_signature"]["reason"] == "staff_decision_required"
        assert all(item["evidence_refs"] == [] for item in summary["missing_items"])

        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/evidence-summary"
        )
        assert reopened.status_code == 200
        assert reopened.json() == summary


def test_evidence_summary_drives_five_forms_and_staff_revision_reopens_append_only():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        with FIXTURE.open("rb") as handle:
            created = client.post(
                f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
                headers=ORIGIN,
                data={
                    "reason": "new_admission",
                    "assessment_date": "2026-08-31",
                    "document_kinds": ["auto"],
                },
                files=[("files", (FIXTURE.name, handle, "application/pdf"))],
            )
        assert created.status_code == 201, created.text
        draft = created.json()
        draft_id = draft["id"]

        summary_response = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/evidence-summary"
        )
        flow_response = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/questions"
        )
        assert summary_response.status_code == 200, summary_response.text
        assert flow_response.status_code == 200, flow_response.text
        summary = summary_response.json()
        workspace = flow_response.json()["form_workspace"]

        assert len(workspace["documents"]) == 4
        assert workspace["care_plan_gate"]["available"] is False

        reviewed_bundle = deepcopy(draft["revisions"][0]["bundle"])
        reviewed_bundle["revision"] = 2
        reviewed_bundle["supersedes_revision"] = 1
        reviewed = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/revisions",
            headers=ORIGIN,
            json={
                "bundle": reviewed_bundle,
                "confirmed_field_keys": [],
                "reviewed_document_types": [
                    "fall_risk_assessment",
                    "pressure_ulcer_risk_assessment",
                    "cognitive_function_assessment",
                    "needs_assessment",
                ],
            },
        )
        assert reviewed.status_code == 201, reviewed.text
        reviewed_payload = reviewed.json()
        reviewed_flow = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/questions"
        )
        assert reviewed_flow.status_code == 200, reviewed_flow.text
        workspace = reviewed_flow.json()["form_workspace"]
        assert len(workspace["documents"]) == 5
        assert workspace["care_plan_gate"]["available"] is True
        assert {item["document_type"] for item in workspace["documents"]} == {
            "fall_risk_assessment",
            "pressure_ulcer_risk_assessment",
            "cognitive_function_assessment",
            "needs_assessment",
            "long_term_care_service_plan",
        }
        summary_items = {
            item["field_key"]: item
            for group in (
                summary["confirmed_facts"],
                summary["conflicts"],
                summary["missing_items"],
            )
            for item in group
        }
        form_items: dict[str, dict] = {}
        for document in workspace["documents"]:
            for item in document["items"]:
                form_items.setdefault(item["field_key"], item)
                assert item["document_types"] == summary_items[item["field_key"]][
                    "document_types"
                ]
                assert document["document_type"] in item["document_types"]
                assert item["evidence_refs"] == summary_items[item["field_key"]][
                    "evidence_refs"
                ]
        assert set(form_items) == set(summary_items)
        assert workspace["analysis_summary"]["source_count"] == 3
        assert workspace["analysis_summary"]["staff_input_count"] == (
            len(summary["missing_items"]) + len(summary["conflicts"])
        )
        assert workspace["safety"] == {
            "official_record_saved": False,
            "signature_confirmed": False,
            "public_insurer_transferred": False,
            "restricted_auto_generation_count": 0,
            "notice": (
                "이 결과는 공식 저장 전 편집용 초안입니다. 직원이 원문 근거를 "
                "확인하고 수정한 뒤 사용해 주세요."
            ),
        }

        document_by_type = {
            item["document_type"]: item for item in workspace["documents"]
        }
        assert document_by_type["fall_risk_assessment"]["sections"][0]["rows"][0][
            "value"
        ] == "3등급"
        assert document_by_type["pressure_ulcer_risk_assessment"]["sections"][0][
            "rows"
        ][0]["value"] == "3등급"
        nutrition_row = next(
            row
            for section in document_by_type["needs_assessment"]["sections"]
            for row in section["rows"]
            if row["row_key"] == "needs_assessment.nutrition"
        )
        assert nutrition_row["value"] == "일반식 2/3 섭취"
        assert summary_items["mobility_support_need"]["selected_value"] is None
        assert form_items["fall_score"]["state"] == "current_observation_required"

        first_bundle = deepcopy(reviewed_payload["revisions"][1]["bundle"])
        first_evidence_refs = deepcopy(draft["revisions"][0]["field_evidence_refs"])
        marker = "직원 보완: 합성 현재 관찰을 원문과 대조함"
        first_bundle["revision"] = 3
        first_bundle["supersedes_revision"] = 2
        first_bundle["document_texts"]["long_term_care_service_plan"] = (
            document_by_type["long_term_care_service_plan"]["draft_text"]
        )
        first_bundle["document_texts"]["needs_assessment"] += f"\n{marker}"
        first_bundle["document_texts"]["long_term_care_service_plan"] += (
            f"\n{marker}"
        )
        saved = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/revisions",
            headers=ORIGIN,
            json={"bundle": first_bundle, "confirmed_field_keys": []},
        )
        assert saved.status_code == 201, saved.text
        saved_payload = saved.json()
        assert saved_payload["current_revision"] == 3
        assert len(saved_payload["revisions"]) == 3
        assert marker not in saved_payload["revisions"][0]["bundle"][
            "document_texts"
        ]["needs_assessment"]
        assert marker in saved_payload["revisions"][2]["bundle"]["document_texts"][
            "needs_assessment"
        ]
        assert saved_payload["revisions"][2]["field_evidence_refs"] == (
            first_evidence_refs
        )
        changed_keys = {
            item["field_key"] for item in saved_payload["revisions"][2]["field_changes"]
        }
        assert changed_keys == {
            "document_text.needs_assessment",
            "document_text.long_term_care_service_plan",
        }

        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/{draft_id}"
        )
        reopened_flow = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/questions"
        )
        reopened_summary = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{draft_id}/evidence-summary"
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json() == saved_payload
        assert reopened_flow.status_code == 200, reopened_flow.text
        assert reopened_flow.json()["revision"] == 3
        reopened_documents = {
            item["document_type"]: item
            for item in reopened_flow.json()["form_workspace"]["documents"]
        }
        assert marker in reopened_documents["needs_assessment"]["draft_text"]
        assert marker in reopened_documents["long_term_care_service_plan"][
            "draft_text"
        ]
        assert reopened_summary.status_code == 200, reopened_summary.text
        assert reopened_summary.json()["counts"] == summary["counts"]
        assert reopened_summary.json()["conflicts"] == summary["conflicts"]

        with SessionLocal() as db:
            assert db.scalar(
                select(func.count(NewAdmissionDraftRecord.id)).where(
                    NewAdmissionDraftRecord.id == draft_id
                )
            ) == 1


def test_structured_json_draft_is_not_counted_as_completed_file_intake():
    content = FIXTURE.read_bytes()
    material = extract_pdf_material(
        content,
        source_ref="json-material-001",
        document_kind="care_grade_certificate",
    )
    bundle = build_assessment_draft_bundle(
        [material],
        case_ref=f"json-only-{uuid4().hex}",
        reason="new_admission",
        assessment_date="2026-08-31",
        period_start=None,
        period_end=None,
    )
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        created = client.post(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts",
            headers=ORIGIN,
            json={"bundle": bundle.model_dump(mode="json")},
        )
        assert created.status_code == 201, created.text
        reopened = client.get(
            f"/api/workdesk/residents/{resident_id}/new-admission-drafts/"
            f"{created.json()['id']}/evidence-ledger"
        )
        assert reopened.status_code == 200, reopened.text
        assert reopened.json()["file_intake_complete"] is False
        assert reopened.json()["items"] == []


def test_rejected_upload_does_not_create_draft_or_evidence_rows():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()
        rejected = client.post(
            f"/api/workdesk/residents/{resident_id}/assessment-drafts/analyze",
            headers=ORIGIN,
            data={
                "reason": "new_admission",
                "assessment_date": "2026-08-31",
                "document_kinds": ["auto"],
            },
            files=[("files", ("invalid.txt", b"not-a-pdf", "text/plain"))],
        )
        assert rejected.status_code == 422, rejected.text

    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(NewAdmissionDraftRecord.id)).where(
                NewAdmissionDraftRecord.resident_id == resident_id
            )
        ) == 0
