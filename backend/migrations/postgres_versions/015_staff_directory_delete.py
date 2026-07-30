"""hide deleted staff while preserving historical chat authors

Revision ID: 015_staff_directory_delete
Revises: 014_staff_position_title
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "015_staff_directory_delete"
down_revision: str | None = "014_staff_position_title"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        op.f("ix_staff_deleted_at"),
        "staff",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_staff_deleted_at"), table_name="staff")
    op.drop_column("staff", "deleted_at")
