"""add staff conversation ownership, admin reauthentication, and message recall

Revision ID: 026_staff_conversation_controls
Revises: 025_staff_application_manifest
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "026_staff_conversation_controls"
down_revision: str | None = "025_staff_application_manifest"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column(
            "admin_conversation_access_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_auth_sessions_admin_conversation_access_expires_at",
        "auth_sessions",
        ["admin_conversation_access_expires_at"],
    )

    op.add_column(
        "staff_hub_messages",
        sa.Column(
            "lifecycle_status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
    )
    op.add_column(
        "staff_hub_messages",
        sa.Column("recalled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "staff_hub_messages",
        sa.Column("recalled_by_user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "staff_hub_messages",
        sa.Column("recall_reason", sa.String(length=500), nullable=True),
    )
    op.add_column(
        "staff_hub_messages",
        sa.Column("recall_group_id", sa.Uuid(), nullable=True),
    )
    op.create_check_constraint(
        "staff_hub_messages_lifecycle_status_check",
        "staff_hub_messages",
        "lifecycle_status IN ('active', 'recalled')",
    )
    op.create_foreign_key(
        "fk_staff_hub_messages_recalled_by_user_id_users",
        "staff_hub_messages",
        "users",
        ["recalled_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_staff_hub_messages_lifecycle",
        "staff_hub_messages",
        ["lifecycle_status", "created_at"],
    )
    op.create_index(
        "ix_staff_hub_messages_recalled_at",
        "staff_hub_messages",
        ["recalled_at"],
    )
    op.create_index(
        "ix_staff_hub_messages_recalled_by_user_id",
        "staff_hub_messages",
        ["recalled_by_user_id"],
    )
    op.create_index(
        "ix_staff_hub_messages_recall_group",
        "staff_hub_messages",
        ["recall_group_id"],
    )

    # 이미 존재하는 직접선택 방은 개설자가 현재 참여 중일 때만 개설자의
    # 직원 연결을 우선 방장으로 사용한다. 개설자가 관리 목적으로 방을 만들고
    # 직접 참여하지 않은 경우에는 현재 참여자 중 가장 먼저 참여한 직원을 쓴다.
    op.execute(
        sa.text(
            """
            UPDATE staff_hub_rooms AS room
            SET owner_staff_id = COALESCE(
                (
                    SELECT actor.staff_id
                    FROM users AS actor
                    WHERE actor.id = room.created_by
                      AND actor.organization_id = room.organization_id
                      AND actor.staff_id IS NOT NULL
                      AND EXISTS (
                          SELECT 1
                          FROM staff_hub_room_memberships AS actor_membership
                          WHERE actor_membership.room_id = room.id
                            AND actor_membership.staff_id = actor.staff_id
                            AND actor_membership.left_at IS NULL
                      )
                    LIMIT 1
                ),
                (
                    SELECT membership.staff_id
                    FROM staff_hub_room_memberships AS membership
                    WHERE membership.room_id = room.id
                      AND membership.left_at IS NULL
                    ORDER BY membership.joined_at, membership.id
                    LIMIT 1
                )
            )
            WHERE room.room_type = 'custom'
              AND room.owner_staff_id IS NULL
            """
        )
    )
    op.create_index(
        "ix_staff_hub_rooms_custom_owner",
        "staff_hub_rooms",
        ["organization_id", "owner_staff_id"],
        postgresql_where=sa.text(
            "room_type = 'custom' AND owner_staff_id IS NOT NULL"
        ),
    )


def downgrade() -> None:
    bind = op.get_bind()
    recalled_count = bind.execute(
        sa.text(
            "SELECT COUNT(*) FROM staff_hub_messages "
            "WHERE lifecycle_status = 'recalled'"
        )
    ).scalar_one()
    if recalled_count:
        raise RuntimeError(
            "recalled messages exist; refusing destructive downgrade"
        )

    op.drop_index("ix_staff_hub_rooms_custom_owner", table_name="staff_hub_rooms")
    op.drop_index("ix_staff_hub_messages_recall_group", table_name="staff_hub_messages")
    op.drop_index(
        "ix_staff_hub_messages_recalled_by_user_id",
        table_name="staff_hub_messages",
    )
    op.drop_index("ix_staff_hub_messages_recalled_at", table_name="staff_hub_messages")
    op.drop_index("ix_staff_hub_messages_lifecycle", table_name="staff_hub_messages")
    op.drop_constraint(
        "fk_staff_hub_messages_recalled_by_user_id_users",
        "staff_hub_messages",
        type_="foreignkey",
    )
    op.drop_constraint(
        "staff_hub_messages_lifecycle_status_check",
        "staff_hub_messages",
        type_="check",
    )
    op.drop_column("staff_hub_messages", "recall_group_id")
    op.drop_column("staff_hub_messages", "recall_reason")
    op.drop_column("staff_hub_messages", "recalled_by_user_id")
    op.drop_column("staff_hub_messages", "recalled_at")
    op.drop_column("staff_hub_messages", "lifecycle_status")
    op.drop_index(
        "ix_auth_sessions_admin_conversation_access_expires_at",
        table_name="auth_sessions",
    )
    op.drop_column("auth_sessions", "admin_conversation_access_expires_at")
