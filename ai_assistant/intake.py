"""From a message to a draft sales order, and the reviewer's actions on it.

1. ``receive`` saves the message (an email from the inbox, or pasted text) -
   nothing is lost, whatever happens next.
2. ``process`` asks Claude to read it (``extract``), matches the customer,
   items and units in our code (``matching``) and makes a **draft** sales
   order as the "draft orders are entered as" login: each line from its
   item's own store (Phase 8 mixed-store orders), at the customer's price.
   Stock, credit and repeats are checked; every doubt is listed as a problem.
3. A person reviews it: picks the customer if unknown, matches a line no item
   was found for (the words are remembered as an alias for next time),
   corrects the draft on the sales order page, then **confirms** - which
   reserves the stock and emails the managers the order summary.

If the AI is off, has no key or reached the day's limit, the message waits
and can be entered by hand.
"""

import datetime

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from ai_assistant import matching
from ai_assistant.claude import AiUnavailable, settings_for
from ai_assistant.models import InboundMessage, ItemAlias
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from inventory.access import require
from inventory.doc_common import local_today
from inventory.models import Warehouse
from sales import orders
from sales.models import SalesOrder, SalesOrderLine

VIEW = ("sales.order", "sales.approve")
Status = InboundMessage.Status


def _audit(actor, membership, action, message, after=None):
    record_company_event(actor=actor, membership=membership, company=membership.company, action=action,
                         obj=message, before={}, after=after or {})


# --------------------------------------------------------------------------
# Receiving and reading
# --------------------------------------------------------------------------

def receive(*, company_id, channel, body, sender="", subject="", message_id="", received_at=None, actor=None):
    """Save a message. An email already saved (same Message-ID) is not saved twice; returns None then."""
    body = (body or "").strip()
    if not body:
        raise ValidationError({"body": "There is no text to read."})
    if message_id and InboundMessage.all_objects.filter(company_id=company_id, message_id=message_id[:500]).exists():
        return None
    return InboundMessage.all_objects.create(
        company_id=company_id, channel=channel, body=body[:50000], sender=sender[:255], subject=subject[:500],
        message_id=message_id[:500], received_at=received_at or timezone.now(),
        entered_by=actor if getattr(actor, "pk", None) else None)


def process(message):
    """Read ``message`` with the AI and draft its order. Returns the message, saved."""
    from ai_assistant.extract import read_order

    if message.sales_order_id or message.status in (Status.CONVERTED, Status.IGNORED):
        return message
    try:
        data = read_order(message)
    except AiUnavailable as exc:
        message.status, message.note = Status.WAITING, " ".join(exc.messages)[:500]
        message.save(update_fields=["status", "note", "updated_at"])
        return message
    except ValidationError as exc:
        message.status, message.note = Status.FAILED, " ".join(exc.messages)[:500]
        message.save(update_fields=["status", "note", "updated_at"])
        return message
    message.extraction, message.note = data, ""
    message.save(update_fields=["extraction", "note", "updated_at"])
    build_draft(message)
    return message


def _sellable(store):
    return (store is not None and store.status == ActiveStatus.ACTIVE
            and store.kind not in orders.NOT_SOLD_FROM)


def _order_store(company_id, items):
    """The order's own store: the first item's usual store, else a finished-goods store, else any sellable one."""
    for item in items:
        if _sellable(item.default_warehouse):
            return item.default_warehouse
    stores = Warehouse.all_objects.filter(company_id=company_id, status=ActiveStatus.ACTIVE).exclude(
        kind__in=orders.NOT_SOLD_FROM)
    return stores.filter(kind=Warehouse.Kind.FINISHED).order_by("code").first() or stores.order_by("code").first()


def _delivery_date(text, today):
    try:
        day = datetime.date.fromisoformat((text or "").strip())
    except ValueError:
        return None
    return day if day >= today else None


@transaction.atomic
def build_draft(message, customer=None):
    """Make the draft order from the message's extraction (once a customer is known)."""
    data = message.extraction or {}
    own = settings_for(message.company_id)
    problems, unmatched = [], []
    if not data.get("is_order", True):
        problems.append("This does not look like an order.")
    found, how, problem = (customer, "chosen", "") if customer else matching.match_customer(
        message.company_id, message.sender, data)
    if problem:
        problems.append(problem)
    if data.get("confidence") == "low":
        problems.append("The AI was not sure about this order: check every line.")
    cache = matching._items(message.company_id)
    lines = []
    for index, line in enumerate(data.get("lines") or []):
        item, item_how = matching.match_item(message.company_id, line, cache)
        qty = matching.quantity(line.get("quantity"))
        if item is None or qty is None:
            unmatched.append({**line, "index": index,
                              "why": "No item found" if item is None else "No quantity"})
            continue
        uom, unit_problem = matching.match_unit(message.company_id, item, line.get("unit_text"))
        if unit_problem:
            problems.append(unit_problem)
        if item_how in ("ai", "partial"):
            problems.append(f"\"{line.get('product_text')}\" was taken as {item.code} {item.name}: check it.")
        lines.append((item, qty, uom, line))
    message.customer = found
    message.problems, message.unmatched = problems, unmatched
    message.status = Status.NEEDS_REVIEW
    if found is None or not lines or own.draft_user_id is None:
        if own.draft_user_id is None:
            problems.append("No login is set to enter AI drafts (AI assistant settings).")
        message.save()
        return message

    company_id = message.company_id
    actor = own.draft_user
    store = _order_store(company_id, [item for item, *_ in lines])
    if store is None:
        problems.append("No store goods are sold from was found.")
        message.save()
        return message
    today = local_today(found.company)
    order = orders.create_order(actor=actor, company_id=company_id, values={
        "customer": found, "warehouse": store, "order_date": today,
        "delivery_date": _delivery_date(data.get("requested_delivery_date"), today),
        "source": SalesOrder.Source.AI, "customer_ref": (data.get("customer_reference") or "")[:64],
        "delivery_address": (data.get("delivery_address") or "")[:500],
        "note": f"AI draft from {message.get_channel_display().lower()}: {message.subject or message.sender}"[:500]})
    for item, qty, uom, line in lines:
        line_store = item.default_warehouse if _sellable(item.default_warehouse) else store
        try:
            orders.add_line(actor=actor, company_id=company_id, order_id=order.pk, values={
                "item": item, "warehouse": line_store, "qty": qty, "uom": uom,
                "note": (line.get("note") or "")[:255]})
        except ValidationError as exc:
            problems.append(f"{item.code} left out: {' '.join(exc.messages)}")
            unmatched.append({**line, "why": " ".join(exc.messages)[:200]})
    _check(order, problems)
    message.sales_order = order
    message.save()
    return message


def _check(order, problems):
    """Stock, credit and repeats, as problems for the reviewer."""
    with use_company(order.company_id):
        available = orders.availability(order)
        lines = list(order.lines.select_related("item__base_uom", "warehouse"))
        for line in lines:
            free = available.get(line.pk)
            if free is not None and free < line.qty_base:
                problems.append(f"{line.item.code}: only {free.normalize():f} {line.item.base_uom.code} free in "
                                f"{line.warehouse.code}, {line.qty_base.normalize():f} ordered.")
        over, message = orders.credit_check(order)
        if over:
            problems.append(message)
        wanted = sorted((line.item_id, line.qty_base) for line in lines)
        for other in SalesOrder.objects.filter(customer=order.customer, order_date=order.order_date).exclude(
                pk=order.pk).exclude(status=SalesOrder.Status.CANCELLED):
            if sorted((l.item_id, l.qty_base) for l in other.lines.all()) == wanted:
                problems.append(f"It looks like a repeat of {other.number} (same customer, day and lines).")


# --------------------------------------------------------------------------
# The reviewer
# --------------------------------------------------------------------------

def get_message(actor, company_id, pk, code=VIEW):
    membership = require(actor, company_id, code)
    message = InboundMessage.all_objects.filter(company_id=company_id, pk=pk).first()
    if message is None:
        raise ValidationError("That message was not found.")
    return membership, message


def _open(message):
    if message.status in (Status.CONVERTED, Status.IGNORED):
        raise ValidationError(f"This message is {message.get_status_display().lower()}.")


@transaction.atomic
def choose_customer(*, actor, company_id, message_id, customer):
    membership, message = get_message(actor, company_id, message_id, "sales.order")
    _open(message)
    if message.sales_order_id:
        raise ValidationError("The draft order exists: change its customer there.")
    if customer is None or customer.company_id != company_id:
        raise ValidationError({"customer": "Choose the customer."})
    if not message.extraction:
        raise ValidationError("The AI has not read this message yet.")
    build_draft(message, customer=customer)
    _audit(actor, membership, "ai.message.customer_chosen", message, {"customer": customer.code})
    return message


@transaction.atomic
def match_line(*, actor, company_id, message_id, index, item, qty, uom, remember=True):
    """Put an unmatched line on the draft as ``item``; remember the words as an alias."""
    membership, message = get_message(actor, company_id, message_id, "sales.order")
    _open(message)
    if not message.sales_order_id:
        raise ValidationError("Choose the customer first: then the draft order is made.")
    line = next((l for l in message.unmatched if l.get("index") == index), None)
    if line is None:
        raise ValidationError("That line is matched already.")
    order = SalesOrder.all_objects.get(pk=message.sales_order_id)
    store = item.default_warehouse if _sellable(item.default_warehouse) else order.warehouse
    orders.add_line(actor=actor, company_id=company_id, order_id=order.pk,
                    values={"item": item, "warehouse": store, "qty": qty, "uom": uom})
    words = matching.normalize(line.get("product_text"))
    if remember and words and len(words) <= 255:
        with use_company(company_id):
            alias = ItemAlias.objects.filter(normalized=words).first()
            if alias is None:
                ItemAlias.objects.create(company=membership.company, item=item, alias=line["product_text"][:255],
                                         normalized=words, created_by=actor, updated_by=actor)
            elif alias.item_id != item.pk:
                alias.item, alias.updated_by = item, actor
                alias.save(update_fields=["item", "updated_by", "updated_at"])
    message.unmatched = [l for l in message.unmatched if l.get("index") != index]
    message.save(update_fields=["unmatched", "updated_at"])
    _audit(actor, membership, "ai.message.line_matched", message,
           {"words": line.get("product_text"), "item": item.code, "remembered": remember})
    return message


@transaction.atomic
def confirm(*, actor, company_id, message_id, credit_note=""):
    """Confirm the draft (``sales.approve``, not the drafting login): stock is
    reserved, and the managers get the order summary."""
    from ai_assistant.summaries import send_order_summary

    membership, message = get_message(actor, company_id, message_id)
    _open(message)
    if not message.sales_order_id:
        raise ValidationError("There is no draft order to confirm yet.")
    if message.unmatched:
        raise ValidationError("Some lines are not matched yet: match each one or leave it out.")
    order = orders.confirm(actor=actor, company_id=company_id, order_id=message.sales_order_id,
                           credit_note=credit_note)
    message.status, message.reviewed_by, message.reviewed_at = Status.CONVERTED, actor, timezone.now()
    message.save(update_fields=["status", "reviewed_by", "reviewed_at", "updated_at"])
    _audit(actor, membership, "ai.message.confirmed", message, {"order": order.number})
    try:
        sent = send_order_summary(order)
    except ValidationError as exc:
        sent = []
        message.note = f"Summary not emailed: {' '.join(exc.messages)}"[:500]
        message.save(update_fields=["note", "updated_at"])
    order.summary_sent_to = sent
    return order


@transaction.atomic
def skip_line(*, actor, company_id, message_id, index):
    """Leave an unmatched line out (it is not on the order)."""
    membership, message = get_message(actor, company_id, message_id, "sales.order")
    _open(message)
    message.unmatched = [l for l in message.unmatched if l.get("index") != index]
    message.save(update_fields=["unmatched", "updated_at"])
    _audit(actor, membership, "ai.message.line_skipped", message, {"index": index})
    return message


@transaction.atomic
def ignore(*, actor, company_id, message_id, reason):
    membership, message = get_message(actor, company_id, message_id, "sales.order")
    _open(message)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is ignored."})
    if message.sales_order_id:
        order = SalesOrder.all_objects.get(pk=message.sales_order_id)
        if order.status == SalesOrder.Status.DRAFT:
            orders.cancel(actor=actor, company_id=company_id, order_id=order.pk, note=f"AI message ignored: {reason}")
    message.status, message.note = Status.IGNORED, reason[:500]
    message.reviewed_by, message.reviewed_at = actor, timezone.now()
    message.save()
    _audit(actor, membership, "ai.message.ignored", message, {"reason": reason})
    return message


def retry(*, actor, company_id, message_id):
    membership, message = get_message(actor, company_id, message_id, "sales.order")
    if message.status not in (Status.FAILED, Status.WAITING, Status.NEW):
        raise ValidationError("Only a message that waits or failed is read again.")
    process(message)
    _audit(actor, membership, "ai.message.retried", message, {"status": message.status})
    return message


def paste(*, actor, company_id, body, sender=""):
    """A person pastes an order (WhatsApp, a phone note): saved and read straight away."""
    membership = require(actor, company_id, "sales.order")
    message = receive(company_id=company_id, channel=InboundMessage.Channel.PASTE, body=body, sender=sender,
                      subject="Pasted order", actor=actor)
    _audit(actor, membership, "ai.message.pasted", message, {"sender": sender})
    return process(message)


def require_settings_keeper(actor, company_id):
    from inventory.access import require_module

    membership = require_module(actor, company_id)
    if membership.role not in ("owner", "company_admin"):
        raise PermissionDenied("The AI assistant is set up by the owner or company administrator.")
    return membership


def draft_lines(message):
    if not message.sales_order_id:
        return []
    return list(SalesOrderLine.all_objects.filter(order_id=message.sales_order_id).select_related(
        "item__base_uom", "uom", "warehouse").order_by("pk"))
