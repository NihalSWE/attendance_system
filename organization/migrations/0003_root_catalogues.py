"""Turn Department and Designation into root-owned global catalogues.

Runs last on purpose. By this point every company-specific row — employee
assignments, department shift links, permission rules and membership scopes —
already points at ``CompanyDepartment`` / ``CompanyDesignation``, so the
``company`` and ``branch`` columns on the catalogue rows have no readers left.

Two things happen:

1. **Collapse.** Ten tenants each created their own "Human Resources" row. Those
   are the same catalogue entry, so they merge into one and every adoption row
   is repointed at the survivor. Departments merge on name; designations merge
   on (surviving department, name).

2. **Strip.** ``company``, ``branch`` and the branch-scoped uniqueness go away,
   and ``code`` / ``name`` become globally unique. ``Designation.parent`` and
   ``hierarchy_level`` go with them: the access ceiling is now carried by
   ``access_control.DepartmentPermission``, not by walking a chain of titles.

The collapse is not reversible in the honest sense. Reversing this migration
rebuilds one catalogue row per adoption row, which restores a working schema but
not the exact ids that existed before.
"""

import django.db.models.deletion
import django.db.models.manager
from django.conf import settings
from django.db import migrations, models


def _merge_key(value):
    return " ".join((value or "").split()).casefold()


def collapse_catalogues(apps, schema_editor):
    """Merge per-company catalogue rows into one global row each."""
    Department = apps.get_model("organization", "Department")
    Designation = apps.get_model("organization", "Designation")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")

    # The parent chain is on its way out two operations below, and it is a
    # PROTECT self-reference: a merged-away title cannot be deleted while some
    # other title still names it as a parent. Break the chain first.
    Designation.objects.exclude(parent__isnull=True).update(parent=None)

    # ------------------------------------------------------------ departments
    survivors = {}
    used_codes = set()
    doomed = []
    for department in Department.objects.order_by("id"):
        key = _merge_key(department.name)
        survivor = survivors.get(key)
        if survivor is None:
            survivors[key] = department
            department.code = _unique_code(department.code, used_codes)
            department.save(update_fields=["code"])
            continue
        CompanyDepartment.objects.filter(department_id=department.id).update(
            department_id=survivor.id
        )
        Designation.objects.filter(department_id=department.id).update(
            department_id=survivor.id
        )
        doomed.append(department.id)
    Department.objects.filter(id__in=doomed).delete()

    # ----------------------------------------------------------- designations
    survivors = {}
    used_codes = {}
    doomed = []
    for designation in Designation.objects.order_by("id"):
        key = (designation.department_id, _merge_key(designation.name))
        survivor = survivors.get(key)
        if survivor is None:
            survivors[key] = designation
            taken = used_codes.setdefault(designation.department_id, set())
            designation.code = _unique_code(designation.code, taken)
            designation.save(update_fields=["code"])
            continue
        CompanyDesignation.objects.filter(designation_id=designation.id).update(
            designation_id=survivor.id
        )
        doomed.append(designation.id)
    Designation.objects.filter(id__in=doomed).delete()

    # Django creates foreign keys as DEFERRABLE INITIALLY DEFERRED, so the
    # updates and deletes above leave trigger events queued. PostgreSQL refuses
    # to ALTER a table in that state, and the column drops below run inside this
    # same transaction. Force the checks now.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def _unique_code(code, taken):
    """Keep the original code unless another survivor already claimed it."""
    candidate = code
    suffix = 2
    while candidate in taken:
        candidate = f"{code}-{suffix}"
        suffix += 1
    taken.add(candidate)
    return candidate


def split_catalogues(apps, schema_editor):
    """Reverse: give every adoption row its own catalogue row again."""
    Department = apps.get_model("organization", "Department")
    Designation = apps.get_model("organization", "Designation")
    CompanyDepartment = apps.get_model("organization", "CompanyDepartment")
    CompanyDesignation = apps.get_model("organization", "CompanyDesignation")

    for adoption in CompanyDepartment.objects.select_related("department"):
        catalogue = adoption.department
        clone = Department.objects.create(
            company_id=adoption.company_id,
            branch_id=adoption.branch_id,
            code=catalogue.code,
            name=catalogue.name,
            description=catalogue.description,
            status=catalogue.status,
            opened_on=adoption.opened_on,
            closed_on=adoption.closed_on,
        )
        CompanyDepartment.objects.filter(pk=adoption.pk).update(department_id=clone.id)

    for adoption in CompanyDesignation.objects.select_related(
        "designation", "company_department"
    ):
        catalogue = adoption.designation
        clone = Designation.objects.create(
            company_id=adoption.company_id,
            department_id=adoption.company_department.department_id,
            code=catalogue.code,
            name=catalogue.name,
            description=catalogue.description,
            status=catalogue.status,
        )
        CompanyDesignation.objects.filter(pk=adoption.pk).update(
            designation_id=clone.id
        )

    used = set(CompanyDesignation.objects.values_list("designation_id", flat=True))
    Designation.objects.exclude(id__in=used).delete()
    used = set(CompanyDepartment.objects.values_list("department_id", flat=True))
    Department.objects.exclude(id__in=used).delete()

    # Django creates foreign keys as DEFERRABLE INITIALLY DEFERRED, so the
    # updates and deletes above leave trigger events queued. PostgreSQL refuses
    # to ALTER a table in that state, and the column drops below run inside this
    # same transaction. Force the checks now.
    schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        ("organization", "0002_company_department_designation"),
        # Every reader must have moved to the adoption rows before the catalogue
        # loses its company column.
        ("employees", "0002_assignment_company_department"),
        ("scheduling", "0002_departmentshift_company_department"),
        ("access_control", "0002_department_permission"),
        ("accounts", "0005_membership_company_department_scope"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="department", name="uniq_department_code_per_branch"
        ),
        migrations.RemoveConstraint(
            model_name="department", name="uniq_department_name_per_branch"
        ),
        migrations.RemoveConstraint(
            model_name="designation", name="designation_parent_not_self"
        ),
        # Dropped for the duration of the collapse and rebuilt straight after.
        # Merging two departments moves their titles under one parent, and two
        # companies can each own a "MGR" — which collides for the moment between
        # the department merge and the title merge.
        migrations.RemoveConstraint(
            model_name="designation", name="uniq_designation_code_per_department"
        ),
        migrations.RunPython(collapse_catalogues, split_catalogues),
        migrations.AddConstraint(
            model_name="designation",
            constraint=models.UniqueConstraint(
                fields=("department", "code"),
                name="uniq_designation_code_per_department",
            ),
        ),
        migrations.RemoveField(model_name="designation", name="parent"),
        migrations.RemoveField(model_name="designation", name="hierarchy_level"),
        migrations.RemoveField(model_name="designation", name="company"),
        migrations.RemoveField(model_name="department", name="branch"),
        migrations.RemoveField(model_name="department", name="company"),
        migrations.RemoveField(model_name="department", name="opened_on"),
        migrations.RemoveField(model_name="department", name="closed_on"),
        migrations.AlterField(
            model_name="department",
            name="code",
            field=models.CharField(max_length=32, unique=True),
        ),
        migrations.AlterField(
            model_name="department",
            name="name",
            field=models.CharField(max_length=255, unique=True),
        ),
        migrations.AddConstraint(
            model_name="designation",
            constraint=models.UniqueConstraint(
                fields=("department", "name"), name="uniq_designation_name_per_department"
            ),
        ),
        migrations.AlterModelOptions(
            name="department", options={"ordering": ("name",)}
        ),
        migrations.AlterModelOptions(
            name="designation", options={"ordering": ("department__name", "name")}
        ),
        migrations.AlterModelManagers(name="department", managers=[]),
        migrations.AlterModelManagers(name="designation", managers=[]),
    ]
