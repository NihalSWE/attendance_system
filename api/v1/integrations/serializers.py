"""What the ERP webhook and audit log endpoints take and give."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer


class WebhookCountsSerializer(serializers.Serializer):
    pending = serializers.IntegerField(help_text="Waiting to be sent.")
    sent = serializers.IntegerField(help_text="Received.")
    failed = serializers.IntegerField(help_text="Not received - will try again.")
    skipped = serializers.IntegerField(help_text="Refused by the receiver.")
    gave_up = serializers.IntegerField(help_text="Given up.")


class WebhookSerializer(serializers.Serializer):
    configured = serializers.BooleanField(help_text="Settings have been saved.")
    url = serializers.CharField(help_text="The ERP's webhook address (https).")
    is_active = serializers.BooleanField(help_text="Attendance is being sent.")
    send_breaks = serializers.BooleanField(help_text="The scans in between are sent too.")
    ping_url = serializers.CharField(help_text="The test address (may be empty: url + /ping).")
    employee_key = serializers.CharField(help_text="The employee field name in each event.")
    mode = serializers.CharField(help_text="arrive_leave or every_scan.")
    batch = serializers.BooleanField(help_text="Up to 500 events a request.")
    send_from = serializers.DateField(allow_null=True, help_text="Earlier days are never sent.")
    has_secret = serializers.BooleanField(help_text="A secret key is saved. It is never shown.")
    has_signing_secret = serializers.BooleanField(help_text="A signing secret is saved.")
    last_tested_at = serializers.DateTimeField(allow_null=True, help_text="Last test.")
    last_test_ok = serializers.BooleanField(allow_null=True, help_text="Whether it passed.")
    last_test_message = serializers.CharField(help_text="What it said.")
    last_sent_at = serializers.DateTimeField(allow_null=True, help_text="Last delivery.")
    counts = WebhookCountsSerializer(help_text="Events by status.")


class WebhookInputSerializer(StrictSerializer):
    url = serializers.CharField(required=False, help_text="https://… - the ERP's webhook.")
    is_active = serializers.BooleanField(required=False, help_text="Send attendance.")
    send_breaks = serializers.BooleanField(required=False, help_text="Send breaks too.")
    ping_url = serializers.CharField(required=False, allow_blank=True,
                                     help_text="A test address, if the ERP has one.")
    employee_key = serializers.CharField(required=False, help_text="e.g. au_user_id.")
    mode = serializers.ChoiceField(choices=("arrive_leave", "every_scan"), required=False,
                                   help_text="arrive_leave (default) or every_scan.")
    batch = serializers.BooleanField(required=False, help_text="Several in one request.")
    send_from = serializers.DateField(required=False, allow_null=True,
                                      help_text="Send days from; null: from switching on.")
    secret = serializers.CharField(required=False, help_text="A new secret key the ERP "
                                                             "checks. Never shown back.")
    signing_secret = serializers.CharField(required=False, help_text="A new signing secret.")
    clear_signing_secret = serializers.BooleanField(required=False,
                                                    help_text="Stop signing requests.")


class NewSecretSerializer(serializers.Serializer):
    secret = serializers.CharField(help_text="The new secret key - shown this once. Give it to "
                                             "the ERP's developer.")


class WebhookTestSerializer(serializers.Serializer):
    ok = serializers.BooleanField(help_text="It worked.")
    title = serializers.CharField(help_text="What happened.")
    detail = serializers.CharField(help_text="More.")
    fix = serializers.CharField(help_text="What to do about it.")
    url = serializers.CharField(help_text="The address called.")


class TestEventInputSerializer(StrictSerializer):
    employee_code = serializers.CharField(max_length=64,
                                          help_text="An Employee ID the ERP also knows.")
    work_date = serializers.DateField(help_text="The day.")
    check_in = serializers.CharField(help_text="HH:MM.")
    check_out = serializers.CharField(required=False, allow_blank=True,
                                      help_text="HH:MM, after the check-in (optional).")


class WebhookEventSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The event id (as sent).")
    employee = RefSerializer(help_text="Whose.")
    work_date = serializers.DateField(help_text="The day.")
    kind = serializers.CharField(help_text="check_in, check_out, update, break_out, break_in "
                                           "or test.")
    status = serializers.CharField(help_text="pending, sent, failed, skipped or gave_up.")
    attempts = serializers.IntegerField(help_text="Tries so far.")
    next_attempt_at = serializers.DateTimeField(allow_null=True, help_text="Next try.")
    last_attempt_at = serializers.DateTimeField(allow_null=True, help_text="Last try.")
    last_status_code = serializers.IntegerField(allow_null=True, help_text="The ERP's answer.")
    last_message = serializers.CharField(help_text="What happened, in words.")
    sent_at = serializers.DateTimeField(allow_null=True, help_text="When it was received.")
    created_at = serializers.DateTimeField(help_text="When it was made.")
    payload = serializers.DictField(help_text="What is (or was) sent.")


class WebhookSentSerializer(serializers.Serializer):
    count = serializers.IntegerField(help_text="Events received, or queued again.")
    detail = serializers.CharField(help_text="What happened, in words.")


class DebugEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The entry id.")
    at = serializers.CharField(help_text="When, company time.")
    what = serializers.CharField(help_text="What was done.")
    ok = serializers.BooleanField(allow_null=True, help_text="Whether it worked.")
    message = serializers.CharField(help_text="What happened.")
    detail = serializers.JSONField(allow_null=True, help_text="The request and answer (secret "
                                                              "keys hidden).")


class DebugSerializer(serializers.Serializer):
    active = serializers.BooleanField(help_text="Debug messages are being kept.")
    seconds_left = serializers.IntegerField(help_text="Until it switches itself off.")
    entries = DebugEntrySerializer(many=True, help_text="Newest first.")


class DebugInputSerializer(StrictSerializer):
    on = serializers.BooleanField(help_text="true: keep debug messages for 15 minutes; false: "
                                            "stop.")


class AuditEntrySerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The entry id.")
    at = serializers.DateTimeField(help_text="When.")
    actor = serializers.CharField(allow_null=True, help_text="Who (email); null: the system.")
    actor_type = serializers.CharField(help_text="user, system, …")
    action = serializers.CharField(help_text="e.g. leave.approved.")
    object_type = serializers.CharField(help_text="What kind of thing, e.g. leaves.leaverequest.")
    object_id = serializers.CharField(help_text="Its id.")
    object = serializers.CharField(help_text="It, in words.")
    before = serializers.JSONField(allow_null=True, help_text="What it was.")
    after = serializers.JSONField(allow_null=True, help_text="What it became.")
    ip_address = serializers.CharField(allow_null=True, help_text="From where.")
