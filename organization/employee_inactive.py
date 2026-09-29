"""Make an employee inactive for a period, and active again (Nihal, 2026-09-29).

"Make employee status inactive" asks for a first day and, if known, a last
day. The period is an ``EmployeeInactivePeriod``:

- **From its first day** their status is Suspended - at once when it starts
  today or earlier, by itself on the first day when it starts later.
- **After its last day** they are active again by themselves (or back on
  probation, if that is what they were). Without a last day it lasts until
  someone makes them active: the owner, the company admin, HR or their
  branch manager - whoever may edit them (``employees.edit``).
- **On its days** their scans are kept but blocked (the punch reads
  "Blocked: employee inactive"), the day is Inactive with nothing worked,
  and no salary is paid for it. Making them active early gives the days
  from then back: scans made on them count again.

A period is refused over a finalised salary month, over leave already
recorded (cancel the leave first), and over another period.

Statuses change on their own day: ``apply_due`` runs at most once an hour per
company on a request (``common.middleware``), and ``manage.py
apply_inactive_periods`` does the same from a scheduler. The days themselves
(scans, attendance, salary) follow the periods by date, so they are right
whether or not the status has been brought up to date yet.
"""

import datetime

from django import forms
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q

from attendance.services import _is_locked, locked_ranges, recalculate
from auditlog.services import record_company_event
from common.forms import StyledFormMixin, date_widget
from common.tenant import use_company
from employees.inactive import covering
from employees.models import Employee, EmployeeInactivePeriod
from organization.employee_detail_services import company_today
from organization.employee_edit_services import get_employee_for_edit

Status = Employee.EmploymentStatus
Period = EmployeeInactivePeriod
WORKING = (Status.ACTIVE, Status.PROBATION)
ENDED = (Status.RESIGNED, Status.TERMINATED, Status.RETIRED)
DAY = datetime.timedelta(days=1)


class InactiveForm(StyledFormMixin, forms.Form):
    start_date = forms.DateField(
        label="From", widget=date_widget("First inactive day"),
        help_text="The first day they are inactive. Today, an earlier day or a later one.")
    end_date = forms.DateField(
        label="Until (optional)", required=False, widget=date_widget("No end date"),
        help_text=("Their last inactive day; the next day they are active again by themselves. "
                   "Empty: inactive until someone makes them active."))
    reason = forms.CharField(label="Why", max_length=255,
                             help_text="Recorded with the change.")

    def clean(self):
        data = super().clean()
        start, end = data.get("start_date"), data.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The last day cannot be before the first.")
        return data


def open_periods(employee, today):
    """Their periods in force today or still to come, earliest first."""
    return list(
        Period.all_objects.filter(employee=employee, status=Period.Status.ACTIVE)
        .filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).order_by("start_date")
    )


def _sync_status(employee, today, actor=None):
    """Suspended while a period covers today; what they were before, once the
    period that suspended them is over. A suspension made without a period
    (before 2026-09-29) is left as it is. Returns True when it changed."""
    period = covering(employee.company_id, employee.pk, today)
    before = employee.employment_status
    if period is not None and before in WORKING:
        employee.employment_status = Status.SUSPENDED
    elif period is None and before == Status.SUSPENDED:
        last = (Period.all_objects.filter(employee=employee, status=Period.Status.ACTIVE,
                                          end_date__lt=today)
                .order_by("-end_date").first())
        if last is None:
            return False
        employee.employment_status = (last.previous_status if last.previous_status in WORKING
                                      else Status.ACTIVE)
    else:
        return False
    employee.updated_by = actor
    Employee.all_objects.filter(pk=employee.pk).update(
        employment_status=employee.employment_status, updated_by=actor)
    return True


def _apply_to_days(company_id, employee, first, last, today):
    """Judge their scans from ``first`` to ``last`` again - blocked or counted
    by the periods as they are now - and rebuild those days. A finalised
    salary month is left alone."""
    import zoneinfo

    from devices.models import PunchEvent
    from devices.services.processing import resolve_and_authorize

    last = min(last, today)
    if first > last:
        return
    company = employee.company
    try:
        zone = zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        zone = zoneinfo.ZoneInfo("UTC")
    locked = locked_ranges(company_id)
    since = datetime.datetime.combine(first, datetime.time.min, tzinfo=zone)
    until = datetime.datetime.combine(last + DAY, datetime.time.min, tzinfo=zone)
    with use_company(company_id):
        for punch in (PunchEvent.objects.select_related("device", "device__company")
                      .filter(employee=employee, punched_at_utc__gte=since,
                              punched_at_utc__lt=until)
                      .order_by("punched_at_utc", "pk")):
            if not _is_locked(punch.punched_at_utc.astimezone(zone).date(), locked):
                resolve_and_authorize(punch)
    recalculate(company_id, employee_ids=[employee.pk], start=first, end=last)


def _audit(actor, membership, employee, action, period, before, after):
    record_company_event(
        actor=actor, membership=membership, company=membership.company, action=action,
        obj=employee, before=before,
        after={**after, "start_date": period.start_date.isoformat(),
               "end_date": period.end_date.isoformat() if period.end_date else "",
               "reason": period.reason},
    )


@transaction.atomic
def make_inactive(*, actor, company_id, employee_id, start_date, end_date=None, reason=""):
    """Inactive from ``start_date`` to ``end_date`` (None: until made active)."""
    from leaves.models import LeaveDay
    from leaves.services import LIVE_LEAVE_DAYS

    membership, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    if employee.user_id is not None and employee.user_id == actor.pk:
        raise PermissionDenied("You cannot change your own status.")
    if employee.employment_status in ENDED:
        raise ValidationError("They have left. Their status stays as it is.")
    reason = (reason or "").strip()
    errors = {}
    if not reason:
        errors["reason"] = "Say why, so the next person reading this knows."
    if start_date is None:
        errors["start_date"] = "Choose the first inactive day."
    elif end_date is not None and end_date < start_date:
        errors["end_date"] = "The last day cannot be before the first."
    if errors:
        raise ValidationError(errors)
    today = company_today(membership.company)
    if employee.employment_status == Status.SUSPENDED and covering(company_id, employee.pk, today) is None:
        raise ValidationError("They are already inactive. Make them active first.")
    if employee.joining_date and start_date < employee.joining_date:
        raise ValidationError({"start_date": (
            f"They joined on {employee.joining_date:%d %b %Y}; start on or after it.")})
    with use_company(company_id):
        clash = (Period.objects.filter(employee=employee, status=Period.Status.ACTIVE)
                 .filter(Q(end_date__isnull=True) | Q(end_date__gte=start_date)))
        if end_date is not None:
            clash = clash.filter(start_date__lte=end_date)
        other = clash.order_by("start_date").first()
        if other is not None:
            raise ValidationError({"start_date": (
                f"They are already inactive from {other.start_date:%d %b %Y}"
                f"{f' to {other.end_date:%d %b %Y}' if other.end_date else ''}. "
                "Make them active first, or choose other days.")})
        leave = (LeaveDay.objects.filter(employee=employee, status__in=LIVE_LEAVE_DAYS,
                                         work_date__gte=start_date)
                 .order_by("work_date"))
        if end_date is not None:
            leave = leave.filter(work_date__lte=end_date)
        leave_day = leave.first()
        if leave_day is not None:
            raise ValidationError({"start_date": (
                f"They have leave on {leave_day.work_date:%d %b %Y}. Cancel it first, "
                "or choose days without leave.")})
    locked = locked_ranges(company_id)
    last_touched = min(end_date or today, today)
    if start_date <= last_touched and any(
            _is_locked(start_date + DAY * offset, locked)
            for offset in range((last_touched - start_date).days + 1)):
        raise ValidationError({"start_date": (
            "Salary for some of those days is finalised, so they can no longer change. "
            "Start after that month.")})

    before = employee.employment_status
    with use_company(company_id):
        period = Period(
            employee=employee, start_date=start_date, end_date=end_date, reason=reason,
            previous_status=before if before in WORKING else Status.ACTIVE,
            created_by=actor, updated_by=actor,
        )
        period.company_id = company_id
        period.full_clean()
        period.save()
    _sync_status(employee, today, actor)
    _apply_to_days(company_id, employee, start_date, end_date or today, today)
    _audit(actor, membership, employee, "employee.made_inactive", period,
           {"employment_status": before}, {"employment_status": employee.employment_status})
    return employee, period


@transaction.atomic
def make_active(*, actor, company_id, employee_id):
    """Active again from today: the period in force ends yesterday; one not
    started yet (or starting today) is cancelled. A Suspended status set
    before periods existed is simply made Active, as before."""
    membership, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.edit")
    if employee.user_id is not None and employee.user_id == actor.pk:
        raise PermissionDenied("You cannot change your own status.")
    if employee.employment_status in ENDED:
        raise ValidationError("They have left. Their status stays as it is.")
    today = company_today(membership.company)
    periods = open_periods(employee, today)
    before = employee.employment_status
    if not periods:
        if before != Status.SUSPENDED:
            raise ValidationError("They are not inactive.")
        with use_company(company_id):
            employee.employment_status = Status.ACTIVE
            employee.updated_by = actor
            employee.save(update_fields=["employment_status", "updated_by", "updated_at"])
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="employee.status_changed", obj=employee,
                before={"employment_status": before},
                after={"employment_status": employee.employment_status, "reason": ""})
        return employee
    restore = periods[0].previous_status if periods[0].previous_status in WORKING else Status.ACTIVE
    for period in periods:
        changed_from = period.start_date if period.start_date >= today else today
        was_end = period.end_date
        if period.start_date >= today:
            period.status = Period.Status.CANCELLED
        else:
            period.end_date = today - DAY
        period.ended_early_by = actor
        period.updated_by = actor
        with use_company(company_id):
            period.save(update_fields=["status", "end_date", "ended_early_by", "updated_by",
                                       "updated_at"])
        _apply_to_days(company_id, employee, changed_from, was_end or today, today)
        _audit(actor, membership, employee, "employee.made_active", period,
               {"employment_status": before},
               {"employment_status": restore, "cancelled": period.status == Period.Status.CANCELLED})
    if employee.employment_status == Status.SUSPENDED:
        employee.employment_status = restore
        Employee.all_objects.filter(pk=employee.pk).update(
            employment_status=restore, updated_by=actor)
    return employee


def apply_due(company_id, today=None):
    """Suspend whoever a period covers today; give back the status of whoever's
    period ended. Returns how many changed. No recalculation is needed: the
    days themselves follow the periods by date."""
    from tenants.models import Company

    company = Company.objects.get(pk=company_id)
    today = today or company_today(company)
    changed = 0
    starting = Period.all_objects.filter(
        company_id=company_id, status=Period.Status.ACTIVE, start_date__lte=today,
        employee__employment_status__in=WORKING,
    ).filter(Q(end_date__isnull=True) | Q(end_date__gte=today)).values_list("employee_id", flat=True)
    ending = Period.all_objects.filter(
        company_id=company_id, status=Period.Status.ACTIVE, end_date__lt=today,
        employee__employment_status=Status.SUSPENDED,
    ).values_list("employee_id", flat=True)
    for employee in Employee.all_objects.filter(pk__in=set(starting) | set(ending)):
        changed += _sync_status(employee, today)
    return changed


def apply_due_hourly(company_id):
    """``apply_due`` at most once an hour per company per process: the first
    request after a company's midnight brings statuses up to date without a
    scheduler (hourly, because the company's midnight is not the server's)."""
    import logging

    from django.utils import timezone

    key = f"inactive-periods-applied:{company_id}:{timezone.now():%Y-%m-%d-%H}"
    if cache.get(key):
        return
    try:
        apply_due(company_id)
    except Exception:  # noqa: BLE001 - a page must never fail on this
        logging.getLogger(__name__).exception("Inactive periods not applied for %s", company_id)
        return
    cache.set(key, True, 60 * 60)
