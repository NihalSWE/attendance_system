"""Store & Inventory reports (Phase 7): what each one shows, who opens it,
and the rows it is built from.

Every report is one page with the project's server-side table and the same
view's ``?format=xlsx|pdf`` download (``inventory.report_views``). Rows hold
plain values - numbers as Decimals, dates as dates - so Excel can sum and sort
them; the page and the PDF format them by the column's ``kind``.

Stock quantities and values come from the stock ledger (``inventory.valuation``),
so every report agrees with the valuation at the same date. Values and costs
show only to whoever sees stock costs in every store the report covers
("See stock costs and values"); the valuation report needs that permission
to open at all.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Sum
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES
from inventory import valuation
from inventory.models import Item, ItemKind, StockBalance, StockMove
from inventory.posting import UNUSABLE_STORES, settings_for
from reports.builders import Column, Result

ZERO = Decimal("0")
CENT = Decimal("0.01")
HUNDRED = Decimal("100")
FAR = date(9999, 12, 31)

AS_OF, RANGE, NOW = "as_of", "range", "now"
STOCK, PURCHASE, PRODUCTION, SALES = "Stock", "Purchase", "Production", "Sales"

STOCK_CODES = "inventory.view"
PURCHASE_CODES = ("purchase.order", "purchase.approve", "purchase.receive")
GRN_CODES = ("purchase.receive", "purchase.order", "purchase.approve", "purchase.qc", "inventory.view")
ISSUE_CODES = ("inventory.view", "inventory.issue", "inventory.issue_approve")
PRODUCTION_CODES = ("production.plan", "production.execute", "inventory.view")
SALES_CODES = ("sales.order", "sales.approve", "sales.invoice")


@dataclass
class Ctx:
    company_id: int
    company: object
    actor: object
    f: object                       # inventory.report_views.Filters
    today: date
    stores: list                    # the stores the report covers (after the store filter)
    branches: object                # ALL_BRANCHES or the set of branch ids it covers
    costs: bool                     # values and costs may show
    item_ids: object = None         # None, or the items the item/category/type filters keep
    notes: list = field(default_factory=list)

    @property
    def store_ids(self):
        return [store.pk for store in self.stores]

    def in_branches(self, queryset, path):
        if self.branches is ALL_BRANCHES:
            return queryset
        return queryset.filter(**{f"{path}__in": self.branches})


def _items(ids):
    return {item.pk: item for item in Item.all_objects.filter(pk__in=ids).select_related(
        "base_uom", "category")}


def _pct(part, whole):
    if not whole:
        return None
    return (part * HUNDRED / whole).quantize(Decimal("0.1"))


def _cost(ctx, *columns):
    """The cost columns, when they may show."""
    return list(columns) if ctx.costs else []


def _money_total(values):
    return sum(values, ZERO).quantize(CENT)


def _money_text(value):
    return f"{value.quantize(CENT):,}"


def _value_note(ctx):
    if not ctx.costs:
        ctx.notes.append("Values are hidden: you do not see stock costs in every store this covers. "
                         "Pick a store whose costs you see.")


def _done(ctx, result):
    if ctx.notes:
        result.note = " ".join(([result.note] if result.note else []) + ctx.notes)
    return result


# --------------------------------------------------------------------------
# Stock
# --------------------------------------------------------------------------

def stock_on_hand(ctx):
    """Now: per item and store, on hand, reserved for sales orders, available."""
    _value_note(ctx)
    rows_q = StockBalance.all_objects.filter(company_id=ctx.company_id, warehouse_id__in=ctx.store_ids)
    if ctx.item_ids is not None:
        rows_q = rows_q.filter(item_id__in=ctx.item_ids)
    held = defaultdict(lambda: [ZERO, ZERO])
    for item_id, store_id, on_hand, reserved in rows_q.exclude(qty_on_hand=0).values_list(
            "item_id", "warehouse_id", "qty_on_hand", "qty_reserved"):
        held[(item_id, store_id)][0] += on_hand
        held[(item_id, store_id)][1] += reserved
    values = valuation.at(ctx.company_id, FAR, store_ids=ctx.store_ids, item_ids=ctx.item_ids,
                          by_store=True) if ctx.costs else {}
    items, stores = _items({i for i, _s in held}), {s.pk: s for s in ctx.stores}
    rows, total = [], []
    for (item_id, store_id), (on_hand, reserved) in sorted(
            held.items(), key=lambda kv: (items[kv[0][0]].code, stores[kv[0][1]].code)):
        item, store = items[item_id], stores[store_id]
        usable = store.kind not in UNUSABLE_STORES
        row = [item.code, item.name, item.get_kind_display(), store.code, item.base_uom.code,
               on_hand, reserved, on_hand - reserved if usable else ZERO]
        if ctx.costs:
            value = values.get((item_id, store_id), (ZERO, ZERO))[1].quantize(CENT)
            row += [(value / on_hand).quantize(Decimal("0.0001")) if on_hand else None, value]
            total.append(value)
        rows.append(row)
    summary = [("Items", len({i for i, _s in held})), ("Stock rows", len(rows))]
    if ctx.costs:
        summary.append(("Value", _money_text(_money_total(total))))
    return _done(ctx, Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Type"), Column("Store"), Column("Unit"),
                 Column("On hand", True, kind="qty"), Column("Reserved", True, kind="qty"),
                 Column("Available", True, kind="qty"),
                 *_cost(ctx, Column("Average cost", True, kind="cost"), Column("Value", True, kind="money"))],
        rows=rows, summary=summary,
        note="Available is what is not reserved for sales orders; stock in quarantine, rejected and "
             "transit stores is never available.",
        empty="No stock in these stores."))


def _document_label(move, numbers):
    number = numbers.get((move.document_type, move.document_id))
    return number or f"{move.document_type.replace('_', ' ')} #{move.document_id}"


def item_card(ctx):
    """One item's ledger over the dates: every move and the running balance."""
    f = ctx.f
    if not f.item:
        return Result(columns=[Column("Date")], rows=[], empty="Pick an item to see its card.")
    _value_note(ctx)
    item = Item.all_objects.select_related("base_uom").get(company_id=ctx.company_id, pk=int(f.item))
    opening = valuation.at(ctx.company_id, f.date_from - timedelta(days=1), store_ids=ctx.store_ids,
                           item_ids=[item.pk]).get((item.pk, None), (ZERO, ZERO))
    moves = list(valuation.moves(ctx.company_id, store_ids=ctx.store_ids, item_ids=[item.pk])
                 .filter(work_date__gte=f.date_from, work_date__lte=f.date_to)
                 .select_related("warehouse", "batch").order_by("work_date", "posted_at", "pk"))
    numbers = valuation.document_numbers((m.document_type, m.document_id) for m in moves)
    balance, balance_value = opening
    rows = [[f.date_from, "Opening balance", "", "", "", None, None, balance,
             *([None, None, balance_value.quantize(CENT)] if ctx.costs else [])]]
    took_in = took_out = ZERO
    for move in moves:
        balance += move.qty
        balance_value += move.value
        took_in += max(move.qty, ZERO)
        took_out += max(-move.qty, ZERO)
        rows.append([move.work_date, _document_label(move, numbers), move.get_move_type_display(),
                     move.warehouse.code, move.batch.batch_no if move.batch else "",
                     move.qty if move.qty > 0 else None, -move.qty if move.qty < 0 else None, balance,
                     *([move.unit_cost, move.value.quantize(CENT), balance_value.quantize(CENT)]
                       if ctx.costs else [])])
    unit = item.base_uom.code
    return _done(ctx, Result(
        columns=[Column("Date", kind="date"), Column("Document", weight=1.4), Column("Kind", weight=1.4),
                 Column("Store"), Column("Batch"), Column(f"In ({unit})", True, kind="qty"),
                 Column(f"Out ({unit})", True, kind="qty"), Column("Balance", True, kind="qty"),
                 *_cost(ctx, Column("Unit cost", True, kind="cost"), Column("Value", True, kind="money"),
                        Column("Balance value", True, kind="money"))],
        rows=rows,
        summary=[("Item", f"{item.code} {item.name}"), ("Opening", f"{opening[0].normalize():,f} {unit}"),
                 ("In", f"{took_in.normalize():,f}"), ("Out", f"{took_out.normalize():,f}"),
                 ("Closing", f"{balance.normalize():,f} {unit}")],
        empty="No moves."))


def movement_summary(ctx):
    """Per item over the dates: opening, in, out and closing."""
    f = ctx.f
    _value_note(ctx)
    found = valuation.movement(ctx.company_id, f.date_from, f.date_to, store_ids=ctx.store_ids,
                               item_ids=ctx.item_ids)
    found = {k: v for k, v in found.items() if any(v[key] for key in ("open_q", "in_q", "out_q", "close_q"))}
    items = _items(found)
    rows, totals = [], defaultdict(lambda: ZERO)
    for item_id, line in sorted(found.items(), key=lambda kv: items[kv[0]].code):
        item = items[item_id]
        row = [item.code, item.name, item.base_uom.code, line["open_q"], line["in_q"], line["out_q"],
               line["close_q"]]
        if ctx.costs:
            row += [line[key].quantize(CENT) for key in ("open_v", "in_v", "out_v", "close_v")]
            for key in ("open_v", "in_v", "out_v", "close_v"):
                totals[key] += line[key]
        rows.append(row)
    summary = [("Items", len(rows))]
    if ctx.costs:
        summary += [("Opening value", _money_text(totals["open_v"])), ("In", _money_text(totals["in_v"])),
                    ("Out", _money_text(totals["out_v"])), ("Closing value", _money_text(totals["close_v"]))]
    return _done(ctx, Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Unit"),
                 Column("Opening", True, kind="qty"), Column("In", True, kind="qty"),
                 Column("Out", True, kind="qty"), Column("Closing", True, kind="qty"),
                 *_cost(ctx, *(Column(label, True, kind="money") for label in
                               ("Opening value", "In value", "Out value", "Closing value")))],
        rows=rows, summary=summary,
        note="A reversal counts the way it moved: a reversed receipt is stock going out.",
        empty="Nothing moved and nothing was held in these dates."))


def valuation_at(ctx):
    """Quantity and value of every item at the close of a day, from the ledger."""
    f = ctx.f
    by_store = f.view == "store"
    held = valuation.at(ctx.company_id, f.as_of, store_ids=ctx.store_ids, item_ids=ctx.item_ids,
                        by_store=by_store)
    items, stores = _items({i for i, _s in held}), {s.pk: s for s in ctx.stores}
    rows, by_kind = [], defaultdict(lambda: ZERO)
    for (item_id, store_id), (qty, value) in sorted(
            held.items(), key=lambda kv: (items[kv[0][0]].code, stores[kv[0][1]].code if kv[0][1] else "")):
        item = items[item_id]
        rows.append([item.code, item.name, item.get_kind_display(),
                     item.category.name if item.category_id else "",
                     *([stores[store_id].code] if by_store else []), item.base_uom.code, qty,
                     (value / qty).quantize(Decimal("0.0001")) if qty else None, value.quantize(CENT)])
        by_kind[item.get_kind_display()] += value
    if f.as_of >= ctx.today:
        later = valuation.moves(ctx.company_id, store_ids=ctx.store_ids, item_ids=ctx.item_ids).filter(
            work_date__gt=f.as_of)
        if later.exists():
            last = later.order_by("-work_date").values_list("work_date", flat=True).first()
            ctx.notes.append(f"{later.count()} stock move(s) are dated after today (up to {last:%d %b %Y}) "
                             "and are not in this valuation.")
    total = sum(by_kind.values(), ZERO)
    summary = [("Value", _money_text(total)), ("Items", len({i for i, _s in held}))]
    summary += [(kind, _money_text(value)) for kind, value in sorted(by_kind.items())]
    return _done(ctx, Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Type"), Column("Category"),
                 *([Column("Store")] if by_store else []), Column("Unit"),
                 Column("Quantity", True, kind="qty"), Column("Average cost", True, kind="cost"),
                 Column("Value", True, kind="money")],
        rows=rows, summary=summary,
        note=f"Every stock move dated on or before {f.as_of:%d %b %Y}, at the cost it was posted at "
             "(moving weighted average).",
        empty="No stock on that day in these stores."))


def batch_expiry(ctx):
    """Batches in stock with their expiry: expired and expiring first."""
    f = ctx.f
    alert = settings_for(ctx.company_id).expiry_alert_days
    rows_q = StockBalance.all_objects.filter(company_id=ctx.company_id, warehouse_id__in=ctx.store_ids,
                                             batch__isnull=False, qty_on_hand__gt=0)
    if ctx.item_ids is not None:
        rows_q = rows_q.filter(item_id__in=ctx.item_ids)
    if f.days:
        rows_q = rows_q.filter(batch__expiry_date__lte=ctx.today + timedelta(days=f.days))
    held = defaultdict(lambda: ZERO)
    for batch_id, store_id, qty in rows_q.values_list("batch_id", "warehouse_id", "qty_on_hand"):
        held[(batch_id, store_id)] += qty
    from inventory.models import Batch

    batches = {b.pk: b for b in Batch.all_objects.filter(pk__in={b for b, _s in held}).select_related(
        "item__base_uom")}
    stores = {s.pk: s for s in ctx.stores}
    counts = defaultdict(int)
    rows = []
    for (batch_id, store_id), qty in sorted(held.items(), key=lambda kv: (
            batches[kv[0][0]].expiry_date or FAR, batches[kv[0][0]].item.code)):
        batch = batches[batch_id]
        item = batch.item
        days_left = (batch.expiry_date - ctx.today).days if batch.expiry_date else None
        if days_left is not None and days_left < 0:
            status = "Expired"
        elif days_left is not None and days_left <= alert:
            status = "Expiring soon"
        elif batch.status != batch.Status.RELEASED:
            status = batch.get_status_display()
        else:
            status = "Good"
        counts[status] += 1
        rows.append([item.code, item.name, batch.batch_no, stores[store_id].code, batch.mfg_date,
                     batch.expiry_date, days_left, status, qty, item.base_uom.code])
    return Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Batch"), Column("Store"),
                 Column("Made", kind="date"), Column("Expires", kind="date"), Column("Days left", True),
                 Column("Status", kind="status"), Column("Quantity", True, kind="qty"), Column("Unit")],
        rows=rows,
        summary=[("Batches", len(rows)), ("Expired", counts["Expired"]),
                 ("Expiring soon", counts["Expiring soon"])],
        note=f"Expiring soon: within {alert} days (Inventory settings).",
        empty="No batches in stock" + (f" expiring within {f.days} days." if f.days else "."))


def aging_report(ctx):
    """How long the stock in each store has been there, first in first out."""
    f = ctx.f
    _value_note(ctx)
    found = valuation.aging(ctx.company_id, f.as_of, store_ids=ctx.store_ids, item_ids=ctx.item_ids)
    items, stores = _items({i for i, _s in found}), {s.pk: s for s in ctx.stores}
    rows, bracket_values = [], [ZERO] * len(valuation.AGE_BUCKETS)
    for (item_id, store_id), line in sorted(found.items(), key=lambda kv: (
            items[kv[0][0]].code, stores[kv[0][1]].code)):
        item = items[item_id]
        row = [item.code, item.name, stores[store_id].code, item.base_uom.code, line["qty"],
               *line["buckets"], line["oldest"]]
        if ctx.costs:
            row.append(line["value"].quantize(CENT))
            for index, qty in enumerate(line["buckets"]):
                bracket_values[index] += line["value"] * qty / line["qty"]
        rows.append(row)
    summary = [("Stock rows", len(rows))]
    if ctx.costs:
        summary += [(label, _money_text(value))
                    for (label, _upto), value in zip(valuation.AGE_BUCKETS, bracket_values)]
    return _done(ctx, Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Store"), Column("Unit"),
                 Column("On hand", True, kind="qty"),
                 *(Column(label, True, kind="qty") for label, _upto in valuation.AGE_BUCKETS),
                 Column("Oldest in", kind="date"), *_cost(ctx, Column("Value", True, kind="money"))],
        rows=rows, summary=summary,
        note=f"Age on {f.as_of:%d %b %Y}, first in first out: what a store holds is what came into it "
             "last. A transfer starts the clock again in the new store.",
        empty="No stock on that day in these stores."))


def slow_moving(ctx):
    """Items in stock not issued, used or sold for a while (or ever)."""
    f = ctx.f
    _value_note(ctx)
    days = f.days or settings_for(ctx.company_id).slow_moving_days
    held = {item_id: qv for (item_id, _s), qv in valuation.at(
        ctx.company_id, FAR, store_ids=ctx.store_ids, item_ids=ctx.item_ids).items() if qv[0] > 0}
    used = valuation.last_used(ctx.company_id, store_ids=ctx.store_ids, item_ids=list(held))
    received = valuation.last_received(ctx.company_id, store_ids=ctx.store_ids, item_ids=list(held))
    items = _items(held)
    rows, tied = [], []
    for item_id, (qty, value) in sorted(held.items(), key=lambda kv: items[kv[0]].code):
        last = used.get(item_id)
        since = (ctx.today - last).days if last else None
        if since is not None and since <= days:
            continue
        item = items[item_id]
        row = [item.code, item.name, item.get_kind_display(), item.base_uom.code, qty, last, since,
               received.get(item_id), "Never used" if last is None else "Slow"]
        if ctx.costs:
            row.append(value.quantize(CENT))
            tied.append(value)
        rows.append(row)
    summary = [("Items", len(rows))]
    if ctx.costs:
        summary.append(("Value held", _money_text(_money_total(tied))))
    return _done(ctx, Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Type"), Column("Unit"),
                 Column("On hand", True, kind="qty"), Column("Last used", kind="date"),
                 Column("Days since", True), Column("Last received", kind="date"),
                 Column("Status", kind="status"), *_cost(ctx, Column("Value", True, kind="money"))],
        rows=rows, summary=summary,
        note=f"Not issued, used in production or delivered in the last {days} days. "
             "Transfers and adjustments are not use.",
        empty=f"Everything in stock was used in the last {days} days."))


def on_order(company_id, branches, item_ids=None):
    """``{item_id: base quantity ordered from suppliers and not yet received}``."""
    from purchasing.models import PurchaseOrder, PurchaseOrderLine

    lines = PurchaseOrderLine.all_objects.filter(
        company_id=company_id, order__status__in=[PurchaseOrder.Status.APPROVED,
                                                  PurchaseOrder.Status.PARTLY_RECEIVED])
    if branches is not ALL_BRANCHES:
        lines = lines.filter(order__branch_id__in=branches)
    if item_ids is not None:
        lines = lines.filter(item_id__in=item_ids)
    found = defaultdict(lambda: ZERO)
    for item_id, qty, factor, received in lines.values_list("item_id", "qty", "factor", "qty_received_base"):
        found[item_id] += max(qty * factor - received, ZERO)
    return found


def available_by_item(company_id, store_ids, item_ids=None):
    """``{item_id: on hand less reserved}`` in the usable stores among ``store_ids``."""
    rows = StockBalance.all_objects.filter(company_id=company_id, warehouse_id__in=store_ids).exclude(
        warehouse__kind__in=UNUSABLE_STORES)
    if item_ids is not None:
        rows = rows.filter(item_id__in=item_ids)
    return {item_id: on_hand - reserved for item_id, on_hand, reserved in
            rows.order_by().values("item_id").annotate(h=Sum("qty_on_hand"), r=Sum("qty_reserved"))
            .values_list("item_id", "h", "r")}


def reorder_rows(company_id, store_ids, branches, item_ids=None):
    """Items at or below their reorder point (or minimum): what to buy or make.

    ``[(item, available, on_order, suggested, status)]``. Suggested: up to
    the maximum when one is set, else the reorder quantity, less what is
    already on order.
    """
    from django.db.models import Q

    candidates = Item.all_objects.filter(company_id=company_id, status="active").filter(
        Q(reorder_point__gt=0) | Q(min_stock__gt=0)).select_related("base_uom")
    if item_ids is not None:
        candidates = candidates.filter(pk__in=item_ids)
    candidates = list(candidates.order_by("code"))
    ids = [item.pk for item in candidates]
    free = available_by_item(company_id, store_ids, ids)
    ordered = on_order(company_id, branches, ids)
    found = []
    for item in candidates:
        point, least = item.reorder_point or ZERO, item.min_stock or ZERO
        most, batch = item.max_stock or ZERO, item.reorder_qty or ZERO
        available, coming = free.get(item.pk, ZERO), ordered.get(item.pk, ZERO)
        if not ((point and available <= point) or (least and available < least)):
            continue
        wanted = (most - available - coming) if most else ((batch or point - available) - coming)
        if available + coming > max(point, least):
            status = "Covered by open orders"
        elif least and available < least:
            status = "Below minimum"
        else:
            status = "Reorder"
        found.append((item, available, coming, max(wanted, ZERO), status))
    return found


def reorder(ctx):
    found = reorder_rows(ctx.company_id, ctx.store_ids, ctx.branches, ctx.item_ids)
    rows = [[item.code, item.name, item.get_kind_display(), item.base_uom.code, available,
             item.reorder_point or ZERO, item.min_stock or ZERO, item.max_stock or ZERO, coming, wanted, status]
            for item, available, coming, wanted, status in found]
    return Result(
        columns=[Column("Item"), Column("Name", weight=2), Column("Type"), Column("Unit"),
                 Column("Available", True, kind="qty"), Column("Reorder point", True, kind="qty"),
                 Column("Minimum", True, kind="qty"), Column("Maximum", True, kind="qty"),
                 Column("On order", True, kind="qty"), Column("Suggested", True, kind="qty"),
                 Column("Status", kind="status")],
        rows=rows,
        summary=[("Items", len(rows)),
                 ("Below minimum", sum(1 for *_r, status in found if status == "Below minimum")),
                 ("To order", sum(1 for *_r, status in found if status != "Covered by open orders"))],
        note="Available: on hand less reserved, in the usable stores. On order: approved purchase "
             "orders not yet received.",
        empty="Nothing is at or below its reorder point.")


# --------------------------------------------------------------------------
# Purchase
# --------------------------------------------------------------------------

def _line_amounts(qty, price, discount_pct, vat_rate):
    amount = (qty * price * (HUNDRED - (discount_pct or ZERO)) / HUNDRED).quantize(CENT)
    vat = (amount * (vat_rate or ZERO) / HUNDRED).quantize(CENT)
    return amount, vat


def purchase_register(ctx):
    """Purchase order lines placed in the dates (approved onwards)."""
    from purchasing.models import PurchaseOrder, PurchaseOrderLine

    f = ctx.f
    lines = ctx.in_branches(PurchaseOrderLine.all_objects.filter(
        company_id=ctx.company_id, order__order_date__gte=f.date_from, order__order_date__lte=f.date_to,
        order__status__in=[PurchaseOrder.Status.APPROVED, PurchaseOrder.Status.PARTLY_RECEIVED,
                           PurchaseOrder.Status.RECEIVED, PurchaseOrder.Status.CLOSED]),
        "order__branch_id")
    if ctx.item_ids is not None:
        lines = lines.filter(item_id__in=ctx.item_ids)
    rows, orders, sums = [], set(), [ZERO, ZERO]
    for line in lines.select_related("order__supplier", "item", "uom").order_by("order__order_date",
                                                                                 "order__number", "pk"):
        amount, vat = _line_amounts(line.qty, line.unit_price, line.discount_pct, line.vat_rate)
        orders.add(line.order_id)
        sums[0] += amount
        sums[1] += vat
        received = (line.qty_received_base / line.factor) if line.factor else ZERO
        rows.append([line.order.number, line.order.order_date, line.order.supplier.name,
                     line.order.get_status_display(), line.item.code, line.item.name, line.qty,
                     line.uom.code, line.unit_price, line.discount_pct, amount, vat, amount + vat,
                     received.quantize(Decimal("0.0001")).normalize()])
    return Result(
        columns=[Column("Order"), Column("Date", kind="date"), Column("Supplier", weight=1.6),
                 Column("Status", kind="status"), Column("Item"), Column("Name", weight=1.6),
                 Column("Quantity", True, kind="qty"), Column("Unit"), Column("Price", True, kind="cost"),
                 Column("Discount %", True, kind="qty"), Column("Amount", True, kind="money"),
                 Column("VAT", True, kind="money"), Column("Total", True, kind="money"),
                 Column("Received", True, kind="qty")],
        rows=rows,
        summary=[("Orders", len(orders)), ("Amount", _money_text(sums[0])), ("VAT", _money_text(sums[1])),
                 ("Total", _money_text(sums[0] + sums[1]))],
        empty="No purchase orders in these dates.")


def grn_register(ctx):
    """Goods received in the dates, line by line."""
    from purchasing.models import GoodsReceipt, GoodsReceiptLine

    f = ctx.f
    _value_note(ctx)
    lines = GoodsReceiptLine.all_objects.filter(
        company_id=ctx.company_id, receipt__warehouse_id__in=ctx.store_ids,
        receipt__received_date__gte=f.date_from, receipt__received_date__lte=f.date_to,
        receipt__status__in=[GoodsReceipt.Status.POSTED, GoodsReceipt.Status.REVERSED])
    if ctx.item_ids is not None:
        lines = lines.filter(item_id__in=ctx.item_ids)
    rows, receipts, total = [], set(), ZERO
    for line in lines.select_related("receipt__supplier", "receipt__order", "receipt__warehouse", "item",
                                     "uom", "batch").order_by("receipt__received_date", "receipt__number", "pk"):
        receipt = line.receipt
        receipts.add(receipt.pk)
        row = [receipt.number, receipt.received_date, receipt.supplier.name,
               receipt.order.number if receipt.order_id else "", receipt.warehouse.code, line.item.code,
               line.item.name, line.batch.batch_no if line.batch_id else line.batch_no, line.qty_received,
               line.qty_rejected, line.uom.code, line.get_qc_status_display(), receipt.get_status_display()]
        if ctx.costs:
            value = (line.qty_received * line.unit_cost).quantize(CENT)
            row += [line.unit_cost, value]
            if receipt.status == GoodsReceipt.Status.POSTED:
                total += value
        rows.append(row)
    summary = [("Receipts", len(receipts)), ("Lines", len(rows))]
    if ctx.costs:
        summary.append(("Value received", _money_text(total)))
    return _done(ctx, Result(
        columns=[Column("GRN"), Column("Date", kind="date"), Column("Supplier", weight=1.6), Column("Order"),
                 Column("Store"), Column("Item"), Column("Name", weight=1.6), Column("Batch"),
                 Column("Received", True, kind="qty"), Column("Rejected", True, kind="qty"), Column("Unit"),
                 Column("QC", kind="status"), Column("Status", kind="status"),
                 *_cost(ctx, Column("Unit cost", True, kind="cost"), Column("Value", True, kind="money"))],
        rows=rows, summary=summary,
        note="Reversed receipts are listed but not counted in the value.",
        empty="No goods received in these dates."))


# --------------------------------------------------------------------------
# Store issues and production
# --------------------------------------------------------------------------

def consumption_by_department(ctx):
    """What each department took from the stores, less what it brought back."""
    from inventory.models import MaterialReturn, StoreIssue

    f = ctx.f
    _value_note(ctx)
    moves = valuation.moves(ctx.company_id, store_ids=ctx.store_ids, item_ids=ctx.item_ids).filter(
        document_type__in=[StoreIssue.DOC_TYPE, MaterialReturn.DOC_TYPE],
        work_date__gte=f.date_from, work_date__lte=f.date_to)
    sums = list(moves.order_by().values("document_type", "document_id", "item_id").annotate(
        q=Sum("qty"), v=Sum("value")))
    departments = {}
    for model in (StoreIssue, MaterialReturn):
        ids = {row["document_id"] for row in sums if row["document_type"] == model.DOC_TYPE}
        for pk, name in model.all_objects.filter(pk__in=ids).values_list("pk", "department__name"):
            departments[(model.DOC_TYPE, pk)] = name
    found = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])     # issued q, v, returned q, v
    for row in sums:
        key = (departments.get((row["document_type"], row["document_id"]), "—"), row["item_id"])
        if row["document_type"] == StoreIssue.DOC_TYPE:
            found[key][0] -= row["q"]
            found[key][1] -= row["v"]
        else:
            found[key][2] += row["q"]
            found[key][3] += row["v"]
    items = _items({i for _d, i in found})
    rows, per_department = [], defaultdict(lambda: ZERO)
    for (department, item_id), (iq, iv, rq, rv) in sorted(
            found.items(), key=lambda kv: (kv[0][0], items[kv[0][1]].code)):
        if not (iq or rq):
            continue
        item = items[item_id]
        row = [department, item.code, item.name, item.base_uom.code, iq, rq, iq - rq]
        if ctx.costs:
            row.append((iv - rv).quantize(CENT))
            per_department[department] += iv - rv
        rows.append(row)
    summary = [("Departments", len({row[0] for row in rows})), ("Lines", len(rows))]
    if ctx.costs:
        summary.append(("Value used", _money_text(sum(per_department.values(), ZERO))))
    return _done(ctx, Result(
        columns=[Column("Department", weight=1.4), Column("Item"), Column("Name", weight=2), Column("Unit"),
                 Column("Issued", True, kind="qty"), Column("Returned", True, kind="qty"),
                 Column("Used", True, kind="qty"), *_cost(ctx, Column("Value used", True, kind="money"))],
        rows=rows, summary=summary,
        note="Store issues less material returns, by the department on the document. A reversed issue "
             "counts as nothing issued.",
        empty="No store issues in these dates."))


def production_yield(ctx):
    """Completed production runs: planned against good and rejected, and their cost."""
    from production import orders as production_orders
    from production.models import ProductionOrder

    f = ctx.f
    _value_note(ctx)
    runs = ProductionOrder.all_objects.filter(
        company_id=ctx.company_id, output_warehouse_id__in=ctx.store_ids,
        status__in=[ProductionOrder.Status.COMPLETED, ProductionOrder.Status.CLOSED],
        completed_at__date__gte=f.date_from, completed_at__date__lte=f.date_to)
    if ctx.item_ids is not None:
        runs = runs.filter(item_id__in=ctx.item_ids)
    rows, planned_total, good_total, cost_total = [], ZERO, ZERO, ZERO
    for run in runs.select_related("item__base_uom", "bom").order_by("completed_at", "number"):
        planned = run.planned_qty * run.factor
        good, rejected = run.good_qty_base or ZERO, run.rejected_qty_base or ZERO
        over = sum(1 for use in production_orders.usage(run) if use["variance"] > 0)
        planned_total += planned
        good_total += good
        row = [run.number, timezone.localtime(run.completed_at).date(), run.item.code, run.item.name, run.item.base_uom.code,
               planned, good, rejected, _pct(good, planned), _pct(rejected, good + rejected), over]
        if ctx.costs:
            row += [(run.material_cost or ZERO).quantize(CENT), run.unit_cost]
            cost_total += run.material_cost or ZERO
        rows.append(row)
    summary = [("Runs", len(rows)), ("Yield", f"{_pct(good_total, planned_total) or 0} %")]
    if ctx.costs:
        summary.append(("Material cost", _money_text(cost_total)))
    return _done(ctx, Result(
        columns=[Column("Order"), Column("Completed", kind="date"), Column("Product"), Column("Name", weight=1.6),
                 Column("Unit"), Column("Planned", True, kind="qty"), Column("Good", True, kind="qty"),
                 Column("Rejected", True, kind="qty"), Column("Yield %", True, kind="pct"),
                 Column("Rejected %", True, kind="pct"), Column("Materials over plan", True),
                 *_cost(ctx, Column("Material cost", True, kind="money"), Column("Unit cost", True, kind="cost"))],
        rows=rows, summary=summary,
        note="Yield: good output against the planned quantity. Rejected %: of all output. Materials over "
             "plan: how many of the run's materials were used beyond the BOM.",
        empty="No production completed in these dates."))


# --------------------------------------------------------------------------
# Sales
# --------------------------------------------------------------------------

def _invoices(ctx):
    from sales.models import SalesInvoice

    f = ctx.f
    return SalesInvoice.all_objects.filter(
        company_id=ctx.company_id, delivery__warehouse_id__in=ctx.store_ids,
        invoice_date__gte=f.date_from, invoice_date__lte=f.date_to,
        status__in=[SalesInvoice.Status.ISSUED, SalesInvoice.Status.PAID])


def sales_register(ctx):
    """Invoices issued in the dates."""
    from sales.models import SalesInvoice

    rows, sums, unpaid = [], [ZERO, ZERO, ZERO], ZERO
    for invoice in _invoices(ctx).select_related("customer", "delivery__order").order_by("invoice_date", "number"):
        sums[0] += invoice.amount
        sums[1] += invoice.vat
        sums[2] += invoice.total
        if invoice.status == SalesInvoice.Status.ISSUED:
            unpaid += invoice.total
        rows.append([invoice.number, invoice.invoice_date, invoice.customer.name, invoice.delivery.number,
                     invoice.delivery.order.number, invoice.get_status_display(), invoice.amount,
                     invoice.vat, invoice.total, invoice.due_date, invoice.paid_on])
    return Result(
        columns=[Column("Invoice"), Column("Date", kind="date"), Column("Customer", weight=1.8),
                 Column("Delivery"), Column("Order"), Column("Status", kind="status"),
                 Column("Amount", True, kind="money"), Column("VAT", True, kind="money"),
                 Column("Total", True, kind="money"), Column("Due", kind="date"), Column("Paid on", kind="date")],
        rows=rows,
        summary=[("Invoices", len(rows)), ("Amount", _money_text(sums[0])), ("VAT", _money_text(sums[1])),
                 ("Total", _money_text(sums[2])), ("Unpaid", _money_text(unpaid))],
        empty="No invoices in these dates.")


def customer_item_sales(ctx):
    """What each customer bought, item by item, from the invoices - and the margin."""
    from sales.models import SalesInvoiceLine

    _value_note(ctx)
    lines = SalesInvoiceLine.all_objects.filter(invoice__in=_invoices(ctx))
    if ctx.item_ids is not None:
        lines = lines.filter(item_id__in=ctx.item_ids)
    lines = list(lines.select_related("invoice__customer", "delivery_line", "item__base_uom"))
    costs = {}
    if ctx.costs:
        for line_id, value in (StockMove.all_objects.filter(
                company_id=ctx.company_id, document_type="delivery",
                document_line_id__in=[line.delivery_line_id for line in lines], move_type="delivery")
                .order_by().values("document_line_id").annotate(v=Sum("value"))
                .values_list("document_line_id", "v")):
            costs[line_id] = -value
    found = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])     # qty, amount, vat, cost
    names = {}
    for line in lines:
        key = (line.invoice.customer_id, line.item_id)
        names[key] = (line.invoice.customer.name, line.item)
        found[key][0] += line.qty * (line.delivery_line.factor if line.delivery_line_id else 1)
        found[key][1] += line.amount
        found[key][2] += line.vat_amount
        found[key][3] += costs.get(line.delivery_line_id, ZERO)
    rows, totals = [], [ZERO, ZERO, ZERO]
    for key, (qty, amount, vat, cost) in sorted(found.items(), key=lambda kv: (names[kv[0]][0], names[kv[0]][1].code)):
        customer, item = names[key]
        totals[0] += amount
        totals[1] += vat
        totals[2] += cost
        row = [customer, item.code, item.name, item.base_uom.code, qty, amount, vat, amount + vat]
        if ctx.costs:
            row += [cost.quantize(CENT), (amount - cost).quantize(CENT), _pct(amount - cost, amount)]
        rows.append(row)
    summary = [("Customers", len({k[0] for k in found})), ("Amount", _money_text(totals[0])),
               ("VAT", _money_text(totals[1]))]
    if ctx.costs:
        summary += [("Cost", _money_text(totals[2])), ("Margin", _money_text(totals[0] - totals[2]))]
    return _done(ctx, Result(
        columns=[Column("Customer", weight=1.8), Column("Item"), Column("Name", weight=1.6), Column("Unit"),
                 Column("Quantity", True, kind="qty"), Column("Amount", True, kind="money"),
                 Column("VAT", True, kind="money"), Column("Total", True, kind="money"),
                 *_cost(ctx, Column("Cost", True, kind="money"), Column("Margin", True, kind="money"),
                        Column("Margin %", True, kind="pct"))],
        rows=rows, summary=summary,
        note="Issued and paid invoices; amounts before VAT and after discount. Cost: what the delivered "
             "goods went out at. Sales returns are not taken off.",
        empty="No invoices in these dates."))


# --------------------------------------------------------------------------
# The catalogue
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Report:
    slug: str
    title: str
    group: str
    code: object             # the permission(s) that open it - any one is enough
    period: str              # as_of, range or now
    build: object
    description: str
    extras: tuple = ("store", "item", "category", "kind")

    @property
    def url_name(self):
        return f"inventory:report_{self.slug.replace('-', '_')}"


ITEM_FILTERS = ("store", "item", "category", "kind")

REPORTS = (
    Report("stock-on-hand", "Stock on hand", STOCK, STOCK_CODES, NOW, stock_on_hand,
           "Every item in every store now: on hand, reserved and available."),
    Report("item-card", "Stock ledger (item card)", STOCK, STOCK_CODES, RANGE, item_card,
           "One item's moves over any dates, with the running balance.", extras=("store", "item")),
    Report("movement", "Movement summary", STOCK, STOCK_CODES, RANGE, movement_summary,
           "Per item: opening, in, out and closing over any dates."),
    Report("valuation", "Stock valuation", STOCK, "inventory.valuation", AS_OF, valuation_at,
           "Quantity and value of every item at the close of any day, from the stock ledger.",
           extras=(*ITEM_FILTERS, "view")),
    Report("batch-expiry", "Batch and expiry", STOCK, STOCK_CODES, NOW, batch_expiry,
           "Batches in stock, expired and expiring first.", extras=(*ITEM_FILTERS, "days")),
    Report("aging", "Stock aging", STOCK, STOCK_CODES, AS_OF, aging_report,
           "How long stock has been in each store, in age brackets."),
    Report("slow-moving", "Slow and non-moving stock", STOCK, STOCK_CODES, NOW, slow_moving,
           "Items in stock that have not been issued, used or sold for a while.",
           extras=(*ITEM_FILTERS, "days")),
    Report("reorder", "Reorder report", STOCK, STOCK_CODES, NOW, reorder,
           "Items at or below their reorder point, with what is on order and what to order.",
           extras=("store", "item", "category", "kind")),
    Report("purchase-register", "Purchase register", PURCHASE, PURCHASE_CODES, RANGE, purchase_register,
           "Purchase order lines placed in any dates, with amounts and what has come in.",
           extras=("item", "category", "kind")),
    Report("grn-register", "GRN register", PURCHASE, GRN_CODES, RANGE, grn_register,
           "Goods received in any dates, line by line, with batches and quality check."),
    Report("consumption", "Consumption by department", STOCK, ISSUE_CODES, RANGE,
           consumption_by_department, "What each department took from the stores, less returns."),
    Report("production-yield", "Production yield and wastage", PRODUCTION, PRODUCTION_CODES, RANGE,
           production_yield, "Completed runs: planned against good and rejected output, and cost."),
    Report("sales-register", "Sales register", SALES, SALES_CODES, RANGE, sales_register,
           "Invoices issued in any dates, with VAT and what is unpaid.", extras=("store",)),
    Report("customer-sales", "Customer and item sales", SALES, SALES_CODES, RANGE, customer_item_sales,
           "What each customer bought, item by item, and the margin."),
)

BY_SLUG = {report.slug: report for report in REPORTS}
KINDS = ItemKind.choices
