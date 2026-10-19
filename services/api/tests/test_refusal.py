"""Tier 2: customer / financing refusals are signed events that invalidate plan assumptions."""

from __future__ import annotations

from sqlalchemy import select

from app.domain.position import compute_position
from app.ledger import service as ledger
from app.models import Action, Business, FinancingQuote
from app.planning.rescue import planner_input
from app.reconciliation import ingest as recon
from app.sandbox import simulator
from tests.conftest import find_action, seed

CNY = 100


def test_customer_refusal_replans_without_customer_and_becomes_infeasible(as_owner, as_staff):
    seed("customer_refusal")
    api = as_owner()
    req = find_action(api, "payment_request", "sent")
    assert api.get("/api/rescue").json()["case"]["status"] == "AWAITING_SETTLEMENT"

    # Simulator controls are owner-only.
    staff = as_staff()
    assert staff.post("/api/sandbox/simulate/customer-refuse", {"action_id": req["id"]}).status_code == 403

    r = api.post("/api/sandbox/simulate/customer-refuse", {"action_id": req["id"]})
    assert r.status_code == 200 and r.json()["outcome"] == "refused"

    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 50_000 * CNY  # a refusal never moves cash
    assert d["position"]["gap_minor"] == 40_000 * CNY
    assert [a["status"] for a in api.get("/api/actions").json() if a["id"] == req["id"]] == ["refused"]

    case = api.get("/api/rescue").json()["case"]
    assert case["status"] == "INFEASIBLE"
    tos = [t["to"] for t in case["transitions"]]
    assert tos[-2:] == ["AT_RISK", "INFEASIBLE"]
    latest = case["plans"][0]
    assert latest["feasible"] is False and latest["trigger"].startswith("request.refused")
    assert "CUSTOMER_REFUSED" in {x["code"] for x in latest["body"]["rejected"]}
    assert not [c for c in latest["body"]["components"] if c["kind"] == "partial_collection"]
    # The refused customer is not re-proposed.
    assert not [a for a in api.get("/api/actions").json() if a["type"] == "payment_request" and a["status"] == "proposed"]

    # Payroll stays blocked; a second refusal (new event id) changes nothing.
    payroll = find_action(api, "payroll_payment", "proposed")
    assert api.post(f"/api/actions/{payroll['id']}/approve").status_code == 409
    assert api.post("/api/sandbox/simulate/customer-refuse", {"action_id": req["id"]}).json()["outcome"] == "ignored_state"
    assert api.get("/api/audit/verify").json()["valid"] is True
    assert api.get("/api/ledger").json()["balanced"] is True


def test_refusal_of_unsent_request_is_ignored(db):
    biz_id = seed("primary")
    proposed = db.execute(select(Action).where(Action.business_id == biz_id, Action.type == "payment_request")).scalars().first()
    # A proposed request was never sent to the customer, so there is nothing to refuse.
    assert simulator.customer_refuses(db, biz_id, proposed.id)["outcome"] == "ignored_state"
    db.refresh(proposed)
    assert proposed.status == "proposed"


def test_quote_refused_event_removes_quote_without_cash_effect(db):
    biz_id = seed("primary")
    q = db.execute(select(FinancingQuote).where(FinancingQuote.business_id == biz_id)).scalars().first()
    raw = simulator.build_event(biz_id, "quote.refused", {"quote_id": str(q.id), "reason": "Lender withdrew"})
    assert recon.ingest(db, raw, recon.sign(raw))["outcome"] == "refused"
    db.expire_all()
    assert db.get(FinancingQuote, q.id).status == "refused"
    assert ledger.settled_cash(db, biz_id) == 50_000 * CNY
    biz = db.get(Business, biz_id)
    assert planner_input(db, biz, compute_position(db, biz)).quotes == []


def test_refused_request_cannot_be_retried_into_money_movement(as_owner):
    from sqlalchemy import func

    from app.db import get_sessionmaker
    from app.models import SandboxEvent, SimBankOp, Transfer
    from tests.conftest import execute

    biz_id = seed("customer_refusal")
    api = as_owner()
    req = find_action(api, "payment_request", "sent")
    assert api.post("/api/sandbox/simulate/customer-refuse", {"action_id": req["id"]}).json()["outcome"] == "refused"

    # Retries through every path stay refused and move nothing.
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 409
    r = execute(api, req["id"])
    assert r.status_code == 409 and "ACTION_STATE_INVALID" in {x["code"] for x in r.json()["error"]["reasons"]}
    assert api.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]}).json()["outcome"] == "ignored_state"
    r = api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    assert r.status_code == 409 and r.json()["error"]["code"] == "REQUEST_NOT_OPEN"
    with get_sessionmaker()() as s:
        assert (
            s.execute(select(func.count(Transfer.id)).where(Transfer.business_id == biz_id, Transfer.direction == "out")).scalar_one() == 0
        )
        assert s.execute(select(func.count(SimBankOp.provider_ref)).where(SimBankOp.business_id == biz_id)).scalar_one() == 0
        refused_events = s.execute(
            select(func.count(SandboxEvent.id)).where(
                SandboxEvent.business_id == biz_id, SandboxEvent.event_type == "request.refused", SandboxEvent.outcome == "refused"
            )
        ).scalar_one()
        assert refused_events == 1
    assert [a["status"] for a in api.get("/api/actions").json() if a["id"] == req["id"]] == ["refused"]

    # The audit record explains the decision without secrets.
    ev = api.get("/api/audit?q=request.refused").json()[0]
    assert ev["payload"]["reason"].startswith("Customer declined") and ev["payload"]["amount_minor"] == 40_000 * CNY
    assert not any(k in str(ev).lower() for k in ("password", "secret", "csrf", "token"))


def test_permitted_flow_still_succeeds_after_a_refusal(as_owner):
    """A lender refusal must not block the unrelated, permitted collection + payroll path."""
    from tests.conftest import execute

    biz_id = seed("primary")
    with __import__("app.db", fromlist=["get_sessionmaker"]).get_sessionmaker()() as s:
        q = s.execute(select(FinancingQuote).where(FinancingQuote.business_id == biz_id)).scalars().first()
        raw = simulator.build_event(biz_id, "quote.refused", {"quote_id": str(q.id), "reason": "Lender withdrew"})
        assert recon.ingest(s, raw, recon.sign(raw))["outcome"] == "refused"
    api = as_owner()
    req = find_action(api, "payment_request", "proposed")
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    assert execute(api, req["id"]).status_code == 200
    assert api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]}).json()["outcome"] == "settled"
    pay = find_action(api, "payroll_payment", "proposed")
    assert api.post(f"/api/actions/{pay['id']}/approve").status_code == 200
    assert execute(api, pay["id"]).status_code == 200
    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 10_000 * CNY and d["case"]["status"] == "RESOLVED"
