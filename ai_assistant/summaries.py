"""What the managers receive (plan §8): the order summary with stock status
when an order is confirmed, and the daily and weekly reports.

The figures always come from the database. The AI only writes the short
paragraph at the top of a report ("what changed, what needs attention"); if
it is off or fails, the report goes without it.
"""

import datetime
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db.models import Sum

from ai_assistant.claude import AiUnavailable, ask_json
from ai_assistant.models import AiCall, AiRecipient, AiReportRun
from common.tenant import use_company
from inventory.doc_common import local_today

CENT = Decimal("0.01")


def _money(value):
    return f"{(value or Decimal('0')).quantize(CENT):,}"


def _qty(value):
    return f"{(value or Decimal('0')).normalize():,f}"


def recipients(company_id, flag):
    return list(AiRecipient.all_objects.filter(company_id=company_id, active=True, **{flag: True}).order_by("name"))


def _send(company, subject, body, to, attachments=()):
    from django.core.mail import EmailMessage

    from organization import mail_settings as mail

    how, from_email, from_name = mail.sender(company.pk)
    if how is None:
        raise ValidationError(mail.not_set_up_reason())
    email = EmailMessage(subject=subject, body=body, to=to,
                         from_email=mail.from_header(from_email, from_name or company.name))
    for name, content, kind in attachments:
        email.attach(name, content, kind)
    mail.send(email, mail.connection_for(company.pk) if how == "company" else None)


# --------------------------------------------------------------------------
# The order summary
# --------------------------------------------------------------------------

def order_summary(order):
    """``(subject, body)``: what was confirmed, and the stock behind each line."""
    from inventory import fefo
    from sales import orders

    with use_company(order.company_id):
        lines = list(order.lines.select_related("item__base_uom", "uom", "warehouse").order_by("pk"))
        figures = orders.totals(order)
    subject = f"New order {order.number}: {order.customer.name}, {_money(figures['total'])}"
    text = [f"Order {order.number} for {order.customer.name} was confirmed"
            + (f" by {order.confirmed_by.get_full_name() or order.confirmed_by.email}" if order.confirmed_by_id else "")
            + ".", ""]
    text.append(f"Order date: {order.order_date:%d %b %Y}"
                + (f" · deliver by {order.delivery_date:%d %b %Y}" if order.delivery_date else ""))
    if order.customer_ref:
        text.append(f"Customer's order no.: {order.customer_ref}")
    text += ["", "LINES"]
    short = []
    for line in lines:
        free = sum((row.available for row in fefo.usable_rows(order.company_id, line.item, line.warehouse,
                                                               local_today(order.company))), Decimal("0"))
        reserved = line.qty_reserved_base
        status = "reserved" if reserved >= line.qty_base else f"SHORT by {_qty(line.qty_base - reserved)}"
        if reserved < line.qty_base:
            short.append(line.item.code)
        text.append(f"- {line.item.code} {line.item.name}: {_qty(line.qty)} {line.uom.code} from {line.warehouse.code}"
                    f" at {_money(line.unit_price)} = {_money(line.amount)} ({status}; "
                    f"{_qty(free)} {line.item.base_uom.code} still free there)")
    text += ["", f"Amount {_money(figures['amount'])} + VAT {_money(figures['vat'])} = {_money(figures['total'])}"]
    if short:
        text += ["", "Short: " + ", ".join(short) + " - plan production or a purchase."]
    if order.credit_note:
        text += ["", f"Over the credit limit, confirmed because: {order.credit_note}"]
    text += ["", f"Source: {order.get_source_display()}. {order.note}".strip(), "", order.company.name]
    return subject, "\n".join(text)


def send_order_summary(order):
    """Email the summary to the managers who get new orders; returns the addresses."""
    to = [r.email for r in recipients(order.company_id, "gets_orders")]
    if not to:
        return []
    subject, body = order_summary(order)
    _send(order.company, subject, body, to)
    return to


# --------------------------------------------------------------------------
# Daily and weekly reports
# --------------------------------------------------------------------------

def period(kind, today):
    return (today, today) if kind == AiReportRun.Kind.DAILY else (today - datetime.timedelta(days=6), today)


def figures(company, kind, today):
    """The report's numbers, as a plain dict (also what the AI summarises)."""
    from inventory.models import StockBalance, Warehouse
    from inventory.posting import settings_for
    from inventory.reports import reorder_rows
    from production.models import Output
    from sales.models import Delivery, SalesInvoice, SalesInvoiceLine, SalesOrder

    start, end = period(kind, today)
    cid = company.pk
    orders = SalesOrder.all_objects.filter(company_id=cid)
    received = orders.filter(order_date__gte=start, order_date__lte=end)
    invoices = SalesInvoice.all_objects.filter(company_id=cid, invoice_date__gte=start, invoice_date__lte=end,
                                               status__in=["issued", "paid"])
    by_item = (SalesInvoiceLine.all_objects.filter(invoice__in=invoices).values("item__code", "item__name")
               .annotate(amount=Sum("amount")).order_by("-amount")[:10])
    by_customer = invoices.values("customer__name").annotate(total=Sum("total")).order_by("-total")[:10]
    stores = list(Warehouse.all_objects.filter(company_id=cid).values_list("pk", flat=True))
    from access_control.branch_access import ALL_BRANCHES

    low = [row for row in reorder_rows(cid, stores, ALL_BRANCHES) if row[-1] != "Covered by open orders"]
    alert = settings_for(cid).expiry_alert_days
    expiring = StockBalance.all_objects.filter(company_id=cid, qty_on_hand__gt=0, batch__expiry_date__isnull=False,
                                               batch__expiry_date__lte=today + datetime.timedelta(days=alert)).exclude(
        warehouse__kind="rejected").count()
    made = (Output.all_objects.filter(company_id=cid, status="posted", output_date__gte=start, output_date__lte=end)
            .values("order__item__code", "order__item__name").annotate(good=Sum("qty_good")))
    return {
        "company": company.name, "kind": kind, "from": start.isoformat(), "to": end.isoformat(),
        "orders_received": received.count(),
        "orders_from_ai": received.filter(source=SalesOrder.Source.AI).count(),
        "orders_confirmed": orders.filter(confirmed_at__date__gte=start, confirmed_at__date__lte=end).count(),
        "deliveries_posted": Delivery.all_objects.filter(company_id=cid, status="posted", delivery_date__gte=start,
                                                         delivery_date__lte=end).count(),
        "orders_waiting_delivery": orders.filter(status__in=["confirmed", "partly_delivered"]).count(),
        "invoiced_total": str((invoices.aggregate(t=Sum("total"))["t"] or Decimal("0")).quantize(CENT)),
        "unpaid_total": str((SalesInvoice.all_objects.filter(company_id=cid, status="issued").aggregate(
            t=Sum("total"))["t"] or Decimal("0")).quantize(CENT)),
        "top_items": [{"item": f"{r['item__code']} {r['item__name']}", "amount": str(r["amount"].quantize(CENT))}
                      for r in by_item],
        "top_customers": [{"customer": r["customer__name"], "total": str(r["total"].quantize(CENT))}
                          for r in by_customer],
        "low_stock": [{"item": f"{item.code} {item.name}", "available": str(available.normalize()),
                       "status": status} for item, available, _coming, _wanted, status in low[:15]],
        "low_stock_count": len(low),
        "batches_expiring": expiring,
        "production": [{"item": f"{r['order__item__code']} {r['order__item__name']}", "good": str(r["good"].normalize())}
                       for r in made],
    }


SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "Two to four sentences: what changed in the period."},
        "attention": {"type": "array", "items": {"type": "string"},
                      "description": "Up to five short points that need a manager's attention."},
    },
    "required": ["summary", "attention"],
    "additionalProperties": False,
}

SUMMARY_PROMPT = """You write the short opening paragraph of a sales and stock report for the managers of a food and beverage manufacturer in Bangladesh. You get the report's figures as JSON. Say plainly what changed in the period and what needs attention (stock running low, orders waiting for delivery, unpaid invoices, expiring batches). Use only the figures given; never invent numbers. Write in plain English, no headings or markdown."""


def ai_summary(company, data):
    """The AI's paragraph and attention points; ``("", [])`` when the AI is off or fails."""
    try:
        answer = ask_json(company_id=company.pk, kind=AiCall.Kind.SUMMARY, system=SUMMARY_PROMPT,
                          content=json.dumps(data, ensure_ascii=False, sort_keys=True), schema=SUMMARY_SCHEMA,
                          max_tokens=4000)
    except (AiUnavailable, ValidationError):
        return "", []
    return (answer.get("summary") or "").strip(), [p.strip() for p in answer.get("attention") or [] if p.strip()]


def report_text(data, summary, attention):
    title = "Daily" if data["kind"] == AiReportRun.Kind.DAILY else "Weekly"
    span = data["from"] if data["from"] == data["to"] else f"{data['from']} to {data['to']}"
    lines = [f"{title} sales and stock report, {data['company']}, {span}", ""]
    if summary:
        lines += [summary, ""]
    if attention:
        lines += ["Needs attention:"] + [f"- {point}" for point in attention] + [""]
    lines += [
        f"Orders received: {data['orders_received']} ({data['orders_from_ai']} read by the AI assistant)",
        f"Orders confirmed: {data['orders_confirmed']}",
        f"Deliveries posted: {data['deliveries_posted']}; orders waiting for delivery: {data['orders_waiting_delivery']}",
        f"Invoiced: {_money(Decimal(data['invoiced_total']))}; unpaid invoices: {_money(Decimal(data['unpaid_total']))}",
        f"Items at or below the reorder point: {data['low_stock_count']}; batches expiring soon: {data['batches_expiring']}",
        "",
    ]
    if data["top_items"]:
        lines += ["Top items:"] + [f"- {r['item']}: {_money(Decimal(r['amount']))}" for r in data["top_items"]] + [""]
    if data["top_customers"]:
        lines += ["Top customers:"] + [f"- {r['customer']}: {_money(Decimal(r['total']))}"
                                       for r in data["top_customers"]] + [""]
    if data["production"]:
        lines += ["Production:"] + [f"- {r['item']}: {r['good']} good" for r in data["production"]] + [""]
    if data["low_stock"]:
        lines += ["Low stock:"] + [f"- {r['item']}: {r['available']} available ({r['status']})"
                                   for r in data["low_stock"]] + [""]
    lines.append("The figures are from the ERP; the opening paragraph is written by the AI assistant.")
    return "\n".join(lines)


def report_sheet(data, title):
    from common import exports

    rows = [["Orders received", "", data["orders_received"]], ["Orders confirmed", "", data["orders_confirmed"]],
            ["Deliveries posted", "", data["deliveries_posted"]],
            ["Orders waiting for delivery", "", data["orders_waiting_delivery"]],
            ["Invoiced", "", Decimal(data["invoiced_total"])], ["Unpaid invoices", "", Decimal(data["unpaid_total"])]]
    rows += [["Top item", r["item"], Decimal(r["amount"])] for r in data["top_items"]]
    rows += [["Top customer", r["customer"], Decimal(r["total"])] for r in data["top_customers"]]
    rows += [["Production (good)", r["item"], Decimal(r["good"])] for r in data["production"]]
    rows += [["Low stock", r["item"], Decimal(r["available"])] for r in data["low_stock"]]
    return exports.table_xlsx(title=title, lines=[f"{data['from']} to {data['to']}"],
                              headers=["What", "Which", "Figure"], rows=rows, numeric=[2],
                              widths={0: 28, 1: 40, 2: 16})


def send_report(company, kind, today, *, dry_run=False):
    """Build and send one report. Returns the ``AiReportRun`` (or None when nobody gets it)."""
    flag = "gets_daily" if kind == AiReportRun.Kind.DAILY else "gets_weekly"
    to = [r.email for r in recipients(company.pk, flag)]
    if not to:
        return None
    data = figures(company, kind, today)
    summary, attention = ("", []) if dry_run else ai_summary(company, data)
    body = report_text(data, summary, attention)
    start, end = period(kind, today)
    run = AiReportRun(company=company, kind=kind, period_start=start, period_end=end, summary=summary,
                      sent_to=", ".join(to))
    if dry_run:
        run.status = "dry-run"
        return run
    title = f"{'Daily' if kind == AiReportRun.Kind.DAILY else 'Weekly'} report"
    try:
        _send(company, f"{title} {end:%d %b %Y}: {company.name}", body, to,
              attachments=[(f"{kind}-report-{end.isoformat()}.xlsx", report_sheet(data, title),
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")])
    except ValidationError as exc:
        run.status, run.error = "failed", " ".join(exc.messages)[:500]
    run.save()
    return run

