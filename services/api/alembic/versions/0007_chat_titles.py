"""several assistant chats per user and workspace: a title per chat

Revision ID: 0007
Revises: 0006
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assistant_conversations", sa.Column("title", sa.String(length=120), nullable=True))
    op.create_index("ix_assistant_conversations_owner", "assistant_conversations", ["user_id", "business_id", "updated_at"])


def downgrade() -> None:
    op.drop_index("ix_assistant_conversations_owner", table_name="assistant_conversations")
    op.drop_column("assistant_conversations", "title")
