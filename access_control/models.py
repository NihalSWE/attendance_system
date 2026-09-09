"""Permission catalogue and the two layers that grant it.

Four layers decide what someone may do, evaluated by
``access_control.services.has_permission``:

1. **Feature** — is the capability even enabled for this company?
2. **DepartmentPermission** — the department's ceiling and floor (dated).
   A DENIED here cannot be overridden by anything below it.
3. **DesignationPermission** — the default for a job title (dated).
4. **EmployeePermissionOverride** — an individual grant/revoke (dated).

The department is the unit of delegation: ``CompanyDepartment.head`` administers
the people inside it, and the department's own rules cap what that head can hand
out. Job titles carry no parent/child hierarchy — the ceiling comes from above,
not from a chain of titles.

Nothing is allowed by default. Enabling a company feature does NOT give every
employee its actions — that separation is a stated product requirement.

See MODEL_FIELD_DICTIONARY.md §12-14 and §86.
"""

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models

from common.db import TstzRange
from common.models import ActorTracked, TenantOwned, TimeStamped

_PERIOD = TstzRange("effective_from", "effective_to", RangeBoundary())


class AccessPermission(TimeStamped):
    """Global catalogue of grantable actions, e.g. ``leave.approve``.

    Shared across tenants (not TenantOwned): companies grant from this catalogue
    rather than inventing their own permission codes, so rules stay comparable.
    """

    class Action(models.TextChoices):
        VIEW = "view", "View"
        CREATE = "create", "Create"
        EDIT = "edit", "Edit"
        APPROVE = "approve", "Approve"
        FINALIZE = "finalize", "Finalize"
        PAY = "pay", "Pay"
        MANAGE = "manage", "Manage"

    feature = models.ForeignKey(
        "tenants.Feature", on_delete=models.PROTECT, related_name="permissions"
    )
    code = models.CharField(max_length=128, unique=True)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    action = models.CharField(max_length=16, choices=Action.choices)
    # Payroll and biometric actions warrant extra care in UI and audit.
    is_sensitive = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "access_control_accesspermission"
        ordering = ("code",)

    def __str__(self):
        return self.code


def _current_department_id(employee_id, at=None):
    """The CompanyDepartment the employee sits in right now, or None.

    Looked up lazily through the app registry: ``employees`` imports
    ``organization`` and ``access_control`` is imported by services that
    ``employees`` also reaches, so a module-level import here would close a
    cycle.
    """
    from django.apps import apps
    from django.utils import timezone

    at = at or timezone.now()
    assignment_model = apps.get_model("employees", "EmployeeAssignment")
    assignment = (
        assignment_model.all_objects.filter(employee_id=employee_id)
        .exclude(status="cancelled")
        .filter(effective_from__lte=at)
        .filter(models.Q(effective_to__isnull=True) | models.Q(effective_to__gt=at))
        .order_by("-effective_from")
        .first()
    )
    return assignment.department_id if assignment else None


class DepartmentPermission(TenantOwned, ActorTracked):
    """What a whole department may do, on a dated interval.

    The department is the unit of delegation. A DENIED rule here is a hard
    ceiling: neither a job title nor an individual grant inside the department
    can exceed it. An ALLOWED rule is the floor everyone in the department gets
    unless something below revokes it individually.
    """

    class AccessLevel(models.TextChoices):
        DEFAULT = "default", "Default (no opinion)"
        ALLOWED = "allowed", "Allowed"
        DENIED = "denied", "Denied"

    company_department = models.ForeignKey(
        "organization.CompanyDepartment",
        on_delete=models.PROTECT,
        related_name="permission_rules",
    )
    permission = models.ForeignKey(
        AccessPermission, on_delete=models.PROTECT, related_name="department_rules"
    )
    access_level = models.CharField(
        max_length=16, choices=AccessLevel.choices, default=AccessLevel.DEFAULT
    )
    # Whether the department head may pass this permission on to their people.
    can_delegate = models.BooleanField(default=False)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        db_table = "access_control_department_permission"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="departmentpermission_end_after_start",
            ),
            # One effective rule per department/permission at any instant.
            ExclusionConstraint(
                name="excl_departmentpermission_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("company_department", RangeOperators.EQUAL),
                    ("permission", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
            ),
        ]
        indexes = [
            models.Index(fields=["company", "company_department", "permission"]),
        ]

    def __str__(self):
        return f"{self.company_department_id}: {self.permission_id} = {self.access_level}"


class DesignationPermission(TenantOwned, ActorTracked):
    """The default access a job title carries, on a dated interval."""

    class AccessLevel(models.TextChoices):
        DEFAULT = "default", "Default (no opinion)"
        ALLOWED = "allowed", "Allowed"
        DENIED = "denied", "Denied"

    designation = models.ForeignKey(
        "organization.CompanyDesignation",
        on_delete=models.PROTECT,
        related_name="permission_rules",
    )
    permission = models.ForeignKey(
        AccessPermission, on_delete=models.PROTECT, related_name="designation_rules"
    )
    access_level = models.CharField(
        max_length=16, choices=AccessLevel.choices, default=AccessLevel.DEFAULT
    )
    # Whether a holder may pass this permission on to a subordinate.
    can_delegate = models.BooleanField(default=False)
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)

    class Meta:
        db_table = "access_control_designationpermission"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="designationpermission_end_after_start",
            ),
            # One effective rule per designation/permission at any instant.
            ExclusionConstraint(
                name="excl_designationpermission_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("designation", RangeOperators.EQUAL),
                    ("permission", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
            ),
        ]
        indexes = [
            models.Index(fields=["company", "designation", "permission"]),
        ]

    def __str__(self):
        return f"{self.designation_id}: {self.permission_id} = {self.access_level}"

    def clean(self):
        super().clean()
        # Department ceiling: a job title may not be ALLOWED something its own
        # department explicitly DENIES. This replaces the old designation
        # parent-chain walk with a single hop — the department is now the unit
        # that carries delegated authority.
        if self.access_level != self.AccessLevel.ALLOWED or not self.designation_id:
            return
        denied = DepartmentPermission.all_objects.filter(
            company_id=self.company_id,
            company_department_id=self.designation.company_department_id,
            permission_id=self.permission_id,
            access_level=DepartmentPermission.AccessLevel.DENIED,
            effective_to__isnull=True,
        ).exists()
        if denied:
            raise ValidationError(
                {
                    "access_level": (
                        "The department denies this permission; a job title "
                        "inside it cannot exceed the department's ceiling."
                    )
                }
            )


class EmployeePermissionOverride(TenantOwned, ActorTracked):
    """An individual grant or revoke for one employee, on a dated interval.

    Wins over the designation default. Optional branch/department scopes narrow
    where the permission applies; empty means "inherit the member's own scope",
    not "everywhere".
    """

    class Effect(models.TextChoices):
        GRANT = "grant", "Grant"
        REVOKE = "revoke", "Revoke"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        REVOKED = "revoked", "Revoked"
        EXPIRED = "expired", "Expired"

    employee = models.ForeignKey(
        "employees.Employee",
        on_delete=models.PROTECT,
        related_name="permission_overrides",
    )
    permission = models.ForeignKey(
        AccessPermission, on_delete=models.PROTECT, related_name="employee_overrides"
    )
    effect = models.CharField(max_length=16, choices=Effect.choices)
    allowed_branches = models.ManyToManyField(
        "organization.Branch", blank=True, related_name="permission_overrides"
    )
    allowed_departments = models.ManyToManyField(
        "organization.CompanyDepartment",
        blank=True,
        related_name="permission_overrides",
    )
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    granted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="granted_permission_overrides",
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "access_control_employeepermissionoverride"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="employeeoverride_end_after_start",
            ),
            # One live override per employee/permission at any instant.
            ExclusionConstraint(
                name="excl_employeeoverride_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("employee", RangeOperators.EQUAL),
                    ("permission", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status="active"),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee", "permission"]),
        ]

    def __str__(self):
        return f"{self.employee_id}: {self.effect} {self.permission_id}"

    def clean(self):
        super().clean()
        # Department ceiling, again: the head of a department administers the
        # people in it, so nothing they grant may exceed what the department
        # itself is denied. Without this an individual grant would be a way to
        # walk straight past the department's own rules.
        if self.effect != self.Effect.GRANT or not self.employee_id:
            return
        department_id = _current_department_id(self.employee_id)
        if department_id is None:
            return
        denied = DepartmentPermission.all_objects.filter(
            company_id=self.company_id,
            company_department_id=department_id,
            permission_id=self.permission_id,
            access_level=DepartmentPermission.AccessLevel.DENIED,
            effective_to__isnull=True,
        ).exists()
        if denied:
            raise ValidationError(
                {
                    "effect": (
                        "This employee's department denies this permission; an "
                        "individual grant cannot exceed the department ceiling."
                    )
                }
            )
