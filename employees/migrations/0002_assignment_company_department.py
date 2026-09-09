"""Point employee placements at the company adoption rows.

An assignment must always name a department and designation *inside its own
company*. Once the catalogue rows go global in ``organization.0003`` they are no
longer safe to point at, so the FKs move here first — while the one-to-one map
built by ``organization.0002`` is still available to translate the old ids.

The move is add / backfill / drop / rename rather than a straight AlterField,
because the new rows have different primary keys.
"""

import django.db.models.deletion
from django.db import migrations, models


def _adoption_maps(apps):
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")
    departments = dict(CompanyDepartment.objects.values_list("department_id", "id"))
    designations = dict(CompanyDesignation.objects.values_list("designation_id", "id"))
    return departments, designations


def forwards(apps, schema_editor):
    EmployeeAssignment = apps.get_model("employees", "EmployeeAssignment")
    departments, designations = _adoption_maps(apps)
    for assignment in EmployeeAssignment.objects.all().iterator():
        EmployeeAssignment.objects.filter(pk=assignment.pk).update(
            company_department_id=departments[assignment.department_id],
            company_designation_id=designations[assignment.designation_id],
        )


def backwards(apps, schema_editor):
    EmployeeAssignment = apps.get_model("employees", "EmployeeAssignment")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")
    departments = dict(CompanyDepartment.objects.values_list("id", "department_id"))
    designations = dict(CompanyDesignation.objects.values_list("id", "designation_id"))
    for assignment in EmployeeAssignment.objects.all().iterator():
        EmployeeAssignment.objects.filter(pk=assignment.pk).update(
            department_id=departments[assignment.company_department_id],
            designation_id=designations[assignment.company_designation_id],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("employees", "0001_initial"),
        ("organization", "0002_company_department_designation"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeeassignment",
            name="company_department",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="organization.companydepartment"),
        ),
        migrations.AddField(
            model_name="employeeassignment",
            name="company_designation",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="organization.companydesignation"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="employeeassignment", name="department"),
        migrations.RemoveField(model_name="employeeassignment", name="designation"),
        migrations.RenameField(model_name="employeeassignment", old_name="company_department", new_name="department"),
        migrations.RenameField(model_name="employeeassignment", old_name="company_designation", new_name="designation"),
        migrations.AlterField(
            model_name="employeeassignment",
            name="department",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="organization.companydepartment"),
        ),
        migrations.AlterField(
            model_name="employeeassignment",
            name="designation",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="assignments", to="organization.companydesignation"),
        ),
    ]
