"""Sales routes (docs/inventory/00-PLAN.md, Phase 6)."""

from django.urls import path

from sales import views

app_name = "sales"

urlpatterns = [
    path("price-lists/", views.price_list_list, name="price_lists"),
    path("price-lists/<int:pk>/", views.price_list_detail, name="price_list_detail"),
    path("price-lists/<int:pk>/prices/<int:line_pk>/remove/", views.price_remove, name="price_remove"),

    path("orders/", views.order_list, name="orders"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/lines/", views.order_line_add, name="order_line_add"),
    path("orders/<int:pk>/lines/<int:line_pk>/remove/", views.order_line_remove, name="order_line_remove"),
    path("orders/<int:pk>/<slug:action>/", views.order_action, name="order_action"),

    path("deliveries/", views.delivery_list, name="deliveries"),
    path("deliveries/<int:pk>/", views.delivery_detail, name="delivery_detail"),
    path("deliveries/<int:pk>/lines/", views.delivery_line_add, name="delivery_line_add"),
    path("deliveries/<int:pk>/lines/<int:line_pk>/remove/", views.delivery_line_remove,
         name="delivery_line_remove"),
    path("deliveries/<int:pk>/scan/", views.delivery_scan, name="delivery_scan"),
    path("deliveries/<int:pk>/challan/", views.delivery_pdf, name="delivery_pdf"),
    path("deliveries/<int:pk>/<slug:action>/", views.delivery_action, name="delivery_action"),

    path("invoices/", views.invoice_list, name="invoices"),
    path("invoices/<int:pk>/", views.invoice_detail, name="invoice_detail"),
    path("invoices/<int:pk>/pdf/", views.invoice_pdf_view, name="invoice_pdf"),
    path("invoices/<int:pk>/<slug:action>/", views.invoice_action, name="invoice_action"),

    path("returns/", views.return_list, name="returns"),
    path("returns/<int:pk>/", views.return_detail, name="return_detail"),
    path("returns/<int:pk>/lines/", views.return_line_add, name="return_line_add"),
    path("returns/<int:pk>/lines/<int:line_pk>/remove/", views.return_line_remove, name="return_line_remove"),
    path("returns/<int:pk>/<slug:action>/", views.return_action, name="return_action"),
]
