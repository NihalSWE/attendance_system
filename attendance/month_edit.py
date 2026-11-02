"""Attendance -> Calendar -> Edit this month (2026-10-26).

One employee's calendar month on one page: each day's status (present, half
day, absent) and its late arrival, with the month's totals worked out from the
days. A change goes through the same corrections as Fix a day
(attendance.correction_services): the day is recalculated, the change keeps
its reason and its before/after, and a finalised salary month is refused. One
reason covers every change saved together, and they are saved all or none.
"""

import calendar
import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import redirect
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from attendance import access, correction_services
from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.services import _is_locked, locked_ranges, recalculate
from base_template.tables import render
from common.exceptions import NotInThisCompany
from common.tenant import use_company
from employees.models import Employee
from organization.views import _company_or_redirect

Status = AttendanceRecord.AttendanceStatus
CHOICES = ((Status.PRESENT, "Present"), (Status.HALF_DAY, "Half day"), (Status.ABSENT, "Absent"))


def _days(company_id, user, employee_id, year, month):
    """Each day of the month: its record and what may be changed on it."""
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    locked = locked_ranges(company_id)
    with use_company(company_id):
        records = {r.work_date: r for r in AttendanceRecord.objects.filter(
            employee_id=employee_id, work_date__gte=first, work_date__lte=last)}
        excused = set(AttendanceCorrection.objects.filter(
            employee_id=employee_id, work_date__gte=first, work_date__lte=last,
            correction_type=AttendanceCorrection.CorrectionType.EXCUSE_LATE,
            status=AttendanceCorrection.Status.APPLIED).values_list("work_date", flat=True))
    rows = []
    for offset in range((last - first).days + 1):
        day = first + datetime.timedelta(days=offset)
        record = records.get(day)
        why_not = ""
        if record is None:
            why_not = "No attendance day"
        elif _is_locked(day, locked):
            why_not = "Salary finalised."
        elif record.is_open:
            why_not = "Not finished yet."
        elif record.leave_day_id or record.attendance_status not in correction_services.CORRECTABLE_DAY_STATUSES:
            why_not = ""  # leave, holiday, weekly off: shown as they are
        elif not access.may_fix_day(user, company_id, employee_id, day):
            why_not = "Not yours to change."
        editable = (record is not None and not why_not and not record.leave_day_id
                    and record.attendance_status in correction_services.CORRECTABLE_DAY_STATUSES)
        rows.append({
            "day": day, "record": record, "editable": editable, "why_not": why_not,
            "status": record.attendance_status if record else "",
            "late": bool(record and record.late_minutes and day not in excused),
            "excused": day in excused,
        })
    return rows


def _totals(rows):
    """The month in numbers, from its days."""
    count = {"working": 0, "present": 0, "late": 0, "half_day": 0, "absent": 0, "leave": 0,
             "off": 0, "incomplete": 0}
    for row in rows:
        record = row["record"]
        if record is None:
            continue
        status = record.attendance_status
        if status in (Status.WEEKLY_OFF, Status.HOLIDAY):
            count["off"] += 1
            continue
        if status == Status.INACTIVE:
            continue
        count["working"] += 1
        if status == Status.LEAVE or record.leave_day_id:
            count["leave"] += 1
        elif status == Status.PRESENT:
            count["present"] += 1
        elif status == Status.HALF_DAY:
            count["half_day"] += 1
        elif status == Status.ABSENT:
            count["absent"] += 1
        elif status == Status.INCOMPLETE:
            count["incomplete"] += 1
        if row["late"]:
            count["late"] += 1
    return count


@login_required
@require_http_methods(["GET", "POST"])
def attendance_month_edit(request, employee_id, year, month):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, _scope = access.fix_scope(request.user, company_id)
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise NotInThisCompany("No such month.")
    with use_company(company_id):
        employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise NotInThisCompany("Employee not found in this company.")
    first = datetime.date(year, month, 1)
    last = datetime.date(year, month, calendar.monthrange(year, month)[1])
    # Every day of the month worked out for this person, not only those due.
    recalculate(company_id, employee_ids=[employee_id], start=first, end=last)
    rows = _days(company_id, request.user, employee_id, year, month)
    here = reverse("attendance:attendance_month_edit", args=[employee_id, year, month])

    errors, reason = {}, ""
    if request.method == "POST":
        reason = request.POST.get("reason", "").strip()
        changes = []
        for row in rows:
            if not row["editable"]:
                continue
            key = row["day"].isoformat()
            wanted = request.POST.get(f"status_{key}", row["status"])
            if wanted != row["status"] and wanted in dict(CHOICES):
                changes.append(("status", row["day"], wanted))
            if row["late"] and request.POST.get(f"excuse_{key}"):
                changes.append(("excuse", row["day"], None))
        if not changes:
            messages.info(request, "Nothing changed.")
            return redirect(here)
        if not reason:
            errors["reason"] = "Say why: it is kept with every change."
        else:
            try:
                with transaction.atomic():
                    for kind, day, status in changes:
                        common = {"actor": request.user, "company_id": company_id,
                                  "employee_id": employee_id, "work_date": day, "reason": reason}
                        try:
                            if kind == "status":
                                correction_services.change_status(status=status, **common)
                            else:
                                correction_services.excuse_late(**common)
                        except ValidationError as exc:
                            errors[day] = " ".join(exc.messages)
                    if errors:
                        raise ValidationError("refused")
            except ValidationError:
                pass
            else:
                messages.success(request, f"Saved {len(changes)} change{'s' if len(changes) != 1 else ''} "
                                          f"for {employee.full_name}, {calendar.month_name[month]} {year}.")
                return redirect(here)
        for row in rows:
            key = row["day"].isoformat()
            row["error"] = errors.get(row["day"], "")
            if row["editable"]:
                row["status"] = request.POST.get(f"status_{key}", row["status"])
                row["excuse_ticked"] = bool(request.POST.get(f"excuse_{key}"))

    previous = first - datetime.timedelta(days=1)
    following = last + datetime.timedelta(days=1)
    return render(request, "attendance/month_edit.html", {
        "employee": employee, "year": year, "month": month,
        "company_tz": membership.company.timezone or "UTC",
        "month_name": calendar.month_name[month], "rows": rows, "totals": _totals(rows),
        "choices": CHOICES, "reason": reason, "reason_error": errors.get("reason", ""),
        "has_errors": bool(errors),
        "previous_url": reverse("attendance:attendance_month_edit",
                                args=[employee_id, previous.year, previous.month]),
        "next_url": reverse("attendance:attendance_month_edit",
                            args=[employee_id, following.year, following.month]),
        "calendar_url": (reverse("attendance:attendance_calendar")
                         + f"?employee={employee_id}&year={year}&month={month}"),
    })
