"""guard open staff service periods and assignments

Revision ID: 024_staff_open_assignment_guards
Revises: 023_staff_service_assignments
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "024_staff_open_assignment_guards"
down_revision: str | None = "023_staff_service_assignments"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "uq_staff_service_periods_open_account",
        "staff_service_periods",
        ["source_account_id"],
        unique=True,
        postgresql_where=sa.text("end_date IS NULL"),
    )
    op.drop_index(
        "ix_staff_service_assignments_current_service",
        table_name="staff_service_assignments",
    )
    op.create_index(
        "uq_staff_service_assignments_open_service",
        "staff_service_assignments",
        ["staff_id", "service_type"],
        unique=True,
        postgresql_where=sa.text("end_date IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_staff_service_assignments_open_service",
        table_name="staff_service_assignments",
    )
    op.create_index(
        "ix_staff_service_assignments_current_service",
        "staff_service_assignments",
        ["staff_id", "service_type"],
        postgresql_where=sa.text("end_date IS NULL"),
    )
    op.drop_index(
        "uq_staff_service_periods_open_account",
        table_name="staff_service_periods",
    )
