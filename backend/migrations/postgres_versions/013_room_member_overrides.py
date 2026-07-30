"""allow admin include and exclude overrides on automatic rooms

Revision ID: 013_room_member_overrides
Revises: 012_audio_transcript_links
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "013_room_member_overrides"
down_revision: str | None = "012_audio_transcript_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_room_membership_overrides",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("staff_id", sa.Uuid(), nullable=False),
        sa.Column("override_action", sa.String(length=20), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
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
            "override_action IN ('include', 'exclude')",
            name="staff_hub_room_membership_overrides_action_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
        ),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["staff_hub_rooms.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["staff_id"],
            ["staff.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "room_id",
            "staff_id",
            name="uq_staff_hub_room_membership_override",
        ),
    )
    op.create_index(
        op.f("ix_staff_hub_room_membership_overrides_organization_id"),
        "staff_hub_room_membership_overrides",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_staff_hub_room_membership_overrides_room_id"),
        "staff_hub_room_membership_overrides",
        ["room_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_staff_hub_room_membership_overrides_staff_id"),
        "staff_hub_room_membership_overrides",
        ["staff_id"],
        unique=False,
    )
    op.create_index(
        "ix_staff_hub_room_membership_overrides_staff",
        "staff_hub_room_membership_overrides",
        ["staff_id", "override_action"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_room_membership_overrides_staff",
        table_name="staff_hub_room_membership_overrides",
    )
    op.drop_index(
        op.f("ix_staff_hub_room_membership_overrides_staff_id"),
        table_name="staff_hub_room_membership_overrides",
    )
    op.drop_index(
        op.f("ix_staff_hub_room_membership_overrides_room_id"),
        table_name="staff_hub_room_membership_overrides",
    )
    op.drop_index(
        op.f("ix_staff_hub_room_membership_overrides_organization_id"),
        table_name="staff_hub_room_membership_overrides",
    )
    op.drop_table("staff_hub_room_membership_overrides")
