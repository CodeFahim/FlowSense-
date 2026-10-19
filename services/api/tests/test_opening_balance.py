"""An opening balance (or a later correction) is existing cash, not money received.

It stays in the settled balance, but monthly "money in", "net" and the dashboard's "money received this
month" count only real receipts. Every month reconciles: opening + in - out + adjustments = closing.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.planning.history import monthly
from tests.test_agent import _user

USD = 100


def _setup(api, cash: str = "20,000"):
    api.c.put(
        "/api/business/profile",
        json={"name": "Dhaka Traders", "currency": "USD", "timezone": "Asia/Dhaka", "confirm_currency_and_timezone": True},
        headers={"X-CSRF-Token": api.csrf},
    )
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    assert api.post("/api/cash/declare", {"amount": cash, "currency": "USD", "as_of": today.isoformat()}).status_code == 200
    return today


def _month(api):
    h = api.get("/api/cashflow/history").json()
    return h, h["months"][0]


def _reconciles(m):
    return m["opening_balance_minor"] + m["money_in_minor"] - m["money_out_minor"] + m["adjustments_minor"] == m["closing_balance_minor"]


def test_opening_balance_only_is_not_money_received():
    api = _user()
    _setup(api)
    h, m = _month(api)
    assert h["settled_balance_minor"] == 20_000 * USD
    assert m["money_in_minor"] == 0 and m["net_minor"] == 0 and m["adjustments_minor"] == 20_000 * USD and _reconciles(m)
    assert h["rows"][0]["kind"] == "adjustment" and h["rows"][0]["source"] == "Declared by you (not bank-verified)"
    assert "not bank-verified" in h["balance_source"] and "No real bank" in h["balance_source"]
    dash = api.get("/api/dashboard").json()
    assert dash["this_month"]["money_in_minor"] == 0 and dash["this_month"]["adjustments_minor"] == 20_000 * USD


def test_opening_balance_plus_customer_payment_counts_only_the_payment():
    api = _user()
    today = _setup(api)
    inv = api.post("/api/invoices", {"new_customer": "Hotel Rosa", "number": "DT-1", "amount": "3,000", "currency": "USD",
                                     "issued_on": today.isoformat(), "due_on": today.isoformat()})
    assert inv.status_code == 200, inv.text
    pay = api.post(f"/api/invoices/{inv.json()['id']}/payments", {"amount": "1,200", "currency": "USD"})
    assert pay.status_code == 200, pay.text
    h, m = _month(api)
    assert h["settled_balance_minor"] == 21_200 * USD
    assert m["money_in_minor"] == 1_200 * USD and m["net_minor"] == 1_200 * USD and m["adjustments_minor"] == 20_000 * USD
    assert _reconciles(m)
    assert [r["kind"] for r in h["rows"]] == ["receipt", "adjustment"]
    assert api.get("/api/dashboard").json()["this_month"]["money_in_minor"] == 1_200 * USD  # dashboard = Money page


def test_repeated_balance_corrections_never_become_income():
    api = _user()
    today = _setup(api)
    for amount in ("25,000", "18,000", "18,000", "30,000"):
        assert api.post("/api/cash/declare", {"amount": amount, "currency": "USD", "as_of": today.isoformat()}).status_code == 200
    h, m = _month(api)
    assert h["settled_balance_minor"] == 30_000 * USD
    assert m["money_in_minor"] == 0 and m["money_out_minor"] == 0 and m["net_minor"] == 0
    assert m["adjustments_minor"] == 30_000 * USD and _reconciles(m)
    assert all(r["kind"] == "adjustment" for r in h["rows"])
    # A same-amount declaration posts nothing; the running balance still follows the ledger.
    assert len(h["rows"]) == 4
    bal = 0
    for r in reversed(h["rows"]):
        bal += r["money_in_minor"] - r["money_out_minor"]
        assert r["balance_minor"] == bal


def test_reporting_periods_carry_opening_balance_forward():
    rows = [
        {"date": "2026-09-03", "kind": "adjustment", "money_in_minor": 20_000, "money_out_minor": 0, "balance_minor": 20_000},
        {"date": "2026-09-20", "kind": "receipt", "money_in_minor": 5_000, "money_out_minor": 0, "balance_minor": 25_000},
        {"date": "2026-10-02", "kind": "payment", "money_in_minor": 0, "money_out_minor": 7_000, "balance_minor": 18_000},
        {"date": "2026-10-05", "kind": "adjustment", "money_in_minor": 0, "money_out_minor": 1_000, "balance_minor": 17_000},
        {"date": "2026-10-09", "kind": "receipt", "money_in_minor": 2_500, "money_out_minor": 0, "balance_minor": 19_500},
    ]
    sep, oct_ = monthly(rows)
    assert sep == {"month": "2026-09", "opening_balance_minor": 0, "money_in_minor": 5_000, "money_out_minor": 0, "net_minor": 5_000,
                   "adjustments_minor": 20_000, "closing_balance_minor": 25_000, "count": 2}
    assert oct_ == {"month": "2026-10", "opening_balance_minor": 25_000, "money_in_minor": 2_500, "money_out_minor": 7_000, "net_minor": -4_500,
                    "adjustments_minor": -1_000, "closing_balance_minor": 19_500, "count": 3}
    assert _reconciles(sep) and _reconciles(oct_)


def test_ledger_reconciles_with_history_after_settlement(as_owner):
    """Sample fixture: the opening balance is an adjustment; a settled customer payment and payroll are real flows."""
    from tests.conftest import execute, find_action, seed

    seed("usd_primary")
    api = as_owner()
    h0 = api.get("/api/cashflow/history").json()
    assert all(r["kind"] == "adjustment" for r in h0["rows"]) and h0["months"][0]["money_in_minor"] == 0
    assert {r["category"] for r in h0["rows"]} == {"Opening balance"}  # not "Customer payment"
    req = find_action(api, "payment_request", "proposed")
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    assert execute(api, req["id"]).status_code == 200
    api.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]})
    api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    h = api.get("/api/cashflow/history").json()
    total_in = sum(m["money_in_minor"] for m in h["months"])
    total_out = sum(m["money_out_minor"] for m in h["months"])
    total_adj = sum(m["adjustments_minor"] for m in h["months"])
    assert total_in == 40_000 * USD and total_out == 0 and total_adj == 50_000 * USD
    assert total_in - total_out + total_adj == h["settled_balance_minor"] == 90_000 * USD
    assert api.get("/api/dashboard").json()["position"]["settled_minor"] == h["settled_balance_minor"]


def test_what_if_expense_is_an_outflow_and_lowers_the_forecast_by_its_amount(as_owner):
    """Report: a USD 1,000 surprise expense was listed as "in ... -1,000". It is money out; the maths is unchanged."""
    from tests.conftest import seed

    seed("usd_primary")
    api = as_owner()
    base = api.get("/api/forecast?days=14").json()
    fc = api.get(f"/api/forecast?days=14&emergency_minor={1_000 * USD}&emergency_day=1").json()
    assert all(f["amount_minor"] > 0 for f in fc["scenario"]["inflows"])
    assert not any("Emergency" in f["label"] for f in fc["scenario"]["inflows"])
    em = [f for f in fc["scenario"]["outflows"] if f["label"] == "Emergency expense (what-if)"]
    assert len(em) == 1 and em[0]["amount_minor"] == 1_000 * USD
    assert fc["scenario"]["min_expected_minor"] == fc["baseline"]["min_expected_minor"] - 1_000 * USD
    assert fc["baseline"]["min_expected_minor"] == base["baseline"]["min_expected_minor"]
    last_b, last_s = fc["baseline"]["points"][-1], fc["scenario"]["points"][-1]
    assert last_s["settled_path_minor"] == last_b["settled_path_minor"] - 1_000 * USD
    assert last_s["expected_path_minor"] == last_b["expected_path_minor"] - 1_000 * USD


def test_overdue_customer_date_is_not_called_next_due(as_owner):
    """Sample clock 12 Oct 2026; INV-1042 was due 9 Oct: it is overdue, not "next due"."""
    from tests.conftest import seed

    seed("usd_primary")
    api = as_owner()
    cust = api.get("/api/customers").json()["customers"]
    c = next(x for x in cust if x["overdue_minor"] > 0)
    assert c["oldest_overdue"] is not None and c["oldest_overdue"] < "2026-10-12"
    assert c["next_due"] is None or c["next_due"] >= "2026-10-12"


def test_demo_scenarios_report_the_currency_they_are_seeded_in(as_owner):
    """The Demo page labelled USD scenarios "CNY" because the API sent no currency for them."""
    from tests.conftest import seed

    seed("usd_primary")
    api = as_owner()
    sc = {s["key"]: s["currency"] for s in api.get("/api/demo/scenarios").json()["scenarios"]}
    assert sc["usd_primary"] == "USD" and sc["primary"] == "CNY" and sc["reserve_breach"] == "USD"
    from app.seed.fixtures import SCENARIOS

    assert all(sc[k] == v.get("currency", "USD") for k, v in SCENARIOS.items())  # fixtures seed USD by default
