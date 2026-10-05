"""What the salary settings, components, penalty rules and LFA endpoints take
and give (phase 9, part b)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer
from api.v1.employees.serializers import FileInputSerializer, PersonSerializer
from api.v1.payroll.serializers import MONEY

MONTH_HELP = "A month: YYYY-MM."


# --- salary settings ----------------------------------------------------------------------

class SalaryRulesSerializer(serializers.Serializer):
    monthly_proration_method = serializers.CharField(
        help_text="One day's pay for absence: fixed_30 (salary / monthly_divisor), "
                  "calendar_days or scheduled_workdays.")
    monthly_divisor = serializers.DecimalField(max_digits=6, decimal_places=2,
                                               coerce_to_string=True,
                                               help_text="Days, for fixed_30 (standard 30).")
    absence_deduction_method = serializers.CharField(
        help_text="day_fraction, scheduled_minutes (also late and early) or rule_only.")
    half_day_pay_percent = serializers.DecimalField(max_digits=6, decimal_places=2,
                                                    coerce_to_string=True,
                                                    help_text="A half day pays this % of a day.")
    incomplete_day_treatment = serializers.CharField(help_text="A day without check-out: "
                                                               "pay_full, pay_half or unpaid.")
    daily_paid_days_off = serializers.BooleanField(help_text="Daily staff paid for holidays and "
                                                             "weekly offs.")
    hourly_paid_days_off = serializers.BooleanField(help_text="Hourly staff paid their shift "
                                                              "hours on them.")
    money_rounding_increment = serializers.CharField(help_text="Round net salary to: 0.01, 1, 5 "
                                                               "or 10.")
    money_rounding_mode = serializers.CharField(help_text="half_up, up or down.")
    allow_negative_net_pay = serializers.BooleanField(help_text="Net salary may go below zero.")
    maximum_period_deduction_percent = serializers.DecimalField(
        max_digits=6, decimal_places=2, coerce_to_string=True, allow_null=True,
        help_text="Penalties take at most this % of a month's pay; null: no limit.")
    overtime_multiplier = serializers.DecimalField(max_digits=6, decimal_places=2,
                                                   coerce_to_string=True,
                                                   help_text="Overtime pays x the hourly rate.")
    holiday_overtime_multiplier = serializers.DecimalField(
        max_digits=6, decimal_places=2, coerce_to_string=True,
        help_text="Work on a day off pays x the hourly rate.")
    minimum_overtime_minutes = serializers.IntegerField(help_text="Less a day pays none.")
    overtime_rounding_minutes = serializers.IntegerField(help_text="Paid in blocks of 0, 15, 30 "
                                                                   "or 60 minutes.")


class RulesVersionSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The version id.")
    number = serializers.IntegerField(help_text="Its number.")
    effective_from = serializers.DateField(help_text="From.")
    last_day = serializers.DateField(allow_null=True, help_text="To; null: open.")
    state = serializers.CharField(help_text="in_use, upcoming, ended or replaced.")


class SalarySettingsSerializer(serializers.Serializer):
    currency = serializers.CharField(help_text="Three letters, e.g. BDT.")
    pay_day = serializers.IntegerField(allow_null=True, help_text="Day of the month salary is "
                                                                  "usually paid.")
    rules = SalaryRulesSerializer(help_text="The rules in force today.")
    rules_from = serializers.DateField(allow_null=True, help_text="Since when; null: the "
                                                                  "standard rules.")
    summary = serializers.ListField(child=serializers.ListField(child=serializers.CharField()),
                                    help_text="The rules in plain words: [what, how].")
    versions = RulesVersionSerializer(many=True, help_text="Every version, newest first.")


class GeneralSettingsInputSerializer(StrictSerializer):
    currency = serializers.CharField(required=False, help_text="Three letters, e.g. BDT.")
    pay_day = serializers.IntegerField(required=False, allow_null=True,
                                       help_text="1-31, or null.")


def _optional(field, **kwargs):
    return field(required=False, **kwargs)


class SalaryRulesInputSerializer(StrictSerializer):
    applies_from = serializers.CharField(help_text=MONTH_HELP + " The rules apply from its 1st. "
                                                                "Fields left out keep their "
                                                                "current value.")
    monthly_proration_method = _optional(serializers.CharField,
                                         help_text="fixed_30, calendar_days or "
                                                   "scheduled_workdays.")
    monthly_divisor = _optional(serializers.DecimalField, max_digits=6, decimal_places=2,
                                help_text="1-31.")
    absence_deduction_method = _optional(serializers.CharField,
                                         help_text="day_fraction, scheduled_minutes or "
                                                   "rule_only.")
    half_day_pay_percent = _optional(serializers.DecimalField, max_digits=6, decimal_places=2,
                                     help_text="0-100.")
    incomplete_day_treatment = _optional(serializers.CharField,
                                         help_text="pay_full, pay_half or unpaid.")
    daily_paid_days_off = _optional(serializers.BooleanField, help_text="true or false.")
    hourly_paid_days_off = _optional(serializers.BooleanField, help_text="true or false.")
    money_rounding_increment = _optional(serializers.CharField, help_text="0.01, 1, 5 or 10.")
    money_rounding_mode = _optional(serializers.CharField, help_text="half_up, up or down.")
    allow_negative_net_pay = _optional(serializers.BooleanField, help_text="true or false.")
    maximum_period_deduction_percent = _optional(serializers.DecimalField, max_digits=6,
                                                 decimal_places=2, allow_null=True,
                                                 help_text="0.01-100, or null for no limit.")
    overtime_multiplier = _optional(serializers.DecimalField, max_digits=6, decimal_places=2,
                                    help_text="1-10.")
    holiday_overtime_multiplier = _optional(serializers.DecimalField, max_digits=6,
                                            decimal_places=2, help_text="1-10.")
    minimum_overtime_minutes = _optional(serializers.IntegerField, help_text="0-1440.")
    overtime_rounding_minutes = _optional(serializers.IntegerField, help_text="0, 15, 30 or 60.")


# --- components ---------------------------------------------------------------------------

class ComponentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The component id.")
    code = serializers.CharField(help_text="Short and unchanging, e.g. HOUSE_RENT.")
    name = serializers.CharField(help_text="As on the payslip.")
    kind = serializers.CharField(help_text="earning (an allowance) or deduction.")
    method = serializers.CharField(help_text="fixed or percent_of_basic.")
    default_amount = serializers.DecimalField(allow_null=True, help_text="For fixed.", **MONEY)
    default_percent = serializers.DecimalField(max_digits=7, decimal_places=3,
                                               coerce_to_string=True, allow_null=True,
                                               help_text="For percent_of_basic.")
    description = serializers.CharField(help_text="May be empty.")
    status = serializers.CharField(help_text="active or inactive (no longer offered).")


class ComponentInputSerializer(StrictSerializer):
    code = serializers.CharField(max_length=40, help_text="e.g. HOUSE_RENT.")
    name = serializers.CharField(max_length=120, help_text="e.g. House rent.")
    kind = serializers.ChoiceField(choices=("earning", "deduction"),
                                   help_text="earning or deduction.")
    method = serializers.ChoiceField(choices=("fixed", "percent_of_basic"),
                                     help_text="fixed or percent_of_basic.")
    default_amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False,
                                              allow_null=True, help_text="For fixed.")
    default_percent = serializers.DecimalField(max_digits=7, decimal_places=3, required=False,
                                               allow_null=True,
                                               help_text="For percent_of_basic, e.g. 40.")
    description = serializers.CharField(required=False, allow_blank=True,
                                        help_text="A description.")


class ComponentStatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=("active", "inactive"),
                                     help_text="active or inactive.")


class EmployeeComponentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="This person's row id (to end it).")
    component = RefSerializer(help_text="The allowance or deduction.")
    kind = serializers.CharField(help_text="earning or deduction.")
    amount = serializers.DecimalField(allow_null=True, help_text="For fixed.", **MONEY)
    percent = serializers.DecimalField(max_digits=7, decimal_places=3, coerce_to_string=True,
                                       allow_null=True, help_text="For percent_of_basic.")
    effective_from = serializers.DateField(help_text="From.")
    effective_to = serializers.DateField(allow_null=True, help_text="Last day; null: open.")
    reason = serializers.CharField(help_text="The note given.")


class GiveComponentInputSerializer(StrictSerializer):
    component_id = serializers.IntegerField(help_text="An active component.")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False,
                                      allow_null=True, help_text="Left out: its default.")
    percent = serializers.DecimalField(max_digits=7, decimal_places=3, required=False,
                                       allow_null=True, help_text="Left out: its default.")
    effective_from = serializers.DateField(help_text="From this day. A change to one they have "
                                                     "ends the old amount the day before.")
    reason = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                   help_text="A note.")


class EndComponentInputSerializer(StrictSerializer):
    last_day = serializers.DateField(help_text="The last day it applies.")


# --- penalty rules ------------------------------------------------------------------------

class PenaltyRuleSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The rule (version) id.")
    code = serializers.CharField(help_text="The rule's code, the same across its versions.")
    name = serializers.CharField(help_text="As on payslips.")
    metric = serializers.CharField(help_text="late_minutes, early_out_minutes, worked_shortfall "
                                             "or absence.")
    operator = serializers.CharField(help_text="gte, gt, lte, lt or equal.")
    threshold_minutes = serializers.IntegerField(allow_null=True, help_text="Minutes.")
    occurrence_mode = serializers.CharField(help_text="single_day, within_period or "
                                                      "consecutive_workdays.")
    required_occurrences = serializers.IntegerField(help_text="How many days.")
    deduction_method = serializers.CharField(help_text="actual_minutes, fixed_minutes, "
                                                       "day_fraction, full_day or fixed_amount.")
    deduction_value = serializers.DecimalField(max_digits=18, decimal_places=4,
                                               coerce_to_string=True,
                                               help_text="Minutes, days or money.")
    exclusive_group = serializers.CharField(help_text="Rules in one group do not add up on a "
                                                      "day.")
    maximum_deduction = serializers.DecimalField(allow_null=True, help_text="At most a month.",
                                                 **MONEY)
    effective_from = serializers.DateField(help_text="From.")
    last_day = serializers.DateField(allow_null=True, help_text="To; null: open.")
    state = serializers.CharField(help_text="in_use or upcoming.")
    when = serializers.CharField(help_text="When it applies, in words.")
    deducts = serializers.CharField(help_text="What it deducts, in words.")
    may_change = serializers.BooleanField(help_text="No later version is saved: it can be "
                                                    "changed or stopped.")


class PenaltyRuleInputSerializer(StrictSerializer):
    name = serializers.CharField(max_length=120, help_text="e.g. Late more than 10 minutes.")
    metric = serializers.CharField(help_text="late_minutes, early_out_minutes, worked_shortfall "
                                             "or absence.")
    operator = serializers.CharField(required=False, default="gte",
                                     help_text="gte (default), gt, lte, lt or equal.")
    threshold_minutes = serializers.IntegerField(required=False, allow_null=True,
                                                 help_text="Minutes; not for absence.")
    occurrence_mode = serializers.CharField(required=False, default="single_day",
                                            help_text="single_day (default), within_period or "
                                                      "consecutive_workdays.")
    required_occurrences = serializers.IntegerField(required=False, default=1,
                                                    help_text="How many days (default 1).")
    deduction_method = serializers.CharField(required=False, default="day_fraction",
                                             help_text="day_fraction (default), actual_minutes, "
                                                       "fixed_minutes, full_day or "
                                                       "fixed_amount.")
    deduction_value = serializers.DecimalField(max_digits=18, decimal_places=4, required=False,
                                               allow_null=True,
                                               help_text="Minutes, days (0.5) or money.")
    exclusive_group = serializers.CharField(max_length=40, required=False, allow_blank=True,
                                            help_text="Optional group.")
    maximum_deduction = serializers.DecimalField(max_digits=14, decimal_places=2,
                                                 required=False, allow_null=True,
                                                 help_text="At most a month; optional.")
    applies_from = serializers.CharField(help_text=MONTH_HELP)


class StopRuleInputSerializer(StrictSerializer):
    stops_from = serializers.CharField(help_text=MONTH_HELP + " It no longer applies from its "
                                                              "1st.")


# --- LFA ----------------------------------------------------------------------------------

class LfaSettingsSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(help_text="On: employees can claim it.")
    name = serializers.CharField(help_text="As on the payslip.")
    description = serializers.CharField(help_text="Note for employees.")
    amount_method = serializers.CharField(help_text="fixed, basic_months or gross_months.")
    fixed_amount = serializers.DecimalField(allow_null=True, help_text="For fixed.", **MONEY)
    months = serializers.DecimalField(max_digits=4, decimal_places=2, coerce_to_string=True,
                                      allow_null=True, help_text="For basic/gross months.")
    max_amount = serializers.DecimalField(allow_null=True, help_text="At most a claim.",
                                          **MONEY)
    min_service_months = serializers.IntegerField(help_text="Months of service needed.")
    probation_eligible = serializers.BooleanField(help_text="People on probation can claim.")
    cycle = serializers.CharField(help_text="calendar_year or service_year.")
    claims_per_cycle = serializers.IntegerField(help_text="Claims a cycle.")
    requires_leave = serializers.BooleanField(help_text="Leave must be taken with it.")
    leave_type_ids = serializers.ListField(child=serializers.IntegerField(),
                                           help_text="Which leave counts; empty: any.")
    min_leave_days = serializers.DecimalField(max_digits=5, decimal_places=2,
                                              coerce_to_string=True, allow_null=True,
                                              help_text="At least this many days of leave.")
    requires_document = serializers.BooleanField(help_text="Proof must be attached.")
    prorate_first_cycle = serializers.BooleanField(help_text="Less in the first year.")
    payment = serializers.CharField(help_text="with_salary or separately.")


class LfaSettingsInputSerializer(StrictSerializer):
    enabled = _optional(serializers.BooleanField, help_text="On or off.")
    name = _optional(serializers.CharField, help_text="As on the payslip.")
    description = _optional(serializers.CharField, allow_blank=True, help_text="For employees.")
    amount_method = _optional(serializers.CharField, help_text="fixed, basic_months or "
                                                               "gross_months.")
    fixed_amount = _optional(serializers.DecimalField, max_digits=14, decimal_places=2,
                             allow_null=True, help_text="For fixed.")
    months = _optional(serializers.DecimalField, max_digits=4, decimal_places=2,
                       allow_null=True, help_text="0.25-24, for basic/gross months.")
    max_amount = _optional(serializers.DecimalField, max_digits=14, decimal_places=2,
                           allow_null=True, help_text="At most a claim.")
    min_service_months = _optional(serializers.IntegerField, help_text="0-600.")
    probation_eligible = _optional(serializers.BooleanField, help_text="true or false.")
    cycle = _optional(serializers.CharField, help_text="calendar_year or service_year.")
    claims_per_cycle = _optional(serializers.IntegerField, help_text="1-12.")
    requires_leave = _optional(serializers.BooleanField, help_text="true or false.")
    leave_type_ids = _optional(serializers.ListField, child=serializers.IntegerField(),
                               help_text="Active leave types; empty: any.")
    min_leave_days = _optional(serializers.DecimalField, max_digits=5, decimal_places=2,
                               allow_null=True, help_text="At least, from 0.5.")
    requires_document = _optional(serializers.BooleanField, help_text="true or false.")
    prorate_first_cycle = _optional(serializers.BooleanField, help_text="true or false.")
    payment = _optional(serializers.CharField, help_text="with_salary or separately.")


class LfaLeaveSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The leave id (send it as leave_id).")
    start_date = serializers.DateField(help_text="Its first day.")
    days = serializers.DecimalField(max_digits=6, decimal_places=2, coerce_to_string=True,
                                    help_text="Days of leave.")


class LfaEligibilitySerializer(serializers.Serializer):
    eligible = serializers.BooleanField(help_text="They may claim now.")
    reasons = serializers.ListField(child=serializers.CharField(), help_text="Why not.")
    cycle_start = serializers.DateField(allow_null=True, help_text="This cycle's first day.")
    cycle_end = serializers.DateField(allow_null=True, help_text="Its last day.")
    used = serializers.IntegerField(help_text="Claims made this cycle.")
    amount = serializers.DecimalField(allow_null=True, help_text="What it pays; null: the "
                                                                 "approver decides.", **MONEY)
    currency = serializers.CharField(help_text="e.g. BDT.")
    how = serializers.CharField(help_text="How the amount is worked out.")
    leave = LfaLeaveSerializer(many=True, help_text="Leave it may go with.")


class LfaClaimSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The claim id.")
    employee = PersonSerializer(help_text="Whose.")
    cycle_start = serializers.DateField(help_text="The cycle it is for.")
    cycle_end = serializers.DateField(help_text="Its last day.")
    leave_id = serializers.IntegerField(allow_null=True, help_text="The leave it goes with.")
    note = serializers.CharField(help_text="Their note.")
    has_document = serializers.BooleanField(help_text="Proof attached (GET …/document).")
    calculated_amount = serializers.DecimalField(allow_null=True, help_text="By the rules.",
                                                 **MONEY)
    approved_amount = serializers.DecimalField(allow_null=True, help_text="Approved.", **MONEY)
    currency = serializers.CharField(help_text="e.g. BDT.")
    status = serializers.CharField(help_text="pending, approved, paid, rejected, withdrawn or "
                                             "cancelled.")
    payment = serializers.CharField(help_text="with_salary or separately.")
    pay_month = serializers.CharField(allow_null=True, help_text="Paid with this month's "
                                                                 "salary.")
    paid_on = serializers.DateField(allow_null=True, help_text="When it was paid.")
    payment_reference = serializers.CharField(help_text="e.g. a cheque number.")
    decided_by = serializers.CharField(allow_null=True, help_text="Who decided (email).")
    decided_at = serializers.DateTimeField(allow_null=True, help_text="When.")
    decision_note = serializers.CharField(help_text="The note given.")
    may_decide = serializers.BooleanField(help_text="You may decide it.")


class LfaDocumentInputSerializer(FileInputSerializer):
    pass


class LfaClaimInputSerializer(StrictSerializer):
    employee_id = serializers.IntegerField(help_text="Whose claim (someone you decide for).")
    leave_id = serializers.IntegerField(required=False, allow_null=True,
                                        help_text="The leave it goes with, when leave is "
                                                  "required (see eligibility).")
    note = serializers.CharField(required=False, allow_blank=True, help_text="A note.")
    document = LfaDocumentInputSerializer(required=False, allow_null=True,
                                          help_text="Proof: PDF, JPG, PNG or WEBP, up to 5 MB.")


class LfaDecideInputSerializer(StrictSerializer):
    decision = serializers.ChoiceField(choices=("approve", "reject"),
                                       help_text="approve or reject.")
    amount = serializers.DecimalField(max_digits=14, decimal_places=2, required=False,
                                      allow_null=True, help_text="Left out: as calculated.")
    pay_month = serializers.CharField(required=False, allow_blank=True,
                                      help_text=MONTH_HELP + " For with_salary: paid with that "
                                                             "month's salary.")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="Needed when rejecting.")


class LfaCancelInputSerializer(StrictSerializer):
    note = serializers.CharField(max_length=255, help_text="Why it is cancelled.")


class LfaPaidInputSerializer(StrictSerializer):
    paid_on = serializers.DateField(help_text="When it was paid.")
    reference = serializers.CharField(max_length=100, required=False, allow_blank=True,
                                      help_text="e.g. the cheque or transfer number.")
