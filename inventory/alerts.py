"""The morning stock alert (Phase 7): reorder and expiry, by email.

``python manage.py stock_alerts`` (a systemd timer, every morning) sends each
company with the module and alert addresses (Inventory settings) one email:

- items at or below their reorder point or minimum, not covered by open
  purchase orders (the Reorder report);
- batches expired, or expiring within the warning days, that are still in a
  store other than Rejected (the Batch and expiry report).

Nothing to say, no email. It goes out with the company's own mail account
(Company -> Email settings), or the server's when it has none.
"""

import re
from datetime import timedelta

from django.core.exceptions import ValidationError

from access_control.branch_access import ALL_BRANCHES
from inventory.models import StockBalance, Warehouse
from inventory.posting import settings_for

#: How many lines of each list the email shows; the rest is in the reports.
SHOWN = 40


def recipients(text):
    """The addresses in ``text`` (commas, semicolons, spaces or lines between), each once."""
    seen, found = set(), []
    for part in re.split(r"[\s,;]+", text or ""):
        address = part.strip()
        if address and address.lower() not in seen:
            seen.add(address.lower())
            found.append(address)
    return found


def findings(company_id, today):
    """``{"low": [...], "expired": [...], "expiring": [...]}`` for the whole company."""
    from inventory.reports import reorder_rows

    store_ids = list(Warehouse.all_objects.filter(company_id=company_id).values_list("pk", flat=True))
    low = [row for row in reorder_rows(company_id, store_ids, ALL_BRANCHES) if row[-1] != "Covered by open orders"]
    days = settings_for(company_id).expiry_alert_days
    batches = (StockBalance.all_objects.filter(
        company_id=company_id, qty_on_hand__gt=0, batch__expiry_date__isnull=False,
        batch__expiry_date__lte=today + timedelta(days=days)).exclude(warehouse__kind="rejected")
        .select_related("item__base_uom", "batch", "warehouse").order_by("batch__expiry_date", "item__code"))
    expired, expiring = [], []
    for row in batches:
        (expired if row.batch.expiry_date < today else expiring).append(row)
    return {"low": low, "expired": expired, "expiring": expiring, "days": days}


def _qty(value, unit):
    return f"{value.normalize():,f} {unit}"


def message_text(company, found, today):
    """``(subject, body)`` of the alert."""
    low, expired, expiring = found["low"], found["expired"], found["expiring"]
    parts = []
    if low:
        parts.append(f"{len(low)} to reorder")
    if expired:
        parts.append(f"{len(expired)} expired")
    if expiring:
        parts.append(f"{len(expiring)} expiring")
    subject = f"Stock alert {today:%d %b %Y}: " + ", ".join(parts)
    lines = [f"Good morning. The stock alert for {company.name} on {today:%d %B %Y}.", ""]
    if low:
        lines += [f"AT OR BELOW THE REORDER POINT ({len(low)})", ""]
        for item, available, coming, wanted, status in low[:SHOWN]:
            unit = item.base_uom.code
            limit = (f"reorder point {_qty(item.reorder_point, unit)}" if item.reorder_point
                     else f"minimum {_qty(item.min_stock, unit)}")
            lines.append(f"- {item.code} {item.name}: {_qty(available, unit)} available, {limit}, "
                         f"on order {_qty(coming, unit)}; "
                         f"order {_qty(wanted, unit)}" + (" - BELOW MINIMUM" if status == "Below minimum" else ""))
        if len(low) > SHOWN:
            lines.append(f"... and {len(low) - SHOWN} more: see Inventory -> Reports -> Reorder report.")
        lines.append("")
    for title, rows in ((f"EXPIRED, STILL IN STOCK ({len(expired)})", expired),
                        (f"EXPIRING WITHIN {found['days']} DAYS ({len(expiring)})", expiring)):
        if not rows:
            continue
        lines += [title, ""]
        for row in rows[:SHOWN]:
            days_left = (row.batch.expiry_date - today).days
            when = f"expired {-days_left} day(s) ago" if days_left < 0 else f"expires in {days_left} day(s)"
            lines.append(f"- {row.item.code} {row.item.name}, batch {row.batch.batch_no} in {row.warehouse.code}: "
                         f"{_qty(row.qty_on_hand, row.item.base_uom.code)}, {when} "
                         f"({row.batch.expiry_date:%d %b %Y})")
        if len(rows) > SHOWN:
            lines.append(f"... and {len(rows) - SHOWN} more: see Inventory -> Reports -> Batch and expiry.")
        lines.append("")
    lines += ["This email is sent every morning to the addresses in Inventory settings.", company.name]
    return subject, "\n".join(lines)


def send_alert(company, today, *, dry_run=False):
    """Send ``company``'s alert. Returns ``(to, found, subject)``; ``to`` is empty when nothing went.

    Raises ValidationError when there is something to send but no way to send it.
    """
    from django.core.mail import EmailMessage

    from organization import mail_settings as mail

    to = recipients(settings_for(company.pk).alert_emails)
    if not to:
        return [], None, ""
    found = findings(company.pk, today)
    if not (found["low"] or found["expired"] or found["expiring"]):
        return [], found, ""
    subject, body = message_text(company, found, today)
    if dry_run:
        return to, found, subject
    how, from_email, from_name = mail.sender(company.pk)
    if how is None:
        raise ValidationError(mail.not_set_up_reason())
    email = EmailMessage(subject=subject, body=body, to=to,
                         from_email=mail.from_header(from_email, from_name or company.name))
    mail.send(email, mail.connection_for(company.pk) if how == "company" else None)
    return to, found, subject
