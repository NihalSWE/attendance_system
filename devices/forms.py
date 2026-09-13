"""Forms for the device management screens.

Uses the project's shared input conventions (common.forms.StyledFormMixin):
database-backed choices get Select2, fixed choices get a styled native select.

Vendor settings are edited as named fields rather than a raw JSON box. The
``settings`` column stays a JSONField, but an administrator should never have
to hand-write JSON to change a push interval — and named fields are what makes
"validated JSONField" true rather than aspirational.
"""

import secrets

from django import forms
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from common.forms import StyledFormMixin
from devices.models import (
    BiometricDevice,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceModel,
)
from devices.services import server_address
from employees.models import Employee
from organization.models import Branch, CompanyDepartment

# Curated rather than the full zoneinfo list: these are the timezones this
# product is deployed in, and a 600-entry dropdown is not a usable control.
TIMEZONE_CHOICES = [
    ("Asia/Dhaka", "Asia/Dhaka (UTC+6)"),
    ("Asia/Kolkata", "Asia/Kolkata (UTC+5:30)"),
    ("Asia/Karachi", "Asia/Karachi (UTC+5)"),
    ("Asia/Kathmandu", "Asia/Kathmandu (UTC+5:45)"),
    ("Asia/Dubai", "Asia/Dubai (UTC+4)"),
    ("Asia/Singapore", "Asia/Singapore (UTC+8)"),
    ("Europe/London", "Europe/London"),
    ("UTC", "UTC"),
]


def generate_comm_key():
    """A device secret the administrator types into the terminal once."""
    return secrets.token_hex(8)


class BiometricDeviceForm(StyledFormMixin, forms.ModelForm):
    """Register or edit one device.

    Tenant-owned relations are declared with an empty queryset and filled in
    __init__. A ModelForm builds its default querysets at import time, when no
    tenant context exists yet, and the scoped manager correctly refuses to run
    then — so the real queryset has to be attached per request.
    """

    branch = forms.ModelChoiceField(queryset=Branch.all_objects.none())
    comm_key = forms.CharField(
        required=False,
        label="Communication key",
        help_text=(
            "The secret the device presents when it pushes. Leave blank to "
            "generate one. It is shown once after saving and stored only as a "
            "hash, so it cannot be displayed again."
        ),
    )
    timezone = forms.ChoiceField(
        choices=TIMEZONE_CHOICES,
        help_text="How the device's local punch times are interpreted.",
    )
    push_interval_seconds = forms.IntegerField(
        min_value=1, max_value=3600, initial=10, required=False,
        label="Push interval (seconds)",
        help_text="How often the device contacts the server when idle.",
    )
    error_delay_seconds = forms.IntegerField(
        min_value=1, max_value=3600, initial=30, required=False,
        label="Retry delay (seconds)",
        help_text="How long the device waits before retrying after a failure.",
    )
    realtime = forms.BooleanField(
        required=False, initial=True, label="Push punches in real time",
        help_text="When off, the device only uploads on its timed interval.",
    )
    server_address = forms.CharField(
        required=False,
        max_length=255,
        label="Server address",
        help_text=(
            "Where the device sends its data, for example "
            "https://attendance.example.com or 192.168.1.20:8000. Changing "
            "this is checked before the device is told anything, and only "
            "saved once the device has connected at the new address."
        ),
    )
    server_address_confirmed = forms.BooleanField(
        required=False,
        label="I understand the risk of changing the server address",
        help_text=(
            "The device can only be reached while it is pointing here. If it "
            "switches to an address it cannot reach, no one can fix it from "
            "this screen — someone has to walk to the terminal and type the "
            "old address back in."
        ),
    )

    class Meta:
        model = BiometricDevice
        fields = (
            "name", "serial_number", "branch", "device_model",
            "external_device_id", "timezone", "installed_at", "status",
        )
        widgets = {
            "installed_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        help_texts = {
            "serial_number": (
                "Exactly as printed on the device. It is how an inbound push is "
                "matched to this record."
            ),
            "external_device_id": "Optional vendor/cloud identifier.",
            "status": (
                "Only pending, active and offline devices may send data. "
                "Retired and suspended devices are refused."
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.instance.pk:
            # Nothing to repoint yet. The address is typed into the terminal
            # during commissioning, and the detail page states what to type.
            del self.fields["server_address"]
            del self.fields["server_address_confirmed"]
        # Tenant-scoped querysets: the manager already filters by the active
        # company, so another tenant's branches can never appear in the list.
        self.fields["branch"].queryset = Branch.objects.order_by("name")
        self.fields["device_model"].queryset = DeviceModel.objects.filter(
            is_active=True
        ).select_related("vendor").order_by("vendor__name", "name")

        if self.instance.pk:
            settings = self.instance.settings or {}
            saved = server_address.current_address(self.instance)
            self.fields["server_address"].initial = saved.text if saved else ""
            if server_address.in_flight_change(self.instance) is not None:
                # A second change while one is running would race the first to
                # decide what the device's address is, so the control is
                # closed rather than left to fail on submit.
                self.fields["server_address"].disabled = True
                self.fields["server_address_confirmed"].disabled = True
                self.fields["server_address"].help_text = (
                    "A change is already in progress. Wait for it to finish "
                    "on the device page before starting another."
                )
            self.fields["push_interval_seconds"].initial = settings.get(
                "push_interval_seconds", 10
            )
            self.fields["error_delay_seconds"].initial = settings.get(
                "error_delay_seconds", 30
            )
            self.fields["realtime"].initial = settings.get("realtime", True)
            self.fields["comm_key"].help_text = (
                "Leave blank to keep the current key. Entering a new value "
                "replaces it, and the device must be updated to match."
            )

    def clean(self):
        """Validate the address format here; the round trip is the view's job.

        ``requested_address`` is left on the form as the parsed value when it
        differs from what is saved, so the view knows whether to start a
        change at all — a save that did not touch the address must not queue
        anything to the device.
        """
        data = super().clean()
        self.requested_address = None
        if "server_address" not in self.fields:
            return data

        typed = (data.get("server_address") or "").strip()
        saved = server_address.current_address(self.instance)
        if not typed:
            if saved is not None:
                self.add_error(
                    "server_address",
                    "Enter the address the device uses. Clearing it would "
                    "leave nothing to point the device at.",
                )
            return data

        try:
            target = server_address.parse_address(typed)
        except server_address.ServerAddressError as exc:
            self.add_error("server_address", str(exc))
            return data

        if saved is not None and saved.matches(target):
            return data
        if not data.get("server_address_confirmed"):
            self.add_error(
                "server_address_confirmed",
                "Tick this to confirm you understand what a wrong server "
                "address does to the device.",
            )
            return data

        self.requested_address = target
        return data

    def clean_serial_number(self):
        serial = (self.cleaned_data["serial_number"] or "").strip()
        clashing = BiometricDevice.objects.filter(serial_number=serial)
        if self.instance.pk:
            clashing = clashing.exclude(pk=self.instance.pk)
        if clashing.exists():
            raise forms.ValidationError(
                "Another device in this company already uses that serial number."
            )
        return serial

    def save(self, commit=True):
        device = super().save(commit=False)
        device.settings = {
            **(device.settings or {}),
            "push_interval_seconds": self.cleaned_data.get("push_interval_seconds") or 10,
            "error_delay_seconds": self.cleaned_data.get("error_delay_seconds") or 30,
            "realtime": bool(self.cleaned_data.get("realtime")),
        }

        # Returned to the view so it can be shown exactly once.
        self.issued_comm_key = ""
        entered = (self.cleaned_data.get("comm_key") or "").strip()
        if entered:
            self.issued_comm_key = entered
        elif not device.authentication_secret_hash:
            self.issued_comm_key = generate_comm_key()

        if self.issued_comm_key:
            device.authentication_secret_hash = make_password(self.issued_comm_key)
            if not device.authentication_key_id:
                device.authentication_key_id = f"key-{secrets.token_hex(4)}"

        if commit:
            device.save()
        return device


class DeviceEnrollmentForm(StyledFormMixin, forms.ModelForm):
    """Map an employee to a device user number, on a dated interval.

    The two booleans are deliberately separate and separately explained:
    enrolling somebody means the device recognises them, which is not the same
    as their punches counting.
    """

    device = forms.ModelChoiceField(queryset=BiometricDevice.all_objects.none())
    employee = forms.ModelChoiceField(queryset=Employee.all_objects.none())

    class Meta:
        model = DeviceEnrollment
        fields = (
            "device", "employee", "device_user_id", "device_privilege",
            "attendance_enabled", "assigned_device_authorized",
            "effective_from", "effective_to",
        )
        widgets = {
            "effective_from": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "effective_to": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        labels = {
            "device_user_id": "Device user number",
            "attendance_enabled": "Attendance enabled",
            "assigned_device_authorized": "Authorised for assigned-devices mode",
        }
        help_texts = {
            "device_user_id": (
                "The user number stored on the device itself, exactly as the "
                "device reports it."
            ),
            "attendance_enabled": (
                "Master switch. When off, punches from this enrollment are "
                "excluded under every scope, including company-wide."
            ),
            "assigned_device_authorized": (
                "Only consulted in assigned-devices mode. Leave off for a "
                "recognition-only enrollment."
            ),
            "effective_to": "Leave blank while the enrollment is open-ended.",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["device"].queryset = (
            BiometricDevice.objects.exclude(status=BiometricDevice.Status.RETIRED)
            .select_related("branch")
            .order_by("name")
        )
        self.fields["employee"].queryset = Employee.objects.order_by(
            "first_name", "last_name"
        )
        if not self.instance.pk:
            self.fields["effective_from"].initial = timezone.now()
            # Both on by default for a new enrollment, and only a new one —
            # editing keeps whatever was saved.
            #
            # attendance_enabled already defaults True on the model.
            # assigned_device_authorized does not, and leaving it off is a
            # silent trap: the company default scope is assigned-devices, so
            # every punch from an enrollment without it is filed as
            # unauthorized_device, and the person shows up absent in
            # attendance and unpaid in salary. Someone enrolling an employee
            # means them to be recognised *and* counted; withholding the
            # second is the deliberate act, so that is the one that needs a
            # click.
            self.fields["attendance_enabled"].initial = True
            self.fields["assigned_device_authorized"].initial = True

    def clean(self):
        data = super().clean()
        start = data.get("effective_from")
        end = data.get("effective_to")
        if start and end and end <= start:
            self.add_error("effective_to", "The end must be after the start.")

        device = data.get("device")
        user_id = (data.get("device_user_id") or "").strip()
        if device and user_id and start:
            # Mirrors the database exclusion constraint so the administrator
            # gets a readable message instead of an IntegrityError page.
            overlapping = DeviceEnrollment.objects.filter(
                device=device, device_user_id=user_id
            ).exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
            if self.instance.pk:
                overlapping = overlapping.exclude(pk=self.instance.pk)
            for other in overlapping:
                if self._overlaps(start, end, other.effective_from, other.effective_to):
                    self.add_error(
                        "device_user_id",
                        f"User number {user_id} is already mapped to "
                        f"{other.employee} for an overlapping period. End that "
                        "enrollment first so the history stays unambiguous.",
                    )
                    break

        employee = data.get("employee")
        if device and employee and start:
            # The second exclusion constraint: one employee cannot hold two
            # open enrollments on the same device at once. Without this check
            # the database refused it with an IntegrityError page.
            same_person = DeviceEnrollment.objects.filter(
                device=device, employee=employee
            ).exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
            if self.instance.pk:
                same_person = same_person.exclude(pk=self.instance.pk)
            for other in same_person:
                if self._overlaps(start, end, other.effective_from, other.effective_to):
                    self.add_error(
                        "employee",
                        f"{employee} is already enrolled on {device} as user "
                        f"{other.device_user_id} from {other.effective_from:%d %b %Y}. "
                        "Edit that enrollment, or end it before adding a new one.",
                    )
                    break
        return data

    @staticmethod
    def _overlaps(start_a, end_a, start_b, end_b):
        if end_a is not None and end_a <= start_b:
            return False
        if end_b is not None and end_b <= start_a:
            return False
        return True


class DeviceDepartmentForm(StyledFormMixin, forms.ModelForm):
    """Map a device to a department for department-devices mode."""

    department = forms.ModelChoiceField(queryset=CompanyDepartment.all_objects.none())

    class Meta:
        model = DeviceDepartment
        fields = ("department", "effective_from", "effective_to")
        widgets = {
            "effective_from": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "effective_to": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
        }
        help_texts = {
            "effective_to": "Leave blank while the mapping is open-ended.",
        }

    def __init__(self, *args, device=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.device = device
        # A department mapping only makes sense within the device's own branch.
        self.fields["department"].queryset = (
            CompanyDepartment.objects.filter(branch_id=device.branch_id)
            .select_related("department")
            .order_by("department__name")
        )
        if not self.instance.pk:
            self.fields["effective_from"].initial = timezone.now()

    def clean(self):
        data = super().clean()
        start = data.get("effective_from")
        end = data.get("effective_to")
        if start and end and end <= start:
            self.add_error("effective_to", "The end must be after the start.")
        return data
