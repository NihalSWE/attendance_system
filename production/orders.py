"""Production orders: plan, release, use materials, post the output, close.

- **Planned** (``production.plan`` in the finished-goods store's branch): the
  product, how many, the BOM version (the active one), the stores.
- **Released** by someone with ``production.plan`` - not the person who
  planned it, unless owner/admin (the factory manager's yes, Phase 0).
- **Materials** (``production.execute``): "Use planned materials" drafts what
  the BOM still needs, first expiry first; lines can be added by scanning
  package labels; posting takes them out of the materials store. Unused
  material goes back at the cost it was used at.
- **Output** (``production.execute``): posted once, at the end: the good
  quantity becomes a new batch of the product (expiry from shelf life, in
  quarantine if the product needs QC) in the finished-goods store, with one
  label per package, at **unit cost = materials used (less returns) ÷ good
  quantity**. Rejects are recorded, not stocked. The order is then Completed;
  Close locks it. Nothing more is used after the output.

Example: a 1,000-bottle batch uses 45 kg mango pulp at 210 (9,450), 22 kg
sugar at 125 (2,750), 1,015 bottles at 3.20 (3,248) and 1,010 caps at 0.90
(909) = 16,357; 990 good bottles -> 16.5222 a bottle.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, posting
from inventory import labels as label_service
from inventory.doc_common import (
    _writable,
    audit,
    check_own_approval,
    check_qty,
    expiry_from,
    get,
    now,
    plain,
    require,
    unit_factor,
)
from inventory.issues import _in_unit, label_from_scan
from inventory.models import Batch, MoveType, StockMove
from production.models import Bom, BomLine, Consumption, ConsumptionLine, Output, ProductionOrder

FOUR = Decimal("0.0001")
Status = ProductionOrder.Status
FIELDS = ("item", "bom", "planned_qty", "uom", "planned_date", "material_warehouse", "output_warehouse",
          "line_name", "shift_name", "note")
LINE_FIELDS = ("item", "batch", "location", "qty", "uom", "not_fefo_reason", "note")
OUTPUT_FIELDS = ("output_date", "qty_good", "qty_rejected", "uom", "batch_no", "mfg_date", "expiry_date",
                 "location", "packages", "note")


def get_order(*, actor, company_id, pk, code="production.plan"):
    order = get(ProductionOrder, company_id, pk, "Production order")
    return require(actor, company_id, code, order.output_warehouse.branch_id), order


@transaction.atomic
def create_order(*, actor, company_id, values):
    values = _writable(values, FIELDS)
    item, output, materials = values.get("item"), values.get("output_warehouse"), values.get("material_warehouse")
    if item is None:
        raise ValidationError({"item": "Choose the product."})
    bom = values.get("bom")
    with use_company(company_id):
        if bom is None:
            bom = values["bom"] = Bom.objects.filter(item=item, status=Bom.Status.ACTIVE).first()
    if bom is None:
        raise ValidationError({"item": f"{item.code} has no active BOM: activate its recipe first."})
    if bom.item_id != item.pk or bom.status != Bom.Status.ACTIVE:
        raise ValidationError({"bom": "Choose the product's active BOM version."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("planned_qty"), factor, field="planned_qty")
    for field, store in (("material_warehouse", materials), ("output_warehouse", output)):
        if store is None or store.status != ActiveStatus.ACTIVE:
            raise ValidationError({field: "Choose an active store."})
        if store.kind in ("quarantine", "rejected", "transit"):
            raise ValidationError({field: f"{store.code} is a {store.get_kind_display().lower()} store."})
    if values.get("planned_date") is None:
        raise ValidationError({"planned_date": "Give the date."})
    membership = require(actor, company_id, "production.plan", output.branch_id)
    with use_company(company_id):
        order = ProductionOrder(company=membership.company, created_by=actor, updated_by=actor, factor=factor,
                                number=posting.next_number(company_id, ProductionOrder.DOC_TYPE,
                                                           ProductionOrder.PREFIX, values["planned_date"].year),
                                **values)
        order.full_clean()
        order.save()
        audit(actor, membership, "production.order.created", order,
              after={"number": order.number, "item": item.code, "qty": plain(order.planned_base),
                     "bom": bom.version})
    return order


@transaction.atomic
def release(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status != Status.PLANNED:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}.")
    check_own_approval(membership, actor, order.created_by_id)
    with use_company(company_id):
        order.status, order.released_at, order.released_by, order.updated_by = Status.RELEASED, now(), actor, actor
        order.save()
        audit(actor, membership, "production.order.released", order,
              before={"status": "planned"}, after={"status": "released"})
    return order


@transaction.atomic
def cancel(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status not in (Status.PLANNED, Status.RELEASED):
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}.")
    with use_company(company_id):
        if order.consumptions.filter(status=Consumption.Status.POSTED).exists():
            raise ValidationError("Materials were used: return them, then post the output (or a zero run).")
        before, order.status, order.updated_by = order.status, Status.CANCELLED, actor
        order.save()
        order.consumptions.filter(status=Consumption.Status.DRAFT).update(status=Consumption.Status.CANCELLED)
        audit(actor, membership, "production.order.cancelled", order,
              before={"status": before}, after={"status": "cancelled"})
    return order


@transaction.atomic
def close(*, actor, company_id, order_id):
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id)
    if order.status != Status.COMPLETED:
        raise ValidationError("Only a completed order is closed.")
    with use_company(company_id):
        order.status, order.closed_at, order.closed_by, order.updated_by = Status.CLOSED, now(), actor, actor
        order.save()
        audit(actor, membership, "production.order.closed", order,
              before={"status": "completed"}, after={"status": "closed"})
    return order


# --------------------------------------------------------------------------
# Planned vs used
# --------------------------------------------------------------------------

def _used(order):
    """``{item_id: (base units used net of returns, cost)}`` from the ledger."""
    ids = list(Consumption.all_objects.filter(order=order, status=Consumption.Status.POSTED)
               .values_list("pk", flat=True))
    rows = (StockMove.all_objects.filter(document_type=Consumption.DOC_TYPE, document_id__in=ids)
            .values("item_id").annotate(qty=Sum("qty"), value=Sum("value")))
    return {r["item_id"]: (-r["qty"], -r["value"]) for r in rows}


def usage(order):
    """Planned vs used per material: ``[{item, planned, used, left, variance, cost}]``."""
    bom = order.bom
    used = _used(order)
    rows, seen = [], set()
    for line in BomLine.all_objects.filter(bom=bom).select_related("item__base_uom").order_by("is_packaging", "pk"):
        planned = line.planned_base(order.planned_base, bom.batch_size_base)
        qty, cost = used.get(line.item_id, (Decimal("0"), Decimal("0")))
        rows.append({"item": line.item, "planned": planned, "used": qty, "left": max(planned - qty, Decimal("0")),
                     "variance": qty - planned, "cost": cost, "packaging": line.is_packaging})
        seen.add(line.item_id)
    from inventory.models import Item

    for item_id, (qty, cost) in used.items():
        if item_id not in seen:
            item = Item.all_objects.select_related("base_uom").get(pk=item_id)
            rows.append({"item": item, "planned": Decimal("0"), "used": qty, "left": Decimal("0"),
                         "variance": qty, "cost": cost, "packaging": False})
    return rows


def material_cost(order):
    return sum((cost for _qty, cost in _used(order).values()), Decimal("0"))


# --------------------------------------------------------------------------
# Consumption
# --------------------------------------------------------------------------

def _running(order):
    if order.status != Status.RELEASED:
        raise ValidationError(f"{order.number} is {order.get_status_display().lower()}: materials are used "
                              "only on a released order before its output.")


def get_consumption(*, actor, company_id, pk):
    row = get(Consumption, company_id, pk, "Material use")
    membership = require(actor, company_id, "production.execute", row.order.output_warehouse.branch_id)
    return membership, row


@transaction.atomic
def start_consumption(*, actor, company_id, order_id, kind=Consumption.Kind.ISSUE, work_date=None,
                      fill_planned=True):
    """A draft material use (or return) for the order; "use planned materials"
    fills what the BOM still needs, first expiry first."""
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id, code="production.execute")
    _running(order)
    if kind not in Consumption.Kind.values:
        raise ValidationError("Unknown kind.")
    work_date = work_date or order.planned_date
    short = []
    with use_company(company_id):
        if order.consumptions.filter(status=Consumption.Status.DRAFT).exists():
            raise ValidationError("A draft material use is open on this order: post or cancel it first.")
        row = Consumption.objects.create(
            company=membership.company, created_by=actor, updated_by=actor, order=order, kind=kind,
            work_date=work_date, number=posting.next_number(company_id, Consumption.DOC_TYPE,
                                                            Consumption.PREFIX, work_date.year))
        if kind == Consumption.Kind.ISSUE and fill_planned:
            for need in usage(order):
                want = need["left"]
                item = need["item"]
                if want <= 0:
                    continue
                if item.track_batch:
                    for balance in fefo.usable_rows(company_id, item, order.material_warehouse, work_date):
                        if want <= 0:
                            break
                        take = min(balance.available, want)
                        ConsumptionLine.objects.create(company=membership.company, consumption=row, item=item,
                                                       batch=balance.batch, location=balance.location,
                                                       qty=take, uom=item.base_uom)
                        want -= take
                else:
                    have = sum((b.available for b in fefo.usable_rows(company_id, item, order.material_warehouse,
                                                                       work_date)), Decimal("0"))
                    take = min(have, want)
                    if take > 0:
                        ConsumptionLine.objects.create(company=membership.company, consumption=row, item=item,
                                                       qty=take, uom=item.base_uom)
                        want -= take
                if want > 0:
                    short.append(f"{item.code} short by {want.normalize():f} {item.base_uom.code}")
        audit(actor, membership, "production.consumption.started", row,
              after={"number": row.number, "order": order.number, "kind": kind})
    row.shortages = short
    return row


def _draft(row):
    if row.status != Consumption.Status.DRAFT:
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}.")


@transaction.atomic
def add_line(*, actor, company_id, consumption_id, values, label=None):
    membership, row = get_consumption(actor=actor, company_id=company_id, pk=consumption_id)
    _draft(row)
    order = row.order
    _running(order)
    values = _writable(values, LINE_FIELDS)
    item, batch, location = values.get("item"), values.get("batch"), values.get("location")
    if item is None:
        raise ValidationError({"item": "Choose the material."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor, active=False)
    if location is not None and location.warehouse_id != order.material_warehouse_id:
        raise ValidationError({"location": f"Bin {location.code} is not in {order.material_warehouse.code}."})
    if batch is not None and batch.item_id != item.pk:
        raise ValidationError({"batch": "That batch is of another material."})
    with use_company(company_id):
        if label is not None and ConsumptionLine.objects.filter(consumption=row, label=label).exists():
            raise ValidationError(f"Label {label.code} is on this material use already.")
        if row.kind == Consumption.Kind.ISSUE and item.track_batch and batch is None:
            made = []
            for balance, take in fefo.allocate(company_id, item, order.material_warehouse,
                                               values["qty"] * factor, row.work_date, location):
                qty, uom, line_factor = _in_unit(take, values["uom"], factor, item)
                made.append(ConsumptionLine.objects.create(
                    company=membership.company, consumption=row, item=item, batch=balance.batch,
                    location=balance.location, qty=qty, uom=uom, factor=line_factor, note=values.get("note", "")))
            return made
        if row.kind == Consumption.Kind.ISSUE and batch is not None:
            if not batch.usable_on(row.work_date):
                raise ValidationError({"batch": f"Batch {batch.batch_no} cannot be used "
                                                f"({'expired' if batch.expiry_date and batch.expiry_date < row.work_date else batch.get_status_display().lower()})."})
            if fefo.expiring_first(company_id, item, order.material_warehouse, row.work_date, batch) \
                    and not (values.get("not_fefo_reason") or "").strip():
                first = fefo.first_batch(company_id, item, order.material_warehouse, row.work_date)
                raise ValidationError({"not_fefo_reason": (
                    f"Batch {first.batch_no} expires first ({first.expiry_date:%d %b %Y}); "
                    "say why this one is used instead.")})
        if row.kind == Consumption.Kind.RETURN:
            used_qty, _cost = _used(order).get(item.pk, (Decimal("0"), Decimal("0")))
            on_draft = sum((l.qty_base for l in row.lines.filter(item=item)), Decimal("0"))
            if values["qty"] * factor + on_draft > used_qty:
                raise ValidationError({"qty": f"More than used: {used_qty - on_draft:f} {item.base_uom.code} "
                                              f"of {item.code} can go back."})
            if item.track_batch and batch is None:
                raise ValidationError({"batch": f"{item.code} keeps batches: say which batch goes back."})
        line = ConsumptionLine(company=membership.company, consumption=row, factor=factor, label=label, **values)
        line.full_clean()
        line.save()
        return [line]


@transaction.atomic
def scan_into_consumption(*, actor, company_id, consumption_id, code):
    _, row = get_consumption(actor=actor, company_id=company_id, pk=consumption_id)
    values, label = label_from_scan(company_id, code, row.order.material_warehouse)
    values["not_fefo_reason"] = "Taken by scanning" if values.get("batch") else ""
    return add_line(actor=actor, company_id=company_id, consumption_id=consumption_id, values=values,
                    label=label)


@transaction.atomic
def remove_line(*, actor, company_id, consumption_id, line_id):
    _, row = get_consumption(actor=actor, company_id=company_id, pk=consumption_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this material use.")


@transaction.atomic
def post_consumption(*, actor, company_id, consumption_id):
    membership, row = get_consumption(actor=actor, company_id=company_id, pk=consumption_id)
    with use_company(company_id):
        row = Consumption.objects.select_for_update(of=("self",)).get(pk=row.pk)
        _draft(row)
        order = ProductionOrder.objects.select_for_update(of=("self",)).select_related(
            "material_warehouse").get(pk=row.order_id)
        _running(order)
        lines = list(row.lines.select_related("item__base_uom", "batch", "location", "label").order_by("pk"))
        if not lines:
            raise ValidationError("Add the materials before posting.")
        if row.kind == Consumption.Kind.ISSUE:
            moves = posting.post(
                company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE, document_id=row.pk,
                move_type=MoveType.PRODUCTION_CONSUME, work_date=row.work_date,
                lines=[posting.Line(item=l.item, warehouse=order.material_warehouse, location=l.location,
                                    batch=l.batch, qty=-l.qty_base, line_id=l.pk, note=f"{row.number} for {order.number}")
                       for l in lines])
            from inventory.models import BarcodeLabel

            for line in lines:
                if line.label_id and line.qty_base >= line.label.quantity * unit_factor(line.item, line.label.uom):
                    BarcodeLabel.objects.filter(pk=line.label_id).update(status=BarcodeLabel.Status.CONSUMED)
        else:
            used = _used(order)
            moves = posting.post(
                company_id=company_id, actor=actor, document_type=Consumption.DOC_TYPE, document_id=row.pk,
                move_type=MoveType.ISSUE_RETURN, work_date=row.work_date,
                lines=[posting.Line(item=l.item, warehouse=order.material_warehouse, location=l.location,
                                    batch=l.batch, qty=l.qty_base, line_id=l.pk,
                                    # Back at the cost it was used at on this order.
                                    unit_cost=(used[l.item_id][1] / used[l.item_id][0]).quantize(FOUR, ROUND_HALF_UP)
                                    if used.get(l.item_id) and used[l.item_id][0] else None,
                                    note=f"{row.number} unused, from {order.number}") for l in lines])
        row.status, row.posted_at, row.posted_by, row.updated_by = Consumption.Status.POSTED, moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "production.consumption.posted", row,
              after={"order": order.number, "kind": row.kind, "lines": len(lines),
                     "value": plain(-sum((m.value for m in moves), Decimal("0")))})
    return row


@transaction.atomic
def cancel_consumption(*, actor, company_id, consumption_id):
    membership, row = get_consumption(actor=actor, company_id=company_id, pk=consumption_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = Consumption.Status.CANCELLED, actor
        row.save()
    return row


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------

def _batch_no(company_id, order, mfg):
    base = f"{order.item.code.replace('-', '')}-{mfg:%y%m%d}"
    if order.line_name:
        base += "-" + "".join(ch for ch in order.line_name.upper() if ch.isalnum())[:6]
    number, n = base, 1
    while Batch.all_objects.filter(company_id=company_id, item=order.item, batch_no=number).exists():
        n += 1
        number = f"{base}-{n}"
    return number


@transaction.atomic
def post_output(*, actor, company_id, order_id, values):
    """The run is complete: the good quantity into the finished-goods store as
    a new batch at materials ÷ good quantity, labels made, the order Completed."""
    membership, order = get_order(actor=actor, company_id=company_id, pk=order_id, code="production.execute")
    values = _writable(values, OUTPUT_FIELDS)
    with use_company(company_id):
        order = ProductionOrder.objects.select_for_update(of=("self",)).select_related(
            "item__base_uom", "output_warehouse").get(pk=order.pk)
        _running(order)
        if order.consumptions.filter(status=Consumption.Status.DRAFT).exists():
            raise ValidationError("A draft material use is still open: post or cancel it first.")
        item = order.item
        factor = unit_factor(item, values.get("uom"))
        check_qty(item, values.get("qty_good"), factor, field="qty_good", active=False)
        rejected = values.get("qty_rejected") or Decimal("0")
        values["qty_rejected"] = rejected
        if rejected < 0:
            raise ValidationError({"qty_rejected": "Zero or more."})
        location = values.get("location")
        if location is not None and location.warehouse_id != order.output_warehouse_id:
            raise ValidationError({"location": f"Bin {location.code} is not in {order.output_warehouse.code}."})
        mfg = values.get("mfg_date") or values.get("output_date")
        if values.get("output_date") is None or mfg is None:
            raise ValidationError({"output_date": "Give the date."})
        values["mfg_date"] = mfg
        expiry = expiry_from(item, mfg, values.get("expiry_date"))
        if item.track_expiry and not expiry:
            raise ValidationError({"expiry_date": f"{item.code} has an expiry date: give it (or set its shelf life)."})
        values["expiry_date"] = expiry
        cost = material_cost(order)
        good_base = (values["qty_good"] * factor).quantize(FOUR)
        if cost <= 0:
            raise ValidationError("No materials were used on this order: use them before posting the output.")
        unit_cost = (cost / good_base).quantize(FOUR, ROUND_HALF_UP)
        batch = None
        if item.track_batch:
            number = (values.get("batch_no") or "").strip() or _batch_no(company_id, order, mfg)
            if Batch.objects.filter(item=item, batch_no=number).exists():
                raise ValidationError({"batch_no": f"Batch {number} of {item.code} exists already."})
            batch = posting.batch_for(company_id=company_id, actor=actor, item=item, batch_no=number,
                                      source=Batch.Source.PRODUCTION, mfg_date=mfg, expiry_date=expiry,
                                      unit_cost=unit_cost,
                                      status=Batch.Status.QUARANTINE if item.qc_required else Batch.Status.RELEASED)
            values["batch_no"] = number
        output = Output(company=membership.company, created_by=actor, updated_by=actor, order=order, factor=factor,
                        batch=batch, status=Output.Status.POSTED, posted_by=actor,
                        number=posting.next_number(company_id, Output.DOC_TYPE, Output.PREFIX,
                                                   values["output_date"].year), **values)
        output.full_clean()
        output.save()
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=Output.DOC_TYPE, document_id=output.pk,
            move_type=MoveType.PRODUCTION_OUTPUT, work_date=output.output_date,
            lines=[posting.Line(item=item, warehouse=order.output_warehouse, location=location, batch=batch,
                                qty=good_base, unit_cost=unit_cost, line_id=output.pk,
                                note=f"{order.number} output")])
        output.posted_at = moves[0].posted_at
        output.save(update_fields=["posted_at"])
        if output.packages:
            label_service.package_labels(
                actor=actor, company_id=company_id, item_id=item.pk, batch_id=batch.pk if batch else None,
                uom_id=output.uom_id, quantity=(output.qty_good / output.packages).quantize(FOUR),
                count=output.packages, warehouse_id=order.output_warehouse_id,
                location_id=location.pk if location else None, source_type="production_output",
                source_id=output.pk, permission="production.execute")
        order.status, order.completed_at, order.completed_by, order.updated_by = Status.COMPLETED, now(), actor, actor
        order.material_cost, order.good_qty_base = cost, good_base
        order.rejected_qty_base = (rejected * factor).quantize(FOUR)
        order.unit_cost = unit_cost
        order.save()
        audit(actor, membership, "production.output.posted", order,
              after={"output": output.number, "batch": output.batch_no, "good": plain(good_base),
                     "rejected": plain(order.rejected_qty_base), "material_cost": plain(cost),
                     "unit_cost": plain(unit_cost)})
    return output


def output_labels(output):
    from inventory.models import BarcodeLabel

    return BarcodeLabel.all_objects.filter(company_id=output.company_id, source_type="production_output",
                                           source_id=output.pk).order_by("pk")


@transaction.atomic
def record_qc(*, actor, company_id, order_id, passed, notes=""):
    """QC of a finished batch that waits in quarantine (``purchase.qc``)."""
    _, order = get_order(actor=actor, company_id=company_id, pk=order_id, code="inventory.view")
    membership = require(actor, company_id, "purchase.qc", order.output_warehouse.branch_id)
    with use_company(company_id):
        output = Output.objects.select_related("batch").filter(order=order).first()
        if output is None or output.batch is None or output.batch.status != Batch.Status.QUARANTINE:
            raise ValidationError("Nothing of this order waits for QC.")
        notes = (notes or "").strip()
        if not passed and not notes:
            raise ValidationError({"notes": "Say what failed."})
        output.batch.status = Batch.Status.RELEASED if passed else Batch.Status.BLOCKED
        output.batch.updated_by = actor
        output.batch.save(update_fields=["status", "updated_by", "updated_at"])
        audit(actor, membership, f"production.qc.{'passed' if passed else 'failed'}", order,
              after={"batch": output.batch.batch_no, "notes": notes})
    return output
