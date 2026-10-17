"""Opening stock pages: the list, one document (its lines, Post, Reverse),
and the Excel import that makes a draft from the filled template."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access, forms, opening
from inventory.models import DocumentStatus, OpeningStock
from inventory.views import branch_ids, company_or_bail


def _list(request, company_id, new_form=None, open_dialog=""):
    access.require(request.user, company_id, "inventory.view")
    can_enter = access.may(request.user, company_id, "inventory.adjust")
    with use_company(company_id):
        table = paginate(request, OpeningStock.objects.all(),
                         search=("number", "note", "status"),
                         order=("number", "work_date", "status", "note", None))
    if can_enter and new_form is None:
        new_form = forms.OpeningStockForm()
    return render(request, "inventory/opening_list.html", {
        "rows": table, "can_enter": can_enter, "new_form": new_form, "open_dialog": open_dialog,
    })


@login_required
def opening_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if request.method != "POST":
        return _list(request, company_id)
    access.require(request.user, company_id, "inventory.adjust")
    form = forms.OpeningStockForm(request.POST)
    if form.is_valid():
        try:
            document = opening.create_document(actor=request.user, company_id=company_id,
                                               values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{document.number} started. Add the counted stock.")
            return redirect("inventory:opening_detail", pk=document.pk)
    return _list(request, company_id, new_form=form, open_dialog="new-dialog")


def _detail(request, company_id, pk, line_form=None, open_dialog=""):
    _, document = opening.get_document(actor=request.user, company_id=company_id, pk=pk)
    sees_costs = access.sees_costs(request.user, company_id)
    with use_company(company_id):
        lines = list(document.lines.select_related("item__base_uom", "warehouse", "location", "uom")
                     .order_by("pk"))
    can_enter = document.editable and access.may(request.user, company_id, "inventory.adjust")
    can_post = access.may(request.user, company_id, "inventory.adjust_approve")
    # Once posted, each line's packages can be labelled (the count day: label
    # every bag as it is counted). The batch exists only after posting.
    can_label = (document.status == DocumentStatus.POSTED
                 and access.may(request.user, company_id, "inventory.labels"))
    if can_label:
        from inventory.models import Batch

        batches = {(b.item_id, b.batch_no): b.pk for b in Batch.all_objects.filter(
            company_id=company_id, item_id__in={line.item_id for line in lines})}
        for line in lines:
            line.batch_pk = batches.get((line.item_id, line.batch_no))
        from inventory.models import BarcodeLabel

        labels_printed = BarcodeLabel.all_objects.filter(
            company_id=company_id, batch_id__in=[line.batch_pk for line in lines if line.batch_pk],
            printed_count__gt=0).exists()
    if can_enter and line_form is None:
        line_form = forms.OpeningLineForm(company_id=company_id,
                                          branch_ids=branch_ids(request.user, company_id,
                                                                "inventory.adjust"))
    return render(request, "inventory/opening_detail.html", {
        "document": document, "lines": lines, "sees_costs": sees_costs,
        "total": sum((line.value for line in lines), Decimal("0")),
        "can_enter": can_enter, "can_post": can_post and document.editable and bool(lines),
        "can_reverse": can_post and document.status == DocumentStatus.POSTED,
        "can_label": can_label, "print_form": forms.PrintForm() if can_label else None,
        "labels_printed": can_label and labels_printed,
        "line_form": line_form, "reason_form": forms.ReasonForm(), "open_dialog": open_dialog,
    })


@login_required
def opening_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _detail(request, company_id, pk)


@login_required
@require_POST
def opening_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.adjust")
    form = forms.OpeningLineForm(request.POST, company_id=company_id,
                                 branch_ids=branch_ids(request.user, company_id, "inventory.adjust"))
    if form.is_valid():
        try:
            opening.add_line(actor=request.user, company_id=company_id, document_id=pk,
                             values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("inventory:opening_detail", pk=pk)
    return _detail(request, company_id, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def opening_line_remove(request, pk, line_pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        opening.remove_line(actor=request.user, company_id=company_id, document_id=pk,
                            line_id=line_pk)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Line removed.")
    return redirect("inventory:opening_detail", pk=pk)


def _action(request, pk, action, success):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        document = action(company_id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, success.format(number=document.number))
    return redirect("inventory:opening_detail", pk=pk)


@login_required
@require_POST
def opening_post(request, pk):
    return _action(request, pk, lambda cid: opening.post_document(
        actor=request.user, company_id=cid, document_id=pk),
        "{number} posted: the stock is on hand.")


@login_required
@require_POST
def opening_cancel(request, pk):
    return _action(request, pk, lambda cid: opening.cancel_document(
        actor=request.user, company_id=cid, document_id=pk), "{number} cancelled.")


@login_required
@require_POST
def opening_reverse(request, pk):
    return _action(request, pk, lambda cid: opening.reverse_document(
        actor=request.user, company_id=cid, document_id=pk,
        reason=request.POST.get("reason", "")),
        "{number} reversed: its stock is taken back out.")
