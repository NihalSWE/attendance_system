"""The productions list and one production's page in the new look (2026-10-27,
docs/inventory/PRODUCTION_PLAN.md §8.3-8.6, §15 phase 7; production.making).

Planned → Start → Finish on one page: Start shows what leaves which store
(the store keeper changes a line only when they gave more or less); Finish
asks good and damaged, the stores it goes into, the day's costs and the
leftovers; the result shows the full cost against what was expected, with
Reverse while nothing made was touched. Every action answers in place."""

from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from base_template import fetch
from base_template.tables import paginate, render
from common.tenant import use_company
from inventory import access
from inventory.views import branch_ids
from production import making, orders, planning
from production.gate import company_or_bail
from production.models import Leftover, OrderCost, Output, OutputPart, ProductionOrder

Status = ProductionOrder.Status
TONE = {"planned": "info", "released": "wait", "completed": "ok", "closed": "ok", "cancelled": "bad",
        "failed": "bad", "reversed": "bad"}
VIEW = ("inventory.view", "production.plan", "production.execute")


def _number(value):
    text = (value or "").replace(",", "").strip()
    if not text:
        return Decimal("0")
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def _date(value):
    from django.utils.dateparse import parse_date

    try:
        return parse_date((value or "").strip())
    except ValueError:
        return None


@login_required
def runs(request):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    access.require(request.user, company_id, VIEW)
    status = request.GET.get("status", "")
    with use_company(company_id):
        rows = access.scope_warehouses(ProductionOrder.objects.select_related("item", "uom"), request.user,
                                       company_id, VIEW, field="output_warehouse__branch")
        if status in Status.values:
            rows = rows.filter(status=status)
        table = paginate(request, rows.order_by("-planned_date", "-pk"),
                         search=("number", "item__code", "item__name"),
                         order=("number", "item__name", "planned_date", "planned_qty", "status", None))
        for row in table.object_list:
            row.tone = TONE.get(row.status, "info")
    return render(request, "inventory/hub/runs.html", {
        "active": "make", "production": True, "rows": table, "status": status,
        "statuses": Status.choices, "can_plan": access.may(request.user, company_id, "production.plan")})


def _order(request, company_id, pk):
    _, order = orders.get_order(actor=request.user, company_id=company_id, pk=pk, code=VIEW)
    with use_company(company_id):
        return ProductionOrder.objects.select_related("item__base_uom", "bom__batch_uom", "uom",
                                                      "output_warehouse__branch").get(pk=order.pk)


@login_required
def run(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    order = _order(request, company_id, pk)
    today = timezone.localdate()
    can_execute = access.may(request.user, company_id, "production.execute", order.output_warehouse.branch_id)
    context = {"active": "make", "production": True, "order": order, "item": order.item,
               "tone": TONE.get(order.status, "info"), "today": today, "can_execute": can_execute,
               "sees_costs": access.sees_costs(request.user, company_id),
               "batches": making.batches_of(order).normalize(), "Status": Status}
    if order.status == Status.PLANNED:
        context["proposal"] = making.proposal(company_id, order, today)
    if order.status == Status.RELEASED:
        stores = planning.output_stores(company_id, branch_ids(request.user, company_id, "production.execute"))
        context.update({"usage": orders.usage(order), "into_stores": stores,
                        "day_costs": making.day_costs(company_id, order) + [("", None), ("", None)]})
    if order.status in (Status.COMPLETED, Status.CLOSED, Status.FAILED, Status.REVERSED):
        with use_company(company_id):
            output = Output.objects.filter(order=order).first()
            context.update({
                "usage": orders.usage(order),
                "output": output,
                "parts": list(OutputPart.objects.filter(output=output).select_related("warehouse")) if output else [],
                "made": making.made_batches(output) if output else [],
                "costs": list(OrderCost.objects.filter(order=order)),
                "leftovers": list(Leftover.objects.filter(order=order).select_related("item__base_uom")),
            })
        total = (order.material_cost or Decimal("0")) + (order.other_cost or Decimal("0"))
        context.update({"total_cost": total, "can_reverse": order.status in (Status.COMPLETED, Status.FAILED)
                        and making.untouched(order) and can_execute,
                        "waits_qc": any(batch.status == "quarantine" for batch in context["made"])})
    return render(request, "inventory/hub/run.html", context)


def _answer(call, success, redirect):
    try:
        result = call()
    except ValidationError as exc:
        return fetch.refused_errors(exc)
    return fetch.saved(success(result), redirect)


@login_required
@require_POST
def run_start(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    order = _order(request, company_id, pk)
    day = _date(request.POST.get("work_date")) or timezone.localdate()
    changes, bad = {}, {}
    for need in making.proposal(company_id, order, day).needs:
        for take in need.takes:
            name = f"qty-{need.item.pk}-{take.key}"
            if name in request.POST:
                qty = _number(request.POST[name])
                if qty is None:
                    bad[name] = ["Write a number."]
                else:
                    changes[(need.item.pk, take.key)] = qty
    if bad:
        return fetch.refused_errors(ValidationError(bad))
    here = reverse("production:run", args=[order.pk])
    return _answer(lambda: making.start(actor=request.user, company_id=company_id, order_id=order.pk,
                                        work_date=day, changes=changes, reason=request.POST.get("reason", "")),
                   lambda done: f"{done.number} started: the materials left the stores.", here)


@login_required
@require_POST
def run_finish(request, pk):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    order = _order(request, company_id, pk)
    post, bad = request.POST, {}

    def number(name):
        value = _number(post.get(name))
        if value is None:
            bad[name] = ["Write a number."]
            return Decimal("0")
        return value

    good, damaged = number("good"), number("damaged")
    stores = planning.output_stores(company_id, branch_ids(request.user, company_id, "production.execute"))
    parts = [(store, number(f"into-{store.pk}")) for store in stores if post.get(f"into-{store.pk}", "").strip()]
    costs = []
    for i in range(12):
        name, amount = post.get(f"cost-name-{i}", "").strip(), number(f"cost-amount-{i}")
        if amount:
            costs.append((name, amount))
    leftovers = {}
    for row in orders.usage(order):
        pk_ = row["item"].pk
        returned, wasted = number(f"returned-{pk_}"), number(f"wasted-{pk_}")
        if returned or wasted:
            leftovers[pk_] = (returned, wasted, post.get(f"reason-{pk_}", ""))
    if bad:
        return fetch.refused_errors(ValidationError(bad))
    values = {"output_date": _date(post.get("output_date")), "good": good, "damaged": damaged, "parts": parts,
              "costs": costs, "leftovers": leftovers, "reason": post.get("reason", ""),
              "batch_no": post.get("batch_no", ""), "expiry_date": _date(post.get("expiry_date"))}

    def said(result):
        done, output = result
        if output is None:
            return f"{done.number} recorded as failed: its cost is a loss."
        return (f"{done.number} finished: {done.good_qty_base.normalize():f} {done.item.base_uom.code} into stock"
                + (f" as batch {output.batch_no}" if output.batch_no else "") + ".")

    return _answer(lambda: making.finish(actor=request.user, company_id=company_id, order_id=order.pk,
                                         values=values), said, reverse("production:run", args=[order.pk]))


@login_required
@require_POST
def run_action(request, pk, action):
    company_id, bail = company_or_bail(request)
    if bail:
        return bail
    order = _order(request, company_id, pk)
    here = reverse("production:run", args=[order.pk])
    reason, today = request.POST.get("reason", ""), timezone.localdate()
    kw = {"actor": request.user, "company_id": company_id, "order_id": order.pk}
    if action == "cancel" and order.status == Status.PLANNED:
        return _answer(lambda: orders.cancel(**kw), lambda done: f"{done.number} cancelled.", here)
    if action == "cancel":
        return _answer(lambda: making.cancel_started(**kw, reason=reason, work_date=today),
                       lambda done: f"{done.number} cancelled: every material went back to its store.", here)
    if action == "reverse":
        return _answer(lambda: making.reverse(**kw, reason=reason, work_date=today),
                       lambda done: f"{done.number} reversed: what it made left stock, the materials went back.",
                       here)
    if action in ("qc-pass", "qc-fail"):
        passed = action == "qc-pass"
        return _answer(lambda: orders.record_qc(**kw, passed=passed, notes=request.POST.get("notes", "")),
                       lambda done: "Passed: it can be sold now." if passed else "Failed: it stays blocked.", here)
    return fetch.refused(message="Unknown action.")
