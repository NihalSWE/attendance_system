"""What the employee's own ("me") endpoints take and give."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.attendance.serializers import CalendarDaySerializer
from api.v1.company.serializers import RefSerializer
from api.v1.employees.serializers import EducationSerializer, PersonalInputSerializer
from api.v1.leave.serializers import BalanceLineSerializer
from api.v1.payroll.serializers import MONEY

MOMENT_HELP = "In company time: YYYY-MM-DDTHH:MM."


class NowStatusSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="in, on_break, left, not_in_yet, absent, on_leave, "
                                          "off_today or no_shift.")
    label = serializers.CharField(help_text="The same in words.")
    since = serializers.CharField(help_text="HH:MM since when (may be empty).")
    detail = serializers.CharField(help_text="More, in words (may be empty).")


class MyPlacementSerializer(serializers.Serializer):
    employee_code = serializers.CharField(help_text="Their Employee ID.")
    branch = RefSerializer(help_text="Where they work.")
    department = RefSerializer(allow_null=True, help_text="Their department.")
    designation = RefSerializer(allow_null=True, help_text="Their designation.")
    line_manager = RefSerializer(allow_null=True, help_text="Who they report to.")
    since = serializers.DateTimeField(help_text="Placed since.")


class MyEmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Their employee id.")
    name = serializers.CharField(help_text="Full name.")
    work_email = serializers.CharField(help_text="May be empty.")
    phone = serializers.CharField(help_text="May be empty.")
    joining_date = serializers.DateField(allow_null=True, help_text="When they joined.")
    status = serializers.CharField(help_text="active, probation, …")
    has_photo = serializers.BooleanField(help_text="GET /me/photo.")


class MyHomeSerializer(serializers.Serializer):
    company = RefSerializer(help_text="The company.")
    role = serializers.CharField(help_text="Their login's role: employee, manager, …")
    employee = MyEmployeeSerializer(allow_null=True, help_text="Their record; null for a login "
                                                               "without one.")
    placement = MyPlacementSerializer(allow_null=True, help_text="Where they work now.")
    shift_today = serializers.CharField(allow_null=True, help_text="Today's shift and times.")
    now = NowStatusSerializer(allow_null=True, help_text="Their status right now.")
    month_summary = serializers.DictField(allow_null=True,
                                          help_text="This month so far: present, late, absent "
                                                    "… and worked.")
    managed_branches = RefSerializer(many=True, help_text="A branch manager's branches.")
    leave_waiting = serializers.IntegerField(help_text="Leave requests waiting for them to "
                                                       "decide (GET /leave/requests).")


class MyProfileSerializer(serializers.Serializer):
    employee = MyEmployeeSerializer(help_text="Their record.")
    placement = MyPlacementSerializer(allow_null=True, help_text="Where they work now.")
    details = serializers.DictField(help_text="What they may change themselves (PATCH "
                                              "/me/details).")
    education = EducationSerializer(many=True, help_text="Their education history.")


class MyDetailsInputSerializer(PersonalInputSerializer):
    confirmation_date = None
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                  help_text="Their phone.")


class MyCalendarSerializer(serializers.Serializer):
    year = serializers.IntegerField(help_text="The year.")
    month = serializers.IntegerField(help_text="The month.")
    days = CalendarDaySerializer(many=True, help_text="Every day of the month.")
    summary = serializers.DictField(help_text="The month's totals.")


class MyMissedScanInputSerializer(StrictSerializer):
    kind = serializers.ChoiceField(choices=("check_in", "check_out", "both", "whole_day"),
                                   required=False, default="check_in",
                                   help_text="What is missing.")
    at = serializers.CharField(required=False, allow_blank=True,
                               help_text=MOMENT_HELP + " The scan (the check-in when both). "
                                                       "After midnight on a night shift, the "
                                                       "next date.")
    at_out = serializers.CharField(required=False, allow_blank=True,
                                   help_text=MOMENT_HELP + " The check-out, for both.")
    work_date = serializers.DateField(required=False, allow_null=True,
                                      help_text="The missing day, for whole_day.")
    reason = serializers.CharField(help_text="What happened; whoever approves it reads this.")


class MyLeaveSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The leave id.")
    leave_type = RefSerializer(help_text="The leave type.")
    start_date = serializers.DateField(help_text="First day.")
    end_date = serializers.DateField(help_text="Last day.")
    duration = serializers.CharField(help_text="full_day, half_day or hourly.")
    half_day_part = serializers.CharField(help_text="morning or afternoon.")
    start_time = serializers.CharField(help_text="HH:MM, for hourly.")
    end_time = serializers.CharField(help_text="HH:MM, for hourly.")
    days = serializers.DecimalField(allow_null=True, help_text="Working days.", **MONEY)
    pay_type = serializers.CharField(help_text="paid, unpaid or partial (as asked, or as "
                                               "approved).")
    status = serializers.CharField(help_text="pending, approved, rejected, withdrawn, "
                                             "cancelled or partially_cancelled.")
    reason = serializers.CharField(help_text="Their reason.")
    decision_note = serializers.CharField(help_text="The approver's note.")
    has_document = serializers.BooleanField(help_text="GET /me/leave/{id}/document.")
    submitted_at = serializers.DateTimeField(allow_null=True, help_text="When.")
    decided_at = serializers.DateTimeField(allow_null=True, help_text="When decided.")
    may_withdraw = serializers.BooleanField(help_text="Still waiting: they may withdraw it.")


class LeaveTakenSerializer(serializers.Serializer):
    leave_type = serializers.CharField(help_text="The leave type.")
    pay_type = serializers.CharField(help_text="paid, unpaid or partial.")
    days = serializers.DecimalField(help_text="Days taken this year.", **MONEY)


class MyLeaveYearSerializer(serializers.Serializer):
    year = serializers.IntegerField(help_text="The year.")
    leave_types = RefSerializer(many=True, help_text="The types they may ask for.")
    balances = BalanceLineSerializer(many=True, help_text="Given, taken and left of each "
                                                          "type.")
    taken = LeaveTakenSerializer(many=True, help_text="Days taken this year, by type and pay.")


class MyLeaveDocumentSerializer(StrictSerializer):
    filename = serializers.CharField(max_length=200, help_text="e.g. certificate.pdf.")
    content_base64 = serializers.CharField(help_text="The file, base64-encoded.")


class MyLeaveInputSerializer(StrictSerializer):
    leave_type_id = serializers.IntegerField(help_text="An active leave type.")
    start_date = serializers.DateField(help_text="First day.")
    end_date = serializers.DateField(help_text="Last day (the same for half_day or hourly).")
    duration = serializers.ChoiceField(choices=("full_day", "half_day", "hourly"),
                                       required=False, default="full_day",
                                       help_text="full_day, half_day or hourly.")
    half_day_part = serializers.ChoiceField(choices=("morning", "afternoon"), required=False,
                                            allow_blank=True, help_text="For half_day.")
    start_time = serializers.CharField(required=False, allow_blank=True,
                                       help_text="HH:MM, for hourly.")
    end_time = serializers.CharField(required=False, allow_blank=True,
                                     help_text="HH:MM, for hourly.")
    pay_type = serializers.ChoiceField(choices=("paid", "unpaid", "partial"), required=False,
                                       default="paid",
                                       help_text="The pay asked for; the approver decides.")
    pay_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, required=False,
                                              allow_null=True, help_text="1-99, for partial.")
    reason = serializers.CharField(help_text="Why (needed).")
    document = MyLeaveDocumentSerializer(required=False, allow_null=True,
                                         help_text="A certificate or letter, if the type needs "
                                                   "one: PDF, JPG, PNG or WEBP, up to 5 MB.")


class BranchDaySerializer(serializers.Serializer):
    employee = RefSerializer(help_text="Who.")
    employee_code = serializers.CharField(help_text="Their Employee ID.")
    branch = RefSerializer(help_text="Where.")
    status = serializers.CharField(allow_null=True, help_text="The day's status; null: nothing "
                                                              "yet.")
    check_in = serializers.DateTimeField(allow_null=True, help_text="First scan in.")
    check_out = serializers.DateTimeField(allow_null=True, help_text="Check-out.")
    late_minutes = serializers.IntegerField(help_text="Minutes late.")
    now = NowStatusSerializer(allow_null=True, help_text="Right now (today only).")


class MyLfaSettingsSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(help_text="LFA is on.")
    name = serializers.CharField(help_text="What it is called.")
    description = serializers.CharField(help_text="The company's note.")
    requires_leave = serializers.BooleanField(help_text="Leave must be taken with it.")
    requires_document = serializers.BooleanField(help_text="Proof must be attached.")
    payment = serializers.CharField(help_text="with_salary or separately.")


class MyLfaSerializer(serializers.Serializer):
    settings = MyLfaSettingsSerializer(help_text="The company's rules, in short.")
    eligible = serializers.BooleanField(help_text="They may claim now.")
    reasons = serializers.ListField(child=serializers.CharField(), help_text="Why not.")
    amount = serializers.DecimalField(allow_null=True, help_text="What it pays; null: the "
                                                                 "approver decides.", **MONEY)
    currency = serializers.CharField(help_text="e.g. BDT.")
    how = serializers.CharField(help_text="How the amount is worked out.")
    leave = serializers.ListField(child=serializers.DictField(),
                                  help_text="Leave it may go with: {id, start_date, days}.")


class MyLfaClaimSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The claim id.")
    cycle_start = serializers.DateField(help_text="The cycle it is for.")
    cycle_end = serializers.DateField(help_text="Its last day.")
    note = serializers.CharField(help_text="Their note.")
    has_document = serializers.BooleanField(help_text="Proof attached.")
    calculated_amount = serializers.DecimalField(allow_null=True, help_text="By the rules.",
                                                 **MONEY)
    approved_amount = serializers.DecimalField(allow_null=True, help_text="Approved.", **MONEY)
    currency = serializers.CharField(help_text="e.g. BDT.")
    status = serializers.CharField(help_text="pending, approved, paid, rejected, withdrawn or "
                                             "cancelled.")
    pay_month = serializers.CharField(allow_null=True, help_text="Paid with this month's "
                                                                 "salary.")
    paid_on = serializers.DateField(allow_null=True, help_text="When it was paid.")
    decision_note = serializers.CharField(help_text="The approver's note.")


class MyLfaClaimInputSerializer(StrictSerializer):
    leave_id = serializers.IntegerField(required=False, allow_null=True,
                                        help_text="The leave it goes with, when needed (from "
                                                  "GET /me/lfa).")
    note = serializers.CharField(required=False, allow_blank=True, help_text="A note.")
    document = MyLeaveDocumentSerializer(required=False, allow_null=True,
                                         help_text="Proof, when needed.")


class MyPayslipRowSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The payslip id.")
    period = serializers.CharField(help_text="e.g. September 2026.")
    period_start = serializers.DateField(help_text="First day.")
    currency = serializers.CharField(help_text="e.g. BDT.")
    gross = serializers.DecimalField(help_text="Gross pay.", **MONEY)
    deductions = serializers.DecimalField(help_text="Deductions.", **MONEY)
    net = serializers.DecimalField(help_text="Net pay.", **MONEY)
