from django.contrib import admin

from .models import Company
from common.forms import BangladeshPhoneInput


@admin.register(Company)
class CompanyAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "slug", "status", "currency", "created_at")
    search_fields = ("name", "legal_name", "code", "slug", "email")
    list_filter = ("status",)
    readonly_fields = ("public_id", "code", "slug")

    class Media:
        css = {"all": ("base_template/css/input-conventions.css",)}
        js = ("base_template/js/forms.js",)

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name == "phone":
            kwargs["widget"] = BangladeshPhoneInput()
        return super().formfield_for_dbfield(db_field, request, **kwargs)
