"""Who may call an endpoint.

The API denies by default (settings: DenyAll). Every endpoint names its
permission classes itself - the documentation test refuses an endpoint that
does not - so nothing is ever open by accident. ``Public`` says, in so many
words, that an endpoint needs no login (only ``ping``, login, password reset).
"""

from rest_framework.permissions import BasePermission


class DenyAll(BasePermission):
    """The default for any endpoint that forgot to say who may use it."""

    message = "This endpoint is not open."

    def has_permission(self, request, view):
        return False


class Public(BasePermission):
    """No login needed. Use only for endpoints the plan lists as public."""

    def has_permission(self, request, view):
        return True
