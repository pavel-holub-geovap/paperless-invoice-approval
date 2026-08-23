"""Generate an image-only synthetic invoice fixture for Paperless OCR tests."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "fixtures" / "synthetic" / "synthetic-invoice-cs-en.pdf"
TMP_DIR = ROOT / "tmp" / "pdfs"


def font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = ["arialbd.ttf" if bold else "arial.ttf", "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"]
    candidates = [Path("C:/Windows/Fonts") / name for name in names]
    candidates += [Path("/usr/share/fonts/truetype/dejavu") / name for name in names]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size)
    raise RuntimeError("Arial or DejaVu Sans font is required")


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    png_path = TMP_DIR / "synthetic-invoice-cs-en.png"

    page = Image.new("RGB", (1240, 1754), "white")
    draw = ImageDraw.Draw(page)
    navy = "#17324d"
    blue = "#1f6f9f"
    muted = "#52606d"
    line = "#d6dde3"

    draw.rectangle((0, 0, 1240, 190), fill=navy)
    draw.text((70, 50), "SYNTHETICKÁ TESTOVACÍ FAKTURA", fill="white", font=font(34, bold=True))
    draw.text((72, 111), "SYNTHETIC TEST INVOICE", fill="#d9edf7", font=font(25))
    draw.rounded_rectangle((865, 42, 1165, 143), radius=12, fill="#b3261e")
    draw.text((908, 64), "NENÍ ÚČETNÍ", fill="white", font=font(24, bold=True))
    draw.text((935, 101), "DOKLAD", fill="white", font=font(24, bold=True))

    draw.text((70, 245), "Dodavatel / Supplier", fill=blue, font=font(24, bold=True))
    draw.text((70, 290), "TESTOVACÍ DODAVATEL s.r.o.", fill=navy, font=font(30, bold=True))
    supplier_lines = [
        "Fiktivní 123, 100 00 Praha",
        "IČO: 00000019     DIČ: CZ00000019",
        "Tato entita i všechny údaje jsou pouze syntetické.",
    ]
    for index, text in enumerate(supplier_lines):
        draw.text((70, 345 + index * 38), text, fill=muted, font=font(22))

    draw.text((690, 245), "Odběratel / Customer", fill=blue, font=font(24, bold=True))
    customer_lines = [
        "TESTOVACÍ ODBĚRATEL",
        "Demonstrační 456, 602 00 Brno",
        "IČO: 00000027",
    ]
    for index, text in enumerate(customer_lines):
        draw.text((690, 290 + index * 42), text, fill=navy if index == 0 else muted, font=font(25 if index == 0 else 22, bold=index == 0))

    draw.line((70, 505, 1170, 505), fill=line, width=3)
    fields = [
        ("Číslo faktury", "TEST-2026-0001"),
        ("Datum vystavení", "20. 08. 2026"),
        ("Datum zdanitelného plnění", "20. 08. 2026"),
        ("Datum splatnosti", "03. 09. 2026"),
        ("Variabilní symbol", "20260001"),
        ("Měna", "CZK"),
    ]
    for index, (label, value) in enumerate(fields):
        col = index % 2
        row = index // 2
        x = 70 + col * 620
        y = 550 + row * 95
        draw.text((x, y), label, fill=muted, font=font(19))
        draw.text((x, y + 34), value, fill=navy, font=font(25, bold=True))

    table_top = 865
    draw.rectangle((70, table_top, 1170, table_top + 58), fill=blue)
    headers = [("Popis / Description", 90), ("Množství", 690), ("Cena bez DPH", 845), ("DPH", 1050)]
    for label, x in headers:
        draw.text((x, table_top + 15), label, fill="white", font=font(18, bold=True))
    draw.rectangle((70, table_top + 58, 1170, table_top + 170), outline=line, width=2)
    draw.text((90, table_top + 90), "Testovací softwarové služby", fill=navy, font=font(22))
    draw.text((720, table_top + 90), "1", fill=navy, font=font(22))
    draw.text((875, table_top + 90), "1 000,00 Kč", fill=navy, font=font(22))
    draw.text((1070, table_top + 90), "21 %", fill=navy, font=font(22))

    totals = [
        ("Základ DPH / Net", "1 000,00 Kč"),
        ("DPH / VAT 21 %", "210,00 Kč"),
        ("CELKEM / TOTAL", "1 210,00 Kč"),
    ]
    for index, (label, value) in enumerate(totals):
        y = 1100 + index * 65
        bold = index == 2
        draw.text((690, y), label, fill=navy, font=font(23 if bold else 21, bold=bold))
        draw.text((1150, y), value, fill=navy, font=font(25 if bold else 22, bold=bold), anchor="ra")
    draw.line((690, 1220, 1170, 1220), fill=blue, width=4)

    draw.text((70, 1355), "Platební údaje / Payment details", fill=blue, font=font(23, bold=True))
    draw.text((70, 1402), "Účet: 0000000000/0000 (syntetický)", fill=navy, font=font(22))
    draw.text((70, 1444), "Poznámka: Dokument je určen výhradně pro automatizovaný OCR a workflow test.", fill=muted, font=font(19))

    draw.line((70, 1570, 1170, 1570), fill=line, width=2)
    draw.text((70, 1605), "GENEROVANÁ FIXTURE - ŽÁDNÉ SKUTEČNÉ OSOBNÍ ANI FIREMNÍ ÚDAJE", fill="#b3261e", font=font(19, bold=True))
    draw.text((70, 1645), "Generated fixture - no real personal or company data", fill=muted, font=font(18))

    page.save(png_path, format="PNG", optimize=True)
    width, height = A4
    canvas = Canvas(str(OUTPUT), pagesize=A4)
    canvas.drawImage(ImageReader(page), 0, 0, width=width, height=height)
    canvas.showPage()
    canvas.save()
    print(OUTPUT)


if __name__ == "__main__":
    main()
