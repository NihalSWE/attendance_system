"""Point department shift links at the company adoption rows.

The two exclusion constraints on this table are expressed over ``department``,
so they are dropped before the column moves and rebuilt afterwards. Leaving them
in place would make PostgreSQL refuse to drop the column they depend on.
"""

import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
import django.db.models.deletion
from django.db import migrations, models

import common.db


def _date_period():
    """A fresh range expression per constraint — Func instances are not shared."""
    return common.db.DateRange(
        "effective_from",
        "effective_to",
        django.contrib.postgres.fields.ranges.RangeBoundary(),
    )


def forwards(apps, schema_editor):
    DepartmentShift = apps.get_model("scheduling", "DepartmentShift")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    mapping = dict(CompanyDepartment.objects.values_list("department_id", "id"))
    for link in DepartmentShift.objects.all().iterator():
        DepartmentShift.objects.filter(pk=link.pk).update(
            company_department_id=mapping[link.department_id]
        )


def backwards(apps, schema_editor):
    DepartmentShift = apps.get_model("scheduling", "DepartmentShift")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    mapping = dict(CompanyDepartment.objects.values_list("id", "department_id"))
    for link in DepartmentShift.objects.all().iterator():
        DepartmentShift.objects.filter(pk=link.pk).update(
            department_id=mapping[link.company_department_id]
        )


class Migration(migrations.Migration):

    dependencies = [
        ("scheduling", "0001_initial"),
        ("organization", "0002_company_department_designation"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="departmentshift", name="excl_departmentshift_duplicate_period"),
        migrations.RemoveConstraint(model_name="departmentshift", name="excl_departmentshift_single_default"),
        migrations.AddField(
            model_name="departmentshift",
            name="company_department",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="shift_links", to="organization.companydepartment"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="departmentshift", name="department"),
        migrations.RenameField(model_name="departmentshift", old_name="company_department", new_name="department"),
        migrations.AlterField(
            model_name="departmentshift",
            name="department",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="shift_links", to="organization.companydepartment"),
        ),
        migrations.AddConstraint(
            model_name="departmentshift",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                expressions=[
                    ("company", "="),
                    ("department", "="),
                    ("shift", "="),
                    (_date_period(), "&&"),
                ],
                name="excl_departmentshift_duplicate_period",
            ),
        ),
        migrations.AddConstraint(
            model_name="departmentshift",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                condition=models.Q(("is_default", True)),
                expressions=[
                    ("company", "="),
                    ("department", "="),
                    (_date_period(), "&&"),
                ],
                name="excl_departmentshift_single_default",
            ),
        ),
    ]
