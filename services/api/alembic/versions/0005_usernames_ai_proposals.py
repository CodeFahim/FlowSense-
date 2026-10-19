"""usernames; AI action proposals awaiting the user's explicit confirmation

- users.username: optional for existing and seeded accounts, required by sign-up. Stored lower-case,
  unique (case-insensitive by construction).
- assistant_proposals: an action the AI suggested (save a record, request a payment). Nothing happens
  until the signed-in user presses Confirm; the server then runs the same validated path as the forms
  and records the verified outcome. Bound to (user, business) and expiring.

Revision ID: 0005
Revises: 0004
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=30), nullable=True))
    op.create_index("ux_users_username", "users", ["username"], unique=True)
    op.create_table(
        "assistant_proposals",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("summary", sa.String(length=400), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('pending','done','failed','cancelled','expired')", name="ck_proposal_status"),
    )


def downgrade() -> None:
    op.drop_table("assistant_proposals")
    op.drop_index("ux_users_username", table_name="users")
    op.drop_column("users", "username")
