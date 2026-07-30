"""add append-only OCR correction events

Revision ID: 018_ocr_correction_events
Revises: 017_web_push_subscriptions
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "018_ocr_correction_events"
down_revision: str | None = "017_web_push_subscriptions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "attachment_text_extractions",
        sa.Column("original_extracted_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "attachment_text_extractions",
        sa.Column(
            "visual_signature",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE attachment_text_extractions
        SET original_extracted_text = extracted_text
        WHERE original_extracted_text IS NULL
          AND extracted_text IS NOT NULL
        """
    )
    op.create_table(
        "staff_hub_ocr_correction_events",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_id", sa.Uuid(), nullable=True),
        sa.Column("attachment_id", sa.Uuid(), nullable=True),
        sa.Column("source_message_id", sa.Uuid(), nullable=True),
        sa.Column("source_writer_id", sa.Uuid(), nullable=True),
        sa.Column("reviewed_by_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(length=30), nullable=False),
        sa.Column("raw_text", sa.Text(), nullable=False),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column(
            "correction_pairs",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("content_type", sa.String(length=40), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model_name", sa.String(length=120), nullable=False),
        sa.Column(
            "visual_signature",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("selected_candidate_id", sa.String(length=120), nullable=True),
        sa.Column("confirmed", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "decision IN "
            "('keep_raw', 'apply_candidate', 'direct_edit', 'needs_review')",
            name="staff_hub_ocr_correction_events_decision_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["attachment_text_extractions.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["attachments.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["staff_hub_messages.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_writer_id"],
            ["users.id"],
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(["reviewed_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_events_organization_id",
        "staff_hub_ocr_correction_events",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_events_org_confirmed",
        "staff_hub_ocr_correction_events",
        ["organization_id", "confirmed", "created_at"],
    )
    op.create_index(
        "ix_staff_hub_ocr_correction_events_extraction",
        "staff_hub_ocr_correction_events",
        ["extraction_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_hub_ocr_correction_events_extraction",
        table_name="staff_hub_ocr_correction_events",
    )
    op.drop_index(
        "ix_staff_hub_ocr_correction_events_org_confirmed",
        table_name="staff_hub_ocr_correction_events",
    )
    op.drop_index(
        "ix_staff_hub_ocr_correction_events_organization_id",
        table_name="staff_hub_ocr_correction_events",
    )
    op.drop_table("staff_hub_ocr_correction_events")
    op.drop_column("attachment_text_extractions", "visual_signature")
    op.drop_column("attachment_text_extractions", "original_extracted_text")
