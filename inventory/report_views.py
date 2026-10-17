"""Phase 7 pages: the inventory dashboard, the reports (with their Excel and
PDF downloads) and month-end close.

A report's page and its download are the same view (``?format=xlsx|pdf``):
the same permission, dates and filters, and the download holds every row the
table pages through, searched and sorted as it was on screen.
"""

import zoneinfo
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Sum
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.html import format_html
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods, require_POST

from access_control.branch_access import ALL_BRANCHES, branches_for
from base_template.tables import paginate_rows, render, table_rows
from common import exports
from common.forms import StyledFormMixin, apply_service_errors
from common.tenant import use_company
from inventory import access, periods, valuation
from inventory import reports as catalogue
from inventory.doc_common import local_today
from inventory.forms import ReasonForm
from inventory.models import Item, ItemCategory, ItemKind, PeriodLock, StockBalance, Warehouse
from inventory.posting import ledger_differences, settings_for
from inventory.views import company_or_bail

CENT = Decimal("0.01")
FOUR = Decimal("0.0001")
MAX_DAYS = 366

#: A status word -> its badge colour.
TONE = {
    "Expired": "danger", "Expiring soon": "warning", "Good": "success", "Quarantined": "warning",
    "Blocked": "danger", "Below minimum": "danger", "Reorder": "warning", "Covered by open orders": "info",
    "Never used": "danger", "Slow": "warning", "Posted": "success", "Reversed": "neutral",
    "Issued": "info", "Paid": "success", "Approved": "info", "Received": "success",
    "Partly received": "warning", "Closed": "neutral", "Passed": "success", "Failed": "danger",
    "Waiting for QC": "warning", "Not required": "neutral",
}


# --------------------------------------------------------------------------
# Filters
# --------------------------------------------------------------------------

@dataclass
class Filters:
    period: str
    as_of: date
    date_from: date
    date_to: date
    store: str = ""
    item: str = ""
    category: str = ""
    kind: str = ""
    days: int = 0
    view: str = ""
    errors: list = field(default_factory=list)

    @property
    def label(self):
        if self.period == catalogue.AS_OF:
            return f"At the close of {self.as_of:%d %b %Y}"
        if self.period == catalogue.RANGE:
            return f"{self.date_from:%d %b %Y} – {self.date_to:%d %b %Y}"
        return "Now"

    def slug(self):
        if self.period == catalogue.AS_OF:
            return self.as_of.isoformat()
        if self.period == catalogue.RANGE:
            return f"{self.date_from.isoformat()}_{self.date_to.isoformat()}"
        return "now"


def _date(text, default):
    try:
        return date.fromisoformat((text or "").strip())
    except ValueError:
        return default


def _digits(text):
    text = (text or "").strip()
    return text if text.isdigit() else ""


def read_filters(source, report, today):
    f = Filters(period=report.period, as_of=min(_date(source.get("as_of"), today), today),
                date_from=_date(source.get("date_from"), today.replace(day=1)),
                date_to=_date(source.get("date_to"), today))
    if f.date_from > f.date_to:
        f.date_from, f.date_to = f.date_to, f.date_from
    if (f.date_to - f.date_from).days >= MAX_DAYS:
        f.date_from = f.date_to - timedelta(days=MAX_DAYS - 1)
        f.errors.append(f"A report covers at most {MAX_DAYS} days; showing from {f.date_from:%d %b %Y}.")
    extras = report.extras
    if "store" in extras:
        f.store = _digits(source.get("store"))
    if "item" in extras:
        f.item = _digits(source.get("item"))
    if "category" in extras:
        f.category = _digits(source.get("category"))
    if "kind" in extras and source.get("kind") in ItemKind.values:
        f.kind = source.get("kind")
    if "days" in extras:
        days = _digits(source.get("days"))
        f.days = min(int(days), 3650) if days else 0
    if "view" in extras and source.get("view") == "store":
        f.view = "store"
    return f


def _described(f, report, choices):
    said = [f.label]
    if f.store:
        said.append("Store: " + next((str(s) for s in choices["stores"] if str(s.pk) == f.store), f.store))
    elif "store" in report.extras:
        said.append("All stores")
    if f.item:
        said.append("Item: " + next((f"{i.code} {i.name}" for i in choices["items"] if str(i.pk) == f.item), f.item))
    if f.category:
        said.append("Category: " + next((c.name for c in choices["categories"] if str(c.pk) == f.category), ""))
    if f.kind:
        said.append("Type: " + dict(ItemKind.choices)[f.kind])
    if f.days:
        said.append(f"Days: {f.days}")
    if f.view == "store":
        said.append("By store")
    return said


# --------------------------------------------------------------------------
# Cells
# --------------------------------------------------------------------------

def display(value, column):
    """A cell's text, by the column's kind: what the page and the PDF show."""
    if value is None or value == "":
        return ""
    if isinstance(value, date):
        return value.strftime("%d %b %Y")
    if isinstance(value, Decimal):
        kind = column.kind
        if kind == "money":
            return f"{value.quantize(CENT):,}"
        if kind == "cost":
            exact = value.quantize(FOUR)
            return f"{exact:,.2f}" if exact == exact.quantize(CENT) else f"{exact:,.4f}".rstrip("0")
        if kind == "pct":
            return f"{value} %"
        return f"{value.normalize():,f}" if value else "0"
    return str(value)


@dataclass
class Cell:
    html: str
    css: str


_EMPTY = mark_safe('<span class="report-cell--empty">—</span>')


def cell(value, column):
    text = display(value, column)
    numeric = "numeric" if column.numeric else ""
    if not text:
        return Cell(_EMPTY, numeric)
    if column.kind == "status":
        return Cell(format_html('<span class="badge badge--{}">{}</span>', TONE.get(text, "neutral"), text), "")
    return Cell(format_html("{}", text), numeric)


# --------------------------------------------------------------------------
# Reports
# --------------------------------------------------------------------------

def _codes(code):
    return (code,) if isinstance(code, str) else tuple(code)


def _branches(user, company_id, code):
    found = set()
    for one in _codes(code):
        branches = branches_for(user, company_id, one)
        if branches is ALL_BRANCHES:
            return ALL_BRANCHES
        found |= set(branches)
    return found


def _sees_costs_in(user, company_id, stores):
    if not stores:
        return False
    allowed = branches_for(user, company_id, "inventory.valuation")
    return allowed is ALL_BRANCHES or all(store.branch_id in allowed for store in stores)


def visible_reports(user, company_id):
    return [report for report in catalogue.REPORTS
            if any(access.may(user, company_id, code) for code in _codes(report.code))]


@login_required
def report_index(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    shown = visible_reports(request.user, company_id)
    if not shown:
        access.require(request.user, company_id, "inventory.view")
    groups = {}
    for report in shown:
        groups.setdefault(report.group, []).append(report)
    return render(request, "inventory/reports/index.html", {"groups": groups})


def build(user, company_id, report, params):
    """Run ``report`` for ``user`` with the filters in ``params`` (the page's
    query string, or the API's): ``(membership, filters, result, described, choices)``.
    The panel page and the API both call it, so they show the same figures."""
    membership = access.require(user, company_id, report.code)
    company = membership.company
    zone = zoneinfo.ZoneInfo(company.timezone or "Asia/Dhaka")
    today = local_today(company)
    f = read_filters(params, report, today)
    with use_company(company_id):
        stores = list(access.scope_warehouses(Warehouse.objects.select_related("branch").order_by("code"),
                                              user, company_id, report.code))
        choices = {
            "stores": stores,
            "items": list(Item.objects.order_by("code").only("pk", "code", "name"))
            if "item" in report.extras else [],
            "categories": list(ItemCategory.objects.order_by("name")) if "category" in report.extras else [],
        }
    # A filter outside the viewer's reach is dropped, not trusted.
    if f.store and not any(str(s.pk) == f.store for s in stores):
        f.store = ""
    if f.item and not any(str(i.pk) == f.item for i in choices["items"]):
        f.item = ""
    if f.category and not any(str(c.pk) == f.category for c in choices["categories"]):
        f.category = ""
    covered = [s for s in stores if str(s.pk) == f.store] if f.store else stores
    item_ids = None
    if f.item or f.category or f.kind:
        items = Item.all_objects.filter(company_id=company_id)
        if f.item:
            items = items.filter(pk=int(f.item))
        if f.category:
            items = items.filter(category_id=int(f.category))
        if f.kind:
            items = items.filter(kind=f.kind)
        item_ids = list(items.values_list("pk", flat=True))
    branches = _branches(user, company_id, report.code)
    if f.store and covered:
        branches = {covered[0].branch_id}
    ctx = catalogue.Ctx(company_id=company_id, company=company, actor=user, f=f, today=today,
                        stores=covered, branches=branches,
                        costs=_sees_costs_in(user, company_id, covered), item_ids=item_ids)
    with timezone.override(zone), use_company(company_id):
        result = report.build(ctx)
    return membership, f, result, _described(f, report, choices), choices


@login_required
@require_http_methods(["GET"])
def report(request, slug):
    report = catalogue.BY_SLUG.get(slug)
    if report is None:
        raise Http404("No such report.")
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    membership, f, result, described, choices = build(request.user, company_id, report, request.GET)
    today = local_today(membership.company)

    fmt = request.GET.get("format")
    if fmt in exports.FORMATS:
        return _download(request, report, result, f, described, membership, fmt)

    page = paginate_rows(request, result.rows, columns=len(result.columns))
    query = request.GET.copy()
    for drop in ("page", "per_page", "table_q", "table", "draw", "start", "length", "format", "search[value]"):
        query.pop(drop, None)
    for key in [key for key in query if key.startswith(("order[", "columns["))]:
        query.pop(key)
    return render(request, "inventory/reports/report.html", {
        "report": report, "result": result, "page": page, "f": f, "described": described,
        "cells": [[cell(v, c) for v, c in zip(row, result.columns)] for row in page.object_list],
        "query": query.urlencode(), "kinds": ItemKind.choices, "today": today, **choices,
    })


def _download(request, report, result, f, described, membership, fmt):
    rows, table_search, sorted_by = table_rows(request, result.rows, columns=len(result.columns))
    if table_search:
        described = described + [f'Table search: "{table_search}"']
    if sorted_by:
        described = described + ["Sorted by: " + ", ".join(
            f"{result.columns[c].label} {'descending' if d else 'ascending'}" for c, d in sorted_by)]
    try:
        exports.check_size(len(rows), fmt, noun="report rows")
    except exports.TooManyRows as refusal:
        messages.error(request, str(refusal))
        params = request.GET.copy()
        params.pop("format", None)
        return redirect(f"{request.path}?{params.urlencode()}")
    return export(report, result, rows, f, described, membership, fmt, request.user)


def export(report, result, rows, f, described, membership, fmt, user):
    """The report as an Excel or PDF download (the panel's and the API's)."""
    company = membership.company
    now = timezone.localtime(timezone.now(), zoneinfo.ZoneInfo(company.timezone or "Asia/Dhaka"))
    lines = [" · ".join(described)]
    if result.summary:
        lines.append(" · ".join(f"{label}: {value}" for label, value in result.summary))
    lines.append(f"{len(rows)} row{'s' if len(rows) != 1 else ''} · downloaded {now:%d %b %Y %H:%M} "
                 f"by {user.get_username()}")
    if result.note:
        lines.append(result.note)
    title = f"{company.name} — {report.title}"
    headers = [column.label for column in result.columns]
    numeric = [i for i, column in enumerate(result.columns) if column.numeric]
    widths = {i: column.weight for i, column in enumerate(result.columns)}
    if fmt == exports.XLSX:
        plain = [[value if value is not None else "" for value in row] for row in rows]
        content = exports.table_xlsx(title=title, lines=lines, headers=headers, rows=plain, numeric=numeric,
                                     widths={i: max(8, int(w * 12)) for i, w in widths.items()})
    else:
        shown = [[display(v, c) for v, c in zip(row, result.columns)] for row in rows]
        content = exports.table_pdf(title=title, lines=lines, headers=headers, rows=shown, numeric=numeric,
                                    widths=widths, compact=len(headers) > 11)
    exports.record(actor=user, membership=membership, page=f"inventory-report:{report.slug}",
                   fmt=fmt, filters={"described": described}, count=len(rows))
    return exports.response(content, fmt, exports.filename(report.slug, company.name, f.slug(), fmt))


# --------------------------------------------------------------------------
# Dashboard
# --------------------------------------------------------------------------

@login_required
def dashboard(request):
    """Stock at a glance: value by type and store, low stock, expiry,
    quarantine, the work waiting, and today's production and sales."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return render(request, "inventory/dashboard.html", dashboard_figures(request.user, company_id))


def dashboard_figures(user, company_id):
    """The dashboard's figures for ``user`` (the panel page's and the API's)."""
    membership = access.require(user, company_id, "inventory.view")
    company = membership.company
    today = local_today(company)
    with use_company(company_id):
        stores = list(access.scope_warehouses(Warehouse.objects.select_related("branch").order_by("code"),
                                              user, company_id, "inventory.view"))
        store_ids = [s.pk for s in stores]
        costs = _sees_costs_in(user, company_id, stores)
        held = valuation.at(company_id, catalogue.FAR, store_ids=store_ids, by_store=True)
        items = {i.pk: i for i in Item.all_objects.filter(pk__in={i for i, _s in held})}
        by_store = {s.pk: {"store": s, "items": set(), "value": Decimal("0")} for s in stores}
        by_kind = {value: {"label": label, "items": set(), "value": Decimal("0")} for value, label in ItemKind.choices}
        total = Decimal("0")
        for (item_id, store_id), (qty, value) in held.items():
            if qty <= 0:
                continue
            by_store[store_id]["items"].add(item_id)
            by_store[store_id]["value"] += value
            by_kind[items[item_id].kind]["items"].add(item_id)
            by_kind[items[item_id].kind]["value"] += value
            total += value
        low = [row for row in catalogue.reorder_rows(company_id, store_ids, _branches(user, company_id, "inventory.view"))
               if row[-1] != "Covered by open orders"]
        alert_days = settings_for(company_id).expiry_alert_days
        expiring = list(StockBalance.objects.filter(
            warehouse_id__in=store_ids, qty_on_hand__gt=0, batch__expiry_date__isnull=False,
            batch__expiry_date__lte=today + timedelta(days=alert_days)).exclude(warehouse__kind="rejected")
            .select_related("item__base_uom", "batch", "warehouse").order_by("batch__expiry_date", "item__code"))
        for row in expiring:
            row.days_left = (row.batch.expiry_date - today).days
        quarantine = (StockBalance.objects.filter(warehouse_id__in=store_ids, qty_on_hand__gt=0)
                      .filter(warehouse__kind="quarantine").aggregate(rows=Count("pk"))["rows"])
        waiting = _waiting(user, company_id)
        produced, sold = _today(user, company_id, today)
    return {
        "today": today, "costs": costs, "total": total.quantize(CENT),
        "stock_items": len({i for (i, _s), (q, _v) in held.items() if q > 0}),
        "by_store": [line for line in by_store.values() if line["items"]],
        "by_kind": [line for line in by_kind.values() if line["items"]],
        "low": low[:10], "low_count": len(low), "expiring": expiring[:10], "expiring_count": len(expiring),
        "expired_count": sum(1 for row in expiring if row.days_left < 0), "alert_days": alert_days,
        "quarantine": quarantine, "waiting": waiting, "produced": produced, "sold": sold,
        "reports": bool(visible_reports(user, company_id)),
    }


def _waiting(user, company_id):
    """Work waiting for someone: ``[(label, count, url name)]``, for what the viewer may see."""
    from production.models import ProductionOrder
    from purchasing.models import GoodsReceipt, GoodsReceiptLine, PurchaseOrder
    from inventory.models import StoreIssue, StoreRequisition
    from sales.models import Delivery, SalesOrder

    found = []

    def add(label, codes, queryset, path, url):
        if any(access.may(user, company_id, code) for code in _codes(codes)):
            number = access.scope_warehouses(queryset, user, company_id, codes, field=path).count()
            found.append((label, number, url))

    add("Purchase orders to approve", "purchase.approve",
        PurchaseOrder.objects.filter(status="submitted"), "branch", "purchasing:orders")
    add("Purchase orders still to come in", ("purchase.order", "purchase.receive"),
        PurchaseOrder.objects.filter(status__in=["approved", "partly_received"]), "branch", "purchasing:orders")
    add("Goods receipts not posted", "purchase.receive",
        GoodsReceipt.objects.filter(status="draft"), "warehouse__branch", "purchasing:receipts")
    add("Lines waiting for QC", "purchase.qc",
        GoodsReceiptLine.objects.filter(receipt__status="posted", qc_status="pending"),
        "receipt__warehouse__branch", "purchasing:qc")
    add("Store requisitions to approve", "inventory.issue_approve",
        StoreRequisition.objects.filter(status="submitted"), "warehouse__branch", "inventory:store_requisitions")
    add("Store issues not posted", "inventory.issue",
        StoreIssue.objects.filter(status="draft"), "warehouse__branch", "inventory:store_issues")
    add("Production orders running", ("production.plan", "production.execute"),
        ProductionOrder.objects.filter(status="released"), "output_warehouse__branch", "production:orders")
    add("Sales orders to confirm", "sales.approve",
        SalesOrder.objects.filter(status="draft"), "warehouse__branch", "sales:orders")
    add("Sales orders to deliver", "sales.deliver",
        SalesOrder.objects.filter(status__in=["confirmed", "partly_delivered"]), "warehouse__branch", "sales:orders")
    add("Deliveries to invoice", "sales.invoice",
        Delivery.objects.filter(status="posted").exclude(invoices__status__in=["draft", "issued", "paid"]),
        "warehouse__branch", "sales:deliveries")
    return found


def _today(user, company_id, today):
    """Today's production output and sales, where the viewer may see them."""
    from production.models import Output
    from sales.models import Delivery, SalesInvoice

    produced = None
    if any(access.may(user, company_id, c) for c in catalogue.PRODUCTION_CODES):
        outputs = access.scope_warehouses(
            Output.objects.filter(status="posted", output_date=today), user, company_id,
            catalogue.PRODUCTION_CODES, field="order__output_warehouse__branch").select_related(
            "order__item__base_uom", "uom")
        produced = [{"number": o.order.number, "item": o.order.item, "good": o.qty_good, "rejected": o.qty_rejected,
                     "unit": o.uom.code} for o in outputs.order_by("pk")]
    sold = None
    if any(access.may(user, company_id, c) for c in catalogue.SALES_CODES + ("sales.deliver",)):
        codes = catalogue.SALES_CODES + ("sales.deliver",)
        deliveries = access.scope_warehouses(Delivery.objects.filter(status="posted", delivery_date=today),
                                             user, company_id, codes, field="warehouse__branch")
        invoices = access.scope_warehouses(
            SalesInvoice.objects.filter(status__in=["issued", "paid"], invoice_date=today),
            user, company_id, codes, field="delivery__order__warehouse__branch")
        sold = {"deliveries": deliveries.count(), "invoices": invoices.count(),
                "total": (invoices.aggregate(t=Sum("total"))["t"] or Decimal("0")).quantize(CENT)}
    return produced, sold


# --------------------------------------------------------------------------
# Month-end close
# --------------------------------------------------------------------------

def _ended_months(today, count=13):
    first = today.replace(day=1)
    months = []
    for _ in range(count):
        first = (first - timedelta(days=1)).replace(day=1)
        months.append(first)
    return months


class CloseForm(StyledFormMixin, forms.Form):
    month = forms.ChoiceField(label="Month")
    reason = forms.CharField(label="Note", max_length=255, required=False,
                             help_text="Optional: e.g. 'Counted and checked with accounts'.")

    def __init__(self, *args, months=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["month"].choices = [(m.isoformat(), f"{m:%B %Y}") for m in months]


@login_required
@require_http_methods(["GET", "POST"])
def period_close(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    membership = access.require(request.user, company_id, periods.CODE)
    today = local_today(membership.company)
    with use_company(company_id):
        locks = list(PeriodLock.objects.select_related("locked_by").order_by("-month"))
    closed = {lock.month for lock in locks}
    open_months = [m for m in _ended_months(today) if m not in closed]
    form = CloseForm(request.POST or None, months=open_months)
    if request.method == "POST" and form.is_valid():
        try:
            lock = periods.close_month(actor=request.user, company_id=company_id,
                                       month=form.cleaned_data["month"], reason=form.cleaned_data["reason"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{lock.month:%B %Y} is closed: nothing more is posted dated in it.")
            return redirect("inventory:period_close")
    last = open_months[0] if open_months else None
    checklist = None
    if last is not None:
        checklist = {"month": last, "waiting": periods.waiting(company_id, last),
                     "differences": len(ledger_differences(company_id))}
    return render(request, "inventory/periods.html", {
        "locks": locks, "form": form, "checklist": checklist, "reason_form": ReasonForm(),
        "open_dialog": "close-dialog" if request.method == "POST" else "",
        "sees_valuation": access.may(request.user, company_id, "inventory.valuation"),
    })


@login_required
@require_POST
def period_reopen(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        month = periods.reopen_month(actor=request.user, company_id=company_id, lock_id=pk,
                                     reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, " ".join(exc.messages))
    else:
        messages.success(request, f"{month:%B %Y} is open again.")
    return redirect("inventory:period_close")
