"""Introduce the per-company adoption rows for departments and designations.

This migration only ADDS. It creates ``CompanyDepartment`` and
``CompanyDesignation`` and fills them from the tenant-owned departments and
designations that exist today, so that every employee assignment, shift link
and permission rule has a company row to be repointed at.

The catalogue rows are not stripped of their company/branch columns until
``0003_root_catalogues``, which runs after every dependant app has moved across.
Doing it in that order is the whole point: the old columns are the only source
of truth for which company adopted what.
"""

import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models


def adopt_existing(apps, schema_editor):
    """Give every existing department and designation a company adoption row."""
    Department = apps.get_model("organization", "Department")
    Designation = apps.get_model("organization", "Designation")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")

    department_map = {}
    for department in Department.objects.all().iterator():
        adoption = CompanyDepartment.objects.create(
            company_id=department.company_id,
            branch_id=department.branch_id,
            department_id=department.id,
            description=department.description,
            status=department.status,
            opened_on=department.opened_on,
            closed_on=department.closed_on,
            created_by_id=department.created_by_id,
            updated_by_id=department.updated_by_id,
        )
        department_map[department.id] = adoption.id

    for designation in Designation.objects.all().iterator():
        CompanyDesignation.objects.create(
            company_id=designation.company_id,
            company_department_id=department_map[designation.department_id],
            designation_id=designation.id,
            status=designation.status,
            created_by_id=designation.created_by_id,
            updated_by_id=designation.updated_by_id,
        )


def drop_adoptions(apps, schema_editor):
    apps.get_model("organization", "CompanyDesignation").objects.all().delete()
    apps.get_model("organization", "CompanyDepartment").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0001_initial"),
        # CompanyDepartment.head points at an employee.
        ("employees", "0001_initial"),
        ("tenants", "0001_initial"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CompanyDepartment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("description", models.TextField(blank=True)),
                ("status", models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=16)),
                ("opened_on", models.DateField(blank=True, null=True)),
                ("closed_on", models.DateField(blank=True, null=True)),
                ("branch", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_departments", to="organization.branch")),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="%(app_label)s_%(class)s_set", to="tenants.company")),
                ("department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_links", to="organization.department")),
                ("head", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="headed_departments", to="employees.employee")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "organization_company_department",
                "ordering": ("branch__name", "department__name"),
            },
            managers=[
                ("objects", django.db.models.manager.Manager()),
                ("all_objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.CreateModel(
            name="CompanyDesignation",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("status", models.CharField(choices=[("active", "Active"), ("inactive", "Inactive")], default="active", max_length=16)),
                ("company", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="%(app_label)s_%(class)s_set", to="tenants.company")),
                ("company_department", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="designations", to="organization.companydepartment")),
                ("designation", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="company_links", to="organization.designation")),
                ("created_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
                ("updated_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="+", to=settings.AUTH_USER_MODEL)),
            ],
            options={
                "db_table": "organization_company_designation",
                "ordering": ("company_department__department__name", "designation__name"),
            },
            managers=[
                ("objects", django.db.models.manager.Manager()),
                ("all_objects", django.db.models.manager.Manager()),
            ],
        ),
        migrations.AddIndex(
            model_name="companydepartment",
            index=models.Index(fields=["company", "branch"], name="organizatio_company_31e7ff_idx"),
        ),
        migrations.AddConstraint(
            model_name="companydepartment",
            constraint=models.UniqueConstraint(fields=("branch", "department"), name="uniq_companydepartment_per_branch"),
        ),
        migrations.AddIndex(
            model_name="companydesignation",
            index=models.Index(fields=["company", "company_department"], name="organizatio_company_eb7205_idx"),
        ),
        migrations.AddConstraint(
            model_name="companydesignation",
            constraint=models.UniqueConstraint(fields=("company_department", "designation"), name="uniq_companydesignation_per_department"),
        ),
        migrations.RunPython(adopt_existing, drop_adoptions),
    ]
