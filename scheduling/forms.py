"""Forms for the working calendar. Convenience only — ``scheduling.services``
re-validates every value and re-checks scope before writing."""

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin
from organization.models import Branch
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule
from scheduling.services import scheduled_minutes_between


def _date_widget(placeholder):
    # data-datepicker swaps in our own calendar (datepicker.js); the native
    # input still holds and posts the value.
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={"type": "date", "data-datepicker": "", "data-placeholder": placeholder},
    )


def _time_field(label, help_text):
    # A plain text box rather than <input type="time">: the browser draws its
    # own clock control for that, which the design does not allow.
    return forms.TimeField(
        label=label,
        input_formats=["%H:%M", "%H.%M"],
        help_text=help_text,
        widget=forms.TextInput(
            attrs={"placeholder": "HH:MM", "maxlength": 5, "inputmode": "numeric"}
        ),
    )


class ShiftForm(StyledFormMixin, forms.ModelForm):
    start_time = _time_field("Starts at", "24-hour time, e.g. 09:00.")
    end_time = _time_field("Ends at", "24-hour time, e.g. 18:00.")

    class Meta:
        model = Shift
        fields = (
            "code",
            "name",
            "start_time",
            "end_time",
            "spans_next_day",
            "grace_in_minutes",
            "minimum_full_day_minutes",
            "minimum_half_day_minutes",
        )
        labels = {
            "code": "Shift code",
            "name": "Shift name",
            "spans_next_day": "Ends on the next day (night shift)",
            "grace_in_minutes": "Late after (minutes)",
            "minimum_full_day_minutes": "Full day needs (minutes)",
            "minimum_half_day_minutes": "Half day needs (minutes)",
        }
        help_texts = {
            "code": "Short identifier, unique in this company.",
            "grace_in_minutes": (
                "Arriving within this many minutes of the start is not late."
            ),
            "minimum_full_day_minutes": (
                "Worked minutes needed for a full present day. Cannot exceed the "
                "shift length."
            ),
            "minimum_half_day_minutes": (
                "Worked minutes needed for a half day. Less than this is absent."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].widget.format = "%H:%M"
        self.fields["end_time"].widget.format = "%H:%M"
        self.fields["spans_next_day"].widget.attrs["class"] = ""

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end:
            # The length is derived, not typed. Set it before model validation
            # runs, because Shift.clean() checks the break against it.
            self.instance.scheduled_minutes = scheduled_minutes_between(
                start, end, cleaned.get("spans_next_day", False)
            )
            if self.instance.scheduled_minutes <= 0:
                self.add_error(
                    "end_time",
                    "End must be after start. Tick 'Ends on the next day' for a "
                    "night shift.",
                )
        return cleaned


class ShiftStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")


class AttendanceSettingsForm(StyledFormMixin, forms.ModelForm):
    # Declared with an empty queryset: the default manager is tenant-scoped and
    # would raise at import time. The view passes the company's own shifts.
    company_shift = forms.ModelChoiceField(
        queryset=Shift.all_objects.none(),
        label="Company shift",
        help_text="Every employee's attendance is measured against this shift.",
    )

    class Meta:
        model = CompanyAttendanceSettings
        fields = ("company_shift", "missing_punch_policy")
        labels = {"missing_punch_policy": "When a punch is missing"}
        help_texts = {
            "missing_punch_policy": (
                "A day with an IN but no OUT. Review required marks it for "
                "checking; Treat as absent counts it as an absent day."
            ),
        }

    def __init__(self, *args, shifts=None, **kwargs):
        super().__init__(*args, **kwargs)
        if shifts is not None:
            self.fields["company_shift"].queryset = shifts
        self.fields["company_shift"].empty_label = "Select a shift"


# Saturday first: the Bangladeshi working week starts on Saturday, so the row
# reads in the order a person thinks about their week.
_WEEK_ORDER = (5, 6, 0, 1, 2, 3, 4)


class WeeklyOffForm(StyledFormMixin, forms.Form):
    """Pick several weekdays at once; one rule is stored per selected day."""

    weekdays = forms.TypedMultipleChoiceField(
        label="Days off",
        coerce=int,
        choices=[(day, WeeklyOffRule.Weekday(day).label) for day in _WEEK_ORDER],
        widget=forms.CheckboxSelectMultiple(attrs={"class": "day-picker__input"}),
        help_text="Select every day that is off each week.",
        error_messages={"required": "Select at least one day."},
    )
    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        required=False,
        label="Branch",
        empty_label="All branches",
        help_text="Leave as All branches for a company-wide weekly off.",
    )
    effective_from = forms.DateField(
        label="Starts from",
        widget=_date_widget("Select start date"),
    )
    is_paid = forms.BooleanField(label="Paid day off", required=False)

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
        # StyledFormMixin gives every widget the text-input class; checkboxes
        # carry their own.
        self.fields["weekdays"].widget.attrs["class"] = "day-picker__input"
        self.fields["is_paid"].widget.attrs["class"] = ""


class EndWeeklyOffForm(StyledFormMixin, forms.Form):
    effective_to = forms.DateField(
        label="Stops from",
        help_text=(
            "The first day it no longer applies. Earlier dates keep it, so past "
            "attendance does not change."
        ),
        widget=_date_widget("Select end date"),
    )


class HolidayForm(StyledFormMixin, forms.ModelForm):
    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        required=False,
        label="Branch",
        empty_label="All branches",
        help_text="Leave as All branches for a company-wide holiday.",
    )

    class Meta:
        model = Holiday
        fields = ("holiday_date", "name", "branch", "is_paid", "description")
        labels = {
            "holiday_date": "Date",
            "name": "Holiday name",
            "is_paid": "Paid holiday",
        }
        widgets = {
            "holiday_date": _date_widget("Select date"),
            "description": forms.TextInput(),
        }

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
        self.fields["is_paid"].widget.attrs["class"] = ""
