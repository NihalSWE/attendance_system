"""Company sidebar destinations. Existing pages remain the source of each screen.

Fragments expose sections of shared pages without duplicating their forms.
Aliases keep an area's item selected while editing one of its records.
"""

from django.urls import reverse


def item(label, view, *aliases, fragment="", manage=False, unrestricted=False, record=False,
         salary=False, group="", report_kind=""):
    # record: shown to people who may record leave (HR as well as administrators).
    # salary: shown to people who see pay (the payroll manager as well; not HR).
    # group: the dropdown inside its menu the item sits in (Reports: "Attendance
    # Report" opens to Daily, Weekly, Monthly, Customize).
    # report_kind: whose rules decide who may open it (reports.access).
    return {"label": label, "view": view, "aliases": aliases, "fragment": fragment,
            "manage": manage, "unrestricted": unrestricted, "record": record,
            "salary": salary, "group": group, "report_kind": report_kind}


def report_item(slug):
    """A Reports menu entry, from the report catalogue (reports/catalogue.py)."""
    from reports.catalogue import BY_SLUG

    report = BY_SLUG[slug]
    return item(report.menu, report.url_name, group=report.group, report_kind=report.kind)


COMPANY_MENUS = (
    ("employees", "Employees", (
        item("All employees", "employee_list", "organization:employee_edit",
             "organization:employee_detail", "organization:employee_end", unrestricted=True),
        item("Create employee", "organization:employee_create", manage=True),
    )),
    ("attendance", "Attendance", (
        item("Daily list", "attendance:attendance_list"),
        item("Late entries", "attendance:attendance_late"),
        item("Calendar", "attendance:attendance_calendar", "attendance:attendance_day"),
        item("Days to review", "attendance:attendance_review", "attendance:attendance_day_fix",
             "attendance:attendance_correction_withdraw"),
        item("Missed scans", "attendance:missed_scan_list", "attendance:missed_scan_decide"),
    )),
    ("leave", "Leave", (
        item("Leave list", "leaves:leave_list", "leaves:leave_cancel"),
        item("Record leave", "leaves:leave_record", record=True),
        item("Approval inbox", "me:leave_inbox", "me:leave_decide", manage=True),
        item("Leave types", "leaves:leave_type_list", "leaves:leave_type_create",
             "leaves:leave_type_edit", "leaves:leave_type_status"),
    )),
    ("salary", "Salary", (
        item("Salary by month", "payroll:payroll_home", "payroll:payslip", "payroll:penalty_waive",
             "payroll:payroll_submit", "payroll:payroll_finalise", "payroll:payroll_return",
             "payroll:payroll_reopen", salary=True),
        item("Salary settings", "payroll:salary_settings", manage=True),
        item("Penalty rules", "payroll:salary_settings", "payroll:penalty_rule_create",
             "payroll:penalty_rule_change", "payroll:penalty_rule_stop",
             fragment="penalty-rules", manage=True),
        item("Overtime", "payroll:overtime_list", "payroll:overtime_decide", "payroll:overtime_undo"),
        item("Overtime settings", "payroll:salary_settings", fragment="overtime-rules", manage=True),
    )),
    ("shifts", "Shifts", (
        item("Overview", "scheduling:schedule_overview"),
        item("Shifts", "scheduling:schedule_overview", "scheduling:shift_create",
             "scheduling:shift_edit", "scheduling:shift_status", fragment="shifts"),
        item("Department shifts", "scheduling:schedule_overview", "scheduling:department_shift_set",
             fragment="department-shifts"),
        item("Weekly off days", "scheduling:schedule_overview", "scheduling:weekly_off_create",
             "scheduling:weekly_off_start", "scheduling:weekly_off_end", fragment="weekly-offs"),
        item("Holidays", "scheduling:holiday_list", "scheduling:holiday_create",
             "scheduling:holiday_edit", "scheduling:holiday_cancel"),
        item("Holiday calendar", "scheduling:holiday_year", manage=True),
        item("Attendance settings", "scheduling:attendance_settings_edit", manage=True),
    )),
    ("reports", "Reports", (
        report_item("daily-attendance"),
        report_item("weekly-attendance"),
        report_item("monthly-attendance"),
        report_item("custom-attendance"),
        # Leave Report hidden from the sidebar for now (Nihal, 2026-09-26). Only
        # this link: the page, its URL and the Reports page still work. Remove
        # the "# " below to show it in the sidebar again.
        # report_item("leave"),
        report_item("daily-absent"),
        report_item("monthly-absent"),
        report_item("daily-late"),
        report_item("monthly-late"),
        report_item("working-hours"),
        report_item("short-hours"),
        report_item("overtime"),
        report_item("entry-logs"),
    )),
    ("organisation", "Organisation", (
        item("Branches", "organization:branch_list", "organization:branch_create",
             "organization:branch_edit", "organization:branch_status"),
        item("Departments", "organization:adoption_list", "organization:adoption_create",
             "organization:adoption_edit", "organization:adoption_copy",
             "organization:adoption_status", "department_list"),
        item("Designations", "organization:designation_list", "organization:designation_create",
             "organization:designation_edit", "organization:designation_status"),
        item("Company profile", "organization:company_profile", manage=True),
        item("Email settings", "organization:mail_settings", manage=True),
        item("Access", "organization:access", "organization:access_person", manage=True),
    )),
    ("devices", "Devices", (
        item("All devices", "devices:device_list", "devices:device_detail", "devices:device_edit",
             "devices:device_users", "devices:device_department_add", "devices:device_department_end"),
        item("Register device", "devices:device_register"),
        item("Enrollments", "devices:enrollment_list", "devices:enrollment_create", "devices:enrollment_edit"),
        item("Punches", "devices:punch_list", "devices:punch_detail"),
        item("Messages", "devices:message_list", "devices:message_detail"),
        item("Unresolved", "devices:unresolved_queue"),
        item("Which devices count", "devices:attendance_rules", "devices:attendance_recheck"),
    )),
)


def company_menus(request, *, can_manage, can_manage_devices, can_record_leave=None,
                  can_see_salary=None, report_kinds=None, allowed=None):
    """The company menus for this request.

    ``allowed`` (A12): for a branch manager or a person given access, a check
    on each entry's page (``access_control.page_access``) replaces the role
    flags — they see exactly the branch pages they may open.
    """
    current = getattr(getattr(request, "resolver_match", None), "view_name", "")
    can_record_leave = can_manage if can_record_leave is None else can_record_leave
    can_see_salary = can_manage if can_see_salary is None else can_see_salary
    menus = []
    for key, label, entries in COMPANY_MENUS:
        if key == "devices" and not can_manage_devices and allowed is None:
            continue
        links = []
        for entry in entries:
            if allowed is not None:
                if not allowed(entry["view"]):
                    continue
            elif entry["manage"] and not can_manage:
                continue
            elif entry["record"] and not can_record_leave:
                continue
            elif entry["salary"] and not can_see_salary:
                continue
            elif (entry["report_kind"] and report_kinds is not None
                  and entry["report_kind"] not in report_kinds):
                continue
            elif entry["unrestricted"] and not can_manage_devices:
                continue
            matches = current == entry["view"] or current in entry["aliases"]
            selected = matches and (not entry["fragment"] or current in entry["aliases"])
            url = reverse(entry["view"])
            if entry["fragment"]:
                url += "#" + entry["fragment"]
            links.append({**entry, "url": url, "active": selected, "matches": matches})
        if links:
            menus.append({"key": key, "label": label, "links": links, "tree": _tree(links),
                          "active": any(link["matches"] for link in links)})
    return menus


def _tree(links):
    """The menu's links with each group gathered into a dropdown of its own:
    ``[{"link": ...}, {"group": "Attendance Report", "links": [...], "active": ...}]``.
    ``links`` stays flat for everything else that reads the menu."""
    tree = []
    for link in links:
        if not link["group"]:
            tree.append({"link": link})
        elif tree and tree[-1].get("group") == link["group"]:
            tree[-1]["links"].append(link)
        else:
            tree.append({"group": link["group"], "links": [link]})
    for node in tree:
        if "group" in node:
            node["active"] = any(link["matches"] for link in node["links"])
    return tree
