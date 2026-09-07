from django.contrib import admin

from .models import (
    CompanyAttendanceSettings,
    DepartmentShift,
    EmployeeShiftAssignment,
    Holiday,
    HolidayWorkAssignment,
    Shift,
    WeeklyOffRule,
)


from common.admin import TenantOwnedAdmin


@admin.register(Shift)
class ShiftAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "company", "start_time", "end_time",
                    "spans_next_day", "scheduled_minutes", "status")
    search_fields = ("name", "code")
    list_filter = ("status", "spans_next_day")


@admin.register(CompanyAttendanceSettings)
class CompanyAttendanceSettingsAdmin(TenantOwnedAdmin):
    list_display = ("company", "shift_mode", "company_shift",
                    "device_attendance_scope", "settings_version")
    list_filter = ("shift_mode", "device_attendance_scope")


@admin.register(DepartmentShift)
class DepartmentShiftAdmin(TenantOwnedAdmin):
    list_display = ("department", "shift", "is_default", "effective_from",
                    "effective_to", "status")
    list_filter = ("status", "is_default")


@admin.register(EmployeeShiftAssignment)
class EmployeeShiftAssignmentAdmin(TenantOwnedAdmin):
    list_display = ("employee", "shift", "assignment_type", "effective_from",
                    "effective_to", "status")
    list_filter = ("assignment_type", "status")


@admin.register(WeeklyOffRule)
class WeeklyOffRuleAdmin(TenantOwnedAdmin):
    list_display = ("weekday", "branch", "company", "is_paid",
                    "effective_from", "effective_to", "status")
    list_filter = ("weekday", "is_paid", "status")


@admin.register(Holiday)
class HolidayAdmin(TenantOwnedAdmin):
    list_display = ("name", "holiday_date", "branch", "company", "is_paid", "status")
    search_fields = ("name",)
    list_filter = ("status", "is_paid")


@admin.register(HolidayWorkAssignment)
class HolidayWorkAssignmentAdmin(TenantOwnedAdmin):
    list_display = ("employee", "work_date", "holiday", "weekly_off_rule",
                    "treatment", "status")
    list_filter = ("treatment", "status")
