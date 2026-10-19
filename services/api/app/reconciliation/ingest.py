"""Authenticated, duplicate-safe ingestion of sandbox bank / counterparty events.

Only events carrying a valid HMAC-SHA256 signature (shared secret between the
simulator and FlowSense) are processed. The browser can never assert that a
payment settled; it can only ask the *simulator* (demo-only) to produce an
event, which then comes through this same verification path.

Duplicate events (same provider_event_id) are recorded once and ignored after.
A transfer that is already settled ignores further settlement events.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, time
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.audit.chain import canonical, sha256_hex
from app.clock import business_now
from app.config import get_settings
from app.domain.actions import lock_business
from app.errors import ApiError
from app.ledger import service as ledger
from app.models import (
    Action,
    Approval,
    Business,
    Counterparty,
    FinancingQuote,
    Invoice,
    InvoiceAllocation,
    Obligation,
    Receipt,
    SandboxEvent,
    Transfer,
)
from app.money import MAX_MINOR, fmt


class EventIn(BaseModel):
    event_id: str = Field(min_length=8, max_length=64)
    type: Literal[
        "transfer.settled",
        "transfer.failed",
        "request.accepted",
        "request.refused",
        "quote.refused",
        "payment.received",
        "balance.declared",
        "payment.sent_externally",
    ]
    business_id: uuid.UUID
    data: dict[str, Any]


def sign(raw: bytes) -> str:
    secret = get_settings().sandbox_event_secret.get_secret_value().encode()
    return "sha256=" + hmac.new(secret, raw, hashlib.sha256).hexdigest()


def verify_signature(raw: bytes, signature: str | None) -> bool:
    return bool(signature) and hmac.compare_digest(sign(raw), signature or "")


def _amount(data: dict[str, Any]) -> int:
    v = data.get("amount_minor")
    if isinstance(v, bool) or not isinstance(v, int) or not 0 < v <= MAX_MINOR:
        raise ApiError(422, "EVENT_INVALID", "amount_minor must be a positive integer within bounds.")
    return v


def _receipt(db: Session, biz: Business, transfer: Transfer, content: dict[str, Any]) -> Receipt:
    n = db.execute(select(func.count(Receipt.id)).where(Receipt.business_id == biz.id)).scalar_one() + 1
    content = {"number": f"R-{n:05d}", **content}
    r = Receipt(
        business_id=biz.id,
        number=content["number"],
        transfer_id=transfer.id,
        content=content,
        digest=sha256_hex(canonical(content)),
    )
    db.add(r)
    db.flush()
    return r


def _on_transfer_settled(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    t = db.execute(
        select(Transfer).where(Transfer.provider_ref == str(data.get("provider_ref", ""))).with_for_update()
    ).scalar_one_or_none()
    if t is None or t.business_id != biz.id or t.direction != "out":
        return "unmatched", "No matching outgoing transfer."
    if t.status == "settled":
        return "already_settled", f"{t.provider_ref} was already settled; nothing changed."
    if t.status != "pending":
        return "ignored_state", f"Transfer is {t.status}."
    amount = _amount(data)
    if amount != t.amount_minor:
        return "amount_mismatch", f"Event amount {amount} != transfer amount {t.amount_minor}; held for review."
    ob = db.execute(select(Obligation).where(Obligation.id == t.obligation_id).with_for_update()).scalar_one()
    when = business_now(biz)
    ledger.post_entry(
        db,
        biz.id,
        effective_at=when,
        memo=f"Settled {ob.description} via {t.provider_ref}",
        source_type="transfer",
        source_id=t.id,
        lines=[(ob.payable_account_code, amount, 0), (ledger.CASH, 0, amount)],
    )
    ob.paid_minor += amount
    t.status = "settled"
    t.settled_at = when
    action = db.get(Action, t.action_id) if t.action_id else None
    approval_id = None
    if action is not None:
        action.status = "settled"
        action.settled_minor = amount
        ap = db.execute(select(Approval.id).where(Approval.action_id == action.id, Approval.consumed_at.is_not(None))).scalar_one_or_none()
        approval_id = str(ap) if ap else None
    cp = db.get(Counterparty, t.counterparty_id)
    receipt = _receipt(
        db,
        biz,
        t,
        {
            "direction": "outgoing",
            "amount_minor": amount,
            "currency": t.currency,
            "amount_display": fmt(amount, t.currency),
            "counterparty": cp.name if cp else None,
            "account_ref": t.account_ref,
            "recipient_version": t.counterparty_version,
            "provider_ref": t.provider_ref,
            "purpose": ob.description,
            "settled_at_business_time": when.isoformat(),
            "action_id": str(action.id) if action else None,
            "approval_id": approval_id,
            "sandbox": True,
        },
    )
    audit.append(
        db,
        biz.id,
        event_type="transfer.settled",
        actor_type="sandbox_bank",
        outcome="settled",
        payload={"provider_ref": t.provider_ref, "amount_minor": amount, "obligation_id": ob.id, "receipt_number": receipt.number},
        action_id=t.action_id,
        case_id=action.case_id if action else None,
        approval_id=approval_id,
        receipt_id=receipt.id,
    )
    return "settled", f"{fmt(amount, t.currency)} settled to {t.account_ref}; receipt {receipt.number}."


def _on_transfer_failed(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    t = db.execute(
        select(Transfer).where(Transfer.provider_ref == str(data.get("provider_ref", ""))).with_for_update()
    ).scalar_one_or_none()
    if t is None or t.business_id != biz.id:
        return "unmatched", "No matching transfer."
    if t.status != "pending":
        return "ignored_state", f"Transfer is already {t.status}; a settled transfer cannot be reversed by a failure event."
    t.status = "failed"
    action = db.get(Action, t.action_id) if t.action_id else None
    if action is not None:
        action.status = "failed"
    audit.append(
        db,
        biz.id,
        event_type="transfer.failed",
        actor_type="sandbox_bank",
        outcome="failed",
        payload={"provider_ref": t.provider_ref, "reason": str(data.get("reason", ""))[:200]},
        action_id=t.action_id,
    )
    return "failed", "Transfer failed; reservation released."


def _on_request_accepted(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    try:
        aid = uuid.UUID(str(data.get("action_id")))
    except ValueError:
        return "unmatched", "Invalid action id."
    a = db.execute(select(Action).where(Action.id == aid, Action.business_id == biz.id).with_for_update()).scalar_one_or_none()
    if a is None or a.type != "payment_request":
        return "unmatched", "No matching payment request."
    if a.status != "sent":
        return "ignored_state", f"Request is {a.status}."
    a.status = "accepted"
    audit.append(
        db,
        biz.id,
        event_type="request.accepted",
        actor_type="sandbox_counterparty",
        outcome="accepted",
        payload={"amount_minor": a.amount_minor, "note": "Acceptance is a promise, not cash. Settled cash unchanged."},
        action_id=a.id,
        case_id=a.case_id,
    )
    return "accepted", "Customer accepted the request (no change to settled cash)."


def _on_request_refused(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    """The customer declines a payment request. No cash effect; the plan's assumption is invalidated."""
    try:
        aid = uuid.UUID(str(data.get("action_id")))
    except ValueError:
        return "unmatched", "Invalid action id."
    a = db.execute(select(Action).where(Action.id == aid, Action.business_id == biz.id).with_for_update()).scalar_one_or_none()
    if a is None or a.type != "payment_request":
        return "unmatched", "No matching payment request."
    if a.status not in ("sent", "accepted") or a.settled_minor > 0:
        return "ignored_state", f"Request is {a.status}."
    a.status = "refused"
    audit.append(
        db,
        biz.id,
        event_type="request.refused",
        actor_type="sandbox_counterparty",
        outcome="refused",
        payload={
            "amount_minor": a.amount_minor,
            "reason": str(data.get("reason", ""))[:200],
            "note": "Refusal has no cash effect. The plan assumption is invalidated; the customer is not re-proposed.",
        },
        action_id=a.id,
        case_id=a.case_id,
    )
    return "refused", "Customer refused the request (no change to settled cash); replanning without this customer."


def _on_quote_refused(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    """The lender withdraws a financing quote. No cash effect; the quote leaves the plan."""
    try:
        qid = uuid.UUID(str(data.get("quote_id")))
    except ValueError:
        return "unmatched", "Invalid quote id."
    q = db.execute(
        select(FinancingQuote).where(FinancingQuote.id == qid, FinancingQuote.business_id == biz.id).with_for_update()
    ).scalar_one_or_none()
    if q is None:
        return "unmatched", "No matching financing quote."
    if q.status != "available":
        return "ignored_state", f"Quote is {q.status}."
    q.status = "refused"
    audit.append(
        db,
        biz.id,
        event_type="quote.refused",
        actor_type="sandbox_counterparty",
        outcome="refused",
        payload={"principal_minor": q.principal_minor, "reason": str(data.get("reason", ""))[:200]},
    )
    return "refused", "Lender withdrew the financing quote (no change to settled cash)."


def _on_payment_received(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    amount = _amount(data)
    provider_ref = str(data.get("provider_ref", ""))[:64]
    if not provider_ref:
        raise ApiError(422, "EVENT_INVALID", "provider_ref is required.")
    if db.execute(select(Transfer.id).where(Transfer.provider_ref == provider_ref)).first():
        return "already_settled", f"Payment {provider_ref} was already recorded."
    try:
        inv_id = uuid.UUID(str(data.get("invoice_id")))
    except ValueError:
        return "unmatched", "Invalid invoice id."
    inv = db.execute(select(Invoice).where(Invoice.id == inv_id, Invoice.business_id == biz.id).with_for_update()).scalar_one_or_none()
    if inv is None:
        return "unmatched", "No matching invoice."
    if amount > inv.outstanding_minor:
        audit.append(
            db,
            biz.id,
            event_type="payment.rejected",
            actor_type="sandbox_counterparty",
            outcome="rejected",
            reasons=[{"code": "OVERPAYMENT_REJECTED", "message": "Payment exceeds invoice outstanding; MVP rejects overpayments."}],
            payload={"invoice": inv.number, "amount_minor": amount, "outstanding_minor": inv.outstanding_minor},
        )
        return "rejected_overpayment", "Payment exceeds invoice outstanding."
    cp = db.get(Counterparty, inv.customer_id)
    assert cp is not None
    when = business_now(biz)
    t = Transfer(
        business_id=biz.id,
        direction="in",
        status="settled",
        invoice_id=inv.id,
        counterparty_id=cp.id,
        counterparty_version=cp.version,
        account_ref=cp.account_ref,
        amount_minor=amount,
        currency=biz.ccy,
        provider_ref=provider_ref,
        settled_at=when,
    )
    db.add(t)
    db.flush()
    db.add(InvoiceAllocation(business_id=biz.id, invoice_id=inv.id, transfer_id=t.id, amount_minor=amount))
    inv.collected_minor += amount
    ledger.post_entry(
        db,
        biz.id,
        effective_at=when,
        memo=f"Customer payment for {inv.number} via {provider_ref}",
        source_type="transfer",
        source_id=t.id,
        lines=[(ledger.CASH, amount, 0), (ledger.AR, 0, amount)],
    )
    action = None
    if data.get("action_id"):
        try:
            action = db.execute(
                select(Action).where(Action.id == uuid.UUID(str(data["action_id"])), Action.business_id == biz.id).with_for_update()
            ).scalar_one_or_none()
        except ValueError:
            action = None
    if action is not None and action.type == "payment_request" and action.status in ("sent", "accepted"):
        action.settled_minor += min(amount, action.amount_minor - action.settled_minor)
        action.status = "settled" if action.settled_minor >= action.amount_minor else "partially_settled"
    receipt = _receipt(
        db,
        biz,
        t,
        {
            "direction": "incoming",
            "amount_minor": amount,
            "currency": biz.ccy,
            "amount_display": fmt(amount, biz.ccy),
            "counterparty": cp.name,
            "account_ref": cp.account_ref,
            "provider_ref": provider_ref,
            "purpose": f"Payment toward invoice {inv.number}",
            "invoice_outstanding_after_minor": inv.outstanding_minor,
            "recorded_by": "owner (not bank-verified)" if data.get("source") == "owner_recorded" else "sandbox bank",
            "settled_at_business_time": when.isoformat(),
            "action_id": str(action.id) if action else None,
            "sandbox": True,
        },
    )
    audit.append(
        db,
        biz.id,
        event_type="payment.received",
        actor_type="sandbox_bank",
        outcome="settled",
        payload={
            "invoice": inv.number,
            "amount_minor": amount,
            "invoice_outstanding_minor": inv.outstanding_minor,
            "request_status": action.status if action else None,
            "receipt_number": receipt.number,
        },
        action_id=action.id if action else None,
        case_id=action.case_id if action else None,
        receipt_id=receipt.id,
    )
    return "settled", f"{fmt(amount, biz.ccy)} received for {inv.number}; outstanding now {fmt(inv.outstanding_minor, biz.ccy)}."


def _on_balance_declared(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    """The owner states the money available in the account today (opening balance or a correction).

    Posts the difference to settled cash against equity, so the ledger shows the declared figure.
    A declared balance is labelled as owner-declared, never as bank-verified.
    """
    v = data.get("amount_minor")
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= MAX_MINOR:
        raise ApiError(422, "EVENT_INVALID", "amount_minor must be a non-negative integer within bounds.")
    if db.execute(select(Transfer.id).where(Transfer.business_id == biz.id, Transfer.status == "pending")).first():
        return "rejected_pending", "A payment is still in progress; declare the balance after it settles."
    current = ledger.settled_cash(db, biz.id)
    delta = v - current
    when = business_now(biz)
    if delta:
        ledger.post_entry(
            db,
            biz.id,
            effective_at=when,
            memo=("Opening balance declared by owner" if biz.cash_declared_at is None else "Balance correction declared by owner"),
            source_type="balance_declaration",
            source_id=str(data.get("declaration_id", ""))[:64] or None,
            lines=[(ledger.CASH, delta, 0), (ledger.EQUITY, 0, delta)] if delta > 0 else [(ledger.EQUITY, -delta, 0), (ledger.CASH, 0, -delta)],
        )
    from datetime import date as _date

    biz.cash_declared_at = when
    try:
        biz.cash_as_of = _date.fromisoformat(str(data.get("as_of")))
    except ValueError:
        biz.cash_as_of = when.date()
    label = str(data.get("account_label") or "").strip()[:100]
    biz.cash_account_label = label or None
    audit.append(
        db,
        biz.id,
        event_type="cash.declared",
        actor_type="user",
        actor_id=data.get("user_id"),
        outcome="declared",
        payload={"amount_minor": v, "previous_minor": current, "as_of": str(biz.cash_as_of), "provenance": "owner_declared"},
    )
    return "declared", f"Available cash recorded as {fmt(v, biz.ccy)} (declared by the owner, not bank-verified)."


def _on_payment_sent_externally(db: Session, biz: Business, data: dict[str, Any]) -> tuple[str, str]:
    """The owner records that a bill was already paid outside FlowSense. No money moves through FlowSense."""
    amount = _amount(data)
    provider_ref = str(data.get("provider_ref", ""))[:64]
    if not provider_ref:
        raise ApiError(422, "EVENT_INVALID", "provider_ref is required.")
    if db.execute(select(Transfer.id).where(Transfer.provider_ref == provider_ref)).first():
        return "already_settled", f"Payment {provider_ref} was already recorded."
    try:
        ob_id = uuid.UUID(str(data.get("obligation_id")))
    except ValueError:
        return "unmatched", "Invalid obligation id."
    ob = db.execute(select(Obligation).where(Obligation.id == ob_id, Obligation.business_id == biz.id).with_for_update()).scalar_one_or_none()
    if ob is None:
        return "unmatched", "No matching obligation."
    reserved = int(
        db.execute(
            select(func.coalesce(func.sum(Transfer.amount_minor), 0)).where(Transfer.obligation_id == ob.id, Transfer.status == "pending")
        ).scalar_one()
    )
    if amount > ob.unpaid_minor - reserved:
        return "rejected_overpayment", "The amount is more than what is still unpaid on this bill."
    cp = db.get(Counterparty, ob.counterparty_id)
    assert cp is not None
    recorded = business_now(biz)
    included = data.get("included_in_declared_balance") is True
    paid_on = _paid_on(data, biz, recorded)
    # Dated on the day it was paid (12:00 local, never later than now); the audit trail keeps the entry time.
    when = min(datetime.combine(paid_on, time(12, 0), ZoneInfo(biz.tz_name)).astimezone(UTC), recorded)
    t = Transfer(
        business_id=biz.id,
        direction="out",
        status="settled",
        obligation_id=ob.id,
        counterparty_id=cp.id,
        counterparty_version=cp.version,
        account_ref=cp.account_ref,
        amount_minor=amount,
        currency=biz.ccy,
        provider_ref=provider_ref,
        settled_at=when,
    )
    db.add(t)
    db.flush()
    if included:
        # Paid before the date of the declared balance: that balance already reflects it. Close the bill
        # against equity (where the declared balance was booked) so cash is not reduced a second time.
        ledger.post_entry(
            db,
            biz.id,
            effective_at=recorded,
            memo=f"{ob.description} paid {paid_on:%d %b %Y}, before the declared balance of {data.get('declared_as_of')}: already in that balance, cash unchanged (recorded by owner)",
            source_type="transfer",
            source_id=t.id,
            lines=[(ob.payable_account_code, amount, 0), (ledger.EQUITY, 0, amount)],
        )
    else:
        ledger.post_entry(
            db,
            biz.id,
            effective_at=when,
            memo=f"{ob.description} paid outside FlowSense on {paid_on:%d %b %Y} (recorded by owner)",
            source_type="transfer",
            source_id=t.id,
            lines=[(ob.payable_account_code, amount, 0), (ledger.CASH, 0, amount)],
        )
    ob.paid_minor += amount
    receipt = _receipt(
        db,
        biz,
        t,
        {
            "direction": "outgoing",
            "amount_minor": amount,
            "currency": biz.ccy,
            "amount_display": fmt(amount, biz.ccy),
            "counterparty": cp.name,
            "account_ref": cp.account_ref,
            "provider_ref": provider_ref,
            "purpose": ob.description,
            "settled_at_business_time": when.isoformat(),
            "paid_on": paid_on.isoformat(),
            "recorded_at": recorded.isoformat(),
            "cash_effect": "none: already included in the declared balance" if included else "cash reduced once",
            "recorded_by": "owner (paid outside FlowSense, not bank-verified)",
            "sandbox": True,
        },
    )
    audit.append(
        db,
        biz.id,
        event_type="payment.recorded_external",
        actor_type="user",
        actor_id=data.get("user_id"),
        outcome="settled",
        payload={"obligation": ob.description, "amount_minor": amount, "receipt_number": receipt.number, "paid_on": paid_on.isoformat(), "included_in_declared_balance": included},
        receipt_id=receipt.id,
    )
    if included:
        return "settled", f"Recorded {fmt(amount, biz.ccy)} paid for {ob.description} on {paid_on:%d %b %Y}. It is already part of the balance you declared, so cash didn't change."
    return "settled", f"Recorded {fmt(amount, biz.ccy)} paid for {ob.description} on {paid_on:%d %b %Y}."


def _paid_on(data: dict[str, Any], biz: Business, recorded: datetime) -> date:
    today = recorded.astimezone(ZoneInfo(biz.tz_name)).date()
    try:
        d = date.fromisoformat(str(data.get("paid_on")))
    except ValueError:
        return today
    return min(d, today)


_HANDLERS = {
    "transfer.settled": _on_transfer_settled,
    "transfer.failed": _on_transfer_failed,
    "request.accepted": _on_request_accepted,
    "request.refused": _on_request_refused,
    "quote.refused": _on_quote_refused,
    "payment.received": _on_payment_received,
    "balance.declared": _on_balance_declared,
    "payment.sent_externally": _on_payment_sent_externally,
}


def ingest(db: Session, raw: bytes, signature: str | None) -> dict[str, Any]:
    """Verify, de-duplicate and apply one event. Commits."""
    from app.planning.rescue import refresh  # local import: rescue depends on actions

    if not verify_signature(raw, signature):
        raise ApiError(401, "EVENT_SIGNATURE_INVALID", "Event signature is missing or invalid.")
    try:
        ev = EventIn.model_validate(json.loads(raw))
    except (ValueError, ValidationError) as exc:
        raise ApiError(422, "EVENT_INVALID", "Malformed sandbox event.") from exc
    if db.get(Business, ev.business_id) is None:
        raise ApiError(404, "NOT_FOUND", "Business not found.")
    biz = lock_business(db, ev.business_id)
    inserted = db.execute(
        insert(SandboxEvent)
        .values(
            id=uuid.uuid4(),
            business_id=biz.id,
            provider_event_id=ev.event_id,
            event_type=ev.type,
            payload=ev.data,
            outcome="processing",
            detail="",
        )
        .on_conflict_do_nothing(index_elements=["provider_event_id"])
        .returning(SandboxEvent.id)
    ).scalar_one_or_none()
    if inserted is None:
        db.rollback()
        return {"event_id": ev.event_id, "outcome": "duplicate", "detail": "Event already processed; ignored."}
    outcome, detail = _HANDLERS[ev.type](db, biz, ev.data)
    row = db.get(SandboxEvent, inserted)
    assert row is not None
    row.outcome, row.detail = outcome, detail[:500]
    if outcome in ("settled", "failed", "accepted", "refused", "declared"):
        refresh(db, biz, trigger=f"{ev.type}: {detail}")
    db.commit()
    return {"event_id": ev.event_id, "outcome": outcome, "detail": detail}
