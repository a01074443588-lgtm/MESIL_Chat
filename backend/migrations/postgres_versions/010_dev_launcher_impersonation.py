"""add development launcher impersonation marker

Revision ID: 010_dev_launcher_impersonation
Revises: 009_resident_sync_batches
Create Date: 2026-07-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "010_dev_launcher_impersonation"
down_revision: str | None = "009_resident_sync_batches"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "auth_sessions",
        sa.Column("impersonated_by_user_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_auth_sessions_impersonated_by_user_id_users",
        "auth_sessions",
        "users",
        ["impersonated_by_user_id"],
        ["id"],
    )
    op.create_index(
        "ix_auth_sessions_impersonated_by_user_id",
        "auth_sessions",
        ["impersonated_by_user_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_auth_sessions_impersonated_by_user_id",
        table_name="auth_sessions",
    )
    op.drop_constraint(
        "fk_auth_sessions_impersonated_by_user_id_users",
        "auth_sessions",
        type_="foreignkey",
    )
    op.drop_column("auth_sessions", "impersonated_by_user_id")
