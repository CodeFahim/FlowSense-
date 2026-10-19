"""Reading uploaded business documents: PDF, photos/scans (PNG, JPEG), Word (.docx) and Excel (.xlsx).

Safety:
- The file type comes from the bytes (magic numbers and zip contents), never from the name or the
  browser's content type. Old binary .doc/.xls, archives, executables and anything else are refused.
- Size limits per file and per batch; image dimensions are read from the header and capped before any
  decoding (decompression bombs); zip members are size- and ratio-checked (zip bombs).
- Parsing runs in a separate, memory-capped process with a time limit (as for PDF evidence). The XML
  parser is the standard library's expat, which resolves no external entities and limits entity expansion.
- Text from documents is untrusted data: it is shown to the user and passed to extractors as data only.
"""

from __future__ import annotations

import io
import multiprocessing as mp
import shutil
import struct
import subprocess
import zipfile
from dataclasses import dataclass
from typing import Any

from app.documents import pdf as pdf_intake
from app.errors import ApiError

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_FILES = 10
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
MAX_IMAGE_PIXELS = 40_000_000  # e.g. 8000 x 5000
MAX_ZIP_MEMBER_BYTES = 30 * 1024 * 1024
MAX_ZIP_RATIO = 100
MAX_SHEET_ROWS = 500
PARSE_TIMEOUT_S = 15.0
OCR_TIMEOUT_S = 30.0
_CHILD_MEMORY_BYTES = 512 * 1024 * 1024

KINDS = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpeg": "image/jpeg",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
EXT = {"pdf": "pdf", "png": "png", "jpeg": "jpg", "docx": "docx", "xlsx": "xlsx"}


@dataclass
class Extracted:
    text: str
    method: str  # pdf_text | ocr | docx | xlsx | none
    pages: int | None = None
    note: str | None = None  # plain-language note when little or nothing could be read
    needs_vision: bool = False  # an image or scanned PDF: an AI vision model may read it better


# --------------------------------------------------------------------------- type detection


def _png_size(b: bytes) -> tuple[int, int] | None:
    if len(b) >= 24 and b[12:16] == b"IHDR":
        w, h = struct.unpack(">II", b[16:24])
        return int(w), int(h)
    return None


def _jpeg_size(b: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            i += 1
            continue
        marker = b[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg = struct.unpack(">H", b[i + 2 : i + 4])[0]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h, w = struct.unpack(">HH", b[i + 5 : i + 9])
            return int(w), int(h)
        i += 2 + seg
    return None


def _zip_kind(data: bytes) -> str | None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            names = set(z.namelist())
            if "[Content_Types].xml" not in names:
                return None
            for info in z.infolist():
                if info.file_size > MAX_ZIP_MEMBER_BYTES or (info.compress_size and info.file_size / max(info.compress_size, 1) > MAX_ZIP_RATIO):
                    raise ApiError(422, "DOC_ZIP_BOMB", "This file expands to an unsafe size and was refused.")
            if "word/document.xml" in names:
                return "docx"
            if "xl/workbook.xml" in names:
                return "xlsx"
    except zipfile.BadZipFile:
        return None
    return None


def detect(filename: str | None, data: bytes) -> str:
    """Return the document kind from the bytes, or refuse with a plain-language reason."""
    if not data:
        raise ApiError(422, "DOC_EMPTY", f"{filename or 'The file'} is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ApiError(413, "DOC_TOO_LARGE", f"{filename or 'A file'} is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    if data.startswith(b"%PDF-"):
        return "pdf"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        size = _png_size(data)
        if size is None or size[0] * size[1] > MAX_IMAGE_PIXELS:
            raise ApiError(422, "DOC_IMAGE_TOO_BIG", "This image is too large or damaged. Use a photo under 40 megapixels.")
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        size = _jpeg_size(data)
        if size is None or size[0] * size[1] > MAX_IMAGE_PIXELS:
            raise ApiError(422, "DOC_IMAGE_TOO_BIG", "This image is too large or damaged. Use a photo under 40 megapixels.")
        return "jpeg"
    if data.startswith(b"PK\x03\x04"):
        kind = _zip_kind(data)
        if kind:
            return kind
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise ApiError(415, "DOC_OLD_OFFICE", "Old .doc/.xls files aren't supported. Save it as .docx or .xlsx (or PDF) and try again.")
    raise ApiError(415, "DOC_TYPE", "Use a PDF, a photo (JPG or PNG), a Word file (.docx) or an Excel file (.xlsx).")


# --------------------------------------------------------------------------- parsing (child process)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def _docx_text(data: bytes) -> str:
    import defusedxml.ElementTree as ET

    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for p in root.iter(f"{_W}p"):
        line = "".join(t.text or "" for t in p.iter(f"{_W}t"))
        if line.strip():
            lines.append(line)
    # Table cells are paragraphs too, so they are included above.
    return "\n".join(lines)


def _col(ref: str) -> int:
    n = 0
    for ch in ref:
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
        else:
            break
    return n


def _xlsx_text(data: bytes) -> str:
    import defusedxml.ElementTree as ET

    out: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        shared: list[str] = []
        if "xl/sharedStrings.xml" in names:
            for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        sheets = sorted(n for n in names if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
        rows_seen = 0
        for sheet in sheets[:5]:
            out.append(f"[Sheet {sheet.rsplit('/', 1)[-1][:-4]}]")
            for row in ET.fromstring(z.read(sheet)).iter(f"{_S}row"):
                cells: dict[int, str] = {}
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    t = c.get("t")
                    if t == "s" and v is not None and v.text is not None and v.text.isdigit():
                        idx = int(v.text)
                        val = shared[idx] if idx < len(shared) else ""
                    elif t == "inlineStr":
                        val = "".join(x.text or "" for x in c.iter(f"{_S}t"))
                    else:
                        val = v.text if v is not None and v.text is not None else ""
                    if val.strip():
                        cells[_col(c.get("r", "A"))] = val.strip()
                if cells:
                    out.append(" | ".join(cells[k] for k in sorted(cells)))
                rows_seen += 1
                if rows_seen >= MAX_SHEET_ROWS:
                    out.append("[More rows not read]")
                    return "\n".join(out)
    return "\n".join(out)


def _parse(kind: str, data: bytes) -> tuple[str, Any]:
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (_CHILD_MEMORY_BYTES, _CHILD_MEMORY_BYTES))  # type: ignore[attr-defined]
    except (ImportError, ValueError, OSError):
        pass
    try:
        return "ok", (_docx_text(data) if kind == "docx" else _xlsx_text(data))
    except Exception:  # malformed input of any kind
        return "error", "This file could not be read. It may be damaged."


def _child(kind: str, data: bytes, conn: Any) -> None:
    try:
        conn.send(_parse(kind, data))
    finally:
        conn.close()


def _run_parser(kind: str, data: bytes) -> str:
    ctx = mp.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child, args=(kind, data, send), daemon=True)
    proc.start()
    send.close()
    try:
        if not recv.poll(PARSE_TIMEOUT_S):
            raise ApiError(422, "DOC_TIMEOUT", "Reading this file took too long.")
        try:
            status, value = recv.recv()
        except EOFError as exc:
            raise ApiError(422, "DOC_MALFORMED", "This file could not be read. It may be damaged.") from exc
    finally:
        recv.close()
        if proc.is_alive():
            proc.kill()
        proc.join(5)
    if status != "ok":
        raise ApiError(422, "DOC_MALFORMED", str(value))
    return str(value)


# --------------------------------------------------------------------------- OCR


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _ocr(data: bytes) -> str:
    """Tesseract OCR on one image, fed through stdin (no temporary files), with a time limit."""
    exe = shutil.which("tesseract")
    if exe is None:
        raise ApiError(503, "OCR_UNAVAILABLE", "Text recognition isn't installed on this server.")
    try:
        # Fixed arguments, no shell; the untrusted image is only data on stdin.
        r = subprocess.run([exe, "stdin", "stdout", "-l", "eng", "--psm", "3"], input=data, capture_output=True, timeout=OCR_TIMEOUT_S, check=False)  # noqa: S603
    except subprocess.TimeoutExpired as exc:
        raise ApiError(422, "OCR_TIMEOUT", "Reading the text in this image took too long. Try a smaller or clearer photo.") from exc
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else ""


ocr_runner = _ocr  # replaced in tests

MAX_OCR_PAGES = 10
RASTER_MAX_SIDE_PX = 2200  # longest side of a rendered page, whatever the PDF claims its page size is
RASTER_TIMEOUT_S = 30.0
MAX_PAGE_IMAGE_BYTES = 15 * 1024 * 1024
_RASTER_MEMORY_BYTES = 768 * 1024 * 1024
_RASTER_FILE_BYTES = 40 * 1024 * 1024
OCR_TOTAL_BUDGET_S = 35.0  # with the 20 s text step, a scan answers within about a minute even on the free host


def _raster_limits() -> None:
    """Runs in the pdftoppm child before exec (POSIX): caps its memory and the size of any file it writes."""
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (_RASTER_MEMORY_BYTES, _RASTER_MEMORY_BYTES))  # type: ignore[attr-defined]
    resource.setrlimit(resource.RLIMIT_FSIZE, (_RASTER_FILE_BYTES, _RASTER_FILE_BYTES))  # type: ignore[attr-defined]


def _rasterize(data: bytes, max_pages: int = MAX_OCR_PAGES) -> list[bytes]:
    """Render the first pages of a (scanned) PDF to PNG images with Poppler's pdftoppm, for OCR.

    The PDF is passed on stdin and written as images into a private temporary folder that is removed
    afterwards; fixed arguments, no shell. Bounded: every page is scaled to at most RASTER_MAX_SIDE_PX
    (a huge MediaBox can't produce a huge image), the process has memory and file-size limits and a time
    limit, and an oversized image is skipped. Returns [] when pdftoppm isn't installed.
    """
    import os
    import tempfile
    from pathlib import Path

    exe = shutil.which("pdftoppm")
    if exe is None:
        return []
    with tempfile.TemporaryDirectory(prefix="fs-ocr-") as tmp:
        try:
            subprocess.run(  # noqa: S603
                [exe, "-png", "-gray", "-scale-to", str(RASTER_MAX_SIDE_PX), "-f", "1", "-l", str(max_pages), "-", str(Path(tmp) / "p")],
                input=data, capture_output=True, timeout=RASTER_TIMEOUT_S, check=False,
                preexec_fn=_raster_limits if os.name == "posix" else None,  # noqa: PLW1509 (no threads touched before exec)
            )
        except subprocess.TimeoutExpired as exc:
            raise ApiError(422, "OCR_TIMEOUT", "Reading this scanned PDF took too long. Upload fewer pages or clearer scans.") from exc
        pages = sorted(Path(tmp).glob("p*.png"), key=lambda p: int(p.stem.rsplit("-", 1)[-1]) if "-" in p.stem else 0)
        return [p.read_bytes() for p in pages[:max_pages] if p.stat().st_size <= MAX_PAGE_IMAGE_BYTES]


pdf_rasterizer = _rasterize  # replaced in tests


def ocr_pdf_available() -> bool:
    return (shutil.which("pdftoppm") is not None or pdf_rasterizer is not _rasterize) and (ocr_available() or ocr_runner is not _ocr)


def _ocr_scanned_pdf(data: bytes) -> tuple[str, int]:
    """OCR every rendered page (up to MAX_OCR_PAGES), marked "[Page N]" so values can be traced."""
    import time

    images = pdf_rasterizer(data)
    parts = []
    deadline = time.monotonic() + OCR_TOTAL_BUDGET_S  # one budget for all pages, not 30 s each
    for i, img in enumerate(images, 1):
        if time.monotonic() > deadline:
            parts.append(f"[Page {i}]\n[Not read: the time limit for this document was reached]")
            break
        t = ocr_runner(img).strip()
        if t:
            parts.append(f"[Page {i}]\n{t}")
    return "\n\n".join(parts), len(images)


# --------------------------------------------------------------------------- public


def _clean(text: str) -> str:
    from app.documents.evidence import clean_text

    if not (text or "").strip():
        return ""  # nothing readable: the caller explains that, it isn't an error
    return clean_text(text)[:MAX_TEXT_CHARS]


def extract(kind: str, data: bytes) -> Extracted:
    if kind == "pdf":
        try:
            r = pdf_intake.extract_text(data, page_marks=True)
            return Extracted(_clean(r.text), "pdf_text", pages=r.pages)
        except ApiError as exc:
            if exc.code == "PDF_ENCRYPTED":
                raise ApiError(422, "DOC_ENCRYPTED", "This PDF is password-protected. Remove the password (or print it to a new PDF) and upload it again.") from exc
            if exc.code == "PDF_MALFORMED":
                raise ApiError(422, "DOC_MALFORMED", "This PDF is damaged and couldn't be read. Export it again, or upload a photo of it.") from exc
            if exc.code not in ("PDF_NO_TEXT", "PDF_TIMEOUT"):
                raise
        # No text layer (a scanned PDF), or the text layer took too long to read (usually page after page of
        # images): render the first pages and read them with text recognition instead of failing.
        if not ocr_pdf_available():
            return Extracted("", "none", note="This PDF is a scan and text recognition isn't available on this server. Upload photos of the pages, or type the details.", needs_vision=True)
        text, pages = _ocr_scanned_pdf(data)
        text = _clean(text)
        note = None
        if pages == 0:
            note = "This scanned PDF couldn't be rendered for text recognition. Upload photos of the pages, or type the details."
        elif len(text.strip()) < 20:
            note = "Very little text could be read from this scan. Use a sharper scan or photo, or type the details."
        elif pages >= MAX_OCR_PAGES:
            note = f"Only the first {MAX_OCR_PAGES} pages were read."
        return Extracted(text, "pdf_ocr", pages=pages or None, note=note, needs_vision=True)
    if kind in ("png", "jpeg"):
        if not ocr_available() and ocr_runner is _ocr:
            return Extracted("", "none", note="Text recognition isn't available on this server, so type the details (or upload a PDF, Word or Excel file).", needs_vision=True)
        text = _clean(ocr_runner(data))
        note = None if len(text.strip()) >= 20 else "Very little text could be read from this image. Use a sharp, well-lit photo, or type the details."
        return Extracted(text, "ocr", note=note, needs_vision=True)
    if kind in ("docx", "xlsx"):
        return Extracted(_clean(_run_parser(kind, data)), kind)
    raise ApiError(415, "DOC_TYPE", "Unsupported file type.")
