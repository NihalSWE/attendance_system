"""LFA forms (2026-09-28). Convenience only: ``payroll.lfa`` checks everything."""

import datetime

from django import forms
from django.utils import timezone

from common.forms import StyledFormMixin
from employees.models import Employee
from leaves.documents import DocumentField
from leaves.models import LeaveType
from payroll.models import LfaSettings

YES_NO = (("0", "No"), ("1", "Yes"))


def _yes_no(label, help_text=""):
    return forms.TypedChoiceField(choices=YES_NO, coerce=lambda v: v == "1", label=label,
                                  widget=forms.RadioSelect, help_text=help_text,
                                  empty_value=False)


class LfaSettingsForm(StyledFormMixin, forms.Form):
    """The company's LFA rules, filled in and switched on by the owner or admin."""

    enabled = _yes_no("LFA is on", "Off: nobody can claim it. Claims already made stay.")
    name = forms.CharField(max_length=100, label="What it is called",
                           help_text="As on the payslip, e.g. Leave Fare Assistance.")
    description = forms.CharField(required=False, label="Note for employees",
                                  widget=forms.Textarea(attrs={"rows": 2}),
                                  help_text="Shown to employees on their LFA page.")
    amount_method = forms.ChoiceField(choices=LfaSettings.AmountMethod.choices,
                                      label="How much")
    fixed_amount = forms.DecimalField(required=False, min_value=1, max_digits=14,
                                      decimal_places=2, label="Amount")
    months = forms.DecimalField(required=False, min_value=0.25, max_value=24, max_digits=4,
                                decimal_places=2, label="How many months",
                                help_text="1 = one month's salary; 1.5 = one and a half.")
    max_amount = forms.DecimalField(required=False, min_value=1, max_digits=14,
                                    decimal_places=2, label="At most",
                                    help_text="Optional: the most one claim can pay.")
    min_service_months = forms.IntegerField(min_value=0, max_value=600,
                                            label="Months of service needed",
                                            help_text="0 for none. Counted from the joining date.")
    probation_eligible = _yes_no("People on probation can claim it")
    cycle = forms.ChoiceField(choices=LfaSettings.Cycle.choices, label="How often")
    claims_per_cycle = forms.IntegerField(min_value=1, max_value=12, label="Claims in that year",
                                          help_text="Usually 1.")
    requires_leave = _yes_no("Leave must be taken with it",
                             "No: they can claim it without taking leave or going anywhere.")
    leave_types = forms.ModelMultipleChoiceField(
        queryset=LeaveType.all_objects.none(), required=False, label="Which leave counts",
        help_text="None chosen: any leave type.", widget=forms.CheckboxSelectMultiple)
    min_leave_days = forms.DecimalField(required=False, min_value=0.5, max_digits=5,
                                        decimal_places=2, label="At least this many days of leave")
    requires_document = _yes_no("Proof must be attached",
                                "e.g. a ticket or receipt. No: nothing to attach.")
    prorate_first_cycle = _yes_no(
        "Less in the first year",
        "Someone who joined during the year gets a share for the months they were here.")
    payment = forms.ChoiceField(choices=LfaSettings.Payment.choices, label="Paid")

    def __init__(self, *args, leave_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        if leave_types is not None:
            self.fields["leave_types"].queryset = leave_types
        # Shown only when they apply (base_template/js/dependent.js).
        shown = {"fixed_amount": "amount_method:fixed",
                 "months": "amount_method:basic_months,gross_months",
                 "leave_types": "requires_leave:1", "min_leave_days": "requires_leave:1"}
        for name, rule in shown.items():
            self.fields[name].widget.attrs["data-show-when"] = rule

    @classmethod
    def initial_from(cls, settings):
        data = {name: getattr(settings, name) for name in (
            "name", "description", "amount_method", "fixed_amount", "months",
            "max_amount", "min_service_months", "cycle", "claims_per_cycle", "min_leave_days",
            "payment")}
        for flag in ("enabled", "probation_eligible", "requires_leave", "requires_document",
                     "prorate_first_cycle"):
            data[flag] = "1" if getattr(settings, flag) else "0"
        data["leave_types"] = [t.pk for t in settings.leave_types.all()] if settings.pk else []
        return data


def _month_choices(today=None, before=2, after=10):
    today = today or timezone.localdate()
    first = today.replace(day=1)
    months = []
    for step in range(-before, after + 1):
        month = first.month - 1 + step
        day = datetime.date(first.year + month // 12, month % 12 + 1, 1)
        months.append((day.isoformat(), f"{day:%B %Y}"))
    return months


class LfaClaimForm(StyledFormMixin, forms.Form):
    """A claim: from the employee, or entered for them."""

    employee = forms.ModelChoiceField(queryset=Employee.all_objects.none(), label="Employee")
    leave_request = forms.ChoiceField(required=False, label="The leave it goes with")
    note = forms.CharField(required=False, label="Note", widget=forms.Textarea(attrs={"rows": 2}))
    document = DocumentField(label="Proof", help_text="A ticket or receipt: PDF, JPG, PNG or "
                                                      "WEBP, up to 5 MB.")

    def __init__(self, *args, employees=None, leave_requests=(), needs_leave=False,
                 needs_document=False, **kwargs):
        super().__init__(*args, **kwargs)
        if employees is None:
            del self.fields["employee"]
        else:
            self.fields["employee"].queryset = employees
        self.leave_requests = {str(r.pk): r for r in leave_requests}
        if needs_leave:
            self.fields["leave_request"].required = True
            self.fields["leave_request"].choices = [("", "Choose the leave")] + [
                (str(r.pk), f"{r.lfa_units.normalize():f} day(s) from "
                            f"{min(s.start_date for s in r.segments.all()):%d %b %Y}")
                for r in leave_requests]
        else:
            del self.fields["leave_request"]
        if needs_document:
            self.fields["document"].required = True
        else:
            self.fields["document"].help_text = "Optional. " + self.fields["document"].help_text

    def clean_leave_request(self):
        value = self.cleaned_data.get("leave_request")
        return self.leave_requests.get(value) if value else None


class LfaDecisionForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=(("approve", "Approve"), ("reject", "Reject")),
                                 widget=forms.RadioSelect, initial="approve")
    amount = forms.DecimalField(required=False, min_value=1, max_digits=14, decimal_places=2,
                                label="Amount to pay")
    pay_month = forms.ChoiceField(required=False, label="Paid with the salary of")
    note = forms.CharField(required=False, max_length=255, label="Note",
                           help_text="Needed when rejecting; the employee can read it.")

    def __init__(self, *args, with_salary=True, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["amount"].widget.attrs["data-show-when"] = "decision:approve"
        if with_salary:
            self.fields["pay_month"].choices = _month_choices()
            self.fields["pay_month"].widget.attrs["data-show-when"] = "decision:approve"
        else:
            del self.fields["pay_month"]

    def clean(self):
        values = super().clean()
        if values.get("decision") == "reject" and not values.get("note"):
            self.add_error("note", "Say why it is rejected; they can read it.")
        if values.get("decision") == "approve" and "pay_month" in self.fields:
            if not values.get("pay_month"):
                self.add_error("pay_month", "Choose the salary month.")
            else:
                values["pay_month"] = datetime.date.fromisoformat(values["pay_month"])
        return values


class LfaCancelForm(StyledFormMixin, forms.Form):
    note = forms.CharField(max_length=255, label="Why is it cancelled?")


class LfaPaidForm(StyledFormMixin, forms.Form):
    paid_on = forms.DateField(label="Paid on", widget=forms.DateInput(
        format="%Y-%m-%d", attrs={"type": "date", "data-datepicker": ""}))
    reference = forms.CharField(max_length=100, required=False, label="Reference",
                                help_text="e.g. the cheque or transfer number.")
