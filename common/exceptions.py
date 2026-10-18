"""Refusals shared across apps."""

from django.core.exceptions import PermissionDenied


class NotInThisCompany(PermissionDenied):
    """A row asked for by id that is not in the actor's company (2026-10-18).

    Still a ``PermissionDenied``, so every service and view that catches one
    keeps working, and the API keeps its documented answer. The web panel
    answers it as "not found" (``common.middleware.NotFoundOutsideCompany``):
    a refusal would confirm that the number exists somewhere.
    """
