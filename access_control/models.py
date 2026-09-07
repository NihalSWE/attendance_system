"""Permission catalogue and the two layers that grant it.

Three layers decide what someone may do, evaluated by
``access_control.services.has_permission``:

1. **Feature** — is the capability even enabled for this company?
2. **DesignationPermission** — the default for a job title (dated).
3. **EmployeePermissionOverride** — an individual grant/revoke (dated, wins).

Nothing is allowed by default. Enabling a company feature does NOT give every
employee its actions — that separation is a stated product requirement.

See MODEL_FIELD_DICTIONARY.md §12-14.
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


class DesignationPermission(TenantOwned, ActorTracked):
    """The default access a job title carries, on a dated interval."""

    class AccessLevel(models.TextChoices):
        DEFAULT = "default", "Default (no opinion)"
        ALLOWED = "allowed", "Allowed"
        DENIED = "denied", "Denied"

    designation = models.ForeignKey(
        "organization.Designation",
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
        # Hierarchy ceiling: a child designation may not be ALLOWED something its
        # parent chain explicitly DENIES. Managers delegate downward; a
        # subordinate title must not out-rank its parent.
        if self.access_level != self.AccessLevel.ALLOWED or not self.designation_id:
            return
        parent = self.designation.parent
        seen = set()
        while parent is not None and parent.pk not in seen:
            seen.add(parent.pk)
            denied = (
                DesignationPermission.all_objects.filter(
                    company_id=self.company_id,
                    designation_id=parent.pk,
                    permission_id=self.permission_id,
                    access_level=self.AccessLevel.DENIED,
                    effective_to__isnull=True,
                )
                .exists()
            )
            if denied:
                raise ValidationError(
                    {
                        "access_level": (
                            "A parent designation denies this permission; a child "
                            "cannot exceed its parent's ceiling."
                        )
                    }
                )
            parent = parent.parent


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
        "organization.Department", blank=True, related_name="permission_overrides"
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
