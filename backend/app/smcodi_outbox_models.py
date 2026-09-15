from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .models import JSON_DATA, utcnow, uuid_pk


class SmcodiHandoverTargetMapping(Base):
    """기관이 확인한 로컬 어르신과 SMCODI 전달대상의 연결값."""

    __tablename__ = "smcodi_handover_target_mappings"
    __table_args__ = (
        CheckConstraint(
            "target_unit_id IS NOT NULL OR assigned_user_id IS NOT NULL",
            name="smcodi_handover_target_mapping_target_check",
        ),
        Index(
            "uq_smcodi_handover_target_mapping_active_resident",
            "organization_id",
            "local_resident_id",
            unique=True,
            postgresql_where=text("is_active"),
        ),
        Index(
            "ix_smcodi_handover_target_mapping_target_recipient",
            "organization_id",
            "target_recipient_id",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    local_resident_id: Mapped[UUID] = mapped_column(
        ForeignKey("recipients.id", ondelete="RESTRICT")
    )
    # 아래 UUID는 SMCODI 쪽 식별자이므로 이 데이터베이스의 FK 대상이 아니다.
    target_recipient_id: Mapped[UUID] = mapped_column()
    target_unit_id: Mapped[UUID | None] = mapped_column(nullable=True)
    assigned_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    verified_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class SmcodiHandoverOutbox(Base):
    """SMCODI 전송 전 검토만 담당하는 인수인계 대기함.

    이 모델에는 전송 시각이나 외부 응답 필드가 의도적으로 없다. 4차 단계는
    확정기록의 특정 불변 버전을 SMCODI 계약으로 변환하고 검토하는 데까지만
    책임진다.
    """

    __tablename__ = "smcodi_handover_outbox"
    __table_args__ = (
        CheckConstraint(
            "contract_name = 'smcodi_staff_hub_handover_v1'",
            name="smcodi_handover_outbox_contract_check",
        ),
        CheckConstraint(
            "status IN ('ready', 'blocked')",
            name="smcodi_handover_outbox_status_check",
        ),
        CheckConstraint(
            "source_version > 0",
            name="smcodi_handover_outbox_source_version_check",
        ),
        UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_smcodi_handover_outbox_idempotency",
        ),
        Index(
            "ix_smcodi_handover_outbox_org_status_created",
            "organization_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_smcodi_handover_outbox_source",
            "source_record_id",
            "source_version",
        ),
    )

    id: Mapped[UUID] = uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    source_record_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_confirmed_records.id", ondelete="RESTRICT")
    )
    source_version_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_confirmed_record_versions.id", ondelete="RESTRICT")
    )
    source_version: Mapped[int] = mapped_column(Integer)
    source_content_hash: Mapped[str] = mapped_column(String(64))
    contract_name: Mapped[str] = mapped_column(
        String(80), default="smcodi_staff_hub_handover_v1"
    )
    target_mapping_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("smcodi_handover_target_mappings.id", ondelete="RESTRICT"),
        nullable=True,
    )
    target_recipient_id: Mapped[UUID | None] = mapped_column(nullable=True)
    target_unit_id: Mapped[UUID | None] = mapped_column(nullable=True)
    assigned_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    validation_issues: Mapped[list[dict[str, str]]] = mapped_column(JSON_DATA)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON_DATA)
    payload_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(64))
    created_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    is_test_data: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
