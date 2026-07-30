"""separate room membership scope from resident priority floor

Revision ID: 005_resident_scope_unit
Revises: 004_action_item_index
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "005_resident_scope_unit"
down_revision: str | None = "004_action_item_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_hub_rooms",
        sa.Column("resident_scope_unit_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_staff_hub_rooms_resident_scope_unit",
        "staff_hub_rooms",
        "organization_units",
        ["resident_scope_unit_id"],
        ["id"],
    )
    op.create_index(
        "ix_staff_hub_rooms_resident_scope_unit_id",
        "staff_hub_rooms",
        ["resident_scope_unit_id"],
    )
    op.execute(
        """
        UPDATE staff_hub_rooms
        SET resident_scope_unit_id = unit_id
        WHERE room_type = 'floor' AND resident_scope = 'floor'
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_rooms_resident_scope_unit_id",
        table_name="staff_hub_rooms",
    )
    op.drop_constraint(
        "fk_staff_hub_rooms_resident_scope_unit",
        "staff_hub_rooms",
        type_="foreignkey",
    )
    op.drop_column("staff_hub_rooms", "resident_scope_unit_id")
