"""Production routes (docs/inventory/00-PLAN.md, Phase 5)."""

from django.urls import path

from production import cost_views, recipe_views, views

app_name = "production"

urlpatterns = [
    # Cost rates and a product's costs of one batch (2026-10-27, plan §7.4).
    path("cost-rates/", cost_views.cost_rates, name="cost_rates"),
    path("cost-rates/<int:pk>/", cost_views.cost_rate_save, name="cost_rate_save"),
    path("cost-rates/<int:pk>/status/", cost_views.cost_rate_status, name="cost_rate_status"),
    path("products/<int:pk>/costs/", cost_views.cost_line_add, name="cost_line_add"),
    path("products/<int:pk>/costs/<int:line_pk>/remove/", cost_views.cost_line_remove,
         name="cost_line_remove"),
    # The Materials card on a finished good's page (2026-10-26).
    path("recipes/<int:pk>/batch/", recipe_views.recipe_batch, name="recipe_batch"),
    path("recipes/<int:pk>/materials/", recipe_views.recipe_add, name="recipe_add"),
    path("recipes/<int:pk>/materials/<int:material_pk>/remove/", recipe_views.recipe_remove,
         name="recipe_remove"),
    path("boms/", views.bom_list, name="boms"),
    path("boms/<int:pk>/", views.bom_detail, name="bom_detail"),
    path("boms/<int:pk>/lines/", views.bom_line_add, name="bom_line_add"),
    path("boms/<int:pk>/lines/<int:line_pk>/remove/", views.bom_line_remove, name="bom_line_remove"),
    path("boms/<int:pk>/<slug:action>/", views.bom_action, name="bom_action"),

    path("orders/", views.order_list, name="orders"),
    path("orders/<int:pk>/", views.order_detail, name="order_detail"),
    path("orders/<int:pk>/output/", views.order_output, name="order_output"),
    path("orders/<int:pk>/labels/", views.order_labels, name="order_labels"),
    path("orders/<int:pk>/<slug:action>/", views.order_action, name="order_action"),

    path("materials/<int:pk>/", views.consumption_detail, name="consumption_detail"),
    path("materials/<int:pk>/lines/", views.consumption_line_add, name="consumption_line_add"),
    path("materials/<int:pk>/lines/<int:line_pk>/remove/", views.consumption_line_remove,
         name="consumption_line_remove"),
    path("materials/<int:pk>/scan/", views.consumption_scan, name="consumption_scan"),
    path("materials/<int:pk>/<slug:action>/", views.consumption_action, name="consumption_action"),

    path("report/", views.report, name="report"),
]
