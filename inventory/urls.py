"""Store & Inventory routes (docs/inventory/00-PLAN.md, Phase 1)."""

from django.urls import path
from django.views.generic import RedirectView

from inventory import import_views, label_views, opening_views, stock_views, views

app_name = "inventory"

MASTERS = ("units", "categories", "items", "stores", "bins", "suppliers", "customers")

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="inventory:stock"), name="home"),
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
