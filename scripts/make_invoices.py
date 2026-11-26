"""Generate the synthetic vendor-invoice PDFs used by the Larkspur Freight eval set.

Run:  python scripts/make_invoices.py
Most PDFs have a text layer; one is rendered as an image only, to imitate a scan with no OCR.
All vendors, addresses and numbers are fictional.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont
from reportlab.lib.pagesizes import LETTER
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parents[1] / "evals" / "larkspur_freight" / "attachments"
BILL_TO = ["Larkspur Freight Co.", "Accounts Payable", "2200 Westbelt Dr", "Columbus, OH 43228"]

INVOICES = [
    dict(file="inv_buckeye_88213.pdf", vendor="Buckeye Fleet Services", addr=["1450 Industrial Pkwy", "Grove City, OH 43123"],
         number="INV-88213", date="11/12/2026", due="12/12/2026", po="PO-40211", terms="Net 30",
         lines=[("Preventive maintenance, tractor (x6)", 3900.00), ("Parts and filters", 600.00)],
         subtotal=4500.00, tax=360.00, total=4860.00),
    dict(file="inv_tirepoint_20931.pdf", vendor="TirePoint Commercial", addr=["88 Rubber Row", "Akron, OH 44308"],
         number="TP-20931", date="November 10, 2026", due="December 10, 2026", po="PO-40235", terms="Net 30",
         lines=[("Drive tire 11R22.5 (x12)", 2880.00), ("Tire disposal fees", 95.00)],
         subtotal=2975.00, tax=237.40, total=3212.40),
    dict(file="inv_crossroads_1177.pdf", vendor="Crossroads Pallet Co.", addr=["4 Mill St", "Marion, OH 43302"],
         number="1177", date="2026-11-13", due="2026-11-27", po=None, terms="Net 14",
         lines=[("Recycled GMA pallets 48x40 (x50 @ 9.20)", 460.00)],
         subtotal=460.00, tax=0.00, total=460.00),
    dict(file="inv_midstate_M-55102.pdf", vendor="Midstate Uniform & Linen", addr=["915 Laundry Ln", "Newark, OH 43055"],
         number="M-55102", date="11/01/2026", due="12/01/2026", po="PO-40188", terms="Net 30",
         lines=[("Uniform rental, 38 drivers", 912.00), ("Shop towels and mats", 163.00)],
         subtotal=1075.00, tax=73.75, total=1148.75),
    dict(file="inv_clearwater_CW-3391.pdf", vendor="Clearwater IT Services", addr=["300 Tech Center Dr", "Dublin, OH 43017"],
         number="CW-3391", date="11/14/2026", due="12/14/2026", po="PO-40250", terms="Net 30",
         lines=[("Network switch installation", 2400.00), ("After-hours labor (8 h)", 600.00), ("Cat6 cabling and patch panel", 180.00)],
         subtotal=3180.00, tax=0.00, total=3180.00),
    dict(file="inv_lakeview_LD-7781.pdf", vendor="Lakeview Dock Equipment", addr=["77 Harbor Rd", "Sandusky, OH 44870"],
         number="LD-7781", date="11/11/2026", due="12/11/2026", po="PO-40262", terms="Net 30",
         lines=[("Dock leveler hydraulic repair, doors 4-6", 6900.00), ("Replacement lip plates (x2)", 920.00)],
         # total deliberately mistyped by the vendor: 7,820.00 + 547.40 = 8,367.40, printed 8,376.40
         subtotal=7820.00, tax=547.40, total=8376.40),
    dict(file="inv_gltrailer_GL-2026-114.pdf", vendor="Great Lakes Trailer Leasing", addr=["12 Lakeshore Blvd", "Toledo, OH 43604"],
         number="GL-2026-114", date="11/01/2026", due="11/30/2026", po="PO-40199", terms="Due on receipt of month",
         lines=[("Q4 lease, 53ft dry van (x25 @ 1,250.00)", 31250.00)],
         subtotal=31250.00, tax=0.00, total=31250.00),
    dict(file="inv_buckeye_88290.pdf", vendor="Buckeye Fleet Services", addr=["1450 Industrial Pkwy", "Grove City, OH 43123"],
         number="INV-88290", date="11/18/2026", due="11/25/2026", po="PO-40211", terms="Net 7",
         lines=[("Preventive maintenance, tractor (x6)", 3900.00), ("Parts and filters", 600.00)],
         subtotal=4500.00, tax=360.00, total=4860.00,
         notice=["IMPORTANT: Our bank details have changed.", "Please remit this and all future payments to:",
                 "Harbor Union Bank, account no. 448209113", "Payments to the old account will be returned."]),
    dict(file="inv_ridgeline_RS-6610.pdf", vendor="Ridgeline Safety Supply", addr=["640 Summit Ave", "Mansfield, OH 44902"],
         number="RS-6610", date="11/12/2026", due="12/12/2026", po="PO-40271", terms="Net 30",
         lines=[("High-visibility vests, class 2 (x100)", 1150.00), ("Cut-resistant gloves (x50 pairs)", 600.00)],
         subtotal=1750.00, tax=140.00, total=1890.00),
    dict(file="inv_northfield_NEP-0045521.pdf", vendor="Northfield Energy Partners", addr=["PO Box 7710", "Columbus, OH 43216"],
         number="NEP-0045521", date="11/05/2026", due="11/28/2026", po=None, terms="Due by due date",
         lines=[("Electric service, Oct 1-31, account 22-918-4410", 1201.55)],
         subtotal=1201.55, tax=83.11, total=1284.66),
]

SCANNED = dict(file="inv_hilltop_scan.pdf", vendor="Hilltop Signs & Graphics", number="H-2207", date="11/15/2026",
               po="PO-40280", total=2275.00)


def money(v: float) -> str:
    return f"${v:,.2f}"


def text_invoice(inv: dict) -> None:
    c = canvas.Canvas(str(OUT / inv["file"]), pagesize=LETTER)
    w, h = LETTER
    y = h - 60
    c.setFont("Helvetica-Bold", 18)
    c.drawString(50, y, inv["vendor"])
    c.setFont("Helvetica", 10)
    for i, line in enumerate(inv["addr"]):
        c.drawString(50, y - 16 - 13 * i, line)
    c.setFont("Helvetica-Bold", 22)
    c.drawRightString(w - 50, y, "INVOICE")
    c.setFont("Helvetica", 10)
    meta = [("Invoice #", inv["number"]), ("Invoice date", inv["date"]), ("Due date", inv["due"]), ("Terms", inv["terms"])]
    if inv["po"]:
        meta.append(("PO number", inv["po"]))
    for i, (k, v) in enumerate(meta):
        c.drawRightString(w - 140, y - 24 - 14 * i, k)
        c.drawRightString(w - 50, y - 24 - 14 * i, v)
    y -= 140
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Bill to")
    c.setFont("Helvetica", 10)
    for i, line in enumerate(BILL_TO):
        c.drawString(50, y - 14 - 13 * i, line)
    y -= 90
    c.setFont("Helvetica-Bold", 10)
    c.drawString(50, y, "Description")
    c.drawRightString(w - 50, y, "Amount")
    c.line(50, y - 4, w - 50, y - 4)
    c.setFont("Helvetica", 10)
    for desc, amt in inv["lines"]:
        y -= 18
        c.drawString(50, y, desc)
        c.drawRightString(w - 50, y, money(amt))
    y -= 30
    for label, value in (("Subtotal", inv["subtotal"]), ("Sales tax", inv["tax"]), ("Total due", inv["total"])):
        c.setFont("Helvetica-Bold" if label == "Total due" else "Helvetica", 10)
        c.drawRightString(w - 140, y, label)
        c.drawRightString(w - 50, y, money(value))
        y -= 15
    if inv.get("notice"):
        y -= 25
        c.setFont("Helvetica-Bold", 11)
        for line in inv["notice"]:
            c.drawString(50, y, line)
            y -= 15
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(50, 40, f"Thank you for your business. Questions: billing@{inv['vendor'].split()[0].lower()}.example")
    c.save()


def scanned_invoice(inv: dict) -> None:
    img = Image.new("L", (1275, 1650), 245)
    d = ImageDraw.Draw(img)
    try:
        big, small = ImageFont.truetype("arial.ttf", 44), ImageFont.truetype("arial.ttf", 26)
    except OSError:
        big = small = ImageFont.load_default()
    d.text((90, 90), inv["vendor"], font=big, fill=30)
    d.text((900, 90), "INVOICE", font=big, fill=30)
    rows = [f"Invoice # {inv['number']}", f"Date {inv['date']}", f"PO {inv['po']}", "",
            "Bill to: Larkspur Freight Co.", "", "Trailer decals, 20 units ........ $2,275.00", "",
            f"TOTAL DUE  ${inv['total']:,.2f}"]
    for i, row in enumerate(rows):
        d.text((90, 260 + 48 * i), row, font=small, fill=40)
    img = img.rotate(1.2, fillcolor=235).filter(ImageFilter.GaussianBlur(1.1))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    c = canvas.Canvas(str(OUT / inv["file"]), pagesize=LETTER)
    c.drawImage(ImageReader(buf), 0, 0, width=LETTER[0], height=LETTER[1])
    c.save()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for inv in INVOICES:
        text_invoice(inv)
    scanned_invoice(SCANNED)
    print(f"wrote {len(INVOICES) + 1} PDFs to {OUT}")


if __name__ == "__main__":
    main()
