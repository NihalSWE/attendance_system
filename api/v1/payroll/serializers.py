"""What the salary endpoints take and give."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer
from api.v1.employees.serializers import PersonSerializer

MONEY = {"max_digits": 18, "decimal_places": 2, "coerce_to_string": True}
RUN_STATUS_HELP = "draft, submitted (waiting for approval) or posted (finalised)."


# --- salary by month ----------------------------------------------------------------------

class PayrollTotalsSerializer(serializers.Serializer):
    employees = serializers.IntegerField(help_text="Payslips you see.")
    gross = serializers.DecimalField(allow_null=True, help_text="Their gross pay.", **MONEY)
    deductions = serializers.DecimalField(allow_null=True, help_text="Their deductions.", **MONEY)
    net = serializers.DecimalField(allow_null=True, help_text="Their net pay.", **MONEY)


class PayrollMonthSerializer(serializers.Serializer):
    year = serializers.IntegerField(help_text="The year.")
    month = serializers.IntegerField(help_text="The month, 1-12.")
    status = serializers.CharField(allow_null=True, help_text=RUN_STATUS_HELP + " null: not "
                                                                               "generated yet.")
    generated_at = serializers.DateTimeField(allow_null=True, help_text="When it was last "
                                                                        "generated.")
    submitted_by = serializers.CharField(allow_null=True, help_text="Who submitted it (email).")
    submitted_at = serializers.DateTimeField(allow_null=True, help_text="When.")
    finalised_by = serializers.CharField(allow_null=True, help_text="Who approved it (email).")
    finalised_at = serializers.DateTimeField(allow_null=True, help_text="When.")
    sent_back_reason = serializers.CharField(allow_null=True,
                                             help_text="Why it was last sent back.")
    skipped = serializers.ListField(child=serializers.CharField(),
                                    help_text="People skipped: no salary set.")
    totals = PayrollTotalsSerializer(help_text="Over the payslips you see.")
    may_generate = serializers.BooleanField(help_text="You may generate it (a draft, or none "
                                                      "yet).")
    may_submit = serializers.BooleanField(help_text="You may submit it for approval.")
    approval_blocked = serializers.CharField(allow_null=True, help_text="Why you may not approve "
                                                                        "it; null: you may.")
    attendance_changed = serializers.IntegerField(help_text="Days fixed since it was generated "
                                                            "(generate again before approving).")
    overtime_decided_since = serializers.IntegerField(help_text="Overtime decided since the "
                                                                "draft was generated.")
    overtime_waiting = serializers.IntegerField(help_text="Overtime days waiting for a decision.")


class PayslipRowSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The payslip id.")
    employee = PersonSerializer(help_text="Whose.")
    employee_code = serializers.CharField(allow_null=True, help_text="Their Employee ID.")
    branch = RefSerializer(allow_null=True, help_text="Where they were placed at month end.")
    pay_basis = serializers.CharField(help_text="monthly, daily or hourly.")
    base_rate = serializers.DecimalField(allow_null=True, help_text="Per month, day or hour.",
                                         **MONEY)
    currency = serializers.CharField(help_text="e.g. BDT.")
    gross = serializers.DecimalField(help_text="Gross pay.", **MONEY)
    deductions = serializers.DecimalField(help_text="Deductions.", **MONEY)
    net = serializers.DecimalField(help_text="Net pay.", **MONEY)
    counts = serializers.DictField(help_text="present, half_day, absent, paid_leave, "
                                             "unpaid_leave, holiday, weekly_off, late_minutes, "
                                             "overtime_minutes, worked_minutes …")


class MonthReasonSerializer(StrictSerializer):
    reason = serializers.CharField(help_text="Why - kept in the audit log.")


class GeneratedSerializer(serializers.Serializer):
    month = PayrollMonthSerializer(help_text="The month after generating.")
    generated = serializers.IntegerField(help_text="Payslips made (yours, for a branch login).")
    skipped = serializers.ListField(child=serializers.CharField(),
                                    help_text="Skipped this time: no salary set.")


# --- payslips -----------------------------------------------------------------------------

class PayslipLineSerializer(serializers.Serializer):
    type = serializers.CharField(help_text="earning or deduction.")
    source = serializers.CharField(help_text="basic, component, overtime, absence, penalty, "
                                             "adjustment …")
    code = serializers.CharField(help_text="Its code.")
    description = serializers.CharField(help_text="As printed on the payslip.")
    quantity = serializers.DecimalField(max_digits=18, decimal_places=4, coerce_to_string=True,
                                        allow_null=True, help_text="Days, hours … (may be null).")
    rate = serializers.DecimalField(max_digits=18, decimal_places=4, coerce_to_string=True,
                                    allow_null=True, help_text="Per unit (may be null).")
    amount = serializers.DecimalField(help_text="The amount.", **MONEY)
    added_by_hand = serializers.BooleanField(help_text="A bonus or deduction line added by hand.")


class PenaltySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The penalty id (to waive it).")
    rule = serializers.CharField(help_text="The penalty rule.")
    period_start = serializers.DateField(help_text="From.")
    period_end = serializers.DateField(help_text="To.")
    occurrences = serializers.IntegerField(help_text="How many times it happened.")
    amount = serializers.DecimalField(help_text="Deducted.", **MONEY)
    status = serializers.CharField(help_text="applied or waived.")


class AdjustmentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The line id (to remove it).")
    type = serializers.CharField(help_text="earning (bonus) or deduction.")
    amount = serializers.DecimalField(help_text="The amount.", **MONEY)
    reason = serializers.CharField(help_text="Shown on the payslip.")
    paid_in = serializers.CharField(allow_null=True, help_text="For a correction: the month it "
                                                               "is paid with.")
    status = serializers.CharField(help_text="active or cancelled.")


class PayslipSerializer(PayslipRowSerializer):
    period = serializers.CharField(help_text="The month, e.g. October 2026.")
    period_start = serializers.DateField(help_text="First day.")
    period_end = serializers.DateField(help_text="Last day.")
    status = serializers.CharField(help_text="The month's: " + RUN_STATUS_HELP)
    department = serializers.CharField(allow_null=True, help_text="Their department.")
    designation = serializers.CharField(allow_null=True, help_text="Their designation.")
    earnings = PayslipLineSerializer(many=True, help_text="Earning lines.")
    deduction_lines = PayslipLineSerializer(many=True, help_text="Deduction lines.")
    penalties = PenaltySerializer(many=True, help_text="Penalties this month.")
    adjustments = AdjustmentSerializer(many=True, help_text="Bonus and deduction lines added by "
                                                            "hand for this month.")
    corrections = AdjustmentSerializer(many=True, help_text="For a finalised month: corrections "
                                                            "paid in a later month.")
    may_adjust = serializers.BooleanField(help_text="You may add or remove lines (a draft).")
    may_correct = serializers.BooleanField(help_text="You may add a correction (finalised).")
    may_waive = serializers.BooleanField(help_text="You may waive its penalties.")
    may_email = serializers.BooleanField(help_text="You may email it.")
    email_blocked = serializers.CharField(allow_null=True, help_text="Why it cannot be emailed "
                                                                     "now; null: it can.")


class PayslipLineInputSerializer(StrictSerializer):
    type = serializers.ChoiceField(choices=("earning", "deduction"),
                                   help_text="earning (a bonus) or deduction.")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2,
                                      help_text="Above zero.")
    reason = serializers.CharField(max_length=255, help_text="Shown on the payslip, e.g. Eid "
                                                             "bonus.")


class PayslipCorrectionSerializer(serializers.Serializer):
    correction = AdjustmentSerializer(help_text="The correction.")
    payslip = PayslipSerializer(help_text="The finalised payslip, unchanged.")


class EmailInputSerializer(StrictSerializer):
    to = serializers.EmailField(required=False, help_text="Left out: the employee's own "
                                                          "address.")
    subject = serializers.CharField(max_length=200, required=False,
                                    help_text="Left out: the standard subject.")
    body = serializers.CharField(max_length=5000, required=False,
                                 help_text="Left out: the standard message.")
    from_name = serializers.CharField(max_length=120, required=False, allow_blank=True,
                                      help_text="The sender name the employee sees.")


class EmailedSerializer(serializers.Serializer):
    sent_to = serializers.EmailField(help_text="The address it went to.")


# --- overtime -----------------------------------------------------------------------------

class OvertimeDaySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The attendance day's id (to decide it).")
    date = serializers.DateField(help_text="The day.")
    employee = PersonSerializer(help_text="Whose.")
    branch = RefSerializer(allow_null=True, help_text="Where.")
    day_off = serializers.BooleanField(help_text="Worked on a holiday or weekly off.")
    minutes = serializers.IntegerField(help_text="Overtime minutes attendance counted.")
    open_from = serializers.DateTimeField(allow_null=True, help_text="Nobody scanned out of the "
                                                                     "session started then: "
                                                                     "decide with check_out.")
    state = serializers.CharField(help_text="automatic (paid as counted), waiting, approved, "
                                            "rejected or too_short.")
    approved_minutes = serializers.IntegerField(help_text="Minutes approved.")
    paid_minutes = serializers.IntegerField(help_text="Minutes the salary rules pay.")
    decided_by = serializers.CharField(allow_null=True, help_text="Who decided (email).")
    note = serializers.CharField(help_text="The decision's note (may be empty).")
    changed_since = serializers.BooleanField(help_text="A scan arrived after the decision and "
                                                       "changed the minutes.")
    may_decide = serializers.BooleanField(help_text="You may decide it.")


class OvertimeDecideInputSerializer(StrictSerializer):
    decision = serializers.ChoiceField(choices=("approve", "reject"), help_text="approve or "
                                                                               "reject.")
    minutes = serializers.IntegerField(required=False, allow_null=True,
                                       help_text="Approve this many (1 up to what was counted); "
                                                 "left out: all.")
    check_out = serializers.CharField(required=False, allow_blank=True,
                                      help_text="HH:MM they left, for a day nobody scanned out "
                                                "(open_from set).")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="Kept with the decision.")
