"""add immutable staff application manifest evidence tables

Revision ID: 025_staff_application_manifest
Revises: 024_staff_open_assignment_guards
Create Date: 2026-08-02
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "025_staff_application_manifest"
down_revision: str | None = "024_staff_open_assignment_guards"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "staff_application_runs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_schema_version", sa.Integer(), nullable=False),
        sa.Column("plan_schema_version", sa.Integer(), nullable=False),
        sa.Column("plan_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("change_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("manifest_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("organization_catalog_version", sa.String(length=40), nullable=False),
        sa.Column("organization_catalog_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("leave_access_policy_version", sa.String(length=40), nullable=False),
        sa.Column("leave_access_policy_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("policy_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("plan_summary", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("entry_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_by_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('prepared', 'backup_verified', 'restore_verified', "
            "'evidence_ready', 'stale')",
            name="staff_application_runs_status_check",
        ),
        sa.ForeignKeyConstraint(["batch_id"], ["staff_sync_batches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"]),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("manifest_fingerprint"),
        sa.UniqueConstraint(
            "organization_id",
            "batch_id",
            "plan_fingerprint",
            "change_fingerprint",
            name="uq_staff_application_run_plan",
        ),
    )
    op.create_index(
        "ix_staff_application_runs_batch_id",
        "staff_application_runs",
        ["batch_id"],
    )
    op.create_index(
        "ix_staff_application_runs_created_by_id",
        "staff_application_runs",
        ["created_by_id"],
    )
    op.create_index(
        "ix_staff_application_runs_organization_id",
        "staff_application_runs",
        ["organization_id"],
    )
    op.create_index(
        "ix_staff_application_runs_status",
        "staff_application_runs",
        ["status"],
    )
    op.create_index(
        "ix_staff_application_runs_org_created",
        "staff_application_runs",
        ["organization_id", "created_at"],
    )

    op.create_table(
        "staff_application_entries",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("entry_key", sa.String(length=140), nullable=False),
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("action", sa.String(length=20), nullable=False),
        sa.Column("row_id", sa.Uuid(), nullable=False),
        sa.Column("natural_key", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("before_state", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after_state", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("before_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("after_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("dependency_keys", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "action IN ('create', 'update')",
            name="staff_application_entries_action_check",
        ),
        sa.CheckConstraint(
            "entity_type IN ('staff', 'source_account', 'service_period', "
            "'service_assignment')",
            name="staff_application_entries_entity_type_check",
        ),
        sa.ForeignKeyConstraint(["run_id"], ["staff_application_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "entry_key", name="uq_staff_application_entry_key"),
        sa.UniqueConstraint("run_id", "ordinal", name="uq_staff_application_entry_ordinal"),
    )
    op.create_index(
        "ix_staff_application_entries_entity_type",
        "staff_application_entries",
        ["entity_type"],
    )
    op.create_index(
        "ix_staff_application_entries_row_id",
        "staff_application_entries",
        ["row_id"],
    )
    op.create_index(
        "ix_staff_application_entries_run_id",
        "staff_application_entries",
        ["run_id"],
    )

    op.create_table(
        "staff_application_backup_proofs",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_filename", sa.String(length=220), nullable=False),
        sa.Column("evidence_sha256", sa.String(length=64), nullable=False),
        sa.Column("dump_filename", sa.String(length=220), nullable=False),
        sa.Column("dump_bytes", sa.BigInteger(), nullable=False),
        sa.Column("dump_sha256", sa.String(length=64), nullable=False),
        sa.Column("database_schema", sa.String(length=80), nullable=False),
        sa.Column("alembic_revision", sa.String(length=80), nullable=False),
        sa.Column("protected_state_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("protected_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("source_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verified_by_id", sa.Uuid(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["staff_application_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_staff_application_backup_proof_run"),
    )
    op.create_index(
        "ix_staff_application_backup_proofs_run_id",
        "staff_application_backup_proofs",
        ["run_id"],
    )
    op.create_index(
        "ix_staff_application_backup_proofs_verified_by_id",
        "staff_application_backup_proofs",
        ["verified_by_id"],
    )

    op.create_table(
        "staff_application_restore_receipts",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("receipt_filename", sa.String(length=220), nullable=False),
        sa.Column("receipt_sha256", sa.String(length=64), nullable=False),
        sa.Column("dump_sha256", sa.String(length=64), nullable=False),
        sa.Column("restored_schema", sa.String(length=80), nullable=False),
        sa.Column("restored_alembic_revision", sa.String(length=80), nullable=False),
        sa.Column("restored_state_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("restored_tables", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cleanup_succeeded", sa.Boolean(), nullable=False),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("verified_by_id", sa.Uuid(), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["staff_application_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["verified_by_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", name="uq_staff_application_restore_receipt_run"),
    )
    op.create_index(
        "ix_staff_application_restore_receipts_run_id",
        "staff_application_restore_receipts",
        ["run_id"],
    )
    op.create_index(
        "ix_staff_application_restore_receipts_verified_by_id",
        "staff_application_restore_receipts",
        ["verified_by_id"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    run_count = bind.execute(sa.text("SELECT COUNT(*) FROM staff_application_runs")).scalar_one()
    if run_count:
        raise RuntimeError(
            "staff application manifest records exist; refusing destructive downgrade"
        )

    op.drop_index(
        "ix_staff_application_restore_receipts_verified_by_id",
        table_name="staff_application_restore_receipts",
    )
    op.drop_index(
        "ix_staff_application_restore_receipts_run_id",
        table_name="staff_application_restore_receipts",
    )
    op.drop_table("staff_application_restore_receipts")
    op.drop_index(
        "ix_staff_application_backup_proofs_verified_by_id",
        table_name="staff_application_backup_proofs",
    )
    op.drop_index(
        "ix_staff_application_backup_proofs_run_id",
        table_name="staff_application_backup_proofs",
    )
    op.drop_table("staff_application_backup_proofs")
    op.drop_index(
        "ix_staff_application_entries_run_id",
        table_name="staff_application_entries",
    )
    op.drop_index(
        "ix_staff_application_entries_row_id",
        table_name="staff_application_entries",
    )
    op.drop_index(
        "ix_staff_application_entries_entity_type",
        table_name="staff_application_entries",
    )
    op.drop_table("staff_application_entries")
    op.drop_index(
        "ix_staff_application_runs_org_created",
        table_name="staff_application_runs",
    )
    op.drop_index("ix_staff_application_runs_status", table_name="staff_application_runs")
    op.drop_index(
        "ix_staff_application_runs_organization_id",
        table_name="staff_application_runs",
    )
    op.drop_index(
        "ix_staff_application_runs_created_by_id",
        table_name="staff_application_runs",
    )
    op.drop_index(
        "ix_staff_application_runs_batch_id",
        table_name="staff_application_runs",
    )
    op.drop_table("staff_application_runs")
