"""Go-live pages (Phase 10): the readiness checklist, count sheets, labels for
opening stock, and the parallel-run comparison."""

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from base_template.tables import render
from common import exports
from common.forms import StyledFormMixin
from common.tenant import use_company
from inventory import access, labels
from inventory import golive as service
from inventory.label_views import _pdf
from inventory.models import ItemKind, Warehouse
from inventory.views import company_or_bail

CODES = ("inventory.adjust", "inventory.adjust_approve")


class CountSheetForm(StyledFormMixin, forms.Form):
    store = forms.ModelChoiceField(queryset=Warehouse.all_objects.none(), label="Store")
    kind = forms.ChoiceField(choices=[("", "Every item type")] + list(ItemKind.choices), required=False,
                             label="Item type")
    everything = forms.BooleanField(required=False, label="Every active item, not only the store's own")
    file_type = forms.ChoiceField(choices=[("xlsx", "Excel (fill in and import)"), ("pdf", "PDF (print)")],
                                  label="As")

    def __init__(self, *args, stores=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["store"].queryset = stores


class CompareForm(StyledFormMixin, forms.Form):
    upload = forms.FileField(label="The old system's stock list (.xlsx, .xls or .csv)",
                             help_text="Headings: Item code, Quantity - and Store code and Unit if it has them.")
    download = forms.BooleanField(required=False, label="Download the comparison as Excel")


def _stores(request, company_id):
    return access.scope_warehouses(Warehouse.objects.filter(status="active").order_by("code"), request.user,
                                   company_id, "inventory.adjust")


@login_required
def golive(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, CODES)
    sections = service.readiness(company_id)
    with use_company(company_id):
        stores = _stores(request, company_id)
        count_form = CountSheetForm(stores=stores, initial={"file_type": "xlsx"})
    return render(request, "inventory/golive.html", {
        "sections": sections, "totals": service.totals(sections), "count_form": count_form,
        "compare_form": CompareForm(), "comparison": None,
    })


@login_required
def count_sheet(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.adjust")
    with use_company(company_id):
        form = CountSheetForm(request.GET, stores=_stores(request, company_id))
        if not form.is_valid():
            messages.error(request, "Choose a store for the count sheet.")
            return redirect("inventory:golive")
        data = form.cleaned_data
        content, name = service.count_sheet(company_id, data["store"], kind=data["kind"],
                                           everything=data["everything"], fmt=data["file_type"])
    return exports.response(content, data["file_type"], name)


@login_required
@require_POST
def compare(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    form = CompareForm(request.POST, request.FILES)
    comparison = None
    if form.is_valid():
        try:
            differences, problems = service.compare(company_id, form.cleaned_data["upload"])
        except ValidationError as exc:
            form.add_error("upload", " ".join(exc.messages))
        else:
            if form.cleaned_data["download"]:
                rows = [[d.item, d.name, d.store, d.old, d.ours, d.difference, d.unit, d.status] for d in differences]
                content = exports.table_xlsx(
                    title="Parallel run", lines=[f"{sum(d.status != 'Same' for d in differences)} of {len(rows)} "
                                                 "lines differ"] + problems[:20],
                    headers=["Item", "Name", "Store", "Old system", "Software", "Difference", "Unit", "Status"],
                    rows=rows, numeric=[3, 4, 5])
                return exports.response(content, "xlsx", "parallel-run.xlsx")
            comparison = {"rows": differences, "problems": problems,
                          "differ": sum(d.status != "Same" for d in differences)}
    sections = service.readiness(company_id) if access.may(request.user, company_id, CODES[0]) or access.may(
        request.user, company_id, CODES[1]) else []
    with use_company(company_id):
        stores = _stores(request, company_id)
    return render(request, "inventory/golive.html", {
        "sections": sections, "totals": service.totals(sections), "count_form": CountSheetForm(stores=stores),
        "compare_form": form, "comparison": comparison,
    })


@login_required
@require_POST
def opening_labels(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        made = service.opening_labels(actor=request.user, company_id=company_id, document_id=pk)
        if not made:
            raise ValidationError("Nothing to label on this document.")
        pdf = labels.print_labels(actor=request.user, company_id=company_id, label_ids=[l.pk for l in made],
                                  copies=request.POST.get("copies") or 1, reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("inventory:opening_detail", pk=pk)
    return _pdf(pdf, f"opening-{pk}-labels")
