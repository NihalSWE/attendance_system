"""Explicit root-only, company-bound Django admin support."""
from django.contrib import admin
from django.core.exceptions import PermissionDenied
from common.tenant import use_company


class TenantOwnedAdmin(admin.ModelAdmin):
    class Media:
        css = {"all": ("base_template/css/input-conventions.css",)}
        js = ("base_template/js/forms.js",)

    def has_module_permission(self, request):
        return request.user.is_active and request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_module_permission(request)

    has_add_permission = has_view_permission
    has_change_permission = has_view_permission
    has_delete_permission = has_view_permission

    def get_queryset(self, request):
        if not self.has_module_permission(request):
            raise PermissionDenied
        return self.model.all_objects.all()

    def changeform_view(self, request, object_id=None, form_url="", extra_context=None):
        if not self.has_module_permission(request):
            raise PermissionDenied
        obj = self.get_object(request, object_id) if object_id else None
        try:
            company_id = obj.company_id if obj else int(request.POST.get("company", -1))
        except (TypeError, ValueError):
            company_id = -1
        request.admin_company_id = company_id
        with use_company(company_id):
            response = super().changeform_view(request, object_id, form_url, extra_context)
            if hasattr(response, "render"):
                response.render()
            return response

    def formfield_for_foreignkey(self, db_field, request, **kwargs):
        model = db_field.remote_field.model
        if hasattr(model, "all_objects"):
            kwargs["queryset"] = model.all_objects.filter(company_id=getattr(request, "admin_company_id", -1))
        return super().formfield_for_foreignkey(db_field, request, **kwargs)

    def formfield_for_manytomany(self, db_field, request, **kwargs):
        model = db_field.remote_field.model
        if hasattr(model, "all_objects"):
            kwargs["queryset"] = model.all_objects.filter(company_id=getattr(request, "admin_company_id", -1))
        return super().formfield_for_manytomany(db_field, request, **kwargs)

    def get_readonly_fields(self, request, obj=None):
        fields = list(super().get_readonly_fields(request, obj))
        if obj:
            fields.append("company")
        return fields
