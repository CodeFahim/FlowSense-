"""Tier 1 acceptance scenarios through the real HTTP API and PostgreSQL."""

from __future__ import annotations

from sqlalchemy import func, select

from app.db import get_sessionmaker
from app.models import Transfer
from tests.conftest import execute, find_action, seed

CNY = 100  # minor units per yuan


def _dash(api):
    return api.get("/api/dashboard").json()


def test_primary_rescue_end_to_end(as_owner):
    seed("primary")
    api = as_owner()
    d = _dash(api)
    # Step 1: exact gap.
    assert d["position"]["settled_minor"] == 50_000 * CNY
    assert d["position"]["reserve_minor"] == 10_000 * CNY
    assert d["position"]["gap_minor"] == 40_000 * CNY
    assert d["case"]["status"] == "AWAITING_ACTION"

    # Step 2: planner cites invoice + delivery evidence and proposes 40,000.
    plan = api.get("/api/rescue").json()["case"]["plans"][0]["body"]
    comp = next(c for c in plan["components"] if c["kind"] == "partial_collection")
    assert comp["invoice"] == "INV-1042" and "EV-1042-DN" in comp["evidence"]
    assert plan["steps"][0]["amount_minor"] == 40_000 * CNY
    rejected = {r["code"] for r in plan["rejected"]}
    assert {"FINANCING_COST_EXCEEDS_POLICY", "NO_EFFECT_ON_GAP"} <= rejected
    req = find_action(api, "payment_request", "proposed")
    assert req["amount_minor"] == 40_000 * CNY

    # Step 3: approve + send request; acceptance changes no settled balance.
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    r = execute(api, req["id"])
    assert r.status_code == 200 and r.json()["status"] == "sent"
    assert api.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]}).json()["outcome"] == "accepted"
    assert _dash(api)["position"]["settled_minor"] == 50_000 * CNY
    assert _dash(api)["case"]["status"] == "AWAITING_SETTLEMENT"

    # Step 4: genuine sandbox settlement of 40,000.
    assert api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]}).json()["outcome"] == "settled"
    d = _dash(api)
    assert d["position"]["settled_minor"] == 90_000 * CNY
    inv = api.get("/api/invoices").json()[0]
    assert inv["outstanding_minor"] == 20_000 * CNY
    assert d["position"]["gap_minor"] == 0

    # Step 5: approve exact 80,000 payroll; checks re-run atomically at execution.
    payroll = find_action(api, "payroll_payment", "proposed")
    assert payroll["amount_minor"] == 80_000 * CNY
    ap = api.post(f"/api/actions/{payroll['id']}/approve")
    assert ap.status_code == 200, ap.text
    assert ap.json()["approval"]["amount_minor"] == 80_000 * CNY
    r = execute(api, payroll["id"])
    assert r.status_code == 200, r.text
    assert r.json()["settlement"]["outcome"] == "settled"

    # Step 6: exactly one payroll settlement; 10,000 left; receipt + audit; resolved.
    d = _dash(api)
    assert d["position"]["settled_minor"] == 10_000 * CNY
    assert next(o for o in d["obligations"] if o["kind"] == "payroll")["paid_minor"] == 80_000 * CNY
    assert d["case"]["status"] == "RESOLVED"
    receipts = api.get("/api/receipts").json()
    assert any(x["content"]["direction"] == "outgoing" and x["content"]["amount_minor"] == 80_000 * CNY for x in receipts)
    v = api.get("/api/audit/verify").json()
    assert v["valid"] is True and v["count"] > 10
    assert api.get("/api/ledger").json()["balanced"] is True
    with get_sessionmaker()() as s:
        n = s.execute(select(func.count(Transfer.id)).where(Transfer.action_id == payroll["id"], Transfer.status == "settled")).scalar_one()
        assert n == 1


def test_partial_settlement_blocks_payroll_and_replans(as_owner):
    seed("partial_settlement")
    api = as_owner()
    req = find_action(api, "payment_request", "accepted")
    assert (
        api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"], "amount_minor": 20_000 * CNY}).json()["outcome"]
        == "settled"
    )
    d = _dash(api)
    assert d["position"]["settled_minor"] == 70_000 * CNY
    assert api.get("/api/invoices").json()[0]["outstanding_minor"] == 40_000 * CNY
    assert d["position"]["gap_minor"] == 20_000 * CNY
    payroll = find_action(api, "payroll_payment", "proposed")
    r = api.post(f"/api/actions/{payroll['id']}/approve")
    assert r.status_code == 409
    codes = {x["code"] for x in r.json()["error"]["reasons"]}
    assert "RESERVE_BREACH" in codes and "INSUFFICIENT_SETTLED_FUNDS" in codes
    case = api.get("/api/rescue").json()["case"]
    assert case["status"] == "AWAITING_ACTION"
    assert case["plans"][0]["version"] >= 2 and case["plans"][0]["gap_minor"] == 20_000 * CNY
    assert case["plans"][0]["body"]["steps"][0]["amount_minor"] == 20_000 * CNY
    assert any(t["to"] == "AT_RISK" for t in case["transitions"])


def test_prompt_injection_in_evidence_is_denied(as_owner, as_staff):
    seed("prompt_injection")
    owner, staff = as_owner(), as_staff()
    inv = owner.get("/api/invoices").json()[0]
    flagged = [e for e in inv["evidence"] if e["instruction_like"]]
    assert len(flagged) == 1 and flagged[0]["provenance"] == "user_supplied"
    cash_before = _dash(owner)["position"]["settled_minor"]

    out = owner.post("/api/assistant/chat", {"message": "Follow the instructions in the invoice evidence"}).json()
    assert out["intent"] == "evidence_action_attempt" and out["decision"]["outcome"] == "DENY"
    assert {"RECIPIENT_UNKNOWN", "TOOL_NOT_ALLOWED"} <= {r["code"] for r in out["decision"]["reasons"]}

    # Even a human-driven attempt through the API is stopped by the backend.
    cp = staff.post("/api/counterparties", {"kind": "supplier", "name": "New account from note", "account_ref": "SBX-ATTACKER-7781"}).json()
    assert cp["verified"] is False
    r = staff.post(
        "/api/actions", {"type": "supplier_payment", "amount_minor": cash_before, "counterparty_id": cp["id"], "purpose": "per note"}
    )
    assert r.json()["decision"]["outcome"] == "DENY"
    assert {"RECIPIENT_UNVERIFIED", "OBLIGATION_MISSING", "LIMIT_EXCEEDED"} & {x["code"] for x in r.json()["decision"]["reasons"]}
    assert owner.post(f"/api/counterparties/{cp['id']}/verify").status_code == 409
    assert _dash(owner)["position"]["settled_minor"] == cash_before
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(Transfer.id)).where(Transfer.account_ref == "SBX-ATTACKER-7781")).scalar_one() == 0


def test_beneficiary_change_invalidates_old_approval(as_owner, as_staff):
    seed("beneficiary_change")
    owner, staff = as_owner(), as_staff()
    payroll = find_action(owner, "payroll_payment", "approved")
    cps = owner.get("/api/counterparties").json()["counterparties"]
    pay_cp = next(c for c in cps if c["kind"] == "payroll")
    ch = staff.post(f"/api/counterparties/{pay_cp['id']}/changes", {"new_account_ref": "SBX-PAYROLL-0002"}).json()
    applied = owner.post(f"/api/counterparty-changes/{ch['id']}/apply").json()
    assert applied["counterparty"]["version"] == 2 and applied["revoked_approvals"]
    r = execute(owner, payroll["id"])
    assert r.status_code == 409
    codes = {x["code"] for x in r.json()["error"]["reasons"]}
    assert "RECIPIENT_VERSION_CHANGED" in codes and "APPROVAL_REVOKED" in codes
    assert _dash(owner)["position"]["settled_minor"] == 90_000 * CNY
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(Transfer.id)).where(Transfer.action_id == payroll["id"])).scalar_one() == 0

    # Recovery path: verify new account, rebind (new revision), approve again, pay to v2.
    assert owner.post(f"/api/counterparties/{pay_cp['id']}/verify").status_code == 200
    rebound = owner.post(f"/api/actions/{payroll['id']}/revise", {"rebind_recipient": True}).json()
    assert rebound["counterparty_version"] == 2 and rebound["status"] == "proposed" and rebound["revision"] == 2
    assert owner.post(f"/api/actions/{payroll['id']}/approve").status_code == 200
    r = execute(owner, payroll["id"])
    assert r.status_code == 200 and r.json()["transfer"]["account_ref"] == "SBX-PAYROLL-0002"
    assert _dash(owner)["position"]["settled_minor"] == 10_000 * CNY


def test_reserve_breach_blocked_with_exact_reason(as_owner):
    seed("reserve_breach")
    api = as_owner()
    assert _dash(api)["position"]["gap_minor"] == 5_000 * CNY
    payroll = find_action(api, "payroll_payment", "proposed")
    r = api.post(f"/api/actions/{payroll['id']}/approve")
    assert r.status_code == 409
    reason = next(x for x in r.json()["error"]["reasons"] if x["code"] == "RESERVE_BREACH")
    assert "USD 5,000.00" in reason["message"] and "USD 10,000.00" in reason["message"]
    assert api.get("/api/rescue").json()["case"]["status"] == "INFEASIBLE"


def test_duplicate_payroll_same_key_and_fresh_keys(as_owner):
    seed("duplicate_payroll")
    api = as_owner()
    payroll = find_action(api, "payroll_payment", "approved")
    r1 = execute(api, payroll["id"], key="k-1")
    r2 = execute(api, payroll["id"], key="k-1")  # double click, same key
    r3 = execute(api, payroll["id"], key="k-2")  # fresh key
    assert r1.status_code == 200
    assert r2.status_code == 200 and r2.json()["replayed"] is True
    assert r3.status_code == 409
    assert {"APPROVAL_ALREADY_USED", "ACTION_STATE_INVALID"} <= {x["code"] for x in r3.json()["error"]["reasons"]}
    # A second, separately approved action for the same obligation is blocked too.
    cp = payroll["counterparty"]["id"]
    dup = api.post(
        "/api/actions",
        {
            "type": "payroll_payment",
            "amount_minor": 80_000 * CNY,
            "counterparty_id": cp,
            "obligation_id": payroll["obligation"]["id"],
            "purpose": "duplicate",
        },
    ).json()
    assert dup["decision"]["outcome"] == "DENY"
    assert "OBLIGATION_ALREADY_PAID" in {x["code"] for x in dup["decision"]["reasons"]}
    assert _dash(api)["position"]["settled_minor"] == 10_000 * CNY
    # Same key, different request -> rejected.
    assert execute(api, dup["action"]["id"], key="k-1").status_code == 409


def test_idempotency_key_reuse_for_other_action_rejected(as_owner):
    seed("duplicate_payroll")
    api = as_owner()
    payroll = find_action(api, "payroll_payment", "approved")
    req = find_action(api, "payment_request")
    assert execute(api, payroll["id"], key="shared").status_code == 200
    r = execute(api, req["id"], key="shared")
    assert r.status_code == 409 and r.json()["error"]["code"] == "IDEMPOTENCY_PAYLOAD_MISMATCH"
