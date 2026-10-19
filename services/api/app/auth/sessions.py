"""Session issuing and password rules shared by sign-in, sign-up, reset, workspace switch and demo reset."""

from __future__ import annotations

import uuid
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.auth.security import new_token, token_hash
from app.clock import real_now
from app.config import get_settings
from app.errors import ApiError
from app.models import AuthSession, Business, Membership, User

SESSION_COOKIE = "flowsense_session"

# A short list of passwords that are always refused, on top of the length rule.
_COMMON = {
    "password1234", "123456789012", "qwertyuiop12", "letmein12345", "iloveyou1234", "passwordpassword",
    "welcome12345", "administrator", "1234567890123", "flowsense1234", "changeme1234",
}


def check_password_rules(password: str, email: str) -> None:
    s = get_settings()
    if len(password) < s.password_min_length:
        raise ApiError(422, "PASSWORD_TOO_SHORT", f"Use at least {s.password_min_length} characters. A short phrase of several words works well.")
    if len(password) > 200:
        raise ApiError(422, "PASSWORD_TOO_LONG", "Use at most 200 characters.")
    low = password.lower()
    if low in _COMMON or low == email.lower() or (len(set(low)) <= 3):
        raise ApiError(422, "PASSWORD_TOO_WEAK", "This password is too easy to guess. Try a longer phrase.")


def issue(db: Session, request: Request | None, response: Response, user: User, biz: Business) -> str:
    """Create a fresh session bound to (user, business). Rotates any session the browser already had."""
    s = get_settings()
    old = request.cookies.get(SESSION_COOKIE) if request is not None else None
    if old:  # session rotation: never reuse a pre-login session
        db.execute(update(AuthSession).where(AuthSession.token_hash == token_hash(old)).values(revoked_at=real_now()))
    token, csrf = new_token(), new_token()
    db.add(
        AuthSession(
            token_hash=token_hash(token),
            user_id=user.id,
            business_id=biz.id,
            csrf_token=csrf,
            expires_at=real_now() + timedelta(minutes=s.session_ttl_minutes),
        )
    )
    user.active_business_id = biz.id
    response.set_cookie(
        SESSION_COOKIE, token, httponly=True, samesite="lax", secure=s.cookie_secure, max_age=s.session_ttl_minutes * 60, path="/"
    )
    return csrf


def revoke_all(db: Session, user_id: uuid.UUID, *, except_hash: str | None = None) -> None:
    q = update(AuthSession).where(AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None))
    if except_hash:
        q = q.where(AuthSession.token_hash != except_hash)
    db.execute(q.values(revoked_at=real_now()))


def memberships(db: Session, user_id: uuid.UUID) -> list[tuple[Membership, Business]]:
    rows = db.execute(
        select(Membership, Business)
        .join(Business, Business.id == Membership.business_id)
        .where(Membership.user_id == user_id, Membership.active.is_(True), Business.archived_at.is_(None))
        .order_by(Business.created_at.desc())
    ).all()
    return [(m, b) for m, b in rows]


def pick_business(db: Session, user: User) -> tuple[Membership, Business] | None:
    rows = memberships(db, user.id)
    for m, b in rows:
        if b.id == user.active_business_id:
            return m, b
    # Prefer the user's own workspace over shared demos when nothing was used last.
    rows.sort(key=lambda mb: (mb[1].kind != "workspace", -mb[1].created_at.timestamp()))
    return rows[0] if rows else None
