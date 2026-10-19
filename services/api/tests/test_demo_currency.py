"""The demo can be built in any currency the owner chooses: same story (50,000 cash, 80,000 payroll, 10,000
reserve, gap 40,000), books really in that currency, amounts scaled exactly to its decimal places, and the
full rescue still works. The choice carries over to later resets; a scenario with its own currency keeps it."""

from __future__ import annotations

import pytest

from tests.conftest import execute, find_action, seed


def _reset(api, scenario: str = "usd_primary", currency: str | None = None):
    body = {"scenario": scenario, "confirm": "RESET"} | ({"currency": currency} if currency else {})
    r = api.post("/api/demo/reset", body)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.parametrize(("currency", "unit"), [("JPY", 1), ("KWD", 1000), ("BDT", 100), ("EUR", 100)])
def test_demo_books_in_the_chosen_currency_and_the_rescue_still_works(as_owner, currency, unit):
    seed("usd_primary")
    api = as_owner()
    me = _reset(api, currency=currency)
    api.csrf = me["csrf_token"]
    assert me["business"]["currency"] == currency
    d = api.get("/api/dashboard").json()
    assert d["currency"] == currency
    assert d["position"]["settled_minor"] == 50_000 * unit and d["position"]["gap_minor"] == 40_000 * unit
    assert d["position"]["reserve_minor"] == 10_000 * unit
    plan = api.get("/api/rescue").json()["case"]["plans"][0]["body"]
    assert {r["code"] for r in plan["rejected"]} >= {"FINANCING_COST_EXCEEDS_POLICY"}  # 39.70% still above the 8% cap
    assert any("39.70%" in r["reason"] for r in plan["rejected"])
    # The whole rescue, in that currency.
    req = find_action(api, "payment_request", "proposed")
    assert req["currency"] == currency and req["amount_minor"] == 40_000 * unit
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    assert execute(api, req["id"]).status_code == 200
    api.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]})
    api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    payroll = find_action(api, "payroll_payment", "proposed")
    assert api.post(f"/api/actions/{payroll['id']}/approve").status_code == 200
    assert execute(api, payroll["id"]).status_code == 200
    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 10_000 * unit and d["case"]["status"] == "RESOLVED"
    assert {r["content"]["currency"] for r in api.get("/api/receipts").json()} == {currency}
    assert api.get("/api/ledger").json()["balanced"] is True


def test_chosen_currency_carries_over_and_fixed_scenarios_keep_theirs(as_owner):
    seed("usd_primary")
    api = as_owner()
    api.csrf = _reset(api, currency="JPY")["csrf_token"]
    me = _reset(api, "reserve_breach")  # no currency given: keeps JPY
    api.csrf = me["csrf_token"]
    assert me["business"]["currency"] == "JPY"
    assert api.get("/api/dashboard").json()["position"]["settled_minor"] == 85_000
    sc = {s["key"]: s for s in api.get("/api/demo/scenarios").json()["scenarios"]}
    assert sc["usd_primary"]["currency"] == "JPY" and sc["primary"]["currency"] == "CNY" and sc["primary"]["fixed_currency"] is True
    me = _reset(api, "primary", currency="JPY")  # the regional sample states CNY and keeps it
    api.csrf = me["csrf_token"]
    assert me["business"]["currency"] == "CNY"
    me = _reset(api, "usd_primary")  # after a fixed-currency scenario, the default is USD again
    assert me["business"]["currency"] == "USD"


def test_unknown_currency_or_scenario_is_refused(as_owner):
    seed("usd_primary")
    api = as_owner()
    r = api.post("/api/demo/reset", {"scenario": "usd_primary", "confirm": "RESET", "currency": "XXZ"})
    assert r.status_code == 422 and r.json()["error"]["code"] == "CURRENCY_UNSUPPORTED"
    r = api.post("/api/demo/reset", {"scenario": "nope", "confirm": "RESET"})
    assert r.status_code == 422
    # Nothing was reset by the refused requests.
    assert api.get("/api/dashboard").json()["currency"] == "USD"
