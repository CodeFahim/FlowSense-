"""Cash flow history, generated from the ledger (never typed in separately).

Settled rows are the journal lines on the settled-cash account, in time order, with a running balance:
only money that actually arrived or left changes it. Money in progress (submitted payments not yet
confirmed by the bank) and expected money (unpaid invoices, upcoming bills) are listed separately and
never enter the running balance. Approving something changes nothing here; a settlement does.

Each settled row also has a ``kind``: ``receipt`` (money received, e.g. a customer payment), ``payment``
(money paid out) or ``adjustment`` (an opening balance or a correction the owner declared, a sample opening
balance, a reversal). Adjustments change the balance but are not income or spending, so monthly "money in"
and "money out" count receipts and payments only; adjustments are reported separately and every month
reconciles: opening + money in - money out + adjustments = closing.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ledger import service as ledger
from app.models import Business, Counterparty, Invoice, JournalEntry, JournalLine, LedgerAccount, Obligation, Transfer
from app.money import fmt

CATEGORY = {
    ledger.AR: "Customer payment",
    ledger.PAYROLL_PAYABLE: "Payroll",
    ledger.SUPPLIER_PAYABLE: "Bills and suppliers",
    ledger.LOANS_PAYABLE: "Loan",
    ledger.FINANCING_COST: "Financing cost",
    ledger.EQUITY: "Balance you declared",
}
CATEGORIES = [*dict.fromkeys(CATEGORY.values()), "Opening balance", "Correction", "Other"]
ADJUSTMENT_SOURCES = {"balance_declaration", "opening", "reversal"}


def _source(e: JournalEntry) -> str:
    if e.source_type == "balance_declaration":
        return "Declared by you (not bank-verified)"
    if e.source_type == "opening":
        return "Sample opening balance (synthetic)"
    if e.source_type == "reversal":
        return "Correction"
    # Older ledger lines (append-only) still carry the previous product name.
    if "outside FlowSense" in e.memo or "outside Lifeline" in e.memo:
        return "Recorded by you (paid outside FlowSense, not bank-verified)"
    if e.source_type == "transfer":
        return "Sandbox bank (signed settlement)"
    return e.source_type.replace("_", " ").capitalize()


def _local(biz: Business, dt: datetime) -> datetime:
    return dt.astimezone(ZoneInfo(biz.tz_name))


def settled_rows(db: Session, biz: Business) -> list[dict[str, Any]]:
    """Every settled cash movement, oldest first, with the running balance after it."""
    accounts = {aid: code for aid, code in db.execute(select(LedgerAccount.id, LedgerAccount.code).where(LedgerAccount.business_id == biz.id))}
    cash_id = next((aid for aid, code in accounts.items() if code == ledger.CASH), None)
    if cash_id is None:
        return []
    entries = db.execute(
        select(JournalEntry)
        .join(JournalLine, JournalLine.entry_id == JournalEntry.id)
        .where(JournalEntry.business_id == biz.id, JournalLine.account_id == cash_id)
        .order_by(JournalEntry.effective_at, JournalEntry.created_at, JournalEntry.id)
        .distinct()
    ).scalars().all()
    if not entries:
        return []
    lines = db.execute(select(JournalLine).where(JournalLine.entry_id.in_([e.id for e in entries]))).scalars().all()
    by_entry: dict[uuid.UUID, list[JournalLine]] = {}
    for ln in lines:
        by_entry.setdefault(ln.entry_id, []).append(ln)
    out: list[dict[str, Any]] = []
    balance = 0
    for e in entries:
        ls = by_entry.get(e.id, [])
        delta = sum(ln.debit_minor - ln.credit_minor for ln in ls if ln.account_id == cash_id)
        if delta == 0:
            continue
        others = [accounts.get(ln.account_id, "") for ln in ls if ln.account_id != cash_id]
        if e.source_type == "reversal":
            cat = "Correction"
        elif e.source_type == "opening":
            cat = "Opening balance"  # the sample's opening entry also touches receivables: it is still not a customer payment
        else:
            cat = next((CATEGORY[c] for c in others if c in CATEGORY), "Other")
        is_adjustment = e.source_type in ADJUSTMENT_SOURCES or ledger.EQUITY in others
        kind = "adjustment" if is_adjustment else ("receipt" if delta > 0 else "payment")
        balance += delta
        when = _local(biz, e.effective_at)
        out.append(
            {
                "id": str(e.id),
                "date": when.date().isoformat(),
                "at": e.effective_at.isoformat(),
                "description": e.memo,
                "category": cat,
                "kind": kind,
                "money_in_minor": delta if delta > 0 else 0,
                "money_out_minor": -delta if delta < 0 else 0,
                "balance_minor": balance,
                "source": _source(e),
                "status": "settled",
            }
        )
    return out


def pending_and_expected(db: Session, biz: Business) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    pending = []
    for t in db.execute(select(Transfer).where(Transfer.business_id == biz.id, Transfer.status == "pending").order_by(Transfer.submitted_at)).scalars():
        cp = db.get(Counterparty, t.counterparty_id)
        pending.append(
            {
                "id": str(t.id),
                "date": _local(biz, t.submitted_at).date().isoformat(),
                "description": f"Payment to {cp.name if cp else 'payee'} waiting for the bank" if t.direction == "out" else f"Payment from {cp.name if cp else 'customer'} waiting for the bank",
                "money_in_minor": t.amount_minor if t.direction == "in" else 0,
                "money_out_minor": t.amount_minor if t.direction == "out" else 0,
                "status": "pending",
                "source": "Sandbox bank (not settled yet)",
            }
        )
    expected = []
    for inv in db.execute(select(Invoice).where(Invoice.business_id == biz.id).order_by(Invoice.due_on)).scalars():
        if inv.outstanding_minor > 0:
            cp = db.get(Counterparty, inv.customer_id)
            expected.append({"id": str(inv.id), "date": inv.due_on.isoformat(), "description": f"Invoice {inv.number} from {cp.name if cp else 'customer'} (due)", "category": "Customer payment",
                             "money_in_minor": inv.outstanding_minor, "money_out_minor": 0, "status": "expected", "source": "Your invoice"})
    for ob in db.execute(select(Obligation).where(Obligation.business_id == biz.id).order_by(Obligation.due_at)).scalars():
        if ob.unpaid_minor > 0:
            expected.append({"id": str(ob.id), "date": _local(biz, ob.due_at).date().isoformat(), "description": f"{ob.description} (due)", "category": "Payroll" if ob.kind == "payroll" else "Bills and suppliers",
                             "money_in_minor": 0, "money_out_minor": ob.unpaid_minor, "status": "expected", "source": "Your bill"})
    expected.sort(key=lambda r: str(r["date"]))
    return pending, expected


def monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Per month: opening balance, money received, money paid, adjustments and closing balance.

    ``money_in_minor`` / ``money_out_minor`` / ``net_minor`` count receipts and payments only; declared
    balances and corrections are in ``adjustments_minor`` so they never look like income or spending.
    """
    months: OrderedDict[str, dict[str, Any]] = OrderedDict()
    balance = 0
    for r in rows:
        m = r["date"][:7]
        cur = months.get(m)
        if cur is None:
            cur = months[m] = {
                "month": m, "opening_balance_minor": balance, "money_in_minor": 0, "money_out_minor": 0, "net_minor": 0,
                "adjustments_minor": 0, "closing_balance_minor": balance, "count": 0,
            }
        delta = r["money_in_minor"] - r["money_out_minor"]
        if r.get("kind") == "adjustment":
            cur["adjustments_minor"] += delta
        else:
            cur["money_in_minor"] += r["money_in_minor"]
            cur["money_out_minor"] += r["money_out_minor"]
            cur["net_minor"] += delta
        balance = r["balance_minor"]
        cur["closing_balance_minor"] = balance
        cur["count"] += 1
    return list(months.values())


def balance_source(rows: list[dict[str, Any]]) -> str:
    """Where the settled balance comes from, in words. FlowSense never claims a real bank verified anything."""
    parts = []
    srcs = [str(r["source"]) for r in rows]
    if any(x.startswith("Sample opening") for x in srcs):
        parts.append("a synthetic sample opening balance")
    if any(x.startswith("Declared by you") for x in srcs):
        parts.append("the balance you declared (not bank-verified)")
    if any(x.startswith("Recorded by you") for x in srcs):
        parts.append("payments you recorded")
    if any(x.startswith("Sandbox bank") for x in srcs):
        parts.append("sandbox-confirmed settlements")
    if not parts:
        return "No money recorded yet."
    text = parts[0] if len(parts) == 1 else ", ".join(parts[:-1]) + " and " + parts[-1]
    return f"From {text}. No real bank is connected."


def history(
    db: Session, biz: Business, *, start: date | None = None, end: date | None = None, direction: str = "all", category: str | None = None,
) -> dict[str, Any]:
    rows = settled_rows(db, biz)
    summary = monthly(rows)  # summaries always cover the full history, before filters
    shown = [
        r for r in rows
        if (start is None or r["date"] >= start.isoformat())
        and (end is None or r["date"] <= end.isoformat())
        and (direction == "all" or (direction == "in" and r["money_in_minor"]) or (direction == "out" and r["money_out_minor"]))
        and (category is None or r["category"] == category)
    ]
    pending, expected = pending_and_expected(db, biz)
    ccy = biz.ccy
    return {
        "currency": ccy,
        "settled_balance_minor": rows[-1]["balance_minor"] if rows else 0,
        "settled_balance": fmt(rows[-1]["balance_minor"] if rows else 0, ccy),
        "balance_source": balance_source(rows),
        "rows": list(reversed(shown)),  # newest first for reading
        "months": list(reversed(summary)),
        "pending": pending,
        "expected": expected,
        "categories": CATEGORIES,
        "note": (
            "Only settled money changes the balance. Balances you declare are adjustments, not money received. "
            "Payments in progress and expected money are listed separately."
        ),
    }
