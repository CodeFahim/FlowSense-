"""account lifecycle, workspace kinds and setup state, assistant chat, rate limits

- users: email verification, soft deletion (personal data removed), account kind
  (designated submission identities and fixture accounts cannot be deleted).
- businesses: workspace kind, optional place/type/country, nullable currency and
  timezone until the owner confirms them, cash declaration and "no entries"
  confirmations for the setup checklist.
- account_tokens: single-use, short-lived email verification and password reset
  tokens (only SHA-256 hashes are stored).
- email_deliveries: delivery log without tokens or message bodies.
- rate_limit_hits: hashed buckets for abuse limits (registration, reset, chat, ...).
- assistant_conversations / assistant_messages: tenant-scoped chat history
  (deletable by the user; not part of the append-only financial record).

Revision ID: 0003
Revises: 0002
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("account_kind", sa.String(length=16), nullable=False, server_default="user"))
    op.add_column("users", sa.Column("active_business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_user_account_kind", "users", "account_kind IN ('user','designated','fixture')")
    # Seeded synthetic accounts existed before signup: mark them as fixture accounts (not deletable) and verified.
    op.execute("UPDATE users SET account_kind = 'fixture', email_verified_at = created_at")

    op.add_column("businesses", sa.Column("kind", sa.String(length=16), nullable=False, server_default="workspace"))
    op.create_check_constraint("ck_business_kind", "businesses", "kind IN ('workspace','demo','sample','isolation')")
    op.add_column("businesses", sa.Column("place", sa.String(length=200), nullable=True))
    op.add_column("businesses", sa.Column("business_type", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("country", sa.String(length=2), nullable=True))
    op.add_column("businesses", sa.Column("timezone_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("cash_declared_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("cash_as_of", sa.Date(), nullable=True))
    op.add_column("businesses", sa.Column("cash_account_label", sa.String(length=100), nullable=True))
    op.add_column("businesses", sa.Column("money_in_none_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("money_out_none_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("businesses", sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    # A fresh workspace has no currency or timezone until the owner confirms them.
    op.alter_column("businesses", "currency", existing_type=sa.String(length=3), nullable=True, server_default=None)
    op.alter_column("businesses", "timezone", existing_type=sa.String(length=64), nullable=True)
    op.execute("UPDATE businesses SET kind = 'demo' WHERE fixture_key = 'pearl-river-demo'")
    op.execute("UPDATE businesses SET kind = 'isolation' WHERE fixture_key = 'harbor-tea-isolation'")
    # Seeded fixtures were built with an explicit (stated) timezone and opening cash.
    op.execute(
        "UPDATE businesses SET timezone_confirmed_at = created_at, cash_declared_at = created_at, "
        "cash_as_of = (sandbox_now AT TIME ZONE 'UTC')::date WHERE fixture_key IS NOT NULL"
    )

    op.create_table(
        "account_tokens",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("purpose", sa.String(length=16), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("purpose IN ('verify','reset')", name="ck_token_purpose"),
    )
    op.create_table(
        "email_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("purpose", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("detail", sa.String(length=200), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "rate_limit_hits",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("bucket", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_rate_limit_bucket_time", "rate_limit_hits", ["bucket", "created_at"])
    op.create_table(
        "assistant_conversations",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_table(
        "assistant_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("conversation_id", sa.UUID(), sa.ForeignKey("assistant_conversations.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('user','assistant')", name="ck_msg_role"),
    )


def downgrade() -> None:
    op.drop_table("assistant_messages")
    op.drop_table("assistant_conversations")
    op.drop_index("ix_rate_limit_bucket_time", table_name="rate_limit_hits")
    op.drop_table("rate_limit_hits")
    op.drop_table("email_deliveries")
    op.drop_table("account_tokens")
    op.execute("UPDATE businesses SET currency = 'USD' WHERE currency IS NULL")
    op.execute("UPDATE businesses SET timezone = 'UTC' WHERE timezone IS NULL")
    op.alter_column("businesses", "timezone", existing_type=sa.String(length=64), nullable=False)
    op.alter_column("businesses", "currency", existing_type=sa.String(length=3), nullable=False)
    for col in (
        "created_by", "money_out_none_at", "money_in_none_at", "cash_account_label", "cash_as_of", "cash_declared_at",
        "timezone_confirmed_at", "country", "business_type", "place",
    ):
        op.drop_column("businesses", col)
    op.drop_constraint("ck_business_kind", "businesses")
    op.drop_column("businesses", "kind")
    op.drop_constraint("ck_user_account_kind", "users")
    for col in ("password_changed_at", "active_business_id", "account_kind", "deleted_at", "email_verified_at"):
        op.drop_column("users", col)
