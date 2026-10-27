"""A finished good's recipe as one thing, on its own page (2026-10-26).

The client could not find where a finished good's materials go: they were a
"BOM" on a separate page, with a batch unit, a packaging tick, an effective
date, a note and Draft -> Activate. Here the finished good's page keeps it
simple, on top of ``production.boms`` (which still keeps the versions):

- "One batch makes N <the product's unit>": the batch unit is the product's.
- Add material: a raw material or packaging item and how much one batch
  uses, in the material's own unit; packaging is the material's own type.
- The recipe is in use as soon as it has a material. While no production
  order has used it, changes apply in place; after that a change is saved as a
  new version, so past production keeps the recipe it was made with.
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from common.tenant import use_company
from inventory.doc_common import check_qty
from inventory.models import ItemKind
from production import boms
from production.models import Bom, ProductionOrder

MATERIAL_KINDS = (ItemKind.RAW, ItemKind.PACKAGING)


def current(company_id, item):
    """The version in use, else the one being written; None when there is none."""
    with use_company(company_id):
        versions = Bom.objects.filter(item=item).select_related("batch_uom")
        return (versions.filter(status=Bom.Status.ACTIVE).first()
                or versions.filter(status=Bom.Status.DRAFT).order_by("-version").first())


def page(company_id, item):
    """What the finished good's Materials card shows."""
    bom = current(company_id, item)
    with use_company(company_id):
        lines = (list(bom.lines.select_related("item__base_uom", "uom").order_by("is_packaging", "pk"))
                 if bom else [])
        versions = list(Bom.objects.filter(item=item).exclude(status=Bom.Status.DRAFT)
                        .order_by("-version").values("version", "status", "activated_at"))
        used = bool(bom) and ProductionOrder.objects.filter(bom=bom).exists()
    return {"bom": bom, "lines": lines, "versions": versions, "used": used}


def _editable(*, actor, company_id, item):
    """The version a change goes into: the one being written, the one in use
    while production has not used it, or else a new version copied from it."""
    bom = current(company_id, item)
    if bom is None:
        raise ValidationError("Say first how much one batch makes.")
    if bom.status == Bom.Status.ACTIVE and ProductionOrder.all_objects.filter(bom=bom).exists():
        bom = boms.new_version(actor=actor, company_id=company_id, bom_id=bom.pk)
    return bom


def _in_use(*, actor, company_id, bom):
    """A version with materials that is not in use yet is put in use."""
    if bom.status == Bom.Status.DRAFT and bom.lines.exists():
        boms.activate(actor=actor, company_id=company_id, bom_id=bom.pk)


@transaction.atomic
def set_batch(*, actor, company_id, item, batch_size):
    """How much one batch makes, in the product's own unit."""
    if item.kind != ItemKind.FINISHED:
        raise ValidationError("Only a finished good has a recipe.")
    if item.sourcing == item.Sourcing.BUY:
        raise ValidationError(f"{item.code} is bought ready: set How you get it to Make or Both first.")
    bom = current(company_id, item)
    if bom is None:
        return boms.create_bom(actor=actor, company_id=company_id, values={
            "item": item, "batch_size": batch_size, "batch_uom": item.base_uom})
    bom = _editable(actor=actor, company_id=company_id, item=item)
    check_qty(item, batch_size, bom.batch_factor, field="batch_size")
    with use_company(company_id):
        bom.batch_size, bom.updated_by = batch_size, actor
        bom.save(update_fields=["batch_size", "updated_by", "updated_at"])
        _in_use(actor=actor, company_id=company_id, bom=bom)
    return bom


@transaction.atomic
def add_material(*, actor, company_id, item, material, qty, scrap_pct=None):
    """A material and how much one batch uses, in the material's own unit."""
    if material.kind not in MATERIAL_KINDS:
        raise ValidationError({"material": "Choose a raw material or a packaging item."})
    bom = _editable(actor=actor, company_id=company_id, item=item)
    boms.add_line(actor=actor, company_id=company_id, bom_id=bom.pk, values={
        "item": material, "qty": qty, "uom": material.base_uom, "scrap_pct": scrap_pct,
        "is_packaging": material.kind == ItemKind.PACKAGING, "note": ""})
    with use_company(company_id):
        _in_use(actor=actor, company_id=company_id, bom=bom)
    return bom


@transaction.atomic
def remove_material(*, actor, company_id, item, material_id):
    """Take a material off the recipe; a recipe in use keeps at least one."""
    bom = current(company_id, item)
    if bom is None:
        raise ValidationError("There is no recipe yet.")
    with use_company(company_id):
        if bom.status == Bom.Status.ACTIVE and bom.lines.count() <= 1:
            raise ValidationError("A recipe in use needs at least one material: add the new one "
                                  "first, then remove this one.")
    bom = _editable(actor=actor, company_id=company_id, item=item)
    with use_company(company_id):
        line = bom.lines.filter(item_id=material_id).first()
    if line is None:
        raise ValidationError("That material is not on the recipe.")
    boms.remove_line(actor=actor, company_id=company_id, bom_id=bom.pk, line_id=line.pk)
    with use_company(company_id):
        _in_use(actor=actor, company_id=company_id, bom=bom)
    return bom
