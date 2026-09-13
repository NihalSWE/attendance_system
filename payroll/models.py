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
    Overtime, penalty stacking and recovery caps are stored with their
    defaults and come alive with those features (plan steps A4, A9, A13).
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
        max_length=16, choices=OvertimeMethod.choices, default=OvertimeMethod.NONE
    )
    overtime_multiplier = models.DecimalField(max_digits=18, decimal_places=6, default=Decimal("1"))
    holiday_overtime_multiplier = models.DecimalField(
        max_digits=18, decimal_places=6, default=Decimal("1")
    )
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
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
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


class PayrollLine(TenantOwned):
    class LineType(models.TextChoices):
        EARNING = "earning", "Earning"
        DEDUCTION = "deduction", "Deduction"

    payroll_record = models.ForeignKey(
        PayrollRecord, on_delete=models.PROTECT, related_name="lines"
    )
    line_type = models.CharField(max_length=16, choices=LineType.choices)
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
