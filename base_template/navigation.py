"""Company sidebar destinations. Existing pages remain the source of each screen.

Fragments expose sections of shared pages without duplicating their forms.
Aliases keep an area's item selected while editing one of its records.
"""

from django.urls import reverse


def item(label, view, *aliases, fragment="", manage=False, unrestricted=False, record=False):
    # record: shown to people who may record leave (HR as well as administrators).
    return {"label": label, "view": view, "aliases": aliases, "fragment": fragment,
            "manage": manage, "unrestricted": unrestricted, "record": record}


COMPANY_MENUS = (
    ("employees", "Employees", (
        item("All employees", "employee_list", "organization:employee_edit",
             "organization:employee_detail", "organization:employee_end", unrestricted=True),
        item("Create employee", "organization:employee_create", manage=True),
    )),
    ("attendance", "Attendance", (
        item("Daily list", "attendance:attendance_list"),
        item("Calendar", "attendance:attendance_calendar", "attendance:attendance_day"),
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
             "payroll:payroll_finalise", "payroll:payroll_reopen"),
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
    ("organisation", "Organisation", (
        item("Branches", "organization:branch_list", "organization:branch_create",
             "organization:branch_edit", "organization:branch_status"),
        item("Departments", "organization:adoption_list", "organization:adoption_create",
             "organization:adoption_edit", "organization:adoption_copy",
             "organization:adoption_status", "department_list"),
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


def company_menus(request, *, can_manage, can_manage_devices, can_record_leave=None):
    current = getattr(getattr(request, "resolver_match", None), "view_name", "")
    can_record_leave = can_manage if can_record_leave is None else can_record_leave
    menus = []
    for key, label, entries in COMPANY_MENUS:
        if key == "devices" and not can_manage_devices:
            continue
        links = []
        for entry in entries:
            if entry["manage"] and not can_manage:
                continue
            if entry["record"] and not can_record_leave:
                continue
            if entry["unrestricted"] and not can_manage_devices:
                continue
            matches = current == entry["view"] or current in entry["aliases"]
            selected = matches and (not entry["fragment"] or current in entry["aliases"])
            url = reverse(entry["view"])
            if entry["fragment"]:
                url += "#" + entry["fragment"]
            links.append({**entry, "url": url, "active": selected, "matches": matches})
        if links:
            menus.append({"key": key, "label": label, "links": links,
                          "active": any(link["matches"] for link in links)})
    return menus
