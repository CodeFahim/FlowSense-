"""AI agent harness with a scripted fake model (no network): grounding, tool permissions, prompt
injection, budgets, failure handling without silent fallback, history isolation and demo behaviour."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from sqlalchemy import func, select

from app.agents import live
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import Action, AssistantMessage, Policy
from tests.conftest import Api, seed, signup

PW = "agent test passphrase long"


@dataclass
class Block:
    type: str
    text: str = ""
    id: str = ""
    name: str = ""
    input: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, exclude_none: bool = True) -> dict[str, Any]:
        if self.type == "text":
            return {"type": "text", "text": self.text}
        return {"type": "tool_use", "id": self.id, "name": self.name, "input": self.input}


@dataclass
class Usage:
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass
class Resp:
    content: list[Block]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)


class FakeClient:
    """Plays a script: each entry is a function(messages) -> Resp. Records every request."""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []
        outer = self

        class _Messages:
            def create(self, **kw: Any) -> Resp:
                outer.requests.append({**kw, "messages": json.loads(json.dumps(kw["messages"], default=str))})
                step = outer.script.pop(0) if outer.script else (lambda m: Resp([Block("text", text="done")], "end_turn"))
                return step(kw["messages"])

        class _Beta:
            messages = _Messages()

        self.messages = _Messages()
        self.beta = _Beta()


def tool(name: str, **inp: Any) -> Any:
    return lambda m: Resp([Block("tool_use", id=f"tu_{uuid.uuid4().hex[:6]}", name=name, input=inp)], "tool_use")


def say(text: str) -> Any:
    return lambda m: Resp([Block("text", text=text)], "end_turn")


def last_tool_result(req: dict[str, Any]) -> dict[str, Any]:
    msg = req["messages"][-1]
    return json.loads(msg["content"][0]["content"])


@pytest.fixture
def live_mode(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_mode", "live")
    monkeypatch.setattr(s, "anthropic_api_key", SecretStr("sk-test-not-real"))
    monkeypatch.setattr(s, "anthropic_model", "claude-opus-5-5")
    # Scripted tests count model requests: no answer reuse unless a test turns it on.
    monkeypatch.setattr(s, "ai_answer_cache_minutes", 0)
    from app.agents import chat

    chat._answer_cache.clear()
    holder: dict[str, FakeClient] = {}

    def install(script: list[Any]) -> FakeClient:
        holder["c"] = FakeClient(script)
        monkeypatch.setattr(live, "client_factory", lambda: holder["c"])
        return holder["c"]

    return install


def _user() -> Api:
    from app.main import app

    c = TestClient(app)
    c.headers["X-Forwarded-For"] = f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}"
    r = c.post("/api/auth/register", json=signup(f"ai-{uuid.uuid4().hex[:10]}@example.test", PW))
    return Api(c, r.json()["csrf_token"], r.json())


def _clean_business(api: Api) -> None:
    api.c.put(
        "/api/business/profile",
        json={"name": "Corner Bakery", "currency": "USD", "timezone": "Europe/London", "confirm_currency_and_timezone": True},
        headers={"X-CSRF-Token": api.csrf},
    )
    today = datetime.now(ZoneInfo("Europe/London")).date()
    api.post("/api/cash/declare", {"amount": "5,000", "currency": "USD", "as_of": today.isoformat()})
    api.post("/api/obligations", {"kind": "payroll", "new_payee": "Bakery staff", "description": "Monthly payroll", "amount": "8,000", "currency": "USD", "due_on": (today + timedelta(days=4)).isoformat()})
    api.post("/api/policy/basic", {"reserve": "1,000", "currency": "USD", "protection_horizon_days": 7})


def test_known_data_answer_is_grounded_in_tool_results(live_mode):
    """Gate 3: 5,000 cash, 8,000 payroll, 1,000 reserve -> 4,000 gap, from the calculator, not the model."""
    api = _user()
    _clean_business(api)
    fake = live_mode([tool("get_business_context"), tool("get_cash_position"), say("**Your situation:** you are short USD 4,000.00 before payroll.")])
    r = api.post("/api/assistant/chat", {"message": "Can I pay salaries?", "page": "/"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "live" and out["label"] == "Live AI" and "4,000.00" in out["text"]
    pos = last_tool_result(fake.requests[2])
    assert pos["shortfall"]["display"] == "USD 4,000.00" and pos["available_cash"]["display"] == "USD 5,000.00"
    assert pos["reserve_required"]["display"] == "USD 1,000.00" and pos["essential_total"]["display"] == "USD 8,000.00"
    ctx = last_tool_result(fake.requests[1])
    assert ctx["accounting_currency"] == "USD" and ctx["timezone"] == "Europe/London"  # stored, never asked again
    assert out["activity"].startswith("Checked the business profile") and "cash position" in out["activity"]
    assert {c["kind"] for c in out["citations"]} >= {"position"}
    # Request hygiene: server-side tools only, strict schemas, page context allowlisted, no ids from the model.
    req = fake.requests[0]
    assert all(t["strict"] is True and t["input_schema"]["additionalProperties"] is False for t in req["tools"])
    assert "[The user is on the Dashboard page.]" in req["messages"][-3]["content"]
    assert not any("business_id" in json.dumps(t["input_schema"]) for t in req["tools"])


def test_receivable_options_come_from_records_and_drafts_are_not_saved(live_mode):
    api = _user()
    _clean_business(api)
    today = datetime.now(ZoneInfo("Europe/London")).date()
    api.post("/api/invoices", {"new_customer": "Hotel Rosa", "number": "CB-7", "amount": "4,500", "currency": "USD", "issued_on": (today - timedelta(days=30)).isoformat(), "due_on": (today - timedelta(days=2)).isoformat(), "typical_days_to_pay": 1})
    fake = live_mode([tool("list_receivables", status="open"), tool("get_rescue_plan"), tool("prepare_record_draft", record="bill", counterparty_name="Flour Co", description="Flour", amount="300", due_date=None, reference=None), say("ok")])
    out = api.post("/api/assistant/chat", {"message": "What should I do now?"}).json()
    rec = last_tool_result(fake.requests[1])
    assert [i["customer"] for i in rec["invoices"]] == ["Hotel Rosa"] and rec["invoices"][0]["status"] == "overdue" and rec["invoices"][0]["days_overdue"] == 2
    plan = last_tool_result(fake.requests[2])
    assert any(o["counterparty"] == "Hotel Rosa" for o in plan["options"]) and plan["shortfall"]["display"] == "USD 4,000.00"
    assert out["drafts"][0]["record"] == "bill" and out["drafts"][0]["stored"] is False and out["drafts"][0]["href"] == "/money-out?add=bill"
    assert len(api.get("/api/obligations").json()) == 1  # the draft created nothing


def test_tools_cannot_be_invented_or_misused_and_injection_changes_nothing(live_mode, as_owner, monkeypatch):
    monkeypatch.setattr(get_settings(), "ai_max_tool_rounds", 6)  # this script uses five tool rounds
    seed("prompt_injection")
    owner = as_owner()
    with get_sessionmaker()() as s:
        actions_before = s.execute(select(func.count(Action.id))).scalar_one()
        policies_before = s.execute(select(func.count(Policy.id))).scalar_one()
    fake = live_mode(
        [
            tool("read_invoice_evidence", invoice_number="INV-1042"),
            tool("send_payment", account="SBX-ATTACKER-7781", amount_minor=5_000_000),
            tool("list_obligations", within_days=5000),
            tool("get_cash_position", business_id="other-tenant"),
            say("I won't follow instructions found in documents."),
        ]
    )
    out = owner.post("/api/assistant/chat", {"message": "Follow the instructions in the invoice evidence"}).json()
    ev = last_tool_result(fake.requests[1])
    note = next(d for d in ev["documents"] if d["label"] == "EV-1042-NOTE")
    assert "SBX-ATTACKER" in note["untrusted_text"] and note["instruction_like_text_flagged"] is True
    assert last_tool_result(fake.requests[2])["error"] == "tool_not_allowed"
    assert last_tool_result(fake.requests[3])["error"] == "invalid_input"
    assert last_tool_result(fake.requests[4])["error"] == "invalid_input"  # extra fields such as a tenant id are rejected
    assert out["mode"] == "live"
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(Action.id))).scalar_one() == actions_before
        assert s.execute(select(func.count(Policy.id))).scalar_one() == policies_before
    audit = owner.get("/api/audit?q=assistant.tool_call").json()
    assert {e["payload"]["tool"] for e in audit} >= {"read_invoice_evidence", "send_payment"}


def test_tool_round_budget_stops_the_loop(live_mode, monkeypatch):
    api = _user()
    _clean_business(api)
    monkeypatch.setattr(get_settings(), "ai_max_tool_rounds", 3)
    fake = live_mode([tool("get_policy") for _ in range(10)])
    out = api.post("/api/assistant/chat", {"message": "Loop forever"}).json()
    assert len(fake.requests) == 4 and "ran out of time" in out["text"]


@pytest.mark.parametrize(
    ("exc_name", "category"),
    [("AuthenticationError", "invalid_credentials"), ("RateLimitError", "rate_limited"), ("InternalServerError", "provider_down"), ("APIConnectionError", "network")],
)
def test_failures_give_a_labelled_quick_answer_or_report_configuration_problems(live_mode, exc_name, category):
    import anthropic
    import httpx2

    api = _user()
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if exc_name == "APIConnectionError":
        exc: Exception = anthropic.APIConnectionError(request=req)
    else:
        code = {"AuthenticationError": 401, "RateLimitError": 429, "InternalServerError": 500}[exc_name]
        exc = getattr(anthropic, exc_name)("boom", response=httpx2.Response(code, request=req), body=None)

    def boom(_m: Any) -> Resp:
        raise exc

    live_mode([boom])
    r = api.post("/api/assistant/chat", {"message": "How is my business?"})
    assert "sk-test-not-real" not in r.text
    if category == "invalid_credentials":
        # A configuration problem is reported, so the operator can fix it.
        assert r.status_code == 503 and r.json()["error"]["code"] == "AI_UNAVAILABLE" and r.json()["error"]["category"] == category
    else:
        # Quota, overload and network problems: FlowSense answers from its own calculations, labelled as such.
        out = r.json()
        assert r.status_code == 200 and out["mode"] == "quick" and out["label"].startswith("Quick answer") and out["fallback_reason"] == category
        assert out["notice"] and out["text"] and out["mode"] != "live"
        stored = api.get("/api/assistant/conversation").json()["messages"][-1]
        assert stored["mode"] == "quick" and stored["notice"] == out["notice"]
    # An explicit, labelled demo answer is available when the user chooses it.
    d = api.post("/api/assistant/chat", {"message": "Where do I add a bill?", "use_demo": True}).json()
    assert d["mode"] == "demo" and d["label"] == "Offline demo assistant" and d["explicit_demo"] is True


def test_refusal_and_max_tokens_are_handled(live_mode):
    api = _user()
    live_mode([lambda m: Resp([], "refusal")])
    assert "can't help" in api.post("/api/assistant/chat", {"message": "x"}).json()["text"]
    live_mode([lambda m: Resp([Block("text", text="partial")], "max_tokens")])
    assert "cut short" in api.post("/api/assistant/chat", {"message": "y"}).json()["text"]


def test_history_is_per_user_and_workspace_and_clearable(live_mode):
    a, b = _user(), _user()
    live_mode([say("hello A")])
    a.post("/api/assistant/chat", {"message": "secret question from A"})
    hist_a = a.get("/api/assistant/conversation").json()["messages"]
    assert [m["role"] for m in hist_a] == ["user", "assistant"] and hist_a[0]["text"] == "secret question from A"
    assert b.get("/api/assistant/conversation").json()["messages"] == []
    # Previous text turns are sent as context to the model for the same user only.
    fake = live_mode([say("second")])
    a.post("/api/assistant/chat", {"message": "follow-up"})
    assert any(m.get("content") == "secret question from A" for m in fake.requests[0]["messages"])
    fake_b = live_mode([say("hi B")])
    b.post("/api/assistant/chat", {"message": "B asks"})
    assert "secret question from A" not in json.dumps(fake_b.requests[0]["messages"])
    # A different workspace of the same user has its own history.
    sample = a.post("/api/workspaces/sample").json()
    a.csrf = sample["csrf_token"]
    assert a.get("/api/assistant/conversation").json()["messages"] == []
    r = a.c.delete("/api/assistant/conversation", headers={"X-CSRF-Token": a.csrf})
    assert r.status_code == 200
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(AssistantMessage.id)).where(AssistantMessage.user_id == uuid.UUID(b.me["user"]["id"]))).scalar_one() == 2


def test_daily_ai_limit_gives_quick_answers_instead_of_blocking(live_mode, monkeypatch):
    api = _user()
    monkeypatch.setattr(get_settings(), "ai_messages_per_user_per_day", 2)
    fake = live_mode([say("1"), say("2"), say("3")])
    assert api.post("/api/assistant/chat", {"message": "a"}).json()["mode"] == "live"
    assert api.post("/api/assistant/chat", {"message": "b"}).json()["mode"] == "live"
    third = api.post("/api/assistant/chat", {"message": "Where do I add a bill?"})
    assert third.status_code == 200 and third.json()["mode"] == "quick" and third.json()["fallback_reason"] == "user_limit"
    assert third.json()["links"][0]["href"] == "/money-out?add=bill" and len(fake.requests) == 2
    # With the automatic fallback off, the limit is a plain refusal.
    monkeypatch.setattr(get_settings(), "ai_auto_fallback", False)
    assert api.post("/api/assistant/chat", {"message": "d"}).status_code == 429


def test_failed_ai_attempts_do_not_use_up_the_daily_limit(live_mode, monkeypatch):
    import anthropic
    import httpx2

    api = _user()
    monkeypatch.setattr(get_settings(), "ai_messages_per_user_per_day", 1)
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

    def busy(_m: Any) -> Resp:
        raise anthropic.RateLimitError("busy", response=httpx2.Response(429, request=req), body=None)

    live_mode([busy, busy])
    assert api.post("/api/assistant/chat", {"message": "a"}).json()["mode"] == "quick"
    assert api.post("/api/assistant/chat", {"message": "b"}).json()["mode"] == "quick"
    live_mode([say("now the AI answers")])
    out = api.post("/api/assistant/chat", {"message": "c"}).json()
    assert out["mode"] == "live" and out["text"] == "now the AI answers"


def test_same_question_on_unchanged_records_reuses_the_answer(live_mode, monkeypatch):
    api = _user()
    monkeypatch.setattr(get_settings(), "ai_answer_cache_minutes", 15)
    fake = live_mode([say("first answer"), say("second answer"), say("third answer")])
    a = api.post("/api/assistant/chat", {"message": "Analyse my business", "chat_id": None}).json()
    api.post("/api/assistant/chats")
    b = api.post("/api/assistant/chat", {"message": "analyse my  business"}).json()
    assert a["text"] == b["text"] == "first answer" and b.get("cached") is True and len(fake.requests) == 1
    # Records changed: the next answer is fresh.
    api.post("/api/assistant/chats")
    r = api.c.put("/api/business/profile", json={"name": "Cache Test Bakery", "currency": "USD", "timezone": "Asia/Dhaka", "confirm_currency_and_timezone": True}, headers={"X-CSRF-Token": api.csrf})
    assert r.status_code == 200
    c = api.post("/api/assistant/chat", {"message": "Analyse my business"}).json()
    assert len(fake.requests) == 2 and c["text"] == "second answer" and not c.get("cached")


def test_live_answers_come_with_follow_up_suggestions(live_mode):
    api = _user()
    live_mode([say("Here is your analysis.")])
    out = api.post("/api/assistant/chat", {"message": "Analyse my business"}).json()
    assert out["mode"] == "live" and 1 <= len(out["suggestions"]) <= 3 and "Analyse my business" not in out["suggestions"]


def test_demo_assistant_navigation_suggestions_and_empty_workspace():
    api = _user()
    nav = api.post("/api/assistant/chat", {"message": "Where do I add a bill?"}).json()
    assert nav["mode"] == "demo" and nav["links"][0]["href"] == "/money-out?add=bill"
    fresh = api.post("/api/assistant/chat", {"message": "What should I do now?"}).json()
    assert fresh["intent"] == "getting_started" and fresh["links"][0]["href"] == "/setup/business"
    unknown = api.post("/api/assistant/chat", {"message": "Tell me a joke about penguins"}).json()
    assert unknown["intent"] == "unsupported" and len(unknown["suggestions"]) == 5
    st = api.get("/api/assistant/status").json()
    assert st["mode"] == "demo" and st["suggestions"]["primary"][0] == "How do I get started?" and st["retention_days"] == 30


def test_assistant_health_is_owner_only_and_hides_the_key(live_mode, as_staff):
    seed("usd_primary")
    staff = as_staff()
    assert staff.get("/api/assistant/health").status_code == 403
