from datetime import date
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.database import SessionLocal
from app.main import app
from app.models import Organization, Resident, ResidentCarePlanningState
from app.resident_care_planning import add_months, candidate_reasons


ORIGIN = {"origin": "http://testserver"}


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
            internal_code=f"M21-H-{uuid4().hex}",
            display_name="기초사정 검증어르신(가명)",
            service_type="facility",
            is_test_data=True,
        )
        db.add(resident)
        db.commit()
        return str(resident.id)


def test_cycle_math_and_candidate_rules_are_deterministic():
    assert add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)
    assert candidate_reasons(
        as_of=date(2026, 8, 26),
        assessment_date=date(2026, 2, 26),
        next_due_date=date(2026, 8, 26),
        reassess_on_state_change=True,
        state_change_triggered=False,
        status="confirmed",
    ) == ["사용자 설정 운영주기 도래"]
    assert candidate_reasons(
        as_of=date(2026, 8, 26),
        assessment_date=date(2026, 7, 1),
        next_due_date=date(2027, 1, 1),
        reassess_on_state_change=True,
        state_change_triggered=True,
        status="needs_review",
    ) == ["상태 변경으로 즉시 재검토", "직원 확인 필요"]


def test_resident_care_planning_save_reload_and_human_boundaries():
    with TestClient(app) as client:
        _login_admin(client)
        resident_id = _create_test_resident()

        initial = client.get(
            f"/api/workdesk/residents/{resident_id}/care-planning",
            params={"as_of": "2026-08-26"},
        )
        assert initial.status_code == 200, initial.text
        initial_payload = initial.json()
        assert len(initial_payload["items"]) == 5
        assert all(item["is_candidate"] for item in initial_payload["items"])
        assert all(item["version"] == 0 for item in initial_payload["items"])
        assert "사용자 제공 운영 원칙" in initial_payload["legal_standard_notice"]

        cognitive = client.put(
            (
                f"/api/workdesk/residents/{resident_id}/care-planning/"
                "cognitive_function_assessment"
            ),
            headers=ORIGIN,
            json={
                "assessment_date": "2026-02-26",
                "cycle_months": 6,
                "reassess_on_state_change": True,
                "state_change_triggered": False,
                "status": "confirmed",
                "evidence_refs": ["합성 대화 근거 1건"],
                "known_facts": ["저장된 사실만 기록"],
                "questions_required": ["직원이 직접 검사 문항을 확인"],
                "professional_review_fields": ["점수와 판정은 전문가 확인"],
                "author_confirmed": True,
            },
        )
        assert cognitive.status_code == 200, cognitive.text
        cognitive_payload = cognitive.json()
        assert cognitive_payload["next_due_date"] == "2026-08-26"
        assert cognitive_payload["valid_until"] == "2026-08-26"
        assert cognitive_payload["version"] == 1
        assert cognitive_payload["author_confirmed"] is True
        assert "score" not in cognitive_payload
        assert "diagnosis" not in cognitive_payload

        fall = client.put(
            (
                f"/api/workdesk/residents/{resident_id}/care-planning/"
                "fall_risk_assessment"
            ),
            headers=ORIGIN,
            json={
                "assessment_date": "2026-07-01",
                "next_due_date": "2027-01-01",
                "cycle_months": 6,
                "reassess_on_state_change": True,
                "state_change_triggered": True,
                "change_reason": "합성 보행 상태 변경",
                "status": "needs_review",
                "evidence_refs": ["합성 안전 관찰 1건"],
                "known_facts": ["보행 상태 변경 사실"],
                "questions_required": ["직원이 낙상위험 항목을 직접 평가"],
                "professional_review_fields": ["점수와 위험도 판정"],
                "author_confirmed": False,
            },
        )
        assert fall.status_code == 200, fall.text
        assert fall.json()["candidate_reasons"] == [
            "상태 변경으로 즉시 재검토",
            "직원 확인 필요",
        ]

        reloaded = client.get(
            f"/api/workdesk/residents/{resident_id}/care-planning",
            params={"as_of": "2026-08-26"},
        )
        assert reloaded.status_code == 200, reloaded.text
        reloaded_by_type = {
            item["document_type"]: item for item in reloaded.json()["items"]
        }
        assert reloaded_by_type["cognitive_function_assessment"]["version"] == 1
        assert reloaded_by_type["cognitive_function_assessment"]["known_facts"] == [
            "저장된 사실만 기록"
        ]
        assert reloaded_by_type["fall_risk_assessment"]["change_reason"] == (
            "합성 보행 상태 변경"
        )

        invalid_confirmation = client.put(
            (
                f"/api/workdesk/residents/{resident_id}/care-planning/"
                "needs_assessment"
            ),
            headers=ORIGIN,
            json={"status": "confirmed", "author_confirmed": False},
        )
        assert invalid_confirmation.status_code == 422

        with SessionLocal() as db:
            saved_count = db.scalar(
                select(func.count(ResidentCarePlanningState.id)).where(
                    ResidentCarePlanningState.resident_id == resident_id
                )
            )
            assert saved_count == 2
