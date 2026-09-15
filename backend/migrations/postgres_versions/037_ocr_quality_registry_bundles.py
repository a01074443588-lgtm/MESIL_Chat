"""Add bundle-aware OCR quality-registry classification.

Revision ID: 037_ocr_quality_registry_bundles
Revises: 036_ocr_quality_registry
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "037_ocr_quality_registry_bundles"
down_revision: str | None = "036_ocr_quality_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "ocr_quality_registry_entries_status_check",
        "ocr_quality_registry_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ocr_quality_registry_entries_status_check",
        "ocr_quality_registry_entries",
        "primary_status IN ('rules_usable', 'visual_learning_candidate', "
        "'evaluation_candidate', 'hold', 'excluded', 'hold_or_excluded')",
    )
    op.add_column(
        "ocr_quality_registry_entries",
        sa.Column("bundle_fingerprint", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "ocr_quality_registry_entries",
        sa.Column("bundle_size", sa.Integer(), nullable=True),
    )
    op.add_column(
        "ocr_quality_registry_entries",
        sa.Column("member_primary_status", sa.String(length=40), nullable=True),
    )
    op.create_check_constraint(
        "ocr_quality_registry_entries_member_status_check",
        "ocr_quality_registry_entries",
        "member_primary_status IS NULL OR member_primary_status IN "
        "('rules_usable', 'visual_learning_candidate', "
        "'evaluation_candidate', 'hold', 'excluded')",
    )
    op.create_check_constraint(
        "ocr_quality_registry_entries_bundle_size_check",
        "ocr_quality_registry_entries",
        "bundle_size IS NULL OR bundle_size > 0",
    )
    op.create_index(
        "ix_ocr_quality_registry_entries_bundle_fingerprint",
        "ocr_quality_registry_entries",
        ["bundle_fingerprint"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ocr_quality_registry_entries_bundle_fingerprint",
        table_name="ocr_quality_registry_entries",
    )
    op.drop_constraint(
        "ocr_quality_registry_entries_bundle_size_check",
        "ocr_quality_registry_entries",
        type_="check",
    )
    op.drop_constraint(
        "ocr_quality_registry_entries_member_status_check",
        "ocr_quality_registry_entries",
        type_="check",
    )
    op.drop_column("ocr_quality_registry_entries", "member_primary_status")
    op.drop_column("ocr_quality_registry_entries", "bundle_size")
    op.drop_column("ocr_quality_registry_entries", "bundle_fingerprint")
    op.drop_constraint(
        "ocr_quality_registry_entries_status_check",
        "ocr_quality_registry_entries",
        type_="check",
    )
    op.create_check_constraint(
        "ocr_quality_registry_entries_status_check",
        "ocr_quality_registry_entries",
        "primary_status IN ('rules_usable', 'visual_learning_candidate', "
        "'evaluation_candidate', 'hold_or_excluded')",
    )
