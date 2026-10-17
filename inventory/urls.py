"""Store & Inventory routes (docs/inventory/00-PLAN.md, Phase 1)."""

from django.urls import path
from django.views.generic import RedirectView

from inventory import (
    golive_views,
    import_views,
    label_views,
    opening_views,
    report_views,
    stock_views,
    store_views,
    views,
)
from inventory.reports import REPORTS

app_name = "inventory"

MASTERS = ("units", "categories", "items", "stores", "bins", "suppliers", "customers")

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="inventory:dashboard"), name="home"),
    path("dashboard/", report_views.dashboard, name="dashboard"),
    path("reports/", report_views.report_index, name="reports"),
    *[path(f"reports/{report.slug}/", report_views.report, {"slug": report.slug},
           name=report.url_name.split(":", 1)[1]) for report in REPORTS],
    path("month-end/", report_views.period_close, name="period_close"),
    path("month-end/<int:pk>/reopen/", report_views.period_reopen, name="period_reopen"),
    # Phase 10: going live.
    path("go-live/", golive_views.golive, name="golive"),
    path("go-live/count-sheet/", golive_views.count_sheet, name="golive_count_sheet"),
    path("go-live/compare/", golive_views.compare, name="golive_compare"),
    path("opening/<int:pk>/labels/", golive_views.opening_labels, name="opening_labels"),
    path("stock/", stock_views.stock, name="stock"),
    path("items/<int:pk>/", stock_views.item_detail, name="item_detail"),
    path("items/<int:pk>/packs/", stock_views.pack_unit_add, name="pack_unit_add"),
    path("items/<int:pk>/packs/<int:pack_pk>/remove/", stock_views.pack_unit_remove,
         name="pack_unit_remove"),
    path("units/standard/", views.standard_units, name="standard_units"),
    path("settings/", views.settings_page, name="settings"),

    path("opening/", opening_views.opening_list, name="opening"),
    path("opening/<int:pk>/", opening_views.opening_detail, name="opening_detail"),
    path("opening/<int:pk>/lines/", opening_views.opening_line_add, name="opening_line_add"),
    path("opening/<int:pk>/lines/<int:line_pk>/remove/", opening_views.opening_line_remove,
         name="opening_line_remove"),
    path("opening/<int:pk>/post/", opening_views.opening_post, name="opening_post"),
    path("opening/<int:pk>/cancel/", opening_views.opening_cancel, name="opening_cancel"),
    path("opening/<int:pk>/reverse/", opening_views.opening_reverse, name="opening_reverse"),

    path("labels/", label_views.label_list, name="labels"),
    path("labels/<int:pk>/print/", label_views.label_print, name="label_print"),
    path("labels/<int:pk>/void/", label_views.label_void, name="label_void"),
    path("labels/bins/", label_views.bin_labels_print, name="bin_labels_print"),
    path("items/<int:pk>/label/", label_views.item_label_print, name="item_label_print"),
    path("items/<int:pk>/packages/", label_views.package_labels, name="package_labels"),
    path("batches/<int:pk>/label/", label_views.batch_label_print, name="batch_label_print"),
    path("scan/", label_views.scan, name="scan"),
    path("scan/lookup/", label_views.scan_lookup, name="scan_lookup"),

    # Phase 4: store operations, one set of views for the six documents.
    path("store/transfers/<int:pk>/receive/", store_views.transfer_receive, name="transfer_receive"),
    path("store/counts/<int:pk>/save/", store_views.count_save, name="count_save"),
    # Each document's list and page under its own name too (the menu marks
    # the right entry); the shared names below build the same addresses.
    *[p for key in store_views.DOCS for p in (
        path(f"store/{key}/", store_views.doc_list, {"key": key}, name=f"store_{key}"),
        path(f"store/{key}/<int:pk>/", store_views.doc_detail, {"key": key}, name=f"store_{key}_detail"),
    )],
    path("store/<slug:key>/", store_views.doc_list, name="store_list"),
    path("store/<slug:key>/<int:pk>/", store_views.doc_detail, name="store_detail"),
    path("store/<slug:key>/<int:pk>/lines/", store_views.doc_line_add, name="store_line_add"),
    path("store/<slug:key>/<int:pk>/lines/<int:line_pk>/remove/", store_views.doc_line_remove,
         name="store_line_remove"),
    path("store/<slug:key>/<int:pk>/scan/", store_views.doc_scan, name="store_scan"),
    path("store/<slug:key>/<int:pk>/<slug:action>/", store_views.doc_action, name="store_action"),

    path("import/<slug:kind>/", import_views.import_page, name="import"),
    path("import/<slug:kind>/confirm/", import_views.import_confirm, name="import_confirm"),
    path("import/<slug:kind>/template/", import_views.template_download, name="import_template"),
]

for key in MASTERS:
    urlpatterns += [
        path(f"{key}/", views.master_list, {"key": key}, name=key),
        path(f"{key}/<int:pk>/edit/", views.master_edit, {"key": key}, name=f"{key}_edit"),
        path(f"{key}/<int:pk>/status/", views.master_status, {"key": key}, name=f"{key}_status"),
    ]
