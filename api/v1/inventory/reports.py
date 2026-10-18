"""Inventory reports (as data and as Excel / PDF files), the inventory
dashboard's figures, and month-end close."""

from decimal import Decimal

from rest_framework import serializers
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.serializers import StrictSerializer
from api.v1.inventory.engine import ERRORS, MODULE_NOTE, ONE, WRITE_ERRORS, InventoryView, plain, validated
from inventory import access, periods
from inventory import reports as catalogue
from inventory.models import PeriodLock

AREA = "inventory"
CENT = Decimal("0.01")
REPORT_ROLES = ["Each report by its own permission (View stock; See stock costs and values for the valuation; "
                "a purchase, production or sales permission for theirs)"]
REPORT_PARAMS = [
    Param("slug", PATH, "string", "The report, e.g. valuation (GET /inventory/reports lists them).", required=True,
          example="valuation"),
    Param("as_of", QUERY, "string (date)", "For reports at a date: its close (YYYY-MM-DD; default today).",
          example="2026-09-30"),
    Param("date_from", QUERY, "string (date)", "For reports over dates: from (default: the 1st of this month).",
          example="2026-10-01"),
    Param("date_to", QUERY, "string (date)", "For reports over dates: to (default today; at most 366 days).",
          example="2026-10-31"),
    Param("store", QUERY, "integer", "Only this store's id (reports that take it).", example=3),
    Param("item", QUERY, "integer", "Only this item's id.", example=7),
    Param("category", QUERY, "integer", "Only this category's id.", example=2),
    Param("kind", QUERY, "string", "Only this item type: raw, packaging, finished, spare, general.", example="raw"),
    Param("days", QUERY, "integer", "Batch and expiry: expiring within; slow-moving: not used for.", example=30),
    Param("view", QUERY, "string", "Valuation: store for one line per item and store.", example="store"),
]


class ReportsView(InventoryView):
    permission_classes = [PanelRule]
    read_scope, write_scope = "reports:read", None


def _report(slug):
    report = catalogue.BY_SLUG.get(slug)
    if report is None:
        raise ApiError("not_found", "No such inventory report.")
    return report


class _OneReport(ReportsView):
    @property
    def panel_page(self):
        report = catalogue.BY_SLUG.get(self.kwargs.get("slug", ""))
        return report.url_name if report else "inventory:reports"


# --- the list -------------------------------------------------------------------------------

class InventoryReportInfoSerializer(serializers.Serializer):
    slug = serializers.CharField(help_text="Its address: GET /inventory/reports/{slug}.")
    title = serializers.CharField(help_text="Its name.")
    group = serializers.CharField(help_text="Stock, Purchase, Production or Sales.")
    period = serializers.CharField(help_text="as_of (at a date), range (over dates) or now.")
    filters = serializers.ListField(child=serializers.CharField(), help_text="The filters it takes besides the "
                                                                             "dates: store, item, category, kind, "
                                                                             "days, view.")
    description = serializers.CharField(help_text="What it shows.")


def _info(report):
    return {"slug": report.slug, "title": report.title, "group": report.group, "period": report.period,
            "filters": list(report.extras), "description": report.description}


class InventoryReportListView(ReportsView):
    permission_classes = [PanelRule]
    panel_page = "inventory:reports"

    @endpoint(id="inventory-reports", area=AREA, title="Inventory reports",
              summary="The inventory reports you may open, and how each is asked for.",
              what_it_does=["Lists the 14 reports (stock, purchase, production, sales) your login may open."],
              description=f"Run one with GET /inventory/reports/{{slug}}; download it with …/export. {MODULE_NOTE}",
              roles=["Anyone in the company; each report by its own permission"], scopes=["reports:read"],
              response=InventoryReportInfoSerializer,
              response_example=[{"slug": "valuation", "title": "Stock valuation", "group": "Stock",
                                 "period": "as_of", "filters": ["store", "item", "category", "kind", "view"],
                                 "description": "Quantity and value of every item at the close of any day, from "
                                                "the stock ledger."}],
              errors=ERRORS)
    def get(self, request):
        from inventory.report_views import visible_reports

        return Response(InventoryReportInfoSerializer(
            [_info(r) for r in visible_reports(request.user, request.company_id)], many=True).data)


# --- one report ------------------------------------------------------------------------------

class ColumnSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="The column's heading.")
    numeric = serializers.BooleanField(help_text="A number (right-aligned).")
    kind = serializers.CharField(help_text="qty, money, cost, pct, date, status - or empty for text.")


class LabelValueSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="What it is, e.g. Value.")
    value = serializers.CharField(help_text="Its figure, as shown.")


class InventoryReportSerializer(serializers.Serializer):
    slug = serializers.CharField(help_text="The report.")
    title = serializers.CharField(help_text="Its name.")
    period = serializers.CharField(help_text="The period in words, e.g. At the close of 30 Sep 2026.")
    filters = serializers.ListField(child=serializers.CharField(), help_text="The filters applied, in words.")
    warnings = serializers.ListField(child=serializers.CharField(), help_text="E.g. a range shortened to 366 days.")
    columns = ColumnSerializer(many=True, help_text="The columns, in order.")
    summary = LabelValueSerializer(many=True, help_text="The totals at the top.")
    note = serializers.CharField(help_text="How it is worked out (may be empty).")
    count = serializers.IntegerField(help_text="Rows in all.")
    next = serializers.CharField(allow_null=True, help_text="The next page's address, or null.")
    previous = serializers.CharField(allow_null=True, help_text="The previous page's address, or null.")
    results = serializers.ListField(child=serializers.ListField(child=serializers.JSONField()),
                                    help_text="This page's rows: each a list, one value per column (numbers as "
                                              "strings, dates YYYY-MM-DD, empty as null).")


def _cell(value):
    return plain(value)


class InventoryReportView(_OneReport):
    permission_classes = [PanelRule]

    @endpoint(id="inventory-reports-one", area=AREA, title="Run an inventory report",
              summary="A report as data: its columns, rows (paged) and totals.",
              what_it_does=["Builds the report exactly as the panel's page shows it (the same code)."],
              description=("The period follows the report: as_of for a report at a date, date_from and date_to "
                           "over dates, nothing for now. Values and costs show only to whoever sees stock costs in "
                           "every store the report covers - otherwise those columns are left out and note says so."),
              roles=REPORT_ROLES, scopes=["reports:read"], params=REPORT_PARAMS, response=InventoryReportSerializer,
              response_example={"slug": "valuation", "title": "Stock valuation",
                                "period": "At the close of 30 Sep 2026", "filters": ["At the close of 30 Sep 2026",
                                                                                     "All stores"],
                                "warnings": [], "columns": [{"label": "Item", "numeric": False, "kind": ""},
                                                            {"label": "Value", "numeric": True, "kind": "money"}],
                                "summary": [{"label": "Value", "value": "25,887.50"}],
                                "note": "Every stock move dated on or before 30 Sep 2026 ...", "count": 1,
                                "next": None, "previous": None, "results": [["RM-0001", "4000.00"]]},
              errors=ERRORS + ONE)
    def get(self, request, slug):
        from inventory.report_views import build

        report = _report(slug)
        _m, f, result, described, _choices = build(request.user, request.company_id, report, request.query_params)
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(result.rows, request, view=self)
        paged = paginator.get_paginated_response([[_cell(v) for v in row] for row in chunk]).data
        return Response(InventoryReportSerializer({
            "slug": report.slug, "title": report.title, "period": f.label, "filters": described,
            "warnings": f.errors, "columns": [{"label": c.label, "numeric": c.numeric, "kind": c.kind}
                                              for c in result.columns],
            "summary": [{"label": str(label), "value": str(value)} for label, value in result.summary],
            "note": result.note or "", **paged}).data)


class InventoryReportExportView(_OneReport):
    permission_classes = [PanelRule]

    @endpoint(id="inventory-reports-export", area=AREA, title="Download an inventory report",
              summary="The same report as an Excel (xlsx) or PDF file.",
              what_it_does=["Answers the file, with the company, period, filters and totals at the top; the "
                            "download is kept in the audit log."],
              description="Too many rows are refused (Excel 10,000, PDF 1,500): narrow the filters.",
              roles=REPORT_ROLES, scopes=["reports:read"],
              params=REPORT_PARAMS + [Param("file_type", QUERY, "string", "xlsx (default) or pdf.", example="pdf")],
              errors=ERRORS + ONE + ["validation_error"])
    def get(self, request, slug):
        from common import exports
        from inventory.report_views import build, export

        report = _report(slug)
        fmt = request.query_params.get("file_type") or "xlsx"
        if fmt not in ("xlsx", "pdf"):
            refuse({"file_type": ["xlsx or pdf."]})
        membership, f, result, described, _choices = build(request.user, request.company_id, report,
                                                           request.query_params)
        try:
            exports.check_size(len(result.rows), fmt, noun="report rows")
        except exports.TooManyRows as refusal:
            raise ApiError("validation_error", str(refusal)) from None
        return export(report, result, result.rows, f, described, membership, fmt, request.user)


# --- the dashboard ----------------------------------------------------------------------------

class WaitingCountSerializer(serializers.Serializer):
    label = serializers.CharField(help_text="What is waiting, e.g. Purchase orders to approve.")
    count = serializers.IntegerField(help_text="How many.")


class GroupValueSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="The item type or store.")
    items = serializers.IntegerField(help_text="Items with stock.")
    value = serializers.CharField(allow_null=True, help_text="Their value - null without See stock costs and values.")


class InventoryDashboardSerializer(serializers.Serializer):
    today = serializers.CharField(help_text="The company's today (YYYY-MM-DD).")
    stock_value = serializers.CharField(allow_null=True, help_text="All stock you may see, at cost - null without "
                                                                   "See stock costs and values everywhere.")
    items_in_stock = serializers.IntegerField(help_text="Items with stock.")
    to_reorder = serializers.IntegerField(help_text="Items at or below their reorder point, not covered by orders.")
    expiring = serializers.IntegerField(help_text="Batches expiring within the warning days (expired included).")
    expired = serializers.IntegerField(help_text="Batches expired and still in stock.")
    expiry_alert_days = serializers.IntegerField(help_text="The warning days (Inventory settings).")
    quarantine_rows = serializers.IntegerField(help_text="Stock rows in quarantine stores.")
    by_kind = GroupValueSerializer(many=True, help_text="Stock by item type.")
    by_store = GroupValueSerializer(many=True, help_text="Stock by store.")
    waiting = WaitingCountSerializer(many=True, help_text="Work waiting, for what your login takes part in.")


class InventoryDashboardView(InventoryView):
    permission_classes = [PanelRule]
    panel_page = "inventory:dashboard"
    read_scope, write_scope = "inventory:read", None

    @endpoint(id="inventory-dashboard", area=AREA, title="Inventory dashboard",
              summary="Stock at a glance: value, low stock, expiry, quarantine and work waiting.",
              what_it_does=["Answers the same figures as the panel's Inventory dashboard."],
              description=MODULE_NOTE, roles=["Anyone with View stock and items"], scopes=["inventory:read"],
              response=InventoryDashboardSerializer,
              response_example={"today": "2026-10-17", "stock_value": "971983.50", "items_in_stock": 5,
                                "to_reorder": 2, "expiring": 1, "expired": 0, "expiry_alert_days": 30,
                                "quarantine_rows": 0,
                                "by_kind": [{"name": "Raw material", "items": 2, "value": "753133.50"}],
                                "by_store": [{"name": "RMS · Raw material store", "items": 2, "value": "753133.50"}],
                                "waiting": [{"label": "Purchase orders to approve", "count": 1}]},
              errors=ERRORS)
    def get(self, request):
        from inventory.report_views import dashboard_figures

        data = dashboard_figures(request.user, request.company_id)
        costs = data["costs"]
        return Response(InventoryDashboardSerializer({
            "today": data["today"].isoformat(), "stock_value": plain(data["total"]) if costs else None,
            "items_in_stock": data["stock_items"], "to_reorder": data["low_count"],
            "expiring": data["expiring_count"], "expired": data["expired_count"],
            "expiry_alert_days": data["alert_days"], "quarantine_rows": data["quarantine"] or 0,
            "by_kind": [{"name": line["label"], "items": len(line["items"]),
                         "value": plain(line["value"].quantize(CENT)) if costs else None} for line in data["by_kind"]],
            "by_store": [{"name": f"{line['store'].code} · {line['store'].name}", "items": len(line["items"]),
                          "value": plain(line["value"].quantize(CENT)) if costs else None}
                         for line in data["by_store"]],
            "waiting": [{"label": label, "count": count} for label, count, _url in data["waiting"]],
        }).data)


# --- month-end close ---------------------------------------------------------------------------

class PeriodSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The closed month's id (to open it again).")
    month = serializers.CharField(help_text="The month's first day (YYYY-MM-DD).")
    closed_by = serializers.DictField(allow_null=True, help_text="Who closed it: id and name.")
    closed_at = serializers.CharField(help_text="When (ISO 8601).")
    reason = serializers.CharField(help_text="The note given (may be empty).")


class WaitingDocumentSerializer(serializers.Serializer):
    what = serializers.CharField(help_text="The kind of document, e.g. Goods receipt.")
    number = serializers.CharField(help_text="Its number.")
    status = serializers.CharField(help_text="Its status.")


class PeriodsSerializer(serializers.Serializer):
    closed = PeriodSerializer(many=True, help_text="The closed months, newest first.")
    next_to_close = serializers.CharField(allow_null=True, help_text="The latest ended month still open (YYYY-MM-DD).")
    waiting = WaitingDocumentSerializer(many=True, help_text="Documents dated in that month not posted yet - they "
                                                             "must be posted, cancelled or re-dated before closing.")
    stock_check_differences = serializers.IntegerField(help_text="Differences between the ledger and the balances "
                                                                 "(must be 0 to close).")


class CloseSerializer(StrictSerializer):
    month = serializers.DateField(help_text="Any day in the month to close (YYYY-MM-DD); it must have ended.")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                   help_text="A note, e.g. Counted and checked with accounts.")


class ReopenSerializer(StrictSerializer):
    reason = serializers.CharField(max_length=255, help_text="Why the month is opened again (recorded).")


def _period_out(lock):
    who = lock.locked_by
    return {"id": lock.pk, "month": lock.month.isoformat(),
            "closed_by": {"id": who.pk, "name": who.get_full_name() or who.email} if who else None,
            "closed_at": plain(lock.created_at), "reason": lock.reason or ""}


CLOSERS = ["Close a stock month (inventory.period_close)"]


class InventoryPeriodsView(InventoryView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "inventory:period_close", "POST": "inventory:period_close"}

    @endpoint(id="inventory-periods", area=AREA, title="Month-end close",
              summary="The closed months, and what stands in the way of closing the next one.",
              what_it_does=["Lists the closed months; for the latest ended month still open: the documents dated "
                            "in it that are not posted, and the stock check."],
              description="A closed month takes no more stock entries dated in it; corrections go in the open month.",
              roles=CLOSERS, scopes=["inventory:read"], response=PeriodsSerializer,
              response_example={"closed": [{"id": 3, "month": "2026-08-01", "closed_by": {"id": 2, "name": "Nadia"},
                                            "closed_at": "2026-09-04T11:00:00+06:00", "reason": "Checked"}],
                                "next_to_close": "2026-09-01", "waiting": [], "stock_check_differences": 0},
              errors=ERRORS)
    def get(self, request):
        from inventory.doc_common import local_today
        from inventory.posting import ledger_differences
        from inventory.report_views import _ended_months

        membership = access.require(request.user, request.company_id, periods.CODE)
        locks = list(PeriodLock.objects.select_related("locked_by").order_by("-month"))
        closed = {lock.month for lock in locks}
        open_months = [m for m in _ended_months(local_today(membership.company)) if m not in closed]
        nxt = open_months[0] if open_months else None
        return Response(PeriodsSerializer({
            "closed": [_period_out(lock) for lock in locks], "next_to_close": nxt.isoformat() if nxt else None,
            "waiting": [{"what": w, "number": n, "status": s} for w, n, s in
                        (periods.waiting(request.company_id, nxt) if nxt else [])],
            "stock_check_differences": len(ledger_differences(request.company_id)),
        }).data)

    @endpoint(id="inventory-periods-close", area=AREA, title="Close a stock month",
              summary="Nothing more is posted dated in it.",
              what_it_does=["Closes the month; records it in the audit log."],
              description="Refused while documents dated in it are not posted, or the stock check finds differences, "
                          "or the month has not ended.",
              roles=CLOSERS, scopes=["inventory:write"], request=CloseSerializer, response=PeriodSerializer,
              response_status=201, request_example={"month": "2026-09-30", "reason": "Counted and checked"},
              response_example={"id": 4, "month": "2026-09-01", "closed_by": {"id": 2, "name": "Nadia"},
                                "closed_at": "2026-10-04T10:00:00+06:00", "reason": "Counted and checked"},
              errors=WRITE_ERRORS)
    def post(self, request):
        data = validated(CloseSerializer, request)
        with service_errors():
            lock = periods.close_month(actor=request.user, company_id=request.company_id, month=data["month"],
                                       reason=data.get("reason", ""))
        return Response(PeriodSerializer(_period_out(lock)).data, status=201)


class PeriodReopenResultSerializer(serializers.Serializer):
    month = serializers.CharField(help_text="The month opened again (YYYY-MM-DD).")


class InventoryPeriodReopenView(InventoryView):
    permission_classes = [PanelRule]
    panel_page = "inventory:period_reopen"

    @endpoint(id="inventory-periods-reopen", area=AREA, title="Open a stock month again",
              summary="Entries dated in it can be posted again until it is closed once more.",
              what_it_does=["Opens it; the reason is recorded in the audit log."],
              description="Use sparingly - figures already reported for the month may change.",
              roles=CLOSERS, scopes=["inventory:write"],
              params=[Param("period_id", PATH, "integer", "The closed month's id (closed[].id).", required=True,
                            example=4)],
              request=ReopenSerializer, response=PeriodReopenResultSerializer,
              request_example={"reason": "A late GRN for September"}, response_example={"month": "2026-09-01"},
              errors=WRITE_ERRORS + ONE)
    def post(self, request, period_id):
        if not PeriodLock.objects.filter(pk=period_id).exists():
            raise ApiError("not_found", "No such closed month.")
        reason = validated(ReopenSerializer, request)["reason"]
        with service_errors():
            month = periods.reopen_month(actor=request.user, company_id=request.company_id, lock_id=period_id,
                                         reason=reason)
        return Response(PeriodReopenResultSerializer({"month": month.isoformat()}).data)

