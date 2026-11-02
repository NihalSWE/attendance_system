"""Production pages open only when the company has the Production module, on
top of Store & Inventory (2026-10-27, plan §1)."""

from django.shortcuts import render

from inventory import access
from inventory.views import company_or_bail as inventory_or_bail


def company_or_bail(request):
    """``(company_id, None)``, or ``(None, response)`` when the page cannot open."""
    company_id, bail = inventory_or_bail(request)
    if bail:
        return company_id, bail
    if not access.production_on(company_id):
        return None, render(request, "inventory/not_enabled.html", {"module": "Production"}, status=404)
    return company_id, None
