"""Makes a realistic-looking but clearly fictional trade licence for testing FlowSense's document reader.

  samples/documents/licence-sample.pdf          text PDF (normal text extraction)
  samples/documents/licence-sample-scanned.pdf  the same page as an image only (exercises OCR)

Every value is synthetic, the issuer does not exist, and each page carries a SPECIMEN watermark.
Needs: reportlab, Pillow.  Run: python scripts/make_sample_licence.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent.parent / "samples" / "documents"

ISSUER = "Northbridge City Corporation (fictional)"
FIELDS = [
    ("Trade Licence No", "TRAD/NBCC/2026/004521"),
    ("Name of Business", "Rahman Textiles Ltd."),
    ("Nature of Business", "Textile wholesale and trading"),
    ("Proprietor", "Ayesha Rahman"),
    ("Business Address", "House 12, Road 7, Mirpur 10, Dhaka"),
    ("Country", "Bangladesh"),
    ("Date of Issue", "01 Jul 2026"),
    ("Valid Until", "30 Jun 2027"),
    ("Licence Fee Paid", "BDT 5,000.00"),
]


def text_pdf(path: Path) -> None:
    w, h = A4
    c = canvas.Canvas(str(path), pagesize=A4)
    c.setTitle("Trade Licence (specimen)")
    # Border and header band.
    c.setStrokeColor(colors.HexColor("#0F766E"))
    c.setLineWidth(2)
    c.rect(36, 36, w - 72, h - 72)
    c.setFillColor(colors.HexColor("#0F766E"))
    c.rect(36, h - 140, w - 72, 104, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 22)
    c.drawCentredString(w / 2, h - 82, "TRADE LICENCE")
    c.setFont("Helvetica", 11)
    c.drawCentredString(w / 2, h - 102, ISSUER)
    c.drawCentredString(w / 2, h - 118, "Licensing Department · Form TL-1")
    # Fields: one "Label: value" line each, so a reader sees the label and the value together.
    c.setFillColor(colors.black)
    y = h - 190
    for label, value in FIELDS:
        c.setFont("Helvetica-Bold", 12)
        c.drawString(72, y, f"{label}:")
        c.setFont("Helvetica", 12)
        c.drawString(230, y, value)
        c.setStrokeColor(colors.HexColor("#DDDDDD"))
        c.setLineWidth(0.5)
        c.line(72, y - 8, w - 72, y - 8)
        y -= 34
    c.setFont("Helvetica-Oblique", 10)
    c.setFillColor(colors.HexColor("#555555"))
    c.drawString(72, y - 10, "This licence must be displayed at the business premises and renewed before it expires.")
    # Seal and signature.
    c.setStrokeColor(colors.HexColor("#0F766E"))
    c.setLineWidth(1.5)
    c.circle(w - 150, 150, 46)
    c.setFont("Helvetica-Bold", 8)
    c.setFillColor(colors.HexColor("#0F766E"))
    c.drawCentredString(w - 150, 154, "LICENSING")
    c.drawCentredString(w - 150, 142, "OFFICE")
    c.setStrokeColor(colors.black)
    c.line(72, 120, 260, 120)
    c.setFont("Helvetica", 10)
    c.setFillColor(colors.black)
    c.drawString(72, 106, "Licensing Officer (specimen signature)")
    # Watermark.
    c.saveState()
    c.translate(w / 2, h / 2)
    c.rotate(35)
    c.setFillColor(colors.Color(0.85, 0.1, 0.1, alpha=0.18))
    c.setFont("Helvetica-Bold", 54)
    c.drawCentredString(0, 0, "SPECIMEN - NOT A REAL DOCUMENT")
    c.restoreState()
    c.setFont("Helvetica", 8)
    c.setFillColor(colors.HexColor("#888888"))
    c.drawCentredString(w / 2, 46, "Synthetic test document for FlowSense. The issuer, business and people are fictional.")
    c.save()


def scanned_pdf(path: Path) -> None:
    """The same licence drawn as a 200 dpi greyscale image, saved as an image-only PDF (no text layer)."""
    W, H = 1654, 2339  # A4 at 200 dpi
    img = Image.new("L", (W, H), 250)
    d = ImageDraw.Draw(img)

    def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
        for name in (("arialbd.ttf" if bold else "arial.ttf"), ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf")):
            try:
                return ImageFont.truetype(name, size)
            except OSError:
                continue
        return ImageFont.load_default()

    d.rectangle([60, 60, W - 60, H - 60], outline=60, width=5)
    d.rectangle([60, 60, W - 60, 330], fill=70)
    d.text((W / 2, 150), "TRADE LICENCE", fill=255, font=font(64, True), anchor="mm")
    d.text((W / 2, 230), ISSUER, fill=235, font=font(32), anchor="mm")
    d.text((W / 2, 280), "Licensing Department - Form TL-1", fill=235, font=font(28), anchor="mm")
    y = 450
    for label, value in FIELDS:
        d.text((170, y), f"{label}:", fill=20, font=font(36, True))
        d.text((640, y), value, fill=20, font=font(36))
        d.line([170, y + 58, W - 170, y + 58], fill=200, width=2)
        y += 100
    d.text((170, y + 20), "This licence must be displayed at the business premises and renewed before it expires.", fill=80, font=font(27))
    d.ellipse([W - 420, H - 480, W - 160, H - 220], outline=60, width=5)
    d.text((W - 290, H - 360), "LICENSING", fill=60, font=font(28, True), anchor="mm")
    d.text((W - 290, H - 320), "OFFICE", fill=60, font=font(28, True), anchor="mm")
    d.line([170, H - 300, 700, H - 300], fill=30, width=3)
    d.text((170, H - 280), "Licensing Officer (specimen signature)", fill=30, font=font(28))
    d.text((W / 2, H - 110), "Synthetic test document for FlowSense. The issuer, business and people are fictional.", fill=120, font=font(24), anchor="mm")
    # Watermark on its own layer, rotated, light enough for OCR to still read the fields.
    wm = Image.new("L", (W, H), 0)
    ImageDraw.Draw(wm).text((W / 2, H / 2), "SPECIMEN - NOT A REAL DOCUMENT", fill=255, font=font(120, True), anchor="mm")
    wm = wm.rotate(35, center=(W / 2, H / 2))
    img.paste(Image.new("L", (W, H), 200), (0, 0), wm.point(lambda v: int(v * 0.35)))
    img.save(path, "PDF", resolution=200.0)


if __name__ == "__main__":
    OUT.mkdir(parents=True, exist_ok=True)
    text_pdf(OUT / "licence-sample.pdf")
    scanned_pdf(OUT / "licence-sample-scanned.pdf")
    print("wrote", OUT / "licence-sample.pdf", "and", OUT / "licence-sample-scanned.pdf")
