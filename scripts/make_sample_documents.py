"""Synthetic sample documents for the document reader (no real people or businesses).

Needs Poppler's pdftoppm (installed in the API image). From the repo root:
  docker compose cp scripts/make_sample_documents.py api:/tmp/m.py
  docker compose exec -T api python /tmp/m.py /tmp/sampledocs
  docker compose cp api:/tmp/sampledocs samples/documents
Writes:
  invoice-text.pdf      two-page text invoice (items, subtotal, VAT, amount due, BDT)
  invoice-scanned.pdf   the same invoice as page images only (no text layer: needs OCR)
  invoice-photo-1.jpg / invoice-photo-2.jpg   the two pages as photos
  licence-text.pdf      a one-page trade licence
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
from pathlib import Path

PAGE1 = ["INVOICE", "Brightwater Components (synthetic)", "Invoice No: BW-2026-117", "Invoice date: 02 Oct 2026",
         "Due date: 30 Oct 2026", "Bill to: Riverside Hotel (sample)", "Email: accounts@riverside.example"]
PAGE2 = ["Description   Qty   Unit price   Amount", "Cotton towels  120  250.00  30,000.00", "Bath robes  40  600.00  24,000.00",
         "Subtotal BDT 54,000.00", "VAT (15%) 8,100.00", "Amount due BDT 62,100.00", "Payment terms: net 28"]
LICENCE = ["TRADE LICENCE", "Licence No: TRAD/DNCC/2026/88412", "Name of business: Brightwater Components (synthetic)",
           "Nature of business: Textile trading", "Proprietor: Sample Owner", "Business address: Mirpur 10, Dhaka",
           "Country: Bangladesh", "Date of issue: 01 Jul 2026", "Valid until: 30 Jun 2027"]


def text_pdf(*pages: list[str]) -> bytes:
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for i, lines in enumerate(pages):
        kids.append(f"{4 + 2 * i} 0 R")
        body = " T* ".join("(" + ln.replace("(", "\\(").replace(")", "\\)") + ") Tj" for ln in lines)
        stream = f"BT /F1 14 Tf 20 TL 60 740 Td {body} ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
    return _write(objs)


def image_pdf(jpegs: list[tuple[bytes, int, int, int]]) -> bytes:
    """A PDF whose pages are only JPEG images (like a scanner makes): no text layer."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b""]
    kids = []
    for data, w, h, comps in jpegs:
        space = "/DeviceGray" if comps == 1 else "/DeviceRGB"
        page, img, content = len(objs) + 1, len(objs) + 2, len(objs) + 3
        kids.append(f"{page} 0 R")
        draw = b"q 612 0 0 792 0 0 cm /Im0 Do Q"
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /XObject << /Im0 {img} 0 R >> >> /Contents {content} 0 R >>".encode())
        objs.append(f"<< /Type /XObject /Subtype /Image /Width {w} /Height {h} /ColorSpace {space} /BitsPerComponent 8 /Filter /DCTDecode /Length {len(data)} >>\nstream\n".encode() + data + b"\nendstream")
        objs.append(b"<< /Length " + str(len(draw)).encode() + b" >>\nstream\n" + draw + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(jpegs)} >>".encode()
    return _write(objs)


def _write(objs: list[bytes]) -> bytes:
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{n} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return out.getvalue()


def _jpeg_size(b: bytes) -> tuple[int, int, int]:
    """Width, height and colour components from the JPEG frame header."""
    import struct

    i = 2
    while i < len(b):
        marker, seg = b[i + 1], struct.unpack(">H", b[i + 2:i + 4])[0]
        if marker in (0xC0, 0xC2):
            h, w = struct.unpack(">HH", b[i + 5:i + 9])
            return w, h, b[i + 9]
        i += 2 + seg
    raise ValueError("no JPEG size")


def main(out_dir: str) -> None:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    text = text_pdf(PAGE1, PAGE2)
    (out / "invoice-text.pdf").write_bytes(text)
    (out / "licence-text.pdf").write_bytes(text_pdf(LICENCE))
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(["pdftoppm", "-jpeg", "-gray", "-r", "150", "-", str(Path(tmp) / "pg")], input=text, check=True)  # noqa: S603, S607
        pages = sorted(Path(tmp).glob("pg*.jpg"))
        jpegs = [p.read_bytes() for p in pages]
    for n, data in enumerate(jpegs, 1):
        (out / f"invoice-photo-{n}.jpg").write_bytes(data)
    (out / "invoice-scanned.pdf").write_bytes(image_pdf([(d, *_jpeg_size(d)) for d in jpegs]))
    print("wrote", sorted(p.name for p in out.iterdir()))


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "samples/documents")
