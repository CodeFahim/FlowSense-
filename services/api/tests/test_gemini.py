"""Gemini adapter with a scripted fake transport (no network): records loaded before the model answers,
tool calls validated and audited, suggestions still need the user's click, failures reported as
"AI unavailable" (never the demo), budgets, refusals, and the key never leaves the server."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from app.agents import gemini
from app.config import Settings, get_settings
from tests.test_agent import _clean_business, _user

KEY = "AIza-test-not-real"


def fn(name: str, **args: Any) -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": name, "args": args}, "thoughtSignature": "sig-" + name}]}, "finishReason": "STOP"}],
            "usageMetadata": {"promptTokenCount": 100, "candidatesTokenCount": 20}}


def say(text: str, reason: str = "STOP") -> dict[str, Any]:
    return {"candidates": [{"content": {"role": "model", "parts": [{"text": text}]}, "finishReason": reason}], "usageMetadata": {"promptTokenCount": 50, "candidatesTokenCount": 30}}


class Fake:
    """Plays scripted (status, body) answers and records every request."""

    def __init__(self, script: list[Any]):
        self.script = list(script)
        self.requests: list[dict[str, Any]] = []

    def __call__(self, path: str, payload: dict[str, Any] | None, timeout: float) -> tuple[int, dict[str, Any]]:
        self.requests.append({"path": path, "payload": json.loads(json.dumps(payload)) if payload is not None else None})
        step = self.script.pop(0) if self.script else say("done")
        if isinstance(step, Exception):
            raise step
        return step if isinstance(step, tuple) else (200, step)


@pytest.fixture
def gem(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "ai_mode", "live")
    monkeypatch.setattr(s, "ai_provider", "gemini")
    monkeypatch.setattr(s, "gemini_api_key", SecretStr(KEY))
    monkeypatch.setattr(s, "gemini_model", "gemini-flash-test")
    # Scripted tests count requests: no answer reuse and no other models unless a test turns them on.
    monkeypatch.setattr(s, "ai_answer_cache_minutes", 0)
    monkeypatch.setattr(s, "gemini_fallback_models", "off")
    from app.agents import chat

    chat._answer_cache.clear()
    gemini._cooling.clear()
    monkeypatch.setattr(gemini, "_discovered", None)

    def install(script: list[Any]) -> Fake:
        f = Fake(script)
        monkeypatch.setattr(gemini, "transport", f)
        return f

    return install


def _result(req: dict[str, Any], name: str) -> dict[str, Any]:
    part = req["payload"]["contents"][-1]["parts"][0]["functionResponse"]
    assert part["name"] == name
    return part["response"]


def test_gemini_answers_from_the_users_records(gem):
    api = _user()
    _clean_business(api)
    fake = gem([fn("get_cash_position"), say("**Your situation:** you are short USD 4,000.00 before payroll.")])
    r = api.post("/api/assistant/chat", {"message": "Explain my cash shortage", "page": "/"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["mode"] == "live" and out["label"] == "Live AI" and out["provider"] == "gemini" and "4,000.00" in out["text"]
    first = fake.requests[0]
    assert first["path"] == "models/gemini-flash-test:generateContent"
    # The snapshot is in the first request: the model never has to ask for figures.
    user_parts = first["payload"]["contents"][-1]["parts"]
    assert user_parts[0]["text"].startswith("Explain my cash shortage") and "[The user is on the Dashboard page.]" in user_parts[0]["text"]
    snap = json.loads(user_parts[1]["text"].split("\n", 1)[1])
    assert snap["cash_position"]["shortfall"]["display"] == "USD 4,000.00" and snap["business"]["accounting_currency"] == "USD"
    # Tool result comes from the calculator, and the model's turn (with its signature) is echoed back unchanged.
    pos = _result(fake.requests[1], "get_cash_position")
    assert pos["shortfall"]["display"] == "USD 4,000.00" and pos["available_cash"]["display"] == "USD 5,000.00"
    assert fake.requests[1]["payload"]["contents"][-2]["parts"][0]["thoughtSignature"] == "sig-get_cash_position"
    # Server-side tools only; the key travels in a header, never in the request body or the answer.
    names = {d["name"] for d in first["payload"]["tools"][0]["functionDeclarations"]}
    assert {"get_business_snapshot", "propose_action", "get_rescue_plan"} <= names and "send_payment" not in names
    assert KEY not in json.dumps(fake.requests) and KEY not in r.text
    assert [c["tool"] for c in out["tool_calls"]] == ["get_business_snapshot", "get_cash_position"]
    audit = api.get("/api/audit?q=assistant.tool_call").json()
    assert {e["payload"]["tool"] for e in audit} >= {"get_business_snapshot", "get_cash_position"}


def test_gemini_numeric_choices_and_invented_tools(gem):
    api = _user()
    _clean_business(api)
    fake = gem([
        fn("calculate_cash_forecast", days=14.0, collection_delay_days=5, collection_percent=100, extra_expense=None),
        fn("send_payment", account="SBX-ATTACKER", amount_minor=5_000_000),
        fn("get_cash_position", business_id="other-tenant"),
        say("ok"),
    ])
    out = api.post("/api/assistant/chat", {"message": "What if my customer pays 5 days late?"}).json()
    assert _result(fake.requests[1], "calculate_cash_forecast")["days"] == 14
    assert _result(fake.requests[2], "send_payment")["error"] == "tool_not_allowed"
    assert _result(fake.requests[3], "get_cash_position")["error"] == "invalid_input"
    assert [c["error"] for c in out["tool_calls"]] == [False, False, True, True]


def test_gemini_suggestion_still_needs_the_users_confirmation(gem):
    api = _user()
    _clean_business(api)
    from tests.test_signup_ai import _today

    due = (_today() + timedelta(days=9)).isoformat()
    gem([fn("propose_action", kind="add_invoice", customer_name="Hotel Rosa", invoice_number="CB-11", amount="1,200", due_on=due), say("Press Confirm to save it.")])
    out = api.post("/api/assistant/chat", {"message": "Hotel Rosa owes me 1,200 on CB-11"}).json()
    (prop,) = out["proposals"]
    assert prop["status"] == "pending" and api.get("/api/invoices").json() == []
    done = api.post(f"/api/assistant/proposals/{prop['id']}/confirm").json()
    assert done["status"] == "done" and done["result"]["verified"] is True
    assert [i["number"] for i in api.get("/api/invoices").json()] == ["CB-11"]


@pytest.mark.parametrize(
    ("status", "body", "category"),
    [
        (400, {"error": {"status": "INVALID_ARGUMENT", "message": "API key not valid. Please pass a valid API key."}}, "invalid_credentials"),
        (403, {"error": {"status": "PERMISSION_DENIED", "message": "denied"}}, "permission"),
        (404, {"error": {"status": "NOT_FOUND", "message": "models/x is not found"}}, "model_not_found"),
        (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota"}}, "rate_limited"),
        (503, {"error": {"status": "UNAVAILABLE", "message": "overloaded"}}, "provider_down"),
    ],
)
def test_gemini_failures_quick_answer_or_report(gem, status, body, category):
    api = _user()
    gem([(status, body)])
    r = api.post("/api/assistant/chat", {"message": "How is my business?"})
    assert KEY not in r.text
    if category in ("rate_limited", "provider_down"):
        assert r.status_code == 200 and r.json()["mode"] == "quick" and r.json()["fallback_reason"] == category
    else:
        assert r.status_code == 503 and r.json()["error"]["code"] == "AI_UNAVAILABLE" and r.json()["error"]["category"] == category
    gemini._cooling.clear()
    d = api.post("/api/assistant/chat", {"message": "Where do I add a bill?", "use_demo": True}).json()
    assert d["mode"] == "demo" and d["explicit_demo"] is True


def test_gemini_network_timeout_refusal_cutoff_and_budget(gem, monkeypatch):
    api = _user()
    gem([httpx.ReadTimeout("slow")])
    assert api.post("/api/assistant/chat", {"message": "a"}).json()["fallback_reason"] == "timeout"
    gem([httpx.ConnectError("down")])
    assert api.post("/api/assistant/chat", {"message": "b"}).json()["fallback_reason"] == "network"
    gem([{"candidates": [], "promptFeedback": {"blockReason": "SAFETY"}}])
    assert "can't help" in api.post("/api/assistant/chat", {"message": "c"}).json()["text"]
    gem([say("partial", "MAX_TOKENS")])
    assert "cut short" in api.post("/api/assistant/chat", {"message": "d"}).json()["text"]
    monkeypatch.setattr(get_settings(), "ai_max_tool_rounds", 2)
    fake = gem([fn("get_policy") for _ in range(10)])
    assert "ran out of time" in api.post("/api/assistant/chat", {"message": "e"}).json()["text"] and len(fake.requests) == 3


QUOTA = (429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "quota", "details": [
    {"@type": "type.googleapis.com/google.rpc.QuotaFailure", "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]},
    {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "40s"},
]}})


def test_quota_refusal_switches_to_the_next_model_and_remembers_it(gem, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_fallback_models", "gemini-lite-test")
    api = _user()
    fake = gem([QUOTA, say("answered by the lite model")])
    out = api.post("/api/assistant/chat", {"message": "How is my business?"}).json()
    assert out["mode"] == "live" and out["text"] == "answered by the lite model"
    assert [r["path"] for r in fake.requests] == ["models/gemini-flash-test:generateContent", "models/gemini-lite-test:generateContent"]
    # The refused model is left alone (daily quota: re-checked hourly); the next question goes straight to the other one.
    fake = gem([say("straight to lite")])
    assert api.post("/api/assistant/chat", {"message": "Next question"}).json()["text"] == "straight to lite"
    assert [r["path"] for r in fake.requests] == ["models/gemini-lite-test:generateContent"]
    # Every model out of quota: a labelled quick answer, and no request at all while they cool down.
    fake = gem([QUOTA])
    q = api.post("/api/assistant/chat", {"message": "Where do I add a bill?"}).json()
    assert q["mode"] == "quick" and q["fallback_reason"] == "rate_limited" and q["links"][0]["href"] == "/money-out?add=bill"
    fake = gem([say("should not be asked")])
    assert api.post("/api/assistant/chat", {"message": "Where do I add an invoice?"}).json()["mode"] == "quick" and fake.requests == []


def test_auto_discovers_flash_lite_models_after_a_quota_refusal(gem, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_fallback_models", "auto")
    api = _user()
    listing = {"models": [
        {"name": "models/gemini-flash-test", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9.0-flash", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9.0-flash-lite", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/gemini-9.0-flash-image", "supportedGenerationMethods": ["generateContent"]},
        {"name": "models/text-embedding-9", "supportedGenerationMethods": ["embedContent"]},
    ]}
    fake = gem([QUOTA, listing, say("lite answered")])
    out = api.post("/api/assistant/chat", {"message": "How is my business?"}).json()
    assert out["text"] == "lite answered"
    assert [r["path"] for r in fake.requests] == ["models/gemini-flash-test:generateContent", "models?pageSize=200", "models/gemini-9.0-flash-lite:generateContent"]
    # A normal answer costs one request: no listing when the main model has quota.
    gemini._cooling.clear()
    fake = gem([say("main model again")])
    api.post("/api/assistant/chat", {"message": "Again"})
    assert [r["path"] for r in fake.requests] == ["models/gemini-flash-test:generateContent"]


def test_lite_models_answer_without_thinking(gem, monkeypatch):
    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-2.5-flash-lite")
    api = _user()
    fake = gem([say("quick")])
    api.post("/api/assistant/chat", {"message": "hey"})
    assert fake.requests[0]["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}


def test_gemini_health_and_config(gem, as_owner):
    from tests.conftest import seed

    seed("usd_primary")
    owner = as_owner()
    fake = gem([{"name": "models/gemini-flash-test"}])
    h = owner.get("/api/assistant/health").json()
    assert h == {"mode": "live", "status": "ok", "provider": "gemini", "model": "gemini-flash-test"}
    assert fake.requests[0] == {"path": "models/gemini-flash-test", "payload": None}
    gem([(400, {"error": {"message": "API key not valid"}})])
    bad = owner.get("/api/assistant/health")
    assert bad.json()["status"] == "invalid_credentials" and KEY not in bad.text
    st = owner.get("/api/assistant/status").json()
    assert st["provider"] == "gemini" and st["model"] == "gemini-flash-test"
    with pytest.raises(ValidationError, match="LIFELINE_GEMINI_API_KEY"):
        Settings(ai_mode="live", ai_provider="gemini", gemini_model="m")


def test_gemini_asks_for_low_thinking_and_retries_without_it_if_refused(gem, monkeypatch):
    api = _user()
    fake = gem([say("hello")])
    api.post("/api/assistant/chat", {"message": "hey"})
    assert fake.requests[0]["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingLevel": "low"}
    monkeypatch.setattr(get_settings(), "gemini_model", "gemini-2.5-flash")
    fake = gem([say("hello")])
    api.post("/api/assistant/chat", {"message": "hey"})
    assert fake.requests[0]["payload"]["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 512}
    # A model that rejects the setting: one retry with the model's default, and the answer still arrives.
    fake = gem([(400, {"error": {"status": "INVALID_ARGUMENT", "message": "Thinking level is not supported for this model."}}), say("hello again")])
    out = api.post("/api/assistant/chat", {"message": "hey"}).json()
    assert out["text"] == "hello again" and "thinkingConfig" not in fake.requests[1]["payload"]["generationConfig"]
    assert "Thinking level" not in json.dumps(out)
    monkeypatch.setattr(get_settings(), "gemini_thinking", "default")
    fake = gem([say("hi")])
    api.post("/api/assistant/chat", {"message": "hey"})
    assert "thinkingConfig" not in fake.requests[0]["payload"]["generationConfig"]
