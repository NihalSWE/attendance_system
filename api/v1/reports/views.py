"""Reports and the dashboard (docs/api/00-PLAN.md phase 11; the guide:
docs/api/110-reports.md).

Every report of the panel's Reports menu, built by the same code
(``reports.catalogue``, ``reports.filters``, ``reports.builders``): each opens
by the rule of the page it reads from - attendance reports the Daily list's,
the Leave report the Leave list's, the Overtime report the Overtime page's -
so a branch manager sees their branches and the company every branch.
"""

import zoneinfo

from django.core.exceptions import PermissionDenied
from django.http import QueryDict
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import refuse
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.reports import serializers as s
from reports.access import may_see, report_scope
from reports.catalogue import BY_SLUG, REPORTS

AREA = "reports"
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required", "scope_missing",
          "permission_denied", "rate_limited", "server_error"]
SLUG_PARAM = Param("slug", PATH, "string", "The report, from GET /reports.", required=True,
                   example="daily-attendance")
REPORT_PARAMS = [
    SLUG_PARAM,
    Param("on", QUERY, "string (date)", "A day report's day (today by default).",
          example="2026-10-05"),
    Param("week_start", QUERY, "string (date)", "A week report: any day of the week (weeks "
                                                "start on Saturday).", example="2026-10-03"),
    Param("year", QUERY, "integer", "A month report's year.", example=2026),
    Param("month", QUERY, "integer", "A month report's month, 1-12.", example=10),
    Param("from", QUERY, "string (date)", "A range report's first day (the 1st of this "
                                          "month by default).", example="2026-10-01"),
    Param("to", QUERY, "string (date)", "Its last day (today by default).",
          example="2026-10-31"),
    Param("branch_id", QUERY, "integer", "Only this branch.", example=3),
    Param("department_id", QUERY, "integer", "Only this department.", example=8),
    Param("employee_id", QUERY, "integer", "Only this person.", example=41),
    Param("view", QUERY, "string", "Customize report: summary (default) or daily.",
          example="daily"),
    Param("status", QUERY, "string", "Customize report: one attendance status.",
          example="absent"),
    Param("leave_status", QUERY, "string", "Leave report: approved, pending, …",
          example="approved"),
    Param("leave_type", QUERY, "integer", "Leave report: one leave type's id.", example=1),
]


def _report(slug):
    report = BY_SLUG.get(slug)
    if report is None:
        raise ApiError("not_found", "No such report. GET /reports lists them.")
    return report


def _built(request, report):
    """``(membership, filters, result, described)`` - as the panel's report page."""
    from reports.builders import Context
    from reports.views import _choices, _described
    from reports import filters as report_filters

    membership, scope = report_scope(request.user, request.company_id, report.kind)
    q = request.query_params
    source = QueryDict(mutable=True)
    for api_name, name in (("on", "on"), ("week_start", "week_start"), ("year", "year"),
                           ("month", "month"), ("from", "date_from"), ("to", "date_to"),
                           ("branch_id", "branch"), ("department_id", "department"),
                           ("employee_id", "employee"), *((e, e) for e in report.extras)):
        if q.get(api_name):
            source[name] = q[api_name]
    f = report_filters.read(source, report.period, max_days=report.max_days,
                            extras=report.extras)
    choices = _choices(request.company_id, scope, report)
    # A filter outside the caller's reach is refused, not quietly widened.
    if f.branch and not any(str(b.pk) == f.branch for b in choices["branches"]):
        raise PermissionDenied("That branch is outside the ones you may report on.")
    if f.department and not choices["departments"].filter(pk=int(f.department)).exists():
        raise PermissionDenied("That department is outside the ones you may report on.")
    if f.employee and not choices["employees"].filter(pk=int(f.employee)).exists():
        raise PermissionDenied("That person is outside the ones you may report on.")
    company = membership.company
    result = report.build(Context(company_id=request.company_id, company=company,
                                  zone=zoneinfo.ZoneInfo(company.timezone or "UTC"),
                                  scope=scope, filters=f))
    return membership, f, result, _described(f, choices)


def _cell(value):
    """A cell as JSON: numbers stay numbers, everything else is its text."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


class ReportsView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "reports:read", None
    throttle_scope = "write"


class ReportListView(ReportsView):
    permission_classes = [PanelRule]
    # The list itself is harmless: each report is filtered by its own rule.
    panel_page = "me:home"

    @endpoint(
        id="reports", area=AREA, title="Reports",
        summary="The reports you may open, and how each is asked for.",
        what_it_does=["Lists every report of the Reports menu that your login may see."],
        description=("Attendance reports follow the Daily list's rule, the Leave report the "
                     "Leave list's, the Overtime report the Overtime page's."),
        roles=["Anyone in the company; each report by its own page's rule"],
        scopes=["reports:read"], response=s.ReportInfoSerializer,
        response_example=[{"slug": "daily-attendance", "title": "Daily Attendance Report",
                           "group": "Attendance Report", "period": "day", "max_days": 92,
                           "filters": [], "description": "Everyone's day: status, in and out, "
                                                         "hours worked, late and overtime."}],
        errors=ERRORS,
    )
    def get(self, request):
        kinds = {kind: may_see(request.user, request.company_id, kind)
                 for kind in {report.kind for report in REPORTS}}
        return Response(s.ReportInfoSerializer([{
            "slug": report.slug, "title": report.title, "group": report.group,
            "period": report.period, "max_days": report.max_days,
            "filters": list(report.extras), "description": report.description,
        } for report in REPORTS if kinds[report.kind]], many=True).data)


class OneReportView(ReportsView):
    """A report, by its slug; the gate is that report's own page."""

    permission_classes = [PanelRule]

    @property
    def panel_page(self):
        report = BY_SLUG.get(self.kwargs.get("slug", ""))
        return report.url_name if report else "reports:none"


class ReportView(OneReportView):
    permission_classes = [PanelRule]

    @endpoint(
        id="reports-one", area=AREA, title="Run a report",
        summary="A report for a period: its columns, rows (paged) and totals.",
        what_it_does=["Builds the report from the attendance, leave, overtime and scans "
                      "recorded - exactly as the panel's page shows it."],
        description=("The period follows the report's kind (see GET /reports): on= for a "
                     "day, week_start= for a week, year= and month= for a month, from= and "
                     "to= for a range. Each row is a list, one value per column. A range "
                     "longer than allowed is shortened and said in warnings."),
        roles=["As the report's page: the Daily list, the Leave list or the Overtime page"],
        scopes=["reports:read"], params=REPORT_PARAMS, response=s.ReportSerializer,
        response_example={"slug": "daily-late", "title": "Daily Late Report",
                          "period": "Monday, 05 October 2026", "first": "2026-10-05",
                          "last": "2026-10-05", "filters": ["Period: Monday, 05 October 2026",
                                                            "Branch: All branches"],
                          "warnings": [], "columns": [
                              {"label": "Employee", "numeric": False, "kind": ""},
                              {"label": "Late by", "numeric": True, "kind": "duration"}],
                          "summary": [{"label": "Late", "value": "1"}], "legend": "",
                          "note": "", "count": 1, "next": None, "previous": None,
                          "results": [["Rahim Uddin", "0:12"]]},
        errors=ERRORS + ["not_found"],
    )
    def get(self, request, slug):
        report = _report(slug)
        _m, f, result, described = _built(request, report)
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(result.rows, request, view=self)
        paged = paginator.get_paginated_response([[_cell(v) for v in row] for row in chunk]).data
        return Response(s.ReportSerializer({
            "slug": report.slug, "title": report.title, "period": f.label, "first": f.first,
            "last": f.last, "filters": described, "warnings": f.errors,
            "columns": [{"label": c.label, "numeric": c.numeric, "kind": c.kind}
                        for c in result.columns],
            "summary": [{"label": str(label), "value": str(value)}
                        for label, value in result.summary],
            "legend": result.legend or "", "note": result.note or "", **paged}).data)


class ReportExportView(OneReportView):
    permission_classes = [PanelRule]

    @endpoint(
        id="reports-export", area=AREA, title="Download a report",
        summary="The same report as an Excel (xlsx) or PDF file.",
        what_it_does=["Answers the file, with the company, period, filters and totals at the "
                      "top; the download is kept in the audit log."],
        description="Too many rows are refused (Excel 10,000, PDF 1,500): narrow the filters.",
        roles=["As the report's page"], scopes=["reports:read"],
        params=REPORT_PARAMS + [Param("file_type", QUERY, "string", "xlsx (default) or pdf.",
                                      example="pdf")],
        errors=ERRORS + ["not_found", "validation_error"],
    )
    def get(self, request, slug):
        from common import exports
        from reports.views import _download

        report = _report(slug)
        fmt = request.query_params.get("file_type") or "xlsx"
        if fmt not in ("xlsx", "pdf"):
            refuse({"file_type": ["xlsx or pdf."]})
        membership, f, result, described = _built(request, report)
        try:
            exports.check_size(len(result.rows), fmt, noun="report rows")
        except exports.TooManyRows as refusal:
            raise ApiError("validation_error", str(refusal)) from None
        return _download(request._request, report, result, f, described, membership, fmt)


# --- the dashboard ------------------------------------------------------------------------

class DashboardView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "dashboard"
    read_scope, write_scope = "reports:read", None
    throttle_scope = "write"

    @endpoint(
        id="dashboard", area=AREA, title="Dashboard",
        summary="The company at a glance: headcount, branches, the newest people, devices "
                "not calling in, and anyone still in long after their shift.",
        what_it_does=["Answers the panel dashboard's figures and alerts."],
        description=("The owner or an unrestricted company administrator, as on the panel. "
                     "stopped_devices is for whoever may manage devices; still_in for "
                     "whoever may fix attendance."),
        roles=["Company owner or administrator (not limited to some branches)"],
        scopes=["reports:read"], response=s.DashboardSerializer,
        response_example={"employees": 42, "active": 38, "probation": 3, "resigned": 1,
                          "branches": 2, "departments": 6,
                          "recent": [{"id": 41, "name": "Rahim Uddin"}],
                          "stopped_devices": [{"id": "6f1c9a2b-…", "name": "Main gate",
                                               "branch": {"id": 3, "name": "Chattogram"},
                                               "last_seen_at": "2026-10-04T18:00:00+06:00",
                                               "detail": "Last seen 16 hours ago"}],
                          "still_in": [{"employee": {"id": 41, "name": "Rahim Uddin"},
                                        "since": "09:02", "shift_ended": "18:00"}]},
        errors=ERRORS,
    )
    def get(self, request):
        from accounts.services import get_active_memberships
        from attendance import access as attendance_access
        from devices.services import connection, panel_access
        from employees.models import Employee
        from organization.models import Branch, Department

        member = get_active_memberships(request.user).filter(
            company_id=request.company_id).first()
        if (not member or member.role not in ("owner", "company_admin")
                or member.allowed_branches.exists() or member.allowed_departments.exists()):
            raise PermissionDenied("The dashboard is for the owner or an unrestricted company "
                                   "administrator.")
        employees = Employee.objects.all()
        stopped = (connection.stopped_devices(request.company_id)
                   if panel_access.may_manage_devices(request.user, request.company_id)
                   else [])
        still_in = attendance_access.still_in_for(request.user, request.company_id)
        return Response(s.DashboardSerializer({
            "employees": employees.count(),
            "active": employees.filter(employment_status="active").count(),
            "probation": employees.filter(employment_status="probation").count(),
            "resigned": employees.filter(employment_status="resigned").count(),
            "branches": Branch.objects.count(), "departments": Department.objects.count(),
            "recent": [{"id": e.pk, "name": e.full_name}
                       for e in employees.order_by("-created_at")[:8]],
            "stopped_devices": [{"id": str(device.public_id), "name": device.name,
                                 "branch": {"id": device.branch_id, "name": device.branch.name},
                                 "last_seen_at": state.last_seen_at, "detail": state.detail}
                                for device, state in stopped],
            "still_in": [{"employee": {"id": e.pk, "name": e.full_name},
                          "since": status.since_text, "shift_ended": status.shift_end_text}
                         for e, status in still_in],
        }).data)
