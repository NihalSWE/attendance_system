"""What the leave endpoints take and give."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer
from api.v1.employees.serializers import FileInputSerializer, PersonSerializer

DAYS = {"max_digits": 6, "decimal_places": 2, "coerce_to_string": True}
DURATION_HELP = "full_day, half_day (one date) or hourly (one date, with times)."
PAY_HELP = "paid, unpaid or partial (part paid: give pay_percentage)."


# --- leave types --------------------------------------------------------------------------

class LeaveTypeSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The leave type id.")
    code = serializers.CharField(help_text="Short code, unique in the company, e.g. CL.")
    name = serializers.CharField(help_text="As employees know it, e.g. Casual leave.")
    days_per_year = serializers.DecimalField(allow_null=True, help_text="null: no limit.", **DAYS)
    needs_document = serializers.BooleanField(help_text="Recording or requesting it needs a "
                                                        "certificate or letter.")
    description = serializers.CharField(help_text="May be empty.")
    status = serializers.CharField(help_text="active or inactive (cannot be chosen for new "
                                             "leave).")


class LeaveTypeInputSerializer(StrictSerializer):
    code = serializers.CharField(max_length=20, help_text="Short code, e.g. CL (upper-cased).")
    name = serializers.CharField(max_length=100, help_text="e.g. Casual leave.")
    days_per_year = serializers.DecimalField(max_digits=5, decimal_places=1, required=False,
                                             allow_null=True,
                                             help_text="null or left out: no limit. Whole or half "
                                                       "days, e.g. 10 or 10.5.")
    needs_document = serializers.BooleanField(required=False, default=False,
                                              help_text="Needs a certificate or letter.")
    description = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                        help_text="A description.")


class DefaultsAddedSerializer(serializers.Serializer):
    added = LeaveTypeSerializer(many=True, help_text="The default types added (empty: the "
                                                     "company had them all).")


# --- policies -----------------------------------------------------------------------------

class PolicyRuleSerializer(serializers.Serializer):
    leave_type = RefSerializer(help_text="The leave type.")
    days_per_year = serializers.DecimalField(help_text="Days given a year.", **DAYS)
    accrual = serializers.CharField(help_text="yearly (all at the start of the year) or monthly "
                                              "(a twelfth each month).")
    carry_forward_days = serializers.DecimalField(allow_null=True, help_text="Carried into the "
                                                                             "next year, at most.",
                                                  **DAYS)
    carry_forward_expires_months = serializers.IntegerField(
        allow_null=True, help_text="Carried days expire after this many months.")
    allow_half_day = serializers.BooleanField(help_text="Half days allowed.")
    allow_hourly = serializers.BooleanField(help_text="Leave by the hour allowed.")
    allow_negative = serializers.BooleanField(help_text="The balance may go below zero.")


class PolicyVersionSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The version id.")
    number = serializers.IntegerField(help_text="1, 2, …")
    effective_from = serializers.DateField(help_text="Its rules apply from this day until the "
                                                     "next version.")
    note = serializers.CharField(help_text="What changed (may be empty).")
    started = serializers.BooleanField(help_text="It has started: it can no longer change.")
    rules = PolicyRuleSerializer(many=True, help_text="One rule per leave type it covers.")


class LeavePolicySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The policy id.")
    code = serializers.CharField(help_text="Short code, e.g. STAFF.")
    name = serializers.CharField(help_text="e.g. Staff.")
    description = serializers.CharField(help_text="May be empty.")
    is_default = serializers.BooleanField(help_text="Everyone not given another policy has it.")
    status = serializers.CharField(help_text="active or inactive.")
    people = serializers.IntegerField(help_text="People given it now (the default reaches "
                                                "everyone else too).")
    versions = PolicyVersionSerializer(many=True, help_text="Newest first.")


class PolicyInputSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32, help_text="Short code, e.g. STAFF.")
    name = serializers.CharField(max_length=150, help_text="e.g. Staff.")
    description = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                        help_text="A description.")
    is_default = serializers.BooleanField(required=False, default=False,
                                          help_text="Make it the company default (from today).")


class PolicyRuleInputSerializer(StrictSerializer):
    leave_type_id = serializers.IntegerField(help_text="An active leave type.")
    days_per_year = serializers.DecimalField(max_digits=5, decimal_places=2,
                                             help_text="Days given a year.")
    accrual = serializers.ChoiceField(choices=("yearly", "monthly"), required=False,
                                      default="yearly", help_text="yearly or monthly.")
    carry_forward_days = serializers.DecimalField(max_digits=5, decimal_places=2,
                                                  required=False, allow_null=True,
                                                  help_text="Carried into the next year, at most.")
    carry_forward_expires_months = serializers.IntegerField(
        required=False, allow_null=True, help_text="1-12: carried days expire after that.")
    allow_half_day = serializers.BooleanField(required=False, default=True,
                                              help_text="Half days allowed (default true).")
    allow_hourly = serializers.BooleanField(required=False, default=True,
                                            help_text="By the hour allowed (default true).")
    allow_negative = serializers.BooleanField(required=False, default=False,
                                              help_text="May go below zero.")


class PolicyVersionInputSerializer(StrictSerializer):
    effective_from = serializers.DateField(help_text="From this day (today or later once the "
                                                     "policy has a version).")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="What changed, for the record.")
    rules = PolicyRuleInputSerializer(many=True, allow_empty=False,
                                      help_text="One per leave type it covers.")


# --- balances -----------------------------------------------------------------------------

class BalanceLineSerializer(serializers.Serializer):
    leave_type = RefSerializer(help_text="The leave type.")
    by_policy = serializers.BooleanField(help_text="Given by a policy (else the type's days per "
                                                   "year).")
    policy = serializers.CharField(allow_null=True, help_text="The policy's name.")
    given = serializers.DecimalField(help_text="Days given this year (accrued, carried, "
                                               "adjusted).", **DAYS)
    taken = serializers.DecimalField(help_text="Days taken.", **DAYS)
    left = serializers.DecimalField(help_text="Days left.", **DAYS)


class PersonBalanceSerializer(serializers.Serializer):
    employee = PersonSerializer(help_text="Whose.")
    employee_code = serializers.CharField(allow_null=True, help_text="Their Employee ID.")
    branch = serializers.CharField(allow_null=True, help_text="Where they are placed.")
    year = serializers.IntegerField(help_text="The year.")
    balances = BalanceLineSerializer(many=True, help_text="One line per leave type.")


# --- records ------------------------------------------------------------------------------

class LeaveRecordSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The leave id.")
    employee = PersonSerializer(help_text="Whose leave.")
    leave_type = RefSerializer(help_text="The leave type (its current part).")
    start_date = serializers.DateField(help_text="First day.")
    end_date = serializers.DateField(help_text="Last day.")
    duration = serializers.CharField(help_text=DURATION_HELP)
    half_day_part = serializers.CharField(help_text="morning or afternoon, for a half day.")
    start_time = serializers.CharField(help_text="HH:MM, for hourly (may be empty).")
    end_time = serializers.CharField(help_text="HH:MM, for hourly (may be empty).")
    days = serializers.DecimalField(allow_null=True, help_text="Working days that count.", **DAYS)
    pay_type = serializers.CharField(help_text=PAY_HELP)
    pay_percentage = serializers.DecimalField(max_digits=5, decimal_places=2,
                                              coerce_to_string=True, allow_null=True,
                                              help_text="The share of pay kept, for partial.")
    status = serializers.CharField(help_text="approved, partially_cancelled, cancelled, pending, "
                                             "rejected or withdrawn.")
    reason = serializers.CharField(help_text="May be empty.")
    has_document = serializers.BooleanField(help_text="A document is attached (GET …/document).")
    recorded_by = serializers.CharField(allow_null=True, help_text="Who recorded or requested it "
                                                                   "(email).")
    recorded_at = serializers.DateTimeField(allow_null=True, help_text="When.")


class LeaveRecordDetailSerializer(LeaveRecordSerializer):
    days_counting = serializers.ListField(child=serializers.DateField(),
                                          help_text="The dates that still count as leave.")
    may_change = serializers.BooleanField(help_text="You may change or cancel it.")


class LeaveDocumentInputSerializer(FileInputSerializer):
    pass


class RecordInputSerializer(StrictSerializer):
    employee_id = serializers.IntegerField(help_text="Who. Someone you may record leave for.")
    leave_type_id = serializers.IntegerField(help_text="An active leave type.")
    start_date = serializers.DateField(help_text="First day.")
    end_date = serializers.DateField(help_text="Last day (the same day for half_day or hourly).")
    duration = serializers.ChoiceField(choices=("full_day", "half_day", "hourly"),
                                       required=False, default="full_day", help_text=DURATION_HELP)
    half_day_part = serializers.ChoiceField(choices=("morning", "afternoon"), required=False,
                                            allow_blank=True, help_text="For half_day.")
    start_time = serializers.CharField(required=False, allow_blank=True,
                                       help_text="HH:MM, for hourly.")
    end_time = serializers.CharField(required=False, allow_blank=True,
                                     help_text="HH:MM, for hourly (inside that day's shift).")
    pay_type = serializers.ChoiceField(choices=("paid", "unpaid", "partial"), required=False,
                                       default="paid", help_text=PAY_HELP)
    pay_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, required=False,
                                              allow_null=True,
                                              help_text="1-99, for partial.")
    reason = serializers.CharField(required=False, allow_blank=True, help_text="Why.")
    document = LeaveDocumentInputSerializer(required=False, allow_null=True,
                                       help_text="A certificate or letter: PDF, JPG, PNG or WEBP, "
                                                 "up to 5 MB.")


class AmendInputSerializer(RecordInputSerializer):
    employee_id = None
    leave_type_id = serializers.IntegerField(required=False, help_text="Unchanged when left out.")
    start_date = serializers.DateField(required=False, help_text="Unchanged when left out.")
    end_date = serializers.DateField(required=False, help_text="Unchanged when left out.")
    duration = serializers.ChoiceField(choices=("full_day", "half_day", "hourly"),
                                       required=False, help_text=DURATION_HELP)
    pay_type = serializers.ChoiceField(choices=("paid", "unpaid", "partial"), required=False,
                                       help_text=PAY_HELP)


class CancelInputSerializer(StrictSerializer):
    days = serializers.ListField(child=serializers.DateField(), required=False,
                                 help_text="Only these dates (someone came back early); left "
                                           "out: the whole leave.")
    reason = serializers.CharField(required=False, allow_blank=True,
                                   help_text="Why. Needed when cancelling some days.")


# --- requests (approval) ------------------------------------------------------------------

class AllowanceSerializer(serializers.Serializer):
    left = serializers.DecimalField(help_text="Days left this year.", **DAYS)
    given = serializers.DecimalField(help_text="Days given this year.", **DAYS)


class LeaveRequestSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The request id.")
    employee = PersonSerializer(help_text="Who asked.")
    branch = RefSerializer(allow_null=True, help_text="Where they asked from.")
    leave_type = RefSerializer(help_text="The leave type.")
    start_date = serializers.DateField(help_text="First day.")
    end_date = serializers.DateField(help_text="Last day.")
    duration = serializers.CharField(help_text=DURATION_HELP)
    half_day_part = serializers.CharField(help_text="For a half day.")
    start_time = serializers.CharField(help_text="HH:MM, for hourly.")
    end_time = serializers.CharField(help_text="HH:MM, for hourly.")
    days = serializers.DecimalField(allow_null=True, help_text="Working days asked for.", **DAYS)
    pay_type = serializers.CharField(help_text="The pay asked for: " + PAY_HELP)
    reason = serializers.CharField(help_text="Their reason.")
    status = serializers.CharField(help_text="pending, approved or rejected.")
    has_document = serializers.BooleanField(help_text="GET /leave/records/{id}/document.")
    submitted_at = serializers.DateTimeField(allow_null=True, help_text="When they asked.")
    decided_at = serializers.DateTimeField(allow_null=True, help_text="When it was decided.")
    decision_note = serializers.CharField(help_text="The note given with the decision.")
    allowance = AllowanceSerializer(allow_null=True, help_text="Their allowance of that type "
                                                               "this year; null: no limit.")


class DecideInputSerializer(StrictSerializer):
    decision = serializers.ChoiceField(choices=("approve", "reject"), help_text="approve or reject.")
    pay_type = serializers.ChoiceField(choices=("paid", "unpaid", "partial"), required=False,
                                       allow_blank=True,
                                       help_text="The pay approved (needed to approve).")
    pay_percentage = serializers.DecimalField(max_digits=5, decimal_places=2, required=False,
                                              allow_null=True, help_text="1-99, for partial.")
    note = serializers.CharField(required=False, allow_blank=True,
                                 help_text="Needed when rejecting; the employee reads it.")
