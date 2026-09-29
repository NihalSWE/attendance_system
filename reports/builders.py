"""The reports themselves: each turns a period and filters into columns, rows
and a summary. The page and the Excel/PDF downloads show exactly this.

Everything is read from what the system already records - the attendance
days (``AttendanceRecord``), approved and requested leave, overtime decisions
and the raw scans (``PunchEvent``) - so a report never disagrees with the Daily
list, the Leave list or the Overtime page. Late minutes are already net of
the shift's grace minutes, as on the Daily list.
"""

import datetime
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from django.db.models import Q
from django.utils import timezone

from attendance import access
from attendance.models import AttendanceRecord
from attendance.services import refresh
from base_template.tables import Sortable
from leaves.shape import pay_label

S = AttendanceRecord.AttendanceStatus
WORKING = (S.PRESENT, S.HALF_DAY, S.INCOMPLETE)

#: One letter or two per day on the weekly and monthly grids.
CODES = {S.PRESENT: "P", S.HALF_DAY: "HD", S.ABSENT: "A", S.LEAVE: "LV",
         S.HOLIDAY: "H", S.WEEKLY_OFF: "W", S.INCOMPLETE: "IN", S.INACTIVE: "IA"}
LEGEND = ("P present · LT present, came late · HD half day · A absent · LV leave · "
          "H holiday · W weekly off · IN incomplete (no check-out) · IA inactive · "
          "blank: no record")


@dataclass
class Column:
    label: str
    numeric: bool = False
    weight: float = 1


@dataclass
class Result:
    columns: list
    rows: list
    summary: list = field(default_factory=list)
    legend: str = ""
    note: str = ""
    grid: bool = False       # a day-by-day grid: compact type in the PDF
    empty: str = "Nothing for this period and these filters."


@dataclass
class Context:
    company_id: int
    company: object
    zone: object
    scope: object
    filters: object
    include_hidden: bool = False   # the profile: a hidden person's own summary


# --- small helpers ----------------------------------------------------------


def hm(minutes):
    """``485`` -> ``"8:05"``: hours and minutes, as a timesheet shows them."""
    minutes = int(minutes or 0)
    sign = "-" if minutes < 0 else ""
    whole = abs(minutes)
    # Sorted by the minutes, not the text: 10:00 after 9:59.
    return Sortable(f"{sign}{whole // 60}:{whole % 60:02d}", minutes)


def when(day, pattern="%a %d %b"):
    """A date as the reports print it, sorted as a date."""
    return Sortable(f"{day:{pattern}}", day.toordinal())


def _days(number):
    """``Decimal("1.00")`` -> ``"1"``, ``Decimal("0.50")`` -> ``"0.5"``."""
    text = format(number.normalize(), "f") if hasattr(number, "normalize") else str(number)
    return Sortable(text, float(number))


def clock(moment, zone):
    return timezone.localtime(moment, zone).strftime("%H:%M") if moment else ""


def code_of(record):
    if record is None:
        return ""
    if record.attendance_status == S.PRESENT and record.late_minutes:
        return "LT"
    return CODES.get(record.attendance_status, "?")


def expected_minutes(record):
    """The work the day's shift expects - the same rule salary uses."""
    from payroll.services import expected_minutes as payroll_expected

    return payroll_expected(record)


def employee_id(record):
    assignment = record.employee_assignment
    return assignment.employee_code if assignment else ""


def department(record):
    assignment = record.employee_assignment
    return assignment.department.name if assignment and assignment.department_id else ""


def _branch(record):
    return record.branch.name if record.branch_id else ""


def records(ctx, **where):
    """The attendance days in the period and filters, brought up to date first."""
    f = ctx.filters
    today = timezone.localdate()
    if f.first <= today:
        # Live, as the Daily list: days close and scans arrive all the time.
        refresh(ctx.company_id, start=f.first, end=min(f.last, today))
    queryset = access.scope(
        AttendanceRecord.objects.select_related(
            "employee", "branch", "shift", "employee_assignment__department",
        ).filter(work_date__gte=f.first, work_date__lte=f.last, **where),
        ctx.scope,
    )
    if not ctx.include_hidden:
        # "Hide from reports" on the profile: their days still count, just not
        # in the reports. Their own profile calls these builders with it on.
        queryset = queryset.exclude(employee__hide_from_reports=True)
    if f.branch:
        queryset = queryset.filter(branch_id=int(f.branch))
    if f.department:
        queryset = queryset.filter(employee_assignment__department_id=int(f.department))
    if f.employee:
        queryset = queryset.filter(employee_id=int(f.employee))
    return queryset.order_by("employee__first_name", "employee__last_name",
                             "employee_id", "work_date")


def by_employee(rows):
    """``[(first record, [records])]`` per employee, in name order."""
    grouped = defaultdict(list)
    for record in rows:
        grouped[record.employee_id].append(record)
    return [(days[0], days) for days in grouped.values()]


def totals(days):
    """What a run of days adds up to, for the summary columns."""
    statuses = Counter(record.attendance_status for record in days)
    working = [record for record in days if record.attendance_status in WORKING]
    return {
        "present": statuses[S.PRESENT] + statuses[S.INCOMPLETE],
        "half_day": statuses[S.HALF_DAY],
        "absent": statuses[S.ABSENT],
        "leave": statuses[S.LEAVE],
        "off": statuses[S.HOLIDAY] + statuses[S.WEEKLY_OFF],
        "late_days": sum(1 for record in working if record.late_minutes),
        "late_minutes": sum(record.late_minutes for record in working),
        "worked": sum(record.worked_minutes for record in working),
        "expected": sum(expected_minutes(record) for record in working),
        "overtime": sum(record.calculated_overtime_minutes for record in days),
        "days_worked": len(working),
    }


def status_summary(days):
    statuses = Counter(record.attendance_status for record in days)
    late = sum(1 for record in days if record.attendance_status in WORKING and record.late_minutes)
    return [("Days", len(days)), ("Present", statuses[S.PRESENT] + statuses[S.INCOMPLETE]),
            ("Late", late), ("Half day", statuses[S.HALF_DAY]), ("Absent", statuses[S.ABSENT]),
            ("Leave", statuses[S.LEAVE]),
            ("Holiday / weekly off", statuses[S.HOLIDAY] + statuses[S.WEEKLY_OFF])]


PERSON = [Column("Employee ID", weight=1.1), Column("Name", weight=2)]
PLACE = [Column("Branch", weight=1.4), Column("Department", weight=1.4)]


def person(record):
    return [employee_id(record), record.employee.full_name]


# --- attendance ---------------------------------------------------------------


def _day_row(ctx, record, with_date=False):
    return ([when(record.work_date)] if with_date else []) + person(record) + [
        _branch(record), department(record), record.get_attendance_status_display(),
        clock(record.first_in_at, ctx.zone), clock(record.last_out_at, ctx.zone),
        hm(record.worked_minutes), record.late_minutes or 0,
        record.early_out_minutes or 0, hm(record.calculated_overtime_minutes),
    ]


def _day_columns(with_date=False):
    return ([Column("Date", weight=1.1)] if with_date else []) + PERSON + PLACE + [
        Column("Status", weight=1.1), Column("In", True), Column("Out", True),
        Column("Worked", True), Column("Late (min)", True), Column("Early out (min)", True),
        Column("Overtime", True),
    ]


def daily_attendance(ctx):
    days = list(records(ctx))
    return Result(columns=_day_columns(), rows=[_day_row(ctx, r) for r in days],
                  summary=status_summary(days))


def _grid(ctx):
    f = ctx.filters
    days = list(records(ctx))
    columns = PERSON + [Column(f"{day:%a}"[:2] + f" {day.day}", True, 0.55) for day in f.days] + [
        Column("Present", True, 0.8), Column("Late", True, 0.7), Column("Half", True, 0.7),
        Column("Absent", True, 0.8), Column("Leave", True, 0.8), Column("Off", True, 0.7),
        Column("Worked", True, 0.9),
    ]
    rows = []
    for first, own in by_employee(days):
        on = {record.work_date: record for record in own}
        t = totals(own)
        rows.append(person(first) + [code_of(on.get(day)) for day in f.days] + [
            t["present"], t["late_days"], t["half_day"], t["absent"], t["leave"], t["off"],
            hm(t["worked"])])
    return Result(columns=columns, rows=rows, summary=status_summary(days), legend=LEGEND,
                  grid=True)


weekly_attendance = _grid
monthly_attendance = _grid


def custom_attendance(ctx):
    """Any range; every day, or one line per person. Optionally one status."""
    status = ctx.filters.extra.get("status", "")
    where = {"attendance_status": status} if status in S.values else {}
    days = list(records(ctx, **where))
    if ctx.filters.extra.get("view") == "detail":
        return Result(columns=_day_columns(with_date=True),
                      rows=[_day_row(ctx, r, with_date=True) for r in days],
                      summary=status_summary(days))
    rows = []
    for first, own in by_employee(days):
        t = totals(own)
        rows.append(person(first) + [_branch(first), department(first), len(own),
                    t["present"], t["late_days"], t["half_day"], t["absent"], t["leave"],
                    t["off"], hm(t["worked"]), t["late_minutes"], hm(t["overtime"])])
    return Result(
        columns=PERSON + PLACE + [
            Column("Days", True, 0.7), Column("Present", True, 0.8), Column("Late", True, 0.7),
            Column("Half day", True, 0.8), Column("Absent", True, 0.8), Column("Leave", True, 0.8),
            Column("Holiday / off", True, 0.9), Column("Worked", True, 0.9),
            Column("Late (min)", True, 0.9), Column("Overtime", True, 0.9)],
        rows=rows, summary=status_summary(days))


# --- absent and late ------------------------------------------------------------


def daily_absent(ctx):
    all_days = list(records(ctx))
    absent = [r for r in all_days if r.attendance_status == S.ABSENT]
    rows = [person(r) + [_branch(r), department(r), r.shift.name if r.shift_id else ""]
            for r in absent]
    return Result(columns=PERSON + PLACE + [Column("Shift", weight=1.3)], rows=rows,
                  summary=[("Absent", len(absent)), ("Out of", len(all_days))],
                  empty="Nobody was absent that day.")


def monthly_absent(ctx):
    absent = list(records(ctx, attendance_status=S.ABSENT))
    rows = [person(first) + [_branch(first), department(first), len(own),
            ", ".join(f"{r.work_date.day}" for r in own)]
            for first, own in by_employee(absent)]
    rows.sort(key=lambda row: -row[4])
    return Result(columns=PERSON + PLACE + [Column("Days absent", True, 0.9),
                                            Column("Dates", weight=3)],
                  rows=rows, summary=[("Absent days", len(absent)), ("People", len(rows))],
                  note="Dates are days of the month.", empty="Nobody was absent this month.")


def _late(ctx):
    return [r for r in records(ctx, late_minutes__gt=0) if r.attendance_status in WORKING]


def daily_late(ctx):
    late = _late(ctx)
    rows = [person(r) + [_branch(r), department(r), clock(r.scheduled_start_at, ctx.zone),
            clock(r.first_in_at, ctx.zone), r.late_minutes] for r in late]
    rows.sort(key=lambda row: -row[-1])
    return Result(columns=PERSON + PLACE + [Column("Shift starts", True), Column("Came in", True),
                                            Column("Late (min)", True)],
                  rows=rows, summary=[("Late", len(late)),
                                      ("Minutes late", sum(r.late_minutes for r in late))],
                  note="Late minutes are after the shift's grace minutes, as on the Daily list.",
                  empty="Nobody was late that day.")


def monthly_late(ctx):
    late = _late(ctx)
    rows = []
    for first, own in by_employee(late):
        minutes = sum(r.late_minutes for r in own)
        rows.append(person(first) + [_branch(first), department(first), len(own), minutes,
                    round(minutes / len(own)), ", ".join(f"{r.work_date.day}" for r in own)])
    rows.sort(key=lambda row: (-row[4], -row[5]))
    return Result(columns=PERSON + PLACE + [
        Column("Days late", True, 0.8), Column("Minutes late", True, 0.9),
        Column("Average (min)", True, 0.9), Column("Dates", weight=2.4)],
        rows=rows, summary=[("Late days", len(late)), ("People", len(rows)),
                            ("Minutes late", sum(r.late_minutes for r in late))],
        note="Dates are days of the month. Late minutes are after the shift's grace minutes.",
        empty="Nobody was late this month.")


# --- hours and overtime -----------------------------------------------------------


def working_hours(ctx):
    days = list(records(ctx))
    rows, all_worked, all_expected = [], 0, 0
    for first, own in by_employee(days):
        t = totals(own)
        if not t["days_worked"]:
            continue
        all_worked, all_expected = all_worked + t["worked"], all_expected + t["expected"]
        rows.append(person(first) + [_branch(first), department(first), t["days_worked"],
                    hm(t["expected"]), hm(t["worked"]), hm(t["worked"] - t["expected"]),
                    hm(t["worked"] // t["days_worked"]), hm(t["overtime"])])
    return Result(columns=PERSON + PLACE + [
        Column("Days worked", True, 0.9), Column("Shift hours", True), Column("Worked", True),
        Column("Difference", True), Column("Average a day", True), Column("Overtime", True)],
        rows=rows, summary=[("People", len(rows)), ("Worked", hm(all_worked)),
                            ("Shift hours", hm(all_expected)),
                            ("Difference", hm(all_worked - all_expected))],
        note="Shift hours: each worked day's shift, less an unpaid break - as salary counts "
             "it. Worked: time in the office, breaks out excluded.",
        empty="Nobody worked in this period.")


def short_hours(ctx):
    """Days someone came in but worked less than their shift, leave aside."""
    rows, short = [], 0
    for r in records(ctx, attendance_status__in=WORKING, leave_day__isnull=True):
        expected = expected_minutes(r)
        if expected and r.worked_minutes < expected:
            short += expected - r.worked_minutes
            rows.append([when(r.work_date)] + person(r) + [
                _branch(r), r.get_attendance_status_display(), clock(r.first_in_at, ctx.zone),
                clock(r.last_out_at, ctx.zone), hm(expected), hm(r.worked_minutes),
                hm(expected - r.worked_minutes)])
    return Result(columns=[Column("Date", weight=1.1)] + PERSON + [
        Column("Branch", weight=1.3), Column("Status", weight=1.1), Column("In", True),
        Column("Out", True), Column("Shift hours", True), Column("Worked", True),
        Column("Short by", True)],
        rows=rows, summary=[("Short days", len(rows)), ("Short by, in all", hm(short))],
        note="Days with approved leave (a half day, say) are left out: that time was granted.",
        empty="Nobody worked less than their shift in this period.")


def overtime(ctx):
    """The Overtime page's days, read the Overtime page's way (payroll.overtime):
    time after the shift, work on a day off, and sessions nobody scanned out
    of - each with its state and what salary pays for it."""
    from payroll import overtime as ot
    from payroll.models import OvertimeDecision
    from payroll.policy import rules_for

    labels = {ot.AUTOMATIC: "Approved automatically",
              OvertimeDecision.Status.APPROVED: "Approved",
              OvertimeDecision.Status.REJECTED: "Rejected",
              ot.WAITING: "Waiting for a decision", ot.TOO_SHORT: "Too short to pay"}
    days = list(records(ctx).filter(is_open=False).filter(ot.CANDIDATES)
                .prefetch_related("sessions").distinct())
    decisions = {
        (d.employee_id, d.work_date): d
        for d in OvertimeDecision.objects.filter(
            work_date__gte=ctx.filters.first, work_date__lte=ctx.filters.last,
            employee_id__in={r.employee_id for r in days})
    } if days else {}
    rules_by_month, rows, totals_ = {}, [], Counter()
    for r in days:
        claim = ot.claim_for(r)
        if not claim.exists:
            continue
        month = r.work_date.replace(day=1)
        if month not in rules_by_month:
            rules_by_month[month] = rules_for(ctx.company_id, month)
        rules = rules_by_month[month]
        decision = decisions.get((r.employee_id, r.work_date))
        row = ot.Row(record=r, claim=claim, decision=decision,
                     state=ot.state_of(claim, decision, rules))
        paid = rules.payable_overtime(row.approved_minutes)
        totals_.update(minutes=claim.minutes, approved=row.approved_minutes, paid=paid)
        if row.state == ot.WAITING:
            totals_["waiting"] += 1
        rows.append([when(r.work_date)] + person(r) + [
            _branch(r), r.get_attendance_status_display(),
            clock(r.scheduled_end_at, ctx.zone) if claim.overtime_from else "Day off",
            clock(r.last_out_at, ctx.zone) if claim.open_from is None else "Not scanned out",
            hm(claim.minutes), hm(row.approved_minutes), hm(paid), labels.get(row.state, row.state)])
    return Result(columns=[Column("Date", weight=1.1)] + PERSON + [
        Column("Branch", weight=1.3), Column("Status", weight=1), Column("Shift ends", True),
        Column("Left", True), Column("Overtime", True), Column("Approved", True),
        Column("Paid", True), Column("Decision", weight=1.5)],
        rows=rows, summary=[("Days", len(rows)), ("Overtime", hm(totals_["minutes"])),
                            ("Approved", hm(totals_["approved"])), ("Paid", hm(totals_["paid"])),
                            ("Waiting", totals_["waiting"])],
        note="As on the Overtime page: time after the shift ends, and every minute worked on "
             "a day off. Scanning out approves it automatically; a day nobody scanned out of "
             "waits for a decision. Paid is what salary pays after the company's minimum and "
             "rounding.",
        empty="No overtime in this period.")


# --- leave ---------------------------------------------------------------------


def leave(ctx):
    from leaves.models import LeaveRequest, LeaveRequestSegment

    from django.db.models import Sum

    from leaves.services import LIVE_LEAVE_DAYS

    f = ctx.filters
    status = f.extra.get("leave_status") or LeaveRequest.Status.APPROVED
    cancelled = LeaveRequestSegment.Status.CANCELLED
    segments = LeaveRequestSegment.objects.select_related(
        "leave_type", "leave_request__employee",
        "leave_request__submission_assignment__branch",
        "leave_request__submission_assignment__department",
    ).filter(start_date__lte=f.last, end_date__gte=f.first).filter(
        # A changed leave keeps its old part, cancelled: list the current one.
        # A leave cancelled whole is still listed as what it was.
        ~Q(status=cancelled) | Q(leave_request__status=LeaveRequest.Status.CANCELLED),
    ).annotate(
        # The days that still count, once some were cancelled.
        live_units=Sum("days__balance_units", filter=Q(days__status__in=LIVE_LEAVE_DAYS)),
    )
    if not ctx.include_hidden:
        segments = segments.exclude(leave_request__employee__hide_from_reports=True)
    if status == LeaveRequest.Status.APPROVED:
        # Partly cancelled leave is approved leave with fewer days.
        segments = segments.filter(leave_request__status__in=(
            LeaveRequest.Status.APPROVED, LeaveRequest.Status.PARTIALLY_CANCELLED))
    elif status != "all":
        segments = segments.filter(leave_request__status=status)
    segments = access.scope(
        segments, ctx.scope, field="leave_request__submission_assignment__branch",
        department_field="leave_request__submission_assignment__department")
    if f.branch:
        segments = segments.filter(leave_request__submission_assignment__branch_id=int(f.branch))
    if f.department:
        segments = segments.filter(
            leave_request__submission_assignment__department_id=int(f.department))
    if f.employee:
        segments = segments.filter(leave_request__employee_id=int(f.employee))
    if f.extra.get("leave_type", "").isdigit():
        segments = segments.filter(leave_type_id=int(f.extra["leave_type"]))
    segments = segments.order_by("leave_request__employee__first_name", "start_date")

    rows, by_type = [], Counter()
    for s in segments:
        request = s.leave_request
        placed = request.submission_assignment
        counted = request.status in (LeaveRequest.Status.APPROVED,
                                     LeaveRequest.Status.PARTIALLY_CANCELLED)
        units = (s.live_units or 0) if counted else s.requested_units
        by_type[s.leave_type.name] += units
        rows.append([
            placed.employee_code if placed else "", request.employee.full_name,
            placed.branch.name if placed else "", s.leave_type.name,
            when(s.start_date, "%d %b %Y"), when(s.end_date, "%d %b %Y"),
            _days(units), pay_label(s.requested_pay_type, s.requested_pay_percentage or 0),
            request.get_status_display(), request.reason or "",
        ])
    return Result(columns=PERSON + [
        Column("Branch", weight=1.3), Column("Leave type", weight=1.2), Column("From", True),
        Column("To", True), Column("Days", True, 0.6), Column("Pay", weight=0.8),
        Column("Status", weight=1), Column("Reason", weight=2.2)],
        rows=rows,
        summary=[("Requests", len(rows))] + [
            (name, f"{_days(days)} day{'' if days == 1 else 's'}")
            for name, days in sorted(by_type.items())],
        note="Every leave that touches the period is listed whole, with all its days.",
        empty="No leave in this period.")


# --- entry logs ------------------------------------------------------------------


def entry_logs(ctx):
    """Every scan the terminals sent in the period, as they sent it."""
    from devices.models import PunchEvent
    from organization.access_services import people

    f = ctx.filters
    begin = datetime.datetime.combine(f.first, datetime.time.min, tzinfo=ctx.zone)
    end = datetime.datetime.combine(f.last + datetime.timedelta(days=1), datetime.time.min,
                                    tzinfo=ctx.zone)
    punches = PunchEvent.objects.select_related("employee", "device", "branch").filter(
        punched_at_utc__gte=begin, punched_at_utc__lt=end)
    if not ctx.include_hidden:
        punches = punches.exclude(employee__hide_from_reports=True)
    if not ctx.scope.is_all:
        # A branch login: scans in its branches; a department head: its people's.
        mine = Q(branch_id__in=ctx.scope.branches) if ctx.scope.branches else Q(pk__in=[])
        if ctx.scope.departments:
            mine |= Q(employee_id__in=people(ctx.scope).values("pk"))
        punches = punches.filter(mine)
    if f.branch:
        punches = punches.filter(branch_id=int(f.branch))
    if f.employee:
        punches = punches.filter(employee_id=int(f.employee))
    if f.department:
        punches = punches.filter(employee__assignments__department_id=int(f.department)).distinct()
    rows, counted = [], 0
    A, D = PunchEvent.AuthorizationStatus, PunchEvent.DedupeStatus
    for p in punches.order_by("punched_at_utc", "pk"):
        local = timezone.localtime(p.punched_at_utc, ctx.zone)
        if p.dedupe_status != D.UNIQUE:
            state = "Duplicate - not counted"
        elif p.authorization_status == A.AUTHORIZED:
            state, counted = "Counted", counted + 1
        else:
            state = f"Not counted: {p.get_authorization_status_display()}"
        rows.append([when(local.date()), f"{local:%H:%M:%S}", p.device_user_id,
                     p.employee.full_name if p.employee_id else "Not linked to anyone",
                     p.device.name if p.device_id else "", p.branch.name if p.branch_id else "",
                     p.get_verification_method_display(), state])
    return Result(columns=[
        Column("Date", weight=1.1), Column("Time", True), Column("Employee ID", weight=1.1),
        Column("Name", weight=2), Column("Device", weight=1.4), Column("Branch", weight=1.3),
        Column("Method", weight=1), Column("Counted?", weight=2)],
        rows=rows, summary=[("Scans", len(rows)), ("Counted", counted),
                            ("Not counted", len(rows) - counted)],
        note="Times are in the company's time zone. Employee ID is the number the terminal "
             "sent.",
        empty="No scans in this period.")
