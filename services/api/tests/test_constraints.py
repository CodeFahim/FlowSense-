"""Hard constraints survive across chat turns, and advice that breaks one is never shown as a recovery option.

Report scenario: 20,000 cash, 10,000 protected reserve, a hypothetical 15,000 essential bill -> 5,000 short.
The user asks for options "for that hypothetical"; a model that suggests lowering the reserve is relabelled
by server code, and the next turn's snapshot carries both the policy and the user's stated constraint.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.agents import constraints
from tests import test_agent
from tests.conftest import seed
from tests.test_agent import _user, say, tool

live_mode = test_agent.live_mode  # shared pytest fixture


def _workspace(api, financing_pct: str | None = None):
    api.c.put(
        "/api/business/profile",
        json={"name": "Dhaka Traders", "currency": "BDT", "timezone": "Asia/Dhaka", "confirm_currency_and_timezone": True},
        headers={"X-CSRF-Token": api.csrf},
    )
    today = datetime.now(ZoneInfo("Asia/Dhaka")).date()
    api.post("/api/cash/declare", {"amount": "20,000", "currency": "BDT", "as_of": today.isoformat()})
    body = {"reserve": "10,000", "currency": "BDT", "protection_horizon_days": 7}
    if financing_pct is not None:
        body["max_financing_pct"] = financing_pct
    assert api.post("/api/policy/basic", body).status_code == 200


def _snapshot(req):
    import json

    for m in req["messages"]:
        if isinstance(m["content"], list):
            for b in m["content"]:
                if b.get("type") == "tool_result" and b.get("tool_use_id") == "toolu_flowsense_snapshot":
                    return json.loads(b["content"])
    raise AssertionError("snapshot not sent")


def test_reserve_constraint_is_kept_across_turns_and_violations_are_relabelled(live_mode):
    api = _user()
    _workspace(api)
    fake = live_mode([
        say("If a BDT 15,000 essential bill were due, you would be BDT 5,000 short while keeping the BDT 10,000 reserve."),
        say("Three ways to cover that hypothetical:\n1. Ask a customer to pay early.\n2. Temporarily lower your reserve to BDT 5,000.\n3. Delay a non-essential purchase."),
    ])
    r1 = api.post("/api/assistant/chat", {"message": "Hypothetically, a 15,000 essential bill is due. Keep my 10,000 reserve protected. How short am I?"})
    assert r1.status_code == 200 and "5,000" in r1.json()["text"]
    r2 = api.post("/api/assistant/chat", {"message": "Give me three practical recovery options for that hypothetical, without changing records."})
    assert r2.status_code == 200, r2.text
    text = r2.json()["text"]
    lines = text.split("\n")
    assert lines[2].startswith(f"2. *{constraints.flag_text('reserve')}*") and "Ask a customer" in lines[1] and constraints.FLAG not in lines[1]
    assert "**Rule check (FlowSense):**" in text and "BDT 10,000.00" in text.split("Rule check")[1]
    # The second turn's snapshot carries the policy reserve AND the constraint the user stated in turn 1.
    rules = _snapshot(fake.requests[1])["hard_constraints"]["rules"]
    assert any("Protected reserve BDT 10,000.00" in x for x in rules)
    assert any("user said in this chat that the reserve must stay protected" in x for x in rules)


def test_answer_that_respects_constraints_is_unchanged(live_mode):
    api = _user()
    _workspace(api)
    answer = "You are BDT 5,000 short. Lowering the reserve is not a recovery option under your rules.\n1. Ask a customer to pay early."
    live_mode([say(answer)])
    r = api.post("/api/assistant/chat", {"message": "What can I do about a 5,000 gap? Keep the reserve intact."})
    assert r.json()["text"] == answer


def test_borrowing_is_flagged_when_policy_allows_none_or_user_refuses(live_mode):
    api = _user()
    _workspace(api, financing_pct="0")
    live_mode([say("Options:\n- Take a short-term loan of BDT 5,000.\n- Ask a customer to pay early.")])
    text = api.post("/api/assistant/chat", {"message": "How do I cover a 5,000 gap?"}).json()["text"]
    assert f"- *{constraints.flag_text('borrowing')}* Take a short-term loan" in text and "borrowing is not allowed" in text
    assert "- Ask a customer to pay early." in text


def test_stated_constraints_detection():
    keep, no_borrow = constraints.from_conversation(["Please keep the 10,000 reserve protected", "and don't take any loans"])
    assert keep and no_borrow
    assert constraints.from_conversation(["How much cash do I have?"]) == (False, False)
    assert constraints.from_conversation(["Don't touch my reserve"])[0]


def test_financing_above_cap_is_rejected_by_the_planner_and_visible_to_the_assistant(as_owner, live_mode):
    """Sample fixture: the 39.70% quote breaks the 8% cap. The planner (not the model) rejects it."""
    seed("usd_primary")
    api = as_owner()
    plan = api.get("/api/rescue").json()["case"]["plans"][0]["body"]
    fin = [r for r in plan["rejected"] if r["code"] == "FINANCING_COST_EXCEEDS_POLICY"]
    assert fin and "exceeds the policy cap of 8.00%" in fin[0]["reason"]
    assert all(c["kind"] != "financing" for c in plan["components"])
    fake = live_mode([tool("get_rescue_plan"), say("The loan quote is rejected: it exceeds your 8% cap.")])
    api.post("/api/assistant/chat", {"message": "Can I borrow to cover payroll?"})
    rules = _snapshot(fake.requests[0])["hard_constraints"]["rules"]
    assert any("at or below 8.00%" in x for x in rules)


def test_lines_that_already_rule_a_break_out_are_left_alone():
    """Live answer on 18 Oct: the model listed "Lowering the reserve: Prohibited" under "Why other options are unavailable"."""
    c = constraints.Constraints(1_000_000, 800, None, 1, True, False)
    text = (
        "Only one practical option exists.\n\n**Why other options are unavailable under your rules:**\n"
        "* **Financing:** Rejected. 39.70% exceeds your 8.00% cap.\n"
        "* **Lowering the reserve or delaying payroll:** Prohibited by your hard constraints."
    )
    assert constraints.guard(text, c, "USD") == (text, [])
    assert constraints.guard("* **Lowering the reserve:** prohibited.", c, "USD")[1] == []
    # A recommendation after a normal heading is still caught.
    out, flags = constraints.guard("**Other possible ways:**\n- Temporarily lower your reserve to USD 5,000.", c, "USD")
    assert flags == ["reserve"] and constraints.FLAG in out
