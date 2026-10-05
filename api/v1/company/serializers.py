"""What the company, branch, department and designation endpoints take and
give. The panels' forms do the checking (api/core/forms.py); these describe
the fields for the documentation and refuse unknown ones."""

from rest_framework import serializers

from api.core.serializers import StrictSerializer
from common.choices import ActiveStatus

STATUS_HELP = "active or inactive. Nothing is ever deleted: inactive keeps the history."


# --- company ---------------------------------------------------------------------

class CompanyProfileSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The company id (the X-Company value).")
    code = serializers.CharField(help_text="The company's code, set by the platform.")
    name = serializers.CharField(help_text="Shown across the panel and on payslips.")
    legal_name = serializers.CharField(help_text="The registered name (may be empty).")
    contact_person = serializers.CharField(help_text="Who to contact (may be empty).")
    email = serializers.CharField(help_text="The company's email (may be empty).")
    phone = serializers.CharField(help_text="The company's phone (may be empty).")
    address = serializers.CharField(help_text="The company's address (may be empty).")
    logo_url = serializers.CharField(allow_null=True,
                                     help_text="The logo's address, or null when there is none.")
    timezone = serializers.CharField(help_text="The company's time zone, e.g. Asia/Dhaka.")
    currency = serializers.CharField(help_text="The salary currency, e.g. BDT.")
    status = serializers.CharField(help_text="The company's status on the platform.")


class CompanyChangeSerializer(StrictSerializer):
    name = serializers.CharField(max_length=255, required=False,
                                 help_text="Shown across the panel and on payslips.")
    legal_name = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                       help_text="The registered name.")
    contact_person = serializers.CharField(max_length=120, required=False, allow_blank=True,
                                           help_text="Who to contact.")
    email = serializers.CharField(max_length=254, required=False, allow_blank=True,
                                  help_text="The company's email. No other company may use it.")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                  help_text="The company's phone. No other company may use it.")
    address = serializers.CharField(required=False, allow_blank=True,
                                    help_text="The company's address, on one line.")


class LogoSerializer(StrictSerializer):
    filename = serializers.CharField(
        max_length=200, help_text="The file's name with its type, e.g. logo.png "
                                  "(.png, .jpg, .jpeg, .webp or .svg).")
    content_base64 = serializers.CharField(
        help_text="The file itself, base64-encoded. At most 2 MB before encoding.")


# --- mail settings -------------------------------------------------------------------

class MailTestResultSerializer(serializers.Serializer):
    at = serializers.DateTimeField(allow_null=True, help_text="When the last test was sent.")
    ok = serializers.BooleanField(allow_null=True, help_text="Whether it worked; null: never tested.")
    message = serializers.CharField(help_text="What the mail server said (may be empty).")


class MailSettingsSerializer(serializers.Serializer):
    saved = serializers.BooleanField(help_text="True when the company has its own mail account.")
    from_email = serializers.CharField(help_text="The address emails come from.")
    from_name = serializers.CharField(help_text="The sender's name people see.")
    host = serializers.CharField(help_text="The mail server, e.g. smtp.gmail.com.")
    port = serializers.IntegerField(allow_null=True, help_text="Usually 587.")
    security = serializers.CharField(help_text="starttls, ssl or none.")
    username = serializers.CharField(help_text="The login on the mail server.")
    has_password = serializers.BooleanField(
        help_text="A password is saved. It is never shown - not here, not anywhere.")
    is_active = serializers.BooleanField(
        help_text="The company's email goes through this account (false: the server's).")
    last_test = MailTestResultSerializer(help_text="The last test email.")
    sending_through = serializers.CharField(
        allow_null=True, help_text='How email goes out now: "company" (this account), '
                                   '"server" (the server\'s account) or null (it cannot).')
    sending_from = serializers.CharField(help_text="The address email goes out from now.")


class MailSettingsChangeSerializer(StrictSerializer):
    from_email = serializers.CharField(max_length=254, required=False,
                                       help_text="The address emails come from.")
    from_name = serializers.CharField(max_length=120, required=False, allow_blank=True,
                                      help_text="What people see as the sender.")
    host = serializers.CharField(max_length=255, required=False,
                                 help_text="Just the server name, e.g. smtp.gmail.com.")
    port = serializers.IntegerField(required=False, help_text="25, 465, 587 or 2525.")
    security = serializers.ChoiceField(choices=("starttls", "ssl", "none"), required=False,
                                       help_text="starttls (port 587), ssl (465) or none.")
    username = serializers.CharField(max_length=255, required=False, allow_blank=True,
                                     help_text="For SendGrid it is the word apikey.")
    password = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=False, max_length=500,
        help_text="The password or API key. Leave it out to keep the saved one.")
    is_active = serializers.BooleanField(
        required=False, help_text="false: go back to the server's own mail account.")


class MailTestSerializer(StrictSerializer):
    to = serializers.EmailField(help_text="Where to send the test email.")


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField(help_text="What was done, in plain words.")


# --- branches -----------------------------------------------------------------------

class BranchSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The branch id.")
    code = serializers.CharField(help_text="Short code, unique in the company (upper case).")
    name = serializers.CharField(help_text="The branch's name.")
    address = serializers.CharField(help_text="May be empty.")
    city = serializers.CharField(help_text="May be empty.")
    postal_code = serializers.CharField(help_text="May be empty.")
    timezone = serializers.CharField(help_text="The branch's time zone, e.g. Asia/Dhaka.")
    email = serializers.CharField(help_text="May be empty.")
    phone = serializers.CharField(help_text="May be empty.")
    is_default = serializers.BooleanField(help_text="The company's default branch (exactly one).")
    status = serializers.CharField(help_text=STATUS_HELP)
    opened_on = serializers.DateField(allow_null=True, help_text="When it opened.")
    closed_on = serializers.DateField(allow_null=True, help_text="When it closed, if it has.")
    department_count = serializers.IntegerField(help_text="How many departments it has.")


class BranchCreatedSerializer(BranchSerializer):
    departments_copied = serializers.IntegerField(
        help_text="A new branch gets the company's existing departments and designations: "
                  "how many departments were copied (0 for the first).")


class BranchInputSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32, help_text="Short code, unique in the company; "
                                                          "stored in upper case.")
    name = serializers.CharField(max_length=255, help_text="The branch's name.")
    address = serializers.CharField(required=False, allow_blank=True, help_text="One line.")
    city = serializers.CharField(max_length=128, required=False, allow_blank=True,
                                 help_text="The city.")
    postal_code = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                        help_text="The postal code.")
    timezone = serializers.CharField(max_length=64, required=False, allow_blank=True,
                                     help_text="e.g. Asia/Dhaka. Left out: the company's.")
    email = serializers.CharField(max_length=254, required=False, allow_blank=True,
                                  help_text="The branch's email.")
    phone = serializers.CharField(max_length=32, required=False, allow_blank=True,
                                  help_text="The branch's phone.")
    is_default = serializers.BooleanField(
        required=False, help_text="Make it the default branch (the old default stops being "
                                  "it). The default cannot be un-set - make another the default.")
    status = serializers.ChoiceField(choices=ActiveStatus.choices, required=False,
                                     help_text=STATUS_HELP + " New branches: active.")
    opened_on = serializers.DateField(required=False, allow_null=True,
                                      help_text="When it opened (YYYY-MM-DD).")
    closed_on = serializers.DateField(required=False, allow_null=True,
                                      help_text="Only when it has really closed; not before opened_on.")


class StatusSerializer(StrictSerializer):
    status = serializers.ChoiceField(choices=ActiveStatus.choices, help_text=STATUS_HELP)


class BranchStatusSerializer(StatusSerializer):
    reason = serializers.CharField(max_length=2000, required=False, allow_blank=True,
                                   help_text="Why - kept in the audit log.")


# --- departments --------------------------------------------------------------------

class RefSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="Its id.")
    name = serializers.CharField(help_text="Its name.")


class EmployeeRefSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The employee id.")
    name = serializers.CharField(source="full_name", help_text="The employee's name.")


class TitleSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The designation id.")
    code = serializers.CharField(help_text="Its code.")
    name = serializers.CharField(help_text="Its name.")
    status = serializers.CharField(help_text=STATUS_HELP)


class DepartmentSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The department id.")
    branch = RefSerializer(help_text="The branch it belongs to (fixed once created).")
    code = serializers.CharField(help_text="Short code, unique in the branch.")
    name = serializers.CharField(help_text="Its name, unique in the branch.")
    head = EmployeeRefSerializer(allow_null=True,
                         help_text="The employee who heads it (administers its people), or null.")
    description = serializers.CharField(help_text="May be empty.")
    status = serializers.CharField(help_text=STATUS_HELP)
    designation_count = serializers.IntegerField(help_text="How many designations it has.")
    employee_count = serializers.IntegerField(help_text="How many placements point at it.")


class DepartmentDetailSerializer(DepartmentSerializer):
    designations = TitleSerializer(many=True, help_text="Its designations (job titles).")


class NewTitleSerializer(StrictSerializer):
    code = serializers.CharField(max_length=32, help_text="The designation's code.")
    name = serializers.CharField(max_length=255, help_text="The designation's name.")


class DepartmentInputSerializer(StrictSerializer):
    branch_id = serializers.IntegerField(help_text="The branch (one you may manage). Fixed "
                                                   "once created - not changeable later.")
    code = serializers.CharField(max_length=32, help_text="Short code, unique in the branch "
                                                          "(e.g. SW, HR).")
    name = serializers.CharField(max_length=255, help_text="Its name, unique in the branch.")
    head_employee_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="The employee who heads it, or null for none. The head administers the "
                  "people in the department.")
    description = serializers.CharField(required=False, allow_blank=True, help_text="One line.")
    status = serializers.ChoiceField(choices=ActiveStatus.choices, required=False,
                                     help_text=STATUS_HELP + " New: active.")
    designations = NewTitleSerializer(
        many=True, required=False,
        help_text="Only when creating: designations to add under it at once.")


class CopySerializer(StrictSerializer):
    source_branch_id = serializers.IntegerField(
        help_text="Copy from this branch: its active departments and their designations.")
    target_branch_id = serializers.IntegerField(
        help_text="Into this branch. Departments it already has (same code) are left as they "
                  "are, so copying again is safe.")


class CopyResultSerializer(serializers.Serializer):
    created = DepartmentSerializer(many=True, help_text="The departments created.")
    skipped = serializers.ListField(
        child=serializers.CharField(help_text="A department's name."),
        help_text="Departments the target branch already had.")


# --- designations -------------------------------------------------------------------

class DesignationDepartmentSerializer(RefSerializer):
    branch = RefSerializer(help_text="The department's branch.")


class DesignationSerializer(serializers.Serializer):
    id = serializers.IntegerField(help_text="The designation id.")
    department = DesignationDepartmentSerializer(
        help_text="The department it belongs to (fixed once created).")
    code = serializers.CharField(help_text="Short code, unique in the department.")
    name = serializers.CharField(help_text="The job title.")
    parent = RefSerializer(allow_null=True,
                           help_text="The senior title it reports to (same department), or null.")
    hierarchy_level = serializers.IntegerField(help_text="0 at the top; parent's level + 1.")
    status = serializers.CharField(help_text=STATUS_HELP)
    employee_count = serializers.IntegerField(help_text="How many placements hold it.")


class DesignationInputSerializer(StrictSerializer):
    department_id = serializers.IntegerField(
        help_text="The department (active, in a branch you may manage). Fixed once created.")
    code = serializers.CharField(max_length=32, help_text="Short code, unique in the department.")
    name = serializers.CharField(max_length=255, help_text="The job title.")
    parent_id = serializers.IntegerField(
        required=False, allow_null=True,
        help_text="A senior title in the same department it reports to, or null.")
    status = serializers.ChoiceField(choices=ActiveStatus.choices, required=False,
                                     help_text=STATUS_HELP + " New: active.")
