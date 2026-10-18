"""Shared pieces of the store document services (Phase 4).

The same shape as ``purchasing.common``: fetch a company's row or refuse,
the unit and quantity checks every line needs, the audit line, the company's
own today, and the "someone else approves what you submitted" rule.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.utils import timezone

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from inventory import access
from inventory.masters import _writable, plain, units_for

__all__ = ["_writable", "plain", "require", "get", "audit", "unit_factor", "check_qty",
           "check_own_approval", "now", "local_today", "expiry_from"]

OWNER_ROLES = ("owner", "company_admin")


def require(actor, company_id, code, branch_id=None):
    return access.require(actor, company_id, code, branch_id)


def get(model, company_id, pk, what, *, lock=False):
    rows = model.all_objects.filter(company_id=company_id, pk=pk)
    if lock:
        rows = rows.select_for_update(of=("self",))
    row = rows.first() if pk else None
    if row is None:
        raise PermissionDenied(f"{what} not found in this company.")
    return row


def audit(actor, membership, action, obj, before=None, after=None):
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action=action, obj=obj, before=before or {}, after=after or {})


def unit_factor(item, uom):
    factors = units_for(item)
    if uom is None or uom.pk not in factors:
        raise ValidationError({"uom": f"{item.code} is counted in {item.base_uom.code}"
                                      + (" or its pack units." if len(factors) > 1 else ".")})
    return factors[uom.pk]


def check_qty(item, qty, factor, field="qty", allow_negative=False, active=True):
    """A quantity the item's base unit can hold (and, by default, an active item)."""
    if active and item.status != ActiveStatus.ACTIVE:
        raise ValidationError({"item": f"{item.code} is inactive."})
    if qty is None or qty == 0 or (qty < 0 and not allow_negative):
        raise ValidationError({field: "Give a quantity above zero." if not allow_negative
                               else "Give how much changes (+ found, − out)."})
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


def expiry_from(item, mfg, expiry):
    if item.track_expiry and not expiry and mfg and item.shelf_life_days:
        return mfg + datetime.timedelta(days=item.shelf_life_days)
    return expiry
