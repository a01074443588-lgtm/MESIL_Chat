"""Add append-only assessment cycles for every resident.

Revision ID: 041_resident_assessment_cycles
Revises: 040_new_admission_drafts
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "041_resident_assessment_cycles"
down_revision: str | None = "040_new_admission_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resident_assessment_cycles",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("cycle_number", sa.Integer(), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("source_provider", sa.String(length=30), server_default="staff_manual", nullable=False),
        sa.Column("previous_cycle_id", sa.Uuid(), nullable=True),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("status", sa.String(length=30), server_default="needs_confirmation", nullable=False),
        sa.Column("external_transfer_allowed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "reason IN ('new_admission', 'periodic_reassessment', 'state_change', 'care_plan_change', 'staff_review')",
            name="resident_assessment_cycle_reason_check",
        ),
        sa.CheckConstraint(
            "source_provider IN ('staff_manual', 'carefor_readonly')",
            name="resident_assessment_cycle_provider_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="resident_assessment_cycle_status_check",
        ),
        sa.CheckConstraint(
            "cycle_number > 0 AND current_revision > 0",
            name="resident_assessment_cycle_numbers_check",
        ),
        sa.CheckConstraint(
            "external_transfer_allowed = false",
            name="resident_assessment_cycle_external_transfer_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resident_id"], ["recipients.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["previous_cycle_id"], ["resident_assessment_cycles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "resident_id",
            "cycle_number",
            name="uq_resident_assessment_cycle_number",
        ),
    )
    op.create_index(
        "ix_resident_assessment_cycles_resident",
        "resident_assessment_cycles",
        ["organization_id", "resident_id", "cycle_number"],
    )
    for column_name in (
        "organization_id",
        "resident_id",
        "reason",
        "previous_cycle_id",
        "created_by_id",
    ):
        op.create_index(
            f"ix_resident_assessment_cycles_{column_name}",
            "resident_assessment_cycles",
            [column_name],
        )

    op.create_table(
        "resident_assessment_cycle_revisions",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("cycle_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("supersedes_revision", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("baseline_sources", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("current_facts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("comparison_items", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selected_plan_candidate_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmation_questions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_changes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name="resident_assessment_cycle_revision_number_check"),
        sa.CheckConstraint(
            "supersedes_revision IS NULL OR supersedes_revision < revision",
            name="resident_assessment_cycle_revision_supersedes_check",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'needs_confirmation')",
            name="resident_assessment_cycle_revision_status_check",
        ),
        sa.ForeignKeyConstraint(["cycle_id"], ["resident_assessment_cycles.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cycle_id", "revision", name="uq_resident_assessment_cycle_revision"),
    )
    op.create_index(
        "ix_resident_assessment_cycle_revisions_history",
        "resident_assessment_cycle_revisions",
        ["cycle_id", "revision"],
    )
    for column_name in ("cycle_id", "created_by_id"):
        op.create_index(
            f"ix_resident_assessment_cycle_revisions_{column_name}",
            "resident_assessment_cycle_revisions",
            [column_name],
        )


def downgrade() -> None:
    for column_name in reversed(("cycle_id", "created_by_id")):
        op.drop_index(
            f"ix_resident_assessment_cycle_revisions_{column_name}",
            table_name="resident_assessment_cycle_revisions",
        )
    op.drop_index(
        "ix_resident_assessment_cycle_revisions_history",
        table_name="resident_assessment_cycle_revisions",
    )
    op.drop_table("resident_assessment_cycle_revisions")

    for column_name in reversed(
        ("organization_id", "resident_id", "reason", "previous_cycle_id", "created_by_id")
    ):
        op.drop_index(
            f"ix_resident_assessment_cycles_{column_name}",
            table_name="resident_assessment_cycles",
        )
    op.drop_index(
        "ix_resident_assessment_cycles_resident",
        table_name="resident_assessment_cycles",
    )
    op.drop_table("resident_assessment_cycles")
