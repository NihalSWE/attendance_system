"""Reports: one page for every report, and ``?format=xlsx|pdf`` for its download.

The page and the download are the same view, so the same permissions,
period and filters - a download holds every row the page pages through.
"""

import zoneinfo

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import redirect
from django.views.decorators.http import require_http_methods

from attendance.models import AttendanceRecord
from base_template.tables import paginate_rows, render, table_rows
from common import exports
from common.tenant import use_company
from organization.access_services import branch_choices, people
from organization.models import Department
from organization.views import _company_or_redirect
from reports import filters as report_filters
from reports.access import may_see, report_scope
from reports.builders import Context
from reports.catalogue import BY_SLUG, REPORTS

@login_required
@require_http_methods(["GET"])
def report_index(request):
    """Reports: every report this person may open, by group."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    kinds = {report.kind for report in REPORTS}
    allowed = {kind for kind in kinds if may_see(request.user, company_id, kind)}
    return render(request, "reports/index.html", {
        "reports": [report for report in REPORTS if report.kind in allowed],
    })


def _choices(company_id, scope, report):
    """The branch, department and employee dropdowns, within ``scope``."""
    branches = branch_choices(company_id, scope)
    departments = Department.objects.filter(branch__in=branches).select_related("branch")
    if not scope.is_all and not scope.branches:
        departments = departments.filter(pk__in=scope.departments)
    choices = {
        "branches": branches,
        "departments": departments.order_by("branch__name", "name"),
        "employees": people(scope).order_by("first_name", "last_name"),
    }
    if "leave_type" in report.extras:
        from leaves.models import LeaveType

        choices["leave_types"] = LeaveType.objects.order_by("name")
    if "leave_status" in report.extras:
        from leaves.models import LeaveRequest

        choices["leave_statuses"] = [
            (value, label) for value, label in LeaveRequest.Status.choices
            if value in ("approved", "pending", "rejected", "cancelled", "withdrawn")
        ]
    if "status" in report.extras:
        choices["statuses"] = AttendanceRecord.AttendanceStatus.choices
    return choices


@login_required
@require_http_methods(["GET"])
def report(request, slug):
    report = BY_SLUG.get(slug)
    if report is None:
        raise Http404("No such report.")
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    membership, scope = report_scope(request.user, company_id, report.kind)
    company = membership.company
    zone = zoneinfo.ZoneInfo(company.timezone or "UTC")
    f = report_filters.read(request.GET, report.period, max_days=report.max_days,
                            extras=report.extras)

    with use_company(company_id):
        choices = _choices(company_id, scope, report)
        # A filter outside the viewer's reach is dropped, not trusted.
        if f.branch and not any(str(b.pk) == f.branch for b in choices["branches"]):
            f.branch = ""
        if f.department and not choices["departments"].filter(pk=int(f.department)).exists():
            f.department = ""
        if f.employee and not choices["employees"].filter(pk=int(f.employee)).exists():
            f.employee = ""
        result = report.build(Context(company_id=company_id, company=company, zone=zone,
                                      scope=scope, filters=f))
        described = _described(f, choices)

    fmt = request.GET.get("format")
    if fmt in exports.FORMATS:
        return _download(request, report, result, f, described, membership, fmt)

    # The project's server-side table: entries per page, search, any column
    # sorted, numbered pages - over the report's rows (base_template.tables).
    page = paginate_rows(request, result.rows, columns=len(result.columns))
    query = request.GET.copy()
    for drop in ("page", "per_page", "table_q", "table", "draw", "start", "length",
                 "format", "search[value]"):
        query.pop(drop, None)
    for key in [key for key in query if key.startswith(("order[", "columns["))]:
        query.pop(key)
    # The period before and after, keeping every other filter.
    steps = []
    for values in report_filters.neighbours(f):
        if values is None:
            steps.append("")
            continue
        moved = query.copy()
        for key, value in values.items():
            moved[key] = str(value)
        steps.append(moved.urlencode())
    return render(request, "reports/report.html", {
        "report": report,
        "reports": REPORTS,
        "result": result,
        "page": page,
        "f": f,
        "described": described,
        "query": query.urlencode(),
        "earlier": steps[0],
        "later": steps[1],
        "months": report_filters.MONTHS,
        "years": list(range(f.first.year + 1, f.first.year - 5, -1)),
        **choices,
    })


def _described(f, choices):
    """The filters in words, for the page and the download's header."""
    said = [f"Period: {f.label}"]
    branch = next((b for b in choices["branches"] if str(b.pk) == f.branch), None)
    said.append(f"Branch: {branch.name if branch else 'All branches'}")
    if f.department:
        department = choices["departments"].filter(pk=int(f.department)).first()
        said.append(f"Department: {department.name}")
    if f.employee:
        employee = choices["employees"].filter(pk=int(f.employee)).first()
        said.append(f"Employee: {employee.full_name}")
    for name, value in f.extra.items():
        if value and value not in ("summary",):
            label = {"view": "View", "status": "Status", "leave_status": "Leave status",
                     "leave_type": "Leave type"}.get(name, name)
            if name == "view":
                value = "Every day"
            elif name == "status":
                value = dict(AttendanceRecord.AttendanceStatus.choices).get(value, value)
            elif name == "leave_type":
                value = next((t.name for t in choices.get("leave_types", ())
                              if str(t.pk) == value), value)
            elif name == "leave_status":
                value = dict(choices.get("leave_statuses", ())).get(value, value)
            said.append(f"{label}: {value}")
    return said


def _download(request, report, result, f, described, membership, fmt):
    from django.utils import timezone

    # What the table showed: its search and its sort (export_links.js).
    rows, table_search, sorted_by = table_rows(request, result.rows,
                                               columns=len(result.columns))
    if table_search:
        described = described + [f'Table search: "{table_search}"']
    if sorted_by:
        described = described + ["Sorted by: " + ", ".join(
            f"{result.columns[column].label} {'descending' if descending else 'ascending'}"
            for column, descending in sorted_by)]
    try:
        exports.check_size(len(rows), fmt, noun="report rows")
    except exports.TooManyRows as refusal:
        messages.error(request, str(refusal))
        params = request.GET.copy()
        params.pop("format", None)
        return redirect(f"{request.path}?{params.urlencode()}")
    company = membership.company
    now = timezone.localtime()
    lines = [" · ".join(described),
             " · ".join(f"{label}: {value}" for label, value in result.summary),
             f"{len(rows)} row{'s' if len(rows) != 1 else ''} · downloaded "
             f"{now:%d %b %Y %H:%M} by {request.user.get_username()}"]
    if result.legend:
        lines.append(result.legend)
    if result.note:
        lines.append(result.note)
    title = f"{company.name} — {report.title}"
    headers = [column.label for column in result.columns]
    numeric = [i for i, column in enumerate(result.columns) if column.numeric]
    widths = {i: column.weight for i, column in enumerate(result.columns)}
    if fmt == exports.XLSX:
        content = exports.table_xlsx(title=title, lines=lines, headers=headers,
                                     rows=rows, numeric=numeric,
                                     widths={i: max(6, int(w * 12)) for i, w in widths.items()})
    else:
        content = exports.table_pdf(title=title, lines=lines, headers=headers,
                                    rows=rows, numeric=numeric, widths=widths,
                                    compact=result.grid)
    exports.record(actor=request.user, membership=membership, page=f"report:{report.slug}",
                   fmt=fmt, filters={"described": described}, count=len(rows))
    name = exports.filename(report.slug, company.name, f.slug(), fmt)
    return exports.response(content, fmt, name)
