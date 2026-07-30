"""add residents, message detail, photos, and workdesk

Revision ID: a81d4c70b2f9
Revises: f00e52892218
Create Date: 2026-07-26

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a81d4c70b2f9"
down_revision: Union[str, Sequence[str], None] = "f00e52892218"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "can_process_records",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.create_index(
        op.f("ix_users_can_process_records"),
        "users",
        ["can_process_records"],
        unique=False,
    )

    op.create_table(
        "residents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("service_type", sa.String(length=30), nullable=False),
        sa.Column("floor_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["floor_id"], ["org_units.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "service_type",
            "display_name",
            name="uq_resident_service_name",
        ),
    )
    op.create_index(
        "ix_residents_active_floor",
        "residents",
        ["is_active", "floor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_residents_floor_id"),
        "residents",
        ["floor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_residents_is_active"),
        "residents",
        ["is_active"],
        unique=False,
    )
    op.create_index(
        op.f("ix_residents_service_type"),
        "residents",
        ["service_type"],
        unique=False,
    )

    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.add_column(sa.Column("resident_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_messages_resident_id_residents",
            "residents",
            ["resident_id"],
            ["id"],
        )
        batch_op.create_index(
            batch_op.f("ix_messages_resident_id"),
            ["resident_id"],
            unique=False,
        )

    op.create_table(
        "message_attachments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("uploader_id", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(length=100), nullable=False),
        sa.Column("original_name", sa.String(length=255), nullable=False),
        sa.Column("mime_type", sa.String(length=80), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["uploader_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index(
        "ix_message_attachments_message",
        "message_attachments",
        ["message_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_attachments_message_id"),
        "message_attachments",
        ["message_id"],
        unique=False,
    )

    op.create_table(
        "message_comments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("author_id", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["author_id"], ["users.id"]),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_message_comments_message_id_id",
        "message_comments",
        ["message_id", "id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_comments_message_id"),
        "message_comments",
        ["message_id"],
        unique=False,
    )

    op.create_table(
        "message_read_receipts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "message_id",
            "user_id",
            name="uq_message_read_user",
        ),
    )
    op.create_index(
        "ix_message_receipts_message_read",
        "message_read_receipts",
        ["message_id", "read_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_read_receipts_message_id"),
        "message_read_receipts",
        ["message_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_message_read_receipts_user_id"),
        "message_read_receipts",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "work_items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("source_message_id", sa.Integer(), nullable=False),
        sa.Column("resident_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("document_types", sa.JSON(), nullable=True),
        sa.Column("processing_notes", sa.Text(), nullable=True),
        sa.Column("handled_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["handled_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["resident_id"], ["residents.id"]),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["messages.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_work_items_status_created",
        "work_items",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_items_resident_id"),
        "work_items",
        ["resident_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_work_items_source_message_id"),
        "work_items",
        ["source_message_id"],
        unique=True,
    )
    op.create_index(
        op.f("ix_work_items_status"),
        "work_items",
        ["status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_work_items_status"), table_name="work_items")
    op.drop_index(
        op.f("ix_work_items_source_message_id"),
        table_name="work_items",
    )
    op.drop_index(op.f("ix_work_items_resident_id"), table_name="work_items")
    op.drop_index("ix_work_items_status_created", table_name="work_items")
    op.drop_table("work_items")

    op.drop_index(
        op.f("ix_message_read_receipts_user_id"),
        table_name="message_read_receipts",
    )
    op.drop_index(
        op.f("ix_message_read_receipts_message_id"),
        table_name="message_read_receipts",
    )
    op.drop_index(
        "ix_message_receipts_message_read",
        table_name="message_read_receipts",
    )
    op.drop_table("message_read_receipts")

    op.drop_index(
        op.f("ix_message_comments_message_id"),
        table_name="message_comments",
    )
    op.drop_index(
        "ix_message_comments_message_id_id",
        table_name="message_comments",
    )
    op.drop_table("message_comments")

    op.drop_index(
        op.f("ix_message_attachments_message_id"),
        table_name="message_attachments",
    )
    op.drop_index(
        "ix_message_attachments_message",
        table_name="message_attachments",
    )
    op.drop_table("message_attachments")

    with op.batch_alter_table("messages", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_messages_resident_id"))
        batch_op.drop_constraint(
            "fk_messages_resident_id_residents",
            type_="foreignkey",
        )
        batch_op.drop_column("resident_id")

    op.drop_index(op.f("ix_residents_service_type"), table_name="residents")
    op.drop_index(op.f("ix_residents_is_active"), table_name="residents")
    op.drop_index(op.f("ix_residents_floor_id"), table_name="residents")
    op.drop_index("ix_residents_active_floor", table_name="residents")
    op.drop_table("residents")

    op.drop_index(op.f("ix_users_can_process_records"), table_name="users")
    op.drop_column("users", "can_process_records")
