"""align document draft index names with model metadata

Revision ID: 020_document_draft_index_names
Revises: 019_work_item_document_drafts
Create Date: 2026-07-28
"""

from collections.abc import Sequence

from alembic import op


revision: str = "020_document_draft_index_names"
down_revision: str | None = "019_work_item_document_drafts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_INDEX_RENAMES = (
    (
        "ix_work_item_document_drafts_organization_id",
        "ix_staff_hub_work_item_document_drafts_organization_id",
    ),
    (
        "ix_work_item_document_drafts_work_item_id",
        "ix_staff_hub_work_item_document_drafts_work_item_id",
    ),
    (
        "ix_work_item_document_drafts_document_type",
        "ix_staff_hub_work_item_document_drafts_document_type",
    ),
    (
        "ix_work_item_document_drafts_status",
        "ix_staff_hub_work_item_document_drafts_status",
    ),
    (
        "ix_work_item_document_drafts_is_current",
        "ix_staff_hub_work_item_document_drafts_is_current",
    ),
    (
        "ix_work_item_document_drafts_approved_by_id",
        "ix_staff_hub_work_item_document_drafts_approved_by_id",
    ),
)


def upgrade() -> None:
    for old_name, new_name in _INDEX_RENAMES:
        op.execute(f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"')


def downgrade() -> None:
    for old_name, new_name in reversed(_INDEX_RENAMES):
        op.execute(f'ALTER INDEX "{new_name}" RENAME TO "{old_name}"')
