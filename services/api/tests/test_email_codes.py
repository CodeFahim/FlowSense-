"""6-digit email codes (expiry, single use, resend invalidation, attempt and resend limits, no
enumeration), the signed-in password change with re-authentication, and the Gmail SMTP sender
(fake SMTP server: nothing leaves the machine)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

from app import notify
from app.auth import codes
from app.clock import real_now
from app.config import get_settings
from app.db import get_sessionmaker
from app.models import AccountToken, EmailDelivery, User
from tests import test_accounts
from tests.conftest import Api, signup
from tests.test_accounts import GOOD_PW, _client, _code, _email, _register

email_on = test_accounts.email_on  # shared pytest fixture


def _user_id(e: str) -> uuid.UUID:
    with get_sessionmaker()() as s:
        return s.execute(select(User.id).where(User.email == e)).scalar_one()


def _age_codes(e: str, *, seconds: int) -> None:
    """Pretend the codes were created `seconds` ago (to pass the resend wait or reach expiry)."""
    with get_sessionmaker()() as s:
        s.execute(update(AccountToken).where(AccountToken.user_id == _user_id(e)).values(created_at=AccountToken.created_at - timedelta(seconds=seconds), expires_at=AccountToken.expires_at - timedelta(seconds=seconds)))
        s.commit()


def test_code_expires_after_ten_minutes(email_on):
    e = _email()
    _register(_client(), e)
    code = _code(email_on[-1]["text"])
    assert "expires in 10 minutes" in email_on[-1]["text"]
    _age_codes(e, seconds=10 * 60 + 1)
    r = _client().post("/api/auth/verify-code", json={"email": e, "code": code, "password": GOOD_PW})
    assert r.status_code == 400 and r.json()["error"]["code"] == "CODE_INVALID"


def test_resend_invalidates_the_old_code_and_is_rate_limited(email_on):
    e = _email()
    _register(_client(), e)
    old = _code(email_on[-1]["text"])
    n = len(email_on)
    # Too soon: no new email (and the answer is the same generic one).
    r = _client().post("/api/auth/verify-email/resend", json={"email": e})
    assert r.status_code == 200 and len(email_on) == n
    _age_codes(e, seconds=61)
    _client().post("/api/auth/verify-email/resend", json={"email": e})
    new = _code(email_on[-1]["text"])
    assert len(email_on) == n + 1
    if new != old:
        assert _client().post("/api/auth/verify-code", json={"email": e, "code": old, "password": GOOD_PW}).status_code == 400
    assert _client().post("/api/auth/verify-code", json={"email": e, "code": new, "password": GOOD_PW}).status_code == 200
    # Unknown addresses get the identical answer and no email.
    unknown = _client().post("/api/auth/verify-email/resend", json={"email": _email()})
    assert unknown.status_code == 200 and len(email_on) == n + 2  # +1 = the welcome email after verification


def _from(ip: str):
    c = _client(ip)
    return c


def _verify(ip: str, e: str, code: str, pw: str = GOOD_PW):
    return _from(ip).post("/api/auth/verify-code", json={"email": e, "code": code, "password": pw})


def test_wrong_guesses_are_limited_per_network_and_in_total(email_on):
    e = _email()
    _register(_client(), e)
    code = _code(email_on[-1]["text"])
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"
    attacker = "203.0.113.201"
    for _ in range(codes.ATTEMPTS_PER_NETWORK):
        assert _verify(attacker, e, wrong).status_code == 400
    # That network is stopped for this email, but the real owner on another network can still use the code.
    assert _verify(attacker, e, code).json()["error"]["code"] == "CODE_ATTEMPTS"
    assert _verify("198.51.100.77", e, code).status_code == 200
    # In total, a code dies after MAX_ATTEMPTS wrong guesses, whatever the networks.
    e2 = _email()
    _register(_client(), e2)
    code2 = _code(email_on[-1]["text"])
    wrong2 = f"{(int(code2) + 1) % 1_000_000:06d}"
    for n in range(codes.MAX_ATTEMPTS // codes.ATTEMPTS_PER_NETWORK):
        for _ in range(codes.ATTEMPTS_PER_NETWORK):
            _verify(f"192.0.2.{100 + n}", e2, wrong2)
    assert _verify("198.51.100.78", e2, code2).status_code == 400


def test_lockout_and_messages_do_not_reveal_which_accounts_exist(email_on):
    real = _email()
    _register(_client(), real)
    code = _code(email_on[-1]["text"])
    unknown = _email()
    net = "203.0.113.202"
    # Five misses lock out an unknown address exactly like a real one.
    for e in (real, unknown):
        answers = [_verify(net, e, "000000").json()["error"] for _ in range(codes.ATTEMPTS_PER_NETWORK)]
        assert {a["code"] for a in answers} == {"CODE_INVALID"}
        assert _verify(net, e, "000000").json()["error"]["code"] == "CODE_ATTEMPTS"
    # Malformed codes, unknown accounts and wrong passwords all get the same message.
    msgs = {
        _verify("198.51.100.90", real, "12ab56").json()["error"]["message"],
        _verify("198.51.100.91", _email(), "123456").json()["error"]["message"],
        _verify("198.51.100.92", real, code, "not the password at all").json()["error"]["message"],
    }
    assert len(msgs) == 1


def test_pre_registered_address_is_taken_back_by_the_inbox_owner(email_on):
    victim = _email()
    attacker_pw = "attacker chose this passphrase"
    # Someone registers the victim's address with their own password first.
    _client().post("/api/auth/register", json=signup(victim, attacker_pw))
    code = _code(email_on[-1]["text"])
    # The victim signs up too: same generic answer, and the inbox gets a notice instead of silence.
    again = _client().post("/api/auth/register", json=signup(victim, GOOD_PW, username="victim_" + uuid.uuid4().hex[:6]))
    assert again.status_code == 200 and again.json()["status"] == "verify_code"
    assert email_on[-1]["subject"] == "Someone tried to sign up with your email"
    # The code alone, with the victim's password, does not hand over the attacker's account.
    r = _client().post("/api/auth/verify-code", json={"email": victim, "code": code, "password": GOOD_PW})
    assert r.status_code == 400 and "Forgot password" in r.json()["error"]["message"]
    # "Forgot password" proves the inbox, replaces the password and makes the account the victim's.
    _age(victim)
    _client().post("/api/auth/password/forgot", json={"email": victim})
    reset = _code(email_on[-1]["text"])
    ok = _client().post("/api/auth/password/reset", json={"email": victim, "code": reset, "password": GOOD_PW, "confirm_password": GOOD_PW})
    assert ok.status_code == 200
    assert _client().post("/api/auth/login", json={"email": victim, "password": attacker_pw}).status_code == 401
    assert _client().post("/api/auth/login", json={"email": victim, "password": GOOD_PW}).status_code == 200


def _age(e: str) -> None:
    _age_codes(e, seconds=61)


def test_reset_answers_are_the_same_for_unknown_addresses(email_on):
    known = _email()
    _register(_client(), known)
    weak = {"code": "123456", "password": "short", "confirm_password": "short"}
    a = _client().post("/api/auth/password/reset", json={"email": known, **weak})
    b = _client().post("/api/auth/password/reset", json={"email": _email(), **weak})
    assert a.status_code == b.status_code == 422 and a.json() == b.json()
    good = {"code": "123456", "password": "a perfectly fine passphrase", "confirm_password": "a perfectly fine passphrase"}
    a2 = _client().post("/api/auth/password/reset", json={"email": known, **good})
    b2 = _client().post("/api/auth/password/reset", json={"email": _email(), **good})
    assert a2.status_code == b2.status_code == 400 and a2.json() == b2.json()


def test_codes_are_purpose_bound(email_on):
    e = _email()
    _register(_client(), e)
    signup_code = _code(email_on[-1]["text"])
    r = _client().post("/api/auth/password/reset", json={"email": e, "code": signup_code, "password": "another long passphrase 1", "confirm_password": "another long passphrase 1"})
    assert r.status_code == 400 and r.json()["error"]["code"] == "CODE_INVALID"


def test_signed_in_password_change_needs_current_password_and_a_code(email_on):
    e = _email()
    c = _client()
    _register(c, e)
    me = c.post("/api/auth/verify-code", json={"email": e, "code": _code(email_on[-1]["text"]), "password": GOOD_PW}).json()
    api = Api(c, me["csrf_token"], me)
    other = _client()
    assert other.post("/api/auth/login", json={"email": e, "password": GOOD_PW}).status_code == 200
    bad = api.post("/api/account/password/code", {"current_password": "wrong password"})
    assert bad.status_code == 403 and "current_password" in bad.json()["error"]["fields"]
    sent = api.post("/api/account/password/code", {"current_password": GOOD_PW})
    assert sent.status_code == 200 and sent.json()["status"] == "code_sent"
    code = _code(email_on[-1]["text"])
    new_pw = "my new long passphrase 77"
    no_code = api.post("/api/account/password", {"current_password": GOOD_PW, "new_password": new_pw})
    assert no_code.status_code == 400 and no_code.json()["error"]["code"] == "CODE_INVALID"
    ok = api.post("/api/account/password", {"current_password": GOOD_PW, "new_password": new_pw, "code": code})
    assert ok.status_code == 200
    assert api.get("/api/auth/me").status_code == 200 and other.get("/api/auth/me").status_code == 401
    assert email_on[-1]["subject"] == "Your FlowSense password was changed"


def test_failed_delivery_is_reported_and_the_code_is_withdrawn(email_on, monkeypatch):
    e = _email()
    c = _client()
    _register(c, e)
    me = c.post("/api/auth/verify-code", json={"email": e, "code": _code(email_on[-1]["text"]), "password": GOOD_PW}).json()
    api = Api(c, me["csrf_token"], me)

    def broken(*_a: Any) -> str:
        raise OSError("smtp down")

    monkeypatch.setattr(notify, "transport", broken)
    r = api.post("/api/account/password/code", {"current_password": GOOD_PW})
    assert r.status_code == 502 and r.json()["error"]["code"] == "EMAIL_NOT_SENT" and "sent" not in r.json().get("status", "")
    with get_sessionmaker()() as s:
        live = s.execute(select(AccountToken).where(AccountToken.user_id == _user_id(e), AccountToken.purpose == "change_code", AccountToken.used_at.is_(None))).first()
        assert live is None
        assert s.execute(select(EmailDelivery.status).where(EmailDelivery.user_id == _user_id(e), EmailDelivery.purpose == "change_code")).scalar_one() == "failed"


class FakeSMTP:
    sent: list[Any] = []
    logins: list[tuple[str, str]] = []

    def __init__(self, host: str, port: int, timeout: float, context: Any = None):
        self.host, self.port = host, port

    def __enter__(self) -> FakeSMTP:
        return self

    def __exit__(self, *a: Any) -> None:
        return None

    def login(self, user: str, pw: str) -> None:
        FakeSMTP.logins.append((user, pw))

    def send_message(self, msg: Any) -> None:
        FakeSMTP.sent.append(msg)


def test_gmail_smtp_sender_uses_the_no_reply_name_and_env_credentials(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "smtp")
    monkeypatch.setattr(s, "email_from", "teamlifeline01@gmail.com")
    monkeypatch.setattr(s, "smtp_username", "teamlifeline01@gmail.com")
    monkeypatch.setattr(s, "smtp_password", SecretStr("abcd efgh ijkl mnop"))
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", FakeSMTP)
    assert s.email_configured is True
    FakeSMTP.sent.clear()
    FakeSMTP.logins.clear()
    with get_sessionmaker()() as db:
        r = notify.send(db, user_id=None, purpose="test", to="owner@example.test", subject="Your FlowSense sign-up code", lines=["Use this code", "123456"])
        db.rollback()
    assert r.status == "sent"
    msg = FakeSMTP.sent[0]
    assert "FlowSense" in msg["From"] and "No Reply" in str(msg["From"]) and "teamlifeline01@gmail.com" in msg["From"]
    assert FakeSMTP.logins == [("teamlifeline01@gmail.com", "abcdefghijklmnop")]  # spaces in Gmail app passwords removed
    body = msg.get_body(preferencelist=("plain",)).get_content()
    assert "isn't monitored" in body and "123456" in body and "blocks" not in body.lower()


def test_email_not_configured_without_password(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "smtp")
    monkeypatch.setattr(s, "email_from", "teamlifeline01@gmail.com")
    monkeypatch.setattr(s, "smtp_username", "teamlifeline01@gmail.com")
    monkeypatch.setattr(s, "smtp_password", None)
    assert s.email_configured is False


@pytest.mark.parametrize("purpose", codes.PURPOSES)
def test_issued_codes_are_six_random_digits(purpose):
    e = _email()
    _register(_client(), e)
    with get_sessionmaker()() as db:
        user = db.execute(select(User).where(User.email == e)).scalar_one()
        seen = {codes.issue(db, user, purpose) for _ in range(20)}
        db.rollback()
    assert all(len(c) == 6 and c.isdigit() for c in seen) and len(seen) > 15
    assert real_now()  # clock import used


def test_brevo_sender_posts_over_https_and_reports_failures(monkeypatch):
    import httpx

    s = get_settings()
    monkeypatch.setattr(s, "email_provider", "brevo")
    monkeypatch.setattr(s, "email_from", "teamlifeline01@gmail.com")
    monkeypatch.setattr(s, "brevo_api_key", SecretStr("xkeysib-test-key"))
    assert s.email_configured is True
    sent: list[dict[str, Any]] = []

    def fake_post(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float, follow_redirects: bool) -> httpx.Response:
        sent.append({"url": url, "headers": headers, "json": json})
        return httpx.Response(201, json={"messageId": "<abc@smtp-relay.brevo.com>"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", fake_post)
    with get_sessionmaker()() as db:
        r = notify.send(db, user_id=None, purpose="test", to="owner@example.test", subject="Your FlowSense sign-up code", lines=["Use this code", "123456"])
        db.rollback()
    assert r.status == "sent"
    body = sent[0]["json"]
    assert sent[0]["url"] == "https://api.brevo.com/v3/smtp/email" and sent[0]["headers"]["api-key"] == "xkeysib-test-key"
    assert body["sender"] == {"name": "FlowSense — No Reply", "email": "teamlifeline01@gmail.com"} and body["to"] == [{"email": "owner@example.test"}]
    assert "123456" in body["textContent"] and "isn't monitored" in body["textContent"]

    def refused(url: str, headers: dict[str, str], json: dict[str, Any], timeout: float, follow_redirects: bool) -> httpx.Response:
        return httpx.Response(401, json={"code": "unauthorized", "message": "Key not found"}, request=httpx.Request("POST", url))

    monkeypatch.setattr(notify.httpx, "post", refused)
    with get_sessionmaker()() as db:
        r = notify.send(db, user_id=None, purpose="test", to="owner@example.test", subject="x", lines=["y"])
        row = db.execute(select(EmailDelivery.status, EmailDelivery.detail).order_by(EmailDelivery.id.desc()).limit(1)).one()
        db.rollback()
    assert r.status == "failed" and row.status == "failed" and "xkeysib" not in (row.detail or "")
