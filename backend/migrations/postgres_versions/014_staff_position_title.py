"""store staff position independently from job assignment

Revision ID: 014_staff_position_title
Revises: 013_room_member_overrides
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "014_staff_position_title"
down_revision: str | None = "013_room_member_overrides"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff",
        sa.Column("position_title", sa.String(length=100), nullable=True),
    )
    op.execute(
        """
        UPDATE staff AS s
        SET position_title = current_job.position_title
        FROM (
            SELECT DISTINCT ON (staff_id)
                staff_id,
                position_title
            FROM staff_job_assignments
            WHERE end_date IS NULL
              AND is_primary = true
              AND position_title IS NOT NULL
            ORDER BY staff_id, start_date DESC, created_at DESC
        ) AS current_job
        WHERE s.id = current_job.staff_id
        """
    )


def downgrade() -> None:
    op.drop_column("staff", "position_title")
