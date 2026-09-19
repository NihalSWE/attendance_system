"""Forms for the working calendar. Convenience only — ``scheduling.services``
re-validates every value and re-checks scope before writing."""

import datetime

from django import forms

from common.choices import ActiveStatus
from common.forms import StyledFormMixin
from organization.models import Branch, Department
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule
from scheduling.services import scheduled_minutes_between, spans_next_day


def _date_widget(placeholder):
    # data-datepicker swaps in our own calendar (datepicker.js); the native
    # input still holds and posts the value.
    return forms.DateInput(
        format="%Y-%m-%d",
        attrs={"type": "date", "data-datepicker": "", "data-placeholder": placeholder},
    )


def _time_field(label, help_text):
    # A text box rather than <input type="time">: the browser draws its own
    # clock control for that, which the design does not allow. data-timepicker
    # adds the project time picker (timepicker.js); typing still works.
    return forms.TimeField(
        label=label,
        input_formats=["%H:%M", "%H.%M"],
        help_text=help_text,
        widget=forms.TextInput(
            attrs={
                "placeholder": "HH:MM", "maxlength": 5, "inputmode": "numeric",
                "autocomplete": "off", "data-timepicker": "",
            }
        ),
    )


class ShiftForm(StyledFormMixin, forms.ModelForm):
    start_time = _time_field("Starts at", "24-hour time, e.g. 09:00.")
    end_time = _time_field(
        "Ends at",
        "24-hour time, e.g. 18:00. An end earlier than the start (22:00 → 06:00) "
        "is a night shift that ends the next day.",
    )

    class Meta:
        model = Shift
        # No "ends on the next day" box: the times already say it (Ajay,
        # 2026-09-14). The service derives spans_next_day from them.
        fields = (
            "code",
            "name",
            "start_time",
            "end_time",
            "grace_in_minutes",
            "grace_out_minutes",
            "minimum_full_day_minutes",
            "minimum_half_day_minutes",
            "default_break_minutes",
            "break_is_paid",
            "overtime_after_minutes",
        )
        labels = {
            "code": "Shift code",
            "name": "Shift name",
            "grace_in_minutes": "Late after (minutes)",
            "grace_out_minutes": "Leaving early after (minutes)",
            "minimum_full_day_minutes": "Full day needs (minutes)",
            "minimum_half_day_minutes": "Half day needs (minutes)",
            "default_break_minutes": "Break (minutes)",
            "break_is_paid": "The break is paid",
            "overtime_after_minutes": "Overtime starts after (minutes)",
        }
        help_texts = {
            "code": "Short identifier, unique in this company.",
            "grace_in_minutes": (
                "Arriving within this many minutes of the start is not late."
            ),
            "grace_out_minutes": (
                "Leaving within this many minutes of the end is not leaving early."
            ),
            "default_break_minutes": (
                "The break the shift allows, e.g. 60 for lunch. Unpaid unless ticked below."
            ),
            "overtime_after_minutes": (
                "Minutes after the shift's end before overtime counts, e.g. 30. 0 = straight away."
            ),
            "minimum_full_day_minutes": (
                "Worked minutes needed for a full present day. Cannot exceed the "
                "shift length."
            ),
            "minimum_half_day_minutes": (
                "Worked minutes needed for a half day. Less than this is absent."
            ),
        }

    # Blank means 0 for these: no grace, no break, overtime straight away.
    OPTIONAL_MINUTES = ("grace_out_minutes", "default_break_minutes", "overtime_after_minutes")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start_time"].widget.format = "%H:%M"
        self.fields["end_time"].widget.format = "%H:%M"
        for name in self.OPTIONAL_MINUTES:
            self.fields[name].required = False
        # "The break is paid" only means something once there is a break.
        self.fields["break_is_paid"].widget.attrs["data-show-when"] = "default_break_minutes:>0"

    def _minutes_or_zero(self, name):
        value = self.cleaned_data.get(name)
        return 0 if value in (None, "") else value

    def clean_grace_out_minutes(self):
        return self._minutes_or_zero("grace_out_minutes")

    def clean_default_break_minutes(self):
        return self._minutes_or_zero("default_break_minutes")

    def clean_overtime_after_minutes(self):
        return self._minutes_or_zero("overtime_after_minutes")

    def clean_code(self):
        return (self.cleaned_data.get("code") or "").strip().upper()

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start_time"), cleaned.get("end_time")
        if start and end:
            if start == end:
                self.add_error("end_time", "The shift cannot start and end at the same time.")
                return cleaned
            # Derived, not typed: an end before the start ends the next day, and
            # the length follows. Set both before model validation runs, because
            # Shift.clean() checks the break against the length.
            self.instance.spans_next_day = spans_next_day(start, end)
            self.instance.scheduled_minutes = scheduled_minutes_between(
                start, end, self.instance.spans_next_day
            )
        return cleaned


class ShiftStatusForm(StyledFormMixin, forms.Form):
    status = forms.ChoiceField(choices=ActiveStatus.choices, label="Status")


class AttendanceSettingsForm(StyledFormMixin, forms.ModelForm):
    # Declared with an empty queryset: the default manager is tenant-scoped and
    # would raise at import time. The view passes the company's own shifts.
    company_shift = forms.ModelChoiceField(
        queryset=Shift.all_objects.none(),
        required=False,
        label="Company shift",
        help_text=(
            "One shift for the company: everyone works this shift. Shifts per "
            "department: used for any department that has no shift of its own."
        ),
    )

    class Meta:
        model = CompanyAttendanceSettings
        fields = ("shift_mode", "company_shift", "missing_punch_policy")
        labels = {
            "shift_mode": "How shifts are assigned",
            "missing_punch_policy": "When a punch is missing",
        }
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
        self.fields["company_shift"].empty_label = "No company shift"

    def clean(self):
        cleaned = super().clean()
        if (
            cleaned.get("shift_mode") == CompanyAttendanceSettings.ShiftMode.COMPANY_SINGLE_SHIFT
            and not cleaned.get("company_shift")
        ):
            self.add_error("company_shift", "One shift for the company needs that shift chosen.")
        return cleaned


class DepartmentShiftForm(StyledFormMixin, forms.Form):
    """Give one department its shift from a date."""

    department = forms.ModelChoiceField(
        queryset=Department.all_objects.none(), label="Department"
    )
    shift = forms.ModelChoiceField(queryset=Shift.all_objects.none(), label="Shift")
    effective_from = forms.DateField(
        label="From",
        help_text="Everyone in the department works this shift from this date.",
        widget=_date_widget("Select start date"),
    )

    def __init__(self, *args, departments=None, shifts=None, **kwargs):
        super().__init__(*args, **kwargs)
        if departments is not None:
            self.fields["department"].queryset = departments
        if shifts is not None:
            self.fields["shift"].queryset = shifts
        self.fields["department"].empty_label = "Select a department"
        self.fields["shift"].empty_label = "Select a shift"
        self.fields["department"].label_from_instance = (
            lambda dept: f"{dept.name} ({dept.branch.name})"
        )


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

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
        # The day buttons are visually hidden inputs styled by the day picker,
        # not the shared checkbox.
        self.fields["weekdays"].widget.attrs["class"] = "day-picker__input"


class ChangeWeeklyOffStartForm(StyledFormMixin, forms.Form):
    effective_from = forms.DateField(
        label="Starts from",
        help_text="Attendance is updated for the dates that change. Finalised salary months stay locked.",
        widget=_date_widget("Select start date"),
    )


class EndWeeklyOffForm(StyledFormMixin, forms.Form):
    effective_to = forms.DateField(
        label="Stops from",
        help_text=(
            "The first day it no longer applies. Earlier dates keep it, so past "
            "attendance does not change."
        ),
        widget=_date_widget("Select end date"),
    )


class EmployeeShiftForm(StyledFormMixin, forms.Form):
    """Give one employee their own shift (the employee is fixed by the page)."""

    shift = forms.ModelChoiceField(queryset=Shift.all_objects.none(), label="Shift")
    first_day = forms.DateField(label="From", widget=_date_widget("Select first day"))
    last_day = forms.DateField(
        label="Until (optional)", required=False, widget=_date_widget("No end"),
        help_text="Leave empty to keep it until changed. With a last day it is temporary.",
    )
    reason = forms.CharField(label="Reason", required=False, max_length=255)

    def __init__(self, *args, shifts=None, **kwargs):
        super().__init__(*args, **kwargs)
        if shifts is not None:
            self.fields["shift"].queryset = shifts


class EndEmployeeShiftForm(StyledFormMixin, forms.Form):
    last_day = forms.DateField(
        label="Last day on this shift", widget=_date_widget("Select last day"),
        help_text="From the next day the employee works their department's shift again.",
    )


def posted_holiday_rows(data):
    """The year calendar's selected dates, as posted.

    One row per selected day: parallel ``date`` and ``name`` lists. Read
    leniently so the page can show back exactly what was posted; a date that
    cannot be read keeps ``date=None`` and fails validation.
    """
    rows = []
    for raw, name in zip(data.getlist("date"), data.getlist("name")):
        try:
            day = datetime.date.fromisoformat(raw)
        except ValueError:
            day = None
        rows.append({
            "date": day,
            "iso": raw,
            "label": f"{day:%a, %d %b %Y}" if day else raw,
            "name": name.strip(),
        })
    rows.sort(key=lambda row: row["iso"])
    return rows


class HolidayYearForm(StyledFormMixin, forms.Form):
    """Many holidays at once from the year calendar.

    The branch applies to every selected date; each date carries
    its own name. The dates arrive as rows (see ``posted_holiday_rows``), not
    as a form field, because the calendar adds and removes them.
    """

    branch = forms.ModelChoiceField(
        queryset=Branch.all_objects.none(),
        required=False,
        label="Applies to",
        empty_label="All branches",
    )

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
        self.rows = posted_holiday_rows(self.data) if self.is_bound else []

    def clean(self):
        data = super().clean()
        if any(row["date"] is None for row in self.rows):
            raise forms.ValidationError(
                "A selected date could not be read. Remove it and select it again."
            )
        data["days"] = [(row["date"], row["name"]) for row in self.rows]
        return data


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
        fields = ("holiday_date", "name", "branch", "description")
        labels = {
            "holiday_date": "Date",
            "name": "Holiday name",
        }
        widgets = {
            "holiday_date": _date_widget("Select date"),
            "description": forms.TextInput(),
        }

    def __init__(self, *args, branches=None, **kwargs):
        super().__init__(*args, **kwargs)
        if branches is not None:
            self.fields["branch"].queryset = branches
