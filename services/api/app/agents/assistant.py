"""Offline demo assistant.

AI_MODE=demo: "Offline demo assistant" - a deterministic intent router with
templated explanations. Every money figure comes from backend calculations.

It cannot approve, activate policies, assign roles, verify or create recipients,
create payment requests, fetch URLs or write ledger entries. It may only *propose*
a policy (the owner reviews and activates it), suggest an action the user confirms
with a button (agents.proposals), and read authorized data.

AI_MODE=live is handled by app.agents.live (tool-grounded model). app.agents.chat
chooses the mode and never silently substitutes these canned answers for live AI.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.clock import business_now
from app.config import get_settings
from app.domain.actions import Actor, evaluate_action
from app.domain.position import active_policy, compute_position
from app.errors import ApiError
from app.models import Action, Business, Counterparty, Evidence, FinancingQuote, Invoice, Obligation, Policy
from app.money import annualized_all_in_bp, fmt
from app.planning.forecast import forecast
from app.planning.rescue import current_case, latest_plan
from app.policies.compiler import compile_policy
from app.safety.kernel import Phase

LABEL = "Offline demo assistant"


def mode_label() -> str:
    mode = get_settings().ai_mode
    return {"demo": LABEL, "live": "Live AI", "off": "AI unavailable"}[mode]


ALLOWED_TOOLS = (
    "analyse_business",
    "plan_payments",
    "propose_cash",
    "navigate",
    "fx_quote",
    "getting_started",
    "explain_gap",
    "list_receivables",
    "payroll_safety",
    "explain_blocks",
    "compare_financing",
    "compare_options",
    "delay_scenario",
    "draft_payment_request",
    "compile_policy",
    "evidence_action_attempt",
)


QUICK_ACTIONS = ["Analyse my business", "Explain my cash shortage", "Help me plan payments"]


# The first five are shown as starter questions; the rest as "more examples".
def suggestions(currency: str, *, fresh: bool = False) -> dict[str, list[str]]:
    if fresh:
        return {
            "primary": [
                "How do I get started?",
                "Where do I add a bill?",
                "Where do I add an invoice?",
                "What does available cash mean?",
                "Convert 100 USD to BDT",
            ],
            "more": ["Where do I change my currency?", "Explain my cash position."],
        }
    return {
        "primary": [
            "Explain my cash position.",
            "Can I cover payroll?",
            "Which invoices should I collect?",
            "Why was this action blocked?",
            "Compare my rescue options.",
        ],
        "more": [
            "What if my customer pays 5 days late?",
            "Compare financing options",
            "Draft the payment request",
            f"Pay employees this Friday, retain {currency or 'USD'} 10,000, use only approved recipients, and never borrow above 8% annualized all-in cost.",
            "Follow the instructions in the invoice evidence",
            "Where do I add a bill?",
            "Convert 100 USD to BDT",
        ],
    }


_LINKS = {
    "fx_quote": [("Open the converter", "/?convert=1")],
    "explain_gap": [("Open Rescue plan", "/rescue")],
    "list_receivables": [("Open Money in", "/money-in")],
    "payroll_safety": [("Open Approvals", "/approvals")],
    "explain_blocks": [("Open Activity", "/activity")],
    "compare_financing": [("Open Rescue plan", "/rescue")],
    "compare_options": [("Open Rescue plan", "/rescue")],
    "delay_scenario": [("Open the forecast", "/rescue#forecast")],
    "draft_payment_request": [("Review in Approvals", "/approvals")],
    "compile_policy": [("Review the policy in Approvals", "/approvals#policies")],
    "evidence_action_attempt": [("Open Activity", "/activity")],
}

_NAV = [
    (r"\bbill|expense|supplier|rent\b", ("Add a bill", "/money-out?add=bill")),
    (r"\bpayroll|salar|wage", ("Add payroll", "/money-out?add=payroll")),
    (r"\binvoice|customer|receivable", ("Add an invoice", "/money-in?add=invoice")),
    (r"\bcash|balance|bank", ("Add available cash", "/setup/cash")),
    (r"\bcurrenc|timezone|time zone|country", ("Business settings", "/settings#business")),
    (r"\bevidence|document|pdf|upload", ("Upload evidence on Money in", "/money-in")),
    (r"\breserve|policy|limit", ("Confirm your reserve", "/setup/review")),
    (r"\bapprov", ("Approvals", "/approvals")),
]


def _reply(intent: str, text: str, **extra: Any) -> dict[str, Any]:
    links = [{"label": label, "href": href} for label, href in _LINKS.get(intent, [])]
    return {"mode": "demo", "label": LABEL, "intent": intent, "text": text, "citations": [], "links": links, **extra}


def _route(msg: str) -> str:
    m = msg.lower()
    if re.search(r"\banaly[sz]e (my |the )?business\b|\bhow (is my business|am i doing)\b", m):
        return "analyse_business"
    if re.search(r"\bplan (my )?payments\b|\bwhat (should|do) i pay first\b|\bpayment plan\b", m):
        return "plan_payments"
    if re.search(r"\bexplain (my )?(cash )?(shortage|shortfall|gap)\b", m):
        return "explain_gap"
    if re.search(r"\b(record|add|set|update)\b.{0,20}\bcash\b.{0,20}\d|\bi have\b.{0,15}\d[\d,.]*.{0,15}\b(cash|in (the )?bank)\b", m):
        return "propose_cash"
    if re.search(r"\b(retain|reserve|keep)\b.*\b([a-z]{3}|rmb)\s?\d|\bnever borrow\b", m):
        return "compile_policy"
    if re.search(r"(follow|do what|obey|execute).{0,40}(instruction|note|evidence)|send all money", m):
        return "evidence_action_attempt"
    if re.search(r"\b(where|how)\b.{0,25}\b(add|enter|record|change|set|upload|find)\b", m):
        return "navigate"
    if re.search(r"\bconvert\b|\bexchange rate\b|\b\d[\d,.]*\s*[a-z]{3}\s+(to|in)\s+[a-z]{3}\b", m):
        return "fx_quote"
    if re.search(r"\bget(ting)? started\b|\bwhat (do|should) i do first\b|\bset ?up\b|\bwhat does\b.{0,30}\bmean\b", m):
        return "getting_started"
    if re.search(r"\bwhat if\b|\blate\b|\bdelay", m):
        return "delay_scenario"
    if re.search(r"\b(draft|prepare|create)\b.{0,30}\brequest\b", m):
        return "draft_payment_request"
    if re.search(r"\b(blocked|denied|why was|why can'?t|refused)\b", m):
        return "explain_blocks"
    if re.search(r"\b(rescue|recovery) (options?|plans?)\b|\bcompare (my |the |all )?(options|plans?)\b", m):
        return "compare_options"
    if re.search(r"\b(payroll|salar|wages)\b.{0,30}\b(safe|pay|ok)\b|\bcan (we|i) (pay|cover)\b|\bcover (the )?payroll\b", m):
        return "payroll_safety"
    if re.search(r"\b(financ|loan|borrow|quote|lender)", m):
        return "compare_financing"
    if re.search(r"\b(owe|receivable|invoice|customer)", m):
        return "list_receivables"
    if re.search(r"\b(gap|short|shortfall|cash|enough|how is my business|how am i doing|what should i do)\b", m):
        return "explain_gap"
    return "unsupported"


_NEEDS_DATA = {"analyse_business", "plan_payments", "explain_gap", "list_receivables", "payroll_safety", "explain_blocks", "compare_financing", "compare_options", "delay_scenario", "draft_payment_request"}


def chat(db: Session, biz: Business, actor: Actor, message: str) -> dict[str, Any]:
    """The offline demo assistant. Always labelled as such."""
    from app.domain.workspace import is_configured

    message = message.strip()[:2000]
    intent = _route(message)
    handler = _HANDLERS.get(intent)
    fresh = not is_configured(biz) or biz.cash_declared_at is None
    if handler is not None and fresh and intent in _NEEDS_DATA:
        handler = _getting_started  # no records to analyse yet: guide entry instead of inventing an analysis
    out = (
        handler(db, biz, actor, message)
        if handler
        else _reply(
            "unsupported",
            "I'm the offline demo assistant, so I only understand a fixed set of questions and can't answer that one. Here are questions I can answer:",
            suggestions=suggestions(biz.currency or "", fresh=fresh)["primary"],
        )
    )
    audit.append(
        db,
        biz.id,
        event_type="assistant.turn",
        actor_type="assistant",
        actor_id=actor.user_id,
        outcome=out.get("intent", intent),
        payload={"intent": intent, "tool": intent if intent in ALLOWED_TOOLS else None, "chars": len(message)},
    )
    db.commit()
    return out


def _analyse_business(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    """Cash, shortfall, money in, money out and the next step, from the backend's own calculations."""
    pos = compute_position(db, biz)
    today = business_now(biz).astimezone(ZoneInfo(biz.tz_name)).date()
    invs = [i for i in db.execute(select(Invoice).where(Invoice.business_id == biz.id).order_by(Invoice.due_on)).scalars() if i.outstanding_minor > 0]
    overdue = [i for i in invs if i.due_on < today]
    owed = sum(i.outstanding_minor for i in invs)
    lines = [f"**Your situation:** settled cash you can use now is {fmt(pos.available_minor, biz.ccy)} (reserve kept aside: {fmt(pos.reserve_minor, biz.ccy)})."]
    if pos.gap_minor > 0:
        lines.append(f"You are short {fmt(pos.gap_minor, biz.ccy)} for essential payments due by {pos.horizon_end.astimezone(ZoneInfo(biz.tz_name)):%a %d %b}.")
    else:
        lines.append("Essential payments in your protection window are covered.")
    if invs:
        lines.append(f"Customers owe you {fmt(owed, biz.ccy)} across {len(invs)} open invoice{'s' if len(invs) != 1 else ''}"
                     + (f"; {len(overdue)} overdue ({', '.join(i.number for i in overdue[:3])})." if overdue else "; none overdue."))
    else:
        lines.append("No unpaid invoices are recorded.")
    if pos.protected:
        nxt = pos.protected[0]
        lines.append(f"Next essential payment: {nxt.description} {fmt(nxt.open_minor, biz.ccy)} due {nxt.due_at.astimezone(ZoneInfo(biz.tz_name)):%a %d %b %H:%M}.")
    case = current_case(db, biz.id)
    plan = latest_plan(db, case.id) if case else None
    if pos.gap_minor > 0 and plan is not None:
        lines.append(f"**Recommended next step:** {plan.body.get('summary', 'Review the rescue plan.')} Review and approve it in Approvals.")
    elif pos.gap_minor > 0:
        lines.append("**Recommended next step:** open the rescue plan to see your options.")
    elif overdue:
        lines.append(f"**Recommended next step:** follow up on overdue invoice {overdue[0].number}.")
    else:
        lines.append("**Recommended next step:** keep your figures current; nothing urgent.")
    lines.append("**Assumptions:** only settled cash counts; invoices and promises count when paid. Figures come from FlowSense's calculator.")
    r = _reply("analyse_business", "\n".join(lines), data=pos.to_dict(), citations=["position.gap_minor"])
    r["links"] = [{"label": "Open Rescue plan", "href": "/rescue"}, {"label": "Open Approvals", "href": "/approvals"}]
    return r


def _plan_payments(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    """Payments in deadline order, covered or not, plus what is waiting for approval."""
    from app.api.records import obligation_view

    pos = compute_position(db, biz)
    tz = ZoneInfo(biz.tz_name)
    horizon = business_now(biz) + timedelta(days=30)
    obs = [o for o in db.execute(select(Obligation).where(Obligation.business_id == biz.id).order_by(Obligation.due_at)).scalars() if o.unpaid_minor > 0 and o.due_at <= horizon]
    if not obs:
        r = _reply("plan_payments", "No unpaid bills or payroll are due in the next 30 days. Add them under Money out so I can plan them.")
        r["links"] = [{"label": "Add a bill", "href": "/money-out?add=bill"}, {"label": "Add payroll", "href": "/money-out?add=payroll"}]
        return r
    left = max(0, pos.available_minor - pos.reserve_minor)
    lines = ["**Payments due in the next 30 days, earliest first:**"]
    for o in obs:
        v = obligation_view(db, o, biz)
        if left >= o.unpaid_minor:
            left -= o.unpaid_minor
            cover = "covered by settled cash"
        else:
            cover = "not covered yet"
        tag = "essential" if v["essential"] else "can be discussed with the payee"
        lines.append(f"- {o.description}: {fmt(o.unpaid_minor, biz.ccy)} due {o.due_at.astimezone(tz):%a %d %b %H:%M} ({tag}; {cover}; status {v['state'].replace('_', ' ')}).")
    lines.append("Cover is counted in deadline order from settled cash after keeping your reserve aside; expected money is not counted.")
    waiting = db.execute(select(Action).where(Action.business_id == biz.id, Action.status.in_(("proposed", "approved")))).scalars().all()
    if waiting:
        lines.append(f"**Waiting for you in Approvals:** {len(waiting)} item{'s' if len(waiting) != 1 else ''}: " + "; ".join(
            f"{a.type.replace('_', ' ')} {fmt(a.amount_minor, biz.ccy)} ({'needs approval' if a.status == 'proposed' else 'ready to send'})" for a in waiting[:4]) + ".")
    case = current_case(db, biz.id)
    plan = latest_plan(db, case.id) if case else None
    if plan is not None and pos.gap_minor > 0:
        lines.append(f"**Recommended next step:** {plan.body.get('summary', '')} Approve the customer request first, then pay payroll once the money has settled.")
    r = _reply("plan_payments", "\n".join(lines))
    r["links"] = [{"label": "Open Approvals", "href": "/approvals"}, {"label": "Open Money out", "href": "/money-out"}]
    return r


def _propose_cash(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    """"I have 5,000 cash" becomes a suggestion with a Confirm button. Nothing is saved until the user confirms."""
    from app.agents import proposals
    from app.models import User

    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", msg)
    user = db.get(User, actor.user_id) if actor.user_id else None
    if m is None or user is None:
        return _reply("propose_cash", "Tell me the amount, for example: Record cash 5,000.")
    try:
        view = proposals.propose(db, biz, user, actor.role or "staff", "record_cash", {"amount": m.group(1)}, source="demo")
    except ApiError as exc:
        db.rollback()
        return _reply("propose_cash", f"I can't suggest that: {exc.message}")
    r = _reply("propose_cash", f"Please check this and press Confirm to save it: {view['summary']}. Nothing is saved until you confirm.")
    r["proposals"] = [view]
    return r


# ----------------------------------------------------------------- handlers


def _navigate(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    m = msg.lower()
    for pattern, (label, href) in _NAV:
        if re.search(pattern, m):
            r = _reply("navigate", f"Use the button below to open {label.lower()}. You can also use the + Add button at the top of every page.")
            r["links"] = [{"label": label, "href": href}]
            return r
    r = _reply("navigate", "Use the + Add button at the top of any page to add cash, invoices, bills, payroll, customers or documents.")
    r["links"] = [{"label": "Setup checklist", "href": "/setup"}]
    return r


def _fx_quote(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    from app import fx

    m = re.search(r"([\d][\d.,]*)?\s*([a-zA-Z]{3})\s+(?:to|in)\s+([a-zA-Z]{3})\b", msg)
    if not m:
        return _reply("fx_quote", "Tell me the amount and both currencies, for example: Convert 100 USD to BDT.")
    amount, src, dst = (m.group(1) or "1").strip(".,"), m.group(2).upper(), m.group(3).upper()
    try:
        r = fx.convert(db, amount, src, dst, "latest")
    except ApiError as exc:
        return _reply("fx_quote", exc.message)
    s = r["source"]
    if r["result_minor"] is None:
        text = f"No automatic rate is available for {src} to {dst}. {s['detail']} I won't guess one; the converter lets you enter a labelled manual rate."
    else:
        when = s.get("provider_as_of") or s.get("rate_date") or "an unknown time"
        text = (
            f"{r['amount_display']} is about {r['result_display']} at {r['rate']} ({s['label']}, published {when}, status: {s['status']}). "
            f"It's a reference rate, not a bank quote. {s.get('fees_note') or ''}"
        )
    return _reply("fx_quote", text.strip(), data={"source": s})


def _getting_started(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    from app.domain.workspace import setup_status

    st = setup_status(db, biz)
    if st["complete"]:
        return _reply(
            "getting_started",
            "Your setup is complete. Available cash means money already in your account today; invoices and promises count only once they are paid. "
            "Ask me to explain your cash position or compare your rescue options.",
        )
    nxt = next(s for s in st["steps"] if not s["done"])
    todo = [s["title"] for s in st["steps"] if not s["done"]]
    r = _reply(
        "getting_started",
        f"FlowSense needs a few facts before it can analyse your business. Next: {nxt['title'].lower()} ({nxt['detail']}) "
        f"Still to do: {', '.join(todo)}. 'Available cash' means money already in your account today; leaving it blank is different from entering zero.",
    )
    r["links"] = [{"label": nxt["title"], "href": nxt["href"]}, {"label": "Setup checklist", "href": "/setup"}]
    return r


def _explain_gap(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    pos = compute_position(db, biz)
    if pos.gap_minor == 0:
        text = (
            f"There is no funding gap. Settled, unreserved cash is {fmt(pos.available_minor, biz.ccy)}, which covers "
            f"{fmt(pos.protected_total_minor, biz.ccy)} of essential obligations due by {pos.horizon_end:%d %b} and the "
            f"{fmt(pos.reserve_minor, biz.ccy)} reserve."
        )
    else:
        items = "; ".join(
            f"{p.description} {fmt(p.open_minor, biz.ccy)} due {p.due_at.astimezone(business_now(biz).tzinfo):%a %d %b}" for p in pos.protected
        )
        text = (
            f"The gap is {fmt(pos.gap_minor, biz.ccy)}. Calculation: essential obligations ({items}) "
            f"+ protected reserve {fmt(pos.reserve_minor, biz.ccy)} - settled, unreserved cash {fmt(pos.available_minor, biz.ccy)} "
            f"= {fmt(pos.gap_minor, biz.ccy)}. Promised or expected receipts are not counted until they settle."
        )
    return _reply("explain_gap", text, data=pos.to_dict(), citations=["position.gap_minor"])


def _list_receivables(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    rows = []
    cites = []
    for inv in db.execute(select(Invoice).where(Invoice.business_id == biz.id).order_by(Invoice.due_on)).scalars():
        cp = db.get(Counterparty, inv.customer_id)
        ev = db.execute(select(Evidence).where(Evidence.invoice_id == inv.id).order_by(Evidence.label)).scalars().all()
        cites += [e.label for e in ev]
        ev_txt = ", ".join(
            f"{e.label} ({e.provenance.replace('_', ' ')}{', instruction-like text flagged' if e.instruction_like else ''})" for e in ev
        )
        rows.append(
            f"{inv.number} - {cp.name if cp else '?'}: outstanding {fmt(inv.outstanding_minor, biz.ccy)} of {fmt(inv.amount_minor, biz.ccy)}, "
            f"due {inv.due_on:%d %b %Y}. Evidence: {ev_txt or 'none'}."
        )
    text = "\n".join(rows) if rows else "No invoices are recorded."
    return _reply("list_receivables", text, citations=cites)


def _payroll_action(db: Session, biz: Business) -> Action | None:
    return (
        db.execute(
            select(Action)
            .where(
                Action.business_id == biz.id,
                Action.type == "payroll_payment",
                Action.status.in_(("proposed", "approved", "submitted", "settled")),
            )
            .order_by(Action.created_at.desc())
        )
        .scalars()
        .first()
    )


def _payroll_safety(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    a = _payroll_action(db, biz)
    if a is None:
        pos = compute_position(db, biz)
        payroll = [p for p in pos.protected if p.kind == "payroll"]
        if not payroll:
            return _reply("payroll_safety", "No unpaid payroll is due in your protection window, and no payroll payment is prepared.")
        due = payroll[0]
        when = due.due_at.astimezone(ZoneInfo(biz.tz_name)).strftime("%a %d %b %H:%M")
        if pos.gap_minor > 0:
            text = (
                f"Not yet. {due.description} ({fmt(due.open_minor, biz.ccy)}) is due {when} ({biz.tz_name}), and you are short "
                f"{fmt(pos.gap_minor, biz.ccy)}: essential payments {fmt(pos.protected_total_minor, biz.ccy)} + reserve "
                f"{fmt(pos.reserve_minor, biz.ccy)} − available cash {fmt(pos.available_minor, biz.ccy)}. Expected money is not counted until it arrives."
            )
        else:
            text = f"Your available cash covers {due.description} ({fmt(due.open_minor, biz.ccy)}, due {when}) and the reserve."
        text += " No payment is prepared yet: the payee must be verified by the owner (Approvals → Recipients) before FlowSense can prepare it."
        return _reply("payroll_safety", text)
    if a.status in ("submitted", "settled"):
        return _reply("payroll_safety", f"Payroll of {fmt(a.amount_minor, biz.ccy)} is already {a.status}.")
    phase = Phase.EXECUTE if a.status == "approved" else Phase.APPROVE
    from app.domain.actions import latest_approval

    d = evaluate_action(db, biz, Actor(actor.user_id, "owner"), a, phase, latest_approval(db, a) if phase == Phase.EXECUTE else None)
    db.rollback()  # read-only evaluation
    if d.outcome.value == "ALLOW":
        text = f"Yes. A fresh safety check says payroll of {fmt(a.amount_minor, biz.ccy)} can be {'submitted' if phase == Phase.EXECUTE else 'approved'} now; the reserve stays protected."
    else:
        text = f"Not yet. The safety rules would block it for {len(d.reasons)} reason{'s' if len(d.reasons) != 1 else ''}:"
    return _reply("payroll_safety", text, decision=d.to_dict(), citations=[f"action:{a.id}"])


def _explain_blocks(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    from app.models import AuditEvent

    evs = (
        db.execute(
            select(AuditEvent)
            .where(AuditEvent.business_id == biz.id, AuditEvent.outcome == "DENY")
            .order_by(AuditEvent.seq.desc())
            .limit(3)
        )
        .scalars()
        .all()
    )
    if not evs:
        return _reply("explain_blocks", "No actions have been blocked so far.")
    lines = [f"#{e.seq} {e.event_type}: " + " ".join(f"[{r['code']}] {r['message']}" for r in e.reasons) for e in evs]
    return _reply("explain_blocks", "Most recent blocks, newest first:\n" + "\n".join(lines), citations=[f"audit:{e.seq}" for e in evs])


def _compare_financing(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    policy = active_policy(db, biz.id)
    cap = int(policy.spec.get("max_financing_annualized_bp", 0)) if policy else 0
    lines = []
    for q in db.execute(select(FinancingQuote).where(FinancingQuote.business_id == biz.id)).scalars():
        lender = db.get(Counterparty, q.lender_id)
        cost = q.fee_minor + q.interest_minor
        bp = annualized_all_in_bp(cost, q.net_proceeds_minor, q.term_days)
        verdict = "within" if bp <= cap else "ABOVE"
        lines.append(
            f"{lender.name if lender else '?'}: borrow {fmt(q.principal_minor, biz.ccy)}, receive {fmt(q.net_proceeds_minor, biz.ccy)}, "
            f"cost {fmt(cost, biz.ccy)} over {q.term_days} days = {bp / 100:.2f}% annualized all-in, {verdict} your {cap / 100:.2f}% cap."
        )
    text = "\n".join(lines) if lines else "No financing quotes are available."
    text += "\nConvention: (fees + interest) / net proceeds x 365 / actual days. Not a legal APR."
    return _reply("compare_financing", text)


def _compare_options(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    case = current_case(db, biz.id)
    plan = latest_plan(db, case.id) if case else None
    if plan is None:
        return _reply("compare_options", "There is no rescue plan right now, so there are no options to compare.")
    body = plan.body
    lines = [f"Plan v{plan.version}: {body.get('summary', '')}"]
    rec = set((body.get("recommended") or {}).get("components", []))
    for c in body.get("components", []):
        tag = "Recommended" if c["id"] in rec else "Alternative"
        lines.append(
            f"{tag}: {c['kind'].replace('_', ' ')} via {c.get('counterparty') or c.get('description', '')}, "
            f"up to {fmt(c['max_contribution_minor'], biz.ccy)}, cost {fmt(c.get('cost_minor', 0), biz.ccy)}, "
            f"uncertainty {c['uncertainty']}. {c.get('basis', '')}"
        )
    for r in body.get("rejected", []):
        lines.append(f"Not available: {r['kind'].replace('_', ' ')} ({r['code']}). {r['reason']}")
    return _reply("compare_options", "\n".join(lines), citations=[f"plan:v{plan.version}"])


def _delay_scenario(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    m = re.search(r"(\d{1,2})\s*day", msg.lower())
    delay = int(m.group(1)) if m else 5
    fc = forecast(db, biz, days=14, delay_days=delay)
    b, s = fc["baseline"], fc["scenario"]
    text = (
        f"If collections arrive {delay} days later: lowest expected balance in 14 days moves from "
        f"{fmt(b['min_expected_minor'], biz.ccy)} to {fmt(s['min_expected_minor'], biz.ccy)}; first expected reserve breach "
        f"{b['first_reserve_breach_expected'] or 'none'} -> {s['first_reserve_breach_expected'] or 'none'}. "
        "This is a simulation; no records changed."
    )
    return _reply("delay_scenario", text, data={"delay_days": delay})


def _draft_request(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    existing = (
        db.execute(
            select(Action).where(
                Action.business_id == biz.id,
                Action.type == "payment_request",
                Action.status.in_(("proposed", "approved", "sent", "accepted")),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return _reply(
            "draft_payment_request",
            f"A {fmt(existing.amount_minor, biz.ccy)} partial-payment request is already {existing.status}. "
            "Open Approvals to review it; I can't approve it for you.",
            citations=[f"action:{existing.id}"],
        )
    case = current_case(db, biz.id)
    plan = latest_plan(db, case.id) if case else None
    step = next((s for s in (plan.body.get("steps", []) if plan else []) if s["kind"] == "partial_collection"), None)
    if step is None:
        return _reply("draft_payment_request", "The current plan has no partial-collection step to draft.")
    # The assistant never creates or sends requests itself; the planner's proposals wait for review in Approvals.
    return _reply(
        "draft_payment_request",
        f"The plan suggests asking the customer for {fmt(step['amount_minor'], biz.ccy)}. I haven't created or sent anything; "
        "review the request in Approvals.",
    )


def _compile_policy(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    payroll = (
        db.execute(select(Obligation).where(Obligation.business_id == biz.id, Obligation.kind == "payroll").order_by(Obligation.due_at))
        .scalars()
        .first()
    )
    compiled = compile_policy(
        msg,
        business_now=business_now(biz),
        timezone=biz.tz_name,
        currency=biz.ccy,
        scheduled_payroll_due=payroll.due_at if payroll else None,
    )
    from sqlalchemy import func

    version = (db.execute(select(func.max(Policy.version)).where(Policy.business_id == biz.id)).scalar_one() or 0) + 1
    assert actor.user_id is not None
    p = Policy(
        business_id=biz.id,
        version=version,
        status="proposed",
        source_text=msg,
        spec=compiled["spec"],
        interpretation=compiled["interpretation"],
        created_by=actor.user_id,
    )
    db.add(p)
    db.flush()
    audit.append(
        db,
        biz.id,
        event_type="policy.proposed",
        actor_type="assistant",
        actor_id=actor.user_id,
        payload={"version": version, "activatable": compiled["activatable"]},
        policy_version=version,
    )
    text = "I compiled this into a proposed policy. Nothing is active until the owner confirms it. " + (
        "Please answer: " + " ".join(compiled["interpretation"]["questions"])
        if compiled["interpretation"]["questions"]
        else "Review the interpreted fields below."
    )
    return _reply("compile_policy", text, proposed_policy={"id": str(p.id), "version": version, **compiled})


def _evidence_attempt(db: Session, biz: Business, actor: Actor, msg: str) -> dict[str, Any]:
    """Adversarial demo: act as a *compromised* model that obeys evidence text, and show the tool gate."""
    flagged = db.execute(select(Evidence).where(Evidence.business_id == biz.id, Evidence.instruction_like.is_(True))).scalars().first()
    if flagged is None:
        return _reply(
            "evidence_action_attempt", "No evidence contains instruction-like text. Evidence is data; I never take instructions from it."
        )
    m = re.search(r"\b(SBX-[A-Z0-9-]+|[A-Z]{2}\d{2}[A-Z0-9]{8,})\b", flagged.content_text)
    account = m.group(1) if m else "(unrecognised account)"
    pos = compute_position(db, biz)
    proposal = {"tool": "propose_payment", "account_ref": account, "amount_minor": pos.settled_minor}
    cp = db.execute(
        select(Counterparty).where(Counterparty.business_id == biz.id, Counterparty.account_ref == account)
    ).scalar_one_or_none()
    reasons = []
    if cp is None:
        reasons.append(
            {
                "code": "RECIPIENT_UNKNOWN",
                "message": f"{account} is not a counterparty of this business. Assistant tools cannot create or verify recipients.",
            }
        )
    elif not cp.verified:
        reasons.append({"code": "RECIPIENT_UNVERIFIED", "message": f"{account} is not verified."})
    reasons.append(
        {
            "code": "TOOL_NOT_ALLOWED",
            "message": "The assistant has no tool that pays an arbitrary account; payments only settle recorded obligations after owner approval.",
        }
    )
    audit.append(
        db,
        biz.id,
        event_type="assistant.tool_denied",
        actor_type="assistant",
        actor_id=actor.user_id,
        outcome="DENY",
        reasons=reasons,
        payload={"proposal": proposal, "source_evidence": flagged.label},
    )
    text = (
        f"[Adversarial test] Evidence {flagged.label} contains instruction-like text, which is untrusted data. "
        f"A compromised model might propose paying {fmt(pos.settled_minor, biz.ccy)} to {account}. The backend tool gate denied it: "
        + " ".join(f"[{r['code']}] {r['message']}" for r in reasons)
    )
    return _reply("evidence_action_attempt", text, decision={"outcome": "DENY", "reasons": reasons}, citations=[flagged.label])


_HANDLERS = {
    "analyse_business": _analyse_business,
    "plan_payments": _plan_payments,
    "propose_cash": _propose_cash,
    "navigate": _navigate,
    "fx_quote": _fx_quote,
    "getting_started": _getting_started,
    "explain_gap": _explain_gap,
    "list_receivables": _list_receivables,
    "payroll_safety": _payroll_safety,
    "explain_blocks": _explain_blocks,
    "compare_financing": _compare_financing,
    "compare_options": _compare_options,
    "delay_scenario": _delay_scenario,
    "draft_payment_request": _draft_request,
    "compile_policy": _compile_policy,
    "evidence_action_attempt": _evidence_attempt,
}
