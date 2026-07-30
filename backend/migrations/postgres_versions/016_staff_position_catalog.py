"""add organization-managed staff position catalog

Revision ID: 016_staff_position_catalog
Revises: 015_staff_directory_delete
Create Date: 2026-07-27
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "016_staff_position_catalog"
down_revision: str | None = "015_staff_directory_delete"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_position_codes",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("internal_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "internal_code",
            name="uq_staff_position_organization_code",
        ),
        sa.UniqueConstraint(
            "organization_id",
            "name",
            name="uq_staff_position_organization_name",
        ),
    )
    op.create_index(
        "ix_staff_position_codes_organization_id",
        "staff_position_codes",
        ["organization_id"],
        unique=False,
    )
    op.create_index(
        "ix_staff_position_organization_active",
        "staff_position_codes",
        ["organization_id", "is_active"],
        unique=False,
    )

    op.execute(
        """
        INSERT INTO staff_position_codes
            (organization_id, internal_code, name, sort_order, is_active)
        SELECT
            source.organization_id,
            'position_' || substr(md5(source.name), 1, 16),
            source.name,
            row_number() OVER (
                PARTITION BY source.organization_id
                ORDER BY source.name
            ) * 10,
            true
        FROM (
            SELECT organization_id, position_title AS name
            FROM staff
            WHERE position_title IS NOT NULL
              AND btrim(position_title) <> ''
            UNION
            SELECT organizations.id, defaults.name
            FROM organizations
            CROSS JOIN (
                VALUES
                    ('대표'),
                    ('원장'),
                    ('사무국장'),
                    ('선임사회복지사'),
                    ('간호팀장'),
                    ('요양팀장')
            ) AS defaults(name)
        ) AS source
        ON CONFLICT (organization_id, name) DO NOTHING
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_staff_position_organization_active",
        table_name="staff_position_codes",
    )
    op.drop_index(
        "ix_staff_position_codes_organization_id",
        table_name="staff_position_codes",
    )
    op.drop_table("staff_position_codes")
