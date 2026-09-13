"""add Carefor staff identity history and preview-only synchronization

Revision ID: 021_staff_carefor_sync_preview
Revises: 020_document_draft_index_names
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "021_staff_carefor_sync_preview"
down_revision: str | None = "020_document_draft_index_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _uuid() -> sa.Uuid:
    return sa.Uuid()


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
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
    )


def upgrade() -> None:
    created_at, updated_at = _timestamps()
    op.create_table(
        "staff_source_accounts",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("staff_id", _uuid(), nullable=True),
        sa.Column("source_system", sa.String(30), server_default="carefor", nullable=False),
        sa.Column("service_type", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("display_name_snapshot", sa.String(100), nullable=False),
        sa.Column("job_name_snapshot", sa.String(100), server_default="", nullable=False),
        sa.Column("employment_status", sa.String(20), server_default="active", nullable=False),
        sa.Column("source_status", sa.String(50), server_default="", nullable=False),
        sa.Column("latest_start_date", sa.Date(), nullable=True),
        sa.Column("latest_end_date", sa.Date(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        created_at,
        updated_at,
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "source_system",
            "service_type",
            "external_id",
            name="uq_staff_source_account_identity",
        ),
    )
    for column in ("organization_id", "staff_id", "is_active"):
        op.create_index(f"ix_staff_source_accounts_{column}", "staff_source_accounts", [column])
    op.create_index(
        "ix_staff_source_accounts_staff_status",
        "staff_source_accounts",
        ["staff_id", "employment_status"],
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "staff_service_periods",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("staff_id", _uuid(), nullable=False),
        sa.Column("source_account_id", _uuid(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("employment_status", sa.String(20), server_default="active", nullable=False),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        created_at,
        updated_at,
        sa.CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_service_periods_dates_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(
            ["source_account_id"], ["staff_source_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "source_account_id", "start_date", name="uq_staff_service_period_account_start"
        ),
    )
    for column in ("organization_id", "staff_id", "source_account_id"):
        op.create_index(f"ix_staff_service_periods_{column}", "staff_service_periods", [column])
    op.create_index(
        "ix_staff_service_periods_staff_dates",
        "staff_service_periods",
        ["staff_id", "start_date", "end_date"],
    )

    created_at, updated_at = _timestamps()
    op.create_table(
        "staff_sync_batches",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column(
            "source",
            sa.String(60),
            server_default="carefor_read_only_capture",
            nullable=False,
        ),
        sa.Column("original_name", sa.String(180), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=False),
        sa.Column("source_generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(30), server_default="preview", nullable=False),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_by_id", _uuid(), nullable=False),
        created_at,
        updated_at,
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("organization_id", "file_sha256", "status", "created_by_id"):
        op.create_index(f"ix_staff_sync_batches_{column}", "staff_sync_batches", [column])
    op.create_index(
        "ix_staff_sync_batches_org_created",
        "staff_sync_batches",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "staff_sync_items",
        sa.Column("id", _uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("batch_id", _uuid(), nullable=False),
        sa.Column("organization_id", _uuid(), nullable=False),
        sa.Column("source_key", sa.String(140), nullable=False),
        sa.Column("service_type", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("change_type", sa.String(30), nullable=False),
        sa.Column("status", sa.String(30), server_default="pending", nullable=False),
        sa.Column("current_staff_id", _uuid(), nullable=True),
        sa.Column("incoming_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("conflict_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["staff_sync_batches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["current_staff_id"], ["staff.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("batch_id", "source_key", name="uq_staff_sync_item_source_key"),
    )
    for column in (
        "batch_id",
        "organization_id",
        "change_type",
        "status",
        "current_staff_id",
    ):
        op.create_index(f"ix_staff_sync_items_{column}", "staff_sync_items", [column])
    op.create_index(
        "ix_staff_sync_items_batch_status",
        "staff_sync_items",
        ["batch_id", "status", "change_type"],
    )


def downgrade() -> None:
    op.drop_table("staff_sync_items")
    op.drop_table("staff_sync_batches")
    op.drop_table("staff_service_periods")
    op.drop_table("staff_source_accounts")
