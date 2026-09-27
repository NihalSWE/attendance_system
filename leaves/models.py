"""Leave: types, requests, their date segments, and the approved days they cover.

Built on 2026-09-12 as the thin slice of the salary fast-track: a company
administrator records leave that is already approved, full days only, paid or
unpaid. The field names follow MODEL_FIELD_DICTIONARY.md §39 and §46-48 so the
full workflow — employee requests, approval steps, half-day and hourly leave,
policies, balances, attachments, amendment — extends these tables rather than
replacing them. A8 (2026-09-15) adds employee requests and branch-manager
decisions using these existing tables. Remaining full-leave features are
listed under A10 in docs/PHASE_STATUS.md.

Whether a leave is paid is decided per request, not fixed on the leave type:
the design has a manager decide paid, unpaid or partial pay when approving.

Attendance and payroll read ``LeaveDay``: one row per working day on leave.
Weekly offs and holidays inside a leave range are not leave days.
"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned


class PayType(models.TextChoices):
    PAID = "paid", "Paid"
    UNPAID = "unpaid", "Unpaid"
    # Phase E (2026-09-27): a share of pay kept, 1-99 %, in the percentage.
    PARTIAL = "partial", "Part paid"


class LeaveType(TenantOwned, ActorTracked):
    """A kind of leave a company offers, e.g. Casual, Sick, Annual."""

    class BalanceUnit(models.TextChoices):
        DAYS = "days", "Days"
        MINUTES = "minutes", "Minutes"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    # Optional yearly allowance in days (kept simple: no accrual or carry-forward).
    # Blank means no limit. Approved leave in a calendar year counts; a half day is 0.5.
    days_per_year = models.DecimalField(
        max_digits=5, decimal_places=1, null=True, blank=True,
        validators=[MinValueValidator(Decimal("0.5"))],
    )
    default_balance_unit = models.CharField(
        max_length=16, choices=BalanceUnit.choices, default=BalanceUnit.DAYS
    )
    requires_attachment_by_default = models.BooleanField(default=False)
    color = models.CharField(max_length=16, blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "payroll_leave_type"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_leave_type_code_per_company"
            ),
        ]

    def __str__(self):
        return self.name


class LeaveRequest(TenantOwned, ActorTracked):
    """One leave application: pending a decision, or recorded already approved."""

    class ActionType(models.TextChoices):
        NEW = "new", "New"
        AMEND = "amend", "Amend"
        CANCEL = "cancel", "Cancel"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Submitted"
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"
        CANCELLED = "cancelled", "Cancelled"
        PARTIALLY_CANCELLED = "partially_cancelled", "Partially cancelled"

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="leave_requests"
    )
    # The placement the employee held when the leave was recorded.
    submission_assignment = models.ForeignKey(
        "employees.EmployeeAssignment",
        on_delete=models.PROTECT,
        related_name="leave_requests",
    )
    action_type = models.CharField(
        max_length=16, choices=ActionType.choices, default=ActionType.NEW
    )
    reason = models.TextField(blank=True)
    status = models.CharField(
        max_length=24, choices=Status.choices, default=Status.DRAFT
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="submitted_leave_requests",
    )
    decision_snapshot = models.JSONField(default=dict, blank=True)
    decided_at = models.DateTimeField(null=True, blank=True)
    # One supporting document - a medical certificate, a letter (2026-09-27).
    # Stored under a random name; served only by leaves.documents.
    attachment = models.FileField(upload_to="leave_documents/", null=True, blank=True)
    attachment_name = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "payroll_leave_request"
        indexes = [
            models.Index(fields=["company", "employee", "status"]),
        ]

    def __str__(self):
        return f"Leave {self.public_id}"


class LeaveRequestSegment(TenantOwned):
    """One continuous stretch of one leave type inside a request."""

    class DurationType(models.TextChoices):
        FULL_DAY = "full_day", "Full day"
        HALF_DAY = "half_day", "Half day"
        HOURLY = "hourly", "Hourly"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        SUPERSEDED = "superseded", "Superseded"
        CANCELLED = "cancelled", "Cancelled"

    leave_request = models.ForeignKey(
        LeaveRequest, on_delete=models.PROTECT, related_name="segments"
    )
    leave_type = models.ForeignKey(
        LeaveType, on_delete=models.PROTECT, related_name="segments"
    )
    duration_type = models.CharField(
        max_length=16, choices=DurationType.choices, default=DurationType.FULL_DAY
    )
    start_date = models.DateField()
    end_date = models.DateField()
    half_day_part = models.CharField(max_length=16, blank=True)
    start_time = models.TimeField(null=True, blank=True)
    end_time = models.TimeField(null=True, blank=True)
    timezone = models.CharField(max_length=64, blank=True)
    requested_units = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    requested_minutes = models.PositiveIntegerField(default=0)
    requested_pay_type = models.CharField(max_length=16, choices=PayType.choices)
    requested_pay_percentage = models.DecimalField(
        max_digits=5, decimal_places=2, null=True, blank=True
    )
    sequence_number = models.PositiveIntegerField(default=1)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "payroll_leave_request_segment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")),
                name="leave_segment_end_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.leave_type} {self.start_date}–{self.end_date}"

    def clean(self):
        super().clean()
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError({"end_date": "The last day cannot be before the first."})


class LeaveDay(TenantOwned):
    """One approved working day on leave. Attendance and payroll read these."""

    class Status(models.TextChoices):
        RESERVED = "reserved", "Reserved"
        APPROVED = "approved", "Approved"
        CONSUMED = "consumed", "Consumed"
        CANCELLED = "cancelled", "Cancelled"
        REVERSED = "reversed", "Reversed"

    request_segment = models.ForeignKey(
        LeaveRequestSegment, on_delete=models.PROTECT, related_name="days"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="leave_days"
    )
    employee_assignment = models.ForeignKey(
        "employees.EmployeeAssignment",
        on_delete=models.PROTECT,
        related_name="leave_days",
    )
    work_date = models.DateField()
    covered_start_at = models.DateTimeField()
    covered_end_at = models.DateTimeField()
    scheduled_minutes_snapshot = models.PositiveIntegerField()
    leave_minutes = models.PositiveIntegerField()
    balance_units = models.DecimalField(max_digits=6, decimal_places=2, default=1)
    approved_pay_type = models.CharField(max_length=16, choices=PayType.choices)
    approved_pay_percentage = models.DecimalField(max_digits=5, decimal_places=2)
    calendar_snapshot = models.JSONField(default=dict, blank=True)
    shift_snapshot = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.APPROVED
    )
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payroll_leave_day"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(covered_end_at__gt=models.F("covered_start_at")),
                name="leave_day_covered_interval_positive",
            ),
            # An employee cannot be on two live leaves on the same day.
            models.UniqueConstraint(
                fields=["company", "employee", "work_date"],
                condition=models.Q(status__in=["reserved", "approved", "consumed"]),
                name="uniq_live_leave_day_per_employee_date",
            ),
            # Pay type and percentage must agree (part paid: 1-99 %, Phase E).
            models.CheckConstraint(
                condition=(
                    models.Q(approved_pay_type="paid", approved_pay_percentage=100)
                    | models.Q(approved_pay_type="unpaid", approved_pay_percentage=0)
                    | models.Q(approved_pay_type="partial", approved_pay_percentage__gte=1,
                               approved_pay_percentage__lte=99)
                ),
                name="leave_day_pay_type_matches_percentage",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee", "work_date"]),
            models.Index(fields=["company", "work_date"]),
        ]

    def __str__(self):
        return f"{self.employee_id} on leave {self.work_date}"


# --------------------------------------------------------------------------
# Leave policies, their versions and the ledger (Phase E, 2026-09-27;
# docs/LEAVE_FULL_DESIGN.md §2-3). Salary and attendance never read these:
# they decide how much leave someone may take, not what a leave day is.
# --------------------------------------------------------------------------


class LeavePolicy(TenantOwned, ActorTracked):
    """A set of leave entitlements - "Staff", "Workers" - given to employees.
    One may be the company default: everyone not given another has it."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=150)
    description = models.CharField(max_length=255, blank=True)
    is_default = models.BooleanField(default=False)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "leaves_policy"
        ordering = ("name",)
        constraints = [
            models.UniqueConstraint(fields=["company", "code"],
                                    name="uniq_leave_policy_code_per_company"),
            models.UniqueConstraint(fields=["company"], condition=models.Q(is_default=True),
                                    name="uniq_default_leave_policy_per_company"),
        ]

    def __str__(self):
        return self.name


class LeavePolicyVersion(TenantOwned, ActorTracked):
    """A policy's rules from a date until its next version. A version that has
    started is never edited: a change is a new version from a date, so what
    someone was given earlier can always be worked out again."""

    policy = models.ForeignKey(LeavePolicy, on_delete=models.CASCADE, related_name="versions")
    number = models.PositiveIntegerField()
    effective_from = models.DateField()
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        db_table = "leaves_policy_version"
        ordering = ("policy", "effective_from")
        constraints = [
            models.UniqueConstraint(fields=["policy", "number"],
                                    name="uniq_leave_policy_version_number"),
            models.UniqueConstraint(fields=["policy", "effective_from"],
                                    name="uniq_leave_policy_version_start"),
        ]

    def __str__(self):
        return f"{self.policy} v{self.number} from {self.effective_from}"


class LeavePolicyRule(TenantOwned):
    """What one leave type gives under one policy version."""

    class Accrual(models.TextChoices):
        YEARLY = "yearly", "All at the start of the year"
        MONTHLY = "monthly", "A twelfth each month"

    version = models.ForeignKey(LeavePolicyVersion, on_delete=models.CASCADE,
                                related_name="rules")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="rules")
    days_per_year = models.DecimalField(max_digits=5, decimal_places=2,
                                        validators=[MinValueValidator(Decimal("0"))])
    accrual = models.CharField(max_length=16, choices=Accrual.choices, default=Accrual.YEARLY)
    carry_forward_days = models.DecimalField(max_digits=5, decimal_places=2, null=True,
                                             blank=True,
                                             validators=[MinValueValidator(Decimal("0"))])
    carry_forward_expires_months = models.PositiveSmallIntegerField(null=True, blank=True)
    allow_half_day = models.BooleanField(default=True)
    allow_hourly = models.BooleanField(default=True)
    allow_negative = models.BooleanField(default=False)

    class Meta:
        db_table = "leaves_policy_rule"
        constraints = [
            models.UniqueConstraint(fields=["version", "leave_type"],
                                    name="uniq_leave_policy_rule_per_type"),
        ]

    def __str__(self):
        return f"{self.version}: {self.leave_type} {self.days_per_year}"


class EmployeeLeavePolicy(TenantOwned, ActorTracked):
    """The policy one employee has, from a date (until the next one)."""

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE,
                                 related_name="leave_policies")
    policy = models.ForeignKey(LeavePolicy, on_delete=models.PROTECT, related_name="employees")
    effective_from = models.DateField()
    # The last day it applies; empty while it is their policy.
    effective_to = models.DateField(null=True, blank=True)

    class Meta:
        db_table = "leaves_employee_policy"
        ordering = ("employee", "effective_from")
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="leave_employee_policy_end_not_before_start",
            ),
        ]

    def __str__(self):
        return f"{self.employee_id}: {self.policy} from {self.effective_from}"


class LeaveLedgerEntry(TenantOwned):
    """Leave given or taken back, in days, for one employee, type and year.
    Leave taken is not written here: it is the live ``LeaveDay`` units."""

    class Kind(models.TextChoices):
        ACCRUAL = "accrual", "Earned"
        CARRY_FORWARD = "carry_forward", "Carried forward"
        EXPIRY = "expiry", "Carried days expired"
        ADJUSTMENT = "adjustment", "Adjusted by hand"

    employee = models.ForeignKey("employees.Employee", on_delete=models.CASCADE,
                                 related_name="leave_ledger")
    leave_type = models.ForeignKey(LeaveType, on_delete=models.PROTECT, related_name="ledger")
    year = models.PositiveSmallIntegerField()
    entry_date = models.DateField()
    kind = models.CharField(max_length=16, choices=Kind.choices)
    units = models.DecimalField(max_digits=7, decimal_places=2)
    # One automatic entry per period: "2026", "2026-03", "cf-2026", "exp-2026".
    period_key = models.CharField(max_length=32, blank=True)
    policy_version = models.ForeignKey(LeavePolicyVersion, null=True, blank=True,
                                       on_delete=models.PROTECT, related_name="+")
    note = models.CharField(max_length=255, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                   on_delete=models.SET_NULL, related_name="+")

    class Meta:
        db_table = "leaves_ledger_entry"
        ordering = ("entry_date", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee", "leave_type", "kind", "period_key"],
                condition=~models.Q(kind="adjustment"),
                name="uniq_automatic_leave_ledger_entry",
            ),
        ]
        indexes = [models.Index(fields=["company", "employee", "leave_type", "year"])]

    def __str__(self):
        return f"{self.employee_id} {self.leave_type_id} {self.year} {self.kind} {self.units}"
