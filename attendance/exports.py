"""Attendance downloads: the Daily list (Excel, PDF) and the calendar (PDF).

Both are served by their page's own view with ``?format=``, from the page's
own query, so a download holds exactly what the page shows to this viewer:
the same branch/department scoping (A12 part 7), the same month or date
window, branch, employee and status filters, and - for the Daily list - the
same table search and sort (carried by base_template/js/export_links.js).

The calendar is PDF only: a month grid is not a table, and forcing it into a
spreadsheet would lose the thing it is. It is laid out as the page is - one
person's month, Monday first, with the summary strip - and the page is not
changed to suit it.
"""

from django.contrib import messages
from django.shortcuts import redirect
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES
from attendance.models import AttendanceRecord
from base_template.tables import table_queryset
from common import exports
from common.tenant import use_company
from organization.models import Branch

HEADERS = ["Date", "Employee ID", "Employee", "Branch", "Status", "In", "Out",
           "Worked (min)", "Late (min)", "Payable", "Note"]
#: The on-screen columns, by the index the table's sort uses.
SCREEN_HEADERS = ["Date", "Employee", "Branch", "Status", "In", "Out",
                  "Worked (min)", "Late (min)", "Payable", "Note"]


def _period(first, last, window):
    """``2026-09`` for a month, ``2026-09-21`` for a day, else ``from-to``."""
    if window is None:
        return f"{first:%Y-%m}"
    if first == last:
        return first.isoformat()
    return f"{first.isoformat()}-to-{last.isoformat()}"


def _period_text(first, last, window):
    if window is None:
        return f"{first:%B %Y}"
    if first == last:
        return f"{first:%d %b %Y}"
    return f"{first:%d %b %Y} – {last:%d %b %Y}"


def _scope_name(visible, branch):
    """The branch a download is named after: the chosen one, or the viewer's
    only one; otherwise the company (empty string)."""
    if branch is not None:
        return branch.name
    branches = getattr(visible, "branches", visible)
    if branches is not ALL_BRANCHES and len(branches) == 1 and not getattr(visible, "departments", None):
        return Branch.objects.filter(pk__in=branches).values_list("name", flat=True).first() or ""
    return ""


def _clock(moment, zone):
    return timezone.localtime(moment, zone).strftime("%H:%M") if moment else ""


def export_daily_list(request, company_id, daily, fmt):
    import zoneinfo

    from attendance.views import DAILY_ORDER, DAILY_SEARCH

    membership = daily["membership"]
    company = membership.company
    zone = zoneinfo.ZoneInfo(company.timezone or "UTC")
    first, last, window = daily["first"], daily["last"], daily["date_window"]

    with use_company(company_id):
        queryset, table_search, sorted_by = table_queryset(
            request, daily["queryset"], search=DAILY_SEARCH, order=DAILY_ORDER)
        count = queryset.count()
        try:
            exports.check_size(count, fmt, noun="attendance rows")
        except exports.TooManyRows as refusal:
            messages.error(request, str(refusal))
            params = request.GET.copy()
            params.pop("format", None)
            return redirect(f"{request.path}?{params.urlencode()}")

        branch = (Branch.objects.filter(pk=int(daily["branch_id"])).first()
                  if daily["branch_id"].isdigit() else None)
        employee = None
        if daily["employee_id"].isdigit():
            from employees.models import Employee

            employee = Employee.objects.filter(pk=int(daily["employee_id"])).first()
        scope_name = _scope_name(daily["visible"], branch)

        rows = []
        for record in queryset:
            assignment = record.employee_assignment
            rows.append([
                record.work_date,
                assignment.employee_code if assignment else "",
                record.employee.full_name,
                record.branch.name if record.branch_id else "",
                record.get_attendance_status_display(),
                _clock(record.first_in_at, zone),
                _clock(record.last_out_at, zone),
                record.worked_minutes or 0,
                record.late_minutes or 0,
                float(record.payable_fraction or 0),
                record.note or "",
            ])

    statuses = dict(AttendanceRecord.AttendanceStatus.choices)
    sort = ", ".join(
        f"{SCREEN_HEADERS[column]} {'descending' if descending else 'ascending'}"
        for column, descending in sorted_by if column < len(SCREEN_HEADERS)
    ) or "Date, then employee"
    shown = [f"Period: {_period_text(first, last, window)}",
             f"Branch: {branch.name if branch else (scope_name or 'All branches')}"]
    if employee is not None:
        shown.append(f"Employee: {employee.full_name}")
    if daily["status"] in statuses:
        shown.append(f"Status: {statuses[daily['status']]}")
    if table_search:
        shown.append(f'Table search: "{table_search}"')
    shown.append(f"Sorted by: {sort}")
    now = timezone.localtime()
    lines = [" · ".join(shown),
             f"{count} row{'s' if count != 1 else ''} · downloaded {now:%d %b %Y %H:%M} "
             f"by {request.user.get_username()} · times in {company.timezone or 'UTC'}"]
    title = f"{company.name} — Attendance"
    numeric = (7, 8, 9)

    if fmt == exports.XLSX:
        content = exports.table_xlsx(title=title, lines=lines, headers=HEADERS, rows=rows,
                                     numeric=numeric, widths={0: 13, 2: 26, 3: 18, 10: 30})
    else:
        content = exports.table_pdf(title=title, lines=lines, headers=HEADERS, rows=rows,
                                    numeric=numeric,
                                    widths={0: 1.1, 2: 1.8, 3: 1.3, 4: 1.1, 10: 2})

    filters = {"period": [first.isoformat(), last.isoformat()],
               "branch": daily["branch_id"], "employee": daily["employee_id"],
               "status": daily["status"], "table_search": table_search, "sort": sort}
    exports.record(actor=request.user, membership=membership, page="attendance",
                   fmt=fmt, filters=filters, count=count)
    name = exports.filename("attendance", scope_name or company.name,
                            _period(first, last, window), fmt)
    return exports.response(content, fmt, name)


def _cell(day):
    """What one calendar square says, line by line - as the page's square does."""
    if getattr(day, "is_filler", False):
        return ""
    lines = [str(day.number)]
    if day.has_record:
        lines.append(day.status_label)
        if day.check_in:
            lines.append(f"{day.check_in} – {day.check_out or '…'}")
            lines.append(f"In office {day.in_office}")
        if day.is_late:
            lines.append(f"Late {day.record.late_minutes} min")
        if day.note:
            lines.append(day.note)
    return "\n".join(lines)


def export_calendar(request, membership, employee, calendar, year, month):
    """One person's month as a PDF, the grid the page draws."""
    company = membership.company
    assignment_code = ""
    with use_company(company.pk):
        current = (employee.assignments.exclude(status="cancelled")
                   .order_by("-effective_from").first())
        if current is not None:
            assignment_code = current.employee_code
    heading = employee.full_name + (f" · Employee ID {assignment_code}" if assignment_code else "")
    summary = calendar["summary"]
    strip = (f"Present {summary['present']} · Late {summary['late']} · "
             f"Half day {summary['half_day']} · Absent {summary['absent']} · "
             f"Leave {summary['leave']} · Holiday {summary['holiday']} · "
             f"Weekly off {summary['weekly_off']} · Incomplete {summary['incomplete']} · "
             f"In office {summary['in_office']}")
    now = timezone.localtime()
    first = calendar["month_start"]
    lines = [f"{heading} · {first:%B %Y}", strip,
             f"Downloaded {now:%d %b %Y %H:%M} by {request.user.get_username()} · "
             f"times in {company.timezone or 'UTC'}"]
    weeks = [[_cell(day) for day in week] for week in calendar["weeks"]]
    content = exports.grid_pdf(
        title=f"{company.name} — Attendance calendar",
        lines=lines, blocks=[("", list(calendar["weekday_names"]), weeks)],
    )
    exports.record(actor=request.user, membership=membership, page="attendance_calendar",
                   fmt=exports.PDF,
                   filters={"employee": employee.pk, "month": f"{year:04d}-{month:02d}"},
                   count=len(calendar["days"]))
    name = exports.filename("attendance-calendar", employee.full_name,
                            f"{year:04d}-{month:02d}", exports.PDF)
    return exports.response(content, exports.PDF, name)
