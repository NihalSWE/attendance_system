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

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from common.choices import ActiveStatus
from common.models import ActorTracked, TenantOwned


class PayType(models.TextChoices):
    PAID = "paid", "Paid"
    UNPAID = "unpaid", "Unpaid"


class LeaveType(TenantOwned, ActorTracked):
    """A kind of leave a company offers, e.g. Casual, Sick, Annual."""

    class BalanceUnit(models.TextChoices):
        DAYS = "days", "Days"
        MINUTES = "minutes", "Minutes"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
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
            # Pay type and percentage must agree.
            models.CheckConstraint(
                condition=(
                    models.Q(approved_pay_type="paid", approved_pay_percentage=100)
                    | models.Q(approved_pay_type="unpaid", approved_pay_percentage=0)
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
