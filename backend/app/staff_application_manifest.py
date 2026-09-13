from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .config import settings
from .models import (
    StaffApplicationBackupProof,
    StaffApplicationEntry,
    StaffApplicationRestoreReceipt,
    StaffApplicationRun,
    StaffSyncBatch,
    utcnow,
)


MAX_EVIDENCE_BYTES = 2 * 1024 * 1024


class StaffApplicationManifestError(ValueError):
    pass


def _stable(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): _stable(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_stable(item) for item in value]
    if isinstance(value, (UUID, datetime)):
        return value.isoformat() if isinstance(value, datetime) else str(value)
    return value


def _fingerprint(value: Any) -> str:
    canonical = json.dumps(
        _stable(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _parse_datetime(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise StaffApplicationManifestError(f"{label} 시간이 올바르지 않습니다.") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _evidence_root() -> Path:
    return (Path(settings.upload_dir).resolve().parent / "backups").resolve()


def _read_evidence(filename: str) -> tuple[Path, dict[str, Any], str]:
    if not filename or Path(filename).name != filename or not filename.endswith(".json"):
        raise StaffApplicationManifestError("증명 파일 이름만 입력해 주세요.")
    root = _evidence_root()
    path = (root / filename).resolve()
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise StaffApplicationManifestError("허용된 백업 폴더 밖의 파일입니다.") from exc
    if not path.is_file():
        raise StaffApplicationManifestError("증명 파일을 찾을 수 없습니다.")
    if path.stat().st_size > MAX_EVIDENCE_BYTES:
        raise StaffApplicationManifestError("증명 파일이 허용 크기를 넘었습니다.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise StaffApplicationManifestError("증명 JSON 파일을 읽을 수 없습니다.") from exc
    if not isinstance(payload, dict):
        raise StaffApplicationManifestError("증명 JSON 최상위 값은 객체여야 합니다.")
    return path, payload, _file_sha256(path)


def _current_alembic_revision(db: Session) -> str:
    revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    return str(revision)


def prepare_application_run(
    db: Session,
    *,
    organization_id: UUID,
    batch: StaffSyncBatch,
    created_by_id: UUID,
    plan: dict[str, Any],
) -> StaffApplicationRun:
    change_entries = list(plan.get("_change_entries") or [])
    if not plan.get("change_manifest_supported") or not change_entries:
        raise StaffApplicationManifestError(
            "현재 계획에는 행별 복구기록을 안전하게 만들 수 없는 변경이 있습니다."
        )
    unresolved = {
        str(blocker.get("code"))
        for blocker in plan.get("blockers") or []
        if blocker.get("code") != "rollback_manifest_missing"
    }
    if unresolved:
        raise StaffApplicationManifestError(
            "조직·휴직·관리자 결정 차단을 모두 해결한 뒤 실행기록을 준비해 주세요."
        )
    summary = dict(plan.get("summary") or {})
    if (
        summary.get("prepared_candidates") != summary.get("candidate_count")
        or summary.get("needs_design_candidates")
        or summary.get("blocked_candidates")
        or summary.get("login_account_changes")
        or summary.get("room_membership_changes")
    ):
        raise StaffApplicationManifestError("현재 계획은 첫 직원정보 반영 경계와 다릅니다.")

    manifest_material = {
        "manifest_schema_version": 1,
        "organization_id": organization_id,
        "batch_id": batch.id,
        "source_file_sha256": batch.file_sha256,
        "plan_schema_version": plan["plan_schema_version"],
        "plan_fingerprint": plan["plan_fingerprint"],
        "change_fingerprint": plan["change_fingerprint"],
        "organization_catalog_version": plan["organization_catalog_version"],
        "organization_catalog_fingerprint": plan[
            "organization_catalog_fingerprint"
        ],
        "leave_access_policy_version": plan["leave_access_policy_version"],
        "leave_access_policy_fingerprint": plan[
            "leave_access_policy_fingerprint"
        ],
        "entries": change_entries,
    }
    manifest_fingerprint = _fingerprint(manifest_material)
    existing = db.scalar(
        select(StaffApplicationRun).where(
            StaffApplicationRun.organization_id == organization_id,
            StaffApplicationRun.batch_id == batch.id,
            StaffApplicationRun.plan_fingerprint == plan["plan_fingerprint"],
            StaffApplicationRun.change_fingerprint == plan["change_fingerprint"],
        )
    )
    if existing is not None:
        if existing.manifest_fingerprint != manifest_fingerprint:
            raise StaffApplicationManifestError("같은 계획의 실행기록 지문이 일치하지 않습니다.")
        return existing

    run = StaffApplicationRun(
        organization_id=organization_id,
        batch_id=batch.id,
        manifest_schema_version=1,
        plan_schema_version=plan["plan_schema_version"],
        plan_fingerprint=plan["plan_fingerprint"],
        change_fingerprint=plan["change_fingerprint"],
        manifest_fingerprint=manifest_fingerprint,
        organization_catalog_version=plan["organization_catalog_version"],
        organization_catalog_fingerprint=plan["organization_catalog_fingerprint"],
        leave_access_policy_version=plan["leave_access_policy_version"],
        leave_access_policy_fingerprint=plan["leave_access_policy_fingerprint"],
        policy_snapshot={"leave_access_policy": plan["leave_access_policy"]},
        plan_summary=summary,
        entry_count=len(change_entries),
        status="prepared",
        created_by_id=created_by_id,
    )
    db.add(run)
    db.flush()
    for ordinal, entry in enumerate(change_entries, start=1):
        db.add(
            StaffApplicationEntry(
                run_id=run.id,
                ordinal=ordinal,
                entry_key=str(entry["entry_key"]),
                entity_type=str(entry["entity_type"]),
                action=str(entry["action"]),
                row_id=UUID(str(entry["row_id"])),
                natural_key=entry["natural_key"],
                before_state=entry.get("before_state"),
                after_state=entry["after_state"],
                before_fingerprint=entry.get("before_fingerprint"),
                after_fingerprint=str(entry["after_fingerprint"]),
                dependency_keys=list(entry.get("dependency_keys") or []),
            )
        )
    db.flush()
    return run


def attach_backup_proof(
    db: Session,
    *,
    run: StaffApplicationRun,
    evidence_filename: str,
    verified_by_id: UUID,
) -> StaffApplicationBackupProof:
    if run.status == "stale":
        raise StaffApplicationManifestError("현재 실행기록은 자료 변경으로 만료되었습니다.")
    if run.backup_proof is not None:
        if run.backup_proof.evidence_filename != evidence_filename:
            raise StaffApplicationManifestError("다른 백업 증명이 이미 연결되어 있습니다.")
        return run.backup_proof

    path, payload, evidence_sha = _read_evidence(evidence_filename)
    if int(payload.get("manifest_schema_version") or 0) < 2:
        raise StaffApplicationManifestError("SHA와 보호지문이 포함된 백업 증명이 아닙니다.")
    dump_filename = str(payload.get("database_backup") or "")
    if not dump_filename or Path(dump_filename).name != dump_filename:
        raise StaffApplicationManifestError("백업 파일 이름이 올바르지 않습니다.")
    dump_path = (path.parent / dump_filename).resolve()
    if dump_path.parent != path.parent or not dump_path.is_file():
        raise StaffApplicationManifestError("백업 dump 파일을 찾을 수 없습니다.")
    dump_bytes = dump_path.stat().st_size
    dump_sha = _file_sha256(dump_path)
    if dump_bytes != int(payload.get("database_backup_bytes") or -1):
        raise StaffApplicationManifestError("백업 dump 크기가 증명과 다릅니다.")
    if dump_sha != str(payload.get("database_backup_sha256") or "").upper():
        raise StaffApplicationManifestError("백업 dump SHA-256이 증명과 다릅니다.")
    revision = str(payload.get("alembic_revision") or "")
    if revision != _current_alembic_revision(db):
        raise StaffApplicationManifestError("백업 DB 버전이 현재 계획의 DB 버전과 다릅니다.")
    protected_state = str(payload.get("protected_state_fingerprint_sha256") or "")
    protected_tables = payload.get("protected_tables")
    if len(protected_state) != 64 or not isinstance(protected_tables, list):
        raise StaffApplicationManifestError("백업 보호자료 지문이 없습니다.")
    source_created_at = _parse_datetime(payload.get("created_at"), "백업 생성")
    if source_created_at > datetime.now(timezone.utc):
        raise StaffApplicationManifestError("백업 생성 시간이 현재보다 미래입니다.")

    proof = StaffApplicationBackupProof(
        run_id=run.id,
        evidence_filename=evidence_filename,
        evidence_sha256=evidence_sha,
        dump_filename=dump_filename,
        dump_bytes=dump_bytes,
        dump_sha256=dump_sha,
        database_schema=str(payload.get("schema") or ""),
        alembic_revision=revision,
        protected_state_fingerprint=protected_state.upper(),
        protected_tables=protected_tables,
        source_created_at=source_created_at,
        raw_payload=payload,
        verified_by_id=verified_by_id,
    )
    db.add(proof)
    run.status = "backup_verified"
    run.updated_at = utcnow()
    db.flush()
    return proof


def attach_restore_receipt(
    db: Session,
    *,
    run: StaffApplicationRun,
    receipt_filename: str,
    verified_by_id: UUID,
) -> StaffApplicationRestoreReceipt:
    if run.status == "stale":
        raise StaffApplicationManifestError("현재 실행기록은 자료 변경으로 만료되었습니다.")
    if run.backup_proof is None:
        raise StaffApplicationManifestError("먼저 백업 증명을 연결해 주세요.")
    if run.restore_receipt is not None:
        if run.restore_receipt.receipt_filename != receipt_filename:
            raise StaffApplicationManifestError("다른 복원 영수증이 이미 연결되어 있습니다.")
        return run.restore_receipt

    _, payload, receipt_sha = _read_evidence(receipt_filename)
    if int(payload.get("receipt_schema_version") or 0) != 1:
        raise StaffApplicationManifestError("지원하지 않는 복원 영수증 형식입니다.")
    if str(payload.get("verification_result") or "") != "passed":
        raise StaffApplicationManifestError("복원 검증이 통과된 영수증이 아닙니다.")
    if payload.get("cleanup_succeeded") is not True:
        raise StaffApplicationManifestError("격리 복원 DB 정리가 확인되지 않았습니다.")
    dump_sha = str(payload.get("backup_sha256") or "").upper()
    if dump_sha != run.backup_proof.dump_sha256:
        raise StaffApplicationManifestError("복원한 dump가 연결된 백업과 다릅니다.")
    restored_revision = str(payload.get("restored_alembic_revision") or "")
    if restored_revision != run.backup_proof.alembic_revision:
        raise StaffApplicationManifestError("복원 DB 버전이 백업 증명과 다릅니다.")
    restored_state = str(payload.get("restored_state_fingerprint_sha256") or "").upper()
    if restored_state != run.backup_proof.protected_state_fingerprint:
        raise StaffApplicationManifestError("복원 보호자료 지문이 백업 증명과 다릅니다.")
    restored_tables = payload.get("restored_tables")
    if not isinstance(restored_tables, list):
        raise StaffApplicationManifestError("복원 표 지문이 없습니다.")
    started_at = _parse_datetime(payload.get("started_at"), "복원 시작")
    finished_at = _parse_datetime(payload.get("finished_at"), "복원 종료")
    if finished_at < started_at:
        raise StaffApplicationManifestError("복원 종료 시간이 시작 시간보다 빠릅니다.")

    receipt = StaffApplicationRestoreReceipt(
        run_id=run.id,
        receipt_filename=receipt_filename,
        receipt_sha256=receipt_sha,
        dump_sha256=dump_sha,
        restored_schema=str(payload.get("restored_schema") or ""),
        restored_alembic_revision=restored_revision,
        restored_state_fingerprint=restored_state,
        restored_tables=restored_tables,
        started_at=started_at,
        finished_at=finished_at,
        cleanup_succeeded=True,
        raw_payload=payload,
        verified_by_id=verified_by_id,
    )
    db.add(receipt)
    run.status = "evidence_ready"
    run.updated_at = utcnow()
    db.flush()
    return receipt


def application_run_response(run: StaffApplicationRun) -> dict[str, Any]:
    entry_summary = dict(Counter(entry.entity_type for entry in run.entries))
    for entity_type in ("staff", "source_account", "service_period", "service_assignment"):
        entry_summary.setdefault(entity_type, 0)
    return {
        "id": run.id,
        "batch_id": run.batch_id,
        "manifest_schema_version": run.manifest_schema_version,
        "plan_schema_version": run.plan_schema_version,
        "plan_fingerprint": run.plan_fingerprint,
        "change_fingerprint": run.change_fingerprint,
        "manifest_fingerprint": run.manifest_fingerprint,
        "organization_catalog_version": run.organization_catalog_version,
        "leave_access_policy_version": run.leave_access_policy_version,
        "status": run.status,
        "entry_count": run.entry_count,
        "entry_summary": entry_summary,
        "plan_summary": run.plan_summary,
        "backup_verified": run.backup_proof is not None,
        "restore_verified": run.restore_receipt is not None,
        "evidence_ready": run.status == "evidence_ready",
        "apply_locked": True,
        "can_apply": False,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }
