from django.urls import path

from reports import views
from reports.catalogue import REPORTS

app_name = "reports"

urlpatterns = [
    # One name per report, so each opens by its own permission and menu entry.
    path(f"{report.slug}/", views.report, {"slug": report.slug},
         name=report.url_name.split(":", 1)[1])
    for report in REPORTS
]
