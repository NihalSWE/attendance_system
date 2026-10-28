"""Stock in, Stock, Move stock and Sell in the new look (2026-10-28,
docs/inventory/PRODUCTION_PLAN.md §3, §15 phase 9.4; the screens approved on
sample data in phase 2.5). Each list opens its documents' own pages; each
form fills its document and posts it at once (``inventory.flows``), answering
in place above the Save button."""

import datetime
import json
from decimal import Decimal, InvalidOperation

from django import forms as django_forms
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import Sortable, paginate_rows, render
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from inventory import access, flows, hub
from inventory.models import Batch, Item, ItemKind, ItemUom, StockBalance, Warehouse
from inventory.posting import UNUSABLE_STORES, settings_for
from inventory.views import company_or_bail

LINES = 30
ROWS_SHOWN = 3


def _common(request, company_id, active):
    return {"active": active, "production": access.production_on(company_id),
            "sees_costs": access.sees_costs(request.user, company_id)}


def _stores(request, company_id, code, usable=True):
    with use_company(company_id):
        rows = Warehouse.objects.filter(status="active").select_related("branch").order_by("name")
        if usable:
            rows = rows.exclude(kind__in=UNUSABLE_STORES)
        return list(access.scope_warehouses(rows, request.user, company_id, code))


def _item_options(company_id, kinds=None):
    """Every active item with what a line needs to follow it: its units (and
    each one's own price), whether it keeps batches and expires."""
    with use_company(company_id):
        items = Item.objects.filter(status="active").select_related("base_uom").order_by("code")
        if kinds:
            items = items.filter(kind__in=kinds)
        items = list(items)
        packs = {}
        for pack in ItemUom.objects.filter(item__in=items).select_related("uom"):
            packs.setdefault(pack.item_id, []).append(pack)
    options = []
    for item in items:
        units = [{"id": item.base_uom_id, "code": item.base_uom.code, "factor": "1",
                  "price": format(item.sale_price.normalize(), "f") if item.sale_price else ""}]
        for pack in packs.get(item.pk, []):
            price = pack.sale_price if pack.sale_price is not None else (
                item.sale_price * pack.factor if item.sale_price else None)
            units.append({"id": pack.uom_id, "code": pack.uom.code, "factor": format(pack.factor.normalize(), "f"),
                          "price": format(price.normalize(), "f") if price else ""})
        options.append({"item": item, "units": json.dumps(units),
                        "batch": "1" if item.track_batch or item.track_expiry else "",
                        "expiry": "1" if item.track_expiry else ""})
    return options


def _number(text):
    text = (text or "").replace(",", "").strip()
    if not text:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        raise ValueError from None


def _lines(request, company_id, *, cost=False, price=False, batch_no=False, condition=False, batch=False):
    """The lines typed on the page as ``flows.Line``, or the reasons by box."""
    from inventory.masters import units_for

    post, found, errors = request.POST, [], {}
    with use_company(company_id):
        items = {str(item.pk): item for item in Item.objects.filter(status="active").select_related("base_uom")}
    for row in range(1, LINES + 1):
        item_id = post.get(f"item_{row}", "").strip()
        if not item_id:
            continue
        item = items.get(item_id)
        if item is None:
            errors[f"item_{row}"] = ["Choose an item from the list."]
            continue
        values = {}
        for box, wanted in (("qty", True), ("cost", cost), ("price", price)):
            if not wanted:
                continue
            try:
                values[box] = _number(post.get(f"{box}_{row}"))
            except ValueError:
                errors[f"{box}_{row}"] = ["Write a number."]
        if "qty" in values and (values["qty"] is None or values["qty"] <= 0):
            errors[f"qty_{row}"] = ["Write a quantity above 0."]
        units = units_for(item)
        unit_id = post.get(f"unit_{row}", "")
        uom_id = int(unit_id) if unit_id.isdigit() and int(unit_id) in units else item.base_uom_id
        line = flows.Line(row=row, item=item, qty=values.get("qty"), uom=_uom(uom_id),
                          unit_cost=values.get("cost"), unit_price=values.get("price"))
        if batch_no:
            line.batch_no = post.get(f"batch_{row}", "").strip()
            expiry = post.get(f"expiry_{row}", "").strip()
            line.expiry_date = parse_date(expiry) if expiry else None
            if expiry and line.expiry_date is None:
                errors[f"expiry_{row}"] = ["Pick a date."]
        if condition:
            line.condition = "damaged" if post.get(f"condition_{row}") == "damaged" else "good"
        if batch and post.get(f"batch_{row}", "").isdigit():
            line.batch = Batch.all_objects.filter(company_id=company_id, item=item,
                                                  pk=int(post[f"batch_{row}"])).first()
        found.append(line)
    return found, errors


def _uom(pk):
    from inventory.models import Uom

    return Uom.all_objects.get(pk=pk)


def _answer(call, success, redirect):
    try:
        result = call()
    except ValidationError as exc:
        return fetch.refused_errors(exc)
    return fetch.saved(success(result), redirect)


class _HeadForm(StyledFormMixin, django_forms.Form):
    def __init__(self, *args, stores, **kwargs):
        super().__init__(*args, **kwargs)
        ids = [store.pk for store in stores]
        for name in ("store", "from_store", "to_store"):
            if name in self.fields:
                self.fields[name].queryset = Warehouse.all_objects.filter(pk__in=ids).order_by("name")
                self.fields[name].label_from_instance = lambda store: f"{store.name} ({store.branch.name})"


# --- Stock ----------------------------------------------------------------------

@login_required
def stock_now(request):
    """Everything in stock, by item, with where it is, what to keep, its value,
    and the batches that expire first."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    context = _common(request, company_id, "stock")
    store = request.GET.get("store", "").strip()
    kind = request.GET.get("kind", "").strip()
    state = request.GET.get("status", "").strip()
    kinds = [kind] if kind in ItemKind.values else list(ItemKind.values)
    found = [line for line in hub.lines(company_id, request.user, kinds, store=store, state=state)
             if line.on_hand or line.keep]
    with use_company(company_id):
        rows = access.scope_warehouses(StockBalance.objects.filter(qty_on_hand__gt=0), request.user, company_id,
                                       "inventory.view", field="warehouse__branch")
        if store.isdigit():
            rows = rows.filter(warehouse_id=int(store))
        where = {}
        for item_id, name, qty in (rows.values("item_id", "warehouse__name").annotate(q=Sum("qty_on_hand"))
                                   .values_list("item_id", "warehouse__name", "q").order_by("warehouse__name")):
            where.setdefault(item_id, []).append(f"{name} {qty.normalize():,f}")
        days = settings_for(company_id).expiry_alert_days
        soon = timezone.localdate() + datetime.timedelta(days=days)
        expiring = list(rows.filter(batch__expiry_date__lte=soon).select_related("item__base_uom", "batch", "warehouse")
                        .order_by("batch__expiry_date")[:50])
    cells = []
    for line in found:
        line.where = " · ".join(where.get(line.item.pk, [])) or "—"
        row = [line.item.code, line.item.name, line.item.get_kind_display(),
               Sortable(line.stock_text, line.on_hand), line.where, line.keep]
        if context["sees_costs"]:
            row.append(line.value)
        cells.append(_Row(row + [line.words], line))
    table = paginate_rows(request, cells, columns=len(cells[0]) if cells else 1)
    stores = _stores(request, company_id, "inventory.view", usable=False)
    return render(request, "inventory/hub/stock_now.html", {
        **context, "rows": table, "stores": stores, "store": store, "kind": kind, "kinds": ItemKind.choices,
        "status": state, "statuses": hub.STATUSES, "filtered": bool(store or kind or state),
        "value": sum((line.value for line in found), hub.ZERO), "low": sum(1 for line in found if line.tone != "ok"),
        "expiring": expiring, "days": days, "today": timezone.localdate()})


class _Row(list):
    def __init__(self, cells, line):
        super().__init__(cells)
        self.line = line


# --- Stock in --------------------------------------------------------------------

class StockInForm(_HeadForm):
    source = django_forms.ChoiceField(label="Where it comes from", choices=flows.STOCK_IN_SOURCES,
                                      widget=django_forms.RadioSelect(attrs={"class": "check__input"}),
                                      initial="purchase")
    store = django_forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Into store")
    day = django_forms.DateField(label="Date", widget=date_widget())
    supplier = django_forms.ModelChoiceField(queryset=Item.all_objects.none(), required=False, label="Supplier")
    customer = django_forms.ModelChoiceField(queryset=Item.all_objects.none(), required=False, label="Customer")
    reason = django_forms.CharField(label="Why it came back", required=False, max_length=255)

    def __init__(self, *args, company_id, **kwargs):
        from purchasing.models import Supplier
        from sales.models import Customer

        super().__init__(*args, **kwargs)
        self.fields["supplier"].queryset = Supplier.all_objects.filter(company_id=company_id, status="active").order_by("name")
        self.fields["customer"].queryset = Customer.all_objects.filter(company_id=company_id, status="active").order_by("name")
        self.fields["supplier"].widget.attrs["data-show-when"] = "source:purchase"
        self.fields["customer"].widget.attrs["data-show-when"] = "source:return"
        self.fields["reason"].widget.attrs["data-show-when"] = "source:return"


@login_required
def stock_in(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, ("inventory.view", "purchase.receive", "inventory.adjust"))
    from purchasing.models import GoodsReceipt
    from sales.models import SalesReturn

    from inventory.models import OpeningStock

    branches = [store.branch_id for store in _stores(request, company_id, "inventory.view", usable=False)]
    rows = []
    with use_company(company_id):
        for doc in (GoodsReceipt.objects.filter(warehouse__branch_id__in=branches)
                    .select_related("supplier", "warehouse").annotate(n=Count("lines"))):
            rows.append((doc.received_date, doc.number, "Purchase", doc.warehouse.name, doc.supplier.name, doc.n,
                         doc.get_status_display(), reverse("purchasing:receipt_detail", args=[doc.pk])))
        for doc in OpeningStock.objects.annotate(n=Count("lines")):
            rows.append((doc.work_date, doc.number, "First count", "", "", doc.n, doc.get_status_display(),
                         reverse("inventory:opening_detail", args=[doc.pk])))
        for doc in (SalesReturn.objects.filter(warehouse__branch_id__in=branches)
                    .select_related("customer", "warehouse").annotate(n=Count("lines"))):
            rows.append((doc.return_date, doc.number, "Customer return", doc.warehouse.name, doc.customer.name,
                         doc.n, doc.get_status_display(), reverse("sales:return_detail", args=[doc.pk])))
    rows.sort(key=lambda row: (row[0], row[1]), reverse=True)
    cells = [_Row([Sortable(f"{day:%d %b %Y}", day), number, how, store, who, n, status], url)
             for day, number, how, store, who, n, status, url in rows]
    table = paginate_rows(request, cells, columns=7)
    return render(request, "inventory/hub/stock_in.html", {**_common(request, company_id, "stock_in"), "rows": table})


@login_required
def stock_in_new(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    stores = _stores(request, company_id, ("purchase.receive", "inventory.adjust", "sales.deliver"))
    if request.method == "POST":
        form = StockInForm(request.POST, company_id=company_id, stores=stores)
        lines, errors = _lines(request, company_id, cost=True, batch_no=True, condition=True)
        if not form.is_valid():
            return fetch.refused(form)
        if errors:
            return fetch.refused_errors(ValidationError(errors))
        data = form.cleaned_data
        return _answer(lambda: flows.stock_in(
            actor=request.user, company_id=company_id, source=data["source"], store=data["store"], day=data["day"],
            lines=lines, supplier=data["supplier"], customer=data["customer"], reason=data["reason"]),
            lambda done: done[2], reverse("inventory:stock_in"))
    access.require(request.user, company_id, ("purchase.receive", "inventory.adjust", "sales.deliver"))
    return render(request, "inventory/hub/stock_in_new.html", {
        **_common(request, company_id, "stock_in"),
        "form": StockInForm(company_id=company_id, stores=stores, initial={"day": timezone.localdate()}),
        "items": _item_options(company_id), "rows": range(1, ROWS_SHOWN + 1), "most": LINES})


# --- Move stock -------------------------------------------------------------------

class MoveForm(_HeadForm):
    from_store = django_forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="From store")
    to_store = django_forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="To store")
    day = django_forms.DateField(label="Date", widget=date_widget())

    def __init__(self, *args, to_stores, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["to_store"].queryset = Warehouse.all_objects.filter(pk__in=[s.pk for s in to_stores]).order_by("name")
        self.fields["to_store"].label_from_instance = lambda store: f"{store.name} ({store.branch.name})"


@login_required
def moves(request):
    from inventory.models_store import StockTransfer

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, ("inventory.view", "inventory.transfer"))
    branches = [store.branch_id for store in _stores(request, company_id, ("inventory.view", "inventory.transfer"),
                                                     usable=False)]
    with use_company(company_id):
        transfers = list(StockTransfer.objects.filter(Q(from_warehouse__branch_id__in=branches)
                                                      | Q(to_warehouse__branch_id__in=branches))
                         .select_related("from_warehouse", "to_warehouse").annotate(n=Count("lines"))
                         .order_by("-transfer_date", "-pk"))
    cells = []
    for row in transfers:
        row.can_receive = (row.status == StockTransfer.Status.IN_TRANSIT and
                           access.may(request.user, company_id, "inventory.transfer", row.to_warehouse.branch_id))
        row.tone = {"in_transit": "wait", "received": "ok", "draft": "info"}.get(row.status, "bad")
        cells.append(_Row([row.number, Sortable(f"{row.transfer_date:%d %b %Y}", row.transfer_date),
                           row.from_warehouse.name, row.to_warehouse.name, row.n, row.get_status_display(), ""], row))
    table = paginate_rows(request, cells, columns=7)
    return render(request, "inventory/hub/moves.html", {**_common(request, company_id, "moves"), "rows": table,
                                                        "today": timezone.localdate()})


def _balances_json(request, company_id, stores):
    """What is in each store, by item and batch, for the From → item → batch lists."""
    with use_company(company_id):
        rows = (StockBalance.objects.filter(qty_on_hand__gt=0, warehouse__in=stores)
                .select_related("batch").order_by("batch__expiry_date"))
        found = [{"store": row.warehouse_id, "item": row.item_id, "batch": row.batch_id or "",
                  "label": (f"{row.batch.batch_no}" + (f" · expires {row.batch.expiry_date:%d %b %Y}"
                                                       if row.batch.expiry_date else "") if row.batch_id else ""),
                  "qty": format(row.qty_on_hand.normalize(), "f")} for row in rows]
    return found          # written into the page with json_script


@login_required
def move_new(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    from_stores = _stores(request, company_id, "inventory.transfer")
    to_stores = _stores(request, company_id, "inventory.view", usable=False)
    to_stores = [store for store in to_stores if store.kind != Warehouse.Kind.TRANSIT]
    if request.method == "POST":
        form = MoveForm(request.POST, stores=from_stores, to_stores=to_stores)
        lines, errors = _lines(request, company_id, batch=True)
        if not form.is_valid():
            return fetch.refused(form)
        if errors:
            return fetch.refused_errors(ValidationError(errors))
        data = form.cleaned_data
        if data["from_store"] == data["to_store"]:
            return fetch.refused_errors(ValidationError({"to_store": "Choose another store than the one it leaves."}))
        return _answer(lambda: flows.move(actor=request.user, company_id=company_id, from_store=data["from_store"],
                                          to_store=data["to_store"], day=data["day"], lines=lines),
                       lambda row: f"{row.number} sent: it waits in transit until {row.to_warehouse.name} receives it.",
                       reverse("inventory:moves"))
    access.require(request.user, company_id, "inventory.transfer")
    return render(request, "inventory/hub/move_new.html", {
        **_common(request, company_id, "moves"),
        "form": MoveForm(stores=from_stores, to_stores=to_stores, initial={"day": timezone.localdate()}),
        "items": _item_options(company_id), "balances": _balances_json(request, company_id, from_stores),
        "rows": range(1, ROWS_SHOWN + 1), "most": LINES})


@login_required
@require_POST
def move_receive(request, pk):
    from inventory.models_store import StockTransfer

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    with use_company(company_id):
        row = StockTransfer.objects.filter(pk=pk).select_related("to_warehouse").first()
    if row is None:
        return fetch.refused(message="Not found.", status=404)
    return _answer(lambda: flows.receive_all(actor=request.user, company_id=company_id, transfer=row,
                                             day=timezone.localdate()),
                   lambda done: f"{row.number} received in {row.to_warehouse.name}.", reverse("inventory:moves"))


# --- Sell -------------------------------------------------------------------------

class SaleForm(_HeadForm):
    customer = django_forms.ModelChoiceField(queryset=Item.all_objects.none(), label="Customer")
    store = django_forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="From store")
    day = django_forms.DateField(label="Date", widget=date_widget())
    invoice = django_forms.BooleanField(label="Make the invoice now", required=False)
    credit_note = django_forms.CharField(label="Why, if over the credit limit", required=False, max_length=255)

    def __init__(self, *args, company_id, can_invoice, **kwargs):
        from sales.models import Customer

        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = Customer.all_objects.filter(company_id=company_id, status="active").order_by("name")
        if not can_invoice:
            del self.fields["invoice"]


@login_required
def sales(request):
    from sales.models import Delivery, SalesInvoice, SalesOrder
    from sales.orders import totals

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, ("inventory.view", "sales.order", "sales.deliver", "sales.invoice"))
    branches = [store.branch_id for store in _stores(request, company_id, "inventory.view", usable=False)]
    with use_company(company_id):
        orders = list(SalesOrder.objects.filter(warehouse__branch_id__in=branches)
                      .select_related("customer", "warehouse").order_by("-order_date", "-pk")[:2000])
        delivered = {}
        for order_id, number, pk in (Delivery.objects.filter(order__in=orders, status="posted")
                                     .values_list("order_id", "number", "pk")):
            delivered.setdefault(order_id, (number, pk))
        billed = dict(SalesInvoice.objects.filter(delivery__order__in=orders).values_list("delivery__order_id", "number"))
    sees = access.sees_costs(request.user, company_id) or access.may(request.user, company_id, "sales.order")
    cells = []
    for order in orders:
        order.total = totals(order)["total"] if sees else None
        order.delivery = delivered.get(order.pk)
        order.invoice = billed.get(order.pk)
        order.tone = {"delivered": "ok", "closed": "ok", "confirmed": "wait", "partly_delivered": "wait",
                      "draft": "info"}.get(order.status, "bad")
        cells.append(_Row([order.number, Sortable(f"{order.order_date:%d %b %Y}", order.order_date),
                           order.customer.name, order.warehouse.name, order.total or Decimal("0"),
                           order.get_status_display(), order.delivery[0] if order.delivery else "",
                           order.invoice or ""], order))
    table = paginate_rows(request, cells, columns=8)
    return render(request, "inventory/hub/sales.html", {**_common(request, company_id, "sell"), "rows": table,
                                                        "shows_money": sees})


@login_required
def sale_new(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    stores = _stores(request, company_id, "sales.order")
    can_invoice = access.may(request.user, company_id, "sales.invoice")
    if request.method == "POST":
        form = SaleForm(request.POST, company_id=company_id, stores=stores, can_invoice=can_invoice)
        lines, errors = _lines(request, company_id, price=True)
        if not form.is_valid():
            return fetch.refused(form)
        if errors:
            return fetch.refused_errors(ValidationError(errors))
        data = form.cleaned_data
        return _answer(lambda: flows.sell(actor=request.user, company_id=company_id, customer=data["customer"],
                                          store=data["store"], day=data["day"], lines=lines,
                                          credit_note=data["credit_note"], invoice=data.get("invoice", False)),
                       lambda done: done[3], reverse("inventory:sales"))
    access.require(request.user, company_id, "sales.order")
    return render(request, "inventory/hub/sale_new.html", {
        **_common(request, company_id, "sell"),
        "form": SaleForm(company_id=company_id, stores=stores, can_invoice=can_invoice,
                         initial={"day": timezone.localdate()}),
        "items": _item_options(company_id), "balances": _balances_json(request, company_id, stores),
        "rows": range(1, ROWS_SHOWN + 1), "most": LINES})
