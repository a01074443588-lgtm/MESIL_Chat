"""add roster-aware OCR suggestion drafts

Revision ID: 032_roster_aware_ocr_drafts
Revises: 031_mobile_push_devices
Create Date: 2026-08-12
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "032_roster_aware_ocr_drafts"
down_revision: str | None = "031_mobile_push_devices"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_text_extractions",
        sa.Column("suggested_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "attachment_text_extractions",
        sa.Column("suggested_service_context", sa.String(length=30), nullable=True),
    )
    op.add_column(
        "attachment_text_extractions",
        sa.Column(
            "suggestion_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("attachment_text_extractions", "suggestion_details")
    op.drop_column("attachment_text_extractions", "suggested_service_context")
    op.drop_column("attachment_text_extractions", "suggested_text")
