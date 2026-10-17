"""Company sidebar destinations. Existing pages remain the source of each screen.

Fragments expose sections of shared pages without duplicating their forms.
Aliases keep an area's item selected while editing one of its records.
"""

from django.urls import reverse

from inventory.reports import REPORTS as INVENTORY_REPORTS

#: The inventory reports open under "Reports" (Phase 7); any of their
#: permissions shows the entry.
REPORT_URLS = tuple(report.url_name for report in INVENTORY_REPORTS)
INVENTORY_REPORT_CODES = tuple(dict.fromkeys(
    code for report in INVENTORY_REPORTS
    for code in ((report.code,) if isinstance(report.code, str) else report.code)))


def item(label, view, *aliases, fragment="", manage=False, unrestricted=False, record=False,
         salary=False, group="", report_kind="", people="", needs=""):
    # record: shown to people who may record leave (HR as well as administrators).
    # salary: shown to people who see pay (the payroll manager as well; not HR).
    # group: the dropdown inside its menu the item sits in (Reports: "Attendance
    # Report" opens to Daily, Weekly, Monthly, Customize).
    # report_kind: whose rules decide who may open it (reports.access).
    # people: the employees permission that also opens it for HR, who views and
    # edits employees in every branch (Ajay, 2026-09-27).
    # needs: a module permission the viewer must hold somewhere (Store &
    # Inventory); such an entry is hidden when the company lacks the module.
    return {"label": label, "view": view, "aliases": aliases, "fragment": fragment,
            "manage": manage, "unrestricted": unrestricted, "record": record,
            "salary": salary, "group": group, "report_kind": report_kind, "people": people,
            "needs": needs}


def report_item(slug):
    """A Reports menu entry, from the report catalogue (reports/catalogue.py)."""
    from reports.catalogue import BY_SLUG

    report = BY_SLUG[slug]
    return item(report.menu, report.url_name, group=report.group, report_kind=report.kind)


COMPANY_MENUS = (
    ("employees", "Employees", (
        item("All employees", "employee_list", "organization:employee_edit",
             "organization:employee_detail", "organization:employee_end", unrestricted=True,
             people="employees.view"),
        item("Create employee", "organization:employee_create", manage=True,
             people="employees.edit"),
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
        # Phase E (2026-09-27): policies are set up by the owner or admin;
        # balances are seen by whoever sees leave.
        item("Leave policies", "leaves:leave_policy_list", "leaves:leave_policy_create",
             "leaves:leave_policy_detail", "leaves:leave_policy_edit",
             "leaves:leave_policy_version_create", "leaves:leave_policy_version_edit",
             manage=True),
        item("Leave balances", "leaves:leave_balance_list"),
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
        # Leave Fare Assistance (2026-09-28): claims for whoever prepares salary;
        # the rules for the owner or admin.
        item("LFA claims", "payroll:lfa_claims", "payroll:lfa_claim", "payroll:lfa_claim_new",
             salary=True),
        item("LFA settings", "payroll:lfa_settings", manage=True),
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
        report_item("leave"),
        report_item("daily-absent"),
        report_item("monthly-absent"),
        report_item("daily-late"),
        report_item("monthly-late"),
        report_item("working-hours"),
        report_item("short-hours"),
        report_item("overtime"),
        report_item("entry-logs"),
    )),
    # Store & Inventory (docs/inventory/00-PLAN.md, Phase 1). Shown once the
    # platform owner enables the module, to those holding "View stock and items".
    ("inventory", "Inventory", (
        item("Dashboard", "inventory:dashboard", "inventory:home", needs="inventory.view"),
        item("Stock on hand", "inventory:stock", needs="inventory.view"),
        item("Reports", "inventory:reports", *REPORT_URLS, needs=("inventory.view", *INVENTORY_REPORT_CODES)),
        item("Month-end close", "inventory:period_close", needs="inventory.period_close"),
        item("Go-live", "inventory:golive", "inventory:golive_count_sheet", "inventory:golive_compare",
             needs=("inventory.adjust", "inventory.adjust_approve")),
        item("Items", "inventory:items", "inventory:items_edit", "inventory:items_status",
             "inventory:item_detail", "inventory:pack_unit_add", "inventory:pack_unit_remove",
             needs="inventory.view"),
        item("Scan", "inventory:scan", needs="inventory.view"),
        item("Labels", "inventory:labels", "inventory:label_print", "inventory:label_void",
             "inventory:bin_labels_print", "inventory:package_labels", needs="inventory.view"),
        item("Store requisitions", "inventory:store_requisitions", "inventory:store_requisitions_detail",
             needs=("inventory.issue", "inventory.issue_approve")),
        item("Store issues", "inventory:store_issues", "inventory:store_issues_detail",
             needs=("inventory.issue", "inventory.view")),
        item("Material returns", "inventory:store_returns", "inventory:store_returns_detail",
             needs=("inventory.issue", "inventory.view")),
        item("Transfers", "inventory:store_transfers", "inventory:store_transfers_detail",
             needs=("inventory.transfer", "inventory.view")),
        item("Adjustments", "inventory:store_adjustments", "inventory:store_adjustments_detail",
             needs=("inventory.adjust", "inventory.adjust_approve")),
        item("Stock counts", "inventory:store_counts", "inventory:store_counts_detail",
             needs=("inventory.adjust", "inventory.adjust_approve")),
        item("Opening stock", "inventory:opening", "inventory:opening_detail",
             "inventory:opening_line_add", "inventory:opening_line_remove",
             "inventory:opening_post", "inventory:opening_cancel", "inventory:opening_reverse",
             needs="inventory.view"),
        item("Stores", "inventory:stores", "inventory:stores_edit", "inventory:stores_status",
             needs="inventory.view"),
        item("Bins", "inventory:bins", "inventory:bins_edit", "inventory:bins_status",
             needs="inventory.view"),
        item("Units", "inventory:units", "inventory:units_edit", "inventory:units_status",
             "inventory:standard_units", needs="inventory.view"),
        item("Item categories", "inventory:categories", "inventory:categories_edit",
             "inventory:categories_status", needs="inventory.view"),
        item("Suppliers", "inventory:suppliers", "inventory:suppliers_edit",
             "inventory:suppliers_status", needs="inventory.view"),
        item("Customers", "inventory:customers", "inventory:customers_edit",
             "inventory:customers_status", needs="inventory.view"),
        item("Inventory settings", "inventory:settings", manage=True, needs="inventory.view"),
    )),
    # Purchase (Phase 3): requisition -> order -> goods receipt -> QC -> return.
    ("purchase", "Purchase", (
        item("Requisitions", "purchasing:requisitions", "purchasing:requisition_detail",
             "purchasing:requisition_line_add", "purchasing:requisition_line_remove",
             "purchasing:requisition_submit", "purchasing:requisition_decide",
             "purchasing:requisition_cancel", needs=("purchase.request", "purchase.approve")),
        item("Purchase orders", "purchasing:orders", "purchasing:order_detail",
             "purchasing:order_from_requisitions", "purchasing:order_line_add",
             "purchasing:order_line_remove", "purchasing:order_email", "purchasing:order_action",
             needs=("purchase.order", "purchase.approve", "purchase.receive")),
        item("Goods receipts", "purchasing:receipts", "purchasing:receipt_detail",
             "purchasing:receipt_line_add", "purchasing:receipt_line_remove",
             "purchasing:receipt_action", "purchasing:receipt_labels",
             needs=("purchase.receive", "inventory.view")),
        item("Quality check", "purchasing:qc", "purchasing:qc_record", needs="purchase.qc"),
        item("Purchase returns", "purchasing:returns", "purchasing:return_detail",
             "purchasing:return_line_add", "purchasing:return_line_remove",
             "purchasing:return_action", needs=("purchase.receive", "purchase.approve")),
        item("Suppliers", "inventory:suppliers", needs="inventory.view"),
    )),
    # Production (Phase 5): recipes, runs, and what each run cost.
    ("production", "Production", (
        item("Production orders", "production:orders", "production:order_detail", "production:order_output",
             "production:order_labels", "production:order_action", "production:consumption_detail",
             "production:consumption_line_add", "production:consumption_line_remove",
             "production:consumption_scan", "production:consumption_action",
             needs=("production.plan", "production.execute", "inventory.view")),
        item("BOMs", "production:boms", "production:bom_detail", "production:bom_line_add",
             "production:bom_line_remove", "production:bom_action", needs=("production.bom", "inventory.view")),
        item("Production report", "production:report", needs="inventory.view"),
    )),
    # Sales (Phase 6): order, reserve, deliver, invoice, return.
    ("sales", "Sales", (
        item("Sales orders", "sales:orders", "sales:order_detail", "sales:order_line_add",
             "sales:order_line_remove", "sales:order_action",
             needs=("sales.order", "sales.approve", "sales.deliver", "inventory.view")),
        item("Deliveries", "sales:deliveries", "sales:delivery_detail", "sales:delivery_line_add",
             "sales:delivery_line_remove", "sales:delivery_scan", "sales:delivery_action", "sales:delivery_pdf",
             needs=("sales.deliver", "inventory.view")),
        item("Invoices", "sales:invoices", "sales:invoice_detail", "sales:invoice_action", "sales:invoice_pdf",
             needs=("sales.invoice", "inventory.view")),
        item("Sales returns", "sales:returns", "sales:return_detail", "sales:return_line_add",
             "sales:return_line_remove", "sales:return_action", needs=("sales.deliver", "inventory.view")),
        item("Customers", "inventory:customers", needs="inventory.view"),
        item("Price lists", "sales:price_lists", "sales:price_list_detail", "sales:price_remove",
             needs=("sales.order", "inventory.view")),
        # Phase 8: orders read by the AI into drafts; a person confirms each.
        item("AI order inbox", "ai_assistant:inbox", "ai_assistant:message", "ai_assistant:message_action",
             needs=("sales.order", "sales.approve")),
        item("AI assistant settings", "ai_assistant:settings", "ai_assistant:recipient_add",
             "ai_assistant:recipient_remove", manage=True, needs=("sales.order", "inventory.view")),
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
        item("ERP webhook", "webhooks:settings", "webhooks:guide", manage=True),
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
                  can_see_salary=None, report_kinds=None, allowed=None, people_codes=None,
                  module_codes=frozenset()):
    """The company menus for this request.

    ``allowed`` (A12): for a branch manager or a person given access, a check
    on each entry's page (``access_control.page_access``) replaces the role
    flags — they see exactly the branch pages they may open.

    ``people_codes`` (HR): the employees permissions held; an entry marked
    ``people`` is shown when its code is among them.
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
            # ``module_codes``: the module permissions held, empty when the
            # company lacks the module - its entries are then never shown.
            needs = entry["needs"]
            if needs and not module_codes.intersection((needs,) if isinstance(needs, str) else needs):
                continue
            if allowed is not None:
                if not allowed(entry["view"]):
                    continue
            elif people_codes is not None and entry["people"]:
                if entry["people"] not in people_codes:
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
