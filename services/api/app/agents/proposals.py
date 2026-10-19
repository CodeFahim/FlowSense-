"""AI-suggested actions that run only after the signed-in user confirms them.

The assistant (live or demo) can *propose* a small set of actions. A proposal is stored server-side,
bound to the (user, business) that saw it, validated when it is created and validated again when it
is confirmed, and it expires. Confirming runs exactly the same domain functions as the forms, with
the confirming user as the actor (never the assistant), then reads the result back from the database
to verify it and recomputes the cash position so the next answer uses fresh figures.

What a proposal can never do: approve or send a payment, move money, change a policy or recipient,
verify an account or delete anything. "Request a payment" creates a *proposed* request; the owner
still approves and sends it in Approvals, where the safety kernel checks it again. So any external
message (the payment request to a customer) and any money movement keeps its own explicit approval.
"""

from __future__ import annotations

import uuid
from datetime import date, time, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.clock import real_now
from app.domain import records as rec
from app.domain.actions import Actor, create_action, lock_business
from app.domain.position import compute_position
from app.domain.workspace import is_configured
from app.errors import ApiError, not_found
from app.models import Action, AssistantProposal, Business, Counterparty, Invoice, User
from app.money import fmt
from app.planning import rescue

TTL = timedelta(minutes=30)
MAX_PENDING = 5


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RecordCash(_P):
    amount: str = Field(min_length=1, max_length=32)
    as_of: date | None = None


class AddInvoice(_P):
    customer_name: str = Field(min_length=1, max_length=200)
    number: str = Field(min_length=1, max_length=64)
    amount: str = Field(min_length=1, max_length=32)
    due_on: date
    issued_on: date | None = None
    description: str | None = Field(default=None, max_length=300)


class AddBill(_P):
    payee_name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=300)
    amount: str = Field(min_length=1, max_length=32)
    due_on: date
    essential: bool = False


class AddPayroll(_P):
    description: str = Field(min_length=1, max_length=300)
    amount: str = Field(min_length=1, max_length=32)
    due_on: date
    payee_name: str | None = Field(default=None, max_length=200)


class RequestPayment(_P):
    invoice_number: str = Field(min_length=1, max_length=64)
    amount: str = Field(min_length=1, max_length=32)


Kind = Literal["record_cash", "add_invoice", "add_bill", "add_payroll", "request_payment"]
MODELS: dict[str, type[_P]] = {
    "record_cash": RecordCash,
    "add_invoice": AddInvoice,
    "add_bill": AddBill,
    "add_payroll": AddPayroll,
    "request_payment": RequestPayment,
}
OWNER_ONLY = {"record_cash"}
# Shown on the confirmation card so the user knows what pressing Confirm does and does not do.
EFFECT = {
    "record_cash": "Saves this as your available cash (declared by you, not bank-verified). No money moves.",
    "add_invoice": "Saves this invoice under Money in. It is not counted as cash until it is paid.",
    "add_bill": "Saves this bill under Money out. Nothing is paid.",
    "add_payroll": "Saves this payroll under Money out. Nothing is paid.",
    "request_payment": "Creates a payment request that waits in Approvals. Nothing is sent to the customer until the owner approves and sends it there.",
}


def _date(_biz: Business, d: date) -> str:
    return d.strftime("%a %d %b %Y")


def _find_invoice(db: Session, biz: Business, number: str) -> Invoice | None:
    return db.execute(select(Invoice).where(Invoice.business_id == biz.id, Invoice.number == number.strip())).scalar_one_or_none()


def _find_party(db: Session, biz: Business, name: str, kinds: tuple[str, ...]) -> Counterparty | None:
    return db.execute(
        select(Counterparty).where(Counterparty.business_id == biz.id, Counterparty.kind.in_(kinds), func.lower(Counterparty.name) == name.strip().lower()).limit(1)
    ).scalar_one_or_none()


def _check(db: Session, biz: Business, role: str, kind: str, p: Any) -> str:
    """Validate a proposal against current records without changing anything. Returns its summary."""
    if not is_configured(biz):
        raise ApiError(409, "SETUP_REQUIRED", "Confirm the business currency and timezone first (Settings → Business).")
    if kind in OWNER_ONLY and role != "owner":
        raise ApiError(403, "OWNER_ONLY", "Only the business owner can record available cash.")
    ccy = biz.ccy
    if kind == "record_cash":
        if biz.kind != "workspace":
            raise ApiError(409, "DEMO_CASH_FIXED", "Demo and sample workspaces have fixed sandbox balances.")
        minor = rec.amount_minor(p.amount, ccy, biz, allow_zero=True)
        as_of = p.as_of or rec.business_today(biz)
        if as_of > rec.business_today(biz):
            raise ApiError(422, "DATE_IN_FUTURE", "The balance date can't be in the future.")
        return f"Record available cash of {fmt(minor, ccy)} as of {_date(biz, as_of)}"
    if kind == "add_invoice":
        minor = rec.amount_minor(p.amount, ccy, biz)
        issued = p.issued_on or rec.business_today(biz)
        if p.due_on < issued:
            raise ApiError(422, "DATES_INVALID", "The due date can't be before the issue date.")
        if _find_invoice(db, biz, p.number) is not None:
            raise ApiError(409, "INVOICE_DUPLICATE", f"An invoice numbered {p.number.strip()} already exists.")
        return f"Add invoice {p.number.strip()} to {p.customer_name.strip()} for {fmt(minor, ccy)}, due {_date(biz, p.due_on)}"
    if kind in ("add_bill", "add_payroll"):
        minor = rec.amount_minor(p.amount, ccy, biz)
        what = "payroll" if kind == "add_payroll" else "bill"
        to = p.payee_name.strip() if p.payee_name else "Staff wages"
        return f"Add {what} '{p.description.strip()}' to {to} for {fmt(minor, ccy)}, due {_date(biz, p.due_on)}"
    if kind == "request_payment":
        inv = _find_invoice(db, biz, p.invoice_number)
        if inv is None:
            raise ApiError(404, "NOT_FOUND", f"No invoice {p.invoice_number.strip()} in this business.")
        minor = rec.amount_minor(p.amount, ccy, biz)
        if minor > inv.outstanding_minor:
            raise ApiError(422, "AMOUNT_TOO_HIGH", f"Invoice {inv.number} has only {fmt(inv.outstanding_minor, ccy)} outstanding.")
        busy = db.execute(select(Action.id).where(Action.invoice_id == inv.id, Action.type == "payment_request", Action.status.in_(("proposed", "approved", "sent", "accepted", "partially_settled"))).limit(1)).first()
        if busy is not None:
            raise ApiError(409, "REQUEST_EXISTS", f"Invoice {inv.number} already has an open payment request. Review it in Approvals.")
        cp = db.get(Counterparty, inv.customer_id)
        return f"Prepare a payment request to {cp.name if cp else 'the customer'} for {fmt(minor, ccy)} toward invoice {inv.number}"
    raise ApiError(422, "PROPOSAL_KIND", "Unsupported action.")


def parse(kind: str, params: dict[str, Any]) -> Any:
    model = MODELS.get(kind)
    if model is None:
        raise ApiError(422, "PROPOSAL_KIND", f"Unsupported action {kind!r}.")
    try:
        return model.model_validate(params)
    except ValidationError as exc:
        raise ApiError(422, "PROPOSAL_INVALID", "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())[:400]) from exc


def view(p: AssistantProposal) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "kind": p.kind,
        "summary": p.summary,
        "effect": EFFECT.get(p.kind, ""),
        "status": "expired" if p.status == "pending" and p.expires_at <= real_now() else p.status,
        "expires_at": p.expires_at.isoformat(),
        "result": p.result,
    }


def propose(db: Session, biz: Business, user: User, role: str, kind: str, params: dict[str, Any], *, source: str) -> dict[str, Any]:
    """Validate and store a proposal. Nothing else changes."""
    p = parse(kind, params)
    summary = _check(db, biz, role, kind, p)
    pending = db.execute(
        select(func.count(AssistantProposal.id)).where(
            AssistantProposal.user_id == user.id, AssistantProposal.business_id == biz.id, AssistantProposal.status == "pending", AssistantProposal.expires_at > real_now()
        )
    ).scalar_one()
    if pending >= MAX_PENDING:
        raise ApiError(429, "TOO_MANY_PROPOSALS", "There are already several suggestions waiting. Confirm or dismiss them first.")
    row = AssistantProposal(
        business_id=biz.id,
        user_id=user.id,
        kind=kind,
        params=p.model_dump(mode="json", exclude_none=True),
        summary=summary[:400],
        expires_at=real_now() + TTL,
    )
    db.add(row)
    db.flush()
    audit.append(db, biz.id, event_type="assistant.proposal_created", actor_type="assistant", actor_id=user.id, payload={"kind": kind, "source": source, "proposal": str(row.id)})
    return view(row)


def _get(db: Session, biz: Business, user: User, proposal_id: str) -> AssistantProposal:
    try:
        pid = uuid.UUID(proposal_id)
    except ValueError:
        raise not_found("Suggestion") from None
    row = db.execute(select(AssistantProposal).where(AssistantProposal.id == pid).with_for_update()).scalar_one_or_none()
    # Another user's or another workspace's proposal looks exactly like a missing one.
    if row is None or row.business_id != biz.id or row.user_id != user.id:
        raise not_found("Suggestion")
    return row


def _usable(row: AssistantProposal) -> None:
    if row.status != "pending":
        raise ApiError(409, "PROPOSAL_DONE", f"This suggestion was already {row.status}.")
    if row.expires_at <= real_now():
        row.status = "expired"
        raise ApiError(409, "PROPOSAL_EXPIRED", "This suggestion expired. Ask the assistant again so it uses your latest records.")


def _execute(db: Session, biz: Business, user: User, role: str, row: AssistantProposal) -> dict[str, Any]:
    """Run the confirmed action through the normal domain path and verify it by reading it back."""
    p = parse(row.kind, row.params)
    ccy = biz.ccy
    src = "assistant_confirmed"
    if row.kind == "record_cash":
        out = rec.declare_cash(db, biz, user.id, amount=p.amount, currency=ccy, as_of=p.as_of or rec.business_today(biz), account_label="Recorded via the assistant")
        if out.get("outcome") != "declared":
            raise ApiError(409, "CASH_NOT_RECORDED", out.get("detail") or "The cash figure was not recorded.")
        fresh = db.get(Business, biz.id, populate_existing=True)
        ok = fresh is not None and fresh.cash_declared_at is not None
        return {"verified": ok, "record": "cash", "message": "Available cash recorded." if ok else "The cash figure could not be confirmed."}
    if row.kind == "add_invoice":
        cp = _find_party(db, biz, p.customer_name, ("customer",))
        inv = rec.create_invoice(
            db, biz, user.id, customer_id=cp.id if cp else None, new_customer=None if cp else p.customer_name, number=p.number,
            description=p.description or "", amount=p.amount, currency=ccy, issued_on=p.issued_on or rec.business_today(biz), due_on=p.due_on, source=src,
        )
        rescue.refresh(db, biz, f"Invoice {inv.number} added (assistant suggestion confirmed)")
        db.flush()
        saved = db.get(Invoice, inv.id)
        ok = saved is not None and saved.amount_minor == rec.amount_minor(p.amount, ccy, biz)
        return {"verified": ok, "record": "invoice", "id": str(inv.id), "href": f"/money-in?invoice={inv.id}", "message": f"Invoice {inv.number} saved for {fmt(inv.amount_minor, ccy)}."}
    if row.kind in ("add_bill", "add_payroll"):
        kind = "payroll" if row.kind == "add_payroll" else "supplier"
        name = (p.payee_name or "").strip() or ("Staff wages" if kind == "payroll" else "")
        cp = _find_party(db, biz, name, ("payroll",) if kind == "payroll" else ("supplier",))
        obs = rec.create_obligation(
            db, biz, user.id, kind=kind, payee_id=cp.id if cp else None, new_payee=None if cp else name, description=p.description,
            amount=p.amount, currency=ccy, due_on=p.due_on, due_time=time(17, 0), essential=kind == "payroll" or bool(getattr(p, "essential", False)), source=src,
        )
        rescue.refresh(db, biz, f"{obs[0].description} added (assistant suggestion confirmed)")
        db.flush()
        ok = all(o.amount_minor == rec.amount_minor(p.amount, ccy, biz) for o in obs)
        return {"verified": ok, "record": kind, "id": str(obs[0].id), "href": "/money-out", "message": f"'{obs[0].description}' saved for {fmt(obs[0].amount_minor, ccy)}. Nothing was paid."}
    if row.kind == "request_payment":
        target = _find_invoice(db, biz, p.invoice_number)
        if target is None:
            raise not_found("Invoice")
        minor = rec.amount_minor(p.amount, ccy, biz)
        # Not attached to the rescue case: a replan supersedes only the planner's own unapproved requests.
        a, d = create_action(
            db, biz.id, Actor(user.id, role, "user"), type="payment_request", amount_minor=minor, counterparty_id=target.customer_id,
            invoice_id=target.id, purpose=f"Payment request toward invoice {target.number} (suggested by the assistant, confirmed by the user)",
            source=src, lock=False,
        )
        act = db.get(Action, a.id)
        if act is None or act.status == "denied":
            reasons = "; ".join(str(r.get("message", "")) for r in d.reasons)
            return {"verified": True, "record": "payment_request", "id": str(a.id), "href": "/approvals", "blocked": True,
                    "message": f"The safety check refused this request: {reasons or 'see Activity for details'}. Nothing was sent."}
        return {"verified": act.status == "proposed", "record": "payment_request", "id": str(a.id), "href": "/approvals",
                "message": f"Payment request for {fmt(minor, ccy)} is waiting in Approvals. It has not been sent; the owner must approve and send it."}
    raise ApiError(422, "PROPOSAL_KIND", "Unsupported action.")


def _position_note(db: Session, biz: Business) -> dict[str, Any] | None:
    if not is_configured(biz):
        return None
    pos = compute_position(db, biz)
    if pos.cash_status != "declared":
        return {"cash_status": pos.cash_status, "text": "Available cash is not entered yet, so the shortfall can't be calculated."}
    gap = pos.gap_minor if pos.policy_version is not None else None
    if gap is None:
        text = "Confirm your safety reserve to see whether you have a shortfall."
    elif gap > 0:
        text = f"Updated position: you are short {fmt(gap, biz.ccy)} for essential payments in the protection window."
    else:
        text = "Updated position: essential payments in the protection window are covered by settled cash."
    return {"cash_status": pos.cash_status, "available": fmt(pos.available_minor, biz.ccy), "shortfall": fmt(gap, biz.ccy) if gap is not None else None, "text": text}


def confirm(db: Session, biz_id: uuid.UUID, user: User, role: str, proposal_id: str) -> dict[str, Any]:
    biz = lock_business(db, biz_id)
    row = _get(db, biz, user, proposal_id)
    try:
        _usable(row)
        p = parse(row.kind, row.params)
        _check(db, biz, role, row.kind, p)  # records may have changed since the suggestion
        result = _execute(db, biz, user, role, row)
    except ApiError as exc:
        db.rollback()
        biz = lock_business(db, biz_id)
        row = _get(db, biz, user, proposal_id)
        if row.status == "pending":
            row.status = "expired" if exc.code == "PROPOSAL_EXPIRED" else "failed"
            row.result = {"verified": False, "error": exc.code, "message": exc.message}
            row.decided_at = real_now()
            audit.append(db, biz.id, event_type="assistant.proposal_failed", actor_type="user", actor_id=user.id, outcome=exc.code, payload={"kind": row.kind, "proposal": str(row.id)})
        db.commit()
        raise
    row = _get(db, lock_business(db, biz_id), user, proposal_id)
    row.status = "done"
    row.decided_at = real_now()
    fresh_biz = db.get(Business, biz_id)
    assert fresh_biz is not None
    result["position"] = _position_note(db, fresh_biz)
    row.result = result
    audit.append(db, biz_id, event_type="assistant.proposal_confirmed", actor_type="user", actor_id=user.id, outcome="verified" if result.get("verified") else "unverified", payload={"kind": row.kind, "proposal": str(row.id)})
    db.commit()
    return view(row)


def dismiss(db: Session, biz_id: uuid.UUID, user: User, proposal_id: str) -> dict[str, Any]:
    biz = lock_business(db, biz_id)
    row = _get(db, biz, user, proposal_id)
    if row.status == "pending":
        row.status = "cancelled"
        row.decided_at = real_now()
        audit.append(db, biz.id, event_type="assistant.proposal_dismissed", actor_type="user", actor_id=user.id, payload={"kind": row.kind, "proposal": str(row.id)})
    db.commit()
    return view(row)


def pending(db: Session, biz: Business, user: User) -> list[dict[str, Any]]:
    rows = db.execute(
        select(AssistantProposal)
        .where(AssistantProposal.user_id == user.id, AssistantProposal.business_id == biz.id, AssistantProposal.status == "pending", AssistantProposal.expires_at > real_now())
        .order_by(AssistantProposal.created_at)
    ).scalars().all()
    return [view(r) for r in rows]
