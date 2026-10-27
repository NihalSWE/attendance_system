"""Start and Finish a production (2026-10-27, docs/inventory/PRODUCTION_PLAN.md
§8.3-8.6, §11; §15 phase 7). Plan → Start → Finish, in plain words.

- **Start** (``production.execute``): the materials leave the stores. Each
  store's lines are filled from the plan - every usable store of the branch,
  the usual store first, first expiry first. The store keeper changes a line
  only when they gave more or less (more than 5 % over the plan needs a
  reason). Start is refused while anything is short. Stock goes down in each
  store at its real cost. What one unit is expected to cost is kept.
- **Finish**: good and damaged quantity; the good goes into one store or is
  split (it must add up); the day's other costs come from the cost sheet and
  can be corrected; leftover material goes back to the store it came from, or
  is recorded as wasted with a reason. Good below 90 % or above 105 % of the
  plan needs a reason. Nothing good is a **failed** production: a reason, and
  its cost is a loss.
- **Full cost**: materials actually used + the day's costs, divided by the
  good quantity. Damaged goods carry no cost of their own. The new stock is
  valued at that cost; the product's page shows expected against actual.
- **Batch numbers**: one per production, or one per mix when the product says
  so (then into one store).
- **Reverse** (a reason): while none of the made stock has been sold, moved or
  used, every stock move of the production is undone.
"""

from decimal import ROUND_FLOOR, ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from common.tenant import use_company
from inventory import posting
from inventory.doc_common import audit, expiry_from, now, plain, require
from inventory.models import Batch, MoveType, StockMove
from inventory.posting import UNUSABLE_STORES
from production import orders, planning
from production.models import (
    Consumption,
    ConsumptionLine,
    Leftover,
    OrderCost,
    Output,
    OutputPart,
    ProductionOrder,
)

Status = ProductionOrder.Status
ZERO = Decimal("0")
FOUR = Decimal("0.0001")
CENT = Decimal("0.01")
LOW, HIGH, OVER_USE = Decimal("0.90"), Decimal("1.05"), Decimal("1.05")


def batches_of(order):
    """How many batches (mixes) the production is: 2,000 bottles at 1,000 a batch = 2."""
    size = order.bom.batch_size_base
    return (order.planned_base / size) if size else Decimal("1")


def proposal(company_id, order, day):
    """What Start would take, store by store (``production.planning.plan``)."""
    return planning.plan(company_id, order.item, int(batches_of(order).to_integral_value(ROUND_HALF_UP)) or 1,
                         order.output_warehouse, day, exclude_order=order.pk, bom=order.bom,
                         makes=order.planned_base)


def _execute(actor, company_id, order_id):
    membership, order = orders.get_order(actor=actor, company_id=company_id, pk=order_id,
                                         code="production.execute")
    with use_company(company_id):
        order = ProductionOrder.objects.select_for_update(of=("self",)).select_related(
            "item__base_uom", "bom", "output_warehouse__branch").get(pk=order.pk)
    return membership, order


def _number(company_id, day):
    return posting.next_number(company_id, Consumption.DOC_TYPE, Consumption.PREFIX, day.year)


# --- Start ---------------------------------------------------------------------

@transaction.atomic
def start(*, actor, company_id, order_id, work_date, changes=None, reason=""):
    """Materials out of their stores. ``changes``: ``{(material id, take key): qty}``
    for lines the store keeper gave more or less of."""
    membership, order = _execute(actor, company_id, order_id)
    if order.status != Status.PLANNED:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: only a planned "
                              "production starts.")
    posting.check_period_open(company_id, work_date)
    found = proposal(company_id, order, work_date)
    if found.short:
        raise ValidationError(["Can't start: " + ", ".join(
            f"{need.item.name} is short by {need.short.normalize():f} {need.item.base_uom.code}"
            for need in found.short) + ".", "Make fewer, or buy the rest first."])
    changes = changes or {}
    by_store, errors = {}, {}
    for need in found.needs:
        total = ZERO
        for take in need.takes:
            qty = changes.get((need.item.pk, take.key), take.qty)
            if qty < 0:
                errors[f"qty-{need.item.pk}-{take.key}"] = "Zero or more."
                continue
            if qty > take.available:
                errors[f"qty-{need.item.pk}-{take.key}"] = (f"Only {take.available.normalize():f} "
                                                            f"{need.item.base_uom.code} there.")
                continue
            total += qty
            if qty > 0:
                by_store.setdefault(take.store, []).append((need.item, take, qty))
        if total > need.need * OVER_USE and not reason.strip():
            errors["reason"] = (f"{need.item.name}: {total.normalize():f} is more than 5 % over the plan "
                                f"({need.need.normalize():f}): say why.")
    if errors:
        raise ValidationError(errors)
    if not by_store:
        raise ValidationError("Nothing would leave the stores.")
    with use_company(company_id):
        for store, lines in by_store.items():
            use = Consumption.objects.create(
                company=membership.company, created_by=actor, updated_by=actor, order=order, warehouse=store,
                kind=Consumption.Kind.ISSUE, work_date=work_date, note=reason.strip()[:255],
                number=_number(company_id, work_date))
            made = [ConsumptionLine.objects.create(
                company=membership.company, consumption=use, item=item, batch=take.batch, location=take.location,
                qty=qty, uom=item.base_uom) for item, take, qty in lines]
            moves = posting.post(
                company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE, document_id=use.pk,
                move_type=MoveType.PRODUCTION_CONSUME, work_date=work_date,
                lines=[posting.Line(item=line.item, warehouse=store, location=line.location, batch=line.batch,
                                    qty=-line.qty_base, line_id=line.pk, note=f"{use.number} for {order.number}")
                       for line in made])
            use.status, use.posted_at, use.posted_by = Consumption.Status.POSTED, moves[0].posted_at, actor
            use.save(update_fields=["status", "posted_at", "posted_by"])
        from production.costing import expected_unit_cost

        order.status, order.released_at, order.released_by, order.updated_by = Status.RELEASED, now(), actor, actor
        order.expected_unit_cost = expected_unit_cost(company_id, order.item)
        if reason.strip():
            order.reason = reason.strip()[:500]
        order.save()
        audit(actor, membership, "production.order.started", order,
              after={"stores": [store.code for store in by_store], "reason": reason.strip(),
                     "material_cost": plain(orders.material_cost(order))})
    return order


# --- Finish --------------------------------------------------------------------

def issued(order):
    """``{material id: [(store, batch, qty)]}`` that went out at Start, newest first:
    where leftovers go back to."""
    found = {}
    with use_company(order.company_id):
        lines = (ConsumptionLine.objects.filter(consumption__order=order, consumption__status="posted",
                                                consumption__kind=Consumption.Kind.ISSUE)
                 .select_related("consumption__warehouse", "batch", "item").order_by("-pk"))
        for line in lines:
            store = line.consumption.warehouse or order.material_warehouse
            found.setdefault(line.item_id, []).append((store, line.batch, line.location, line.qty_base, line.item))
    return found


def day_costs(company_id, order):
    """``[(name, amount)]``: the product's cost sheet for this many batches,
    to fill the Finish screen (it can be corrected)."""
    from production.costing import sheet

    found = sheet(company_id, order.item)
    if found is None:
        return []
    times = batches_of(order)
    return [(row.name, (row.amount * times).quantize(CENT)) for row in found.costs]


def _return_leftovers(company_id, actor, membership, order, leftovers, day):
    """Post what goes back, into the store (and batch) it came from, at the cost
    it was used at."""
    sent = issued(order)
    used = orders._used(order)
    by_store = {}
    for item_id, (returned, _wasted, _reason) in leftovers.items():
        if returned <= 0:
            continue
        net = used.get(item_id, (ZERO, ZERO))[0]
        if returned > net:
            raise ValidationError({f"returned-{item_id}": f"More than was used ({net.normalize():f})."})
        remaining = returned
        for store, batch, location, qty, item in sent.get(item_id, []):
            if remaining <= 0:
                break
            back = min(qty, remaining)
            by_store.setdefault(store, []).append((item, batch, location, back))
            remaining -= back
    for store, lines in by_store.items():
        with use_company(company_id):
            row = Consumption.objects.create(
                company=membership.company, created_by=actor, updated_by=actor, order=order, warehouse=store,
                kind=Consumption.Kind.RETURN, work_date=day, number=_number(company_id, day))
            made = [ConsumptionLine.objects.create(company=membership.company, consumption=row, item=item,
                                                   batch=batch, location=location, qty=qty, uom=item.base_uom)
                    for item, batch, location, qty in lines]
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.ISSUE_RETURN, work_date=day,
            lines=[posting.Line(item=line.item, warehouse=store, location=line.location, batch=line.batch,
                                qty=line.qty_base, line_id=line.pk, note=f"{row.number} left over, {order.number}",
                                unit_cost=(used[line.item_id][1] / used[line.item_id][0]).quantize(FOUR, ROUND_HALF_UP)
                                if used[line.item_id][0] else None)
                   for line in made])
        row.status, row.posted_at, row.posted_by = Consumption.Status.POSTED, moves[0].posted_at, actor
        row.save(update_fields=["status", "posted_at", "posted_by"])


def _mix_split(good, count, item):
    """``good`` shared over ``count`` mixes, whole to the unit; the last takes the rest."""
    step = Decimal(1).scaleb(-item.base_uom.decimals)
    share = (good / count).quantize(step, rounding=ROUND_FLOOR)
    return [share] * (count - 1) + [good - share * (count - 1)]


def _batch_no(company_id, order, day, suffix=""):
    base = f"{order.item.code.replace('-', '')}-{day:%d%m%y}"
    n = 1
    while True:
        number = f"{base}-{n}{suffix}"
        if not Batch.all_objects.filter(company_id=company_id, item=order.item, batch_no=number).exists():
            return number
        n += 1


@transaction.atomic
def finish(*, actor, company_id, order_id, values):
    """``values``: output_date, good and damaged (in the product's unit), parts
    ``[(store, qty)]``, costs ``[(name, amount)]``, leftovers ``{material id:
    (returned, wasted, reason)}``, reason, batch_no."""
    membership, order = _execute(actor, company_id, order_id)
    if order.status != Status.RELEASED:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: only a started "
                              "production is finished.")
    day = values.get("output_date")
    if day is None:
        raise ValidationError({"output_date": "Give the date."})
    posting.check_period_open(company_id, day)
    item = order.item
    good, damaged = values.get("good") or ZERO, values.get("damaged") or ZERO
    reason = (values.get("reason") or "").strip()
    errors = {}
    if good < 0:
        errors["good"] = "Zero or more."
    if damaged < 0:
        errors["damaged"] = "Zero or more."
    step = Decimal(1).scaleb(-item.base_uom.decimals)
    if good != good.quantize(step):
        errors["good"] = f"{item.name} is counted in whole {item.base_uom.code}."
    leftovers = values.get("leftovers") or {}
    for item_id, (_returned, wasted, why) in leftovers.items():
        if wasted > 0 and not (why or "").strip():
            errors[f"reason-{item_id}"] = "Say why it was wasted."
    if good == 0 and not reason:
        errors["reason"] = "Nothing good came out: say why (the production is recorded as failed)."
    elif good > 0 and not reason and not (LOW <= good / order.planned_base <= HIGH):
        errors["reason"] = (f"Good is {(good / order.planned_base * 100).quantize(Decimal('1'))} % of the plan: "
                            "say why.")
    parts = [(store, qty) for store, qty in (values.get("parts") or []) if qty]
    if good > 0:
        if not parts:
            parts = [(order.output_warehouse, good)]
        total = sum((qty for _store, qty in parts), ZERO)
        if total != good:
            errors["parts"] = f"The stores add up to {total.normalize():f}, not {good.normalize():f}."
        for store, _qty in parts:
            if store.kind in UNUSABLE_STORES or store.status != "active":
                errors["parts"] = f"{store.name} cannot take new stock."
        mixes = int(batches_of(order).to_integral_value(ROUND_HALF_UP)) or 1
        per_mix = item.track_batch and item.batch_per_mix and mixes > 1
        if per_mix and len(parts) > 1:
            errors["parts"] = "Every mix gets its own batch number: put them into one store."
    if errors:
        raise ValidationError(errors)
    for store, _qty in parts:
        require(actor, company_id, "production.execute", store.branch_id)

    _return_leftovers(company_id, actor, membership, order, leftovers, day)
    material = orders.material_cost(order)
    costs = [(name.strip()[:64] or "Other", amount) for name, amount in (values.get("costs") or []) if amount]
    other = sum((amount for _name, amount in costs), ZERO)
    with use_company(company_id):
        for name, amount in costs:
            OrderCost.objects.create(company=membership.company, order=order, name=name, amount=amount)
        for item_id, (returned, wasted, why) in leftovers.items():
            if returned or wasted:
                Leftover.objects.create(company=membership.company, order=order, item_id=item_id,
                                        returned=returned, wasted=wasted, reason=(why or "").strip()[:255])
    order.material_cost, order.other_cost = material, other
    order.good_qty_base, order.rejected_qty_base = good, damaged
    order.reason = reason[:500]
    order.completed_at, order.completed_by, order.updated_by = now(), actor, actor
    if good == 0:
        # A failed production: its cost is a loss (plan §11.13).
        order.status, order.unit_cost = Status.FAILED, None
        with use_company(company_id):
            order.save()
        audit(actor, membership, "production.order.failed", order,
              after={"reason": reason, "material_cost": plain(material), "other_cost": plain(other)})
        return order, None

    unit_cost = ((material + other) / good).quantize(FOUR, ROUND_HALF_UP)
    expiry = expiry_from(item, day, values.get("expiry_date"))
    if item.track_expiry and not expiry:
        raise ValidationError({"expiry_date": f"{item.name} has an expiry date: give it (or set its shelf life)."})
    status = Batch.Status.QUARANTINE if item.qc_required else Batch.Status.RELEASED
    batches = []
    with use_company(company_id):
        if item.track_batch:
            number = (values.get("batch_no") or "").strip()
            if number and Batch.objects.filter(item=item, batch_no=number).exists():
                raise ValidationError({"batch_no": f"Batch {number} of {item.name} exists already."})
            if per_mix:
                for share in _mix_split(good, mixes, item):
                    batches.append((posting.batch_for(
                        company_id=company_id, actor=actor, item=item, batch_no=_batch_no(company_id, order, day),
                        source=Batch.Source.PRODUCTION, mfg_date=day, expiry_date=expiry, unit_cost=unit_cost,
                        status=status), parts[0][0], share))
            else:
                batch = posting.batch_for(
                    company_id=company_id, actor=actor, item=item,
                    batch_no=number or _batch_no(company_id, order, day), source=Batch.Source.PRODUCTION,
                    mfg_date=day, expiry_date=expiry, unit_cost=unit_cost, status=status)
                batches = [(batch, store, qty) for store, qty in parts]
        else:
            batches = [(None, store, qty) for store, qty in parts]
        first = batches[0][0]
        output = Output(company=membership.company, created_by=actor, updated_by=actor, order=order,
                        output_date=day, qty_good=good, qty_rejected=damaged, uom=item.base_uom,
                        batch_no=first.batch_no if first else "", mfg_date=day, expiry_date=expiry, batch=first,
                        status=Output.Status.POSTED, posted_by=actor,
                        number=posting.next_number(company_id, Output.DOC_TYPE, Output.PREFIX, day.year))
        output.full_clean()
        output.save()
        for store, qty in parts:
            OutputPart.objects.create(company=membership.company, output=output, warehouse=store, qty_base=qty)
    moves = posting.post(
        company_id=company_id, actor=actor, document_type=Output.DOC_TYPE, document_id=output.pk,
        move_type=MoveType.PRODUCTION_OUTPUT, work_date=day,
        lines=[posting.Line(item=item, warehouse=store, batch=batch, qty=qty, unit_cost=unit_cost,
                            line_id=output.pk, note=f"{order.number} made") for batch, store, qty in batches])
    with use_company(company_id):
        output.posted_at = moves[0].posted_at
        output.save(update_fields=["posted_at"])
        order.status, order.unit_cost = Status.COMPLETED, unit_cost
        order.save()
    audit(actor, membership, "production.order.finished", order,
          after={"good": plain(good), "damaged": plain(damaged), "material_cost": plain(material),
                 "other_cost": plain(other), "unit_cost": plain(unit_cost),
                 "batches": [batch.batch_no for batch, _s, _q in batches if batch],
                 "stores": [store.code for store, _q in parts]})
    return order, output


# --- Reverse -------------------------------------------------------------------

def made_batches(output):
    """The batches a finish made (several when every mix has its own)."""
    ids = StockMove.all_objects.filter(document_type=Output.DOC_TYPE, document_id=output.pk,
                                       batch__isnull=False).values_list("batch_id", flat=True)
    return list(Batch.all_objects.filter(pk__in=set(ids)))


def untouched(order):
    """Whether none of the made stock was sold, moved or used since."""
    output = Output.all_objects.filter(order=order).first()
    if output is None:
        return True
    mine = StockMove.all_objects.filter(document_type=Output.DOC_TYPE, document_id=output.pk)
    batches = made_batches(output)
    if batches:
        return not StockMove.all_objects.filter(batch__in=batches).exclude(pk__in=mine).exists()
    stores = mine.values_list("warehouse_id", flat=True)
    return not (StockMove.all_objects.filter(item=order.item, warehouse_id__in=list(stores),
                                             posted_at__gt=output.posted_at).exclude(pk__in=mine).exists())


@transaction.atomic
def reverse(*, actor, company_id, order_id, reason, work_date):
    """Undo a finished (or failed) production while its stock is untouched."""
    membership, order = _execute(actor, company_id, order_id)
    if order.status not in (Status.COMPLETED, Status.FAILED):
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: only a finished or "
                              "failed production is reversed.")
    if not (reason or "").strip():
        raise ValidationError({"reason": "Say why it is reversed."})
    if not untouched(order):
        raise ValidationError("Some of what it made was already sold, moved or used: correct it with a "
                              "stock adjustment instead.")
    posting.check_period_open(company_id, work_date)
    note = f"{order.number} reversed: {reason.strip()}"[:255]
    with use_company(company_id):
        output = Output.objects.filter(order=order).first()
        if output is not None:
            posting.reverse(company_id=company_id, actor=actor, document_type=Output.DOC_TYPE,
                            document_id=output.pk, work_date=work_date, note=note)
        uses = list(order.consumptions.filter(status=Consumption.Status.POSTED).order_by("-pk"))
    try:
        for use in uses:
            posting.reverse(company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE,
                            document_id=use.pk, work_date=work_date, note=note)
    except ValidationError as exc:
        raise ValidationError("Leftovers it returned were used since: " + " ".join(exc.messages)) from None
    with use_company(company_id):
        order.status, order.reversed_at, order.reversed_by, order.updated_by = Status.REVERSED, now(), actor, actor
        order.reason = reason.strip()[:500]
        order.save()
    audit(actor, membership, "production.order.reversed", order, after={"reason": reason.strip()})
    return order


@transaction.atomic
def cancel_started(*, actor, company_id, order_id, reason, work_date):
    """After Start, cancelling returns every material first (plan §8.6, §11.21)."""
    membership, order = _execute(actor, company_id, order_id)
    if order.status != Status.RELEASED:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}.")
    if not (reason or "").strip():
        raise ValidationError({"reason": "Say why it is cancelled."})
    posting.check_period_open(company_id, work_date)
    with use_company(company_id):
        uses = list(order.consumptions.filter(status=Consumption.Status.POSTED).order_by("-pk"))
    for use in uses:
        posting.reverse(company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE,
                        document_id=use.pk, work_date=work_date,
                        note=f"{order.number} cancelled: materials back"[:255])
    with use_company(company_id):
        order.status, order.reason, order.updated_by = Status.CANCELLED, reason.strip()[:500], actor
        order.save()
    audit(actor, membership, "production.order.cancelled", order,
          before={"status": "released"}, after={"status": "cancelled", "reason": reason.strip()})
    return order
