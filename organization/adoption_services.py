"""Company-scoped writes for adopting a catalogue department into a branch.

Separate module from ``organization.services`` only for size; it follows the
same contract and reuses that module's authorization helpers:

1. **Membership** - the actor holds an active CompanyMembership here.
2. **Action permission** - their role may manage organization structure.
3. **Row scope** - a branch-restricted member may only touch their branches,
   re-checked against submitted ids because a form is not a security boundary.

Every write shares one transaction with its AuditLog row.

What a company may set is deliberately narrow. The catalogue's ``code`` and
``name`` are root's, so they are absent from ADOPTION_FIELDS: a company copy
that could drift from the canonical name is precisely what the catalogue /
adoption split exists to prevent.
"""

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction

from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.services import create_validated
from common.tenant import use_company
from organization.models import CompanyDepartment, CompanyDesignation
from organization.services import (
    assert_branch_in_scope,
    require_structure_manager,
)

ADOPTION_FIELDS = (
    "branch", "department", "head", "description", "status",
    "opened_on", "closed_on",
)


def adoption_snapshot(adoption):
    """Audit snapshot including the job titles, so a title change is visible."""
    return {
        "branch_id": adoption.branch_id,
        "department_id": adoption.department_id,
        "department_name": adoption.department.name,
        "head_id": adoption.head_id,
        "description": adoption.description,
        "status": adoption.status,
        "titles": sorted(
            adoption.designations.values_list("designation__name", flat=True)
        ),
    }


def visible_adoptions(membership):
    """Adopted departments this membership may see, respecting branch scope."""
    queryset = (
        CompanyDepartment.objects.select_related("branch", "department", "head")
        .prefetch_related("designations__designation")
    )
    if membership.allowed_branches.exists():
        queryset = queryset.filter(branch__in=membership.allowed_branches.values("pk"))
    return queryset


def get_adoption_for_edit(*, actor, company_id, adoption_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        adoption = (
            CompanyDepartment.objects.select_related("branch", "department")
            .filter(pk=adoption_id)
            .first()
        )
        if adoption is None:
            raise PermissionDenied("Department not found in this company.")
        assert_branch_in_scope(membership, adoption.branch)
    return membership, adoption


def _reject_unsupported(values):
    unsupported = set(values) - set(ADOPTION_FIELDS)
    if unsupported:
        raise ValidationError(
            f"Unsupported field: {', '.join(sorted(unsupported))}"
        )


def _validate_titles(department, designations):
    """Every chosen title must belong to the chosen catalogue department.

    ``CompanyDesignation.clean()`` rejects a mismatch anyway. Catching it here
    turns it into a readable field error rather than a save-time failure, and
    blocks a crafted POST that offers a title from another department.
    """
    wrong = [d for d in designations if d.department_id != department.pk]
    if wrong:
        raise ValidationError({
            "designations": (
                "These job titles belong to a different department: "
                + ", ".join(sorted(d.name for d in wrong))
            )
        })


@transaction.atomic
def adopt_department(*, actor, company_id, values):
    """Adopt a catalogue department into a branch, with its job titles.

    One CompanyDepartment plus one CompanyDesignation per chosen title, in a
    single transaction with the audit row, so a company never ends up with a
    department whose titles failed to save.
    """
    membership = require_structure_manager(actor, company_id)
    values = dict(values)
    designations = list(values.pop("designations", []) or [])
    _reject_unsupported(values)

    branch = values.get("branch")
    department = values.get("department")
    if branch is None or department is None:
        raise ValidationError("A branch and a department are both required.")

    _validate_titles(department, designations)

    with use_company(company_id):
        # Inside the context: allowed_branches is a tenant-scoped relation, so
        # checking it outside would trip the scoped manager rather than the
        # scope rule it is meant to enforce.
        assert_branch_in_scope(membership, branch)

        if CompanyDepartment.objects.filter(
            branch=branch, department=department
        ).exists():
            raise ValidationError({
                "department": (
                    f"{branch.name} has already adopted {department.name}. "
                    "Edit that entry instead of adding it a second time."
                )
            })

        adoption = create_validated(
            CompanyDepartment,
            company=membership.company,
            created_by=actor,
            updated_by=actor,
            **values,
        )
        for designation in designations:
            create_validated(
                CompanyDesignation,
                company=membership.company,
                company_department=adoption,
                designation=designation,
                created_by=actor,
                updated_by=actor,
            )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="company_department.adopted", obj=adoption,
            after=adoption_snapshot(adoption),
        )
    return adoption


@transaction.atomic
def update_adoption(*, actor, company_id, adoption_id, values):
    """Edit an adopted department; add job titles or deactivate them.

    Titles are never deleted. One an employee currently holds is referenced by
    a PROTECTed assignment, so a delete would raise ProtectedError; this
    refuses with a readable message instead, and deactivates the rest so the
    history stays resolvable.
    """
    membership, adoption = get_adoption_for_edit(
        actor=actor, company_id=company_id, adoption_id=adoption_id
    )
    values = dict(values)
    designations = list(values.pop("designations", []) or [])
    _reject_unsupported(values)

    # Branch and catalogue department are fixed once adopted: changing either
    # would silently move every employee filed under this row.
    values.pop("branch", None)
    values.pop("department", None)
    _validate_titles(adoption.department, designations)

    with use_company(company_id):
        before = adoption_snapshot(adoption)
        for field, value in values.items():
            setattr(adoption, field, value)
        adoption.updated_by = actor
        adoption.full_clean()
        adoption.save()

        chosen = {d.pk for d in designations}
        existing = {
            link.designation_id: link
            for link in CompanyDesignation.objects.filter(
                company_department=adoption
            ).select_related("designation")
        }

        # Added, or previously deactivated and chosen again.
        for designation in designations:
            link = existing.get(designation.pk)
            if link is None:
                create_validated(
                    CompanyDesignation,
                    company=membership.company,
                    company_department=adoption,
                    designation=designation,
                    created_by=actor,
                    updated_by=actor,
                )
            elif link.status != ActiveStatus.ACTIVE:
                link.status = ActiveStatus.ACTIVE
                link.updated_by = actor
                link.full_clean()
                link.save()

        # Unchecked and currently active: deactivate, unless someone holds it.
        for designation_id, link in existing.items():
            if designation_id in chosen or link.status != ActiveStatus.ACTIVE:
                continue
            if link.assignments.exists():
                raise ValidationError({
                    "designations": (
                        f"{link.designation.name} cannot be removed while "
                        "employees are assigned to it. Move them to another "
                        "job title first."
                    )
                })
            link.status = ActiveStatus.INACTIVE
            link.updated_by = actor
            link.full_clean()
            link.save()

        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="company_department.updated", obj=adoption,
            before=before, after=adoption_snapshot(adoption),
        )
    return adoption


@transaction.atomic
def set_adoption_status(*, actor, company_id, adoption_id, status):
    """Activate or deactivate an adopted department. Never deletes."""
    membership, adoption = get_adoption_for_edit(
        actor=actor, company_id=company_id, adoption_id=adoption_id
    )
    if status not in dict(ActiveStatus.choices):
        raise ValidationError({"status": "Unknown status."})

    with use_company(company_id):
        before = adoption_snapshot(adoption)
        if status == ActiveStatus.INACTIVE and adoption.assignments.exists():
            raise ValidationError({
                "status": (
                    "Employees are still assigned to this department. Move them "
                    "to another department first, then deactivate it."
                )
            })
        adoption.status = status
        adoption.updated_by = actor
        adoption.full_clean()
        adoption.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="company_department.status_changed", obj=adoption,
            before=before, after=adoption_snapshot(adoption),
        )
    return adoption


@transaction.atomic
def copy_adoptions_between_branches(*, actor, company_id, source_branch, target_branch):
    """Copy one branch's active departments and job titles to another branch.

    A department adoption is per-branch by design: it carries a head, a status
    and dated open/close values that describe *that* branch's use of the
    catalogue name, and device rules attach to it. So a new branch genuinely
    starts empty. This only removes the retyping.

    Two things are deliberately not copied:

    - ``head``. The head administers people inside one department at one
      branch. Copying it would appoint someone over a branch they may not even
      work at, silently.
    - Inactive rows. A department the source branch stopped using is not
      something to hand a new branch.

    Departments the target already has are skipped rather than failing, so the
    action is safe to repeat after adding one more department to the source.
    """
    membership = require_structure_manager(actor, company_id)
    if source_branch.pk == target_branch.pk:
        raise ValidationError(
            {"target_branch": "Choose a different branch to copy into."}
        )

    created, skipped = [], []
    with use_company(company_id):
        assert_branch_in_scope(membership, source_branch)
        assert_branch_in_scope(membership, target_branch)

        existing = set(
            CompanyDepartment.objects.filter(branch=target_branch).values_list(
                "department_id", flat=True
            )
        )
        sources = (
            CompanyDepartment.objects.filter(
                branch=source_branch, status=ActiveStatus.ACTIVE
            )
            .select_related("department")
            .prefetch_related("designations__designation")
        )

        for source in sources:
            if source.department_id in existing:
                skipped.append(source.department.name)
                continue

            adoption = create_validated(
                CompanyDepartment,
                company=membership.company,
                branch=target_branch,
                department=source.department,
                description=source.description,
                status=ActiveStatus.ACTIVE,
                created_by=actor,
                updated_by=actor,
            )
            for link in source.designations.all():
                if link.status != ActiveStatus.ACTIVE:
                    continue
                create_validated(
                    CompanyDesignation,
                    company=membership.company,
                    company_department=adoption,
                    designation=link.designation,
                    created_by=actor,
                    updated_by=actor,
                )
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action="company_department.copied", obj=adoption,
                before={"copied_from_branch_id": source_branch.pk},
                after=adoption_snapshot(adoption),
            )
            created.append(adoption)

    return created, skipped


def provision_new_branch(*, actor, company_id, branch):
    """Give a newly created branch the company's existing department set.

    The company's operating rule is that every branch offers the same
    departments and job titles. The schema still stores one adoption row per
    branch — it has to, because each branch carries its own head, status,
    opening dates and device rules for a department — so "the same everywhere"
    is achieved by provisioning the set rather than by sharing one row.

    Source is the default branch when it has departments, otherwise whichever
    branch has the most. That matters when the default branch is newer or
    emptier than the one people actually set up.

    Silent no-op when there is nothing to copy: the company's very first
    branch has no source, and that is normal rather than an error.
    """
    from organization.models import Branch

    with use_company(company_id):
        candidates = (
            Branch.objects.exclude(pk=branch.pk)
            .filter(status=ActiveStatus.ACTIVE)
            .annotate(
                department_count=models.Count(
                    "company_departments",
                    filter=models.Q(company_departments__status=ActiveStatus.ACTIVE),
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
