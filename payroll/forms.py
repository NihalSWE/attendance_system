"""Salary settings forms. Convenience only — ``payroll.policy`` re-validates
every value and re-checks the actor before writing."""

import datetime
from decimal import Decimal

from django import forms
from django.utils import timezone

from attendance.views import MONTHS
from common.forms import StyledFormMixin
from payroll.models import PayrollPolicyVersion
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
        }

    def service_values(self):
        data = dict(self.cleaned_data)
        data["effective_from"] = datetime.date(data.pop("applies_year"), data.pop("applies_month"), 1)
        return data


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
