"""The Warranty page (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §10; §15
phase 8): items with a warranty, serial numbers in and out, claims and what
they cost. Every form saves with fetch; a serial number typed into a claim is
looked up as you type (covered or not)."""

import io

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.http import Http404, HttpResponse, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import paginate, render
from common.tenant import use_company
from inventory import access, forms, warranty
from inventory.models import Item, SerialNumber, Warehouse, WarrantyClaim
from inventory.views import company_or_bail

VIEW = ("inventory.view", warranty.CODE)


def _stores(request, company_id):
    with use_company(company_id):
        rows = Warehouse.objects.filter(status="active").exclude(kind__in=("transit",))
        return list(access.scope_warehouses(rows, request.user, company_id, warranty.CODE))


@login_required
def warranty_page(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, VIEW)
    common = {"active": "warranty", "production": access.production_on(company_id)}
    if not warranty.warranty_on(company_id):
        membership = access.require_module(request.user, company_id)
        return render(request, "inventory/hub/warranty.html", {
            **common, "off": True, "can_switch": membership.role in ("owner", "company_admin")})
    can_record = access.may(request.user, company_id, warranty.CODE)
    with use_company(company_id):
        items = list(Item.objects.filter(Q(warranty_months__isnull=False) | Q(serial_numbers=True))
                     .annotate(out=Count("serials", filter=Q(serials__status="delivered")),
                               kept=Count("serials", filter=Q(serials__status="in_stock")))
                     .select_related("base_uom").order_by("code"))
        claims = paginate(request, WarrantyClaim.objects.select_related("item", "serial", "customer"),
                          search=("number", "item__name", "item__code", "serial__serial", "customer__name",
                                  "problem"),
                          order=("number", "claim_date", "item__name", "serial__serial", "customer__name",
                                 "covered", "outcome", "cost"))
        year = timezone.localdate().replace(month=1, day=1)
        this_year = WarrantyClaim.objects.filter(claim_date__gte=year)
        tiles = {"claims": this_year.count(), "cost": sum((c for c in this_year.values_list("cost", flat=True)), 0),
                 "out": SerialNumber.objects.filter(status="delivered").count()}
    per_item, problems = warranty.summary(company_id, year)
    stores = _stores(request, company_id)
    today = timezone.localdate()
    return render(request, "inventory/hub/warranty.html", {
        **common, "items": items, "rows": claims, "tiles": tiles, "per_item": per_item, "problems": problems,
        "can_record": can_record, "sees_costs": access.sees_costs(request.user, company_id),
        "claim_form": forms.ClaimForm(company_id=company_id, stores=stores, initial={"claim_date": today})
        if can_record else None,
        "serials_form": forms.SerialsForm(company_id=company_id, initial={"received_on": today})
        if can_record else None,
        "delivery_form": forms.DeliverySerialsForm(company_id=company_id) if can_record else None,
        "rejected": [store for store in stores if store.kind == Warehouse.Kind.REJECTED],
    })


@login_required
def serial_lookup(request):
    """What the claim form says under the serial number box."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, VIEW)
    found = warranty.lookup(company_id, request.GET.get("serial", ""))
    if found is None:
        return JsonResponse({"found": False, "text": "No unit with this serial number."})
    serial, item = found["serial"], found["item"]
    parts = [item.name, serial.get_status_display().lower()]
    if found["start"]:
        parts.append(f"{'delivered' if serial.delivered_on else 'received'} {found['start']:%d %b %Y}"
                     + (f" ({found['who']})" if found["who"] else ""))
    parts.append(found["words"] if found["until"] else "no warranty")
    return JsonResponse({"found": True, "covered": found["covered"], "stage": found["stage"],
                         "text": " · ".join(parts)})


def _answer(call, message, redirect=None):
    try:
        result = call()
    except ValidationError as exc:
        return fetch.refused_errors(exc)
    return fetch.saved(message(result), redirect or reverse("inventory:warranty"))


@login_required
@require_POST
def claim_save(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.ClaimForm(request.POST, company_id=company_id, stores=_stores(request, company_id))
    if not form.is_valid():
        return fetch.refused(form)
    return _answer(lambda: warranty.record_claim(actor=request.user, company_id=company_id,
                                                 values=form.cleaned_data),
                   lambda claim: f"{claim.number} saved: {claim.get_outcome_display().lower()}"
                                 + ("" if claim.covered else " (out of warranty)") + ".")


@login_required
@require_POST
def serials_add(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.SerialsForm(request.POST, company_id=company_id)
    if not form.is_valid():
        return fetch.refused(form)
    data = form.cleaned_data
    return _answer(lambda: warranty.register(actor=request.user, company_id=company_id, item=data["item"],
                                             serials=warranty.codes(data["serials"]),
                                             received_on=data["received_on"], supplier=data["supplier"]),
                   lambda made: f"{len(made)} serial number{'s' if len(made) != 1 else ''} of "
                                f"{data['item'].name} entered.")


@login_required
@require_POST
def serials_deliver(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.DeliverySerialsForm(request.POST, company_id=company_id)
    if not form.is_valid():
        return fetch.refused(form)
    data = form.cleaned_data
    return _answer(lambda: warranty.deliver(actor=request.user, company_id=company_id, delivery=data["delivery"],
                                            serials=warranty.codes(data["serials"])),
                   lambda count: f"{count} serial number{'s' if count != 1 else ''} out on "
                                 f"{data['delivery'].number}: their warranty runs from "
                                 f"{data['delivery'].delivery_date:%d %b %Y}.")


@login_required
def serial_labels(request):
    """Labels for serial numbers still in stock: of an item, or of what one
    production made (``?output=``)."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, (warranty.CODE, "inventory.labels"))
    with use_company(company_id):
        rows = SerialNumber.objects.filter(status="in_stock").select_related("item")
        if request.GET.get("output", "").isdigit():
            rows = rows.filter(source="production", source_id=int(request.GET["output"]))
        elif request.GET.get("item", "").isdigit():
            rows = rows.filter(item_id=int(request.GET["item"]))
        else:
            raise Http404("Say whose serial numbers to print.")
        rows = list(rows.order_by("serial")[:warranty.MOST])
    if not rows:
        raise Http404("No serial numbers in stock to print.")
    return HttpResponse(_labels_pdf(rows), content_type="application/pdf",
                        headers={"Content-Disposition": 'inline; filename="serial-numbers.pdf"'})


def _labels_pdf(rows):
    from reportlab.pdfgen import canvas

    from inventory import label_pdf

    width, height = label_pdf.size_of("50x25")
    buffer = io.BytesIO()
    c = canvas.Canvas(buffer)
    c.setTitle("Serial numbers")
    m = 1.6 * label_pdf.mm
    for row in rows:
        c.setPageSize((width, height))
        label_pdf.text(c, row.item.name, m, height - m - 7, 7, width - 2 * m, bold=True)
        label_pdf.draw_code128(c, row.serial, m, m + 9, width - 2 * m, height - m - 10 - (m + 9))
        label_pdf.text(c, row.serial, m, m + 1, 8, width - 2 * m, bold=True, centre=True)
        c.showPage()
    c.save()
    return buffer.getvalue()

