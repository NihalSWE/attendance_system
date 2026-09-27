"""Leave forms. Convenience only — ``leaves.services`` re-validates everything."""

import datetime

from django import forms

from common.choices import ActiveStatus
from common.forms import TIME_INPUT_FORMATS, StyledFormMixin, time_widget
from employees.models import Employee
from leaves.documents import DocumentField
from leaves.models import LeaveType, PayType
from leaves.shape import PARTS


def _range_input(key, placeholder, presets=True):
    # Two inputs sharing a data-daterange key become one range picker
    # (datepicker.js); both still post their own value. Report-style quick
    # ranges ("Last 30 days") are switched off where they make no sense.
    attrs = {"type": "date", "data-daterange": key, "data-placeholder": placeholder}
    if not presets:
        attrs["data-presets"] = "none"
    return forms.DateInput(format="%Y-%m-%d", attrs=attrs)


class LeaveTypeForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = LeaveType
        fields = ("code", "name", "days_per_year", "requires_attachment_by_default", "description")
        labels = {"code": "Code", "name": "Leave type", "days_per_year": "Days per year",
                  "requires_attachment_by_default": "Needs a document"}
        help_texts = {
            "requires_attachment_by_default": (
                "Recording or requesting this leave then needs a certificate or letter "
                "attached (PDF or a picture)."),
            "code": "Short identifier, unique in this company, e.g. CL.",
            "name": "As employees know it, e.g. Casual leave.",
            "days_per_year": "Leave blank for no limit. Approved leave in a calendar year counts; a half day is 0.5.",
        }
        widgets = {"description": forms.TextInput()}

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()


class LeaveTypeStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")


class RecordLeaveForm(StyledFormMixin, forms.Form):
    """Record leave that has already been approved: full days, paid or unpaid."""

    # Empty querysets at import time: the default managers are tenant-scoped.
    # The view passes the company's own employees and leave types.
    employee = forms.ModelChoiceField(
        queryset=Employee.all_objects.none(), label="Employee"
    )
    leave_type = forms.ModelChoiceField(
        queryset=LeaveType.all_objects.none(), label="Leave type"
    )
    start_date = forms.DateField(
        label="From", widget=_range_input("leave", "Select leave dates", presets=False)
    )
    end_date = forms.DateField(
        label="To", widget=_range_input("leave", "Select leave dates")
    )
    duration = forms.ChoiceField(
        choices=(("full_day", "Full day"), ("half_day", "Half day"), ("hourly", "Some hours")),
        label="Length", required=False, initial="full_day",
        help_text="A half day or some hours is for one date.",
    )
    half_day_part = forms.ChoiceField(
        choices=PARTS, label="Which half", required=False, widget=forms.RadioSelect,
        help_text="The morning excuses a late arrival, the afternoon an early leaving.",
    )
    start_time = forms.TimeField(label="From (time)", required=False, widget=time_widget(),
                                 input_formats=TIME_INPUT_FORMATS)
    end_time = forms.TimeField(label="Until (time)", required=False, widget=time_widget(),
                               input_formats=TIME_INPUT_FORMATS,
                               help_text="Inside that day's shift.")
    pay_type = forms.ChoiceField(
        choices=PayType.choices,
        label="Pay",
        help_text="Unpaid leave is deducted from salary; paid leave is not; part paid keeps "
                  "the share you give.",
    )
    pay_percentage = forms.DecimalField(
        label="Share of pay kept (%)", required=False, min_value=1, max_value=99,
        decimal_places=2, help_text="For part paid leave, e.g. 50.",
    )
    document = DocumentField()
    reason = forms.CharField(
        label="Reason", required=False, widget=forms.Textarea
    )

    def __init__(self, *args, employees=None, leave_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        if employees is not None:
            self.fields["employee"].queryset = employees
        if leave_types is not None:
            self.fields["leave_type"].queryset = leave_types
        self.fields["employee"].empty_label = "Select an employee"
        self.fields["leave_type"].empty_label = "Select a leave type"
        self.fields["employee"].label_from_instance = _employee_label
        # Shown only when they apply (base_template/js/dependent.js).
        self.fields["half_day_part"].widget.attrs["data-show-when"] = "duration:half_day"
        self.fields["start_time"].widget.attrs["data-show-when"] = "duration:hourly"
        self.fields["end_time"].widget.attrs["data-show-when"] = "duration:hourly"
        self.fields["pay_percentage"].widget.attrs["data-show-when"] = "pay_type:partial"

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The last day cannot be before the first.")
        # A value that does not apply to the choice made is dropped, not used.
        if cleaned.get("duration") != "half_day":
            cleaned["half_day_part"] = ""
        if cleaned.get("duration") != "hourly":
            cleaned["start_time"] = cleaned["end_time"] = None
        if cleaned.get("pay_type") != "partial":
            cleaned["pay_percentage"] = None
        return cleaned


def _employee_label(employee):
    # The view annotates the current employee code, so the picker can find
    # people by code as well as by name.
    code = getattr(employee, "table_code", "")
    return f"{code} · {employee.full_name}" if code else employee.full_name


class CancelLeaveForm(StyledFormMixin, forms.Form):
    """Cancel the whole leave, or some of its days (someone came back early)."""

    what = forms.ChoiceField(
        label="Cancel", initial="all", required=False,
        choices=(("all", "The whole leave"), ("some", "Some days")),
        widget=forms.RadioSelect,
    )
    work_dates = forms.TypedMultipleChoiceField(
        label="Which days", required=False, coerce=lambda value: datetime.date.fromisoformat(value),
        widget=forms.CheckboxSelectMultiple,
        help_text="The days that stop counting as leave. The rest stay.",
    )
    reason = forms.CharField(
        label="Why is it being cancelled?", required=False, widget=forms.Textarea,
        help_text="Recorded in the audit trail. Needed when cancelling some days.",
    )

    def __init__(self, *args, days=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["work_dates"].choices = [
            (day.isoformat(), f"{day:%a %d %b %Y}") for day in days]
        if len(days) < 2:
            # One day left: there is nothing to cancel but all of it.
            del self.fields["what"]
            del self.fields["work_dates"]

    def clean(self):
        data = super().clean()
        if data.get("what") == "some" and not data.get("work_dates"):
            self.add_error("work_dates", "Choose the days to cancel.")
        return data


class AmendLeaveForm(RecordLeaveForm):
    """Change an approved leave: its type, dates, half or full day, or pay."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields["employee"]
        self.fields["reason"].help_text = "Recorded with the change."
        self.fields["document"].help_text = (
            "Replaces the document it has, if you choose one. PDF, JPG, PNG or WEBP, up to 5 MB.")


class RequestLeaveForm(RecordLeaveForm):
    """The service determines the employee from the signed-in account."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        del self.fields['employee']
        self.fields['reason'].required = True
        self.fields['pay_type'].label = 'Requested pay'
        self.fields['pay_type'].help_text = 'Your approver decides whether the leave is paid, unpaid or part paid.'


class DecideLeaveForm(StyledFormMixin, forms.Form):
    decision = forms.ChoiceField(choices=(('approve', 'Approve'), ('reject', 'Reject')))
    pay_type = forms.ChoiceField(choices=PayType.choices, label='Approved pay', required=False)
    pay_percentage = forms.DecimalField(label='Share of pay kept (%)', required=False,
                                        min_value=1, max_value=99, decimal_places=2)
    reason = forms.CharField(label='Decision note', required=False, widget=forms.Textarea,
                             help_text='Required when rejecting. The employee can read this note.')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['pay_type'].widget.attrs['data-show-when'] = 'decision:approve'
        self.fields['pay_percentage'].widget.attrs['data-show-when'] = 'pay_type:partial'

    def clean(self):
        values = super().clean()
        if values.get('decision') == 'reject' and not values.get('reason'):
            self.add_error('reason', 'Give a reason for rejecting the request.')
        if values.get('decision') == 'approve' and not values.get('pay_type'):
            self.add_error('pay_type', 'Choose paid, unpaid or part paid.')
        if (values.get('decision') == 'approve' and values.get('pay_type') == 'partial'
                and values.get('pay_percentage') is None):
            self.add_error('pay_percentage', 'Give the share of pay kept, from 1 to 99 %.')
        return values


# --------------------------------------------------------------------------
# Leave policies (Phase E, 2026-09-27)
# --------------------------------------------------------------------------


class LeavePolicyForm(StyledFormMixin, forms.Form):
    code = forms.CharField(max_length=32, label="Code", help_text="Short, e.g. STAFF.")
    name = forms.CharField(max_length=150, label="Policy",
                           help_text="As people know it, e.g. Staff, Workers.")
    description = forms.CharField(max_length=255, required=False, label="Description")
    is_default = forms.BooleanField(
        required=False, label="The company default",
        help_text="Everyone not given another policy has this one.")


class PolicyVersionForm(StyledFormMixin, forms.Form):
    effective_from = forms.DateField(
        label="From", widget=forms.DateInput(format="%Y-%m-%d", attrs={
            "type": "date", "data-datepicker": "", "data-placeholder": "Select date"}),
        help_text="These rules apply from this day until the next version.")
    note = forms.CharField(max_length=255, required=False, label="Note",
                           help_text="What changed, for the record.")


class PolicyRuleForm(StyledFormMixin, forms.Form):
    """One leave type's rule in a version (the version page has one per type)."""

    include = forms.BooleanField(required=False, label="Covered")
    days_per_year = forms.DecimalField(required=False, min_value=0, max_digits=5,
                                       decimal_places=2, label="Days per year")
    accrual = forms.ChoiceField(choices=(("yearly", "At the start of the year"),
                                         ("monthly", "A twelfth each month")),
                                required=False, label="Given")
    carry_forward_days = forms.DecimalField(required=False, min_value=0, max_digits=5,
                                            decimal_places=2, label="Carry forward up to")
    carry_forward_expires_months = forms.IntegerField(required=False, min_value=1,
                                                      max_value=12,
                                                      label="Carried days expire after (months)")
    allow_half_day = forms.BooleanField(required=False, initial=True, label="Half days")
    allow_hourly = forms.BooleanField(required=False, initial=True, label="By the hour")
    allow_negative = forms.BooleanField(required=False, label="May go below zero")

    def clean(self):
        values = super().clean()
        if values.get("include") and values.get("days_per_year") is None:
            self.add_error("days_per_year", "Give the days per year.")
        if values.get("carry_forward_expires_months") and not values.get("carry_forward_days"):
            self.add_error("carry_forward_expires_months",
                           "Only carried days can expire: give how many are carried.")
        return values


class AssignPolicyForm(StyledFormMixin, forms.Form):
    policy = forms.ModelChoiceField(queryset=None, required=False, label="Leave policy",
                                    help_text="Empty: the company default.")
    effective_from = forms.DateField(
        label="From", widget=forms.DateInput(format="%Y-%m-%d", attrs={
            "type": "date", "data-datepicker": "", "data-placeholder": "Select date"}),
        help_text="Their leave is given by this policy from this day.")

    def __init__(self, *args, policies=None, **kwargs):
        super().__init__(*args, **kwargs)
        from leaves.models import LeavePolicy

        self.fields["policy"].queryset = policies if policies is not None else LeavePolicy.all_objects.none()
        self.fields["policy"].empty_label = "The company default"


class AdjustBalanceForm(StyledFormMixin, forms.Form):
    leave_type = forms.ModelChoiceField(queryset=LeaveType.all_objects.none(), label="Leave type")
    year = forms.IntegerField(min_value=2000, max_value=2100, label="Year")
    units = forms.DecimalField(max_digits=6, decimal_places=2, label="Days",
                               help_text="Days to add; with a minus, e.g. -1.5, to take away.")
    note = forms.CharField(max_length=255, label="Why")

    def __init__(self, *args, leave_types=None, **kwargs):
        super().__init__(*args, **kwargs)
        if leave_types is not None:
            self.fields["leave_type"].queryset = leave_types
