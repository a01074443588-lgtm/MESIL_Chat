"""Add append-only staff-approved handwriting correction versions.

Revision ID: 042_handwriting_approvals
Revises: 041_resident_assessment_cycles
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "042_handwriting_approvals"
down_revision: str | None = "041_resident_assessment_cycles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "handwriting_correction_approvals",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("approval_group_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_approval_id", sa.Uuid(), nullable=True),
        sa.Column("image_attachment_id", sa.Uuid(), nullable=False),
        sa.Column("audio_attachment_id", sa.Uuid(), nullable=True),
        sa.Column("mode", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="approved", nullable=False),
        sa.Column("initial_ocr", sa.Text(), nullable=False),
        sa.Column("audio_or_explanation_text", sa.Text(), nullable=True),
        sa.Column("ai_suggestion", sa.Text(), nullable=True),
        sa.Column("approved_final_text", sa.Text(), nullable=False),
        sa.Column("sentence_decisions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("conflicts_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("approved_by_id", sa.Uuid(), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("official_record_saved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("training_data_adopted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("external_transfer_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "mode IN ('direct_typing', 'full_reading', 'partial_correction', 'story_hint')",
            name="handwriting_correction_approval_mode_check",
        ),
        sa.CheckConstraint("status = 'approved'", name="handwriting_correction_approval_status_check"),
        sa.CheckConstraint("revision > 0", name="handwriting_correction_approval_revision_check"),
        sa.CheckConstraint(
            "official_record_saved = false AND training_data_adopted = false "
            "AND external_transfer_allowed = false",
            name="handwriting_correction_approval_safety_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supersedes_approval_id"], ["handwriting_correction_approvals.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["image_attachment_id"], ["attachments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["audio_attachment_id"], ["attachments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "approval_group_id",
            "revision",
            name="uq_handwriting_correction_approval_revision",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_handwriting_correction_approval_idempotency",
        ),
    )
    op.create_index(
        "ix_handwriting_correction_approvals_image_history",
        "handwriting_correction_approvals",
        ["organization_id", "image_attachment_id", "created_at"],
    )
    for column_name in (
        "organization_id",
        "approval_group_id",
        "image_attachment_id",
        "audio_attachment_id",
        "approved_by_id",
    ):
        op.create_index(
            f"ix_handwriting_correction_approvals_{column_name}",
            "handwriting_correction_approvals",
            [column_name],
        )


def downgrade() -> None:
    for column_name in reversed(
        (
            "organization_id",
            "approval_group_id",
            "image_attachment_id",
            "audio_attachment_id",
            "approved_by_id",
        )
    ):
        op.drop_index(
            f"ix_handwriting_correction_approvals_{column_name}",
            table_name="handwriting_correction_approvals",
        )
    op.drop_index(
        "ix_handwriting_correction_approvals_image_history",
        table_name="handwriting_correction_approvals",
    )
    op.drop_table("handwriting_correction_approvals")
