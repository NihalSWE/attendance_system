"""The sales invoice and the delivery challan as A4 PDFs.

A plain tax invoice (seller and buyer with BIN, lines with VAT, totals). If
the accountant wants the NBR Mushak-6.3 layout, it is a change of layout
here, not of the data (Phase 0, D12).
"""

import io
from decimal import Decimal

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from inventory.label_pdf import latin

INK = colors.HexColor("#17171a")
MUTED = colors.HexColor("#6f6b63")
LINE = colors.HexColor("#ded8cc")
FILL = colors.HexColor("#f0ede7")


def _esc(text):
    return latin(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _money(value):
    return f"{Decimal(value):,.2f}"


def _qty(value):
    return format(Decimal(value).normalize(), "f")


def _styles():
    base = getSampleStyleSheet()["Normal"]
    body = ParagraphStyle("body", parent=base, fontName="Helvetica", fontSize=9, leading=12, textColor=INK)
    return {"body": body,
            "small": ParagraphStyle("small", parent=body, fontSize=8, textColor=MUTED),
            "title": ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=16, leading=20),
            "right": ParagraphStyle("right", parent=body, fontName="Helvetica-Bold", fontSize=13, leading=17,
                                    alignment=2)}


def _party(customer):
    parts = [customer.name, customer.contact_person, customer.address, customer.phone, customer.email]
    return "<br/>".join(_esc(p) for p in parts if p)


def _doc(story, title, company):
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm,
                      bottomMargin=15 * mm, title=title, author=latin(company.name)).build(story)
    return buffer.getvalue()


def invoice(inv):
    """``(pdf, filename)`` for a sales invoice."""
    s = _styles()
    company = inv.company
    order = inv.delivery.order
    lines = list(inv.lines.select_related("item", "uom").order_by("pk"))
    story = [
        Table([[Paragraph(_esc(company.name), s["title"]),
                Paragraph(f"TAX INVOICE<br/>{_esc(inv.number)}", s["right"])]], colWidths=[110 * mm, 70 * mm]),
        Spacer(1, 6 * mm),
        Table([[Paragraph("<b>Bill to</b>", s["body"]), Paragraph("<b>Invoice</b>", s["body"])],
               [Paragraph(_party(inv.customer), s["body"]),
                Paragraph("<br/>".join(_esc(x) for x in (
                    f"Date: {inv.invoice_date:%d %b %Y}",
                    f"Due: {inv.due_date:%d %b %Y}" if inv.due_date else "",
                    f"Order: {order.number}" + (f" (your ref. {order.customer_ref})" if order.customer_ref else ""),
                    f"Delivery: {inv.delivery.number} on {inv.delivery.delivery_date:%d %b %Y}",
                    f"Status: {inv.get_status_display()}") if x), s["body"])]],
              colWidths=[100 * mm, 80 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])),
        Spacer(1, 6 * mm),
    ]
    rows = [["#", "Item", "Quantity", "Unit price", "Disc %", "VAT %", "VAT", "Amount"]]
    for n, line in enumerate(lines, 1):
        rows.append([str(n), Paragraph(_esc(f"{line.item.code} · {line.item.name}"), s["body"]),
                     f"{_qty(line.qty)} {line.uom.code}", _money(line.unit_price), _qty(line.discount_pct),
                     _qty(line.vat_rate), _money(line.vat_amount), _money(line.amount)])
    rows += [["", "", "", "", "", "", "Amount", _money(inv.amount)],
             ["", "", "", "", "", "", "VAT", _money(inv.vat)],
             ["", "", "", "", "", "", "Total", _money(inv.total)]]
    table = Table(rows, colWidths=[8 * mm, 62 * mm, 22 * mm, 22 * mm, 13 * mm, 13 * mm, 18 * mm, 22 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), FILL), ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, len(lines)), 0.4, LINE),
        ("FONT", (6, -3), (-1, -1), "Helvetica-Bold", 8.5), ("LINEABOVE", (6, -1), (-1, -1), 0.8, INK)]))
    story += [table, Spacer(1, 8 * mm)]
    if inv.note:
        story.append(Paragraph(f"<b>Note:</b> {_esc(inv.note)}", s["body"]))
    story.append(Paragraph(_esc(f"Payment within {inv.customer.payment_terms_days} days."
                                if inv.customer.payment_terms_days else "Payment on delivery."), s["small"]))
    if inv.status == "paid" and inv.paid_on:
        story.append(Paragraph(_esc(f"Paid on {inv.paid_on:%d %b %Y}. {inv.paid_note}"), s["small"]))
    return _doc(story, inv.number, company), f"{inv.number}.pdf"


def challan(delivery):
    """``(pdf, filename)`` for a delivery challan: what goes, batch and expiry, signatures."""
    s = _styles()
    company = delivery.company
    order = delivery.order
    lines = list(delivery.lines.select_related("item", "uom", "batch").order_by("pk"))
    story = [
        Table([[Paragraph(_esc(company.name), s["title"]),
                Paragraph(f"DELIVERY CHALLAN<br/>{_esc(delivery.number)}", s["right"])]],
              colWidths=[110 * mm, 70 * mm]),
        Spacer(1, 6 * mm),
        Table([[Paragraph("<b>Deliver to</b>", s["body"]), Paragraph("<b>Delivery</b>", s["body"])],
               [Paragraph(_party(order.customer) + (f"<br/>{_esc(order.delivery_address)}"
                                                    if order.delivery_address else ""), s["body"]),
                Paragraph("<br/>".join(_esc(x) for x in (
                    f"Date: {delivery.delivery_date:%d %b %Y}", f"Order: {order.number}",
                    f"From: {delivery.warehouse}",
                    f"Vehicle: {delivery.vehicle_no}" if delivery.vehicle_no else "",
                    f"Driver: {delivery.driver}" if delivery.driver else "") if x), s["body"])]],
              colWidths=[100 * mm, 80 * mm], style=TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")])),
        Spacer(1, 6 * mm),
    ]
    rows = [["#", "Item", "Batch", "Expiry", "Quantity"]]
    for n, line in enumerate(lines, 1):
        rows.append([str(n), Paragraph(_esc(f"{line.item.code} · {line.item.name}"), s["body"]),
                     line.batch.batch_no if line.batch_id else "—",
                     f"{line.batch.expiry_date:%d %b %Y}" if line.batch_id and line.batch.expiry_date else "—",
                     f"{_qty(line.qty)} {line.uom.code}"])
    table = Table(rows, colWidths=[8 * mm, 82 * mm, 34 * mm, 26 * mm, 30 * mm], repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5), ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), FILL), ("ALIGN", (4, 0), (4, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE)]))
    story += [table, Spacer(1, 20 * mm),
              Table([["Issued by", "Driver", "Received by (customer)"]], colWidths=[60 * mm] * 3,
                    style=TableStyle([("LINEABOVE", (0, 0), (-1, 0), 0.6, INK),
                                      ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5)]))]
    return _doc(story, delivery.number, company), f"{delivery.number}.pdf"
