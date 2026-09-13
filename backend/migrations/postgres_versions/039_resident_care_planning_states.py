"""Add resident-level care planning status metadata.

Revision ID: 039_resident_care_planning
Revises: 038_ai_analysis_snapshots
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "039_resident_care_planning"
down_revision: str | None = "038_ai_analysis_snapshots"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resident_care_planning_states",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("resident_id", sa.Uuid(), nullable=False),
        sa.Column("document_type", sa.String(length=60), nullable=False),
        sa.Column("assessment_date", sa.Date(), nullable=True),
        sa.Column("valid_until", sa.Date(), nullable=True),
        sa.Column("next_due_date", sa.Date(), nullable=True),
        sa.Column("cycle_months", sa.Integer(), server_default="6", nullable=False),
        sa.Column(
            "reassess_on_state_change",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
        sa.Column(
            "state_change_triggered",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("change_reason", sa.Text(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="not_started",
            nullable=False,
        ),
        sa.Column(
            "evidence_refs",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "known_facts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "questions_required",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "professional_review_fields",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "author_confirmed",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column("author_verified_by_id", sa.Uuid(), nullable=True),
        sa.Column("author_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
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
            "document_type IN ('cognitive_function_assessment', "
            "'fall_risk_assessment', 'pressure_ulcer_risk_assessment', "
            "'needs_assessment', 'long_term_care_service_plan')",
            name="resident_care_planning_document_type_check",
        ),
        sa.CheckConstraint(
            "status IN ('not_started', 'draft', 'needs_review', 'confirmed')",
            name="resident_care_planning_status_check",
        ),
        sa.CheckConstraint(
            "cycle_months >= 1 AND cycle_months <= 24",
            name="resident_care_planning_cycle_check",
        ),
        sa.CheckConstraint(
            "version > 0", name="resident_care_planning_version_check"
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resident_id"], ["recipients.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["author_verified_by_id"], ["users.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "resident_id",
            "document_type",
            name="uq_resident_care_planning_document",
        ),
    )
    op.create_index(
        "ix_resident_care_planning_resident",
        "resident_care_planning_states",
        ["organization_id", "resident_id"],
    )
    op.create_index(
        "ix_resident_care_planning_due",
        "resident_care_planning_states",
        ["organization_id", "next_due_date"],
    )
    for column_name in (
        "organization_id",
        "resident_id",
        "document_type",
        "next_due_date",
        "status",
        "author_verified_by_id",
    ):
        op.create_index(
            f"ix_resident_care_planning_states_{column_name}",
            "resident_care_planning_states",
            [column_name],
        )


def downgrade() -> None:
    for column_name in reversed(
        (
            "organization_id",
            "resident_id",
            "document_type",
            "next_due_date",
            "status",
            "author_verified_by_id",
        )
    ):
        op.drop_index(
            f"ix_resident_care_planning_states_{column_name}",
            table_name="resident_care_planning_states",
        )
    op.drop_index(
        "ix_resident_care_planning_due",
        table_name="resident_care_planning_states",
    )
    op.drop_index(
        "ix_resident_care_planning_resident",
        table_name="resident_care_planning_states",
    )
    op.drop_table("resident_care_planning_states")
