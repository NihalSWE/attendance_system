"""Company-scoped writes for a company's own departments and designations.

Departments and designations are company-owned (see PHASE_STATUS.md). This
module keeps the same contract as ``organization.services`` and reuses its
authorization helpers:

1. **Membership** - the actor holds an active CompanyMembership here.
2. **Action permission** - their role may manage organization structure.
3. **Row scope** - a branch-restricted member may only touch their branches,
   re-checked against submitted ids because a form is not a security boundary.

Every write shares one transaction with its AuditLog row. Function names are
kept from the earlier "adoption" model so the views and tests keep calling the
same API; there is no longer a root catalogue to adopt from, so each call
simply creates the company's own rows.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.models import Department, Designation
from organization.services import assert_branch_in_scope, require_structure_manager

DEPARTMENT_FIELDS = (
    "branch", "code", "name", "head", "description", "status",
    "opened_on", "closed_on",
)


def adoption_snapshot(department):
    """Audit snapshot including the designations, so a change is visible."""
    return {
        "branch_id": department.branch_id,
        "code": department.code,
        "name": department.name,
        "head_id": department.head_id,
        "description": department.description,
        "status": department.status,
        "titles": sorted(department.designations.values_list("name", flat=True)),
    }


def visible_adoptions(membership):
    """Departments this membership may see, respecting branch scope."""
    queryset = (
        Department.objects.select_related("branch", "head")
        .prefetch_related("designations")
    )
    if membership.allowed_branches.exists():
        queryset = queryset.filter(branch__in=membership.allowed_branches.values("pk"))
    return queryset


def get_adoption_for_edit(*, actor, company_id, adoption_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        department = (
            Department.objects.select_related("branch").filter(pk=adoption_id).first()
        )
        if department is None:
            raise PermissionDenied("Department not found in this company.")
        assert_branch_in_scope(membership, department.branch)
    return membership, department


def _reject_unsupported(values):
    unsupported = set(values) - set(DEPARTMENT_FIELDS)
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")


def _clean_titles(designations):
    """Normalise a list of {code, name} title dicts; drop blanks."""
    titles = []
    for entry in designations or []:
        code = str(entry.get("code", "")).strip()
        name = str(entry.get("name", "")).strip()
        if not code and not name:
            continue
        if not code or not name:
            raise ValidationError({"designations": "Each title needs a code and a name."})
        titles.append({"code": code, "name": name})
    return titles


@transaction.atomic
def adopt_department(*, actor, company_id, values):
    """Create a department in a branch, with any designations given."""
    membership = require_structure_manager(actor, company_id)
    values = dict(values)
    titles = _clean_titles(values.pop("designations", []))
    _reject_unsupported(values)

    branch = values.get("branch")
    if branch is None or not values.get("code") or not values.get("name"):
        raise ValidationError("A branch, code and name are required.")

    with use_company(company_id):
        assert_branch_in_scope(membership, branch)
        if Department.objects.filter(branch=branch, code=values["code"]).exists():
            raise ValidationError({
                "code": f"{branch.name} already has a department with code "
                        f"{values['code']}."
            })
        department = create_validated(
            Department, company=membership.company,
            created_by=actor, updated_by=actor, **values,
        )
        for title in titles:
            create_validated(
                Designation, company=membership.company, department=department,
                code=title["code"], name=title["name"],
                created_by=actor, updated_by=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="department.created", obj=department,
            after=adoption_snapshot(department),
        )
    return department


@transaction.atomic
def update_adoption(*, actor, company_id, adoption_id, values):
    """Edit one of the company's departments (not its designations here)."""
    membership, department = get_adoption_for_edit(
        actor=actor, company_id=company_id, adoption_id=adoption_id
    )
    values = dict(values)
    values.pop("designations", None)
    # Branch is fixed once created: changing it would move every employee filed
    # under this department to another branch silently.
    values.pop("branch", None)
    _reject_unsupported(values)

    with use_company(company_id):
        before = adoption_snapshot(department)
        for field, value in values.items():
            setattr(department, field, value)
        department.updated_by = actor
        department.full_clean()
        department.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="department.updated", obj=department,
            before=before, after=adoption_snapshot(department),
        )
    return department


@transaction.atomic
def add_designation(*, actor, company_id, adoption_id, code, name):
    """Add (or reactivate) a designation under one of the company's departments."""
    membership, department = get_adoption_for_edit(
        actor=actor, company_id=company_id, adoption_id=adoption_id
    )
    code, name = str(code).strip(), str(name).strip()
    if not code or not name:
        raise ValidationError({"name": "A code and a name are both required."})
    with use_company(company_id):
        existing = Designation.objects.filter(department=department, code=code).first()
        if existing is not None:
            if existing.status == ActiveStatus.ACTIVE:
                raise ValidationError({"code": f"{code} already exists here."})
            existing.status = ActiveStatus.ACTIVE
            existing.name = name
            existing.updated_by = actor
            existing.full_clean()
            existing.save()
            return existing
        return create_validated(
            Designation, company=membership.company, department=department,
            code=code, name=name, created_by=actor, updated_by=actor,
        )


def visible_designations(membership):
    """Designations this membership may see, respecting branch scope."""
    queryset = Designation.objects.select_related("department", "department__branch", "parent")
    if membership.allowed_branches.exists():
        queryset = queryset.filter(
            department__branch__in=membership.allowed_branches.values("pk")
        )
    return queryset


def get_designation_for_edit(*, actor, company_id, designation_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        designation = (
            Designation.objects.select_related("department__branch")
            .filter(pk=designation_id).first()
        )
        if designation is None:
            raise PermissionDenied("Designation not found in this company.")
        assert_branch_in_scope(membership, designation.department.branch)
    return membership, designation


@transaction.atomic
def create_designation(*, actor, company_id, values):
    """Create a designation under one of the company's departments."""
    membership = require_structure_manager(actor, company_id)
    values = dict(values)
    department = values.get("department")
    if department is None or not values.get("code") or not values.get("name"):
        raise ValidationError("A department, code and name are required.")
    with use_company(company_id):
        assert_branch_in_scope(membership, department.branch)
        if Designation.objects.filter(department=department, code=values["code"]).exists():
            raise ValidationError({
                "code": f"{department.name} already has a designation with code "
                        f"{values['code']}."
            })
        designation = create_validated(
            Designation, company=membership.company,
            created_by=actor, updated_by=actor, **values,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="designation.created", obj=designation,
            after={"department_id": designation.department_id, "code": designation.code,
                   "name": designation.name, "status": designation.status},
        )
    return designation


@transaction.atomic
def update_designation(*, actor, company_id, designation_id, values):
    """Edit one of the company's designations. The department is fixed once set."""
    membership, designation = get_designation_for_edit(
        actor=actor, company_id=company_id, designation_id=designation_id
    )
    values = dict(values)
    # The department is fixed once created: moving it would reshape who holds
    # the title. Add a new designation under another department instead.
    values.pop("department", None)
    with use_company(company_id):
        before = {"code": designation.code, "name": designation.name,
                  "parent_id": designation.parent_id, "status": designation.status}
        for field, value in values.items():
            setattr(designation, field, value)
        designation.updated_by = actor
        designation.full_clean()
        designation.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="designation.updated", obj=designation,
            before=before,
            after={"code": designation.code, "name": designation.name,
                   "parent_id": designation.parent_id, "status": designation.status},
        )
    return designation


@transaction.atomic
def set_designation_status(*, actor, company_id, designation_id, status):
    """Activate or deactivate a designation. Never deletes; refuses if in use."""
    membership = require_structure_manager(actor, company_id)
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        designation = (
            Designation.objects.select_related("department__branch")
            .filter(pk=designation_id).first()
        )
        if designation is None:
            raise PermissionDenied("Designation not found in this company.")
        assert_branch_in_scope(membership, designation.department.branch)
        if status == ActiveStatus.INACTIVE and designation.assignments.exists():
            raise ValidationError({
                "status": f"{designation.name} cannot be removed while employees "
                          "hold it. Move them to another designation first."
            })
        designation.status = status
        designation.updated_by = actor
        designation.full_clean()
        designation.save()
    return designation


@transaction.atomic
def set_adoption_status(*, actor, company_id, adoption_id, status):
    """Activate or deactivate one of the company's departments. Never deletes."""
    membership, department = get_adoption_for_edit(
        actor=actor, company_id=company_id, adoption_id=adoption_id
    )
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})
    with use_company(company_id):
        before = adoption_snapshot(department)
        if status == ActiveStatus.INACTIVE and department.assignments.exists():
            raise ValidationError({
                "status": "Employees are still assigned to this department. Move "
                          "them to another department first, then deactivate it."
            })
        department.status = status
        department.updated_by = actor
        department.full_clean()
        department.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="department.status_changed", obj=department,
            before=before, after=adoption_snapshot(department),
        )
    return department


@transaction.atomic
def copy_adoptions_between_branches(*, actor, company_id, source_branch, target_branch):
    """Copy one branch's active departments and designations to another branch.

    ``head`` is not copied (it administers people at one branch), and inactive
    rows are skipped. Departments the target already has (by code) are skipped,
    so the action is safe to repeat.
    """
    membership = require_structure_manager(actor, company_id)
    if source_branch.pk == target_branch.pk:
        raise ValidationError({"target_branch": "Choose a different branch to copy into."})

    created, skipped = [], []
    with use_company(company_id):
        assert_branch_in_scope(membership, source_branch)
        assert_branch_in_scope(membership, target_branch)

        existing = set(
            Department.objects.filter(branch=target_branch).values_list("code", flat=True)
        )
        sources = (
            Department.objects.filter(branch=source_branch, status=ActiveStatus.ACTIVE)
            .prefetch_related("designations")
        )
        for source in sources:
            if source.code in existing:
                skipped.append(source.name)
                continue
            department = create_validated(
                Department, company=membership.company, branch=target_branch,
                code=source.code, name=source.name, description=source.description,
                status=ActiveStatus.ACTIVE, created_by=actor, updated_by=actor,
            )
            for title in source.designations.filter(status=ActiveStatus.ACTIVE):
                create_validated(
                    Designation, company=membership.company, department=department,
                    code=title.code, name=title.name,
                    created_by=actor, updated_by=actor,
                )
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="department.copied", obj=department,
                before={"copied_from_branch_id": source_branch.pk},
                after=adoption_snapshot(department),
            )
            created.append(department)
    return created, skipped


def provision_new_branch(*, actor, company_id, branch):
    """Give a newly created branch the company's existing department set."""
    from organization.models import Branch

    with use_company(company_id):
        candidates = (
            Branch.objects.exclude(pk=branch.pk)
            .filter(status=ActiveStatus.ACTIVE)
            .annotate(
                department_count=models.Count(
                    "departments",
                    filter=models.Q(departments__status=ActiveStatus.ACTIVE),
                    distinct=True,
                )
            )
            .filter(department_count__gt=0)
            .order_by("-is_default", "-department_count", "pk")
        )
        source = candidates.first()
    if source is None:
        return [], []
    return copy_adoptions_between_branches(
        actor=actor, company_id=company_id,
        source_branch=source, target_branch=branch,
    )
