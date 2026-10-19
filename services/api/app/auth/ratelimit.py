"""Database-backed sliding-window rate limits (shared by every API worker).

Keys are hashed before they are stored, so the table never holds raw emails or
IP addresses. Limits are per key and per window; a request that is over the
limit is refused before any expensive or externally visible work happens.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from datetime import timedelta

from fastapi import Request
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.clock import real_now
from app.errors import ApiError
from app.models import RateLimitHit


def bucket(*parts: str) -> str:
    return hashlib.sha256("|".join(parts).lower().encode()).hexdigest()


_IP = re.compile(r"^[0-9A-Fa-f:.]{2,45}$")


def client_ip(request: Request) -> str:
    """Client address for rate limiting.

    1. If the request carries the shared FLOWSENSE_PROXY_SECRET (added by the web frontend's proxy.ts),
       the client IP that proxy saw (Vercel overwrites its forwarding headers) is used.
    2. Otherwise the entry FLOWSENSE_TRUSTED_PROXY_HOPS places from the right of X-Forwarded-For: it was
       appended by our own proxy (Render) and cannot be forged by the caller. With hops=0, the socket peer.
    IP limits are always combined with per-account and global limits and never trusted alone.
    """
    from app.config import get_settings

    s = get_settings()
    secret = s.proxy_secret.get_secret_value() if s.proxy_secret else ""
    sent = request.headers.get("x-flowsense-proxy-secret", "")
    if secret and sent and hmac.compare_digest(sent, secret):
        claimed = request.headers.get("x-flowsense-client-ip", "").strip()
        if _IP.match(claimed):
            return claimed
    hops = s.proxy_hops
    entries = [e.strip() for e in request.headers.get("x-forwarded-for", "").split(",") if e.strip()]
    peer = request.client.host if request.client else ""
    ip = entries[-hops] if hops > 0 and len(entries) >= hops else peer
    return ip[:64] or "unknown"


def hits(db: Session, key: str, window: timedelta) -> int:
    since = real_now() - window
    return int(db.execute(select(func.count(RateLimitHit.id)).where(RateLimitHit.bucket == key, RateLimitHit.created_at >= since)).scalar_one())


def check(db: Session, key: str, limit: int, window: timedelta, message: str, *, record: bool = True) -> None:
    """Refuse with 429 when `key` already has `limit` hits inside `window`; otherwise record one hit."""
    if hits(db, key, window) >= limit:
        minutes = max(1, int(window.total_seconds() // 60))
        raise ApiError(429, "RATE_LIMITED", message, extra={"retry_after_minutes": minutes})
    if record:
        db.add(RateLimitHit(bucket=key))
        db.flush()


def prune(db: Session, older_than: timedelta = timedelta(days=2)) -> None:
    db.execute(delete(RateLimitHit).where(RateLimitHit.created_at < real_now() - older_than))
