"""The approvals list must show safety checks against the current balances, not stale ones."""

from __future__ import annotations

from tests.conftest import find_action, seed

CNY = 100


def test_proposed_action_check_is_recomputed_after_cash_changes(as_owner):
    seed("partial_settlement")
    api = as_owner()
    payroll = find_action(api, "payroll_payment", "proposed")
    before = " ".join(w["message"] for w in payroll["current_check"]["warnings"])
    assert "USD 50,000.00 of settled" in before

    req = find_action(api, "payment_request", "accepted")
    assert api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"], "amount_minor": 20_000 * CNY}).status_code == 200

    payroll = find_action(api, "payroll_payment", "proposed")
    after = " ".join(w["message"] for w in payroll["current_check"]["warnings"])
    assert "USD 70,000.00 of settled" in after and "USD 50,000.00" not in after
    # The stored decision stays as history; only proposed actions get a live check.
    assert payroll["last_decision"] is not None
    settled = [a for a in api.get("/api/actions").json() if a["status"] != "proposed"]
    assert settled and all(a["current_check"] is None for a in settled)
