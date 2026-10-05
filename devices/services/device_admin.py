"""The device writes behind the administrator screens - registering, editing
and retiring a device, mapping it to departments, and enrolling people on it.

They lived in the views. Here, the panel and the API run the same code: the
same checks (``panel_access``, re-checked rather than trusted from a
decorator), the same writes and the same audit rows. ``before_data`` carries
every field a change touched - historical punches are judged by it
(``devices.services.policy_history``).
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from auditlog.models import AuditLog
from devices.models import BiometricDevice, DeviceDepartment
from devices.services import panel_access

OVERLAP_MESSAGE = (
    "This enrollment overlaps an existing one for the same employee or user "
    "number on this device. Edit or end the existing enrollment first."
)


def audit(*, actor, company_id, action, obj, before=None, after=None, ip=None):
    """Record a device policy change, so historical punches stay reconstructable."""
    return AuditLog.objects.create(
        company_id=company_id,
        actor_user=actor,
        actor_type=AuditLog.ActorType.USER,
        action=action,
        object_app=obj._meta.app_label,
        object_model=obj._meta.model_name,
        object_id=str(obj.pk),
        object_public_id=str(getattr(obj, "public_id", "")),
        object_display=str(obj)[:255],
        before_data=before or {},
        after_data=after or {},
        ip_address=ip,
    )


def _device_snapshot(device):
    return {"name": device.name, "serial_number": device.serial_number,
            "status": device.status, "timezone": device.timezone, "branch": device.branch_id}


def register_device(*, actor, company_id, form, ip=None):
    """Save a valid BiometricDeviceForm as a new device. A typed communication
    key is on ``form.issued_comm_key`` - shown once, never stored in plain."""
    panel_access.assert_may_manage_devices(actor, company_id)
    with transaction.atomic():
        device = form.save()
        audit(actor=actor, company_id=company_id, action="device.registered", obj=device,
              after={"name": device.name, "serial_number": device.serial_number,
                     "status": device.status}, ip=ip)
    return device


def update_device(*, actor, company_id, device, form, ip=None):
    """Save a valid BiometricDeviceForm for an existing device. A requested
    server address change is not made here (``server_address``)."""
    panel_access.assert_may_manage_devices(actor, company_id)
    # From the database: a validated ModelForm has already put the new values
    # on ``device``.
    before = _device_snapshot(BiometricDevice.objects.get(pk=device.pk))
    with transaction.atomic():
        device = form.save()
        audit(actor=actor, company_id=company_id, action="device.updated", obj=device,
              before=before, after=_device_snapshot(device), ip=ip)
    return device


def retire_device(*, actor, company_id, device, ip=None):
    """Retire a device without deleting any of its history."""
    panel_access.assert_may_manage_devices(actor, company_id)
    if device.status == BiometricDevice.Status.RETIRED:
        raise ValidationError("This device is already retired.")
    before = {"status": device.status, "decommissioned_at": None}
    with transaction.atomic():
        device.status = BiometricDevice.Status.RETIRED
        device.decommissioned_at = timezone.now()
        device.save(update_fields=["status", "decommissioned_at", "updated_at"])
        audit(actor=actor, company_id=company_id, action="device.retired", obj=device,
              before=before, after={"status": device.status,
                                    "decommissioned_at": device.decommissioned_at.isoformat()},
              ip=ip)
    return device


def add_department_link(*, actor, company_id, device, form, ip=None):
    """Save a valid DeviceDepartmentForm: the device serves that department."""
    panel_access.assert_may_manage_devices(actor, company_id)
    link = form.save(commit=False)
    link.device = device
    link.company_id = company_id
    try:
        # Savepointed, so a rejected insert is rolled back cleanly.
        with transaction.atomic():
            link.save()
            audit(actor=actor, company_id=company_id, action="device_department.created",
                  obj=link, after={"device": device.pk, "department": link.department_id,
                                   "effective_from": link.effective_from.isoformat()}, ip=ip)
    except IntegrityError:
        # The form checks the overlap; this is two administrators saving at once.
        raise ValidationError({"department": (
            f"{device.name} was mapped to that department while you were filling this "
            "in. Reload the device page to see the current mappings.")}) from None
    return link


def end_department_link(*, actor, company_id, link, ip=None):
    panel_access.assert_may_manage_devices(actor, company_id)
    if link.status == DeviceDepartment.Status.ENDED:
        raise ValidationError("This mapping has already ended.")
    before = {"status": link.status, "effective_to": None}
    with transaction.atomic():
        link.effective_to = timezone.now()
        link.status = DeviceDepartment.Status.ENDED
        link.save(update_fields=["effective_to", "status", "updated_at"])
        audit(actor=actor, company_id=company_id, action="device_department.ended", obj=link,
              before=before, after={"status": link.status,
                                    "effective_to": link.effective_to.isoformat()}, ip=ip)
    return link


def _enrollment_snapshot(enrollment):
    return {
        "attendance_enabled": enrollment.attendance_enabled,
        "assigned_device_authorized": enrollment.assigned_device_authorized,
        "device_user_id": enrollment.device_user_id,
        "card_number": enrollment.card_number,
        "device_privilege": enrollment.device_privilege,
        "effective_from": enrollment.effective_from.isoformat(),
        "effective_to": enrollment.effective_to.isoformat() if enrollment.effective_to else None,
    }


def create_enrollment(*, actor, company_id, form, ip=None):
    """Save a valid DeviceEnrollmentForm: an employee is a user number on a device."""
    panel_access.assert_may_manage_devices(actor, company_id)
    enrollment = form.save(commit=False)
    enrollment.company_id = company_id
    try:
        with transaction.atomic():
            enrollment.save()
            audit(actor=actor, company_id=company_id, action="device_enrollment.created",
                  obj=enrollment, after={
                      "device": enrollment.device_id, "employee": enrollment.employee_id,
                      "device_user_id": enrollment.device_user_id,
                      "attendance_enabled": enrollment.attendance_enabled,
                      "assigned_device_authorized": enrollment.assigned_device_authorized},
                  ip=ip)
    except IntegrityError:
        # The form mirrors both overlap constraints; this is a concurrent save.
        raise ValidationError(OVERLAP_MESSAGE) from None
    return enrollment


def update_enrollment(*, actor, company_id, enrollment, form, ip=None):
    """Save a valid DeviceEnrollmentForm for an enrollment. A new card or role
    is sent to the terminals the person is on. Returns ``(enrollment, resend
    result or None)``."""
    panel_access.assert_may_manage_devices(actor, company_id)
    # From the database: a validated ModelForm has already put the new values
    # on ``enrollment``, and historical punches are judged by before_data.
    before = _enrollment_snapshot(type(enrollment).objects.get(pk=enrollment.pk))
    try:
        with transaction.atomic():
            enrollment = form.save()
            audit(actor=actor, company_id=company_id, action="device_enrollment.updated",
                  obj=enrollment, before=before, after=_enrollment_snapshot(enrollment), ip=ip)
    except IntegrityError:
        raise ValidationError(OVERLAP_MESSAGE) from None
    result = None
    if (before["card_number"], before["device_privilege"]) != (
            enrollment.card_number, enrollment.device_privilege):
        from devices.services import mapping as device_mapping

        result = device_mapping.resend_identity(actor=actor, employee=enrollment.employee)
    return enrollment, result
