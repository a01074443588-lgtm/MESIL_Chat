"""add administrator-approved resident roster synchronization

Revision ID: 009_resident_sync_batches
Revises: 008_ocr_correction_memory
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "009_resident_sync_batches"
down_revision: str | None = "008_ocr_correction_memory"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recipient_sync_batches",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column(
            "source",
            sa.String(length=60),
            server_default=sa.text("'smcodi_read_only_export'"),
            nullable=False,
        ),
        sa.Column("original_name", sa.String(length=180), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("source_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'preview'"),
            nullable=False,
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("applied_by_id", sa.Uuid(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["applied_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_recipient_sync_batches_organization_id",
        "recipient_sync_batches",
        ["organization_id"],
    )
    op.create_index(
        "ix_recipient_sync_batches_file_sha256",
        "recipient_sync_batches",
        ["file_sha256"],
    )
    op.create_index(
        "ix_recipient_sync_batches_status",
        "recipient_sync_batches",
        ["status"],
    )
    op.create_index(
        "ix_recipient_sync_batches_created_by_id",
        "recipient_sync_batches",
        ["created_by_id"],
    )
    op.create_index(
        "ix_recipient_sync_batches_org_created",
        "recipient_sync_batches",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "recipient_sync_items",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=80), nullable=False),
        sa.Column("change_type", sa.String(length=30), nullable=False),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("current_resident_id", sa.Uuid(), nullable=True),
        sa.Column(
            "incoming_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "current_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["recipient_sync_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["current_resident_id"], ["recipients.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "external_id",
            name="uq_recipient_sync_item_external_id",
        ),
    )
    op.create_index(
        "ix_recipient_sync_items_batch_id",
        "recipient_sync_items",
        ["batch_id"],
    )
    op.create_index(
        "ix_recipient_sync_items_organization_id",
        "recipient_sync_items",
        ["organization_id"],
    )
    op.create_index(
        "ix_recipient_sync_items_change_type",
        "recipient_sync_items",
        ["change_type"],
    )
    op.create_index(
        "ix_recipient_sync_items_status",
        "recipient_sync_items",
        ["status"],
    )
    op.create_index(
        "ix_recipient_sync_items_current_resident_id",
        "recipient_sync_items",
        ["current_resident_id"],
    )
    op.create_index(
        "ix_recipient_sync_items_batch_status",
        "recipient_sync_items",
        ["batch_id", "status", "change_type"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_recipient_sync_items_batch_status",
        table_name="recipient_sync_items",
    )
    op.drop_index(
        "ix_recipient_sync_items_current_resident_id",
        table_name="recipient_sync_items",
    )
    op.drop_index(
        "ix_recipient_sync_items_status",
        table_name="recipient_sync_items",
    )
    op.drop_index(
        "ix_recipient_sync_items_change_type",
        table_name="recipient_sync_items",
    )
    op.drop_index(
        "ix_recipient_sync_items_organization_id",
        table_name="recipient_sync_items",
    )
    op.drop_index(
        "ix_recipient_sync_items_batch_id",
        table_name="recipient_sync_items",
    )
    op.drop_table("recipient_sync_items")

    op.drop_index(
        "ix_recipient_sync_batches_org_created",
        table_name="recipient_sync_batches",
    )
    op.drop_index(
        "ix_recipient_sync_batches_created_by_id",
        table_name="recipient_sync_batches",
    )
    op.drop_index(
        "ix_recipient_sync_batches_status",
        table_name="recipient_sync_batches",
    )
    op.drop_index(
        "ix_recipient_sync_batches_file_sha256",
        table_name="recipient_sync_batches",
    )
    op.drop_index(
        "ix_recipient_sync_batches_organization_id",
        table_name="recipient_sync_batches",
    )
    op.drop_table("recipient_sync_batches")
