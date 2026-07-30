"""add session-bound web push subscriptions

Revision ID: 017_web_push_subscriptions
Revises: 016_staff_position_catalog
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "017_web_push_subscriptions"
down_revision: str | None = "016_staff_position_catalog"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_push_subscriptions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("login_session_id", sa.Uuid(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("endpoint_hash", sa.String(length=64), nullable=False),
        sa.Column("p256dh", sa.Text(), nullable=False),
        sa.Column("auth", sa.Text(), nullable=False),
        sa.Column("expiration_time", sa.BigInteger(), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "failure_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["login_session_id"],
            ["auth_sessions.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "endpoint_hash",
            name="uq_staff_hub_push_subscriptions_endpoint_hash",
        ),
    )
    op.create_index(
        "ix_staff_hub_push_subscriptions_organization_id",
        "staff_hub_push_subscriptions",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_push_subscriptions_user_id",
        "staff_hub_push_subscriptions",
        ["user_id"],
    )
    op.create_index(
        "ix_staff_hub_push_subscriptions_login_session_id",
        "staff_hub_push_subscriptions",
        ["login_session_id"],
    )
    op.create_index(
        "ix_staff_hub_push_subscriptions_is_active",
        "staff_hub_push_subscriptions",
        ["is_active"],
    )
    op.create_index(
        "ix_staff_hub_push_subscriptions_user_active",
        "staff_hub_push_subscriptions",
        ["user_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_push_subscriptions_user_active",
        table_name="staff_hub_push_subscriptions",
    )
    op.drop_index(
        "ix_staff_hub_push_subscriptions_is_active",
        table_name="staff_hub_push_subscriptions",
    )
    op.drop_index(
        "ix_staff_hub_push_subscriptions_login_session_id",
        table_name="staff_hub_push_subscriptions",
    )
    op.drop_index(
        "ix_staff_hub_push_subscriptions_user_id",
        table_name="staff_hub_push_subscriptions",
    )
    op.drop_index(
        "ix_staff_hub_push_subscriptions_organization_id",
        table_name="staff_hub_push_subscriptions",
    )
    op.drop_table("staff_hub_push_subscriptions")
