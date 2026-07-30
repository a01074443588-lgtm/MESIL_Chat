"""add local text extraction records for report image attachments

Revision ID: 006_attachment_text_extraction
Revises: 005_resident_scope_unit
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "006_attachment_text_extraction"
down_revision: str | None = "005_resident_scope_unit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_text_extractions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=True),
        sa.Column("reviewed_text", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("requested_by_id", sa.Uuid(), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed')",
            name="attachment_text_extractions_status_check",
        ),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["requested_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_attachment_text_extractions_attachment_id",
        "attachment_text_extractions",
        ["attachment_id"],
        unique=True,
    )
    op.create_index(
        "ix_attachment_text_extractions_organization_id",
        "attachment_text_extractions",
        ["organization_id"],
    )
    op.create_index(
        "ix_attachment_text_extractions_status_created",
        "attachment_text_extractions",
        ["status", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachment_text_extractions_status_created",
        table_name="attachment_text_extractions",
    )
    op.drop_index(
        "ix_attachment_text_extractions_organization_id",
        table_name="attachment_text_extractions",
    )
    op.drop_index(
        "ix_attachment_text_extractions_attachment_id",
        table_name="attachment_text_extractions",
    )
    op.drop_table("attachment_text_extractions")
