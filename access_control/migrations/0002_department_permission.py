"""Move the access model from a title hierarchy to a department ceiling.

Three things happen here:

1. ``DesignationPermission`` and the override department scope repoint at the
   company adoption rows, for the same reason as everywhere else: a permission
   rule belongs to one company, and the catalogue rows are about to go global.
2. ``DepartmentPermission`` arrives. The department is now the unit of
   delegation, and its rules cap what a designation or an individual grant may
   reach.
3. Nothing is dropped from ``Designation`` here. Its ``parent`` column, and the
   ceiling walk that used it, are removed in ``organization.0003`` because the
   rule it enforced now lives on the department instead.
"""

import django.contrib.postgres.constraints
import django.contrib.postgres.fields.ranges
import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models

import common.db


def _tstz_period():
    """A fresh range expression per constraint; Func instances are not shared."""
    return common.db.TstzRange(
        "effective_from",
        "effective_to",
        django.contrib.postgres.fields.ranges.RangeBoundary(),
    )


def forwards(apps, schema_editor):
    DesignationPermission = apps.get_model("access_control", "DesignationPermission")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")
    mapping = dict(CompanyDesignation.objects.values_list("designation_id", "id"))
    for rule in DesignationPermission.objects.all().iterator():
        DesignationPermission.objects.filter(pk=rule.pk).update(
            company_designation_id=mapping[rule.designation_id]
        )

    # The override scope is a many-to-many, so its through rows move as well.
    Override = apps.get_model("access_control", "EmployeePermissionOverride")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    departments = dict(CompanyDepartment.objects.values_list("department_id", "id"))
    for override in Override.objects.all().iterator():
        old_ids = list(override.allowed_departments.values_list("id", flat=True))
        if old_ids:
            override.allowed_company_departments.set(
                [departments[old_id] for old_id in old_ids]
            )


def backwards(apps, schema_editor):
    DesignationPermission = apps.get_model("access_control", "DesignationPermission")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")
    mapping = dict(CompanyDesignation.objects.values_list("id", "designation_id"))
    for rule in DesignationPermission.objects.all().iterator():
        DesignationPermission.objects.filter(pk=rule.pk).update(
            designation_id=mapping[rule.company_designation_id]
        )

    Override = apps.get_model("access_control", "EmployeePermissionOverride")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    departments = dict(CompanyDepartment.objects.values_list("id", "department_id"))
    for override in Override.objects.all().iterator():
        new_ids = list(
            override.allowed_company_departments.values_list("id", flat=True)
        )
        if new_ids:
            override.allowed_departments.set(
                [departments[new_id] for new_id in new_ids]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("access_control", "0001_initial"),
        ("organization", "0002_company_department_designation"),
        ("tenants", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ------------------------------------------------ new department layer
        migrations.CreateModel(
            name="DepartmentPermission",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("access_level", models.CharField(choices=[("default", "Default (no opinion)"), ("allowed", "Allowed"), ("denied", "Denied")], default="default", max_length=16)),
                ("can_delegate", models.BooleanField(default=False)),
                ("effective_from", models.DateTimeField()),
                ("effective_to", models.DateTimeField(blank=True, null=True)),
                ("reason", models.TextField(blank=True)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="%(app_label)s_%(class)s_set", to="tenants.company")),
                ("company_department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="permission_rules", to="organization.companydepartment")),
                ("permission", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="department_rules", to="access_control.accesspermission")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "access_control_department_permission",
            },
            managers=[
                ("objects", django.db.models.manager.Manager()),
                ("all_objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AddIndex(
            model_name="departmentpermission",
            index=models.Index(fields=["company", "company_department", "permission"], name="access_cont_company_c788db_idx"),
        ),
        migrations.AddConstraint(
            model_name="departmentpermission",
            constraint=models.CheckConstraint(
                condition=models.Q(("effective_to__isnull", True), ("effective_to__gt", models.F("effective_from")), _connector="OR"),
                name="departmentpermission_end_after_start",
            ),
        ),
        migrations.AddConstraint(
            model_name="departmentpermission",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                expressions=[
                    ("company", "="),
                    ("company_department", "="),
                    ("permission", "="),
                    (_tstz_period(), "&&"),
                ],
                name="excl_departmentpermission_overlap",
            ),
        ),
        # ------------------------------------------------- repoint the old two
        migrations.AddField(
            model_name="designationpermission",
            name="company_designation",
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name="permission_rules", to="organization.companydesignation"),
        ),
        migrations.AddField(
            model_name="employeepermissionoverride",
            name="allowed_company_departments",
            field=models.ManyToManyField(blank=True, related_name="permission_overrides", to="organization.companydepartment"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveConstraint(model_name="designationpermission", name="excl_designationpermission_overlap"),
        migrations.RemoveIndex(model_name="designationpermission", name="access_cont_company_f9f05d_idx"),
        migrations.RemoveField(model_name="designationpermission", name="designation"),
        migrations.RenameField(model_name="designationpermission", old_name="company_designation", new_name="designation"),
        migrations.AlterField(
            model_name="designationpermission",
            name="designation",
            field=models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="permission_rules", to="organization.companydesignation"),
        ),
        migrations.AddIndex(
            model_name="designationpermission",
            index=models.Index(fields=["company", "designation", "permission"], name="access_cont_company_f9f05d_idx"),
        ),
        migrations.AddConstraint(
            model_name="designationpermission",
            constraint=django.contrib.postgres.constraints.ExclusionConstraint(
                expressions=[
                    ("company", "="),
                    ("designation", "="),
                    ("permission", "="),
                    (_tstz_period(), "&&"),
                ],
                name="excl_designationpermission_overlap",
            ),
        ),
        migrations.RemoveField(model_name="employeepermissionoverride", name="allowed_departments"),
        migrations.RenameField(model_name="employeepermissionoverride", old_name="allowed_company_departments", new_name="allowed_departments"),
    ]
