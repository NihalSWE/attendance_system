"""Warranty (2026-10-27, docs/inventory/PRODUCTION_PLAN.md §10; §15 phase 8).

- **One company switch** (Inventory settings), off by default. Off, nothing
  about warranty shows anywhere.
- **Any item** can carry a warranty, in months: on what you sell it runs from
  the day it was delivered; on what you buy it is the supplier's, from the
  day it was received.
- **Serial numbers** for items tracked one by one: made when a production
  finishes (``production.making``), or entered for units received; scanned
  against a delivery; printed as labels.
- **Claims**: found by serial (or by item and the day it was delivered); the
  page says whether it is still covered. Replace, repair (cost), refund
  (amount) or refuse (reason). Covering one out of warranty needs a reason.
  The faulty unit can go into a store (the rejected store); the new unit
  leaves one.

Recording needs "Record warranty claims and serial numbers"
(``inventory.warranty``).
"""

import calendar
import datetime
import re
from collections import Counter
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from auditlog.services import record_company_event
from common.tenant import use_company
from inventory import access, fefo, posting
from inventory.models import ItemCost, MoveType, SerialNumber, WarrantyClaim
from inventory.posting import settings_for

CODE = "inventory.warranty"
ZERO = Decimal("0")
#: The most serial numbers one finish or one entry makes.
MOST = 20000


def warranty_on(company_id):
    return bool(company_id) and access.inventory_on(company_id) and settings_for(company_id).warranty_on


def _require(actor, company_id, branch_id=None):
    membership = access.require(actor, company_id, CODE, branch_id)
    if not warranty_on(company_id):
        raise PermissionDenied("Warranty is off: switch it on in Inventory settings.")
    return membership


def add_months(day, months):
    """``day`` plus ``months``; 31 Jan + 1 month = 28/29 Feb."""
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def covered_until(item, start):
    if not item.warranty_months or not start:
        return None
    return add_months(start, item.warranty_months)


def codes(text):
    """Serial numbers typed, pasted or scanned: one per line, comma or space."""
    found, seen = [], set()
    for code in re.split(r"[\s,;]+", text or ""):
        code = code.strip().upper()
        if code and code not in seen:
            seen.add(code)
            found.append(code)
    return found


def _audit(actor, membership, action, obj, **after):
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action=f"inventory.warranty.{action}", obj=obj, after=after)


# --- serial numbers -----------------------------------------------------------

def made_at_finish(*, actor, company_id, item, lines, source_id):
    """Serial numbers for what a production made (``[(batch, store, qty)]``):
    the batch number and a running number, BIS2710261-00001. None unless the
    company's warranty is on and the item takes serial numbers."""
    if not item.serial_numbers or not warranty_on(company_id):
        return []
    made = []
    with use_company(company_id):
        for batch, _store, qty in lines:
            count = int(qty)
            if count != qty or count > MOST:
                raise ValidationError({"good": f"{item.name} takes a serial number per unit: give a whole number "
                                               f"up to {MOST}."})
            prefix = (batch.batch_no if batch else item.code).replace(" ", "")
            taken = SerialNumber.objects.filter(serial__startswith=f"{prefix}-").count()
            made += [SerialNumber(company_id=company_id, item=item, batch=batch, serial=f"{prefix}-{taken + n:05d}",
                                  source="production", source_id=source_id, created_by=actor, updated_by=actor)
                     for n in range(1, count + 1)]
        SerialNumber.objects.bulk_create(made)
    return made


@transaction.atomic
def register(*, actor, company_id, item, serials, received_on, supplier=None, batch=None):
    """Serial numbers of units received (or already in stock) of an item."""
    membership = _require(actor, company_id)
    if not item.serial_numbers:
        raise ValidationError({"item": f"{item.name} does not take serial numbers: switch it on for the item."})
    if not serials:
        raise ValidationError({"serials": "Scan or type at least one serial number."})
    if len(serials) > MOST:
        raise ValidationError({"serials": f"At most {MOST} at a time."})
    if received_on is None:
        raise ValidationError({"received_on": "Give the date they were received."})
    with use_company(company_id):
        taken = list(SerialNumber.objects.filter(serial__in=serials).values_list("serial", flat=True))
        if taken:
            raise ValidationError({"serials": "Already entered: " + ", ".join(taken[:10])
                                   + ("…" if len(taken) > 10 else "")})
        made = SerialNumber.objects.bulk_create([
            SerialNumber(company=membership.company, item=item, batch=batch, serial=code, source="receipt",
                         received_on=received_on, supplier=supplier, created_by=actor, updated_by=actor)
            for code in serials])
    _audit(actor, membership, "serials_added", item, count=len(made), first=serials[0])
    return made


@transaction.atomic
def deliver(*, actor, company_id, delivery, serials):
    """The serial numbers that went out on a delivery: each must be in stock, of
    an item on the delivery, and no more of an item than was delivered."""
    from sales.models import Delivery

    membership = _require(actor, company_id, delivery.warehouse.branch_id)
    if delivery.status != Delivery.Status.POSTED:
        raise ValidationError({"delivery": f"{delivery.number} is not delivered yet."})
    if not serials:
        raise ValidationError({"serials": "Scan or type at least one serial number."})
    with use_company(company_id):
        delivered = {}
        for line in delivery.lines.select_related("item"):
            delivered[line.item_id] = delivered.get(line.item_id, ZERO) + line.qty_base
        found = {row.serial: row for row in SerialNumber.objects.select_for_update().filter(serial__in=serials)
                 .select_related("item")}
        problems = []
        for code in serials:
            row = found.get(code)
            if row is None:
                problems.append(f"{code}: no such serial number")
            elif row.item_id not in delivered:
                problems.append(f"{code}: {row.item.name} is not on {delivery.number}")
            elif row.status != SerialNumber.Status.IN_STOCK:
                problems.append(f"{code}: {row.get_status_display().lower()} already")
        already = dict(SerialNumber.objects.filter(delivery_id=delivery.pk).values("item_id")
                       .annotate(n=Count("pk")).values_list("item_id", "n"))
        wanted = Counter(found[code].item_id for code in serials if code in found)
        for item_id, count in wanted.items():
            if item_id in delivered and count + already.get(item_id, 0) > delivered[item_id]:
                problems.append(f"More serial numbers than the {delivered[item_id].normalize():f} delivered")
        if problems:
            raise ValidationError({"serials": problems})
        customer = delivery.order.customer
        SerialNumber.objects.filter(pk__in=[found[code].pk for code in serials]).update(
            status=SerialNumber.Status.DELIVERED, delivered_on=delivery.delivery_date, customer=customer,
            delivery_id=delivery.pk, updated_by=actor)
    _audit(actor, membership, "serials_delivered", delivery, count=len(serials), customer=customer.pk)
    return len(serials)


def lookup(company_id, code, today=None):
    """What a claim screen shows for a serial number; None when unknown."""
    today = today or timezone.localdate()
    with use_company(company_id):
        row = (SerialNumber.objects.select_related("item", "customer", "supplier")
               .filter(serial=(code or "").strip().upper()).first())
    if row is None:
        return None
    start = row.delivered_on or row.received_on
    until = covered_until(row.item, start)
    return {"serial": row, "item": row.item, "start": start, "until": until,
            "covered": bool(until and today <= until), "who": row.customer or row.supplier}


# --- claims -------------------------------------------------------------------

def _unit_cost(company_id, item, serial):
    if serial is not None and serial.batch_id and serial.batch.unit_cost is not None:
        return serial.batch.unit_cost
    cost = ItemCost.all_objects.filter(company_id=company_id, item=item, warehouse=None).values_list(
        "avg_cost", flat=True).first()
    return cost or item.standard_cost or ZERO


@transaction.atomic
def record_claim(*, actor, company_id, values):
    """``values``: serial (code) or item and sold_on; customer, claim_date,
    problem, outcome, cost, reason, back_to, replaced_from."""
    membership = _require(actor, company_id)
    Outcome = WarrantyClaim.Outcome
    day = values.get("claim_date") or timezone.localdate()
    code = (values.get("serial") or "").strip().upper()
    errors = {}
    serial = None
    if code:
        found = lookup(company_id, code, day)
        if found is None:
            raise ValidationError({"serial": "No unit with this serial number."})
        serial, item, start = found["serial"], found["item"], found["start"]
        customer = serial.customer or values.get("customer")
    else:
        item, start, customer = values.get("item"), values.get("sold_on"), values.get("customer")
        if item is None:
            errors["serial"] = "Scan the serial number, or choose the item."
        elif item.serial_numbers:
            errors["serial"] = f"{item.name} has serial numbers: scan the unit's."
        if start is None and values.get("outcome") != Outcome.REFUSE:
            errors["sold_on"] = "When was it delivered?"
    if not (values.get("problem") or "").strip():
        errors["problem"] = "Say what is wrong."
    outcome = values.get("outcome")
    if outcome not in Outcome.values:
        errors["outcome"] = "Choose what you do."
    if errors:
        raise ValidationError(errors)
    until = covered_until(item, start)
    covered = bool(until and day <= until)
    reason = (values.get("reason") or "").strip()
    if outcome == Outcome.REFUSE and not reason:
        raise ValidationError({"reason": "Say why it is refused."})
    if outcome != Outcome.REFUSE and not covered and not reason:
        raise ValidationError({"reason": (f"Out of warranty since {until:%d %b %Y}" if until else
                                          f"{item.name} carries no warranty")
                               + ": refuse it, or say why you cover it."})
    if serial is not None and WarrantyClaim.all_objects.filter(
            company_id=company_id, serial=serial, outcome__in=(Outcome.REPLACE, Outcome.REFUND)).exists():
        raise ValidationError({"serial": f"{serial.serial} was replaced or refunded already."})
    back_to, replaced_from = values.get("back_to"), values.get("replaced_from")
    if outcome == Outcome.REFUSE:
        back_to = replaced_from = None
    if replaced_from is not None and outcome != Outcome.REPLACE:
        replaced_from = None
    with use_company(company_id):
        claim = WarrantyClaim(
            company=membership.company, created_by=actor, updated_by=actor, claim_date=day, item=item,
            serial=serial, customer=customer, sold_on=start, covered_until=until, covered=covered,
            problem=values["problem"].strip()[:255], outcome=outcome, cost=values.get("cost") or ZERO,
            reason=reason[:255], back_to=back_to, replaced_from=replaced_from,
            number=posting.next_number(company_id, WarrantyClaim.DOC_TYPE, WarrantyClaim.PREFIX, day.year))
        claim.full_clean()
        claim.save()
    # The stock moves of the claim, in the company's own records.
    with use_company(company_id):
        lines = []
        if back_to is not None:
            access.require(actor, company_id, CODE, back_to.branch_id)
            batch = None
            if item.track_batch or item.track_expiry:
                # A faulty unit is kept in its own blocked batch: never sold or used again.
                from inventory.models import Batch

                old = serial.batch if serial is not None else None
                batch = posting.batch_for(company_id=company_id, actor=actor, item=item, batch_no=claim.number,
                                          source=Batch.Source.ADJUSTMENT, mfg_date=old.mfg_date if old else None,
                                          expiry_date=(old.expiry_date if old and old.expiry_date else day),
                                          unit_cost=_unit_cost(company_id, item, serial), status=Batch.Status.BLOCKED)
            lines.append(posting.Line(item=item, warehouse=back_to, batch=batch,
                                      qty=Decimal("1"), unit_cost=_unit_cost(company_id, item, serial),
                                      line_id=claim.pk, note=f"{claim.number}: faulty unit back"))
            if serial is not None:
                SerialNumber.all_objects.filter(pk=serial.pk).update(status=SerialNumber.Status.RETURNED)
        if lines:
            posting.post(company_id=company_id, actor=actor, document_type=WarrantyClaim.DOC_TYPE,
                         document_id=claim.pk, move_type=MoveType.SALES_RETURN, work_date=day, lines=lines)
        if replaced_from is not None:
            access.require(actor, company_id, CODE, replaced_from.branch_id)
            out = [posting.Line(item=item, warehouse=replaced_from, batch=row.batch, location=row.location,
                                qty=-take, line_id=claim.pk, note=f"{claim.number}: new unit out")
                   for row, take in fefo.allocate(company_id, item, replaced_from, Decimal("1"), day)]
            posting.post(company_id=company_id, actor=actor, document_type=WarrantyClaim.DOC_TYPE,
                         document_id=claim.pk, move_type=MoveType.DELIVERY, work_date=day, lines=out)
    _audit(actor, membership, "claim", claim, number=claim.number, outcome=outcome, covered=covered,
           serial=code, cost=format(claim.cost.normalize(), "f"))
    return claim


def summary(company_id, since=None):
    """Claims per item (how many, covered, refused, cost) and the commonest problems."""
    with use_company(company_id):
        rows = WarrantyClaim.objects.all()
        if since:
            rows = rows.filter(claim_date__gte=since)
        per_item = list(rows.values("item__code", "item__name").annotate(
            claims=Count("pk"), cost=Sum("cost")).order_by("-claims", "item__code"))
        refused = dict(rows.filter(outcome=WarrantyClaim.Outcome.REFUSE).values("item__code")
                       .annotate(n=Count("pk")).values_list("item__code", "n"))
        covered = dict(rows.filter(covered=True).values("item__code").annotate(n=Count("pk"))
                       .values_list("item__code", "n"))
        problems = Counter(problem.strip().lower() for problem in rows.values_list("problem", flat=True))
    for row in per_item:
        row["refused"], row["covered"] = refused.get(row["item__code"], 0), covered.get(row["item__code"], 0)
    return per_item, problems.most_common(5)
