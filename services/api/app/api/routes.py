"""HTTP routes. Every route derives the business from the session (Ctx).

Every money route goes through app.domain.actions -> safety kernel. There is
no route that writes ledger lines directly.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, Response, UploadFile
from pydantic import BaseModel, Field, StrictInt
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.orm import Session

from app import fx
from app.api import overview, personal
from app.api import serialize as S
from app.api.accounts import me_view
from app.api.deps import Ctx, get_ctx, mutating_ctx, owner_ctx, owner_ctx_read
from app.audit import chain as audit
from app.auth import sessions as sess
from app.clock import real_now
from app.config import get_settings
from app.db import get_db
from app.documents import pdf as pdf_intake
from app.documents.evidence import add_text_evidence
from app.domain import actions as svc
from app.domain import workspace as ws
from app.domain.position import active_policy, compute_position
from app.errors import ApiError, not_found
from app.ledger import service as ledger
from app.models import (
    Action,
    AuditEvent,
    Counterparty,
    CounterpartyChange,
    Evidence,
    Invoice,
    JournalEntry,
    Obligation,
    Policy,
    Receipt,
    User,
)
from app.money import MAX_MINOR, fmt
from app.planning import rescue
from app.planning.forecast import forecast as build_forecast
from app.reconciliation import ingest as recon
from app.sandbox import simulator
from app.seed import fixtures

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]
Amount = Annotated[StrictInt, Field(gt=0, le=MAX_MINOR)]


def _uuid(v: str, what: str) -> uuid.UUID:
    try:
        return uuid.UUID(v)
    except ValueError as exc:
        raise not_found(what) from exc


# --------------------------------------------------------------------- health


@router.get("/health")
def health(db: DB) -> dict[str, Any]:
    db.execute(text("SELECT 1"))
    s = get_settings()
    return {"status": "ok", "database": "ok", "env": s.env, "ai_mode": s.ai_mode}


# ------------------------------------------------------------------ dashboard


def _this_month(db: Session, biz: Any) -> dict[str, Any]:
    """Settled money received and paid this calendar month (business timezone), from the ledger history.

    Declared balances and corrections are adjustments, reported separately: they are not money received."""
    from app.domain.records import business_today
    from app.planning import history

    month = business_today(biz).strftime("%Y-%m")
    row = next((m for m in history.monthly(history.settled_rows(db, biz)) if m["month"] == month), None)
    return {
        "month": month,
        "money_in_minor": row["money_in_minor"] if row else 0,
        "money_out_minor": row["money_out_minor"] if row else 0,
        "adjustments_minor": row["adjustments_minor"] if row else 0,
    }


@router.get("/dashboard")
def dashboard(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    biz = ctx.business
    if not ws.is_configured(biz):
        # A fresh workspace: no figures are shown until the owner confirms currency and timezone.
        return {"setup_required": True, "setup": ws.setup_status(db, biz), "currency": None}
    pos = compute_position(db, biz)
    policy = active_policy(db, biz.id)
    case = rescue.current_case(db, biz.id)
    plan = rescue.latest_plan(db, case.id) if case else None
    invoices = db.execute(select(Invoice).where(Invoice.business_id == biz.id).order_by(Invoice.due_on)).scalars().all()
    from app.domain.records import business_today

    today = business_today(biz)
    obligations = db.execute(select(Obligation).where(Obligation.business_id == biz.id).order_by(Obligation.due_at)).scalars().all()
    pending_actions = db.execute(
        select(func.count(Action.id)).where(Action.business_id == biz.id, Action.status.in_(("proposed", "approved")))
    ).scalar_one()
    # Recent business activity; sign-ins and assistant chatter are left out (they stay in the full log).
    recent = (
        db.execute(
            select(AuditEvent)
            .where(
                AuditEvent.business_id == biz.id,
                AuditEvent.event_type.not_in(("auth.login", "auth.logout", "assistant.turn", "preferences.updated")),
            )
            .order_by(AuditEvent.seq.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    if case is None:
        next_step = "No rescue case open. Cash covers essential obligations and the reserve."
    elif case.status == "RESOLVED":
        next_step = "Rescue resolved. Review the receipt and audit trail."
    elif case.status == "INFEASIBLE":
        next_step = "No feasible plan. Review the remaining gap and unmet constraints in the planner."
    elif case.status == "AWAITING_SETTLEMENT":
        next_step = "Waiting for sandbox settlement of submitted or accepted actions."
    else:
        next_step = f"{pending_actions} action(s) await review in Approvals. {plan.body.get('summary', '') if plan else ''}".strip()
    tb = ledger.trial_balance(db, biz.id)
    display = personal.prefs_view(personal.get_prefs(db, ctx.user))["display_currency"] or biz.ccy
    figures = {"settled": pos.settled_minor, "upcoming": pos.protected_total_minor, "reserve": pos.reserve_minor, "gap": pos.gap_minor}
    plan_summary = plan.body.get("summary") if plan else None
    return {
        "currency": biz.currency,
        "headline": overview.headline(biz, pos, case, int(pending_actions)),
        "home": overview.home_summary(db, biz, pos, case),
        "next_action": overview.next_action(db, biz, pos, case, plan_summary),
        # Approximate equivalents in the user's display currency, for information only.
        "display_equivalents": fx.equivalents(db, figures, biz.ccy, display),
        "position": pos.to_dict(),
        "policy": S.policy(policy) if policy else None,
        "kill_switch": biz.kill_switch,
        "case": {"id": str(case.id), "status": case.status} if case else None,
        "plan_summary": plan.body.get("summary") if plan else None,
        "next_step": next_step,
        "receivables": {
            "outstanding_minor": sum(i.outstanding_minor for i in invoices),
            "overdue_minor": sum(i.outstanding_minor for i in invoices if i.due_on < today),
            "count": len([i for i in invoices if i.outstanding_minor > 0]),
        },
        "obligations": [
            {
                "id": str(o.id),
                "kind": o.kind,
                "description": o.description,
                "amount_minor": o.amount_minor,
                "paid_minor": o.paid_minor,
                "due_at": o.due_at.isoformat(),
                "essential": o.essential or o.kind == "payroll",
            }
            for o in obligations
        ],
        "pending_approvals": pending_actions,
        "ledger_balanced": tb["balanced"],
        "recent_activity": [S.audit_event(e) for e in recent],
        "this_month": _this_month(db, biz),
    }


# ------------------------------------------------------------ invoices/evidence


@router.get("/invoices")
def list_invoices(db: DB, ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    rows = db.execute(select(Invoice).where(Invoice.business_id == ctx.business.id).order_by(Invoice.due_on)).scalars().all()
    return [S.invoice(db, i) for i in rows]


def _invoice(db: Session, ctx: Ctx, invoice_id: str) -> Invoice:
    inv = db.get(Invoice, _uuid(invoice_id, "Invoice"))
    if inv is None or inv.business_id != ctx.business.id:
        raise not_found("Invoice")
    return inv


@router.get("/invoices/{invoice_id}")
def get_invoice(invoice_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    inv = _invoice(db, ctx, invoice_id)
    d = S.invoice(db, inv, with_evidence=True)
    d["requests"] = [
        S.action(db, a) for a in db.execute(select(Action).where(Action.invoice_id == inv.id).order_by(Action.created_at)).scalars()
    ]
    return d


class EvidenceIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=20_000)


@router.post("/invoices/{invoice_id}/evidence")
def add_evidence(invoice_id: str, body: EvidenceIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    inv = _invoice(db, ctx, invoice_id)
    svc.lock_business(db, ctx.business.id)
    ev = add_text_evidence(
        db, ctx.business.id, inv, title=body.title, text=body.text, provenance="user_supplied", actor_type="user", actor_id=ctx.user.id
    )
    db.commit()
    return S.evidence(ev, full=True)


@router.post("/invoices/{invoice_id}/evidence/pdf")
def add_pdf_evidence(
    invoice_id: str,
    db: DB,
    file: UploadFile = File(...),
    title: str | None = Form(default=None, max_length=200),
    preview: bool = Form(default=False),
    ctx: Ctx = Depends(mutating_ctx),
) -> dict[str, Any]:
    """Store the extracted *text* of an uploaded PDF as user-supplied evidence.

    preview=true extracts and returns the text for the user to review, without storing anything.

    The file itself is never written to disk or the database (no storage paths,
    no orphaned files); only its extracted text, a generated name and digests.
    Every outcome is audited without document contents.
    """
    inv = _invoice(db, ctx, invoice_id)
    biz_id, user_id, number = ctx.business.id, ctx.user.id, inv.number
    # The request size is already capped by the guard middleware; read at most one byte past the limit.
    data = file.file.read(pdf_intake.MAX_PDF_BYTES + 1)
    digest = hashlib.sha256(data).hexdigest()
    filename = f"{number}-{digest[:12]}.pdf"  # generated; the client's filename is never stored
    db.commit()  # release the read transaction while the bounded extraction runs
    try:
        pdf_intake.validate_upload(file.filename, data)
        extracted = pdf_intake.extract_text(data)
    except ApiError as exc:
        db.rollback()
        svc.lock_business(db, biz_id)
        audit.append(
            db,
            biz_id,
            event_type="evidence.pdf_rejected",
            actor_type="user",
            actor_id=user_id,
            outcome="rejected",
            reasons=[{"code": exc.code, "message": exc.message}],
            payload={"invoice": number, "size_bytes": len(data), "file_sha256": digest},
        )
        db.commit()
        raise
    if preview:
        from app.documents.evidence import looks_like_instructions

        db.rollback()
        return {
            "preview": True,
            "stored": False,
            "pages": extracted.pages,
            "truncated": extracted.truncated,
            "chars": len(extracted.text),
            "instruction_like": looks_like_instructions(extracted.text),
            "content_text": extracted.text,
            "note": "A PDF's own text only (scans are read by “Add from a document”). Not stored until you save it. Saving does not verify the document.",
        }
    svc.lock_business(db, biz_id)
    inv = _invoice(db, ctx, invoice_id)
    # Same file already attached to this invoice (e.g. a double click): do not create a second record.
    existing = db.execute(
        select(Evidence).where(Evidence.invoice_id == inv.id, Evidence.kind == "pdf", Evidence.filename == filename)
    ).scalar_one_or_none()
    if existing is not None:
        db.rollback()
        raise ApiError(
            409, "PDF_DUPLICATE", f"This PDF is already attached to {number} as {existing.label}.", extra={"label": existing.label}
        )
    pages = f"{extracted.pages} page{'s' if extracted.pages != 1 else ''}"
    ev = add_text_evidence(
        db,
        biz_id,
        inv,
        title=(title or "").strip() or f"PDF upload ({pages}{', text truncated' if extracted.truncated else ''})",
        text=extracted.text,
        provenance="user_supplied",
        actor_type="user",
        actor_id=user_id,
        filename=filename,
        kind="pdf",
    )
    db.commit()
    return {**S.evidence(ev, full=True), "pages": extracted.pages, "truncated": extracted.truncated}


@router.get("/evidence/{evidence_id}")
def get_evidence(evidence_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    ev = db.get(Evidence, _uuid(evidence_id, "Evidence"))
    if ev is None or ev.business_id != ctx.business.id:
        raise not_found("Evidence")
    return S.evidence(ev, full=True)


# ------------------------------------------------------------ forecast/rescue


@router.get("/forecast")
def get_forecast(
    db: DB,
    ctx: Ctx = Depends(get_ctx),
    days: int = 14,
    delay_days: int = 0,
    collection_pct: int = 100,
    emergency_minor: int = 0,
    emergency_day: int = 1,
) -> dict[str, Any]:
    return build_forecast(
        db,
        ctx.business,
        days=days,
        delay_days=delay_days,
        collection_pct=collection_pct,
        emergency_minor=emergency_minor,
        emergency_day=emergency_day,
    )


@router.get("/rescue")
def get_rescue(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    case = rescue.current_case(db, ctx.business.id)
    pos = compute_position(db, ctx.business)
    return {"position": pos.to_dict(), "case": rescue.case_view(db, case) if case else None}


@router.post("/rescue/replan")
def post_replan(db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    biz = svc.lock_business(db, ctx.business.id)
    case = rescue.current_case(db, biz.id)
    if case is None or case.status == "RESOLVED":
        case = rescue.refresh(db, biz, trigger="Manual scan requested")
    else:
        if case.status != "AT_RISK":
            rescue.transition(db, case, "AT_RISK", "Owner/staff requested a replan")
        p = rescue.replan(db, biz, case, "Manual replan requested")
        rescue.transition(db, case, "AWAITING_ACTION" if p.feasible else "INFEASIBLE", p.body["summary"])
    db.commit()
    return {"case": rescue.case_view(db, case) if case else None}


# -------------------------------------------------------------------- actions


@router.get("/actions")
def list_actions(db: DB, ctx: Ctx = Depends(get_ctx), status: str | None = None) -> list[dict[str, Any]]:
    q = select(Action).where(Action.business_id == ctx.business.id).order_by(Action.created_at.desc()).limit(100)
    if status:
        q = q.where(Action.status == status)
    out = []
    for a in db.execute(q).scalars():
        d = S.action(db, a)
        # last_decision is a historical record. Proposed actions also get a fresh, read-only kernel
        # check so the UI never shows a "would be blocked" reason computed against stale balances.
        d["current_check"] = svc.evaluate_action(db, ctx.business, ctx.actor, a, svc.Phase.PROPOSE).to_dict() if a.status == "proposed" else None
        out.append(d)
    db.rollback()  # read-only endpoint: discard anything the evaluation may have loaded or flushed
    return out


def _action(db: Session, ctx: Ctx, action_id: str) -> Action:
    a = db.get(Action, _uuid(action_id, "Action"))
    if a is None or a.business_id != ctx.business.id:
        raise not_found("Action")
    return a


@router.get("/actions/{action_id}")
def get_action(action_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return S.action(db, _action(db, ctx, action_id))


@router.get("/actions/{action_id}/preflight")
def preflight(action_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    """Read-only safety evaluation for display. Execution re-evaluates under lock."""
    a = _action(db, ctx, action_id)
    phase = svc.Phase.EXECUTE if a.status == "approved" else svc.Phase.APPROVE
    ap = svc.latest_approval(db, a) if phase == svc.Phase.EXECUTE else None
    d = svc.evaluate_action(db, ctx.business, svc.Actor(ctx.user.id, "owner"), a, phase, ap)
    pos = compute_position(db, ctx.business)
    after = pos.available_minor - a.amount_minor if a.type in ("payroll_payment", "supplier_payment") else pos.available_minor
    db.rollback()
    return {
        "decision": d.to_dict(),
        "phase": phase.value,
        "available_minor": pos.available_minor,
        "available_after_minor": after,
        "reserve_minor": pos.reserve_minor,
        "evaluated_as": "owner",
    }


class ActionIn(BaseModel):
    type: Literal["payroll_payment", "supplier_payment", "payment_request"]
    amount_minor: Amount
    counterparty_id: uuid.UUID
    obligation_id: uuid.UUID | None = None
    invoice_id: uuid.UUID | None = None
    purpose: str = Field(min_length=1, max_length=500)


@router.post("/actions")
def create_action(body: ActionIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    a, d = svc.create_action(
        db,
        ctx.business.id,
        ctx.actor,
        type=body.type,
        amount_minor=body.amount_minor,
        counterparty_id=body.counterparty_id,
        obligation_id=body.obligation_id,
        invoice_id=body.invoice_id,
        purpose=body.purpose,
        source="user",
    )
    db.commit()
    return {"action": S.action(db, a), "decision": d.to_dict()}


class ReviseIn(BaseModel):
    amount_minor: Amount | None = None
    rebind_recipient: bool = False


@router.post("/actions/{action_id}/revise")
def revise(action_id: str, body: ReviseIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    a = _action(db, ctx, action_id)
    if body.rebind_recipient:
        biz = svc.lock_business(db, ctx.business.id)
        a = svc.lock_action(db, biz.id, a.id)
        cp = db.get(Counterparty, a.counterparty_id)
        if a.status not in ("proposed", "approved") or cp is None:
            raise ApiError(409, "ACTION_STATE_INVALID", f"Cannot rebind an action that is '{a.status}'.")
        old_v = a.counterparty_version
        a.counterparty_version, a.revision, a.status = cp.version, a.revision + 1, "proposed"
        svc.revoke_open_approvals(db, a, "Recipient rebound to new version")
        audit.append(
            db,
            biz.id,
            event_type="action.recipient_rebound",
            actor_type="user",
            actor_id=ctx.user.id,
            payload={"from_version": old_v, "to_version": cp.version, "account_ref": cp.account_ref},
            action_id=a.id,
        )
    if body.amount_minor is not None:
        a = svc.revise_amount(db, ctx.business.id, ctx.actor, a.id, body.amount_minor)
    rescue.refresh(db, svc.lock_business(db, ctx.business.id), "Action revised")
    db.commit()
    return S.action(db, a)


@router.post("/actions/{action_id}/approve")
def approve(action_id: str, db: DB, ctx: Ctx = Depends(owner_ctx)) -> Any:
    a = _action(db, ctx, action_id)
    ap, d, a = svc.approve_action(db, ctx.business.id, ctx.actor, a.id)
    if ap is not None:
        rescue.refresh(db, ctx.business, "Owner approved an action")
    db.commit()
    if ap is None:
        raise ApiError(
            409,
            "SAFETY_DENIED",
            "The safety kernel blocked this approval.",
            reasons=d.reasons,
            extra={"decision": d.to_dict(), "action": S.action(db, a)},
        )
    return {"approval": S.approval(ap), "decision": d.to_dict(), "action": S.action(db, a)}


@router.post("/actions/{action_id}/execute")
def execute(
    action_id: str,
    db: DB,
    response: Response,
    ctx: Ctx = Depends(owner_ctx),
    idempotency_key: str = Header(default="", alias="Idempotency-Key"),
) -> Any:
    a = _action(db, ctx, action_id)

    def ser(act: Action, tr: Any, d: Any) -> dict[str, Any]:
        return {"action_id": str(act.id), "status": act.status, "transfer": S.transfer(tr), "decision": d.to_dict()}

    res = svc.execute_action(db, ctx.business.id, ctx.actor, a.id, idempotency_key, ser)
    if not res.replayed:
        rescue.refresh(db, ctx.business, "Action submitted")
    db.commit()
    settlement = None
    if res.transfer_id is not None:
        settlement = simulator.bank_submit(db, res.transfer_id)
    if res.status_code != 200:
        raise ApiError(
            409,
            "SAFETY_DENIED",
            "The safety kernel blocked this submission.",
            reasons=res.body["decision"]["reasons"],
            extra={"decision": res.body["decision"], "replayed": res.replayed},
        )
    return {**res.body, "replayed": res.replayed, "settlement": settlement, "action": S.action(db, _action(db, ctx, action_id))}


class CancelIn(BaseModel):
    reason: str = Field(default="Cancelled by user", max_length=200)


@router.post("/actions/{action_id}/cancel")
def cancel(action_id: str, body: CancelIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    a = _action(db, ctx, action_id)
    a = svc.cancel_action(db, ctx.business.id, ctx.actor, a.id, body.reason)
    rescue.refresh(db, ctx.business, "Action cancelled")
    db.commit()
    return S.action(db, a)


# ------------------------------------------------------------- counterparties


@router.get("/counterparties")
def list_counterparties(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    cps = (
        db.execute(select(Counterparty).where(Counterparty.business_id == ctx.business.id).order_by(Counterparty.kind, Counterparty.name))
        .scalars()
        .all()
    )
    changes = (
        db.execute(
            select(CounterpartyChange)
            .where(CounterpartyChange.business_id == ctx.business.id)
            .order_by(CounterpartyChange.created_at.desc())
        )
        .scalars()
        .all()
    )
    return {
        "counterparties": [S.counterparty(c) for c in cps],
        "changes": [
            {
                "id": str(c.id),
                "counterparty_id": str(c.counterparty_id),
                "old_account_ref": c.old_account_ref,
                "new_account_ref": c.new_account_ref,
                "status": c.status,
                "created_at": c.created_at.isoformat(),
            }
            for c in changes
        ],
    }


class CounterpartyIn(BaseModel):
    kind: Literal["supplier", "customer", "other"]
    name: str = Field(min_length=1, max_length=200)
    account_ref: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


@router.post("/counterparties")
def create_counterparty(body: CounterpartyIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    svc.lock_business(db, ctx.business.id)
    cp = Counterparty(business_id=ctx.business.id, kind=body.kind, name=body.name, account_ref=body.account_ref, verified=False)
    db.add(cp)
    db.flush()
    audit.append(
        db,
        ctx.business.id,
        event_type="counterparty.created_unverified",
        actor_type="user",
        actor_id=ctx.user.id,
        payload={"name": body.name, "account_ref": body.account_ref},
    )
    db.commit()
    return S.counterparty(cp)


class ChangeIn(BaseModel):
    new_account_ref: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9-]+$")


@router.post("/counterparties/{cp_id}/changes")
def request_change(cp_id: str, body: ChangeIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    svc.lock_business(db, ctx.business.id)
    cp = db.get(Counterparty, _uuid(cp_id, "Counterparty"))
    if cp is None or cp.business_id != ctx.business.id:
        raise not_found("Counterparty")
    ch = CounterpartyChange(
        business_id=ctx.business.id,
        counterparty_id=cp.id,
        old_account_ref=cp.account_ref,
        new_account_ref=body.new_account_ref,
        requested_by=ctx.user.id,
    )
    db.add(ch)
    db.flush()
    audit.append(
        db,
        ctx.business.id,
        event_type="counterparty.change_requested",
        actor_type="user",
        actor_id=ctx.user.id,
        payload={"counterparty": cp.name, "old": cp.account_ref, "new": body.new_account_ref},
    )
    db.commit()
    return {"id": str(ch.id), "status": ch.status}


@router.post("/counterparty-changes/{change_id}/apply")
def apply_change(change_id: str, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = svc.lock_business(db, ctx.business.id)
    ch = db.get(CounterpartyChange, _uuid(change_id, "Change"))
    if ch is None or ch.business_id != biz.id:
        raise not_found("Change")
    if ch.status != "pending":
        raise ApiError(409, "CHANGE_NOT_PENDING", f"Change is {ch.status}.")
    cp = db.execute(select(Counterparty).where(Counterparty.id == ch.counterparty_id).with_for_update()).scalar_one()
    cp.account_ref, cp.version = ch.new_account_ref, cp.version + 1
    # A changed account must be re-verified against trusted sandbox records.
    cp.verified, cp.verified_at = False, None
    ch.status, ch.reviewed_by, ch.reviewed_at = "applied", ctx.user.id, real_now()
    revoked: list[str] = []
    for a in db.execute(select(Action).where(Action.counterparty_id == cp.id, Action.status.in_(("proposed", "approved")))).scalars():
        revoked += svc.revoke_open_approvals(db, a, f"Recipient {cp.name} changed to version {cp.version}")
    audit.append(
        db,
        biz.id,
        event_type="counterparty.changed",
        actor_type="user",
        actor_id=ctx.user.id,
        payload={"counterparty": cp.name, "new_version": cp.version, "new_account_ref": cp.account_ref, "revoked_approvals": revoked},
    )
    db.commit()
    return {"counterparty": S.counterparty(cp), "revoked_approvals": revoked}


@router.post("/counterparties/{cp_id}/verify")
def verify_counterparty(cp_id: str, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = svc.lock_business(db, ctx.business.id)
    cp = db.get(Counterparty, _uuid(cp_id, "Counterparty"))
    if cp is None or cp.business_id != biz.id:
        raise not_found("Counterparty")
    # "Verified" = matches the synthetic trusted registry (sandbox accounts only); not real bank verification.
    if not cp.account_ref.startswith("SBX-") or "ATTACKER" in cp.account_ref:
        audit.append(
            db,
            biz.id,
            event_type="counterparty.verification_failed",
            actor_type="user",
            actor_id=ctx.user.id,
            outcome="DENY",
            reasons=[{"code": "NOT_IN_TRUSTED_REGISTRY", "message": "Account is not in the trusted sandbox registry."}],
            payload={"account_ref": cp.account_ref},
        )
        db.commit()
        raise ApiError(409, "NOT_IN_TRUSTED_REGISTRY", f"{cp.account_ref} is not in the trusted sandbox registry.")
    cp.verified, cp.verified_at = True, real_now()
    audit.append(
        db,
        biz.id,
        event_type="counterparty.verified",
        actor_type="user",
        actor_id=ctx.user.id,
        payload={"counterparty": cp.name, "version": cp.version, "account_ref": cp.account_ref},
    )
    db.commit()
    return S.counterparty(cp)


# ------------------------------------------------------------------- policies


@router.get("/policies")
def list_policies(db: DB, ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    return [
        S.policy(p)
        for p in db.execute(select(Policy).where(Policy.business_id == ctx.business.id).order_by(Policy.version.desc())).scalars()
    ]


@router.post("/policies/{policy_id}/activate")
def activate_policy(policy_id: str, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = svc.lock_business(db, ctx.business.id)
    p = db.get(Policy, _uuid(policy_id, "Policy"))
    if p is None or p.business_id != biz.id:
        raise not_found("Policy")
    if p.status != "proposed":
        raise ApiError(409, "POLICY_NOT_PROPOSED", f"Policy v{p.version} is {p.status}.")
    if p.interpretation.get("questions"):
        raise ApiError(
            409, "POLICY_AMBIGUOUS", "Resolve the open questions before activating.", extra={"questions": p.interpretation["questions"]}
        )
    db.execute(update(Policy).where(Policy.business_id == biz.id, Policy.status == "active").values(status="superseded"))
    db.flush()
    p.status, p.activated_by, p.activated_at = "active", ctx.user.id, real_now()
    audit.append(
        db,
        biz.id,
        event_type="policy.activated",
        actor_type="user",
        actor_id=ctx.user.id,
        policy_version=p.version,
        payload={"note": "Approvals bound to earlier policy versions can no longer execute."},
    )
    rescue.refresh(db, biz, "Policy changed")
    db.commit()
    return S.policy(p)


# ----------------------------------------------------------------- kill switch


class KillIn(BaseModel):
    enabled: bool


@router.post("/kill-switch")
def kill_switch(body: KillIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    biz = svc.lock_business(db, ctx.business.id)
    biz.kill_switch = body.enabled
    audit.append(
        db,
        biz.id,
        event_type="kill_switch.enabled" if body.enabled else "kill_switch.disabled",
        actor_type="user",
        actor_id=ctx.user.id,
        payload={"note": "Blocks new submissions; reconciliation of submitted actions continues."},
    )
    db.commit()
    return {"kill_switch": biz.kill_switch}


# ----------------------------------------------------------- audit & receipts


@router.get("/audit")
def list_audit(
    db: DB,
    ctx: Ctx = Depends(get_ctx),
    q: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    stmt = select(AuditEvent).where(AuditEvent.business_id == ctx.business.id)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(AuditEvent.event_type.ilike(like), AuditEvent.outcome.ilike(like), AuditEvent.action_id.ilike(like)))
    return [S.audit_event(e) for e in db.execute(stmt.order_by(AuditEvent.seq.desc()).limit(limit)).scalars()]


@router.get("/audit/verify")
def verify_audit(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return {
        **audit.verify(db, ctx.business.id),
        "trust_note": "Detects edits, gaps and truncation relative to the stored head. A database administrator could "
        "rewrite the whole chain; compare the head hash with an independently kept checkpoint.",
    }


@router.get("/receipts")
def list_receipts(db: DB, ctx: Ctx = Depends(get_ctx)) -> list[dict[str, Any]]:
    return [
        S.receipt(r)
        for r in db.execute(select(Receipt).where(Receipt.business_id == ctx.business.id).order_by(Receipt.created_at.desc())).scalars()
    ]


@router.get("/receipts/{receipt_id}")
def get_receipt(receipt_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    r = db.get(Receipt, _uuid(receipt_id, "Receipt"))
    if r is None or r.business_id != ctx.business.id:
        raise not_found("Receipt")
    return S.receipt(r)


@router.get("/ledger")
def get_ledger(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    entries = (
        db.execute(
            select(JournalEntry).where(JournalEntry.business_id == ctx.business.id).order_by(JournalEntry.created_at.desc()).limit(50)
        )
        .scalars()
        .all()
    )
    return {
        **ledger.trial_balance(db, ctx.business.id),
        "entries": [
            {
                "id": str(e.id),
                "memo": e.memo,
                "effective_at": e.effective_at.isoformat(),
                "source_type": e.source_type,
                "reverses_entry_id": str(e.reverses_entry_id) if e.reverses_entry_id else None,
            }
            for e in entries
        ],
    }


# ------------------------------------------------------------------ assistant


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    page: str | None = Field(default=None, max_length=64)  # allowlisted page context, see agents.live.PAGES
    use_demo: bool = False  # explicit, labelled choice of the offline demo assistant
    chat_id: str | None = Field(default=None, max_length=64)  # which of the user's chats; default = most recent


@router.post("/assistant/chat")
def chat(body: ChatIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.send(db, ctx.user, ctx.business, ctx.role, body.message, body.page, body.use_demo, body.chat_id)


@router.get("/assistant/conversation")
def get_conversation(db: DB, ctx: Ctx = Depends(get_ctx), chat_id: str | None = Query(default=None, max_length=64)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.conversation(db, ctx.user, ctx.business, chat_id)


@router.get("/assistant/chats")
def list_chats(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return {"chats": conversation.list_chats(db, ctx.user, ctx.business)}


@router.post("/assistant/chats")
def new_chat(db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.new_chat(db, ctx.user, ctx.business)


class ChatRenameIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)


@router.patch("/assistant/chats/{chat_id}")
def rename_chat(chat_id: str, body: ChatRenameIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.rename_chat(db, ctx.user, ctx.business, chat_id, body.title)


@router.delete("/assistant/chats/{chat_id}")
def delete_chat(chat_id: str, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.delete_chat(db, ctx.user, ctx.business, chat_id)


@router.delete("/assistant/conversation")
def clear_conversation(db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return {"deleted": conversation.clear(db, ctx.user, ctx.business)}


@router.get("/assistant/proposals")
def list_proposals(db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    from app.agents import proposals

    return {"proposals": proposals.pending(db, ctx.business, ctx.user)}


@router.post("/assistant/proposals/{proposal_id}/confirm")
def confirm_proposal(proposal_id: str, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """The user's explicit confirmation of one AI suggestion. Runs the same checks as the forms."""
    from app.agents import chat as conversation

    return conversation.confirm_proposal(db, ctx.user, ctx.business, ctx.role, proposal_id)


@router.post("/assistant/proposals/{proposal_id}/dismiss")
def dismiss_proposal(proposal_id: str, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    from app.agents import proposals

    return proposals.dismiss(db, ctx.business.id, ctx.user, proposal_id)


@router.get("/assistant/status")
def assistant_status(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    from app.agents import chat as conversation

    return conversation.status(ctx.business)


@router.get("/assistant/health")
def assistant_health(ctx: Ctx = Depends(owner_ctx_read)) -> dict[str, Any]:
    """Owner-only connectivity check: missing config, invalid key, quota/billing, provider down or ok. No secrets."""
    from app.agents import live

    return live.health()


# ------------------------------------------------------------- reconciliation


@router.post("/reconcile")
def reconcile(db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """Check the bank for every pending outgoing transfer and settle/fail it exactly once."""
    from app.reconciliation.recovery import reconcile_pending

    biz_id, user_id = ctx.business.id, ctx.user.id
    db.commit()
    results = reconcile_pending(db, biz_id, actor_type="user", actor_id=user_id)
    return {"checked": len(results), "results": results}


# -------------------------------------------------------------------- sandbox


@router.post("/sandbox/events")
async def sandbox_event(request: Request, db: DB, x_sandbox_signature: str | None = Header(default=None)) -> dict[str, Any]:
    raw = await request.body()
    return recon.ingest(db, raw, x_sandbox_signature)


def _require_demo() -> None:
    if not fixtures.demo_reset_allowed():
        raise ApiError(403, "DEMO_DISABLED", "Sandbox simulation controls are disabled in this environment.")


class SimIn(BaseModel):
    action_id: uuid.UUID
    amount_minor: Amount | None = None


@router.post("/sandbox/simulate/customer-accept")
def sim_accept(body: SimIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    _require_demo()
    a = _action(db, ctx, str(body.action_id))
    db.commit()
    return simulator.customer_accepts(db, ctx.business.id, a.id)


@router.post("/sandbox/simulate/customer-refuse")
def sim_refuse(body: SimIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    _require_demo()
    a = _action(db, ctx, str(body.action_id))
    if a.type != "payment_request":
        raise ApiError(422, "NOT_A_REQUEST", "Customer refusals simulate a reply to a payment request.")
    db.commit()
    return simulator.customer_refuses(db, ctx.business.id, a.id, "Customer declined the partial-payment request (sandbox).")


@router.post("/sandbox/simulate/customer-pay")
def sim_pay(body: SimIn, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    _require_demo()
    a = _action(db, ctx, str(body.action_id))
    if a.type != "payment_request" or a.invoice_id is None:
        raise ApiError(422, "NOT_A_REQUEST", "Customer payments simulate a reply to a payment request.")
    if a.status not in ("sent", "accepted"):
        raise ApiError(409, "REQUEST_NOT_OPEN", f"The simulated customer only pays open requests; this one is '{a.status}'.")
    amount = body.amount_minor or (a.amount_minor - a.settled_minor)
    db.commit()
    return simulator.customer_pays(db, ctx.business.id, a.invoice_id, amount, a.id)


# ----------------------------------------------------------------------- demo


@router.get("/demo/scenarios")
def scenarios(ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    return {
        "enabled": fixtures.demo_reset_allowed(),
        "current": ctx.business.scenario,
        # A scenario with its own currency keeps it; the others are seeded in this demo's chosen books currency.
        "currency": ctx.business.currency,
        "scenarios": [{"key": k, **v, "currency": v.get("currency") or ctx.business.currency or "USD", "fixed_currency": bool(v.get("currency"))} for k, v in fixtures.SCENARIOS.items()],
    }


class ResetIn(BaseModel):
    scenario: str = Field(max_length=64)
    confirm: Literal["RESET"]
    # Books currency for the reset demo (any supported ISO 4217 code). Omitted: keep the current one.
    currency: str | None = Field(default=None, min_length=3, max_length=3)


@router.post("/demo/reset")
def demo_reset(body: ResetIn, response: Response, db: DB, ctx: Ctx = Depends(owner_ctx)) -> dict[str, Any]:
    """Reset only the current demo or private sample workspace (archives it, never deletes)."""
    _require_demo()
    biz0 = ctx.business
    if biz0.kind == "demo" and biz0.fixture_key == fixtures.FIXTURE_KEY:
        key, owner_user = fixtures.FIXTURE_KEY, None
    elif biz0.kind == "sample" and biz0.fixture_key == ws.sample_key(ctx.user.id):
        key, owner_user = biz0.fixture_key, ctx.user
    else:
        raise ApiError(403, "RESET_SCOPE", "Only a demo or sample workspace can be reset. Your own business workspace is never reset.")
    old_id = biz0.id
    if body.scenario not in fixtures.SCENARIOS:
        raise ApiError(422, "SCENARIO_UNKNOWN", "Unknown scenario.")
    # The owner's chosen books currency carries over to later resets (unless a scenario states its own).
    previous_fixed = fixtures.SCENARIOS.get(biz0.scenario or "", {}).get("currency")
    currency = (body.currency or "").upper() or (biz0.currency if not previous_fixed else None)
    if currency:
        from app.money import SUPPORTED_CURRENCIES

        if currency not in SUPPORTED_CURRENCIES:
            raise ApiError(422, "CURRENCY_UNSUPPORTED", f"{currency} isn't a supported ISO 4217 currency.")
    db.commit()
    biz = fixtures.seed_fixture(db, body.scenario, reset_by=ctx.user.id, key=key, owner_user=owner_user, currency=currency)
    fixtures.revoke_sessions_for(db, old_id)
    user = db.get(User, ctx.user.id)
    assert user is not None
    csrf = sess.issue(db, None, response, user, biz)
    db.commit()
    return {
        "scenario": body.scenario,
        "message": f"Reset to '{fixtures.SCENARIOS[body.scenario]['title']}'. Previous workspace archived, not deleted.",
        **me_view(db, user, biz, "owner", csrf),
    }


_ = fmt  # re-exported for templates in future routes
