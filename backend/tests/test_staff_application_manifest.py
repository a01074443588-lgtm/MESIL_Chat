import json
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.main import app
from app.models import (
    AuditEvent,
    Organization,
    OrgUnit,
    RoomMembership,
    Staff,
    StaffApplicationEntry,
    StaffApplicationRun,
    StaffServiceAssignment,
    StaffServicePeriod,
    StaffSourceAccount,
    User,
)
from app.staff_sync import _build_creation_only_change_entries


ORIGIN = {"origin": "http://testserver"}


def _login_admin(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "AdminPass!234"},
        headers=ORIGIN,
    )
    assert response.status_code == 200, response.text


def _protected_counts() -> dict[str, int]:
    with SessionLocal() as db:
        return {
            model.__tablename__: db.scalar(select(func.count()).select_from(model))
            for model in (
                Staff,
                User,
                StaffSourceAccount,
                StaffServicePeriod,
                StaffServiceAssignment,
                RoomMembership,
                AuditEvent,
            )
        }


def test_creation_manifest_preserves_approved_position_without_code() -> None:
    with TestClient(app):
        with SessionLocal() as db:
            organization = db.scalar(select(Organization))
            assert organization is not None
            business = db.scalar(
                select(OrgUnit).where(
                    OrgUnit.organization_id == organization.id,
                    OrgUnit.unit_type == "business",
                    OrgUnit.name == "방문요양",
                    OrgUnit.is_test_data.is_(False),
                )
            )
            assert business is not None
            source_key = "homecare:POSITION-WITHOUT-CODE"
            entries = [
                {
                    "source_key": source_key,
                    "incoming_payload": {
                        "display_name": "직위 증빙 시험직원",
                        "job_name": "시설장(관리책임자)",
                        "employment_status": "active",
                        "source_status": "재직",
                        "start_date": "2025-01-01",
                        "end_date": None,
                    },
                }
            ]
            candidates = [
                {
                    "candidate_key": "position-without-code",
                    "display_name": "직위 증빙 시험직원",
                    "accounts": [
                        {
                            "source_key": source_key,
                            "service_type": "homecare",
                            "external_id": "POSITION-WITHOUT-CODE",
                            "organizations": [
                                {
                                    "unit_type": "business",
                                    "unit_id": str(business.id),
                                }
                            ],
                            "job": {"code": None},
                            "position": {"proposed_name": "관리책임자"},
                        }
                    ],
                    "review_draft": None,
                }
            ]
            candidate_plans = [
                {
                    "candidate_key": "position-without-code",
                    "display_name": "직위 증빙 시험직원",
                    "decision_source": "auto_default",
                    "outcome": "prepared",
                    "person_plans": [
                        {
                            "source_keys": [source_key],
                            "planned_internal_code": "CF-POSITION-WITHOUT-CODE",
                            "staff_action": "create",
                            "employment_status": "active",
                            "source_account_actions": {"create": 1},
                            "service_period_actions": {"create": 1},
                            "service_assignment_actions": {"create": 1},
                        }
                    ],
                }
            ]

            change_entries, supported = _build_creation_only_change_entries(
                db,
                organization.id,
                batch_id=organization.id,
                entries=entries,
                candidates=candidates,
                candidate_plans=candidate_plans,
            )

            assert supported is True
            assignment = next(
                item for item in change_entries if item["entity_type"] == "service_assignment"
            )
            assert assignment["after_state"]["position_code_id"] is None
            assert assignment["after_state"]["position_title_snapshot"] == "관리책임자"


def test_application_run_prepares_exact_create_manifest_without_applying(
    tmp_path,
    monkeypatch,
) -> None:
    roster_path = tmp_path / "carefor_staff.local.json"
    roster_path.write_text(
        json.dumps(
            {
                "staff": [
                    {
                        "external_id": "MANIFEST-FAC-001",
                        "display_name": "실행기록 시험직원",
                        "service_type": "facility",
                        "status": "재직",
                        "job_name": "사회복지사",
                        "birth_date": "1991-02-03",
                        "start_date": "2025-01-01",
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    original_path = settings.carefor_staff_roster_path
    settings.carefor_staff_roster_path = roster_path.as_posix()
    try:
        with TestClient(app) as client:
            _login_admin(client)
            batch_response = client.post(
                "/api/admin/carefor-staff-sync/preview",
                headers=ORIGIN,
            )
            assert batch_response.status_code == 201, batch_response.text
            batch_id = batch_response.json()["id"]
            plan_response = client.get(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-plan"
            )
            assert plan_response.status_code == 200, plan_response.text
            plan = plan_response.json()
            assert plan["change_manifest_supported"] is True
            assert "_change_entries" not in plan
            assert {item["code"] for item in plan["blockers"]} == {
                "rollback_manifest_missing"
            }

            before = _protected_counts()
            prepared = client.post(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-runs",
                json={
                    "expected_plan_fingerprint": plan["plan_fingerprint"],
                    "expected_change_fingerprint": plan["change_fingerprint"],
                },
                headers=ORIGIN,
            )
            assert prepared.status_code == 201, prepared.text
            run = prepared.json()
            assert run["status"] == "prepared"
            assert run["entry_count"] == 4
            assert run["entry_summary"] == {
                "staff": 1,
                "source_account": 1,
                "service_period": 1,
                "service_assignment": 1,
            }
            assert run["apply_locked"] is True
            assert run["can_apply"] is False
            assert run["backup_verified"] is False
            assert run["restore_verified"] is False
            after = _protected_counts()
            assert after == before

            repeated = client.post(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-runs",
                json={
                    "expected_plan_fingerprint": plan["plan_fingerprint"],
                    "expected_change_fingerprint": plan["change_fingerprint"],
                },
                headers=ORIGIN,
            )
            assert repeated.status_code == 201, repeated.text
            assert repeated.json()["id"] == run["id"]
            assert repeated.json()["manifest_fingerprint"] == run["manifest_fingerprint"]

            stale_expected = client.post(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/application-runs",
                json={
                    "expected_plan_fingerprint": "0" * 64,
                    "expected_change_fingerprint": plan["change_fingerprint"],
                },
                headers=ORIGIN,
            )
            assert stale_expected.status_code == 409

            with SessionLocal() as db:
                stored_run = db.get(StaffApplicationRun, run["id"])
                assert stored_run is not None
                entries = list(
                    db.scalars(
                        select(StaffApplicationEntry)
                        .where(StaffApplicationEntry.run_id == stored_run.id)
                        .order_by(StaffApplicationEntry.ordinal)
                    ).all()
                )
                assert len(entries) == 4
                assert all(entry.action == "create" for entry in entries)
                assert all(entry.before_state is None for entry in entries)
                assert len({entry.row_id for entry in entries}) == 4
                assert all("password" not in json.dumps(entry.after_state) for entry in entries)
                assert all("username" not in json.dumps(entry.after_state) for entry in entries)

            assert client.post(
                f"/api/admin/carefor-staff-sync/batches/{batch_id}/apply",
                json={},
                headers=ORIGIN,
            ).status_code == 404
            assert client.post(
                f"/api/admin/carefor-staff-sync/application-runs/{run['id']}/rollback",
                json={},
                headers=ORIGIN,
            ).status_code == 404

            backup_root = Path(settings.upload_dir).resolve().parent / "backups"
            backup_root.mkdir(parents=True, exist_ok=True)
            traversal = client.post(
                f"/api/admin/carefor-staff-sync/application-runs/{run['id']}/backup-proof",
                json={"evidence_filename": "../outside.json"},
                headers=ORIGIN,
            )
            assert traversal.status_code == 409

            dump_path = backup_root / "manifest-fixture.dump"
            dump_path.write_bytes(b"isolated-test-dump")
            dump_sha = sha256(dump_path.read_bytes()).hexdigest().upper()
            state_fingerprint = "A" * 64
            backup_manifest_path = backup_root / "manifest-fixture.json"
            backup_manifest_path.write_text(
                json.dumps(
                    {
                        "manifest_schema_version": 2,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "schema": "test_schema",
                        "database_backup": dump_path.name,
                        "database_backup_bytes": dump_path.stat().st_size,
                        "database_backup_sha256": dump_sha,
                        "alembic_revision": "025_staff_application_manifest",
                        "protected_state_fingerprint_sha256": state_fingerprint,
                        "protected_tables": [
                            {
                                "table": "staff",
                                "row_count": 1,
                                "row_digest_md5": "0" * 32,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            monkeypatch.setattr(
                "app.staff_application_manifest._current_alembic_revision",
                lambda db: "025_staff_application_manifest",
            )
            proof = client.post(
                f"/api/admin/carefor-staff-sync/application-runs/{run['id']}/backup-proof",
                json={"evidence_filename": backup_manifest_path.name},
                headers=ORIGIN,
            )
            assert proof.status_code == 200, proof.text
            assert proof.json()["status"] == "backup_verified"
            assert proof.json()["backup_verified"] is True
            assert proof.json()["apply_locked"] is True

            receipt_path = backup_root / "manifest-fixture.restore-receipt.json"
            now = datetime.now(timezone.utc).isoformat()
            receipt_path.write_text(
                json.dumps(
                    {
                        "receipt_schema_version": 1,
                        "backup_sha256": dump_sha,
                        "restored_schema": "test_schema",
                        "restored_alembic_revision": "025_staff_application_manifest",
                        "restored_state_fingerprint_sha256": state_fingerprint,
                        "restored_tables": [
                            {
                                "table": "staff",
                                "row_count": 1,
                                "row_digest_md5": "0" * 32,
                            }
                        ],
                        "started_at": now,
                        "finished_at": now,
                        "cleanup_succeeded": True,
                        "verification_result": "passed",
                    }
                ),
                encoding="utf-8",
            )
            evidence = client.post(
                f"/api/admin/carefor-staff-sync/application-runs/{run['id']}/restore-receipt",
                json={"evidence_filename": receipt_path.name},
                headers=ORIGIN,
            )
            assert evidence.status_code == 200, evidence.text
            assert evidence.json()["status"] == "evidence_ready"
            assert evidence.json()["evidence_ready"] is True
            assert evidence.json()["apply_locked"] is True
            assert evidence.json()["can_apply"] is False
    finally:
        settings.carefor_staff_roster_path = original_path
