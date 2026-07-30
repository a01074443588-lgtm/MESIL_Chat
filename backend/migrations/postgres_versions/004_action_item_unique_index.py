"""normalize action item source message uniqueness

Revision ID: 004_action_item_index
Revises: 003_chat_ops
Create Date: 2026-07-26
"""

from collections.abc import Sequence

from alembic import op


revision: str = "004_action_item_index"
down_revision: str | None = "003_chat_ops"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "staff_hub_action_items_source_message_id_key",
        "staff_hub_action_items",
        type_="unique",
    )
    op.drop_index(
        "ix_staff_hub_action_items_source_message_id",
        table_name="staff_hub_action_items",
    )
    op.create_index(
        "ix_staff_hub_action_items_source_message_id",
        "staff_hub_action_items",
        ["source_message_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_action_items_source_message_id",
        table_name="staff_hub_action_items",
    )
    op.create_index(
        "ix_staff_hub_action_items_source_message_id",
        "staff_hub_action_items",
        ["source_message_id"],
    )
    op.create_unique_constraint(
        "staff_hub_action_items_source_message_id_key",
        "staff_hub_action_items",
        ["source_message_id"],
    )
