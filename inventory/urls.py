"""Store & Inventory routes (docs/inventory/00-PLAN.md, Phase 1)."""

from django.urls import path
from django.views.generic import RedirectView

from inventory import (
    flow_views,
    golive_views,
    hub_views,
    import_views,
    label_views,
    opening_views,
    preview_views,
    qc_views,
    report_views,
    stock_views,
    store_views,
    views,
    warranty_views,
)
from inventory.reports import REPORTS

app_name = "inventory"

MASTERS = ("units", "categories", "items", "stores", "bins", "suppliers", "customers",
           # One page per item type (2026-10-26).
           "raw_materials", "packaging", "finished_goods", "spare_parts", "general_items")

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

# The redesigned screens on real records (plan §15 phase 4).
urlpatterns += [
    path("start/", hub_views.start, name="start"),
    path("materials/", hub_views.materials, name="materials"),
    path("materials/<int:pk>/", hub_views.material, name="material"),
    path("products/", hub_views.products, name="products"),
    path("products/<int:pk>/", hub_views.product, name="product"),
    path("items/<int:pk>/save/", hub_views.item_save, name="item_save"),
    path("items/<int:pk>/price/", hub_views.item_price, name="item_price"),
    # Stock in, Stock, Move stock and Sell in the new look (2026-10-28, plan §15 9.4).
    path("stock-in/", flow_views.stock_in, name="stock_in"),
    path("stock-in/new/", flow_views.stock_in_new, name="stock_in_new"),
    path("stock/now/", flow_views.stock_now, name="stock_now"),
    # Batches waiting for a quality check, from any source (2026-11-01).
    path("stock/quality-check/", qc_views.qc_page, name="qc"),
    path("stock/quality-check/<int:batch_id>/", qc_views.qc_decide, name="qc_decide"),
    path("move/", flow_views.moves, name="moves"),
    path("move/new/", flow_views.move_new, name="move_new"),
    path("move/<int:pk>/receive/", flow_views.move_receive, name="move_receive"),
    path("sell/", flow_views.sales, name="sales"),
    path("sell/new/", flow_views.sale_new, name="sale_new"),
    # Warranty (2026-10-27, plan §10).
    path("warranty/", warranty_views.warranty_page, name="warranty"),
    path("warranty/serial/", warranty_views.serial_lookup, name="serial_lookup"),
    path("warranty/claims/", warranty_views.claim_save, name="claim_save"),
    path("warranty/serials/", warranty_views.serials_add, name="serials_add"),
    path("warranty/serials/delivered/", warranty_views.serials_deliver, name="serials_deliver"),
    path("warranty/serials/labels/", warranty_views.serial_labels, name="serial_labels"),
]

# The redesigned screens on sample data, for approval (plan §15 phase 2).
urlpatterns += [
    path("preview/", preview_views.home, name="preview_home"),
    path("preview/materials/", preview_views.materials, name="preview_materials"),
    path("preview/materials/<str:code>/", preview_views.material, name="preview_material"),
    path("preview/products/", preview_views.products, name="preview_products"),
    path("preview/products/<str:code>/", preview_views.product, name="preview_product"),
    path("preview/make/", preview_views.make, name="preview_make"),
    path("preview/make/new/", preview_views.plan, name="preview_plan"),
    path("preview/make/<str:number>/", preview_views.production, name="preview_production"),
    path("preview/stock-in/", preview_views.stock_in, name="preview_stock_in"),
    path("preview/stock-in/new/", preview_views.stock_in_new, name="preview_stock_in_new"),
    path("preview/stock/", preview_views.stock, name="preview_stock"),
    path("preview/move/", preview_views.moves, name="preview_moves"),
    path("preview/move/new/", preview_views.move_new, name="preview_move_new"),
    path("preview/sell/", preview_views.sales, name="preview_sales"),
    path("preview/sell/new/", preview_views.sale_new, name="preview_sale_new"),
    path("preview/warranty/", preview_views.warranty, name="preview_warranty"),
    path("preview/save/<slug:screen>/", preview_views.save, name="preview_save"),
]

for key in MASTERS:
    urlpatterns += [
        path(f"{key.replace('_', '-')}/", views.master_list, {"key": key}, name=key),
        path(f"{key.replace('_', '-')}/<int:pk>/edit/", views.master_edit, {"key": key},
             name=f"{key}_edit"),
        path(f"{key.replace('_', '-')}/<int:pk>/status/", views.master_status, {"key": key},
             name=f"{key}_status"),
    ]
# Delete an item nothing uses yet (2026-10-26), from the item pages.
# And a store nothing has used (2026-11-03).
for key in ("items", "raw_materials", "packaging", "finished_goods", "spare_parts", "general_items",
            "stores"):
    urlpatterns.append(path(f"{key.replace('_', '-')}/<int:pk>/delete/", views.item_delete,
                            {"key": key}, name=f"{key}_delete"))
