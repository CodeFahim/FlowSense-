"""PDF evidence intake: bounded text extraction only.

The upload is untrusted. We check the extension, the %PDF- magic bytes and the
size before parsing, then extract text in a separate, short-lived process with
a hard wall-clock timeout (and, on Linux, an address-space limit), so a hostile
file cannot hang or exhaust the API worker. Only page text is read: links,
annotations, forms, attachments and JavaScript are never followed or run.
Encrypted, malformed, over-long and image-only files are rejected.
"""

from __future__ import annotations

import io
import multiprocessing as mp
from dataclasses import dataclass
from typing import Any

from app.errors import ApiError

MAX_PDF_BYTES = 5 * 1024 * 1024
MAX_PDF_PAGES = 20
# Generous because the free hosting plan has a small share of one CPU: starting the child interpreter and walking
# 15 image-only pages took over 10 s there (live, 19 Oct) although it takes 2 s on a laptop.
EXTRACT_TIMEOUT_S = 20.0
MAX_EXTRACT_CHARS = 20_000  # same cap as text evidence
_CHILD_MEMORY_BYTES = 512 * 1024 * 1024


@dataclass
class PdfText:
    text: str
    pages: int
    truncated: bool


def validate_upload(filename: str | None, data: bytes) -> None:
    if not (filename or "").lower().endswith(".pdf"):
        raise ApiError(415, "PDF_EXTENSION", "Only .pdf files are accepted.")
    if len(data) > MAX_PDF_BYTES:
        raise ApiError(413, "PDF_TOO_LARGE", f"PDF uploads are limited to {MAX_PDF_BYTES // (1024 * 1024)} MB.")
    if not data.startswith(b"%PDF-"):
        raise ApiError(415, "PDF_NOT_PDF", "File is not a PDF (missing %PDF- header).")


def _extract(data: bytes, page_marks: bool = False) -> tuple[str, Any, Any]:
    """Runs in the child process. Returns (status, a, b)."""
    try:  # best-effort memory cap; resource is POSIX-only
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (_CHILD_MEMORY_BYTES, _CHILD_MEMORY_BYTES))  # type: ignore[attr-defined]
    except (ImportError, ValueError, OSError):
        pass
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            return "error", "PDF_ENCRYPTED", "Encrypted or password-protected PDFs are not accepted."
        n = len(reader.pages)
        if n > MAX_PDF_PAGES:
            return "error", "PDF_TOO_MANY_PAGES", f"PDFs are limited to {MAX_PDF_PAGES} pages (this one has {n})."
        parts: list[str] = []
        size = 0
        for i, page in enumerate(reader.pages, 1):
            t = page.extract_text() or ""
            # Page markers let a reviewer see where a value came from (document reader only).
            parts.append(f"[Page {i}]\n{t}" if page_marks and t.strip() else t)
            size += len(t)
            if size > MAX_EXTRACT_CHARS:
                break
        return "ok", "\n\n".join(parts), n
    except Exception:  # malformed input of any kind
        return "error", "PDF_MALFORMED", "The PDF could not be parsed."


def _child(data: bytes, conn: Any, page_marks: bool = False) -> None:
    try:
        conn.send(_extract(data, page_marks))
    finally:
        conn.close()


def extract_text(data: bytes, timeout_s: float = EXTRACT_TIMEOUT_S, page_marks: bool = False) -> PdfText:
    ctx = mp.get_context("spawn")  # fresh interpreter: no inherited DB connections or threads
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(data, send, page_marks), daemon=True)
    proc.start()
    send.close()
    try:
        if not recv.poll(timeout_s):
            raise ApiError(422, "PDF_TIMEOUT", "This PDF took too long to read. Upload only the pages you need (for example the licence page), or a photo of it.")
        try:
            status, a, b = recv.recv()
        except EOFError as exc:  # child died (e.g. memory limit)
            raise ApiError(422, "PDF_MALFORMED", "The PDF could not be parsed.") from exc
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)
    if status != "ok":
        raise ApiError(422, a, b)
    text = str(a).replace("\x00", "")
    if not text.strip():
        raise ApiError(422, "PDF_NO_TEXT", "This PDF has no text of its own (it is probably a scan). Type the key facts as a note, or use “Add from a document”, which reads scans.")
    truncated = len(text) > MAX_EXTRACT_CHARS
    return PdfText(text=text[:MAX_EXTRACT_CHARS], pages=int(b), truncated=truncated)
