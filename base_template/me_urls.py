from django.contrib.auth.decorators import login_required
from django.urls import path

from attendance import scan_request_views
from base_template import me_views
from leaves import request_views
from organization import employee_self_views
from payroll import lfa_views

app_name = "me"

# The only pages an Employee or Branch manager login may open
# (common.middleware.SelfServiceGate lets this namespace through).
urlpatterns = [
    path("", me_views.my_account, name="home"),
    # My profile: their own details, photo and education (2026-09-27).
    path("profile/", employee_self_views.my_profile, name="profile"),
    path("profile/details/", employee_self_views.my_details, name="details"),
    path("profile/photo/", employee_self_views.my_photo_change, name="photo_change"),
    path("profile/photo/file/", employee_self_views.my_photo, name="photo"),
    path("profile/education/", employee_self_views.my_education_save, name="education_add"),
    path("profile/education/<int:row>/", employee_self_views.my_education_save,
         name="education_edit"),
    path("profile/education/<int:row>/remove/", employee_self_views.my_education_remove,
         name="education_remove"),
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
    path("leave/<int:pk>/document/", request_views.leave_document, name="leave_document"),
    path("leave-inbox/", request_views.leave_inbox, name="leave_inbox"),
    path("leave-inbox/<int:pk>/", request_views.leave_decide, name="leave_decide"),
    path("branch-attendance/", request_views.branch_attendance, name="branch_attendance"),
    path("payslips/", me_views.my_payslips, name="payslips"),
    path("lfa/", lfa_views.my_lfa, name="lfa"),
    path("lfa/<int:pk>/withdraw/", lfa_views.my_lfa_withdraw, name="lfa_withdraw"),
    path("lfa/<int:pk>/document/", lfa_views.lfa_document, name="lfa_document"),
    path("payslips/<int:pk>/", me_views.my_payslip, name="payslip"),
    path("password/", login_required(me_views.MyPasswordView.as_view()), name="password"),
]
