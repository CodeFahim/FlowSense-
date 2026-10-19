"""uploaded business documents (private per workspace), links to the records made from them, licence details

Revision ID: 0008
Revises: 0007
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("businesses", sa.Column("licence", postgresql.JSONB(), nullable=True))
    op.add_column("counterparties", sa.Column("contact", postgresql.JSONB(), nullable=True))
    op.create_table(
        "documents",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column("business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=False, index=True),
        sa.Column("uploaded_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("batch_id", sa.UUID(), nullable=False, index=True),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("original_name", sa.String(120), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False, index=True),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False, server_default=""),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=True),
        sa.Column("instruction_like", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("suggestion", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("kind IN ('pdf','png','jpeg','docx','xlsx')", name="ck_document_kind"),
        sa.CheckConstraint("purpose IN ('business','customer')", name="ck_document_purpose"),
    )
    op.create_table(
        "document_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("documents.id"), nullable=False, index=True),
        sa.Column("business_id", sa.UUID(), sa.ForeignKey("businesses.id"), nullable=False),
        sa.Column("record_type", sa.String(16), nullable=False),
        sa.Column("record_id", sa.UUID(), nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "record_type", "record_id"),
        sa.CheckConstraint("record_type IN ('invoice','counterparty','business')", name="ck_document_link_type"),
    )


def downgrade() -> None:
    op.drop_table("document_links")
    op.drop_table("documents")
    op.drop_column("counterparties", "contact")
    op.drop_column("businesses", "licence")
