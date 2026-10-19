"""Plain-English overview helpers for the dashboard.

Everything here is derived deterministically from backend figures (position,
case status and actions). Nothing is invented: if the state does not support a
sentence, it is not produced.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain.position import Position
from app.models import Action, Business, Counterparty, Invoice, RescueCase
from app.money import fmt

_TYPE_LABEL = {"payment_request": "payment request", "payroll_payment": "payroll payment", "supplier_payment": "supplier payment"}


def _when(biz: Business, dt: Any) -> str:
    from zoneinfo import ZoneInfo

    return f"{dt.astimezone(ZoneInfo(biz.tz_name)):%a %d %b %H:%M} ({biz.tz_name})"


def headline(biz: Business, pos: Position, case: RescueCase | None, waiting: int) -> str:
    cur = biz.ccy
    if pos.cash_status != "declared":
        return "Add your available cash so FlowSense can check whether upcoming payments are covered."
    if pos.policy_version is None:
        return "Confirm the cash reserve you want to keep, so FlowSense can analyse your upcoming payments."
    if pos.gap_minor > 0:
        target = next((p for p in pos.protected if p.due_at == pos.gap_date), pos.protected[0] if pos.protected else None)
        what = f"{target.description} due {_when(biz, target.due_at)}" if target else "essential payments"
        text = f"You need an additional {fmt(pos.gap_minor, cur)} to cover {what} while keeping your {fmt(pos.reserve_minor, cur)} reserve."
        if case is not None and case.status == "AWAITING_SETTLEMENT":
            text += " Money already requested has not arrived yet."
        elif case is not None and case.status == "INFEASIBLE":
            text += " No option allowed by your policy covers it."
        return text
    if case is not None and case.status == "RESOLVED":
        return f"The rescue is complete: the target payment is settled and your {fmt(pos.reserve_minor, cur)} reserve is intact."
    text = f"Settled cash covers essential payments due by {_when(biz, pos.horizon_end)} and your {fmt(pos.reserve_minor, cur)} reserve."
    if waiting:
        text += f" {waiting} action{'s' if waiting != 1 else ''} still need{'s' if waiting == 1 else ''} your attention in Approvals."
    return text


def next_action(db: Session, biz: Business, pos: Position, case: RescueCase | None, plan_summary: str | None) -> dict[str, Any]:
    cur = biz.ccy
    acts = (
        db.execute(
            select(Action)
            .where(Action.business_id == biz.id, Action.status.in_(("proposed", "approved")))
            .order_by(Action.created_at)
        )
        .scalars()
        .all()
    )
    # Collections come first: payroll usually depends on them.
    acts = sorted(acts, key=lambda a: (a.type != "payment_request", a.created_at))
    if case is not None and case.status == "INFEASIBLE":
        return {
            "kind": "infeasible",
            "title": "No safe plan covers the shortfall",
            "why": plan_summary or "Every available option breaks a policy rule or arrives too late.",
            "outcome": "Review the options and the remaining gap. Nothing will be paid automatically.",
            "amount_minor": pos.gap_minor,
            "requires_approval": False,
            "href": "/planner",
            "cta": "Open Rescue Plan",
        }
    if acts:
        a = acts[0]
        cp = db.get(Counterparty, a.counterparty_id)
        inv = db.get(Invoice, a.invoice_id) if a.invoice_id else None
        label = _TYPE_LABEL.get(a.type, a.type.replace("_", " "))
        name = cp.name if cp else "the recipient"
        if a.type == "payment_request":
            outcome = f"Asks {name} to pay {fmt(a.amount_minor, cur)}{f' toward {inv.number}' if inv else ''}. Cash changes only when their payment settles."
        else:
            outcome = f"Pays {fmt(a.amount_minor, cur)} to {name} from settled cash. The reserve is checked again before anything is sent."
        if a.status == "proposed":
            title, cta = f"Approve the {label} of {fmt(a.amount_minor, cur)}", "Review in Approvals"
        else:
            title = f"{'Send' if a.type == 'payment_request' else 'Submit'} the approved {label}"
            cta = "Open Approvals"
        return {
            "kind": "action",
            "action_id": str(a.id),
            "title": title,
            "why": a.purpose,
            "outcome": outcome,
            "amount_minor": a.amount_minor,
            "requires_approval": a.status == "proposed",
            "href": "/approvals",
            "cta": cta,
        }
    if case is not None and case.status == "AWAITING_SETTLEMENT":
        return {
            "kind": "waiting",
            "title": "Wait for settlement",
            "why": "Requested or submitted money has not settled yet.",
            "outcome": "Settled cash updates automatically when the sandbox bank confirms. You can check status in Approvals.",
            "amount_minor": None,
            "requires_approval": False,
            "href": "/approvals",
            "cta": "Track in Approvals",
        }
    if case is not None and case.status == "RESOLVED":
        return {
            "kind": "done",
            "title": "Review the receipts",
            "why": "The rescue finished. Receipts and the audit trail show exactly what happened.",
            "outcome": "Nothing else needs to be done.",
            "amount_minor": None,
            "requires_approval": False,
            "href": "/activity",
            "cta": "Open Activity & Receipts",
        }
    return {
        "kind": "none",
        "title": "No action needed",
        "why": "Settled cash covers essential payments and the reserve.",
        "outcome": "FlowSense keeps watching upcoming payments.",
        "amount_minor": None,
        "requires_approval": False,
        "href": "/planner",
        "cta": "See the forecast",
    }


# ------------------------------------------------------------------ Home summary


def home_summary(db: Session, biz: Business, pos: Position, case: RescueCase | None) -> dict[str, Any]:
    """Status sentence, data completeness and an ordered task list for Home.

    Never reports a business as safe when information is missing: incomplete data gives an
    "incomplete" status (neutral, never green), whatever the arithmetic says.
    """
    from sqlalchemy import func

    from app.domain.records import business_today
    from app.domain.workspace import setup_status
    from app.models import Obligation, Policy

    cur = biz.ccy
    today = business_today(biz)
    invoices = db.execute(select(Invoice).where(Invoice.business_id == biz.id)).scalars().all()
    open_inv = [i for i in invoices if i.outstanding_minor > 0]
    overdue = [i for i in open_inv if i.due_on < today]
    n_obl = db.execute(select(func.count(Obligation.id)).where(Obligation.business_id == biz.id, Obligation.paid_minor < Obligation.amount_minor)).scalar_one()
    has_policy = db.execute(select(Policy.id).where(Policy.business_id == biz.id, Policy.status == "active")).first() is not None
    counts = dict(
        db.execute(
            select(Action.status, func.count(Action.id)).where(Action.business_id == biz.id, Action.status.in_(("proposed", "approved"))).group_by(Action.status)
        ).all()
    )
    cash_age = (today - biz.cash_as_of).days if biz.cash_as_of else None
    data = {
        "cash_status": pos.cash_status,
        "cash_as_of": biz.cash_as_of.isoformat() if biz.cash_as_of else None,
        "cash_age_days": cash_age,
        "cash_outdated": bool(biz.kind == "workspace" and cash_age is not None and cash_age > 7),
        "open_invoices": len(open_inv),
        "overdue_invoices": len(overdue),
        "open_obligations": int(n_obl),
        "money_in_none": biz.money_in_none_at is not None,
        "money_out_none": biz.money_out_none_at is not None,
        "policy": has_policy,
        "business_date": today.isoformat(),
        "simulated_clock": biz.sandbox_now is not None,
        "horizon_end": pos.horizon_end.isoformat(),
    }
    missing = []
    if pos.cash_status != "declared":
        missing.append("available cash")
    if not open_inv and not data["money_in_none"] and biz.kind == "workspace":
        missing.append("money coming in")
    if not n_obl and not data["money_out_none"] and biz.kind == "workspace":
        missing.append("money going out")
    if not has_policy:
        missing.append("your cash reserve")
    if missing:
        status = {"tone": "incomplete", "label": "Analysis incomplete", "sentence": f"FlowSense can't judge your position yet: {', '.join(missing)} {'is' if len(missing) == 1 else 'are'} missing."}
    elif pos.gap_minor > 0:
        status = {"tone": "danger", "label": "Shortfall", "sentence": headline(biz, pos, case, int(sum(counts.values())))}
    elif data["cash_outdated"]:
        status = {"tone": "warn", "label": "Check your figures", "sentence": f"Essential payments look covered, but your cash balance is {cash_age} days old. Update it to be sure."}
    else:
        status = {"tone": "ok", "label": "Covered", "sentence": headline(biz, pos, case, int(sum(counts.values())))}

    tasks: list[dict[str, Any]] = []
    for st in setup_status(db, biz)["steps"]:
        if not st["done"]:
            tasks.append({"key": f"setup_{st['key']}", "title": st["title"], "why": st["detail"], "href": st["href"], "cta": "Start" if st["key"] == "profile" else "Add"})
    if data["cash_outdated"]:
        tasks.append({"key": "cash_outdated", "title": "Update your available cash", "why": f"Your balance was last entered {cash_age} days ago.", "href": "/setup/cash", "cta": "Update"})
    if counts.get("proposed"):
        n = int(counts["proposed"])
        tasks.append({"key": "needs_approval", "title": f"Review {n} payment{'s' if n != 1 else ''} or request{'s' if n != 1 else ''} needing approval", "why": "Nothing is sent until you approve it, and safety rules are checked again first.", "href": "/approvals", "cta": "Review"})
    if counts.get("approved"):
        n = int(counts["approved"])
        tasks.append({"key": "ready_to_send", "title": f"Send {n} approved item{'s' if n != 1 else ''}", "why": "Approved items are not sent yet.", "href": "/approvals", "cta": "Open"})
    for inv in overdue[:3]:
        cp = db.get(Counterparty, inv.customer_id)
        tasks.append(
            {
                "key": f"overdue_{inv.number}",
                "title": f"Follow up overdue invoice {inv.number}" + (f" ({cp.name})" if cp else ""),
                "why": f"{fmt(inv.outstanding_minor, cur)} was due {inv.due_on:%d %b %Y} and has not arrived. It is not counted as cash.",
                "amount_minor": inv.outstanding_minor,
                "date": inv.due_on.isoformat(),
                "href": f"/money-in?invoice={inv.id}",
                "cta": "Review",
            }
        )
    if pos.gap_minor > 0 and not missing:
        tasks.append({"key": "rescue", "title": "See ways to close the shortfall", "why": f"{fmt(pos.gap_minor, cur)} is needed by {_when(biz, pos.gap_date or pos.horizon_end)}.", "amount_minor": pos.gap_minor, "href": "/rescue", "cta": "Open plan"})
    return {"status": status, "data": data, "tasks": tasks[:8]}
