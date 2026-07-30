"""add immutable source, prototype suggestion, and reviewer confirmation

Revision ID: 002_work_item_contract
Revises: 001_smcodi_pg
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "002_work_item_contract"
down_revision = "001_smcodi_pg"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("source_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("ai_generator", sa.String(length=80), nullable=True),
    )
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("ai_generated_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("confirmed_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("confirmed_by_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "staff_hub_processing_items",
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_staff_hub_processing_items_confirmed_by",
        "staff_hub_processing_items",
        "users",
        ["confirmed_by_id"],
        ["id"],
    )
    op.create_index(
        "ix_staff_hub_processing_items_confirmed_by_id",
        "staff_hub_processing_items",
        ["confirmed_by_id"],
    )
    op.execute(
        """
        UPDATE staff_hub_processing_items item
        SET source_snapshot = jsonb_build_object(
            'message_id', message.id::text,
            'room_id', message.room_id::text,
            'room_name', room.name,
            'sender_id', message.author_user_id::text,
            'sender_name', staff.display_name,
            'resident_id', item.recipient_id::text,
            'resident_name', recipient.display_name,
            'body', message.body,
            'message_type', message.message_type,
            'attachment_ids', COALESCE(
                (
                    SELECT jsonb_agg(attachment.id::text ORDER BY attachment.created_at)
                    FROM attachments attachment
                    WHERE attachment.entity_id = message.id
                ),
                '[]'::jsonb
            ),
            'created_at', message.created_at
        )
        FROM staff_hub_messages message
        JOIN staff_hub_rooms room ON room.id = message.room_id
        JOIN users account ON account.id = message.author_user_id
        JOIN staff ON staff.id = account.staff_id
        CROSS JOIN recipients recipient
        WHERE item.source_message_id = message.id
          AND recipient.id = item.recipient_id
        """
    )
    op.alter_column(
        "staff_hub_processing_items",
        "source_snapshot",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_processing_items_confirmed_by_id",
        table_name="staff_hub_processing_items",
    )
    op.drop_constraint(
        "fk_staff_hub_processing_items_confirmed_by",
        "staff_hub_processing_items",
        type_="foreignkey",
    )
    for column_name in [
        "confirmed_at",
        "confirmed_by_id",
        "confirmed_payload",
        "ai_generated_at",
        "ai_generator",
        "source_snapshot",
    ]:
        op.drop_column("staff_hub_processing_items", column_name)
