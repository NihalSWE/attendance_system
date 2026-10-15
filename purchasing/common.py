"""Shared pieces of the purchase services: permission, lookup, lines, audit.

Approval rule (D6, recommended in docs/inventory/01-PHASE-0-DECISIONS.md and
used until the client's chains are signed off): whoever holds "Approve
purchase requisitions, orders and returns" in the document's branch
approves - but not a document they submitted themselves, unless they are the
owner or company administrator (who approve everything anyway).
"""

from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from inventory import access
from inventory.masters import _writable, plain, units_for

__all__ = ["_writable", "plain", "require", "get", "audit", "unit_factor", "check_item",
           "check_own_approval", "now", "local_today"]

OWNER_ROLES = ("owner", "company_admin")


def require(actor, company_id, code, branch_id=None):
    return access.require(actor, company_id, code, branch_id)


def get(model, company_id, pk, what, *, lock=False):
    rows = model.all_objects.filter(company_id=company_id, pk=pk)
    if lock:
        rows = rows.select_for_update()
    row = rows.first() if pk else None
    if row is None:
        raise PermissionDenied(f"{what} not found in this company.")
    return row


def audit(actor, membership, action, obj, before=None, after=None):
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action=action, obj=obj, before=before or {}, after=after or {})


def unit_factor(item, uom):
    """Base units in one ``uom`` of ``item``; refused for a unit the item has not."""
    factors = units_for(item)
    if uom is None or uom.pk not in factors:
        raise ValidationError({"uom": f"{item.code} is counted in {item.base_uom.code}"
                                      + (" or its pack units." if len(factors) > 1 else ".")})
    return factors[uom.pk]


def check_item(item, qty, factor, field="qty"):
    """An active item and a quantity its base unit can hold."""
    if item.status != ActiveStatus.ACTIVE:
        raise ValidationError({"item": f"{item.code} is inactive."})
    if qty is None or qty <= 0:
        raise ValidationError({field: "Give a quantity above zero."})
    base = qty * factor
    decimals = item.base_uom.decimals
    if base != base.quantize(Decimal(1).scaleb(-decimals)):
        raise ValidationError({field: f"{item.code} is counted in whole {item.base_uom.code}."
                               if decimals == 0 else
                               f"{item.code} takes at most {decimals} digits after the point "
                               f"in {item.base_uom.code}."})


def check_own_approval(membership, actor, submitted_by_id):
    if submitted_by_id == actor.pk and membership.role not in OWNER_ROLES:
        raise PermissionDenied("You submitted this yourself; someone else approves it.")


def now():
    return timezone.now()


def local_today(company):
    from zoneinfo import ZoneInfo

    return timezone.now().astimezone(ZoneInfo(company.timezone or "Asia/Dhaka")).date()
