"""Workspaces and the first-use setup checklist.

A fresh workspace starts with no name, currency, timezone, cash or records: nothing is
preset from a country, IP address or sample. Financial records can only be created after
the owner has explicitly confirmed the accounting currency and business timezone.

Setup steps (resumable; each step is derived from stored facts, not from a wizard flag):
  1. profile   - business name, accounting currency and timezone confirmed
  2. cash      - available cash declared (a confirmed zero is different from "not entered")
  3. money_in  - at least one invoice, or the owner said there are none
  4. money_out - at least one bill/payroll obligation, or the owner said there are none
  5. review    - an owner-confirmed safety policy (reserve) is active, so analysis can run
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.errors import ApiError
from app.ledger import service as ledger
from app.models import Business, Invoice, Membership, Obligation, Policy, User

SAMPLE_PREFIX = "sample:"


def is_configured(biz: Business) -> bool:
    if not (biz.currency and biz.timezone):
        return False
    # Demo/sample fixtures state their currency and timezone; a real workspace needs the owner's confirmation.
    return biz.kind != "workspace" or bool(biz.currency_confirmed_at and biz.timezone_confirmed_at)


def require_configured(biz: Business) -> None:
    if not is_configured(biz):
        raise ApiError(
            409,
            "SETUP_REQUIRED",
            "Confirm your accounting currency and business timezone first (Settings → Business, or the setup checklist).",
        )


def create_workspace(db: Session, user: User, name: str = "") -> Business:
    """An empty business owned by `user`. No currency, timezone, records or policy."""
    biz = Business(name=name.strip()[:200], kind="workspace", created_by=user.id)
    db.add(biz)
    db.flush()
    audit.init_head(db, biz.id)
    ledger.create_chart(db, biz.id)
    db.add(Membership(user_id=user.id, business_id=biz.id, role="owner"))
    audit.append(db, biz.id, event_type="workspace.created", actor_type="user", actor_id=user.id, payload={"kind": "workspace"})
    db.flush()
    return biz


def setup_status(db: Session, biz: Business) -> dict[str, Any]:
    if biz.kind in ("demo", "sample", "isolation"):
        return {"complete": True, "steps": [], "next": None, "analysis_ready": True}
    has_invoice = db.execute(select(Invoice.id).where(Invoice.business_id == biz.id).limit(1)).first() is not None
    has_obligation = db.execute(select(Obligation.id).where(Obligation.business_id == biz.id).limit(1)).first() is not None
    has_policy = db.execute(select(Policy.id).where(Policy.business_id == biz.id, Policy.status == "active").limit(1)).first() is not None
    steps = [
        {
            "key": "profile",
            "title": "Set up your business",
            "done": bool(biz.name) and is_configured(biz),
            "detail": "Name, accounting currency and business timezone.",
            "href": "/setup/business",
        },
        {
            "key": "cash",
            "title": "Add available cash",
            "done": biz.cash_declared_at is not None,
            "detail": "Money already in your account today. Zero is a valid answer; leaving it blank is not.",
            "href": "/setup/cash",
        },
        {
            "key": "money_in",
            "title": "Add money coming in",
            "done": has_invoice or biz.money_in_none_at is not None,
            "none_confirmed": biz.money_in_none_at is not None and not has_invoice,
            "detail": "Invoices customers still owe you.",
            "href": "/money-in?add=invoice",
        },
        {
            "key": "money_out",
            "title": "Add money going out",
            "done": has_obligation or biz.money_out_none_at is not None,
            "none_confirmed": biz.money_out_none_at is not None and not has_obligation,
            "detail": "Bills, payroll and other payments you must make.",
            "href": "/money-out?add=bill",
        },
        {
            "key": "review",
            "title": "Review your position",
            "done": has_policy,
            "detail": "Confirm the cash reserve you want to keep, then analyse your business.",
            "href": "/setup/review",
        },
    ]
    nxt = next((s for s in steps if not s["done"]), None)
    return {
        "complete": nxt is None,
        "steps": steps,
        "next": nxt["key"] if nxt else None,
        "analysis_ready": all(s["done"] for s in steps[:4]) and has_policy,
    }


def sample_key(user_id: uuid.UUID) -> str:
    return f"{SAMPLE_PREFIX}{user_id}"
