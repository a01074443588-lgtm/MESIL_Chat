"""Preserve previous attachment text extraction attempts.

Revision ID: 033_extraction_attempts
Revises: 032_roster_aware_ocr_drafts
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "033_extraction_attempts"
down_revision: str | None = "032_roster_aware_ocr_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_text_extraction_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("suggested_service_context", sa.String(length=30), nullable=True),
        sa.Column("suggestion_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reviewed_text", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reviewed_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["attachment_text_extractions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "extraction_id",
            "attempt_number",
            name="uq_attachment_text_extraction_attempt_number",
        ),
    )
    op.create_index(
        "ix_attachment_text_extraction_attempts_organization_id",
        "attachment_text_extraction_attempts",
        ["organization_id"],
    )
    op.create_index(
        "ix_attachment_text_extraction_attempts_extraction_id",
        "attachment_text_extraction_attempts",
        ["extraction_id"],
    )
    op.create_index(
        "ix_attachment_text_extraction_attempts_attachment_id",
        "attachment_text_extraction_attempts",
        ["attachment_id"],
    )
    op.create_index(
        "ix_attachment_text_extraction_attempts_attachment",
        "attachment_text_extraction_attempts",
        ["attachment_id", "attempt_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachment_text_extraction_attempts_attachment",
        table_name="attachment_text_extraction_attempts",
    )
    op.drop_index(
        "ix_attachment_text_extraction_attempts_attachment_id",
        table_name="attachment_text_extraction_attempts",
    )
    op.drop_index(
        "ix_attachment_text_extraction_attempts_extraction_id",
        table_name="attachment_text_extraction_attempts",
    )
    op.drop_index(
        "ix_attachment_text_extraction_attempts_organization_id",
        table_name="attachment_text_extraction_attempts",
    )
    op.drop_table("attachment_text_extraction_attempts")
