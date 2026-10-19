"""Upload business documents, read them, and suggest form fields for the user to review.

Flow (one request per file keeps each request small enough for the hosting proxy):
  1. POST /api/documents            one file + purpose (+ batch_id for the 2nd, 3rd ... file)
  2. POST /api/documents/batches/{batch_id}/suggest   read the whole batch -> suggested fields, flags
  3. the user reviews and saves through the normal forms, then
  4. POST /api/documents/link       remembers which document a saved record came from.
Nothing here creates invoices, customers, cash or ledger entries. Files are kept privately per workspace
and served only to members of that workspace, as downloads with a fixed content type.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import Ctx, get_ctx, mutating_ctx
from app.audit import chain as audit
from app.auth import ratelimit
from app.db import get_db
from app.documents import fields as F
from app.documents import intake
from app.documents.evidence import looks_like_instructions
from app.errors import ApiError, not_found
from app.models import Business, Counterparty, Document, DocumentLink, Invoice

router = APIRouter(prefix="/api")
DB = Annotated[Session, Depends(get_db)]
H = timedelta(hours=1)
_SAFE_NAME = re.compile(r"[^\w .()\-]+", re.UNICODE)


def _display_name(name: str | None, kind: str) -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _SAFE_NAME.sub("", base).strip(" .")[:100]
    return base or f"document.{intake.EXT[kind]}"


def _view(d: Document, links: list[DocumentLink] | None = None) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "batch_id": str(d.batch_id),
        "purpose": d.purpose,
        "kind": d.kind,
        "name": d.original_name,
        "size_bytes": d.size_bytes,
        "pages": d.pages,
        "method": d.method,
        "chars": len(d.text or ""),
        "text_preview": (d.text or "")[:1500],
        "instruction_like": d.instruction_like,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "file_url": f"/api/documents/{d.id}/file",
        "linked": [{"type": link.record_type, "id": str(link.record_id)} for link in (links or [])],
    }


def _doc(db: Session, ctx: Ctx, doc_id: str | uuid.UUID) -> Document:
    try:
        did = uuid.UUID(str(doc_id))
    except ValueError:
        raise not_found("Document") from None
    d = db.get(Document, did)
    # Another workspace's document, and a business (licence) document for a non-owner, look missing.
    if d is None or d.business_id != ctx.business.id or (d.purpose == "business" and ctx.role != "owner"):
        raise not_found("Document")
    return d


@router.post("/documents")
def upload(
    db: DB,
    file: UploadFile = File(...),
    purpose: Literal["business", "customer"] = Form(...),
    batch_id: str | None = Form(default=None, max_length=64),
    ctx: Ctx = Depends(mutating_ctx),
) -> dict[str, Any]:
    ratelimit.check(db, ratelimit.bucket("doc-upload", str(ctx.user.id)), 60, H, "Too many uploads. Try again in an hour.")
    if purpose == "business" and ctx.role != "owner":
        raise ApiError(403, "OWNER_ONLY", "Only the owner can set up the business details.")
    if batch_id:
        try:
            batch = uuid.UUID(batch_id)
        except ValueError:
            raise ApiError(422, "BATCH_INVALID", "Start a new upload.") from None
        first = db.execute(select(Document.business_id, Document.purpose).where(Document.batch_id == batch).limit(1)).first()
        if first is None or first.business_id != ctx.business.id:
            raise not_found("Upload")
        if first.purpose != purpose:
            raise ApiError(422, "BATCH_PURPOSE", "This file belongs to a different kind of upload. Start a new upload.")
        n = db.execute(select(func.count(Document.id)).where(Document.batch_id == batch)).scalar_one()
        size = db.execute(select(func.coalesce(func.sum(Document.size_bytes), 0)).where(Document.batch_id == batch)).scalar_one()
    else:
        batch, n, size = uuid.uuid4(), 0, 0
    if n >= intake.MAX_FILES:
        raise ApiError(422, "DOC_TOO_MANY", f"Up to {intake.MAX_FILES} files at a time.")
    data = file.file.read(intake.MAX_FILE_BYTES + 1)
    if size + len(data) > intake.MAX_BATCH_BYTES:
        raise ApiError(413, "DOC_BATCH_TOO_LARGE", f"Together these files are larger than {intake.MAX_BATCH_BYTES // (1024 * 1024)} MB.")
    kind = intake.detect(file.filename, data)
    digest = hashlib.sha256(data).hexdigest()
    seen = db.execute(select(Document.created_at).where(Document.business_id == ctx.business.id, Document.sha256 == digest).order_by(Document.created_at).limit(1)).scalar_one_or_none()
    biz_id, user_id = ctx.business.id, ctx.user.id
    db.commit()  # release the transaction while the bounded parsing runs
    ex = intake.extract(kind, data)
    d = Document(
        business_id=biz_id, uploaded_by=user_id, batch_id=batch, purpose=purpose, kind=kind,
        original_name=_display_name(file.filename, kind), size_bytes=len(data), sha256=digest, data=data,
        text=ex.text, method=ex.method, pages=ex.pages, instruction_like=looks_like_instructions(ex.text),
    )
    db.add(d)
    db.flush()
    audit.append(db, biz_id, event_type="document.uploaded", actor_type="user", actor_id=user_id,
                 payload={"document": str(d.id), "kind": kind, "purpose": purpose, "size_bytes": len(data), "sha256": digest, "method": ex.method, "chars": len(ex.text)})
    db.commit()
    return {
        "batch_id": str(batch),
        "document": _view(d),
        "note": ex.note,
        "duplicate_file": seen.isoformat() if seen else None,
    }


def _flags(db: Session, biz: Business, purpose: str, fields: dict[str, dict[str, str]], docs: list[Document], doc_type: str = "unknown") -> list[dict[str, str]]:
    flags: list[dict[str, str]] = []
    if purpose == "customer" and doc_type == "business_licence":
        flags.append({"tone": "warn", "text": "This looks like a business licence, not a customer document. Use it in Setup → Business details instead."})
    if purpose == "business" and doc_type in ("invoice", "receipt"):
        flags.append({"tone": "warn", "text": f"This looks like {'an invoice' if doc_type == 'invoice' else 'a receipt'}, not a business licence. Add it from Customers instead."})
    if purpose == "customer" and doc_type == "receipt":
        flags.append({"tone": "info", "text": "This looks like a receipt. Saving adds the customer and, if you keep it ticked, an invoice as money owed. It never records a payment or changes your cash: record a payment separately if one arrived."})
    for d in docs:
        if d.instruction_like:
            flags.append({"tone": "warn", "text": f"{d.original_name} contains text that looks like instructions (for example to send money). FlowSense ignores it: documents are data only."})
        prior = db.execute(select(Document.id, Document.created_at).where(Document.business_id == biz.id, Document.sha256 == d.sha256, Document.id != d.id).order_by(Document.created_at)).all()
        if prior:
            # The same file already became a saved invoice: importing it again would duplicate that invoice.
            saved = db.execute(select(DocumentLink.record_type).where(DocumentLink.document_id.in_([p.id for p in prior]), DocumentLink.record_type == "invoice").limit(1)).first()
            if saved is not None and purpose == "customer":
                flags.append({"tone": "error", "field": "invoice_number", "code": "ALREADY_IMPORTED",
                              "text": f"{d.original_name} was already imported as an invoice on {prior[0].created_at:%d %b %Y}. Saving it again would create a duplicate, so the invoice is skipped unless you change the number."})
            else:
                flags.append({"tone": "warn", "text": f"{d.original_name} was already uploaded on {prior[0].created_at:%d %b %Y}. Check you aren't adding the same record twice."})
    if purpose == "customer":
        num = fields.get("invoice_number", {}).get("value")
        if num and db.execute(select(Invoice.id).where(Invoice.business_id == biz.id, Invoice.number == num)).first():
            flags.append({"tone": "error", "field": "invoice_number", "text": f"Invoice {num} already exists. Saving it again would create a duplicate, so change the number or skip the invoice."})
        name = fields.get("customer_name", {}).get("value")
        if name:
            cp = db.execute(select(Counterparty).where(Counterparty.business_id == biz.id, Counterparty.kind == "customer", func.lower(Counterparty.name) == name.lower()).limit(1)).scalar_one_or_none()
            if cp is not None:
                flags.append({"tone": "info", "field": "customer_name", "text": f"{cp.name} is already one of your customers. The invoice will be added to that customer, not a new one."})
        cur = fields.get("currency", {}).get("value")
        if cur and biz.currency and cur != biz.currency:
            flags.append({"tone": "error", "field": "currency", "text": f"This document is in {cur}, but your books are in {biz.currency}. FlowSense records amounts only in {biz.currency}; convert the amount first."})
    else:
        name = fields.get("name", {}).get("value")
        if name and biz.name and name != biz.name:
            flags.append({"tone": "info", "field": "name", "text": f"Your saved business name is “{biz.name}”. Saving will change it to “{name}” only if you keep this value."})
        exp = fields.get("expiry_date", {}).get("value")
        if exp:
            from app.domain.records import business_today

            try:
                today = business_today(biz) if biz.timezone else None
            except Exception:
                today = None
            if today and exp < today.isoformat():
                flags.append({"tone": "warn", "field": "expiry_date", "text": f"This licence appears to have expired on {exp}."})
    return flags


@router.post("/documents/batches/{batch_id}/suggest")
def suggest(batch_id: str, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    ratelimit.check(db, ratelimit.bucket("doc-suggest", str(ctx.user.id)), 60, H, "Too many requests. Try again in an hour.")
    try:
        batch = uuid.UUID(batch_id)
    except ValueError:
        raise not_found("Upload") from None
    docs = db.execute(select(Document).where(Document.batch_id == batch, Document.business_id == ctx.business.id).order_by(Document.created_at)).scalars().all()
    if not docs:
        raise not_found("Upload")
    purpose = docs[0].purpose
    if purpose == "business" and ctx.role != "owner":
        raise ApiError(403, "OWNER_ONLY", "Only the owner can read business documents.")
    text = "\n\n".join(f"[{d.original_name}]\n{d.text}" for d in docs if d.text.strip())[: intake.MAX_TEXT_CHARS]
    # Pictures and scanned PDFs (read by OCR, or not at all) also go to the AI reader, which reads scans better.
    images = [(intake.KINDS[d.kind], d.data) for d in docs if d.kind in ("png", "jpeg") or (d.kind == "pdf" and (d.method in ("pdf_ocr", "none") or not d.text.strip()))]
    biz = ctx.business
    db.commit()
    out = F.suggest(purpose, text, images)
    out["flags"] = _flags(db, biz, purpose, out["fields"], list(docs), out.get("doc_type", "unknown"))
    out["documents"] = [_view(d) for d in docs]
    out["batch_id"] = str(batch)
    out["purpose"] = purpose
    out["nothing_saved"] = True
    for d in docs:
        d.suggestion = {k: v for k, v in out.items() if k in ("fields", "missing", "missing_for_invoice", "notes", "ai_used", "doc_type")}
    audit.append(db, biz.id, event_type="document.read", actor_type="user", actor_id=ctx.user.id,
                 payload={"batch": str(batch), "files": len(docs), "fields": sorted(out["fields"]), "ai_used": out["ai_used"], "doc_type": out.get("doc_type")})
    db.commit()
    return out


@router.get("/documents")
def list_documents(db: DB, ctx: Ctx = Depends(get_ctx), purpose: Literal["business", "customer"] | None = None) -> dict[str, Any]:
    q = select(Document).where(Document.business_id == ctx.business.id).order_by(Document.created_at.desc()).limit(100)
    if purpose:
        q = q.where(Document.purpose == purpose)
    if ctx.role != "owner":
        q = q.where(Document.purpose != "business")  # licence documents are the owner's
    docs = db.execute(q).scalars().all()
    links = db.execute(select(DocumentLink).where(DocumentLink.document_id.in_([d.id for d in docs]))).scalars().all() if docs else []
    by: dict[uuid.UUID, list[DocumentLink]] = {}
    for link in links:
        by.setdefault(link.document_id, []).append(link)
    return {"documents": [_view(d, by.get(d.id)) for d in docs]}


@router.get("/documents/{doc_id}")
def get_document(doc_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> dict[str, Any]:
    d = _doc(db, ctx, doc_id)
    links = db.execute(select(DocumentLink).where(DocumentLink.document_id == d.id)).scalars().all()
    return {**_view(d, list(links)), "text": d.text, "suggestion": d.suggestion}


@router.get("/documents/{doc_id}/file")
def download(doc_id: str, db: DB, ctx: Ctx = Depends(get_ctx)) -> Response:
    d = _doc(db, ctx, doc_id)
    # Always a download with the type FlowSense detected (never the uploader's), never rendered inline.
    name = f"flowsense-document-{str(d.id)[:8]}.{intake.EXT[d.kind]}"
    return Response(
        content=d.data,
        media_type=intake.KINDS[d.kind],
        headers={"Content-Disposition": f'attachment; filename="{name}"', "X-Content-Type-Options": "nosniff"},
    )


class LinkIn(BaseModel):
    document_ids: list[uuid.UUID] = Field(min_length=1, max_length=intake.MAX_FILES)
    record_type: Literal["invoice", "counterparty", "business"]
    record_id: uuid.UUID


@router.post("/documents/link")
def link(body: LinkIn, db: DB, ctx: Ctx = Depends(mutating_ctx)) -> dict[str, Any]:
    """Remember that a saved record came from these documents (the record itself was saved by the user)."""
    biz = ctx.business
    model = {"invoice": Invoice, "counterparty": Counterparty, "business": Business}[body.record_type]
    rec = db.get(model, body.record_id)
    rec_biz = rec.id if isinstance(rec, Business) else getattr(rec, "business_id", None)
    if rec is None or rec_biz != biz.id:
        raise not_found("Record")
    made = 0
    for did in dict.fromkeys(body.document_ids):
        d = _doc(db, ctx, did)
        exists = db.execute(select(DocumentLink.id).where(DocumentLink.document_id == d.id, DocumentLink.record_type == body.record_type, DocumentLink.record_id == body.record_id)).first()
        if exists is None:
            db.add(DocumentLink(document_id=d.id, business_id=biz.id, record_type=body.record_type, record_id=body.record_id))
            made += 1
    audit.append(db, biz.id, event_type="document.linked", actor_type="user", actor_id=ctx.user.id, payload={"record_type": body.record_type, "record_id": str(body.record_id), "documents": made})
    db.commit()
    return {"linked": made}
