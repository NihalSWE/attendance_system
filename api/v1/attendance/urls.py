from django.urls import path

from api.v1.attendance import views

urlpatterns = [
    path("attendance", views.DailyListView.as_view(), name="attendance"),
    path("attendance/late", views.LateListView.as_view(), name="attendance_late"),
    path("attendance/export", views.ExportView.as_view(), name="attendance_export"),
    path("attendance/now", views.NowView.as_view(), name="attendance_now"),
    path("attendance/calendar", views.CalendarView.as_view(), name="attendance_calendar"),
    path("attendance/calendar/export", views.CalendarExportView.as_view(),
         name="attendance_calendar_export"),
    path("attendance/review", views.ReviewView.as_view(), name="attendance_review"),
    path("attendance/days/<int:employee_id>/<str:date>", views.DayView.as_view(),
         name="attendance_day"),
    path("attendance/days/<int:employee_id>/<str:date>/add-scan", views.AddScanView.as_view(),
         name="attendance_add_scan"),
    path("attendance/days/<int:employee_id>/<str:date>/change-status",
         views.ChangeStatusView.as_view(), name="attendance_change_status"),
    path("attendance/days/<int:employee_id>/<str:date>/accept-review",
         views.AcceptReviewView.as_view(), name="attendance_accept_review"),
    path("attendance/days/<int:employee_id>/<str:date>/excuse-late",
         views.ExcuseLateView.as_view(), name="attendance_excuse_late"),
    path("attendance/corrections/<int:correction_id>/withdraw", views.WithdrawView.as_view(),
         name="attendance_withdraw"),
    path("missed-scans", views.MissedScanListView.as_view(), name="missed_scans"),
    path("missed-scans/<int:request_id>/decide", views.MissedScanDecideView.as_view(),
         name="missed_scan_decide"),
    path("employees/<int:employee_id>/missing-attendance", views.EnterMissingView.as_view(),
         name="employee_missing_attendance"),
]
