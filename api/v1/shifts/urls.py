from django.urls import path

from api.v1.shifts import views

urlpatterns = [
    path("schedule", views.OverviewView.as_view(), name="schedule"),
    path("attendance-settings", views.SettingsView.as_view(), name="attendance_settings"),
    path("shifts", views.ShiftListView.as_view(), name="shifts"),
    path("shifts/<int:shift_id>", views.ShiftDetailView.as_view(), name="shift"),
    path("shifts/<int:shift_id>/status", views.ShiftStatusView.as_view(), name="shift_status"),
    path("department-shifts", views.DepartmentShiftView.as_view(), name="department_shifts"),
    path("employees/<int:employee_id>/shifts", views.EmployeeShiftView.as_view(),
         name="employee_shifts"),
    path("employees/<int:employee_id>/shifts/<int:assignment_id>/end",
         views.EndEmployeeShiftView.as_view(), name="employee_shift_end"),
    path("weekly-offs", views.WeeklyOffListView.as_view(), name="weekly_offs"),
    path("weekly-offs/<int:rule_id>/start", views.WeeklyOffStartView.as_view(),
         name="weekly_off_start"),
    path("weekly-offs/<int:rule_id>/end", views.WeeklyOffEndView.as_view(),
         name="weekly_off_end"),
    path("holidays", views.HolidayListView.as_view(), name="holidays"),
    path("holidays/year", views.HolidayYearView.as_view(), name="holidays_year"),
    path("holidays/<int:holiday_id>", views.HolidayDetailView.as_view(), name="holiday"),
    path("holidays/<int:holiday_id>/cancel", views.HolidayCancelView.as_view(),
         name="holiday_cancel"),
]
