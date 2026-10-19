"""Sandbox counterparties: a simulated bank and simulated customers.

The simulator is the *only* source of settlement facts. It keeps its own
records (sim_bank_ops) and reports outcomes as HMAC-signed events delivered to
reconciliation.ingest - the same path an external webhook would use.
Nothing here touches real banks or sends real messages.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models import Business, SimBankOp, Transfer
from app.reconciliation import ingest as recon


def build_event(business_id: uuid.UUID, type_: str, data: dict[str, Any], event_id: str | None = None) -> bytes:
    body = {
        "event_id": event_id or "EVT-" + uuid.uuid4().hex[:24].upper(),
        "type": type_,
        "business_id": str(business_id),
        "occurred_at": datetime.now(UTC).isoformat(),
        "data": data,
    }
    return json.dumps(body, sort_keys=True).encode()


def deliver(db: Session, raw: bytes) -> dict[str, Any]:
    return recon.ingest(db, raw, recon.sign(raw))


# In these demo scenarios the bank accepts transfers but its confirmation is
# "lost" (a timeout), so FlowSense must recover through status checks.
TIMEOUT_SCENARIOS = {"pending_recovery"}


def bank_submit(db: Session, transfer_id: uuid.UUID, *, auto_settle: bool = True) -> dict[str, Any] | None:
    """Simulated bank accepts an outgoing transfer (idempotent on provider_ref) and, by default, settles it."""
    t = db.get(Transfer, transfer_id)
    if t is None:
        return None
    if db.get(SimBankOp, t.provider_ref) is None:
        db.add(
            SimBankOp(
                provider_ref=t.provider_ref,
                business_id=t.business_id,
                direction="out",
                amount_minor=t.amount_minor,
                account_ref=t.account_ref,
                status="accepted",
            )
        )
        db.commit()
    biz = db.get(Business, t.business_id)
    if biz is not None and biz.scenario in TIMEOUT_SCENARIOS and auto_settle:
        return {
            "outcome": "timeout",
            "detail": f"Bank did not confirm {t.provider_ref} (simulated timeout). Funds stay reserved until reconciliation.",
        }
    if not auto_settle:
        return None
    return bank_complete(db, t.provider_ref)


def bank_status(db: Session, provider_ref: str) -> str | None:
    """What the bank says about a transfer: None (never received), accepted, settled or failed."""
    db.expire_all()  # read the bank's current record, not a cached one
    op = db.get(SimBankOp, provider_ref)
    return op.status if op else None


def bank_complete(db: Session, provider_ref: str) -> dict[str, Any]:
    """The bank finishes processing and (re)sends its settlement event. Safe to repeat."""
    op = db.get(SimBankOp, provider_ref)
    assert op is not None
    if op.status == "accepted":
        op.status = "settled"
        db.commit()
    raw = build_event(
        op.business_id,
        "transfer.settled",
        {"provider_ref": op.provider_ref, "amount_minor": op.amount_minor},
        # Deterministic id: a re-sent confirmation is recognised as a duplicate.
        event_id="EVT-SETTLE-" + op.provider_ref,
    )
    return deliver(db, raw)


def customer_accepts(db: Session, business_id: uuid.UUID, action_id: uuid.UUID) -> dict[str, Any]:
    return deliver(db, build_event(business_id, "request.accepted", {"action_id": str(action_id)}))


def customer_refuses(db: Session, business_id: uuid.UUID, action_id: uuid.UUID, reason: str = "") -> dict[str, Any]:
    return deliver(db, build_event(business_id, "request.refused", {"action_id": str(action_id), "reason": reason}))


def customer_pays(
    db: Session, business_id: uuid.UUID, invoice_id: uuid.UUID, amount_minor: int, action_id: uuid.UUID | None
) -> dict[str, Any]:
    data = {
        "provider_ref": "SBX-IN-" + uuid.uuid4().hex[:20].upper(),
        "invoice_id": str(invoice_id),
        "amount_minor": amount_minor,
        "action_id": str(action_id) if action_id else None,
    }
    return deliver(db, build_event(business_id, "payment.received", data))
