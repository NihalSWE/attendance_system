"""Device permissions on the profile (Ajay, 2026-09-27): for each device a
person is on, whether their scans there count, whether it is one of their
assigned devices, their card (RFID) and their role on the terminal.

The same change as Devices → Enrollments → Edit, with the same rules: only
whoever manages the company's devices (``may_manage_devices``), and an audit
record whose ``before_data`` carries every field it touched - historical
punches are judged by it (``devices.services.policy_history``). A new card or
role is sent to the terminals they are on. The dates and the device user
number stay on the Enrollments page.
"""

from django import forms
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.forms import StyledFormMixin
from common.tenant import use_company
from devices.models import DeviceEnrollment
from devices.services.panel_access import may_manage_devices
from organization.employee_edit_services import get_employee_for_edit

FIELDS = ("attendance_enabled", "assigned_device_authorized", "card_number", "device_privilege")


class DevicePermissionForm(StyledFormMixin, forms.ModelForm):
    class Meta:
        model = DeviceEnrollment
        fields = FIELDS
        labels = {
            "attendance_enabled": "Their scans on this device count",
            "assigned_device_authorized": "One of their assigned devices",
            "card_number": "RFID card number",
            "device_privilege": "Role on the terminal",
        }
        help_texts = {
            "attendance_enabled": "Off: scans here never count for them, whatever the setting.",
            "assigned_device_authorized": "Counts when their scans must be on an assigned device.",
            "card_number": "Digits only, as printed on the card. Sent to the terminals they are on.",
        }


def current(company_id, employee):
    """Their enrolments not yet ended, with the device."""
    now = timezone.now()
    with use_company(company_id):
        return list(DeviceEnrollment.objects.select_related("device", "device__branch")
                    .filter(employee=employee)
                    .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
                    .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
                    .order_by("device__name"))


def change(*, actor, company_id, employee_id, enrollment_id, values):
    """Save one enrolment's switches. Returns ``(enrollment, resend result or None)``."""
    if not may_manage_devices(actor, company_id):
        raise PermissionDenied("Device permissions are changed by whoever manages the devices.")
    membership, employee, _a, _c = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="employees.view")
    with transaction.atomic(), use_company(company_id):
        enrollment = next((row for row in current(company_id, employee)
                           if str(row.pk) == str(enrollment_id)), None)
        if enrollment is None:
            raise PermissionDenied("That device enrolment is not theirs, or has ended.")
        before = {field: getattr(enrollment, field) for field in FIELDS}
        for field in FIELDS:
            setattr(enrollment, field, values[field] if field != "card_number"
                    else values.get(field) or "")
        enrollment.updated_by = actor
        enrollment.full_clean()
        enrollment.save()
        after = {field: getattr(enrollment, field) for field in FIELDS}
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="device_enrollment.updated", obj=enrollment, before=before, after=after,
        )
    result = None
    if (before["card_number"], before["device_privilege"]) != (
            after["card_number"], after["device_privilege"]):
        from devices.services import mapping as device_mapping

        result = device_mapping.resend_identity(actor=actor, employee=employee)
    return enrollment, result
