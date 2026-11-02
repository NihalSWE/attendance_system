"""The searches Ask runs (2026-11-02). Read-only, and each one built on the code
its page already uses, so the numbers are the page's numbers:

- attendance: ``attendance.access.view_scope`` and the Daily list's query
  (days brought up to date with ``refresh`` first; "late" is the Late page's
  ``late_minutes > 0``);
- in the office now: ``access.now_scope`` and ``live_status.statuses_for``;
- leave: the Leave list's ``_list_scope``, approved leave days (what
  attendance and salary read), and the Leave balances page's figures;
- overtime: ``overtime_scope`` and the Overtime page's ``overtime_queryset``;
- salary: ``salary_month_branches`` (HR and the auditor see no pay), the
  Salary page's run and its ``_by_department``.

Who may see what is decided here, by those same rules, and never by the AI.
Employee names are matched here too, among the people the viewer may see.
"""

import datetime
import zoneinfo
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Count, F, Max, Q, Sum
from django.urls import reverse
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES, Scope
from ask.interpret import OVERTIME
from attendance import access, live_status
from attendance.models import AttendanceRecord
from attendance.services import refresh
from common.tenant import use_company
from organization.access_services import people
from organization.models import Department
from tenants.models import Company

#: A table holds at most this many rows; the page it links to holds them all.
LIMIT = 2000
LEFT = ("resigned", "terminated", "retired")
Status = AttendanceRecord.AttendanceStatus


class Cell(str):
    """A table cell: its text, and optionally a link, a sort key and whether
    it is a number (right-aligned). Searched and sorted like any text cell."""

    def __new__(cls, text, url="", sort=None, numeric=False):
        cell = super().__new__(cls, "" if text is None else text)
        cell.url, cell.numeric = url, numeric
        if sort is not None:
            cell.sort_key = sort
        return cell


@dataclass
class Result:
    answer: str = ""
    columns: list = field(default_factory=list)   # [(label, numeric)]
    rows: list = field(default_factory=list)      # [[Cell]]
    choices: list = field(default_factory=list)   # several people matched: [(label, query)]
    link: tuple = None                            # (label, url) of the page it comes from
    refused: str = ""
    cut: int = 0                                  # rows left out past LIMIT


# --- small helpers -------------------------------------------------------------

def day(value):
    return f"{value.day} {value:%b %Y}"


def span(first, last):
    """"on 2 Nov 2026", or "from 1 Oct to 2 Nov 2026"."""
    if first == last:
        return f"on {day(first)}"
    start = f"{first.day} {first:%b}" if first.year == last.year else day(first)
    return f"from {start} to {day(last)}"


def plural(count, one, many=None):
    return f"{count} {one if count == 1 else (many or one + 's')}"


def money(value):
    value = value or Decimal("0")
    return Cell(f"{value:,.2f}", sort=value, numeric=True)


def number(value):
    return Cell(f"{value}", sort=value, numeric=True)


def days_cell(value):
    value = value or Decimal("0")
    return Cell(f"{value.normalize():f}", sort=value, numeric=True)


def hours(minutes):
    minutes = minutes or 0
    return Cell(f"{minutes // 60}:{minutes % 60:02d}", sort=minutes, numeric=True)


def _zone(company_id):
    name = Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first()
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def _clock(moment, zone):
    return moment.astimezone(zone).strftime("%H:%M") if moment else ""


def _profile(employee):
    return reverse("organization:employee_detail", args=[employee.public_id])


def _calendar(employee_id, on):
    return f"{reverse('attendance:attendance_calendar')}?employee={employee_id}&date={on.isoformat()}"


def _query(**values):
    from urllib.parse import urlencode

    return urlencode({key: value for key, value in values.items() if value not in (None, "")})


def _person(ask, candidates):
    """``(employee, None)``, or ``(None, Result)`` when the name matched no one
    or several people (they are listed to choose from). ``(None, None)``: no
    name asked. ``candidates``: the people the viewer may see, from
    ``organization.access_services.people``. Call inside the company."""
    if ask.employee_id:
        found = candidates.filter(pk=ask.employee_id).first()
        if found is None:
            return None, Result(answer="That person is not among the people you can see.")
        return found, None
    text = ask.employee.strip()
    if not text:
        return None, None
    matches = list(candidates.filter(table_code__iexact=text))
    if not matches:
        words = Q()
        for word in text.split():
            words &= (Q(first_name__icontains=word) | Q(middle_name__icontains=word)
                      | Q(last_name__icontains=word))
        matches = list(candidates.filter(words).order_by("first_name", "last_name", "pk")[:30])
    if not matches:
        return None, Result(answer=f"No one called “{text}” among the people you can see.")
    if len(matches) > 1:
        return None, Result(
            answer=f"{len(matches)} people match “{text}”. Which one?",
            choices=[(" · ".join(part for part in (person.full_name, person.table_code or "",
                                                     person.table_branch or "") if part),
                      ask.query(employee_id=person.pk)) for person in matches])
    return matches[0], None


def _department(queryset, name, field):
    return queryset.filter(**{f"{field}__name__iexact": name}) if name else queryset


# --- attendance ------------------------------------------------------------------

PHRASE = {"": "Attendance", "late": "Late", "absent": "Absent", "present": "Present",
          "half_day": "Half day", "on_leave": "On leave", "early_out": "Left early",
          "incomplete": "Missing scan", "inactive": "Inactive"}
# What each status means as a filter on the day's record (the Daily list's
# Status column; "late" and "left early" are the minutes the pages show).
STATUS_FILTER = {
    "late": Q(late_minutes__gt=0), "early_out": Q(early_out_minutes__gt=0),
    "on_leave": Q(attendance_status=Status.LEAVE), "present": Q(attendance_status=Status.PRESENT),
    "absent": Q(attendance_status=Status.ABSENT), "half_day": Q(attendance_status=Status.HALF_DAY),
    "incomplete": Q(attendance_status=Status.INCOMPLETE),
    "inactive": Q(attendance_status=Status.INACTIVE),
}
#: The Daily list's own status filter, where it has one.
LIST_STATUS = {"present": "present", "absent": "absent", "half_day": "half_day", "on_leave": "leave",
               "incomplete": "incomplete", "inactive": "inactive"}


def _attendance_start(actor, company_id, ask):
    """``(visible, person, None)`` or ``(.., .., Result)`` to show instead."""
    try:
        _membership, visible = access.view_scope(actor, company_id)
    except PermissionDenied:
        return None, None, Result(refused="Your login does not show attendance.")
    with use_company(company_id):
        person, stop = _person(ask, people(visible))
    return visible, person, stop


def _days(company_id, visible, ask, person):
    """The Daily list's rows for the dates asked, before the status filter."""
    first, last = ask.period
    refresh(company_id, start=first, end=last)
    queryset = access.scope(
        AttendanceRecord.objects.filter(work_date__gte=first, work_date__lte=last), visible)
    if ask.branch_id:
        queryset = queryset.filter(branch_id=ask.branch_id)
    queryset = _department(queryset, ask.department, "employee_assignment__department")
    if person is not None:
        queryset = queryset.filter(employee=person)
    return queryset


def attendance_days(actor, company_id, ask):
    visible, person, stop = _attendance_start(actor, company_id, ask)
    if stop:
        return stop
    first, last = ask.period
    zone = _zone(company_id)
    with use_company(company_id):
        queryset = _days(company_id, visible, ask, person)
        if ask.status:
            queryset = queryset.filter(STATUS_FILTER[ask.status])
        total = queryset.count()
        how_many = queryset.values("employee_id").distinct().count()
        records = list(queryset.select_related("employee", "branch", "employee_assignment")
                       .order_by("work_date", "employee__first_name", "employee__last_name")[:LIMIT])
    rows = [[
        Cell(f"{record.work_date:%a} {day(record.work_date)}", _calendar(record.employee_id, record.work_date),
             sort=record.work_date.toordinal()),
        Cell(record.employee_assignment.employee_code if record.employee_assignment else ""),
        Cell(record.employee.full_name, _profile(record.employee)),
        Cell(record.branch.name if record.branch_id else ""),
        Cell(record.get_attendance_status_display()),
        Cell(_clock(record.first_in_at, zone)), Cell(_clock(record.last_out_at, zone)),
        number(record.late_minutes),
    ] for record in records]
    label = PHRASE[ask.status]
    if person is not None:
        answer = f"{person.full_name}, {label.lower()} {span(first, last)}: {plural(total, 'day')}."
    elif first == last:
        answer = f"{label} {span(first, last)}: {plural(how_many, 'person', 'people') if how_many else 'no one'}."
    else:
        answer = (f"{label} {span(first, last)}: {plural(total, 'day')}, {plural(how_many, 'person', 'people')}."
                  if total else f"{label} {span(first, last)}: no one.")
    if ask.status == "late":
        link = ("Open the Late entries page", reverse("attendance:attendance_late") + "?" + _query(
            date_from=first.isoformat(), date_to=last.isoformat(), branch=ask.branch_id,
            employee=person.pk if person else None))
    else:
        link = ("Open the Daily list", reverse("attendance:attendance_list") + "?" + _query(
            date_from=first.isoformat(), date_to=last.isoformat(), branch=ask.branch_id,
            employee=person.pk if person else None, status=LIST_STATUS.get(ask.status)))
    return Result(answer=answer, rows=rows, link=link, cut=max(0, total - LIMIT), columns=[
        ("Date", False), ("Employee ID", False), ("Name", False), ("Branch", False), ("Status", False),
        ("In", False), ("Out", False), ("Late (min)", True)])


COUNTS = (("present", "Present"), ("late", "Late"), ("absent", "Absent"), ("half_day", "Half day"),
          ("on_leave", "On leave"), ("early_out", "Left early"))


def attendance_counts(actor, company_id, ask):
    visible, person, stop = _attendance_start(actor, company_id, ask)
    if stop:
        return stop
    first, last = ask.period
    with use_company(company_id):
        queryset = _days(company_id, visible, ask, person)
        found = list(queryset.order_by().values(
            "employee_id", first_name=F("employee__first_name"), middle_name=F("employee__middle_name"),
            last_name=F("employee__last_name"), public_id=F("employee__public_id")).annotate(
            code=Max("employee_assignment__employee_code"), branch=Max("branch__name"),
            days=Count("pk"), **{key: Count("pk", filter=STATUS_FILTER[key]) for key, _ in COUNTS}))
    for row in found:
        row["name"] = " ".join(part for part in (row["first_name"], row["middle_name"], row["last_name"]) if part)
    if ask.status:
        found = [row for row in found if row[ask.status]]
        found.sort(key=lambda row: (-row[ask.status], row["name"].casefold()))
    else:
        found.sort(key=lambda row: row["name"].casefold())
    rows = [[
        Cell(row["code"] or ""),
        Cell(row["name"], reverse("organization:employee_detail", args=[row["public_id"]])),
        Cell(row["branch"] or ""), number(row["days"]),
        *(number(row[key]) for key, _ in COUNTS),
    ] for row in found[:LIMIT]]
    label = PHRASE[ask.status].lower()
    if person is not None:
        count = found[0][ask.status] if found and ask.status else (found[0]["days"] if found else 0)
        answer = (f"{person.full_name}, {label} {span(first, last)}: {plural(count, 'day')}." if ask.status
                  else f"{person.full_name}, {span(first, last)}: {plural(count, 'day')} of attendance.")
    elif ask.status and found:
        top = found[0]
        answer = (f"{PHRASE[ask.status]} {span(first, last)}: {plural(len(found), 'person', 'people')}; "
                  f"most {top['name']} ({plural(top[ask.status], 'day')}).")
    elif ask.status:
        answer = f"{PHRASE[ask.status]} {span(first, last)}: no one."
    else:
        answer = f"Attendance {span(first, last)}: {plural(len(found), 'person', 'people')}."
    return Result(answer=answer, rows=rows, cut=max(0, len(found) - LIMIT), link=(
        "Open the Daily list", reverse("attendance:attendance_list") + "?" + _query(
            date_from=first.isoformat(), date_to=last.isoformat(), branch=ask.branch_id,
            employee=person.pk if person else None)), columns=[
        ("Employee ID", False), ("Name", False), ("Branch", False), ("Days", True),
        *((label, True) for _, label in COUNTS)])


def present_now(actor, company_id, ask):
    try:
        _membership, visible = access.now_scope(actor, company_id)
    except PermissionDenied:
        return Result(refused="Your login does not show who is in the office.")
    with use_company(company_id):
        candidates = people(visible)
        person, stop = _person(ask, candidates)
        if stop:
            return stop
        if person is not None:
            candidates = candidates.filter(pk=person.pk)
        if ask.branch_id:
            candidates = candidates.filter(table_branch_id=ask.branch_id)
        if ask.department:
            candidates = candidates.filter(table_department_id__in=Department.objects.filter(
                name__iexact=ask.department).values("pk"))
        everyone = {employee.pk: employee for employee in candidates.order_by("first_name", "last_name")}
    statuses = live_status.statuses_for(company_id, employee_ids=list(everyone)) if everyone else {}
    shown = [(employee, statuses[pk]) for pk, employee in everyone.items() if pk in statuses
             and (person is not None or statuses[pk].key in ("in_office", "on_break"))]
    rows = [[Cell(employee.table_code or ""), Cell(employee.full_name, _profile(employee)),
             Cell(employee.table_branch or ""), Cell(status.label), Cell(status.since_text)]
            for employee, status in shown]
    if person is not None:
        status = statuses.get(person.pk)
        answer = (f"{person.full_name}: {status.label.lower()}" + (f" since {status.since_text}" if status.since_text
                                                                  else "") + "." if status
                  else f"{person.full_name}: nothing today.")
    else:
        breaks = sum(1 for _, status in shown if status.key == "on_break")
        answer = (f"In the office now: {plural(len(shown), 'person', 'people')}"
                  + (f", {breaks} of them on a break" if breaks else "") + "." if shown
                  else "In the office now: no one.")
    return Result(answer=answer, rows=rows, link=("Open Employees", reverse("employee_list")), columns=[
        ("Employee ID", False), ("Name", False), ("Branch", False), ("Now", False), ("Since", False)])


# --- leave --------------------------------------------------------------------------

def _leave_start(actor, company_id, ask):
    from leaves.views import _list_scope

    try:
        _company_wide, scope, _record = _list_scope(actor, company_id)
    except PermissionDenied:
        return None, None, Result(refused="Your login does not show leave.")
    with use_company(company_id):
        person, stop = _person(ask, people(scope))
    return scope, person, stop


def leave_on(actor, company_id, ask):
    from leaves.models import LeaveDay
    from leaves.services import LIVE_LEAVE_DAYS

    scope, person, stop = _leave_start(actor, company_id, ask)
    if stop:
        return stop
    first, last = ask.period
    with use_company(company_id):
        queryset = access.scope(
            LeaveDay.objects.filter(status__in=LIVE_LEAVE_DAYS, work_date__gte=first, work_date__lte=last),
            scope, field="employee_assignment__branch", department_field="employee_assignment__department")
        if ask.branch_id:
            queryset = queryset.filter(employee_assignment__branch_id=ask.branch_id)
        queryset = _department(queryset, ask.department, "employee_assignment__department")
        if person is not None:
            queryset = queryset.filter(employee=person)
        found = {}
        for leave_day in queryset.select_related("employee", "employee_assignment__branch",
                                                 "request_segment__leave_type").order_by("work_date"):
            key = (leave_day.employee_id, leave_day.request_segment.leave_type_id)
            row = found.setdefault(key, {"day": leave_day, "first": leave_day.work_date, "units": Decimal("0")})
            row["last"] = leave_day.work_date
            row["units"] += leave_day.balance_units
    rows = []
    for row in sorted(found.values(), key=lambda row: (row["day"].employee.full_name.casefold(), row["first"])):
        leave_day = row["day"]
        rows.append([
            Cell(leave_day.employee_assignment.employee_code), Cell(leave_day.employee.full_name,
                                                                    _profile(leave_day.employee)),
            Cell(leave_day.employee_assignment.branch.name), Cell(leave_day.request_segment.leave_type.name),
            Cell(day(row["first"]), sort=row["first"].toordinal()), Cell(day(row["last"]), sort=row["last"].toordinal()),
            days_cell(row["units"])])
    how_many = len({employee_id for employee_id, _ in found})
    if person is not None:
        units = sum((row["units"] for row in found.values()), Decimal("0"))
        answer = (f"{person.full_name}, on leave {span(first, last)}: {units.normalize():f} "
                  f"day{'' if units == 1 else 's'}."
                  if found else f"{person.full_name} is not on leave {span(first, last)}.")
    else:
        answer = f"On leave {span(first, last)}: {plural(how_many, 'person', 'people') if how_many else 'no one'}."
    return Result(answer=answer, rows=rows, link=("Open the Leave list", reverse("leaves:leave_list") + "?" + _query(
        month=first.month, year=first.year)), columns=[
        ("Employee ID", False), ("Name", False), ("Branch", False), ("Leave type", False),
        ("From", False), ("To", False), ("Days", True)])


def leave_balance(actor, company_id, ask):
    from leaves import policies
    from leaves.models import LeavePolicy
    from leaves.policy_views import _allowances

    scope, person, stop = _leave_start(actor, company_id, ask)
    if stop:
        return stop
    year, today = ask.year, timezone.localdate()
    rows, by_person = [], defaultdict(list)
    with use_company(company_id):
        employees = people(scope).exclude(employment_status__in=LEFT)
        if person is not None:
            employees = employees.filter(pk=person.pk)
        if ask.branch_id:
            employees = employees.filter(table_branch_id=ask.branch_id)
        if ask.department:
            employees = employees.filter(table_department_id__in=Department.objects.filter(
                name__iexact=ask.department).values("pk"))
        employees = list(employees.order_by("first_name", "last_name")[:LIMIT])
        # The Leave balances page's own figures (leaves/policy_views.balance_list).
        if LeavePolicy.objects.exists():
            overviews = ((employee, policies.overview(employee, year, today)) for employee in employees)
        else:
            overviews = _allowances(employees, year)
        for employee, found in overviews:
            for row in found:
                by_person[employee.pk].append(row)
                rows.append([Cell(employee.table_code or ""), Cell(employee.full_name, _profile(employee) + "#leave"),
                             Cell(employee.table_branch or ""), Cell(row["leave_type"].name),
                             days_cell(row["given"]), days_cell(row["taken"]), days_cell(row["left"])])
    if person is not None:
        mine = by_person.get(person.pk, [])
        answer = (f"{person.full_name}, leave left in {year}: " + ", ".join(
            f"{row['leave_type'].name} {row['left'].normalize():f}" for row in mine) + "." if mine
                  else f"{person.full_name} has no leave balance in {year}.")
    else:
        answer = f"Leave balances in {year}: {plural(len(by_person), 'person', 'people')}."
    return Result(answer=answer, rows=rows, link=(
        "Open Leave balances", reverse("leaves:leave_balance_list") + f"?year={year}"), columns=[
        ("Employee ID", False), ("Name", False), ("Branch", False), ("Leave type", False),
        ("Given", True), ("Taken", True), ("Left", True)])


# --- overtime ------------------------------------------------------------------------

STATE_LABELS = {"automatic": "Approved (scanned out)", "approved": "Approved", "rejected": "Rejected",
                "waiting": "Waiting", "too_short": "Too short to pay"}


def _months(first, last):
    """``(month_first, start, end)`` for each month the dates touch."""
    start = first
    while start <= last:
        month_first = start.replace(day=1)
        following = (month_first + datetime.timedelta(days=32)).replace(day=1)
        end = min(last, following - datetime.timedelta(days=1))
        yield month_first, start, end
        start = following


def overtime(actor, company_id, ask):
    from payroll import overtime as rules_of_overtime
    from payroll.policy import rules_for
    from payroll.table_views import overtime_queryset

    try:
        scope = rules_of_overtime.overtime_scope(actor, company_id)
    except PermissionDenied:
        return Result(refused="Your login does not show overtime.")
    with use_company(company_id):
        reach = (Scope(ALL_BRANCHES) if scope.company_wide else
                 Scope(set(scope.branches.values_list("pk", flat=True)), set(scope.departments)))
        person, stop = _person(ask, people(reach))
    if stop:
        return stop
    first, last = ask.period
    states = {"approved": rules_of_overtime.APPROVED_STATES, "waiting": (rules_of_overtime.WAITING,)}.get(ask.overtime)
    records, paid = [], 0
    for month_first, start, end in _months(first, last):
        refresh(company_id, start=start, end=end)
        rules = rules_for(company_id, month_first)
        with use_company(company_id):
            queryset = overtime_queryset(scope.branches, start, end, rules, scope.departments)
            if ask.branch_id:
                queryset = queryset.filter(branch_id=ask.branch_id)
            queryset = _department(queryset, ask.department, "employee_assignment__department")
            if person is not None:
                queryset = queryset.filter(employee=person)
            if states:
                queryset = queryset.filter(table_state__in=states)
            found = list(queryset.select_related("employee_assignment")
                         .order_by("work_date", "employee__first_name", "employee__last_name"))
        records += found
        paid += sum(record.table_paid for record in found)
    rows = [[
        Cell(f"{record.work_date:%a} {day(record.work_date)}", _calendar(record.employee_id, record.work_date),
             sort=record.work_date.toordinal()),
        Cell(record.employee.full_name, _profile(record.employee)), Cell(record.branch.name),
        hours(record.table_minutes), Cell(STATE_LABELS.get(record.table_state, record.table_state)),
        hours(record.table_paid),
    ] for record in records[:LIMIT]]
    label = f"{OVERTIME[ask.overtime]} overtime" if ask.overtime else "Overtime"
    who = f"{person.full_name}, {label.lower()}" if person is not None else label
    paid_text = hours(paid)
    answer = (f"{who} {span(first, last)}: {plural(len(records), 'day')}, {paid_text} hours paid."
              if records else f"{who} {span(first, last)}: none.")
    return Result(answer=answer, rows=rows, cut=max(0, len(records) - LIMIT), link=(
        "Open Overtime", reverse("payroll:overtime_list") + "?" + _query(
            year=first.year, month=first.month, show=ask.overtime or None)), columns=[
        ("Date", False), ("Name", False), ("Branch", False), ("Overtime", True), ("State", False),
        ("Paid", True)])


# --- salary ---------------------------------------------------------------------------

NO_SALARY = "Your login does not show salaries."


def _monthly_run(company_id, year, month):
    from payroll import periods
    from payroll.models import PayrollRun

    first, last = periods.bounds(company_id, year, month)
    return PayrollRun.objects.select_related("payroll_period").filter(
        payroll_period__start_date=first, payroll_period__end_date=last, pay_group=None).order_by("-pk").first()


def salary_of(actor, company_id, ask):
    from payroll.models import PayrollLine, PayrollRun
    from payroll.services import record_branch_id, salary_month_branches
    from payroll.views import payslip_records

    _membership, view = salary_month_branches(actor, company_id)
    if not view:
        return Result(refused=NO_SALARY)
    if not ask.employee and not ask.employee_id:
        return Result(answer="Whose salary? Add a name, for example “salary of Rahim this month”.")
    month_name = ask.month_name
    with use_company(company_id):
        person, stop = _person(ask, people(view))
        if stop:
            return stop
        monthly = _monthly_run(company_id, ask.year, ask.month)
        records = list(payslip_records().filter(payroll_run=monthly, employee=person)) if monthly else []
        # A weekly pay group's weeks that end in the month: the latest run of each.
        latest = {}
        for run in (PayrollRun.objects.filter(pay_group__isnull=False, payroll_period__end_date__year=ask.year,
                                              payroll_period__end_date__month=ask.month).order_by("-pk")):
            latest.setdefault((run.payroll_period_id, run.pay_group_id), run.pk)
        records += list(payslip_records().select_related("payroll_run__pay_group").filter(
            payroll_run_id__in=latest.values(), employee=person)
            .order_by("payroll_run__payroll_period__start_date"))
        if view is not ALL_BRANCHES:
            records = [record for record in records if record_branch_id(record) in view]
        penalties = dict(PayrollLine.objects.filter(payroll_record__in=records, source_type="penalty")
                         .values("payroll_record").annotate(total=Sum("amount"))
                         .values_list("payroll_record", "total"))
    if not records:
        answer = (f"{person.full_name} has no payslip for {month_name}." if monthly or latest
                  else f"Salary for {month_name} has not been generated yet.")
        return Result(answer=answer, link=("Open Salary", reverse("payroll:payroll_home") + "?" + _query(
            month=ask.month, year=ask.year)))
    rows = []
    for record in records:
        period = record.payroll_run.payroll_period
        group = record.payroll_run.pay_group
        name = (f"{group.name}: {day(period.start_date)} – {day(period.end_date)}" if group else month_name)
        placement = record.employee_assignment_at_period_end
        rows.append([
            Cell(name, reverse("payroll:payslip", args=[record.pk]), sort=period.start_date.toordinal()),
            Cell(placement.employee_code if placement else ""), Cell(record.employee.full_name, _profile(record.employee)),
            Cell(record.payroll_run.get_status_display()), money(record.gross_earnings),
            money(record.total_deductions), money(penalties.get(record.pk)), money(record.net_pay)])
    if len(records) == 1:
        record = records[0]
        penalty = penalties.get(record.pk)
        answer = (f"{person.full_name}, salary for {month_name}: net {record.currency} {record.net_pay:,.2f} "
                  f"(gross {record.gross_earnings:,.2f}, deductions {record.total_deductions:,.2f}"
                  + (f", of which penalties {penalty:,.2f}" if penalty else "")
                  + f"). {record.payroll_run.get_status_display()}.")
    else:
        net = sum(record.net_pay for record in records)
        answer = (f"{person.full_name}, salary for {month_name}: {plural(len(records), 'payslip')}, "
                  f"net {records[0].currency} {net:,.2f} in all.")
    return Result(answer=answer, rows=rows, link=("Open Salary", reverse("payroll:payroll_home") + "?" + _query(
        month=ask.month, year=ask.year)), columns=[
        ("Payslip", False), ("Employee ID", False), ("Name", False), ("Status", False), ("Gross", True),
        ("Deductions", True), ("Penalties", True), ("Net pay", True)])


def salary_totals(actor, company_id, ask):
    from payroll.services import salary_month_branches
    from payroll.views import _by_department

    _membership, view = salary_month_branches(actor, company_id)
    if not view:
        return Result(refused=NO_SALARY)
    link = ("Open Salary", reverse("payroll:payroll_home") + "?" + _query(
        month=ask.month, year=ask.year, view="departments"))
    with use_company(company_id):
        run = _monthly_run(company_id, ask.year, ask.month)
        if run is None:
            return Result(answer=f"Salary for {ask.month_name} has not been generated yet.", link=link)
        # The Salary page's own selection (payroll.views.payroll_home).
        shown = run.records.all()
        if view is not ALL_BRANCHES:
            shown = shown.filter(employee_assignment_at_period_end__branch_id__in=view)
        if ask.branch_id:
            shown = shown.filter(employee_assignment_at_period_end__branch_id=ask.branch_id)
        shown = _department(shown, ask.department, "employee_assignment_at_period_end__department")
        totals = shown.aggregate(people=Count("pk"), gross=Sum("gross_earnings"),
                                 deductions=Sum("total_deductions"), net=Sum("net_pay"))
        currency = shown.values_list("currency", flat=True).first() or ""
        if ask.group_by == "branch":
            groups = [{"name": row["employee_assignment_at_period_end__branch__name"] or "No branch", "branch": "",
                       "people": row["people"], "gross": row["gross"], "deductions": row["deductions"],
                       "net": row["net"]}
                      for row in shown.values("employee_assignment_at_period_end__branch__name").annotate(
                          people=Count("pk"), gross=Sum("gross_earnings"), deductions=Sum("total_deductions"),
                          net=Sum("net_pay")).order_by("employee_assignment_at_period_end__branch__name")]
        else:
            groups = _by_department(shown)
    by_branch = ask.group_by == "branch"
    rows = [[Cell(group["name"]), *([] if by_branch else [Cell(group["branch"])]), number(group["people"]),
             money(group["gross"]), money(group["deductions"]), money(group["net"])] for group in groups]
    answer = (f"Salary for {ask.month_name} (monthly staff, {run.get_status_display().lower()}): "
              f"{plural(totals['people'], 'person', 'people')}, net {currency} {(totals['net'] or 0):,.2f} in total.")
    return Result(answer=answer, rows=rows, link=link, columns=[
        ("Branch" if by_branch else "Department", False), *([] if by_branch else [("Branch", False)]),
        ("People", True), ("Gross", True), ("Deductions", True), ("Net pay", True)])


SEARCHES = {
    "attendance_days": attendance_days, "attendance_counts": attendance_counts, "present_now": present_now,
    "leave_on": leave_on, "leave_balance": leave_balance, "overtime": overtime, "salary_of": salary_of,
    "salary_totals": salary_totals,
}


def run(actor, company_id, ask):
    return SEARCHES[ask.search](actor, company_id, ask)

