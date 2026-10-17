"""What an API key may do. Each endpoint that a key may call names its scope;
a key without it is refused (``scope_missing``). People are not limited by
scopes - their role in the company decides, as in the panels."""

SCOPES = {
    "company:read": "Read the company profile, branches, departments, designations.",
    "company:write": "Change the company profile, branches, departments, designations.",
    "employees:read": "Read employees and their profiles.",
    "employees:write": "Create and change employees.",
    "shifts:read": "Read shifts, attendance settings, weekly offs, holidays.",
    "shifts:write": "Change shifts, attendance settings, weekly offs, holidays.",
    "devices:read": "Read devices, their users, messages, punches and commands.",
    "devices:write": "Register and change devices; send commands and users to them.",
    "punches:write": "Push scans from a device that is not a ZKTeco terminal.",
    "attendance:read": "Read attendance days, calendars, reviews and exports.",
    "attendance:write": "Fix days and decide missed scans.",
    "leave:read": "Read leave types, policies, balances and records.",
    "leave:write": "Record, change and decide leave.",
    "payroll:read": "Read salary runs, payslips, components, overtime.",
    "payroll:write": "Run and change salary.",
    "reports:read": "Read reports and exports (the inventory reports too).",
    # Store & Inventory (Phase 9 of the inventory plan).
    "inventory:read": "Read items, stores, stock, batches, labels and store documents.",
    "inventory:write": "Change items and stores; enter, approve and post store documents and month-end close.",
    "purchase:read": "Read purchase requisitions, orders, goods receipts and returns.",
    "purchase:write": "Enter, approve and post purchase documents; record quality checks.",
    "production:read": "Read BOMs, production orders and material use.",
    "production:write": "Set up BOMs; plan, release and run production; record output.",
    "sales:read": "Read sales orders, deliveries, invoices and returns.",
    "sales:write": "Enter and confirm sales orders, send orders in, deliver, invoice and record payments.",
    "webhook:manage": "Manage the ERP webhook.",
}
