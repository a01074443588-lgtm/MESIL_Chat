"""store staff-approved OCR token corrections

Revision ID: 008_ocr_correction_memory
Revises: 007_multi_resident_links
Create Date: 2026-07-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "008_ocr_correction_memory"
down_revision: str | None = "007_multi_resident_links"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_ocr_correction_memories",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("recognized_text", sa.String(length=80), nullable=False),
        sa.Column("corrected_text", sa.String(length=80), nullable=False),
        sa.Column(
            "occurrence_count",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column("last_reviewed_by_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["last_reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "recognized_text",
            "corrected_text",
            name="uq_staff_hub_ocr_correction_memory",
        ),
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_memories_organization_id",
        "staff_hub_ocr_correction_memories",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_memories_lookup",
        "staff_hub_ocr_correction_memories",
        ["organization_id", "occurrence_count", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_ocr_correction_memories_lookup",
        table_name="staff_hub_ocr_correction_memories",
    )
    op.drop_index(
        "ix_staff_hub_ocr_correction_memories_organization_id",
        table_name="staff_hub_ocr_correction_memories",
    )
    op.drop_table("staff_hub_ocr_correction_memories")
