"""Hash-chained, append-only audit log.

Canonical serialization: JSON with sorted keys, compact separators, ASCII
escaping, UTC ISO-8601 timestamps. hash = SHA-256(canonical(material)) where
material includes prev_hash, so editing any event breaks every later hash.

Appends are serialized per business by locking that business's audit_heads
row. A database trigger rejects UPDATE/DELETE on audit_events.

Trust limit: an administrator with database access could rewrite the whole
chain consistently. Detecting that needs an independently stored checkpoint
(e.g. the head hash printed in evidence/ or shared with a third party).
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.clock import real_now
from app.models import AuditEvent, AuditHead

GENESIS = "0" * 64
correlation_id: contextvars.ContextVar[str] = contextvars.ContextVar("correlation_id", default="")

_SENSITIVE = ("password", "token", "secret", "csrf", "cookie", "api_key", "authorization")


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if any(s in k.lower() for s in _SENSITIVE) else sanitize(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [sanitize(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float):
        raise TypeError("Floats are not allowed in audit payloads; use integer minor units")
    return value


def _material(ev: AuditEvent) -> dict[str, Any]:
    return {
        "business_id": str(ev.business_id),
        "seq": ev.seq,
        "occurred_at": ev.occurred_at.astimezone(UTC).isoformat(),
        "actor_type": ev.actor_type,
        "actor_id": ev.actor_id,
        "event_type": ev.event_type,
        "case_id": ev.case_id,
        "action_id": ev.action_id,
        "approval_id": ev.approval_id,
        "receipt_id": ev.receipt_id,
        "policy_version": ev.policy_version,
        "outcome": ev.outcome,
        "reasons": ev.reasons,
        "correlation_id": ev.correlation_id,
        "payload": ev.payload,
        "prev_hash": ev.prev_hash,
    }


def compute_hash(ev: AuditEvent) -> str:
    return sha256_hex(canonical(_material(ev)))


def init_head(db: Session, business_id: uuid.UUID) -> None:
    db.add(AuditHead(business_id=business_id, last_seq=0, last_hash=GENESIS))
    db.flush()


def _sid(v: Any) -> str | None:
    return None if v is None else str(v)


def append(
    db: Session,
    business_id: uuid.UUID,
    *,
    event_type: str,
    actor_type: str,
    actor_id: Any = None,
    outcome: str = "ok",
    reasons: list[dict[str, str]] | None = None,
    payload: dict[str, Any] | None = None,
    case_id: Any = None,
    action_id: Any = None,
    approval_id: Any = None,
    receipt_id: Any = None,
    policy_version: int | None = None,
) -> AuditEvent:
    head = db.execute(select(AuditHead).where(AuditHead.business_id == business_id).with_for_update()).scalar_one()
    # Python and Postgres both keep microsecond precision, so this round-trips exactly.
    now = real_now().astimezone(UTC)
    ev = AuditEvent(
        business_id=business_id,
        seq=head.last_seq + 1,
        occurred_at=now,
        actor_type=actor_type,
        actor_id=_sid(actor_id),
        event_type=event_type,
        case_id=_sid(case_id),
        action_id=_sid(action_id),
        approval_id=_sid(approval_id),
        receipt_id=_sid(receipt_id),
        policy_version=policy_version,
        outcome=outcome,
        reasons=sanitize(reasons or []),
        correlation_id=correlation_id.get() or uuid.uuid4().hex,
        payload=sanitize(payload or {}),
        prev_hash=head.last_hash,
        hash="",
    )
    ev.hash = compute_hash(ev)
    head.last_seq = ev.seq
    head.last_hash = ev.hash
    db.add(ev)
    db.flush()
    return ev


def verify(db: Session, business_id: uuid.UUID) -> dict[str, Any]:
    events = db.execute(select(AuditEvent).where(AuditEvent.business_id == business_id).order_by(AuditEvent.seq)).scalars()
    prev = GENESIS
    count = 0
    for i, ev in enumerate(events, start=1):
        count = i
        if ev.seq != i:
            return {"valid": False, "count": count, "first_invalid_seq": ev.seq, "reason": "sequence gap"}
        if ev.prev_hash != prev:
            return {"valid": False, "count": count, "first_invalid_seq": ev.seq, "reason": "prev_hash mismatch"}
        if compute_hash(ev) != ev.hash:
            return {"valid": False, "count": count, "first_invalid_seq": ev.seq, "reason": "hash mismatch"}
        prev = ev.hash
    head = db.get(AuditHead, business_id)
    if head is not None and (head.last_hash != prev or head.last_seq != count):
        return {"valid": False, "count": count, "first_invalid_seq": None, "reason": "head does not match chain (truncation?)"}
    return {"valid": True, "count": count, "head_hash": prev, "first_invalid_seq": None, "reason": None}
