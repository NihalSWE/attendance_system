"""Purchase routes (docs/inventory/00-PLAN.md, Phase 3)."""

from django.urls import path

from purchasing import views

app_name = "purchasing"

urlpatterns = [
    path("requisitions/", views.requisition_list, name="requisitions"),
    path("requisitions/<int:pk>/", views.requisition_detail, name="requisition_detail"),
    path("requisitions/<int:pk>/lines/", views.requisition_line_add, name="requisition_line_add"),
    path("requisitions/<int:pk>/lines/<int:line_pk>/remove/", views.requisition_line_remove,
         name="requisition_line_remove"),
    path("requisitions/<int:pk>/submit/", views.requisition_submit, name="requisition_submit"),
    path("requisitions/<int:pk>/decide/", views.requisition_decide, name="requisition_decide"),
    path("requisitions/<int:pk>/cancel/", views.requisition_cancel, name="requisition_cancel"),

    path("orders/", views.order_list, name="orders"),
    path("orders/from-requisitions/", views.order_from_requisitions, name="order_from_requisitions"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/lines/", views.order_line_add, name="order_line_add"),
    path("orders/<int:pk>/lines/<int:line_pk>/remove/", views.order_line_remove, name="order_line_remove"),
    path("orders/<int:pk>/pdf/", views.order_pdf_view, name="order_pdf"),
    path("orders/<int:pk>/email/", views.order_email, name="order_email"),
    path("orders/<int:pk>/<slug:action>/", views.order_action, name="order_action"),

    path("receipts/", views.receipt_list, name="receipts"),
    path("receipts/<int:pk>/", views.receipt_detail, name="receipt_detail"),
    path("receipts/<int:pk>/lines/", views.receipt_line_add, name="receipt_line_add"),
    path("receipts/<int:pk>/lines/<int:line_pk>/remove/", views.receipt_line_remove,
         name="receipt_line_remove"),
    path("receipts/<int:pk>/labels/", views.receipt_labels_print, name="receipt_labels"),
    path("receipts/<int:pk>/<slug:action>/", views.receipt_action, name="receipt_action"),

    path("quality-check/", views.qc_list, name="qc"),
    path("quality-check/<int:line_pk>/", views.qc_record, name="qc_record"),

    path("returns/", views.return_list, name="returns"),
    path("returns/<int:pk>/", views.return_detail, name="return_detail"),
    path("returns/<int:pk>/lines/", views.return_line_add, name="return_line_add"),
    path("returns/<int:pk>/lines/<int:line_pk>/remove/", views.return_line_remove,
         name="return_line_remove"),
    path("returns/<int:pk>/<slug:action>/", views.return_action, name="return_action"),
]
