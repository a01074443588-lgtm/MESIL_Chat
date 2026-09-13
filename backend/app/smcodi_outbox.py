from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .confirmed_records import ConfirmedWorkRecord, ConfirmedWorkRecordVersion
from .database import get_db
from .dependencies import get_current_user
from .models import User, utcnow
from .smcodi_outbox_models import (
    SmcodiHandoverOutbox,
    SmcodiHandoverTargetMapping,
)
from .smcodi_outbox_schemas import (
    SMCODI_HANDOVER_CONTRACT,
    SmcodiOutboxListResponse,
    SmcodiOutboxPreviewRequest,
    SmcodiOutboxPreviewResponse,
    SmcodiOutboxResponse,
    SmcodiStaffHubHandoverV1,
    SmcodiValidationIssue,
)


class SmcodiOutboxNotFoundError(LookupError):
    pass


class SmcodiOutboxSourceError(RuntimeError):
    pass


@dataclass(frozen=True)
class SmcodiOutboxWriteResult:
    item: SmcodiHandoverOutbox
    created: bool


URGENCY_MAP: dict[str, str] = {
    "low": "normal",
    "medium": "caution",
    "high": "caution",
    "urgent": "urgent",
}


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_hash(payload: dict[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _test_data_scope(user: User) -> bool | None:
    """시험 사용자와 실제 사용자의 대기함을 자동으로 분리한다.

    관리자는 기관 전체를 관리하므로 일반 관리 화면에서는 두 구분을 모두 볼 수
    있다. 단, 심사 체험 세션은 시험자료만 볼 수 있다.
    """

    if getattr(user, "_reviewer_experience", None) is not None:
        return True
    if user.role == "admin":
        return None
    if user.staff is not None:
        return bool(user.staff.is_test_data)
    # 직원 연결이 손상된 비관리자 계정이 실제 자료를 보게 두지 않는다.
    return True


def _require_outbox_user(user: User = Depends(get_current_user)) -> User:
    if (
        getattr(user, "_reviewer_experience", None) is not None
        or (user.role != "admin" and not user.can_process_records)
    ):
        raise HTTPException(
            status_code=403,
            detail="SMCODI 연계 대기함은 관리자 또는 업무처리자만 사용할 수 있습니다.",
        )
    return user


def _load_source(
    db: Session,
    *,
    organization_id: UUID,
    source_record_id: UUID,
    source_version: int | None,
    test_data_scope: bool | None,
) -> tuple[ConfirmedWorkRecord, ConfirmedWorkRecordVersion]:
    record = db.scalar(
        select(ConfirmedWorkRecord).where(
            ConfirmedWorkRecord.id == source_record_id,
            ConfirmedWorkRecord.organization_id == organization_id,
        )
    )
    if record is None or (
        test_data_scope is not None and record.is_test_data != test_data_scope
    ):
        raise SmcodiOutboxNotFoundError("확정 업무기록을 찾을 수 없습니다.")

    # 변경 가능한 현재 레코드가 실제 불변 버전을 가리키는지도 먼저 확인한다.
    current = db.scalar(
        select(ConfirmedWorkRecordVersion).where(
            ConfirmedWorkRecordVersion.organization_id == organization_id,
            ConfirmedWorkRecordVersion.record_id == record.id,
            ConfirmedWorkRecordVersion.version == record.current_version,
        )
    )
    if current is None or current.content_hash != record.current_content_hash:
        raise SmcodiOutboxSourceError(
            "확정 업무기록의 현재 버전 이력이 일치하지 않습니다."
        )
    if _payload_hash(current.snapshot) != current.content_hash:
        raise SmcodiOutboxSourceError(
            "확정 업무기록의 현재 버전 내용 해시가 일치하지 않습니다."
        )

    selected_version = source_version or record.current_version
    version = db.scalar(
        select(ConfirmedWorkRecordVersion).where(
            ConfirmedWorkRecordVersion.organization_id == organization_id,
            ConfirmedWorkRecordVersion.record_id == record.id,
            ConfirmedWorkRecordVersion.version == selected_version,
        )
    )
    if version is None:
        raise SmcodiOutboxNotFoundError(
            "선택한 확정 업무기록 버전을 찾을 수 없습니다."
        )
    if _payload_hash(version.snapshot) != version.content_hash:
        raise SmcodiOutboxSourceError(
            "선택한 확정 업무기록 버전의 내용 해시가 일치하지 않습니다."
        )
    snapshot_resident_id = version.snapshot.get("resident_id")
    if snapshot_resident_id and str(snapshot_resident_id) != str(record.resident_id):
        raise SmcodiOutboxSourceError(
            "확정 업무기록 버전의 어르신 식별자가 현재 기록과 다릅니다."
        )
    return record, version


def _load_target_mapping(
    db: Session,
    *,
    organization_id: UUID,
    record: ConfirmedWorkRecord,
    mapping_id: UUID | None,
) -> SmcodiHandoverTargetMapping | None:
    if mapping_id is None:
        return None
    return db.scalar(
        select(SmcodiHandoverTargetMapping).where(
            SmcodiHandoverTargetMapping.id == mapping_id,
            SmcodiHandoverTargetMapping.organization_id == organization_id,
            SmcodiHandoverTargetMapping.local_resident_id == record.resident_id,
            SmcodiHandoverTargetMapping.is_test_data == record.is_test_data,
            SmcodiHandoverTargetMapping.is_active.is_(True),
            SmcodiHandoverTargetMapping.verified_at.is_not(None),
        )
    )


def _validation_issues(
    request: SmcodiOutboxPreviewRequest,
    *,
    record: ConfirmedWorkRecord,
    mapping: SmcodiHandoverTargetMapping | None,
) -> list[SmcodiValidationIssue]:
    issues: list[SmcodiValidationIssue] = []
    if record.lifecycle_status != "active":
        issues.append(
            SmcodiValidationIssue(
                code="source_record_inactive",
                field="source_record_id",
                message="철회된 확정 업무기록은 SMCODI 연계 준비를 할 수 없습니다.",
            )
        )
    if request.target_mapping_id is None:
        issues.append(
            SmcodiValidationIssue(
                code="missing_target_mapping",
                field="target_mapping_id",
                message="확인된 SMCODI 어르신·전달대상 연결값이 필요합니다.",
            )
        )
    elif mapping is None:
        issues.append(
            SmcodiValidationIssue(
                code="unverified_target_mapping",
                field="target_mapping_id",
                message="이 기관·어르신에 대해 확인된 SMCODI 연결값이 아닙니다.",
            )
        )
    elif mapping.target_unit_id is None and mapping.assigned_user_id is None:
        issues.append(
            SmcodiValidationIssue(
                code="missing_handover_target",
                field="target_mapping_id",
                message="확인된 연결값에 SMCODI 전달팀 또는 담당자가 없습니다.",
            )
        )
    return issues


def _current_item_validation_issues(
    db: Session,
    *,
    item: SmcodiHandoverOutbox,
) -> list[SmcodiValidationIssue]:
    """현재 원본과 연결표를 기준으로 기존 ready 항목을 다시 확인한다."""

    issues: list[SmcodiValidationIssue] = []
    record = db.scalar(
        select(ConfirmedWorkRecord).where(
            ConfirmedWorkRecord.id == item.source_record_id,
            ConfirmedWorkRecord.organization_id == item.organization_id,
        )
    )
    if record is None or record.lifecycle_status != "active":
        issues.append(
            SmcodiValidationIssue(
                code="source_record_inactive",
                field="source_record_id",
                message="원본 확정 업무기록이 철회되었거나 더 이상 유효하지 않습니다.",
            )
        )

    version = db.scalar(
        select(ConfirmedWorkRecordVersion).where(
            ConfirmedWorkRecordVersion.id == item.source_version_id,
            ConfirmedWorkRecordVersion.organization_id == item.organization_id,
            ConfirmedWorkRecordVersion.record_id == item.source_record_id,
            ConfirmedWorkRecordVersion.version == item.source_version,
        )
    )
    payload = item.payload if isinstance(item.payload, dict) else {}
    source_integrity_ok = (
        version is not None
        and version.content_hash == item.source_content_hash
        and _payload_hash(version.snapshot) == version.content_hash
        and payload.get("source_record_id") == str(item.source_record_id)
        and payload.get("source_version_id") == str(item.source_version_id)
        and payload.get("source_version") == item.source_version
        and payload.get("source_content_hash") == item.source_content_hash
        and payload.get("idempotency_key") == item.idempotency_key
        and _payload_hash(payload) == item.payload_hash
    )
    if not source_integrity_ok:
        issues.append(
            SmcodiValidationIssue(
                code="preview_integrity_changed",
                field="source_version_id",
                message="원본 버전 또는 연계 미리보기의 무결성을 다시 확인해야 합니다.",
            )
        )

    mapping: SmcodiHandoverTargetMapping | None = None
    if item.target_mapping_id is not None and record is not None:
        mapping = db.scalar(
            select(SmcodiHandoverTargetMapping).where(
                SmcodiHandoverTargetMapping.id == item.target_mapping_id,
                SmcodiHandoverTargetMapping.organization_id == item.organization_id,
                SmcodiHandoverTargetMapping.local_resident_id == record.resident_id,
                SmcodiHandoverTargetMapping.is_test_data == item.is_test_data,
                SmcodiHandoverTargetMapping.is_active.is_(True),
                SmcodiHandoverTargetMapping.verified_at.is_not(None),
            )
        )
    if mapping is None:
        issues.append(
            SmcodiValidationIssue(
                code="unverified_target_mapping",
                field="target_mapping_id",
                message="확인했던 SMCODI 연결값이 해제되었거나 더 이상 유효하지 않습니다.",
            )
        )
    else:
        expected_targets = {
            "recipient_id": str(mapping.target_recipient_id),
            "target_unit_id": (
                str(mapping.target_unit_id) if mapping.target_unit_id else None
            ),
            "assigned_user_id": (
                str(mapping.assigned_user_id) if mapping.assigned_user_id else None
            ),
        }
        stored_targets = {
            "recipient_id": (
                str(item.target_recipient_id) if item.target_recipient_id else None
            ),
            "target_unit_id": (
                str(item.target_unit_id) if item.target_unit_id else None
            ),
            "assigned_user_id": (
                str(item.assigned_user_id) if item.assigned_user_id else None
            ),
        }
        if expected_targets != stored_targets or any(
            payload.get(field) != expected
            for field, expected in expected_targets.items()
        ):
            issues.append(
                SmcodiValidationIssue(
                    code="target_mapping_changed",
                    field="target_mapping_id",
                    message="확인했던 SMCODI 전달대상이 변경되어 새 미리보기가 필요합니다.",
                )
            )
    return issues


def _downgrade_invalid_ready_previews(
    db: Session,
    *,
    conditions: list[Any],
) -> None:
    changed = False
    ready_items = list(
        db.scalars(
            select(SmcodiHandoverOutbox).where(
                SmcodiHandoverOutbox.status == "ready",
                *conditions,
            )
        ).all()
    )
    for item in ready_items:
        issues = _current_item_validation_issues(db, item=item)
        if not issues:
            continue
        item.status = "blocked"
        item.validation_issues = [
            issue.model_dump(mode="json") for issue in issues
        ]
        item.updated_at = utcnow()
        changed = True
    if changed:
        db.flush()


def _build_payload(
    *,
    organization_id: UUID,
    record: ConfirmedWorkRecord,
    version: ConfirmedWorkRecordVersion,
    request: SmcodiOutboxPreviewRequest,
    mapping: SmcodiHandoverTargetMapping | None,
) -> tuple[dict[str, Any], str, str]:
    snapshot = version.snapshot
    urgency = URGENCY_MAP.get(str(snapshot.get("urgency") or "").strip())
    if urgency is None:
        raise SmcodiOutboxSourceError(
            "확정 업무기록 버전의 긴급도 값을 변환할 수 없습니다."
        )

    candidate: dict[str, Any] = {
        "contract": SMCODI_HANDOVER_CONTRACT,
        "recipient_id": mapping.target_recipient_id if mapping else None,
        "occurred_at": snapshot.get("occurred_at"),
        "observation_text": snapshot.get("observation_text", ""),
        "action_text": snapshot.get("action_text", ""),
        # SMCODI 현재 입력 계약에는 없는 결과 항목도 손실 없이 함께 보존한다.
        "result_text": snapshot.get("result_text", ""),
        "measurement_text": snapshot.get("measurement_text", ""),
        "target_unit_id": mapping.target_unit_id if mapping else None,
        "assigned_user_id": mapping.assigned_user_id if mapping else None,
        "urgency": urgency,
        "needs_follow_up": bool(snapshot.get("needs_follow_up", False)),
        "source_record_id": record.id,
        "source_version_id": version.id,
        "source_version": version.version,
        "source_content_hash": version.content_hash,
        "idempotency_key": "0" * 64,
    }
    try:
        validated = SmcodiStaffHubHandoverV1.model_validate(candidate)
    except ValidationError as exc:
        raise SmcodiOutboxSourceError(
            "확정 업무기록 버전을 SMCODI 인수인계 형식으로 변환할 수 없습니다."
        ) from exc

    without_key = validated.model_dump(mode="json")
    without_key.pop("idempotency_key", None)
    content_payload_hash = _payload_hash(without_key)
    idempotency_key = _payload_hash(
        {
            "organization_id": str(organization_id),
            "source_record_id": str(record.id),
            "source_version_id": str(version.id),
            "source_version": version.version,
            "source_content_hash": version.content_hash,
            "target_mapping_id": str(mapping.id) if mapping else (
                str(request.target_mapping_id) if request.target_mapping_id else None
            ),
            "target_recipient_id": str(mapping.target_recipient_id) if mapping else None,
            "target_unit_id": str(mapping.target_unit_id) if mapping and mapping.target_unit_id else None,
            "assigned_user_id": str(mapping.assigned_user_id) if mapping and mapping.assigned_user_id else None,
            "content_payload_hash": content_payload_hash,
        }
    )
    final_payload = SmcodiStaffHubHandoverV1.model_validate(
        {**without_key, "idempotency_key": idempotency_key}
    ).model_dump(mode="json")
    return final_payload, _payload_hash(final_payload), idempotency_key


def create_or_get_handover_preview(
    db: Session,
    *,
    actor: User,
    request: SmcodiOutboxPreviewRequest,
) -> SmcodiOutboxWriteResult:
    if actor.role != "admin" and not actor.can_process_records:
        raise PermissionError("SMCODI 연계 대기함 사용 권한이 없습니다.")

    record, version = _load_source(
        db,
        organization_id=actor.organization_id,
        source_record_id=request.source_record_id,
        source_version=request.source_version,
        test_data_scope=_test_data_scope(actor),
    )
    mapping = _load_target_mapping(
        db,
        organization_id=actor.organization_id,
        record=record,
        mapping_id=request.target_mapping_id,
    )
    _downgrade_invalid_ready_previews(
        db,
        conditions=[
            SmcodiHandoverOutbox.organization_id == actor.organization_id,
            SmcodiHandoverOutbox.source_record_id == record.id,
            SmcodiHandoverOutbox.source_version_id == version.id,
        ],
    )
    payload, payload_hash, idempotency_key = _build_payload(
        organization_id=actor.organization_id,
        record=record,
        version=version,
        request=request,
        mapping=mapping,
    )
    existing = db.scalar(
        select(SmcodiHandoverOutbox).where(
            SmcodiHandoverOutbox.organization_id == actor.organization_id,
            SmcodiHandoverOutbox.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return SmcodiOutboxWriteResult(item=existing, created=False)

    issues = _validation_issues(request, record=record, mapping=mapping)
    item = SmcodiHandoverOutbox(
        organization_id=actor.organization_id,
        source_record_id=record.id,
        source_version_id=version.id,
        source_version=version.version,
        source_content_hash=version.content_hash,
        contract_name=SMCODI_HANDOVER_CONTRACT,
        target_mapping_id=mapping.id if mapping else None,
        target_recipient_id=mapping.target_recipient_id if mapping else None,
        target_unit_id=mapping.target_unit_id if mapping else None,
        assigned_user_id=mapping.assigned_user_id if mapping else None,
        status="blocked" if issues else "ready",
        validation_issues=[issue.model_dump(mode="json") for issue in issues],
        payload=payload,
        payload_hash=payload_hash,
        idempotency_key=idempotency_key,
        created_by_user_id=actor.id,
        is_test_data=record.is_test_data,
    )
    try:
        with db.begin_nested():
            db.add(item)
            db.flush()
    except IntegrityError:
        existing = db.scalar(
            select(SmcodiHandoverOutbox).where(
                SmcodiHandoverOutbox.organization_id == actor.organization_id,
                SmcodiHandoverOutbox.idempotency_key == idempotency_key,
            )
        )
        if existing is None:
            raise
        return SmcodiOutboxWriteResult(item=existing, created=False)
    return SmcodiOutboxWriteResult(item=item, created=True)


def _scope_conditions(user: User) -> list[Any]:
    scope = _test_data_scope(user)
    if scope is None:
        return []
    return [SmcodiHandoverOutbox.is_test_data == scope]


def list_handover_previews(
    db: Session,
    *,
    actor: User,
    status: Literal["ready", "blocked"] | None = None,
    source_record_id: UUID | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[SmcodiHandoverOutbox], int]:
    if actor.role != "admin" and not actor.can_process_records:
        raise PermissionError("SMCODI 연계 대기함 조회 권한이 없습니다.")
    conditions: list[Any] = [
        SmcodiHandoverOutbox.organization_id == actor.organization_id,
        *_scope_conditions(actor),
    ]
    _downgrade_invalid_ready_previews(db, conditions=conditions)
    if status is not None:
        conditions.append(SmcodiHandoverOutbox.status == status)
    if source_record_id is not None:
        conditions.append(
            SmcodiHandoverOutbox.source_record_id == source_record_id
        )
    total = int(
        db.scalar(
            select(func.count(SmcodiHandoverOutbox.id)).where(*conditions)
        )
        or 0
    )
    items = list(
        db.scalars(
            select(SmcodiHandoverOutbox)
            .where(*conditions)
            .order_by(
                SmcodiHandoverOutbox.created_at.desc(),
                SmcodiHandoverOutbox.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        ).all()
    )
    return items, total


def get_handover_preview(
    db: Session,
    *,
    actor: User,
    outbox_id: UUID,
) -> SmcodiHandoverOutbox | None:
    if actor.role != "admin" and not actor.can_process_records:
        raise PermissionError("SMCODI 연계 대기함 조회 권한이 없습니다.")
    conditions: list[Any] = [
        SmcodiHandoverOutbox.id == outbox_id,
        SmcodiHandoverOutbox.organization_id == actor.organization_id,
        *_scope_conditions(actor),
    ]
    _downgrade_invalid_ready_previews(db, conditions=conditions)
    return db.scalar(
        select(SmcodiHandoverOutbox).where(
            *conditions,
        )
    )


def _response(item: SmcodiHandoverOutbox) -> SmcodiOutboxResponse:
    return SmcodiOutboxResponse.model_validate(
        {
            "id": item.id,
            "organization_id": item.organization_id,
            "source_record_id": item.source_record_id,
            "source_version_id": item.source_version_id,
            "source_version": item.source_version,
            "source_content_hash": item.source_content_hash,
            "contract_name": item.contract_name,
            "target_mapping_id": item.target_mapping_id,
            "target_recipient_id": item.target_recipient_id,
            "target_unit_id": item.target_unit_id,
            "assigned_user_id": item.assigned_user_id,
            "status": item.status,
            "validation_issues": item.validation_issues,
            "payload": item.payload,
            "payload_hash": item.payload_hash,
            "idempotency_key": item.idempotency_key,
            "created_by_user_id": item.created_by_user_id,
            "is_test_data": item.is_test_data,
            "created_at": item.created_at,
            "updated_at": item.updated_at,
        }
    )


router = APIRouter(prefix="/api/smcodi-outbox", tags=["smcodi-outbox"])


def _set_private_no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "private, no-store"


@router.post("/preview", response_model=SmcodiOutboxPreviewResponse)
def preview_smcodi_handover(
    payload: SmcodiOutboxPreviewRequest,
    response: Response,
    actor: User = Depends(_require_outbox_user),
    db: Session = Depends(get_db),
) -> SmcodiOutboxPreviewResponse:
    _set_private_no_store(response)
    try:
        result = create_or_get_handover_preview(
            db,
            actor=actor,
            request=payload,
        )
        db.commit()
        db.refresh(result.item)
    except SmcodiOutboxNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SmcodiOutboxSourceError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    return SmcodiOutboxPreviewResponse(
        created=result.created,
        item=_response(result.item),
    )


@router.get("", response_model=SmcodiOutboxListResponse)
def list_smcodi_handover_previews(
    response: Response,
    status: Literal["ready", "blocked"] | None = None,
    source_record_id: UUID | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(_require_outbox_user),
    db: Session = Depends(get_db),
) -> SmcodiOutboxListResponse:
    _set_private_no_store(response)
    items, total = list_handover_previews(
        db,
        actor=actor,
        status=status,
        source_record_id=source_record_id,
        limit=limit,
        offset=offset,
    )
    db.commit()
    return SmcodiOutboxListResponse(
        items=[_response(item) for item in items],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{outbox_id}", response_model=SmcodiOutboxResponse)
def get_smcodi_handover_preview(
    outbox_id: UUID,
    response: Response,
    actor: User = Depends(_require_outbox_user),
    db: Session = Depends(get_db),
) -> SmcodiOutboxResponse:
    _set_private_no_store(response)
    item = get_handover_preview(db, actor=actor, outbox_id=outbox_id)
    if item is None:
        raise HTTPException(status_code=404, detail="SMCODI 연계 대기항목을 찾을 수 없습니다.")
    db.commit()
    return _response(item)
