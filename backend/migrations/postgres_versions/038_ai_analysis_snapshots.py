"""Add immutable AI analysis snapshots and source evidence.

Revision ID: 038_ai_analysis_snapshots
Revises: 037_ocr_quality_registry_bundles
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "038_ai_analysis_snapshots"
down_revision: str | None = "037_ocr_quality_registry_bundles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_analysis_snapshots",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("subject_type", sa.String(length=30), nullable=False),
        sa.Column("subject_key", sa.String(length=160), nullable=False),
        sa.Column("primary_message_id", sa.Uuid(), nullable=True),
        sa.Column("analysis_kind", sa.String(length=60), nullable=False),
        sa.Column("sequence_no", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("processor_kind", sa.String(length=30), nullable=False),
        sa.Column("processor_version", sa.String(length=80), nullable=False),
        sa.Column("provider", sa.String(length=50), nullable=True),
        sa.Column("model_name", sa.String(length=160), nullable=True),
        sa.Column("prompt_version", sa.String(length=80), nullable=True),
        sa.Column("transmitted_external", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "result_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "uncertainties",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("analysis_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "subject_type IN ('message', 'care_incident', 'care_briefing')",
            name="ai_analysis_snapshots_subject_type_check",
        ),
        sa.CheckConstraint(
            "processor_kind IN ('local_rules', 'external_model', 'admin_control', "
            "'linked_action', 'system_fallback')",
            name="ai_analysis_snapshots_processor_kind_check",
        ),
        sa.CheckConstraint(
            "status IN ('completed', 'failed')",
            name="ai_analysis_snapshots_status_check",
        ),
        sa.CheckConstraint(
            "sequence_no > 0",
            name="ai_analysis_snapshots_sequence_check",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR (confidence >= 0 AND confidence <= 1)",
            name="ai_analysis_snapshots_confidence_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["primary_message_id"],
            ["staff_hub_messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "subject_type",
            "subject_key",
            "analysis_kind",
            "sequence_no",
            name="uq_ai_analysis_snapshot_sequence",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "analysis_fingerprint",
            name="uq_ai_analysis_snapshot_fingerprint",
        ),
    )
    op.create_index(
        "ix_ai_analysis_snapshots_subject",
        "ai_analysis_snapshots",
        [
            "organization_id",
            "subject_type",
            "subject_key",
            "analysis_kind",
            "created_at",
        ],
    )
    op.create_index(
        "ix_ai_analysis_snapshots_kind_created",
        "ai_analysis_snapshots",
        ["organization_id", "analysis_kind", "created_at"],
    )
    op.create_index(
        "ix_ai_analysis_snapshots_primary_message",
        "ai_analysis_snapshots",
        ["primary_message_id"],
    )

    op.create_table(
        "ai_analysis_evidence",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=30), nullable=False),
        sa.Column("evidence_role", sa.String(length=20), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_comment_id", sa.Uuid(), nullable=True),
        sa.Column("source_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("source_extraction_id", sa.Uuid(), nullable=True),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "source_meta",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("included_in_prompt", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source_type IN ('message', 'comment', 'attachment', 'text_extraction')",
            name="ai_analysis_evidence_source_type_check",
        ),
        sa.CheckConstraint(
            "evidence_role IN ('primary', 'supporting')",
            name="ai_analysis_evidence_role_check",
        ),
        sa.CheckConstraint(
            "ordinal > 0", name="ai_analysis_evidence_ordinal_check"
        ),
        sa.CheckConstraint(
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
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"], ["ai_analysis_snapshots.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["staff_hub_messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_comment_id"],
            ["staff_hub_message_comments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_attachment_id"], ["attachments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_extraction_id"],
            ["attachment_text_extractions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "snapshot_id", "ordinal", name="uq_ai_analysis_evidence_ordinal"
        ),
    )
    op.create_index(
        "ix_ai_analysis_evidence_message",
        "ai_analysis_evidence",
        ["source_message_id", "snapshot_id"],
    )
    op.create_index(
        "ix_ai_analysis_evidence_comment",
        "ai_analysis_evidence",
        ["source_comment_id", "snapshot_id"],
    )
    op.create_index(
        "ix_ai_analysis_evidence_attachment",
        "ai_analysis_evidence",
        ["source_attachment_id", "snapshot_id"],
    )

    op.execute(
        """
        CREATE FUNCTION reject_ai_analysis_history_mutation()
        RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'AI analysis history is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table_name in ("ai_analysis_snapshots", "ai_analysis_evidence"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_append_only
            BEFORE UPDATE OR DELETE ON {table_name}
            FOR EACH ROW EXECUTE FUNCTION reject_ai_analysis_history_mutation()
            """
        )


def downgrade() -> None:
    for table_name in ("ai_analysis_evidence", "ai_analysis_snapshots"):
        op.execute(
            f"DROP TRIGGER IF EXISTS trg_{table_name}_append_only ON {table_name}"
        )
    op.execute("DROP FUNCTION IF EXISTS reject_ai_analysis_history_mutation()")

    op.drop_index(
        "ix_ai_analysis_evidence_attachment", table_name="ai_analysis_evidence"
    )
    op.drop_index("ix_ai_analysis_evidence_comment", table_name="ai_analysis_evidence")
    op.drop_index("ix_ai_analysis_evidence_message", table_name="ai_analysis_evidence")
    op.drop_table("ai_analysis_evidence")

    op.drop_index(
        "ix_ai_analysis_snapshots_primary_message",
        table_name="ai_analysis_snapshots",
    )
    op.drop_index(
        "ix_ai_analysis_snapshots_kind_created",
        table_name="ai_analysis_snapshots",
    )
    op.drop_index(
        "ix_ai_analysis_snapshots_subject", table_name="ai_analysis_snapshots"
    )
    op.drop_table("ai_analysis_snapshots")
