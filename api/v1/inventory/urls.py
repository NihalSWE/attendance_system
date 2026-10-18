"""Store & Inventory routes: masters, stock, store documents, purchase,
production, sales, reports and month-end close (Phase 9 of the inventory plan)."""

from django.urls import path

from api.v1.inventory import masters, production, purchase, reports, sales, stock, store
from api.v1.inventory.engine import document_urls, master_urls

urlpatterns = [
    path("inventory/stock", stock.StockView.as_view(), name="inventory_stock"),
    path("inventory/items/<int:item_id>/ledger", stock.LedgerView.as_view(), name="inventory_item_ledger"),
    path("inventory/batches", stock.BatchesView.as_view(), name="inventory_batches"),
    path("inventory/labels/lookup", stock.LookupView.as_view(), name="inventory_labels_lookup"),
    path("inventory/dashboard", reports.InventoryDashboardView.as_view(), name="inventory_dashboard"),
    path("inventory/reports", reports.InventoryReportListView.as_view(), name="inventory_reports"),
    path("inventory/reports/<slug:slug>", reports.InventoryReportView.as_view(), name="inventory_report"),
    path("inventory/reports/<slug:slug>/export", reports.InventoryReportExportView.as_view(),
         name="inventory_report_export"),
    path("inventory/periods", reports.InventoryPeriodsView.as_view(), name="inventory_periods"),
    path("inventory/periods/<int:period_id>/reopen", reports.InventoryPeriodReopenView.as_view(),
         name="inventory_period_reopen"),
    path("sales/intake", sales.IntakeView.as_view(), name="sales_intake"),
]

for spec in masters.SPECS:
    urlpatterns += master_urls(spec)
for spec in store.SPECS + purchase.SPECS + production.SPECS + sales.SPECS:
    urlpatterns += document_urls(spec)
