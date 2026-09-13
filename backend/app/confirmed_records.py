from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from .attachment_review_policy import (
    ATTACHMENT_STAFF_REVIEW_REQUIRED_DETAIL,
    attachment_evidence_text,
    required_attachment_reviews_current,
    requires_staff_review,
)
from .attachment_review_policy import has_staff_review
import json
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from .confirmed_record_schemas import (
    ConfirmedRecordContent,
    ConfirmedRecordDetailResponse,
    ConfirmedRecordResponse,
    ConfirmedRecordSearchFilters,
    ConfirmedRecordSearchResponse,
    ConfirmedRecordSourceResponse,
    ConfirmedRecordVersionResponse,
    ConfirmedRecordLifecycle,
    RecordUrgency,
    ServiceContext,
    WorkCategory,
)
from .database import Base, get_db
from .dependencies import get_current_user
from .models import (
    AttachmentTextExtraction,
    JSON_DATA,
    Message,
    MessageAttachment,
    MessageResidentLink,
    Resident,
    User,
    WorkItem,
    utcnow,
    uuid_pk,
)


class ConfirmedWorkRecord(Base):
    __tablename__ = "staff_hub_confirmed_records"
    __table_args__ = (
        CheckConstraint(
            "service_context IN "
            "('facility_care', 'day_care', 'home_visit_care')",
            name="staff_hub_confirmed_records_service_context_check",
        ),
        CheckConstraint(
            "work_category IN "
            "('daily_care', 'nutrition', 'health', 'safety', "
            "'consultation', 'rehabilitation')",
            name="staff_hub_confirmed_records_work_category_check",
        ),
        CheckConstraint(
            "urgency IN ('low', 'medium', 'high', 'urgent')",
            name="staff_hub_confirmed_records_urgency_check",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'withdrawn')",
            name="staff_hub_confirmed_records_lifecycle_check",
        ),
        CheckConstraint(
            "length(btrim(observation_text)) > 0",
            name="staff_hub_confirmed_records_observation_check",
        ),
        CheckConstraint(
            "current_version > 0",
            name="staff_hub_confirmed_records_version_check",
        ),
        UniqueConstraint(
            "work_item_id",
            "recipient_id",
            name="uq_confirmed_record_work_item_recipient",
        ),
        Index(
            "ix_confirmed_records_org_recipient_occurred",
            "organization_id",
            "recipient_id",
            "occurred_at",
        ),
        Index(
            "ix_confirmed_records_org_service_occurred",
            "organization_id",
            "service_context",
            "occurred_at",
        ),
        Index(
            "ix_confirmed_records_org_category_occurred",
            "organization_id",
            "work_category",
            "occurred_at",
        ),
        Index("ix_confirmed_records_work_item", "work_item_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    work_item_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_processing_items.id", ondelete="RESTRICT")
    )
    resident_id: Mapped[UUID] = mapped_column(
        "recipient_id",
        ForeignKey("recipients.id", ondelete="RESTRICT"),
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    service_context: Mapped[str] = mapped_column(String(30))
    work_category: Mapped[str] = mapped_column(String(40))
    observation_text: Mapped[str] = mapped_column(Text)
    action_text: Mapped[str] = mapped_column(Text, default="")
    result_text: Mapped[str] = mapped_column(Text, default="")
    measurement_text: Mapped[str] = mapped_column(Text, default="")
    urgency: Mapped[str] = mapped_column(String(20), default="low")
    needs_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    lifecycle_status: Mapped[str] = mapped_column(String(20), default="active")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    current_content_hash: Mapped[str] = mapped_column(String(64))
    confirmed_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ConfirmedWorkRecordVersion(Base):
    __tablename__ = "staff_hub_confirmed_record_versions"
    __table_args__ = (
        CheckConstraint(
            "version > 0",
            name="staff_hub_confirmed_record_versions_version_check",
        ),
        UniqueConstraint(
            "record_id",
            "version",
            name="uq_confirmed_record_version",
        ),
        UniqueConstraint(
            "record_id",
            "content_hash",
            name="uq_confirmed_record_version_hash",
        ),
        Index(
            "ix_confirmed_record_versions_record_created",
            "record_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    record_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_confirmed_records.id", ondelete="RESTRICT")
    )
    version: Mapped[int] = mapped_column(Integer)
    content_hash: Mapped[str] = mapped_column(String(64))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    change_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ConfirmedWorkRecordSource(Base):
    __tablename__ = "staff_hub_confirmed_record_sources"
    __table_args__ = (
        CheckConstraint(
            "evidence_role IN ('primary', 'supporting')",
            name="staff_hub_confirmed_record_sources_role_check",
        ),
        UniqueConstraint(
            "record_version_id",
            "source_key",
            name="uq_confirmed_record_source_key",
        ),
        Index("ix_confirmed_record_sources_version", "record_version_id"),
        Index(
            "ix_confirmed_record_sources_message",
            "source_message_id",
            "record_version_id",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    record_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_confirmed_record_versions.id", ondelete="RESTRICT")
    )
    source_message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="RESTRICT")
    )
    attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="RESTRICT"), nullable=True
    )
    text_extraction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    evidence_role: Mapped[str] = mapped_column(String(20))
    source_key: Mapped[str] = mapped_column(String(64))
    source_content_hash: Mapped[str] = mapped_column(String(64))
    excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class ConfirmedRecordError(ValueError):
    def __init__(self, message: str, *, code: str = "invalid_confirmed_record"):
        super().__init__(message)
        self.code = code


class ConfirmedRecordHistoryMutationError(RuntimeError):
    pass


@event.listens_for(ConfirmedWorkRecordVersion, "before_update")
@event.listens_for(ConfirmedWorkRecordVersion, "before_delete")
@event.listens_for(ConfirmedWorkRecordSource, "before_update")
@event.listens_for(ConfirmedWorkRecordSource, "before_delete")
def _reject_history_mutation(*_args: Any, **_kwargs: Any) -> None:
    raise ConfirmedRecordHistoryMutationError(
        "확정 업무기록 이력은 수정하거나 삭제할 수 없습니다. 새 버전을 추가해 주세요."
    )


@dataclass(frozen=True)
class ConfirmedRecordEvidence:
    source_message_id: UUID
    attachment_id: UUID | None
    text_extraction_id: UUID | None
    evidence_role: str
    source_key: str
    source_content_hash: str
    excerpt: str | None


@dataclass(frozen=True)
class ConfirmedRecordWriteResult:
    record: ConfirmedWorkRecord
    version: ConfirmedWorkRecordVersion
    created: bool
    appended: bool


@dataclass(frozen=True)
class ConfirmedRecordSearchHit:
    record: ConfirmedWorkRecord
    resident_name: str


@dataclass(frozen=True)
class ConfirmedRecordSearchPage:
    items: list[ConfirmedRecordSearchHit]
    total: int
    limit: int
    offset: int


SERVICE_CONTEXT_ALIASES: dict[str, str] = {
    "facility": "facility_care",
    "facility_care": "facility_care",
    "daycare": "day_care",
    "day_care": "day_care",
    "homecare": "home_visit_care",
    "home_visit_care": "home_visit_care",
}


def normalize_service_context(value: str) -> str:
    normalized = SERVICE_CONTEXT_ALIASES.get(value.strip().lower())
    if normalized is None:
        raise ConfirmedRecordError(
            "서비스 구분은 시설·주간보호·방문요양 중 하나여야 합니다.",
            code="invalid_service_context",
        )
    return normalized


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ConfirmedRecordError(
            "업무 발생 시각에는 시간대 정보가 필요합니다.",
            code="naive_occurred_at",
        )
    return value.astimezone(timezone.utc)


def _iso_utc(value: datetime) -> str:
    return _utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _source_key(
    *,
    message_id: UUID,
    attachment_id: UUID | None,
    extraction_id: UUID | None,
) -> str:
    return _payload_hash(
        {
            "message_id": str(message_id),
            "attachment_id": str(attachment_id) if attachment_id else None,
            "text_extraction_id": str(extraction_id) if extraction_id else None,
        }
    )


def _message_content_hash(message: Message) -> str:
    return _payload_hash(
        {
            "message_id": str(message.id),
            "body": message.body,
            "created_at": _iso_utc(message.created_at),
            "edited_at": _iso_utc(message.edited_at) if message.edited_at else None,
        }
    )


def _attachment_content_hash(
    attachment: MessageAttachment,
    extraction: AttachmentTextExtraction | None,
) -> str:
    return _payload_hash(
        {
            "attachment_id": str(attachment.id),
            "sha256": attachment.sha256,
            "size_bytes": attachment.size_bytes,
            "content_type": attachment.mime_type,
            "text_extraction_id": str(extraction.id) if extraction else None,
            "reviewed_text": extraction.reviewed_text if extraction else None,
            "extracted_text": extraction.extracted_text if extraction else None,
        }
    )


def _load_active_evidence(
    db: Session,
    *,
    organization_id: UUID,
    primary_message_id: UUID,
    source_message_ids: Sequence[UUID],
) -> list[ConfirmedRecordEvidence]:
    unique_ids = set(source_message_ids)
    unique_ids.add(primary_message_id)
    ordered_ids = [
        primary_message_id,
        *sorted(
            (message_id for message_id in unique_ids if message_id != primary_message_id),
            key=str,
        ),
    ]
    messages = db.scalars(
        select(Message).where(Message.id.in_(ordered_ids))
    ).all()
    messages_by_id = {message.id: message for message in messages}
    missing_ids = [message_id for message_id in ordered_ids if message_id not in messages_by_id]
    if missing_ids:
        raise ConfirmedRecordError(
            "근거 메시지를 찾을 수 없습니다.",
            code="missing_source_message",
        )

    for message in messages:
        if message.organization_id != organization_id:
            raise ConfirmedRecordError(
                "다른 기관의 메시지는 근거로 연결할 수 없습니다.",
                code="cross_organization_source",
            )
        if message.lifecycle_status == "recalled":
            raise ConfirmedRecordError(
                "회수한 메시지는 새 확정 업무기록의 근거로 사용할 수 없습니다.",
                code="recalled_source_message",
            )

    attachment_rows = db.scalars(
        select(MessageAttachment)
        .where(MessageAttachment.message_id.in_(ordered_ids))
        .order_by(MessageAttachment.created_at, MessageAttachment.id)
    ).all()
    attachment_ids = [attachment.id for attachment in attachment_rows]
    extraction_by_attachment: dict[UUID, AttachmentTextExtraction] = {}
    if attachment_ids:
        extraction_rows = db.scalars(
            select(AttachmentTextExtraction).where(
                AttachmentTextExtraction.attachment_id.in_(attachment_ids)
            )
        ).all()
        extraction_by_attachment = {
            extraction.attachment_id: extraction for extraction in extraction_rows
        }
    attachments_by_message: dict[UUID, list[MessageAttachment]] = {}
    for attachment in attachment_rows:
        attachments_by_message.setdefault(attachment.message_id, []).append(attachment)

    evidence: list[ConfirmedRecordEvidence] = []
    for message_id in ordered_ids:
        message = messages_by_id[message_id]
        role = "primary" if message_id == primary_message_id else "supporting"
        evidence.append(
            ConfirmedRecordEvidence(
                source_message_id=message.id,
                attachment_id=None,
                text_extraction_id=None,
                evidence_role=role,
                source_key=_source_key(
                    message_id=message.id,
                    attachment_id=None,
                    extraction_id=None,
                ),
                source_content_hash=_message_content_hash(message),
                excerpt=message.body.strip()[:4000] or None,
            )
        )
        for attachment in attachments_by_message.get(message.id, []):
            extraction = extraction_by_attachment.get(attachment.id)
            extracted_text = attachment_evidence_text(attachment, extraction)
            if requires_staff_review(attachment) and not extracted_text:
                continue
            evidence.append(
                ConfirmedRecordEvidence(
                    source_message_id=message.id,
                    attachment_id=attachment.id,
                    text_extraction_id=extraction.id if extraction else None,
                    evidence_role="supporting",
                    source_key=_source_key(
                        message_id=message.id,
                        attachment_id=attachment.id,
                        extraction_id=extraction.id if extraction else None,
                    ),
                    source_content_hash=_attachment_content_hash(
                        attachment,
                        extraction,
                    ),
                    excerpt=(extracted_text.strip()[:4000] or None)
                    if extracted_text
                    else None,
                )
            )
    return evidence


def _payload_text(
    payload: Mapping[str, Any],
    *keys: str,
    join_lists: bool = False,
) -> str:
    for key in keys:
        value = payload.get(key)
        if value is None:
            continue
        if join_lists and isinstance(value, Sequence) and not isinstance(value, str):
            return "\n".join(str(item).strip() for item in value if str(item).strip())
        text_value = str(value).strip()
        if text_value:
            return text_value
    return ""


def _payload_follow_up(payload: Mapping[str, Any]) -> bool:
    for key in ("needs_follow_up", "follow_up"):
        if key not in payload:
            continue
        value = payload[key]
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)
    questions = payload.get("verification_questions")
    return bool(questions)


def _resident_ids_for_work_item(
    db: Session,
    *,
    work_item: WorkItem,
    resident_ids: Sequence[UUID] | None,
) -> list[UUID]:
    links = db.scalars(
        select(MessageResidentLink)
        .where(MessageResidentLink.message_id == work_item.source_message_id)
        .order_by(MessageResidentLink.created_at, MessageResidentLink.id)
    ).all()
    if any(link.status == "candidate" for link in links):
        raise ConfirmedRecordError(
            "확정되지 않은 어르신 후보가 남아 있습니다.",
            code="pending_resident_candidates",
        )
    linked_ids = [link.resident_id for link in links if link.status == "confirmed"]

    if resident_ids is None:
        selected_ids = linked_ids or (
            [work_item.resident_id] if work_item.resident_id is not None else []
        )
    else:
        selected_ids = list(dict.fromkeys(resident_ids))
        allowed_ids = set(linked_ids)
        if not allowed_ids and work_item.resident_id is not None:
            allowed_ids.add(work_item.resident_id)
        if not set(selected_ids).issubset(allowed_ids):
            raise ConfirmedRecordError(
                "업무 원문에 확정 연결되지 않은 어르신이 포함되어 있습니다.",
                code="unlinked_resident",
            )

    if not selected_ids:
        raise ConfirmedRecordError(
            "확정 업무기록에 연결할 어르신이 없습니다.",
            code="missing_resident",
        )
    return selected_ids


def _snapshot(
    *,
    work_item_id: UUID,
    resident_id: UUID,
    content: ConfirmedRecordContent,
    evidence: Sequence[ConfirmedRecordEvidence],
) -> dict[str, Any]:
    return {
        "work_item_id": str(work_item_id),
        "resident_id": str(resident_id),
        "occurred_at": _iso_utc(content.occurred_at),
        "service_context": content.service_context,
        "work_category": content.work_category,
        "observation_text": content.observation_text,
        "action_text": content.action_text,
        "result_text": content.result_text,
        "measurement_text": content.measurement_text,
        "urgency": content.urgency,
        "needs_follow_up": content.needs_follow_up,
        "evidence": [
            {
                "source_message_id": str(item.source_message_id),
                "attachment_id": str(item.attachment_id) if item.attachment_id else None,
                "text_extraction_id": (
                    str(item.text_extraction_id) if item.text_extraction_id else None
                ),
                "evidence_role": item.evidence_role,
                "source_key": item.source_key,
                "source_content_hash": item.source_content_hash,
            }
            for item in evidence
        ],
    }


def _create_or_append_record(
    db: Session,
    *,
    work_item: WorkItem,
    resident: Resident,
    content: ConfirmedRecordContent,
    evidence: Sequence[ConfirmedRecordEvidence],
    confirmed_by_user_id: UUID,
    change_reason: str | None,
) -> ConfirmedRecordWriteResult:
    snapshot = _snapshot(
        work_item_id=work_item.id,
        resident_id=resident.id,
        content=content,
        evidence=evidence,
    )
    content_hash = _payload_hash(snapshot)
    record = db.scalar(
        select(ConfirmedWorkRecord)
        .where(
            ConfirmedWorkRecord.work_item_id == work_item.id,
            ConfirmedWorkRecord.resident_id == resident.id,
        )
        .with_for_update()
    )
    if record is not None and record.current_content_hash == content_hash:
        current = db.scalar(
            select(ConfirmedWorkRecordVersion).where(
                ConfirmedWorkRecordVersion.record_id == record.id,
                ConfirmedWorkRecordVersion.version == record.current_version,
            )
        )
        if current is None:
            raise ConfirmedRecordError(
                "현재 확정기록 버전을 찾을 수 없습니다.",
                code="missing_current_version",
            )
        return ConfirmedRecordWriteResult(
            record=record,
            version=current,
            created=False,
            appended=False,
        )

    now = utcnow()
    created = record is None
    version_number = 1 if record is None else record.current_version + 1
    if record is None:
        record = ConfirmedWorkRecord(
            organization_id=work_item.organization_id,
            work_item_id=work_item.id,
            resident_id=resident.id,
            occurred_at=_utc(content.occurred_at),
            service_context=content.service_context,
            work_category=content.work_category,
            observation_text=content.observation_text,
            action_text=content.action_text,
            result_text=content.result_text,
            measurement_text=content.measurement_text,
            urgency=content.urgency,
            needs_follow_up=content.needs_follow_up,
            lifecycle_status="active",
            current_version=version_number,
            current_content_hash=content_hash,
            confirmed_by_user_id=confirmed_by_user_id,
            confirmed_at=now,
            is_test_data=work_item.is_test_data,
        )
        db.add(record)
        db.flush()
    else:
        record.occurred_at = _utc(content.occurred_at)
        record.service_context = content.service_context
        record.work_category = content.work_category
        record.observation_text = content.observation_text
        record.action_text = content.action_text
        record.result_text = content.result_text
        record.measurement_text = content.measurement_text
        record.urgency = content.urgency
        record.needs_follow_up = content.needs_follow_up
        record.lifecycle_status = "active"
        record.current_version = version_number
        record.current_content_hash = content_hash
        record.confirmed_by_user_id = confirmed_by_user_id
        record.confirmed_at = now
        record.is_test_data = work_item.is_test_data
        record.updated_at = now

    version = ConfirmedWorkRecordVersion(
        organization_id=work_item.organization_id,
        record_id=record.id,
        version=version_number,
        content_hash=content_hash,
        snapshot=snapshot,
        change_reason=change_reason,
        created_by_user_id=confirmed_by_user_id,
    )
    db.add(version)
    db.flush()
    for item in evidence:
        db.add(
            ConfirmedWorkRecordSource(
                organization_id=work_item.organization_id,
                record_version_id=version.id,
                source_message_id=item.source_message_id,
                attachment_id=item.attachment_id,
                text_extraction_id=item.text_extraction_id,
                evidence_role=item.evidence_role,
                source_key=item.source_key,
                source_content_hash=item.source_content_hash,
                excerpt=item.excerpt,
            )
        )
    db.flush()
    return ConfirmedRecordWriteResult(
        record=record,
        version=version,
        created=created,
        appended=True,
    )


def persist_confirmed_records_from_work_item(
    db: Session,
    *,
    work_item: WorkItem,
    confirmed_payload: Mapping[str, Any],
    confirmed_by_user_id: UUID,
    occurred_at: datetime | None = None,
    service_context: str | None = None,
    resident_ids: Sequence[UUID] | None = None,
    source_message_ids: Sequence[UUID] | None = None,
    change_reason: str | None = "업무함 최종 확인",
) -> list[ConfirmedRecordWriteResult]:
    """기존 WorkItem 최종확인 값을 어르신별 확정 업무기록으로 저장한다.

    이 함수는 커밋하지 않는다. 호출 중 오류가 발생하면 기존 WorkItem 확인과
    같은 거래에서 전체를 되돌릴 수 있도록 호출자가 커밋을 담당한다.
    """

    if change_reason is not None:
        change_reason = change_reason.strip() or None
        if change_reason is not None and len(change_reason) > 500:
            raise ConfirmedRecordError(
                "변경 사유는 500자 이하여야 합니다.",
                code="change_reason_too_long",
            )

    primary_message = db.get(Message, work_item.source_message_id)
    if (
        primary_message is None
        or primary_message.organization_id != work_item.organization_id
    ):
        raise ConfirmedRecordError(
            "업무 원문 메시지를 찾을 수 없습니다.",
            code="missing_source_message",
        )
    if not required_attachment_reviews_current(db, primary_message.attachments):
        raise ConfirmedRecordError(
            ATTACHMENT_STAFF_REVIEW_REQUIRED_DETAIL,
            code="attachment_staff_review_required",
        )
    evidence = _load_active_evidence(
        db,
        organization_id=work_item.organization_id,
        primary_message_id=work_item.source_message_id,
        source_message_ids=source_message_ids or [work_item.source_message_id],
    )
    selected_resident_ids = _resident_ids_for_work_item(
        db,
        work_item=work_item,
        resident_ids=resident_ids,
    )
    residents = db.scalars(
        select(Resident).where(Resident.id.in_(selected_resident_ids))
    ).all()
    residents_by_id = {resident.id: resident for resident in residents}
    if len(residents_by_id) != len(selected_resident_ids):
        raise ConfirmedRecordError(
            "확정 업무기록에 연결할 어르신을 찾을 수 없습니다.",
            code="missing_resident",
        )

    occurred_value = (
        occurred_at
        or confirmed_payload.get("occurred_at")
        or primary_message.created_at
    )
    work_category = confirmed_payload.get("work_category") or confirmed_payload.get(
        "classification"
    )
    observation_text = _payload_text(
        confirmed_payload,
        "observation_text",
        "observation_details",
        "corrected_text",
    )
    action_text = _payload_text(
        confirmed_payload,
        "action_text",
        "actions_taken",
        join_lists=True,
    )
    result_text = _payload_text(
        confirmed_payload,
        "result_text",
        "resident_response",
    )
    measurement_text = _payload_text(confirmed_payload, "measurement_text")
    urgency = confirmed_payload.get("urgency") or confirmed_payload.get(
        "risk_level", "low"
    )
    needs_follow_up = _payload_follow_up(confirmed_payload)

    plans: list[tuple[Resident, ConfirmedRecordContent]] = []
    for resident_id in selected_resident_ids:
        resident = residents_by_id[resident_id]
        if resident.organization_id != work_item.organization_id:
            raise ConfirmedRecordError(
                "다른 기관의 어르신은 확정 업무기록에 연결할 수 없습니다.",
                code="cross_organization_resident",
            )
        resident_service = normalize_service_context(resident.service_type)
        normalized_service = (
            normalize_service_context(service_context)
            if service_context is not None
            else resident_service
        )
        if normalized_service != resident_service:
            raise ConfirmedRecordError(
                "어르신의 서비스 구분과 업무기록의 서비스 구분이 다릅니다.",
                code="service_context_mismatch",
            )
        try:
            content = ConfirmedRecordContent.model_validate(
                {
                    "occurred_at": occurred_value,
                    "service_context": normalized_service,
                    "work_category": work_category,
                    "observation_text": observation_text,
                    "action_text": action_text,
                    "result_text": result_text,
                    "measurement_text": measurement_text,
                    "urgency": urgency,
                    "needs_follow_up": needs_follow_up,
                }
            )
        except ValidationError as exc:
            raise ConfirmedRecordError(
                "확정 업무기록의 발생시각·업무분류·기록 내용을 확인해 주세요.",
                code="invalid_record_content",
            ) from exc
        plans.append((resident, content))

    return [
        _create_or_append_record(
            db,
            work_item=work_item,
            resident=resident,
            content=content,
            evidence=evidence,
            confirmed_by_user_id=confirmed_by_user_id,
            change_reason=change_reason,
        )
        for resident, content in plans
    ]


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search_confirmed_records(
    db: Session,
    *,
    organization_id: UUID,
    filters: ConfirmedRecordSearchFilters,
) -> ConfirmedRecordSearchPage:
    """메모리 선별 없이 데이터베이스 조건과 본문 검색을 직접 실행한다."""

    conditions = [ConfirmedWorkRecord.organization_id == organization_id]
    # Historical automatic transcripts must not re-enter via signed-record search.
    # Keep the stored records intact; hide sources until an explicit review exists.
    from sqlalchemy import and_, not_
    unsafe_source = (select(ConfirmedWorkRecordSource.id)
        .join(ConfirmedWorkRecordVersion, ConfirmedWorkRecordVersion.id == ConfirmedWorkRecordSource.record_version_id)
        .join(MessageAttachment, MessageAttachment.id == ConfirmedWorkRecordSource.attachment_id)
        .outerjoin(AttachmentTextExtraction, AttachmentTextExtraction.attachment_id == MessageAttachment.id)
        .where(ConfirmedWorkRecordVersion.record_id == ConfirmedWorkRecord.id,
            not_(or_(MessageAttachment.mime_type.like("image/%"), MessageAttachment.mime_type.like("video/%"))),
            or_(AttachmentTextExtraction.id.is_(None),
                AttachmentTextExtraction.status.notin_(["reviewed", "completed"]),
                AttachmentTextExtraction.reviewed_by_id.is_(None), AttachmentTextExtraction.reviewed_at.is_(None),
                AttachmentTextExtraction.reviewed_text.is_(None), func.length(func.trim(AttachmentTextExtraction.reviewed_text)) == 0,
                AttachmentTextExtraction.reviewed_at > ConfirmedWorkRecordVersion.created_at)).exists())
    conditions.append(~unsafe_source)
    if filters.resident_id is not None:
        conditions.append(ConfirmedWorkRecord.resident_id == filters.resident_id)
    if filters.service_context is not None:
        conditions.append(
            ConfirmedWorkRecord.service_context == filters.service_context
        )
    if filters.work_category is not None:
        conditions.append(ConfirmedWorkRecord.work_category == filters.work_category)
    if filters.urgency is not None:
        conditions.append(ConfirmedWorkRecord.urgency == filters.urgency)
    if filters.needs_follow_up is not None:
        conditions.append(
            ConfirmedWorkRecord.needs_follow_up == filters.needs_follow_up
        )
    if filters.lifecycle_status is not None:
        conditions.append(
            ConfirmedWorkRecord.lifecycle_status == filters.lifecycle_status
        )
    if filters.occurred_from is not None:
        conditions.append(
            ConfirmedWorkRecord.occurred_at >= _utc(filters.occurred_from)
        )
    if filters.occurred_to is not None:
        conditions.append(
            ConfirmedWorkRecord.occurred_at <= _utc(filters.occurred_to)
        )
    if filters.is_test_data is not None:
        conditions.append(ConfirmedWorkRecord.is_test_data == filters.is_test_data)
    if filters.q:
        pattern = f"%{_escape_like(filters.q)}%"
        conditions.append(
            or_(
                Resident.display_name.ilike(pattern, escape="\\"),
                ConfirmedWorkRecord.observation_text.ilike(pattern, escape="\\"),
                ConfirmedWorkRecord.action_text.ilike(pattern, escape="\\"),
                ConfirmedWorkRecord.result_text.ilike(pattern, escape="\\"),
                ConfirmedWorkRecord.measurement_text.ilike(pattern, escape="\\"),
            )
        )

    total = int(
        db.scalar(
            select(func.count(ConfirmedWorkRecord.id))
            .join(Resident, Resident.id == ConfirmedWorkRecord.resident_id)
            .where(*conditions)
        )
        or 0
    )
    rows = db.execute(
        select(ConfirmedWorkRecord, Resident.display_name)
        .join(Resident, Resident.id == ConfirmedWorkRecord.resident_id)
        .where(*conditions)
        .order_by(ConfirmedWorkRecord.occurred_at.desc(), ConfirmedWorkRecord.id.desc())
        .limit(filters.limit)
        .offset(filters.offset)
    ).all()
    return ConfirmedRecordSearchPage(
        items=[
            ConfirmedRecordSearchHit(record=record, resident_name=resident_name)
            for record, resident_name in rows
        ],
        total=total,
        limit=filters.limit,
        offset=filters.offset,
    )


def _record_response(
    record: ConfirmedWorkRecord,
    *,
    resident_name: str,
) -> ConfirmedRecordResponse:
    return ConfirmedRecordResponse(
        id=record.id,
        organization_id=record.organization_id,
        work_item_id=record.work_item_id,
        resident_id=record.resident_id,
        resident_name=resident_name,
        occurred_at=record.occurred_at,
        service_context=record.service_context,
        work_category=record.work_category,
        observation_text=record.observation_text,
        action_text=record.action_text,
        result_text=record.result_text,
        measurement_text=record.measurement_text,
        urgency=record.urgency,
        needs_follow_up=record.needs_follow_up,
        lifecycle_status=record.lifecycle_status,
        current_version=record.current_version,
        current_content_hash=record.current_content_hash,
        confirmed_by_user_id=record.confirmed_by_user_id,
        confirmed_at=record.confirmed_at,
        is_test_data=record.is_test_data,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def load_confirmed_record_detail(
    db: Session,
    *,
    organization_id: UUID,
    record_id: UUID,
) -> ConfirmedRecordDetailResponse | None:
    row = db.execute(
        select(ConfirmedWorkRecord, Resident.display_name)
        .join(Resident, Resident.id == ConfirmedWorkRecord.resident_id)
        .where(
            ConfirmedWorkRecord.id == record_id,
            ConfirmedWorkRecord.organization_id == organization_id,
        )
    ).one_or_none()
    if row is None:
        return None
    record, resident_name = row
    versions = db.scalars(
        select(ConfirmedWorkRecordVersion)
        .where(ConfirmedWorkRecordVersion.record_id == record.id)
        .order_by(ConfirmedWorkRecordVersion.version.desc())
    ).all()
    version_ids = [version.id for version in versions]
    sources = (
        db.scalars(
            select(ConfirmedWorkRecordSource)
            .where(ConfirmedWorkRecordSource.record_version_id.in_(version_ids))
            .order_by(
                ConfirmedWorkRecordSource.created_at,
                ConfirmedWorkRecordSource.id,
            )
        ).all()
        if version_ids
        else []
    )
    sources_by_version: dict[UUID, list[ConfirmedWorkRecordSource]] = {}
    for source in sources:
        if source.attachment_id is not None:
            attachment = db.get(MessageAttachment, source.attachment_id)
            if attachment is not None and requires_staff_review(attachment):
                extraction = attachment.text_extraction
                if not has_staff_review(extraction) or source.source_content_hash != _attachment_content_hash(attachment, extraction):
                    raise HTTPException(409, "근거의 직원 확인 상태가 변경되었습니다. 원본 확인 후 기록 담당자에게 정정을 요청해 주세요.")
        sources_by_version.setdefault(source.record_version_id, []).append(source)

    base = _record_response(record, resident_name=resident_name)
    return ConfirmedRecordDetailResponse(
        **base.model_dump(),
        versions=[
            ConfirmedRecordVersionResponse(
                id=version.id,
                version=version.version,
                content_hash=version.content_hash,
                snapshot=version.snapshot,
                change_reason=version.change_reason,
                created_by_user_id=version.created_by_user_id,
                created_at=version.created_at,
                sources=[
                    ConfirmedRecordSourceResponse(
                        id=source.id,
                        record_version_id=source.record_version_id,
                        source_message_id=source.source_message_id,
                        attachment_id=source.attachment_id,
                        text_extraction_id=source.text_extraction_id,
                        evidence_role=source.evidence_role,
                        source_key=source.source_key,
                        source_content_hash=source.source_content_hash,
                        excerpt=source.excerpt,
                        created_at=source.created_at,
                    )
                    for source in sources_by_version.get(version.id, [])
                ],
            )
            for version in versions
        ],
    )


def _require_confirmed_record_reader(
    user: User = Depends(get_current_user),
) -> User:
    if user.role != "admin" and not user.can_process_records:
        raise HTTPException(status_code=403, detail="확정 업무기록 조회 권한이 없습니다.")
    return user


router = APIRouter(prefix="/api/confirmed-records", tags=["confirmed-records"])


@router.get("", response_model=ConfirmedRecordSearchResponse)
def list_confirmed_records(
    q: str | None = Query(default=None, max_length=200),
    resident_id: UUID | None = None,
    service_context: ServiceContext | None = None,
    work_category: WorkCategory | None = None,
    urgency: RecordUrgency | None = None,
    needs_follow_up: bool | None = None,
    lifecycle_status: ConfirmedRecordLifecycle | None = "active",
    occurred_from: datetime | None = None,
    occurred_to: datetime | None = None,
    limit: int = Query(default=100, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    processor: User = Depends(_require_confirmed_record_reader),
    db: Session = Depends(get_db),
) -> ConfirmedRecordSearchResponse:
    filters = ConfirmedRecordSearchFilters(
        q=q,
        resident_id=resident_id,
        service_context=service_context,
        work_category=work_category,
        urgency=urgency,
        needs_follow_up=needs_follow_up,
        lifecycle_status=lifecycle_status,
        occurred_from=occurred_from,
        occurred_to=occurred_to,
        is_test_data=(
            True if getattr(processor, "_reviewer_experience", None) is not None else None
        ),
        limit=limit,
        offset=offset,
    )
    page = search_confirmed_records(
        db,
        organization_id=processor.organization_id,
        filters=filters,
    )
    return ConfirmedRecordSearchResponse(
        items=[
            _record_response(hit.record, resident_name=hit.resident_name)
            for hit in page.items
        ],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/{record_id}", response_model=ConfirmedRecordDetailResponse)
def get_confirmed_record(
    record_id: UUID,
    processor: User = Depends(_require_confirmed_record_reader),
    db: Session = Depends(get_db),
) -> ConfirmedRecordDetailResponse:
    detail = load_confirmed_record_detail(
        db,
        organization_id=processor.organization_id,
        record_id=record_id,
    )
    if detail is None or (
        getattr(processor, "_reviewer_experience", None) is not None
        and not detail.is_test_data
    ):
        raise HTTPException(status_code=404, detail="확정 업무기록을 찾을 수 없습니다.")
    return detail
