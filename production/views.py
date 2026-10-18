"""Production pages: BOMs, production orders (with planned vs used and the
output), material uses, and the production report. Thin adapters over
``production.boms`` and ``production.orders``."""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db.models import Count
from django.http import HttpResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_POST

from base_template.tables import paginate, render
from common.forms import apply_service_errors
from common.tenant import use_company
from inventory import access
from inventory.forms import PrintForm
from inventory.views import branch_ids, company_or_bail
from production import boms, forms, orders
from production.models import Bom, Consumption, Output, ProductionOrder


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


# --------------------------------------------------------------------------
# BOMs
# --------------------------------------------------------------------------

@login_required
def bom_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_edit = access.may(request.user, company_id, boms.CODE)
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, boms.CODE)
        new_form = forms.BomForm(request.POST, company_id=company_id)
        if new_form.is_valid():
            try:
                bom = boms.create_bom(actor=request.user, company_id=company_id, values=new_form.cleaned_data)
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{bom} started. Add the materials, then activate it.")
                return redirect("production:bom_detail", pk=bom.pk)
    with use_company(company_id):
        rows = Bom.objects.select_related("item", "batch_uom").annotate(line_count=Count("lines"))
        status = request.GET.get("status", "")
        if status in Bom.Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows, search=("item__code", "item__name", "note"),
                         order=("item__code", "item__name", "version", "batch_size", "line_count", "status", None))
    return render(request, "production/bom_list.html", {
        "rows": table, "status": status, "statuses": Bom.Status.choices, "can_edit": can_edit,
        "new_form": new_form or (forms.BomForm(company_id=company_id) if can_edit else None),
        "open_dialog": "new-dialog" if new_form is not None else "",
    })


def _bom_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, bom = boms.get_bom(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    with use_company(company_id):
        lines = list(bom.lines.select_related("item__base_uom", "uom").order_by("is_packaging", "pk"))
        versions = list(Bom.objects.filter(item=bom.item).order_by("-version"))
    can_edit = access.may(request.user, company_id, boms.CODE)
    return render(request, "production/bom_detail.html", {
        "bom": bom, "lines": lines, "versions": versions, "can_edit": can_edit,
        "is_draft": bom.status == Bom.Status.DRAFT,
        "line_form": line_form or (forms.BomLineForm(company_id=company_id)
                                   if can_edit and bom.status == Bom.Status.DRAFT else None),
        "open_dialog": open_dialog,
    })


@login_required
def bom_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _bom_page(request, pk)


@login_required
@require_POST
def bom_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, boms.CODE)
    form = forms.BomLineForm(request.POST, company_id=company_id)
    if form.is_valid():
        try:
            boms.add_line(actor=request.user, company_id=company_id, bom_id=pk, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Material added.")
            return redirect("production:bom_detail", pk=pk)
    return _bom_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def bom_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: boms.remove_line(actor=request.user, company_id=cid, bom_id=pk,
                                                      line_id=line_pk), "Material removed.",
                "production:bom_detail", pk)


@login_required
@require_POST
def bom_action(request, pk, action):
    if action == "activate":
        return _act(request, lambda cid: boms.activate(actor=request.user, company_id=cid, bom_id=pk),
                    "{obj} is now the active recipe.", "production:bom_detail", pk)
    if action == "new_version":
        company_id, bail = company_or_bail(request)
        if bail:
            return bail
        try:
            bom = boms.new_version(actor=request.user, company_id=company_id, bom_id=pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("production:bom_detail", pk=pk)
        messages.success(request, f"{bom} started from the previous version.")
        return redirect("production:bom_detail", pk=bom.pk)
    if action == "delete":
        company_id, bail = company_or_bail(request)
        if bail:
            return bail
        try:
            boms.delete_draft(actor=request.user, company_id=company_id, bom_id=pk)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("production:bom_detail", pk=pk)
        messages.success(request, "Draft deleted.")
        return redirect("production:boms")
    return redirect("production:bom_detail", pk=pk)


# --------------------------------------------------------------------------
# Production orders
# --------------------------------------------------------------------------

def _order_form(request, data=None):
    return forms.OrderForm(data, company_id=request.company_id,
                           branch_ids=branch_ids(request.user, request.company_id, "production.plan"))


@login_required
def order_list(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require_module(request.user, company_id)
    can_plan = access.may(request.user, company_id, "production.plan")
    new_form = None
    if request.method == "POST":
        access.require(request.user, company_id, "production.plan")
        new_form = _order_form(request, request.POST)
        if new_form.is_valid():
            try:
                order = orders.create_order(actor=request.user, company_id=company_id,
                                            values={**new_form.cleaned_data, "bom": None})
            except ValidationError as exc:
                apply_service_errors(new_form, exc)
            else:
                messages.success(request, f"{order.number} planned. Someone else releases it.")
                return redirect("production:order_detail", pk=order.pk)
    with use_company(company_id):
        rows = access.scope_warehouses(
            ProductionOrder.objects.select_related("item", "bom", "output_warehouse"), request.user,
            company_id, "inventory.view", field="output_warehouse__branch")
        status = request.GET.get("status", "")
        if status in ProductionOrder.Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows, search=("number", "item__code", "item__name", "line_name"),
                         order=("number", "planned_date", "item__code", None, "line_name", "status", None))
    return render(request, "production/order_list.html", {
        "rows": table, "status": status, "statuses": ProductionOrder.Status.choices, "can_plan": can_plan,
        "new_form": new_form or (_order_form(request) if can_plan else None),
        "open_dialog": "new-dialog" if new_form is not None else "",
    })


def _order_page(request, pk, output_form=None, open_dialog=""):
    company_id = request.company_id
    _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="inventory.view")
    branch = order.output_warehouse.branch_id
    with use_company(company_id):
        usage = orders.usage(order)
        uses = list(order.consumptions.order_by("pk"))
        output = Output.objects.select_related("batch", "uom", "location").filter(order=order).first()
        made = list(orders.output_labels(output)) if output else []
    may_plan = access.may(request.user, company_id, "production.plan", branch)
    may_run = access.may(request.user, company_id, "production.execute", branch)
    running = order.status == ProductionOrder.Status.RELEASED
    return render(request, "production/order_detail.html", {
        "order": order, "usage": usage, "uses": uses, "output": output, "labels": made,
        "material_cost": orders.material_cost(order),
        "sees_costs": access.sees_costs(request.user, company_id),
        "can_release": order.status == "planned" and may_plan,
        "can_cancel": order.status in ("planned", "released") and may_plan,
        "can_close": order.status == "completed" and may_plan,
        "can_run": running and may_run,
        "has_draft_use": any(u.status == "draft" for u in uses),
        "can_qc": bool(output and output.batch and output.batch.status == "quarantine"
                       and access.may(request.user, company_id, "purchase.qc", branch)),
        "can_print": bool(made) and may_run,
        "output_form": output_form or (forms.OutputForm(company_id=company_id, order=order, initial={
            "uom": order.uom_id, "qty_good": order.planned_qty, "output_date": order.planned_date})
            if running and may_run else None),
        "qc_form": forms.QcForm(), "print_form": PrintForm(), "open_dialog": open_dialog,
    })


@login_required
def order_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _order_page(request, pk)


@login_required
@require_POST
def order_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    simple = {
        "release": (orders.release, "{obj.number} released: materials can be used."),
        "cancel": (orders.cancel, "{obj.number} cancelled."),
        "close": (orders.close, "{obj.number} closed."),
    }
    if action in simple:
        run, success = simple[action]
        return _act(request, lambda cid: run(actor=request.user, company_id=cid, order_id=pk), success,
                    "production:order_detail", pk)
    if action in ("use", "return"):
        try:
            use = orders.start_consumption(
                actor=request.user, company_id=company_id, order_id=pk,
                kind=Consumption.Kind.ISSUE if action == "use" else Consumption.Kind.RETURN)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("production:order_detail", pk=pk)
        short = getattr(use, "shortages", [])
        messages.success(request, f"{use.number} drafted." + (
            " Not enough usable stock: " + "; ".join(short) + "." if short else " Check it, then post it."))
        return redirect("production:consumption_detail", pk=use.pk)
    if action == "qc":
        return _act(request, lambda cid: orders.record_qc(
            actor=request.user, company_id=cid, order_id=pk, passed=request.POST.get("result") == "passed",
            notes=request.POST.get("notes", "")), "QC saved; the batch follows it.", "production:order_detail", pk)
    return redirect("production:order_detail", pk=pk)


@login_required
@require_POST
def order_output(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="production.execute")
    form = forms.OutputForm(request.POST, company_id=company_id, order=order)
    if form.is_valid():
        try:
            output = orders.post_output(actor=request.user, company_id=company_id, order_id=pk,
                                        values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, f"{output.number}: batch {output.batch_no or '—'} is in stock.")
            return redirect("production:order_detail", pk=pk)
    return _order_page(request, pk, output_form=form, open_dialog="output-dialog")


@login_required
@require_POST
def order_labels(request, pk):
    from inventory import labels as label_service

    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code="production.execute")
    with use_company(company_id):
        output = Output.objects.filter(order=order).first()
        ids = list(orders.output_labels(output).exclude(status="void").values_list("pk", flat=True)) if output else []
    try:
        pdf = label_service.print_labels(actor=request.user, company_id=company_id, label_ids=ids,
                                         copies=request.POST.get("copies") or 1,
                                         reason=request.POST.get("reason", ""), permission="production.execute")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
        return redirect("production:order_detail", pk=pk)
    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'inline; filename="{order.number}-labels.pdf"'
    return response


# --------------------------------------------------------------------------
# Material use
# --------------------------------------------------------------------------

def _use_page(request, pk, line_form=None, open_dialog=""):
    company_id = request.company_id
    _, use = orders.get_consumption(actor=request.user, company_id=company_id, pk=pk)
    with use_company(company_id):
        lines = list(use.lines.select_related("item__base_uom", "batch", "location", "uom", "label").order_by("pk"))
    editable = use.status == Consumption.Status.DRAFT and use.order.status == "released"
    return render(request, "production/consumption_detail.html", {
        "use": use, "order": use.order, "lines": lines, "can_edit": editable,
        "line_form": line_form or (forms.ConsumptionLineForm(company_id=company_id, consumption=use)
                                   if editable else None),
        "open_dialog": open_dialog,
    })


@login_required
def consumption_detail(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    return _use_page(request, pk)


@login_required
@require_POST
def consumption_line_add(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, use = orders.get_consumption(actor=request.user, company_id=company_id, pk=pk)
    form = forms.ConsumptionLineForm(request.POST, company_id=company_id, consumption=use)
    if form.is_valid():
        try:
            orders.add_line(actor=request.user, company_id=company_id, consumption_id=pk, values=form.cleaned_data)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Added.")
            return redirect("production:consumption_detail", pk=pk)
    return _use_page(request, pk, line_form=form, open_dialog="line-dialog")


@login_required
@require_POST
def consumption_line_remove(request, pk, line_pk):
    return _act(request, lambda cid: orders.remove_line(actor=request.user, company_id=cid, consumption_id=pk,
                                                        line_id=line_pk), "Line removed.",
                "production:consumption_detail", pk)


@login_required
@require_POST
def consumption_scan(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    code = request.POST.get("code", "").strip()[:500]
    try:
        made = orders.scan_into_consumption(actor=request.user, company_id=company_id, consumption_id=pk, code=code)
    except ValidationError as exc:
        messages.error(request, f"{code}: " + "; ".join(exc.messages))
    else:
        messages.success(request, f"{code}: " + "; ".join(
            f"{l.qty.normalize():f} {l.uom.code} {l.item.code}" + (f" batch {l.batch.batch_no}" if l.batch_id else "")
            for l in made) + " added.")
    return redirect(reverse("production:consumption_detail", args=[pk]) + "#scan")


@login_required
@require_POST
def consumption_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    _, use = orders.get_consumption(actor=request.user, company_id=company_id, pk=pk)
    if action == "post":
        return _act(request, lambda cid: orders.post_consumption(actor=request.user, company_id=cid,
                                                                 consumption_id=pk),
                    "{obj.number} posted.", "production:order_detail", use.order_id)
    if action == "cancel":
        return _act(request, lambda cid: orders.cancel_consumption(actor=request.user, company_id=cid,
                                                                   consumption_id=pk),
                    "{obj.number} cancelled.", "production:order_detail", use.order_id)
    return redirect("production:consumption_detail", pk=pk)


# --------------------------------------------------------------------------
# Report
# --------------------------------------------------------------------------

@login_required
def report(request):
    """Completed runs: planned, good, rejected, yield, material cost, unit cost."""
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, "inventory.view")
    with use_company(company_id):
        rows = access.scope_warehouses(
            ProductionOrder.objects.filter(status__in=["completed", "closed"]).select_related("item__base_uom"),
            request.user, company_id, "inventory.view", field="output_warehouse__branch")
        table = paginate(request, rows.order_by("-completed_at"),
                         search=("number", "item__code", "item__name", "line_name"),
                         order=("number", "completed_at", "item__code", "planned_qty", "good_qty_base",
                                "rejected_qty_base", None, "material_cost", "unit_cost"))
        totals = rows.values_list("material_cost", "good_qty_base")
        cost = sum((c or Decimal("0") for c, _g in totals), Decimal("0"))
    return render(request, "production/report.html", {
        "rows": table, "sees_costs": access.sees_costs(request.user, company_id), "total_cost": cost,
    })
