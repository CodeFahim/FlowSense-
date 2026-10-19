"""JSON views of persisted records. Money is always integer minor units plus currency."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Action,
    Approval,
    AuditEvent,
    Counterparty,
    Evidence,
    Invoice,
    Obligation,
    Policy,
    Receipt,
    Transfer,
)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def counterparty(c: Counterparty) -> dict[str, Any]:
    return {
        "id": str(c.id),
        "kind": c.kind,
        "name": c.name,
        "account_ref": c.account_ref,
        "version": c.version,
        "verified": c.verified,
        "accepts_partial": c.accepts_partial,
        "typical_days_to_pay": c.typical_days_to_pay,
        "contact": c.contact or {},
    }


def approval(a: Approval | None) -> dict[str, Any] | None:
    if a is None:
        return None
    return {
        "id": str(a.id),
        "action_revision": a.action_revision,
        "amount_minor": a.amount_minor,
        "currency": a.currency,
        "counterparty_id": str(a.counterparty_id),
        "counterparty_version": a.counterparty_version,
        "policy_version": a.policy_version,
        "payload_digest": a.payload_digest,
        "expires_at": _iso(a.expires_at),
        "consumed_at": _iso(a.consumed_at),
        "revoked_at": _iso(a.revoked_at),
        "revoke_reason": a.revoke_reason,
        "created_at": _iso(a.created_at),
    }


def transfer(t: Transfer | None) -> dict[str, Any] | None:
    if t is None:
        return None
    return {
        "id": str(t.id),
        "direction": t.direction,
        "status": t.status,
        "amount_minor": t.amount_minor,
        "currency": t.currency,
        "account_ref": t.account_ref,
        "provider_ref": t.provider_ref,
        "submitted_at": _iso(t.submitted_at),
        "settled_at": _iso(t.settled_at),
        "action_id": str(t.action_id) if t.action_id else None,
    }


def action(db: Session, a: Action) -> dict[str, Any]:
    cp = db.get(Counterparty, a.counterparty_id)
    ob = db.get(Obligation, a.obligation_id) if a.obligation_id else None
    inv = db.get(Invoice, a.invoice_id) if a.invoice_id else None
    ap = db.execute(select(Approval).where(Approval.action_id == a.id).order_by(Approval.created_at.desc()).limit(1)).scalar_one_or_none()
    tr = db.execute(select(Transfer).where(Transfer.action_id == a.id).order_by(Transfer.submitted_at.desc()).limit(1)).scalar_one_or_none()
    return {
        "id": str(a.id),
        "type": a.type,
        "status": a.status,
        "revision": a.revision,
        "amount_minor": a.amount_minor,
        "settled_minor": a.settled_minor,
        "currency": a.currency,
        "purpose": a.purpose,
        "source": a.source,
        "counterparty": counterparty(cp) if cp else None,
        "counterparty_version": a.counterparty_version,
        "obligation": {
            "id": str(ob.id),
            "description": ob.description,
            "amount_minor": ob.amount_minor,
            "paid_minor": ob.paid_minor,
            "due_at": _iso(ob.due_at),
        }
        if ob
        else None,
        "invoice": {"id": str(inv.id), "number": inv.number, "outstanding_minor": inv.outstanding_minor} if inv else None,
        "case_id": str(a.case_id) if a.case_id else None,
        "last_decision": a.last_decision,
        "approval": approval(ap),
        "transfer": transfer(tr),
        "created_at": _iso(a.created_at),
        "updated_at": _iso(a.updated_at),
    }


def evidence(e: Evidence, full: bool = False) -> dict[str, Any]:
    d = {
        "id": str(e.id),
        "label": e.label,
        "title": e.title,
        "kind": e.kind,
        "filename": e.filename,
        "provenance": e.provenance,
        "instruction_like": e.instruction_like,
        "sha256": e.sha256,
        "created_at": _iso(e.created_at),
        "chars": len(e.content_text),
    }
    if full:
        d["content_text"] = e.content_text
    return d


def invoice(db: Session, inv: Invoice, with_evidence: bool = False) -> dict[str, Any]:
    cp = db.get(Counterparty, inv.customer_id)
    d: dict[str, Any] = {
        "id": str(inv.id),
        "number": inv.number,
        "description": inv.description,
        "customer": counterparty(cp) if cp else None,
        "issued_on": inv.issued_on.isoformat(),
        "due_on": inv.due_on.isoformat(),
        "amount_minor": inv.amount_minor,
        "collected_minor": inv.collected_minor,
        "outstanding_minor": inv.outstanding_minor,
        "disputed": inv.disputed,
    }
    evs = db.execute(select(Evidence).where(Evidence.invoice_id == inv.id).order_by(Evidence.created_at)).scalars().all()
    d["evidence"] = [evidence(e, full=with_evidence) for e in evs]
    return d


def policy(p: Policy) -> dict[str, Any]:
    return {
        "id": str(p.id),
        "version": p.version,
        "status": p.status,
        "source_text": p.source_text,
        "spec": p.spec,
        "interpretation": p.interpretation,
        "activated_at": _iso(p.activated_at),
        "created_at": _iso(p.created_at),
    }


def audit_event(e: AuditEvent) -> dict[str, Any]:
    return {
        "seq": e.seq,
        "occurred_at": _iso(e.occurred_at),
        "actor_type": e.actor_type,
        "actor_id": e.actor_id,
        "event_type": e.event_type,
        "case_id": e.case_id,
        "action_id": e.action_id,
        "approval_id": e.approval_id,
        "receipt_id": e.receipt_id,
        "policy_version": e.policy_version,
        "outcome": e.outcome,
        "reasons": e.reasons,
        "correlation_id": e.correlation_id,
        "payload": e.payload,
        "prev_hash": e.prev_hash,
        "hash": e.hash,
    }


def receipt(r: Receipt) -> dict[str, Any]:
    return {"id": str(r.id), "number": r.number, "content": r.content, "digest": r.digest, "created_at": _iso(r.created_at)}
