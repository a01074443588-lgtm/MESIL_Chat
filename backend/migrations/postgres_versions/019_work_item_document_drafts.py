"""add versioned work item document drafts

Revision ID: 019_work_item_document_drafts
Revises: 018_ocr_correction_events
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "019_work_item_document_drafts"
down_revision: str | None = "018_ocr_correction_events"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_work_item_document_drafts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(length=50), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "verification_questions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("is_current", sa.Boolean(), nullable=False),
        sa.Column("generator", sa.String(length=120), nullable=False),
        sa.Column("change_request", sa.Text(), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_by_id", sa.Uuid(), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "document_type IN "
            "('care_service_record', 'nursing_log', 'consultation_log', "
            "'physical_restraint_log', 'program_log')",
            name="work_item_document_drafts_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'not_used')",
            name="work_item_document_drafts_status_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["staff_hub_processing_items.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["approved_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "document_type",
            "version",
            name="uq_work_item_document_draft_version",
        ),
    )
    op.create_index(
        "ix_work_item_document_drafts_organization_id",
        "staff_hub_work_item_document_drafts",
        ["organization_id"],
    )
    op.create_index(
        "ix_work_item_document_drafts_work_item_id",
        "staff_hub_work_item_document_drafts",
        ["work_item_id"],
    )
    op.create_index(
        "ix_work_item_document_drafts_document_type",
        "staff_hub_work_item_document_drafts",
        ["document_type"],
    )
    op.create_index(
        "ix_work_item_document_drafts_status",
        "staff_hub_work_item_document_drafts",
        ["status"],
    )
    op.create_index(
        "ix_work_item_document_drafts_is_current",
        "staff_hub_work_item_document_drafts",
        ["is_current"],
    )
    op.create_index(
        "ix_work_item_document_drafts_approved_by_id",
        "staff_hub_work_item_document_drafts",
        ["approved_by_id"],
    )
    op.create_index(
        "ix_work_item_document_drafts_current",
        "staff_hub_work_item_document_drafts",
        ["work_item_id", "is_current"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_work_item_document_drafts_current",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_approved_by_id",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_is_current",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_status",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_document_type",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_work_item_id",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_index(
        "ix_work_item_document_drafts_organization_id",
        table_name="staff_hub_work_item_document_drafts",
    )
    op.drop_table("staff_hub_work_item_document_drafts")
