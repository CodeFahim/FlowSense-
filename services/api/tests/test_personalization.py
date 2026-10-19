"""Display preferences, onboarding, ISO 4217 reference data, currency conversion and the USD fixture.

Exchange-rate tests use the fixed demo table or a fake ECB fetcher: no network.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.audit import chain as audit
from app.db import get_sessionmaker
from app.ledger import service as ledger
from app.models import Business, JournalEntry, Membership, User
from tests.conftest import OWNER, execute, find_action, login, seed

CNY = 100


def _ledger_snapshot(biz_id):
    with get_sessionmaker()() as s:
        return ledger.settled_cash(s, biz_id), s.execute(select(func.count(JournalEntry.id)).where(JournalEntry.business_id == biz_id)).scalar_one()


def test_reference_data_is_iso_4217_and_cldr(as_owner):
    seed("primary")
    api = as_owner()
    cur = {c["code"]: c for c in api.get("/api/reference/currencies").json()["currencies"]}
    assert len(cur) >= 150, "every active ISO 4217 fiat currency, not a short hardcoded list"
    assert cur["BDT"]["name"] == "Bangladeshi Taka" and cur["BDT"]["minor_units"] == 2
    assert cur["JPY"]["minor_units"] == 0 and cur["KWD"]["minor_units"] == 3 and cur["BHD"]["minor_units"] == 3
    for not_fiat in ("XAU", "XDR", "CLF", "USN", "XTS"):
        assert not_fiat not in cur
    regions = {r["code"]: r for r in api.get("/api/reference/regions").json()["regions"]}
    assert regions["BD"]["name"] == "Bangladesh" and regions["BD"]["currencies"] == ["BDT"]
    assert "USD" in regions["ZW"]["currencies"]


def test_onboarding_preferences_persist_and_never_touch_money(as_owner, as_staff, password):
    biz_id = seed("primary")
    api = as_owner()
    me = api.get("/api/auth/me").json()
    assert me["needs_onboarding"] is True
    # No preset display currency: until the user picks one, amounts show in the accounting currency.
    assert me["preferences"]["display_currency"] is None and me["preferences"]["effective_display_currency"] is None
    before = _ledger_snapshot(biz_id)
    dash_before = api.get("/api/dashboard").json()["position"]

    # Bangladesh is the country, but the user may choose any currency (here USD).
    r = api.c.put("/api/me/preferences", json={"country": "BD", "display_currency": "USD", "theme": "dark", "accent": "violet",
                                                  "number_locale": "en-GB", "complete_onboarding": True},
                  headers={"X-CSRF-Token": api.csrf})
    assert r.status_code == 200, r.text
    assert r.json()["country"] == "BD" and r.json()["display_currency"] == "USD" and r.json()["onboarded"] is True

    # Persisted across a fresh login; onboarding for the user's own part does not repeat.
    again = login(OWNER, password).get("/api/auth/me").json()
    assert again["preferences"]["theme"] == "dark" and again["preferences"]["accent"] == "violet"
    assert again["preferences"]["country"] == "BD"

    # Owner still has to confirm the books' currency (this seed was not a UI reset).
    assert again["needs_onboarding"] is True and again["accounting_currency"]["confirmed"] is False
    r = api.post("/api/business/accounting-currency", {"currency": "CNY"})
    assert r.status_code == 200 and r.json()["confirmed"] is True and r.json()["locked"] is True
    assert login(OWNER, password).get("/api/auth/me").json()["needs_onboarding"] is False

    # Display preferences never change stored amounts, the ledger or the position.
    assert _ledger_snapshot(biz_id) == before
    assert api.get("/api/dashboard").json()["position"] == dash_before
    assert api.get("/api/audit/verify").json()["valid"] is True

    # Validation, CSRF, and per-user isolation.
    bad = api.c.put("/api/me/preferences", json={"display_currency": "XAU"}, headers={"X-CSRF-Token": api.csrf})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "CURRENCY_UNKNOWN"
    assert api.c.put("/api/me/preferences", json={"country": "QQ"}, headers={"X-CSRF-Token": api.csrf}).status_code == 422
    assert api.c.put("/api/me/preferences", json={"theme": "neon"}, headers={"X-CSRF-Token": api.csrf}).status_code == 422
    assert api.c.put("/api/me/preferences", json={"theme": "light"}).status_code == 403
    staff = as_staff()
    assert staff.get("/api/me/preferences").json()["theme"] == "system"
    # Staff only confirms their own display choices; never the business currency.
    assert staff.post("/api/business/accounting-currency", {"currency": "CNY"}).status_code == 403
    cleared = api.c.put("/api/me/preferences", json={"clear": ["country"]}, headers={"X-CSRF-Token": api.csrf}).json()
    assert cleared["country"] is None and cleared["display_currency"] == "USD"


def test_accounting_currency_locked_once_history_exists_but_settable_on_a_new_business(as_owner, password):
    seed("primary")
    api = as_owner()
    r = api.post("/api/business/accounting-currency", {"currency": "USD"})
    assert r.status_code == 409 and r.json()["error"]["code"] == "ACCOUNTING_CURRENCY_LOCKED"
    assert api.get("/api/auth/me").json()["business"]["currency"] == "CNY"

    with get_sessionmaker()() as s:
        owner = s.execute(select(User).where(User.email == OWNER)).scalar_one()
        fresh = Business(name="Brand-new business (test)", currency="CNY", timezone="Asia/Shanghai")
        s.add(fresh)
        s.flush()
        audit.init_head(s, fresh.id)
        s.add(Membership(user_id=owner.id, business_id=fresh.id, role="owner"))
        s.commit()
        fresh_id = fresh.id
    try:
        new = login(OWNER, password)
        assert str(fresh_id) in {w["id"] for w in new.me["workspaces"]}
        sw = new.post(f"/api/workspaces/{fresh_id}/switch")
        assert sw.status_code == 200, sw.text
        new.csrf = sw.json()["csrf_token"]
        assert new.get("/api/auth/me").json()["business"]["id"] == str(fresh_id)
        assert new.get("/api/business/accounting-currency").json()["locked"] is False
        r = new.post("/api/business/accounting-currency", {"currency": "BDT"})
        assert r.status_code == 200 and r.json()["currency"] == "BDT" and r.json()["confirmed"] is True
    finally:
        with get_sessionmaker()() as s:
            s.get(Business, fresh_id).archived_at = datetime.now(UTC)
            s.commit()


def test_demo_conversion_is_decimal_safe_and_respects_minor_units(as_owner):
    biz_id = seed("primary")
    api = as_owner()
    before = _ledger_snapshot(biz_id)

    def conv(amount, f, t, mode="demo", **kw):
        return api.post("/api/fx/convert", {"amount": amount, "from_currency": f, "to_currency": t, "mode": mode, **kw})

    r = conv("100", "USD", "BDT").json()
    assert r["source"]["status"] == "demo" and "not market data" in r["source"]["label"]
    assert r["result_minor"] == 1_181_818 and r["result_display"] == "BDT 11,818.18" and r["rate"] == "118.182"
    assert conv("10", "USD", "JPY").json()["result_display"] == "JPY 1,455"  # zero-decimal currency
    assert conv("1", "USD", "KWD").json()["result_display"] == "KWD 0.309"  # three-decimal currency
    assert conv("5", "USD", "USD").json()["result_display"] == "USD 5.00"
    # Demo table does not cover every currency: say so instead of inventing a rate.
    nok = conv("5", "USD", "NOK").json()
    assert nok["result_minor"] is None and nok["missing"] == ["NOK"]

    manual = conv("2.5", "BDT", "EUR", mode="manual", manual_rate="0.0077").json()
    assert manual["source"]["status"] == "manual" and manual["result_display"] == "EUR 0.02"
    for bad in ("0", "-1", "NaN", "inf", "abc"):
        assert conv("1", "USD", "EUR", mode="manual", manual_rate=bad).status_code == 422
    assert conv("1.5", "JPY", "USD").status_code == 422  # JPY has no decimals
    assert conv("1", "USD", "XAU").json()["error"]["code"] == "CURRENCY_UNKNOWN"
    assert conv("1", "USD", "ABC").status_code == 422
    assert api.c.post("/api/fx/convert", json={"amount": "1", "from_currency": "USD", "to_currency": "EUR"}).status_code == 403

    # Conversion is a calculation: no ledger entries, no cash change.
    assert _ledger_snapshot(biz_id) == before


def test_dashboard_headline_next_action_and_equivalents(as_owner):
    seed("primary")
    api = as_owner()
    api.c.put("/api/me/preferences", json={"clear": ["display_currency"]}, headers={"X-CSRF-Token": api.csrf})
    d = api.get("/api/dashboard").json()
    assert d["headline"].startswith("You need an additional CNY 40,000.00 to cover October payroll")
    assert "CNY 10,000.00 reserve" in d["headline"]
    na = d["next_action"]
    assert na["kind"] == "action" and na["requires_approval"] is True and na["amount_minor"] == 40_000 * CNY
    assert na["title"] == "Approve the payment request of CNY 40,000.00"
    # No display currency chosen: figures stay in the accounting currency (no preset, no conversion).
    eq = d["display_equivalents"]
    assert eq["currency"] == "CNY" and eq["status"] == "same"
    assert api.get("/api/nav/counts").json() == {"approvals": 2, "needs_approval": 2, "ready_to_send": 0, "in_progress": 0, "rescue_open": 1}


def test_usd_fixture_full_rescue_and_cny_records_preserved(as_owner):
    cny_id = seed("primary")
    cny_before = _ledger_snapshot(cny_id)
    usd_id = seed("usd_primary")
    api = as_owner()
    d = api.get("/api/dashboard").json()
    assert d["currency"] == "USD" and d["position"]["gap_minor"] == 40_000 * 100
    assert d["headline"].startswith("You need an additional USD 40,000.00")
    assert "USD" in api.get("/api/assistant/suggestions").json()["more"][3]

    req = find_action(api, "payment_request", "proposed")
    assert api.post(f"/api/actions/{req['id']}/approve").status_code == 200
    assert execute(api, req["id"]).status_code == 200
    assert api.post("/api/sandbox/simulate/customer-pay", {"action_id": req["id"]}).json()["outcome"] == "settled"
    pay = find_action(api, "payroll_payment", "proposed")
    assert api.post(f"/api/actions/{pay['id']}/approve").status_code == 200
    r = execute(api, pay["id"])
    assert r.status_code == 200 and r.json()["settlement"]["outcome"] == "settled"
    # Duplicate submission of the same approved action pays nothing more.
    assert execute(api, pay["id"]).status_code in (200, 409)
    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == 10_000 * 100 and d["case"]["status"] == "RESOLVED"
    receipts = api.get("/api/receipts").json()
    assert {x["content"]["currency"] for x in receipts} == {"USD"}
    assert any(x["content"]["amount_display"] == "USD 80,000.00" for x in receipts)
    assert len([x for x in receipts if x["content"]["direction"] == "outgoing"]) == 1

    # The archived CNY fixture is untouched: still CNY, same ledger.
    with get_sessionmaker()() as s:
        old = s.get(Business, cny_id)
        assert old.currency == "CNY" and old.archived_at is not None
    assert _ledger_snapshot(cny_id) == cny_before
    assert usd_id != cny_id


@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("Explain my cash position.", "explain_gap"),
        ("Can I cover payroll?", "payroll_safety"),
        ("Which invoices should I collect?", "list_receivables"),
        ("Why was this action blocked?", "explain_blocks"),
        ("Compare my rescue options.", "compare_options"),
        ("Compare financing options", "compare_financing"),
        ("Compare the options", "compare_options"),
    ],
)
def test_starter_questions_route_to_real_answers(as_owner, message, intent):
    seed("primary")
    api = as_owner()
    r = api.post("/api/assistant/chat", {"message": message}).json()
    assert r["intent"] == intent and r["mode"] == "demo" and r["links"]


def test_welcome_steps_can_be_run_again_without_losing_choices(as_owner):
    seed("primary")
    api = as_owner()
    put = lambda body: api.c.put("/api/me/preferences", json=body, headers={"X-CSRF-Token": api.csrf}).json()  # noqa: E731
    put({"country": "DE", "display_currency": "EUR", "complete_onboarding": True})
    again = put({"restart_onboarding": True})
    assert again["onboarded"] is False and again["country"] == "DE" and again["display_currency"] == "EUR"
    assert api.get("/api/auth/me").json()["needs_onboarding"] is True
    assert put({"complete_onboarding": True})["onboarded"] is True
