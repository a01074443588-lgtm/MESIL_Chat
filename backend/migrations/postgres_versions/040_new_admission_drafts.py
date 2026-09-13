"""Add append-only new-admission draft revisions.

Revision ID: 040_new_admission_drafts
Revises: 039_resident_care_planning
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "040_new_admission_drafts"
down_revision: str | None = "039_resident_care_planning"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "new_admission_drafts",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("case_ref", sa.String(length=80), nullable=False),
        sa.Column("processing_scope", sa.String(length=30), nullable=False),
        sa.Column(
            "external_transfer_allowed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "current_revision", sa.Integer(), server_default="1", nullable=False
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="needs_confirmation",
            nullable=False,
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
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
            "processing_scope IN ('internal_only', 'deidentified_dev')",
            name="new_admission_draft_scope_check",
        ),
        sa.CheckConstraint(
            "external_transfer_allowed = false",
            name="new_admission_draft_external_transfer_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="new_admission_draft_status_check",
        ),
        sa.CheckConstraint(
            "current_revision > 0",
            name="new_admission_draft_current_revision_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"], ["recipients.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "resident_id",
            "case_ref",
            name="uq_new_admission_draft_case",
        ),
    )
    op.create_index(
        "ix_new_admission_drafts_resident",
        "new_admission_drafts",
        ["organization_id", "resident_id"],
    )
    for column_name in ("organization_id", "resident_id", "created_by_id"):
        op.create_index(
            f"ix_new_admission_drafts_{column_name}",
            "new_admission_drafts",
            [column_name],
        )

    op.create_table(
        "new_admission_draft_revisions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_revision", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column(
            "bundle_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "field_evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "field_changes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "field_confirmations",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="new_admission_draft_revision_number_check",
        ),
        sa.CheckConstraint(
            "supersedes_revision IS NULL OR supersedes_revision < revision",
            name="new_admission_draft_supersedes_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="new_admission_draft_revision_status_check",
        ),
        sa.ForeignKeyConstraint(
            ["draft_id"], ["new_admission_drafts.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "draft_id",
            "revision",
            name="uq_new_admission_draft_revision",
        ),
    )
    op.create_index(
        "ix_new_admission_draft_revisions_history",
        "new_admission_draft_revisions",
        ["draft_id", "revision"],
    )
    for column_name in ("draft_id", "created_by_id"):
        op.create_index(
            f"ix_new_admission_draft_revisions_{column_name}",
            "new_admission_draft_revisions",
            [column_name],
        )


def downgrade() -> None:
    for column_name in reversed(("draft_id", "created_by_id")):
        op.drop_index(
            f"ix_new_admission_draft_revisions_{column_name}",
            table_name="new_admission_draft_revisions",
        )
    op.drop_index(
        "ix_new_admission_draft_revisions_history",
        table_name="new_admission_draft_revisions",
    )
    op.drop_table("new_admission_draft_revisions")

    for column_name in reversed(("organization_id", "resident_id", "created_by_id")):
        op.drop_index(
            f"ix_new_admission_drafts_{column_name}",
            table_name="new_admission_drafts",
        )
    op.drop_index(
        "ix_new_admission_drafts_resident",
        table_name="new_admission_drafts",
    )
    op.drop_table("new_admission_drafts")
