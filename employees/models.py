"""Employees: permanent identity plus dated organization and pay history.

The central invariant (PROJECT_HANDOFF.md): ``Employee.id`` is permanent, while
``EmployeeAssignment`` owns the *reusable* business ``employee_code`` on a dated
interval. A new holder of a recycled code never inherits the previous person's
history, because all history hangs off Employee, not off the code.

See MODEL_FIELD_DICTIONARY.md §9-11.
"""

import uuid

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models

from common.choices import DeviceAttendanceScope
from common.db import TstzRange
from common.models import ActorTracked, TenantOwned

# Reused by the exclusion constraints below: '[)' == inclusive start, exclusive end.
_PERIOD = TstzRange("effective_from", "effective_to", RangeBoundary())


class Employee(TenantOwned, ActorTracked):
    """A person employed by a company. This identity is permanent.

    An Employee may have no User account (HR can act on their behalf); linking a
    login later must not create a second employee or reset history.
    """

    class EmploymentStatus(models.TextChoices):
        APPLICANT = "applicant", "Applicant"
        ACTIVE = "active", "Active"
        PROBATION = "probation", "Probation"
        SUSPENDED = "suspended", "Suspended"
        RESIGNED = "resigned", "Resigned"
        TERMINATED = "terminated", "Terminated"
        RETIRED = "retired", "Retired"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="employee_profiles",
    )

    first_name = models.CharField(max_length=150)
    middle_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    preferred_name = models.CharField(max_length=150, blank=True)

    work_email = models.EmailField(blank=True)
    personal_email = models.EmailField(blank=True)
    phone = models.CharField(max_length=32, blank=True)

    date_of_birth = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=32, blank=True)
    blood_group = models.CharField(max_length=8, blank=True)
    marital_status = models.CharField(max_length=32, blank=True)

    # Protected identifiers; treat as sensitive in exports and logs.
    national_id = models.CharField(max_length=64, blank=True)
    passport_number = models.CharField(max_length=64, blank=True)

    address = models.TextField(blank=True)
    emergency_contact_name = models.CharField(max_length=150, blank=True)
    emergency_contact_phone = models.CharField(max_length=32, blank=True)
    emergency_contact_relation = models.CharField(max_length=64, blank=True)

    joining_date = models.DateField(null=True, blank=True)
    confirmation_date = models.DateField(null=True, blank=True)
    leaving_date = models.DateField(null=True, blank=True)

    employment_status = models.CharField(
        max_length=16,
        choices=EmploymentStatus.choices,
        default=EmploymentStatus.ACTIVE,
    )
    # FileField (not ImageField) keeps Pillow out of the dependency set.
    photo = models.FileField(upload_to="employee_photos/", null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "employees_employee"
        constraints = [
            # One employee row per linked user per company; many employees may
            # have no user at all, so the constraint is partial.
            models.UniqueConstraint(
                fields=["company", "user"],
                condition=models.Q(user__isnull=False),
                name="uniq_employee_user_per_company",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employment_status"]),
            models.Index(fields=["company", "user"]),
        ]

    def __str__(self):
        return self.full_name

    @property
    def full_name(self):
        parts = [self.first_name, self.middle_name, self.last_name]
        return " ".join(p for p in parts if p)


class EmployeeAssignment(TenantOwned, ActorTracked):
    """Dated organization placement carrying the reusable ``employee_code``.

    Changing branch/department/designation/code closes the current interval and
    opens a successor, so payroll and attendance can always reconstruct where a
    person sat on any given date.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="assignments"
    )
    # Manually entered business code. Reusable by another person *after* this
    # interval ends — enforced by the exclusion constraint below, not by
    # uniqueness on the column.
    employee_code = models.CharField(max_length=64)

    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="assignments"
    )
    # The company's adoption rows, never the root catalogue rows: a placement
    # is always inside one company's own branch and department.
    department = models.ForeignKey(
        "organization.Department",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    designation = models.ForeignKey(
        "organization.Designation",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    manager = models.ForeignKey(
        Employee,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="direct_reports",
    )

    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    change_reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    # Employee-level device policy override; null inherits branch then company.
    device_attendance_scope_override = models.CharField(
        max_length=32, choices=DeviceAttendanceScope.choices, null=True, blank=True
    )

    class Meta:
        db_table = "employees_employeeassignment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="assignment_period_end_after_start",
            ),
            # A person cannot hold two overlapping placements...
            ExclusionConstraint(
                name="excl_assignment_overlap_per_employee",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("employee", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(status="cancelled"),
            ),
            # ...and one employee_code cannot be occupied by two people at once.
            # Cancelled rows are void, so they are excluded; ENDED rows still
            # occupied the code during their interval and must block overlap.
            ExclusionConstraint(
                name="excl_employee_code_overlap_per_company",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("employee_code", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(status="cancelled"),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee_code", "effective_from"]),
            models.Index(fields=["company", "employee", "effective_from"]),
        ]

    def __str__(self):
        return f"{self.employee_code} ({self.employee_id})"

    def clean(self):
        super().clean()
        errors = {}
        if self.effective_to and self.effective_to <= self.effective_from:
            errors["effective_to"] = "End must be after start."
        if self.branch_id and self.department_id:
            if self.department.branch_id != self.branch_id:
                errors["department"] = "Department does not belong to this branch."
        if self.department_id and self.designation_id:
            if self.designation.department_id != self.department_id:
                errors["designation"] = (
                    "Designation does not belong to this department."
                )
        if self.manager_id and self.manager_id == self.employee_id:
            errors["manager"] = "An employee cannot be their own manager."
        if errors:
            raise ValidationError(errors)


class EmployeeCompensation(TenantOwned, ActorTracked):
    """Dated pay history. Payroll reads the row effective on the work date."""

    class PayBasis(models.TextChoices):
        MONTHLY = "monthly", "Monthly"
        DAILY = "daily", "Daily"
        HOURLY = "hourly", "Hourly"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        Employee, on_delete=models.PROTECT, related_name="compensations"
    )
    pay_basis = models.CharField(max_length=16, choices=PayBasis.choices)
    base_rate = models.DecimalField(max_digits=18, decimal_places=2)
    currency = models.CharField(max_length=3)
    overtime_rate_override = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "employees_employeecompensation"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(base_rate__gt=0),
                name="compensation_base_rate_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="compensation_period_end_after_start",
            ),
            # Draft rows are not yet effective, so they do not reserve a period.
            ExclusionConstraint(
                name="excl_compensation_overlap_per_employee",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("employee", RangeOperators.EQUAL),
                    (_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status__in=("active", "ended")),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee", "effective_from"]),
        ]

    def __str__(self):
        return f"{self.employee_id}: {self.base_rate} {self.currency} ({self.pay_basis})"

    def clean(self):
        super().clean()
        if self.effective_to and self.effective_to <= self.effective_from:
            raise ValidationError({"effective_to": "End must be after start."})
