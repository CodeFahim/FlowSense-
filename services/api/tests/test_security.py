"""Authentication, authorization, tenant isolation, CSRF, sessions, throttling, kill switch."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db import get_sessionmaker
from app.main import app
from app.models import Business, Invoice
from app.seed.fixtures import ISOLATION_KEY
from tests.conftest import OTHER, execute, find_action, login, seed


def test_staff_cannot_approve_or_submit(as_staff, as_owner):
    seed("primary")
    staff = as_staff()
    req = find_action(staff, "payment_request", "proposed")
    r = staff.post(f"/api/actions/{req['id']}/approve")
    assert r.status_code == 403 and r.json()["error"]["code"] == "ROLE_FORBIDDEN"
    owner = as_owner()
    assert owner.post(f"/api/actions/{req['id']}/approve").status_code == 200
    r = execute(staff, req["id"])
    assert r.status_code == 403
    # Staff also cannot change policy, kill switch, apply recipient changes or reset.
    assert staff.post("/api/kill-switch", {"enabled": True}).status_code == 403
    assert staff.post("/api/demo/reset", {"scenario": "primary", "confirm": "RESET"}).status_code == 403


def test_cross_tenant_reads_are_denied(as_owner, password):
    seed("primary")
    owner = as_owner()
    other = login(OTHER, password)
    pearl_invoice = owner.get("/api/invoices").json()[0]["id"]
    assert other.get(f"/api/invoices/{pearl_invoice}").status_code == 404
    pearl_action = owner.get("/api/actions").json()[0]["id"]
    assert other.get(f"/api/actions/{pearl_action}").status_code == 404
    assert other.post(f"/api/actions/{pearl_action}/approve").status_code == 404
    with get_sessionmaker()() as s:
        harbor = s.execute(select(Business).where(Business.fixture_key == ISOLATION_KEY)).scalar_one()
        harbor_inv = s.execute(select(Invoice).where(Invoice.business_id == harbor.id)).scalars().first()
    assert owner.get(f"/api/invoices/{harbor_inv.id}").status_code == 404
    # The other tenant sees only its own data.
    assert [i["number"] for i in other.get("/api/invoices").json()] == ["HT-0007"]


def test_csrf_required_for_mutations(as_owner):
    seed("primary")
    owner = as_owner()
    req = find_action(owner, "payment_request", "proposed")
    r = owner.c.post(f"/api/actions/{req['id']}/approve")  # no CSRF header
    assert r.status_code == 403 and r.json()["error"]["code"] == "CSRF_FAILED"
    r = owner.c.post(f"/api/actions/{req['id']}/approve", headers={"X-CSRF-Token": "forged"})
    assert r.status_code == 403


def test_unauthenticated_and_logout(as_owner):
    seed("primary")
    anon = TestClient(app)
    assert anon.get("/api/dashboard").status_code == 401
    owner = as_owner()
    assert owner.get("/api/auth/me").status_code == 200
    assert owner.post("/api/auth/logout").status_code == 200
    assert owner.get("/api/auth/me").status_code == 401


def test_login_throttling(password):
    c = TestClient(app)
    email = f"probe-{uuid.uuid4().hex[:8]}@example.test"  # isolated so other tests' accounts are not locked
    for _ in range(5):
        assert c.post("/api/auth/login", json={"email": email, "password": "wrong-password"}).status_code == 401
    r = c.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 429 and r.json()["error"]["code"] == "LOGIN_THROTTLED"


def test_kill_switch_blocks_submission(as_owner):
    seed("duplicate_payroll")
    owner = as_owner()
    payroll = find_action(owner, "payroll_payment", "approved")
    assert owner.post("/api/kill-switch", {"enabled": True}).json()["kill_switch"] is True
    r = execute(owner, payroll["id"])
    assert r.status_code == 409 and "KILL_SWITCH_ACTIVE" in {x["code"] for x in r.json()["error"]["reasons"]}
    owner.post("/api/kill-switch", {"enabled": False})
    assert execute(owner, payroll["id"]).status_code == 200


def test_policy_change_invalidates_approval(as_owner):
    seed("duplicate_payroll")
    owner = as_owner()
    payroll = find_action(owner, "payroll_payment", "approved")
    out = owner.post(
        "/api/assistant/chat",
        {
            "message": "Pay employees this Friday, retain USD 10,000, use only approved recipients, and never borrow above 8% annualized all-in cost."
        },
    ).json()
    pid = out["proposed_policy"]["id"]
    assert owner.post(f"/api/policies/{pid}/activate").status_code == 200
    r = execute(owner, payroll["id"])
    assert r.status_code == 409 and "POLICY_VERSION_CHANGED" in {x["code"] for x in r.json()["error"]["reasons"]}


def test_amount_validation_rejects_bad_inputs(as_owner):
    seed("primary")
    owner = as_owner()
    cp = owner.get("/api/counterparties").json()["counterparties"][0]["id"]
    for bad in (0, -5, 1.5, "100", 10**13, True):
        r = owner.post("/api/actions", {"type": "supplier_payment", "amount_minor": bad, "counterparty_id": cp, "purpose": "x"})
        assert r.status_code == 422, (bad, r.text)


def test_demo_reset_archives_not_deletes(as_owner):
    first = seed("primary")
    owner = as_owner()
    r = owner.post("/api/demo/reset", {"scenario": "reserve_breach", "confirm": "RESET"})
    assert r.status_code == 200
    with get_sessionmaker()() as s:
        old = s.get(Business, first)
        assert old is not None and old.archived_at is not None
        harbor = s.execute(select(Business).where(Business.fixture_key == ISOLATION_KEY)).scalar_one()
        assert harbor.archived_at is None
    # Cookie was rotated to the new fixture.
    assert owner.get("/api/auth/me").json()["business"]["scenario"] == "reserve_breach"


def test_live_ai_mode_fails_closed_without_credentials(monkeypatch):
    """A blank key (as in .env.example) must not satisfy AI_MODE=live; the app refuses to start."""
    import pytest
    from pydantic import ValidationError

    from app.config import Settings

    base = {"database_url": "postgresql+psycopg://x@y/z", "sandbox_event_secret": "s" * 40}
    for key, model in (("", "some-model"), ("   ", "some-model"), ("k", ""), (None, None)):
        with pytest.raises(ValidationError):
            Settings(**base, ai_mode="live", anthropic_api_key=key, anthropic_model=model)
    assert Settings(**base, ai_mode="demo", anthropic_api_key="").ai_mode == "demo"


def test_hosted_demo_configuration_guards():
    """The public demo mode keeps demo accounts but requires HTTPS cookies and a strong generated password;
    production guards are unchanged."""
    import pytest as _pytest
    from pydantic import ValidationError

    from app.config import Settings, normalize_database_url

    base = {"database_url": "postgresql://u:p@db.internal/lifeline", "sandbox_event_secret": "x" * 40}
    base = {**base, "trusted_proxy_hops": 1}
    ok = Settings(**base, env="hosted_demo", cookie_secure=True, demo_password="y" * 44)
    with _pytest.raises(ValidationError):
        Settings(**{**base, "trusted_proxy_hops": 0}, env="hosted_demo", cookie_secure=True, demo_password="y" * 44)
    unset = {k: v for k, v in base.items() if k != "trusted_proxy_hops"}
    assert Settings(**unset, trusted_proxy_hops=None, env="hosted_demo", cookie_secure=True, demo_password="y" * 44).proxy_hops == 1
    assert Settings(**unset, trusted_proxy_hops=None).proxy_hops == 0
    assert ok.database_url == "postgresql+psycopg://u:p@db.internal/lifeline"
    for bad in (
        {"cookie_secure": False, "demo_password": "y" * 44},
        {"cookie_secure": True, "demo_password": None},
        {"cookie_secure": True, "demo_password": "short-password"},
        {"cookie_secure": True, "demo_password": "y" * 44, "fresh_account_password": "too-short-password"},
        {"cookie_secure": True, "demo_password": "y" * 44, "public_app_url": "http://insecure.example"},
        {"cookie_secure": True, "demo_password": "y" * 44, "public_app_url": "https://ok.example/with/path"},
        {"cookie_secure": True, "demo_password": "y" * 44, "ai_mode": "live", "anthropic_api_key": " ", "anthropic_model": "m"},
    ):
        with _pytest.raises(ValidationError):
            Settings(**base, env="hosted_demo", **bad)
    # Live AI is allowed on the hosted demo once a key and model are configured (server-side only).
    assert Settings(**base, env="hosted_demo", cookie_secure=True, demo_password="y" * 44, ai_mode="live", anthropic_api_key="k", anthropic_model="m").ai_mode == "live"
    # Production still refuses demo controls and demo credentials.
    with _pytest.raises(ValidationError):
        Settings(**base, env="production", cookie_secure=True, demo_controls_enabled=True)
    with _pytest.raises(ValidationError):
        Settings(**base, env="production", cookie_secure=True, demo_controls_enabled=False, demo_password="y" * 44)
    assert normalize_database_url("postgres://a@b/c") == "postgresql+psycopg://a@b/c"
    assert normalize_database_url("postgresql+psycopg://a@b/c") == "postgresql+psycopg://a@b/c"


def test_demo_password_rotation_from_settings(password, monkeypatch):
    """Changing LIFELINE_DEMO_PASSWORD and restarting rotates the demo accounts' password; data is untouched."""
    from pydantic import SecretStr

    from app.config import get_settings
    from app.seed.fixtures import sync_demo_passwords
    from tests.conftest import OWNER

    seed("primary")
    from tests.conftest import login as _login

    before = _login(OWNER, password)  # a session opened with the old password
    with get_sessionmaker()() as s:
        assert sync_demo_passwords(s) == 0  # already in sync
    new = "rotated-demo-passphrase-for-tests-0123"
    monkeypatch.setattr(get_settings(), "demo_password", SecretStr(new))
    with get_sessionmaker()() as s:
        assert sync_demo_passwords(s) == 3
    from fastapi.testclient import TestClient

    from app.main import app

    assert before.get("/api/auth/me").status_code == 401  # old sessions are revoked on rotation
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"email": OWNER, "password": password}).status_code == 401
    assert c.post("/api/auth/login", json={"email": OWNER, "password": new}).status_code == 200
    monkeypatch.setattr(get_settings(), "demo_password", SecretStr(password))
    with get_sessionmaker()() as s:
        assert sync_demo_passwords(s) == 3
