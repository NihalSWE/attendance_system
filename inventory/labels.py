"""Labels (plan §3 D3, Phase 2): made, printed, reprinted and voided here.

Who: "Print and reprint labels" (``inventory.labels``) - anywhere for item
and batch labels, in the store's branch for bin labels and for package
labels placed in a store.

- **Making** a label gives it its code and nothing else; it can be printed
  at once (``print_labels``) or later from the label register.
- **Printing** writes a ``LabelPrint`` row and counts the copies. Printing a
  label that was printed before is a **reprint** and needs a reason (a torn
  label, a lost one), so the register shows why there are two of the same.
- **Voiding** a label (stuck on the wrong bag, replaced) stops it resolving:
  a scan of it is refused with the reason.
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import access
from inventory.masters import units_for
from inventory.models import (
    Batch,
    BarcodeLabel,
    DocumentSequence,
    Item,
    LabelPrint,
    Location,
    Uom,
    Warehouse,
)
from tenants.models import Company

CODE = "inventory.labels"
PREFIX = {BarcodeLabel.Kind.PACKAGE: "P", BarcodeLabel.Kind.BATCH: "B",
          BarcodeLabel.Kind.LOCATION: "L"}
#: At most this many labels made or printed in one go (one PDF).
MAX_LABELS = 500
MAX_COPIES = 20


def next_codes(company, kind, count):
    """``count`` new codes for ``kind``, e.g. ``P7-000001`` for company 7. The
    company's number (its id: short, never changes) keeps codes unique across
    companies and short enough for a 40 mm label. Call inside a transaction:
    the sequence row stays locked until it commits."""
    filters = {"company_id": company.pk, "doc_type": f"label_{kind}", "year": 0}
    row = DocumentSequence.all_objects.select_for_update().filter(**filters).first()
    if row is None:
        try:
            with transaction.atomic():
                DocumentSequence.all_objects.create(prefix=PREFIX[kind], **filters)
        except IntegrityError:
            pass
        row = DocumentSequence.all_objects.select_for_update().get(**filters)
    first = row.next_number
    row.next_number += count
    row.save(update_fields=["next_number"])
    return [f"{row.prefix}{company.pk}-{number:06d}" for number in range(first, first + count)]


def _membership(actor, company_id, branch_id=None, code=CODE):
    return access.require(actor, company_id, code, branch_id)


def _audit(actor, membership, action, label, **after):
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action=action, obj=label, after={"code": label.code, **after})


def _get(model, company_id, pk, what):
    row = model.all_objects.filter(company_id=company_id, pk=pk).first() if pk else None
    if row is None:
        raise PermissionDenied(f"{what} not found in this company.")
    return row


@transaction.atomic
def item_label(*, actor, company_id, item_id):
    """The item's label: its own barcode if it has one, else its code. One per item."""
    membership = _membership(actor, company_id)
    item = _get(Item, company_id, item_id, "Item")
    with use_company(company_id):
        label = BarcodeLabel.objects.filter(kind=BarcodeLabel.Kind.ITEM, item=item,
                                            status=BarcodeLabel.Status.ACTIVE).first()
        if label is not None:
            return label
        code = item.barcode or item.code
        clash = BarcodeLabel.objects.filter(code=code).first()
        if clash is not None:
            raise ValidationError(f"The code {code} is already used by label {clash.code} "
                                  f"({clash.get_kind_display().lower()}).")
        label = BarcodeLabel(company=membership.company, kind=BarcodeLabel.Kind.ITEM, item=item,
                             code=code, created_by=actor, updated_by=actor)
        label.full_clean()
        label.save()
        _audit(actor, membership, "inventory.label.created", label, kind="item", item=item.code)
    return label


@transaction.atomic
def batch_label(*, actor, company_id, batch_id):
    """The batch's card (no quantity). One active per batch."""
    membership = _membership(actor, company_id)
    batch = _get(Batch, company_id, batch_id, "Batch")
    with use_company(company_id):
        label = BarcodeLabel.objects.filter(kind=BarcodeLabel.Kind.BATCH, batch=batch,
                                            status=BarcodeLabel.Status.ACTIVE).first()
        if label is not None:
            return label
        [code] = next_codes(membership.company, BarcodeLabel.Kind.BATCH, 1)
        label = BarcodeLabel(company=membership.company, kind=BarcodeLabel.Kind.BATCH,
                             item_id=batch.item_id, batch=batch, code=code,
                             created_by=actor, updated_by=actor)
        label.full_clean()
        label.save()
        _audit(actor, membership, "inventory.label.created", label, kind="batch",
               batch=batch.batch_no)
    return label


@transaction.atomic
def location_labels(*, actor, company_id, location_ids):
    """Each bin's label, made if it has none. Needs the permission in each store's branch."""
    access.require_module(actor, company_id)
    with use_company(company_id):
        locations = list(Location.objects.filter(pk__in=location_ids).select_related("warehouse")
                         .order_by("warehouse__code", "code"))
        if not locations or len(locations) != len(set(location_ids)):
            raise PermissionDenied("Bin not found in this company.")
        if len(locations) > MAX_LABELS:
            raise ValidationError(f"At most {MAX_LABELS} labels at a time.")
        membership = None
        for branch_id in {loc.warehouse.branch_id for loc in locations}:
            membership = _membership(actor, company_id, branch_id)
        existing = {label.location_id: label for label in BarcodeLabel.objects.filter(
            kind=BarcodeLabel.Kind.LOCATION, location__in=locations,
            status=BarcodeLabel.Status.ACTIVE)}
        new = [loc for loc in locations if loc.pk not in existing]
        codes = iter(next_codes(membership.company, BarcodeLabel.Kind.LOCATION, len(new))) if new else None
        labels = []
        for location in locations:
            label = existing.get(location.pk)
            if label is None:
                label = BarcodeLabel(company=membership.company, kind=BarcodeLabel.Kind.LOCATION,
                                     warehouse=location.warehouse, location=location,
                                     code=next(codes), created_by=actor, updated_by=actor)
                label.full_clean()
                label.save()
                _audit(actor, membership, "inventory.label.created", label, kind="location",
                       bin=f"{location.warehouse.code}/{location.code}")
            labels.append(label)
    return labels


@transaction.atomic
def package_labels(*, actor, company_id, item_id, batch_id=None, uom_id=None, quantity=None,
                   count=1, warehouse_id=None, location_id=None, source_type="", source_id=None,
                   permission=CODE):
    """``count`` package labels of ``quantity`` ``uom`` each (25 bags of 50 KG).

    The unit is the item's base unit or one of its packs; the quantity is
    what one package holds.
    """
    item = _get(Item, company_id, item_id, "Item")
    warehouse = _get(Warehouse, company_id, warehouse_id, "Store") if warehouse_id else None
    membership = _membership(actor, company_id, warehouse.branch_id if warehouse else None, permission)
    batch = _get(Batch, company_id, batch_id, "Batch") if batch_id else None
    location = _get(Location, company_id, location_id, "Bin") if location_id else None
    uom = _get(Uom, company_id, uom_id or item.base_uom_id, "Unit")
    errors = {}
    try:
        count = int(count)
    except (TypeError, ValueError):
        count = 0
    if not 1 <= count <= MAX_LABELS:
        errors["count"] = f"Between 1 and {MAX_LABELS} packages."
    if quantity is None or Decimal(quantity) <= 0:
        errors["quantity"] = "Give what one package holds."
    if uom.pk not in units_for(item):
        errors["uom"] = f"{item.code} is counted in {item.base_uom.code} or its pack units."
    if item.status != ActiveStatus.ACTIVE:
        errors["item"] = f"{item.code} is inactive."
    if location is not None and warehouse is None:
        warehouse = location.warehouse
    if errors:
        raise ValidationError(errors)
    with use_company(company_id):
        codes = next_codes(membership.company, BarcodeLabel.Kind.PACKAGE, count)
        labels = []
        for code in codes:
            label = BarcodeLabel(company=membership.company, kind=BarcodeLabel.Kind.PACKAGE,
                                 code=code, item=item, batch=batch, uom=uom,
                                 quantity=Decimal(quantity), warehouse=warehouse, location=location,
                                 source_type=source_type, source_id=source_id,
                                 created_by=actor, updated_by=actor)
            label.full_clean()
            label.save()
            labels.append(label)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="inventory.label.packages_created", obj=item,
            after={"codes": [codes[0], codes[-1]], "count": count, "batch": batch.batch_no if batch else "",
                   "quantity": format(Decimal(quantity).normalize(), "f"), "uom": uom.code},
        )
    return labels


@transaction.atomic
def void_label(*, actor, company_id, label_id, reason):
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why the label is void."})
    with use_company(company_id):
        label = BarcodeLabel.objects.select_for_update(of=("self",)).select_related("warehouse").filter(
            pk=label_id).first()
    if label is None:
        raise PermissionDenied("Label not found in this company.")
    membership = _membership(actor, company_id, label.warehouse.branch_id if label.warehouse else None)
    if label.status == BarcodeLabel.Status.VOID:
        raise ValidationError("This label is void already.")
    label.status = BarcodeLabel.Status.VOID
    label.void_reason = reason[:255]
    label.updated_by = actor
    label.save(update_fields=["status", "void_reason", "updated_by", "updated_at"])
    _audit(actor, membership, "inventory.label.voided", label, reason=reason)
    return label


def settings_sizes(company_id):
    from inventory.posting import settings_for

    current = settings_for(company_id)
    return current.small_label, current.large_label, current.gs1_labels


@transaction.atomic
def print_labels(*, actor, company_id, label_ids, copies=1, reason="", permission=CODE):
    """The PDF for these labels, ``copies`` of each, and the print recorded.

    A label printed before is a reprint and needs ``reason``. A void label
    is never printed.
    """
    from inventory import label_pdf

    try:
        copies = int(copies)
    except (TypeError, ValueError):
        copies = 0
    if not 1 <= copies <= MAX_COPIES:
        raise ValidationError({"copies": f"Between 1 and {MAX_COPIES} copies."})
    reason = (reason or "").strip()
    access.require_module(actor, company_id)
    with use_company(company_id):
        labels = list(BarcodeLabel.objects.select_for_update(of=("self",)).filter(pk__in=label_ids)
                      .select_related("item__base_uom", "batch", "uom", "warehouse", "location")
                      .order_by("pk"))
    if not labels or len(labels) != len(set(label_ids)):
        raise PermissionDenied("Label not found in this company.")
    if len(labels) * copies > MAX_LABELS:
        raise ValidationError(f"At most {MAX_LABELS} labels in one PDF.")
    membership = None
    for branch_id in {label.warehouse.branch_id if label.warehouse else None for label in labels}:
        membership = _membership(actor, company_id, branch_id, permission)
    void = [label.code for label in labels if label.status == BarcodeLabel.Status.VOID]
    if void:
        raise ValidationError(f"Void labels are not printed: {', '.join(void)}.")
    reprints = [label for label in labels if label.printed_count]
    if reprints and not reason:
        raise ValidationError({"reason": "These were printed before: say why they are printed again."})
    small, large, gs1 = settings_sizes(company_id)
    company = Company.objects.get(pk=company_id)
    pdf = label_pdf.render(labels, copies=copies, small=small, large=large, gs1=gs1,
                           company_name=company.name)
    now = timezone.now()
    for label in labels:
        size = small if label.kind in (BarcodeLabel.Kind.ITEM, BarcodeLabel.Kind.LOCATION) else large
        LabelPrint.all_objects.create(company_id=company_id, label=label, printed_at=now,
                                      printed_by=actor, copies=copies,
                                      reprint=bool(label.printed_count),
                                      reason=reason[:255] if label.printed_count else "", size=size)
        label.printed_count += copies
        label.last_printed_at = now
        label.last_printed_by = actor
        label.save(update_fields=["printed_count", "last_printed_at", "last_printed_by", "updated_at"])
    if reprints:
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="inventory.label.reprinted", obj=reprints[0],
                             after={"codes": [label.code for label in reprints][:50],
                                    "copies": copies, "reason": reason})
    return pdf
