"""Leave forms. Convenience only — ``leaves.services`` re-validates everything."""

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin
from employees.models import Employee
from leaves.models import LeaveType, PayType


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
        fields = ("code", "name", "description")
        labels = {"code": "Code", "name": "Leave type"}
        help_texts = {
            "code": "Short identifier, unique in this company, e.g. CL.",
            "name": "As employees know it, e.g. Casual leave.",
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
    pay_type = forms.ChoiceField(
        choices=PayType.choices,
        label="Pay",
        help_text="Unpaid leave days are deducted from salary; paid leave days are not.",
    )
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

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_date"), cleaned.get("end_date")
        if start and end and end < start:
            self.add_error("end_date", "The last day cannot be before the first.")
        return cleaned


def _employee_label(employee):
    return employee.full_name


class CancelLeaveForm(StyledFormMixin, forms.Form):
    reason = forms.CharField(
        label="Why is it being cancelled?", required=False, widget=forms.Textarea,
        help_text="Recorded in the audit trail.",
    )
