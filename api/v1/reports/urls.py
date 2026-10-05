from django.urls import path

from api.v1.reports import views

urlpatterns = [
    path("reports", views.ReportListView.as_view(), name="reports"),
    path("reports/<slug:slug>", views.ReportView.as_view(), name="report"),
    path("reports/<slug:slug>/export", views.ReportExportView.as_view(), name="report_export"),
    path("dashboard", views.DashboardView.as_view(), name="dashboard"),
]
