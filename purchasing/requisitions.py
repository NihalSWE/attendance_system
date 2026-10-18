"""Purchase requisitions: a department asks to buy; an approver says yes or no.

Draft -> Waiting for approval -> Approved (or Rejected, with a reason) ->
Ordered once purchase orders cover every line. Cancelled while nothing is
ordered. Raising and editing need "Raise purchase requisitions"
(``purchase.request``) in the requisition's branch; deciding needs
``purchase.approve`` there (see ``purchasing.common`` for own approvals).
"""

from django.core.exceptions import ValidationError
from django.db import transaction

from common.tenant import use_company
from inventory import posting
from purchasing.common import (
    _writable,
    audit,
    check_item,
    check_own_approval,
    get,
    now,
    plain,
    require,
    unit_factor,
)
from purchasing.models import PurchaseRequisition, PurchaseRequisitionLine

Status = PurchaseRequisition.Status
HEADER_FIELDS = ("branch", "department", "request_date", "needed_by", "reason")
LINE_FIELDS = ("item", "qty", "uom", "note")


def get_requisition(*, actor, company_id, pk, code="purchase.request"):
    requisition = get(PurchaseRequisition, company_id, pk, "Requisition")
    membership = require(actor, company_id, code, requisition.branch_id)
    return membership, requisition


def _draft(requisition):
    if requisition.status != Status.DRAFT:
        raise ValidationError(f"{requisition.number} is {requisition.get_status_display().lower()}; "
                              "only a draft can change.")


def _clean_header(values):
    branch, department = values.get("branch"), values.get("department")
    if branch is None:
        raise ValidationError({"branch": "Choose the branch."})
    if department is not None and department.branch_id != branch.pk:
        raise ValidationError({"department": "This department is in another branch."})
    if values.get("request_date") is None:
        raise ValidationError({"request_date": "Give the date."})
    if values.get("needed_by") and values["needed_by"] < values["request_date"]:
        raise ValidationError({"needed_by": "Needed before it was asked for?"})


@transaction.atomic
def create_requisition(*, actor, company_id, values):
    values = _writable(values, HEADER_FIELDS)
    _clean_header(values)
    membership = require(actor, company_id, "purchase.request", values["branch"].pk)
    with use_company(company_id):
        requisition = PurchaseRequisition(
            company=membership.company, created_by=actor, updated_by=actor,
            number=posting.next_number(company_id, PurchaseRequisition.DOC_TYPE,
                                       PurchaseRequisition.PREFIX, values["request_date"].year),
            **values)
        requisition.full_clean()
        requisition.save()
        audit(actor, membership, "purchase.requisition.created", requisition,
              after={"number": requisition.number, "branch": requisition.branch_id})
    return requisition


@transaction.atomic
def add_line(*, actor, company_id, requisition_id, values):
    membership, requisition = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(requisition)
    values = _writable(values, LINE_FIELDS)
    item = values.get("item")
    if item is None:
        raise ValidationError({"item": "Choose the item."})
    factor = unit_factor(item, values.get("uom"))
    check_item(item, values.get("qty"), factor)
    with use_company(company_id):
        line = PurchaseRequisitionLine(company=membership.company, requisition=requisition,
                                       factor=factor, **values)
        line.full_clean()
        line.save()
    return line


@transaction.atomic
def remove_line(*, actor, company_id, requisition_id, line_id):
    _, requisition = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(requisition)
    with use_company(company_id):
        deleted, _ = requisition.lines.filter(pk=line_id).delete()
    if not deleted:
        raise ValidationError("That line is not on this requisition.")


@transaction.atomic
def submit(*, actor, company_id, requisition_id):
    membership, requisition = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    _draft(requisition)
    with use_company(company_id):
        if not requisition.lines.exists():
            raise ValidationError("Add what is needed before sending it for approval.")
        requisition.status = Status.SUBMITTED
        requisition.submitted_at, requisition.submitted_by = now(), actor
        requisition.updated_by = actor
        requisition.save()
        audit(actor, membership, "purchase.requisition.submitted", requisition,
              before={"status": "draft"}, after={"status": requisition.status})
    return requisition


@transaction.atomic
def decide(*, actor, company_id, requisition_id, approve, note=""):
    membership, requisition = get_requisition(actor=actor, company_id=company_id, pk=requisition_id,
                                              code="purchase.approve")
    if requisition.status != Status.SUBMITTED:
        raise ValidationError(f"{requisition.number} is not waiting for approval.")
    check_own_approval(membership, actor, requisition.submitted_by_id)
    note = (note or "").strip()
    if not approve and not note:
        raise ValidationError({"note": "Say why it is rejected."})
    with use_company(company_id):
        requisition.status = Status.APPROVED if approve else Status.REJECTED
        requisition.decided_at, requisition.decided_by = now(), actor
        requisition.decision_note = note[:255]
        requisition.updated_by = actor
        requisition.save()
        audit(actor, membership, f"purchase.requisition.{'approved' if approve else 'rejected'}",
              requisition, before={"status": "submitted"},
              after={"status": requisition.status, "note": note})
    return requisition


@transaction.atomic
def cancel(*, actor, company_id, requisition_id):
    membership, requisition = get_requisition(actor=actor, company_id=company_id, pk=requisition_id)
    if requisition.status not in (Status.DRAFT, Status.SUBMITTED, Status.APPROVED, Status.REJECTED):
        raise ValidationError(f"{requisition.number} is {requisition.get_status_display().lower()}.")
    with use_company(company_id):
        if requisition.lines.filter(qty_ordered_base__gt=0).exists():
            raise ValidationError("Part of it is on a purchase order already; cancel that first.")
        before = requisition.status
        requisition.status = Status.CANCELLED
        requisition.updated_by = actor
        requisition.save()
        audit(actor, membership, "purchase.requisition.cancelled", requisition,
              before={"status": before}, after={"status": "cancelled"})
    return requisition


def refresh_status(requisition):
    """Approved <-> Ordered as purchase orders cover its lines."""
    lines = list(PurchaseRequisitionLine.all_objects.filter(requisition=requisition))
    if requisition.status not in (Status.APPROVED, Status.ORDERED):
        return
    covered = all(line.qty_open_base <= 0 for line in lines)
    status = Status.ORDERED if covered else Status.APPROVED
    if status != requisition.status:
        requisition.status = status
        requisition.save(update_fields=["status", "updated_at"])


def snapshot(requisition):
    return {"number": requisition.number, "status": requisition.status,
            "request_date": plain(requisition.request_date)}
