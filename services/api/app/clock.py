"""Clocks.

Two notions of time are kept apart on purpose:

* real time (UTC wall clock): sessions, login throttling, approval expiry, audit timestamps;
* business time: the sandbox clock stored on each business (fixed for the demo
  fixture) used for due dates, forecasts, and "this Friday" resolution.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models import Business

_real_now: Callable[[], datetime] = lambda: datetime.now(UTC)  # noqa: E731


def real_now() -> datetime:
    return _real_now()


def set_real_now(fn: Callable[[], datetime] | None) -> None:
    """Test hook for injecting a clock."""
    global _real_now
    _real_now = fn or (lambda: datetime.now(UTC))


def business_now(biz: Business) -> datetime:
    return biz.sandbox_now if biz.sandbox_now is not None else real_now()


def tz(biz: Business) -> ZoneInfo:
    return ZoneInfo(biz.tz_name)


def local(dt: datetime, biz: Business) -> datetime:
    return dt.astimezone(tz(biz))
