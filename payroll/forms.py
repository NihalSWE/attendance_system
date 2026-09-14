"""Salary settings forms. Convenience only — ``payroll.policy`` re-validates
every value and re-checks the actor before writing."""

import datetime
from decimal import Decimal

from django import forms
from django.utils import timezone

from attendance.views import MONTHS
from common.forms import StyledFormMixin
from payroll import penalties
from payroll.models import AttendancePenaltyRule, PayrollPolicyVersion
from payroll.policy import plain

Version = PayrollPolicyVersion

ROUNDING_STEPS = (
    ("0.01", "Exact amount (no rounding)"),
    ("1", "Whole units (1)"),
    ("5", "Nearest 5"),
    ("10", "Nearest 10"),
)


class SalaryRulesForm(StyledFormMixin, forms.Form):
    """New salary rules from a month. Saving never edits the rules in force."""

    applies_month = forms.TypedChoiceField(label="Applies from month", choices=MONTHS, coerce=int)
    applies_year = forms.TypedChoiceField(label="Year", coerce=int)

    monthly_proration_method = forms.ChoiceField(
        label="One day of a monthly salary is",
        choices=[
            choice for choice in Version.MonthlyProration.choices
            if choice[0] != Version.MonthlyProration.NONE
        ],
        help_text="Used to deduct absent days, unpaid leave and unpaid days off.",
    )
    monthly_divisor = forms.DecimalField(
        label="Fixed number of days",
        min_value=Decimal("1"), max_value=Decimal("31"), decimal_places=2,
        help_text="Only used with “a fixed number of days”. The standard is 30.",
    )
    absence_deduction_method = forms.ChoiceField(
        label="Absence is deducted",
        choices=Version.AbsenceDeduction.choices,
        help_text=(
            "“By minutes” also deducts arriving late or leaving early. "
            "“Only through penalty rules” deducts nothing until penalty rules are set."
        ),
    )
    half_day_pay_percent = forms.DecimalField(
        label="A half day pays (%)",
        min_value=Decimal("0"), max_value=Decimal("100"), decimal_places=2,
        help_text="Share of one day's pay. The standard is 50%.",
    )
    incomplete_day_treatment = forms.ChoiceField(
        label="A day without a check-out is",
        choices=Version.INCOMPLETE_CHOICES,
        help_text="Until someone corrects the day.",
    )
    daily_paid_days_off = forms.BooleanField(
        required=False, label="Pay daily-rate staff for paid holidays and weekly offs",
    )
    hourly_paid_days_off = forms.BooleanField(
        required=False, label="Pay hourly staff their shift hours on paid holidays and weekly offs",
    )
    money_rounding_increment = forms.TypedChoiceField(
        label="Round net salary to", choices=ROUNDING_STEPS, coerce=Decimal,
    )
    money_rounding_mode = forms.ChoiceField(
        label="Rounding direction", choices=Version.RoundingMode.choices,
    )
    allow_negative_net_pay = forms.BooleanField(
        required=False, label="Allow a salary below zero when deductions are larger than pay",
    )
    maximum_period_deduction_percent = forms.DecimalField(
        label="Penalties can take at most (%)", required=False,
        min_value=Decimal("0.01"), max_value=Decimal("100"), decimal_places=2,
        help_text="Of a month's pay, all penalty rules together. Leave empty for no limit.",
    )

    def __init__(self, *args, years=None, **kwargs):
        super().__init__(*args, **kwargs)
        today = timezone.localdate()
        years = set(years or range(today.year - 1, today.year + 3))
        # A change already saved further ahead must still be selectable.
        start = self.initial.get("applies_year")
        if start:
            years |= set(range(min(years), start + 1))
        self.fields["applies_year"].choices = [(year, year) for year in sorted(years)]

    @classmethod
    def initial_from(cls, rules, today):
        """Start the form from the rules in force, applying from this month."""
        version = rules.version or Version()
        return {
            "applies_month": today.month,
            "applies_year": today.year,
            "monthly_proration_method": version.monthly_proration_method,
            "monthly_divisor": plain(version.monthly_divisor),
            "absence_deduction_method": version.absence_deduction_method,
            "half_day_pay_percent": Decimal(str(version.config("half_day_pay_percent"))),
            "incomplete_day_treatment": version.config("incomplete_day_treatment"),
            "daily_paid_days_off": version.config("daily_paid_days_off"),
            "hourly_paid_days_off": version.config("hourly_paid_days_off"),
            "money_rounding_increment": plain(version.money_rounding_increment),
            "money_rounding_mode": version.money_rounding_mode,
            "allow_negative_net_pay": version.allow_negative_net_pay,
            "maximum_period_deduction_percent": (
                plain(version.maximum_period_deduction_percent)
                if version.maximum_period_deduction_percent is not None else None
            ),
        }

    def service_values(self):
        data = dict(self.cleaned_data)
        data["effective_from"] = datetime.date(data.pop("applies_year"), data.pop("applies_month"), 1)
        return data


def _month_fields(label):
    return (
        forms.TypedChoiceField(label=label, choices=MONTHS, coerce=int),
        forms.TypedChoiceField(label="Year", coerce=int),
    )


def _set_month_choices(form, month_key, year_key):
    """This month by default; years around today, plus any year already chosen."""
    today = timezone.localdate()
    form.fields[month_key].initial = today.month
    form.fields[year_key].initial = today.year
    years = set(range(today.year - 1, today.year + 3))
    if form.initial.get(year_key):
        years.add(form.initial[year_key])
    form.fields[year_key].choices = [(year, year) for year in sorted(years)]


class PenaltyRuleForm(StyledFormMixin, forms.Form):
    """A penalty rule, or a new version of one from a month."""

    Rule = AttendancePenaltyRule

    name = forms.CharField(label="Rule name", max_length=120,
                           help_text="Shown on payslips, e.g. “Late more than 10 minutes”.")
    metric = forms.ChoiceField(
        label="What it measures",
        choices=[(m, AttendancePenaltyRule.Metric(m).label) for m in penalties.AVAILABLE_METRICS],
    )
    operator = forms.ChoiceField(label="By", choices=AttendancePenaltyRule.Operator.choices)
    threshold_minutes = forms.IntegerField(
        label="Minutes", required=False, min_value=0, max_value=1440,
        help_text="Not used for an absent day.",
    )
    occurrence_mode = forms.ChoiceField(
        label="It counts",
        choices=[(m, AttendancePenaltyRule.OccurrenceMode(m).label) for m in penalties.AVAILABLE_MODES],
    )
    required_occurrences = forms.IntegerField(
        label="How many days", min_value=1, max_value=31, initial=1,
        help_text="For “every so many days” or “in a row”, e.g. 3.",
    )
    deduction_method = forms.ChoiceField(
        label="Deduct", choices=AttendancePenaltyRule.DeductionMethod.choices,
    )
    deduction_value = forms.DecimalField(
        label="Amount", required=False, min_value=Decimal("0"), decimal_places=4,
        help_text="Minutes, days (0.5 = half a day) or money, depending on what is deducted.",
    )
    exclusive_group = forms.CharField(
        label="Group", required=False, max_length=40,
        help_text="Rules in one group do not add up on the same day: the larger counts. Optional.",
    )
    maximum_deduction = forms.DecimalField(
        label="At most per month", required=False, min_value=Decimal("0.01"), decimal_places=2,
        help_text="Leave empty for no limit.",
    )
    applies_month, applies_year = _month_fields("Applies from month")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _set_month_choices(self, "applies_month", "applies_year")

    @classmethod
    def initial_from(cls, rule, today):
        month = max(rule.effective_from, today.replace(day=1))
        return {
            **{field: getattr(rule, field) for field in penalties.RULE_FIELDS},
            "deduction_value": plain(rule.deduction_value),
            "applies_month": month.month,
            "applies_year": month.year,
        }

    def service_values(self):
        data = dict(self.cleaned_data)
        data["effective_from"] = datetime.date(data.pop("applies_year"), data.pop("applies_month"), 1)
        if data.get("deduction_value") is None:
            data["deduction_value"] = Decimal("0")
        return data


class StopPenaltyRuleForm(StyledFormMixin, forms.Form):
    stops_month, stops_year = _month_fields("No longer applies from month")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _set_month_choices(self, "stops_month", "stops_year")

    def stops_from(self):
        return datetime.date(self.cleaned_data["stops_year"], self.cleaned_data["stops_month"], 1)


class GeneralSettingsForm(StyledFormMixin, forms.Form):
    currency = forms.RegexField(
        label="Currency", regex=r"^[A-Za-z]{3}$", max_length=3,
        error_messages={"invalid": "Use a three-letter currency code, e.g. BDT."},
        help_text="Three-letter code, e.g. BDT. Used on payslips when a salary has none.",
    )
    default_pay_day = forms.IntegerField(
        label="Pay day", required=False, min_value=1, max_value=31,
        help_text="Day of the month salary is usually paid. Optional.",
    )
