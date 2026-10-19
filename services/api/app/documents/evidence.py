"""Evidence intake. Content is untrusted data: stored and shown as plain text,
never executed, never rendered as HTML, never treated as instructions.

The instruction-like detector is a *labeling aid* only. Safety does not depend
on it: even unflagged malicious text cannot move money, because every action
passes the safety kernel and owner approval.
"""

from __future__ import annotations

import hashlib
import re
import uuid

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.audit import chain as audit
from app.errors import ApiError
from app.models import Evidence, Invoice

MAX_TEXT_CHARS = 20_000
_INSTRUCTION_PATTERNS = [
    r"\bignore\b.{0,40}\b(instruction|policy|policies|rule|security)",
    r"\bdisregard\b",
    r"\bsend\b.{0,30}\b(all|every)\b.{0,20}\b(money|funds|cash)\b",
    r"\b(transfer|pay|wire)\b.{0,60}\b(new|this|following|different)\s+account\b",
    r"\bsystem prompt\b",
    r"\byou are now\b",
    r"\bapprove\b.{0,30}\bwithout\b",
]
_INSTRUCTION_RE = re.compile("|".join(_INSTRUCTION_PATTERNS), re.IGNORECASE | re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def looks_like_instructions(text: str) -> bool:
    return bool(_INSTRUCTION_RE.search(text))


def clean_text(text: str) -> str:
    text = _CONTROL.sub("", text)
    if len(text) > MAX_TEXT_CHARS:
        raise ApiError(413, "EVIDENCE_TOO_LARGE", f"Evidence text is limited to {MAX_TEXT_CHARS} characters.")
    if not text.strip():
        raise ApiError(422, "EVIDENCE_EMPTY", "Evidence text is empty.")
    return text


def add_text_evidence(
    db: Session,
    business_id: uuid.UUID,
    invoice: Invoice,
    *,
    title: str,
    text: str,
    provenance: str,
    actor_type: str,
    actor_id: uuid.UUID | None,
    label: str | None = None,
    filename: str | None = None,
    kind: str = "text",
) -> Evidence:
    text = clean_text(text)
    if provenance not in ("user_supplied", "sandbox_verified"):
        raise ApiError(422, "PROVENANCE_INVALID", "Invalid provenance.")
    n = db.execute(select(func.count(Evidence.id)).where(Evidence.invoice_id == invoice.id)).scalar_one() + 1
    ev = Evidence(
        business_id=business_id,
        invoice_id=invoice.id,
        label=label or f"EV-{invoice.number.split('-')[-1]}-{n}",
        title=title[:200],
        kind=kind,
        filename=filename,
        content_text=text,
        sha256=hashlib.sha256(text.encode()).hexdigest(),
        provenance=provenance,
        instruction_like=looks_like_instructions(text),
        created_by=actor_id,
    )
    db.add(ev)
    db.flush()
    audit.append(
        db,
        business_id,
        event_type="evidence.added",
        actor_type=actor_type,
        actor_id=actor_id,
        outcome="flagged_instruction_like" if ev.instruction_like else "ok",
        payload={"label": ev.label, "invoice": invoice.number, "sha256": ev.sha256, "provenance": provenance, "chars": len(text)},
    )
    return ev
