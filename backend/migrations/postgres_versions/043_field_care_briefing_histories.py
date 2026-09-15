"""Add append-only field care briefing histories.

Revision ID: 043_field_briefings
Revises: 042_handwriting_approvals
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "043_field_briefings"
down_revision: str | None = "042_handwriting_approvals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "field_care_briefing_histories",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("resident_id", sa.Uuid(), nullable=True),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("overall_summary", sa.Text(), nullable=False),
        sa.Column("daily_care_references", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("consultation_references", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_refs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("response_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("care_reference_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("consultation_reference_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("generator", sa.String(length=120), nullable=False),
        sa.Column("is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("official_record_saved", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("revision > 0", name="field_care_briefing_history_revision_check"),
        sa.CheckConstraint(
            "official_record_saved = false",
            name="field_care_briefing_history_official_record_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["staff_hub_rooms.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["resident_id"], ["recipients.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "scope_key",
            "revision",
            name="uq_field_care_briefing_history_revision",
        ),
    )
    for column_name in ("organization_id", "created_by_id", "scope_key", "period_start", "period_end", "is_test_data"):
        op.create_index(
            f"ix_field_care_briefing_histories_{column_name}",
            "field_care_briefing_histories",
            [column_name],
        )
    op.create_index(
        "ix_field_care_briefing_histories_recent",
        "field_care_briefing_histories",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_field_care_briefing_histories_recent",
        table_name="field_care_briefing_histories",
    )
    for column_name in reversed(("organization_id", "created_by_id", "scope_key", "period_start", "period_end", "is_test_data")):
        op.drop_index(
            f"ix_field_care_briefing_histories_{column_name}",
            table_name="field_care_briefing_histories",
        )
    op.drop_table("field_care_briefing_histories")
