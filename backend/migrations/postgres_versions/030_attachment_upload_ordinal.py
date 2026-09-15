"""preserve attachment upload order

Revision ID: 030_attachment_upload_ordinal
Revises: 029_smcodi_handover_outbox
Create Date: 2026-08-05
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "030_attachment_upload_ordinal"
down_revision: str | None = "029_smcodi_handover_outbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachments",
        sa.Column(
            "upload_ordinal",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.execute(
        """
        WITH ranked AS (
            SELECT
                id,
                row_number() OVER (
                    PARTITION BY entity_id
                    ORDER BY created_at, id
                ) - 1 AS ordinal
            FROM attachments
        )
        UPDATE attachments AS target
        SET upload_ordinal = ranked.ordinal
        FROM ranked
        WHERE target.id = ranked.id
        """
    )
    op.create_index(
        "ix_attachments_entity_upload_ordinal",
        "attachments",
        ["entity_id", "upload_ordinal"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_attachments_entity_upload_ordinal", table_name="attachments")
    op.drop_column("attachments", "upload_ordinal")
