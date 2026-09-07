from django.contrib import admin

from .models import Branch, Department, Designation


from common.admin import TenantOwnedAdmin


@admin.register(Branch)
class BranchAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "company", "is_default", "status")
    search_fields = ("name", "code", "city")
    list_filter = ("status", "is_default")


@admin.register(Department)
class DepartmentAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "branch", "company", "status")
    search_fields = ("name", "code")
    list_filter = ("status",)


@admin.register(Designation)
class DesignationAdmin(TenantOwnedAdmin):
    list_display = ("name", "code", "department", "parent", "hierarchy_level", "status")
    search_fields = ("name", "code")
    list_filter = ("status",)
