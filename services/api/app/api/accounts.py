"""Account lifecycle: sign up, sign in/out, email verification, password reset and change,
account deletion, workspaces (switch, private sample, start over).

Passwords are hashed with Argon2id; sessions are opaque HttpOnly cookies whose SHA-256
hash is stored; mutations need the per-session CSRF token. The tenant always comes from
the session. Responses to "forgot password" (and to sign-up when email is configured)
are identical for existing and unknown addresses.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import notify
from app.agents import assistant
from app.api import personal
from app.api.deps import Ctx, get_ctx, mutating_ctx, owner_ctx
from app.audit import chain as audit
from app.auth import codes, ratelimit
from app.auth import sessions as sess
from app.auth.security import hash_password, new_token, verify_password
from app.clock import real_now
from app.config import get_settings
from app.db import get_db
from app.domain import workspace as ws
from app.errors import ApiError
from app.models import (
    AccountToken,
    AssistantConversation,
    AuthSession,
    Business,
    Evidence,
    LoginAttempt,
    Membership,
    User,
    UserPreference,
)
from app.seed import fixtures

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]
H = timedelta(hours=1)


# Format check only: a well-formed address can still be mistyped or belong to someone else, so
# ownership is proven by the verification email (when email delivery is configured).
_EMAIL_LOCAL = re.compile(r"^[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*$")
_EMAIL_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_TLD = re.compile(r"^(?:[a-z]{2,63}|xn--[a-z0-9-]{1,59})$")
_USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,28}[a-z0-9]$")
_RESERVED_USERNAMES = {"admin", "administrator", "root", "support", "help", "lifeline", "system", "owner", "staff", "demo", "fresh", "security", "api", "null", "undefined"}


def email_problem(email: str) -> str | None:
    """Why `email` (already trimmed and lower-cased) is not a valid address, or None."""
    if not email:
        return "Enter your email address."
    if len(email) > 254 or email.count("@") != 1:
        return "Enter a valid email address, like name@example.com."
    local, domain = email.split("@")
    labels = domain.split(".")
    if not local or len(local) > 64 or not _EMAIL_LOCAL.match(local):
        return "The part before @ isn't valid. Check for spaces or unusual characters."
    if len(labels) < 2 or not all(_EMAIL_LABEL.match(lb) for lb in labels) or not _TLD.match(labels[-1]):
        return "The part after @ isn't a valid domain, like gmail.com or outlook.com."
    return None


def _norm_email(email: str) -> str:
    e = email.strip().lower()
    if (problem := email_problem(e)) is not None:
        raise ApiError(422, "EMAIL_INVALID", problem, extra={"fields": {"email": problem}})
    return e


def username_problem(username: str) -> str | None:
    if not username:
        return "Choose a username."
    if len(username) < 3 or len(username) > 30:
        return "Use 3 to 30 characters."
    if not _USERNAME.match(username) or any(d in username for d in ("..", "__", "--", ".-", "-.", "._", "_.", "-_", "_-")):
        return "Use lower-case letters, numbers, dots, dashes or underscores, starting and ending with a letter or number."
    if username in _RESERVED_USERNAMES:
        return "This username is reserved. Choose another."
    return None


# ------------------------------------------------------------------- session view


def me_view(db: Session, user: User, biz: Business, role: str, csrf: str) -> dict[str, Any]:
    s = get_settings()
    prefs = personal.prefs_view(personal.get_prefs(db, user))
    biz_currency = personal.business_currency_view(db, biz)
    setup = ws.setup_status(db, biz)
    needs_profile = biz.kind == "workspace" and role == "owner" and not (bool(biz.name) and ws.is_configured(biz))
    return {
        "preferences": prefs,
        "accounting_currency": biz_currency,
        # First run: choose demo or own business, then confirm the business profile (owners).
        "needs_onboarding": (not prefs["onboarded"]) or needs_profile or (biz.kind == "demo" and role == "owner" and not biz_currency["confirmed"]),
        "setup": setup,
        "user": {
            "id": str(user.id),
            "email": user.email,
            "username": user.username,
            "display_name": user.display_name,
            "email_verified": user.email_verified_at is not None,
            "account_kind": user.account_kind,
            "deletable": user.account_kind == "user",
        },
        "business": {
            "id": str(biz.id),
            "name": biz.name,
            "kind": biz.kind,
            "place": biz.place,
            "business_type": biz.business_type,
            "country": biz.country,
            "currency": biz.currency,
            "currency_minor_units": biz_currency["minor_units"],
            "timezone": biz.timezone,
            "timezone_confirmed": biz.timezone_confirmed_at is not None,
            "configured": ws.is_configured(biz),
            "kill_switch": biz.kill_switch,
            "sandbox_now": biz.sandbox_now.isoformat() if biz.sandbox_now else None,
            "simulated_clock": biz.sandbox_now is not None,
            "scenario": biz.scenario,
        },
        "workspaces": [
            {"id": str(b.id), "name": b.name or "Your business (not named yet)", "kind": b.kind, "role": m.role, "active": b.id == biz.id}
            for m, b in sess.memberships(db, user.id)
        ],
        "role": role,
        "csrf_token": csrf,
        "ai_mode": s.ai_mode,
        "assistant_label": assistant.mode_label(),
        "demo_controls": fixtures.demo_reset_allowed(),
        "email_delivery": "configured" if s.email_configured else "not_configured",
        "signup_enabled": s.signup_enabled,
    }


# ----------------------------------------------------------------------- sign in


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=200)


@router.post("/auth/login")
def login(body: LoginIn, request: Request, response: Response, db: DB) -> dict[str, Any]:
    s = get_settings()
    ident = body.email.strip().lower()
    # Sign in with the email address or the username chosen at sign-up. Failures are counted against
    # the account's email either way, so the two forms don't double the allowed guesses.
    by = User.email == ident if "@" in ident else User.username == ident
    user = db.execute(select(User).where(by, User.deleted_at.is_(None))).scalar_one_or_none()
    email = user.email if user is not None else ident
    window_start = real_now() - timedelta(minutes=s.login_window_minutes)
    failures = db.execute(
        select(func.count(LoginAttempt.id)).where(
            LoginAttempt.email == email, LoginAttempt.success.is_(False), LoginAttempt.created_at >= window_start
        )
    ).scalar_one()
    if failures >= s.login_max_failures:
        raise ApiError(429, "LOGIN_THROTTLED", f"Too many failed sign-ins. Try again in {s.login_window_minutes} minutes.")
    # Per-IP ceiling across all emails (spraying many accounts from one client).
    ratelimit.check(db, ratelimit.bucket("login-ip", ratelimit.client_ip(request)), 300, H, "Too many sign-in attempts from this network. Try again later.")
    ok = verify_password(user.password_hash if user else None, body.password)
    db.add(LoginAttempt(email=email, success=ok))
    if not ok or user is None:
        db.commit()
        raise ApiError(401, "LOGIN_FAILED", "Email, username or password is incorrect.")
    if user.email_verified_at is None and s.email_configured:
        db.commit()
        raise ApiError(403, "EMAIL_NOT_VERIFIED", "Confirm your email address first. Use the link we sent, or request a new one.")
    picked = sess.pick_business(db, user)
    if picked is None:
        db.commit()
        raise ApiError(403, "NO_ACTIVE_MEMBERSHIP", "This account has no active business.")
    m, biz = picked
    csrf = sess.issue(db, request, response, user, biz)
    audit.append(db, biz.id, event_type="auth.login", actor_type="user", actor_id=user.id, payload={"role": m.role})
    db.commit()
    return me_view(db, user, biz, m.role, csrf)


@router.post("/auth/logout")
def logout(response: Response, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, str]:
    ctx.session.revoked_at = real_now()
    audit.append(db, ctx.business.id, event_type="auth.logout", actor_type="user", actor_id=ctx.user.id)
    db.commit()
    response.delete_cookie(sess.SESSION_COOKIE, path="/")
    return {"status": "signed_out"}


@router.get("/auth/me")
def me(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return me_view(db, ctx.user, ctx.business, ctx.role, ctx.session.csrf_token)


# ----------------------------------------------------------------------- sign up


class RegisterIn(BaseModel):
    email: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)
    full_name: str = Field(min_length=1, max_length=120)
    username: str = Field(min_length=1, max_length=60)
    business_type: str | None = Field(default=None, max_length=100)
    business_name: str | None = Field(default=None, max_length=200)


def _clean_text(v: str | None, limit: int) -> str:
    # Collapse whitespace and drop control characters from names typed by people.
    return " ".join("".join(ch if ch.isprintable() else (" " if ch.isspace() else "") for ch in (v or "")).split())[:limit]


def _signup_field_errors(body: RegisterIn) -> tuple[dict[str, str], dict[str, str]]:
    """Validate every field at once so the form can show all problems inline. Returns (errors, cleaned)."""
    errors: dict[str, str] = {}
    email = body.email.strip().lower()
    if (p := email_problem(email)) is not None:
        errors["email"] = p
    name = _clean_text(body.full_name, 120)
    if not name:
        errors["full_name"] = "Enter your full name."
    username = body.username.strip().lower()
    if (p := username_problem(username)) is not None:
        errors["username"] = p
    try:
        sess.check_password_rules(body.password, email)
    except ApiError as exc:
        errors["password"] = exc.message
    if body.confirm_password != body.password:
        errors["confirm_password"] = "The passwords don't match."
    cleaned = {
        "email": email,
        "full_name": name,
        "username": username,
        "business_type": _clean_text(body.business_type, 100),
        "business_name": _clean_text(body.business_name, 200),
    }
    return errors, cleaned


class UsernameIn(BaseModel):
    username: str = Field(min_length=1, max_length=60)


@router.post("/auth/username-check")
def username_check(body: UsernameIn, request: Request, db: DB) -> dict[str, Any]:
    """Is a username free? Usernames are public handles, so "taken" reveals no secret; still rate limited."""
    ratelimit.check(db, ratelimit.bucket("username-check-ip", ratelimit.client_ip(request)), 120, H, "Too many checks. Try again later.")
    db.commit()
    u = body.username.strip().lower()
    if (p := username_problem(u)) is not None:
        return {"available": False, "message": p}
    taken = db.execute(select(User.id).where(User.username == u)).first() is not None
    return {"available": not taken, "message": "This username is already taken. Try another." if taken else "Available."}


def _code_lines(intro: str, code: str) -> list[str]:
    return [intro, "Your code is:", code, f"It expires in {codes.CODE_MINUTES} minutes and works once. Never share it: FlowSense staff will never ask for it."]


def _queue_code(bg: BackgroundTasks, user: User, purpose: str, code: str) -> None:
    """Send a code after the response (unauthenticated flows: timing must not reveal whether an account exists)."""
    subject, intro = {
        "verify_code": ("Your FlowSense sign-up code", "Use this code to confirm your email address and finish creating your FlowSense account."),
        "reset_code": ("Your FlowSense password reset code", "Someone asked to reset the password of your FlowSense account. Use this code to choose a new password."),
    }[purpose]
    bg.add_task(notify.send_in_background, user.id, purpose, user.email, subject, _code_lines(intro, code))


def _queue_notice(bg: BackgroundTasks, user: User, purpose: str, subject: str, lines: list[str]) -> None:
    if get_settings().email_configured:
        bg.add_task(notify.send_in_background, user.id, purpose, user.email, subject, lines)


@router.post("/auth/register")
def register(body: RegisterIn, request: Request, response: Response, bg: BackgroundTasks, db: DB) -> dict[str, Any]:
    s = get_settings()
    if not s.signup_enabled:
        raise ApiError(403, "SIGNUP_DISABLED", "Creating new accounts is turned off on this server.")
    errors, f = _signup_field_errors(body)
    if errors:
        # A form the user still has to correct does not count against the rate limits.
        raise ApiError(422, "SIGNUP_INVALID", "Some details need attention.", extra={"fields": errors})
    email, username = f["email"], f["username"]
    ip = ratelimit.client_ip(request)
    ratelimit.check(db, ratelimit.bucket("register-attempt-ip", ip), 60, H, "Too many sign-up attempts from this network. Try again in an hour.")
    ratelimit.check(db, ratelimit.bucket("register-email", email), 5, H, "Too many attempts for this email. Try again in an hour.")
    # Accounts *created* per network and globally (checked here, counted only once an account exists).
    ratelimit.check(db, ratelimit.bucket("register-ip", ip), 10, H, "Too many accounts were created from this network. Try again in an hour.", record=False)
    ratelimit.check(db, ratelimit.bucket("register-global"), s.signups_per_hour, H, "Too many new accounts are being created right now. Try again later.", record=False)
    holder = db.execute(select(User.email).where(User.username == username)).scalar_one_or_none()
    # Without email delivery, someone re-submitting their own sign-up (same email and username) gets the
    # duplicate-email answer below. With email delivery on, a taken username is always "taken", so the
    # answer never reveals which email owns a username (the duplicate-email answer there is generic).
    if holder is not None and (s.email_configured or holder != email):
        db.commit()
        msg = "This username is already taken. Try another."
        raise ApiError(409, "USERNAME_TAKEN", msg, extra={"fields": {"username": msg}})
    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    generic = {
        "status": "verify_code",
        "email": email,
        "message": f"If {email} can be used, we've sent a 6-digit code to it. Enter the code to finish creating your account. It expires in {codes.CODE_MINUTES} minutes.",
    }
    if existing is not None:
        hash_password(body.password)  # spend comparable time
        if s.email_configured:
            # Same answer as a new sign-up (no enumeration); the address owner learns what to do instead.
            notice = ratelimit.bucket("signup-notice", email)
            if existing.deleted_at is None and ratelimit.hits(db, notice, H) == 0:
                db.add(ratelimit.RateLimitHit(bucket=notice))  # at most one such notice an hour per address
                bg.add_task(notify.send_in_background, existing.id, "signup_existing", existing.email, "Someone tried to sign up with your email", [
                    "Someone (maybe you) tried to create a FlowSense account with this email address, which already has an account.",
                    "If it was you, sign in, or use \u201cForgot password\u201d to choose a new password. If it wasn't you, you can ignore this email.",
                ])
            db.commit()
            return generic  # identical response: no account enumeration
        db.commit()
        msg = "An account already uses this email. Sign in instead, or reset your password."
        raise ApiError(409, "SIGNUP_UNAVAILABLE", msg, extra={"fields": {"email": msg}})
    user = User(email=email, username=username, display_name=f["full_name"], password_hash=hash_password(body.password), account_kind="user")
    db.add(user)
    try:
        db.flush()
    except IntegrityError:  # a concurrent sign-up took the email or username first
        db.rollback()
        msg = "This username or email was just taken. Try another username."
        raise ApiError(409, "USERNAME_TAKEN", msg, extra={"fields": {"username": msg}}) from None
    biz = ws.create_workspace(db, user, f["business_name"])
    if f["business_type"]:
        biz.business_type = f["business_type"]
    db.add(ratelimit.RateLimitHit(bucket=ratelimit.bucket("register-global")))
    db.add(ratelimit.RateLimitHit(bucket=ratelimit.bucket("register-ip", ip)))
    audit.append(db, biz.id, event_type="account.created", actor_type="user", actor_id=user.id, payload={"verification": "email" if s.email_configured else "not_configured"})
    if s.email_configured:
        _queue_code(bg, user, "verify_code", codes.issue(db, user, "verify_code"))  # sent after the response, like the duplicate branch
        db.commit()
        return generic
    # No email delivery on this server: the account is usable now and marked unverified.
    csrf = sess.issue(db, request, response, user, biz)
    db.commit()
    return {"status": "signed_in", **me_view(db, user, biz, "owner", csrf)}


class EmailIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class VerifyCodeIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=1, max_length=12)
    # The password chosen at sign-up. Proving the inbox alone is not enough: if someone else registered
    # this address first with their own password, the inbox owner must use "Forgot password" instead,
    # which replaces that password and signs everyone out.
    password: str = Field(min_length=1, max_length=200)


VERIFY_WRONG = (
    "That code or password isn't right, or the code has expired. Check the latest email and the password you chose. "
    "If you didn't create this account, use “Forgot password” instead."
)


def _code_limits(db: Session, purpose: str, email: str, ip: str) -> None:
    """Limits for typing codes, all keyed on what was typed (never on whether the account exists):
    5 failed tries per (email, network) per 10 minutes; requests per network; per email per hour and day."""
    codes.guard(db, purpose, email, ip)
    ratelimit.check(db, ratelimit.bucket("code-ip", ip), 60, H, "Too many attempts from this network. Try again later.")
    # Per-email caps count only wrong tries (recorded in codes.record_miss), so correct tries never use them up.
    ratelimit.check(db, ratelimit.bucket("code-email", email), 20, H, "Too many wrong tries for this email. Try again in an hour.", record=False)
    ratelimit.check(db, ratelimit.bucket("code-email-day", email), 60, timedelta(days=1), "Too many wrong tries for this email today. Try again tomorrow.", record=False)
    db.commit()


def _check_code(db: Session, user: User | None, purpose: str, code: str, email: str, client: str, wrong: str = codes.WRONG) -> None:
    """codes.check with one uniform failure: any miss is counted against (email, network) and answered alike."""
    try:
        codes.check(db, user, purpose, code)
    except ApiError as exc:
        if exc.code != "CODE_INVALID":
            raise
        codes.record_miss(db, purpose, email, client)
        raise ApiError(400, "CODE_INVALID", wrong) from None


def _active_user(db: Session, email: str) -> User | None:
    return db.execute(select(User).where(User.email == email, User.deleted_at.is_(None))).scalar_one_or_none()


@router.post("/auth/verify-code")
def verify_code(body: VerifyCodeIn, request: Request, response: Response, bg: BackgroundTasks, db: DB) -> dict[str, Any]:
    """Confirm the sign-up email with the 6-digit code and the account password, then sign in and welcome."""
    email = _norm_email(body.email)
    ip = ratelimit.client_ip(request)
    _code_limits(db, "verify_code", email, ip)
    user = _active_user(db, email)
    ok_pw = verify_password(user.password_hash if user else None, body.password)
    # A wrong password gets exactly the same answer (and counts the same) as a wrong code.
    _check_code(db, user if user is not None and user.email_verified_at is None and ok_pw else None, "verify_code", body.code, email, ip, VERIFY_WRONG)
    assert user is not None
    user.email_verified_at = real_now()
    picked = sess.pick_business(db, user)
    if picked is None:
        db.commit()
        raise ApiError(403, "NO_ACTIVE_MEMBERSHIP", "This account has no active business.")
    m, biz = picked
    csrf = sess.issue(db, request, response, user, biz)
    audit.append(db, biz.id, event_type="account.email_verified", actor_type="user", actor_id=user.id)
    _queue_notice(bg, user, "account_created", "Welcome to FlowSense: your account is ready", [
        f"Hi {user.display_name}, your FlowSense account is ready.",
        f"Sign in with {user.email}" + (f" or your username {user.username}" if user.username else "") + ".",
        "FlowSense is a sandbox: it uses synthetic money only and never connects to a real bank.",
    ])
    db.commit()
    return {"status": "signed_in", **me_view(db, user, biz, m.role, csrf)}


@router.post("/auth/verify-email/resend")
def resend_verification(body: EmailIn, request: Request, bg: BackgroundTasks, db: DB) -> dict[str, str]:
    s = get_settings()
    if not s.email_configured:
        raise ApiError(503, "EMAIL_NOT_CONFIGURED", "Email delivery is not configured on this server, so no codes can be sent.")
    email = _norm_email(body.email)
    ratelimit.check(db, ratelimit.bucket("verify-resend-ip", ratelimit.client_ip(request)), 20, H, "Too many requests. Try again later.")
    user = _active_user(db, email)
    if user is not None and user.email_verified_at is None and codes.can_send(db, user, "verify_code") is None:
        _queue_code(bg, user, "verify_code", codes.issue(db, user, "verify_code"))
    db.commit()
    return {"status": "verify_code", "message": f"If {email} has an unconfirmed account, we've sent a new code (older codes stop working). You can ask again after a minute."}


# ---------------------------------------------------------------- password reset


@router.post("/auth/password/forgot")
def forgot_password(body: EmailIn, request: Request, bg: BackgroundTasks, db: DB) -> dict[str, str]:
    s = get_settings()
    if not s.email_configured:
        # Says nothing about whether the account exists.
        raise ApiError(
            503,
            "EMAIL_NOT_CONFIGURED",
            "Password reset emails are not available on this server because email delivery is not configured. "
            "Contact whoever runs this FlowSense sandbox.",
        )
    email = _norm_email(body.email)
    ratelimit.check(db, ratelimit.bucket("forgot-ip", ratelimit.client_ip(request)), 20, H, "Too many reset requests from this network. Try again later.")
    user = _active_user(db, email)
    if user is not None and user.account_kind != "fixture" and codes.can_send(db, user, "reset_code") is None:
        # Sent after the response so timing does not reveal whether the account exists.
        _queue_code(bg, user, "reset_code", codes.issue(db, user, "reset_code"))
    db.commit()
    return {"status": "reset_code", "message": f"If an account exists for {email}, we've sent a 6-digit code to it. It expires in {codes.CODE_MINUTES} minutes."}


class ResetIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    code: str = Field(min_length=1, max_length=12)
    password: str = Field(min_length=1, max_length=200)
    confirm_password: str = Field(min_length=1, max_length=200)


@router.post("/auth/password/reset")
def reset_password(body: ResetIn, request: Request, response: Response, bg: BackgroundTasks, db: DB) -> dict[str, str]:
    email = _norm_email(body.email)
    ip = ratelimit.client_ip(request)
    if body.password != body.confirm_password:
        raise ApiError(422, "PASSWORDS_DIFFER", "The passwords don't match.", extra={"fields": {"confirm_password": "The passwords don't match."}})
    # Checked for every address before any lookup, so the answer is the same whether or not an account exists.
    try:
        sess.check_password_rules(body.password, email)
    except ApiError as exc:
        raise ApiError(422, exc.code, exc.message, extra={"fields": {"password": exc.message}}) from None
    _code_limits(db, "reset_code", email, ip)
    user = _active_user(db, email)
    _check_code(db, user if user is not None and user.account_kind != "fixture" else None, "reset_code", body.code, email, ip)
    assert user is not None
    user.password_hash = hash_password(body.password)
    user.password_changed_at = real_now()
    if user.email_verified_at is None:
        user.email_verified_at = real_now()  # they proved control of the inbox
    sess.revoke_all(db, user.id)  # every existing session ends
    db.execute(update(AccountToken).where(AccountToken.user_id == user.id, AccountToken.used_at.is_(None)).values(used_at=real_now()))
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == user.email, LoginAttempt.success.is_(False)))
    for _m, b in sess.memberships(db, user.id)[:1]:
        audit.append(db, b.id, event_type="account.password_reset", actor_type="user", actor_id=user.id, payload={"sessions_revoked": True})
    _queue_notice(bg, user, "password_changed", "Your FlowSense password was changed", [
        "The password of your FlowSense account was just reset, and every device was signed out.",
        "If this wasn't you, reset your password again straight away.",
    ])
    db.commit()
    response.delete_cookie(sess.SESSION_COOKIE, path="/")
    return {"status": "password_reset", "message": "Your password was changed and you were signed out everywhere. Sign in with the new password."}


class ReauthIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)


def _reauth(db: Session, ctx: Ctx, password: str) -> None:
    if ctx.user.account_kind != "user":
        raise ApiError(403, "ACCOUNT_PROTECTED", "The password of a shared demo or test account is managed by the server operator.")
    ratelimit.check(db, ratelimit.bucket("pw-change", str(ctx.user.id)), 10, H, "Too many attempts. Try again later.")
    if not verify_password(ctx.user.password_hash, password):
        db.commit()
        raise ApiError(403, "REAUTH_FAILED", "Your current password is incorrect.", extra={"fields": {"current_password": "Your current password is incorrect."}})


@router.post("/account/password/code")
def password_change_code(body: ReauthIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, str]:
    """Signed-in password change, step 1: re-enter the current password, then a code goes to your email.
    Sent while you wait, so the answer says truthfully whether the email provider accepted it."""
    _reauth(db, ctx, body.current_password)
    if not get_settings().email_configured:
        raise ApiError(503, "EMAIL_NOT_CONFIGURED", "Email delivery isn't configured on this server, so no code can be sent. You can change the password with your current password alone.")
    if (why := codes.can_send(db, ctx.user, "change_code")) is not None:
        raise ApiError(429, "CODE_RATE_LIMITED", why)
    code = codes.issue(db, ctx.user, "change_code")
    sent = notify.send(db, user_id=ctx.user.id, purpose="change_code", to=ctx.user.email, subject="Your FlowSense password change code",
                       lines=_code_lines("Use this code to confirm changing the password of your FlowSense account.", code))
    if sent.status != "sent":
        db.execute(update(AccountToken).where(AccountToken.user_id == ctx.user.id, AccountToken.purpose == "change_code", AccountToken.used_at.is_(None)).values(used_at=real_now()))
        db.commit()
        raise ApiError(502, "EMAIL_NOT_SENT", "We couldn't send the code: the email service didn't accept the message. Nothing was changed. Try again in a few minutes.")
    db.commit()
    return {"status": "code_sent", "message": f"We sent a 6-digit code to {ctx.user.email}. It expires in {codes.CODE_MINUTES} minutes."}


class ChangePasswordIn(BaseModel):
    current_password: str = Field(min_length=1, max_length=200)
    new_password: str = Field(min_length=1, max_length=200)
    code: str | None = Field(default=None, max_length=12)


@router.post("/account/password")
def change_password(body: ChangePasswordIn, bg: BackgroundTasks, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, str]:
    """Step 2: current password again, the emailed code (when email is configured) and the new password."""
    _reauth(db, ctx, body.current_password)
    try:
        sess.check_password_rules(body.new_password, ctx.user.email)
    except ApiError as exc:
        raise ApiError(422, exc.code, exc.message, extra={"fields": {"new_password": exc.message}}) from None
    if get_settings().email_configured:
        codes.guard(db, "change_code", ctx.user.email, str(ctx.user.id))
        _check_code(db, ctx.user, "change_code", body.code or "", ctx.user.email, str(ctx.user.id))
    ctx.user.password_hash = hash_password(body.new_password)
    ctx.user.password_changed_at = real_now()
    sess.revoke_all(db, ctx.user.id, except_hash=ctx.session.token_hash)
    audit.append(db, ctx.business.id, event_type="account.password_changed", actor_type="user", actor_id=ctx.user.id, payload={"other_sessions_revoked": True})
    _queue_notice(bg, ctx.user, "password_changed", "Your FlowSense password was changed", [
        "The password of your FlowSense account was just changed. Other devices were signed out.",
        "If this wasn't you, reset your password straight away.",
    ])
    db.commit()
    return {"status": "changed", "message": "Password changed. Other devices were signed out."}


# ----------------------------------------------------------------- delete account


def deletion_plan(db: Session, user: User) -> dict[str, Any]:
    """What deleting this account would do. Refuses if another person depends on a workspace only this user owns."""
    archive, leave, blocked = [], [], []
    for m, b in sess.memberships(db, user.id):
        others = db.execute(
            select(Membership.user_id, Membership.role).where(Membership.business_id == b.id, Membership.active.is_(True), Membership.user_id != user.id)
        ).all()
        other_owners = [o for o in others if o.role == "owner"]
        label = b.name or "Your business (not named yet)"
        if m.role == "owner" and others and not other_owners:
            blocked.append(label)
        elif m.role == "owner" and not others:
            archive.append(label)
        else:
            leave.append(label)
    return {"archive": archive, "leave": leave, "blocked": blocked}


@router.get("/account/deletion")
def deletion_preview(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    plan = deletion_plan(db, ctx.user)
    return {
        "allowed": ctx.user.account_kind == "user" and not plan["blocked"],
        "protected": ctx.user.account_kind != "user",
        **plan,
        "deleted": [
            "Your name, email address and password (the account can no longer sign in)",
            "Your display preferences",
            "Your AI assistant conversations",
            "The text of documents uploaded to workspaces that only you own",
            "All your sessions, on every device",
        ],
        "retained": [
            "Ledger entries, receipts and the audit trail of archived workspaces. They are append-only by design so the "
            "history stays verifiable; they are archived, hidden from everyone and refer to you only as a deleted user.",
        ],
    }


class DeleteIn(BaseModel):
    password: str = Field(min_length=1, max_length=200)
    confirm: Literal["DELETE MY ACCOUNT"]


@router.post("/account/delete")
def delete_account(body: DeleteIn, response: Response, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, str]:
    user = ctx.user
    if user.account_kind != "user":
        raise ApiError(403, "ACCOUNT_PROTECTED", "Shared demo and test accounts can't be deleted.")
    ratelimit.check(db, ratelimit.bucket("delete-account", str(user.id)), 5, H, "Too many attempts. Try again later.")
    if not verify_password(user.password_hash, body.password):  # recent authentication
        db.commit()
        raise ApiError(403, "REAUTH_FAILED", "Your password is incorrect.")
    plan = deletion_plan(db, user)
    if plan["blocked"]:
        raise ApiError(
            409,
            "OWNERSHIP_TRANSFER_REQUIRED",
            "Other people still use a workspace that only you own: " + ", ".join(plan["blocked"]) + ". Remove them or make another person an owner first.",
        )
    now = real_now()
    for m, b in sess.memberships(db, user.id):
        others = db.execute(select(func.count(Membership.id)).where(Membership.business_id == b.id, Membership.active.is_(True), Membership.user_id != user.id)).scalar_one()
        if m.role == "owner" and others == 0:
            b.archived_at = now
            db.execute(update(Evidence).where(Evidence.business_id == b.id).values(content_text="[removed when the owner deleted their account]"))
            audit.append(db, b.id, event_type="workspace.archived", actor_type="user", actor_id=user.id, payload={"reason": "Owner deleted their account"})
        else:
            audit.append(db, b.id, event_type="membership.left", actor_type="user", actor_id=user.id, payload={"reason": "Member deleted their account"})
        m.active = False
    db.execute(delete(AssistantConversation).where(AssistantConversation.user_id == user.id))
    db.execute(delete(UserPreference).where(UserPreference.user_id == user.id))
    db.execute(delete(AccountToken).where(AccountToken.user_id == user.id))
    db.execute(delete(LoginAttempt).where(LoginAttempt.email == user.email))
    sess.revoke_all(db, user.id)
    user.email = f"deleted-{user.id}@deleted.invalid"
    user.display_name = "Deleted user"
    user.username = None  # the handle becomes available again
    user.password_hash = hash_password(new_token())  # unusable
    user.deleted_at = now
    user.active_business_id = None
    db.commit()
    response.delete_cookie(sess.SESSION_COOKIE, path="/")
    return {"status": "deleted", "message": "Your account was deleted."}


# -------------------------------------------------------------------- workspaces


@router.post("/workspaces/{business_id}/switch")
def switch_workspace(business_id: str, request: Request, response: Response, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    target = next(((m, b) for m, b in sess.memberships(db, ctx.user.id) if str(b.id) == business_id), None)
    if target is None:
        raise ApiError(404, "NOT_FOUND", "Workspace not found.")
    m, b = target
    csrf = sess.issue(db, request, response, ctx.user, b)
    audit.append(db, b.id, event_type="auth.workspace_switched", actor_type="user", actor_id=ctx.user.id, payload={"role": m.role})
    db.commit()
    return me_view(db, ctx.user, b, m.role, csrf)


@router.post("/workspaces/sample")
def open_sample(request: Request, response: Response, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """'Try the demo': a private sample workspace for this user. It never touches their own workspace."""
    if not fixtures.demo_reset_allowed():
        raise ApiError(403, "DEMO_DISABLED", "Sample workspaces are turned off on this server.")
    user = ctx.user
    key = ws.sample_key(user.id)
    biz = db.execute(select(Business).where(Business.fixture_key == key, Business.archived_at.is_(None))).scalar_one_or_none()
    if biz is None:
        ratelimit.check(db, ratelimit.bucket("sample-create", str(user.id)), 5, timedelta(days=1), "You can create at most 5 sample workspaces a day.")
        db.commit()
        biz = fixtures.seed_fixture(db, "usd_primary", reset_by=user.id, key=key, owner_user=db.get(User, user.id))
    fresh_user = db.get(User, ctx.user.id)
    assert fresh_user is not None
    user = fresh_user
    csrf = sess.issue(db, request, response, user, biz)
    db.commit()
    return me_view(db, user, biz, "owner", csrf)


class StartOverIn(BaseModel):
    confirm: Literal["START OVER"]


@router.post("/workspaces/start-over")
def start_over(body: StartOverIn, request: Request, response: Response, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    """Archive (never delete) this workspace and create a new empty one. Only for your own business workspace."""
    biz = ctx.business
    if biz.kind != "workspace":
        raise ApiError(409, "NOT_A_WORKSPACE", "Only your own business workspace can be started over. Sample workspaces use Demo scenarios.")
    others = db.execute(select(func.count(Membership.id)).where(Membership.business_id == biz.id, Membership.active.is_(True), Membership.user_id != ctx.user.id)).scalar_one()
    if others:
        raise ApiError(409, "WORKSPACE_SHARED", "Other people use this workspace, so it can't be archived from here.")
    ratelimit.check(db, ratelimit.bucket("start-over", str(ctx.user.id)), 5, timedelta(days=1), "You can start over at most 5 times a day.")
    biz.archived_at = real_now()
    db.execute(update(Membership).where(Membership.business_id == biz.id).values(active=False))
    db.execute(update(AuthSession).where(AuthSession.business_id == biz.id, AuthSession.revoked_at.is_(None)).values(revoked_at=real_now()))
    audit.append(db, biz.id, event_type="workspace.archived", actor_type="user", actor_id=ctx.user.id, payload={"reason": "Owner started over"})
    db.execute(delete(AssistantConversation).where(AssistantConversation.business_id == biz.id))
    new = ws.create_workspace(db, ctx.user)
    csrf = sess.issue(db, None, response, ctx.user, new)
    db.commit()
    return me_view(db, ctx.user, new, "owner", csrf)
