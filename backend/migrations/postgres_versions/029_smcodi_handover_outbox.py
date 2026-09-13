"""add preview-only SMCODI handover outbox

Revision ID: 029_smcodi_handover_outbox
Revises: 028_ai_assist
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "029_smcodi_handover_outbox"
down_revision: str | None = "028_ai_assist"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "smcodi_handover_target_mappings",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("local_resident_id", sa.Uuid(), nullable=False),
        # 아래 UUID 3종은 SMCODI 쪽 식별자이므로 이 데이터베이스에 FK를 걸지 않는다.
        sa.Column("target_recipient_id", sa.Uuid(), nullable=False),
        sa.Column("target_unit_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("verified_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "target_unit_id IS NOT NULL OR assigned_user_id IS NOT NULL",
            name="smcodi_handover_target_mapping_target_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["local_resident_id"],
            ["recipients.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_smcodi_handover_target_mapping_active_resident",
        "smcodi_handover_target_mappings",
        ["organization_id", "local_resident_id"],
        unique=True,
        postgresql_where=sa.text("is_active"),
    )
    op.create_index(
        "ix_smcodi_handover_target_mapping_target_recipient",
        "smcodi_handover_target_mappings",
        ["organization_id", "target_recipient_id"],
        unique=False,
    )

    op.create_table(
        "smcodi_handover_outbox",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("source_record_id", sa.Uuid(), nullable=False),
        sa.Column("source_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "contract_name",
            sa.String(length=80),
            server_default=sa.text("'smcodi_staff_hub_handover_v1'"),
            nullable=False,
        ),
        # 아래 UUID 3종은 SMCODI 쪽 식별자이므로 이 데이터베이스에 FK를 걸지 않는다.
        sa.Column("target_mapping_id", sa.Uuid(), nullable=True),
        sa.Column("target_recipient_id", sa.Uuid(), nullable=True),
        sa.Column("target_unit_id", sa.Uuid(), nullable=True),
        sa.Column("assigned_user_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column(
            "validation_issues",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "contract_name = 'smcodi_staff_hub_handover_v1'",
            name="smcodi_handover_outbox_contract_check",
        ),
        sa.CheckConstraint(
            "status IN ('ready', 'blocked')",
            name="smcodi_handover_outbox_status_check",
        ),
        sa.CheckConstraint(
            "source_version > 0",
            name="smcodi_handover_outbox_source_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_record_id"],
            ["staff_hub_confirmed_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_version_id"],
            ["staff_hub_confirmed_record_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["target_mapping_id"],
            ["smcodi_handover_target_mappings.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "organization_id",
            "idempotency_key",
            name="uq_smcodi_handover_outbox_idempotency",
        ),
    )
    op.create_index(
        "ix_smcodi_handover_outbox_org_status_created",
        "smcodi_handover_outbox",
        ["organization_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_smcodi_handover_outbox_source",
        "smcodi_handover_outbox",
        ["source_record_id", "source_version"],
        unique=False,
    )


def downgrade() -> None:
    bind = op.get_bind()
    outbox_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM smcodi_handover_outbox")
    ).scalar_one()
    mapping_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM smcodi_handover_target_mappings")
    ).scalar_one()
    if outbox_count or mapping_count:
        raise RuntimeError(
            "SMCODI preview or verified mapping records exist; refusing destructive downgrade"
        )

    op.drop_index(
        "ix_smcodi_handover_outbox_source",
        table_name="smcodi_handover_outbox",
    )
    op.drop_index(
        "ix_smcodi_handover_outbox_org_status_created",
        table_name="smcodi_handover_outbox",
    )
    op.drop_table("smcodi_handover_outbox")
    op.drop_index(
        "ix_smcodi_handover_target_mapping_target_recipient",
        table_name="smcodi_handover_target_mappings",
    )
    op.drop_index(
        "uq_smcodi_handover_target_mapping_active_resident",
        table_name="smcodi_handover_target_mappings",
    )
    op.drop_table("smcodi_handover_target_mappings")
