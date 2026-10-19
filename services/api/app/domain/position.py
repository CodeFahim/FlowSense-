"""Cash position and funding gap.

Definitions (authoritative, settled-funds based):
  settled   = ledger balance of the sandbox bank account
  reserved  = pending outgoing transfers (funds held while a transfer is in flight)
  available = settled - reserved
  protected = unpaid, unreserved essential obligations due on or before
              horizon_end = business_now + protection_horizon_days
  gap       = max deficit over the dated path of protected outflows:
              max_t(0, reserve + cumulative_protected(t) - available)

Expected receipts, accepted requests and approved loans never enter this
calculation; they only appear in the forecast with visible assumptions.
For the demo fixture this reduces to max(0, payroll + reserve - settled).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.clock import business_now
from app.ledger.service import settled_cash
from app.models import Business, Obligation, Policy, Transfer


@dataclass(frozen=True)
class ProtectedItem:
    obligation_id: uuid.UUID
    description: str
    kind: str
    due_at: datetime
    open_minor: int


@dataclass(frozen=True)
class Position:
    as_of: datetime
    currency: str
    settled_minor: int
    reserved_minor: int
    available_minor: int
    reserve_minor: int
    horizon_end: datetime
    protected: tuple[ProtectedItem, ...]
    protected_total_minor: int
    gap_minor: int
    gap_date: datetime | None
    policy_version: int | None
    notes: list[str] = field(default_factory=list)
    # declared = the owner (or a fixture) stated the available cash; not_entered = unknown, never zero.
    cash_status: str = "declared"
    cash_as_of: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of": self.as_of.isoformat(),
            "currency": self.currency,
            "settled_minor": self.settled_minor,
            "reserved_minor": self.reserved_minor,
            "available_minor": self.available_minor,
            "reserve_minor": self.reserve_minor,
            "horizon_end": self.horizon_end.isoformat(),
            "protected": [
                {
                    "obligation_id": str(p.obligation_id),
                    "description": p.description,
                    "kind": p.kind,
                    "due_at": p.due_at.isoformat(),
                    "open_minor": p.open_minor,
                }
                for p in self.protected
            ],
            "protected_total_minor": self.protected_total_minor,
            "gap_minor": self.gap_minor,
            "gap_date": self.gap_date.isoformat() if self.gap_date else None,
            "policy_version": self.policy_version,
            "notes": self.notes,
            "cash_status": self.cash_status,
            "cash_as_of": self.cash_as_of,
        }


def active_policy(db: Session, business_id: uuid.UUID) -> Policy | None:
    return db.execute(select(Policy).where(Policy.business_id == business_id, Policy.status == "active")).scalar_one_or_none()


def pending_out_total(db: Session, business_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(Transfer.amount_minor), 0)).where(
                Transfer.business_id == business_id, Transfer.direction == "out", Transfer.status == "pending"
            )
        ).scalar_one()
    )


def reserved_for_obligation(db: Session, obligation_id: uuid.UUID) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(Transfer.amount_minor), 0)).where(
                Transfer.obligation_id == obligation_id, Transfer.direction == "out", Transfer.status == "pending"
            )
        ).scalar_one()
    )


def compute_position(db: Session, biz: Business, policy: Policy | None = None) -> Position:
    policy = policy if policy is not None else active_policy(db, biz.id)
    now = business_now(biz)
    notes: list[str] = []
    if policy is None:
        notes.append("No active policy: reserve treated as 0 and all payments are blocked (fail closed).")
        reserve, horizon_days, essential_kinds = 0, 7, ["payroll"]
    else:
        reserve = int(policy.spec.get("reserve_minor", 0))
        horizon_days = int(policy.spec.get("protection_horizon_days", 7))
        essential_kinds = list(policy.spec.get("essential_obligation_kinds", ["payroll"]))
    horizon_end = now + timedelta(days=horizon_days)

    settled = settled_cash(db, biz.id)
    reserved = pending_out_total(db, biz.id)
    available = settled - reserved

    obligations = db.execute(
        select(Obligation)
        .where(Obligation.business_id == biz.id, Obligation.paid_minor < Obligation.amount_minor, Obligation.due_at <= horizon_end)
        .order_by(Obligation.due_at, Obligation.created_at)
    ).scalars()
    protected: list[ProtectedItem] = []
    for ob in obligations:
        if not (ob.essential or ob.kind in essential_kinds):
            continue
        open_minor = ob.unpaid_minor - reserved_for_obligation(db, ob.id)
        if open_minor > 0:
            protected.append(ProtectedItem(ob.id, ob.description, ob.kind, ob.due_at, open_minor))

    gap = max(0, reserve - available)
    gap_date: datetime | None = now if gap > 0 else None
    cumulative = 0
    for item in protected:
        cumulative += item.open_minor
        deficit = reserve + cumulative - available
        if deficit > gap:
            gap = deficit
        if deficit > 0 and gap_date is None:
            gap_date = item.due_at
    return Position(
        as_of=now,
        currency=biz.ccy,
        settled_minor=settled,
        reserved_minor=reserved,
        available_minor=available,
        reserve_minor=reserve,
        horizon_end=horizon_end,
        protected=tuple(protected),
        protected_total_minor=cumulative,
        gap_minor=gap,
        gap_date=gap_date,
        policy_version=policy.version if policy else None,
        notes=notes + ([] if biz.cash_declared_at else ["Available cash has not been entered, so the shortfall can't be calculated yet."]),
        cash_status="declared" if biz.cash_declared_at else "not_entered",
        cash_as_of=biz.cash_as_of.isoformat() if biz.cash_as_of else None,
    )
