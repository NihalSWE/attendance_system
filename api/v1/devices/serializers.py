"""What the device setup endpoints take and give. The panel's forms do the
checking (api/core/forms.py)."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer
from common.choices import DeviceAttendanceScope
from devices.forms import TIMEZONE_CHOICES
from devices.models import BiometricDevice, DeviceEnrollment

MOMENT_HELP = "In company time: YYYY-MM-DD, or YYYY-MM-DDTHH:MM for a time too (midnight if left out)."


class ConnectionSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="connected, late, not_connected, retired or suspended.")
    label = serializers.CharField(help_text="The same in words, e.g. Connected.")
    tone = serializers.CharField(help_text="How the panel colours it: success, warning, danger or "
                                           "neutral.")
    detail = serializers.CharField(help_text="e.g. Checked in 12 seconds ago (may be empty).")
    last_seen_at = serializers.DateTimeField(allow_null=True, help_text="Its last check-in.")


class DeviceRefSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The device id (a UUID).")
    name = serializers.CharField(help_text="The device's name.")


class DeviceConnectionSerializer(serializers.Serializer):
    device = DeviceRefSerializer(help_text="The device.")
    connection = ConnectionSerializer(help_text="Is it calling in.")


class DeviceModelSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The model id.")
    name = serializers.CharField(help_text="e.g. SenseFace 2A.")
    vendor = serializers.CharField(help_text="e.g. ZKTeco.")


class DeviceSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The device id (a UUID).")
    name = serializers.CharField(help_text="The device's name.")
    serial_number = serializers.CharField(help_text="Exactly as printed on the device.")
    branch = RefSerializer(help_text="Its branch.")
    device_model = DeviceModelSerializer(allow_null=True, help_text="Its model.")
    external_device_id = serializers.CharField(help_text="A vendor or cloud id (may be empty).")
    timezone = serializers.CharField(help_text="How its local punch times are read.")
    status = serializers.CharField(help_text="pending, active, offline, suspended or retired. "
                                             "Only pending, active and offline may send data.")
    installed_at = serializers.DateTimeField(allow_null=True, help_text="When installed.")
    push_interval_seconds = serializers.IntegerField(help_text="How often it calls in when idle.")
    error_delay_seconds = serializers.IntegerField(help_text="How long it waits after a failure.")
    realtime = serializers.BooleanField(help_text="It pushes punches at once.")
    has_comm_key = serializers.BooleanField(
        help_text="A communication key is set: the device must send it (a ZKTeco push device "
                  "cannot - leave none).")
    connection = ConnectionSerializer(help_text="Is it calling in.")


class RegisteredSerializer(DeviceSerializer):
    comm_key = serializers.CharField(
        allow_null=True, help_text="The communication key just set - shown only now; null "
                                   "without one.")


class DeviceInputSerializer(StrictSerializer):
    branch_id = serializers.IntegerField(required=False, help_text="Its branch (needed when "
                                                                   "registering).")
    name = serializers.CharField(max_length=255, required=False,
                                 help_text="The device's name (needed when registering).")
    device_model_id = serializers.IntegerField(required=False, allow_null=True,
                                               help_text="Its model (GET /api/v1/device-models).")
    serial_number = serializers.CharField(
        max_length=100, required=False,
        help_text="Exactly as printed on the device (needed when registering). One device "
                  "sends to one company only.")
    external_device_id = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                               help_text="A vendor or cloud id.")
    timezone = serializers.ChoiceField(choices=[c for c, _ in TIMEZONE_CHOICES], required=False,
                                       help_text="e.g. Asia/Dhaka (needed when registering).")
    installed_at = serializers.CharField(required=False, allow_blank=True, help_text=MOMENT_HELP)
    status = serializers.ChoiceField(choices=BiometricDevice.Status.choices, required=False,
                                     help_text="pending (new), active, offline, suspended. "
                                               "Retire with POST …/retire.")
    push_interval_seconds = serializers.IntegerField(required=False,
                                                     help_text="1 to 3600 (10).")
    error_delay_seconds = serializers.IntegerField(required=False, help_text="1 to 3600 (30).")
    realtime = serializers.BooleanField(required=False, help_text="Push punches at once (true).")
    comm_key = serializers.CharField(
        max_length=100, required=False, allow_blank=True,
        help_text="Only for a device that sends a key with every request; a ZKTeco push device "
                  "cannot - leave it out. Shown once in the answer.")
    remove_comm_key = serializers.BooleanField(required=False,
                                               help_text="true: remove the key it has.")


class SerialCheckSerializer(serializers.Serializer):
    ok = serializers.BooleanField(help_text="The serial is free.")
    message = serializers.CharField(help_text="Why not, in the words saving would use (may be "
                                              "empty).")


class SetupLineSerializer(serializers.Serializer):
    setting = serializers.CharField(help_text="The terminal's menu entry.")
    value = serializers.CharField(help_text="What to enter there.")
    note = serializers.CharField(help_text="A hint (may be empty).")


class SetupSerializer(serializers.Serializer):
    lines = SetupLineSerializer(many=True, help_text="What to type on the terminal, in order.")
    endpoint_url = serializers.CharField(help_text="The address the device calls, for testing.")
    host_unreachable = serializers.BooleanField(
        help_text="This server is reached at an address a device cannot use (e.g. localhost).")


class AdviceSerializer(serializers.Serializer):
    title = serializers.CharField(help_text="What to check.")
    text = serializers.CharField(help_text="How.")
    values = serializers.ListField(child=serializers.ListField(
        child=serializers.CharField(help_text="A setting or its value."),
        help_text="[setting, value]"), help_text="Values to compare on the terminal.")


class TestSerializer(serializers.Serializer):
    connection = ConnectionSerializer(help_text="The connection now.")
    started_at = serializers.DateTimeField(help_text="When the test started.")
    checked_in = serializers.BooleanField(help_text="It called in since then: it works.")
    checked_in_at = serializers.DateTimeField(allow_null=True, help_text="When it did.")
    waited_seconds = serializers.IntegerField(help_text="Seconds since the test started.")
    gave_up = serializers.BooleanField(help_text="Waited long enough: see advice.")
    command = serializers.DictField(help_text="The test command: queued, picked_up or answered.")
    advice = AdviceSerializer(many=True, help_text="What to check, once it gave up.")


class TestStartedSerializer(serializers.Serializer):
    started_at = serializers.DateTimeField(help_text="Send it as since when asking how it went.")
    command_id = serializers.IntegerField(allow_null=True,
                                          help_text="The harmless command sent to prove it takes "
                                                    "commands; send it as command_id.")


class DepartmentLinkSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The mapping id.")
    department = RefSerializer(help_text="The department it serves.")
    effective_from = serializers.DateTimeField(help_text="From when.")
    effective_to = serializers.DateTimeField(allow_null=True, help_text="Until when; null: still.")
    status = serializers.CharField(help_text="active or ended.")


class DepartmentLinkInputSerializer(StrictSerializer):
    department_id = serializers.IntegerField(help_text="A department of the device's branch.")
    effective_from = serializers.CharField(help_text=MOMENT_HELP)
    effective_to = serializers.CharField(required=False, allow_blank=True,
                                         help_text=MOMENT_HELP + " Left out: open-ended.")


class EnrollmentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The enrollment id.")
    device = DeviceRefSerializer(help_text="The device.")
    employee = RefSerializer(help_text="The employee.")
    device_user_id = serializers.CharField(help_text="Their user number on the device.")
    card_number = serializers.CharField(help_text="Their RFID card (may be empty).")
    device_privilege = serializers.CharField(help_text="Their role on the terminal.")
    attendance_enabled = serializers.BooleanField(help_text="Master switch: their punches here "
                                                            "can count.")
    assigned_device_authorized = serializers.BooleanField(
        help_text="Counts in assigned-devices mode (the company default).")
    effective_from = serializers.DateTimeField(help_text="From when.")
    effective_to = serializers.DateTimeField(allow_null=True, help_text="Until when; null: still.")


class EnrollmentInputSerializer(StrictSerializer):
    device_id = serializers.UUIDField(required=False, help_text="The device (not retired).")
    employee_id = serializers.IntegerField(required=False, help_text="The employee.")
    device_user_id = serializers.CharField(max_length=100, required=False,
                                           help_text="Their user number on the device.")
    card_number = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                        help_text="Digits only. A change is sent to the terminals.")
    device_privilege = serializers.ChoiceField(choices=DeviceEnrollment.Privilege.choices,
                                               required=False,
                                               help_text="Their role on the terminal.")
    attendance_enabled = serializers.BooleanField(required=False,
                                                  help_text="Their punches can count (true).")
    assigned_device_authorized = serializers.BooleanField(
        required=False, help_text="Counts in assigned-devices mode (true for a new one).")
    effective_from = serializers.CharField(required=False, help_text=MOMENT_HELP)
    effective_to = serializers.CharField(required=False, allow_blank=True,
                                         help_text=MOMENT_HELP + " Empty: open-ended.")


class ExcludedSerializer(serializers.Serializer):
    status = serializers.CharField(help_text="Why they do not count, as stored.")
    label = serializers.CharField(help_text="The same in words.")
    what_to_do = serializers.CharField(help_text="How to make them count.")
    count = serializers.IntegerField(help_text="How many punches.")


class RulesSerializer(serializers.Serializer):
    scope = serializers.CharField(
        help_text="Which devices punches count on, company-wide: assigned_devices, "
                  "department_devices, branch_devices or company_devices.")
    scope_help = serializers.DictField(child=serializers.CharField(help_text="Its meaning."),
                                       help_text="Each scope and what it means.")
    branch_overrides = RefSerializer(many=True, help_text="Branches with their own rule.")
    employees_with_own_rule = serializers.IntegerField(help_text="People with their own rule.")
    start = serializers.DateField(help_text="The range the counts below cover.")
    end = serializers.DateField(help_text="Its last day.")
    excluded = ExcludedSerializer(many=True, help_text="Punches in the range that do not count, "
                                                       "by reason.")


class ScopeInputSerializer(StrictSerializer):
    scope = serializers.ChoiceField(choices=DeviceAttendanceScope.choices,
                                    help_text="assigned_devices, branch_devices, "
                                              "department_devices or company_devices.")


class RecheckInputSerializer(StrictSerializer):
    start = serializers.DateField(help_text="The first day.")
    end = serializers.DateField(help_text="The last day (at most a year after start).")


class RecheckSerializer(serializers.Serializer):
    checked = serializers.IntegerField(help_text="Punches judged again.")
    now_count = serializers.IntegerField(help_text="Now counting: their days were rebuilt.")
    still_excluded = serializers.IntegerField(help_text="Still not counting.")
    skipped_locked = serializers.IntegerField(help_text="In a finalised salary month: left alone.")
