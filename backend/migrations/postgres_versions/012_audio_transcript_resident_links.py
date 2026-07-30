"""allow resident candidates detected in local audio transcripts

Revision ID: 012_audio_transcript_links
Revises: 011_personal_self_rooms
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op


revision: str = "012_audio_transcript_links"
down_revision: str | None = "011_personal_self_rooms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(
        "staff_hub_message_recipient_links_source_check",
        "staff_hub_message_recipient_links",
        type_="check",
    )
    op.create_check_constraint(
        "staff_hub_message_recipient_links_source_check",
        "staff_hub_message_recipient_links",
        "source IN ('manual', 'text_exact', 'ocr_exact', 'audio_transcript')",
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM staff_hub_message_recipient_links
        WHERE source = 'audio_transcript'
        """
    )
    op.drop_constraint(
        "staff_hub_message_recipient_links_source_check",
        "staff_hub_message_recipient_links",
        type_="check",
    )
    op.create_check_constraint(
        "staff_hub_message_recipient_links_source_check",
        "staff_hub_message_recipient_links",
        "source IN ('manual', 'text_exact', 'ocr_exact')",
    )
