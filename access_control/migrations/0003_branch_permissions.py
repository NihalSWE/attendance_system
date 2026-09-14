"""A12 part 1: the branch permission catalogue.

Adds the features and permissions that ``access_control.branch_access`` checks.
Existing rows with the same code (for example ``leave.view`` from seed_demo) are
kept as they are. Data only; no schema change.
"""

from django.db import migrations

FEATURES = (
    ("employees", "Employees", 5),
    ("leave", "Leave management", 10),
    ("payroll", "Payroll", 30),
    ("access", "Access", 40),
)

PERMISSIONS = (
    ("employees.view", "View employees", "employees", "view"),
    ("employees.edit", "Create and edit employees", "employees", "edit"),
    ("employees.logins", "Create and manage logins", "employees", "manage"),
    ("leave.view", "View leave", "leave", "view"),
    ("leave.record", "Record and cancel leave", "leave", "create"),
    ("leave.approve", "Approve leave requests", "leave", "approve"),
    ("overtime.view", "View overtime", "payroll", "view"),
    ("overtime.decide", "Decide overtime", "payroll", "approve"),
    ("salary.view", "View salaries and payslips", "payroll", "view"),
    ("salary.prepare", "Generate salary and add bonus or deduction lines", "payroll", "edit"),
    ("access.grant", "Give access to others", "access", "manage"),
)


def seed(apps, schema_editor):
    Feature = apps.get_model("tenants", "Feature")
    AccessPermission = apps.get_model("access_control", "AccessPermission")
    for code, name, order in FEATURES:
        Feature.objects.get_or_create(code=code, defaults={"name": name, "sort_order": order})
    for code, name, feature_code, action in PERMISSIONS:
        AccessPermission.objects.get_or_create(
            code=code,
            defaults={
                "name": name,
                "action": action,
                "feature": Feature.objects.get(code=feature_code),
                "is_sensitive": feature_code == "payroll",
            },
        )


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0002_department_permission"),
        ("tenants", "0005_alter_feature_table"),
    ]

    operations = [
        migrations.RunPython(seed, migrations.RunPython.noop),
    ]
