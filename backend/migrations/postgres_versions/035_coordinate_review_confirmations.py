"""Link append-only coordinate versions to staff confirmation events.

Revision ID: 035_coordinate_confirmations
Revises: 034_coordinate_reviews
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "035_coordinate_confirmations"
down_revision: str | None = "034_coordinate_reviews"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "staff_hub_ocr_correction_events",
        sa.Column(
            "coordinate_review_id",
            postgresql.UUID(as_uuid=True),
            nullable=True,
        ),
    )
    op.create_foreign_key(
        "fk_ocr_correction_event_coordinate_review",
        "staff_hub_ocr_correction_events",
        "attachment_coordinate_reviews",
        ["coordinate_review_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_events_coordinate_review",
        "staff_hub_ocr_correction_events",
        ["coordinate_review_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_ocr_correction_events_coordinate_review",
        table_name="staff_hub_ocr_correction_events",
    )
    op.drop_constraint(
        "fk_ocr_correction_event_coordinate_review",
        "staff_hub_ocr_correction_events",
        type_="foreignkey",
    )
    op.drop_column(
        "staff_hub_ocr_correction_events",
        "coordinate_review_id",
    )
