"""Document extraction into the right fields: text PDFs (several pages), scanned PDFs (pages rendered and read
with OCR, both replaced by fakes), document type, items / subtotal / tax / total with a consistency check,
source references, duplicate imports, clear errors, and nothing ever becoming cash. Synthetic documents only."""

from __future__ import annotations

import io

from pypdf import PdfReader, PdfWriter

from app.documents import intake
from tests.conftest import Api
from tests.test_agent import _clean_business, _user
from tests.test_documents import counts, make_png, up
from tests.test_pdf_evidence import make_pdf


def pdf_pages(*pages: list[str]) -> bytes:
    """A text PDF with several lines on each page (synthetic)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>", b"", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for i, lines in enumerate(pages):
        kids.append(f"{4 + 2 * i} 0 R")
        body = " T* ".join("(" + ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") + ") Tj" for ln in lines)
        stream = f"BT /F1 11 Tf 14 TL 50 750 Td {body} ET".encode()
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {5 + 2 * i} 0 R >>".encode())
        objs.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(pages)} >>".encode()
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


PAGE1 = ["INVOICE", "Invoice No: DT-0042", "Invoice date: 05 Oct 2026", "Due date: 20 Oct 2026", "Bill to: Hotel Rosa Ltd", "Email: accounts@hotelrosa.example"]
PAGE2 = ["Description   Qty   Unit price   Amount", "Cotton towels  120  25.00  3,000.00", "Bath robes  40  60.00  2,400.00",
         "Subtotal USD 5,400.00", "VAT (15%) 810.00", "Amount due USD 6,210.00"]


def _read(api: Api, name: str, data: bytes, purpose: str = "customer") -> dict:
    r = up(api, name, data, purpose)
    assert r.status_code == 200, r.text
    s = api.post(f"/api/documents/batches/{r.json()['batch_id']}/suggest")
    assert s.status_code == 200, s.text
    return {**s.json(), "upload": r.json()}


def test_multipage_text_invoice_maps_every_field_with_page_references():
    api = _user()
    _clean_business(api)
    before = counts(api)
    s = _read(api, "dt-0042.pdf", pdf_pages(PAGE1, PAGE2))
    f = s["fields"]
    assert s["doc_type"] == "invoice" and s["doc_type_label"] == "Invoice"
    assert f["customer_name"]["value"] == "Hotel Rosa Ltd" and f["invoice_number"]["value"] == "DT-0042"
    assert f["issue_date"]["value"] == "2026-10-05" and f["due_date"]["value"] == "2026-10-20"
    assert f["amount"]["value"] == "6210" and f["currency"]["value"] == "USD"
    assert f["subtotal"]["value"] == "5400" and f["tax"]["value"] == "810" and "For checking only" in f["tax"]["note"]
    assert f["items"]["value"].startswith("Cotton towels × 120 @ 25.00 = 3,000.00; Bath robes × 40")
    assert f["description"]["value"].startswith("Cotton towels") and "item lines" in f["description"]["note"]
    # Every value says where it came from: file, page and the line.
    assert f["invoice_number"]["ref"] == "dt-0042.pdf, page 1: “Invoice No: DT-0042”"
    assert f["amount"]["ref"] == "dt-0042.pdf, page 2: “Amount due USD 6,210.00”"
    assert s["missing_for_invoice"] == [] and s["nothing_saved"] is True
    assert counts(api) == before  # reading saves nothing


def test_totals_that_dont_add_up_are_flagged_not_guessed():
    api = _user()
    _clean_business(api)
    page = PAGE2[:-1] + ["Amount due USD 9,999.00"]
    f = _read(api, "bad-total.pdf", pdf_pages(PAGE1, page))["fields"]
    assert f["amount"]["value"] == "9999" and f["amount"]["confidence"] == "low"
    assert "Subtotal + tax = 6210" in f["amount"]["note"]


def test_scanned_pdf_pages_are_rendered_and_read_with_ocr(monkeypatch):
    api = _user()
    _clean_business(api)
    rendered: list[bytes] = []

    def fake_raster(data: bytes, max_pages: int = intake.MAX_OCR_PAGES) -> list[bytes]:
        rendered.append(data)
        return [b"page-1-image", b"page-2-image"]

    pages = {b"page-1-image": "\n".join(PAGE1), b"page-2-image": "\n".join(PAGE2)}
    monkeypatch.setattr(intake, "pdf_rasterizer", fake_raster)
    monkeypatch.setattr(intake, "ocr_runner", lambda img: pages[img])
    s = _read(api, "scan.pdf", make_pdf(""))  # a PDF with no text layer
    doc = s["documents"][0]
    assert doc["method"] == "pdf_ocr" and len(rendered) == 1
    assert s["fields"]["invoice_number"]["value"] == "DT-0042" and s["fields"]["amount"]["value"] == "6210"
    assert s["fields"]["amount"]["ref"] == "scan.pdf, page 2: “Amount due USD 6,210.00”"
    # Persisted: reopening the document after a refresh gives the same text and suggestion.
    again = api.get(f"/api/documents/{doc['id']}").json()
    assert "[Page 2]" in again["text"] and again["suggestion"]["fields"]["invoice_number"]["value"] == "DT-0042"
    assert again["suggestion"]["doc_type"] == "invoice"


def test_unreadable_scan_and_several_photos(monkeypatch):
    api = _user()
    _clean_business(api)
    monkeypatch.setattr(intake, "pdf_rasterizer", lambda data, max_pages=10: [b"blurry"])
    monkeypatch.setattr(intake, "ocr_runner", lambda img: "~ ~" if img == b"blurry" else "")
    r = up(api, "blurry.pdf", make_pdf(""))
    assert r.status_code == 200 and "Very little text" in r.json()["note"]
    # Two photos of one invoice are read together; values found on either photo are used.
    photos = {"p1": "\n".join(PAGE1), "p2": "\n".join(PAGE2)}
    a, b = make_png(41, 20), make_png(42, 20)
    monkeypatch.setattr(intake, "ocr_runner", lambda img: photos["p1"] if img == a else photos["p2"])
    r1 = up(api, "photo-1.png", a)
    r2 = up(api, "photo-2.png", b, batch=r1.json()["batch_id"])
    assert r2.status_code == 200
    s = api.post(f"/api/documents/batches/{r1.json()['batch_id']}/suggest").json()
    assert s["fields"]["customer_name"]["value"] == "Hotel Rosa Ltd" and s["fields"]["amount"]["value"] == "6210"
    assert s["fields"]["amount"]["ref"].startswith("photo-2.png")


def test_corrupt_password_protected_and_unsupported_files_get_clear_errors():
    api = _user()
    _clean_business(api)
    w = PdfWriter(clone_from=PdfReader(io.BytesIO(make_pdf("secret"))))
    w.encrypt(user_password="pw", algorithm="RC4-128")
    buf = io.BytesIO()
    w.write(buf)
    r = up(api, "locked.pdf", buf.getvalue())
    assert r.status_code == 422 and r.json()["error"]["code"] == "DOC_ENCRYPTED" and "password" in r.json()["error"]["message"]
    r = up(api, "broken.pdf", b"%PDF-1.4\n" + b"\x00garbage" * 50)
    assert r.status_code == 422 and r.json()["error"]["code"] in ("DOC_MALFORMED", "PDF_MALFORMED")
    r = up(api, "notes.txt", b"just text")
    assert r.status_code == 415 and r.json()["error"]["code"] == "DOC_TYPE"


def test_document_type_mismatch_and_receipts_are_explained():
    api = _user()
    _clean_business(api)
    lic = _read(api, "licence.pdf", pdf_pages(["TRADE LICENCE", "Licence No: TL-778", "Name of business: Corner Bakery", "Proprietor: A. Rahman", "Valid until: 30 Jun 2027"]))
    assert lic["doc_type"] == "business_licence" and any("business licence, not a customer document" in f["text"] for f in lic["flags"])
    rec = _read(api, "receipt.pdf", pdf_pages(["RECEIPT", "Received from: Cafe Lune", "Amount paid USD 120.00", "Thank you for your payment"]))
    assert rec["doc_type"] == "receipt" and any("never records a payment or changes your cash" in f["text"] for f in rec["flags"])


def test_saved_invoice_is_money_owed_and_reimporting_the_file_is_blocked():
    api = _user()
    _clean_business(api)
    data = pdf_pages(PAGE1, PAGE2)
    s = _read(api, "dt-0042.pdf", data)
    cash0 = api.get("/api/dashboard").json()["position"]["settled_minor"]
    # The user confirms: the same calls the review form makes.
    cp = api.post("/api/parties", {"kind": "customer", "name": s["fields"]["customer_name"]["value"], "email": s["fields"]["customer_email"]["value"], "accepts_partial": True}).json()
    inv = api.post("/api/invoices", {"customer_id": cp["id"], "number": "DT-0042", "description": s["fields"]["description"]["value"][:300], "amount": "6210", "currency": "USD",
                                     "issued_on": "2026-10-05", "due_on": "2026-10-20"})
    assert inv.status_code == 200, inv.text
    assert api.post("/api/documents/link", {"document_ids": [d["id"] for d in s["documents"]], "record_type": "invoice", "record_id": inv.json()["id"]}).status_code == 200
    # An invoice is money owed, never settled cash.
    d = api.get("/api/dashboard").json()
    assert d["position"]["settled_minor"] == cash0 and d["receivables"]["outstanding_minor"] == 621_000
    # The same file again: flagged as already imported, and a second save of that invoice is refused.
    again = _read(api, "dt-0042 (copy).pdf", data)
    assert any(f.get("code") == "ALREADY_IMPORTED" and f["tone"] == "error" for f in again["flags"])
    assert any("Invoice DT-0042 already exists" in f["text"] for f in again["flags"])
    dup = api.post("/api/invoices", {"customer_id": cp["id"], "number": "DT-0042", "amount": "6210", "currency": "USD", "issued_on": "2026-10-05", "due_on": "2026-10-20"})
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "INVOICE_DUPLICATE"
    # The existing customer is reused, never duplicated or overwritten.
    cp2 = api.post("/api/parties", {"kind": "customer", "name": "hotel rosa ltd", "email": "other@example.test", "accepts_partial": True}).json()
    assert cp2["id"] == cp["id"]
    assert len([c for c in api.get("/api/customers").json()["customers"] if c["name"].lower() == "hotel rosa ltd"]) == 1


def test_a_huge_page_size_cannot_make_a_huge_image():
    """Security review: a PDF claiming a 200,000-point page must not render a gigantic bitmap."""
    import shutil
    import time

    import pytest

    if shutil.which("pdftoppm") is None:
        pytest.skip("Poppler isn't installed here (it is in the API image)")
    pdf = (b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
           b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200000 200000] >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n")
    started = time.monotonic()
    images = intake._rasterize(pdf)
    assert time.monotonic() - started < 10
    assert all(len(img) <= intake.MAX_PAGE_IMAGE_BYTES for img in images)
    for img in images:  # PNG header: width and height are capped at RASTER_MAX_SIDE_PX
        import struct

        w, h = struct.unpack(">II", img[16:24])
        assert max(w, h) <= intake.RASTER_MAX_SIDE_PX


def test_slow_text_layer_falls_back_to_text_recognition(monkeypatch):
    """Live, 19 Oct: a 15-page image-only PDF hit the 10 s text step on the free host and the upload failed.
    A text step that runs out of time is now treated like a scan: its first pages are read by OCR."""
    from app.documents import pdf as pdf_intake
    from app.errors import ApiError

    def slow(data, timeout_s=0, page_marks=False):
        raise ApiError(422, "PDF_TIMEOUT", "This PDF took too long to read.")

    monkeypatch.setattr(pdf_intake, "extract_text", slow)
    monkeypatch.setattr(intake, "pdf_rasterizer", lambda data, max_pages=10: [b"p1", b"p2"])
    monkeypatch.setattr(intake, "ocr_runner", lambda img: "TRADE LICENCE No. 12345 Rahman Textiles" if img == b"p1" else "Dhaka")
    out = intake.extract("pdf", make_pdf("x"))
    assert out.method == "pdf_ocr" and "TRADE LICENCE" in out.text and out.pages == 2
    assert pdf_intake.EXTRACT_TIMEOUT_S >= 20


def test_timeout_message_is_plain():
    from app.documents import pdf as pdf_intake
    from app.errors import ApiError

    try:
        pdf_intake.extract_text(make_pdf("slow"), timeout_s=0.001)
    except ApiError as exc:
        assert exc.code == "PDF_TIMEOUT" and "seconds" not in exc.message and "photo" in exc.message


def test_licence_heading_followed_by_issuer_is_not_a_licence_number():
    """19 Oct sample licence: "TRADE LICENCE" then "Northbridge City Corporation" was read as licence no. "rthbridge…"."""
    from app.documents import fields

    text = (
        "TRADE LICENCE\nNorthbridge City Corporation (fictional)\nLicensing Department · Form TL-1\n"
        "Trade Licence No: TRAD/NBCC/2026/004521\nName of Business: Rahman Textiles Ltd.\n"
        "Nature of Business: Textile wholesale and trading\nProprietor: Ayesha Rahman\n"
        "Business Address: House 12, Road 7, Mirpur 10, Dhaka\nCountry: Bangladesh\n"
        "Date of Issue: 01 Jul 2026\nValid Until: 30 Jun 2027\nReg. No.: 77-123\n"
    )
    got = {k: v[0] for k, v in fields.rules("business", text).items()}
    assert got["registration_number"] == "TRAD/NBCC/2026/004521"
    assert got["name"] == "Rahman Textiles Ltd." and got["owner_name"] == "Ayesha Rahman"
    assert got["issue_date"] == "2026-07-01" and got["expiry_date"] == "2027-06-30"
    # A label ending in a dot still works.
    assert fields.rules("business", "Reg. No.: 77-123\n")["registration_number"][0] == "77-123"
