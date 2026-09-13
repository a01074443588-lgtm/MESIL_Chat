"""Add append-only coordinate reviews for visual OCR editing.

Revision ID: 034_coordinate_reviews
Revises: 033_extraction_attempts
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "034_coordinate_reviews"
down_revision: str | None = "033_extraction_attempts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "attachment_coordinate_reviews",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("image_width", sa.Integer(), nullable=False),
        sa.Column("image_height", sa.Integer(), nullable=False),
        sa.Column("editor_version", sa.String(length=40), nullable=False),
        sa.Column("document_template", sa.String(length=80), nullable=True),
        sa.Column(
            "regions",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("editor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "image_width > 0 AND image_height > 0",
            name="attachment_coordinate_reviews_image_size_check",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"], ["attachments.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["extraction_id"],
            ["attachment_text_extractions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["editor_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "attachment_id",
            "version_number",
            name="uq_attachment_coordinate_review_version",
        ),
    )
    op.create_index(
        "ix_attachment_coordinate_reviews_organization_id",
        "attachment_coordinate_reviews",
        ["organization_id"],
    )
    op.create_index(
        "ix_attachment_coordinate_reviews_attachment_id",
        "attachment_coordinate_reviews",
        ["attachment_id"],
    )
    op.create_index(
        "ix_attachment_coordinate_reviews_extraction_id",
        "attachment_coordinate_reviews",
        ["extraction_id"],
    )
    op.create_index(
        "ix_attachment_coordinate_reviews_editor_id",
        "attachment_coordinate_reviews",
        ["editor_id"],
    )
    op.create_index(
        "ix_attachment_coordinate_reviews_attachment_version",
        "attachment_coordinate_reviews",
        ["attachment_id", "version_number"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_attachment_coordinate_reviews_attachment_version",
        table_name="attachment_coordinate_reviews",
    )
    op.drop_index(
        "ix_attachment_coordinate_reviews_editor_id",
        table_name="attachment_coordinate_reviews",
    )
    op.drop_index(
        "ix_attachment_coordinate_reviews_extraction_id",
        table_name="attachment_coordinate_reviews",
    )
    op.drop_index(
        "ix_attachment_coordinate_reviews_attachment_id",
        table_name="attachment_coordinate_reviews",
    )
    op.drop_index(
        "ix_attachment_coordinate_reviews_organization_id",
        table_name="attachment_coordinate_reviews",
    )
    op.drop_table("attachment_coordinate_reviews")
