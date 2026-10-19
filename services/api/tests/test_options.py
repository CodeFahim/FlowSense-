"""One deterministic classification per recovery option, used by the assistant's text and its option cards.

Regression (19 Oct report): an answer said a supplier partial payment could help, the server stamped the same
line "Outside your rules, not a recovery option", and the answer then called it valid after supplier agreement.
Cause: the text guard matched "reduce ... reserve" anywhere within 60 characters, so a line that kept the reserve
intact was relabelled. Classes now come from agents.options; the guard only catches the reserve as the object.
"""

from __future__ import annotations

from app.agents import constraints, options
from tests import test_agent
from tests.conftest import seed
from tests.test_agent import _user, last_tool_result, say, tool
from tests.test_constraints import _workspace

live_mode = test_agent.live_mode  # shared pytest fixture


def _by_title(result, word):
    return [o for o in result["options"] if word.lower() in o["title"].lower()]


def test_hypothetical_bill_gap_and_classes(live_mode):
    """BDT 20,000 cash, 10,000 reserve, hypothetical 15,000 essential bill tomorrow, no receipts, no borrowing."""
    api = _user()
    _workspace(api, financing_pct="0")
    fake = live_mode([
        tool("assess_options", extra_payment_amount="15,000", extra_payment_due_in_days=1, extra_payment_description="Essential bill"),
        say("You would be BDT 5,000 short. Conditional: ask the supplier to accept BDT 10,000 now and the rest later."),
    ])
    r = api.post("/api/assistant/chat", {"message": "If a 15,000 essential bill is due tomorrow, how short am I? Keep my reserve, no loans."})
    assert r.status_code == 200, r.text
    res = last_tool_result(fake.requests[1])
    s = res["situation"]
    assert s["spendable_cash"]["display"] == "BDT 10,000.00" and s["gap"]["display"] == "BDT 5,000.00"
    assert res["hypothetical"]["saved"] is False and s["saved_records_gap"]["minor"] == 0
    partial = _by_title(res, "now and")[0]
    assert partial["class"] == options.CONDITIONAL and partial["conditions_met"] is False
    assert partial["title"] == "Pay BDT 10,000.00 now and BDT 5,000.00 later"
    assert any("agrees in writing" in c for c in partial["conditions"]) and any("binding" in c for c in partial["conditions"])
    assert _by_title(res, "reserve")[0]["class"] == options.PROHIBITED
    assert _by_title(res, "borrow")[0]["class"] == options.PROHIBITED
    # Nothing is invented: no customer, no feasible option, collections ruled out with a reason.
    assert not [o for o in res["options"] if o["class"] == options.FEASIBLE]
    assert any(x["title"] == "Collect from customers" for x in res["ruled_out"])
    assert res["recommended"]["class"] == options.CONDITIONAL
    # The same classification reaches the user as cards; the conditional line is not relabelled.
    body = r.json()
    assert body["options"]["options"] == res["options"] and constraints.FLAG not in body["text"]


def test_supplier_line_that_keeps_the_reserve_is_not_relabelled():
    c = constraints.Constraints(1_000_000, 0, None, 1, True, True)
    line = "- Negotiate a partial payment with the supplier, reducing what you pay now so your reserve stays intact."
    assert constraints.guard(line, c, "BDT") == (line, [])
    line2 = "- Pay part of the bill and use the cash above your reserve for it."
    assert constraints.guard(line2, c, "BDT") == (line2, [])
    # Live answer on 19 Oct (before this fix): the recommended collection was stamped "Outside your rules"
    # because it said "avoiding borrowing".
    live = "Recommended next step: Request partial collection of CZK 60,000.00 from overdue invoice INV-1042 to cover the expanded gap while keeping your reserve intact and avoiding borrowing."
    assert constraints.guard(live, c, "CZK") == (live, [])
    assert constraints.guard("- Take a short-term loan of BDT 5,000; no fees apply.", c, "BDT")[1] == ["borrowing"]
    # Real recommendations to break the rule are still caught, with the class's own words.
    for bad in ("- Use part of your reserve for the bill.", "- Release 5,000 from the reserve.", "- The reserve could be temporarily lowered to 5,000."):
        out, flags = constraints.guard(bad, c, "BDT")
        assert flags == ["reserve"] and constraints.flag_text("reserve") in out, bad


def test_sample_rescue_classes_match_the_planner(as_owner, db):
    biz_id = seed("usd_primary")
    api = as_owner()
    from app.models import Business

    biz = db.get(Business, biz_id)
    assert biz is not None
    res = options.assess(db, biz, [])
    assert res["status"] == "shortfall" and res["situation"]["gap"]["display"] == "USD 40,000.00"
    collect = _by_title(res, "INV-1042")[0]
    assert collect["class"] == options.FEASIBLE and collect["cash_impact"]["display"] == "USD 40,000.00"
    assert any("approves" in r for r in collect["requirements"]) and any("not cash" in r for r in collect["requirements"])
    fin = [o for o in res["options"] if o["id"].startswith("finance:")][0]
    assert fin["class"] == options.PROHIBITED and "39.70%" in fin["rule"] and "8.00%" in fin["rule"]
    assert _by_title(res, "reserve")[0]["class"] == options.PROHIBITED
    assert res["recommended"]["id"] == collect["id"] and res["feasible_options_cover_gap"] is True
    assert api.get("/api/rescue").status_code == 200


def test_missing_records_are_not_enough_information_not_zero_risk(live_mode):
    api = _user()
    api.c.put(
        "/api/business/profile",
        json={"name": "New Shop", "currency": "BDT", "timezone": "Asia/Dhaka", "confirm_currency_and_timezone": True},
        headers={"X-CSRF-Token": api.csrf},
    )
    fake = live_mode([tool("assess_options", extra_payment_amount=None, extra_payment_due_in_days=None, extra_payment_description=None), say("I need your cash first.")])
    body = api.post("/api/assistant/chat", {"message": "What are my options?"}).json()
    res = last_tool_result(fake.requests[1])
    assert res["status"] == options.INSUFFICIENT and any("available cash" in m for m in res["missing"])
    assert "Missing records do not mean there is no risk." in res["note"]
    assert body["options"]["status"] == options.INSUFFICIENT


def test_answers_without_an_assessment_have_no_cards(live_mode):
    api = _user()
    _workspace(api)
    live_mode([say("Money in is under Money → Money in.")])
    assert api.post("/api/assistant/chat", {"message": "Where do I add an invoice?"}).json()["options"] is None


def test_offline_and_quick_answers_carry_the_same_cards(as_owner, live_mode):
    seed("usd_primary")
    api = as_owner()
    demo = api.post("/api/assistant/chat", {"message": "Compare my rescue options.", "use_demo": True}).json()
    classes = {o["id"].split(":")[0]: o["class"] for o in demo["options"]["options"]}
    assert classes["collect"] == options.FEASIBLE and classes["finance"] == options.PROHIBITED and classes["reserve"] == options.PROHIBITED

    import anthropic
    import httpx2

    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def busy(_m):
        raise anthropic.RateLimitError("busy", response=httpx2.Response(429, request=req), body=None)

    live_mode([busy])
    quick = api.post("/api/assistant/chat", {"message": "Compare my rescue options."}).json()
    assert quick["mode"] == "quick" and quick["options"]["options"] == demo["options"]["options"]
    assert api.post("/api/assistant/chat", {"message": "Where do I add an invoice?", "use_demo": True}).json()["options"] is None


def test_minor_unit_amount_from_the_model_is_sent_back(live_mode):
    """Live, 19 Oct: the model passed "1500000" for the user's "15,000", so the what-if showed CZK 1,500,000."""
    api = _user()
    _workspace(api, financing_pct="0")
    fake = live_mode([
        tool("assess_options", extra_payment_amount="1500000", extra_payment_due_in_days=1, extra_payment_description="Supplier bill"),
        tool("assess_options", extra_payment_amount="15,000", extra_payment_due_in_days=1, extra_payment_description="Supplier bill"),
        say("You would be BDT 5,000 short."),
    ])
    body = api.post("/api/assistant/chat", {"message": "Hypothetically a 15,000 essential bill is due tomorrow. What can I do?"}).json()
    first = last_tool_result(fake.requests[1])
    assert first["error"] == "amount_units"
    assert last_tool_result(fake.requests[2])["situation"]["gap"]["display"] == "BDT 5,000.00"
    assert body["options"]["hypothetical"]["amount"]["display"] == "BDT 15,000.00"


def test_stated_amounts_parsing():
    from decimal import Decimal

    from app.agents.tools import _stated_amounts

    got = _stated_amounts(["a 15,000 bill", "pay 2500.50 and 15k", "on 13 Oct"])
    assert {Decimal(15000), Decimal("2500.50"), Decimal(13)} <= got


def test_what_if_is_assessed_by_code_even_when_the_model_skips_the_tool(live_mode):
    """Live, 19 Oct: the model answered a what-if without calling assess_options and computed a wrong gap."""
    import json

    api = _user()
    _workspace(api, financing_pct="0")
    fake = live_mode([say("You would be BDT 5,000 short.")])
    msg = "Keep my 10,000 reserve protected and no borrowing. If a 15,000 essential bill came due tomorrow, how short am I? Give me three options."
    body = api.post("/api/assistant/chat", {"message": msg}).json()
    cards = body["options"]
    assert cards["hypothetical"]["amount"]["display"] == "BDT 15,000.00" and cards["situation"]["gap"]["display"] == "BDT 5,000.00"
    assert any(o["class"] == options.CONDITIONAL for o in cards["options"])
    snap = json.dumps(fake.requests[0])
    assert "what_if_assessment" in snap and "BDT 5,000.00" in snap


def test_stated_hypothetical_parsing():
    from types import SimpleNamespace

    biz = SimpleNamespace(ccy="BDT", tz_name="Asia/Dhaka", sandbox_now=None)
    from datetime import UTC, datetime
    from unittest.mock import patch

    now = datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
    with patch("app.agents.options.business_now", return_value=now):
        h = options.stated_hypothetical(biz, ["Keep my 10,000 reserve protected. If a 15,000 essential bill came due tomorrow, how short am I?"])
        assert h is not None and h.amount_minor == 15_000_00 and (h.due_at - now).days == 1
        h = options.stated_hypothetical(biz, ["What if a 2.5k repair payment is due in 3 days?"])
        assert h is not None and h.amount_minor == 2_500_00 and (h.due_at - now).days == 3
        h = options.stated_hypothetical(biz, ["Suppose a 15,000 bill came due", "Give me three options for that"])
        assert h is not None and h.amount_minor == 15_000_00
        assert options.stated_hypothetical(biz, ["I paid a 15,000 bill yesterday"]) is None
        assert options.stated_hypothetical(biz, ["If my 10,000 reserve stays, am I fine?"]) is None


def test_what_if_bill_before_payroll_counts_payroll_too(as_owner, db):
    """Live, 19 Oct: a 15,000 bill due tomorrow, payroll 80,000 on Friday -> 55,000 short (was "no shortfall")."""
    from datetime import timedelta

    from app.clock import business_now
    from app.models import Business

    biz_id = seed("usd_primary")
    as_owner()
    biz = db.get(Business, biz_id)
    hyp = options.Hypothetical(15_000_00, business_now(biz) + timedelta(days=1), "Hypothetical bill")
    res = options.assess(db, biz, ["Keep my reserve, no borrowing"], hyp)
    assert res["status"] == "shortfall" and res["situation"]["gap"]["display"] == "USD 55,000.00"
    assert res["situation"]["saved_records_gap"]["display"] == "USD 40,000.00"
    collect = _by_title(res, "INV-1042")[0]
    assert collect["class"] == options.FEASIBLE and collect["cash_impact"]["display"] == "USD 55,000.00"
    assert _by_title(res, "Move the due date")[0]["class"] == options.CONDITIONAL


def test_empty_model_text_points_to_the_button(live_mode):
    """Live, 19 Oct: "Where do I add a new bill?" ended with the right button and no text."""
    api = _user()
    _workspace(api)
    live_mode([tool("app_link", destination="add_bill"), say("")])
    body = api.post("/api/assistant/chat", {"message": "Where do I add a new bill?"}).json()
    assert "couldn't produce" not in body["text"] and "Add a bill" in body["text"]
    assert any(link["href"] == "/money-out?add=bill" for link in body["links"])


def test_options_questions_get_cards_even_without_the_tool(as_owner, live_mode):
    """Live, 19 Oct: "What are my options to cover payroll?" answered without cards."""
    seed("usd_primary")
    api = as_owner()
    live_mode([say("One option fits your rules: collect from INV-1042.")])
    body = api.post("/api/assistant/chat", {"message": "What are my options to cover payroll?"}).json()
    classes = {o["class"] for o in body["options"]["options"]}
    assert options.FEASIBLE in classes and options.PROHIBITED in classes
    live_mode([say("Your cash is 50,000.")])
    assert api.post("/api/assistant/chat", {"message": "How much cash do I have?"}).json()["options"] is None


def test_prompt_keeps_scope_and_amount_words():
    from app.agents import live

    p = live.SYSTEM_PROMPT
    assert "Stay on this business's money" in p and '"spendable" is available cash above the protected reserve' in p
    assert "demo and sample workspaces" in p and "never leave the text empty" in p
