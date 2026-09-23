"""Monthly payroll: company salary settings and rule versions, period, run,
one record per employee, and its lines.

Names follow MODEL_FIELD_DICTIONARY.md §54-55, §60-62 and §65. Salary
structures, approval steps, payments and corrections are listed in
docs/PHASE_STATUS.md as not built yet.
"""

from decimal import Decimal

from django.conf import settings
from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import RangeBoundary, RangeOperators
from django.core.exceptions import ValidationError
from django.db import models

from common.choices import ActiveStatus
from common.db import DateRange
from common.models import ActorTracked, TenantOwned

ZERO = Decimal("0.00")
_DATE_PERIOD = DateRange("effective_from", "effective_to", RangeBoundary())


class PayrollPolicyVersion(TenantOwned, ActorTracked):
    """The salary calculation rules in force from a date (dictionary §55).

    Never edited once active: a change is a new version from a later month,
    so a month's salary can always be recalculated with the rules that
    applied to it. Every version starts on the 1st of a month; a month uses
    the version in force on its first day.

    Only the rules the calculation reads today are on the settings page.
    Penalty stacking and recovery caps are stored with their defaults and
    come alive with those features (plan steps A4, A13). Overtime (A9) pays
    approved minutes at × the hourly rate: 2× by default (Ajay, 2026-09-14).
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Replaced"

    class MonthlyProration(models.TextChoices):
        FIXED_DIVISOR = "fixed_30", "Monthly salary ÷ a fixed number of days"
        CALENDAR_DAYS = "calendar_days", "Monthly salary ÷ days in that month"
        SCHEDULED_WORKDAYS = "scheduled_workdays", "Monthly salary ÷ working days in that month"
        NONE = "none", "No per-day value"

    class DailyRateMethod(models.TextChoices):
        EXPLICIT_RATE = "explicit_rate", "The employee's own daily rate"
        MONTHLY_DIVISOR = "monthly_divisor", "Monthly salary ÷ the fixed number of days"
        SCHEDULED_WORKDAYS = "scheduled_workdays", "Monthly salary ÷ working days"

    class HourlyRateMethod(models.TextChoices):
        EXPLICIT_RATE = "explicit_rate", "The employee's own hourly rate"
        DAILY_SCHEDULED_HOURS = "daily_scheduled_hours", "Daily rate ÷ shift hours"
        MONTHLY_STANDARD_HOURS = "monthly_standard_hours", "Monthly salary ÷ standard monthly hours"

    class AbsenceDeduction(models.TextChoices):
        DAY_FRACTION = "day_fraction", "By day: an absent day is one day's pay"
        SCHEDULED_MINUTES = "scheduled_minutes", "By minutes: every minute short of the shift"
        RULE_ONLY = "rule_only", "Only through penalty rules"

    class PaidLeaveTreatment(models.TextChoices):
        FULL_PAY = "full_pay", "Paid in full"

    class PartialLeaveTreatment(models.TextChoices):
        PAY_PERCENTAGE = "pay_percentage", "Paid at the approved percentage"

    class UnpaidLeaveTreatment(models.TextChoices):
        DEDUCT = "deduct", "Deducted like an absent day"

    class OvertimeMethod(models.TextChoices):
        NONE = "none", "No overtime pay"
        HOURLY_RATE = "hourly_rate", "Hourly rate"
        FIXED_RATE = "fixed_rate", "Fixed rate"
        MULTIPLIER = "multiplier", "Hourly rate × multiplier"

    class LateStacking(models.TextChoices):
        HIGHEST_ONLY = "highest_only", "Highest rule only"
        CUMULATIVE = "cumulative", "All matching rules"
        CAPPED = "capped", "All matching rules, capped"

    class RoundingMode(models.TextChoices):
        HALF_UP = "half_up", "To the nearest"
        UP = "up", "Up"
        DOWN = "down", "Down"

    # Formula parameters kept in calculation_config, with their defaults.
    # Defaults reproduce the 2026-09-12 salary, with one correction: an hourly
    # employee's Incomplete day (no check-out) is paid the shift's hours, as
    # monthly and daily staff already had it counted present until reviewed.
    CONFIG_DEFAULTS = {
        "half_day_pay_percent": "50",
        "incomplete_day_treatment": "pay_full",
        "daily_paid_days_off": False,
        "hourly_paid_days_off": False,
    }
    INCOMPLETE_CHOICES = (
        ("pay_full", "Paid in full until reviewed"),
        ("pay_half", "Paid as a half day"),
        ("unpaid", "Not paid"),
    )
    OVERTIME_STEPS = (0, 15, 30, 60)

    name = models.CharField(max_length=120, default="Salary rules")
    code = models.CharField(max_length=32, default="SALARY")
    version_number = models.PositiveIntegerField()
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    monthly_divisor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("30"))
    monthly_proration_method = models.CharField(
        max_length=24, choices=MonthlyProration.choices, default=MonthlyProration.FIXED_DIVISOR
    )
    daily_rate_method = models.CharField(
        max_length=24, choices=DailyRateMethod.choices, default=DailyRateMethod.EXPLICIT_RATE
    )
    hourly_rate_method = models.CharField(
        max_length=24, choices=HourlyRateMethod.choices, default=HourlyRateMethod.EXPLICIT_RATE
    )
    joining_leaving_proration_method = models.CharField(
        max_length=24, choices=MonthlyProration.choices, default=MonthlyProration.CALENDAR_DAYS
    )
    absence_deduction_method = models.CharField(
        max_length=24, choices=AbsenceDeduction.choices, default=AbsenceDeduction.DAY_FRACTION
    )
    paid_leave_treatment = models.CharField(
        max_length=24, choices=PaidLeaveTreatment.choices, default=PaidLeaveTreatment.FULL_PAY
    )
    partial_leave_treatment = models.CharField(
        max_length=24, choices=PartialLeaveTreatment.choices,
        default=PartialLeaveTreatment.PAY_PERCENTAGE,
    )
    unpaid_leave_treatment = models.CharField(
        max_length=24, choices=UnpaidLeaveTreatment.choices, default=UnpaidLeaveTreatment.DEDUCT
    )
    overtime_method = models.CharField(
        max_length=16, choices=OvertimeMethod.choices, default=OvertimeMethod.MULTIPLIER
    )
    overtime_multiplier = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("2"))
    # Work on a holiday or weekly off: every approved minute is paid at this.
    holiday_overtime_multiplier = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("2")
    )
    # Approved overtime is rounded down to blocks of this many minutes (0 =
    # exact), and a day with less than the minimum pays none.
    overtime_rounding_minutes = models.PositiveIntegerField(default=0)
    minimum_overtime_minutes = models.PositiveIntegerField(default=0)
    require_overtime_approval = models.BooleanField(default=True)
    late_penalty_stacking_method = models.CharField(
        max_length=16, choices=LateStacking.choices, default=LateStacking.HIGHEST_ONLY
    )
    maximum_period_deduction_percent = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    maximum_recovery_percent_of_net = models.DecimalField(
        max_digits=18, decimal_places=6, null=True, blank=True
    )
    money_rounding_mode = models.CharField(
        max_length=16, choices=RoundingMode.choices, default=RoundingMode.HALF_UP
    )
    money_rounding_increment = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("0.01")
    )
    allow_negative_net_pay = models.BooleanField(default=False)
    calculation_config = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="activated_payroll_policies",
    )
    activated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "payroll_policy_version"
        ordering = ("-effective_from", "-version_number")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code", "version_number"],
                name="uniq_payroll_policy_version_number",
            ),
            # One set of rules in force at any time.
            ExclusionConstraint(
                name="excl_payroll_policy_active_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("code", RangeOperators.EQUAL),
                    (_DATE_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status="active"),
            ),
            models.CheckConstraint(
                condition=models.Q(monthly_divisor__gt=0),
                name="chk_payroll_policy_divisor_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(money_rounding_increment__gt=0),
                name="chk_payroll_policy_rounding_positive",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="chk_payroll_policy_period_order",
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version_number} from {self.effective_from:%b %Y}"

    def config(self, key):
        return (self.calculation_config or {}).get(key, self.CONFIG_DEFAULTS[key])

    def clean(self):
        super().clean()
        errors = {}
        if self.effective_from and self.effective_from.day != 1:
            errors["effective_from"] = "Salary rules start on the 1st of a month."
        if self.monthly_divisor is not None and not (Decimal("1") <= self.monthly_divisor <= Decimal("31")):
            errors["monthly_divisor"] = "Use a number of days between 1 and 31."
        config = self.calculation_config or {}
        unknown = set(config) - set(self.CONFIG_DEFAULTS)
        if unknown:
            errors["calculation_config"] = f"Unknown setting: {', '.join(sorted(unknown))}."
        try:
            half = Decimal(str(config.get("half_day_pay_percent", "50")))
        except ArithmeticError:
            half = None
        if half is None or not (Decimal("0") <= half <= Decimal("100")):
            errors["calculation_config"] = "A half day's pay must be between 0% and 100%."
        if config.get("incomplete_day_treatment", "pay_full") not in dict(self.INCOMPLETE_CHOICES):
            errors["calculation_config"] = "Unknown treatment for an incomplete day."
        cap = self.maximum_period_deduction_percent
        if cap is not None and not (Decimal("0") < cap <= Decimal("100")):
            errors["maximum_period_deduction_percent"] = "Use a percentage above 0 and up to 100."
        for field in ("overtime_multiplier", "holiday_overtime_multiplier"):
            value = getattr(self, field)
            if value is not None and not (Decimal("1") <= value <= Decimal("10")):
                errors[field] = "Use a number from 1 to 10, e.g. 2 for double pay."
        if self.overtime_rounding_minutes not in self.OVERTIME_STEPS:
            errors["overtime_rounding_minutes"] = "Choose exact minutes, 15, 30 or 60."
        if self.minimum_overtime_minutes > 24 * 60:
            errors["minimum_overtime_minutes"] = "Use at most 1440 minutes (24 hours)."
        if errors:
            raise ValidationError(errors)


class PayrollSettings(TenantOwned, ActorTracked):
    """One row of current salary settings per company (dictionary §54).

    Calculation rules live in PayrollPolicyVersion, so history never depends
    on this mutable row. ``default_salary_structure`` (§54) is added with the
    salary structure tables (plan step A11).
    """

    class PayFrequency(models.TextChoices):
        MONTHLY = "monthly", "Monthly"

    currency = models.CharField(max_length=3, default="BDT")
    pay_frequency = models.CharField(
        max_length=16, choices=PayFrequency.choices, default=PayFrequency.MONTHLY
    )
    period_start_day = models.PositiveSmallIntegerField(default=1)
    default_pay_day = models.PositiveSmallIntegerField(null=True, blank=True)
    default_policy_version = models.ForeignKey(
        PayrollPolicyVersion, null=True, blank=True, on_delete=models.PROTECT,
        related_name="+",
    )
    monthly_divisor = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("30"))
    overtime_enabled = models.BooleanField(default=False)
    bonus_enabled = models.BooleanField(default=False)
    advances_enabled = models.BooleanField(default=False)
    loans_enabled = models.BooleanField(default=False)
    auto_include_approved_overtime = models.BooleanField(default=False)
    auto_include_approved_leave = models.BooleanField(default=True)
    require_payroll_approval = models.BooleanField(default=False)
    allow_negative_net_pay = models.BooleanField(default=False)
    default_payment_method = models.CharField(max_length=32, blank=True, null=True)
    settings_version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payroll_settings"
        constraints = [
            models.UniqueConstraint(fields=["company"], name="uniq_payroll_settings_company"),
            models.CheckConstraint(
                condition=models.Q(default_pay_day__isnull=True)
                | models.Q(default_pay_day__gte=1, default_pay_day__lte=31),
                name="chk_payroll_settings_pay_day",
            ),
        ]

    def __str__(self):
        return f"Salary settings {self.company_id}"


class OvertimeDecision(TenantOwned):
    """Somebody's decision on one employee-day's overtime (plan step A9).

    Kept apart from ``AttendanceRecord`` because attendance recalculates itself
    and rewrites that row; a decision must survive it. The recalculation copies
    the approved minutes back onto the record (``approved_overtime_minutes``),
    which is what salary reads.

    Keyed by employee and date, not by the record, for the same reason.
    """

    class Status(models.TextChoices):
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="overtime_decisions"
    )
    work_date = models.DateField()
    status = models.CharField(max_length=16, choices=Status.choices)
    # What attendance counted when the decision was made, so a later change to
    # the day (a scan arriving late) can be pointed out.
    calculated_minutes = models.PositiveIntegerField(default=0)
    approved_minutes = models.PositiveIntegerField(default=0)
    # The end the approver set for an overtime session nobody scanned out of.
    check_out_at = models.DateTimeField(null=True, blank=True)
    note = models.CharField(max_length=255, blank=True)
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
        related_name="overtime_decisions",
    )
    decided_at = models.DateTimeField()

    class Meta:
        db_table = "payroll_overtime_decision"
        ordering = ("work_date", "employee_id")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee", "work_date"],
                name="uniq_overtime_decision_per_day",
            ),
            models.CheckConstraint(
                condition=~models.Q(status="rejected") | models.Q(approved_minutes=0),
                name="chk_overtime_rejected_pays_nothing",
            ),
        ]
        indexes = [models.Index(fields=["company", "work_date"])]

    def __str__(self):
        return f"{self.employee_id} {self.work_date} {self.status} {self.approved_minutes}"


class AttendancePenaltyRule(TenantOwned, ActorTracked):
    """When attendance costs salary, and how much (dictionary §36).

    Lives in the payroll app (tables keep their planned ``payroll_*`` names)
    because it is a salary setting and the attendance app is Nihal's
    workstream. Versioned like the salary rules: ``code`` names the rule across
    versions, a change is a new version from the 1st of a month, and a month
    uses the versions in force on its first day.
    """

    class Metric(models.TextChoices):
        LATE_MINUTES = "late_minutes", "Arriving late"
        EARLY_OUT_MINUTES = "early_out_minutes", "Leaving early"
        WORKED_SHORTFALL = "worked_shortfall", "Working less than the shift"
        OUTSIDE_MINUTES = "outside_minutes", "Time out of the office"
        ABSENCE = "absence", "An absent day"

    class Operator(models.TextChoices):
        GTE = "gte", "at least"
        GT = "gt", "more than"
        LTE = "lte", "at most"
        LT = "lt", "less than"
        EQUAL = "equal", "exactly"

    class OccurrenceMode(models.TextChoices):
        SINGLE_DAY = "single_day", "Every day it happens"
        WITHIN_PERIOD = "within_period", "Every so many days in a month"
        CONSECUTIVE_WORKDAYS = "consecutive_workdays", "So many working days in a row"
        ROLLING_WINDOW = "rolling_window", "So many days within a rolling window"

    class DeductionMethod(models.TextChoices):
        ACTUAL_MINUTES = "actual_minutes", "The minutes themselves"
        FIXED_MINUTES = "fixed_minutes", "A fixed number of minutes"
        DAY_FRACTION = "day_fraction", "Part of a day's pay"
        FULL_DAY = "full_day", "A full day's pay"
        FIXED_AMOUNT = "fixed_amount", "A fixed amount"

    class Stacking(models.TextChoices):
        HIGHEST_ONLY = "highest_only", "Only the largest in its group"
        ADDITIVE = "additive", "Adds up with other rules"
        CAPPED = "capped", "Adds up, capped per month"

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        RETIRED = "retired", "Replaced"

    MINUTE_METRICS = (
        Metric.LATE_MINUTES, Metric.EARLY_OUT_MINUTES,
        Metric.WORKED_SHORTFALL, Metric.OUTSIDE_MINUTES,
    )
    # How a run of working days in a row treats days that are not working days.
    DEFAULT_SEQUENCE_POLICY = {
        "weekly_off": "skip", "holiday": "skip", "leave": "break", "absent": "break",
    }

    name = models.CharField(max_length=120)
    code = models.CharField(max_length=32)
    metric = models.CharField(max_length=24, choices=Metric.choices)
    operator = models.CharField(max_length=8, choices=Operator.choices, default=Operator.GTE)
    threshold_minutes = models.PositiveIntegerField(null=True, blank=True)
    required_occurrences = models.PositiveIntegerField(default=1)
    occurrence_mode = models.CharField(
        max_length=24, choices=OccurrenceMode.choices, default=OccurrenceMode.SINGLE_DAY
    )
    rolling_window_days = models.PositiveIntegerField(null=True, blank=True)
    sequence_break_policy = models.JSONField(default=dict, blank=True)
    deduction_method = models.CharField(max_length=16, choices=DeductionMethod.choices)
    deduction_value = models.DecimalField(
        max_digits=14, decimal_places=4, default=Decimal("0")
    )
    priority = models.IntegerField(default=0)
    exclusive_group = models.CharField(max_length=40, blank=True, null=True)
    stacking_policy = models.CharField(
        max_length=16, choices=Stacking.choices, default=Stacking.ADDITIVE
    )
    maximum_deduction = models.DecimalField(
        max_digits=14, decimal_places=2, null=True, blank=True
    )
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payroll_attendance_penalty_rule"
        ordering = ("name", "-version")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code", "version"], name="uniq_penalty_rule_version"
            ),
            ExclusionConstraint(
                name="excl_penalty_rule_active_overlap",
                expressions=[
                    ("company", RangeOperators.EQUAL),
                    ("code", RangeOperators.EQUAL),
                    (_DATE_PERIOD, RangeOperators.OVERLAPS),
                ],
                condition=models.Q(status="active"),
            ),
            models.CheckConstraint(
                condition=models.Q(required_occurrences__gte=1),
                name="chk_penalty_rule_occurrences",
            ),
            models.CheckConstraint(
                condition=models.Q(deduction_value__gte=0),
                name="chk_penalty_rule_value_nonnegative",
            ),
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gt=models.F("effective_from")),
                name="chk_penalty_rule_period_order",
            ),
        ]

    def __str__(self):
        return f"{self.name} v{self.version}"

    def sequence_policy(self):
        return {**self.DEFAULT_SEQUENCE_POLICY, **(self.sequence_break_policy or {})}

    def clean(self):
        """Rule inputs must agree with the metric and the deduction method."""
        super().clean()
        errors = {}
        if self.effective_from and self.effective_from.day != 1:
            errors["effective_from"] = "Penalty rules start on the 1st of a month."
        if self.metric in self.MINUTE_METRICS:
            if self.threshold_minutes is None:
                errors["threshold_minutes"] = "Say how many minutes."
        else:
            # An absent day has no minutes to compare.
            self.threshold_minutes = None
        if self.occurrence_mode == self.OccurrenceMode.SINGLE_DAY:
            self.required_occurrences = 1
        elif self.required_occurrences < 2:
            errors["required_occurrences"] = "Use 2 or more days, or choose “Every day it happens”."
        if self.occurrence_mode == self.OccurrenceMode.ROLLING_WINDOW and not self.rolling_window_days:
            errors["rolling_window_days"] = "Say how many days the window covers."
        value = self.deduction_value or Decimal("0")
        if self.deduction_method == self.DeductionMethod.FIXED_MINUTES and value <= 0:
            errors["deduction_value"] = "Say how many minutes to deduct."
        elif self.deduction_method == self.DeductionMethod.DAY_FRACTION and not (
            Decimal("0") < value <= Decimal("31")
        ):
            errors["deduction_value"] = "Use a number of days above 0, e.g. 0.5 for half a day."
        elif self.deduction_method == self.DeductionMethod.FIXED_AMOUNT and value <= 0:
            errors["deduction_value"] = "Say the amount to deduct."
        if self.maximum_deduction is not None and self.maximum_deduction <= 0:
            errors["maximum_deduction"] = "Leave empty for no limit, or use an amount above 0."
        # The stacking policy follows from the inputs rather than being set twice.
        if self.exclusive_group:
            self.stacking_policy = self.Stacking.HIGHEST_ONLY
        elif self.maximum_deduction:
            self.stacking_policy = self.Stacking.CAPPED
        else:
            self.stacking_policy = self.Stacking.ADDITIVE
        if errors:
            raise ValidationError(errors)


class PenaltyAssessment(TenantOwned):
    """One penalty an employee actually incurred in a month (dictionary §37).

    Drafted by salary generation as ``proposed``; regenerating a draft
    replaces the proposed ones but keeps a ``waived`` one, matched by its
    stable ``occurrence_identity``, so a waiver survives regeneration.
    """

    class Status(models.TextChoices):
        PROPOSED = "proposed", "Proposed"
        APPROVED = "approved", "Approved"
        WAIVED = "waived", "Waived"
        POSTED = "posted", "Posted"
        REVERSED = "reversed", "Reversed"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="penalty_assessments"
    )
    penalty_rule = models.ForeignKey(
        AttendancePenaltyRule, on_delete=models.PROTECT, related_name="assessments"
    )
    payroll_period = models.ForeignKey(
        "payroll.PayrollPeriod", null=True, blank=True, on_delete=models.PROTECT,
        related_name="penalty_assessments",
    )
    period_start = models.DateField()
    period_end = models.DateField()
    occurrence_identity = models.CharField(max_length=160)
    occurrence_count = models.PositiveIntegerField(default=1)
    deduction_minutes = models.PositiveIntegerField(default=0)
    deduction_day_fraction = models.DecimalField(max_digits=8, decimal_places=4, default=Decimal("0"))
    deduction_amount = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    currency = models.CharField(max_length=3, default="BDT")
    calculation_details = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PROPOSED)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="decided_penalty_assessments",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    reversal_of = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.PROTECT, related_name="reversals"
    )
    calculated_at = models.DateTimeField()

    class Meta:
        db_table = "payroll_penalty_assessment"
        ordering = ("period_start", "pk")
        constraints = [
            models.UniqueConstraint(
                fields=["company", "occurrence_identity"],
                condition=~models.Q(status="reversed"),
                name="uniq_penalty_occurrence",
            ),
        ]

    def __str__(self):
        return f"{self.penalty_rule_id} {self.employee_id} {self.deduction_amount}"


class PenaltyAssessmentAttendance(TenantOwned):
    """The attendance days behind one penalty (dictionary §38)."""

    penalty_assessment = models.ForeignKey(
        PenaltyAssessment, on_delete=models.PROTECT, related_name="days"
    )
    # CASCADE, not the dictionary's PROTECT. Attendance recalculates itself and
    # drops a day that no longer applies (e.g. a placement ended); a PROTECT
    # link from a draft penalty would make that recalculation fail. The
    # penalty is proposed and rebuilt at the next salary generation anyway; a
    # finalised month's attendance never recalculates, so its evidence stays.
    attendance_record = models.ForeignKey(
        "attendance.AttendanceRecord", on_delete=models.CASCADE,
        related_name="penalty_links",
    )
    sequence_number = models.PositiveIntegerField(default=1)
    qualifying_value = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    reason_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_penalty_assessment_attendance"
        ordering = ("sequence_number",)
        constraints = [
            models.UniqueConstraint(
                fields=["penalty_assessment", "attendance_record"],
                name="uniq_penalty_assessment_day",
            ),
        ]


class PayrollPeriod(TenantOwned, ActorTracked):
    name = models.CharField(max_length=64)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        db_table = "payroll_period"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "start_date", "end_date"],
                name="uniq_payroll_period_per_company",
            ),
        ]

    def __str__(self):
        return self.name


class PayrollRun(TenantOwned, ActorTracked):
    # Draft -> Waiting for approval -> Finalised. Whoever submits cannot also
    # approve, unless the company has a single owner/admin (payroll/services.py
    # approval_blocker); send back returns it to Draft with a reason.
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        SUBMITTED = "submitted", "Waiting for approval"
        POSTED = "posted", "Finalised"

    payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.PROTECT, related_name="runs"
    )
    # The rules this run was calculated with. Null means the standard rules,
    # used before a company saved its own salary settings; the values used
    # are also copied into totals_snapshot["rules"].
    policy_version = models.ForeignKey(
        PayrollPolicyVersion, null=True, blank=True, on_delete=models.PROTECT,
        related_name="runs",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="generated_payroll_runs",
    )
    calculation_finished_at = models.DateTimeField(null=True, blank=True)
    posted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="posted_payroll_runs",
    )
    posted_at = models.DateTimeField(null=True, blank=True)
    submitted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="submitted_payroll_runs",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    # The last send-back, shown on the Salary page until it is submitted again
    # (every send-back is also in the audit trail).
    returned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="returned_payroll_runs",
    )
    returned_at = models.DateTimeField(null=True, blank=True)
    return_reason = models.TextField(blank=True)
    totals_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_run"
        constraints = [
            # One finalised run per period: a second one needs the correction
            # path, which is not built yet.
            models.UniqueConstraint(
                fields=["company", "payroll_period"],
                condition=models.Q(status="posted"),
                name="uniq_posted_run_per_period",
            ),
        ]

    def __str__(self):
        return f"Payroll {self.payroll_period}"


class PayrollRecord(TenantOwned):
    payroll_run = models.ForeignKey(
        PayrollRun, on_delete=models.PROTECT, related_name="records"
    )
    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="payroll_records"
    )
    employee_assignment_at_period_end = models.ForeignKey(
        "employees.EmployeeAssignment", null=True, blank=True, on_delete=models.PROTECT,
        related_name="payroll_records",
    )
    currency = models.CharField(max_length=3, default="BDT")
    gross_earnings = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    total_deductions = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    net_pay = models.DecimalField(max_digits=14, decimal_places=2, default=ZERO)
    calculation_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_record"
        constraints = [
            models.UniqueConstraint(
                fields=["payroll_run", "employee"], name="uniq_payroll_record_per_run"
            ),
        ]

    def __str__(self):
        return f"{self.employee_id} {self.net_pay}"


class PayrollAdjustment(TenantOwned, ActorTracked):
    """A one-time bonus or deduction on one employee's month (A11 part 2, dictionary §77).

    Kept simple: amount and reason, added on a draft salary. Generation turns
    each active one into a manual payslip line, so it survives regenerating.
    Removing it marks it removed; nothing is deleted.
    """

    class AdjustmentType(models.TextChoices):
        EARNING = "earning", "Bonus"
        DEDUCTION = "deduction", "Deduction"

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Removed"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="payroll_adjustments"
    )
    target_payroll_period = models.ForeignKey(
        PayrollPeriod, on_delete=models.PROTECT, related_name="adjustments"
    )
    adjustment_type = models.CharField(max_length=16, choices=AdjustmentType.choices)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reason = models.CharField(max_length=255)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        db_table = "payroll_adjustment"
        ordering = ("pk",)
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0), name="payroll_adjustment_amount_positive"
            ),
        ]

    def __str__(self):
        return f"{self.get_adjustment_type_display()} {self.amount}"


class PayrollLine(TenantOwned):
    class LineType(models.TextChoices):
        EARNING = "earning", "Earning"
        DEDUCTION = "deduction", "Deduction"

    payroll_record = models.ForeignKey(
        PayrollRecord, on_delete=models.PROTECT, related_name="lines"
    )
    line_type = models.CharField(max_length=16, choices=LineType.choices)
    # What caused the line (dictionary §65). Only "penalty" carries a typed
    # source so far; the other sources arrive with their features.
    source_type = models.CharField(max_length=24, blank=True, default="")
    penalty_assessment = models.ForeignKey(
        PenaltyAssessment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="payroll_lines",
    )
    payroll_adjustment = models.ForeignKey(
        PayrollAdjustment, null=True, blank=True, on_delete=models.PROTECT,
        related_name="payroll_lines",
    )
    code = models.CharField(max_length=32)
    description = models.CharField(max_length=255)
    quantity = models.DecimalField(max_digits=10, decimal_places=2, default=ZERO)
    rate = models.DecimalField(max_digits=14, decimal_places=4, default=ZERO)
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    is_manual = models.BooleanField(default=False)
    sequence = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "payroll_line"
        ordering = ("sequence", "pk")

    def __str__(self):
        return f"{self.code} {self.amount}"


class SalaryComponent(TenantOwned, ActorTracked):
    """One kind of recurring allowance or deduction the company pays or takes.

    The catalogue only: House rent, Transport, Provident fund. What an
    employee actually gets is an ``EmployeeSalaryComponent`` - a component on
    its own pays nobody. Basic salary is not a component; it stays on
    ``EmployeeCompensation`` so it is not kept in two places.
    """

    class Kind(models.TextChoices):
        EARNING = "earning", "Allowance"
        DEDUCTION = "deduction", "Deduction"

    class Method(models.TextChoices):
        FIXED = "fixed", "Fixed amount"
        PERCENT_OF_BASIC = "percent_of_basic", "Percentage of basic"

    code = models.CharField(max_length=32)
    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.EARNING)
    method = models.CharField(max_length=24, choices=Method.choices, default=Method.FIXED)
    # What an employee is given by default; either may be changed per person.
    default_amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    default_percent = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    description = models.TextField(blank=True)
    status = models.CharField(
        max_length=16, choices=ActiveStatus.choices, default=ActiveStatus.ACTIVE
    )

    class Meta:
        db_table = "payroll_salary_component"
        constraints = [
            models.UniqueConstraint(
                fields=["company", "code"], name="uniq_salary_component_code_per_company"
            ),
            models.UniqueConstraint(
                fields=["company", "name"], name="uniq_salary_component_name_per_company"
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(method="fixed", default_percent__isnull=True)
                    | models.Q(method="percent_of_basic", default_amount__isnull=True)
                ),
                name="salary_component_amount_matches_method",
            ),
        ]
        ordering = ("kind", "name")

    def __str__(self):
        return f"{self.code} {self.name}"


class EmployeeSalaryComponent(TenantOwned, ActorTracked):
    """One employee's allowance or deduction, from a date until it is ended.

    Dated rather than edited in place: a payslip already finalised keeps the
    amount it was paid with, and a raise is a new row, exactly as
    ``EmployeeCompensation`` works for basic pay.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        CANCELLED = "cancelled", "Cancelled"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="salary_components"
    )
    component = models.ForeignKey(
        SalaryComponent, on_delete=models.PROTECT, related_name="employees"
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    percent = models.DecimalField(max_digits=7, decimal_places=3, null=True, blank=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)
    reason = models.CharField(max_length=255, blank=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.ACTIVE
    )

    class Meta:
        db_table = "payroll_employee_salary_component"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(effective_to__isnull=True)
                | models.Q(effective_to__gte=models.F("effective_from")),
                name="employee_component_end_after_start",
            ),
            models.UniqueConstraint(
                fields=["employee", "component", "effective_from"],
                condition=~models.Q(status="cancelled"),
                name="uniq_employee_component_start",
            ),
        ]
        indexes = [models.Index(fields=["company", "employee", "effective_from"])]
        ordering = ("component__name", "-effective_from")

    def __str__(self):
        return f"{self.employee_id} {self.component_id}"

    def in_force_on(self, day):
        return (
            self.status == self.Status.ACTIVE
            and self.effective_from <= day
            and (self.effective_to is None or day <= self.effective_to)
        )
