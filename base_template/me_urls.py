from django.contrib.auth.decorators import login_required
from django.urls import path

from attendance import scan_request_views
from base_template import me_views
from leaves import request_views

app_name = "me"

# The only pages an Employee or Branch manager login may open
# (common.middleware.SelfServiceGate lets this namespace through).
urlpatterns = [
    path("", me_views.my_account, name="home"),
    path("attendance/", me_views.my_attendance, name="attendance"),
    # Missed-scan requests (Nihal's N11). Not under attendance/, whose <slug:on>
    # day URL would swallow them.
    path("missed-scans/", scan_request_views.my_missed_scans, name="missed_scans"),
    path("missed-scans/report/", scan_request_views.report_missed_scan, name="missed_scan_report"),
    path("missed-scans/<int:pk>/withdraw/", scan_request_views.withdraw_missed_scan,
         name="missed_scan_withdraw"),
    path("attendance/<slug:on>/", me_views.my_attendance_day, name="attendance_day"),
    path("leave/", me_views.my_leave, name="leave"),
    path("leave/request/", request_views.request_leave, name="leave_request"),
    path("leave/<int:pk>/withdraw/", request_views.withdraw_leave, name="leave_withdraw"),
    path("leave-inbox/", request_views.leave_inbox, name="leave_inbox"),
    path("leave-inbox/<int:pk>/", request_views.leave_decide, name="leave_decide"),
    path("branch-attendance/", request_views.branch_attendance, name="branch_attendance"),
    path("payslips/", me_views.my_payslips, name="payslips"),
    path("payslips/<int:pk>/", me_views.my_payslip, name="payslip"),
    path("password/", login_required(me_views.MyPasswordView.as_view()), name="password"),
]
