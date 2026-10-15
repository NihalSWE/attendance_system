"""The purchase order as a PDF (A4), to print or email to the supplier."""

import io
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from common.tenant import use_company
from inventory.label_pdf import latin
from purchasing import orders
from purchasing.common import audit, require
from purchasing.models import PurchaseOrder

INK = colors.HexColor("#17171a")
MUTED = colors.HexColor("#6f6b63")
LINE = colors.HexColor("#ded8cc")
FILL = colors.HexColor("#f0ede7")


def _money(value):
    return f"{Decimal(value):,.2f}"


def _qty(value):
    return format(Decimal(value).normalize(), "f")


def build(order):
    """``(pdf bytes, filename)`` for ``order``."""
    company = order.company
    lines = list(order.lines.select_related("item", "uom").order_by("pk"))
    figures = orders.totals(order)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="Helvetica", fontSize=9,
                          leading=12, textColor=INK)
    small = ParagraphStyle("small", parent=body, fontSize=8, textColor=MUTED)
    title = ParagraphStyle("title", parent=body, fontName="Helvetica-Bold", fontSize=16, leading=20)

    def esc(text):
        """A value from the database, safe inside Paragraph markup."""
        return latin(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def p(markup, style=body):
        """``markup`` is ours (tags allowed); every value in it went through ``esc``."""
        return Paragraph(markup, style)

    supplier = order.supplier
    story = [
        Table([[p(esc(company.name), title), p(f"PURCHASE ORDER<br/>{esc(order.number)}", ParagraphStyle(
            "num", parent=title, alignment=2, fontSize=13))]], colWidths=[110 * mm, 70 * mm]),
        Spacer(1, 6 * mm),
        Table([
            [p("<b>Supplier</b>"), p("<b>Order</b>")],
            [p("<br/>".join(esc(x) for x in (supplier.name, supplier.contact_person, supplier.address,
                                             supplier.phone, supplier.email,
                                             f"BIN {supplier.bin}" if supplier.bin else "") if x)),
             p("<br/>".join(esc(x) for x in (
                 f"Date: {order.order_date:%d %b %Y}",
                 f"Deliver by: {order.expected_date:%d %b %Y}" if order.expected_date else "",
                 f"Deliver to: {order.branch.name}",
                 f"Status: {order.get_status_display()}") if x))],
        ], colWidths=[100 * mm, 80 * mm], style=TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, 0), 2)])),
        Spacer(1, 6 * mm),
    ]
    head = ["#", "Item", "Quantity", "Unit price", "Disc %", "VAT %", "Amount"]
    rows = [head]
    for number, line in enumerate(lines, 1):
        rows.append([str(number), p(esc(f"{line.item.code} · {line.item.name}")
                                    + (f"<br/><font size=7 color='#6f6b63'>{esc(line.note)}</font>"
                                       if line.note else "")),
                     f"{_qty(line.qty)} {line.uom.code}", _money(line.unit_price),
                     _qty(line.discount_pct), _qty(line.vat_rate), _money(line.amount)])
    rows += [["", "", "", "", "", "Amount", _money(figures["amount"])],
             ["", "", "", "", "", "VAT", _money(figures["vat"])],
             ["", "", "", "", "", "Total", _money(figures["total"])]]
    table = Table(rows, colWidths=[8 * mm, 70 * mm, 24 * mm, 24 * mm, 14 * mm, 14 * mm, 26 * mm],
                  repeatRows=1)
    table.setStyle(TableStyle([
        ("FONT", (0, 0), (-1, -1), "Helvetica", 8.5),
        ("FONT", (0, 0), (-1, 0), "Helvetica-Bold", 8.5),
        ("BACKGROUND", (0, 0), (-1, 0), FILL),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LINEBELOW", (0, 0), (-1, len(lines)), 0.4, LINE),
        ("FONT", (5, -3), (-1, -1), "Helvetica-Bold", 8.5),
        ("LINEABOVE", (5, -1), (-1, -1), 0.8, INK),
    ]))
    story += [table, Spacer(1, 6 * mm)]
    if order.terms:
        story.append(p(f"<b>Terms:</b> {esc(order.terms)}"))
    if order.note:
        story.append(p(f"<b>Note:</b> {esc(order.note)}"))
    story.append(Spacer(1, 10 * mm))
    approver = ""
    if order.approved_by_id:
        approver = order.approved_by.get_full_name() or order.approved_by.email
    story.append(p(esc(f"Approved by {approver} on {order.approved_at:%d %b %Y}") if approver else
                   "Not approved yet: not an order.", small))
    story.append(p(esc(f"Please quote {order.number} on the challan and the invoice."), small))
    buffer = io.BytesIO()
    SimpleDocTemplate(buffer, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm,
                      topMargin=15 * mm, bottomMargin=15 * mm, title=order.number,
                      author=latin(company.name)).build(story)
    return buffer.getvalue(), f"{order.number}.pdf"


@transaction.atomic
def email_order(*, actor, company_id, order_id, to, message=""):
    """Email the approved order to the supplier with the company's mail account."""
    from django import forms
    from django.core.mail import EmailMessage

    from organization import mail_settings as mail

    with use_company(company_id):
        order = PurchaseOrder.objects.select_related("supplier", "branch", "company",
                                                     "approved_by").filter(pk=order_id).first()
    if order is None:
        raise ValidationError("Purchase order not found.")
    membership = require(actor, company_id, "purchase.order", order.branch_id)
    if order.status not in (PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTLY_RECEIVED):
        raise ValidationError("Only an approved order is sent to the supplier.")
    to = (to or order.supplier.email or "").strip()
    if not to:
        raise ValidationError({"to": f"{order.supplier.name} has no email address; type one."})
    forms.EmailField().clean(to)
    how, from_email, from_name = mail.sender(company_id)
    if how is None:
        raise ValidationError(mail.not_set_up_reason())
    with use_company(company_id):
        content, filename = build(order)
    company = order.company
    email = EmailMessage(
        subject=f"Purchase order {order.number} from {company.name}",
        body=(message.strip() + "\n\n" if message.strip() else "")
        + f"Dear {order.supplier.contact_person or order.supplier.name},\n\n"
          f"Please find our purchase order {order.number} attached. Kindly quote this number "
          f"on the delivery challan and the invoice.\n\nThank you,\n{company.name}",
        from_email=mail.from_header(from_email, from_name or company.name), to=[to])
    email.attach(filename, content, "application/pdf")
    mail.send(email, mail.connection_for(company_id) if how == "company" else None)
    with use_company(company_id):
        orders.mark_sent(order, to)
        audit(actor, membership, "purchase.order.emailed", order,
              after={"to": to, "through": how, "sent_at": timezone.now().isoformat()})
    return to
