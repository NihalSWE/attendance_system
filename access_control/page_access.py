"""Which company pages a branch manager or a person given access may open (A12 part 3).

Owner, company admin and HR use the company pages as before; nothing here
changes them. An Employee or Branch-manager login is kept on its own pages by
``common.middleware.SelfServiceGate`` — except the pages listed here, which it
opens to anyone holding the listed permission in at least one branch.

**A page is listed only once it limits what it shows to the viewer's
branches.** Opening a page that still shows the whole company would hand a
branch manager every other branch's people, leave or salary. Parts 4–6 of A12
add each area here as it becomes branch-scoped; the sidebar and the My account
page follow this list, so a page appears the moment it is added.
"""

from access_control.branch_access import CODES, can

# view name -> the branch permission it needs, or a tuple: any one of them.
BRANCH_PAGES = {
    # A12 part 2: who has which access (itself limited to the viewer's branches).
    "organization:access": "access.grant",
    "organization:access_person": "access.grant",
    # A12 part 4: the Employees area. The list shows only your branches' people
    # (pay only where you may see salaries); Create employee offers only your
    # branches; Edit employee opens details, placement and logins for someone
    # in your branch, and pay where you may prepare salary — own shifts and
    # making a branch manager stay with the company.
    "employee_list": "employees.view",
    "organization:employee_create": "employees.edit",
    # Bulk import (Employee ID + Name). Purely additive, and the same permission the
    # one-at-a-time page already asks for: the service checks the chosen branch
    # against employees.edit, so a branch manager imports into their own
    # branch only (the field is locked for them) and nowhere else.
    "organization:employee_import": "employees.edit",
    "organization:employee_import_confirm": "employees.edit",
    "organization:employee_import_demo": "employees.edit",
    "organization:employee_edit": "employees.edit",
    "organization:employee_branch_departments": "employees.edit",
    "organization:employee_department_designations": "employees.edit",
    # Map / Bulk map on the Employees list: the service checks the branch.
    "devices:employee_map": "employees.edit",
    "devices:employee_bulk_map": "employees.edit",
    "devices:employees_send": "employees.edit",
    # A12 part 5: leave and overtime, each limited to your branches. Someone
    # who may record leave also sees the list (that is where Cancel is); the
    # overtime day opens to viewers, and only deciders get its form. Leave
    # types stay the company's. The approval inbox lives under My account.
    "leaves:leave_list": ("leave.view", "leave.record"),
    "leaves:leave_record": "leave.record",
    "leaves:leave_cancel": "leave.record",
    "payroll:overtime_list": ("overtime.view", "overtime.decide"),
    "payroll:overtime_decide": ("overtime.view", "overtime.decide"),
    "payroll:overtime_undo": "overtime.decide",
    # A12 part 6: salary. Salary by month and payslips show the people placed
    # in your branches; Generate rebuilds only their payslips, and bonus or
    # deduction lines need Prepare salary in the payslip's branch. Finalise,
    # Undo finalise, salary settings, penalty rules and waiving a penalty are
    # not listed: they stay with the owner and company admin.
    "payroll:payroll_home": ("salary.view", "salary.prepare"),
    "payroll:payroll_generate": "salary.prepare",
    # Salary approval (A11): whoever prepares salary submits the month, and
    # can take it back; approving stays with the owner and company admin.
    "payroll:payroll_submit": "salary.prepare",
    "payroll:payroll_return": "salary.prepare",
    "payroll:payslip": ("salary.view", "salary.prepare"),
    "payroll:payslip_adjustment_add": "salary.prepare",
    "payroll:payslip_correction_add": "salary.prepare",
    "payroll:payslip_adjustment_remove": "salary.prepare",
    # A12 part 7 (Nihal's N10): attendance and the employee page. The Daily list,
    # Calendar and day panel show only days worked in your branches and pick
    # from their people; the live "Now" answers only for people placed in them.
    # Days to review, Fix a day and Withdraw need Fix attendance in the day's
    # branch, checked again in the services. The employee page hides pay without
    # View salaries there; End employment is refused for someone with more than
    # an Employee login, and for yourself. Devices are not listed: they stay
    # with the owner and company admin.
    "attendance:attendance_list": "attendance.view",
    "attendance:attendance_calendar": "attendance.view",
    "attendance:attendance_day": "attendance.view",
    "attendance:attendance_now": ("employees.view", "attendance.view"),
    "attendance:attendance_review": "attendance.fix",
    "attendance:attendance_day_fix": "attendance.fix",
    "attendance:attendance_correction_withdraw": "attendance.fix",
    # N11: missed scans employees report, for whoever may fix the day's branch.
    "attendance:missed_scan_list": "attendance.fix",
    "attendance:missed_scan_decide": "attendance.fix",
    "organization:employee_detail": "employees.view",
    "organization:employee_end": "employees.edit",
}


def _codes(view_name):
    codes = BRANCH_PAGES.get(view_name, ())
    return (codes,) if isinstance(codes, str) else codes


def may_open(user, company_id, view_name):
    """True when ``view_name`` is a branch page and ``user`` holds its permission somewhere."""
    return any(can(user, company_id, code) for code in _codes(view_name))


def held_codes(user, company_id):
    """The branch permissions ``user`` holds in at least one branch."""
    return {code for code in CODES if can(user, company_id, code)}


def opener(user, company_id):
    """A cached ``may_open`` for one request (the sidebar asks per menu entry)."""
    held = held_codes(user, company_id)

    def allowed(view_name):
        return any(code in held for code in _codes(view_name))

    return allowed
