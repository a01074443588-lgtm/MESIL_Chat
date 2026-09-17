"""Add protected living-space system rooms without converting legacy floor rooms."""

from alembic import op
import sqlalchemy as sa


revision = "050_living_space_rooms"
down_revision = "049_ai_help_rooms"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint("staff_hub_rooms_type_check", "staff_hub_rooms", type_="check")
    op.create_check_constraint(
        "staff_hub_rooms_type_check",
        "staff_hub_rooms",
        "room_type IN ('all', 'business', 'department', 'floor', 'team', 'job', 'custom', 'self', 'ai', 'living_space')",
    )
    op.create_check_constraint(
        "staff_hub_rooms_living_space_shape_check",
        "staff_hub_rooms",
        "room_type != 'living_space' OR (unit_id IS NOT NULL AND job_code IS NULL AND owner_staff_id IS NULL)",
    )
    op.drop_index("uq_staff_hub_rooms_unit", table_name="staff_hub_rooms")
    op.create_index(
        "uq_staff_hub_rooms_unit_kind",
        "staff_hub_rooms",
        ["organization_id", "room_type", "unit_id"],
        unique=True,
        postgresql_where=sa.text("unit_id IS NOT NULL"),
    )


def downgrade():
    raise RuntimeError(
        "생활공간 시스템방과 이력은 삭제하지 말고 호환 Backend로 롤백하세요."
    )
