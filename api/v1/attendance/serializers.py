"""What the attendance endpoints take and give."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer

STATUS_HELP = ("present, half_day, absent, leave, holiday, weekly_off, incomplete (no check-out "
               "yet) or inactive.")
MOMENT_HELP = "In company time: YYYY-MM-DDTHH:MM (or with seconds)."


class DaySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The day's id.")
    date = serializers.DateField(help_text="The working day.")
    employee = RefSerializer(help_text="Whose day.")
    employee_code = serializers.CharField(allow_null=True, help_text="Their Employee ID that day.")
    branch = RefSerializer(allow_null=True, help_text="Where they worked.")
    status = serializers.CharField(help_text=STATUS_HELP)
    check_in = serializers.DateTimeField(allow_null=True, help_text="Their first scan in.")
    check_out = serializers.DateTimeField(allow_null=True, help_text="Their check-out.")
    worked_minutes = serializers.IntegerField(help_text="Worked (paid) minutes.")
    in_office_minutes = serializers.IntegerField(allow_null=True,
                                                 help_text="Minutes in the office.")
    late_minutes = serializers.IntegerField(help_text="Minutes late, after the grace.")
    early_out_minutes = serializers.IntegerField(help_text="Minutes left early.")
    overtime_minutes = serializers.IntegerField(help_text="Overtime worked (before approval).")
    approved_overtime_minutes = serializers.IntegerField(help_text="Overtime approved.")
    payable_fraction = serializers.DecimalField(max_digits=4, decimal_places=2,
                                                coerce_to_string=True,
                                                help_text="How much of the day is paid: 1, 0.5, 0.")
    needs_review = serializers.BooleanField(help_text="It closed without a clear answer.")
    note = serializers.CharField(help_text="Why it is so (may be empty).")


class ScanLineSerializer(serializers.Serializer):
    time = serializers.CharField(help_text="HH:MM:SS, company time.")
    label = serializers.CharField(help_text="What the scan was taken as: Check-in, Break-out, …")
    device = serializers.CharField(help_text="Where it came from (or Added by hand).")
    added_by_hand = serializers.BooleanField(help_text="A scan added by a correction.")
    counted = serializers.BooleanField(help_text="It counts for the day.")
    note = serializers.CharField(help_text="How it was read (may be empty).")


class CorrectionSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The correction id.")
    type = serializers.CharField(help_text="add_scan, change_status, accept_review or "
                                           "excuse_late.")
    scan_at = serializers.DateTimeField(allow_null=True, help_text="The scan added (add_scan).")
    new_status = serializers.CharField(help_text="The status set (change_status; may be empty).")
    reason = serializers.CharField(help_text="Why.")
    status = serializers.CharField(help_text="applied, superseded or withdrawn.")
    by = serializers.CharField(allow_null=True, help_text="Who made it (email).")
    at = serializers.DateTimeField(help_text="When.")


class DayDetailSerializer(serializers.Serializer):
    day = DaySerializer(allow_null=True, help_text="The day; null when nothing is recorded yet.")
    shift = serializers.CharField(help_text="The shift and its times (may be empty).")
    scans = ScanLineSerializer(many=True, help_text="Every scan of the day, in order.")
    corrections = CorrectionSerializer(many=True, help_text="The fixes made to it.")
    may_fix = serializers.BooleanField(help_text="You may fix this day.")
    locked = serializers.BooleanField(help_text="In a finalised salary month: it cannot change.")


class CalendarDaySerializer(serializers.Serializer):
    date = serializers.DateField(help_text="The date.")
    status = serializers.CharField(help_text=STATUS_HELP + " Empty: nothing recorded.")
    label = serializers.CharField(help_text="The status in words (e.g. Leave (paid)).")
    late = serializers.BooleanField(help_text="They came in late.")
    check_in = serializers.CharField(help_text="HH:MM (may be empty).")
    check_out = serializers.CharField(help_text="HH:MM (may be empty).")
    worked = serializers.CharField(help_text="Worked time, e.g. 8h 05m (may be empty).")
    note = serializers.CharField(help_text="May be empty.")


class CalendarSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="Whose month.")
    year = serializers.IntegerField(help_text="The year.")
    month = serializers.IntegerField(help_text="The month, 1-12.")
    days = CalendarDaySerializer(many=True, help_text="Every day of the month.")
    summary = serializers.DictField(help_text="Counts: present, late, absent, leave, holiday, "
                                              "weekly_off, incomplete, half_day, inactive; "
                                              "worked and in_office as hours.")


class NowSerializer(serializers.Serializer):
    employee_id = serializers.IntegerField(help_text="The employee.")
    key = serializers.CharField(help_text="in, on_break, left, not_in_yet, absent, on_leave, "
                                          "off_today or no_shift.")
    label = serializers.CharField(help_text="The same in words.")
    tone = serializers.CharField(help_text="How the panel colours it.")
    since = serializers.CharField(help_text="HH:MM since when (may be empty).")
    detail = serializers.CharField(help_text="More, in words (may be empty).")


class ReviewSerializer(serializers.Serializer):
    day = DaySerializer(help_text="The day.")
    reason = serializers.CharField(help_text="Why it needs a person.")
    department = serializers.CharField(help_text="Their department (may be empty).")


class AddScanSerializer(StrictSerializer):
    at = serializers.CharField(help_text=MOMENT_HELP + " After midnight on a night shift, the "
                                                       "next date.")
    reason = serializers.CharField(help_text="Why - kept with the change.")


class ChangeStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=("present", "half_day", "absent"),
                                     help_text="present, half_day or absent.")
    reason = serializers.CharField(help_text="Why - kept with the change.")


class ReasonSerializer(StrictSerializer):
    reason = serializers.CharField(help_text="Why - kept with the change.")


class WithdrawSerializer(StrictSerializer):
    note = serializers.CharField(required=False, allow_blank=True,
                                 help_text="Why it is withdrawn (optional).")


class MissedScanSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The request id.")
    employee = RefSerializer(help_text="Whose.")
    branch = RefSerializer(help_text="The branch it was filed in.")
    kind = serializers.CharField(help_text="check_in, check_out, both, whole_day (or scan).")
    date = serializers.DateField(help_text="The day it is for.")
    scan_at = serializers.DateTimeField(help_text="The scan (the check-in when both).")
    scan_out_at = serializers.DateTimeField(allow_null=True, help_text="The check-out, when both.")
    reason = serializers.CharField(help_text="What happened, in their words.")
    status = serializers.CharField(help_text="pending, approved, rejected or withdrawn.")
    submitted_at = serializers.DateTimeField(help_text="When it was sent.")
    decided_by = serializers.CharField(allow_null=True, help_text="Who decided (email).")
    decided_at = serializers.DateTimeField(allow_null=True, help_text="When.")
    decision_note = serializers.CharField(help_text="The note given with the decision.")


class DecideSerializer(StrictSerializer):
    decision = serializers.ChoiceField(choices=("approve", "reject"),
                                       help_text="approve (the scan is added) or reject.")
    note = serializers.CharField(required=False, allow_blank=True,
                                 help_text="Needed when rejecting; the employee can read it.")


class EnterMissingSerializer(StrictSerializer):
    kind = serializers.ChoiceField(choices=("check_in", "check_out", "both", "whole_day"),
                                   help_text="What is missing.")
    at = serializers.CharField(required=False, allow_blank=True,
                               help_text=MOMENT_HELP + " The scan (the check-in when both); not "
                                                       "for whole_day.")
    at_out = serializers.CharField(required=False, allow_blank=True,
                                   help_text=MOMENT_HELP + " The check-out, for both.")
    work_date = serializers.DateField(required=False, allow_null=True,
                                      help_text="The missing day, for whole_day.")
    reason = serializers.CharField(help_text="What happened.")
    approve_now = serializers.BooleanField(
        required=False, default=False,
        help_text="The owner or company administrator may approve what they enter at once.")
