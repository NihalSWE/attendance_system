"""Sales pages: price lists, sales orders, deliveries, invoices and returns.
Thin adapters over ``sales.orders`` and ``sales.deliveries``."""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access
from inventory.forms import ReasonForm
from inventory.views import branch_ids, company_or_bail
from sales import deliveries, forms, invoice_pdf, orders
from sales.models import Delivery, PriceList, SalesInvoice, SalesOrder, SalesReturn

#: Who may look at sales documents: anyone who views stock or takes part.
VIEW = ("inventory.view", "sales.order", "sales.approve", "sales.deliver", "sales.invoice")


def _act(request, run, success, target, *args):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    try:
        result = run(company_id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, success.format(obj=result))
    return redirect(target, *args)


def _pdf(content, filename):
    response = HttpResponse(content, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{filename}"'
    return response


def _list(request, template, rows, search, order, statuses, extra):
    with use_company(request.company_id):
        status = request.GET.get("status", "")
        if status in dict(statuses):
            rows = rows.filter(status=status)
        table = paginate(request, rows, search=search, order=order)
    return render(request, template, {"rows": table, "status": status, "statuses": statuses, **extra})


def _create(request, form, create, success, target):
    """Post a header form: the new document's page, or the form back in its modal."""
    if form.is_valid():
        try:
            row = create(form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            shortages = getattr(row, "shortages", [])
            messages.success(request, success.format(obj=row) + (
                " Not enough stock: " + "; ".join(shortages) + "." if shortages else ""))
            return redirect(target, pk=row.pk)
    return None


# --------------------------------------------------------------------------
# Price lists
# --------------------------------------------------------------------------

@login_required
def price_list_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_edit = access.may(request.user, company_id, "sales.order")
    form = None
    if request.method == "POST":
        access.require(request.user, company_id, "sales.order")
        form = forms.PriceListForm(request.POST, company_id=company_id)
        done = _create(request, form, lambda v: orders.create_price_list(actor=request.user, company_id=company_id,
                                                                         values=v),
                       "Price list “{obj.name}” made. Add its prices.", "sales:price_list_detail")
        if done:
            return done
    return _list(request, "sales/price_list_list.html", PriceList.objects.all(), ("name", "note"),
                 ("name", "valid_from", "valid_to", "status", None), (), {
                     "can_edit": can_edit, "open_dialog": "new-dialog" if form is not None else "",
                     "new_form": form or (forms.PriceListForm(company_id=company_id) if can_edit else None)})


def _price_list_page(request, pk, form=None, open_dialog=""):
    company_id = request.company_id
    access.require(request.user, company_id, VIEW)
    with use_company(company_id):
        price_list = PriceList.objects.filter(pk=pk).first()
        if price_list is None:
            from django.core.exceptions import PermissionDenied

            raise PermissionDenied("Price list not found.")
        lines = list(price_list.lines.select_related("item", "uom").order_by("item__code", "min_qty"))
    can_edit = access.may(request.user, company_id, "sales.order")
    return render(request, "sales/price_list_detail.html", {
        "price_list": price_list, "lines": lines, "can_edit": can_edit, "open_dialog": open_dialog,
        "price_form": form or (forms.PriceForm(company_id=company_id) if can_edit else None)})


@login_required
def price_list_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if request.method == "POST":
        access.require(request.user, company_id, "sales.order")
        form = forms.PriceForm(request.POST, company_id=company_id)
        if form.is_valid():
            try:
                orders.set_price(actor=request.user, company_id=company_id, price_list_id=pk,
                                 values=form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(form, exc)
            else:
                messages.success(request, "Price saved.")
                return redirect("sales:price_list_detail", pk=pk)
        return _price_list_page(request, pk, form=form, open_dialog="price-dialog")
    return _price_list_page(request, pk)


@login_required
@require_POST
def price_remove(request, pk, line_pk):
    return _act(request, lambda cid: orders.remove_price(actor=request.user, company_id=cid, price_list_id=pk,
                                                         line_id=line_pk), "Price removed.",
                "sales:price_list_detail", pk)


# --------------------------------------------------------------------------
# Sales orders
# --------------------------------------------------------------------------

def _order_form(request, data=None):
    return forms.OrderForm(data, company_id=request.company_id,
                           branch_ids=branch_ids(request.user, request.company_id, "sales.order"))


@login_required
def order_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_new = access.may(request.user, company_id, "sales.order")
    form = None
    if request.method == "POST":
        access.require(request.user, company_id, "sales.order")
        form = _order_form(request, request.POST)
        done = _create(request, form, lambda v: orders.create_order(actor=request.user, company_id=company_id,
                                                                    values=v),
                       "{obj.number} started. Add what is ordered.", "sales:order_detail")
        if done:
            return done
    with use_company(company_id):
        rows = access.scope_warehouses(SalesOrder.objects.select_related("customer", "warehouse"), request.user,
                                       company_id, VIEW, field="warehouse__branch")
    return _list(request, "sales/order_list.html", rows,
                 ("number", "customer__name", "customer__code", "customer_ref", "note"),
                 ("number", "order_date", "customer__name", "source", "delivery_date", "status", None),
                 SalesOrder.Status.choices,
                 {"can_new": can_new, "open_dialog": "new-dialog" if form is not None else "",
                  "new_form": form or (_order_form(request) if can_new else None)})


def _line_form(request, data=None):
    return forms.OrderLineForm(data, company_id=request.company_id,
                               branch_ids=branch_ids(request.user, request.company_id, "sales.order"))


def _order_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    branch = order.warehouse.branch_id
    with use_company(company_id):
        lines = list(order.lines.select_related("item__base_uom", "uom", "warehouse").order_by("pk"))
        figures = orders.totals(order)
        available = orders.availability(order) if order.status == "draft" else {}
        over, credit_message = orders.credit_check(order) if order.status == "draft" else (False, "")
        made = list(order.deliveries.order_by("-pk"))
    for line in lines:
        line.available = available.get(line.pk)
    may_order = access.may(request.user, company_id, "sales.order", branch)
    stores = {line.warehouse_id for line in lines}
    return render(request, "sales/order_detail.html", {
        "mixed": bool(stores - {order.warehouse_id}),
        "order": order, "lines": lines, "totals": figures, "deliveries": made,
        "over_credit": over, "credit_message": credit_message,
        "can_edit": order.status == "draft" and may_order, "may_order": may_order,
        "can_confirm": order.status == "draft" and bool(lines) and access.may(request.user, company_id,
                                                                              "sales.approve", branch),
        "can_deliver": order.deliverable and any(
            access.may(request.user, company_id, "sales.deliver", store.branch_id)
            for store in deliveries.open_stores(order)),
        "line_form": line_form or (_line_form(request) if order.status == "draft" and may_order else None),
        "credit_form": forms.NoteForm(auto_id="credit_%s"), "cancel_form": forms.NoteForm(auto_id="cancel_%s"),
        "close_form": forms.NoteForm(auto_id="close_%s"), "open_dialog": open_dialog,
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
    form = _line_form(request, request.POST)
    if form.is_valid():
        try:
            orders.add_line(actor=request.user, company_id=company_id, order_id=pk, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line added.")
            return redirect("sales:order_detail", pk=pk)
    return _order_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def order_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: orders.remove_line(actor=request.user, company_id=cid, order_id=pk,
                                                        line_id=line_pk), "Line removed.", "sales:order_detail", pk)


@login_required
@require_POST
def order_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    note = request.POST.get("note", "")
    if action == "deliver":
        _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="sales.deliver")
        from inventory.doc_common import local_today

        try:
            made, skipped = deliveries.deliver_order(
                actor=request.user, company_id=company_id, order_id=order.pk,
                delivery_date=max(order.order_date, local_today(order.company)))
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("sales:order_detail", pk=pk)
        # One challan per store (Phase 8).
        short = [s for delivery in made for s in getattr(delivery, "shortages", [])]
        names = ", ".join(f"{d.number} ({d.warehouse.code})" for d in made) if len(made) > 1 else made[0].number
        messages.success(request, f"{names} drafted from the reserved stock." + (
            " Short: " + "; ".join(short) + "." if short else " Check, or scan the cartons, then post.")
            + (" Not drafted: " + "; ".join(skipped) + "." if skipped else ""))
        if len(made) > 1:
            return redirect("sales:order_detail", pk=pk)
        return redirect("sales:delivery_detail", pk=made[0].pk)
    simple = {
        "confirm": (lambda cid: orders.confirm(actor=request.user, company_id=cid, order_id=pk, credit_note=note),
                    "{obj.number} confirmed: stock reserved."),
        "reserve": (lambda cid: orders.reserve_again(actor=request.user, company_id=cid, order_id=pk),
                    "Reserved again."),
        "cancel": (lambda cid: orders.cancel(actor=request.user, company_id=cid, order_id=pk, note=note),
                   "{obj.number} cancelled: its stock is free."),
        "close": (lambda cid: orders.close(actor=request.user, company_id=cid, order_id=pk, note=note),
                  "{obj.number} closed."),
    }
    if action not in simple:
        return redirect("sales:order_detail", pk=pk)
    run, success = simple[action]
    try:
        result = run(company_id)
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        short = getattr(result, "shortages", [])
        messages.success(request, success.format(obj=result) + (" Short: " + "; ".join(short) + "." if short else ""))
    return redirect("sales:order_detail", pk=pk)


# --------------------------------------------------------------------------
# Deliveries
# --------------------------------------------------------------------------

@login_required
def delivery_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_new = access.may(request.user, company_id, "sales.deliver")
    form = None
    if request.method == "POST":
        access.require(request.user, company_id, "sales.deliver")
        form = forms.DeliveryForm(request.POST, company_id=company_id)
        done = _create(request, form, lambda v: deliveries.create_delivery(actor=request.user, company_id=company_id,
                                                                           values=v),
                       "{obj.number} drafted from the reserved stock.", "sales:delivery_detail")
        if done:
            return done
    with use_company(company_id):
        rows = access.scope_warehouses(Delivery.objects.select_related("order__customer", "warehouse"),
                                       request.user, company_id, VIEW, field="warehouse__branch")
    return _list(request, "sales/delivery_list.html", rows,
                 ("number", "order__number", "order__customer__name", "vehicle_no", "driver"),
                 ("number", "delivery_date", "order__customer__name", "order__number", "vehicle_no", "status", None),
                 Delivery.Status.choices,
                 {"can_new": can_new, "open_dialog": "new-dialog" if form is not None else "",
                  "new_form": form or (forms.DeliveryForm(company_id=company_id) if can_new else None)})


def _delivery_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, delivery = deliveries.get_delivery(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    branch = delivery.warehouse.branch_id
    with use_company(company_id):
        lines = list(delivery.lines.select_related("item__base_uom", "batch", "location", "uom", "label")
                     .order_by("pk"))
        invoice = SalesInvoice.objects.filter(delivery=delivery).exclude(status="cancelled").first()
    may = access.may(request.user, company_id, "sales.deliver", branch)
    editable = delivery.status == "draft" and may
    return render(request, "sales/delivery_detail.html", {
        "delivery": delivery, "order": delivery.order, "lines": lines, "invoice": invoice,
        "can_edit": editable, "can_post": editable and bool(lines),
        "can_reverse": delivery.status == "posted" and may,
        "can_invoice": delivery.status == "posted" and invoice is None and access.may(
            request.user, company_id, "sales.invoice", delivery.order.warehouse.branch_id),
        "line_form": line_form or (forms.DeliveryLineForm(company_id=company_id, delivery=delivery)
                                   if editable else None),
        "reason_form": ReasonForm(), "open_dialog": open_dialog,
    })


@login_required
def delivery_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _delivery_page(request, pk)


@login_required
@require_POST
def delivery_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, delivery = deliveries.get_delivery(actor=request.user, company_id=company_id, pk=pk)
    form = forms.DeliveryLineForm(request.POST, company_id=company_id, delivery=delivery)
    if form.is_valid():
        try:
            deliveries.add_line(actor=request.user, company_id=company_id, delivery_id=pk, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Added.")
            return redirect("sales:delivery_detail", pk=pk)
    return _delivery_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def delivery_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: deliveries.remove_line(actor=request.user, company_id=cid, delivery_id=pk,
                                                            line_id=line_pk), "Line removed.",
                "sales:delivery_detail", pk)


@login_required
@require_POST
def delivery_scan(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    code = request.POST.get("code", "").strip()[:500]
    try:
        made = deliveries.scan_into_delivery(actor=request.user, company_id=company_id, delivery_id=pk, code=code)
    except ValidationError as exc:
        messages.error(request, f"{code}: " + "; ".join(exc.messages))
    else:
        messages.success(request, f"{code}: " + "; ".join(
            f"{l.qty.normalize():f} {l.uom.code} {l.item.code}" + (f" batch {l.batch.batch_no}" if l.batch_id else "")
            for l in made) + " added.")
    return redirect(reverse("sales:delivery_detail", args=[pk]) + "#scan")


@login_required
@require_POST
def delivery_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if action == "invoice":
        try:
            invoice = deliveries.create_invoice(actor=request.user, company_id=company_id, delivery_id=pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("sales:delivery_detail", pk=pk)
        messages.success(request, f"{invoice.number} drafted: check it, then issue it.")
        return redirect("sales:invoice_detail", pk=invoice.pk)
    simple = {
        "post": (lambda cid: deliveries.post_delivery(actor=request.user, company_id=cid, delivery_id=pk),
                 "{obj.number} delivered."),
        "cancel": (lambda cid: deliveries.cancel_delivery(actor=request.user, company_id=cid, delivery_id=pk),
                   "{obj.number} cancelled."),
        "reverse": (lambda cid: deliveries.reverse_delivery(actor=request.user, company_id=cid, delivery_id=pk,
                                                            reason=request.POST.get("reason", "")),
                    "{obj.number} reversed: the goods are back and reserved again."),
    }
    if action not in simple:
        return redirect("sales:delivery_detail", pk=pk)
    run, success = simple[action]
    return _act(request, run, success, "sales:delivery_detail", pk)


@login_required
def delivery_pdf(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, delivery = deliveries.get_delivery(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    with use_company(company_id):
        content, filename = invoice_pdf.challan(delivery)
    return _pdf(content, filename)


# --------------------------------------------------------------------------
# Invoices
# --------------------------------------------------------------------------

@login_required
def invoice_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    with use_company(company_id):
        rows = access.scope_warehouses(SalesInvoice.objects.select_related("customer", "delivery"), request.user,
                                       company_id, VIEW, field="delivery__order__warehouse__branch")
    return _list(request, "sales/invoice_list.html", rows, ("number", "customer__name", "delivery__number"),
                 ("number", "invoice_date", "customer__name", "due_date", "total", "status", None),
                 SalesInvoice.Status.choices, {})


def _invoice_page(request, pk, open_dialog="", paid_form=None):
    company_id = request.company_id
    _, invoice = deliveries.get_invoice(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    with use_company(company_id):
        lines = list(invoice.lines.select_related("item", "uom").order_by("pk"))
    may = access.may(request.user, company_id, "sales.invoice", invoice.delivery.order.warehouse.branch_id)
    return render(request, "sales/invoice_detail.html", {
        "invoice": invoice, "lines": lines, "may": may, "reason_form": ReasonForm(),
        "paid_form": paid_form or forms.PaidForm(initial={"paid_on": invoice.invoice_date}),
        "open_dialog": open_dialog,
    })


@login_required
def invoice_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _invoice_page(request, pk)


@login_required
@require_POST
def invoice_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    if action == "paid":
        form = forms.PaidForm(request.POST)
        if form.is_valid():
            return _act(request, lambda cid: deliveries.mark_paid(actor=request.user, company_id=cid, invoice_id=pk,
                                                                  paid_on=form.cleaned_data["paid_on"],
                                                                  note=form.cleaned_data["note"]),
                        "{obj.number} marked paid.", "sales:invoice_detail", pk)
        return _invoice_page(request, pk, open_dialog="paid-dialog", paid_form=form)
    simple = {
        "issue": (lambda cid: deliveries.issue_invoice(actor=request.user, company_id=cid, invoice_id=pk),
                  "{obj.number} issued."),
        "cancel": (lambda cid: deliveries.cancel_invoice(actor=request.user, company_id=cid, invoice_id=pk,
                                                         reason=request.POST.get("reason", "")),
                   "{obj.number} cancelled."),
    }
    if action not in simple:
        return redirect("sales:invoice_detail", pk=pk)
    run, success = simple[action]
    return _act(request, run, success, "sales:invoice_detail", pk)


@login_required
def invoice_pdf_view(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, invoice = deliveries.get_invoice(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    with use_company(company_id):
        content, filename = invoice_pdf.invoice(invoice)
    return _pdf(content, filename)


# --------------------------------------------------------------------------
# Sales returns
# --------------------------------------------------------------------------

@login_required
def return_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_new = access.may(request.user, company_id, "sales.deliver")
    form = None
    if request.method == "POST":
        access.require(request.user, company_id, "sales.deliver")
        form = forms.ReturnForm(request.POST, company_id=company_id)
        done = _create(request, form, lambda v: deliveries.create_return(actor=request.user, company_id=company_id,
                                                                         values=v),
                       "{obj.number} started. Add what came back.", "sales:return_detail")
        if done:
            return done
    initial = {"delivery": int(request.GET["delivery"])} if request.GET.get("delivery", "").isdigit() else {}
    return _list(request, "sales/return_list.html", SalesReturn.objects.select_related("customer", "delivery"),
                 ("number", "customer__name", "delivery__number", "reason"),
                 ("number", "return_date", "customer__name", "delivery__number", "reason", "status", None),
                 SalesReturn.Status.choices,
                 {"can_new": can_new, "open_dialog": "new-dialog" if form is not None or initial else "",
                  "new_form": form or (forms.ReturnForm(company_id=company_id, initial=initial) if can_new else None)})


def _return_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, row = deliveries.get_return(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    with use_company(company_id):
        lines = list(row.lines.select_related("item__base_uom", "batch", "warehouse", "uom",
                                              "delivery_line__delivery").order_by("pk"))
    may = access.may(request.user, company_id, "sales.deliver", row.warehouse.branch_id)
    editable = row.status == "draft" and may
    return render(request, "sales/return_detail.html", {
        "doc": row, "lines": lines, "can_edit": editable, "can_post": editable and bool(lines),
        "can_reverse": row.status == "posted" and may, "reason_form": ReasonForm(),
        "line_form": line_form or (forms.ReturnLineForm(company_id=company_id, document=row) if editable else None),
        "open_dialog": open_dialog,
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
    _, row = deliveries.get_return(actor=request.user, company_id=company_id, pk=pk)
    form = forms.ReturnLineForm(request.POST, company_id=company_id, document=row)
    if form.is_valid():
        try:
            deliveries.add_return_line(actor=request.user, company_id=company_id, return_id=pk,
                                       values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Added.")
            return redirect("sales:return_detail", pk=pk)
    return _return_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def return_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: deliveries.remove_return_line(actor=request.user, company_id=cid, return_id=pk,
                                                                   line_id=line_pk), "Line removed.",
                "sales:return_detail", pk)


@login_required
@require_POST
def return_action(request, pk, action):
    simple = {
        "post": (lambda cid: deliveries.post_return(actor=request.user, company_id=cid, return_id=pk),
                 "{obj.number} posted: the goods are back."),
        "cancel": (lambda cid: deliveries.cancel_return(actor=request.user, company_id=cid, return_id=pk),
                   "{obj.number} cancelled."),
        "reverse": (lambda cid: deliveries.reverse_return(actor=request.user, company_id=cid, return_id=pk,
                                                          reason=request.POST.get("reason", "")),
                    "{obj.number} reversed."),
    }
    if action not in simple:
        return redirect("sales:return_detail", pk=pk)
    run, success = simple[action]
    return _act(request, run, success, "sales:return_detail", pk)
