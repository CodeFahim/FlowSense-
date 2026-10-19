"""Start-fresh journey through the API: business profile with no presets, available cash,
money in, money out, reserve, analysis; plus validation, edits, external payments and isolation."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_sessionmaker
from app.ledger import service as ledger
from app.models import JournalEntry
from tests.conftest import Api, seed, signup

PW = "a long enough test passphrase"


def _new_user() -> Api:
    from app.main import app

    c = TestClient(app)
    c.headers["X-Forwarded-For"] = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}"
    r = c.post("/api/auth/register", json=signup(f"rec-{uuid.uuid4().hex[:10]}@example.test", PW))
    assert r.status_code == 200, r.text
    return Api(c, r.json()["csrf_token"], r.json())


def _put(api: Api, path: str, body: dict[str, Any]) -> Any:
    return api.c.put(path, json=body, headers={"X-CSRF-Token": api.csrf})


def _patch(api: Api, path: str, body: dict[str, Any]) -> Any:
    return api.c.patch(path, json=body, headers={"X-CSRF-Token": api.csrf})


PROFILE = {
    "name": "Rahman Textiles",
    "place": "Mirpur, Dhaka",
    "business_type": "Garment workshop",
    "country": "BD",
    "currency": "USD",  # accounting in USD while based in Bangladesh: an allowed combination
    "timezone": "Asia/Dhaka",
    "confirm_currency_and_timezone": True,
}


def _configured(api: Api, **over: Any) -> None:
    r = _put(api, "/api/business/profile", {**PROFILE, **over})
    assert r.status_code == 200, r.text


def test_records_need_a_confirmed_profile_and_nothing_is_preset():
    api = _new_user()
    r = api.post("/api/invoices", {"new_customer": "A", "number": "1", "amount": "10", "currency": "USD", "issued_on": "2026-10-01", "due_on": "2026-10-20"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "SETUP_REQUIRED"
    assert _put(api, "/api/business/profile", {**PROFILE, "confirm_currency_and_timezone": False}).status_code == 422
    assert _put(api, "/api/business/profile", {**PROFILE, "timezone": "Mars/Base"}).json()["error"]["code"] == "TIMEZONE_UNKNOWN"
    assert _put(api, "/api/business/profile", {**PROFILE, "currency": "XAU"}).json()["error"]["code"] == "CURRENCY_UNKNOWN"
    tz = api.get("/api/reference/timezones").json()
    assert tz["suggested"] == [] and any(z["name"] == "Asia/Dhaka" for z in tz["timezones"])
    assert api.get("/api/reference/timezones?country=BD").json()["suggested"] == ["Asia/Dhaka"]
    us = api.get("/api/reference/timezones?country=US").json()["suggested"]
    assert len(us) > 5 and "America/New_York" in us  # several timezones: the user must pick one
    # Optional fields may stay empty; country can stay unset.
    _configured(api, place=None, business_type=None, country=None)
    me = api.get("/api/auth/me").json()
    assert me["business"]["country"] is None and me["business"]["currency"] == "USD" and me["business"]["timezone"] == "Asia/Dhaka"
    assert me["needs_onboarding"] is True  # the user's own welcome step is still open
    assert me["setup"]["next"] == "cash"


def test_clean_journey_gap_from_recorded_figures_only():
    api = _new_user()
    _configured(api)
    # Before cash is entered the balance is unknown, not zero.
    d = api.get("/api/dashboard").json()
    assert d["position"]["cash_status"] == "not_entered" and d["case"] is None
    # Confirmed zero is a valid answer (and different from unknown); then the real figure.
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    assert api.post("/api/cash/declare", {"amount": "0", "currency": "USD", "as_of": today.isoformat()}).status_code == 200
    assert api.get("/api/dashboard").json()["position"]["cash_status"] == "declared"
    future = (today + timedelta(days=2)).isoformat()
    assert api.post("/api/cash/declare", {"amount": "5", "currency": "USD", "as_of": future}).json()["error"]["code"] == "DATE_IN_FUTURE"
    r = api.post("/api/cash/declare", {"amount": "5,000", "currency": "USD", "as_of": today.isoformat(), "account_label": "Main account"})
    assert r.status_code == 200 and r.json()["outcome"] == "declared"
    assert "not bank-verified" in r.json()["detail"]

    due = today + timedelta(days=3)
    r = api.post(
        "/api/obligations",
        {"kind": "payroll", "new_payee": "Staff wages", "description": "Weekly wages", "amount": "8,000", "currency": "USD", "due_on": due.isoformat(), "due_time": "17:00:00"},
    )
    assert r.status_code == 200, r.text
    ob = r.json()["created"][0]
    assert ob["essential"] is True and ob["state"] == "planned"
    # 17:00 in Dhaka (UTC+6) is 11:00 UTC on that date.
    assert datetime.fromisoformat(ob["due_at"]).astimezone(UTC).hour == 11
    assert api.post("/api/setup/none", {"step": "money_in", "none": True}).json()["steps"][2]["done"] is True

    r = api.post("/api/policy/basic", {"reserve": "1,000", "currency": "USD", "protection_horizon_days": 7})
    assert r.status_code == 200, r.text
    assert r.json()["setup"]["complete"] is True and r.json()["setup"]["analysis_ready"] is True
    d = api.get("/api/dashboard").json()
    p = d["position"]
    assert (p["settled_minor"], p["protected_total_minor"], p["reserve_minor"], p["gap_minor"]) == (500_000, 800_000, 100_000, 400_000)
    assert d["case"] is not None

    # A collectible invoice: the plan's options come from this record, not from invented customers.
    r = api.post(
        "/api/invoices",
        {"new_customer": "Dhaka Fabrics Ltd", "number": "RT-101", "amount": "4,500", "currency": "USD", "issued_on": (today - timedelta(days=20)).isoformat(), "due_on": (today - timedelta(days=1)).isoformat(), "typical_days_to_pay": 1},
    )
    assert r.status_code == 200, r.text
    case = api.get("/api/rescue").json()["case"]
    plan = case["plans"][0]["body"]
    names = {c.get("counterparty") for c in plan["components"]} | {c.get("counterparty") for c in plan["rejected"]}
    assert "Dhaka Fabrics Ltd" in names and not any(n and "sample" in n.lower() for n in names)
    assert plan["steps"] and plan["steps"][0]["amount_minor"] == 400_000

    # Other currencies are refused with an explanation, never added together.
    bad = api.post("/api/invoices", {"new_customer": "X", "number": "RT-102", "amount": "100", "currency": "BDT", "issued_on": today.isoformat(), "due_on": today.isoformat()})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "CURRENCY_NOT_ACCOUNTING"
    for amount in ("-5", "abc", "1.234", "0"):
        assert api.post("/api/invoices", {"new_customer": "X", "number": f"Z{amount}", "amount": amount, "currency": "USD", "issued_on": today.isoformat(), "due_on": today.isoformat()}).status_code == 422
    dup = api.post("/api/invoices", {"new_customer": "X", "number": "RT-101", "amount": "1", "currency": "USD", "issued_on": today.isoformat(), "due_on": today.isoformat()})
    assert dup.status_code == 409


def test_payments_recorded_outside_lifeline_go_through_signed_events():
    api = _new_user()
    _configured(api)
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    api.post("/api/cash/declare", {"amount": "1,000", "currency": "USD", "as_of": today.isoformat()})
    inv = api.post("/api/invoices", {"new_customer": "Client", "number": "A-1", "amount": "300", "currency": "USD", "issued_on": today.isoformat(), "due_on": today.isoformat()}).json()
    over = api.post(f"/api/invoices/{inv['id']}/payments", {"amount": "301", "currency": "USD"})
    assert over.status_code == 422
    paid = api.post(f"/api/invoices/{inv['id']}/payments", {"amount": "100", "currency": "USD"})
    assert paid.status_code == 200 and paid.json()["outcome"] == "settled"
    ob = api.post("/api/obligations", {"kind": "supplier", "new_payee": "Landlord", "description": "Rent", "amount": "400", "currency": "USD", "due_on": today.isoformat()}).json()["created"][0]
    assert api.post(f"/api/obligations/{ob['id']}/payments", {"amount": "400", "currency": "USD"}).json()["outcome"] == "settled"
    assert api.post(f"/api/obligations/{ob['id']}/payments", {"amount": "1", "currency": "USD"}).status_code == 409
    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 1_000_00 + 100_00 - 400_00
    receipts = api.get("/api/receipts").json()
    assert {r["content"]["recorded_by"] for r in receipts} == {"owner (not bank-verified)", "owner (paid outside FlowSense, not bank-verified)"}
    biz_id = uuid.UUID(api.me["business"]["id"])
    with get_sessionmaker()() as s:
        assert ledger.trial_balance(s, biz_id)["balanced"] is True
        assert ledger.settled_cash(s, biz_id) == 700_00
        memos = [e.memo for e in s.execute(select(JournalEntry).where(JournalEntry.business_id == biz_id)).scalars()]
        assert any("declared by owner" in m for m in memos)
    assert api.get("/api/audit/verify").json()["valid"] is True


def test_edits_adjust_instead_of_rewriting_and_revoke_approvals():
    api = _new_user()
    _configured(api)
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    api.post("/api/cash/declare", {"amount": "20,000", "currency": "USD", "as_of": today.isoformat()})
    api.post("/api/policy/basic", {"reserve": "0", "currency": "USD"})
    ob = api.post("/api/obligations", {"kind": "payroll", "new_payee": "Wages", "description": "Wages", "amount": "1,000", "currency": "USD", "due_on": (today + timedelta(days=2)).isoformat()}).json()["created"][0]
    payee = ob["payee"]["id"]
    assert api.post(f"/api/counterparties/{payee}/verify").status_code == 200
    a = api.post("/api/actions", {"type": "payroll_payment", "amount_minor": 1_000_00, "counterparty_id": payee, "obligation_id": ob["id"], "purpose": "Wages"}).json()["action"]
    assert api.post(f"/api/actions/{a['id']}/approve").status_code == 200
    r = _patch(api, f"/api/obligations/{ob['id']}", {"amount": "900", "currency": "USD"})
    assert r.status_code == 200 and r.json()["revoked_approvals"]
    act = api.get(f"/api/actions/{a['id']}").json()
    assert act["status"] == "proposed" and act["amount_minor"] == 900_00 and act["approval"]["revoked_at"] is not None
    biz_id = uuid.UUID(api.me["business"]["id"])
    with get_sessionmaker()() as s:
        memos = [e.memo for e in s.execute(select(JournalEntry).where(JournalEntry.business_id == biz_id)).scalars()]
        assert any("amount corrected" in m for m in memos)
        assert ledger.trial_balance(s, biz_id)["balanced"] is True


def test_recurring_bills_create_dated_occurrences_and_dst_safe_times():
    api = _new_user()
    _configured(api, timezone="America/New_York", country="US")
    r = api.post(
        "/api/obligations",
        {"kind": "supplier", "new_payee": "Software Co", "description": "Subscription", "amount": "50", "currency": "USD", "due_on": "2026-10-31", "due_time": "09:00:00", "recurrence": "monthly"},
    )
    created = r.json()["created"]
    assert len(created) == 6 and len({c["series_id"] for c in created}) == 1
    # 31 Oct -> 30 Nov (short month); 09:00 New York is 13:00 UTC in October (EDT) and 14:00 UTC in November (EST).
    first, second = (datetime.fromisoformat(c["due_at"]).astimezone(UTC) for c in created[:2])
    assert (first.date(), first.hour) == (date(2026, 10, 31), 13)
    assert (second.date(), second.hour) == (date(2026, 11, 30), 14)


def test_records_are_tenant_isolated_and_demo_cash_is_fixed(as_owner):
    seed("usd_primary")
    demo = as_owner()
    inv_id = demo.get("/api/invoices").json()[0]["id"]
    ob_id = demo.get("/api/obligations").json()[0]["id"]
    other = _new_user()
    _configured(other)
    assert _patch(other, f"/api/invoices/{inv_id}", {"description": "hijack"}).status_code == 404
    assert other.post(f"/api/invoices/{inv_id}/payments", {"amount": "1", "currency": "USD"}).status_code == 404
    assert _patch(other, f"/api/obligations/{ob_id}", {"amount": "1", "currency": "USD"}).status_code == 404
    r = demo.post("/api/cash/declare", {"amount": "1", "currency": "USD", "as_of": "2026-10-12"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "DEMO_CASH_FIXED"
    assert _put(demo, "/api/business/profile", {**PROFILE, "currency": "USD", "timezone": "UTC", "name": "x"}).status_code == 200  # name only
    assert _put(demo, "/api/business/profile", {**PROFILE, "timezone": "Asia/Dhaka"}).json()["error"]["code"] == "FIXTURE_SETTINGS"


def test_staff_cannot_confirm_owner_only_records(as_staff):
    seed("usd_primary")
    staff = as_staff()
    assert staff.post("/api/policy/basic", {"reserve": "1", "currency": "USD"}).status_code == 403
    assert staff.post("/api/cash/declare", {"amount": "1", "currency": "USD", "as_of": "2026-10-12"}).status_code == 403
    assert _put(staff, "/api/business/profile", PROFILE).status_code == 403


def test_home_never_reports_safe_with_missing_data(as_owner):
    api = _new_user()
    _configured(api)
    home = api.get("/api/dashboard").json()["home"]
    assert home["status"]["tone"] == "incomplete" and "available cash" in home["status"]["sentence"]
    assert [t["key"] for t in home["tasks"]][:2] == ["setup_cash", "setup_money_in"]
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    api.post("/api/cash/declare", {"amount": "100", "currency": "USD", "as_of": today.isoformat()})
    api.post("/api/policy/basic", {"reserve": "0", "currency": "USD"})
    # Cash and reserve known, but money in/out not entered and not confirmed as none: still incomplete, not green.
    home = api.get("/api/dashboard").json()["home"]
    assert home["status"]["tone"] == "incomplete" and "money coming in" in home["status"]["sentence"]
    api.post("/api/setup/none", {"step": "money_in", "none": True})
    api.post("/api/setup/none", {"step": "money_out", "none": True})
    assert api.get("/api/dashboard").json()["home"]["status"]["tone"] == "ok"
    # Demo: shortfall, a review task and the overdue invoice are listed with amounts and dates.
    seed("usd_primary")
    d = as_owner().get("/api/dashboard").json()["home"]
    keys = [t["key"] for t in d["tasks"]]
    assert d["status"]["tone"] == "danger" and "needs_approval" in keys and "overdue_INV-1042" in keys
    assert d["data"]["simulated_clock"] is True and d["data"]["business_date"] == "2026-10-12"
