"""6-digit email codes for verifying a sign-up and authorising a password reset or change.

- Cryptographically random (``secrets.randbelow``), shown to the user only in the email.
- Stored as an HMAC-SHA256 keyed with a server secret and a per-code random salt, so a database copy
  alone can't be brute-forced over the 1,000,000 possible codes.
- Bound to one user and one purpose; single use; expire after CODE_MINUTES; issuing a new code for the
  same purpose invalidates the older one.
- Wrong guesses: MAX_ATTEMPTS in total per code (then it is dead), so the odds stay at MAX_ATTEMPTS in
  1,000,000 per code. Callers also limit tries per (email, network) BEFORE any account lookup, with the
  same answer whether or not the account exists (see guard / record_miss), so a stranger can't kill
  someone else's code quickly and the limit itself reveals nothing.
- Resending is limited: at most one code per purpose every RESEND_SECONDS, and RESENDS_PER_HOUR an hour.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.clock import real_now
from app.config import get_settings
from app.errors import ApiError
from app.models import AccountToken, User

CODE_MINUTES = 10
MAX_ATTEMPTS = 10
ATTEMPTS_PER_NETWORK = 5
RESEND_SECONDS = 60
RESENDS_PER_HOUR = 5
PURPOSES = ("verify_code", "reset_code", "change_code")


def _key() -> bytes:
    s = get_settings()
    base = s.sandbox_event_secret.get_secret_value() if s.sandbox_event_secret else ""
    # A key derived for this purpose only; never the event-signing key itself.
    return hashlib.sha256(("flowsense-email-codes|" + base).encode()).digest()


def _digest(user_id: str, purpose: str, salt: str, code: str) -> str:
    return hmac.new(_key(), f"{user_id}|{purpose}|{salt}|{code}".encode(), hashlib.sha256).hexdigest()


def can_send(db: Session, user: User, purpose: str) -> str | None:
    """None if a new code may be sent now, otherwise a plain-language reason (resend limits)."""
    now = real_now()
    recent = db.execute(
        select(func.max(AccountToken.created_at)).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose)
    ).scalar_one()
    if recent is not None and (now - recent).total_seconds() < RESEND_SECONDS:
        return "Please wait a minute before asking for another code."
    hour = db.execute(
        select(func.count(AccountToken.id)).where(AccountToken.user_id == user.id, AccountToken.purpose == purpose, AccountToken.created_at >= now - timedelta(hours=1))
    ).scalar_one()
    if hour >= RESENDS_PER_HOUR:
        return "Too many codes were requested. Try again in an hour."
    return None


def issue(db: Session, user: User, purpose: str) -> str:
    """Create a new code (invalidating older unused ones for this purpose) and return it for the email."""
    assert purpose in PURPOSES
    now = real_now()
    db.execute(
        update(AccountToken)
        .where(AccountToken.user_id == user.id, AccountToken.purpose == purpose, AccountToken.used_at.is_(None))
        .values(used_at=now)
    )
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    db.add(
        AccountToken(
            user_id=user.id, purpose=purpose, salt=salt, attempts=0,
            token_hash=_digest(str(user.id), purpose, salt, code), created_at=now, expires_at=now + timedelta(minutes=CODE_MINUTES),
        )
    )
    db.flush()
    return code


WRONG = "That code is wrong or has expired. Check the latest email, or ask for a new code."


TOO_MANY = "Too many tries from this network for this email. Wait 10 minutes, or ask for a new code."


def _guess_bucket(purpose: str, email: str, client: str) -> str:
    from app.auth import ratelimit

    return ratelimit.bucket("code-guess", purpose, email.lower(), client)


def guard(db: Session, purpose: str, email: str, client: str) -> None:
    """Refuse when this network has failed ATTEMPTS_PER_NETWORK times for this email and purpose within
    CODE_MINUTES. Keyed on what the caller typed, never on whether the account exists."""
    from app.auth import ratelimit

    if ratelimit.hits(db, _guess_bucket(purpose, email, client), timedelta(minutes=CODE_MINUTES)) >= ATTEMPTS_PER_NETWORK:
        raise ApiError(429, "CODE_ATTEMPTS", TOO_MANY)


def record_miss(db: Session, purpose: str, email: str, client: str) -> None:
    """Count one failed try (wrong code, wrong password, unknown account: all the same)."""
    from app.auth import ratelimit

    db.add(ratelimit.RateLimitHit(bucket=_guess_bucket(purpose, email, client)))
    db.add(ratelimit.RateLimitHit(bucket=ratelimit.bucket("code-email", email.lower())))
    db.add(ratelimit.RateLimitHit(bucket=ratelimit.bucket("code-email-day", email.lower())))
    db.commit()


def check(db: Session, user: User | None, purpose: str, code: str) -> None:
    """Accept the latest live code for (user, purpose) once, or raise one generic error.

    The same message is used for "no such account", "no code", "wrong", "expired" and "used", so the
    answer reveals nothing about which accounts exist. Wrong guesses are counted and the code dies
    after MAX_ATTEMPTS.
    """
    code = (code or "").strip().replace(" ", "")
    if user is None or len(code) != 6 or not code.isdigit():
        raise ApiError(400, "CODE_INVALID", WRONG)
    row = db.execute(
        select(AccountToken)
        .where(AccountToken.user_id == user.id, AccountToken.purpose == purpose, AccountToken.used_at.is_(None))
        .order_by(AccountToken.created_at.desc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()
    now = real_now()
    if row is None or row.expires_at <= now or row.salt is None:
        raise ApiError(400, "CODE_INVALID", WRONG)
    if not hmac.compare_digest(row.token_hash, _digest(str(user.id), purpose, row.salt, code)):
        row.attempts = (row.attempts or 0) + 1
        if row.attempts >= MAX_ATTEMPTS:
            row.used_at = now  # too many wrong guesses in total: this code can't be used any more
        db.commit()
        raise ApiError(400, "CODE_INVALID", WRONG)
    row.used_at = now
