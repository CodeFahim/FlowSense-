"""Deterministic assessment of recovery options for assistant answers.

One classification per option, computed by code from the user's records, the active policy and the
rules the user stated in this chat. The model explains and compares these results; it never decides
the class itself. The same data is shown to the user as option cards, so the narrative, the cards and
any buttons agree.

Classes:
- prohibited: conflicts with an identified active rule (the rule is named).
- conditional: could work once stated prerequisites are met (agreement, evidence, timing, approval);
  the prerequisites are listed and are never treated as already satisfied.
- feasible: current records, calculations, timing and policy checks support it; approvals and
  execution steps are still listed.
- insufficient_information: a material fact is missing; the missing facts are listed.

Facts that rule an option out without a rule conflict (a customer refused, money would arrive too late,
a bill due after the deadline) are listed separately under "ruled_out" with the reason.

Read-only: nothing is stored, no plan version is written.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.agents import constraints
from app.clock import business_now
from app.domain.position import compute_position
from app.domain.workspace import is_configured
from app.models import Business
from app.money import MAX_MINOR, SUPPORTED_CURRENCIES, fmt
from app.planning import rescue
from app.planning.planner import build_components

PROHIBITED = "prohibited"
CONDITIONAL = "conditional"
FEASIBLE = "feasible"
INSUFFICIENT = "insufficient_information"
LABELS = {
    PROHIBITED: "Prohibited by your rules",
    CONDITIONAL: "Conditional",
    FEASIBLE: "Feasible",
    INSUFFICIENT: "Not enough information",
}
HOW_TO_USE = (
    "These classes are computed by FlowSense and are final for this answer. Describe every option with exactly its class "
    "and label. Never call a prohibited option valid, and never present a conditional option's conditions as already met. "
    "If a condition changes (for example the supplier agrees in writing and the new date is recorded), FlowSense recalculates; "
    "say what would have to change instead of reclassifying it yourself. Do not add options that are not listed here."
)


@dataclass(frozen=True)
class Hypothetical:
    amount_minor: int
    due_at: datetime
    description: str


def _m(minor: int, ccy: str) -> dict[str, Any]:
    return {"minor": minor, "display": fmt(minor, ccy)}


def _when(biz: Business, dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone(ZoneInfo(biz.tz_name)).strftime("%a %d %b %Y %H:%M") + f" ({biz.tz_name})"


def hypothetical(biz: Business, amount_minor: int, due_in_days: int, description: str | None) -> Hypothetical:
    return Hypothetical(amount_minor, business_now(biz) + timedelta(days=due_in_days), (description or "Hypothetical payment").strip()[:120])


# A what-if payment stated in the user's words: "if a 15,000 essential bill came due tomorrow". Found by code so
# the deterministic assessment (and its cards) never depends on the model choosing to call assess_options
# (live check, 19 Oct: the model skipped the tool and computed a wrong gap itself).
_IF = re.compile(r"\b(if|suppose|supposing|hypothetical\w*|what if|imagine|say)\b", re.I)
_PAYMENT = re.compile(
    r"(?<![\w.,])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*([kK])?\b(?:\s+[A-Z]{3})?\s+(?:[a-z-]+\s+){0,3}?"
    r"(bill|payment|expense|invoice|cost|rent|tax|purchase|repair|payable)\b",
    re.I,
)
_DUE = [
    (re.compile(r"\bday after tomorrow\b", re.I), lambda m: 2),
    (re.compile(r"\btomorrow\b", re.I), lambda m: 1),
    (re.compile(r"\bin (\d{1,2}) days?\b", re.I), lambda m: int(m.group(1))),
    (re.compile(r"\bnext week\b|\bin a week\b", re.I), lambda m: 7),
    (re.compile(r"\b(today|now|right away|immediately)\b", re.I), lambda m: 0),
]


def stated_hypothetical(biz: Business, texts: list[str]) -> Hypothetical | None:
    """The what-if payment in the latest message, or in one of the two before it ("options for that bill")."""
    for text in reversed(texts[-3:]):
        if not _IF.search(text):
            continue
        m = _PAYMENT.search(text)
        if m is None:
            continue
        major = Decimal(m.group(1).replace(",", "") + (m.group(2) or "")) * (1000 if m.group(3) else 1)
        minor = int(major * (Decimal(10) ** SUPPORTED_CURRENCIES[biz.ccy]))
        if not 0 < minor <= MAX_MINOR:
            continue
        days = 0
        for rx, fn in _DUE:
            d = rx.search(text)
            if d:
                days = min(fn(d), 60)
                break
        word = m.group(4).lower()
        return hypothetical(biz, minor, days, f"Hypothetical {word}")
    return None


def _option(oid: str, title: str, cls: str, **extra: Any) -> dict[str, Any]:
    return {"id": oid, "title": title, "class": cls, "label": LABELS[cls], **{k: v for k, v in extra.items() if v not in (None, [], "")}}


def assess(db: Session, biz: Business, user_texts: list[str], hyp: Hypothetical | None = None, *, with_time: bool = True) -> dict[str, Any]:
    """Classify the recovery options for the saved records, or for the records plus one hypothetical payment."""
    out: dict[str, Any] = {"how_to_use": HOW_TO_USE}
    if with_time:
        out["as_of"] = business_now(biz).isoformat()
    if not is_configured(biz):
        out.update(status=INSUFFICIENT, missing=["the business currency and timezone"], options=[], ruled_out=[])
        return out
    ccy = biz.ccy
    pos = compute_position(db, biz)
    rules = constraints.current(db, biz, user_texts)
    missing: list[str] = []
    if pos.cash_status == "not_entered":
        missing.append("available cash (the shortfall can't be calculated without it)")
    if pos.policy_version is None:
        missing.append("a confirmed safety reserve and rules")
    if missing:
        out.update(status=INSUFFICIENT, missing=missing, options=[], ruled_out=[], note="Missing records do not mean there is no risk.")
        return out

    available = pos.available_minor
    spendable = available - pos.reserve_minor
    if hyp is not None:
        # The what-if payment joins the essential payments in the protection window, in due-date order (the same
        # walk as compute_position): the gap is the largest deficit and the deadline is where it first appears.
        # Live check, 19 Oct: counting only payments due up to the what-if's own date missed Friday's payroll.
        schedule = sorted([(p.due_at, p.open_minor) for p in pos.protected] + [(hyp.due_at, hyp.amount_minor)], key=lambda x: x[0])
        target = max(0, pos.reserve_minor - available)
        deadline = pos.as_of if target > 0 else None
        cumulative = 0
        for due, amount in schedule:
            cumulative += amount
            deficit = pos.reserve_minor + cumulative - available
            if deficit > target:
                target = deficit
            if deficit > 0 and deadline is None:
                deadline = due
        before = pos.protected_total_minor
        free_for_hyp = max(0, spendable - sum(p.open_minor for p in pos.protected if p.due_at <= hyp.due_at))
    else:
        deadline = pos.gap_date
        before = pos.protected_total_minor
        target = pos.gap_minor
        free_for_hyp = 0

    situation: dict[str, Any] = {
        "settled_cash": _m(pos.settled_minor, ccy),
        "already_committed_to_payments_in_progress": _m(pos.reserved_minor, ccy),
        "protected_reserve": _m(pos.reserve_minor, ccy),
        "spendable_cash": _m(max(0, spendable), ccy),
        "below_reserve_by": _m(-spendable, ccy) if spendable < 0 else None,
        "saved_essential_payments_due": _m(before, ccy),
        "gap": _m(target, ccy),
        "deadline": _when(biz, deadline),
        "saved_records_gap": _m(pos.gap_minor, ccy) if hyp is not None else None,
    }
    out["situation"] = {k: v for k, v in situation.items() if v is not None}
    out["calculation"] = (
        "spendable cash = settled cash − money already committed to payments in progress − protected reserve; "
        "gap = essential payments due by the deadline" + (" + the hypothetical payment" if hyp else "") + " + reserve − settled cash not already committed. "
        "Expected or promised money is never counted."
    )
    if hyp is not None:
        out["hypothetical"] = {
            "description": hyp.description,
            "amount": _m(hyp.amount_minor, ccy),
            "due": _when(biz, hyp.due_at),
            "saved": False,
            "note": "Hypothetical: not a saved record. Saved records are unchanged.",
        }
    if target <= 0:
        out.update(status="no_shortfall", options=[], ruled_out=[], recommended=None)
        return out

    inp = rescue.planner_input(db, biz, pos)
    if hyp is not None:
        inp = dataclasses.replace(inp, gap_minor=target, gap_date=hyp.due_at)
    accepted, rejected = build_components(inp)
    options: list[dict[str, Any]] = []
    ruled_out: list[dict[str, Any]] = []

    for c in accepted:
        contribution = min(int(c.get("max_contribution_minor") or 0), target)
        if c["kind"] == "partial_collection":
            options.append(_option(
                c["id"], f"Collect {fmt(contribution, ccy)} of {c['invoice']} from {c['counterparty']}", FEASIBLE,
                cash_impact=_m(contribution, ccy), timing=f"Expected by {_when(biz, datetime.fromisoformat(c['expected_by']))}",
                requirements=["The owner approves the payment request in Approvals", *c.get("dependencies", []), "Until the money settles it is not cash"],
                basis=c.get("basis"), cost=_m(0, ccy),
            ))
        elif c["kind"] == "financing":
            if rules.user_no_borrowing:
                options.append(_option(c["id"], f"Financing from {c['counterparty']}", PROHIBITED, rule="You said in this chat not to borrow."))
            else:
                options.append(_option(
                    c["id"], f"Financing from {c['counterparty']}", FEASIBLE, cash_impact=_m(contribution, ccy), cost=_m(int(c.get("cost_minor") or 0), ccy),
                    requirements=["The owner accepts the quote", "Loan proceeds settle"], basis=c.get("basis"),
                ))
        elif c["kind"] == "supplier_extension":
            options.append(_option(
                c["id"], f"Move the due date of {c['description']}", CONDITIONAL, cash_impact=_m(contribution, ccy),
                conditions=[
                    f"The supplier agrees in writing to a due date after {_when(biz, deadline)}",
                    "You record the new due date in Money out (FlowSense then recalculates)",
                    "The bill has no legal or contractual deadline that can't move",
                ],
                conditions_met=False,
            ))
    for r in rejected:
        if r.get("code") == "FINANCING_COST_EXCEEDS_POLICY":
            options.append(_option(r["id"], f"Financing from {r['counterparty']}", PROHIBITED, rule=r["reason"]))
        elif r.get("code") != "PLANNER_BOUND":
            title = f"Collect {r['invoice']} from {r['counterparty']}" if r["kind"] == "partial_collection" else (
                f"Financing from {r['counterparty']}" if r["kind"] == "financing" else f"Move the due date of {r.get('description')}"
            )
            ruled_out.append({"title": title, "reason": r["reason"]})

    if hyp is not None:
        conds = [
            f"The payee agrees in writing before {_when(biz, hyp.due_at)}",
            "The agreed change is recorded in FlowSense (it then recalculates)",
            "The deadline is not binding (tax, wages or a contract penalty usually are); if it is binding, deferral is not available",
        ]
        if 0 < free_for_hyp < hyp.amount_minor:
            options.append(_option(
                "hyp:partial", f"Pay {fmt(free_for_hyp, ccy)} now and {fmt(hyp.amount_minor - free_for_hyp, ccy)} later", CONDITIONAL,
                cash_impact=_m(min(target, hyp.amount_minor - free_for_hyp), ccy), conditions=conds, conditions_met=False,
            ))
        options.append(_option(
            "hyp:defer", f"Move the due date of the {fmt(hyp.amount_minor, ccy)} payment", CONDITIONAL,
            cash_impact=_m(min(target, hyp.amount_minor), ccy), conditions=conds, conditions_met=False,
        ))
        if not any(c["kind"] == "partial_collection" for c in accepted):
            ruled_out.append({"title": "Collect from customers", "reason": "No customer payment is expected to settle before this deadline in your records."})

    if not any(c["kind"] == "financing" for c in [*accepted, *rejected]):
        if rules.borrowing_blocked:
            rule = "You said in this chat not to borrow." if rules.user_no_borrowing else "Your rules don't allow borrowing (financing cost cap 0%)."
            options.append(_option("borrow", "Borrow to cover the gap", PROHIBITED, rule=rule))
        else:
            options.append(_option("borrow", "Borrow to cover the gap", INSUFFICIENT, missing=["a real financing quote (none is on record, so cost and eligibility are unknown)"]))
    if pos.reserve_minor > 0 or rules.user_keep_reserve:
        rule = f"Protected reserve {fmt(pos.reserve_minor, ccy)} (policy v{pos.policy_version}) is not spendable." if pos.reserve_minor else ""
        if rules.user_keep_reserve:
            rule = (rule + " " if rule else "") + "You also said in this chat to keep the reserve protected."
        options.append(_option("reserve", "Use or lower the protected reserve", PROHIBITED, rule=rule + " Changing it is a policy decision for the owner in Settings, not a recovery option."))

    order = {FEASIBLE: 0, CONDITIONAL: 1, INSUFFICIENT: 2, PROHIBITED: 3}
    options.sort(key=lambda o: order[o["class"]])
    feasible_total = sum(o["cash_impact"]["minor"] for o in options if o["class"] == FEASIBLE and "cash_impact" in o)
    rec = next((o for o in options if o["class"] == FEASIBLE), None) or next((o for o in options if o["class"] == CONDITIONAL), None)
    out.update(
        status="shortfall",
        options=options,
        ruled_out=ruled_out,
        recommended={"id": rec["id"], "title": rec["title"], "class": rec["class"]} if rec else None,
        feasible_options_cover_gap=feasible_total >= target,
    )
    return out


def card_view(a: dict[str, Any]) -> dict[str, Any] | None:
    """The part of an assessment shown to the user as option cards (None when there is nothing to choose)."""
    if a.get("status") not in ("shortfall", INSUFFICIENT, "no_shortfall"):
        return None
    keep = ("as_of", "status", "situation", "hypothetical", "options", "ruled_out", "recommended", "missing", "calculation", "feasible_options_cover_gap", "note")
    return {k: a[k] for k in keep if k in a}
