"""Point the membership department scope at the company adoption rows.

``allowed_departments`` narrows where a member may act. It must name that
company's own departments, not catalogue entries every tenant shares.
"""

from django.db import migrations, models


def forwards(apps, schema_editor):
    Membership = apps.get_model("accounts", "CompanyMembership")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    mapping = dict(CompanyDepartment.objects.values_list("department_id", "id"))
    for membership in Membership.objects.all().iterator():
        old_ids = list(membership.allowed_departments.values_list("id", flat=True))
        if old_ids:
            membership.allowed_company_departments.set(
                [mapping[old_id] for old_id in old_ids]
            )


def backwards(apps, schema_editor):
    Membership = apps.get_model("accounts", "CompanyMembership")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    mapping = dict(CompanyDepartment.objects.values_list("id", "department_id"))
    for membership in Membership.objects.all().iterator():
        new_ids = list(
            membership.allowed_company_departments.values_list("id", flat=True)
        )
        if new_ids:
            membership.allowed_departments.set(
                [mapping[new_id] for new_id in new_ids]
            )


class Migration(migrations.Migration):

    dependencies = [
        ("accounts", "0004_companymembership_uniq_current_company_administrator_and_more"),
        ("organization", "0002_company_department_designation"),
    ]

    operations = [
        migrations.AddField(
            model_name="companymembership",
            name="allowed_company_departments",
            field=models.ManyToManyField(blank=True, related_name="scoped_memberships", to="organization.companydepartment"),
        ),
        migrations.RunPython(forwards, backwards),
        migrations.RemoveField(model_name="companymembership", name="allowed_departments"),
        migrations.RenameField(model_name="companymembership", old_name="allowed_company_departments", new_name="allowed_departments"),
    ]
