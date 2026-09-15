"""Distinguish normal no-text/not-required photos from OCR service failures."""
from alembic import op
import sqlalchemy as sa

revision = "047_photo_reading_states"
down_revision = "046_voice_call_invitations"
branch_labels = None
depends_on = None

def upgrade():
    op.drop_constraint("attachment_text_extractions_status_check", "attachment_text_extractions", type_="check")
    op.create_check_constraint("attachment_text_extractions_status_check", "attachment_text_extractions",
        "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed', 'no_text', 'not_required')")

def downgrade():
    # Code rollback keeps this additive constraint. Do not rewrite photo intent
    # into failed/completed solely to fit an old schema.
    count = op.get_bind().execute(sa.text("SELECT count(*) FROM attachment_text_extractions WHERE status IN ('no_text','not_required')")).scalar()
    if count:
        raise RuntimeError("Retain the additive photo-state constraint while rolling back code; existing photo decisions must be preserved.")
    op.drop_constraint("attachment_text_extractions_status_check", "attachment_text_extractions", type_="check")
    op.create_check_constraint("attachment_text_extractions_status_check", "attachment_text_extractions",
        "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed')")
