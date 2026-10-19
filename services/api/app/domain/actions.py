"""Actions: propose -> approve -> execute (submit) -> settle.

Lock ordering for every mutating money operation (prevents deadlocks):
  1. businesses row (FOR UPDATE)  -- serializes all money operations per business
  2. actions row
  3. approvals row
  4. audit_heads row (inside audit.append)
Callers never take a later lock and then an earlier one.

Execution consumes the approval and creates the pending transfer (the
reservation) in one transaction, so concurrent submissions with different
idempotency keys still produce at most one transfer.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.audit.chain import canonical, sha256_hex
from app.clock import real_now
from app.config import get_settings
from app.domain.position import active_policy, compute_position, reserved_for_obligation
from app.errors import ApiError, not_found
from app.models import (
    Action,
    Approval,
    Business,
    Counterparty,
    IdempotencyRecord,
    Invoice,
    Membership,
    Obligation,
    Transfer,
)
from app.money import MoneyError, check_amount
from app.safety.kernel import ACTION_TYPES, PAYMENT_TYPES, Decision, KernelInput, Outcome, Phase, evaluate

OPEN_REQUEST_STATUSES = ("approved", "sent", "accepted")
LIVE_STATUSES = ("proposed", "approved", "submitted", "sent", "accepted")


@dataclass
class Actor:
    user_id: uuid.UUID | None
    role: str | None
    actor_type: str = "user"  # user|assistant|planner|demo_setup


def lock_business(db: Session, business_id: uuid.UUID) -> Business:
    return db.execute(
        select(Business).where(Business.id == business_id).with_for_update().execution_options(populate_existing=True)
    ).scalar_one()


def lock_action(db: Session, business_id: uuid.UUID, action_id: uuid.UUID) -> Action:
    a = db.execute(
        select(Action)
        .where(Action.id == action_id, Action.business_id == business_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one_or_none()
    if a is None:
        raise not_found("Action")
    return a


def payload_digest(a: Action) -> str:
    return sha256_hex(
        canonical(
            {
                "business_id": str(a.business_id),
                "action_id": str(a.id),
                "revision": a.revision,
                "type": a.type,
                "amount_minor": a.amount_minor,
                "currency": a.currency,
                "counterparty_id": str(a.counterparty_id),
                "counterparty_version": a.counterparty_version,
                "invoice_id": str(a.invoice_id) if a.invoice_id else None,
                "obligation_id": str(a.obligation_id) if a.obligation_id else None,
                "quote_id": str(a.quote_id) if a.quote_id else None,
                "purpose": a.purpose,
            }
        )
    )


def invoice_in_flight(db: Session, invoice_id: uuid.UUID, exclude_action: uuid.UUID | None) -> int:
    q = select(func.coalesce(func.sum(Action.amount_minor - Action.settled_minor), 0)).where(
        Action.invoice_id == invoice_id, Action.type == "payment_request", Action.status.in_(OPEN_REQUEST_STATUSES)
    )
    if exclude_action is not None:
        q = q.where(Action.id != exclude_action)
    return int(db.execute(q).scalar_one())


def latest_approval(db: Session, action: Action, *, lock: bool = False) -> Approval | None:
    q = select(Approval).where(Approval.action_id == action.id).order_by(Approval.created_at.desc()).limit(1)
    if lock:
        q = q.with_for_update()
    return db.execute(q).scalar_one_or_none()


def _is_owner(db: Session, business_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return (
        db.execute(
            select(Membership.id).where(
                Membership.business_id == business_id,
                Membership.user_id == user_id,
                Membership.role == "owner",
                Membership.active.is_(True),
            )
        ).first()
        is not None
    )


def evaluate_action(db: Session, biz: Business, actor: Actor, action: Action, phase: Phase, approval: Approval | None = None) -> Decision:
    policy = active_policy(db, biz.id)
    position = compute_position(db, biz, policy)
    cp = db.get(Counterparty, action.counterparty_id)
    ob = db.get(Obligation, action.obligation_id) if action.obligation_id else None
    inv = db.get(Invoice, action.invoice_id) if action.invoice_id else None
    inp = KernelInput(
        phase=phase,
        actor_role=actor.role,
        business=biz,
        policy=policy,
        action=action,
        counterparty=cp,
        obligation=ob,
        obligation_reserved_minor=reserved_for_obligation(db, ob.id) if ob else 0,
        invoice=inv,
        invoice_in_flight_minor=invoice_in_flight(db, inv.id, action.id) if inv else 0,
        position=position,
        approval=approval,
        approval_owner_is_owner=bool(approval and _is_owner(db, biz.id, approval.owner_id)),
        current_digest=payload_digest(action),
        now_real=real_now(),
    )
    return evaluate(inp)


def _audit_decision(db: Session, biz: Business, actor: Actor, action: Action, decision: Decision, event: str, **kw: Any) -> None:
    audit.append(
        db,
        biz.id,
        event_type=event,
        actor_type=actor.actor_type,
        actor_id=actor.user_id,
        outcome=decision.outcome.value,
        reasons=decision.reasons,
        payload={
            "action_type": action.type,
            "amount_minor": action.amount_minor,
            "currency": action.currency,
            "counterparty_id": action.counterparty_id,
            "counterparty_version": action.counterparty_version,
            "revision": action.revision,
            "warnings": [w["code"] for w in decision.warnings],
            **kw.pop("payload", {}),
        },
        action_id=action.id,
        case_id=action.case_id,
        policy_version=_policy_version(db, biz.id),
        **kw,
    )


def _policy_version(db: Session, business_id: uuid.UUID) -> int | None:
    p = active_policy(db, business_id)
    return p.version if p else None


# ------------------------------------------------------------------ propose


def create_action(
    db: Session,
    biz_id: uuid.UUID,
    actor: Actor,
    *,
    type: str,
    amount_minor: int,
    counterparty_id: uuid.UUID,
    purpose: str,
    source: str,
    obligation_id: uuid.UUID | None = None,
    invoice_id: uuid.UUID | None = None,
    case_id: uuid.UUID | None = None,
    plan_id: uuid.UUID | None = None,
    lock: bool = True,
) -> tuple[Action, Decision]:
    biz = lock_business(db, biz_id) if lock else db.get(Business, biz_id)
    assert biz is not None
    if type not in ACTION_TYPES:
        raise ApiError(422, "ACTION_TYPE_UNSUPPORTED", f"Unsupported action type {type!r}.")
    try:
        check_amount(amount_minor)
    except MoneyError as exc:
        raise ApiError(422, "AMOUNT_INVALID", str(exc)) from exc
    cp = db.get(Counterparty, counterparty_id)
    if cp is None or cp.business_id != biz.id:
        raise not_found("Counterparty")
    for model, ref in ((Obligation, obligation_id), (Invoice, invoice_id)):
        if ref is not None:
            row = db.get(model, ref)
            if row is None or getattr(row, "business_id", None) != biz.id:
                raise not_found(model.__name__)
    action = Action(
        business_id=biz.id,
        case_id=case_id,
        plan_id=plan_id,
        type=type,
        status="proposed",
        revision=1,
        amount_minor=amount_minor,
        currency=biz.ccy,
        counterparty_id=cp.id,
        counterparty_version=cp.version,
        invoice_id=invoice_id,
        obligation_id=obligation_id,
        purpose=purpose[:500],
        source=source,
        created_by=actor.user_id,
    )
    db.add(action)
    db.flush()
    decision = evaluate_action(db, biz, actor, action, Phase.PROPOSE)
    action.last_decision = decision.to_dict()
    if decision.outcome == Outcome.DENY:
        action.status = "denied"
        _audit_decision(db, biz, actor, action, decision, "action.proposal_denied", payload={"source": source})
    else:
        _audit_decision(db, biz, actor, action, decision, "action.proposed", payload={"source": source})
    db.flush()
    return action, decision


def revise_amount(db: Session, biz_id: uuid.UUID, actor: Actor, action_id: uuid.UUID, amount_minor: int) -> Action:
    biz = lock_business(db, biz_id)
    a = lock_action(db, biz.id, action_id)
    if a.status not in ("proposed", "approved"):
        raise ApiError(409, "ACTION_STATE_INVALID", f"Cannot revise an action that is '{a.status}'.")
    try:
        check_amount(amount_minor)
    except MoneyError as exc:
        raise ApiError(422, "AMOUNT_INVALID", str(exc)) from exc
    old = a.amount_minor
    a.amount_minor = amount_minor
    a.revision += 1
    a.status = "proposed"
    revoked = revoke_open_approvals(db, a, "Action amount changed")
    audit.append(
        db,
        biz.id,
        event_type="action.revised",
        actor_type=actor.actor_type,
        actor_id=actor.user_id,
        payload={"old_amount_minor": old, "new_amount_minor": amount_minor, "revision": a.revision, "revoked_approvals": revoked},
        action_id=a.id,
        case_id=a.case_id,
    )
    db.flush()
    return a


def revoke_open_approvals(db: Session, action: Action, reason: str) -> list[str]:
    rows = db.execute(
        update(Approval)
        .where(Approval.action_id == action.id, Approval.consumed_at.is_(None), Approval.revoked_at.is_(None))
        .values(revoked_at=real_now(), revoke_reason=reason)
        .returning(Approval.id)
    ).scalars()
    return [str(r) for r in rows]


# ------------------------------------------------------------------ approve


def approve_action(db: Session, biz_id: uuid.UUID, actor: Actor, action_id: uuid.UUID) -> tuple[Approval | None, Decision, Action]:
    biz = lock_business(db, biz_id)
    a = lock_action(db, biz.id, action_id)
    decision = evaluate_action(db, biz, actor, a, Phase.APPROVE)
    a.last_decision = decision.to_dict()
    if decision.outcome != Outcome.ALLOW:
        _audit_decision(db, biz, actor, a, decision, "approval.denied")
        db.flush()
        return None, decision, a
    policy = active_policy(db, biz.id)
    assert policy is not None and actor.user_id is not None
    ap = Approval(
        business_id=biz.id,
        owner_id=actor.user_id,
        action_id=a.id,
        action_revision=a.revision,
        action_type=a.type,
        amount_minor=a.amount_minor,
        currency=a.currency,
        counterparty_id=a.counterparty_id,
        counterparty_version=a.counterparty_version,
        policy_version=policy.version,
        financing_terms=None,
        payload_digest=payload_digest(a),
        expires_at=real_now() + timedelta(minutes=get_settings().approval_ttl_minutes),
    )
    db.add(ap)
    a.status = "approved"
    db.flush()
    _audit_decision(
        db,
        biz,
        actor,
        a,
        decision,
        "approval.granted",
        approval_id=ap.id,
        payload={"expires_at": ap.expires_at, "digest": ap.payload_digest},
    )
    db.flush()
    return ap, decision, a


# ------------------------------------------------------------------ execute


@dataclass
class ExecResult:
    status_code: int
    body: dict[str, Any]
    transfer_id: uuid.UUID | None = None
    replayed: bool = False


def _request_digest(action_id: uuid.UUID) -> str:
    return sha256_hex(canonical({"op": "execute_action", "action_id": str(action_id)}))


def execute_action(
    db: Session,
    biz_id: uuid.UUID,
    actor: Actor,
    action_id: uuid.UUID,
    idempotency_key: str,
    serialize: Any,
) -> ExecResult:
    """Submit an approved action. Caller commits. `serialize(action, transfer)` builds the response body."""
    if not idempotency_key or len(idempotency_key) > 128:
        raise ApiError(400, "IDEMPOTENCY_KEY_REQUIRED", "An Idempotency-Key header (1-128 chars) is required.")
    biz = lock_business(db, biz_id)
    digest = _request_digest(action_id)
    prior = db.execute(
        select(IdempotencyRecord).where(
            IdempotencyRecord.business_id == biz.id,
            IdempotencyRecord.operation == "execute_action",
            IdempotencyRecord.key == idempotency_key,
        )
    ).scalar_one_or_none()
    if prior is not None:
        if prior.payload_digest != digest:
            raise ApiError(409, "IDEMPOTENCY_PAYLOAD_MISMATCH", "This idempotency key was already used for a different request.")
        return ExecResult(prior.status_code, prior.response, replayed=True)

    a = lock_action(db, biz.id, action_id)
    ap = latest_approval(db, a, lock=True)
    decision = evaluate_action(db, biz, actor, a, Phase.EXECUTE, ap)
    a.last_decision = decision.to_dict()

    transfer: Transfer | None = None
    if decision.outcome == Outcome.ALLOW:
        assert ap is not None
        consumed = db.execute(
            update(Approval)
            .where(Approval.id == ap.id, Approval.consumed_at.is_(None), Approval.revoked_at.is_(None))
            .values(consumed_at=real_now())
            .returning(Approval.id)
        ).first()
        if consumed is None:  # pragma: no cover - guarded by the row lock above
            raise ApiError(409, "APPROVAL_ALREADY_USED", "Approval was consumed concurrently.")
        cp = db.get(Counterparty, a.counterparty_id)
        assert cp is not None
        if a.type in PAYMENT_TYPES:
            a.status = "submitted"
            transfer = Transfer(
                business_id=biz.id,
                direction="out",
                status="pending",
                action_id=a.id,
                obligation_id=a.obligation_id,
                counterparty_id=cp.id,
                counterparty_version=cp.version,
                account_ref=cp.account_ref,
                amount_minor=a.amount_minor,
                currency=a.currency,
                # Persisted before the simulated bank is called, so retries can check status.
                provider_ref="SBX-TX-" + uuid.uuid4().hex[:20].upper(),
            )
            db.add(transfer)
            db.flush()
            event = "action.submitted"
        else:
            a.status = "sent"
            event = "request.sent_simulated"
        _audit_decision(
            db,
            biz,
            actor,
            a,
            decision,
            event,
            approval_id=ap.id,
            payload={"transfer_id": transfer.id if transfer else None, "provider_ref": transfer.provider_ref if transfer else None},
        )
        status_code = 200
    else:
        _audit_decision(db, biz, actor, a, decision, "execution.denied", approval_id=ap.id if ap else None)
        status_code = 409

    body = serialize(a, transfer, decision)
    db.add(
        IdempotencyRecord(
            business_id=biz.id,
            operation="execute_action",
            key=idempotency_key,
            payload_digest=digest,
            status_code=status_code,
            response=body,
        )
    )
    db.flush()
    return ExecResult(status_code, body, transfer.id if transfer else None)


def cancel_action(db: Session, biz_id: uuid.UUID, actor: Actor, action_id: uuid.UUID, reason: str) -> Action:
    biz = lock_business(db, biz_id)
    a = lock_action(db, biz.id, action_id)
    if a.status not in ("proposed", "approved"):
        raise ApiError(409, "ACTION_STATE_INVALID", f"Cannot cancel an action that is '{a.status}'.")
    a.status = "cancelled"
    revoke_open_approvals(db, a, f"Action cancelled: {reason}")
    audit.append(
        db,
        biz.id,
        event_type="action.cancelled",
        actor_type=actor.actor_type,
        actor_id=actor.user_id,
        payload={"reason": reason},
        action_id=a.id,
        case_id=a.case_id,
    )
    db.flush()
    return a
