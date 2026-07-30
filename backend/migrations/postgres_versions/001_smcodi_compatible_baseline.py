"""SMCODI-compatible PostgreSQL baseline.

Revision ID: 001_smcodi_pg
Revises:
Create Date: 2026-07-26
"""

from pathlib import Path

from alembic import op


revision = "001_smcodi_pg"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    sql_path = Path(__file__).resolve().parents[1] / "sql" / "001_smcodi_baseline.sql"
    for statement in sql_path.read_text(encoding="utf-8").split(";"):
        if statement.strip():
            op.execute(statement)


def downgrade() -> None:
    for table_name in [
        "staff_hub_processing_items",
        "staff_hub_message_read_receipts",
        "staff_hub_message_comments",
        "attachments",
        "staff_hub_room_memberships",
        "staff_hub_messages",
        "user_roles",
        "staff_organization_assignments",
        "staff_job_assignments",
        "staff_hub_rooms",
        "recipients",
        "auth_sessions",
        "audit_events",
        "users",
        "rooms",
        "staff",
        "organization_units",
        "staff_job_codes",
        "roles",
        "organizations",
        "domain_modules",
        "auth_login_attempts",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table_name} CASCADE")
