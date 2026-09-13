"""Preserve document/audio automatic and staff versions without rewriting rows."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "048_attachment_text_reviews"
down_revision = "047_photo_reading_states"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("attachment_text_extractions_status_check", "attachment_text_extractions", type_="check")
    op.create_check_constraint("attachment_text_extractions_status_check", "attachment_text_extractions",
        "status IN ('pending', 'processing', 'completed', 'failed', 'reviewed', 'no_text', 'not_required', 'unsupported')")
    op.create_table("attachment_text_revisions",
        sa.Column("id",sa.Uuid(),primary_key=True,server_default=sa.text("gen_random_uuid()")),
        sa.Column("organization_id",sa.Uuid(),sa.ForeignKey("organizations.id"),nullable=False),
        sa.Column("attachment_id",sa.Uuid(),sa.ForeignKey("attachments.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("extraction_id",sa.Uuid(),sa.ForeignKey("attachment_text_extractions.id",ondelete="RESTRICT"),nullable=False),
        sa.Column("revision",sa.Integer(),nullable=False),
        sa.Column("kind",sa.String(30),nullable=False),
        sa.Column("text_content",sa.Text(),nullable=False),
        sa.Column("text_sha256",sa.String(64),nullable=False),
        sa.Column("original_file_sha256",sa.String(64)),
        sa.Column("provider",sa.String(40),nullable=False),
        sa.Column("model_name",sa.String(120),nullable=False),
        sa.Column("source_metadata",JSONB(),nullable=False),
        sa.Column("selected_resident_ids",JSONB(),nullable=False),
        sa.Column("reviewed_by_id",sa.Uuid(),sa.ForeignKey("users.id")),
        sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),
        sa.UniqueConstraint("extraction_id","revision",name="uq_attachment_text_revision"),
        sa.CheckConstraint("kind IN ('automatic', 'staff_review')",name="attachment_text_revision_kind_check"))
    for column in ("organization_id","attachment_id","extraction_id"):
        op.create_index("ix_attachment_text_revisions_"+column,"attachment_text_revisions",[column])


def downgrade():
    # Keep the additive table and all new staff records during code rollback.
    raise RuntimeError("Use compatible code rollback; retain attachment text revisions and migration 048.")
