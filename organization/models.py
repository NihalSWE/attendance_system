"""Company structure.

Every company owns its own ``Branch``, ``Department`` and ``Designation`` rows
(all tenant-owned). A department lives in a branch and may name a ``head``;
a designation lives in a department and may have a ``parent`` for delegation.
Employees, shifts, permissions and scopes point straight at these rows.

Reverted from the short-lived root-owned catalogue + adoption model (see
PHASE_STATUS.md): departments and designations are company-level again.
"""

from django.core.exceptions import ValidationError
from django.db import models

from common.choices import ActiveStatus, DeviceAttendanceScope
from common.models import ActorTracked, TenantOwned, TimeStamped


class Branch(TenantOwned, ActorTracked):
    """A physical location of a company. Every company has one default branch."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)

    address = models.TextField(blank=True)
    city = models.CharField(max_length=128, blank=True)
    postal_code = models.CharField(max_length=32, blank=True)
    country_code = models.CharField(max_length=2, blank=True)
    # Defaults from Company at creation time; stored so later company changes
    # do not silently rewrite this branch's historical interpretation.
    timezone = models.CharField(max_length=64, blank=True)

    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    # The employee's assigned/home-branch device policy, NOT the source device's
    # branch policy. Null means inherit CompanyAttendanceSettings.
    device_attendance_scope_override = models.CharField(
        max_length=32,
        choices=DeviceAttendanceScope.choices,
        null=True,
        blank=True,
    )

    opened_on = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "organization_branch"
        verbose_name_plural = "branches"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_branch_code_per_company"
            ),
            # At most one active default branch per company.
            models.UniqueConstraint(
                fields=["company"],
                condition=models.Q(is_default=True, status=ActiveStatus.ACTIVE),
                name="uniq_active_default_branch_per_company",
            ),
        ]

    def __str__(self):
        return self.name



class Department(TenantOwned, ActorTracked):
    """A functional unit inside a branch (HR, Software, Sales...), company-owned."""

    branch = models.ForeignKey(
        Branch, on_delete=models.PROTECT, related_name="departments"
    )
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    # The employee who administers permissions for everyone in this department.
    # Nullable: a department may exist before its head is appointed.
    head = models.ForeignKey(
        "employees.Employee",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="headed_departments",
    )
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )
    opened_on = models.DateField(null=True, blank=True)
    closed_on = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "organization_department"
        ordering = ("branch__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["branch", "code"], name="uniq_department_code_per_branch"
            ),
            models.UniqueConstraint(
                fields=["branch", "name"], name="uniq_department_name_per_branch"
            ),
        ]
        indexes = [models.Index(fields=["company", "branch"])]

    def __str__(self):
        return self.name


class Designation(TenantOwned, ActorTracked):
    """A job title inside a department, with an optional parent for delegation.

    The parent hierarchy stays acyclic and inside one department. How it drives
    delegated access is decided later; the field is kept meanwhile.
    """

    department = models.ForeignKey(
        Department, on_delete=models.PROTECT, related_name="designations"
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
    )
    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    # Derived from the parent chain; maintained in save().
    hierarchy_level = models.PositiveIntegerField(default=0)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "organization_designation"
        ordering = ("department__name", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["department", "code"],
                name="uniq_designation_code_per_department",
            ),
            models.CheckConstraint(
                condition=~models.Q(parent=models.F("id")),
                name="designation_parent_not_self",
            ),
        ]
        indexes = [models.Index(fields=["company", "department"])]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if not self.parent_id:
            return
        if self.pk and self.parent_id == self.pk:
            raise ValidationError({"parent": "A designation cannot be its own parent."})
        parent = self.parent
        if parent.department_id != self.department_id:
            raise ValidationError(
                {"parent": "Parent designation must be in the same department."}
            )
        seen = set()
        node = parent
        while node is not None:
            if self.pk and node.pk == self.pk:
                raise ValidationError(
                    {"parent": "This parent would create a hierarchy cycle."}
                )
            if node.pk in seen:
                break
            seen.add(node.pk)
            node = node.parent

    def save(self, *args, **kwargs):
        self.hierarchy_level = (self.parent.hierarchy_level + 1) if self.parent_id else 0
        super().save(*args, **kwargs)
