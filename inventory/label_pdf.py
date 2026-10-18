"""Label PDFs (reportlab): one page per label, the page the size of the label,
which is what thermal label printers (Zebra, TSC, Xprinter) expect from a PDF.

- **Item and bin labels** (small, 50 x 25 mm by default): a Code 128 of the
  code - or EAN-13 when the item carries a 13-digit product barcode - with the
  code written under it and the name above.
- **Batch and package labels** (large, 100 x 50 mm by default): a QR code
  holding ``code|item|batch|expiry|quantity|unit`` (a phone reads everything
  from it), the details in words, and a Code 128 of the label code at the
  bottom, so a plain 1-D scanner works too: the code alone finds the label.
- **GS1-128** (only when switched on, for an item with a 13/14-digit barcode):
  (01) GTIN, (17) expiry, (310n) net kg or (30) count, (10) batch.

Text is Helvetica (labels are read in English on the floor; the Bangla name
stays on screen). Bars are at least 0.25 mm wide so a 203 dpi printer draws
every bar with two dots.
"""

import io
from decimal import Decimal

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import code128
from reportlab.graphics.barcode.eanbc import Ean13BarcodeWidget
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

FNC1 = "\xf1"
GS = "\x1d"
MAX_BAR = 0.5 * mm


def size_of(name):
    """"100x50" -> (width, height) in points."""
    w, h = name.split("x")
    return int(w) * mm, int(h) * mm


def latin(text):
    """Helvetica draws Latin-1 only: anything else becomes '?'."""
    return str(text or "").encode("latin-1", "replace").decode("latin-1")


def fit(text, font, size, width):
    text = latin(text)
    if stringWidth(text, font, size) <= width:
        return text
    while text and stringWidth(text + "…", font, size) > width:
        text = text[:-1]
    return text + "…"


def quantity_text(value):
    return format(Decimal(value).normalize(), "f")


def ean13(value):
    """The 13 digits when ``value`` is a valid EAN-13, else None."""
    value = (value or "").strip()
    if len(value) != 13 or not value.isdigit():
        return None
    total = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(value[:12]))
    return value if (10 - total % 10) % 10 == int(value[12]) else None


def gtin14(value):
    value = (value or "").strip()
    if value.isdigit() and len(value) in (13, 14):
        return value.zfill(14)
    return None


def qr_payload(label):
    """What the QR holds: the label code first (all the lookup needs), then the
    details, so a phone with no connection still reads what it is."""
    parts = [label.code, label.item.code if label.item_id else ""]
    if label.batch_id:
        parts += [label.batch.batch_no, label.batch.expiry_date.strftime("%Y%m%d")
                  if label.batch.expiry_date else ""]
    else:
        parts += ["", ""]
    if label.quantity is not None:
        parts += [quantity_text(label.quantity), label.uom.code if label.uom_id else ""]
    return "|".join(parts).rstrip("|")


def gs1_data(label):
    """``(barcode data with FNC1, human-readable text)`` or None."""
    gtin = gtin14(label.item.barcode) if label.item_id else None
    if not gtin:
        return None
    data, text = FNC1 + "01" + gtin, f"(01){gtin}"
    if label.batch_id and label.batch.expiry_date:
        expiry = label.batch.expiry_date.strftime("%y%m%d")
        data += "17" + expiry
        text += f"(17){expiry}"
    if label.quantity is not None and label.uom_id:
        qty = Decimal(label.quantity) * (_factor(label))
        base = label.item.base_uom
        if base.code == "KG" and qty < Decimal("1000000"):
            decimals = min(3, max(0, -qty.normalize().as_tuple().exponent))
            digits = f"{int(qty * (10 ** decimals)):06d}"
            data += f"310{decimals}{digits}"
            text += f"(310{decimals}){digits}"
        elif base.kind == "count" and qty == qty.to_integral_value() and qty < 10 ** 8:
            count = str(int(qty))
            # Variable length: goes last, after the batch's FNC1 separator.
            label._gs1_count = count
    if label.batch_id:
        batch = latin(label.batch.batch_no)[:20]
        data += "10" + batch
        text += f"(10){batch}"
    count = getattr(label, "_gs1_count", None)
    if count:
        data += (FNC1 if label.batch_id else "") + "30" + count
        text += f"(30){count}"
    return data, text


def _factor(label):
    from inventory.masters import units_for

    return units_for(label.item).get(label.uom_id, Decimal("1"))


# --- drawing -----------------------------------------------------------------

def draw_code128(c, value, x, y, width, height):
    """A Code 128 centred in ``width`` with its quiet zones, bars at most 0.5 mm."""
    probe = code128.Code128(value, barWidth=1, barHeight=height, quiet=False, humanReadable=False)
    modules = probe.width
    bar = min(width / (modules + 20), MAX_BAR)
    barcode = code128.Code128(value, barWidth=bar, barHeight=height, quiet=False,
                              humanReadable=False)
    barcode.drawOn(c, x + (width - barcode.width) / 2, y)
    return bar


def draw_scaled(c, widget, x, y, width, height):
    left, bottom, right, top = widget.getBounds()
    drawing = Drawing(width, height, transform=[width / (right - left), 0, 0,
                                                height / (top - bottom), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, x, y)


def draw_qr(c, value, x, y, size):
    draw_scaled(c, QrCodeWidget(value, barLevel="M"), x, y, size, size)


def text(c, value, x, y, size, width, bold=False, centre=False):
    font = "Helvetica-Bold" if bold else "Helvetica"
    value = fit(value, font, size, width)
    c.setFont(font, size)
    if centre:
        c.drawCentredString(x + width / 2, y, value)
    else:
        c.drawString(x, y, value)


def small_label(c, label, width, height, company_name):
    m = 1.6 * mm
    inner = width - 2 * m
    if label.kind == "location":
        title = f"{label.warehouse.code} · {label.warehouse.name}"
        value, caption = label.code, f"Bin {label.location.code}" + (
            f" · {label.location.description}" if label.location.description else "")
    else:
        title = label.item.name
        value = label.code
        caption = (f"Unit {label.item.base_uom.code}" if label.code == label.item.code
                   else f"{label.item.code} · {label.item.base_uom.code}")
    # Points from the bottom: caption, then the code in words, then the bars;
    # the name across the top.
    title_y = height - m - 7
    text(c, title, m, title_y, 7, inner, bold=True)
    bar_y = m + 15
    bar_height = title_y - 3 - bar_y
    ean = ean13(label.code) if label.kind == "item" else None
    if ean:
        widget = Ean13BarcodeWidget(ean[:12], barHeight=bar_height, fontSize=7)
        draw_scaled(c, widget, m, m + 7, inner, title_y - 3 - (m + 7))
        text(c, caption, m, m, 5.5, inner, centre=True)
        return
    draw_code128(c, value, m, bar_y, inner, bar_height)
    text(c, value, m, m + 7, 8, inner, bold=True, centre=True)
    text(c, caption, m, m, 5.5, inner, centre=True)


def large_label(c, label, width, height, gs1, company_name):
    m = 2.5 * mm
    gs1_part = gs1_data(label) if gs1 else None
    bottom = m
    if gs1_part:
        data, readable = gs1_part
        draw_code128(c, data, m, bottom + 3.2 * mm, width - 2 * m, 9 * mm)
        text(c, readable, m, bottom, 6.5, width - 2 * m, centre=True)
        bottom += 13.5 * mm
    qr_size = min(height - bottom - m, width * 0.42)
    draw_qr(c, qr_payload(label), m - 1 * mm, bottom, qr_size)
    x = m + qr_size + 1 * mm
    column = width - x - m
    y = height - m - 9
    text(c, label.item.code, x, y, 10, column, bold=True)
    y -= 10
    name = latin(label.item.name)
    text(c, name, x, y, 8, column)
    y -= 11
    if label.batch_id:
        text(c, f"Batch {label.batch.batch_no}", x, y, 8.5, column, bold=True)
        y -= 10
        dates = []
        if label.batch.mfg_date:
            dates.append(f"Made {label.batch.mfg_date:%d %b %Y}")
        if label.batch.expiry_date:
            dates.append(f"Exp {label.batch.expiry_date:%d %b %Y}")
        if dates:
            text(c, "  ".join(dates), x, y, 7.5, column, bold=bool(label.batch.expiry_date))
            y -= 10
    if label.quantity is not None:
        text(c, f"Qty {quantity_text(label.quantity)} {label.uom.code}", x, y, 9, column, bold=True)
        y -= 10
    if label.warehouse_id:
        where = label.warehouse.code + (f" / {label.location.code}" if label.location_id else "")
        text(c, where, x, y, 7, column)
    code_height = 7 * mm
    draw_code128(c, label.code, x - 1 * mm, bottom + 3 * mm, column + 1 * mm, code_height)
    text(c, label.code, x, bottom, 7, column, bold=True, centre=True)


def render(labels, *, copies=1, small="50x25", large="100x50", gs1=False, company_name=""):
    """One PDF, one page per label copy, each page the size of its label."""
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.setTitle("Labels")
    c.setAuthor(latin(company_name))
    for label in labels:
        is_small = label.kind in ("item", "location")
        width, height = size_of(small if is_small else large)
        for _ in range(copies):
            c.setPageSize((width, height))
            if is_small:
                small_label(c, label, width, height, company_name)
            else:
                large_label(c, label, width, height, gs1, company_name)
            c.showPage()
    c.save()
    return buffer.getvalue()
