"""Scheduling: shifts, shift assignment, weekly offs, holidays and holiday work.

This is the calendar layer attendance and payroll read from. Everything here is
effective-dated so a past attendance record can be recalculated against the rules
that were actually in force on that day, not today's settings.

See MODEL_FIELD_DICTIONARY.md §15-21.
"""

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Coalesce

from common.choices import ActiveStatus, DeviceAttendanceScope
from common.db import DateRange, TstzRange
from common.models import ActorTracked, TenantOwned

# Date-only effective period, '[)' bounds (inclusive start, exclusive end).
_DATE_PERIOD = DateRange("effective_from", "effective_to", RangeBoundary())
# Instant-based effective period for employee-level assignment history.
_TS_PERIOD = TstzRange("effective_from", "effective_to", RangeBoundary())
# Treats a NULL branch (company-wide scope) as a comparable value so two
# company-wide rules actually collide; in SQL, NULL never equals NULL.
_BRANCH_SCOPE = Coalesce("branch", 0, output_field=models.BigIntegerField())


class Shift(TenantOwned, ActorTracked):
    """A working-time template: start/end, grace, breaks and overtime threshold."""

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=255)

    start_time = models.TimeField()
    end_time = models.TimeField()
    # A night shift ends on the following calendar day (e.g. 22:00 -> 06:00).
    spans_next_day = models.BooleanField(default=False)

    scheduled_minutes = models.PositiveIntegerField()
    grace_in_minutes = models.PositiveIntegerField(default=0)
    grace_out_minutes = models.PositiveIntegerField(default=0)
    minimum_full_day_minutes = models.PositiveIntegerField(default=0)
    minimum_half_day_minutes = models.PositiveIntegerField(default=0)
    default_break_minutes = models.PositiveIntegerField(default=0)
    break_is_paid = models.BooleanField(default=False)
    overtime_after_minutes = models.PositiveIntegerField(default=0)

    effective_from = models.DateField(null=True, blank=True)
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "scheduling_shift"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_shift_code_per_company"
            ),
            models.CheckConstraint(
                condition=models.Q(scheduled_minutes__gt=0),
                name="shift_scheduled_minutes_positive",
            ),
            # A half day cannot require more minutes than a full day.
            models.CheckConstraint(
                condition=models.Q(
                    minimum_half_day_minutes__lte=models.F("minimum_full_day_minutes")
                ),
                name="shift_half_day_not_over_full_day",
            ),
        ]

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        errors = {}
        # Either time may be missing when a form has already rejected it; the
        # field error is reported there, so do not crash comparing None here.
        if (
            self.start_time is not None
            and self.end_time is not None
            and not self.spans_next_day
            and self.end_time <= self.start_time
        ):
            errors["end_time"] = (
                "End time must be after start time unless the shift spans midnight."
            )
        # Only meaningful for a real shift length. A zero or negative length is
        # a start/end problem, reported against those fields and refused by the
        # scheduled_minutes check constraint, not a break problem.
        if (
            self.scheduled_minutes
            and self.scheduled_minutes > 0
            and self.break_is_paid is False
            and self.default_break_minutes >= self.scheduled_minutes
        ):
            errors["default_break_minutes"] = (
                "Unpaid break cannot consume the whole scheduled time."
            )
        if errors:
            raise ValidationError(errors)


class CompanyAttendanceSettings(TenantOwned, ActorTracked):
    """One attendance-policy row per company.

    The dictionary describes this as a O2O on Company. We keep TenantOwned's
    ``company`` FK (so scoping and the shared base still apply) and add a unique
    constraint on it — at the database level that is exactly what a one-to-one
    is: a unique foreign key. The Django field is an FK; its unique constraint enforces the
    one-settings-row-per-company relationship.
    """

    class ShiftMode(models.TextChoices):
        COMPANY_SINGLE_SHIFT = "company_single_shift", "One shift for the company"
        DEPARTMENT_SHIFTS = "department_shifts", "Shifts per department"

    class MissingPunchPolicy(models.TextChoices):
        REVIEW_REQUIRED = "review_required", "Review required"
        AUTO_ABSENT = "auto_absent", "Treat as absent"

    shift_mode = models.CharField(
        max_length=32, choices=ShiftMode.choices, default=ShiftMode.COMPANY_SINGLE_SHIFT
    )
    company_shift = models.ForeignKey(
        Shift,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="companies_using_as_single_shift",
    )

    punch_pairing_strategy = models.CharField(max_length=32, default="alternating")
    device_attendance_scope = models.CharField(
        max_length=32,
        choices=DeviceAttendanceScope.choices,
        default=DeviceAttendanceScope.ASSIGNED_DEVICES,
    )

    duplicate_punch_window_seconds = models.PositiveIntegerField(default=30)
    attendance_window_before_minutes = models.PositiveIntegerField(default=120)
    attendance_window_after_minutes = models.PositiveIntegerField(default=120)
    # A device outage is not proof of absence, so review is the default.
    missing_punch_policy = models.CharField(
        max_length=32,
        choices=MissingPunchPolicy.choices,
        default=MissingPunchPolicy.REVIEW_REQUIRED,
    )
    overtime_requires_approval = models.BooleanField(default=True)
    round_work_minutes_to = models.PositiveIntegerField(default=1)
    round_overtime_minutes_to = models.PositiveIntegerField(default=1)

    settings_version = models.PositiveIntegerField(default=1)
    effective_from = models.DateTimeField()

    class Meta:
        db_table = "scheduling_companyattendancesettings"
        verbose_name_plural = "company attendance settings"
        constraints = [
            models.UniqueConstraint(
                fields=["company"], name="uniq_attendance_settings_per_company"
            ),
            # A single-shift company must name the shift it uses.
            models.CheckConstraint(
                condition=~models.Q(shift_mode="company_single_shift")
                | models.Q(company_shift__isnull=False),
                name="single_shift_mode_requires_company_shift",
            ),
        ]

    def __str__(self):
        return f"Attendance settings for company {self.company_id}"


class DepartmentShift(TenantOwned, ActorTracked):
    """Which shifts a department offers, and which is its default."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    department = models.ForeignKey(
        "organization.CompanyDepartment",
        on_delete=models.PROTECT,
        related_name="shift_links",
    )
    shift = models.ForeignKey(
        Shift, on_delete=models.PROTECT, related_name="department_links"
    )
    is_default = models.BooleanField(default=False)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "scheduling_departmentshift"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="departmentshift_period_end_after_start",
            ),
            # The same department/shift pair must not be linked twice at once.
            ExclusionConstraint(
                name="excl_departmentshift_duplicate_period",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("department", RangeOperators.EQUAL),
                    ("shift", RangeOperators.EQUAL),
                    (_DATE_PERIOD, RangeOperators.OVERLAPS),
                ],
            ),
            # A department has at most one default shift at any moment.
            ExclusionConstraint(
                name="excl_departmentshift_single_default",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("department", RangeOperators.EQUAL),
                    (_DATE_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=models.Q(is_default=True),
            ),
        ]

    def __str__(self):
        return f"{self.department_id} -> {self.shift_id}"


class EmployeeShiftAssignment(TenantOwned, ActorTracked):
    """An employee's effective shift over time (selection, override or temporary)."""

    class AssignmentType(models.TextChoices):
        DEPARTMENT_SELECTION = "department_selection", "Department selection"
        EMPLOYEE_OVERRIDE = "employee_override", "Employee override"
        TEMPORARY = "temporary", "Temporary"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="shift_assignments"
    )
    shift = models.ForeignKey(
        Shift, on_delete=models.PROTECT, related_name="employee_assignments"
    )
    effective_from = models.DateTimeField()
    effective_to = models.DateTimeField(null=True, blank=True)
    assignment_type = models.CharField(
        max_length=32,
        choices=AssignmentType.choices,
        default=AssignmentType.DEPARTMENT_SELECTION,
    )
    reason = models.TextField(blank=True)
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="shift_assignments_made",
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "scheduling_employeeshiftassignment"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="shiftassignment_period_end_after_start",
            ),
            # One effective shift per employee at any instant.
            ExclusionConstraint(
                name="excl_employee_shift_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("employee", RangeOperators.EQUAL),
                    (_TS_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=~models.Q(status="cancelled"),
            ),
        ]
        indexes = [
            models.Index(fields=["company", "employee", "effective_from"]),
        ]

    def __str__(self):
        return f"{self.employee_id} -> {self.shift_id}"


class WeeklyOffRule(TenantOwned, ActorTracked):
    """A recurring weekly non-working day, company-wide or per branch."""

    class Weekday(models.IntegerChoices):
        MONDAY = 0, "Monday"
        TUESDAY = 1, "Tuesday"
        WEDNESDAY = 2, "Wednesday"
        THURSDAY = 3, "Thursday"
        FRIDAY = 4, "Friday"
        SATURDAY = 5, "Saturday"
        SUNDAY = 6, "Sunday"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ENDED = "ended", "Ended"

    # Null branch means the rule applies company-wide.
    branch = models.ForeignKey(
        "organization.Branch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="weekly_off_rules",
    )
    weekday = models.IntegerField(choices=Weekday.choices)
    is_paid = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "scheduling_weeklyoffrule"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="weeklyoff_period_end_after_start",
            ),
            # No duplicate weekday rule for the same scope at the same time.
            # Coalesce makes two company-wide (NULL branch) rules collide.
            ExclusionConstraint(
                name="excl_weeklyoff_duplicate_period",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    (_BRANCH_SCOPE, RangeOperators.EQUAL),
                    ("weekday", RangeOperators.EQUAL),
                    (_DATE_PERIOD, RangeOperators.OVERLAPS),
                ],
            ),
        ]

    def __str__(self):
        return f"{self.get_weekday_display()} off"


class Holiday(TenantOwned, ActorTracked):
    """A full-date special holiday. Half-day holidays are explicitly out of scope."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    branch = models.ForeignKey(
        "organization.Branch",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="holidays",
    )
    holiday_date = models.DateField()
    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    is_paid = models.BooleanField(default=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    cancelled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="cancelled_holidays",
    )

    class Meta:
        db_table = "scheduling_holiday"
        constraints = [
            # One active holiday per scope per date; Coalesce treats company-wide
            # (NULL branch) as a comparable scope.
            models.UniqueConstraint(
                "company",
                "holiday_date",
                _BRANCH_SCOPE,
                condition=models.Q(status="active"),
                name="uniq_active_holiday_per_scope_date",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "holiday_date"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.holiday_date})"


class HolidayWorkAssignment(TenantOwned, ActorTracked):
    """Approval for an employee to work on a holiday or a recurring weekly off.

    Exactly one source applies: a dated Holiday or a recurring WeeklyOffRule.
    Representing the weekly-off case as its own FK means working a weekly off
    never requires deleting the recurring rule.
    """

    class Treatment(models.TextChoices):
        NORMAL_WORKDAY = "normal_workday", "Normal workday"
        OVERTIME = "overtime", "Overtime"
        COMPENSATORY_LEAVE = "compensatory_leave", "Compensatory leave"
        OVERTIME_AND_COMP_LEAVE = "overtime_and_comp_leave", "Overtime + comp leave"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        APPROVED = "approved", "Approved"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="holiday_work"
    )
    work_date = models.DateField()
    holiday = models.ForeignKey(
        Holiday,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_assignments",
    )
    weekly_off_rule = models.ForeignKey(
        WeeklyOffRule,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="work_assignments",
    )
    shift = models.ForeignKey(
        Shift,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="holiday_work_assignments",
    )
    treatment = models.CharField(
        max_length=32, choices=Treatment.choices, default=Treatment.NORMAL_WORKDAY
    )
    reason = models.TextField(blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="approved_holiday_work",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.DRAFT
    )

    class Meta:
        db_table = "scheduling_holidayworkassignment"
        constraints = [
            # Exactly one source: a holiday XOR a weekly-off rule.
            models.CheckConstraint(
                condition=(
                    models.Q(holiday__isnull=False, weekly_off_rule__isnull=True)
                    | models.Q(holiday__isnull=True, weekly_off_rule__isnull=False)
                ),
                name="holidaywork_exactly_one_source",
            ),
            # One live assignment per employee per date. Slightly stricter than
            # the dictionary's per-source wording: two contradictory assignments
            # for one employee on one date are never legitimate.
            models.UniqueConstraint(
                fields=["company", "employee", "work_date"],
                condition=~models.Q(status="cancelled"),
                name="uniq_live_holiday_work_per_employee_date",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "work_date"]),
        ]

    def __str__(self):
        return f"{self.employee_id} works {self.work_date}"

    def clean(self):
        super().clean()
        errors = {}
        if bool(self.holiday_id) == bool(self.weekly_off_rule_id):
            errors["holiday"] = (
                "Provide exactly one source: a holiday or a weekly-off rule."
            )
        # The chosen source must actually apply on work_date.
        if self.holiday_id and self.holiday.holiday_date != self.work_date:
            errors["work_date"] = "Work date does not match the holiday's date."
        if self.weekly_off_rule_id and self.work_date:
            if self.work_date.weekday() != self.weekly_off_rule.weekday:
                errors["work_date"] = (
                    "Work date does not fall on the rule's weekday."
                )
        if errors:
            raise ValidationError(errors)
