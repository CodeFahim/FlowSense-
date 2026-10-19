"""Paid expenses are dated on the day they were paid, and never counted twice.

Semantics (19 Oct):
- cash movement effective date = the date paid (12:00 business time, never later than now);
- the audit trail and receipt keep the time the owner entered it;
- a payment dated before the declared balance's "as of" date is already inside that balance (same day: counted as after it): it closes the
  bill without changing cash;
- an unpaid bill never changes settled cash; marking it paid creates one cash movement; part payments leave
  the rest outstanding; future dates and dates more than a year back are refused with guidance;
- the same idempotency key and payload is recorded once (retries, double clicks).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from tests.test_agent import _user


def _setup(api, as_of):
    api.c.put(
        "/api/business/profile",
        json={"name": "Dhaka Traders", "currency": "USD", "timezone": "Asia/Dhaka", "confirm_currency_and_timezone": True},
        headers={"X-CSRF-Token": api.csrf},
    )
    assert api.post("/api/cash/declare", {"amount": "10,000", "currency": "USD", "as_of": as_of.isoformat()}).status_code == 200


def _bill(api, due, amount="1,000"):
    r = api.post("/api/obligations", {"kind": "supplier", "new_payee": "Landlord", "description": "Rent", "amount": amount, "currency": "USD", "due_on": due.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()["created"][0]["id"]


def _settled(api):
    return api.get("/api/dashboard").json()["position"]["settled_minor"]


def _today():
    return datetime.now(ZoneInfo("Asia/Dhaka")).date()


def test_backdated_payment_is_reported_in_the_month_it_was_paid():
    api = _user()
    today = _today()
    paid_on = today - timedelta(days=35)
    _setup(api, paid_on - timedelta(days=5))  # balance declared as of a date before the payment
    ob = _bill(api, paid_on)
    r = api.post(f"/api/obligations/{ob}/payments", {"amount": "1,000", "currency": "USD", "paid_on": paid_on.isoformat()})
    assert r.status_code == 200 and r.json()["outcome"] == "settled", r.text
    assert _settled(api) == 9_000_00
    rows = [x for x in api.get("/api/cashflow/history").json()["rows"] if x.get("money_out_minor")]
    assert len(rows) == 1 and rows[0]["date"] == paid_on.isoformat() and rows[0]["kind"] == "payment"
    receipt = [x for x in api.get("/api/receipts").json() if x["content"].get("paid_on")][0]["content"]
    assert receipt["paid_on"] == paid_on.isoformat() and receipt["recorded_at"][:10] > paid_on.isoformat()  # entry time kept
    assert receipt["cash_effect"] == "cash reduced once"


def test_payment_already_in_the_declared_balance_is_not_deducted_again():
    api = _user()
    today = _today()
    _setup(api, today)
    ob = _bill(api, today - timedelta(days=3))
    r = api.post(f"/api/obligations/{ob}/payments", {"amount": "1,000", "currency": "USD", "paid_on": (today - timedelta(days=3)).isoformat()})
    assert r.status_code == 200 and "cash didn't change" in r.json()["detail"]
    assert _settled(api) == 10_000_00
    assert not [x for x in api.get("/api/cashflow/history").json()["rows"] if x.get("money_out_minor")]
    bill = [o for o in api.get("/api/obligations").json() if o["id"] == ob][0]
    assert bill["unpaid_minor"] == 0


def test_unpaid_bill_partial_payment_and_retry():
    api = _user()
    today = _today()
    _setup(api, today - timedelta(days=1))
    ob = _bill(api, today + timedelta(days=10))
    assert _settled(api) == 10_000_00  # an unpaid bill doesn't change settled cash
    body = {"amount": "400", "currency": "USD", "paid_on": today.isoformat(), "idempotency_key": "form-key-123456"}
    first = api.post(f"/api/obligations/{ob}/payments", body)
    again = api.post(f"/api/obligations/{ob}/payments", body)
    assert first.status_code == 200 and again.status_code == 200 and again.json().get("replayed") is True
    assert _settled(api) == 9_600_00
    bill = [o for o in api.get("/api/obligations").json() if o["id"] == ob][0]
    assert bill["unpaid_minor"] == 600_00
    assert len([x for x in api.get("/api/cashflow/history").json()["rows"] if x.get("money_out_minor")]) == 1


def test_future_and_very_old_dates_are_refused_with_guidance():
    api = _user()
    today = _today()
    _setup(api, today)
    ob = _bill(api, today)
    fut = api.post(f"/api/obligations/{ob}/payments", {"amount": "1", "currency": "USD", "paid_on": (today + timedelta(days=1)).isoformat()})
    assert fut.status_code == 422 and fut.json()["error"]["code"] == "DATE_IN_FUTURE" and "Pay later" in fut.json()["error"]["message"]
    old = api.post(f"/api/obligations/{ob}/payments", {"amount": "1", "currency": "USD", "paid_on": (today - timedelta(days=400)).isoformat()})
    assert old.status_code == 422 and old.json()["error"]["code"] == "DATE_TOO_OLD"
    assert _settled(api) == 10_000_00
