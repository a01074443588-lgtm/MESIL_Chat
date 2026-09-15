"""add auditable AI assistance conversations, turns, sources, and attempts

Revision ID: 028_ai_assist
Revises: 027_confirmed_work_records
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "028_ai_assist"
down_revision: str | None = "027_confirmed_work_records"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


TURN_STATUSES = (
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


def upgrade() -> None:
    op.create_table(
        "ai_assist_conversations",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default=sa.text("'queued'"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{item}'" for item in TURN_STATUSES) + ")",
            name="ai_assist_conversations_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["staff_hub_rooms.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["staff_hub_messages.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_conversations_owner_created",
        "ai_assist_conversations",
        ["owner_user_id", "created_at"],
    )
    op.create_index(
        "ix_ai_conversations_org_room",
        "ai_assist_conversations",
        ["organization_id", "room_id"],
    )
    op.create_index(
        "ix_ai_conversations_message",
        "ai_assist_conversations",
        ["message_id"],
    )
    op.create_index(
        "ix_ai_conversations_owner_message_updated",
        "ai_assist_conversations",
        ["owner_user_id", "message_id", "updated_at"],
    )

    op.create_table(
        "ai_assist_turns",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("task_type", sa.String(length=30), nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column(
            "contains_real_data",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("visible_text", sa.Text(), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "uncertainties",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "recommended_actions",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "result_validated",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("provider_model", sa.String(length=160), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
        sa.Column("elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("share_ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("shared_message_id", sa.Uuid(), nullable=True),
        sa.Column("shared_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "sequence_no > 0",
            name="ai_assist_turns_sequence_check",
        ),
        sa.CheckConstraint(
            "task_type IN ('image_text', 'image_explain', 'audio_summary', "
            "'summary', 'history_search', 'risk_check', 'question')",
            name="ai_assist_turns_kind_check",
        ),
        sa.CheckConstraint(
            "elapsed_ms IS NULL OR elapsed_ms >= 0",
            name="ai_assist_turns_elapsed_check",
        ),
        sa.CheckConstraint(
            "status IN (" + ", ".join(f"'{item}'" for item in TURN_STATUSES) + ")",
            name="ai_assist_turns_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai_assist_conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["requested_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["shared_message_id"],
            ["staff_hub_messages.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "conversation_id",
            "sequence_no",
            name="uq_ai_assist_turn_sequence",
        ),
    )
    op.create_index(
        "ix_ai_turns_conversation_created",
        "ai_assist_turns",
        ["conversation_id", "created_at"],
    )
    op.create_index(
        "ix_ai_turns_org_status",
        "ai_assist_turns",
        ["organization_id", "status", "created_at"],
    )
    op.create_index(
        "ix_ai_turns_requester_created",
        "ai_assist_turns",
        ["requested_by_user_id", "created_at"],
    )
    op.create_index(
        "ix_ai_turns_shared_message",
        "ai_assist_turns",
        ["shared_message_id"],
    )

    op.create_table(
        "ai_assist_sources",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("message_id", sa.Uuid(), nullable=True),
        sa.Column("attachment_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_record_id", sa.Uuid(), nullable=True),
        sa.Column("label", sa.String(length=240), nullable=False),
        sa.Column("content_text", sa.Text(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "source_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "included_in_prompt",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal > 0",
            name="ai_assist_sources_ordinal_check",
        ),
        sa.CheckConstraint(
            "source_type IN ('message', 'attachment', 'confirmed_record', 'manual_context')",
            name="ai_assist_sources_type_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["ai_assist_turns.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["staff_hub_rooms.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["staff_hub_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["attachments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_record_id"],
            ["staff_hub_confirmed_records.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id", "ordinal", name="uq_ai_assist_source_ordinal"),
    )
    op.create_index(
        "ix_ai_sources_turn_type",
        "ai_assist_sources",
        ["turn_id", "source_type"],
    )
    op.create_index(
        "ix_ai_sources_message",
        "ai_assist_sources",
        ["message_id"],
    )
    op.create_index(
        "ix_ai_sources_confirmed_record",
        "ai_assist_sources",
        ["confirmed_record_id"],
    )

    op.create_table(
        "ai_assist_provider_attempts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("attempt_no", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=False),
        sa.Column("model_name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "transmitted_external",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("input_bytes", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column(
            "response_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "attempt_no > 0",
            name="ai_assist_attempts_number_check",
        ),
        sa.CheckConstraint(
            "input_bytes >= 0",
            name="ai_assist_attempts_input_bytes_check",
        ),
        sa.CheckConstraint(
            "latency_ms IS NULL OR latency_ms >= 0",
            name="ai_assist_attempts_latency_check",
        ),
        sa.CheckConstraint(
            "status IN ('started', 'completed', 'failed', 'skipped')",
            name="ai_assist_attempts_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["turn_id"],
            ["ai_assist_turns.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("turn_id", "attempt_no", name="uq_ai_assist_attempt_no"),
    )
    op.create_index(
        "ix_ai_attempts_turn_created",
        "ai_assist_provider_attempts",
        ["turn_id", "created_at"],
    )
    op.create_index(
        "ix_ai_attempts_org_provider",
        "ai_assist_provider_attempts",
        ["organization_id", "provider", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    conversation_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM ai_assist_conversations")
    ).scalar_one()
    if conversation_count:
        raise RuntimeError(
            "AI assistance records exist; refusing destructive downgrade"
        )

    op.drop_index("ix_ai_attempts_org_provider", table_name="ai_assist_provider_attempts")
    op.drop_index("ix_ai_attempts_turn_created", table_name="ai_assist_provider_attempts")
    op.drop_table("ai_assist_provider_attempts")
    op.drop_index("ix_ai_sources_confirmed_record", table_name="ai_assist_sources")
    op.drop_index("ix_ai_sources_message", table_name="ai_assist_sources")
    op.drop_index("ix_ai_sources_turn_type", table_name="ai_assist_sources")
    op.drop_table("ai_assist_sources")
    op.drop_index("ix_ai_turns_shared_message", table_name="ai_assist_turns")
    op.drop_index("ix_ai_turns_requester_created", table_name="ai_assist_turns")
    op.drop_index("ix_ai_turns_org_status", table_name="ai_assist_turns")
    op.drop_index("ix_ai_turns_conversation_created", table_name="ai_assist_turns")
    op.drop_table("ai_assist_turns")
    op.drop_index("ix_ai_conversations_message", table_name="ai_assist_conversations")
    op.drop_index(
        "ix_ai_conversations_owner_message_updated",
        table_name="ai_assist_conversations",
    )
    op.drop_index("ix_ai_conversations_org_room", table_name="ai_assist_conversations")
    op.drop_index("ix_ai_conversations_owner_created", table_name="ai_assist_conversations")
    op.drop_table("ai_assist_conversations")
