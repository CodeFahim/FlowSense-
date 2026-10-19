"""Deterministic, bounded rescue planner.

Method: build candidate components (partial collections, financing quotes,
supplier extensions), reject those that violate policy or cannot help before
the gap date (with reasons), then enumerate every subset of the accepted
components (at most 2^8 = 256 subsets; inputs beyond 8 components are
truncated and reported) and keep subsets whose contribution covers the gap.
Ranking key, in order: total cost, number of external dependencies,
uncertainty, latest expected date, then a stable component id order.

Limits: single currency; one partial request per invoice; contributions are
treated as all-or-nothing; no probabilistic modelling; small fixtures only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from itertools import combinations
from typing import Any

from app.money import annualized_all_in_bp, fmt

MAX_COMPONENTS = 8
_UNCERTAINTY_RANK = {"low": 0, "medium": 1, "high": 2}


@dataclass
class InvoiceIn:
    id: str
    number: str
    customer_id: str
    customer_name: str
    requestable_minor: int
    accepts_partial: bool
    days_to_pay: int
    disputed: bool
    evidence: list[dict[str, Any]]
    refused: bool = False


@dataclass
class QuoteIn:
    id: str
    lender: str
    principal_minor: int
    fee_minor: int
    interest_minor: int
    term_days: int
    expires_at: datetime


@dataclass
class SupplierIn:
    id: str
    description: str
    open_minor: int
    due_at: datetime
    essential: bool


@dataclass
class PlannerInput:
    now: datetime
    gap_minor: int
    gap_date: datetime | None
    horizon_end: datetime
    reserve_minor: int
    cost_cap_bp: int
    currency: str
    invoices: list[InvoiceIn] = field(default_factory=list)
    quotes: list[QuoteIn] = field(default_factory=list)
    suppliers: list[SupplierIn] = field(default_factory=list)


def _pct(bp: int) -> str:
    return f"{bp / 100:.2f}%"


def build_components(inp: PlannerInput) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    deadline = inp.gap_date or inp.horizon_end
    base: dict[str, Any]

    for inv in inp.invoices:
        cid = f"collect:{inv.number}"
        verified = [e for e in inv.evidence if e["provenance"] == "sandbox_verified"]
        base = {
            "id": cid,
            "kind": "partial_collection",
            "invoice_id": inv.id,
            "invoice": inv.number,
            "counterparty_id": inv.customer_id,
            "counterparty": inv.customer_name,
            "evidence": [e["label"] for e in inv.evidence],
        }
        expected = inp.now + timedelta(days=inv.days_to_pay)
        if inv.refused:
            rejected.append(
                {**base, "code": "CUSTOMER_REFUSED", "reason": f"{inv.customer_name} refused a payment request; not re-proposed."}
            )
        elif inv.disputed:
            rejected.append({**base, "code": "INVOICE_DISPUTED", "reason": f"{inv.number} is disputed."})
        elif inv.requestable_minor <= 0:
            rejected.append({**base, "code": "NOTHING_REQUESTABLE", "reason": f"{inv.number} has nothing left to request."})
        elif not inv.accepts_partial:
            rejected.append(
                {**base, "code": "PARTIAL_NOT_ACCEPTED", "reason": f"{inv.customer_name} does not accept partial-payment requests."}
            )
        elif expected > deadline:
            rejected.append(
                {**base, "code": "TOO_LATE", "reason": f"Expected settlement {expected:%d %b} is after the gap date {deadline:%d %b}."}
            )
        else:
            accepted.append(
                {
                    **base,
                    "max_contribution_minor": inv.requestable_minor,
                    "cost_minor": 0,
                    "expected_by": expected.isoformat(),
                    "dependencies": ["Customer accepts the partial-payment request", "Customer's payment settles before the deadline"],
                    "uncertainty": "medium" if verified else "high",
                    "basis": (
                        f"{inv.number} has {fmt(inv.requestable_minor, inp.currency)} requestable; delivery evidence "
                        + (", ".join(e["label"] for e in verified) + " is sandbox-verified." if verified else "is not verified.")
                    ),
                }
            )

    for q in inp.quotes:
        cid = f"finance:{q.lender}"
        net = q.principal_minor - q.fee_minor
        cost = q.fee_minor + q.interest_minor
        base = {
            "id": cid,
            "kind": "financing",
            "quote_id": q.id,
            "counterparty": q.lender,
            "principal_minor": q.principal_minor,
            "net_proceeds_minor": net,
            "cost_minor": cost,
            "term_days": q.term_days,
        }
        try:
            bp = annualized_all_in_bp(cost, net, q.term_days)
        except ValueError:
            rejected.append({**base, "code": "QUOTE_INVALID", "reason": "Quote has non-positive proceeds or term."})
            continue
        base["annualized_all_in_bp"] = bp
        if q.expires_at <= inp.now:
            rejected.append({**base, "code": "QUOTE_EXPIRED", "reason": f"Quote expired {q.expires_at:%d %b %H:%M}."})
        elif bp > inp.cost_cap_bp:
            rejected.append(
                {
                    **base,
                    "code": "FINANCING_COST_EXCEEDS_POLICY",
                    "reason": f"Annualized all-in cost {_pct(bp)} exceeds the policy cap of {_pct(inp.cost_cap_bp)} "
                    f"(fees {fmt(cost, inp.currency)} on net proceeds {fmt(net, inp.currency)} over {q.term_days} days).",
                }
            )
        else:
            accepted.append(
                {
                    **base,
                    "max_contribution_minor": net,
                    "expected_by": inp.now.isoformat(),
                    "dependencies": ["Owner accepts the quote", "Loan proceeds settle"],
                    "uncertainty": "low",
                    "basis": f"Quote at {_pct(bp)} annualized all-in, within the {_pct(inp.cost_cap_bp)} cap.",
                }
            )

    for s in inp.suppliers:
        cid = f"extend:{s.description[:40]}"
        base = {"id": cid, "kind": "supplier_extension", "obligation_id": s.id, "description": s.description, "open_minor": s.open_minor}
        counted = s.essential and s.due_at <= deadline
        if not counted:
            rejected.append(
                {
                    **base,
                    "code": "NO_EFFECT_ON_GAP",
                    "reason": f"Due {s.due_at:%d %b}, which is after the gap date or not a protected obligation, so delaying it does not change the gap.",
                }
            )
        else:
            accepted.append(
                {
                    **base,
                    "max_contribution_minor": s.open_minor,
                    "cost_minor": 0,
                    "expected_by": inp.now.isoformat(),
                    "dependencies": ["Supplier accepts a new due date (recorded sandbox acceptance)"],
                    "uncertainty": "medium",
                    "basis": f"Deferring {fmt(s.open_minor, inp.currency)} due {s.due_at:%d %b} beyond the gap date.",
                }
            )

    if len(accepted) > MAX_COMPONENTS:
        for extra in accepted[MAX_COMPONENTS:]:
            rejected.append({**extra, "code": "PLANNER_BOUND", "reason": f"Planner considers at most {MAX_COMPONENTS} components."})
        accepted = accepted[:MAX_COMPONENTS]
    return accepted, rejected


def _rank_key(combo: list[dict[str, Any]]) -> tuple[Any, ...]:
    return (
        sum(c["cost_minor"] for c in combo),
        sum(len(c["dependencies"]) for c in combo),
        max(_UNCERTAINTY_RANK[c["uncertainty"]] for c in combo),
        max(c["expected_by"] for c in combo),
        tuple(c["id"] for c in combo),
    )


def plan(inp: PlannerInput) -> dict[str, Any]:
    accepted, rejected = build_components(inp)
    body: dict[str, Any] = {
        "method": "Exhaustive enumeration of up to 2^8 component subsets; rank by cost, dependencies, uncertainty, timing.",
        "gap_minor": inp.gap_minor,
        "gap_date": inp.gap_date.isoformat() if inp.gap_date else None,
        "components": accepted,
        "rejected": rejected,
    }
    if inp.gap_minor <= 0:
        body.update(
            feasible=True,
            recommended=None,
            alternatives=[],
            steps=[],
            remaining_gap_minor=0,
            summary="No funding gap: protected obligations and the reserve are covered by settled cash.",
        )
        return body

    feasible: list[list[dict[str, Any]]] = []
    for r in range(1, len(accepted) + 1):
        for combo in combinations(accepted, r):
            if sum(c["max_contribution_minor"] for c in combo) >= inp.gap_minor:
                feasible.append(list(combo))
    feasible.sort(key=_rank_key)

    if not feasible:
        best = sum(c["max_contribution_minor"] for c in accepted)
        body.update(
            feasible=False,
            recommended=None,
            alternatives=[],
            steps=[],
            remaining_gap_minor=inp.gap_minor - best,
            unmet_constraints=[r["reason"] for r in rejected] or ["No recovery options are available."],
            summary=f"No feasible plan: even all acceptable options leave {fmt(inp.gap_minor - best, inp.currency)} uncovered.",
        )
        return body

    def steps_for(combo: list[dict[str, Any]]) -> list[dict[str, Any]]:
        need = inp.gap_minor
        steps = []
        for c in sorted(combo, key=lambda c: (c["cost_minor"], c["id"])):
            take = min(need, c["max_contribution_minor"])
            need -= take
            steps.append(
                {
                    "component": c["id"],
                    "kind": c["kind"],
                    "amount_minor": take,
                    **{k: c[k] for k in c if k in ("invoice_id", "counterparty_id", "quote_id", "obligation_id")},
                }
            )
        return steps

    rec = feasible[0]
    summary = "; ".join(f"{s['kind'].replace('_', ' ')} of {fmt(s['amount_minor'], inp.currency)}" for s in steps_for(rec))
    summary = summary[:1].upper() + summary[1:] + f" closes the {fmt(inp.gap_minor, inp.currency)} gap."
    body.update(
        feasible=True,
        recommended={"components": [c["id"] for c in rec], "total_cost_minor": sum(c["cost_minor"] for c in rec)},
        alternatives=[{"components": [c["id"] for c in f], "total_cost_minor": sum(c["cost_minor"] for c in f)} for f in feasible[1:5]],
        steps=steps_for(rec),
        remaining_gap_minor=0,
        summary=summary,
    )
    return body
