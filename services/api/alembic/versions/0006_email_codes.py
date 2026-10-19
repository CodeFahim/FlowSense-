"""6-digit email codes: per-code salt and attempt counter on account_tokens

Revision ID: 0006
Revises: 0005
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("account_tokens", sa.Column("salt", sa.String(length=32), nullable=True))
    op.add_column("account_tokens", sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"))
    op.create_index("ix_account_tokens_user_purpose", "account_tokens", ["user_id", "purpose"])
    op.drop_constraint("ck_token_purpose", "account_tokens", type_="check")
    op.create_check_constraint("ck_token_purpose", "account_tokens", "purpose IN ('verify','reset','verify_code','reset_code','change_code')")


def downgrade() -> None:
    op.drop_constraint("ck_token_purpose", "account_tokens", type_="check")
    op.create_check_constraint("ck_token_purpose", "account_tokens", "purpose IN ('verify','reset')")
    op.drop_index("ix_account_tokens_user_purpose", table_name="account_tokens")
    op.drop_column("account_tokens", "attempts")
    op.drop_column("account_tokens", "salt")
