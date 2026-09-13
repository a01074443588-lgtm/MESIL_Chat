"""Add immutable DEV-only OCR quality-registry snapshots.

Revision ID: 036_ocr_quality_registry
Revises: 035_coordinate_confirmations
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "036_ocr_quality_registry"
down_revision: str | None = "035_coordinate_confirmations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ocr_quality_registry_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("assessment_run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("attachment_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("extraction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.String(length=40), nullable=False),
        sa.Column("primary_status", sa.String(length=40), nullable=False),
        sa.Column("rules_usable", sa.Boolean(), nullable=False),
        sa.Column("visual_learning_candidate", sa.Boolean(), nullable=False),
        sa.Column("evaluation_candidate", sa.Boolean(), nullable=False),
        sa.Column("holdout_eligible", sa.Boolean(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "primary_status IN ('rules_usable', 'visual_learning_candidate', "
            "'evaluation_candidate', 'hold_or_excluded')",
            name="ocr_quality_registry_entries_status_check",
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["extraction_id"], ["attachment_text_extractions.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "assessment_run_id",
            "attachment_id",
            name="uq_ocr_quality_registry_run_attachment",
        ),
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_assessment_run_id",
        "ocr_quality_registry_entries",
        ["assessment_run_id"],
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_organization_id",
        "ocr_quality_registry_entries",
        ["organization_id"],
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_attachment_id",
        "ocr_quality_registry_entries",
        ["attachment_id"],
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_extraction_id",
        "ocr_quality_registry_entries",
        ["extraction_id"],
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_org_created",
        "ocr_quality_registry_entries",
        ["organization_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ocr_quality_registry_entries_org_created",
        table_name="ocr_quality_registry_entries",
    )
    op.drop_index("ix_ocr_quality_registry_entries_extraction_id", table_name="ocr_quality_registry_entries")
    op.drop_index("ix_ocr_quality_registry_entries_attachment_id", table_name="ocr_quality_registry_entries")
    op.drop_index("ix_ocr_quality_registry_entries_organization_id", table_name="ocr_quality_registry_entries")
    op.drop_index("ix_ocr_quality_registry_entries_assessment_run_id", table_name="ocr_quality_registry_entries")
    op.drop_table("ocr_quality_registry_entries")
