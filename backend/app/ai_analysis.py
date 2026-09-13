from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    func,
    select,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .database import Base
from .models import JSON_DATA, utcnow, uuid_pk


class AiAnalysisSnapshot(Base):
    """원문을 바꾸지 않고 추가하는 AI·규칙 분석의 불변 스냅샷."""

    __tablename__ = "ai_analysis_snapshots"
    __table_args__ = (
        CheckConstraint(
            "subject_type IN ('message', 'care_incident', 'care_briefing')",
            name="ai_analysis_snapshots_subject_type_check",
        ),
        CheckConstraint(
            "processor_kind IN ('local_rules', 'external_model', 'admin_control', "
            "'linked_action', 'system_fallback')",
            name="ai_analysis_snapshots_processor_kind_check",
        ),
        CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ai_analysis_snapshots_status_check",
        ),
        CheckConstraint(
            "sequence_no > 0",
            name="ai_analysis_snapshots_sequence_check",
        ),
        CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ai_analysis_snapshots_confidence_check",
        ),
        UniqueConstraint(
            "organization_id",
            "subject_type",
            "subject_key",
            "analysis_kind",
            "sequence_no",
            name="uq_ai_analysis_snapshot_sequence",
        ),
        UniqueConstraint(
            "organization_id",
            "analysis_fingerprint",
            name="uq_ai_analysis_snapshot_fingerprint",
        ),
        Index(
            "ix_ai_analysis_snapshots_subject",
            "organization_id",
            "subject_type",
            "subject_key",
            "analysis_kind",
            "created_at",
        ),
        Index(
            "ix_ai_analysis_snapshots_kind_created",
            "organization_id",
            "analysis_kind",
            "created_at",
        ),
        Index("ix_ai_analysis_snapshots_primary_message", "primary_message_id"),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    subject_type: Mapped[str] = mapped_column(String(30))
    subject_key: Mapped[str] = mapped_column(String(160))
    primary_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="RESTRICT"),
        nullable=True,
    )
    analysis_kind: Mapped[str] = mapped_column(String(60))
    sequence_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="completed")
    processor_kind: Mapped[str] = mapped_column(String(30))
    processor_version: Mapped[str] = mapped_column(String(80))
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    model_name: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    transmitted_external: Mapped[bool] = mapped_column(Boolean, default=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    reason_codes: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    uncertainties: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    input_fingerprint: Mapped[str] = mapped_column(String(64))
    analysis_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    evidence: Mapped[list[AiAnalysisEvidence]] = relationship(
        back_populates="snapshot",
        cascade="all, delete-orphan",
        order_by="AiAnalysisEvidence.ordinal",
    )


class AiAnalysisEvidence(Base):
    """분석 근거의 위치와 해시만 보존하고 원문을 복제하지 않는다."""

    __tablename__ = "ai_analysis_evidence"
    __table_args__ = (
        CheckConstraint(
            "source_type IN ('message', 'comment', 'attachment', 'text_extraction')",
            name="ai_analysis_evidence_source_type_check",
        ),
        CheckConstraint(
            "evidence_role IN ('primary', 'supporting')",
            name="ai_analysis_evidence_role_check",
        ),
        CheckConstraint("ordinal > 0", name="ai_analysis_evidence_ordinal_check"),
        CheckConstraint(
            "(source_type = 'message' AND source_message_id IS NOT NULL "
            "AND source_comment_id IS NULL AND source_attachment_id IS NULL "
            "AND source_extraction_id IS NULL) OR "
            "(source_type = 'comment' AND source_comment_id IS NOT NULL "
            "AND source_message_id IS NOT NULL AND source_attachment_id IS NULL "
            "AND source_extraction_id IS NULL) OR "
            "(source_type = 'attachment' AND source_attachment_id IS NOT NULL "
            "AND source_message_id IS NOT NULL AND source_comment_id IS NULL "
            "AND source_extraction_id IS NULL) OR "
            "(source_type = 'text_extraction' AND source_extraction_id IS NOT NULL "
            "AND source_attachment_id IS NOT NULL AND source_message_id IS NOT NULL "
            "AND source_comment_id IS NULL)",
            name="ai_analysis_evidence_source_reference_check",
        ),
        UniqueConstraint(
            "snapshot_id",
            "ordinal",
            name="uq_ai_analysis_evidence_ordinal",
        ),
        Index("ix_ai_analysis_evidence_message", "source_message_id", "snapshot_id"),
        Index("ix_ai_analysis_evidence_comment", "source_comment_id", "snapshot_id"),
        Index(
            "ix_ai_analysis_evidence_attachment",
            "source_attachment_id",
            "snapshot_id",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    snapshot_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_analysis_snapshots.id", ondelete="RESTRICT")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(30))
    evidence_role: Mapped[str] = mapped_column(String(20), default="supporting")
    source_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_comment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_message_comments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    source_extraction_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachment_text_extractions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    content_sha256: Mapped[str] = mapped_column(String(64))
    source_meta: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    snapshot: Mapped[AiAnalysisSnapshot] = relationship(back_populates="evidence")


class AiAnalysisHistoryMutationError(RuntimeError):
    pass


@event.listens_for(AiAnalysisSnapshot, "before_update")
@event.listens_for(AiAnalysisSnapshot, "before_delete")
@event.listens_for(AiAnalysisEvidence, "before_update")
@event.listens_for(AiAnalysisEvidence, "before_delete")
def _reject_analysis_history_mutation(*_args: Any, **_kwargs: Any) -> None:
    raise AiAnalysisHistoryMutationError(
        "AI 분석 이력은 수정하거나 삭제할 수 없습니다. 새 스냅샷을 추가해 주세요."
    )


@dataclass(frozen=True)
class AiAnalysisEvidenceInput:
    source_type: str
    content_sha256: str
    source_message_id: UUID | None = None
    source_comment_id: UUID | None = None
    source_attachment_id: UUID | None = None
    source_extraction_id: UUID | None = None
    evidence_role: str = "supporting"
    source_meta: Mapping[str, Any] | None = None
    included_in_prompt: bool = False


def content_fingerprint(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _canonical_fingerprint(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return content_fingerprint(serialized)


def append_ai_analysis_snapshot(
    db: Session,
    *,
    organization_id: UUID,
    subject_type: str,
    subject_key: str,
    analysis_kind: str,
    processor_kind: str,
    processor_version: str,
    input_payload: Mapping[str, Any],
    result_payload: Mapping[str, Any],
    evidence: Sequence[AiAnalysisEvidenceInput],
    primary_message_id: UUID | None = None,
    status: str = "completed",
    confidence: float | None = None,
    reason_codes: Sequence[str] = (),
    uncertainties: Sequence[str] = (),
    provider: str | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    transmitted_external: bool = False,
    created_by_user_id: UUID | None = None,
    is_test_data: bool = False,
) -> AiAnalysisSnapshot:
    """동일 입력·처리기 결과는 재사용하고 변경된 판단은 새 이력으로 추가한다."""

    input_hash = _canonical_fingerprint(input_payload)
    analysis_hash = _canonical_fingerprint(
        {
            "organization_id": organization_id,
            "subject_type": subject_type,
            "subject_key": subject_key,
            "analysis_kind": analysis_kind,
            "processor_kind": processor_kind,
            "processor_version": processor_version,
            "provider": provider,
            "model_name": model_name,
            "prompt_version": prompt_version,
            "input_fingerprint": input_hash,
            "result_payload": result_payload,
            "reason_codes": list(reason_codes),
            "uncertainties": list(uncertainties),
        }
    )
    existing = db.scalar(
        select(AiAnalysisSnapshot).where(
            AiAnalysisSnapshot.organization_id == organization_id,
            AiAnalysisSnapshot.analysis_fingerprint == analysis_hash,
        )
    )
    if existing is not None:
        return existing

    sequence_no = int(
        db.scalar(
            select(func.coalesce(func.max(AiAnalysisSnapshot.sequence_no), 0)).where(
                AiAnalysisSnapshot.organization_id == organization_id,
                AiAnalysisSnapshot.subject_type == subject_type,
                AiAnalysisSnapshot.subject_key == subject_key,
                AiAnalysisSnapshot.analysis_kind == analysis_kind,
            )
        )
        or 0
    ) + 1
    snapshot = AiAnalysisSnapshot(
        organization_id=organization_id,
        subject_type=subject_type,
        subject_key=subject_key,
        primary_message_id=primary_message_id,
        analysis_kind=analysis_kind,
        sequence_no=sequence_no,
        status=status,
        processor_kind=processor_kind,
        processor_version=processor_version,
        provider=provider,
        model_name=model_name,
        prompt_version=prompt_version,
        transmitted_external=transmitted_external,
        confidence=confidence,
        result_payload=dict(result_payload),
        reason_codes=list(reason_codes),
        uncertainties=list(uncertainties),
        input_fingerprint=input_hash,
        analysis_fingerprint=analysis_hash,
        created_by_user_id=created_by_user_id,
        is_test_data=is_test_data,
    )
    db.add(snapshot)
    db.flush()
    for ordinal, item in enumerate(evidence, start=1):
        db.add(
            AiAnalysisEvidence(
                organization_id=organization_id,
                snapshot_id=snapshot.id,
                ordinal=ordinal,
                source_type=item.source_type,
                evidence_role=item.evidence_role,
                source_message_id=item.source_message_id,
                source_comment_id=item.source_comment_id,
                source_attachment_id=item.source_attachment_id,
                source_extraction_id=item.source_extraction_id,
                content_sha256=item.content_sha256,
                source_meta=dict(item.source_meta or {}),
                included_in_prompt=item.included_in_prompt,
            )
        )
    db.flush()
    return snapshot
