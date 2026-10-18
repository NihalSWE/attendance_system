"""BOMs (recipes), in versions. Needs "Set up BOMs" (``production.bom``) in
any branch: a recipe belongs to the company.

A version is a **draft** while its lines are written; **activating** it makes
it the one new production orders use and archives the version before it
(orders already made keep the version they were made with). To change an
active recipe, start a **new version**: it copies the latest one's lines.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory.doc_common import _writable, audit, check_qty, now, plain, require, unit_factor
from inventory.models import ItemKind
from production.models import Bom, BomLine

CODE = "production.bom"
FIELDS = ("item", "batch_size", "batch_uom", "effective_from", "note")
LINE_FIELDS = ("item", "qty", "uom", "scrap_pct", "is_packaging", "note")


def get_bom(*, actor, company_id, pk, code=CODE):
    membership = require(actor, company_id, code)
    with use_company(company_id):
        bom = Bom.objects.select_related("item__base_uom", "batch_uom").filter(pk=pk).first()
    if bom is None:
        raise PermissionDenied("BOM not found in this company.")
    return membership, bom


def _draft(bom):
    if bom.status != Bom.Status.DRAFT:
        raise ValidationError(f"{bom} is {bom.get_status_display().lower()}: start a new version to change it.")


@transaction.atomic
def create_bom(*, actor, company_id, values, copy_from=None):
    """A new draft version of a product's recipe (copying ``copy_from``'s lines)."""
    membership = require(actor, company_id, CODE)
    values = _writable(values, FIELDS)
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the finished product."})
    if item.kind != ItemKind.FINISHED:
        raise ValidationError({"item": f"{item.code} is not a finished product."})
    factor = unit_factor(item, values.get("batch_uom"))
    check_qty(item, values.get("batch_size"), factor, field="batch_size")
    with use_company(company_id):
        version = (Bom.objects.filter(item=item).aggregate(top=Max("version"))["top"] or 0) + 1
        bom = Bom(company=membership.company, created_by=actor, updated_by=actor, version=version,
                  batch_factor=factor, **values)
        bom.full_clean()
        bom.save()
        if copy_from is not None:
            for line in copy_from.lines.all():
                BomLine.objects.create(company=membership.company, bom=bom, item=line.item, qty=line.qty,
                                       uom=line.uom, factor=line.factor, scrap_pct=line.scrap_pct,
                                       is_packaging=line.is_packaging, note=line.note)
        audit(actor, membership, "production.bom.created", bom,
              after={"item": item.code, "version": version, "copied_from": copy_from.pk if copy_from else None})
    return bom


@transaction.atomic
def new_version(*, actor, company_id, bom_id):
    _, bom = get_bom(actor=actor, company_id=company_id, pk=bom_id)
    with use_company(company_id):
        if Bom.objects.filter(item=bom.item, status=Bom.Status.DRAFT).exists():
            raise ValidationError(f"{bom.item.code} has a draft version already: finish that one.")
    return create_bom(actor=actor, company_id=company_id, copy_from=bom, values={
        "item": bom.item, "batch_size": bom.batch_size, "batch_uom": bom.batch_uom,
        "effective_from": None, "note": bom.note})


@transaction.atomic
def add_line(*, actor, company_id, bom_id, values):
    membership, bom = get_bom(actor=actor, company_id=company_id, pk=bom_id)
    _draft(bom)
    values = _writable(values, LINE_FIELDS)
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the material."})
    if item.pk == bom.item_id:
        raise ValidationError({"item": "A product is not its own material."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor)
    with use_company(company_id):
        if bom.lines.filter(item=item).exists():
            raise ValidationError({"item": f"{item.code} is on this recipe already."})
        line = BomLine(company=membership.company, bom=bom, factor=factor,
                       scrap_pct=values.pop("scrap_pct", None) or Decimal("0"), **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_line(*, actor, company_id, bom_id, line_id):
    _, bom = get_bom(actor=actor, company_id=company_id, pk=bom_id)
    _draft(bom)
    with use_company(company_id):
        if not bom.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this recipe.")


@transaction.atomic
def activate(*, actor, company_id, bom_id):
    membership, bom = get_bom(actor=actor, company_id=company_id, pk=bom_id)
    _draft(bom)
    with use_company(company_id):
        lines = list(bom.lines.select_related("item"))
        if not lines:
            raise ValidationError("Add the materials before activating the recipe.")
        inactive = [line.item.code for line in lines if line.item.status != ActiveStatus.ACTIVE]
        if inactive:
            raise ValidationError(f"Inactive materials: {', '.join(inactive)}.")
        previous = Bom.objects.select_for_update().filter(item=bom.item, status=Bom.Status.ACTIVE).first()
        if previous is not None:
            previous.status, previous.updated_by = Bom.Status.ARCHIVED, actor
            previous.save(update_fields=["status", "updated_by", "updated_at"])
        bom.status, bom.activated_at, bom.activated_by, bom.updated_by = Bom.Status.ACTIVE, now(), actor, actor
        bom.save()
        audit(actor, membership, "production.bom.activated", bom,
              after={"version": bom.version, "archived": previous.version if previous else None,
                     "lines": [{"item": l.item.code, "qty": plain(l.qty), "uom": l.uom_id} for l in lines]})
    return bom


@transaction.atomic
def delete_draft(*, actor, company_id, bom_id):
    membership, bom = get_bom(actor=actor, company_id=company_id, pk=bom_id)
    _draft(bom)
    with use_company(company_id):
        if bom.orders.exists():
            raise ValidationError("Production orders use this version.")
        audit(actor, membership, "production.bom.deleted", bom, before={"version": bom.version})
        bom.lines.all().delete()
        bom.delete()
