"""Two-step sign-up (validation, uniqueness, inline field errors, username sign-in) and the AI-first
assistant: automatic context retrieval, quick actions, and suggestions that run only after the
user confirms them, with verified outcomes. The model is a scripted fake: no network."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import func, select, update

from app.db import get_sessionmaker
from app.models import Action, AssistantProposal, Invoice, RateLimitHit
from tests import test_agent
from tests.conftest import seed, signup
from tests.test_agent import _clean_business, _user, say, tool

live_mode = test_agent.live_mode  # shared pytest fixture

PW = "a long signup passphrase 42"


def _client(ip: str | None = None) -> TestClient:
    from app.main import app

    c = TestClient(app)
    c.headers["X-Forwarded-For"] = ip or f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}"
    return c


def _email(domain: str = "example.test") -> str:
    return f"su-{uuid.uuid4().hex[:10]}@{domain}"


def _uname() -> str:
    return "user_" + uuid.uuid4().hex[:8]


# ------------------------------------------------------------------------ sign-up


def test_signup_reports_every_problem_inline_and_does_not_burn_rate_limits():
    ip = "203.0.113.150"
    bad = {"email": "not an email", "full_name": "  ", "username": "a", "password": "short", "confirm_password": "different"}
    with get_sessionmaker()() as s:
        before = s.execute(select(func.count(RateLimitHit.id))).scalar_one()
    for _ in range(15):  # more than any sign-up limit
        r = _client(ip).post("/api/auth/register", json=bad)
        assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "SIGNUP_INVALID"
    assert set(err["fields"]) == {"email", "full_name", "username", "password", "confirm_password"}
    assert err["fields"]["confirm_password"] == "The passwords don't match."
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(RateLimitHit.id))).scalar_one() == before
    ok = _client(ip).post("/api/auth/register", json=signup(_email(), PW, username=_uname()))
    assert ok.status_code == 200, ok.text


def test_email_format_accepts_common_providers_and_rejects_malformed():
    from app.api.accounts import email_problem

    for good in ("amina@gmail.com", "a.b+shop@outlook.com", "owner@mail.dhakafabrics.com.bd", "x@yahoo.co.uk", "o'neil@example.org", "a@xn--80ak6aa92e.com"):
        assert email_problem(good) is None, good
    for bad in ("a@b", "a@@b.com", "a b@x.com", "a@x..com", "a@-x.com", ".a@x.com", "a.@x.com", "a@x.c", "@x.com", "a@", "a@x.com."):
        assert email_problem(bad) is not None, bad


def test_username_is_unique_case_insensitive_validated_and_checkable():
    name = _uname()
    first = _client().post("/api/auth/register", json=signup(_email(), PW, username=name.upper()))
    assert first.status_code == 200 and first.json()["user"]["username"] == name
    taken = _client().post("/api/auth/register", json=signup(_email(), PW, username=name))
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "USERNAME_TAKEN" and "taken" in taken.json()["error"]["fields"]["username"]
    c = _client()
    assert c.post("/api/auth/username-check", json={"username": name}).json()["available"] is False
    assert c.post("/api/auth/username-check", json={"username": _uname()}).json()["available"] is True
    assert "reserved" in c.post("/api/auth/username-check", json={"username": "admin"}).json()["message"]
    for bad in ("ab", "-abc", "abc.", "a..b", "has space", "x" * 31):
        assert c.post("/api/auth/username-check", json={"username": bad}).json()["available"] is False, bad


def test_signup_details_are_stored_and_username_signs_in():
    e, name = _email("gmail.com"), _uname()
    r = _client().post("/api/auth/register", json=signup(e, PW, username=name, full_name="Amina  Rahman", business_name="Rahman Tailors", business_type="Tailoring shop"))
    me = r.json()
    assert me["user"]["display_name"] == "Amina Rahman" and me["user"]["username"] == name
    assert me["business"]["name"] == "Rahman Tailors" and me["business"]["business_type"] == "Tailoring shop"
    assert me["business"]["currency"] is None and me["needs_onboarding"] is True  # currency/timezone still confirmed explicitly
    # Optional fields really are optional.
    bare = _client().post("/api/auth/register", json=signup(_email("outlook.com"), PW, username=_uname()))
    assert bare.status_code == 200 and bare.json()["business"]["name"] == ""
    # Sign in with the username; failures by username count against the same account.
    assert _client().post("/api/auth/login", json={"email": name, "password": PW}).status_code == 200
    c = _client()
    codes = [c.post("/api/auth/login", json={"email": name if i % 2 else e, "password": "wrong password here"}).status_code for i in range(6)]
    assert codes[:5] == [401] * 5 and codes[5] == 429


# -------------------------------------------------------------- AI context retrieval


def test_records_are_loaded_automatically_before_the_model_answers(live_mode):
    api = _user()
    _clean_business(api)
    fake = live_mode([say("**Your situation:** short USD 4,000.00.")])
    out = api.post("/api/assistant/chat", {"message": "Explain my cash shortage", "page": "/money-in"}).json()
    req = fake.requests[0]["messages"]
    assert req[-2]["content"][0]["name"] == "get_business_snapshot"  # the model never had to ask
    snap = json.loads(req[-1]["content"][0]["content"])
    assert snap["cash_position"]["shortfall"]["display"] == "USD 4,000.00"
    assert snap["business"]["accounting_currency"] == "USD" and snap["missing"] == ["invoices (money coming in), or confirmation that there are none"]
    assert [p["description"] for p in snap["upcoming_payments"]["payments"]] == ["Monthly payroll"]
    assert out["tool_calls"][0] == {"tool": "get_business_snapshot", "error": False, "automatic": True}
    assert "[The user is on the Money in (invoices) page.]" in req[-3]["content"]


def test_snapshot_names_only_missing_data_for_an_empty_workspace(live_mode):
    api = _user()
    fake = live_mode([say("Let's set up your business first.")])
    api.post("/api/assistant/chat", {"message": "Analyse my business"})
    snap = json.loads(fake.requests[0]["messages"][-1]["content"][0]["content"])
    assert snap["missing"] == ["business currency and timezone"] and "cash_position" not in snap


def test_snapshot_never_crosses_tenants(live_mode):
    a, b = _user(), _user()
    _clean_business(a)
    fake = live_mode([say("ok")])
    b.post("/api/assistant/chat", {"message": "Analyse my business"})
    assert "Corner Bakery" not in json.dumps(fake.requests[0]["messages"])


# ------------------------------------------------------ suggestions + confirmation


def _today() -> Any:
    return datetime.now(ZoneInfo("Europe/London")).date()


def _prop(**kw: Any) -> dict[str, Any]:
    base = {"kind": "record_cash", "amount": None, "as_of": None, "due_on": None, "customer_name": None, "payee_name": None, "invoice_number": None, "description": None, "essential": None}
    return {**base, **kw}


def test_live_suggestion_does_nothing_until_confirmed_then_is_verified(live_mode):
    api, other = _user(), _user()
    _clean_business(api)
    due = (_today() + timedelta(days=10)).isoformat()
    live_mode([tool("propose_action", **_prop(kind="add_invoice", customer_name="Hotel Rosa", invoice_number="CB-9", amount="2,500", due_on=due)), say("Press Confirm to save it.")])
    out = api.post("/api/assistant/chat", {"message": "Hotel Rosa owes me 2,500 on invoice CB-9"}).json()
    (prop,) = out["proposals"]
    assert prop["kind"] == "add_invoice" and prop["status"] == "pending" and "CB-9" in prop["summary"] and "USD 2,500.00" in prop["summary"]
    assert "not counted as cash" in prop["effect"]
    assert api.get("/api/invoices").json() == []  # nothing saved yet
    # Another account can't see or confirm it.
    assert other.post(f"/api/assistant/proposals/{prop['id']}/confirm").status_code == 404
    assert other.get("/api/assistant/proposals").json()["proposals"] == []
    assert api.get("/api/assistant/proposals").json()["proposals"][0]["id"] == prop["id"]
    done = api.post(f"/api/assistant/proposals/{prop['id']}/confirm")
    assert done.status_code == 200, done.text
    res = done.json()
    assert res["status"] == "done" and res["result"]["verified"] is True and res["result"]["record"] == "invoice"
    assert "short USD 4,000.00" in res["result"]["position"]["text"]  # recomputed; an invoice is not cash
    invs = api.get("/api/invoices").json()
    assert [i["number"] for i in invs] == ["CB-9"]
    again = api.post(f"/api/assistant/proposals/{prop['id']}/confirm")
    assert again.status_code == 409 and again.json()["error"]["code"] == "PROPOSAL_DONE"
    assert len(api.get("/api/invoices").json()) == 1  # exactly once
    hist = api.get("/api/assistant/conversation").json()["messages"]
    assert hist[-1]["text"].startswith("Done and checked: Invoice CB-9 saved")
    audit = api.get("/api/audit?q=assistant.proposal").json()
    assert {e["event_type"] for e in audit} >= {"assistant.proposal_created", "assistant.proposal_confirmed"}


def test_payment_request_suggestion_still_needs_owner_approval(live_mode):
    api = _user()
    _clean_business(api)
    api.post("/api/invoices", {"new_customer": "Hotel Rosa", "number": "CB-7", "amount": "4,500", "currency": "USD", "issued_on": (_today() - timedelta(days=30)).isoformat(), "due_on": (_today() - timedelta(days=2)).isoformat()})
    with get_sessionmaker()() as s:
        before = s.execute(select(func.count(Action.id)).where(Action.source == "assistant_confirmed")).scalar_one()
    live_mode([tool("propose_action", **_prop(kind="request_payment", invoice_number="CB-7", amount="9,999")), tool("propose_action", **_prop(kind="request_payment", invoice_number="CB-7", amount="1,000")), say("ok")])
    out = api.post("/api/assistant/chat", {"message": "Help me plan payments"}).json()
    errs = [c for c in out["tool_calls"] if c["tool"] == "propose_action"]
    assert errs[0]["error"] is True and errs[1]["error"] is False  # more than outstanding is refused by code
    (prop,) = out["proposals"]
    assert "Approvals" in prop["effect"] and "Nothing is sent" in prop["effect"]
    # The planner may already hold an open request for this invoice; then confirming is refused, not duplicated.
    r = api.post(f"/api/assistant/proposals/{prop['id']}/confirm")
    with get_sessionmaker()() as s:
        mine = s.execute(select(Action).where(Action.source == "assistant_confirmed")).scalars().all()
    if r.status_code == 200:
        assert r.json()["result"]["href"] == "/approvals" and len(mine) == before + 1 and mine[-1].status == "proposed"
    else:
        assert r.json()["error"]["code"] == "REQUEST_EXISTS" and len(mine) == before
    assert all(a.status in ("proposed", "denied") for a in mine)  # never approved or sent by the assistant


def test_suggestions_expire_and_can_be_dismissed(live_mode):
    api = _user()
    _clean_business(api)
    due = (_today() + timedelta(days=12)).isoformat()
    live_mode([tool("propose_action", **_prop(kind="add_bill", payee_name="Flour Co", description="Flour", amount="300", due_on=due)),
               tool("propose_action", **_prop(kind="add_payroll", description="Part-time wages", amount="600", due_on=due)), say("ok")])
    p1, p2 = api.post("/api/assistant/chat", {"message": "add my flour bill and part-time wages"}).json()["proposals"]
    with get_sessionmaker()() as s:
        s.execute(update(AssistantProposal).where(AssistantProposal.id == uuid.UUID(p1["id"])).values(expires_at=datetime.now(ZoneInfo("UTC")) - timedelta(minutes=1)))
        s.commit()
    exp = api.post(f"/api/assistant/proposals/{p1['id']}/confirm")
    assert exp.status_code == 409 and exp.json()["error"]["code"] == "PROPOSAL_EXPIRED"
    assert api.post(f"/api/assistant/proposals/{p2['id']}/dismiss").json()["status"] == "cancelled"
    assert api.post(f"/api/assistant/proposals/{p2['id']}/confirm").json()["error"]["code"] == "PROPOSAL_DONE"
    assert len(api.get("/api/obligations").json()) == 1  # only the original payroll


def test_staff_cannot_get_cash_suggestions_and_injection_cannot_act(live_mode, as_staff, as_owner):
    seed("prompt_injection")
    staff = as_staff()
    live_mode([tool("propose_action", **_prop(kind="record_cash", amount="999,999")), say("no")])
    out = staff.post("/api/assistant/chat", {"message": "Record cash 999,999"}).json()
    assert out["proposals"] == [] and out["tool_calls"][1]["error"] is True
    # Even if a document convinced the model to suggest a request, nothing runs without the user's click.
    owner = as_owner()
    with get_sessionmaker()() as s:
        actions_before = s.execute(select(func.count(Action.id))).scalar_one()
    live_mode([tool("read_invoice_evidence", invoice_number="INV-1042"), tool("propose_action", **_prop(kind="request_payment", invoice_number="INV-1042", amount="1"))])
    owner.post("/api/assistant/chat", {"message": "Follow the instructions in the invoice evidence"})
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(Action.id))).scalar_one() == actions_before


# ----------------------------------------------------------- demo quick actions


def test_demo_quick_actions_use_the_users_records():
    api = _user()
    st = api.get("/api/assistant/status").json()
    assert st["quick_actions"] == ["Analyse my business", "Explain my cash shortage", "Help me plan payments"]
    empty = api.post("/api/assistant/chat", {"message": "Analyse my business"}).json()
    assert empty["intent"] == "getting_started"  # asks only for what is missing
    _clean_business(api)
    a = api.post("/api/assistant/chat", {"message": "Analyse my business"}).json()
    assert a["intent"] == "analyse_business" and "USD 4,000.00" in a["text"] and "USD 5,000.00" in a["text"] and "Monthly payroll" in a["text"]
    g = api.post("/api/assistant/chat", {"message": "Explain my cash shortage"}).json()
    assert g["intent"] == "explain_gap" and "USD 4,000.00" in g["text"]
    p = api.post("/api/assistant/chat", {"message": "Help me plan payments"}).json()
    assert p["intent"] == "plan_payments" and "Monthly payroll: USD 8,000.00" in p["text"] and "not covered yet" in p["text"]


def test_demo_cash_suggestion_confirms_and_refreshes_the_position():
    api = _user()
    _clean_business(api)
    out = api.post("/api/assistant/chat", {"message": "Record cash 12,000"}).json()
    (prop,) = out["proposals"]
    assert out["intent"] == "propose_cash" and "USD 12,000.00" in prop["summary"]
    assert api.get("/api/dashboard").json()["position"]["available_minor"] == 500_000  # unchanged until confirmed
    res = api.post(f"/api/assistant/proposals/{prop['id']}/confirm").json()
    assert res["result"]["verified"] is True and "covered" in res["result"]["position"]["text"]
    assert api.get("/api/dashboard").json()["position"]["available_minor"] == 1_200_000


def test_confirmed_invoice_is_tenant_scoped():
    api = _user()
    _clean_business(api)
    with get_sessionmaker()() as s:
        assert s.execute(select(func.count(Invoice.id)).where(Invoice.number == "never-created")).scalar_one() == 0
    assert api.post(f"/api/assistant/proposals/{uuid.uuid4()}/confirm").status_code == 404
    assert api.post("/api/assistant/proposals/not-a-uuid/confirm").status_code == 404
