"""Routes for entering and managing business records, the business profile and the setup checklist.

Owners and staff may enter records (invoices, bills, payees); only owners confirm the business
profile, the cash balance, the reserve policy, and record payments made outside FlowSense.
Every route takes the business from the session.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, date, datetime, time
from functools import cache
from importlib import resources
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo, available_timezones

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import serialize as S
from app.api.deps import Ctx, get_ctx, mutating_ctx, owner_ctx
from app.api.personal import business_has_history
from app.audit import chain as audit
from app.clock import real_now
from app.db import get_db
from app.domain import records as rec
from app.domain import workspace as ws
from app.domain.actions import lock_business
from app.errors import ApiError, not_found
from app.models import Counterparty, Invoice, Obligation
from app.money import SUPPORTED_CURRENCIES
from app.planning import rescue
from app.reference import regions

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]


def _uuid(v: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise not_found(what) from exc


# ---------------------------------------------------------------- timezones


@cache
def _zone_countries() -> dict[str, list[str]]:
    """Country -> IANA zones, from the tzdata package's zone.tab (suggestions only)."""
    out: dict[str, list[str]] = {}
    try:
        text = resources.files("tzdata").joinpath("zoneinfo/zone.tab").read_text(encoding="utf-8")
    except (FileNotFoundError, ModuleNotFoundError):
        return out
    for line in text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            out.setdefault(parts[0], []).append(parts[2])
    return out


@cache
def _all_zones() -> list[str]:
    zones = {z for z in available_timezones() if "/" in z and not z.startswith(("Etc/", "SystemV/", "US/", "posix/", "right/")) and z[0].isupper()}
    return sorted(zones | {"UTC"})


def _offset(z: str) -> str:
    off = datetime.now(UTC).astimezone(ZoneInfo(z)).utcoffset()
    mins = int(off.total_seconds() // 60) if off else 0
    sign = "+" if mins >= 0 else "-"
    return f"UTC{sign}{abs(mins) // 60:02d}:{abs(mins) % 60:02d}"


@router.get("/reference/timezones")
def list_timezones(country: str | None = Query(default=None, max_length=2), ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    suggested = _zone_countries().get((country or "").upper(), []) if country else []
    return {
        "source": "IANA time zone database (Python tzdata)",
        "suggested": suggested,  # only after a country is chosen; never applied automatically
        "timezones": [{"name": z, "offset_now": _offset(z)} for z in _all_zones()],
    }


# ---------------------------------------------------------------- business profile


class LicenceIn(BaseModel):
    registration_number: str | None = Field(default=None, max_length=80)
    owner_name: str | None = Field(default=None, max_length=120)
    issue_date: date | None = None
    expiry_date: date | None = None


class ProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    licence: LicenceIn | None = None  # optional details, e.g. read from a trade licence and confirmed by the owner
    place: str | None = Field(default=None, max_length=200)
    business_type: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, max_length=2)
    currency: str = Field(min_length=3, max_length=3)
    timezone: str = Field(min_length=1, max_length=64)
    # The owner explicitly confirms currency and timezone (no silent defaults).
    confirm_currency_and_timezone: Literal[True]


@router.get("/business/profile")
def get_profile(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    b = ctx.business
    return {
        "name": b.name,
        "place": b.place,
        "business_type": b.business_type,
        "country": b.country,
        "licence": b.licence,
        "currency": b.currency,
        "timezone": b.timezone,
        "kind": b.kind,
        "configured": ws.is_configured(b),
        "currency_locked": business_has_history(db, b.id),
        "setup": ws.setup_status(db, b),
    }


@router.put("/business/profile")
def put_profile(body: ProfileIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    code = body.currency.upper()
    if code not in SUPPORTED_CURRENCIES:
        raise ApiError(422, "CURRENCY_UNKNOWN", f"{code} is not an active ISO 4217 currency code.")
    if body.timezone not in _all_zones():
        raise ApiError(422, "TIMEZONE_UNKNOWN", "Choose a timezone from the list.")
    country = body.country.upper() if body.country else None
    if country is not None and country not in regions():
        raise ApiError(422, "COUNTRY_UNKNOWN", "Unknown country or region code.")
    if biz.kind != "workspace" and (code != biz.currency or body.timezone != biz.timezone):
        raise ApiError(409, "FIXTURE_SETTINGS", "A demo or sample workspace keeps the currency and timezone of its scenario.")
    changed: dict[str, Any] = {}
    if code != biz.currency:
        if business_has_history(db, biz.id):
            raise ApiError(
                409,
                "ACCOUNTING_CURRENCY_LOCKED",
                f"This business already has records in {biz.currency}. Its accounting currency can't change, because existing "
                "amounts, approvals and the ledger would no longer mean the same thing.",
            )
        changed["currency"] = {"from": biz.currency, "to": code}
        biz.currency = code
    if body.timezone != biz.timezone:
        # Stored deadlines are absolute instants: changing the timezone changes how they are shown, never when they are due.
        changed["timezone"] = {"from": biz.timezone, "to": body.timezone}
        biz.timezone = body.timezone
    name = body.name.strip()[:200]
    if not name:
        raise ApiError(422, "NAME_REQUIRED", "Enter your business name.")
    if name != biz.name:
        changed["name"] = name
        biz.name = name
    for field in ("place", "business_type"):
        raw = getattr(body, field)
        v = raw.strip()[:200] or None if isinstance(raw, str) else None
        if v != getattr(biz, field):
            changed[field] = v
            setattr(biz, field, v)
    if country != biz.country:
        changed["country"] = country
        biz.country = country
    if body.licence is not None:
        lic = {k: (" ".join(v.split()) if isinstance(v, str) else v.isoformat()) for k, v in body.licence.model_dump().items() if v not in (None, "")}
        if body.licence.issue_date and body.licence.expiry_date and body.licence.expiry_date < body.licence.issue_date:
            raise ApiError(422, "DATES_INVALID", "The licence expiry date can't be before its issue date.")
        if (lic or None) != biz.licence:
            changed["licence"] = sorted(lic)
            biz.licence = lic or None
    now = real_now()
    if biz.currency_confirmed_at is None or "currency" in changed:
        biz.currency_confirmed_at = now
    if biz.timezone_confirmed_at is None or "timezone" in changed:
        biz.timezone_confirmed_at = now
    if changed:
        audit.append(db, biz.id, event_type="business.profile_updated", actor_type="user", actor_id=ctx.user.id, payload={"changed": changed})
    db.commit()
    return get_profile(db, ctx)


class NoneIn(BaseModel):
    step: Literal["money_in", "money_out"]
    none: bool


@router.post("/setup/none")
def mark_none(body: NoneIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    """The owner says there is nothing to enter for a step (different from 'not entered yet')."""
    biz = lock_business(db, ctx.business.id)
    stamp = real_now() if body.none else None
    if body.step == "money_in":
        biz.money_in_none_at = stamp
    else:
        biz.money_out_none_at = stamp
    audit.append(db, biz.id, event_type="setup.none_confirmed" if body.none else "setup.none_cleared", actor_type="user", actor_id=ctx.user.id, payload={"step": body.step})
    db.commit()
    return ws.setup_status(db, biz)


# --------------------------------------------------------------------------- cash


class CashIn(BaseModel):
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)
    as_of: date
    account_label: str | None = Field(default=None, max_length=100)


@router.post("/cash/declare")
def declare_cash(body: CashIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = ctx.business
    out = rec.declare_cash(db, biz, ctx.user.id, amount=body.amount, currency=body.currency, as_of=body.as_of, account_label=body.account_label)
    if out["outcome"] not in ("declared",):
        raise ApiError(409, "CASH_NOT_RECORDED", out["detail"])
    return {**out, "setup": ws.setup_status(db, db.get(type(biz), biz.id) or biz)}


# ----------------------------------------------------------------- counterparties


_EMAIL = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$")


def _contact(email: str | None, phone: str | None) -> dict[str, str] | None:
    out: dict[str, str] = {}
    if email and email.strip():
        e = email.strip().lower()
        if len(e) > 254 or not _EMAIL.match(e):
            raise ApiError(422, "EMAIL_INVALID", "Enter a valid email address, like name@example.com.", extra={"fields": {"email": "Enter a valid email address."}})
        out["email"] = e
    if phone and phone.strip():
        p = " ".join(phone.split())
        digits = re.sub(r"\D", "", p)
        if not re.fullmatch(r"\+?[\d\s()./-]{6,24}", p) or not 6 <= len(digits) <= 15:
            raise ApiError(422, "PHONE_INVALID", "Enter a phone number with 6 to 15 digits.", extra={"fields": {"phone": "Enter a valid phone number."}})
        out["phone"] = p
    return out or None


class PartyIn(BaseModel):
    kind: Literal["customer", "supplier", "payroll"]
    name: str = Field(min_length=1, max_length=200)
    accepts_partial: bool = False
    typical_days_to_pay: int = Field(default=7, ge=0, le=120)
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)


@router.post("/parties")
def create_party(body: PartyIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    contact = _contact(body.email, body.phone)
    cp = rec.create_counterparty(db, biz, ctx.user.id, kind=body.kind, name=body.name, accepts_partial=body.accepts_partial, days_to_pay=body.typical_days_to_pay)
    if contact and not cp.contact:
        cp.contact = contact  # an existing customer's saved details are never overwritten silently
    db.commit()
    return S.counterparty(cp)


class ContactIn(BaseModel):
    email: str | None = Field(default=None, max_length=254)
    phone: str | None = Field(default=None, max_length=40)
    typical_days_to_pay: int | None = Field(default=None, ge=0, le=120)


@router.patch("/parties/{party_id}")
def edit_party(party_id: str, body: ContactIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """Edit contact details (not the name or the sandbox account: those go through the recipient-change workflow)."""
    cp = db.get(Counterparty, _uuid(party_id, "Customer"))
    if cp is None or cp.business_id != ctx.business.id:
        raise ApiError(404, "NOT_FOUND", "Customer not found.")
    cp.contact = _contact(body.email, body.phone)
    if body.typical_days_to_pay is not None:
        cp.typical_days_to_pay = body.typical_days_to_pay
    audit.append(db, ctx.business.id, event_type="counterparty.contact_updated", actor_type="user", actor_id=ctx.user.id, payload={"counterparty": str(cp.id), "fields": sorted((cp.contact or {}).keys())})
    db.commit()
    return S.counterparty(cp)


@router.get("/customers")
def customers(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    """Customers with what they owe, from the invoices (expected money, not cash)."""
    from app.models import DocumentLink

    biz = ctx.business
    today = rec.business_today(biz) if ws.is_configured(biz) else None
    cps = db.execute(select(Counterparty).where(Counterparty.business_id == biz.id, Counterparty.kind == "customer").order_by(Counterparty.name)).scalars().all()
    invs = db.execute(select(Invoice).where(Invoice.business_id == biz.id)).scalars().all()
    docs = {rid for (rid,) in db.execute(select(DocumentLink.record_id).where(DocumentLink.business_id == biz.id, DocumentLink.record_type.in_(("counterparty", "invoice"))))}
    out = []
    for c in cps:
        mine = [i for i in invs if i.customer_id == c.id]
        open_ = [i for i in mine if i.outstanding_minor > 0]
        out.append({
            **S.counterparty(c),
            "invoices": len(mine),
            "open_invoices": len(open_),
            "owed_minor": sum(i.outstanding_minor for i in open_),
            "overdue_minor": sum(i.outstanding_minor for i in open_ if today and i.due_on < today),
            # Dates are compared with the business date (the simulated clock in samples): a past date is never "next due".
            "next_due": min((i.due_on for i in open_ if today is None or i.due_on >= today), default=None),
            "oldest_overdue": min((i.due_on for i in open_ if today and i.due_on < today), default=None),
            "last_invoice": max((i.issued_on for i in mine), default=None),
            "from_document": c.id in docs or any(i.id in docs for i in mine),
        })
    return {"currency": biz.currency, "customers": out, "total_owed_minor": sum(c["owed_minor"] for c in out)}


# ---------------------------------------------------------------------- invoices


class InvoiceIn(BaseModel):
    customer_id: uuid.UUID | None = None
    new_customer: str | None = Field(default=None, max_length=200)
    number: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)
    issued_on: date
    due_on: date
    accepts_partial: bool = True
    typical_days_to_pay: int = Field(default=7, ge=0, le=120)


@router.post("/invoices")
def create_invoice(body: InvoiceIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    inv = rec.create_invoice(
        db,
        biz,
        ctx.user.id,
        customer_id=body.customer_id,
        new_customer=body.new_customer,
        number=body.number,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        issued_on=body.issued_on,
        due_on=body.due_on,
        accepts_partial=body.accepts_partial,
        days_to_pay=body.typical_days_to_pay,
    )
    rescue.refresh(db, biz, f"Invoice {inv.number} added")
    db.commit()
    return S.invoice(db, inv)


class InvoiceEditIn(BaseModel):
    description: str | None = Field(default=None, max_length=500)
    amount: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, max_length=3)
    issued_on: date | None = None
    due_on: date | None = None
    disputed: bool | None = None


def _inv(db: Session, ctx: Ctx, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, _uuid(invoice_id, "Invoice"))
    if inv is None or inv.business_id != ctx.business.id:
        raise not_found("Invoice")
    return inv


@router.patch("/invoices/{invoice_id}")
def edit_invoice(invoice_id: str, body: InvoiceEditIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    inv = rec.update_invoice(db, biz, ctx.user.id, _inv(db, ctx, invoice_id), body.model_dump())
    rescue.refresh(db, biz, f"Invoice {inv.number} edited")
    db.commit()
    return S.invoice(db, inv)


class PaymentIn(BaseModel):
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)


class ObligationPaymentIn(PaymentIn):
    paid_on: date | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=64)


@router.post("/invoices/{invoice_id}/payments")
def record_invoice_payment(invoice_id: str, body: PaymentIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    inv = _inv(db, ctx, invoice_id)
    out = rec.record_customer_payment(db, ctx.business, ctx.user.id, inv, body.amount, body.currency)
    if out["outcome"] != "settled":
        raise ApiError(409, "PAYMENT_NOT_RECORDED", out["detail"])
    return out


# ------------------------------------------------------------------- obligations


class ObligationIn(BaseModel):
    kind: Literal["payroll", "supplier", "other"]
    payee_id: uuid.UUID | None = None
    new_payee: str | None = Field(default=None, max_length=200)
    description: str = Field(min_length=1, max_length=400)
    amount: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)
    due_on: date
    due_time: time = time(17, 0)
    essential: bool = False
    recurrence: Literal["none", "weekly", "monthly"] = "none"


def obligation_view(db: Session, ob: Obligation, biz: Any) -> dict[str, Any]:
    from app.models import Action, Transfer

    cp = db.get(Counterparty, ob.counterparty_id)
    pending = db.execute(select(Transfer.id).where(Transfer.obligation_id == ob.id, Transfer.status == "pending").limit(1)).first() is not None
    act = db.execute(
        select(Action).where(Action.obligation_id == ob.id, Action.status.in_(("proposed", "approved", "submitted"))).order_by(Action.created_at.desc()).limit(1)
    ).scalar_one_or_none()
    if ob.paid_minor >= ob.amount_minor:
        state = "paid"
    elif pending or (act and act.status == "submitted"):
        state = "payment_in_progress"
    elif act and act.status == "approved":
        state = "ready_to_send"
    elif act and act.status == "proposed":
        state = "needs_approval"
    else:
        state = "planned"
    overdue = ob.paid_minor < ob.amount_minor and ob.due_at < (biz.sandbox_now or real_now())
    return {
        "id": str(ob.id),
        "kind": ob.kind,
        "description": ob.description,
        "payee": S.counterparty(cp) if cp else None,
        "amount_minor": ob.amount_minor,
        "paid_minor": ob.paid_minor,
        "unpaid_minor": ob.unpaid_minor,
        "due_at": ob.due_at.isoformat(),
        "essential": ob.essential or ob.kind == "payroll",
        "recurrence": ob.recurrence,
        "series_id": str(ob.series_id) if ob.series_id else None,
        "state": state,
        "overdue": overdue,
    }


@router.get("/obligations")
def list_obligations(db: DB, ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    rows = db.execute(select(Obligation).where(Obligation.business_id == ctx.business.id).order_by(Obligation.due_at)).scalars().all()
    return [obligation_view(db, o, ctx.business) for o in rows]


@router.post("/obligations")
def create_obligation(body: ObligationIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    obs = rec.create_obligation(
        db,
        biz,
        ctx.user.id,
        kind=body.kind,
        payee_id=body.payee_id,
        new_payee=body.new_payee,
        description=body.description,
        amount=body.amount,
        currency=body.currency,
        due_on=body.due_on,
        due_time=body.due_time,
        essential=body.essential,
        recurrence=body.recurrence,
    )
    rescue.refresh(db, biz, f"{obs[0].description} added")
    db.commit()
    return {"created": [obligation_view(db, o, biz) for o in obs]}


class ObligationEditIn(BaseModel):
    description: str | None = Field(default=None, max_length=400)
    amount: str | None = Field(default=None, max_length=32)
    currency: str | None = Field(default=None, max_length=3)
    due_on: date | None = None
    due_time: time | None = None
    essential: bool | None = None


def _ob(db: Session, ctx: Ctx, obligation_id: str) -> Obligation:
    ob = db.get(Obligation, _uuid(obligation_id, "Bill"))
    if ob is None or ob.business_id != ctx.business.id:
        raise not_found("Bill")
    return ob


@router.patch("/obligations/{obligation_id}")
def edit_obligation(obligation_id: str, body: ObligationEditIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    ob, revoked = rec.update_obligation(db, biz, ctx.user.id, _ob(db, ctx, obligation_id), body.model_dump())
    rescue.refresh(db, biz, f"{ob.description} edited")
    db.commit()
    return {**obligation_view(db, ob, biz), "revoked_approvals": revoked}


@router.post("/obligations/{obligation_id}/payments")
def record_obligation_payment(obligation_id: str, body: ObligationPaymentIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    ob = _ob(db, ctx, obligation_id)
    out = rec.record_external_payment(db, ctx.business, ctx.user.id, ob, body.amount, body.currency, body.paid_on, body.idempotency_key)
    if out["outcome"] in ("duplicate", "already_settled") and body.idempotency_key:
        return {**out, "outcome": "settled", "replayed": True, "detail": "This payment was already recorded; nothing was added."}
    if out["outcome"] != "settled":
        raise ApiError(409, "PAYMENT_NOT_RECORDED", out["detail"])
    return out


# ------------------------------------------------------------------------- policy


class BasicPolicyIn(BaseModel):
    reserve: str = Field(min_length=1, max_length=32)
    currency: str = Field(min_length=3, max_length=3)
    protection_horizon_days: Literal[7, 14, 30] = 14
    per_payment_limit: str | None = Field(default=None, max_length=32)
    max_financing_pct: str | None = Field(default=None, max_length=8)


@router.post("/policy/basic")
def basic_policy(body: BasicPolicyIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = lock_business(db, ctx.business.id)
    if biz.kind != "workspace":
        raise ApiError(409, "FIXTURE_POLICY", "Demo and sample workspaces use the scenario's policy. Policies can be proposed with the assistant.")
    p = rec.confirm_basic_policy(
        db,
        biz,
        ctx.user.id,
        reserve=body.reserve,
        currency=body.currency,
        horizon_days=body.protection_horizon_days,
        per_payment_limit=body.per_payment_limit,
        max_financing_pct=body.max_financing_pct,
    )
    rescue.refresh(db, biz, "Owner confirmed the reserve and limits")
    db.commit()
    return {"policy": S.policy(p), "setup": ws.setup_status(db, biz)}
