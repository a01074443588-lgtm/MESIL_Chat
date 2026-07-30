"""add one personal self-chat room per staff member

Revision ID: 011_personal_self_rooms
Revises: 010_dev_launcher_impersonation
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "011_personal_self_rooms"
down_revision: str | None = "010_dev_launcher_impersonation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_hub_rooms",
        sa.Column("owner_staff_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_staff_hub_rooms_owner_staff_id_staff",
        "staff_hub_rooms",
        "staff",
        ["owner_staff_id"],
        ["id"],
    )
    op.create_index(
        "ix_staff_hub_rooms_owner_staff_id",
        "staff_hub_rooms",
        ["owner_staff_id"],
    )
    op.create_index(
        "uq_staff_hub_rooms_self",
        "staff_hub_rooms",
        ["organization_id", "owner_staff_id"],
        unique=True,
        postgresql_where=sa.text(
            "room_type = 'self' AND owner_staff_id IS NOT NULL"
        ),
    )
    op.drop_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        type_="check",
    )
    op.create_check_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        "room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom', 'self')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        type_="check",
    )
    op.create_check_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        "room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom')",
    )
    op.drop_index("uq_staff_hub_rooms_self", table_name="staff_hub_rooms")
    op.drop_index(
        "ix_staff_hub_rooms_owner_staff_id",
        table_name="staff_hub_rooms",
    )
    op.drop_constraint(
        "fk_staff_hub_rooms_owner_staff_id_staff",
        "staff_hub_rooms",
        type_="foreignkey",
    )
    op.drop_column("staff_hub_rooms", "owner_staff_id")
