"""What the employee endpoints take and give. The panel's forms do the
checking (api/core/forms.py); these describe the fields and refuse unknown
ones."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from api.v1.company.serializers import RefSerializer
from employees.models import Employee, EmployeeCompensation, EmployeeDocument
from organization.employee_detail_services import ENDING_STATUSES
from organization.employee_profile import BLOOD_GROUPS, GENDERS, MARITAL

PAY_BASES = EmployeeCompensation.PayBasis.choices
STATUSES = Employee.EmploymentStatus.choices


# --- out --------------------------------------------------------------------------

class PersonSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The employee id.")
    name = serializers.CharField(help_text="Their full name.")


class PaySerializer(serializers.Serializer):
    pay_basis = serializers.CharField(help_text="monthly, daily or hourly.")
    base_rate = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True,
                                         help_text="Per month, day or hour, as pay_basis says.")
    currency = serializers.CharField(help_text="e.g. BDT.")
    since = serializers.DateField(help_text="When this pay started.")


class EmployeeRowSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The employee id.")
    employee_code = serializers.CharField(allow_null=True,
                                          help_text="Their Employee ID in the current placement.")
    name = serializers.CharField(help_text="Their full name.")
    work_email = serializers.CharField(help_text="May be empty.")
    branch = RefSerializer(allow_null=True, help_text="Where they are placed now.")
    department = RefSerializer(allow_null=True, help_text="Their department now.")
    designation = RefSerializer(allow_null=True, help_text="Their designation now.")
    employment_status = serializers.CharField(
        help_text="active, probation, suspended (made inactive), resigned, terminated or retired.")
    pay = PaySerializer(allow_null=True,
                        help_text="Their current pay - null when you may not see pay in their "
                                  "branch, or when none is set.")
    has_login = serializers.BooleanField(help_text="They have a login of their own.")
    needs_setup = serializers.ListField(
        child=serializers.CharField(help_text='"No department" or "No salary".'),
        help_text="What they still need (e.g. after an import).")


class PlacementSerializer(serializers.Serializer):
    employee_code = serializers.CharField(help_text="Their Employee ID.")
    branch = RefSerializer(help_text="The branch.")
    department = RefSerializer(help_text="The department.")
    designation = RefSerializer(help_text="The designation.")
    line_manager = PersonSerializer(allow_null=True, help_text="Who they report to.")
    since = serializers.DateField(help_text="When this placement started.")


class LoginStateSerializer(serializers.Serializer):
    role = serializers.CharField(help_text="employee, manager, hr, company_admin, owner, …")
    label = serializers.CharField(help_text="The role in the panel's words.")
    active = serializers.BooleanField(help_text="False: the login is disabled.")


class PersonalSerializer(serializers.Serializer):
    preferred_name = serializers.CharField(help_text="May be empty.")
    date_of_birth = serializers.DateField(allow_null=True, help_text="May be null.")
    gender = serializers.CharField(help_text="female, male, other or empty.")
    blood_group = serializers.CharField(help_text="e.g. B+; may be empty.")
    marital_status = serializers.CharField(help_text="single, married, widowed, divorced or empty.")
    national_id = serializers.CharField(help_text="May be empty.")
    passport_number = serializers.CharField(help_text="May be empty.")
    personal_email = serializers.CharField(help_text="May be empty.")
    address = serializers.CharField(help_text="May be empty.")
    emergency_contact_name = serializers.CharField(help_text="May be empty.")
    emergency_contact_phone = serializers.CharField(help_text="May be empty.")
    emergency_contact_relation = serializers.CharField(help_text="May be empty.")
    confirmation_date = serializers.DateField(allow_null=True, help_text="When confirmed; may be null.")


class MaySerializer(serializers.Serializer):
    edit = serializers.BooleanField(help_text="You may change their details, placement, personal "
                                              "information and line manager.")
    salary = serializers.BooleanField(help_text="You may see their pay.")
    change_salary = serializers.BooleanField(help_text="You may change their pay.")
    end = serializers.BooleanField(help_text="You may end their employment.")
    logins = serializers.BooleanField(help_text="You may manage their login.")
    attendance = serializers.BooleanField(help_text="You may see their attendance.")


class EmployeeSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The employee id.")
    name = serializers.CharField(help_text="Their full name.")
    first_name = serializers.CharField(help_text="First name.")
    last_name = serializers.CharField(help_text="Last name (may be empty).")
    work_email = serializers.CharField(help_text="May be empty.")
    phone = serializers.CharField(help_text="May be empty.")
    joining_date = serializers.DateField(
        allow_null=True, help_text="Days before it are not counted in attendance or salary.")
    leaving_date = serializers.DateField(allow_null=True, help_text="Their last working day, once left.")
    employment_status = serializers.CharField(
        help_text="active, probation, suspended (made inactive), resigned, terminated or retired.")
    has_left = serializers.BooleanField(help_text="Their employment has ended.")
    has_photo = serializers.BooleanField(help_text="A photo is saved.")
    placement = PlacementSerializer(allow_null=True, help_text="Where they work now.")
    pay = PaySerializer(allow_null=True,
                        help_text="Their current pay - null when you may not see it or none is set.")
    login = LoginStateSerializer(allow_null=True, help_text="Their login, or null without one.")
    personal = PersonalSerializer(help_text="Their personal information.")
    may = MaySerializer(help_text="What you may do for this person.")


class PeriodPlacementSerializer(serializers.Serializer):
    employee_code = serializers.CharField(help_text="Their Employee ID then.")
    branch = RefSerializer(help_text="The branch.")
    department = RefSerializer(help_text="The department.")
    designation = RefSerializer(help_text="The designation.")
    line_manager = PersonSerializer(allow_null=True, help_text="Who they reported to.")
    first_day = serializers.DateField(help_text="The first day.")
    last_day = serializers.DateField(allow_null=True, help_text="The last day; null: still now.")


class PeriodSalarySerializer(serializers.Serializer):
    pay_basis = serializers.CharField(help_text="monthly, daily or hourly.")
    base_rate = serializers.DecimalField(max_digits=18, decimal_places=2, coerce_to_string=True,
                                         help_text="The rate.")
    reason = serializers.CharField(help_text="Why it changed (may be empty).")
    first_day = serializers.DateField(help_text="The first day.")
    last_day = serializers.DateField(allow_null=True, help_text="The last day; null: still now.")


class PeriodDeviceSerializer(serializers.Serializer):
    device = RefSerializer(help_text="The device.")
    user_number = serializers.CharField(help_text="Their user number on that device.")
    first_day = serializers.DateField(help_text="From when their scans count.")
    last_day = serializers.DateField(allow_null=True, help_text="Until when; null: still now.")


class EventSerializer(serializers.Serializer):
    at = serializers.DateTimeField(help_text="When.")
    action = serializers.CharField(help_text="What, as recorded, e.g. employee.details_updated.")
    label = serializers.CharField(help_text="What, in plain words.")
    by = serializers.CharField(allow_null=True, help_text="Who did it (email), when known.")


class HistorySerializer(serializers.Serializer):
    placements = PeriodPlacementSerializer(many=True, help_text="Every placement, newest first.")
    salaries = PeriodSalarySerializer(many=True,
                                      help_text="Every salary, newest first - empty when you may "
                                                "not see pay.")
    devices = PeriodDeviceSerializer(many=True, help_text="Every device link, newest first.")
    events = EventSerializer(many=True, help_text="The latest 15 changes to them.")


class ChoicesSerializer(serializers.Serializer):
    branches = RefSerializer(many=True, help_text="Where you may add people.")
    departments = RefSerializer(many=True, help_text="The active departments of branch_id "
                                                     "(empty without it).")
    designations = RefSerializer(many=True, help_text="The active designations of department_id "
                                                      "(empty without it).")
    managers = PersonSerializer(many=True, help_text="Who may be chosen as their manager.")
    pay = serializers.CharField(
        help_text='Whether you set pay when adding someone: "required", "optional" (only in '
                  'some branches) or "none" (pay is set by whoever prepares salary).')


class EndedSerializer(serializers.Serializer):
    employee = EmployeeSerializer(help_text="The person now.")
    login_disabled = serializers.BooleanField(help_text="Their login was disabled.")
    enrollments_ended = serializers.IntegerField(help_text="How many device links ended.")
    devices_cleared = serializers.ListField(
        child=serializers.CharField(help_text="A device name."),
        help_text="Devices they are being removed from (on their next check-in).")
    devices_by_hand = serializers.ListField(
        child=serializers.DictField(help_text="device, pin and reason."),
        help_text="Devices to delete them on by hand - those terminals cannot be told.")


# --- in ---------------------------------------------------------------------------

class CreateSerializer(StrictSerializer):
    first_name = serializers.CharField(max_length=150, help_text="First name.")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True,
                                      help_text="Last name.")
    employee_code = serializers.CharField(
        max_length=64, help_text="Their Employee ID. Reusable by someone else only once this "
                                 "person's placement has ended.")
    branch_id = serializers.IntegerField(help_text="A branch you may add people to.")
    department_id = serializers.IntegerField(help_text="An active department of that branch.")
    designation_id = serializers.IntegerField(help_text="An active designation of that department.")
    manager_id = serializers.IntegerField(required=False, allow_null=True,
                                          help_text="Who they report to (optional).")
    start_date = serializers.DateField(help_text="When the placement and pay start; also their "
                                                 "joining date.")
    pay_basis = serializers.ChoiceField(choices=PAY_BASES, required=False,
                                        help_text="monthly (default), daily or hourly.")
    base_rate = serializers.DecimalField(
        max_digits=18, decimal_places=2, required=False, allow_null=True,
        help_text="Their pay. Required where you set pay; leave it out where you do not (see "
                  "GET /api/v1/employees/choices).")


class DetailsSerializer(StrictSerializer):
    first_name = serializers.CharField(max_length=150, required=False, help_text="First name. "
                                       "A new name is sent to the devices they are on.")
    last_name = serializers.CharField(max_length=150, required=False, allow_blank=True,
                                      help_text="Last name.")
    work_email = serializers.CharField(max_length=254, required=False, allow_blank=True,
                                       help_text="Work email.")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                  help_text="Phone.")
    joining_date = serializers.DateField(required=False, allow_null=True,
                                         help_text="Days before it are not counted.")


class PersonalInputSerializer(StrictSerializer):
    preferred_name = serializers.CharField(max_length=150, required=False, allow_blank=True,
                                           help_text="What they like to be called.")
    date_of_birth = serializers.DateField(required=False, allow_null=True,
                                          help_text="Not in the future.")
    gender = serializers.ChoiceField(choices=[c for c, _ in GENDERS], required=False,
                                     allow_blank=True, help_text="female, male, other or empty.")
    blood_group = serializers.ChoiceField(choices=[c for c, _ in BLOOD_GROUPS], required=False,
                                          allow_blank=True, help_text="e.g. B+.")
    marital_status = serializers.ChoiceField(choices=[c for c, _ in MARITAL], required=False,
                                             allow_blank=True,
                                             help_text="single, married, widowed or divorced.")
    national_id = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                        help_text="National ID number.")
    passport_number = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                            help_text="Passport number.")
    personal_email = serializers.CharField(max_length=254, required=False, allow_blank=True,
                                           help_text="Personal email.")
    address = serializers.CharField(required=False, allow_blank=True, help_text="Home address.")
    emergency_contact_name = serializers.CharField(max_length=150, required=False,
                                                   allow_blank=True, help_text="Who to call.")
    emergency_contact_phone = serializers.CharField(max_length=32, required=False,
                                                    allow_blank=True, help_text="Their phone.")
    emergency_contact_relation = serializers.CharField(max_length=64, required=False,
                                                       allow_blank=True,
                                                       help_text="e.g. Brother.")
    confirmation_date = serializers.DateField(required=False, allow_null=True,
                                              help_text="When their job was confirmed.")


class PlacementInputSerializer(StrictSerializer):
    branch_id = serializers.IntegerField(help_text="A branch where you may edit people.")
    department_id = serializers.IntegerField(help_text="An active department of that branch.")
    designation_id = serializers.IntegerField(help_text="An active designation of that department.")
    employee_code = serializers.CharField(max_length=64, help_text="Their Employee ID.")
    from_date = serializers.DateField(
        help_text="A later date than the current placement's keeps the old one as history; its "
                  "date or an earlier one replaces what was there from that date (the latest "
                  "save wins).")
    reason = serializers.CharField(required=False, allow_blank=True, help_text="Why.")


class SalaryInputSerializer(StrictSerializer):
    pay_basis = serializers.ChoiceField(choices=PAY_BASES, help_text="monthly, daily or hourly.")
    base_rate = serializers.DecimalField(max_digits=18, decimal_places=2,
                                         help_text="Per month, day or hour; more than 0.")
    from_date = serializers.DateField(
        help_text="A later date keeps the old salary as history; the current one's date or an "
                  "earlier one (not before they were placed) replaces it (the latest save wins).")
    reason = serializers.CharField(required=False, allow_blank=True, help_text="Why.")


class LineManagerInputSerializer(StrictSerializer):
    manager_id = serializers.IntegerField(
        allow_null=True, help_text="Who they report to, or null for nobody. Someone working in a "
                                   "branch you see.")


class EndInputSerializer(StrictSerializer):
    last_day = serializers.DateField(help_text="Their last working day - today or earlier.")
    status = serializers.ChoiceField(choices=[c for c, _ in ENDING_STATUSES],
                                     help_text="resigned, terminated or retired.")
    reason = serializers.CharField(help_text="A note for the record.")
    disable_login = serializers.BooleanField(
        required=False, default=True,
        help_text="Disable their login too (default true; needs the right to manage logins).")
    end_device_enrollments = serializers.BooleanField(
        required=False, default=True,
        help_text="Stop their scans counting after the last day and remove them from the "
                  "terminals (default true). Their saved fingerprint and face are kept.")


class InactiveInputSerializer(StrictSerializer):
    start_date = serializers.DateField(help_text="The first inactive day (any day).")
    end_date = serializers.DateField(
        required=False, allow_null=True,
        help_text="The last inactive day; they are active again the next day by themselves. "
                  "Null: until someone makes them active.")
    reason = serializers.CharField(max_length=255, help_text="Why - recorded with the change.")


# --- part 2: photo, education, documents, logins, settings, import --------------------

class FileInputSerializer(StrictSerializer):
    filename = serializers.CharField(max_length=200,
                                     help_text="The file's name with its type, e.g. rahim.jpg.")
    content_base64 = serializers.CharField(help_text="The file itself, base64-encoded.")


class EducationSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The row id.")
    qualification = serializers.CharField(help_text="e.g. BSc in Computer Science.")
    institution = serializers.CharField(help_text="May be empty.")
    subject = serializers.CharField(help_text="May be empty.")
    result = serializers.CharField(help_text="May be empty.")
    passing_year = serializers.IntegerField(allow_null=True, help_text="May be null.")
    note = serializers.CharField(help_text="May be empty.")


class EducationInputSerializer(StrictSerializer):
    qualification = serializers.CharField(max_length=150, required=False,
                                          help_text="Degree or certificate (needed when adding).")
    institution = serializers.CharField(max_length=200, required=False, allow_blank=True,
                                        help_text="Where.")
    subject = serializers.CharField(max_length=150, required=False, allow_blank=True,
                                    help_text="Subject or group.")
    result = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                   help_text="Result or grade.")
    passing_year = serializers.IntegerField(required=False, allow_null=True,
                                            help_text="The year passed, e.g. 2019.")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="A note.")


class DocumentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The document id.")
    kind = serializers.CharField(help_text="national_id, passport, certificate, contract, cv or "
                                           "other.")
    title = serializers.CharField(help_text="Its name, e.g. National ID card.")
    note = serializers.CharField(help_text="May be empty.")
    file_name = serializers.CharField(help_text="The name the file was uploaded with.")
    added_at = serializers.DateTimeField(help_text="When it was added.")


class DocumentInputSerializer(FileInputSerializer):
    kind = serializers.ChoiceField(choices=[c for c, _ in EmployeeDocument.Kind.choices],
                                   help_text="national_id, passport, certificate, contract, cv "
                                             "or other.")
    title = serializers.CharField(max_length=150, help_text="Its name, e.g. HSC certificate.")
    note = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                 help_text="A note.")


class GiveLoginSerializer(StrictSerializer):
    email = serializers.EmailField(help_text="The email they sign in with; it must be new.")
    password = serializers.CharField(trim_whitespace=False, max_length=256,
                                     help_text="Their first password; give it to them.")
    password_confirm = serializers.CharField(trim_whitespace=False, max_length=256,
                                             help_text="The same password again.")
    role = serializers.ChoiceField(choices=("employee", "manager", "hr"), required=False,
                                   default="employee",
                                   help_text="employee (default): their own pages. manager: also "
                                             "their branches' people. hr: every branch. Only the "
                                             "owner or company administrator gives manager or hr.")
    branch_ids = serializers.ListField(child=serializers.IntegerField(help_text="A branch id."),
                                       required=False, default=list,
                                       help_text="Only for a manager: the branches they manage.")


class LoginRoleInputSerializer(StrictSerializer):
    role = serializers.ChoiceField(choices=("employee", "manager", "hr"),
                                   help_text="employee, manager or hr.")
    branch_ids = serializers.ListField(child=serializers.IntegerField(help_text="A branch id."),
                                       required=False, default=list,
                                       help_text="Only for a manager: the branches they manage.")


class PasswordInputSerializer(StrictSerializer):
    password = serializers.CharField(trim_whitespace=False, max_length=256,
                                     help_text="The new password; give it to them.")
    password_confirm = serializers.CharField(trim_whitespace=False, max_length=256,
                                             help_text="The same password again.")


class LoginOutSerializer(serializers.Serializer):
    email = serializers.CharField(help_text="The email they sign in with.")
    role = serializers.CharField(help_text="employee, manager or hr.")
    label = serializers.CharField(help_text="The role in the panel's words.")
    active = serializers.BooleanField(help_text="False: disabled.")
    branches = RefSerializer(many=True, help_text="A manager's branches (empty otherwise).")


class LeavePolicyInputSerializer(StrictSerializer):
    policy_id = serializers.IntegerField(allow_null=True,
                                         help_text="An active leave policy, or null for the "
                                                   "company default.")
    from_date = serializers.DateField(help_text="Their leave follows it from this day.")


class AdjustmentInputSerializer(StrictSerializer):
    leave_type_id = serializers.IntegerField(help_text="An active leave type.")
    year = serializers.IntegerField(min_value=2000, max_value=2100, help_text="The leave year.")
    days = serializers.DecimalField(max_digits=6, decimal_places=2,
                                    help_text="Days to add; negative (e.g. -1.5) to take away.")
    note = serializers.CharField(max_length=255, help_text="Why.")


class OvertimeInputSerializer(StrictSerializer):
    no_overtime_from = serializers.DateField(
        allow_null=True, help_text="No overtime approved or paid for them from this day; null "
                                   "allows it again. Not inside a finalised salary month.")


class VisibilityInputSerializer(StrictSerializer):
    hidden = serializers.BooleanField(help_text="true: left out of the reports (their attendance "
                                                "still counts). false: shown again.")


class ReportsInputSerializer(StrictSerializer):
    people_ids = serializers.ListField(
        child=serializers.IntegerField(help_text="An employee id."), allow_empty=False,
        help_text="They become these people's line manager.")


class DeviceLinkSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The enrollment id.")
    device = RefSerializer(help_text="The device.")
    branch = RefSerializer(help_text="The device's branch.")
    user_number = serializers.CharField(help_text="Their user number on it.")
    attendance_enabled = serializers.BooleanField(help_text="Their scans on it count.")
    assigned_device_authorized = serializers.BooleanField(help_text="One of their assigned devices.")
    card_number = serializers.CharField(help_text="Their RFID card (may be empty).")
    device_privilege = serializers.CharField(help_text="Their role on the terminal.")


class DeviceLinkInputSerializer(StrictSerializer):
    attendance_enabled = serializers.BooleanField(required=False,
                                                  help_text="Their scans on it count.")
    assigned_device_authorized = serializers.BooleanField(
        required=False, help_text="One of their assigned devices.")
    card_number = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                        help_text="Digits only, as printed on the card.")
    device_privilege = serializers.CharField(max_length=32, required=False,
                                             help_text="Their role on the terminal.")


class ImportInputSerializer(FileInputSerializer):
    branch_id = serializers.IntegerField(required=False, allow_null=True,
                                         help_text="Where everyone in the file joins; left out: "
                                                   "the default branch you may import to.")
    confirm = serializers.BooleanField(
        required=False, default=False,
        help_text="false (default): only check and answer what it would do. true: import.")


class ImportRowSerializer(serializers.Serializer):
    line = serializers.IntegerField(help_text="The row's line in the file.")
    employee_code = serializers.CharField(help_text="Employee ID in the file.")
    name = serializers.CharField(help_text="Name in the file.")
    problems = serializers.ListField(child=serializers.CharField(help_text="One problem."),
                                     help_text="Why it cannot be imported (empty when fine).")


class ImportResultSerializer(serializers.Serializer):
    imported = serializers.BooleanField(help_text="true: done (confirm). false: only checked.")
    branch = RefSerializer(help_text="Where they join.")
    rows = serializers.IntegerField(help_text="Rows read.")
    new = serializers.IntegerField(help_text="New employees (created, or to create).")
    existing = serializers.IntegerField(help_text="Already employees: left as they are, or "
                                                  "renamed when the file names them differently.")
    bad = ImportRowSerializer(many=True, help_text="Every row that cannot be imported.")
