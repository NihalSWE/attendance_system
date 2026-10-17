"""Price lists and sales orders (Phase 6).

**Price list**: prices per item and unit, from a quantity, valid between two
dates; a customer uses the list named on their record ("Distributor"). A new
order line without a price takes the list's price (the highest "from
quantity" the line reaches), else the item's sale price.

**Sales order**: Draft (``sales.order`` in the store's branch) -> **Confirmed**
(``sales.approve``; not by the person who entered it, unless owner/admin) ->
Partly delivered -> Delivered; Closed when the rest will not go; Cancelled
while nothing is delivered.

**Stores** (Phase 8): each line goes from its own store - the order's unless
it names another (juice from finished goods, sugar from the raw material
store, on one order). A line from another branch's store needs
``sales.order`` there too.

**Reservation**: confirming reserves usable stock (released, unexpired
batches in the line's store, first expiry first) for every line. Reserved stock
cannot be issued, used or transferred - only delivered. What could not be
reserved is shown as short; "Reserve again" tries later (after production).
Delivering releases what it delivers; cancelling or closing releases the rest.

**Credit limit**: a customer with a limit (0 = cash only, not checked) whose
unpaid invoices plus open orders plus this order go over it is confirmed
only with a reason - a warning, not a block.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q, Sum

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, posting
from inventory.doc_common import (
    _writable,
    audit,
    check_own_approval,
    check_qty,
    get,
    now,
    plain,
    require,
    unit_factor,
)
from inventory.masters import units_for
from sales.models import (
    CENT,
    PriceList,
    PriceListLine,
    Reservation,
    SalesInvoice,
    SalesOrder,
    SalesOrderLine,
)

FOUR = Decimal("0.0001")
Status = SalesOrder.Status
FIELDS = ("customer", "warehouse", "order_date", "delivery_date", "source", "customer_ref", "delivery_address",
          "note")
LINE_FIELDS = ("item", "warehouse", "qty", "uom", "unit_price", "discount_pct", "vat_rate", "note")
#: Stores whose stock is not for sale.
NOT_SOLD_FROM = ("quarantine", "rejected", "transit")


# --------------------------------------------------------------------------
# Price lists
# --------------------------------------------------------------------------

@transaction.atomic
def create_price_list(*, actor, company_id, values):
    membership = require(actor, company_id, "sales.order")
    values = _writable(values, ("name", "valid_from", "valid_to", "note"))
    if not values.get("name"):
        raise ValidationError({"name": "Give the name customers are given (e.g. Distributor)."})
    if values.get("valid_from") and values.get("valid_to") and values["valid_to"] < values["valid_from"]:
        raise ValidationError({"valid_to": "Ends before it starts?"})
    with use_company(company_id):
        row = PriceList(company=membership.company, created_by=actor, updated_by=actor, **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "sales.price_list.created", row, after={"name": row.name})
    return row


@transaction.atomic
def set_price(*, actor, company_id, price_list_id, values):
    """Add or change one price (item + unit + from quantity)."""
    membership = require(actor, company_id, "sales.order")
    price_list = get(PriceList, company_id, price_list_id, "Price list")
    values = _writable(values, ("item", "uom", "price", "min_qty"))
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    unit_factor(item, values.get("uom"))
    if values.get("price") is None or values["price"] < 0:
        raise ValidationError({"price": "Give the price."})
    values["min_qty"] = values.get("min_qty") or Decimal("0")
    with use_company(company_id):
        line, created = PriceListLine.objects.update_or_create(
            price_list=price_list, item=item, uom=values["uom"], min_qty=values["min_qty"],
            defaults={"price": values["price"], "company": membership.company})
        audit(actor, membership, "sales.price_list.price_set", price_list,
              after={"item": item.code, "uom": values["uom"].code, "min_qty": plain(values["min_qty"]),
                     "price": plain(values["price"]), "new": created})
    return line


@transaction.atomic
def remove_price(*, actor, company_id, price_list_id, line_id):
    membership = require(actor, company_id, "sales.order")
    price_list = get(PriceList, company_id, price_list_id, "Price list")
    with use_company(company_id):
        line = price_list.lines.filter(pk=line_id).select_related("item").first()
        if line is None:
            raise ValidationError("That price is not on this list.")
        audit(actor, membership, "sales.price_list.price_removed", price_list,
              before={"item": line.item.code, "price": plain(line.price)})
        line.delete()


def price_for(customer, item, uom, qty, day):
    """The customer's list price for ``qty`` ``uom`` of ``item`` on ``day``, else
    the item's sale price (per base unit x the unit's size). None if neither."""
    if customer.price_list_name:
        lines = PriceListLine.all_objects.filter(
            price_list__company_id=customer.company_id, price_list__name=customer.price_list_name,
            price_list__status=ActiveStatus.ACTIVE, item=item, uom=uom, min_qty__lte=qty,
        ).filter(Q(price_list__valid_from__isnull=True) | Q(price_list__valid_from__lte=day),
                 Q(price_list__valid_to__isnull=True) | Q(price_list__valid_to__gte=day)).order_by("-min_qty")
        line = lines.first()
        if line is not None:
            return line.price
    if item.sale_price is not None:
        return (item.sale_price * units_for(item).get(uom.pk, Decimal("1"))).quantize(FOUR)
    return None


# --------------------------------------------------------------------------
# Sales orders
# --------------------------------------------------------------------------

def check_store(warehouse, company_id):
    """A store goods may be sold from: active, this company's, and not quarantine, rejected or transit."""
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE or warehouse.company_id != company_id:
        raise ValidationError({"warehouse": "Choose an active store."})
    if warehouse.kind in NOT_SOLD_FROM:
        raise ValidationError({"warehouse": f"{warehouse.code} is not a store goods are sold from."})


def get_order(*, actor, company_id, pk, code="sales.order"):
    order = get(SalesOrder, company_id, pk, "Sales order")
    return require(actor, company_id, code, order.warehouse.branch_id), order


def _draft(order):
    if order.status != Status.DRAFT:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}; only a draft can change.")


def totals(order):
    lines = list(SalesOrderLine.all_objects.filter(order=order))
    amount = sum((line.amount for line in lines), Decimal("0"))
    vat = sum((line.vat_amount for line in lines), Decimal("0"))
    return {"amount": amount, "vat": vat, "total": amount + vat}


@transaction.atomic
def create_order(*, actor, company_id, values):
    values = _writable(values, FIELDS)
    customer, warehouse = values.get("customer"), values.get("warehouse")
    if customer is None or customer.status != ActiveStatus.ACTIVE:
        raise ValidationError({"customer": "Choose an active customer."})
    check_store(warehouse, company_id)
    if values.get("order_date") is None:
        raise ValidationError({"order_date": "Give the date."})
    values.setdefault("source", SalesOrder.Source.MANUAL)
    values["delivery_address"] = values.get("delivery_address") or customer.address
    membership = require(actor, company_id, "sales.order", warehouse.branch_id)
    with use_company(company_id):
        order = SalesOrder(company=membership.company, created_by=actor, updated_by=actor,
                           number=posting.next_number(company_id, SalesOrder.DOC_TYPE, SalesOrder.PREFIX,
                                                      values["order_date"].year), **values)
        order.full_clean()
        order.save()
        audit(actor, membership, "sales.order.created", order,
              after={"number": order.number, "customer": customer.code, "source": order.source})
    return order


@transaction.atomic
def add_line(*, actor, company_id, order_id, values):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    _draft(order)
    values = _writable(values, LINE_FIELDS)
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    # Each line goes from its own store (the order's unless it names another):
    # juice from finished goods and sugar from the raw material store, one order.
    warehouse = values["warehouse"] = values.get("warehouse") or order.warehouse
    check_store(warehouse, company_id)
    if warehouse.pk != order.warehouse_id:
        require(actor, company_id, "sales.order", warehouse.branch_id)
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor)
    if values.get("unit_price") is None:
        values["unit_price"] = price_for(order.customer, item, values["uom"], values["qty"], order.order_date)
        if values["unit_price"] is None:
            raise ValidationError({"unit_price": f"{item.code} has no price for this customer: give it."})
    values["discount_pct"] = values.get("discount_pct") or Decimal("0")
    if values.get("vat_rate") is None:
        values["vat_rate"] = item.vat_rate or Decimal("0")
    with use_company(company_id):
        line = SalesOrderLine(company=membership.company, order=order, factor=factor, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_line(*, actor, company_id, order_id, line_id):
    _, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    _draft(order)
    with use_company(company_id):
        if not order.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this order.")


def outstanding(customer, excluding=None):
    """What the customer owes or has on order: unpaid invoices + undelivered open orders."""
    unpaid = SalesInvoice.all_objects.filter(customer=customer, status=SalesInvoice.Status.ISSUED).aggregate(
        total=Sum("total"))["total"] or Decimal("0")
    open_value = Decimal("0")
    orders = SalesOrder.all_objects.filter(customer=customer, status__in=[Status.CONFIRMED, Status.PARTLY_DELIVERED])
    if excluding is not None:
        orders = orders.exclude(pk=excluding.pk)
    for line in SalesOrderLine.all_objects.filter(order__in=orders):
        if line.qty_base:
            share = line.qty_open_base / line.qty_base
            open_value += ((line.amount + line.vat_amount) * share).quantize(CENT)
    return unpaid + open_value


def credit_check(order):
    """``(over, message)``: whether confirming goes over the customer's credit limit."""
    customer = order.customer
    if not customer.credit_limit:
        return False, ""
    owed = outstanding(customer, excluding=order)
    total = totals(order)["total"]
    if owed + total > customer.credit_limit:
        return True, (f"{customer.name}: {owed:,.2f} unpaid and on order + this order {total:,.2f} = "
                      f"{owed + total:,.2f}, over the credit limit of {customer.credit_limit:,.2f}.")
    return False, ""


def _reserved_for_others(row, line):
    return Reservation.all_objects.filter(balance=row).exclude(order_line=line).aggregate(
        q=Sum("qty_base"))["q"] or Decimal("0")


def reserve_order(order, day=None):
    """Top up every open line's reservation to what is still to deliver, first
    expiry first. Returns ``[shortage messages]``. Call inside a transaction."""
    day = day or order.order_date
    short = []
    for line in SalesOrderLine.all_objects.filter(order=order).select_related("item__base_uom", "warehouse"):
        want = line.qty_open_base - line.qty_reserved_base
        if want <= 0:
            continue
        changes = []
        for row in fefo.usable_rows(order.company_id, line.item, line.warehouse, day):
            if want <= 0:
                break
            take = min(row.available, want)
            if take <= 0:
                continue
            changes.append((row.pk, take))
            want -= take
        if changes:
            posting.change_reserved(order.company_id, changes)
            for balance_id, qty in changes:
                reservation, _ = Reservation.all_objects.get_or_create(
                    company_id=order.company_id, order_line=line, balance_id=balance_id,
                    defaults={"qty_base": Decimal("0")})
                reservation.qty_base += qty
                reservation.save(update_fields=["qty_base"])
            line.qty_reserved_base += sum((qty for _b, qty in changes), Decimal("0"))
            line.save(update_fields=["qty_reserved_base"])
        if want > 0:
            short.append(f"{line.item.code} short by {want.normalize():f} {line.item.base_uom.code}"
                         + (f" in {line.warehouse.code}" if line.warehouse_id != order.warehouse_id else ""))
    return short


def release_line(line, qty_base=None, prefer_balance_ids=()):
    """Release up to ``qty_base`` (all when None) of a line's reservation,
    rows named in ``prefer_balance_ids`` first. Call inside a transaction."""
    reservations = list(Reservation.all_objects.filter(order_line=line).order_by("pk"))
    reservations.sort(key=lambda r: r.balance_id not in prefer_balance_ids)
    left = line.qty_reserved_base if qty_base is None else min(qty_base, line.qty_reserved_base)
    changes = []
    for reservation in reservations:
        if left <= 0:
            break
        take = min(reservation.qty_base, left)
        changes.append((reservation.balance_id, -take))
        reservation.qty_base -= take
        left -= take
        if reservation.qty_base <= 0:
            reservation.delete()
        else:
            reservation.save(update_fields=["qty_base"])
    if changes:
        posting.change_reserved(line.company_id, changes)
        line.qty_reserved_base -= sum((-qty for _b, qty in changes), Decimal("0"))
        line.save(update_fields=["qty_reserved_base"])


@transaction.atomic
def confirm(*, actor, company_id, order_id, credit_note=""):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id, code="sales.approve")
    with use_company(company_id):
        order = SalesOrder.objects.select_for_update(of=("self",)).select_related("customer", "warehouse").get(
            pk=order.pk)
        _draft(order)
        if not order.lines.exists():
            raise ValidationError("Add what is ordered before confirming.")
        check_own_approval(membership, actor, order.created_by_id)
        over, message = credit_check(order)
        credit_note = (credit_note or "").strip()
        if over and not credit_note:
            raise ValidationError({"credit_note": message + " Say why it is confirmed anyway."})
        short = reserve_order(order)
        order.status, order.confirmed_at, order.confirmed_by = Status.CONFIRMED, now(), actor
        order.credit_note, order.updated_by = credit_note[:255] if over else "", actor
        order.save()
        audit(actor, membership, "sales.order.confirmed", order, before={"status": "draft"},
              after={"status": "confirmed", "total": plain(totals(order)["total"]), "short": short,
                     "credit": message if over else ""})
    order.shortages = short
    return order


@transaction.atomic
def reserve_again(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if not order.deliverable:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}.")
    with use_company(company_id):
        short = reserve_order(order, day=max(order.order_date, datetime.date.today()))
        audit(actor, membership, "sales.order.reserved_again", order, after={"short": short})
    order.shortages = short
    return order


@transaction.atomic
def cancel(*, actor, company_id, order_id, note=""):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status not in (Status.DRAFT, Status.CONFIRMED):
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: close it instead.")
    with use_company(company_id):
        if order.deliveries.exclude(status__in=["cancelled", "reversed"]).exists():
            raise ValidationError("A delivery was made or drafted: close the order instead.")
        for line in order.lines.all():
            release_line(line)
        before, order.status = order.status, Status.CANCELLED
        order.closed_note, order.updated_by = (note or "")[:255], actor
        order.save()
        audit(actor, membership, "sales.order.cancelled", order, before={"status": before},
              after={"status": "cancelled", "note": note})
    return order


@transaction.atomic
def close(*, actor, company_id, order_id, note):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status != Status.PARTLY_DELIVERED:
        raise ValidationError("Only a partly delivered order is closed.")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Say why the rest will not go."})
    with use_company(company_id):
        for line in order.lines.all():
            release_line(line)
        order.status, order.closed_note, order.updated_by = Status.CLOSED, note[:255], actor
        order.save()
        audit(actor, membership, "sales.order.closed", order, before={"status": "partly_delivered"},
              after={"status": "closed", "note": note})
    return order


def refresh_status(order):
    if order.status not in (Status.CONFIRMED, Status.PARTLY_DELIVERED, Status.DELIVERED):
        return
    lines = list(SalesOrderLine.all_objects.filter(order=order))
    if all(line.qty_open_base <= 0 for line in lines):
        status = Status.DELIVERED
    elif any(line.qty_delivered_base > 0 for line in lines):
        status = Status.PARTLY_DELIVERED
    else:
        status = Status.CONFIRMED
    if status != order.status:
        order.status = status
        order.save(update_fields=["status", "updated_at"])


def availability(order):
    """``{line_id: usable stock not reserved for other orders}`` for a draft's lines."""
    out = {}
    for line in SalesOrderLine.all_objects.filter(order=order).select_related("item", "warehouse"):
        rows = fefo.usable_rows(order.company_id, line.item, line.warehouse, order.order_date)
        out[line.pk] = sum((r.available for r in rows), Decimal("0")) + line.qty_reserved_base
    return out

