"""add service-specific staff assignments

Revision ID: 023_staff_service_assignments
Revises: 022_staff_sync_review_drafts
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "023_staff_service_assignments"
down_revision: str | None = "022_staff_sync_review_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_service_assignments",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("source_account_id", sa.Uuid(), nullable=False),
        sa.Column("service_period_id", sa.Uuid(), nullable=False),
        sa.Column("service_type", sa.String(30), nullable=False),
        sa.Column("business_unit_id", sa.Uuid(), nullable=True),
        sa.Column("department_unit_id", sa.Uuid(), nullable=True),
        sa.Column("floor_unit_id", sa.Uuid(), nullable=True),
        sa.Column("team_unit_id", sa.Uuid(), nullable=True),
        sa.Column("job_code", sa.String(80), nullable=True),
        sa.Column("job_title_snapshot", sa.String(100), nullable=True),
        sa.Column("position_code_id", sa.Uuid(), nullable=True),
        sa.Column("position_title_snapshot", sa.String(100), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column(
            "assignment_basis",
            sa.String(30),
            server_default="carefor_auto",
            nullable=False,
        ),
        sa.Column("source_batch_id", sa.Uuid(), nullable=True),
        sa.Column("note", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "is_test_data", sa.Boolean(), server_default=sa.false(), nullable=False
        ),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("updated_by", sa.Uuid(), nullable=True),
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
            "service_type IN ('facility', 'daycare', 'homecare')",
            name="staff_service_assignments_service_type_check",
        ),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date > start_date",
            name="staff_service_assignments_dates_check",
        ),
        sa.CheckConstraint(
            "assignment_basis IN ('carefor_auto', 'admin_confirmed', 'legacy_manual')",
            name="staff_service_assignments_basis_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["staff_id"], ["staff.id"]),
        sa.ForeignKeyConstraint(["source_account_id"], ["staff_source_accounts.id"]),
        sa.ForeignKeyConstraint(["service_period_id"], ["staff_service_periods.id"]),
        sa.ForeignKeyConstraint(
            ["business_unit_id"], ["organization_units.id"]
        ),
        sa.ForeignKeyConstraint(
            ["department_unit_id"], ["organization_units.id"]
        ),
        sa.ForeignKeyConstraint(["floor_unit_id"], ["organization_units.id"]),
        sa.ForeignKeyConstraint(["team_unit_id"], ["organization_units.id"]),
        sa.ForeignKeyConstraint(["job_code"], ["staff_job_codes.code"]),
        sa.ForeignKeyConstraint(["position_code_id"], ["staff_position_codes.id"]),
        sa.ForeignKeyConstraint(
            ["source_batch_id"], ["staff_sync_batches.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.ForeignKeyConstraint(["updated_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_period_id",
            "start_date",
            name="uq_staff_service_assignment_period_start",
        ),
    )
    for column in (
        "organization_id",
        "staff_id",
        "source_account_id",
        "service_period_id",
        "service_type",
    ):
        op.create_index(
            f"ix_staff_service_assignments_{column}",
            "staff_service_assignments",
            [column],
        )
    op.create_index(
        "ix_staff_service_assignments_staff_dates",
        "staff_service_assignments",
        ["staff_id", "service_type", "start_date", "end_date"],
    )
    op.create_index(
        "ix_staff_service_assignments_current_service",
        "staff_service_assignments",
        ["staff_id", "service_type"],
        postgresql_where=sa.text("end_date IS NULL"),
    )


def downgrade() -> None:
    row_count = op.get_bind().execute(
        sa.text("SELECT COUNT(*) FROM staff_service_assignments")
    ).scalar_one()
    if row_count:
        raise RuntimeError(
            "staff_service_assignments에 이력이 있어 자동 downgrade를 중단했습니다."
        )
    op.drop_table("staff_service_assignments")
