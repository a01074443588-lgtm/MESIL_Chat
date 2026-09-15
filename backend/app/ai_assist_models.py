from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


AI_TURN_STATUSES = (
    "queued",
    "preparing",
    "extracting",
    "searching",
    "reasoning",
    "validating",
    "completed",
    "failed",
    "cancelled",
)
AI_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled"})
AI_SOURCE_TYPES = (
    "message",
    "attachment",
    "confirmed_record",
    "manual_context",
)
AI_TASK_TYPES = (
    "image_text",
    "image_explain",
    "audio_summary",
    "summary",
    "history_search",
    "risk_check",
    "question",
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid_pk() -> Mapped[UUID]:
    return mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid4,
        server_default=text("gen_random_uuid()"),
    )


JSON_DATA = JSON().with_variant(JSONB, "postgresql")


class AiAssistConversation(Base):
    __tablename__ = "ai_assist_conversations"
    __table_args__ = (
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{item}'" for item in AI_TURN_STATUSES)
            + ")",
            name="ai_assist_conversations_status_check",
        ),
        Index(
            "ix_ai_conversations_owner_created",
            "owner_user_id",
            "created_at",
        ),
        Index(
            "ix_ai_conversations_org_room",
            "organization_id",
            "room_id",
        ),
        Index("ix_ai_conversations_message", "message_id"),
        Index(
            "ix_ai_conversations_owner_message_updated",
            "owner_user_id",
            "message_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT"),
        index=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        index=False,
    )
    room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[UUID] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="RESTRICT")
    )
    title: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    turns: Mapped[list[AiAssistTurn]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="AiAssistTurn.sequence_no",
    )


class AiAssistTurn(Base):
    __tablename__ = "ai_assist_turns"
    __table_args__ = (
        CheckConstraint("sequence_no > 0", name="ai_assist_turns_sequence_check"),
        CheckConstraint(
            "task_type IN ('image_text', 'image_explain', 'audio_summary', "
            "'summary', 'history_search', 'risk_check', 'question')",
            name="ai_assist_turns_kind_check",
        ),
        CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0",
            name="ai_assist_turns_elapsed_check",
        ),
        CheckConstraint(
            "status IN ("
            + ", ".join(f"'{item}'" for item in AI_TURN_STATUSES)
            + ")",
            name="ai_assist_turns_status_check",
        ),
        UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_ai_assist_turn_sequence",
        ),
        Index(
            "ix_ai_turns_conversation_created",
            "conversation_id",
            "created_at",
        ),
        Index(
            "ix_ai_turns_org_status",
            "organization_id",
            "status",
            "created_at",
        ),
        Index(
            "ix_ai_turns_requester_created",
            "requested_by_user_id",
            "created_at",
        ),
        Index("ix_ai_turns_shared_message", "shared_message_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    conversation_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_assist_conversations.id", ondelete="CASCADE")
    )
    requested_by_user_id: Mapped[UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    sequence_no: Mapped[int] = mapped_column(Integer)
    task_type: Mapped[str] = mapped_column(String(30))
    question: Mapped[str] = mapped_column(Text)
    contains_real_data: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(20), default="queued")
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    visible_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON_DATA, default=list)
    uncertainties: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    recommended_actions: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    result_payload: Mapped[dict[str, Any] | None] = mapped_column(
        JSON_DATA,
        nullable=True,
    )
    result_validated: Mapped[bool] = mapped_column(Boolean, default=False)
    provider: Mapped[str | None] = mapped_column(String(50), nullable=True)
    provider_model: Mapped[str | None] = mapped_column(String(160), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(80), nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    share_ready_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    shared_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    shared_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
    )

    conversation: Mapped[AiAssistConversation] = relationship(back_populates="turns")
    sources: Mapped[list[AiAssistSource]] = relationship(
        back_populates="turn",
        cascade="all, delete-orphan",
        order_by="AiAssistSource.ordinal",
    )
    provider_attempts: Mapped[list[AiAssistProviderAttempt]] = relationship(
        back_populates="turn",
        cascade="all, delete-orphan",
        order_by="AiAssistProviderAttempt.attempt_no",
    )


class AiAssistSource(Base):
    __tablename__ = "ai_assist_sources"
    __table_args__ = (
        CheckConstraint("ordinal > 0", name="ai_assist_sources_ordinal_check"),
        CheckConstraint(
            "source_type IN ('message', 'attachment', 'confirmed_record', 'manual_context')",
            name="ai_assist_sources_type_check",
        ),
        UniqueConstraint(
            "turn_id",
            "ordinal",
            name="uq_ai_assist_source_ordinal",
        ),
        Index("ix_ai_sources_turn_type", "turn_id", "source_type"),
        Index("ix_ai_sources_message", "message_id"),
        Index("ix_ai_sources_confirmed_record", "confirmed_record_id"),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    turn_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_assist_turns.id", ondelete="CASCADE")
    )
    ordinal: Mapped[int] = mapped_column(Integer)
    source_type: Mapped[str] = mapped_column(String(30))
    room_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_rooms.id", ondelete="SET NULL"),
        nullable=True,
    )
    message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_messages.id", ondelete="SET NULL"),
        nullable=True,
    )
    attachment_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("attachments.id", ondelete="SET NULL"),
        nullable=True,
    )
    confirmed_record_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("staff_hub_confirmed_records.id", ondelete="RESTRICT"),
        nullable=True,
    )
    label: Mapped[str] = mapped_column(String(240))
    content_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_meta: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    included_in_prompt: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    turn: Mapped[AiAssistTurn] = relationship(back_populates="sources")


class AiAssistProviderAttempt(Base):
    __tablename__ = "ai_assist_provider_attempts"
    __table_args__ = (
        CheckConstraint(
            "attempt_no > 0",
            name="ai_assist_attempts_number_check",
        ),
        CheckConstraint(
            "input_bytes >= 0",
            name="ai_assist_attempts_input_bytes_check",
        ),
        CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ai_assist_attempts_latency_check",
        ),
        CheckConstraint(
            "status IN ('started', 'completed', 'failed', 'skipped')",
            name="ai_assist_attempts_status_check",
        ),
        UniqueConstraint(
            "turn_id",
            "attempt_no",
            name="uq_ai_assist_attempt_no",
        ),
        Index("ix_ai_attempts_turn_created", "turn_id", "created_at"),
        Index(
            "ix_ai_attempts_org_provider",
            "organization_id",
            "provider",
            "created_at",
        ),
    )

    id: Mapped[UUID] = _uuid_pk()
    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    turn_id: Mapped[UUID] = mapped_column(
        ForeignKey("ai_assist_turns.id", ondelete="CASCADE")
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    provider: Mapped[str] = mapped_column(String(50))
    model_name: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(20))
    transmitted_external: Mapped[bool] = mapped_column(Boolean, default=False)
    input_bytes: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_meta: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    turn: Mapped[AiAssistTurn] = relationship(back_populates="provider_attempts")
