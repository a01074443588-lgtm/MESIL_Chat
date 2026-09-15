"""Persist short-lived voice call invitations and participant state.

Revision ID: 046_voice_call_invitations
Revises: 045_evidence_ledger
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "046_voice_call_invitations"
down_revision: str | None = "045_evidence_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "voice_call_invitations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=False),
        sa.Column("caller_user_id", sa.Uuid(), nullable=False),
        sa.Column("call_mode", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("member_count", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["room_id"], ["staff_hub_rooms.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["caller_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_voice_call_invitations_organization_id", "voice_call_invitations", ["organization_id"])
    op.create_index("ix_voice_call_invitations_room_id", "voice_call_invitations", ["room_id"])
    op.create_index("ix_voice_call_invitations_caller_user_id", "voice_call_invitations", ["caller_user_id"])
    op.create_index("ix_voice_call_invitations_state", "voice_call_invitations", ["state"])
    op.create_index("ix_voice_call_invitations_expires_at", "voice_call_invitations", ["expires_at"])
    op.create_index(
        "ix_voice_call_invitations_org_state_expires",
        "voice_call_invitations",
        ["organization_id", "state", "expires_at"],
    )

    op.create_table(
        "voice_call_participants",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("organization_id", sa.Uuid(), nullable=False),
        sa.Column("call_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("participant_role", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("action_token_hash", sa.String(length=64), nullable=True),
        sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["call_id"], ["voice_call_invitations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("call_id", "user_id", name="uq_voice_call_participants_call_user"),
    )
    op.create_index("ix_voice_call_participants_organization_id", "voice_call_participants", ["organization_id"])
    op.create_index("ix_voice_call_participants_call_id", "voice_call_participants", ["call_id"])
    op.create_index("ix_voice_call_participants_user_id", "voice_call_participants", ["user_id"])
    op.create_index("ix_voice_call_participants_state", "voice_call_participants", ["state"])
    op.create_index("ix_voice_call_participants_action_token_hash", "voice_call_participants", ["action_token_hash"])
    op.create_index(
        "ix_voice_call_participants_user_state",
        "voice_call_participants",
        ["user_id", "state"],
    )


def downgrade() -> None:
    op.drop_table("voice_call_participants")
    op.drop_table("voice_call_invitations")
