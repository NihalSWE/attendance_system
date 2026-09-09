from django.contrib import admin

from common.admin import TenantOwnedAdmin

from .models import (
    Branch,
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)


@admin.register(Branch)
class BranchAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "company", "is_default", "status")
    search_fields = ("name", "code", "city")
    list_filter = ("status", "is_default")


# --------------------------------------------------------------- catalogues
# Root-owned and global: plain ModelAdmin, not TenantOwnedAdmin, because these
# rows have no company column to scope by.


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "status")
    search_fields = ("name", "code")
    list_filter = ("status",)


@admin.register(Designation)
class DesignationAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "department", "status")
    search_fields = ("name", "code")
    list_filter = ("status", "department")


# ---------------------------------------------------------- company adoption


@admin.register(CompanyDepartment)
class CompanyDepartmentAdmin(TenantOwnedAdmin):
    list_display = ("department", "branch", "company", "head", "status")
    search_fields = ("department__name", "department__code")
    list_filter = ("status",)


@admin.register(CompanyDesignation)
class CompanyDesignationAdmin(TenantOwnedAdmin):
    list_display = ("designation", "company_department", "company", "status")
    search_fields = ("designation__name", "designation__code")
    list_filter = ("status",)
