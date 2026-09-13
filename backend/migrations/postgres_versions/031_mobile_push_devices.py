"""add session-bound Android mobile push devices

Revision ID: 031_mobile_push_devices
Revises: 030_attachment_upload_ordinal
Create Date: 2026-08-10
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "031_mobile_push_devices"
down_revision: str | None = "030_attachment_upload_ordinal"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_mobile_push_devices",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("login_session_id", sa.Uuid(), nullable=False),
        sa.Column("installation_id", sa.String(length=80), nullable=False),
        sa.Column("platform", sa.String(length=20), server_default="android", nullable=False),
        sa.Column("token", sa.Text(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("app_version", sa.String(length=40), nullable=True),
        sa.Column("user_agent", sa.String(length=300), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("failure_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["login_session_id"], ["auth_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("installation_id", name="uq_staff_hub_mobile_push_devices_installation_id"),
    )
    op.create_index("ix_staff_hub_mobile_push_devices_organization_id", "staff_hub_mobile_push_devices", ["organization_id"])
    op.create_index("ix_staff_hub_mobile_push_devices_user_id", "staff_hub_mobile_push_devices", ["user_id"])
    op.create_index("ix_staff_hub_mobile_push_devices_login_session_id", "staff_hub_mobile_push_devices", ["login_session_id"])
    op.create_index("ix_staff_hub_mobile_push_devices_token_hash", "staff_hub_mobile_push_devices", ["token_hash"])
    op.create_index("ix_staff_hub_mobile_push_devices_is_active", "staff_hub_mobile_push_devices", ["is_active"])
    op.create_index("ix_staff_hub_mobile_push_devices_user_active", "staff_hub_mobile_push_devices", ["user_id", "is_active"])


def downgrade() -> None:
    op.drop_table("staff_hub_mobile_push_devices")
