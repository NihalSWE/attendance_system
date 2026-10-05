"""What the shifts and calendar endpoints take and give. The panel's forms
do the checking (api/core/forms.py)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer, StatusSerializer  # noqa: F401 (shared)
from scheduling.models import CompanyAttendanceSettings, Holiday, WeeklyOffRule

Settings = CompanyAttendanceSettings
WEEKDAYS = [label.lower() for _value, label in WeeklyOffRule.Weekday.choices]
TIME_HELP = "24-hour time HH:MM, e.g. 09:00."


# --- shifts --------------------------------------------------------------------------

class ShiftSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The shift id.")
    code = serializers.CharField(help_text="Short code, unique in the company (upper case).")
    name = serializers.CharField(help_text="The shift's name.")
    start_time = serializers.TimeField(format="%H:%M", help_text="When it starts, HH:MM.")
    end_time = serializers.TimeField(format="%H:%M", help_text="When it ends, HH:MM.")
    ends_next_day = serializers.BooleanField(
        source="spans_next_day",
        help_text="A night shift: it ends the next day (the end is before the start).")
    scheduled_minutes = serializers.IntegerField(help_text="Its length in minutes.")
    grace_in_minutes = serializers.IntegerField(
        help_text="Arriving within this many minutes of the start is not late.")
    grace_out_minutes = serializers.IntegerField(
        help_text="Leaving within this many minutes of the end is not leaving early.")
    minimum_full_day_minutes = serializers.IntegerField(
        help_text="Worked minutes needed for a full present day.")
    minimum_half_day_minutes = serializers.IntegerField(
        help_text="Worked minutes needed for a half day; less is absent.")
    default_break_minutes = serializers.IntegerField(help_text="The break it allows, in minutes.")
    break_is_paid = serializers.BooleanField(help_text="The break counts as worked time.")
    overtime_after_minutes = serializers.IntegerField(
        help_text="Minutes after its end before overtime counts.")
    status = serializers.CharField(help_text="active or inactive (never deleted).")


class ShiftInputSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32, help_text="Short code, unique in the company.")
    name = serializers.CharField(max_length=255, help_text="The shift's name.")
    start_time = serializers.CharField(max_length=8, help_text=TIME_HELP)
    end_time = serializers.CharField(
        max_length=8, help_text=TIME_HELP + " An end before the start (22:00 → 06:00) is a "
                                            "night shift ending the next day.")
    grace_in_minutes = serializers.IntegerField(min_value=0, required=False,
                                                help_text="Late after this many minutes.")
    grace_out_minutes = serializers.IntegerField(
        min_value=0, required=False, help_text="Leaving early after this many minutes (0).")
    minimum_full_day_minutes = serializers.IntegerField(
        min_value=0, required=False,
        help_text="Worked minutes for a full day; not more than the shift's length.")
    minimum_half_day_minutes = serializers.IntegerField(
        min_value=0, required=False, help_text="Worked minutes for a half day.")
    default_break_minutes = serializers.IntegerField(
        min_value=0, required=False, help_text="The break, e.g. 60 (0).")
    break_is_paid = serializers.BooleanField(required=False,
                                             help_text="The break counts as worked time.")
    overtime_after_minutes = serializers.IntegerField(
        min_value=0, required=False, help_text="Minutes after the end before overtime (0).")


# --- settings and overview ------------------------------------------------------------

class SettingsSerializer(serializers.Serializer):
    shift_mode = serializers.CharField(
        help_text="company_single_shift (one shift for everyone) or department_shifts.")
    company_shift = RefSerializer(
        allow_null=True,
        help_text="The company shift: everyone's (one shift) or the departments' without one.")
    missing_punch_policy = serializers.CharField(
        help_text="A day with an IN but no OUT: review_required or auto_absent.")
    punch_pairing_strategy = serializers.CharField(
        help_text="alternating (every scan counts; out and in is a break) or first_last (the "
                  "first scan is the check-in, the last the check-out).")


class SettingsInputSerializer(StrictSerializer):
    shift_mode = serializers.ChoiceField(choices=Settings.ShiftMode.choices, required=False,
                                         help_text="company_single_shift or department_shifts.")
    company_shift_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="An active shift, or null. One shift for the company needs it.")
    missing_punch_policy = serializers.ChoiceField(
        choices=Settings.MissingPunchPolicy.choices, required=False,
        help_text="review_required or auto_absent.")
    punch_pairing_strategy = serializers.ChoiceField(
        choices=Settings.PairingStrategy.choices, required=False,
        help_text="alternating or first_last. Applies to days worked out from now.")


class DepartmentShiftSerializer(serializers.Serializer):
    department = RefSerializer(help_text="The department.")
    branch = RefSerializer(help_text="Its branch.")
    shift = RefSerializer(allow_null=True,
                          help_text="Its shift on the day asked; null: the company shift applies.")
    since = serializers.DateField(allow_null=True, help_text="Since when it has that shift.")


class OverviewSerializer(serializers.Serializer):
    settings = SettingsSerializer(help_text="The attendance settings.")
    ready = serializers.BooleanField(
        help_text="Every working person has a shift: attendance can be worked out.")
    departments_without_shift = RefSerializer(
        many=True, help_text="Active departments with no shift and no company shift to fall back "
                             "on (shifts per department only).")
    active_shifts = serializers.IntegerField(help_text="How many shifts are active.")
    weekly_offs = serializers.ListField(
        child=serializers.CharField(help_text="A weekday."),
        help_text="The company-wide weekly off days in force today.")
    holidays_this_year = serializers.IntegerField(help_text="Active holidays this year.")
    upcoming_holidays = serializers.ListField(
        child=serializers.DictField(help_text="date and name."),
        help_text="The next five holidays.")


class DepartmentShiftInputSerializer(StrictSerializer):
    department_id = serializers.IntegerField(help_text="An active department.")
    shift_id = serializers.IntegerField(help_text="An active shift.")
    from_date = serializers.DateField(help_text="Everyone in it works this shift from this date; "
                                                "earlier days keep theirs.")


# --- an employee's own shift ------------------------------------------------------------

class EmployeeShiftSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The employee-shift id.")
    shift = RefSerializer(help_text="The shift.")
    kind = serializers.CharField(help_text="employee_override (until changed) or temporary "
                                           "(with a last day).")
    first_day = serializers.DateField(help_text="The first day.")
    last_day = serializers.DateField(allow_null=True, help_text="The last day; null: until changed.")
    reason = serializers.CharField(help_text="Why (may be empty).")
    status = serializers.CharField(help_text="active or ended.")


class EmployeeShiftInputSerializer(StrictSerializer):
    shift_id = serializers.IntegerField(help_text="An active shift.")
    first_day = serializers.DateField(help_text="From this day.")
    last_day = serializers.DateField(required=False, allow_null=True,
                                     help_text="Until this day (temporary); null: until changed.")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                   help_text="Why.")


class EndShiftInputSerializer(StrictSerializer):
    last_day = serializers.DateField(help_text="Their last day on it; the next day they work "
                                               "their department's shift again.")


# --- weekly offs -----------------------------------------------------------------------

class WeeklyOffSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The weekly-off id.")
    weekday = serializers.CharField(help_text="monday … sunday.")
    branch = RefSerializer(allow_null=True, help_text="Its branch; null: every branch.")
    from_date = serializers.DateField(help_text="Off from this date.")
    stops_from = serializers.DateField(allow_null=True,
                                       help_text="The first day it no longer applies; null: still.")
    status = serializers.CharField(help_text="active or ended.")


class WeeklyOffInputSerializer(StrictSerializer):
    weekdays = serializers.ListField(
        child=serializers.ChoiceField(choices=WEEKDAYS, help_text="monday … sunday."),
        allow_empty=False, help_text='The days off each week, e.g. ["friday"].')
    branch_id = serializers.IntegerField(required=False, allow_null=True,
                                         help_text="Only this branch; null: every branch.")
    from_date = serializers.DateField(help_text="Off from this date.")


class WeeklyOffStartSerializer(StrictSerializer):
    from_date = serializers.DateField(
        help_text="The new first date. Attendance is updated for the days that change; "
                  "finalised salary months stay as they are.")


class WeeklyOffEndSerializer(StrictSerializer):
    stops_from = serializers.DateField(
        help_text="The first day it no longer applies; earlier days keep it.")


# --- holidays -------------------------------------------------------------------------

class HolidaySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The holiday id.")
    date = serializers.DateField(source="holiday_date", help_text="The day.")
    name = serializers.CharField(help_text="e.g. Victory Day.")
    branch = RefSerializer(allow_null=True, help_text="Its branch; null: every branch.")
    description = serializers.CharField(help_text="May be empty.")
    status = serializers.CharField(help_text="active or cancelled.")


class HolidayInputSerializer(StrictSerializer):
    date = serializers.DateField(required=False, help_text="The day (needed when adding).")
    name = serializers.CharField(max_length=255, required=False,
                                 help_text="e.g. Victory Day (needed when adding).")
    branch_id = serializers.IntegerField(required=False, allow_null=True,
                                         help_text="Only this branch; null: every branch.")
    description = serializers.CharField(required=False, allow_blank=True, help_text="A note.")


class YearDaySerializer(StrictSerializer):
    date = serializers.DateField(help_text="The day.")
    name = serializers.CharField(max_length=255, help_text="Its holiday name.")


class HolidayYearSerializer(StrictSerializer):
    branch_id = serializers.IntegerField(required=False, allow_null=True,
                                         help_text="Only this branch; null: every branch.")
    days = YearDaySerializer(many=True, help_text="The holidays to add, each with its name. All "
                                                  "or nothing: a date already a holiday refuses "
                                                  "the lot and is named.")


