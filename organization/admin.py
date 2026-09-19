from django.contrib import admin

from common.admin import TenantOwnedAdmin

from .models import Branch, Department, Designation


@admin.register(Branch)
class BranchAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "company", "is_default", "status")
    search_fields = ("name", "code", "city")
    list_filter = ("status", "is_default")


@admin.register(Department)
class DepartmentAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "branch", "company", "head", "status")
    search_fields = ("name", "code", "branch__name")
    list_filter = ("status",)


@admin.register(Designation)
class DesignationAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "department", "company", "status")
    search_fields = ("name", "code", "department__name")
    list_filter = ("status",)
