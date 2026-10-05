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


# --- data flow: what the devices sent ---------------------------------------------------

class DeviceMessageSerializer(serializers.Serializer):
    id = serializers.CharField(help_text="The message id (a UUID).")
    device = DeviceRefSerializer(help_text="The device that sent it.")
    message_type = serializers.CharField(
        help_text="punch_batch, heartbeat, enrollment_result, command_result, device_info or "
                  "unknown.")
    received_at = serializers.DateTimeField(help_text="When it arrived (server time).")
    record_count = serializers.IntegerField(allow_null=True,
                                            help_text="How many records it carried.")
    processing_status = serializers.CharField(
        help_text="received, parsing, parsed, partially_failed or failed.")
    processing_error = serializers.CharField(help_text="Why it failed (may be empty).")


class DeviceMessageDetailSerializer(DeviceMessageSerializer):
    content_type = serializers.CharField(help_text="As the device sent it.")
    source_ip = serializers.CharField(allow_null=True, help_text="Where it came from.")
    raw_payload_text = serializers.CharField(
        allow_null=True,
        help_text="Exactly what the device sent - to people only (it can hold fingerprint and "
                  "face templates); null for an API key.")
    punch_ids = serializers.ListField(child=serializers.IntegerField(help_text="A punch id."),
                                      help_text="The punches it became.")


class PunchSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The punch id.")
    device = DeviceRefSerializer(help_text="Where it was scanned.")
    device_user_id = serializers.CharField(help_text="The user number the device reported.")
    employee = RefSerializer(allow_null=True, help_text="Whose it is; null when unknown.")
    punched_at = serializers.DateTimeField(help_text="When, in the device's own time.")
    punched_at_utc = serializers.DateTimeField(help_text="The same instant in UTC.")
    received_at = serializers.DateTimeField(help_text="When it reached the server.")
    verification_method = serializers.CharField(
        help_text="fingerprint, face, card, pin or unknown.")
    authorization_status = serializers.CharField(
        help_text="Whether it counts: authorized, or why not - unauthorized_device, "
                  "unknown_employee, expired_enrollment, department_mismatch, branch_mismatch, "
                  "enrollment_disabled, policy_unresolved, employee_inactive.")
    dedupe_status = serializers.CharField(
        help_text="unique, probable_duplicate or confirmed_duplicate.")
    processing_status = serializers.CharField(
        help_text="pending, allocated (in attendance), excluded, needs_review or failed.")


class PunchDetailSerializer(PunchSerializer):
    message_id = serializers.CharField(allow_null=True, help_text="The message it came in.")
    duplicate_of = serializers.IntegerField(allow_null=True,
                                            help_text="The punch it duplicates, if any.")
    raw_record = serializers.DictField(help_text="The record exactly as the device sent it.")
    authorization_snapshot = serializers.DictField(
        help_text="The facts it was judged on, frozen when it was judged.")
    processing_error = serializers.CharField(help_text="Why it failed (may be empty).")


class UnresolvedCountsSerializer(serializers.Serializer):
    unknown_employee = serializers.IntegerField(help_text="Nobody is this user number.")
    expired_enrollment = serializers.IntegerField(help_text="Scanned outside their enrollment.")
    policy_unresolved = serializers.IntegerField(help_text="The rules could not decide.")
    probable_duplicate = serializers.IntegerField(help_text="Probably the same scan twice.")


class UnlinkedSerializer(serializers.Serializer):
    code = serializers.CharField(help_text="ready, other_number, other_branch, no_employee or "
                                           "not_digits.")
    text = serializers.CharField(help_text="Why, and what to do, in words.")


class DeviceUserSerializer(serializers.Serializer):
    pin = serializers.CharField(help_text="Their user number on the device.")
    name = serializers.CharField(help_text="The name the device holds (may be empty).")
    privilege = serializers.CharField(help_text="Their role on the terminal, in words.")
    card_number = serializers.CharField(help_text="Their card (may be empty).")
    has_password = serializers.BooleanField(help_text="A PIN is set on the device.")
    fingerprint_count = serializers.IntegerField(help_text="Fingerprints on the device.")
    face_count = serializers.IntegerField(help_text="Faces on the device.")
    saved_fingerprints = serializers.IntegerField(help_text="Fingerprints saved on the server.")
    saved_faces = serializers.IntegerField(help_text="Faces saved on the server.")
    only_in_scans = serializers.BooleanField(
        help_text="Seen in scans, but its user record has not arrived yet (refresh the list).")
    removed_from_device = serializers.BooleanField(help_text="Removed from the terminal.")
    employee = RefSerializer(allow_null=True, help_text="Who they are here; null: not linked.")
    attendance_enabled = serializers.BooleanField(help_text="Their punches can count.")
    assigned_device_authorized = serializers.BooleanField(
        help_text="Counts in assigned-devices mode.")
    unlinked = UnlinkedSerializer(allow_null=True, help_text="For someone not linked: why.")


class TemplateFormatSerializer(serializers.Serializer):
    kind = serializers.CharField(help_text="fingerprint, face or other.")
    type = serializers.CharField(help_text="The vendor's type number.")
    version = serializers.CharField(help_text="The algorithm version (may be empty).")
    format = serializers.CharField(help_text="The template format.")
    count = serializers.IntegerField(help_text="How many.")


class TemplatesSerializer(serializers.Serializer):
    can_save = serializers.BooleanField(help_text="The server can keep templates (its key is set).")
    problem = serializers.CharField(help_text="Why it cannot (may be empty).")
    people = serializers.IntegerField(help_text="People with saved templates.")
    fingerprints = serializers.IntegerField(help_text="Fingerprints saved.")
    faces = serializers.IntegerField(help_text="Faces saved.")
    other = serializers.IntegerField(help_text="Other templates saved.")
    last_saved = serializers.DateTimeField(allow_null=True, help_text="When last saved.")
    formats = TemplateFormatSerializer(many=True,
                                       help_text="Their formats - decides whether a template can "
                                                 "go to another device.")


class OptionSerializer(serializers.Serializer):
    key = serializers.CharField(help_text="The option, e.g. push_interval_seconds.")
    help = serializers.CharField(help_text="What it does.")
    current = serializers.CharField(help_text="What the server last set or read (may be empty).")


class OptionsSerializer(serializers.Serializer):
    writable = OptionSerializer(many=True, help_text="The options that can be changed.")
    reported = serializers.DictField(help_text="Everything the device last reported about itself.")


class CommandSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The command id.")
    key = serializers.CharField(help_text="What it is, e.g. query_users (may be empty).")
    body = serializers.CharField(help_text="The command as the device receives it.")


class ResultSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The command id.")
    key = serializers.CharField(help_text="What it was (may be empty).")
    command = serializers.CharField(help_text="The command.")
    returned = serializers.CharField(allow_null=True, help_text="The device's return code.")
    ok = serializers.BooleanField(help_text="It answered with a code.")
    at = serializers.DateTimeField(help_text="When.")


class CommandsSerializer(serializers.Serializer):
    waiting = serializers.IntegerField(help_text="Commands not yet answered.")
    pending = CommandSerializer(many=True, help_text="The next ones it will collect (20 at most).")
    recent_results = ResultSerializer(many=True, help_text="The last answers (10).")


class JobSerializer(serializers.Serializer):
    running = serializers.BooleanField(help_text="Commands are still waiting or in flight.")
    waiting = serializers.IntegerField(help_text="Not yet collected.")
    sent = serializers.IntegerField(help_text="Collected, not yet answered.")
    done = serializers.IntegerField(help_text="Answered: done (last hour).")
    refused = serializers.IntegerField(help_text="Answered: refused (last hour).")
    total = serializers.IntegerField(help_text="All of them.")
    percent = serializers.IntegerField(help_text="How far, 0-100.")
    seconds_left = serializers.IntegerField(help_text="About how long is left.")
    refused_users = serializers.ListField(child=serializers.CharField(help_text="A user number."),
                                          help_text="Users the device refused.")


class LoadSerializer(serializers.Serializer):
    running = serializers.BooleanField(help_text="People are still being prepared.")
    stopped = serializers.BooleanField(help_text="It stopped with an error.")
    total = serializers.IntegerField(help_text="People to prepare.")
    prepared = serializers.IntegerField(help_text="Prepared.")
    failed = serializers.IntegerField(help_text="Could not be prepared.")
    percent = serializers.IntegerField(help_text="How far, 0-100.")
    failures = serializers.ListField(child=serializers.DictField(help_text="Who and why."),
                                     help_text="The first ten failures.")


class JobsSerializer(serializers.Serializer):
    commands = JobSerializer(help_text="How the commands queued for it are going.")
    load = LoadSerializer(allow_null=True,
                          help_text="Loading employees onto it: the running load, or the last "
                                    "one within the hour; null otherwise.")


# --- data flow: telling the devices -------------------------------------------------------

class CommandInputSerializer(StrictSerializer):
    command = serializers.ChoiceField(
        choices=("query_users", "query_biodata", "query_options", "query_attlog"),
        help_text="query_users (send your user list), query_biodata (your fingerprints and "
                  "faces), query_options (your settings) or query_attlog (your scans of the "
                  "last 31 days).")


class QueuedSerializer(serializers.Serializer):
    queued = serializers.BooleanField(
        help_text="Queued now (false: the same command was already waiting).")
    command_id = serializers.IntegerField(allow_null=True, help_text="Its id, to follow it.")
    detail = serializers.CharField(help_text="What happens next, in words.")


class PinsInputSerializer(StrictSerializer):
    pins = serializers.ListField(
        child=serializers.CharField(max_length=100, help_text="A user number."),
        required=False, help_text="The user numbers; left out (or all true): every user.")
    all = serializers.BooleanField(required=False, default=False,
                                   help_text="true: every user on the device.")


class CopyInputSerializer(PinsInputSerializer):
    target_device_id = serializers.UUIDField(
        help_text="Another device of the company, of the same model.")


class SkippedSerializer(serializers.Serializer):
    pin = serializers.CharField(help_text="The user number.")
    reason = serializers.CharField(help_text="Why, in words.")


class RemovedSerializer(serializers.Serializer):
    removed = serializers.ListField(child=serializers.CharField(help_text="A user number."),
                                    help_text="Being removed (a few per check-in).")
    kept = SkippedSerializer(many=True, help_text="Kept, and why (e.g. the last super admin).")


class CopiedSerializer(serializers.Serializer):
    sent = serializers.ListField(child=serializers.CharField(help_text="A user number."),
                                 help_text="Being copied (a few per check-in).")
    fingerprints = serializers.IntegerField(help_text="Fingerprints going with them.")
    faces = serializers.IntegerField(help_text="Faces going with them.")
    employees_linked = serializers.IntegerField(help_text="Employees linked on the target too.")
    failed = SkippedSerializer(many=True, help_text="Not copied, and why.")


class LinkedSerializer(serializers.Serializer):
    pin = serializers.CharField(help_text="The user number.")
    employee = RefSerializer(help_text="The employee it is linked to.")


class LinkResultSerializer(serializers.Serializer):
    linked = LinkedSerializer(many=True, help_text="Linked now.")
    left = SkippedSerializer(many=True, help_text="Left unlinked, and why.")
    rechecked = serializers.IntegerField(allow_null=True,
                                         help_text="Earlier scans that now count (null: not "
                                                   "re-checked - do it with recheck).")


class DeviceImportResultSerializer(serializers.Serializer):
    created = RefSerializer(many=True, help_text="Employees made from device users (filed under "
                                                 "Unassigned: give them a department and pay).")
    linked = RefSerializer(many=True, help_text="Existing employees linked by Employee ID.")
    left = SkippedSerializer(many=True, help_text="Left as they are, and why.")


class LoadStartedSerializer(serializers.Serializer):
    started = serializers.BooleanField(help_text="Started now (false: one was already running).")
    total = serializers.IntegerField(help_text="Employees of the device's branch to load.")
    detail = serializers.CharField(help_text="What happens next, in words.")


class TemplatesSavedSerializer(serializers.Serializer):
    added = serializers.IntegerField(help_text="Templates saved for the first time.")
    updated = serializers.IntegerField(help_text="Templates that had changed.")
    unchanged = serializers.IntegerField(help_text="Already saved.")
    asked_device = serializers.BooleanField(help_text="The device was asked to send them again.")


class OptionInputSerializer(StrictSerializer):
    key = serializers.CharField(max_length=64, help_text="A changeable setting (GET …/options).")
    value = serializers.CharField(max_length=64, help_text="Its new value.")


class AddressInputSerializer(StrictSerializer):
    address = serializers.CharField(
        max_length=255, help_text="Where the device should send, e.g. "
                                  "https://attendance.example.com or 192.168.1.20:8000.")


class AddressSerializer(serializers.Serializer):
    active = serializers.BooleanField(help_text="A change is under way.")
    status = serializers.CharField(help_text="Where the change is (empty: none).")
    message = serializers.CharField(help_text="The same in words.")
    saved_address = serializers.CharField(allow_null=True,
                                          help_text="The address the device is known to use.")
    new_address = serializers.CharField(required=False, help_text="The address asked for.")
    previous_address = serializers.CharField(required=False, help_text="The one before.")
    reason = serializers.CharField(required=False, help_text="Why it failed (may be empty).")
    sent_to_device = serializers.BooleanField(required=False,
                                              help_text="The device has collected the change.")
    requested_at = serializers.CharField(required=False, help_text="When it was asked.")
    requested_by = serializers.CharField(required=False, help_text="Who asked (email).")
    deadline_at = serializers.CharField(required=False, allow_null=True,
                                        help_text="When it gives up waiting for the device.")
    finished = serializers.BooleanField(required=False, help_text="It is over, one way or the "
                                                                  "other.")
    recovery = serializers.JSONField(required=False, allow_null=True,
                                     help_text="When the device went quiet: what to type on the "
                                               "terminal to bring it back.")


class MapInputSerializer(StrictSerializer):
    device_id = serializers.UUIDField(help_text="A device of their branch.")
    start_day = serializers.DateField(required=False, allow_null=True,
                                      help_text="From this day (today when left out); earlier "
                                                "scans are re-checked.")
    attendance_enabled = serializers.BooleanField(required=False, default=True,
                                                  help_text="Their punches can count (true).")
    assigned = serializers.BooleanField(required=False, default=True,
                                        help_text="One of their assigned devices (true).")


class MappedSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="The employee.")
    device = DeviceRefSerializer(help_text="The device.")
    user_number = serializers.CharField(help_text="Their user number on it (their Employee ID).")
    sent_to_device = serializers.BooleanField(help_text="Their record is on its way to the device.")
    with_fingerprint = serializers.BooleanField(help_text="A saved fingerprint goes with it.")
    with_face = serializers.BooleanField(help_text="A saved face goes with it.")
    note = serializers.CharField(help_text="Why it was not sent, if it was not (may be empty).")
    rechecked = serializers.IntegerField(allow_null=True,
                                         help_text="Earlier scans that now count.")


class BulkMapInputSerializer(StrictSerializer):
    branch_id = serializers.IntegerField(help_text="The branch whose active employees are mapped.")
    device_id = serializers.UUIDField(required=False, allow_null=True,
                                      help_text="One device of the branch; left out: all of them.")
    start_day = serializers.DateField(required=False, allow_null=True,
                                      help_text="From this day (today when left out).")
    attendance_enabled = serializers.BooleanField(required=False, default=True,
                                                  help_text="Their punches can count (true).")
    assigned = serializers.BooleanField(required=False, default=True,
                                        help_text="Their assigned devices (true).")


class FailedSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="The employee.")
    device = DeviceRefSerializer(allow_null=True, help_text="The device, if any.")
    reason = serializers.CharField(help_text="Why, in words.")


class BulkMappedSerializer(serializers.Serializer):
    mapped = MappedSerializer(many=True, help_text="Mapped now.")
    already = serializers.IntegerField(help_text="Already mapped, left as they are.")
    failed = FailedSerializer(many=True, help_text="Not mapped, and why.")
    rechecked = serializers.IntegerField(help_text="Earlier scans that now count.")


class SendInputSerializer(StrictSerializer):
    employee_ids = serializers.ListField(child=serializers.IntegerField(help_text="An employee."),
                                         required=False, help_text="Who to send.")
    all = serializers.BooleanField(required=False, default=False,
                                   help_text="true: everyone you may put on a device.")


class SentPairSerializer(serializers.Serializer):
    employee = RefSerializer(help_text="The employee.")
    device = DeviceRefSerializer(help_text="The device.")


class SentSerializer(serializers.Serializer):
    sent = SentPairSerializer(many=True, help_text="On their way (within a minute or two).")
    failed = FailedSerializer(many=True, help_text="Not sent, and why.")



# --- other makes of device: pushing scans ------------------------------------------------

class ScanInputSerializer(StrictSerializer):
    user_number = serializers.CharField(max_length=100,
                                        help_text="The person's user number on the device.")
    time = serializers.CharField(
        max_length=19, help_text="When, in the device's own time: YYYY-MM-DD HH:MM:SS (or with "
                                 "a T in the middle).")
    method = serializers.ChoiceField(choices=("fingerprint", "face", "card", "pin", "unknown"),
                                     required=False, default="unknown",
                                     help_text="How they were recognised.")


class IngestInputSerializer(StrictSerializer):
    device_id = serializers.UUIDField(help_text="The device, registered in the company first.")
    batch_id = serializers.CharField(
        max_length=100, help_text="Your id for this batch. Sending the same batch again (a retry "
                                  "after a lost answer) stores nothing twice.")
    scans = ScanInputSerializer(many=True, allow_empty=False,
                                help_text="The scans, at most 500 per call.")


class IngestedSerializer(serializers.Serializer):
    replay = serializers.BooleanField(help_text="This batch was already received: nothing stored "
                                                "again.")
    message_id = serializers.CharField(help_text="The message it was stored as.")
    punches = serializers.IntegerField(help_text="Punches made from it.")
    detail = serializers.CharField(help_text="What happened, in words.")
