"""add managed rooms, comment state, action items, and room digests

Revision ID: 003_chat_ops
Revises: 002_work_item_contract
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "003_chat_ops"
down_revision = "002_work_item_contract"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "recipients",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.create_index(
        "ix_recipients_sort_order",
        "recipients",
        ["sort_order"],
    )
    op.add_column(
        "staff_hub_rooms",
        sa.Column(
            "resident_scope",
            sa.String(length=30),
            server_default="all",
            nullable=False,
        ),
    )
    op.add_column(
        "staff_hub_rooms",
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
    )
    op.execute(
        """
        UPDATE staff_hub_rooms
        SET resident_scope = 'floor'
        WHERE room_type = 'floor'
        """
    )

    op.create_table(
        "staff_hub_message_thread_views",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("message_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("last_viewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["staff_hub_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_staff_hub_message_thread_view_user",
        ),
    )
    op.create_index(
        "ix_staff_hub_message_thread_views_lookup",
        "staff_hub_message_thread_views",
        ["message_id", "user_id", "last_viewed_at"],
    )
    op.create_index(
        "ix_staff_hub_message_thread_views_message_id",
        "staff_hub_message_thread_views",
        ["message_id"],
    )
    op.create_index(
        "ix_staff_hub_message_thread_views_organization_id",
        "staff_hub_message_thread_views",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_message_thread_views_user_id",
        "staff_hub_message_thread_views",
        ["user_id"],
    )

    op.create_table(
        "staff_hub_action_items",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("action_type", sa.String(length=30), nullable=False),
        sa.Column("assignee_user_id", sa.Uuid(), nullable=True),
        sa.Column("assignee_unit_id", sa.Uuid(), nullable=True),
        sa.Column(
            "priority",
            sa.String(length=20),
            server_default="normal",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=30),
            server_default="assigned",
            nullable=False,
        ),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("is_test_data", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action_type IN ('handover', 'cooperation', 'confirmation')",
            name="staff_hub_action_items_type_check",
        ),
        sa.CheckConstraint(
            "priority IN ('normal', 'important', 'urgent')",
            name="staff_hub_action_items_priority_check",
        ),
        sa.CheckConstraint(
            "status IN ('assigned', 'acknowledged', 'in_progress', 'completed')",
            name="staff_hub_action_items_status_check",
        ),
        sa.ForeignKeyConstraint(["assignee_unit_id"], ["organization_units.id"]),
        sa.ForeignKeyConstraint(["assignee_user_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["staff_hub_messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_message_id"),
    )
    for column_name in [
        "organization_id",
        "source_message_id",
        "assignee_user_id",
        "assignee_unit_id",
        "created_by_id",
        "action_type",
        "priority",
        "status",
    ]:
        op.create_index(
            f"ix_staff_hub_action_items_{column_name}",
            "staff_hub_action_items",
            [column_name],
        )
    op.create_index(
        "ix_staff_hub_action_items_assignee_status",
        "staff_hub_action_items",
        ["assignee_user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_staff_hub_action_items_unit_status",
        "staff_hub_action_items",
        ["assignee_unit_id", "status", "created_at"],
    )

    op.create_table(
        "staff_hub_room_digests",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("message_count", sa.Integer(), nullable=False),
        sa.Column("comment_count", sa.Integer(), nullable=False),
        sa.Column("resident_count", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column(
            "major_points",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "document_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "risk_counts",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "source_message_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "generator",
            sa.String(length=80),
            server_default="prototype-room-digest-v1",
            nullable=False,
        ),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["room_id"],
            ["staff_hub_rooms.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "room_id",
            "period_start",
            "period_end",
            name="uq_staff_hub_room_digest_period",
        ),
    )
    op.create_index(
        "ix_staff_hub_room_digests_organization_id",
        "staff_hub_room_digests",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_room_digests_room_id",
        "staff_hub_room_digests",
        ["room_id"],
    )
    op.create_index(
        "ix_staff_hub_room_digests_period_start",
        "staff_hub_room_digests",
        ["period_start"],
    )
    op.create_index(
        "ix_staff_hub_room_digests_period_end",
        "staff_hub_room_digests",
        ["period_end"],
    )
    op.create_index(
        "ix_staff_hub_room_digests_period",
        "staff_hub_room_digests",
        ["organization_id", "period_start", "period_end"],
    )


def downgrade() -> None:
    op.drop_table("staff_hub_room_digests")
    op.drop_table("staff_hub_action_items")
    op.drop_table("staff_hub_message_thread_views")
    op.drop_column("staff_hub_rooms", "sort_order")
    op.drop_column("staff_hub_rooms", "resident_scope")
    op.drop_index("ix_recipients_sort_order", table_name="recipients")
    op.drop_column("recipients", "sort_order")
