"""recurring obligations (series of dated occurrences); FX provider timestamps

A recurring bill or payroll is stored as separate dated obligations sharing a
series_id, generated a bounded distance ahead. Each occurrence is an ordinary
obligation: the safety kernel, forecast and gap calculation treat it as such.

Revision ID: 0004
Revises: 0003
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("obligations", sa.Column("series_id", sa.UUID(), nullable=True))
    op.add_column("obligations", sa.Column("recurrence", sa.String(length=16), nullable=False, server_default="none"))
    op.create_check_constraint("ck_obl_recurrence", "obligations", "recurrence IN ('none','weekly','monthly')")
    op.create_index("ix_obligations_series_id", "obligations", ["series_id"])
    # The provider's own "last updated" and "next update" times (shown with every rate).
    op.add_column("fx_rate_snapshots", sa.Column("provider_as_of", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fx_rate_snapshots", sa.Column("next_update_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("fx_rate_snapshots", "next_update_at")
    op.drop_column("fx_rate_snapshots", "provider_as_of")
    op.drop_index("ix_obligations_series_id", table_name="obligations")
    op.drop_constraint("ck_obl_recurrence", "obligations")
    op.drop_column("obligations", "recurrence")
    op.drop_column("obligations", "series_id")
