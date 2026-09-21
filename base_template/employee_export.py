"""The Employees list as an Excel or PDF download (``?format=xlsx|pdf``).

Served by the list's own view, from the list's own query
(``views.employee_list_query``), so a download cannot differ from the screen:
the same branch/department scoping, search, status and setup filters, the same
table search and sort (carried by base_template/js/export_links.js).

Pay follows the screen exactly. The Salary columns exist only for a viewer who
may see pay in at least one branch (``show_rate_column``), and a row whose pay
this viewer may not see has them blank - the rule ``show_rate`` applies to the
row on screen. That is the point of this module: a download is the easy way
for pay to reach someone the page would never show it to.
"""

from django.contrib import messages
from django.db.models import OuterRef, Subquery
from django.shortcuts import redirect
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES
from base_template.tables import table_queryset
from common import exports
from employees.models import Employee, EmployeeCompensation
from organization.services import require_company_membership

#: The on-screen column headers, by the index the table's sort uses.
SCREEN_HEADERS = ["", "Employee ID", "Name", "Branch", "Department", "Designation",
                  "Now", "Status"]


def _sort_text(listing, sorted_by):
    headers = SCREEN_HEADERS + (["Base rate"] if listing.show_rate_column else []) + ["", ""]
    parts = [f"{headers[column]} {'descending' if descending else 'ascending'}"
             for column, descending in sorted_by if column < len(headers)]
    return ", ".join(parts) if parts else "Name"


def export_employees(request, listing, fmt):
    from base_template.views import SETUP_FILTERS
    from devices.services.mapping import UNASSIGNED_CODE

    membership = require_company_membership(request.user, request.company_id)
    queryset, table_search, sorted_by = table_queryset(
        request, listing.queryset, search=listing.search_fields, order=listing.columns)

    count = queryset.count()
    try:
        exports.check_size(count, fmt, noun="employees")
    except exports.TooManyRows as refusal:
        messages.error(request, str(refusal))
        params = request.GET.copy()
        params.pop("format", None)
        return redirect(f"{request.path}?{params.urlencode()}")

    compensation = EmployeeCompensation.objects.filter(employee=OuterRef("pk")).exclude(
        status="cancelled").order_by("-effective_from", "-pk")
    queryset = queryset.annotate(
        table_pay_basis=Subquery(compensation.values("pay_basis")[:1]),
        table_currency=Subquery(compensation.values("currency")[:1]),
    )

    salary_branches = listing.salary_branches
    statuses = dict(Employee.EmploymentStatus.choices)
    headers = ["Employee ID", "Name", "Branch", "Department", "Designation", "Status"]
    if listing.show_rate_column:
        headers += ["Salary", "Currency", "Pay basis"]
    headers.append("Needs setup")

    rows = []
    for employee in queryset:
        may_see_pay = (salary_branches is ALL_BRANCHES
                       or (employee.table_branch_id is not None
                           and employee.table_branch_id in salary_branches))
        gaps = []
        if employee.setup_department_code == UNASSIGNED_CODE:
            gaps.append("Department")
        if may_see_pay and employee.table_rate is None:
            gaps.append("Salary")
        row = [employee.table_code or "", employee.full_name, employee.table_branch or "",
               employee.table_department or "", employee.table_designation or "",
               statuses.get(employee.employment_status, employee.employment_status)]
        if listing.show_rate_column:
            if may_see_pay and employee.table_rate is not None:
                row += [employee.table_rate, employee.table_currency or "",
                        (employee.table_pay_basis or "").capitalize()]
            else:
                # Withheld exactly as the row on screen withholds it.
                row += [None, "", ""]
        row.append(", ".join(gaps) if gaps else "")
        rows.append(row)

    company = membership.company
    scope = listing.scope_name or ("All branches" if listing.company_wide else "Your branches")
    setup_label = dict(SETUP_FILTERS).get(listing.setup, "All")
    filters = {
        "scope": scope, "search": listing.search, "status": listing.status,
        "setup": listing.setup, "table_search": table_search,
        "sort": _sort_text(listing, sorted_by),
    }
    shown = [f"Scope: {scope}"]
    if listing.search:
        shown.append(f'Search: "{listing.search}"')
    if listing.status:
        shown.append(f"Status: {statuses.get(listing.status, listing.status)}")
    if listing.setup:
        shown.append(f"Setup: {setup_label}")
    if table_search:
        shown.append(f'Table search: "{table_search}"')
    shown.append(f"Sorted by: {filters['sort']}")
    now = timezone.localtime()
    lines = [
        " · ".join(shown),
        f"{count} employee{'s' if count != 1 else ''} · downloaded "
        f"{now:%d %b %Y %H:%M} by {request.user.get_username()}",
    ]
    title = f"{company.name} — Employees"
    salary_col = headers.index("Salary") if "Salary" in headers else None
    numeric = (salary_col,) if salary_col is not None else ()

    if fmt == exports.XLSX:
        content = exports.table_xlsx(title=title, lines=lines, headers=headers, rows=rows,
                                     numeric=numeric, widths={1: 28, 3: 22, 4: 22})
    else:
        content = exports.table_pdf(title=title, lines=lines, headers=headers, rows=rows,
                                    numeric=numeric, widths={0: 0.9, 1: 1.8, 3: 1.4, 4: 1.4})

    exports.record(actor=request.user, membership=membership, page="employees", fmt=fmt,
                   filters=filters, count=count)
    name = exports.filename("employees", listing.scope_name or company.name,
                            timezone.localdate().isoformat(), fmt)
    return exports.response(content, fmt, name)
