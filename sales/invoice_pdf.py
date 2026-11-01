"""The sales invoice and the delivery challan as A4 PDFs (redesigned 2026-11-01).

A professional layout in the app's own colours:

- **Letterhead**: the company's logo, name, legal name, address, phone, email,
  BIN / TIN and registration number (Organisation -> Company profile), a rule;
  the document's title and its number, date and references in a box.
- **Parties**: Bill to and Ship to (invoice) or Deliver to (challan).
- **Lines**: SL, code, description, quantity, unit, rate, discount, VAT and
  amount (invoice); batch and expiry instead of money (challan); light rows.
- **Totals** (invoice): subtotal, VAT, the grand total in a band, and the
  amount in words (Taka ... only, in lakh and crore).
- **Close**: payment terms and note, signature lines, and on every page the
  page number and that it is computer generated. A draft, cancelled or paid
  invoice says so across the page.

Bangla names print with the bundled Hind Siliguri font (common/exports.py).
If the accountant wants the NBR Mushak-6.3 layout, it is a change of layout
here, not of the data (Phase 0, D12).
"""

import io
from decimal import ROUND_HALF_UP, Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import Image, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from common.exports import FONT, FONT_BOLD, _register_fonts

INK = colors.HexColor("#17171a")
MUTED = colors.HexColor("#6f6b63")
LINE = colors.HexColor("#e2dcd0")
TILE = colors.HexColor("#f6f4ef")
ZEBRA = colors.HexColor("#fbfaf7")
ACCENT = colors.HexColor("#1d5fb4")
GOOD = colors.HexColor("#2f6b34")
BAD = colors.HexColor("#a32a1e")
WIDTH = 180 * mm


def _esc(text):
    return str(text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _money(value):
    return f"{Decimal(value):,.2f}"


def _qty(value):
    return f"{Decimal(value).normalize():,f}"


def _styles():
    _register_fonts()

    def style(name, size=8.6, font=FONT, color=INK, align=0, leading=None):
        return ParagraphStyle(name, fontName=font, fontSize=size, leading=leading or size * 1.42,
                              textColor=color, alignment=align, shaping=1)

    return {
        "company": style("company", 15, FONT_BOLD, leading=19),
        "letter": style("letter", 8, color=MUTED),
        "title": style("title", 17, FONT_BOLD, ACCENT, 2, 21),
        "meta_k": style("meta_k", 7.6, color=MUTED),
        "meta_v": style("meta_v", 8.4, FONT_BOLD, align=2),
        "label": style("label", 7.4, FONT_BOLD, MUTED),
        "body": style("body", 8.6),
        "bold": style("bold", 8.6, FONT_BOLD),
        "cell": style("cell", 8.2),
        "cell_r": style("cell_r", 8.2, align=2),
        "cell_b": style("cell_b", 8.2, FONT_BOLD),
        "head": style("head", 7.6, FONT_BOLD, colors.white),
        "head_r": style("head_r", 7.6, FONT_BOLD, colors.white, 2),
        "small": style("small", 7.6, color=MUTED),
        "words": style("words", 8.4, FONT_BOLD),
        "total_k": style("total_k", 8.6, color=MUTED, align=2),
        "total_v": style("total_v", 8.6, align=2),
        "grand_k": style("grand_k", 10, FONT_BOLD, colors.white, 2),
        "grand_v": style("grand_v", 10.5, FONT_BOLD, colors.white, 2),
    }


# --- amount in words (lakh and crore) ---------------------------------------------

ONES = ("", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven",
        "Twelve", "Thirteen", "Fourteen", "Fifteen", "Sixteen", "Seventeen", "Eighteen", "Nineteen")
TENS = ("", "", "Twenty", "Thirty", "Forty", "Fifty", "Sixty", "Seventy", "Eighty", "Ninety")


def _under_thousand(n):
    words = []
    if n >= 100:
        words += [ONES[n // 100], "Hundred"]
        n %= 100
    if n >= 20:
        words.append(TENS[n // 10] + (f"-{ONES[n % 10]}" if n % 10 else ""))
    elif n:
        words.append(ONES[n])
    return words


def in_words(amount, currency="Taka", fraction="Paisa"):
    """``12,345.50`` -> ``Taka Twelve Thousand Three Hundred Forty-Five and Paisa Fifty Only``."""
    amount = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    whole, cents = int(amount), int((amount - int(amount)) * 100)

    def words_of(n):
        if n == 0:
            return ["Zero"]
        out = []
        crore, n = divmod(n, 10_000_000)
        lakh, n = divmod(n, 100_000)
        thousand, n = divmod(n, 1000)
        if crore:
            out += words_of(crore) + ["Crore"]
        if lakh:
            out += _under_thousand(lakh) + ["Lakh"]
        if thousand:
            out += _under_thousand(thousand) + ["Thousand"]
        out += _under_thousand(n)
        return out

    text = f"{currency} " + " ".join(words_of(whole))
    if cents:
        text += f" and {fraction} " + " ".join(_under_thousand(cents))
    return text + " Only"


# --- shared pieces -------------------------------------------------------------------

def _logo(company):
    if not getattr(company, "logo", None):
        return None
    try:
        image = Image(company.logo.path)
    except Exception:  # a missing or unreadable file: the letterhead without it
        return None
    ratio = image.imageWidth / float(image.imageHeight or 1)
    height = 16 * mm
    image.drawHeight, image.drawWidth = height, min(height * ratio, 40 * mm)
    return image


def _letterhead(company, s, title, meta):
    """Logo and company on the left; the title and its facts on the right."""
    lines = [company.legal_name if company.legal_name and company.legal_name != company.name else "",
             (company.address or "").replace("\n", ", "),
             " · ".join(x for x in (company.phone, company.email) if x),
             " · ".join(x for x in (f"BIN/TIN: {company.tax_identifier}" if company.tax_identifier else "",
                                    f"Reg. no: {company.registration_number}" if company.registration_number else "")
                        if x)]
    who = [Paragraph(_esc(company.name), s["company"])] + [Paragraph(_esc(x), s["letter"]) for x in lines if x]
    logo = _logo(company)
    left = Table([[logo, who]] if logo else [[who]], colWidths=[44 * mm, 66 * mm] if logo else [110 * mm],
                 style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))
    facts = Table([[Paragraph(_esc(k), s["meta_k"]), Paragraph(_esc(v), s["meta_v"])] for k, v in meta if v],
                  colWidths=[24 * mm, 44 * mm],
                  style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), TILE),
                                    ("LINEBELOW", (0, 0), (-1, -2), 0.3, LINE),
                                    ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))
    right = Table([[Paragraph(_esc(title), s["title"])], [Spacer(1, 2 * mm)], [facts]], colWidths=[70 * mm],
                  style=TableStyle([("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    head = Table([[left, right]], colWidths=[110 * mm, 70 * mm],
                 style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                                   ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    rule = Table([[""]], colWidths=[WIDTH], rowHeights=[1.2],
                 style=TableStyle([("BACKGROUND", (0, 0), (-1, -1), INK)]))
    return [head, Spacer(1, 4 * mm), rule, Spacer(1, 5 * mm)]


def _party_box(label, lines, s):
    content = [Paragraph(_esc(label).upper(), s["label"]), Spacer(1, 1.5 * mm)]
    content += [Paragraph(_esc(line), s["bold"] if n == 0 else s["body"]) for n, line in enumerate(lines) if line]
    return content


def _parties(boxes, s):
    widths = [WIDTH / len(boxes)] * len(boxes)
    table = Table([[_party_box(label, lines, s) for label, lines in boxes]], colWidths=widths)
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOX", (0, 0), (-1, -1), 0.5, LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.5, LINE), ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8), ("TOPPADDING", (0, 0), (-1, -1), 7),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    return [table, Spacer(1, 6 * mm)]


def _customer_lines(customer):
    return [customer.name, customer.contact_person, customer.address,
            " · ".join(x for x in (customer.phone, customer.email) if x), f"Customer code: {customer.code}"]


def _lines_table(header, rows, widths, s, numeric):
    data = [[Paragraph(_esc(h), s["head_r"] if i in numeric else s["head"]) for i, h in enumerate(header)]]
    for row in rows:
        data.append([cell if not isinstance(cell, str) else Paragraph(_esc(cell), s["cell_r"] if i in numeric
                                                                         else s["cell"])
                     for i, cell in enumerate(row)])
    table = Table(data, colWidths=widths, repeatRows=1)
    style = [("BACKGROUND", (0, 0), (-1, 0), INK), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
             ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
             ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE)]
    style += [("BACKGROUND", (0, r), (-1, r), ZEBRA) for r in range(2, len(data), 2)]
    table.setStyle(TableStyle(style))
    return table


def _signatures(labels, s):
    cells = [[Paragraph(_esc(label), s["small"]) for label in labels]]
    width = WIDTH / len(labels)
    table = Table(cells, colWidths=[width] * len(labels))
    table.setStyle(TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, INK), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 14), ("TOPPADDING", (0, 0), (-1, -1), 4)]))
    return KeepTogether([Spacer(1, 16 * mm), table])


def _doc(story, title, company, stamp=None):
    """Build with a footer on every page and, for a draft or cancelled or paid
    invoice, a word across the page."""
    _register_fonts()

    def page(canvas, document):
        canvas.saveState()
        canvas.setFont(FONT, 7.2)
        canvas.setFillColor(MUTED)
        canvas.setStrokeColor(LINE)
        canvas.line(15 * mm, 12 * mm, 195 * mm, 12 * mm)
        canvas.drawString(15 * mm, 8 * mm, f"{company.name} | {title} | computer generated")
        canvas.drawRightString(195 * mm, 8 * mm, f"Page {document.page}")
        if stamp:
            word, color = stamp
            canvas.setFont(FONT_BOLD, 64)
            canvas.setFillColor(color)
            canvas.setFillAlpha(0.08)
            canvas.translate(105 * mm, 150 * mm)
            canvas.rotate(30)
            canvas.drawCentredString(0, 0, word)
        canvas.restoreState()

    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm,
                      bottomMargin=18 * mm, title=title, author=company.name).build(
        story, onFirstPage=page, onLaterPages=page)
    return buffer.getvalue()


# --- the invoice --------------------------------------------------------------------

def invoice(inv):
    """``(pdf, filename)`` for a sales invoice."""
    s = _styles()
    company = inv.company
    order = inv.delivery.order
    customer = inv.customer
    lines = list(inv.lines.select_related("item", "uom").order_by("pk"))
    currency = company.currency or "BDT"
    story = _letterhead(company, s, "TAX INVOICE", [
        ("Invoice no", inv.number), ("Date", f"{inv.invoice_date:%d %b %Y}"),
        ("Due", f"{inv.due_date:%d %b %Y}" if inv.due_date else "On delivery"),
        ("Order", order.number), ("Your ref.", order.customer_ref),
        ("Delivery", f"{inv.delivery.number} · {inv.delivery.delivery_date:%d %b %Y}"),
    ])
    ship = [customer.name, order.delivery_address or customer.address]
    story += _parties([("Bill to", _customer_lines(customer)), ("Ship to", ship)], s)

    rows = []
    for n, line in enumerate(lines, 1):
        rows.append([str(n), line.item.code, Paragraph(_esc(line.item.name), s["cell"]), _qty(line.qty),
                     line.uom.code, _money(line.unit_price),
                     f"{_qty(line.discount_pct)}%" if line.discount_pct else "—",
                     f"{_qty(line.vat_rate)}%" if line.vat_rate else "—", _money(line.vat_amount),
                     _money(line.amount)])
    story += [_lines_table(("SL", "Code", "Description", "Qty", "Unit", "Rate", "Disc.", "VAT", "VAT amt", "Amount"),
                           rows, [8 * mm, 18 * mm, 44 * mm, 15 * mm, 12 * mm, 20 * mm, 12 * mm, 12 * mm, 17 * mm,
                                  22 * mm], s, numeric={0, 3, 5, 6, 7, 8, 9}),
              Spacer(1, 4 * mm)]

    totals = Table([
        [Paragraph("Subtotal", s["total_k"]), Paragraph(_money(inv.amount), s["total_v"])],
        [Paragraph("VAT", s["total_k"]), Paragraph(_money(inv.vat), s["total_v"])],
        [Paragraph(f"Total ({_esc(currency)})", s["grand_k"]), Paragraph(_money(inv.total), s["grand_v"])],
    ], colWidths=[34 * mm, 36 * mm])
    totals.setStyle(TableStyle([("BACKGROUND", (0, 2), (-1, 2), INK), ("LINEBELOW", (0, 0), (-1, 0), 0.3, LINE),
                                ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    words = [Paragraph("AMOUNT IN WORDS", s["label"]), Spacer(1, 1.5 * mm),
             Paragraph(_esc(in_words(inv.total, "Taka" if currency == "BDT" else currency)), s["words"]),
             Spacer(1, 4 * mm), Paragraph("TERMS", s["label"]), Spacer(1, 1.5 * mm),
             Paragraph(_esc(f"Payment within {customer.payment_terms_days} days of the invoice date."
                            if customer.payment_terms_days else "Payment on delivery."), s["body"])]
    if inv.note:
        words += [Spacer(1, 2 * mm), Paragraph(f"<b>Note:</b> {_esc(inv.note)}", s["body"])]
    if inv.status == "paid" and inv.paid_on:
        words += [Spacer(1, 2 * mm), Paragraph(_esc(f"Paid on {inv.paid_on:%d %b %Y}. {inv.paid_note}"), s["body"])]
    story.append(KeepTogether(Table([[words, totals]], colWidths=[106 * mm, 74 * mm], style=TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0)]))))
    story.append(_signatures(("Prepared by", "Checked by", f"For {company.name}"), s))
    stamp = {"draft": ("DRAFT", MUTED), "cancelled": ("CANCELLED", BAD), "paid": ("PAID", GOOD)}.get(inv.status)
    return _doc(story, inv.number, company, stamp), f"{inv.number}.pdf"


# --- the delivery challan ------------------------------------------------------------

def challan(delivery):
    """``(pdf, filename)`` for a delivery challan: what goes, batch and expiry, signatures."""
    s = _styles()
    company = delivery.company
    order = delivery.order
    customer = order.customer
    lines = list(delivery.lines.select_related("item", "uom", "batch").order_by("pk"))
    story = _letterhead(company, s, "DELIVERY CHALLAN", [
        ("Challan no", delivery.number), ("Date", f"{delivery.delivery_date:%d %b %Y}"),
        ("Order", order.number), ("Your ref.", order.customer_ref), ("From store", str(delivery.warehouse)),
        ("Vehicle", delivery.vehicle_no), ("Driver", delivery.driver),
    ])
    story += _parties([("Deliver to", [customer.name, order.delivery_address or customer.address,
                                       customer.contact_person,
                                       " · ".join(x for x in (customer.phone, customer.email) if x)]),
                       ("Customer", [customer.name, f"Customer code: {customer.code}"])], s)
    rows, units = [], {}
    for n, line in enumerate(lines, 1):
        units[line.uom.code] = units.get(line.uom.code, Decimal("0")) + line.qty
        rows.append([str(n), line.item.code, Paragraph(_esc(line.item.name), s["cell"]),
                     line.batch.batch_no if line.batch_id else "—",
                     f"{line.batch.expiry_date:%d %b %Y}" if line.batch_id and line.batch.expiry_date else "—",
                     _qty(line.qty), line.uom.code])
    story.append(_lines_table(("SL", "Code", "Description", "Batch", "Expiry", "Qty", "Unit"), rows,
                              [8 * mm, 22 * mm, 64 * mm, 30 * mm, 24 * mm, 18 * mm, 14 * mm], s, numeric={0, 5}))
    total = Table([[Paragraph("Total quantity", s["total_k"]),
                    Paragraph(_esc(" · ".join(f"{_qty(q)} {u}" for u, q in units.items())), s["bold"])]],
                  colWidths=[136 * mm, 44 * mm])
    total.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), TILE), ("TOPPADDING", (0, 0), (-1, -1), 5),
                               ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    story += [total, Spacer(1, 5 * mm),
              Paragraph("Received the goods above in good condition and in the quantities shown.", s["small"])]
    if delivery.note:
        story += [Spacer(1, 2 * mm), Paragraph(f"<b>Note:</b> {_esc(delivery.note)}", s["body"])]
    story.append(_signatures(("Issued by (store)", "Driver", "Received by (customer, with seal)"), s))
    return _doc(story, delivery.number, company), f"{delivery.number}.pdf"
