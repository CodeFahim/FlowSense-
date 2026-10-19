"""Double-entry sandbox ledger.

Entries are immutable (database trigger) and must balance (deferred
constraint trigger checked at commit). Corrections use linked reversals.
There is deliberately no HTTP endpoint that writes arbitrary journal lines.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JournalEntry, JournalLine, LedgerAccount
from app.money import check_amount

CASH = "1000"
AR = "1100"
PAYROLL_PAYABLE = "2000"
SUPPLIER_PAYABLE = "2100"
LOANS_PAYABLE = "2200"
EQUITY = "3000"
PAYROLL_EXPENSE = "5000"
SUPPLIER_EXPENSE = "5100"
FINANCING_COST = "5200"
REVENUE = "4000"

CHART: list[tuple[str, str, str]] = [
    (CASH, "Sandbox bank account (settled cash)", "asset"),
    (AR, "Accounts receivable", "asset"),
    (PAYROLL_PAYABLE, "Payroll payable", "liability"),
    (SUPPLIER_PAYABLE, "Supplier payables", "liability"),
    (LOANS_PAYABLE, "Loans payable", "liability"),
    (EQUITY, "Opening equity", "equity"),
    (REVENUE, "Sales revenue", "income"),
    (PAYROLL_EXPENSE, "Payroll expense", "expense"),
    (SUPPLIER_EXPENSE, "Supplier expense", "expense"),
    (FINANCING_COST, "Financing costs", "expense"),
]
_DEBIT_NORMAL = {"asset", "expense"}


class LedgerError(ValueError):
    pass


def create_chart(db: Session, business_id: uuid.UUID) -> None:
    for code, name, kind in CHART:
        db.add(LedgerAccount(business_id=business_id, code=code, name=name, kind=kind))
    db.flush()


def _account_ids(db: Session, business_id: uuid.UUID) -> dict[str, uuid.UUID]:
    rows = db.execute(select(LedgerAccount.code, LedgerAccount.id).where(LedgerAccount.business_id == business_id))
    return {code: aid for code, aid in rows}


def post_entry(
    db: Session,
    business_id: uuid.UUID,
    *,
    effective_at: datetime,
    memo: str,
    source_type: str,
    source_id: Any,
    lines: list[tuple[str, int, int]],
    reverses_entry_id: uuid.UUID | None = None,
) -> JournalEntry:
    """lines: (account_code, debit_minor, credit_minor)."""
    if len(lines) < 2:
        raise LedgerError("An entry needs at least two lines")
    total_dr = total_cr = 0
    for _code, dr, cr in lines:
        if (dr == 0) == (cr == 0):
            raise LedgerError("Each line must have exactly one positive side")
        if dr:
            total_dr += check_amount(dr)
        else:
            total_cr += check_amount(cr)
    if total_dr != total_cr:
        raise LedgerError(f"Unbalanced entry: debits {total_dr} != credits {total_cr}")
    ids = _account_ids(db, business_id)
    entry = JournalEntry(
        business_id=business_id,
        effective_at=effective_at,
        memo=memo,
        source_type=source_type,
        source_id=None if source_id is None else str(source_id),
        reverses_entry_id=reverses_entry_id,
    )
    db.add(entry)
    db.flush()
    for code, dr, cr in lines:
        if code not in ids:
            raise LedgerError(f"Unknown account {code}")
        db.add(JournalLine(entry_id=entry.id, business_id=business_id, account_id=ids[code], debit_minor=dr, credit_minor=cr))
    db.flush()
    return entry


def reverse_entry(db: Session, entry_id: uuid.UUID, *, effective_at: datetime, memo: str) -> JournalEntry:
    original = db.get(JournalEntry, entry_id)
    if original is None:
        raise LedgerError("Entry not found")
    lines = db.execute(
        select(LedgerAccount.code, JournalLine.debit_minor, JournalLine.credit_minor)
        .join(LedgerAccount, LedgerAccount.id == JournalLine.account_id)
        .where(JournalLine.entry_id == entry_id)
    ).all()
    swapped = [(code, cr, dr) for code, dr, cr in lines]
    return post_entry(
        db,
        original.business_id,
        effective_at=effective_at,
        memo=memo,
        source_type="reversal",
        source_id=original.id,
        lines=swapped,
        reverses_entry_id=original.id,
    )


def balance(db: Session, business_id: uuid.UUID, code: str) -> int:
    row = db.execute(
        select(
            LedgerAccount.kind,
            func.coalesce(func.sum(JournalLine.debit_minor), 0),
            func.coalesce(func.sum(JournalLine.credit_minor), 0),
        )
        .join(JournalLine, JournalLine.account_id == LedgerAccount.id, isouter=True)
        .where(LedgerAccount.business_id == business_id, LedgerAccount.code == code)
        .group_by(LedgerAccount.kind)
    ).one()
    kind, dr, cr = row
    return int(dr - cr) if kind in _DEBIT_NORMAL else int(cr - dr)


def settled_cash(db: Session, business_id: uuid.UUID) -> int:
    return balance(db, business_id, CASH)


def trial_balance(db: Session, business_id: uuid.UUID) -> dict[str, Any]:
    rows = db.execute(
        select(
            LedgerAccount.code,
            LedgerAccount.name,
            LedgerAccount.kind,
            func.coalesce(func.sum(JournalLine.debit_minor), 0),
            func.coalesce(func.sum(JournalLine.credit_minor), 0),
        )
        .join(JournalLine, JournalLine.account_id == LedgerAccount.id, isouter=True)
        .where(LedgerAccount.business_id == business_id)
        .group_by(LedgerAccount.code, LedgerAccount.name, LedgerAccount.kind)
        .order_by(LedgerAccount.code)
    ).all()
    accounts = []
    total_dr = total_cr = 0
    for code, name, kind, dr, cr in rows:
        total_dr += int(dr)
        total_cr += int(cr)
        bal = int(dr - cr) if kind in _DEBIT_NORMAL else int(cr - dr)
        accounts.append({"code": code, "name": name, "kind": kind, "balance_minor": bal})
    return {"accounts": accounts, "total_debits_minor": total_dr, "total_credits_minor": total_cr, "balanced": total_dr == total_cr}
