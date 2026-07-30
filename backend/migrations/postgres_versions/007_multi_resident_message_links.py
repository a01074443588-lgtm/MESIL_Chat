"""add reviewed multi-resident links for chat messages and report images

Revision ID: 007_multi_resident_links
Revises: 006_attachment_text_extraction
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "007_multi_resident_links"
down_revision: str | None = "006_attachment_text_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_message_recipient_links",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "source IN ('manual', 'text_exact', 'ocr_exact')",
            name="staff_hub_message_recipient_links_source_check",
        ),
        sa.CheckConstraint(
            "status IN ('candidate', 'confirmed', 'rejected')",
            name="staff_hub_message_recipient_links_status_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["staff_hub_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["recipient_id"], ["recipients.id"]),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "recipient_id",
            name="uq_staff_hub_message_recipient_link",
        ),
    )
    op.create_index(
        "ix_staff_hub_message_recipient_links_organization_id",
        "staff_hub_message_recipient_links",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_message_recipient_links_message_id",
        "staff_hub_message_recipient_links",
        ["message_id"],
    )
    op.create_index(
        "ix_staff_hub_message_recipient_links_recipient_id",
        "staff_hub_message_recipient_links",
        ["recipient_id"],
    )
    op.create_index(
        "ix_staff_hub_message_recipient_links_status",
        "staff_hub_message_recipient_links",
        ["organization_id", "status", "created_at"],
    )
    op.alter_column(
        "staff_hub_processing_items",
        "recipient_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM staff_hub_processing_items WHERE recipient_id IS NULL"
    )
    op.alter_column(
        "staff_hub_processing_items",
        "recipient_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
    op.drop_index(
        "ix_staff_hub_message_recipient_links_status",
        table_name="staff_hub_message_recipient_links",
    )
    op.drop_index(
        "ix_staff_hub_message_recipient_links_recipient_id",
        table_name="staff_hub_message_recipient_links",
    )
    op.drop_index(
        "ix_staff_hub_message_recipient_links_message_id",
        table_name="staff_hub_message_recipient_links",
    )
    op.drop_index(
        "ix_staff_hub_message_recipient_links_organization_id",
        table_name="staff_hub_message_recipient_links",
    )
    op.drop_table("staff_hub_message_recipient_links")
