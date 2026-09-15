"""One employee's history, and ending their employment (plan step N6).

The history page is read-only: every change still happens on Edit employee.
Ending employment is the one write here. It wraps
``employees.services.terminate_employee`` — which closes the open placement and
salary — with what a screen needs around it: who may do it, what date it can
be, an audit record, and the knock-on effects a person would otherwise have to
remember one by one.

Dates are asked for the way HR says them: the **last working day**. Everything
dated ends at the midnight after it, in company time, which is how the rest of
the system stores an end (a shift's last day is its end minus one day), and
``leaving_date`` is that last day, which is how attendance reads it (no day is
written after it).
"""

import datetime
import zoneinfo
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from accounts.models import CompanyMembership
from attendance.models import AttendanceRecord
from auditlog.models import AuditLog
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import terminate_employee
from organization import employee_login
from organization.employee_edit_services import get_employee_for_edit

#: What ending employment can be recorded as. Suspension is not an ending.
ENDING_STATUSES = (
    (Employee.EmploymentStatus.RESIGNED, "Resigned"),
    (Employee.EmploymentStatus.TERMINATED, "Terminated"),
    (Employee.EmploymentStatus.RETIRED, "Retired"),
)
ENDED = {value for value, _ in ENDING_STATUSES}

#: Audit actions shown on the history page, in words.
ACTION_LABELS = {
    "employee.details_updated": "Details changed",
    "employee.placement_changed": "Placement changed",
    "employee.salary_changed": "Salary changed",
    "employee.salary_set": "Salary set",
    "employee.login_created": "Login given",
    "employee.login_role_changed": "Login access changed",
    "employee.login_password_reset": "Login password reset",
    "employee.login_disabled": "Login disabled",
    "employee.login_enabled": "Login enabled",
    "employee.employment_ended": "Employment ended",
}


def _zone(company):
    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def _local_day(instant, tz):
    return instant.astimezone(tz).date() if instant else None


def _last_day(end, tz):
    """An exclusive end instant as the last day it covered."""
    if end is None:
        return None
    return (end.astimezone(tz) - datetime.timedelta(days=1)).date()


def company_today(company):
    return timezone.now().astimezone(_zone(company)).date()


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@dataclass
class Period:
    """One dated row, with its first and last day in company time."""

    row: object
    first_day: datetime.date
    last_day: datetime.date | None

    @property
    def is_current(self):
        return self.last_day is None


def employee_history(*, actor, company_id, employee_id):
    """Everything the history page shows. Read-only."""
    from devices.models import DeviceEnrollment
    from scheduling import services as schedule

    membership, employee, assignment, compensation = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id
    )
    company = membership.company
    tz = _zone(company)
    today = company_today(company)

    with use_company(company_id):
        placements = [
            Period(row, _local_day(row.effective_from, tz), _last_day(row.effective_to, tz))
            for row in EmployeeAssignment.objects.select_related(
                "branch", "department", "designation", "manager",
            )
            .filter(employee=employee)
            .exclude(status=EmployeeAssignment.Status.CANCELLED)
            .order_by("-effective_from")
        ]
        salaries = [
            Period(row, _local_day(row.effective_from, tz), _last_day(row.effective_to, tz))
            for row in EmployeeCompensation.objects.filter(employee=employee)
            .exclude(status=EmployeeCompensation.Status.CANCELLED)
            .order_by("-effective_from")
        ]
        devices = [
            Period(row, _local_day(row.effective_from, tz), _last_day(row.effective_to, tz))
            for row in DeviceEnrollment.objects.select_related("device", "device__branch")
            .filter(employee=employee)
            .order_by("-effective_from")
        ]
        month_start = today.replace(day=1)
        month_counts = dict(
            AttendanceRecord.objects.filter(
                employee=employee, work_date__gte=month_start, work_date__lte=today,
            )
            .values_list("attendance_status")
            .annotate(total=Count("pk"))
            .values_list("attendance_status", "total")
        )
        events = list(
            AuditLog.objects.select_related("actor_user")
            .filter(
                object_app="employees", object_model="employee",
                object_id=str(employee.pk),
            )
            .order_by("-occurred_at", "-pk")[:15]
        )
        # The login's branches are tenant-scoped, so it is read in here too.
        login = employee_login.login_for(company_id, employee)
    for event in events:
        event.label = ACTION_LABELS.get(
            event.action, event.action.split(".")[-1].replace("_", " ").capitalize()
        )

    return {
        "membership": membership,
        "employee": employee,
        "assignment": assignment,
        "compensation": compensation,
        "placements": placements,
        "salaries": salaries,
        "devices": devices,
        "own_shifts": schedule.employee_shift_history(company_id, employee, tz),
        "login": login,
        "month_start": month_start,
        "month_counts": month_counts,
        "events": events,
        "is_ended": employee.employment_status in ENDED,
        "company_tz": company.timezone or "UTC",
        "today": today,
    }


# --------------------------------------------------------------------------
# Ending employment
# --------------------------------------------------------------------------


def _finalised_until(company_id):
    """The last day of the latest finalised salary month, or None."""
    from attendance.services import locked_ranges

    ends = [end for _start, end in locked_ranges(company_id)]
    return max(ends) if ends else None


def end_employment(*, actor, company_id, employee_id, last_day, status, reason,
                   disable_login=True, end_device_enrollments=True):
    """Record that somebody has left. Returns a summary of what was changed.

    Refused when: the person has already left; the last working day is in the
    future (record it once it has happened — until then they are still at work
    and still on attendance); it is before their current placement began; or
    it falls before the end of a finalised salary month, whose salary was paid
    on the old footing.
    """
    from attendance.services import recalculate
    from devices.models import DeviceEnrollment

    membership, employee, assignment, compensation = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id
    )
    company = membership.company
    tz = _zone(company)
    reason = (reason or "").strip()

    if status not in ENDED:
        raise ValidationError({"status": "Choose resigned, terminated or retired."})
    if not reason:
        raise ValidationError({"reason": "Say why, for the record."})
    if last_day is None:
        raise ValidationError({"last_day": "Choose their last working day."})
    if employee.employment_status in ENDED or (
        assignment is None and employee.leaving_date is not None
    ):
        raise ValidationError("This employee has already left.")
    today = company_today(company)
    if last_day > today:
        raise ValidationError({
            "last_day": "Record this on or after their last working day. Until "
                        "then they are still at work and still on attendance."
        })
    if assignment is not None and last_day < _local_day(assignment.effective_from, tz):
        raise ValidationError({
            "last_day": f"Their current placement began on "
                        f"{_local_day(assignment.effective_from, tz):%d %b %Y}; the "
                        "last working day cannot be before it."
        })
    if compensation is not None and compensation.effective_from.astimezone(tz).date() > last_day:
        raise ValidationError({
            "last_day": f"Their salary starts on "
                        f"{_local_day(compensation.effective_from, tz):%d %b %Y}, after "
                        "that day. Change the salary on Edit employee first."
        })
    finalised = _finalised_until(company_id)
    if finalised is not None and last_day < finalised:
        raise ValidationError({
            "last_day": f"Salary up to {finalised:%d %b %Y} is finalised, so the "
                        "last working day cannot be before that."
        })

    ends_at = datetime.datetime.combine(
        last_day + datetime.timedelta(days=1), datetime.time.min, tzinfo=tz
    )
    summary = {"login_disabled": False, "enrollments_ended": 0, "days_rebuilt": 0}

    with transaction.atomic():
        before = {
            "employment_status": employee.employment_status,
            "leaving_date": employee.leaving_date.isoformat() if employee.leaving_date else None,
            "assignment_id": assignment.pk if assignment else None,
            "compensation_id": compensation.pk if compensation else None,
        }
        terminate_employee(
            employee=employee, effective_at=ends_at, employment_status=status,
            reason=reason, actor=actor,
        )
        # terminate_employee reads the date off the end instant, which is the
        # day *after* the last one. Attendance reads leaving_date as the last
        # day worked, so it is set to exactly that.
        with use_company(company_id):
            Employee.objects.filter(pk=employee.pk).update(leaving_date=last_day)
            employee.refresh_from_db()

            if end_device_enrollments:
                open_rows = DeviceEnrollment.objects.select_for_update().filter(
                    employee=employee, effective_from__lt=ends_at,
                ).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=ends_at))
                for enrollment in open_rows:
                    old_end = enrollment.effective_to
                    enrollment.effective_to = ends_at
                    enrollment.save(update_fields=["effective_to", "updated_at"])
                    # before_data carries the changed field, as the device
                    # policy history requires (devices/services/policy_history.py).
                    record_company_event(
                        actor=actor, membership=membership, company=company,
                        action="device_enrollment.ended_with_employment",
                        obj=enrollment,
                        before={"effective_to": old_end.isoformat() if old_end else None},
                        after={"effective_to": ends_at.isoformat()},
                    )
                    summary["enrollments_ended"] += 1

            record_company_event(
                actor=actor, membership=membership, company=company,
                action="employee.employment_ended", obj=employee,
                before=before,
                after={
                    "employment_status": status,
                    "leaving_date": last_day.isoformat(),
                    "ends_at": ends_at.isoformat(),
                    "reason": reason,
                    "enrollments_ended": summary["enrollments_ended"],
                },
            )

        if disable_login:
            with use_company(company_id):
                login = employee_login.login_for(company_id, employee)
            if login is not None and login.status == CompanyMembership.Status.ACTIVE:
                employee_login.set_login_active(
                    actor=actor, company_id=company_id, employee_id=employee.pk,
                    active=False,
                )
                summary["login_disabled"] = True

    # Days after the last one no longer belong to them; the recalculation
    # removes any that were written (a finalised month is never touched).
    result = recalculate(
        company_id, employee_ids=[employee.pk], start=last_day, end=today,
    )
    summary["days_rebuilt"] = result.get("days", 0)
    return employee, summary
