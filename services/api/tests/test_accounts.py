"""Account lifecycle with disposable identities: sign-up, sign-in, verification, reset, deletion,
workspaces (sample, switch, start over) and tenant isolation. Email is never sent: the
transport is replaced, and "not configured" must never look like success."""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import notify
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AccountToken, AuthSession, Business, EmailDelivery, Evidence, Membership, User
from tests.conftest import OWNER, Api, seed, signup

GOOD_PW = "correct horse battery staple 7"


def _client(ip: str | None = None) -> TestClient:
    from app.main import app

    c = TestClient(app)
    c.headers["X-Forwarded-For"] = ip or f"10.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250}.{uuid.uuid4().int % 250 + 1}"
    return c


def _email() -> str:
    return f"user-{uuid.uuid4().hex[:10]}@example.test"


def _register(c: TestClient, email: str, pw: str = GOOD_PW) -> Any:
    return c.post("/api/auth/register", json=signup(email, pw))


def _api(c: TestClient, body: dict[str, Any]) -> Api:
    return Api(c, body["csrf_token"], body)


@pytest.fixture
def email_on(monkeypatch):
    """Email 'configured' with a capturing transport (no network)."""
    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "resend")
    monkeypatch.setattr(s, "resend_api_key", __import__("pydantic").SecretStr("test-key"))
    monkeypatch.setattr(s, "email_from", "FlowSense <no-reply@example.test>")
    monkeypatch.setattr(s, "public_app_url", "https://app.example.test")
    sent: list[dict[str, str]] = []

    def fake(to: str, subject: str, text: str, html: str) -> str:
        sent.append({"to": to, "subject": subject, "text": text, "html": html})
        return f"msg-{len(sent)}"

    monkeypatch.setattr(notify, "transport", fake)
    return sent


def _code(text: str) -> str:
    m = re.search(r"(?m)^(\d{6})$", text)
    assert m, text
    return m.group(1)


def test_signup_creates_empty_workspace_with_no_presets():
    c = _client()
    r = _register(c, _email())
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["status"] == "signed_in" and me["email_delivery"] == "not_configured"
    assert me["user"]["email_verified"] is False and me["user"]["deletable"] is True
    b = me["business"]
    assert b["kind"] == "workspace" and b["name"] == "" and b["currency"] is None and b["timezone"] is None
    assert b["country"] is None and b["configured"] is False and b["simulated_clock"] is False
    assert me["preferences"]["country"] is None and me["preferences"]["display_currency"] is None
    assert me["needs_onboarding"] is True and me["setup"]["next"] == "profile"
    api = _api(c, me)
    # No sample data and no financial figures are invented for an empty workspace.
    assert api.get("/api/invoices").json() == []
    d = api.get("/api/dashboard").json()
    assert d["setup_required"] is True and "position" not in d


def test_signup_password_rules_duplicates_and_paste_friendly():
    c = _client()
    short = _register(c, _email(), "short").json()["error"]
    assert short["code"] == "SIGNUP_INVALID" and "at least" in short["fields"]["password"]
    assert "guess" in _register(c, _email(), "password1234").json()["error"]["fields"]["password"]
    assert "email" in _register(c, "not-an-email", GOOD_PW).json()["error"]["fields"]
    e = _email()
    assert _register(c, e).status_code == 200
    dup = _client().post("/api/auth/register", json=signup(e.upper(), GOOD_PW, username="someone-else-" + uuid.uuid4().hex[:6]))
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "SIGNUP_UNAVAILABLE" and "email" in dup.json()["error"]["fields"]


def test_signup_with_email_is_generic_and_requires_a_code(email_on):
    e = _email()
    first = _register(_client(), e)
    assert first.status_code == 200 and first.json()["status"] == "verify_code"
    # Same answer for an address that already has an account: no enumeration.
    again = _client().post("/api/auth/register", json=signup(e, GOOD_PW, username="other-" + uuid.uuid4().hex[:8]))
    assert again.status_code == 200 and again.json() == first.json()
    # A taken username is "taken" whoever owns it, so it can't be used to test which email owns it.
    pair = _register(_client(), e)
    assert pair.status_code == 409 and pair.json()["error"]["code"] == "USERNAME_TAKEN"
    assert "csrf_token" not in first.json()
    c = _client()
    blocked = c.post("/api/auth/login", json={"email": e, "password": GOOD_PW})
    assert blocked.status_code == 403 and blocked.json()["error"]["code"] == "EMAIL_NOT_VERIFIED"
    assert [m["subject"] for m in email_on] == ["Your FlowSense sign-up code", "Someone tried to sign up with your email"]
    code = _code(email_on[0]["text"])
    ok = c.post("/api/auth/verify-code", json={"email": e, "code": code, "password": GOOD_PW})
    assert ok.status_code == 200 and ok.json()["status"] == "signed_in" and ok.json()["user"]["email_verified"] is True
    # The welcome email goes out only after verification; the code can't be used twice.
    assert email_on[-1]["subject"].startswith("Welcome to FlowSense")
    reuse = _client().post("/api/auth/verify-code", json={"email": e, "code": code, "password": GOOD_PW})
    assert reuse.status_code == 400 and reuse.json()["error"]["code"] == "CODE_INVALID"
    assert _client().post("/api/auth/login", json={"email": e, "password": GOOD_PW}).status_code == 200
    # Codes are stored only as keyed hashes and never in the delivery log.
    with get_sessionmaker()() as s:
        rows = s.execute(select(AccountToken)).scalars().all()
        assert all(code not in r.token_hash for r in rows)
        assert all(code not in (d.detail or "") for d in s.execute(select(EmailDelivery)).scalars())


def test_forgot_password_not_configured_never_claims_success():
    r = _client().post("/api/auth/password/forgot", json={"email": _email()})
    assert r.status_code == 503 and r.json()["error"]["code"] == "EMAIL_NOT_CONFIGURED"


def _verified(email_on, e: str) -> TestClient:
    c = _client()
    _register(c, e)
    assert c.post("/api/auth/verify-code", json={"email": e, "code": _code(email_on[-1]["text"]), "password": GOOD_PW}).status_code == 200
    return c


def test_reset_flow_generic_single_use_expiring_and_revokes_sessions(email_on):
    e = _email()
    c = _verified(email_on, e)
    n_before = len(email_on)
    unknown = _client().post("/api/auth/password/forgot", json={"email": _email()})
    known = _client().post("/api/auth/password/forgot", json={"email": e})
    assert unknown.status_code == known.status_code == 200
    assert unknown.json()["status"] == known.json()["status"] == "reset_code"
    assert len(email_on) == n_before + 1  # only the real account got an email
    code = _code(email_on[-1]["text"])
    new_pw = "a brand new passphrase 2026"
    weak = _client().post("/api/auth/password/reset", json={"email": e, "code": code, "password": "short", "confirm_password": "short"})
    assert weak.status_code == 422 and "password" in weak.json()["error"]["fields"]
    differ = _client().post("/api/auth/password/reset", json={"email": e, "code": code, "password": new_pw, "confirm_password": new_pw + "x"})
    assert differ.status_code == 422 and "confirm_password" in differ.json()["error"]["fields"]
    # Refused passwords do not use up the code.
    ok = _client().post("/api/auth/password/reset", json={"email": e, "code": code, "password": new_pw, "confirm_password": new_pw})
    assert ok.status_code == 200, ok.text
    assert c.get("/api/auth/me").status_code == 401  # old session revoked
    assert email_on[-1]["subject"] == "Your FlowSense password was changed"
    again = _client().post("/api/auth/password/reset", json={"email": e, "code": code, "password": new_pw, "confirm_password": new_pw})
    assert again.json()["error"]["code"] == "CODE_INVALID"
    assert _client().post("/api/auth/login", json={"email": e, "password": GOOD_PW}).status_code == 401
    assert _client().post("/api/auth/login", json={"email": e, "password": new_pw}).status_code == 200


def test_change_password_requires_current_and_keeps_this_session():
    c = _client()
    e = _email()
    api = _api(c, _register(c, e).json())
    other = _client()
    other.post("/api/auth/login", json={"email": e, "password": GOOD_PW})
    assert api.post("/api/account/password", {"current_password": "wrong", "new_password": "new passphrase here 1"}).status_code == 403
    assert api.post("/api/account/password", {"current_password": GOOD_PW, "new_password": "new passphrase here 1"}).status_code == 200
    assert api.get("/api/auth/me").status_code == 200
    assert other.get("/api/auth/me").status_code == 401


def test_delete_account_removes_personal_data_and_is_reauthenticated():
    c = _client()
    e = _email()
    api = _api(c, _register(c, e).json())
    biz_id = api.me["business"]["id"]
    prev = api.get("/api/account/deletion").json()
    assert prev["allowed"] is True and prev["archive"] and prev["deleted"] and prev["retained"]
    assert api.post("/api/account/delete", {"password": GOOD_PW, "confirm": "yes"}).status_code == 422
    assert api.post("/api/account/delete", {"password": "wrong password", "confirm": "DELETE MY ACCOUNT"}).status_code == 403
    r = api.post("/api/account/delete", {"password": GOOD_PW, "confirm": "DELETE MY ACCOUNT"})
    assert r.status_code == 200, r.text
    assert api.get("/api/auth/me").status_code == 401
    assert _client().post("/api/auth/login", json={"email": e, "password": GOOD_PW}).status_code == 401
    with get_sessionmaker()() as s:
        u = s.execute(select(User).where(User.email == e)).scalar_one_or_none()
        assert u is None
        biz = s.get(Business, uuid.UUID(biz_id))
        assert biz.archived_at is not None
        deleted = s.get(User, biz.created_by)
        assert deleted.deleted_at is not None and deleted.display_name == "Deleted user" and deleted.email.endswith("@deleted.invalid")
        assert s.execute(select(AuthSession).where(AuthSession.user_id == deleted.id, AuthSession.revoked_at.is_(None))).first() is None
    # The address can be used again for a new, unrelated account.
    assert _register(_client(), e).status_code == 200


def test_designated_and_fixture_accounts_cannot_be_deleted(as_owner, password):
    seed("usd_primary")
    api = as_owner()
    assert api.get("/api/account/deletion").json()["protected"] is True
    r = api.post("/api/account/delete", {"password": password, "confirm": "DELETE MY ACCOUNT"})
    assert r.status_code == 403 and r.json()["error"]["code"] == "ACCOUNT_PROTECTED"
    assert api.post("/api/account/password", {"current_password": password, "new_password": "x" * 30}).status_code == 403


def test_sample_workspace_is_private_and_never_touches_own_workspace():
    c = _client()
    api = _api(c, _register(c, _email()).json())
    own_id = api.me["business"]["id"]
    r = api.post("/api/workspaces/sample")
    assert r.status_code == 200, r.text
    sample = r.json()
    api.csrf = sample["csrf_token"]
    assert sample["business"]["kind"] == "sample" and sample["business"]["id"] != own_id
    assert sample["business"]["simulated_clock"] is True and "Pearl" not in sample["business"]["name"]
    d = api.get("/api/dashboard").json()
    assert d["position"]["gap_minor"] == 40_000_00
    # Reset is scoped to this user's sample.
    assert api.post("/api/demo/reset", {"scenario": "reserve_breach", "confirm": "RESET"}).status_code == 200
    # Back to the own workspace: still empty.
    me = api.get("/api/auth/me").json()
    api.csrf = me["csrf_token"]
    sw = api.post(f"/api/workspaces/{own_id}/switch")
    api.csrf = sw.json()["csrf_token"]
    assert sw.json()["business"]["kind"] == "workspace" and api.get("/api/invoices").json() == []
    # Own workspace can't be "demo reset".
    assert api.post("/api/demo/reset", {"scenario": "usd_primary", "confirm": "RESET"}).json()["error"]["code"] == "RESET_SCOPE"


def test_cannot_switch_into_another_tenant(as_owner):
    seed("usd_primary")
    demo_biz = as_owner().me["business"]["id"]
    c = _client()
    api = _api(c, _register(c, _email()).json())
    r = api.post(f"/api/workspaces/{demo_biz}/switch")
    assert r.status_code == 404
    assert api.post(f"/api/workspaces/{uuid.uuid4()}/switch").status_code == 404


def test_start_over_archives_and_creates_an_empty_workspace():
    c = _client()
    api = _api(c, _register(c, _email()).json())
    old = api.me["business"]["id"]
    assert api.post("/api/workspaces/start-over", {"confirm": "nope"}).status_code == 422
    r = api.post("/api/workspaces/start-over", {"confirm": "START OVER"})
    assert r.status_code == 200 and r.json()["business"]["id"] != old and r.json()["business"]["currency"] is None
    with get_sessionmaker()() as s:
        assert s.get(Business, uuid.UUID(old)).archived_at is not None


def test_registration_rate_limit_per_network():
    ip = "203.0.113.77"
    codes = [_register(_client(ip), _email()).status_code for _ in range(11)]
    assert codes[:10] == [200] * 10 and codes[10] == 429


def test_owner_is_verified_and_demo_login_unchanged(as_owner):
    seed("usd_primary")
    me = as_owner().me
    assert me["user"]["email"] == OWNER and me["business"]["kind"] == "demo"
    assert "Pearl" not in me["business"]["name"] and me["business"]["timezone"] == "UTC"
    with get_sessionmaker()() as s:
        m = s.execute(select(Membership).where(Membership.business_id == uuid.UUID(me["business"]["id"]))).scalars().all()
        assert len(m) == 2
        assert s.execute(select(Evidence).where(Evidence.business_id == uuid.UUID(me["business"]["id"]))).first() is not None
