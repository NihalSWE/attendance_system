"""Root-owned catalogue writes: the platform's department and job-title lists.

These rows are shared by every tenant, so only the platform owner may write
them and nothing here ever takes a company context. A catalogue row is not
company data — it is the canonical name that companies adopt through
``CompanyDepartment`` / ``CompanyDesignation``.

Two rules the callers must not be able to bypass:

- **Never hard-delete.** A catalogue row that any company has adopted is
  referenced by a PROTECTed foreign key. Deactivation keeps the historical
  name resolvable; deletion would orphan every adoption that used it.
- **Every write is validated and audited.** Writes go through
  ``create_validated`` so model ``clean()`` and the uniqueness constraints
  run, and each one records an audit entry.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Count, Q

from auditlog.services import record_platform_event
from common.choices import ActiveStatus
from common.services import create_validated
from organization.models import (
    CompanyDepartment,
    CompanyDesignation,
    Department,
    Designation,
)

DEPARTMENT_FIELDS = ("code", "name", "description", "status")
DESIGNATION_FIELDS = ("department", "code", "name", "description", "status")


def require_platform_owner(actor):
    """Root only. Deliberately re-implemented rather than imported from tenants
    so the organisation app does not depend on the tenants app for its own
    authorization rule."""
    if not actor or not actor.is_authenticated or not actor.is_active or not actor.is_superuser:
        raise PermissionDenied("Platform owner access is required.")


def _snapshot(obj, fields):
    """Audit snapshot; foreign keys are recorded by id, not repr."""
    data = {}
    for field in fields:
        value = getattr(obj, field)
        data[field] = value.pk if hasattr(value, "pk") else value
    return data


def _writable(values, allowed):
    """Whitelist. A crafted POST must not reach a column the form omits."""
    return {key: values[key] for key in allowed if key in values}


# --- departments ---------------------------------------------------------


def department_queryset():
    """Catalogue departments with their adoption and title counts."""
    return Department.objects.annotate(
        designation_count=Count("designations", distinct=True),
        adoption_count=Count("company_links", distinct=True),
    )


@transaction.atomic
def create_department(*, actor, values):
    require_platform_owner(actor)
    department = create_validated(
        Department, created_by=actor, updated_by=actor,
        **_writable(values, DEPARTMENT_FIELDS),
    )
    record_platform_event(
        actor=actor, company=None, action="catalogue.department.created",
        obj=department, after=_snapshot(department, DEPARTMENT_FIELDS),
    )
    return department


@transaction.atomic
def update_department(*, actor, department_id, values):
    require_platform_owner(actor)
    department = Department.objects.get(pk=department_id)
    before = _snapshot(department, DEPARTMENT_FIELDS)
    for field, value in _writable(values, DEPARTMENT_FIELDS).items():
        setattr(department, field, value)
    department.updated_by = actor
    department.full_clean()
    department.save()
    record_platform_event(
        actor=actor, company=None, action="catalogue.department.updated",
        obj=department, before=before, after=_snapshot(department, DEPARTMENT_FIELDS),
    )
    return department


@transaction.atomic
def set_department_status(*, actor, department_id, status):
    """Activate or deactivate. Never deletes.

    Deactivating a department that companies still use is allowed and
    deliberate: it stops new adoptions without breaking the existing ones,
    which is the whole point of having a status instead of a delete.
    """
    require_platform_owner(actor)
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    department = Department.objects.get(pk=department_id)
    before = _snapshot(department, DEPARTMENT_FIELDS)
    department.status = status
    department.updated_by = actor
    department.full_clean()
    department.save()
    record_platform_event(
        actor=actor, company=None, action="catalogue.department.status_changed",
        obj=department, before=before, after=_snapshot(department, DEPARTMENT_FIELDS),
    )
    return department


def department_usage(department):
    """Which companies have adopted this department, for the UI to explain."""
    return CompanyDepartment.all_objects.filter(
        department=department
    ).select_related("company", "branch").order_by("company__name", "branch__name")


# --- designations --------------------------------------------------------


def designation_queryset():
    return Designation.objects.select_related("department").annotate(
        adoption_count=Count("company_links", distinct=True)
    )


@transaction.atomic
def create_designation(*, actor, values):
    require_platform_owner(actor)
    designation = create_validated(
        Designation, created_by=actor, updated_by=actor,
        **_writable(values, DESIGNATION_FIELDS),
    )
    record_platform_event(
        actor=actor, company=None, action="catalogue.designation.created",
        obj=designation, after=_snapshot(designation, DESIGNATION_FIELDS),
    )
    return designation


@transaction.atomic
def update_designation(*, actor, designation_id, values):
    """Update a job title.

    Moving a title to a different catalogue department is refused once any
    company has adopted it: their CompanyDesignation rows are validated
    against the department they were filed under, and silently repointing the
    catalogue would leave those adoptions describing a title that no longer
    belongs to their department.
    """
    require_platform_owner(actor)
    designation = Designation.objects.get(pk=designation_id)
    before = _snapshot(designation, DESIGNATION_FIELDS)
    changes = _writable(values, DESIGNATION_FIELDS)

    new_department = changes.get("department")
    if (
        new_department is not None
        and new_department.pk != designation.department_id
        and CompanyDesignation.all_objects.filter(designation=designation).exists()
    ):
        raise ValidationError({
            "department": (
                "This job title is already in use by a company, so it cannot be "
                "moved to another department. Deactivate it and create a new "
                "title under the other department instead."
            )
        })

    for field, value in changes.items():
        setattr(designation, field, value)
    designation.updated_by = actor
    designation.full_clean()
    designation.save()
    record_platform_event(
        actor=actor, company=None, action="catalogue.designation.updated",
        obj=designation, before=before, after=_snapshot(designation, DESIGNATION_FIELDS),
    )
    return designation


@transaction.atomic
def set_designation_status(*, actor, designation_id, status):
    require_platform_owner(actor)
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    designation = Designation.objects.get(pk=designation_id)
    before = _snapshot(designation, DESIGNATION_FIELDS)
    designation.status = status
    designation.updated_by = actor
    designation.full_clean()
    designation.save()
    record_platform_event(
        actor=actor, company=None, action="catalogue.designation.status_changed",
        obj=designation, before=before, after=_snapshot(designation, DESIGNATION_FIELDS),
    )
    return designation


def designation_usage(designation):
    return CompanyDesignation.all_objects.filter(
        designation=designation
    ).select_related("company", "company_department__branch").order_by("company__name")
