"""Admin registration for the custom user model.

Uses Django's UserAdmin adapted for email login so the platform-root
superuser can be managed from /admin/ during development.
"""

from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import CompanyMembership, User
from common.admin import TenantOwnedAdmin


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    ordering = ("email",)
    list_display = ("email", "first_name", "last_name", "is_staff", "is_active")
    search_fields = ("email", "first_name", "last_name", "phone")

    fieldsets = (
        (None, {"fields": ("email", "password")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone",
                                       "timezone", "language", "username")}),
        ("Permissions", {"fields": ("is_active", "is_staff", "is_superuser",
                                     "groups", "user_permissions")}),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "password1", "password2"),
        }),
    )


@admin.register(CompanyMembership)
class CompanyMembershipAdmin(TenantOwnedAdmin):
    list_display = ("user", "company", "role", "status", "joined_at")
    search_fields = ("user__email", "company__name")
    list_filter = ("role", "status")

