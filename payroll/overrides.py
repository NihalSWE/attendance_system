"""Present days set by hand for a salary month or a pay group's week (2026-10-31).

Attendance -> Summary lists each person's days for the period; whoever may
change them types the present days (hourly pay: the hours worked) instead of
fixing day by day:

- **Who**: the owner, company administrator, HR, and a branch admin in its own
  branches (``attendance.fix`` by branch). Heading a department is not enough.
- **A reason** every time. Each change is a new ``DaysOverride`` row and the
  earlier one is ``replaced``; **Back to scans** makes it ``reverted`` and the
  salary is worked out from the check-ins and check-outs again. The history and
  the audit log keep every step, with who did it.
- **Present days count as full days.** The period's working days are made
  present - the days present by the scans first, then no check-out, half days
  and absent days, each in date order - up to that number; the rest are absent.
  A day with part-day leave stays as recorded. Leave, holidays, weekly offs and
  inactive days are not touched. Late arrivals still count as they were scanned.
- **Hourly pay**: the hours replace the hours worked by the scans; a day with no
  check-out then adds no shift hours of its own.
- The attendance days are never rewritten: ``apply`` works on copies, only
  inside the salary calculation.
- Not while the period's salary waits for approval or is finalised.
"""

import copy
import datetime
from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES, branches_for
from attendance.models import AttendanceRecord
from auditlog.services import record_company_event
from common.tenant import use_company
from organization.services import require_company_membership

Status = AttendanceRecord.AttendanceStatus
FIX = "attendance.fix"
#: The days a person was expected at work: what present days are counted out of.
WORKING = (Status.PRESENT, Status.HALF_DAY, Status.ABSENT, Status.INCOMPLETE)
#: Which working days become present first.
ORDER = {Status.PRESENT: 0, Status.INCOMPLETE: 1, Status.HALF_DAY: 2, Status.ABSENT: 3}
ONE, NONE = Decimal("1"), Decimal("0")


def counts(records):
    """The period's day counts from the scans, as the Summary page shows them."""
    found = {"working": 0, "present": 0, "half_day": 0, "incomplete": 0, "absent": 0, "leave": 0,
             "off": 0, "worked_minutes": 0}
    for record in records:
        status = record.attendance_status
        found["worked_minutes"] += record.worked_minutes
        if status in WORKING:
            found["working"] += 1
            found[status] += 1
        elif status == Status.LEAVE:
            found["leave"] += 1
        elif status in (Status.HOLIDAY, Status.WEEKLY_OFF):
            found["off"] += 1
    found["hours"] = (Decimal(found["worked_minutes"]) / Decimal("60")).quantize(Decimal("0.01"))
    return found


def reach(user, company_id):
    """``ALL_BRANCHES`` or the branch ids where ``user`` may change present days."""
    branches = branches_for(user, company_id, FIX)
    if branches is not ALL_BRANCHES and not branches:
        raise PermissionDenied("Changing present days needs owner, company administrator or HR "
                               "access, or access to fix attendance in a branch.")
    return branches


def may(branches, branch_id):
    return branches is ALL_BRANCHES or branch_id in branches


def locked(company_id, first, last, pay_group=None):
    """True when the period's salary waits for approval or is finalised."""
    from payroll.models import PayrollRun

    with use_company(company_id):
        return PayrollRun.objects.filter(
            payroll_period__start_date=first, payroll_period__end_date=last, pay_group=pay_group,
            status__in=(PayrollRun.Status.SUBMITTED, PayrollRun.Status.POSTED)).exists()


def active(company_id, first, last, employee_ids=None):
    """``{employee_id: DaysOverride}`` in force for the period."""
    from payroll.models import DaysOverride

    with use_company(company_id):
        rows = DaysOverride.objects.select_related("created_by").filter(
            start_date=first, end_date=last, status=DaysOverride.Status.ACTIVE)
        if employee_ids is not None:
            rows = rows.filter(employee_id__in=list(employee_ids))
        return {row.employee_id: row for row in rows}


def history(company_id, employee_id, first, last):
    """Every change for one person's period, newest first."""
    from payroll.models import DaysOverride

    with use_company(company_id):
        return list(DaysOverride.objects.select_related("created_by", "ended_by").filter(
            employee_id=employee_id, start_date=first, end_date=last))


def _person(company_id, employee_id, first, last, pay_group):
    """``(employee, records, branch_id, pay_basis)`` for the period, or a refusal."""
    from payroll.services import _compensation_at, period_records

    found, _weekly = period_records(company_id, first, last, pay_group, employee_ids=[employee_id])
    employee, records = next(((e, r) for e, r in found.items() if r), (None, None))
    if employee is None:
        raise ValidationError("This person has no attendance days in this period.")
    end = timezone.make_aware(datetime.datetime.combine(last, datetime.time.max))
    with use_company(company_id):
        compensation = _compensation_at(employee, end)
    basis = compensation.pay_basis if compensation else "monthly"
    return employee, records, records[-1].employee_assignment.branch_id, basis


def _number(value, field, kind):
    try:
        number = Decimal(str(value).strip()) if value not in (None, "") else None
    except InvalidOperation:
        number = None
    if number is None:
        raise ValidationError({field: f"Write the {kind}."})
    return number


@transaction.atomic
def set_override(*, actor, company_id, employee_id, first, last, pay_group=None, present_days=None,
                 hours=None, reason=""):
    """Set (or change again) the present days - for hourly pay the hours - of
    one person's period. Returns the new ``DaysOverride``."""
    from payroll.models import DaysOverride

    branches = reach(actor, company_id)
    membership = require_company_membership(actor, company_id)
    employee, records, branch_id, basis = _person(company_id, employee_id, first, last, pay_group)
    if not may(branches, branch_id):
        raise PermissionDenied("This person works in a branch where you may not change attendance.")
    if locked(company_id, first, last, pay_group):
        raise ValidationError("This period's salary waits for approval or is finalised: send it back "
                              "or undo finalise first.")
    scans = counts(records)
    reason = (reason or "").strip()
    errors = {}
    if not reason:
        errors["reason"] = "Say why it is changed."
    values = {"present_days": None, "hours": None}
    try:
        if basis == "hourly":
            number = _number(hours, "hours", "hours worked")
            limit = Decimal(len(records) * 24)
            if number < 0 or number > limit:
                raise ValidationError({"hours": f"Write hours from 0 to {limit.normalize():f}."})
            values["hours"] = number.quantize(Decimal("0.01"))
        else:
            number = _number(present_days, "present_days", "present days")
            if number != number.to_integral_value() or not 0 <= number <= scans["working"]:
                raise ValidationError({"present_days": f"Write a whole number from 0 to {scans['working']}."})
            values["present_days"] = int(number)
    except ValidationError as exc:
        errors.update(exc.message_dict)
    if errors:
        raise ValidationError(errors)
    now = timezone.now()
    with use_company(company_id):
        before = DaysOverride.objects.select_for_update().filter(
            employee_id=employee.pk, start_date=first, end_date=last,
            status=DaysOverride.Status.ACTIVE).first()
        if before is not None:
            before.status, before.ended_by, before.ended_at = DaysOverride.Status.REPLACED, actor, now
            before.updated_by = actor
            before.save(update_fields=["status", "ended_by", "ended_at", "updated_by", "updated_at"])
        row = DaysOverride.objects.create(
            company=membership.company, employee=employee, pay_group=pay_group, start_date=first,
            end_date=last, reason=reason[:255], scan_present_days=scans["present"],
            scan_hours=scans["hours"], created_by=actor, updated_by=actor, **values)
    record_company_event(
        actor=actor, membership=membership, company=membership.company,
        action="payroll.days_override.set", obj=row,
        before=describe(before) if before else {"scans": _plain(scans)},
        after={**describe(row), "period": f"{first:%Y-%m-%d}..{last:%Y-%m-%d}"})
    return row


@transaction.atomic
def revert(*, actor, company_id, employee_id, first, last, pay_group=None):
    """Back to scans: the salary is worked out from the attendance days again."""
    from payroll.models import DaysOverride

    branches = reach(actor, company_id)
    membership = require_company_membership(actor, company_id)
    _employee, _records, branch_id, _basis = _person(company_id, employee_id, first, last, pay_group)
    if not may(branches, branch_id):
        raise PermissionDenied("This person works in a branch where you may not change attendance.")
    if locked(company_id, first, last, pay_group):
        raise ValidationError("This period's salary waits for approval or is finalised: send it back "
                              "or undo finalise first.")
    with use_company(company_id):
        row = DaysOverride.objects.select_for_update().filter(
            employee_id=employee_id, start_date=first, end_date=last,
            status=DaysOverride.Status.ACTIVE).first()
        if row is None:
            raise ValidationError("Nothing to undo: this period already follows the scans.")
        row.status, row.ended_by, row.ended_at, row.updated_by = (
            DaysOverride.Status.REVERTED, actor, timezone.now(), actor)
        row.save(update_fields=["status", "ended_by", "ended_at", "updated_by", "updated_at"])
    record_company_event(actor=actor, membership=membership, company=membership.company,
                         action="payroll.days_override.reverted", obj=row, before=describe(row),
                         after={"follows": "scans"})
    return row


def apply(records, override):
    """The period's days as the salary should see them: copies, never saved."""
    from payroll.services import expected_minutes

    if override is None:
        return records
    days = [copy.copy(record) for record in records]
    if override.present_days is None:
        # Hourly pay: the hours replace the scans' (``calculate_pay(worked_hours=)``);
        # a day without a check-out adds no shift hours of its own.
        for day in days:
            if day.attendance_status == Status.INCOMPLETE:
                day.attendance_status, day.payable_fraction = Status.PRESENT, ONE
        return days
    work = [day for day in days if day.attendance_status in WORKING]
    kept = [day for day in work if getattr(day, "leave_day", None) is not None]
    free = sorted((day for day in work if getattr(day, "leave_day", None) is None),
                  key=lambda day: (ORDER[day.attendance_status], day.work_date))
    wanted = max(0, override.present_days - sum(1 for day in kept if day.attendance_status == Status.PRESENT))
    for index, day in enumerate(free):
        if index < wanted:
            if day.attendance_status != Status.PRESENT:
                day.attendance_status, day.payable_fraction = Status.PRESENT, ONE
                day.worked_minutes = max(day.worked_minutes, expected_minutes(day))
                day.early_out_minutes = 0
        else:
            day.attendance_status, day.payable_fraction = Status.ABSENT, NONE
            day.worked_minutes = day.late_minutes = day.early_out_minutes = 0
    return days


def describe(override):
    """What a payslip and the audit log keep of an override."""
    if override is None:
        return None
    who = override.created_by
    return {
        "id": override.pk,
        "present_days": override.present_days,
        "hours": _plain(override.hours),
        "scan_present_days": override.scan_present_days,
        "scan_hours": _plain(override.scan_hours),
        "reason": override.reason,
        "by": (who.get_full_name() or who.email) if who else "",
        "at": timezone.localtime(override.created_at).isoformat() if override.created_at else "",
    }


def _plain(value):
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, Decimal):
        return format(value.normalize(), "f")
    return value
