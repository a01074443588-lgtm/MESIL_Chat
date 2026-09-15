"""add append-only confirmed work records and evidence links

Revision ID: 027_confirmed_work_records
Revises: 026_staff_conversation_controls
Create Date: 2026-08-04
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "027_confirmed_work_records"
down_revision: str | None = "026_staff_conversation_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_hub_confirmed_records",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("work_item_id", sa.Uuid(), nullable=False),
        sa.Column("recipient_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("service_context", sa.String(length=30), nullable=False),
        sa.Column("work_category", sa.String(length=40), nullable=False),
        sa.Column("observation_text", sa.Text(), nullable=False),
        sa.Column(
            "action_text",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "result_text",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "measurement_text",
            sa.Text(),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column(
            "urgency",
            sa.String(length=20),
            server_default="low",
            nullable=False,
        ),
        sa.Column(
            "needs_follow_up",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "lifecycle_status",
            sa.String(length=20),
            server_default="active",
            nullable=False,
        ),
        sa.Column(
            "current_version",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
        sa.Column("current_content_hash", sa.String(length=64), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_test_data",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "service_context IN "
            "('facility_care', 'day_care', 'home_visit_care')",
            name="staff_hub_confirmed_records_service_context_check",
        ),
        sa.CheckConstraint(
            "work_category IN "
            "('daily_care', 'nutrition', 'health', 'safety', "
            "'consultation', 'rehabilitation')",
            name="staff_hub_confirmed_records_work_category_check",
        ),
        sa.CheckConstraint(
            "urgency IN ('low', 'medium', 'high', 'urgent')",
            name="staff_hub_confirmed_records_urgency_check",
        ),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'withdrawn')",
            name="staff_hub_confirmed_records_lifecycle_check",
        ),
        sa.CheckConstraint(
            "length(btrim(observation_text)) > 0",
            name="staff_hub_confirmed_records_observation_check",
        ),
        sa.CheckConstraint(
            "current_version > 0",
            name="staff_hub_confirmed_records_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["work_item_id"],
            ["staff_hub_processing_items.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_id"],
            ["recipients.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "work_item_id",
            "recipient_id",
            name="uq_confirmed_record_work_item_recipient",
        ),
    )
    op.create_index(
        "ix_confirmed_records_org_recipient_occurred",
        "staff_hub_confirmed_records",
        ["organization_id", "recipient_id", "occurred_at"],
    )
    op.create_index(
        "ix_confirmed_records_org_service_occurred",
        "staff_hub_confirmed_records",
        ["organization_id", "service_context", "occurred_at"],
    )
    op.create_index(
        "ix_confirmed_records_org_category_occurred",
        "staff_hub_confirmed_records",
        ["organization_id", "work_category", "occurred_at"],
    )
    op.create_index(
        "ix_confirmed_records_work_item",
        "staff_hub_confirmed_records",
        ["work_item_id"],
    )

    op.create_table(
        "staff_hub_confirmed_record_versions",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("record_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("change_reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "version > 0",
            name="staff_hub_confirmed_record_versions_version_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["staff_hub_confirmed_records.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["users.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "record_id",
            "version",
            name="uq_confirmed_record_version",
        ),
        sa.UniqueConstraint(
            "record_id",
            "content_hash",
            name="uq_confirmed_record_version_hash",
        ),
    )
    op.create_index(
        "ix_confirmed_record_versions_record_created",
        "staff_hub_confirmed_record_versions",
        ["record_id", "created_at"],
    )

    op.create_table(
        "staff_hub_confirmed_record_sources",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("record_version_id", sa.Uuid(), nullable=False),
        sa.Column("source_message_id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=True),
        sa.Column("text_extraction_id", sa.Uuid(), nullable=True),
        sa.Column("evidence_role", sa.String(length=20), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("source_content_hash", sa.String(length=64), nullable=False),
        sa.Column("excerpt", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "evidence_role IN ('primary', 'supporting')",
            name="staff_hub_confirmed_record_sources_role_check",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organizations.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["record_version_id"],
            ["staff_hub_confirmed_record_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_message_id"],
            ["staff_hub_messages.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["attachment_id"],
            ["attachments.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["text_extraction_id"],
            ["attachment_text_extractions.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "record_version_id",
            "source_key",
            name="uq_confirmed_record_source_key",
        ),
    )
    op.create_index(
        "ix_confirmed_record_sources_version",
        "staff_hub_confirmed_record_sources",
        ["record_version_id"],
    )
    op.create_index(
        "ix_confirmed_record_sources_message",
        "staff_hub_confirmed_record_sources",
        ["source_message_id", "record_version_id"],
    )

    # 확정 버전과 그 근거는 덮어쓰거나 지우지 않고 새 버전으로만 추가한다.
    op.execute(
        """
        CREATE FUNCTION prevent_confirmed_record_history_mutation()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'confirmed record history is append-only; create a new version';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_confirmed_record_versions_append_only
        BEFORE UPDATE OR DELETE ON staff_hub_confirmed_record_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_confirmed_record_history_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_confirmed_record_sources_append_only
        BEFORE UPDATE OR DELETE ON staff_hub_confirmed_record_sources
        FOR EACH ROW EXECUTE FUNCTION prevent_confirmed_record_history_mutation()
        """
    )


def downgrade() -> None:
    bind = op.get_bind()
    confirmed_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM staff_hub_confirmed_records")
    ).scalar_one()
    if confirmed_count:
        raise RuntimeError(
            "confirmed work records exist; refusing destructive downgrade"
        )

    op.execute(
        "DROP TRIGGER IF EXISTS trg_confirmed_record_sources_append_only "
        "ON staff_hub_confirmed_record_sources"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_confirmed_record_versions_append_only "
        "ON staff_hub_confirmed_record_versions"
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_confirmed_record_history_mutation()")

    op.drop_index(
        "ix_confirmed_record_sources_message",
        table_name="staff_hub_confirmed_record_sources",
    )
    op.drop_index(
        "ix_confirmed_record_sources_version",
        table_name="staff_hub_confirmed_record_sources",
    )
    op.drop_table("staff_hub_confirmed_record_sources")

    op.drop_index(
        "ix_confirmed_record_versions_record_created",
        table_name="staff_hub_confirmed_record_versions",
    )
    op.drop_table("staff_hub_confirmed_record_versions")

    op.drop_index(
        "ix_confirmed_records_work_item",
        table_name="staff_hub_confirmed_records",
    )
    op.drop_index(
        "ix_confirmed_records_org_category_occurred",
        table_name="staff_hub_confirmed_records",
    )
    op.drop_index(
        "ix_confirmed_records_org_service_occurred",
        table_name="staff_hub_confirmed_records",
    )
    op.drop_index(
        "ix_confirmed_records_org_recipient_occurred",
        table_name="staff_hub_confirmed_records",
    )
    op.drop_table("staff_hub_confirmed_records")
