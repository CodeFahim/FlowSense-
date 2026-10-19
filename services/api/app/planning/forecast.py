"""Deterministic dated cash forecast with what-if adjustments.

Read-only: never mutates live records. Three amount classes stay distinct:
  settled   - ledger cash minus pending outgoing reservations
  expected  - receipts with an accepted request, plus remaining invoice
              balances on an explicit collection-lag assumption
  uncertain - requests sent but not yet accepted (shown, never added)
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import business_now, tz
from app.domain.position import compute_position
from app.errors import ApiError
from app.models import Action, Business, Counterparty, Invoice, Obligation
from app.money import MAX_MINOR, fmt

OVERDUE_LAG_DAYS = 14


def _series(
    db: Session, biz: Business, days: int, delay_days: int, collection_pct: int, emergency_minor: int, emergency_day: int
) -> dict[str, Any]:
    zone = tz(biz)
    now_local = business_now(biz).astimezone(zone)
    today = now_local.date()
    pos = compute_position(db, biz)

    outflows: list[tuple[datetime, int, str]] = []
    for ob in db.execute(
        select(Obligation).where(Obligation.business_id == biz.id, Obligation.paid_minor < Obligation.amount_minor)
    ).scalars():
        outflows.append((ob.due_at.astimezone(zone), ob.unpaid_minor, ob.description))
    # Pending outgoing transfers are already removed via `available`; drop their obligations' reserved part.
    reserved_by_desc = pos.reserved_minor

    expected: list[tuple[datetime, int, str]] = []
    uncertain: list[tuple[datetime, int, str]] = []
    for inv in db.execute(select(Invoice).where(Invoice.business_id == biz.id, Invoice.collected_minor < Invoice.amount_minor)).scalars():
        cp = db.get(Counterparty, inv.customer_id)
        lag = cp.typical_days_to_pay if cp else 1
        remaining = inv.outstanding_minor
        for req in db.execute(
            select(Action).where(Action.invoice_id == inv.id, Action.type == "payment_request", Action.status.in_(("sent", "accepted")))
        ).scalars():
            amt = req.amount_minor - req.settled_minor
            when = datetime.combine(today + timedelta(days=lag + delay_days), time(12), zone)
            remaining -= amt
            if req.status == "accepted":
                expected.append((when, amt * collection_pct // 100, f"Accepted request on {inv.number}"))
            else:
                uncertain.append((when, amt, f"Sent (unaccepted) request on {inv.number}"))
        if remaining > 0:
            base = max(inv.due_on, today) + timedelta(days=(OVERDUE_LAG_DAYS if inv.due_on <= today else 0) + delay_days)
            expected.append(
                (datetime.combine(base, time(12), zone), remaining * collection_pct // 100, f"Remaining balance of {inv.number}")
            )
    # A what-if expense is money going out (a positive outflow), never a negative inflow.
    what_if_out: list[tuple[datetime, int, str]] = []
    if emergency_minor:
        what_if_out.append((datetime.combine(today + timedelta(days=emergency_day), time(9), zone), emergency_minor, "Emergency expense (what-if)"))

    points: list[dict[str, Any]] = []
    first_breach_settled = first_breach_expected = None
    for d in range(days + 1):
        day = today + timedelta(days=d)
        end = datetime.combine(day, time(23, 59, 59), zone)
        due = sum(a for when, a, _ in outflows if when <= end)
        # Outflows already reserved by pending transfers must not be counted twice.
        due = max(0, due - reserved_by_desc)
        settled_path = pos.available_minor - due
        exp_in = sum(a for when, a, _ in expected if when <= end)
        unc_in = sum(a for when, a, _ in uncertain if when <= end)
        emergency_out = sum(a for when, a, _ in what_if_out if when <= end)
        expected_path = settled_path + exp_in - emergency_out
        settled_path -= emergency_out
        points.append(
            {
                "date": day.isoformat(),
                "settled_path_minor": settled_path,
                "expected_path_minor": expected_path,
                "uncertain_inflows_minor": unc_in,
                "reserve_minor": pos.reserve_minor,
                "obligations_due_minor": due,
            }
        )
        if first_breach_settled is None and settled_path < pos.reserve_minor:
            first_breach_settled = day.isoformat()
        if first_breach_expected is None and expected_path < pos.reserve_minor:
            first_breach_expected = day.isoformat()
    return {
        "points": points,
        "first_reserve_breach_settled": first_breach_settled,
        "first_reserve_breach_expected": first_breach_expected,
        "min_expected_minor": min(p["expected_path_minor"] for p in points),
        "inflows": [{"date": w.date().isoformat(), "amount_minor": a, "label": lbl, "class": "expected"} for w, a, lbl in expected]
        + [{"date": w.date().isoformat(), "amount_minor": a, "label": lbl, "class": "uncertain"} for w, a, lbl in uncertain],
        "outflows": [{"date": w.date().isoformat(), "amount_minor": a, "label": lbl} for w, a, lbl in sorted([*outflows, *what_if_out], key=lambda x: x[0])],
    }


def forecast(
    db: Session,
    biz: Business,
    *,
    days: int = 14,
    delay_days: int = 0,
    collection_pct: int = 100,
    emergency_minor: int = 0,
    emergency_day: int = 1,
) -> dict[str, Any]:
    if days not in (7, 14, 30, 60):
        raise ApiError(422, "FORECAST_HORIZON_INVALID", "days must be 7, 14, 30 or 60.")
    if (
        not 0 <= delay_days <= 60
        or not 0 <= collection_pct <= 100
        or not 0 <= emergency_minor <= MAX_MINOR
        or not 0 <= emergency_day <= days
    ):
        raise ApiError(422, "WHATIF_INVALID", "What-if parameters out of range.")
    baseline = _series(db, biz, days, 0, 100, 0, 1)
    scenario = _series(db, biz, days, delay_days, collection_pct, emergency_minor, emergency_day)
    pos = compute_position(db, biz)
    return {
        "currency": biz.ccy,
        "as_of": pos.as_of.isoformat(),
        "days": days,
        "position": pos.to_dict(),
        "whatif": {
            "delay_days": delay_days,
            "collection_pct": collection_pct,
            "emergency_minor": emergency_minor,
            "emergency_day": emergency_day,
        },
        "assumptions": [
            "Settled path uses only settled, unreserved cash and scheduled obligations.",
            "Accepted requests are expected after the customer's typical payment lag.",
            f"Overdue invoice balances are expected {OVERDUE_LAG_DAYS} days from today; others on their due date.",
            "Sent-but-unaccepted requests are uncertain and never added to any path.",
            f"Authoritative gap today: {fmt(pos.gap_minor, pos.currency)} (settled funds only).",
        ],
        "baseline": baseline,
        "scenario": scenario,
    }
