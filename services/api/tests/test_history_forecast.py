"""Cash flow history comes from settled ledger entries only (approval or a promise changes nothing;
settlement does), with running balance, categories, filters, monthly summaries and separate
pending/expected lists. Turnover forecast: documented method, exact arithmetic, honest when data is short."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

from tests.conftest import execute, find_action, seed
from tests.test_agent import _clean_business, _user

USD = 100


def _hist(api, query: str = ""):
    r = api.get(f"/api/cashflow/history{query}")
    assert r.status_code == 200, r.text
    return r.json()


def test_history_changes_only_when_money_settles(as_owner):
    seed("usd_primary")
    api = as_owner()
    h0 = _hist(api)
    assert h0["settled_balance_minor"] == 50_000 * USD and len(h0["rows"]) >= 1
    assert all(r["status"] == "settled" for r in h0["rows"])
    exp = {e["description"].split(" (")[0] for e in h0["expected"]}
    assert any("payroll" in d.lower() for d in exp) and any("INV-" in d for d in exp)
    # Approving and sending a request, and the customer's promise, change nothing.
    req = find_action(api, "payment_request", "proposed")
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    assert execute(api, req["id"]).status_code == 200
    api.post("/api/sandbox/simulate/customer-accept", {"action_id": req["id"]})
    h1 = _hist(api)
    assert h1["rows"] == h0["rows"] and h1["settled_balance_minor"] == 50_000 * USD
    # Settlement adds exactly one row, and the running balance follows it.
    api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})
    api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]})  # a repeat can't add another row
    h2 = _hist(api)
    new = [r for r in h2["rows"] if r not in h1["rows"]]
    assert len(new) == 1 and new[0]["category"] == "Customer payment" and new[0]["money_in_minor"] == 40_000 * USD
    assert new[0]["balance_minor"] == 90_000 * USD and new[0]["source"] == "Sandbox bank (signed settlement)"
    # Payroll paid: one money-out row, balance 10,000.
    payroll = find_action(api, "payroll_payment", "proposed")
    assert api.post(f"/api/actions/{payroll['id']}/approve").status_code == 200
    assert execute(api, payroll["id"]).status_code == 200
    h3 = _hist(api)
    assert h3["settled_balance_minor"] == 10_000 * USD
    out = h3["rows"][0]
    assert out["category"] == "Payroll" and out["money_out_minor"] == 80_000 * USD and out["balance_minor"] == 10_000 * USD
    # Running balance is consistent from the oldest row.
    rows = list(reversed(h3["rows"]))
    bal = 0
    for r in rows:
        bal += r["money_in_minor"] - r["money_out_minor"]
        assert r["balance_minor"] == bal
    # Filters and the monthly summary.
    ins = _hist(api, "?direction=in")["rows"]
    assert all(r["money_in_minor"] > 0 for r in ins) and len(ins) < len(h3["rows"])
    assert _hist(api, "?category=Payroll")["rows"] == [out]
    month = h3["months"][0]
    assert month["closing_balance_minor"] == 10_000 * USD and month["net_minor"] == month["money_in_minor"] - month["money_out_minor"]
    assert api.get("/api/cashflow/history?category=Nonsense").status_code == 422


def test_history_for_declared_cash_and_tenant_isolation():
    a, b = _user(), _user()
    _clean_business(a)
    h = _hist(a)
    assert h["settled_balance_minor"] == 5_000 * USD
    assert h["rows"][0]["category"] == "Balance you declared" and h["rows"][0]["source"] == "Declared by you (not bank-verified)"
    assert any(e["category"] == "Payroll" for e in h["expected"])
    # B's workspace isn't set up: B sees its own (empty) setup message, never A's rows.
    assert b.get("/api/cashflow/history").status_code == 409


def test_turnover_needs_assumptions_without_history_and_is_illustrative():
    api = _user()
    _clean_business(api)
    f = api.get("/api/forecast/turnover").json()
    assert f["status"] == "needs_assumptions" and "not the cash in your bank and not profit" in f["definition"]
    r = api.post("/api/forecast/turnover", {"expected_monthly_sales": "10,000", "yearly_growth_pct": 0}).json()
    assert r["status"] == "illustrative" and len(r["months"]) == 12
    assert {m["base_minor"] for m in r["months"]} == {10_000 * USD}
    assert r["months"][0]["low_minor"] == 7_500 * USD and r["months"][0]["high_minor"] == 12_500 * USD
    assert r["annual"]["base_minor"] == 120_000 * USD and "not a promise" in r["disclaimer"]
    grow = api.post("/api/forecast/turnover", {"expected_monthly_sales": "1,000", "yearly_growth_pct": 12}).json()
    # Effective annual rate (reported by the 18 Oct retest): 12% a year ends exactly 12% higher at month 12,
    # i.e. about 0.949% a month compounded, not 1% a month (which would give 12.68%).
    assert grow["months"][0]["base_minor"] == 100_949 and grow["months"][11]["base_minor"] == 112_000
    assert "compounded monthly" in " ".join(grow["assumptions"]) and "not a statistical confidence interval" in " ".join(grow["assumptions"])
    shrink = api.post("/api/forecast/turnover", {"expected_monthly_sales": "1,000", "yearly_growth_pct": -10}).json()
    assert shrink["months"][11]["base_minor"] == 90_000
    flat = api.post("/api/forecast/turnover", {"expected_monthly_sales": "100,000", "yearly_growth_pct": 0}).json()
    assert {m["base_minor"] for m in flat["months"]} == {100_000 * USD}
    assert "every month stays at the starting level" in " ".join(flat["assumptions"])
    assert "month 12 is 12% above the starting month" in " ".join(grow["assumptions"])
    half = api.post("/api/forecast/turnover", {"expected_monthly_sales": "100,000", "yearly_growth_pct": -50}).json()
    assert half["months"][11]["base_minor"] == 50_000 * USD
    words = " ".join(half["assumptions"])
    assert "month 12 is 50% below the starting month" in words and "above" not in words.split("so ")[1].split(".")[0]
    big = api.post("/api/forecast/turnover", {"expected_monthly_sales": "100,000", "yearly_growth_pct": 200}).json()
    assert big["months"][11]["base_minor"] == 300_000 * USD  # the largest allowed rate, still exact
    assert api.post("/api/forecast/turnover", {"expected_monthly_sales": "1,000", "yearly_growth_pct": -51}).status_code == 422
    assert api.post("/api/forecast/turnover", {"expected_monthly_sales": "1,000", "yearly_growth_pct": 900}).status_code == 422


def _month(back: int) -> date:
    today = datetime.now(ZoneInfo("Europe/London")).date().replace(day=1)
    y, m = divmod(today.month - 1 - back, 12)
    return date(today.year + y, m + 1, 10)


def test_turnover_from_invoiced_sales_follows_the_documented_method():
    api = _user()
    _clean_business(api)
    for i, amount in enumerate([1000, 2000, 3000, 4000]):
        d = _month(4 - i)
        r = api.post("/api/invoices", {"new_customer": f"Customer {i}", "number": f"S-{i}", "amount": str(amount), "currency": "USD", "issued_on": d.isoformat(), "due_on": d.isoformat()})
        assert r.status_code == 200, r.text
    f = api.get("/api/forecast/turnover").json()
    assert f["status"] == "estimated" and [h["sales_minor"] for h in f["history"]] == [100_000, 200_000, 300_000, 400_000]
    # base = 2,500 (average); trend = slope 1,000 / 2,500 = 40% -> limited to +5%; month 1 = 2,625.
    assert f["months"][0]["base_minor"] == 262_500
    assert f["months"][1]["base_minor"] == 275_625
    assert all(m["low_minor"] < m["base_minor"] < m["high_minor"] for m in f["months"])
    assert "+5.0% a month" in " ".join(f["assumptions"]) and "least-squares" in f["method"]
    assert f["annual"]["base_minor"] == sum(m["base_minor"] for m in f["months"])
