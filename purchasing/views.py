"""Purchase pages: requisitions, orders, goods receipts, quality check, returns.

Thin adapters over ``purchasing.requisitions`` / ``orders`` / ``receipts`` /
``returns``. Each document has a list (server-side table), a page with its
lines (added in a modal) and its actions (confirmed in a modal). The module
gate and "not found" come from ``inventory.views.company_or_bail``.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.views.decorators.http import require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access
from inventory.forms import PrintForm, ReasonForm
from inventory.views import branch_ids, company_or_bail
from purchasing import forms, order_pdf, orders, receipts, requisitions, returns
from purchasing.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    PurchaseOrder,
    PurchaseRequisition,
    PurchaseReturn,
)


def _may(request, code):
    return access.may(request.user, request.company_id, code)


def _act(request, action, success, target, *args):
    """Run a POST action; success or the refusal as a message; back to ``target``."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        result = action(company_id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, success.format(obj=result))
    return redirect(target, *args)


def _status_filter(request, rows, choices):
    status = request.GET.get("status", "")
    if status in dict(choices):
        rows = rows.filter(status=status)
    return rows, status


def _list(request, *, template, rows, search, order, choices, extra=None):
    with use_company(request.company_id):
        rows, status = _status_filter(request, rows, choices)
        table = paginate(request, rows, search=search, order=order)
    return render(request, template, {"rows": table, "status": status, "statuses": choices,
                                      **(extra or {})})


# --------------------------------------------------------------------------
# Requisitions
# --------------------------------------------------------------------------

def _requisition_form(request, data=None):
    return forms.RequisitionForm(data, company_id=request.company_id,
                                 branch_ids=branch_ids(request.user, request.company_id,
                                                       "purchase.request"))


@login_required
def requisition_list(request, new_form=None):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    if request.method == "POST":
        access.require(request.user, company_id, "purchase.request")
        new_form = _requisition_form(request, request.POST)
        if new_form.is_valid():
            try:
                row = requisitions.create_requisition(actor=request.user, company_id=company_id,
                                                      values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{row.number} started. Add what is needed.")
                return redirect("purchasing:requisition_detail", pk=row.pk)
    can_raise = _may(request, "purchase.request")
    with use_company(company_id):
        rows = access.scope_warehouses(
            PurchaseRequisition.objects.select_related("branch", "department"), request.user,
            company_id, "purchase.request") | access.scope_warehouses(
            PurchaseRequisition.objects.select_related("branch", "department"), request.user,
            company_id, "purchase.approve")
    return _list(request, template="purchasing/doc_list.html", rows=rows.distinct(),
                 search=("number", "reason", "branch__name", "department__name"),
                 order=("number", "request_date", "branch__name", "department__name", "needed_by",
                        "status", None),
                 choices=PurchaseRequisition.Status.choices,
                 extra={"page": {"title": 'Purchase requisitions', "about": 'A department asks to buy; an approver says yes or no; approved lines become purchase orders.', "columns": ('Number', 'Date', 'Branch', 'Department', 'Needed by', 'Status'), "row": 'purchasing/rows/requisition.html', "new_label": 'New requisition', "url": 'purchasing:requisitions'}, "can_new": can_raise, "can_raise": can_raise,
                        "new_form": new_form or (_requisition_form(request) if can_raise else None),
                        "open_dialog": "new-dialog" if new_form is not None else ""})


def _requisition_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    membership, row = requisitions.get_requisition(actor=request.user, company_id=company_id, pk=pk,
                                                   code="inventory.view")
    with use_company(company_id):
        lines = list(row.lines.select_related("item__base_uom", "uom").order_by("pk"))
    can_edit = row.status == row.Status.DRAFT and access.may(request.user, company_id,
                                                              "purchase.request", row.branch_id)
    can_decide = row.status == row.Status.SUBMITTED and access.may(
        request.user, company_id, "purchase.approve", row.branch_id)
    return render(request, "purchasing/requisition_detail.html", {
        "doc": row, "lines": lines, "can_edit": can_edit, "can_decide": can_decide,
        "can_order": row.status == row.Status.APPROVED and _may(request, "purchase.order"),
        "can_cancel": row.status in ("draft", "submitted", "approved", "rejected") and access.may(
            request.user, company_id, "purchase.request", row.branch_id),
        "line_form": line_form or (forms.RequisitionLineForm(company_id=company_id) if can_edit else None),
        "note_form": forms.NoteForm(), "open_dialog": open_dialog,
    })


@login_required
def requisition_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _requisition_page(request, pk)


@login_required
@require_POST
def requisition_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    requisitions.get_requisition(actor=request.user, company_id=company_id, pk=pk)
    form = forms.RequisitionLineForm(request.POST, company_id=company_id)
    if form.is_valid():
        try:
            requisitions.add_line(actor=request.user, company_id=company_id, requisition_id=pk,
                                  values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("purchasing:requisition_detail", pk=pk)
    return _requisition_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def requisition_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: requisitions.remove_line(
        actor=request.user, company_id=cid, requisition_id=pk, line_id=line_pk),
        "Line removed.", "purchasing:requisition_detail", pk)


@login_required
@require_POST
def requisition_submit(request, pk):
    return _act(request, lambda cid: requisitions.submit(actor=request.user, company_id=cid,
                                                         requisition_id=pk),
                "{obj.number} sent for approval.", "purchasing:requisition_detail", pk)


@login_required
@require_POST
def requisition_decide(request, pk):
    approve = request.POST.get("decision") == "approve"
    return _act(request, lambda cid: requisitions.decide(
        actor=request.user, company_id=cid, requisition_id=pk, approve=approve,
        note=request.POST.get("note", "")),
        "{obj.number} " + ("approved." if approve else "rejected."),
        "purchasing:requisition_detail", pk)


@login_required
@require_POST
def requisition_cancel(request, pk):
    return _act(request, lambda cid: requisitions.cancel(actor=request.user, company_id=cid,
                                                         requisition_id=pk),
                "{obj.number} cancelled.", "purchasing:requisition_detail", pk)


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------

def _order_form(request, data=None, cls=forms.OrderForm):
    return cls(data, company_id=request.company_id,
               branch_ids=branch_ids(request.user, request.company_id, "purchase.order"))


@login_required
def order_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, "purchase.order")
        new_form = _order_form(request, request.POST)
        if new_form.is_valid():
            try:
                row = orders.create_order(actor=request.user, company_id=company_id,
                                          values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{row.number} started. Add what is ordered.")
                return redirect("purchasing:order_detail", pk=row.pk)
    can_order = _may(request, "purchase.order")
    with use_company(company_id):
        rows = (access.scope_warehouses(PurchaseOrder.objects.select_related("supplier", "branch"),
                                        request.user, company_id, "purchase.order")
                | access.scope_warehouses(PurchaseOrder.objects.select_related("supplier", "branch"),
                                          request.user, company_id, "purchase.approve")
                | access.scope_warehouses(PurchaseOrder.objects.select_related("supplier", "branch"),
                                          request.user, company_id, "purchase.receive")).distinct()
    return _list(request, template="purchasing/doc_list.html", rows=rows,
                 search=("number", "supplier__name", "supplier__code", "note"),
                 order=("number", "order_date", "supplier__name", "branch__name", "expected_date",
                        "status", None),
                 choices=PurchaseOrder.Status.choices,
                 extra={"page": {"title": 'Purchase orders', "about": 'Orders to suppliers: approved, sent as a PDF, then received in one or more goods receipts.', "columns": ('Number', 'Date', 'Supplier', 'Branch', 'Delivery by', 'Status'), "row": 'purchasing/rows/order.html', "new_label": 'New order', "url": 'purchasing:orders'}, "can_new": can_order, "can_order": can_order,
                        "new_form": new_form or (_order_form(request) if can_order else None),
                        "open_dialog": "new-dialog" if new_form is not None else ""})


@login_required
def order_from_requisitions(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "purchase.order")
    form = _order_form(request, request.POST or None, cls=forms.FromRequisitionsForm)
    if request.method == "POST" and form.is_valid():
        values = {k: v for k, v in form.cleaned_data.items() if k != "requisition_lines"}
        try:
            row = orders.order_from_requisitions(
                actor=request.user, company_id=company_id, values=values,
                requisition_line_ids=[line.pk for line in form.cleaned_data["requisition_lines"]])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{row.number} made from the requisitions. Check the prices.")
            return redirect("purchasing:order_detail", pk=row.pk)
    return render(request, "purchasing/order_from_requisitions.html", {"form": form})


def _order_page(request, pk, line_form=None, email_form=None, open_dialog=""):
    company_id = request.company_id
    _, row = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    with use_company(company_id):
        lines = list(row.lines.select_related("item__base_uom", "uom", "requisition_line__requisition")
                     .order_by("pk"))
        receipts_made = list(row.receipts.order_by("-received_date", "-pk"))
        figures = orders.totals(row)
    may_order = access.may(request.user, company_id, "purchase.order", row.branch_id)
    can_edit = row.status == row.Status.DRAFT and may_order
    return render(request, "purchasing/order_detail.html", {
        "doc": row, "lines": lines, "receipts": receipts_made, "totals": figures,
        "can_edit": can_edit, "may_order": may_order,
        "can_decide": row.status == row.Status.SUBMITTED and access.may(
            request.user, company_id, "purchase.approve", row.branch_id),
        "can_receive": row.receivable and access.may(request.user, company_id, "purchase.receive",
                                                     row.branch_id),
        "line_form": line_form or (forms.OrderLineForm(company_id=company_id) if can_edit else None),
        "email_form": email_form or forms.EmailForm(initial={"to": row.supplier.email}),
        # One note form per dialog: the same field name, ids of their own.
        "cancel_form": forms.NoteForm(auto_id="cancel_%s"),
        "close_form": forms.NoteForm(auto_id="close_%s"),
        "back_form": forms.NoteForm(auto_id="back_%s"), "open_dialog": open_dialog,
    })


@login_required
def order_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _order_page(request, pk)


@login_required
@require_POST
def order_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    orders.get_order(actor=request.user, company_id=company_id, pk=pk)
    form = forms.OrderLineForm(request.POST, company_id=company_id)
    if form.is_valid():
        try:
            orders.add_line(actor=request.user, company_id=company_id, order_id=pk,
                            values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("purchasing:order_detail", pk=pk)
    return _order_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def order_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: orders.remove_line(actor=request.user, company_id=cid,
                                                        order_id=pk, line_id=line_pk),
                "Line removed.", "purchasing:order_detail", pk)


@login_required
@require_POST
def order_action(request, pk, action):
    note = request.POST.get("note", "")
    actions = {
        "submit": (lambda cid: orders.submit(actor=request.user, company_id=cid, order_id=pk),
                   "{obj.number} sent for approval."),
        "approve": (lambda cid: orders.approve(actor=request.user, company_id=cid, order_id=pk),
                    "{obj.number} approved: send it to the supplier."),
        "send_back": (lambda cid: orders.send_back(actor=request.user, company_id=cid, order_id=pk,
                                                   note=note), "{obj.number} sent back to draft."),
        "cancel": (lambda cid: orders.cancel(actor=request.user, company_id=cid, order_id=pk, note=note),
                   "{obj.number} cancelled."),
        "close": (lambda cid: orders.close(actor=request.user, company_id=cid, order_id=pk, note=note),
                  "{obj.number} closed."),
    }
    if action not in actions:
        return redirect("purchasing:order_detail", pk=pk)
    run, success = actions[action]
    return _act(request, run, success, "purchasing:order_detail", pk)


@login_required
def order_pdf_view(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    with use_company(company_id):
        content, filename = order_pdf.build(row)
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


@login_required
@require_POST
def order_email(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    form = forms.EmailForm(request.POST)
    if form.is_valid():
        try:
            to = order_pdf.email_order(actor=request.user, company_id=company_id, order_id=pk,
                                       to=form.cleaned_data["to"], message=form.cleaned_data["message"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"Sent to {to}.")
            return redirect("purchasing:order_detail", pk=pk)
    return _order_page(request, pk, email_form=form, open_dialog="email-dialog")


# --------------------------------------------------------------------------
# Goods receipts
# --------------------------------------------------------------------------

def _receipt_form(request, data=None, initial=None):
    return forms.ReceiptForm(data, initial=initial, company_id=request.company_id,
                             branch_ids=branch_ids(request.user, request.company_id, "purchase.receive"))


@login_required
def receipt_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, "purchase.receive")
        new_form = _receipt_form(request, request.POST)
        if new_form.is_valid():
            try:
                row = receipts.create_receipt(actor=request.user, company_id=company_id,
                                              values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{row.number} started" + (
                    " with the order's open quantities: correct what really came." if row.order_id
                    else ". Add what arrived."))
                return redirect("purchasing:receipt_detail", pk=row.pk)
    can_receive = _may(request, "purchase.receive")
    initial = {}
    if request.GET.get("order", "").isdigit():
        initial["order"] = int(request.GET["order"])
    with use_company(company_id):
        rows = access.scope_warehouses(
            GoodsReceipt.objects.select_related("supplier", "warehouse", "order"), request.user,
            company_id, "inventory.view", field="warehouse__branch")
    return _list(request, template="purchasing/doc_list.html", rows=rows,
                 search=("number", "supplier__name", "order__number", "challan_no", "invoice_no"),
                 order=("number", "received_date", "supplier__name", "order__number",
                        "warehouse__code", "status", None),
                 choices=GoodsReceipt.Status.choices,
                 extra={"page": {"title": 'Goods receipts (GRN)', "about": 'What arrived at a store, against an order or direct. Posting puts it in stock; items needing QC wait in quarantine.', "columns": ('Number', 'Received', 'Supplier', 'Order', 'Store', 'Status'), "row": 'purchasing/rows/receipt.html', "new_label": 'New goods receipt', "url": 'purchasing:receipts'}, "can_new": can_receive, "can_receive": can_receive,
                        "new_form": new_form or (_receipt_form(request, initial=initial)
                                                 if can_receive else None),
                        "open_dialog": "new-dialog" if new_form is not None or initial else ""})


def _receipt_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, row = receipts.get_receipt(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    with use_company(company_id):
        lines = list(row.lines.select_related("item__base_uom", "uom", "location", "order_line", "batch")
                     .order_by("pk"))
        made = list(receipts.receipt_labels(row)) if row.status != "draft" else []
    may_receive = access.may(request.user, company_id, "purchase.receive", row.warehouse.branch_id)
    can_edit = row.status == row.Status.DRAFT and may_receive
    return render(request, "purchasing/receipt_detail.html", {
        "doc": row, "lines": lines, "labels": made, "can_edit": can_edit,
        "can_post": can_edit and bool(lines),
        "can_reverse": row.status == row.Status.POSTED and may_receive,
        "can_print": made and may_receive,
        "can_qc": access.may(request.user, company_id, "purchase.qc", row.warehouse.branch_id),
        "sees_costs": access.sees_costs(request.user, company_id) or may_receive,
        "line_form": line_form or (forms.ReceiptLineForm(company_id=company_id, receipt=row)
                                   if can_edit else None),
        "reason_form": ReasonForm(), "qc_form": forms.QcForm(), "print_form": PrintForm(),
        "open_dialog": open_dialog,
    })


@login_required
def receipt_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _receipt_page(request, pk)


@login_required
@require_POST
def receipt_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = receipts.get_receipt(actor=request.user, company_id=company_id, pk=pk)
    form = forms.ReceiptLineForm(request.POST, company_id=company_id, receipt=row)
    if form.is_valid():
        try:
            receipts.add_line(actor=request.user, company_id=company_id, receipt_id=pk,
                              values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("purchasing:receipt_detail", pk=pk)
    return _receipt_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def receipt_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: receipts.remove_line(actor=request.user, company_id=cid,
                                                          receipt_id=pk, line_id=line_pk),
                "Line removed.", "purchasing:receipt_detail", pk)


@login_required
@require_POST
def receipt_action(request, pk, action):
    actions = {
        "post": (lambda cid: receipts.post_receipt(actor=request.user, company_id=cid, receipt_id=pk),
                 "{obj.number} posted: the goods are in stock."),
        "cancel": (lambda cid: receipts.cancel_receipt(actor=request.user, company_id=cid, receipt_id=pk),
                   "{obj.number} cancelled."),
        "reverse": (lambda cid: receipts.reverse_receipt(actor=request.user, company_id=cid,
                                                         receipt_id=pk, reason=request.POST.get("reason", "")),
                    "{obj.number} reversed."),
    }
    if action not in actions:
        return redirect("purchasing:receipt_detail", pk=pk)
    run, success = actions[action]
    return _act(request, run, success, "purchasing:receipt_detail", pk)


@login_required
@require_POST
def receipt_labels_print(request, pk):
    from inventory import labels as label_service

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = receipts.get_receipt(actor=request.user, company_id=company_id, pk=pk)
    with use_company(company_id):
        made = list(receipts.receipt_labels(row).exclude(status="void").values_list("pk", flat=True))
    try:
        pdf = label_service.print_labels(actor=request.user, company_id=company_id, label_ids=made,
                                         copies=request.POST.get("copies") or 1,
                                         reason=request.POST.get("reason", ""),
                                         permission="purchase.receive")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("purchasing:receipt_detail", pk=pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{row.number}-labels.pdf"'
    return response


# --------------------------------------------------------------------------
# Quality check
# --------------------------------------------------------------------------

@login_required
def qc_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "purchase.qc")
    with use_company(company_id):
        rows = access.scope_warehouses(
            GoodsReceiptLine.objects.filter(receipt__status="posted").exclude(
                qc_status=GoodsReceiptLine.Qc.NOT_REQUIRED).select_related(
                "receipt__supplier", "receipt__warehouse", "item__base_uom", "uom", "batch"),
            request.user, company_id, "purchase.qc", field="receipt__warehouse__branch")
        state = request.GET.get("qc", "pending")
        if state in GoodsReceiptLine.Qc.values:
            rows = rows.filter(qc_status=state)
        table = paginate(request, rows.order_by("-receipt__received_date", "-pk"),
                         search=("receipt__number", "item__code", "item__name", "batch__batch_no",
                                 "receipt__supplier__name"),
                         order=("receipt__number", "receipt__received_date", "item__code",
                                "batch__batch_no", None, "receipt__supplier__name", "qc_status", None))
    return render(request, "purchasing/qc_list.html", {
        "rows": table, "state": state, "states": GoodsReceiptLine.Qc.choices[1:],
        "qc_form": forms.QcForm()})


@login_required
@require_POST
def qc_record(request, line_pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        receipts.record_check(actor=request.user, company_id=company_id, line_id=line_pk,
                              result=request.POST.get("result", ""), notes=request.POST.get("notes", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "QC result saved; the batch follows it.")
    target = request.POST.get("next") or ""
    return redirect(target if target.startswith("/") and not target.startswith("//") else "purchasing:qc")


# --------------------------------------------------------------------------
# Purchase returns
# --------------------------------------------------------------------------

@login_required
def return_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, "purchase.receive")
        new_form = forms.ReturnForm(request.POST, company_id=company_id)
        if new_form.is_valid():
            try:
                row = returns.create_return(actor=request.user, company_id=company_id,
                                            values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{row.number} started. Add what goes back.")
                return redirect("purchasing:return_detail", pk=row.pk)
    can_enter = _may(request, "purchase.receive")
    initial = {}
    if request.GET.get("receipt", "").isdigit():
        initial["receipt"] = int(request.GET["receipt"])
    return _list(request, template="purchasing/doc_list.html",
                 rows=PurchaseReturn.objects.select_related("supplier", "receipt"),
                 search=("number", "supplier__name", "receipt__number", "reason"),
                 order=("number", "return_date", "supplier__name", "receipt__number", "reason",
                        "status", None),
                 choices=PurchaseReturn.Status.choices,
                 extra={"page": {"title": 'Purchase returns', "about": "Goods sent back to the supplier: failed QC, damaged, wrong item. Posting takes them out of stock at the receipt's cost.", "columns": ('Number', 'Date', 'Supplier', 'From receipt', 'Why', 'Status'), "row": 'purchasing/rows/return.html', "new_label": 'New return', "url": 'purchasing:returns'}, "can_new": can_enter, "can_enter": can_enter,
                        "new_form": new_form or (forms.ReturnForm(company_id=company_id, initial=initial)
                                                 if can_enter else None),
                        "open_dialog": "new-dialog" if new_form is not None or initial else ""})


def _return_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, row = returns.get_return(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    with use_company(company_id):
        lines = list(row.lines.select_related("item__base_uom", "uom", "batch", "warehouse", "location",
                                              "receipt_line__receipt").order_by("pk"))
        returnable = returns.returnable_lines(company_id, row) if row.status == "draft" else []
    can_edit = row.status == row.Status.DRAFT and _may(request, "purchase.receive")
    return render(request, "purchasing/return_detail.html", {
        "doc": row, "lines": lines, "can_edit": can_edit,
        "can_post": row.status == row.Status.DRAFT and bool(lines) and _may(request, "purchase.approve"),
        "can_reverse": row.status == row.Status.POSTED and _may(request, "purchase.approve"),
        "line_form": line_form or (forms.ReturnLineForm(company_id=company_id, returnable=returnable)
                                   if can_edit else None),
        "reason_form": ReasonForm(), "open_dialog": open_dialog,
    })


@login_required
def return_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _return_page(request, pk)


@login_required
@require_POST
def return_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, row = returns.get_return(actor=request.user, company_id=company_id, pk=pk)
    with use_company(company_id):
        returnable = returns.returnable_lines(company_id, row)
    form = forms.ReturnLineForm(request.POST, company_id=company_id, returnable=returnable)
    if form.is_valid():
        try:
            returns.add_line(actor=request.user, company_id=company_id, return_id=pk,
                             values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("purchasing:return_detail", pk=pk)
    return _return_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def return_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: returns.remove_line(actor=request.user, company_id=cid,
                                                         return_id=pk, line_id=line_pk),
                "Line removed.", "purchasing:return_detail", pk)


@login_required
@require_POST
def return_action(request, pk, action):
    actions = {
        "post": (lambda cid: returns.post_return(actor=request.user, company_id=cid, return_id=pk),
                 "{obj.number} posted: the goods are out of stock."),
        "cancel": (lambda cid: returns.cancel_return(actor=request.user, company_id=cid, return_id=pk),
                   "{obj.number} cancelled."),
        "reverse": (lambda cid: returns.reverse_return(actor=request.user, company_id=cid, return_id=pk,
                                                       reason=request.POST.get("reason", "")),
                    "{obj.number} reversed."),
    }
    if action not in actions:
        return redirect("purchasing:return_detail", pk=pk)
    run, success = actions[action]
    return _act(request, run, success, "purchasing:return_detail", pk)
