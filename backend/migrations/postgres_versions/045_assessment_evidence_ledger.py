"""Add the append-only assessment evidence ledger.

Revision ID: 045_evidence_ledger
Revises: 044_bulk_transfers
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "045_evidence_ledger"
down_revision: str | None = "044_bulk_transfers"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "assessment_evidence_ledger_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("source_ref", sa.String(length=120), nullable=False),
        sa.Column("document_kind", sa.String(length=100), nullable=False),
        sa.Column("document_date", sa.Date(), nullable=True),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("organization_role", sa.String(length=60), nullable=False),
        sa.Column("reference_locator", sa.String(length=500), nullable=False),
        sa.Column("raw_extracted_text", sa.Text(), nullable=False),
        sa.Column("normalized_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verification_state", sa.String(length=50), nullable=False),
        sa.Column("conflict_groups", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("staff_review_required", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("linked_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("external_transfer_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "external_transfer_allowed = false",
            name="assessment_evidence_ledger_external_transfer_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resident_id"], ["recipients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["draft_id"], ["new_admission_drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("draft_id", "source_ref", name="uq_assessment_evidence_ledger_source"),
    )
    op.create_index(
        "ix_assessment_evidence_ledger_entries_organization_id",
        "assessment_evidence_ledger_entries",
        ["organization_id"],
    )
    op.create_index(
        "ix_assessment_evidence_ledger_entries_resident_id",
        "assessment_evidence_ledger_entries",
        ["resident_id"],
    )
    op.create_index(
        "ix_assessment_evidence_ledger_entries_draft_id",
        "assessment_evidence_ledger_entries",
        ["draft_id"],
    )
    op.create_index(
        "ix_assessment_evidence_ledger_entries_created_by_id",
        "assessment_evidence_ledger_entries",
        ["created_by_id"],
    )
    op.create_index(
        "ix_assessment_evidence_ledger_resident_date",
        "assessment_evidence_ledger_entries",
        ["organization_id", "resident_id", "document_date"],
    )


def downgrade() -> None:
    op.drop_table("assessment_evidence_ledger_entries")
