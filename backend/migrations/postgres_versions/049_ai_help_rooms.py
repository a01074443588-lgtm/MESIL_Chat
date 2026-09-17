"""Add private per-staff AI help rooms and idempotent AI messages."""
from alembic import op
import sqlalchemy as sa


revision = "049_ai_help_rooms"
down_revision = "048_attachment_text_reviews"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("staff_hub_rooms_type_check", "staff_hub_rooms", type_="check")
    op.create_check_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        "room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom', 'self', 'ai')",
    )
    op.create_index(
        "uq_staff_hub_rooms_ai",
        "staff_hub_rooms",
        ["organization_id", "owner_staff_id"],
        unique=True,
        postgresql_where=sa.text("room_type = 'ai' AND owner_staff_id IS NOT NULL"),
    )
    op.add_column(
        "staff_hub_messages",
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
    )
    op.create_index(
        "uq_staff_hub_messages_ai_client_request",
        "staff_hub_messages",
        ["organization_id", "author_user_id", "client_request_id"],
        unique=True,
        postgresql_where=sa.text("client_request_id IS NOT NULL"),
    )


def downgrade():
    raise RuntimeError("AI 도움방 데이터는 삭제하지 말고 기능 플래그를 끈 뒤 호환 Backend로 롤백하세요.")
