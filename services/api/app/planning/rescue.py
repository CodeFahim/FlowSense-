"""Rescue cases: detection, planning, replanning and state transitions.

States and legal transitions:
  OPEN                -> AWAITING_ACTION | AT_RISK | INFEASIBLE | RESOLVED
  AT_RISK             -> AWAITING_ACTION | INFEASIBLE | RESOLVED
  AWAITING_ACTION     -> AWAITING_SETTLEMENT | AT_RISK | INFEASIBLE | RESOLVED
  AWAITING_SETTLEMENT -> AWAITING_ACTION | AT_RISK | RESOLVED
  INFEASIBLE          -> AT_RISK | AWAITING_ACTION
  RESOLVED            -> (terminal)

RESOLVED requires the target obligation to be fully *settled* and settled
cash to be at or above the policy reserve. Promises never resolve a case.
Every plan is stored as a new immutable version.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.clock import real_now
from app.domain.actions import OPEN_REQUEST_STATUSES, Actor, cancel_action, create_action, invoice_in_flight
from app.domain.position import Position, active_policy, compute_position
from app.models import (
    Action,
    Business,
    CaseTransition,
    Counterparty,
    Evidence,
    FinancingQuote,
    Invoice,
    Obligation,
    Plan,
    RescueCase,
    Transfer,
)
from app.money import fmt
from app.planning.planner import InvoiceIn, PlannerInput, QuoteIn, SupplierIn, plan

TRANSITIONS: dict[str | None, set[str]] = {
    None: {"OPEN"},
    "OPEN": {"AWAITING_ACTION", "AT_RISK", "INFEASIBLE", "RESOLVED"},
    "AT_RISK": {"AWAITING_ACTION", "INFEASIBLE", "RESOLVED"},
    "AWAITING_ACTION": {"AWAITING_SETTLEMENT", "AT_RISK", "INFEASIBLE", "RESOLVED"},
    "AWAITING_SETTLEMENT": {"AWAITING_ACTION", "AT_RISK", "RESOLVED"},
    "INFEASIBLE": {"AT_RISK", "AWAITING_ACTION"},
    "RESOLVED": set(),
}
PLANNER = Actor(user_id=None, role="staff", actor_type="planner")


class IllegalTransition(RuntimeError):
    pass


def transition(db: Session, case: RescueCase, to: str, reason: str) -> None:
    if case.status == to:
        return
    if to not in TRANSITIONS.get(case.status, set()):
        raise IllegalTransition(f"{case.status} -> {to} is not allowed")
    db.add(CaseTransition(case_id=case.id, from_status=case.status, to_status=to, reason=reason[:500]))
    audit.append(
        db,
        case.business_id,
        event_type="case.transition",
        actor_type="planner",
        payload={"from": case.status, "to": to, "reason": reason[:300]},
        case_id=case.id,
    )
    case.status = to
    if to == "RESOLVED":
        case.resolved_at = real_now()
    db.flush()


def current_case(db: Session, business_id: uuid.UUID) -> RescueCase | None:
    return db.execute(
        select(RescueCase).where(RescueCase.business_id == business_id).order_by(RescueCase.opened_at.desc()).limit(1)
    ).scalar_one_or_none()


def latest_plan(db: Session, case_id: uuid.UUID) -> Plan | None:
    return db.execute(select(Plan).where(Plan.case_id == case_id).order_by(Plan.version.desc()).limit(1)).scalar_one_or_none()


def planner_input(db: Session, biz: Business, pos: Position) -> PlannerInput:
    policy = active_policy(db, biz.id)
    # A customer who refused a request is an invalidated assumption: never re-proposed.
    refused_customers = set(
        db.execute(
            select(Action.counterparty_id).where(Action.business_id == biz.id, Action.type == "payment_request", Action.status == "refused")
        ).scalars()
    )
    invoices = []
    for inv in db.execute(
        select(Invoice).where(Invoice.business_id == biz.id, Invoice.collected_minor < Invoice.amount_minor).order_by(Invoice.due_on)
    ).scalars():
        cp = db.get(Counterparty, inv.customer_id)
        assert cp is not None
        ev = db.execute(select(Evidence).where(Evidence.invoice_id == inv.id).order_by(Evidence.label)).scalars()
        invoices.append(
            InvoiceIn(
                id=str(inv.id),
                number=inv.number,
                customer_id=str(cp.id),
                customer_name=cp.name,
                requestable_minor=inv.outstanding_minor - invoice_in_flight(db, inv.id, None),
                accepts_partial=cp.accepts_partial,
                days_to_pay=cp.typical_days_to_pay,
                disputed=inv.disputed,
                evidence=[{"label": e.label, "provenance": e.provenance, "instruction_like": e.instruction_like} for e in ev],
                refused=cp.id in refused_customers,
            )
        )
    quotes = [
        QuoteIn(
            id=str(q.id),
            lender=(db.get(Counterparty, q.lender_id) or Counterparty(name="?")).name,
            principal_minor=q.principal_minor,
            fee_minor=q.fee_minor,
            interest_minor=q.interest_minor,
            term_days=q.term_days,
            expires_at=q.expires_at,
        )
        for q in db.execute(
            select(FinancingQuote).where(FinancingQuote.business_id == biz.id, FinancingQuote.status == "available")
        ).scalars()
    ]
    suppliers = [
        SupplierIn(id=str(o.id), description=o.description, open_minor=o.unpaid_minor, due_at=o.due_at, essential=o.essential)
        for o in db.execute(
            select(Obligation).where(
                Obligation.business_id == biz.id, Obligation.kind == "supplier", Obligation.paid_minor < Obligation.amount_minor
            )
        ).scalars()
    ]
    return PlannerInput(
        now=pos.as_of,
        gap_minor=pos.gap_minor,
        gap_date=pos.gap_date,
        horizon_end=pos.horizon_end,
        reserve_minor=pos.reserve_minor,
        cost_cap_bp=int(policy.spec.get("max_financing_annualized_bp", 0)) if policy else 0,
        currency=biz.ccy,
        invoices=invoices,
        quotes=quotes,
        suppliers=suppliers,
    )


def _target_payment_action(db: Session, case: RescueCase) -> Action | None:
    return db.execute(
        select(Action)
        .where(
            Action.case_id == case.id,
            Action.obligation_id == case.target_obligation_id,
            Action.status.in_(("proposed", "approved", "submitted", "settled")),
        )
        .order_by(Action.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def replan(db: Session, biz: Business, case: RescueCase, trigger: str) -> Plan:
    pos = compute_position(db, biz)
    body = plan(planner_input(db, biz, pos))
    body["position"] = pos.to_dict()
    version = (db.execute(select(func.max(Plan.version)).where(Plan.case_id == case.id)).scalar_one() or 0) + 1
    p = Plan(
        business_id=biz.id,
        case_id=case.id,
        version=version,
        trigger=trigger[:200],
        gap_minor=pos.gap_minor,
        feasible=bool(body["feasible"]),
        body=body,
    )
    db.add(p)
    db.flush()

    # Supersede proposed (unapproved) requests from earlier plans.
    for old in (
        db.execute(select(Action).where(Action.case_id == case.id, Action.type == "payment_request", Action.status == "proposed"))
        .scalars()
        .all()
    ):
        cancel_action(db, biz.id, PLANNER, old.id, f"Superseded by plan v{version}")

    created: list[str] = []
    for step in body.get("steps", []):
        if step["kind"] == "partial_collection":
            a, _ = create_action(
                db,
                biz.id,
                PLANNER,
                type="payment_request",
                amount_minor=step["amount_minor"],
                counterparty_id=uuid.UUID(step["counterparty_id"]),
                invoice_id=uuid.UUID(step["invoice_id"]),
                purpose=f"Partial payment request toward invoice ({fmt(step['amount_minor'], biz.ccy)}) to close the payroll gap",
                source="planner",
                case_id=case.id,
                plan_id=p.id,
                lock=False,
            )
            created.append(str(a.id))

    target = db.get(Obligation, case.target_obligation_id)
    assert target is not None
    if target.paid_minor < target.amount_minor and _target_payment_action(db, case) is None:
        a, _ = create_action(
            db,
            biz.id,
            PLANNER,
            type="payroll_payment" if target.kind == "payroll" else "supplier_payment",
            amount_minor=target.unpaid_minor,
            counterparty_id=target.counterparty_id,
            obligation_id=target.id,
            purpose=target.description,
            source="planner",
            case_id=case.id,
            plan_id=p.id,
            lock=False,
        )
        created.append(str(a.id))
    body["created_action_ids"] = created
    audit.append(
        db,
        biz.id,
        event_type="plan.created",
        actor_type="planner",
        outcome="feasible" if p.feasible else "infeasible",
        payload={
            "version": version,
            "gap_minor": pos.gap_minor,
            "summary": body["summary"],
            "trigger": trigger[:200],
            "rejected": [r["code"] for r in body["rejected"]],
        },
        case_id=case.id,
        policy_version=pos.policy_version,
    )
    db.flush()
    return p


def open_case(db: Session, biz: Business, pos: Position, trigger: str) -> RescueCase | None:
    target_item = next((i for i in pos.protected if i.due_at == pos.gap_date), pos.protected[0] if pos.protected else None)
    if target_item is None:
        return None
    case = RescueCase(business_id=biz.id, target_obligation_id=target_item.obligation_id, status="OPEN")
    db.add(case)
    db.flush()
    db.add(CaseTransition(case_id=case.id, from_status=None, to_status="OPEN", reason=trigger[:500]))
    audit.append(
        db,
        biz.id,
        event_type="case.opened",
        actor_type="planner",
        outcome="OPEN",
        payload={"gap_minor": pos.gap_minor, "target": target_item.description, "gap_date": pos.gap_date},
        case_id=case.id,
        policy_version=pos.policy_version,
    )
    p = replan(db, biz, case, trigger)
    transition(db, case, "AWAITING_ACTION" if p.feasible else "INFEASIBLE", p.body["summary"])
    return case


def refresh(db: Session, biz: Business, trigger: str) -> RescueCase | None:
    """Re-evaluate the current case after any state change. Caller holds the business lock."""
    if biz.cash_declared_at is None:
        return current_case(db, biz.id)  # cash not entered: never analyse as if it were zero
    pos = compute_position(db, biz)
    case = current_case(db, biz.id)
    if case is None or case.status == "RESOLVED":
        if pos.gap_minor > 0 and active_policy(db, biz.id) is not None:
            return open_case(db, biz, pos, trigger)
        return case
    target = db.get(Obligation, case.target_obligation_id)
    assert target is not None
    pending_target = db.execute(select(Transfer.id).where(Transfer.obligation_id == target.id, Transfer.status == "pending")).first()
    if target.paid_minor >= target.amount_minor and pos.settled_minor >= pos.reserve_minor:
        transition(db, case, "RESOLVED", f"{target.description} settled; settled cash {fmt(pos.settled_minor, biz.ccy)} meets the reserve.")
    elif pending_target:
        transition(db, case, "AWAITING_SETTLEMENT", "Target payment submitted; awaiting bank settlement.")
    elif pos.gap_minor == 0:
        transition(db, case, "AWAITING_ACTION", "Gap closed by settled funds; target payment awaits owner approval.")
    else:
        open_reqs = (
            db.execute(
                select(Action).where(Action.case_id == case.id, Action.type == "payment_request", Action.status.in_(OPEN_REQUEST_STATUSES))
            )
            .scalars()
            .all()
        )
        in_flight = sum(a.amount_minor - a.settled_minor for a in open_reqs)
        if in_flight >= pos.gap_minor:
            sent = any(a.status in ("sent", "accepted") for a in open_reqs)
            transition(
                db,
                case,
                "AWAITING_SETTLEMENT" if sent else "AWAITING_ACTION",
                f"{fmt(in_flight, biz.ccy)} of requested collections in flight cover the {fmt(pos.gap_minor, biz.ccy)} gap.",
            )
        else:
            proposed = db.execute(
                select(func.coalesce(func.sum(Action.amount_minor), 0)).where(
                    Action.case_id == case.id, Action.type == "payment_request", Action.status == "proposed"
                )
            ).scalar_one()
            if in_flight + int(proposed) >= pos.gap_minor and case.status in ("AWAITING_ACTION", "OPEN"):
                return case  # plan already covers the gap; waiting on owner approval
            transition(db, case, "AT_RISK", f"Gap of {fmt(pos.gap_minor, biz.ccy)} is not covered by in-flight actions ({trigger}).")
            p = replan(db, biz, case, trigger)
            transition(db, case, "AWAITING_ACTION" if p.feasible else "INFEASIBLE", p.body["summary"])
    return case


def case_view(db: Session, case: RescueCase) -> dict[str, Any]:
    plans = db.execute(select(Plan).where(Plan.case_id == case.id).order_by(Plan.version.desc())).scalars().all()
    transitions = (
        db.execute(select(CaseTransition).where(CaseTransition.case_id == case.id).order_by(CaseTransition.created_at)).scalars().all()
    )
    target = db.get(Obligation, case.target_obligation_id)
    return {
        "id": str(case.id),
        "status": case.status,
        "opened_at": case.opened_at.isoformat(),
        "resolved_at": case.resolved_at.isoformat() if case.resolved_at else None,
        "target": {
            "id": str(target.id),
            "description": target.description,
            "amount_minor": target.amount_minor,
            "paid_minor": target.paid_minor,
            "due_at": target.due_at.isoformat(),
        }
        if target
        else None,
        "plans": [
            {
                "id": str(p.id),
                "version": p.version,
                "trigger": p.trigger,
                "gap_minor": p.gap_minor,
                "feasible": p.feasible,
                "created_at": p.created_at.isoformat(),
                "body": p.body,
            }
            for p in plans
        ],
        "transitions": [
            {"from": t.from_status, "to": t.to_status, "reason": t.reason, "at": t.created_at.isoformat()} for t in transitions
        ],
    }
