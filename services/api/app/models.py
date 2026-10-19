"""SQLAlchemy models. All money columns are BIGINT minor units.

Immutability of journal tables and the audit log is enforced by database
triggers created in the initial Alembic migration, not only by this code.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

JSON = JSONB


def _uuid() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def _fk(target: str, nullable: bool = False, index: bool = True) -> Mapped[Any]:
    return mapped_column(UUID(as_uuid=True), ForeignKey(target), nullable=nullable, index=index)


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class Business(Base):
    __tablename__ = "businesses"
    id: Mapped[uuid.UUID] = _uuid()
    name: Mapped[str] = mapped_column(String(200))
    # Null until the owner explicitly confirms them (a fresh workspace has no region presets).
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    timezone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # workspace = the user's own business ("Start fresh"); demo = the shared designated demo;
    # sample = a user's private sample copy ("Try the demo"); isolation = cross-tenant test fixture.
    kind: Mapped[str] = mapped_column(String(16), default="workspace")
    place: Mapped[str | None] = mapped_column(String(200), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    # Optional licence details the owner confirmed (registration number, owner name, issue/expiry dates).
    licence: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    timezone_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Available cash: null = not entered yet (never treated as zero). Set by a signed balance declaration.
    cash_declared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cash_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    cash_account_label: Mapped[str | None] = mapped_column(String(100), nullable=True)
    # The owner explicitly said there is nothing to enter for money in / money out.
    money_in_none_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    money_out_none_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", use_alter=True), nullable=True)

    @property
    def ccy(self) -> str:
        """The confirmed accounting currency. Financial code uses this; a workspace without one cannot hold records."""
        if not self.currency:
            from app.errors import ApiError

            raise ApiError(409, "SETUP_REQUIRED", "Confirm your accounting currency and business timezone first.")
        return self.currency

    @property
    def tz_name(self) -> str:
        """Business timezone for dates and deadlines; UTC only while a fresh workspace has none confirmed (no records exist then)."""
        return self.timezone or "UTC"
    kill_switch: Mapped[bool] = mapped_column(Boolean, default=False)
    fixture_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    scenario: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sandbox_now: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # When an owner confirmed the accounting currency (onboarding). Null = not yet confirmed.
    currency_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class User(Base):
    __tablename__ = "users"
    __table_args__ = (Index("ux_users_username", "username", unique=True),)
    id: Mapped[uuid.UUID] = _uuid()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # Lower-case handle chosen at sign-up (older and seeded accounts may have none).
    username: Mapped[str | None] = mapped_column(String(30), nullable=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str] = mapped_column(String(255))
    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Soft deletion: personal data is removed and the row stays only because append-only records refer to it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # user = signed up; designated = a submission identity; fixture = seeded synthetic/test account.
    account_kind: Mapped[str] = mapped_column(String(16), default="user")
    active_business_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id"), nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


class AccountToken(Base):
    """Single-use email verification / password reset token. Only the SHA-256 hash is stored."""

    __tablename__ = "account_tokens"
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    purpose: Mapped[str] = mapped_column(String(16))  # verify_code|reset_code|change_code (older: verify|reset)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # 6-digit codes: random per-code salt for the keyed hash, and wrong guesses so far.
    salt: Mapped[str | None] = mapped_column(String(32), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EmailDelivery(Base):
    """What was sent, through which provider, and the outcome. Never the token or message body."""

    __tablename__ = "email_deliveries"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    purpose: Mapped[str] = mapped_column(String(32))
    provider: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32))  # sent|failed|not_configured
    provider_message_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = _created()


class RateLimitHit(Base):
    __tablename__ = "rate_limit_hits"
    __table_args__ = (Index("ix_rate_limit_bucket_time", "bucket", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(64))  # hashed key, e.g. sha256("register:ip:...")
    created_at: Mapped[datetime] = _created()


class AssistantConversation(Base):
    __tablename__ = "assistant_conversations"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    title: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class AssistantMessage(Base):
    __tablename__ = "assistant_messages"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    conversation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("assistant_conversations.id", ondelete="CASCADE"), index=True)
    business_id: Mapped[uuid.UUID] = _fk("businesses.id", index=False)
    user_id: Mapped[uuid.UUID] = _fk("users.id", index=False)
    role: Mapped[str] = mapped_column(String(16))  # user|assistant
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


class AssistantProposal(Base):
    """An action the AI suggested. It runs only when the signed-in user confirms it (see agents.proposals)."""

    __tablename__ = "assistant_proposals"
    __table_args__ = (CheckConstraint("status IN ('pending','done','failed','cancelled','expired')", name="ck_proposal_status"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    kind: Mapped[str] = mapped_column(String(32))
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    summary: Mapped[str] = mapped_column(String(400))
    status: Mapped[str] = mapped_column(String(16), default="pending")
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Document(Base):
    """An uploaded business document, kept privately for its workspace (the original bytes are the source
    reference for records created from it). Suggestions read from it are stored; nothing is saved to the
    books until the user reviews and confirms."""

    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("kind IN ('pdf','png','jpeg','docx','xlsx')", name="ck_document_kind"),
        CheckConstraint("purpose IN ('business','customer')", name="ck_document_purpose"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    uploaded_by: Mapped[uuid.UUID] = _fk("users.id", index=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    purpose: Mapped[str] = mapped_column(String(16))
    kind: Mapped[str] = mapped_column(String(8))
    original_name: Mapped[str] = mapped_column(String(120))  # display only; never used as a path
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    text: Mapped[str] = mapped_column(Text, default="")
    method: Mapped[str] = mapped_column(String(16))
    pages: Mapped[int | None] = mapped_column(Integer, nullable=True)
    instruction_like: Mapped[bool] = mapped_column(Boolean, default=False)
    suggestion: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created()


class DocumentLink(Base):
    """Which saved record a document was the source for."""

    __tablename__ = "document_links"
    __table_args__ = (
        UniqueConstraint("document_id", "record_type", "record_id"),
        CheckConstraint("record_type IN ('invoice','counterparty','business')", name="ck_document_link_type"),
    )
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    document_id: Mapped[uuid.UUID] = _fk("documents.id")
    business_id: Mapped[uuid.UUID] = _fk("businesses.id", index=False)
    record_type: Mapped[str] = mapped_column(String(16))
    record_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    created_at: Mapped[datetime] = _created()


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (
        UniqueConstraint("user_id", "business_id"),
        CheckConstraint("role IN ('owner','staff')", name="ck_membership_role"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    role: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[uuid.UUID] = _uuid()
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[uuid.UUID] = _fk("users.id")
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    csrf_token: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class UserPreference(Base):
    """Per-user display preferences. They only change how values are *shown*;
    they never touch stored amounts, approvals, reserves or the ledger."""

    __tablename__ = "user_preferences"
    __table_args__ = (
        CheckConstraint("theme IN ('system','light','dark')", name="ck_pref_theme"),
        CheckConstraint("accent IN ('teal','blue','violet','emerald','amber')", name="ck_pref_accent"),
        CheckConstraint("time_display IN ('business','device')", name="ck_pref_time_display"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), primary_key=True)
    country: Mapped[str | None] = mapped_column(String(2), nullable=True)
    display_currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    convert_from: Mapped[str | None] = mapped_column(String(3), nullable=True)
    convert_to: Mapped[str | None] = mapped_column(String(3), nullable=True)
    number_locale: Mapped[str | None] = mapped_column(String(16), nullable=True)
    time_display: Mapped[str] = mapped_column(String(16), default="business")
    theme: Mapped[str] = mapped_column(String(16), default="system")
    accent: Mapped[str] = mapped_column(String(16), default="teal")
    onboarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FxRateSnapshot(Base):
    """Exchange-rate snapshots fetched from a provider (append-only cache).
    Rates are decimal strings: units of each currency per 1 unit of `base`."""

    __tablename__ = "fx_rate_snapshots"
    __table_args__ = (Index("ix_fx_provider_fetched", "provider", "fetched_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(32))
    base: Mapped[str] = mapped_column(String(3))
    rate_date: Mapped[date] = mapped_column(Date)
    rates: Mapped[dict[str, Any]] = mapped_column(JSON)
    fetched_at: Mapped[datetime] = _created()
    provider_as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_update_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginAttempt(Base):
    __tablename__ = "login_attempts"
    __table_args__ = (Index("ix_login_attempts_email_time", "email", "created_at"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(320))
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = _created()


# --------------------------------------------------------------------------- ledger


class LedgerAccount(Base):
    __tablename__ = "ledger_accounts"
    __table_args__ = (UniqueConstraint("business_id", "code"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # asset|liability|equity|income|expense


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    memo: Mapped[str] = mapped_column(String(500))
    source_type: Mapped[str] = mapped_column(String(64))
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reverses_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("journal_entries.id"), nullable=True, unique=True
    )
    created_at: Mapped[datetime] = _created()


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("debit_minor >= 0 AND credit_minor >= 0", name="ck_line_nonneg"),
        CheckConstraint("(debit_minor = 0) <> (credit_minor = 0)", name="ck_line_one_side"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    entry_id: Mapped[uuid.UUID] = _fk("journal_entries.id")
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    account_id: Mapped[uuid.UUID] = _fk("ledger_accounts.id")
    debit_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    credit_minor: Mapped[int] = mapped_column(BigInteger, default=0)


# --------------------------------------------------------------- counterparties


class Counterparty(Base):
    __tablename__ = "counterparties"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    kind: Mapped[str] = mapped_column(String(16))  # payroll|supplier|customer|lender|other
    name: Mapped[str] = mapped_column(String(200))
    account_ref: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepts_partial: Mapped[bool] = mapped_column(Boolean, default=False)
    typical_days_to_pay: Mapped[int] = mapped_column(Integer, default=1)
    # Optional contact details (email, phone). Never used to send anything automatically.
    contact: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _created()


class CounterpartyChange(Base):
    __tablename__ = "counterparty_changes"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    counterparty_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    old_account_ref: Mapped[str] = mapped_column(String(64))
    new_account_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|applied|rejected
    requested_by: Mapped[uuid.UUID] = _fk("users.id", index=False)
    reviewed_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, index=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------- receivables


class Invoice(Base):
    __tablename__ = "invoices"
    __table_args__ = (
        UniqueConstraint("business_id", "number"),
        CheckConstraint("amount_minor > 0", name="ck_invoice_amount_pos"),
        CheckConstraint("collected_minor >= 0 AND collected_minor <= amount_minor", name="ck_invoice_collected"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    customer_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    number: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(500))
    issued_on: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    collected_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    disputed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _created()

    @property
    def outstanding_minor(self) -> int:
        return self.amount_minor - self.collected_minor


class InvoiceAllocation(Base):
    __tablename__ = "invoice_allocations"
    __table_args__ = (
        UniqueConstraint("transfer_id", "invoice_id"),
        CheckConstraint("amount_minor > 0", name="ck_alloc_pos"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    invoice_id: Mapped[uuid.UUID] = _fk("invoices.id")
    transfer_id: Mapped[uuid.UUID] = _fk("transfers.id")
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = _created()


class Evidence(Base):
    __tablename__ = "evidence"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    invoice_id: Mapped[uuid.UUID | None] = _fk("invoices.id", nullable=True)
    label: Mapped[str] = mapped_column(String(64))
    title: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(16))  # text|pdf
    filename: Mapped[str | None] = mapped_column(String(200), nullable=True)
    content_text: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    provenance: Mapped[str] = mapped_column(String(32))  # user_supplied|sandbox_verified|ai_assessment
    instruction_like: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, index=False)
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------- obligations


class Obligation(Base):
    __tablename__ = "obligations"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_obl_amount_pos"),
        CheckConstraint("paid_minor >= 0 AND paid_minor <= amount_minor", name="ck_obl_paid"),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    kind: Mapped[str] = mapped_column(String(32))  # payroll|supplier|loan_repayment|other
    counterparty_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    description: Mapped[str] = mapped_column(String(500))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    paid_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    essential: Mapped[bool] = mapped_column(Boolean, default=False)
    payable_account_code: Mapped[str] = mapped_column(String(32))
    # Recurring bills are separate dated occurrences sharing a series_id.
    series_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), nullable=True, index=True)
    recurrence: Mapped[str] = mapped_column(String(16), default="none")  # none|weekly|monthly
    created_at: Mapped[datetime] = _created()

    @property
    def unpaid_minor(self) -> int:
        return self.amount_minor - self.paid_minor


class FinancingQuote(Base):
    __tablename__ = "financing_quotes"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    lender_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    principal_minor: Mapped[int] = mapped_column(BigInteger)
    fee_minor: Mapped[int] = mapped_column(BigInteger)
    interest_minor: Mapped[int] = mapped_column(BigInteger)
    term_days: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), default="available")
    created_at: Mapped[datetime] = _created()

    @property
    def net_proceeds_minor(self) -> int:
        return self.principal_minor - self.fee_minor


# ---------------------------------------------------------------- policies


class Policy(Base):
    __tablename__ = "policies"
    __table_args__ = (
        UniqueConstraint("business_id", "version"),
        Index(
            "uq_policy_one_active",
            "business_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16))  # proposed|active|superseded|discarded
    source_text: Mapped[str] = mapped_column(Text)
    spec: Mapped[dict[str, Any]] = mapped_column(JSON)
    interpretation: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_by: Mapped[uuid.UUID] = _fk("users.id", index=False)
    activated_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, index=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------------------ rescue cases


class RescueCase(Base):
    __tablename__ = "rescue_cases"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    target_obligation_id: Mapped[uuid.UUID] = _fk("obligations.id")
    status: Mapped[str] = mapped_column(String(24))
    opened_at: Mapped[datetime] = _created()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CaseTransition(Base):
    __tablename__ = "case_transitions"
    id: Mapped[uuid.UUID] = _uuid()
    case_id: Mapped[uuid.UUID] = _fk("rescue_cases.id")
    from_status: Mapped[str | None] = mapped_column(String(24), nullable=True)
    to_status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(String(500))
    created_at: Mapped[datetime] = _created()


class Plan(Base):
    __tablename__ = "plans"
    __table_args__ = (UniqueConstraint("case_id", "version"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    case_id: Mapped[uuid.UUID] = _fk("rescue_cases.id")
    version: Mapped[int] = mapped_column(Integer)
    trigger: Mapped[str] = mapped_column(String(200))
    gap_minor: Mapped[int] = mapped_column(BigInteger)
    feasible: Mapped[bool] = mapped_column(Boolean)
    body: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


# ----------------------------------------------------- actions and approvals


class Action(Base):
    __tablename__ = "actions"
    __table_args__ = (CheckConstraint("amount_minor > 0", name="ck_action_amount_pos"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    case_id: Mapped[uuid.UUID | None] = _fk("rescue_cases.id", nullable=True)
    plan_id: Mapped[uuid.UUID | None] = _fk("plans.id", nullable=True)
    type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    settled_minor: Mapped[int] = mapped_column(BigInteger, default=0)
    currency: Mapped[str] = mapped_column(String(3))
    counterparty_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    counterparty_version: Mapped[int] = mapped_column(Integer)
    invoice_id: Mapped[uuid.UUID | None] = _fk("invoices.id", nullable=True)
    obligation_id: Mapped[uuid.UUID | None] = _fk("obligations.id", nullable=True)
    quote_id: Mapped[uuid.UUID | None] = _fk("financing_quotes.id", nullable=True)
    purpose: Mapped[str] = mapped_column(String(500))
    source: Mapped[str] = mapped_column(String(32))  # planner|user|assistant
    last_decision: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_by: Mapped[uuid.UUID | None] = _fk("users.id", nullable=True, index=False)
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    owner_id: Mapped[uuid.UUID] = _fk("users.id", index=False)
    action_id: Mapped[uuid.UUID] = _fk("actions.id")
    action_revision: Mapped[int] = mapped_column(Integer)
    action_type: Mapped[str] = mapped_column(String(32))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    counterparty_id: Mapped[uuid.UUID] = _fk("counterparties.id", index=False)
    counterparty_version: Mapped[int] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer)
    financing_terms: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    payload_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = _created()


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (UniqueConstraint("business_id", "operation", "key"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    operation: Mapped[str] = mapped_column(String(64))
    key: Mapped[str] = mapped_column(String(128))
    payload_digest: Mapped[str] = mapped_column(String(64))
    status_code: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = _created()


# ------------------------------------------------ transfers and settlement


class Transfer(Base):
    __tablename__ = "transfers"
    __table_args__ = (
        CheckConstraint("amount_minor > 0", name="ck_transfer_amount_pos"),
        Index(
            "uq_transfer_live_per_action",
            "action_id",
            unique=True,
            postgresql_where=text("direction = 'out' AND status IN ('pending','settled')"),
        ),
    )
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    direction: Mapped[str] = mapped_column(String(8))  # out|in
    status: Mapped[str] = mapped_column(String(16))  # pending|settled|failed
    action_id: Mapped[uuid.UUID | None] = _fk("actions.id", nullable=True)
    obligation_id: Mapped[uuid.UUID | None] = _fk("obligations.id", nullable=True)
    invoice_id: Mapped[uuid.UUID | None] = _fk("invoices.id", nullable=True)
    counterparty_id: Mapped[uuid.UUID] = _fk("counterparties.id")
    counterparty_version: Mapped[int] = mapped_column(Integer)
    account_ref: Mapped[str] = mapped_column(String(64))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    currency: Mapped[str] = mapped_column(String(3))
    provider_ref: Mapped[str] = mapped_column(String(64), unique=True)
    submitted_at: Mapped[datetime] = _created()
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SandboxEvent(Base):
    __tablename__ = "sandbox_events"
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    provider_event_id: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    outcome: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str] = mapped_column(String(500), default="")
    received_at: Mapped[datetime] = _created()


class SimBankOp(Base):
    """State held by the *simulated external bank*, separate from FlowSense's books."""

    __tablename__ = "sim_bank_ops"
    provider_ref: Mapped[str] = mapped_column(String(64), primary_key=True)
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    direction: Mapped[str] = mapped_column(String(8))
    amount_minor: Mapped[int] = mapped_column(BigInteger)
    account_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))  # accepted|settled|failed
    created_at: Mapped[datetime] = _created()


class Receipt(Base):
    __tablename__ = "receipts"
    __table_args__ = (UniqueConstraint("business_id", "number"),)
    id: Mapped[uuid.UUID] = _uuid()
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    number: Mapped[str] = mapped_column(String(32))
    transfer_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("transfers.id"), unique=True)
    content: Mapped[dict[str, Any]] = mapped_column(JSON)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()


# -------------------------------------------------------------------- audit


class AuditHead(Base):
    __tablename__ = "audit_heads"
    business_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("businesses.id"), primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, default=0)
    last_hash: Mapped[str] = mapped_column(String(64))


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (UniqueConstraint("business_id", "seq"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    business_id: Mapped[uuid.UUID] = _fk("businesses.id")
    seq: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor_type: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    case_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    action_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    approval_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    receipt_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    outcome: Mapped[str] = mapped_column(String(32))
    reasons: Mapped[list[Any]] = mapped_column(JSON)
    correlation_id: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
