"""Stock -> Quality check (2026-11-01, inventory/qc.py)."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import render
from inventory import access, qc
from inventory.hub_views import _common
from inventory.views import company_or_bail


def _branches(user, company_id):
    from access_control.branch_access import ALL_BRANCHES, branches_for

    found = branches_for(user, company_id, "inventory.view")
    return None if found is ALL_BRANCHES else found


@login_required
def qc_page(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    waiting = qc.waiting(company_id, branches=_branches(request.user, company_id))
    return render(request, "inventory/hub/qc.html", {
        **_common(request, company_id, "stock"), "waiting": waiting,
        "can_qc": access.may(request.user, company_id, qc.CODE),
        "made": sum(1 for row in waiting if row.source == "made"),
        "bought": sum(1 for row in waiting if row.source == "bought"),
    })


@login_required
@require_POST
def qc_decide(request, batch_id):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    passed = request.POST.get("result") == "passed"
    try:
        row = qc.decide(actor=request.user, company_id=company_id, batch_id=batch_id, passed=passed,
                        notes=request.POST.get("notes", ""))
    except PermissionDenied as exc:
        return fetch.refused(message=str(exc), status=403)
    except ValidationError as exc:
        return fetch.refused_errors(exc)
    back = request.META.get("HTTP_REFERER") or ""
    words = (f"Batch {row.batch.batch_no} passed: {row.qty.normalize():,f} {row.item.base_uom.code} can be used and sold now."
             if passed else f"Batch {row.batch.batch_no} failed and is blocked.")
    return fetch.saved(words, back.split("#")[0] + "#quality-check" if back else None)
