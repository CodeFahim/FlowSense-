"""user preferences, accounting-currency confirmation, FX rate cache

Revision ID: 0002
Revises: 0001
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("currency_confirmed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.UUID(), sa.ForeignKey("users.id"), primary_key=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("display_currency", sa.String(length=3), nullable=True),
        sa.Column("convert_from", sa.String(length=3), nullable=True),
        sa.Column("convert_to", sa.String(length=3), nullable=True),
        sa.Column("number_locale", sa.String(length=16), nullable=True),
        sa.Column("time_display", sa.String(length=16), nullable=False, server_default="business"),
        sa.Column("theme", sa.String(length=16), nullable=False, server_default="system"),
        sa.Column("accent", sa.String(length=16), nullable=False, server_default="teal"),
        sa.Column("onboarded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("theme IN ('system','light','dark')", name="ck_pref_theme"),
        sa.CheckConstraint("accent IN ('teal','blue','violet','emerald','amber')", name="ck_pref_accent"),
        sa.CheckConstraint("time_display IN ('business','device')", name="ck_pref_time_display"),
    )
    op.create_table(
        "fx_rate_snapshots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("base", sa.String(length=3), nullable=False),
        sa.Column("rate_date", sa.Date(), nullable=False),
        sa.Column("rates", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_fx_provider_fetched", "fx_rate_snapshots", ["provider", "fetched_at"])
    # Rate provenance is append-only, like the other evidence tables.
    op.execute("""
    CREATE TRIGGER fx_rate_snapshots_append_only BEFORE UPDATE OR DELETE ON fx_rate_snapshots
    FOR EACH ROW EXECUTE FUNCTION lifeline_reject_mutation();
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS fx_rate_snapshots_append_only ON fx_rate_snapshots")
    op.drop_index("ix_fx_provider_fetched", table_name="fx_rate_snapshots")
    op.drop_table("fx_rate_snapshots")
    op.drop_table("user_preferences")
    op.drop_column("businesses", "currency_confirmed_at")
