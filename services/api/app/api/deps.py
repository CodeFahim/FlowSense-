"""Request context: session cookie -> user -> active membership -> business.

The business is always derived from the authenticated session, never from a
client-supplied tenant id. Mutating routes additionally require the per-session
CSRF token in the X-CSRF-Token header (double submit bound to the session).
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.security import token_hash
from app.auth.sessions import SESSION_COOKIE  # noqa: E402,F401  (re-exported)
from app.clock import real_now
from app.db import get_db
from app.domain.actions import Actor
from app.errors import ApiError
from app.models import AuthSession, Business, Membership, User

CSRF_HEADER = "x-csrf-token"


@dataclass
class Ctx:
    user: User
    business: Business
    role: str
    session: AuthSession

    @property
    def actor(self) -> Actor:
        return Actor(user_id=self.user.id, role=self.role, actor_type="user")


def get_ctx(request: Request, db: Session = Depends(get_db)) -> Ctx:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "AUTH_REQUIRED", "Please sign in.")
    sess = db.execute(select(AuthSession).where(AuthSession.token_hash == token_hash(token))).scalar_one_or_none()
    if sess is None or sess.revoked_at is not None or sess.expires_at <= real_now():
        raise ApiError(401, "SESSION_INVALID", "Your session has expired. Please sign in again.")
    user = db.get(User, sess.user_id)
    biz = db.get(Business, sess.business_id)
    m = db.execute(
        select(Membership).where(
            Membership.user_id == sess.user_id, Membership.business_id == sess.business_id, Membership.active.is_(True)
        )
    ).scalar_one_or_none()
    if user is None or user.deleted_at is not None or biz is None or m is None or biz.archived_at is not None:
        raise ApiError(401, "MEMBERSHIP_INVALID", "You no longer have access to this business. Please sign in again.")
    return Ctx(user=user, business=biz, role=m.role, session=sess)


def mutating_ctx(request: Request, ctx: Ctx = Depends(get_ctx)) -> Ctx:
    sent = request.headers.get(CSRF_HEADER, "")
    if not sent or not hmac.compare_digest(sent, ctx.session.csrf_token):
        raise ApiError(403, "CSRF_FAILED", "Missing or invalid CSRF token.")
    return ctx


def owner_ctx_read(ctx: Ctx = Depends(get_ctx)) -> Ctx:
    """Owner-only read (no CSRF needed for GET)."""
    if ctx.role != "owner":
        raise ApiError(403, "ROLE_FORBIDDEN", "Only the business owner can do this.")
    return ctx


def owner_ctx(ctx: Ctx = Depends(mutating_ctx)) -> Ctx:
    if ctx.role != "owner":
        raise ApiError(403, "ROLE_FORBIDDEN", "Only the business owner can do this.")
    return ctx
