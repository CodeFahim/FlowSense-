"""Business records a user enters: customers/payees, invoices (money coming in), bills and
payroll (money going out), available cash and the basic safety policy.

Rules:
- Records need a confirmed accounting currency and timezone (no presets).
- Records are in the accounting currency only. Other currencies are refused with an
  explanation: FlowSense never adds amounts in different currencies together, and a
  display-currency preference is never a conversion of stored records.
- Invoices post receivable/revenue and bills post expense/payable to the double-entry
  ledger. Neither touches settled cash.
- Settled cash changes only through signed events in reconciliation.ingest: a declared
  balance, a customer payment, or a bill recorded as paid outside FlowSense.
- Editing an amount posts an adjusting entry (history is never rewritten) and is refused
  once money was collected/paid or a request/payment is in flight. Any edit that changes
  an obligation revokes approvals bound to actions on it.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.clock import business_now
from app.domain.actions import revoke_open_approvals
from app.domain.workspace import require_configured
from app.errors import ApiError
from app.ledger import service as ledger
from app.models import Action, Business, Counterparty, Invoice, Obligation, Policy, Transfer
from app.money import MAX_MINOR, MoneyError, fmt, parse_major
from app.policies.compiler import COST_CONVENTION as COST_CONVENTION_NOTE
from app.reconciliation import ingest as recon
from app.sandbox import simulator

OPEN_ACTION_STATUSES = ("proposed", "approved", "submitted", "sent", "accepted", "partially_settled")
KIND_ACCOUNTS = {
    "payroll": (ledger.PAYROLL_EXPENSE, ledger.PAYROLL_PAYABLE),
    "supplier": (ledger.SUPPLIER_EXPENSE, ledger.SUPPLIER_PAYABLE),
    "other": (ledger.SUPPLIER_EXPENSE, ledger.SUPPLIER_PAYABLE),
}
MAX_OCCURRENCES = {"weekly": 13, "monthly": 6}


def amount_minor(text: str, currency: str, biz: Business, *, allow_zero: bool = False) -> int:
    if currency.upper() != biz.ccy:
        raise ApiError(
            422,
            "CURRENCY_NOT_ACCOUNTING",
            f"This business keeps its books in {biz.ccy}. Enter the amount in {biz.ccy}. FlowSense does not add up amounts in "
            "different currencies; use the converter for an indicative figure.",
        )
    t = (text or "").strip()
    if allow_zero and t in ("0", "0.0", "0.00", "0.000"):
        return 0
    try:
        return parse_major(t, biz.ccy)
    except MoneyError as exc:
        msg = str(exc)
        if "decimal places" in msg:
            raise ApiError(422, "AMOUNT_INVALID", f"{biz.ccy} amounts have at most {_places(biz)} decimal places.") from exc
        if "upper bound" in msg:
            raise ApiError(422, "AMOUNT_INVALID", "That amount is larger than FlowSense supports.") from exc
        raise ApiError(422, "AMOUNT_INVALID", f"Enter a positive amount like 1,250.00 (in {biz.ccy}).") from exc


def _places(biz: Business) -> int:
    from app.money import SUPPORTED_CURRENCIES

    return SUPPORTED_CURRENCIES[biz.ccy]


def business_today(biz: Business) -> date:
    return business_now(biz).astimezone(ZoneInfo(biz.tz_name)).date()


def local_deadline(biz: Business, day: date, at: time) -> datetime:
    """A wall-clock time on a date in the business timezone (DST-aware via zoneinfo)."""
    zone = ZoneInfo(biz.tz_name)
    local = datetime.combine(day, at, zone)
    # Non-existent local times (spring-forward gap) are normalised by a UTC round trip.
    return local.astimezone(ZoneInfo("UTC"))


def _sandbox_ref(kind: str) -> str:
    return f"SBX-{kind[:4].upper()}-{secrets.token_hex(4).upper()}"


# ------------------------------------------------------------------ counterparties


def create_counterparty(
    db: Session,
    biz: Business,
    user_id: uuid.UUID,
    *,
    kind: str,
    name: str,
    account_ref: str | None = None,
    accepts_partial: bool = False,
    days_to_pay: int = 7,
) -> Counterparty:
    require_configured(biz)
    name = name.strip()
    if not name:
        raise ApiError(422, "NAME_REQUIRED", "Enter a name.")
    dup = db.execute(
        select(Counterparty).where(Counterparty.business_id == biz.id, func.lower(Counterparty.name) == name.lower(), Counterparty.kind == kind)
    ).scalar_one_or_none()
    if dup is not None:
        return dup
    cp = Counterparty(
        business_id=biz.id,
        kind=kind,
        name=name[:200],
        # A simulated sandbox account; nothing here is a real bank account.
        account_ref=account_ref or _sandbox_ref(kind),
        verified=False,
        accepts_partial=accepts_partial,
        typical_days_to_pay=max(0, min(days_to_pay, 120)),
    )
    db.add(cp)
    db.flush()
    audit.append(db, biz.id, event_type="counterparty.created_unverified", actor_type="user", actor_id=user_id, payload={"name": cp.name, "kind": kind, "account_ref": cp.account_ref})
    return cp


def _counterparty(db: Session, biz: Business, cp_id: uuid.UUID | None, new_name: str | None, kind: str, user_id: uuid.UUID, **kw: Any) -> Counterparty:
    if cp_id is not None:
        cp = db.get(Counterparty, cp_id)
        if cp is None or cp.business_id != biz.id:
            raise ApiError(404, "NOT_FOUND", "Customer or payee not found.")
        if kind == "customer" and cp.kind != "customer":
            raise ApiError(422, "COUNTERPARTY_KIND", f"{cp.name} is not a customer.")
        if kind != "customer" and cp.kind == "customer":
            raise ApiError(422, "COUNTERPARTY_KIND", f"{cp.name} is a customer, not a payee.")
        return cp
    if not (new_name or "").strip():
        raise ApiError(422, "COUNTERPARTY_REQUIRED", "Choose an existing name or type a new one.")
    return create_counterparty(db, biz, user_id, kind=kind, name=new_name or "", **kw)


# ------------------------------------------------------------------------ invoices


def create_invoice(
    db: Session,
    biz: Business,
    user_id: uuid.UUID,
    *,
    customer_id: uuid.UUID | None,
    new_customer: str | None,
    number: str,
    description: str,
    amount: str,
    currency: str,
    issued_on: date,
    due_on: date,
    accepts_partial: bool = True,
    days_to_pay: int = 7,
    source: str = "user",
) -> Invoice:
    require_configured(biz)
    minor = amount_minor(amount, currency, biz)
    number = number.strip()
    if not number:
        raise ApiError(422, "NUMBER_REQUIRED", "Enter the invoice number or reference.")
    if due_on < issued_on:
        raise ApiError(422, "DATES_INVALID", "The due date can't be before the issue date.")
    if db.execute(select(Invoice.id).where(Invoice.business_id == biz.id, Invoice.number == number)).first():
        raise ApiError(409, "INVOICE_DUPLICATE", f"An invoice numbered {number} already exists.")
    cp = _counterparty(db, biz, customer_id, new_customer, "customer", user_id, accepts_partial=accepts_partial, days_to_pay=days_to_pay)
    inv = Invoice(
        business_id=biz.id,
        customer_id=cp.id,
        number=number[:64],
        description=(description.strip() or f"Invoice {number}")[:500],
        issued_on=issued_on,
        due_on=due_on,
        amount_minor=minor,
    )
    db.add(inv)
    db.flush()
    ledger.post_entry(
        db,
        biz.id,
        effective_at=business_now(biz),
        memo=f"Invoice {inv.number} to {cp.name}",
        source_type="invoice",
        source_id=inv.id,
        lines=[(ledger.AR, minor, 0), (ledger.REVENUE, 0, minor)],
    )
    audit.append(
        db,
        biz.id,
        event_type="invoice.created",
        actor_type="user",
        actor_id=user_id,
        payload={"invoice": inv.number, "customer": cp.name, "amount_minor": minor, "due_on": due_on, "source": source},
    )
    return inv


def _invoice_busy(db: Session, inv: Invoice) -> bool:
    return db.execute(select(Action.id).where(Action.invoice_id == inv.id, Action.status.in_(OPEN_ACTION_STATUSES)).limit(1)).first() is not None


def update_invoice(db: Session, biz: Business, user_id: uuid.UUID, inv: Invoice, changes: dict[str, Any]) -> Invoice:
    require_configured(biz)
    changed: dict[str, Any] = {}
    if "amount" in changes and changes["amount"] is not None:
        new = amount_minor(changes["amount"], changes.get("currency") or biz.ccy, biz)
        if new != inv.amount_minor:
            if inv.collected_minor > 0 or _invoice_busy(db, inv):
                raise ApiError(409, "INVOICE_LOCKED", "Money was already collected or requested on this invoice, so its amount can't change. Record a correction instead.")
            delta = new - inv.amount_minor
            ledger.post_entry(
                db,
                biz.id,
                effective_at=business_now(biz),
                memo=f"Invoice {inv.number} amount corrected",
                source_type="invoice_adjustment",
                source_id=inv.id,
                lines=[(ledger.AR, delta, 0), (ledger.REVENUE, 0, delta)] if delta > 0 else [(ledger.REVENUE, -delta, 0), (ledger.AR, 0, -delta)],
            )
            changed["amount_minor"] = {"from": inv.amount_minor, "to": new}
            inv.amount_minor = new
    for field in ("description", "due_on", "issued_on"):
        v = changes.get(field)
        if v is not None and getattr(inv, field) != v:
            changed[field] = {"from": getattr(inv, field), "to": v}
            setattr(inv, field, v)
    if inv.due_on < inv.issued_on:
        raise ApiError(422, "DATES_INVALID", "The due date can't be before the issue date.")
    if "disputed" in changes and changes["disputed"] is not None and changes["disputed"] != inv.disputed:
        changed["disputed"] = changes["disputed"]
        inv.disputed = bool(changes["disputed"])
    if changed:
        audit.append(db, biz.id, event_type="invoice.updated", actor_type="user", actor_id=user_id, payload={"invoice": inv.number, "changed": changed})
    return inv


def record_customer_payment(db: Session, biz: Business, user_id: uuid.UUID, inv: Invoice, amount: str, currency: str) -> dict[str, Any]:
    """Owner records money received for an invoice (own workspace only). Goes through the signed-event path."""
    require_configured(biz)
    if biz.kind != "workspace":
        raise ApiError(409, "USE_SIMULATOR", "In demo and sample workspaces, customer payments come from the sandbox simulator in Approvals.")
    minor = amount_minor(amount, currency, biz)
    if minor > inv.outstanding_minor:
        raise ApiError(422, "AMOUNT_EXCEEDS_OUTSTANDING", f"Only {fmt(inv.outstanding_minor, biz.ccy)} is still outstanding on {inv.number}.")
    raw = simulator.build_event(
        biz.id,
        "payment.received",
        {"invoice_id": str(inv.id), "amount_minor": minor, "provider_ref": f"OWN-IN-{uuid.uuid4().hex[:16].upper()}", "source": "owner_recorded"},
    )
    db.commit()
    return recon.ingest(db, raw, recon.sign(raw))


# --------------------------------------------------------------------- obligations


def create_obligation(
    db: Session,
    biz: Business,
    user_id: uuid.UUID,
    *,
    kind: str,
    payee_id: uuid.UUID | None,
    new_payee: str | None,
    description: str,
    amount: str,
    currency: str,
    due_on: date,
    due_time: time,
    essential: bool,
    recurrence: str = "none",
    source: str = "user",
) -> list[Obligation]:
    require_configured(biz)
    if kind not in KIND_ACCOUNTS:
        raise ApiError(422, "KIND_INVALID", "Choose payroll, supplier bill or other payment.")
    minor = amount_minor(amount, currency, biz)
    if not description.strip():
        raise ApiError(422, "DESCRIPTION_REQUIRED", "Describe the payment, for example 'October rent'.")
    cp = _counterparty(db, biz, payee_id, new_payee, "payroll" if kind == "payroll" else "supplier", user_id)
    expense, payable = KIND_ACCOUNTS[kind]
    essential = essential or kind == "payroll"
    n = MAX_OCCURRENCES.get(recurrence, 1)
    series = uuid.uuid4() if recurrence != "none" else None
    out: list[Obligation] = []
    for i in range(n):
        if recurrence == "weekly":
            day = due_on + timedelta(weeks=i)
        elif recurrence == "monthly":
            m = due_on.month - 1 + i
            y, m = due_on.year + m // 12, m % 12 + 1
            import calendar

            day = date(y, m, min(due_on.day, calendar.monthrange(y, m)[1]))
        else:
            day = due_on
        ob = Obligation(
            business_id=biz.id,
            kind=kind,
            counterparty_id=cp.id,
            description=(description.strip() + (f" ({day:%d %b %Y})" if series else ""))[:500],
            amount_minor=minor,
            due_at=local_deadline(biz, day, due_time),
            essential=essential,
            payable_account_code=payable,
            series_id=series,
            recurrence=recurrence,
        )
        db.add(ob)
        db.flush()
        ledger.post_entry(
            db,
            biz.id,
            effective_at=business_now(biz),
            memo=f"{ob.description} (to {cp.name})",
            source_type="obligation",
            source_id=ob.id,
            lines=[(expense, minor, 0), (payable, 0, minor)],
        )
        out.append(ob)
    audit.append(
        db,
        biz.id,
        event_type="obligation.created",
        actor_type="user",
        actor_id=user_id,
        payload={
            "description": description.strip(),
            "kind": kind,
            "payee": cp.name,
            "amount_minor": minor,
            "first_due_at": out[0].due_at,
            "occurrences": len(out),
            "recurrence": recurrence,
            "essential": essential,
            "source": source,
        },
    )
    return out


def _obligation_busy(db: Session, ob: Obligation) -> bool:
    return (
        db.execute(select(Transfer.id).where(Transfer.obligation_id == ob.id, Transfer.status == "pending").limit(1)).first() is not None
        or db.execute(select(Action.id).where(Action.obligation_id == ob.id, Action.status == "submitted").limit(1)).first() is not None
    )


def update_obligation(db: Session, biz: Business, user_id: uuid.UUID, ob: Obligation, changes: dict[str, Any]) -> tuple[Obligation, list[str]]:
    require_configured(biz)
    if _obligation_busy(db, ob):
        raise ApiError(409, "OBLIGATION_LOCKED", "A payment for this bill is in progress. Wait until it settles or fails.")
    changed: dict[str, Any] = {}
    if changes.get("amount") is not None:
        new = amount_minor(changes["amount"], changes.get("currency") or biz.ccy, biz)
        if new != ob.amount_minor:
            if new < ob.paid_minor:
                raise ApiError(422, "AMOUNT_BELOW_PAID", f"{fmt(ob.paid_minor, biz.ccy)} is already paid; the amount can't be lower.")
            expense, payable = KIND_ACCOUNTS.get(ob.kind, KIND_ACCOUNTS["other"])
            delta = new - ob.amount_minor
            ledger.post_entry(
                db,
                biz.id,
                effective_at=business_now(biz),
                memo=f"{ob.description} amount corrected",
                source_type="obligation_adjustment",
                source_id=ob.id,
                lines=[(expense, delta, 0), (payable, 0, delta)] if delta > 0 else [(payable, -delta, 0), (expense, 0, -delta)],
            )
            changed["amount_minor"] = {"from": ob.amount_minor, "to": new}
            ob.amount_minor = new
    if changes.get("due_on") is not None:
        local = ob.due_at.astimezone(ZoneInfo(biz.tz_name))
        new_at = local_deadline(biz, changes["due_on"], changes.get("due_time") or local.time().replace(tzinfo=None))
        if new_at != ob.due_at:
            changed["due_at"] = {"from": ob.due_at, "to": new_at}
            ob.due_at = new_at
    if changes.get("description") is not None and changes["description"].strip() and changes["description"].strip() != ob.description:
        changed["description"] = changes["description"].strip()[:500]
        ob.description = changed["description"]
    if changes.get("essential") is not None and ob.kind != "payroll" and bool(changes["essential"]) != ob.essential:
        changed["essential"] = bool(changes["essential"])
        ob.essential = bool(changes["essential"])
    revoked: list[str] = []
    if changed:
        for a in db.execute(select(Action).where(Action.obligation_id == ob.id, Action.status.in_(("proposed", "approved")))).scalars():
            if "amount_minor" in changed and a.amount_minor > ob.unpaid_minor:
                a.amount_minor = ob.unpaid_minor
            a.revision += 1
            a.status = "proposed"
            revoked += revoke_open_approvals(db, a, "The bill it pays was edited")
        audit.append(
            db,
            biz.id,
            event_type="obligation.updated",
            actor_type="user",
            actor_id=user_id,
            payload={"description": ob.description, "changed": changed, "revoked_approvals": revoked},
        )
    return ob, revoked


def record_external_payment(
    db: Session,
    biz: Business,
    user_id: uuid.UUID,
    ob: Obligation,
    amount: str,
    currency: str,
    paid_on: date | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """The owner records a payment made outside FlowSense.

    Dates: the cash movement is dated on the day it was paid (``paid_on``, default today); the audit trail keeps the
    time it was entered. A payment dated before the "as of" date of the balance the owner declared is already inside
    that balance, so it closes the bill without changing cash again. Future dates are refused, and so are dates more
    than a year back (declare the current balance instead). The same idempotency key and payload is recorded once.
    """
    require_configured(biz)
    if biz.kind != "workspace":
        raise ApiError(409, "USE_APPROVALS", "In demo and sample workspaces, payments go through Approvals.")
    minor = amount_minor(amount, currency, biz)
    today = business_today(biz)
    paid_on = paid_on or today
    if paid_on > today:
        raise ApiError(422, "DATE_IN_FUTURE", "The payment date can't be in the future. For a bill you will pay later, choose \"Pay later\".", extra={"field": "paid_on"})
    if paid_on < today - timedelta(days=366):
        raise ApiError(
            422, "DATE_TOO_OLD",
            "That is more than a year ago. FlowSense starts from the balance you declare: enter your current balance instead of old payments.",
            extra={"field": "paid_on"},
        )
    included = biz.cash_as_of is not None and paid_on < biz.cash_as_of  # same day: ambiguous, treated as after the balance
    event_id = None
    ref = f"OWN-OUT-{uuid.uuid4().hex[:16].upper()}"
    if idempotency_key:
        h = hashlib.sha256(f"{biz.id}:{ob.id}:{idempotency_key}:{minor}:{paid_on.isoformat()}".encode()).hexdigest().upper()
        event_id, ref = f"EVT-OUT-{h[:24]}", f"OWN-OUT-{h[:16]}"
    raw = simulator.build_event(
        biz.id,
        "payment.sent_externally",
        {
            "obligation_id": str(ob.id),
            "amount_minor": minor,
            "provider_ref": ref,
            "user_id": str(user_id),
            "paid_on": paid_on.isoformat(),
            "included_in_declared_balance": included,
            "declared_as_of": biz.cash_as_of.isoformat() if biz.cash_as_of else None,
        },
        event_id=event_id,
    )
    db.commit()
    return recon.ingest(db, raw, recon.sign(raw))


# --------------------------------------------------------------------------- cash


def declare_cash(db: Session, biz: Business, user_id: uuid.UUID, *, amount: str, currency: str, as_of: date, account_label: str | None) -> dict[str, Any]:
    require_configured(biz)
    if biz.kind != "workspace":
        raise ApiError(409, "DEMO_CASH_FIXED", "Demo and sample workspaces have fixed sandbox balances. Use Demo scenarios to reset them.")
    minor = amount_minor(amount, currency, biz, allow_zero=True)
    if as_of > business_today(biz):
        raise ApiError(422, "DATE_IN_FUTURE", "The balance date can't be in the future.")
    raw = simulator.build_event(
        biz.id,
        "balance.declared",
        {"amount_minor": minor, "as_of": as_of.isoformat(), "account_label": account_label or "", "user_id": str(user_id), "declaration_id": uuid.uuid4().hex},
    )
    db.commit()
    return recon.ingest(db, raw, recon.sign(raw))


# ------------------------------------------------------------------------- policy


def confirm_basic_policy(
    db: Session,
    biz: Business,
    user_id: uuid.UUID,
    *,
    reserve: str,
    currency: str,
    horizon_days: int,
    per_payment_limit: str | None,
    max_financing_pct: str | None,
) -> Policy:
    """The owner confirms the reserve and limits. Nothing is preset: every value comes from the form."""
    from decimal import Decimal, InvalidOperation

    require_configured(biz)
    reserve_minor = amount_minor(reserve, currency, biz, allow_zero=True)
    if horizon_days not in (7, 14, 30):
        raise ApiError(422, "HORIZON_INVALID", "Choose 7, 14 or 30 days.")
    limit_minor = amount_minor(per_payment_limit, currency, biz) if per_payment_limit else MAX_MINOR
    bp = 0
    if max_financing_pct:
        try:
            pct = Decimal(max_financing_pct.strip().rstrip("%"))
        except InvalidOperation as exc:
            raise ApiError(422, "RATE_INVALID", "Enter a percentage such as 8.") from exc
        if not (0 <= pct <= 100):
            raise ApiError(422, "RATE_INVALID", "Enter a percentage between 0 and 100.")
        bp = int(pct * 100)
    spec = {
        "currency": biz.ccy,
        "timezone": biz.tz_name,
        "protection_horizon_days": horizon_days,
        "per_transaction_limit_minor": limit_minor,
        "financing_cost_convention": COST_CONVENTION_NOTE,
        "essential_obligation_kinds": ["payroll"],
        "approved_recipients_only": True,
        "reserve_minor": reserve_minor,
        "max_financing_annualized_bp": bp,
    }
    fields = [
        {"field": "Protected cash reserve", "value": fmt(reserve_minor, biz.ccy), "source": "stated"},
        {"field": "Protection horizon", "value": f"{horizon_days} days of essential payments", "source": "stated"},
        {"field": "Per-payment limit", "value": fmt(limit_minor, biz.ccy) if per_payment_limit else "No limit set", "source": "stated" if per_payment_limit else "default"},
        {"field": "Financing cost cap", "value": f"{bp / 100:.2f}% annualized all-in" if bp else "No borrowing allowed", "source": "stated" if max_financing_pct else "default"},
        {"field": "Recipients", "value": "Only verified sandbox recipients", "source": "default", "note": "FlowSense always requires verified recipients."},
        {"field": "Timezone / currency", "value": f"{biz.tz_name} / {biz.ccy}", "source": "stated"},
    ]
    version = (db.execute(select(func.max(Policy.version)).where(Policy.business_id == biz.id)).scalar_one() or 0) + 1
    from sqlalchemy import update

    db.execute(update(Policy).where(Policy.business_id == biz.id, Policy.status == "active").values(status="superseded"))
    db.flush()
    from app.clock import real_now

    p = Policy(
        business_id=biz.id,
        version=version,
        status="active",
        source_text=f"Keep {fmt(reserve_minor, biz.ccy)} in reserve; protect {horizon_days} days of essential payments.",
        spec=spec,
        interpretation={"fields": fields, "ambiguities": [], "questions": []},
        created_by=user_id,
        activated_by=user_id,
        activated_at=real_now(),
    )
    db.add(p)
    db.flush()
    audit.append(
        db,
        biz.id,
        event_type="policy.activated",
        actor_type="user",
        actor_id=user_id,
        policy_version=version,
        payload={"reserve_minor": reserve_minor, "horizon_days": horizon_days, "note": "Confirmed by the owner in the setup form. Approvals bound to earlier versions can no longer execute."},
    )
    return p
