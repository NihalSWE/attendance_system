from django.contrib.auth.decorators import login_required
from django.urls import path

from base_template import me_views
from leaves import request_views

app_name = "me"

# The only pages an Employee or Branch manager login may open
# (common.middleware.SelfServiceGate lets this namespace through).
urlpatterns = [
    path("", me_views.my_account, name="home"),
    path("attendance/", me_views.my_attendance, name="attendance"),
    path("attendance/<slug:on>/", me_views.my_attendance_day, name="attendance_day"),
    path("leave/", me_views.my_leave, name="leave"),
    path("leave/request/", request_views.request_leave, name="leave_request"),
    path("leave-inbox/", request_views.leave_inbox, name="leave_inbox"),
    path("leave-inbox/<int:pk>/", request_views.leave_decide, name="leave_decide"),
    path("branch-attendance/", request_views.branch_attendance, name="branch_attendance"),
    path("payslips/", me_views.my_payslips, name="payslips"),
    path("payslips/<int:pk>/", me_views.my_payslip, name="payslip"),
    path("password/", login_required(me_views.MyPasswordView.as_view()), name="password"),
]
