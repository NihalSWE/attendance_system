"""Store requisition -> store issue -> material return (Phase 4).

- **Store requisition** (no stock moves): a department asks a store. Draft ->
  Waiting for approval -> Approved (or Rejected) -> Partly issued -> Issued;
  Closed when the rest will not be given. Raised with "Store requisitions,
  issues and returns" (``inventory.issue``) in the department's branch;
  approved with "Approve store requisitions" (``inventory.issue_approve``).
- **Store issue**: goods out of a store to a department, against an approved
  requisition (filled first expiry first) or direct. A line without a batch
  is taken FEFO, split across batches when needed; choosing a later-expiring
  batch needs a reason. Expired, quarantined and blocked batches are refused,
  and so is any quarantine/rejected/in-transit store. Scanning a package label
  adds its contents; a fully issued package's label is marked used up.
- **Material return**: goods back from a department. A line from an issue
  comes back at that issue's cost; damaged goods go to the branch's Rejected
  store, not back to use.
"""

from decimal import Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F

from common.choices import ActiveStatus
from common.tenant import use_company
from inventory import fefo, posting
from inventory.doc_common import (
    _writable,
    audit,
    check_own_approval,
    check_qty,
    get,
    local_today,
    now,
    plain,
    require,
    unit_factor,
)
from inventory.models import (
    BarcodeLabel,
    MaterialReturn,
    MaterialReturnLine,
    MoveType,
    StockMove,
    StoreIssue,
    StoreIssueLine,
    StoreRequisition,
    StoreRequisitionLine,
    Warehouse,
)

FOUR = Decimal("0.0001")
RStatus = StoreRequisition.Status
DStatus = StoreIssue.Status


def _draft(doc):
    if doc.status != "draft":
        raise ValidationError(f"{doc.number} is {doc.get_status_display().lower()}; only a draft can change.")


def _number(company_id, model, day):
    return posting.next_number(company_id, model.DOC_TYPE, model.PREFIX, day.year)


def _in_unit(qty_base, uom, factor, item):
    """``qty_base`` written in ``uom`` when it is a whole number of it, else in the base unit."""
    in_unit = (qty_base / factor).quantize(FOUR)
    if in_unit * factor == qty_base:
        return in_unit, uom, factor
    return qty_base, item.base_uom, Decimal("1")


# --------------------------------------------------------------------------
# Store requisition
# --------------------------------------------------------------------------

REQ_FIELDS = ("department", "warehouse", "request_date", "needed_by", "purpose")
REQ_LINE_FIELDS = ("item", "qty", "uom", "note")


def get_requisition(*, actor, company_id, pk, code="inventory.issue"):
    row = get(StoreRequisition, company_id, pk, "Store requisition")
    return require(actor, company_id, code, row.department.branch_id), row


@transaction.atomic
def create_requisition(*, actor, company_id, values):
    values = _writable(values, REQ_FIELDS)
    department, warehouse = values.get("department"), values.get("warehouse")
    if department is None:
        raise ValidationError({"department": "Choose the department asking."})
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if values.get("request_date") is None:
        raise ValidationError({"request_date": "Give the date."})
    membership = require(actor, company_id, "inventory.issue", department.branch_id)
    with use_company(company_id):
        row = StoreRequisition(company=membership.company, created_by=actor, updated_by=actor,
                               number=_number(company_id, StoreRequisition, values["request_date"]),
                               **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "inventory.store_requisition.created", row,
              after={"number": row.number, "department": department.pk})
    return row


@transaction.atomic
def add_requisition_line(*, actor, company_id, requisition_id, values):
    membership, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(row)
    values = _writable(values, REQ_LINE_FIELDS)
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_qty(item, values.get("qty"), factor)
    with use_company(company_id):
        line = StoreRequisitionLine(company=membership.company, requisition=row, factor=factor, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_requisition_line(*, actor, company_id, requisition_id, line_id):
    _, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this requisition.")


@transaction.atomic
def submit_requisition(*, actor, company_id, requisition_id):
    membership, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.exists():
            raise ValidationError("Add what is needed before sending it for approval.")
        row.status, row.submitted_at, row.submitted_by, row.updated_by = RStatus.SUBMITTED, now(), actor, actor
        row.save()
        audit(actor, membership, "inventory.store_requisition.submitted", row,
              before={"status": "draft"}, after={"status": row.status})
    return row


@transaction.atomic
def decide_requisition(*, actor, company_id, requisition_id, approve, note=""):
    membership, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id,
                                      code="inventory.issue_approve")
    if row.status != RStatus.SUBMITTED:
        raise ValidationError(f"{row.number} is not waiting for approval.")
    check_own_approval(membership, actor, row.submitted_by_id)
    note = (note or "").strip()
    if not approve and not note:
        raise ValidationError({"note": "Say why it is rejected."})
    with use_company(company_id):
        row.status = RStatus.APPROVED if approve else RStatus.REJECTED
        row.decided_at, row.decided_by, row.decision_note, row.updated_by = now(), actor, note[:255], actor
        row.save()
        audit(actor, membership, f"inventory.store_requisition.{'approved' if approve else 'rejected'}",
              row, before={"status": "submitted"}, after={"status": row.status, "note": note})
    return row


@transaction.atomic
def cancel_requisition(*, actor, company_id, requisition_id):
    membership, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    if row.status not in (RStatus.DRAFT, RStatus.SUBMITTED, RStatus.APPROVED, RStatus.REJECTED):
        raise ValidationError(f"{row.number} is {row.get_status_display().lower()}: close it instead.")
    with use_company(company_id):
        before, row.status, row.updated_by = row.status, RStatus.CANCELLED, actor
        row.save()
        audit(actor, membership, "inventory.store_requisition.cancelled", row,
              before={"status": before}, after={"status": "cancelled"})
    return row


@transaction.atomic
def close_requisition(*, actor, company_id, requisition_id, note):
    membership, row = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    if row.status != RStatus.PARTLY_ISSUED:
        raise ValidationError("Only a partly issued requisition is closed.")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Say why the rest will not be given."})
    with use_company(company_id):
        row.status, row.decision_note, row.updated_by = RStatus.CLOSED, note[:255], actor
        row.save()
        audit(actor, membership, "inventory.store_requisition.closed", row,
              before={"status": "partly_issued"}, after={"status": "closed", "note": note})
    return row


def refresh_requisition(row):
    if row.status not in (RStatus.APPROVED, RStatus.PARTLY_ISSUED, RStatus.ISSUED):
        return
    lines = list(StoreRequisitionLine.all_objects.filter(requisition=row))
    if all(line.qty_open_base <= 0 for line in lines):
        status = RStatus.ISSUED
    elif any(line.qty_issued_base > 0 for line in lines):
        status = RStatus.PARTLY_ISSUED
    else:
        status = RStatus.APPROVED
    if status != row.status:
        row.status = status
        row.save(update_fields=["status", "updated_at"])


# --------------------------------------------------------------------------
# Store issue
# --------------------------------------------------------------------------

ISSUE_FIELDS = ("warehouse", "department", "requisition", "issue_date", "received_by", "note")
ISSUE_LINE_FIELDS = ("requisition_line", "item", "batch", "location", "qty", "uom", "not_fefo_reason", "note")


def get_issue(*, actor, company_id, pk, code="inventory.issue"):
    row = get(StoreIssue, company_id, pk, "Store issue")
    return require(actor, company_id, code, row.warehouse.branch_id), row


@transaction.atomic
def create_issue(*, actor, company_id, values, fill_from_requisition=True):
    values = _writable(values, ISSUE_FIELDS)
    requisition = values.get("requisition")
    if requisition is not None:
        requisition = values["requisition"] = get(StoreRequisition, company_id, requisition.pk,
                                                  "Store requisition", lock=True)
        if requisition.status not in (RStatus.APPROVED, RStatus.PARTLY_ISSUED):
            raise ValidationError({"requisition": f"{requisition.number} is "
                                                  f"{requisition.get_status_display().lower()}: "
                                                  "only an approved requisition is issued."})
        values["department"], values["warehouse"] = requisition.department, requisition.warehouse
    warehouse, department = values.get("warehouse"), values.get("department")
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if department is None:
        raise ValidationError({"department": "Choose the department it goes to."})
    if values.get("issue_date") is None:
        raise ValidationError({"issue_date": "Give the date."})
    membership = require(actor, company_id, "inventory.issue", warehouse.branch_id)
    short = []
    with use_company(company_id):
        row = StoreIssue(company=membership.company, created_by=actor, updated_by=actor,
                         number=_number(company_id, StoreIssue, values["issue_date"]), **values)
        row.full_clean()
        row.save()
        if requisition is not None and fill_from_requisition:
            for req_line in requisition.lines.select_related("item__base_uom", "uom"):
                want = req_line.qty_open_base
                if want <= 0:
                    continue
                rows = fefo.usable_rows(company_id, req_line.item, warehouse, row.issue_date)
                for balance in rows:
                    if want <= 0:
                        break
                    take = min(balance.available, want)
                    qty, uom, factor = _in_unit(take, req_line.uom, req_line.factor, req_line.item)
                    StoreIssueLine.objects.create(
                        company=membership.company, issue=row, requisition_line=req_line,
                        item=req_line.item, batch=balance.batch, location=balance.location,
                        qty=qty, uom=uom, factor=factor)
                    want -= take
                if want > 0:
                    short.append(f"{req_line.item.code} short by {want.normalize():f} "
                                 f"{req_line.item.base_uom.code}")
        audit(actor, membership, "inventory.store_issue.created", row,
              after={"number": row.number, "requisition": row.requisition_id})
    row.shortages = short
    return row


def _issued_elsewhere(line_values, issue):
    """Base units of this requisition line already on other draft lines of this issue."""
    return sum((l.qty_base for l in StoreIssueLine.all_objects.filter(
        issue=issue, requisition_line=line_values["requisition_line"])), Decimal("0"))


@transaction.atomic
def add_issue_line(*, actor, company_id, issue_id, values, label=None):
    """Add a line; without a batch (for an item that keeps batches) the
    quantity is taken first-expiry-first and may become several lines."""
    membership, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    _draft(row)
    values = _writable(values, ISSUE_LINE_FIELDS)
    req_line = values.get("requisition_line")
    with use_company(company_id):
        if req_line is not None:
            if req_line.requisition_id != row.requisition_id:
                raise ValidationError({"requisition_line": "That line is on another requisition."})
            values["item"] = req_line.item
        item, batch, location = values.get("item"), values.get("batch"), values.get("location")
        if item is None:
            raise ValidationError({"item": "Choose the item."})
        factor = unit_factor(item, values.get("uom"))
        check_qty(item, values.get("qty"), factor)
        qty_base = values["qty"] * factor
        if location is not None and location.warehouse_id != row.warehouse_id:
            raise ValidationError({"location": f"Bin {location.code} is not in {row.warehouse.code}."})
        if batch is not None and batch.item_id != item.pk:
            raise ValidationError({"batch": "That batch is of another item."})
        if not item.track_batch and batch is not None:
            raise ValidationError({"batch": f"{item.code} does not keep batches."})
        if req_line is not None:
            open_base = req_line.qty_open_base - _issued_elsewhere(values, row)
            if qty_base > open_base:
                raise ValidationError({"qty": f"More than asked: {open_base.normalize():f} "
                                              f"{item.base_uom.code} is still open on the requisition."})
        if label is not None and StoreIssueLine.objects.filter(issue=row, label=label).exists():
            raise ValidationError(f"Label {label.code} is on this issue already.")
        made = []
        if item.track_batch and batch is None:
            for balance, take in fefo.allocate(company_id, item, row.warehouse, qty_base, row.issue_date,
                                               location):
                qty, uom, line_factor = _in_unit(take, values["uom"], factor, item)
                made.append(StoreIssueLine.objects.create(
                    company=membership.company, issue=row, requisition_line=req_line, item=item,
                    batch=balance.batch, location=balance.location, qty=qty, uom=uom,
                    factor=line_factor, note=values.get("note", "")))
            return made
        if batch is not None:
            if not batch.usable_on(row.issue_date):
                reason = "expired" if batch.expiry_date and batch.expiry_date < row.issue_date \
                    else batch.get_status_display().lower()
                raise ValidationError({"batch": f"Batch {batch.batch_no} is {reason} and cannot be issued."})
            if fefo.expiring_first(company_id, item, row.warehouse, row.issue_date, batch) \
                    and not (values.get("not_fefo_reason") or "").strip():
                first = fefo.first_batch(company_id, item, row.warehouse, row.issue_date)
                raise ValidationError({"not_fefo_reason": (
                    f"Batch {first.batch_no} expires first ({first.expiry_date:%d %b %Y}); "
                    "say why this one is taken instead.")})
        line = StoreIssueLine(company=membership.company, issue=row, factor=factor, label=label, **values)
        line.full_clean()
        line.save()
        return [line]


@transaction.atomic
def remove_issue_line(*, actor, company_id, issue_id, line_id):
    _, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this issue.")


def label_from_scan(company_id, code, warehouse):
    """``(values, label)`` for a scanned package label or item barcode, to add a line."""
    from inventory.lookup import lookup
    from inventory.models import Batch, Item, Uom

    found = lookup(company_id, code)
    if not found.get("found"):
        raise ValidationError(found.get("message") or "Not found.")
    if found["kind"] == "location":
        raise ValidationError("That is a bin label: scan the goods.")
    if not found.get("item"):
        raise ValidationError("That label is not for goods.")
    item = Item.all_objects.select_related("base_uom").get(pk=found["item"]["id"])
    values = {"item": item, "batch": None, "location": None, "qty": Decimal("1"), "uom": item.base_uom,
              "note": ""}
    label = None
    if found.get("label") and found["kind"] == "package":
        label = BarcodeLabel.all_objects.select_related("uom", "batch", "location").get(pk=found["label"]["id"])
        if label.status != BarcodeLabel.Status.ACTIVE:
            raise ValidationError(f"Label {label.code} is {label.get_status_display().lower()}.")
        values.update(batch=label.batch, qty=label.quantity, uom=label.uom)
        if label.location_id and label.location.warehouse_id == warehouse.pk:
            values["location"] = label.location
    elif found["kind"] == "pack":
        values["uom"] = Uom.all_objects.get(company_id=company_id, code=found["uom"])
    elif found.get("batch"):
        values["batch"] = Batch.all_objects.get(pk=found["batch"]["id"])
        if found.get("quantity"):
            values.update(qty=Decimal(found["quantity_base"]), uom=item.base_uom)
    return values, label


@transaction.atomic
def scan_into_issue(*, actor, company_id, issue_id, code):
    _, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    values, label = label_from_scan(company_id, code, row.warehouse)
    if row.requisition_id:
        with use_company(company_id):
            req_line = row.requisition.lines.filter(item=values["item"]).first()
        if req_line is None:
            raise ValidationError(f"{values['item'].code} is not on {row.requisition.number}.")
        values["requisition_line"] = req_line
    # A scanned package or batch card is the batch in hand: no FEFO question.
    values["not_fefo_reason"] = "Taken by scanning" if values.get("batch") else ""
    return add_issue_line(actor=actor, company_id=company_id, issue_id=issue_id, values=values, label=label)


@transaction.atomic
def post_issue(*, actor, company_id, issue_id):
    membership, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    with use_company(company_id):
        row = StoreIssue.objects.select_for_update(of=("self",)).select_related("warehouse").get(pk=row.pk)
        _draft(row)
        lines = list(row.lines.select_related("item__base_uom", "batch", "location", "label",
                                              "requisition_line").order_by("pk"))
        if not lines:
            raise ValidationError("Add what is given out before posting.")
        for line in lines:
            if line.requisition_line_id:
                req_line = StoreRequisitionLine.objects.select_for_update().get(pk=line.requisition_line_id)
                if line.qty_base > req_line.qty_open_base:
                    raise ValidationError(f"{line.item.code}: more than the requisition still has open.")
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=StoreIssue.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.ISSUE, work_date=row.issue_date,
            lines=[posting.Line(item=line.item, warehouse=row.warehouse, location=line.location,
                                batch=line.batch, qty=-line.qty_base, line_id=line.pk,
                                note=f"{row.number} to {row.department.name}"[:255]) for line in lines])
        for line in lines:
            if line.requisition_line_id:
                StoreRequisitionLine.objects.filter(pk=line.requisition_line_id).update(
                    qty_issued_base=F("qty_issued_base") + line.qty_base)
            if line.label_id and line.qty_base >= line.label.quantity * unit_factor(line.item, line.label.uom):
                BarcodeLabel.objects.filter(pk=line.label_id).update(status=BarcodeLabel.Status.CONSUMED)
        if row.requisition_id:
            refresh_requisition(row.requisition)
        row.status, row.posted_at, row.posted_by, row.updated_by = DStatus.POSTED, moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "inventory.store_issue.posted", row, before={"status": "draft"},
              after={"status": "posted", "lines": len(lines),
                     "value": plain(-sum((m.value for m in moves), Decimal("0")))})
    return row


@transaction.atomic
def reverse_issue(*, actor, company_id, issue_id, reason):
    membership, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = StoreIssue.objects.select_for_update().get(pk=row.pk)
        if row.status != DStatus.POSTED:
            raise ValidationError("Only a posted issue is reversed.")
        if row.returns.filter(status="posted").exists():
            raise ValidationError("Goods of this issue came back on a material return; reverse that first.")
        posting.reverse(company_id=company_id, actor=actor, document_type=StoreIssue.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        for line in row.lines.all():
            if line.requisition_line_id:
                StoreRequisitionLine.objects.filter(pk=line.requisition_line_id).update(
                    qty_issued_base=F("qty_issued_base") - line.qty_base)
            if line.label_id:
                BarcodeLabel.objects.filter(pk=line.label_id, status=BarcodeLabel.Status.CONSUMED).update(
                    status=BarcodeLabel.Status.ACTIVE)
        if row.requisition_id:
            refresh_requisition(row.requisition)
        row.status, row.reversed_at, row.reversed_by = DStatus.REVERSED, now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "inventory.store_issue.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_issue(*, actor, company_id, issue_id):
    membership, row = get_issue(actor=actor, company_id=company_id, pk=issue_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = DStatus.CANCELLED, actor
        row.save()
        audit(actor, membership, "inventory.store_issue.cancelled", row,
              before={"status": "draft"}, after={"status": "cancelled"})
    return row


# --------------------------------------------------------------------------
# Material return
# --------------------------------------------------------------------------

MR_FIELDS = ("department", "warehouse", "issue", "return_date", "note")
MR_LINE_FIELDS = ("issue_line", "item", "batch", "condition", "location", "qty", "uom", "note")


def get_return(*, actor, company_id, pk, code="inventory.issue"):
    row = get(MaterialReturn, company_id, pk, "Material return")
    return require(actor, company_id, code, row.warehouse.branch_id), row


def rejected_store(company_id, branch_id):
    store = Warehouse.all_objects.filter(company_id=company_id, branch_id=branch_id,
                                         kind=Warehouse.Kind.REJECTED, status=ActiveStatus.ACTIVE).first()
    if store is None:
        raise ValidationError({"condition": "Damaged goods go to a Rejected store; set one up in this "
                                            "branch first (Inventory → Stores)."})
    return store


@transaction.atomic
def create_return(*, actor, company_id, values):
    values = _writable(values, MR_FIELDS)
    issue = values.get("issue")
    if issue is not None:
        issue = values["issue"] = get(StoreIssue, company_id, issue.pk, "Store issue")
        if issue.status != DStatus.POSTED:
            raise ValidationError({"issue": f"{issue.number} is not posted."})
        values["department"] = issue.department
        values.setdefault("warehouse", issue.warehouse)
        values["warehouse"] = values["warehouse"] or issue.warehouse
    warehouse = values.get("warehouse")
    if values.get("department") is None:
        raise ValidationError({"department": "Choose the department returning."})
    if warehouse is None or warehouse.status != ActiveStatus.ACTIVE:
        raise ValidationError({"warehouse": "Choose an active store."})
    if values.get("return_date") is None:
        raise ValidationError({"return_date": "Give the date."})
    membership = require(actor, company_id, "inventory.issue", warehouse.branch_id)
    with use_company(company_id):
        row = MaterialReturn(company=membership.company, created_by=actor, updated_by=actor,
                             number=_number(company_id, MaterialReturn, values["return_date"]), **values)
        row.full_clean()
        row.save()
        audit(actor, membership, "inventory.material_return.created", row,
              after={"number": row.number, "issue": row.issue_id})
    return row


def _returned_base(issue_line):
    return sum((l.qty_base for l in MaterialReturnLine.all_objects.filter(
        issue_line=issue_line, material_return__status__in=["draft", "posted"])), Decimal("0"))


@transaction.atomic
def add_return_line(*, actor, company_id, return_id, values):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    values = _writable(values, MR_LINE_FIELDS)
    issue_line = values.get("issue_line")
    with use_company(company_id):
        cost = None
        if issue_line is not None:
            # As it is now, not as the caller's copy remembers it.
            issue_line = values["issue_line"] = StoreIssueLine.objects.select_related(
                "issue", "item", "batch").filter(pk=issue_line.pk).first()
            if issue_line is None:
                raise ValidationError({"issue_line": "That line is not in this company."})
            if issue_line.issue.status != DStatus.POSTED or issue_line.issue.department_id != row.department_id:
                raise ValidationError({"issue_line": "Pick a line issued to this department."})
            values.update(item=issue_line.item, batch=issue_line.batch)
            move = StockMove.all_objects.filter(document_type=StoreIssue.DOC_TYPE,
                                                document_id=issue_line.issue_id,
                                                document_line_id=issue_line.pk,
                                                reversed_move__isnull=True).first()
            cost = move.unit_cost if move else None
        item = values.get("item")
        if item is None:
            raise ValidationError({"item": "Choose the issued line or the item."})
        factor = unit_factor(item, values.get("uom"))
        check_qty(item, values.get("qty"), factor, active=False)
        if item.track_batch and values.get("batch") is None:
            raise ValidationError({"batch": f"{item.code} keeps batches: say which batch comes back."})
        if issue_line is not None:
            already = _returned_base(issue_line)
            if already + values["qty"] * factor > issue_line.qty_base:
                raise ValidationError({"qty": f"More than issued: {issue_line.qty_base - already:f} "
                                              f"{item.base_uom.code} of that line can come back."})
        condition = values.get("condition") or MaterialReturnLine.Condition.GOOD
        store = row.warehouse if condition == MaterialReturnLine.Condition.GOOD else rejected_store(
            company_id, row.warehouse.branch_id)
        location = values.get("location")
        if location is not None and location.warehouse_id != store.pk:
            location = None
        values.update(condition=condition, location=location)
        line = MaterialReturnLine(company=membership.company, material_return=row, factor=factor,
                                  warehouse=store, unit_cost_base=cost, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_return_line(*, actor, company_id, return_id, line_id):
    _, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        if not row.lines.filter(pk=line_id).delete()[0]:
            raise ValidationError("That line is not on this return.")


@transaction.atomic
def post_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    with use_company(company_id):
        row = MaterialReturn.objects.select_for_update(of=("self",)).get(pk=row.pk)
        _draft(row)
        lines = list(row.lines.select_related("item__base_uom", "batch", "warehouse", "location").order_by("pk"))
        if not lines:
            raise ValidationError("Add what comes back before posting.")
        for branch_id in {line.warehouse.branch_id for line in lines}:
            require(actor, company_id, "inventory.issue", branch_id)
        moves = posting.post(
            company_id=company_id, actor=actor, document_type=MaterialReturn.DOC_TYPE, document_id=row.pk,
            move_type=MoveType.ISSUE_RETURN, work_date=row.return_date,
            lines=[posting.Line(item=line.item, warehouse=line.warehouse, location=line.location,
                                batch=line.batch, qty=line.qty_base, unit_cost=line.unit_cost_base,
                                line_id=line.pk, note=f"{row.number} from {row.department.name}"
                                                      f" ({line.get_condition_display()})"[:255])
                   for line in lines])
        row.status, row.posted_at, row.posted_by, row.updated_by = "posted", moves[0].posted_at, actor, actor
        row.save()
        audit(actor, membership, "inventory.material_return.posted", row, before={"status": "draft"},
              after={"status": "posted", "lines": len(lines)})
    return row


@transaction.atomic
def reverse_return(*, actor, company_id, return_id, reason):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    reason = (reason or "").strip()
    if not reason:
        raise ValidationError({"reason": "Say why it is being reversed."})
    with use_company(company_id):
        row = MaterialReturn.objects.select_for_update().get(pk=row.pk)
        if row.status != "posted":
            raise ValidationError("Only a posted return is reversed.")
        posting.reverse(company_id=company_id, actor=actor, document_type=MaterialReturn.DOC_TYPE,
                        document_id=row.pk, work_date=local_today(membership.company),
                        note=f"Reversal of {row.number}: {reason}")
        row.status, row.reversed_at, row.reversed_by = "reversed", now(), actor
        row.reverse_reason, row.updated_by = reason[:255], actor
        row.save()
        audit(actor, membership, "inventory.material_return.reversed", row, before={"status": "posted"},
              after={"status": "reversed", "reason": reason})
    return row


@transaction.atomic
def cancel_return(*, actor, company_id, return_id):
    membership, row = get_return(actor=actor, company_id=company_id, pk=return_id)
    _draft(row)
    with use_company(company_id):
        row.status, row.updated_by = "cancelled", actor
        row.save()
        audit(actor, membership, "inventory.material_return.cancelled", row,
              before={"status": "draft"}, after={"status": "cancelled"})
    return row
