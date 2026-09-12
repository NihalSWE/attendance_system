"""Make the root designation list independent of departments.

Designations were filed under one catalogue department, which forced root to
create "Manager" once per department. The department-designation relation is
company-wise: it belongs on ``CompanyDesignation``, where one company can put
Manager under Sales while another puts it under Production.

Forward, titles that differed only by department collapse into one row per
name and every company placement is repointed at the survivor. Reverse gives
each title back the department of its first company placement, so the data
survives a rollback even though the split it originally had cannot be known.
"""

from django.db import migrations, models


def _merge_key(name):
    """Names differing only in spacing or case are the same designation."""
    return " ".join((name or "").split()).casefold()


def merge_duplicate_designations(apps, schema_editor):
    Designation = apps.get_model("organization", "Designation")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")

    survivors = {}
    superseded = {}
    for designation in Designation.objects.order_by("pk"):
        key = _merge_key(designation.name)
        keeper = survivors.get(key)
        if keeper is None:
            survivors[key] = designation
        else:
            superseded[designation.pk] = keeper

    if superseded:
        # Repoint company placements before the duplicates disappear. This
        # cannot collide with uniq_companydesignation_per_department: inside one
        # company department every title came from the same catalogue
        # department, where names were already unique.
        for link in CompanyDesignation.objects.filter(
            designation_id__in=superseded
        ).iterator():
            link.designation_id = superseded[link.designation_id].pk
            link.save(update_fields=["designation"])
        Designation.objects.filter(pk__in=superseded).delete()

    # Codes were unique per department, so two departments could each hold MGR.
    # They must be globally unique from here on.
    taken = set()
    for designation in Designation.objects.order_by("pk"):
        code = designation.code
        if code not in taken:
            taken.add(code)
            continue
        suffix = 2
        while f"{code}-{suffix}" in taken:
            suffix += 1
        designation.code = f"{code}-{suffix}"
        designation.save(update_fields=["code"])
        taken.add(designation.code)

    # Foreign keys are DEFERRABLE INITIALLY DEFERRED, so the writes above are
    # still pending when the ALTERs that follow run in this same transaction.
    # PostgreSQL refuses those with "cannot ALTER TABLE because it has pending
    # trigger events" unless the constraints are settled first. This already
    # bit 0003.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def restore_department_links(apps, schema_editor):
    """Reverse: re-file each designation under a department.

    The original split cannot be recovered, so the first company placement is
    used as the best evidence of where a title belonged; anything never placed
    falls back to the first department.
    """
    Designation = apps.get_model("organization", "Designation")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")
    Department = apps.get_model("organization", "Department")

    fallback = Department.objects.order_by("pk").first()
    for designation in Designation.objects.order_by("pk"):
        link = (
            CompanyDesignation.objects.filter(designation=designation)
            .select_related("company_department")
            .order_by("pk")
            .first()
        )
        department_id = (
            link.company_department.department_id
            if link is not None
            else (fallback.pk if fallback else None)
        )
        designation.department_id = department_id
        designation.save(update_fields=["department"])

    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0003_root_catalogues"),
    ]

    operations = [
        # 1. The per-department uniqueness no longer describes the model.
        migrations.RemoveConstraint(
            model_name="designation",
            name="uniq_designation_code_per_department",
        ),
        migrations.RemoveConstraint(
            model_name="designation",
            name="uniq_designation_name_per_department",
        ),
        # 2. Nullable first, so the reverse path can re-add the column before
        #    it knows which department to restore.
        migrations.AlterField(
            model_name="designation",
            name="department",
            field=models.ForeignKey(
                null=True,
                on_delete=models.deletion.PROTECT,
                related_name="designations",
                to="organization.department",
            ),
        ),
        # 3. Collapse duplicates and settle the codes.
        migrations.RunPython(
            merge_duplicate_designations, restore_department_links
        ),
        # 4. Drop the link and make the flat list globally unique.
        migrations.RemoveField(
            model_name="designation",
            name="department",
        ),
        migrations.AlterField(
            model_name="designation",
            name="code",
            field=models.CharField(max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="designation",
            name="name",
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.AlterModelOptions(
            name="designation",
            options={"ordering": ("name",)},
        ),
    ]
