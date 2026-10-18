"""Label and scan pages: the label register (print, reprint, void), printing
item / batch / bin / package labels, and the Scan page with its lookup.

A print answers with the PDF itself, so the print buttons post to a new tab
(``target="_blank"``) and the page they came from stays as it was.
"""

import datetime
from zoneinfo import ZoneInfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_GET, require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access, forms, labels, lookup
from inventory.models import Batch, BarcodeLabel, Item, Location
from inventory.views import branch_ids, company_or_bail


def _pdf(pdf, name):
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{name}.pdf"'
    return response


def _back(request, fallback):
    target = request.POST.get("next") or ""
    return target if target.startswith("/") and not target.startswith("//") else reverse(fallback)


def _print(request, company_id, label_list, name, fallback):
    """Print ``label_list`` (copies and reason from the form) and answer with the
    PDF, or go back with the reason it was refused."""
    try:
        pdf = labels.print_labels(actor=request.user, company_id=company_id,
                                  label_ids=[label.pk for label in label_list],
                                  copies=request.POST.get("copies") or 1,
                                  reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect(_back(request, fallback))
    return _pdf(pdf, name)


# --------------------------------------------------------------------------
# The register
# --------------------------------------------------------------------------

@login_required
def label_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    with use_company(company_id):
        rows = BarcodeLabel.objects.select_related("item", "batch", "uom", "warehouse", "location")
        kind = request.GET.get("kind", "")
        if kind in BarcodeLabel.Kind.values:
            rows = rows.filter(kind=kind)
        status = request.GET.get("status", "")
        if status in BarcodeLabel.Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows.order_by("-pk"),
                         search=("code", "item__code", "item__name", "batch__batch_no",
                                 "location__code", "warehouse__code"),
                         order=("code", "kind", "item__code", "batch__batch_no", "quantity",
                                "warehouse__code", "printed_count", "last_printed_at", "status", None))
    return render(request, "inventory/label_list.html", {
        "rows": table, "kind": kind, "kinds": BarcodeLabel.Kind.choices, "status": status,
        "statuses": BarcodeLabel.Status.choices,
        "can_print": access.may(request.user, company_id, labels.CODE),
        "print_form": forms.PrintForm(), "void_form": forms.VoidForm(),
    })


@login_required
@require_POST
def label_print(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    with use_company(company_id):
        label = BarcodeLabel.objects.filter(pk=pk).first()
    if label is None:
        raise PermissionDenied("Label not found in this company.")
    return _print(request, company_id, [label], label.code, "inventory:labels")


@login_required
@require_POST
def label_void(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        label = labels.void_label(actor=request.user, company_id=company_id, label_id=pk,
                                  reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, f"Label {label.code} is void.")
    return redirect(_back(request, "inventory:labels"))


# --------------------------------------------------------------------------
# Printing from the item, batch, bins and opening stock pages
# --------------------------------------------------------------------------

@login_required
@require_POST
def item_label_print(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        label = labels.item_label(actor=request.user, company_id=company_id, item_id=pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("inventory:item_detail", pk=pk)
    return _print(request, company_id, [label], label.code, "inventory:items")


@login_required
@require_POST
def batch_label_print(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    label = labels.batch_label(actor=request.user, company_id=company_id, batch_id=pk)
    return _print(request, company_id, [label], label.code, "inventory:items")


@login_required
@require_POST
def bin_labels_print(request):
    """One bin (``location``) or every active bin of a store (``store``)."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    store = request.GET.get("store") or request.POST.get("store", "")
    picked = request.GET.getlist("location") or request.POST.getlist("location")
    with use_company(company_id):
        if store.isdigit():
            ids = list(Location.objects.filter(warehouse_id=int(store),
                                               status="active").values_list("pk", flat=True))
        else:
            ids = [int(v) for v in picked if v.isdigit()]
    if not ids:
        messages.error(request, "That store has no active bins.")
        return redirect(_back(request, "inventory:bins"))
    made = labels.location_labels(actor=request.user, company_id=company_id, location_ids=ids)
    return _print(request, company_id, made, "bin-labels", "inventory:bins")


def _local_today(request):
    from tenants.models import Company

    company = Company.objects.get(pk=request.company_id)
    return timezone.now().astimezone(ZoneInfo(company.timezone or "Asia/Dhaka")).date()


@login_required
def package_labels(request, pk):
    """Labels for packages of item ``pk``: the batch, store and bin can come in
    the address (from the item page or an opening stock line)."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, labels.CODE)
    with use_company(company_id):
        item = Item.objects.select_related("base_uom").filter(pk=pk).first()
    if item is None:
        raise PermissionDenied("Item not found in this company.")
    initial = {"uom": item.base_uom_id, "count": 1}
    for key in ("batch", "warehouse", "location", "quantity", "count", "uom"):
        if request.GET.get(key):
            initial[key] = request.GET[key]
    form = forms.PackageLabelForm(request.POST or None, item=item, initial=initial,
                                  branch_ids=branch_ids(request.user, company_id, labels.CODE))
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            made = labels.package_labels(
                actor=request.user, company_id=company_id, item_id=item.pk,
                batch_id=data["batch"].pk if data["batch"] else None, uom_id=data["uom"].pk,
                quantity=data["quantity"], count=data["count"],
                warehouse_id=data["warehouse"].pk if data["warehouse"] else None,
                location_id=data["location"].pk if data["location"] else None,
                source_type=request.GET.get("source_type", "")[:32],
                source_id=int(request.GET["source_id"]) if request.GET.get("source_id", "").isdigit() else None,
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            pdf = labels.print_labels(actor=request.user, company_id=company_id,
                                      label_ids=[label.pk for label in made], copies=1)
            return _pdf(pdf, f"{item.code}-packages")
    return render(request, "inventory/package_labels.html", {"item": item, "form": form})


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------

@login_required
def scan(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    return render(request, "inventory/scan.html", {})


@login_required
@require_GET
def scan_lookup(request):
    """What one scanned code is, as JSON (the scan box asks this)."""
    if request.user.is_superuser or not request.company_id or not access.inventory_on(request.company_id):
        return JsonResponse({"found": False, "message": "Store & Inventory is not available."},
                            status=404)
    access.require(request.user, request.company_id, "inventory.view")
    result = lookup.lookup(request.company_id, request.GET.get("code", "")[:500],
                           today=_local_today(request))
    return JsonResponse(result)
