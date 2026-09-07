from django.contrib import admin

from .models import Employee, EmployeeAssignment, EmployeeCompensation


from common.admin import TenantOwnedAdmin


@admin.register(Employee)
class EmployeeAdmin(TenantOwnedAdmin):
    list_display = ("full_name", "company", "employment_status", "joining_date", "user")
    search_fields = ("first_name", "last_name", "work_email", "phone")
    list_filter = ("employment_status",)
    readonly_fields = ("public_id",)


@admin.register(EmployeeAssignment)
class EmployeeAssignmentAdmin(TenantOwnedAdmin):
    list_display = (
        "employee_code", "employee", "branch", "department", "designation",
        "effective_from", "effective_to", "status",
    )
    search_fields = ("employee_code", "employee__first_name", "employee__last_name")
    list_filter = ("status",)


@admin.register(EmployeeCompensation)
class EmployeeCompensationAdmin(TenantOwnedAdmin):
    list_display = (
        "employee", "pay_basis", "base_rate", "currency",
        "effective_from", "effective_to", "status",
    )
    search_fields = ("employee__first_name", "employee__last_name")
    list_filter = ("pay_basis", "status")
