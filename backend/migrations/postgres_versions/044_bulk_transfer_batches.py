"""Add safe administrator XLSX transfer previews.

Revision ID: 044_bulk_transfers
Revises: 043_field_briefings
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "044_bulk_transfers"
down_revision: str | None = "043_field_briefings"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "bulk_transfer_batches",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="preview", nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column("reference_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("apply_result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("entity_type IN ('resident', 'staff')", name="bulk_transfer_batch_entity_type_check"),
        sa.CheckConstraint("status IN ('preview', 'partially_applied', 'applied')", name="bulk_transfer_batch_status_check"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_bulk_transfer_batches_organization_id", "bulk_transfer_batches", ["organization_id"])
    op.create_index("ix_bulk_transfer_batches_created_by_id", "bulk_transfer_batches", ["created_by_id"])
    op.create_index("ix_bulk_transfer_batches_entity_type", "bulk_transfer_batches", ["entity_type"])
    op.create_index("ix_bulk_transfer_batches_status", "bulk_transfer_batches", ["status"])
    op.create_index("ix_bulk_transfer_batch_recent", "bulk_transfer_batches", ["organization_id", "created_at"])

    op.create_table(
        "bulk_transfer_items",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=True),
        sa.Column("display_code", sa.String(length=100), nullable=True),
        sa.Column("change_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("current_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("incoming_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("issues", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["bulk_transfer_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "row_number", name="uq_bulk_transfer_batch_row"),
    )
    op.create_index("ix_bulk_transfer_items_batch_id", "bulk_transfer_items", ["batch_id"])
    op.create_index("ix_bulk_transfer_items_organization_id", "bulk_transfer_items", ["organization_id"])
    op.create_index("ix_bulk_transfer_items_change_type", "bulk_transfer_items", ["change_type"])
    op.create_index("ix_bulk_transfer_items_status", "bulk_transfer_items", ["status"])
    op.create_index("ix_bulk_transfer_item_batch_status", "bulk_transfer_items", ["batch_id", "status"])


def downgrade() -> None:
    op.drop_table("bulk_transfer_items")
    op.drop_table("bulk_transfer_batches")
