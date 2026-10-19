"""Recovery of outgoing transfers whose outcome is unknown (timeout or crash).

Rules:
* A pending transfer keeps its funds reserved; a timeout never frees them.
* Before any retry, ask the bank for the status of the persisted provider_ref.
* Resubmitting uses the same provider_ref, which the bank treats idempotently.
* Settlement still arrives only as a signed event, de-duplicated by event id,
  so running recovery any number of times yields one debit, one settlement
  record and one receipt.
Runs on API startup (restart recovery) and on demand (POST /api/reconcile).
The kill switch does not stop it: reconciling submitted actions must continue.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.domain.actions import lock_business
from app.models import Transfer
from app.sandbox import simulator


def reconcile_pending(db: Session, business_id: uuid.UUID, *, actor_type: str = "reconciler", actor_id: Any = None) -> list[dict[str, Any]]:
    refs = (
        db.execute(
            select(Transfer.provider_ref).where(
                Transfer.business_id == business_id, Transfer.direction == "out", Transfer.status == "pending"
            )
        )
        .scalars()
        .all()
    )
    results: list[dict[str, Any]] = []
    for ref in refs:
        status = simulator.bank_status(db, ref)
        if status is None:
            t = db.execute(select(Transfer).where(Transfer.provider_ref == ref)).scalar_one()
            simulator.bank_submit(db, t.id, auto_settle=False)  # same provider_ref -> idempotent at the bank
            out = simulator.bank_complete(db, ref)
            step = "resubmitted_then_settled"
        elif status in ("accepted", "settled"):
            out = simulator.bank_complete(db, ref)
            step = "status_checked_then_settled"
        else:
            out = simulator.deliver(
                db, simulator.build_event(business_id, "transfer.failed", {"provider_ref": ref, "reason": "bank reported failure"})
            )
            step = "bank_reported_failure"
        results.append(
            {"provider_ref": ref, "bank_status": status, "step": step, "outcome": out.get("outcome"), "detail": out.get("detail")}
        )
    lock_business(db, business_id)
    audit.append(
        db,
        business_id,
        event_type="reconciliation.run",
        actor_type=actor_type,
        actor_id=actor_id,
        outcome="ok",
        payload={
            "pending_found": len(refs),
            "results": [{k: r[k] for k in ("provider_ref", "bank_status", "step", "outcome")} for r in results],
        },
    )
    db.commit()
    return results


def reconcile_all(db: Session) -> int:
    """Startup recovery across businesses that have pending transfers."""
    biz_ids = (
        db.execute(select(Transfer.business_id).where(Transfer.direction == "out", Transfer.status == "pending").distinct()).scalars().all()
    )
    for b in biz_ids:
        reconcile_pending(db, b, actor_type="startup_recovery")
    return len(biz_ids)
