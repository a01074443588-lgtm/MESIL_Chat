"""add Carefor staff review-only decision drafts

Revision ID: 022_staff_sync_review_drafts
Revises: 021_staff_carefor_sync_preview
Create Date: 2026-08-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "022_staff_sync_review_drafts"
down_revision: str | None = "021_staff_carefor_sync_preview"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_sync_review_drafts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_key", sa.String(40), nullable=False),
        sa.Column(
            "decision_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "completion_status",
            sa.String(20),
            server_default="draft",
            nullable=False,
        ),
        sa.Column(
            "completion_issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("updated_by_id", sa.Uuid(), nullable=False),
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
        sa.CheckConstraint(
            "completion_status IN ('draft', 'complete')",
            name="staff_sync_review_drafts_completion_status_check",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="staff_sync_review_drafts_revision_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["staff_sync_batches.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "batch_id",
            "candidate_key",
            name="uq_staff_sync_review_draft_candidate",
        ),
    )
    for column in (
        "organization_id",
        "batch_id",
        "completion_status",
        "created_by_id",
        "updated_by_id",
    ):
        op.create_index(
            f"ix_staff_sync_review_drafts_{column}",
            "staff_sync_review_drafts",
            [column],
        )
    op.create_index(
        "ix_staff_sync_review_drafts_org_batch",
        "staff_sync_review_drafts",
        ["organization_id", "batch_id"],
    )


def downgrade() -> None:
    op.drop_table("staff_sync_review_drafts")
