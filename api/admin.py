from django.contrib import admin

from api.models import TwoStep


@admin.register(TwoStep)
class TwoStepAdmin(admin.ModelAdmin):
    """The last resort for someone locked out of two-step login: phone lost,
    no email getting through, recovery codes gone. Deleting the row resets it -
    an owner or administrator then sets the app up again at their next login.
    Nothing here shows or changes a secret."""

    list_display = ("user", "confirmed_at")
    search_fields = ("user__email",)
    fields = ("user", "confirmed_at")
    readonly_fields = ("user", "confirmed_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
