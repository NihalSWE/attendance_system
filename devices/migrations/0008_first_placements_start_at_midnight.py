"""A first placement or mapping made by a device begins at the start of its day.

Attendance finds a person's shift from where they were placed at **noon**
(attendance/services.py). Until today the device import and the device user
sync stamped a new placement and its mapping with the moment they ran, so
anyone brought in during the afternoon had authorised punches and no
attendance day to hold them - the day simply did not exist for them (Dia,
2026-09-22, imported at 12:54).

The code now starts them at company midnight. This moves the rows already
written, so people imported before the fix are not left with a hole in their
first day.

Deliberately narrow:

- only a person's **earliest** placement, and only when it is also the earliest
  for that employee - a transfer recorded mid-afternoon is a real event at a
  real time and is left alone;
- only the **earliest** mapping of one number on one device;
- only rows that are not already at midnight, and only ones a device created
  (``metadata`` says so, or the change reason names the import);
- never across a day boundary: a row moves back to the start of *its own* day
  in the company's timezone, at most a few hours.
"""

import datetime
import zoneinfo

from django.db import migrations


def _zone(name):
    try:
        return zoneinfo.ZoneInfo(name or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def pull_back_to_midnight(apps, schema_editor):
    Company = apps.get_model("tenants", "Company")
    EmployeeAssignment = apps.get_model("employees", "EmployeeAssignment")
    DeviceEnrollment = apps.get_model("devices", "DeviceEnrollment")
    Employee = apps.get_model("employees", "Employee")

    zones = {c.pk: _zone(c.timezone) for c in Company.objects.all()}

    moved_assignments = set()
    for assignment in EmployeeAssignment.objects.exclude(status="cancelled").order_by(
        "employee_id", "effective_from", "pk"
    ):
        if assignment.employee_id in moved_assignments:
            continue  # only the earliest one per person
        moved_assignments.add(assignment.employee_id)
        employee = Employee.objects.filter(pk=assignment.employee_id).first()
        metadata = getattr(employee, "metadata", None) or {}
        from_device = bool(
            metadata.get("imported_from_device")
            or metadata.get("created_from_device_sync")
            or "Imported from" in (assignment.change_reason or "")
            or "device sync" in (assignment.change_reason or "")
        )
        if not from_device:
            continue
        zone = zones.get(assignment.company_id) or zoneinfo.ZoneInfo("UTC")
        local = assignment.effective_from.astimezone(zone)
        start = datetime.datetime.combine(local.date(), datetime.time.min, tzinfo=zone)
        if start >= assignment.effective_from:
            continue
        assignment.effective_from = start
        assignment.save(update_fields=["effective_from"])

    seen = set()
    for enrollment in DeviceEnrollment.objects.exclude(
        enrollment_status="removed"
    ).order_by("device_id", "device_user_id", "effective_from", "pk"):
        key = (enrollment.device_id, enrollment.device_user_id)
        if key in seen:
            continue  # only the earliest mapping of that number on that device
        seen.add(key)
        employee = Employee.objects.filter(pk=enrollment.employee_id).first()
        metadata = getattr(employee, "metadata", None) or {}
        if not (metadata.get("imported_from_device")
                or metadata.get("created_from_device_sync")):
            continue
        zone = zones.get(enrollment.company_id) or zoneinfo.ZoneInfo("UTC")
        local = enrollment.effective_from.astimezone(zone)
        start = datetime.datetime.combine(local.date(), datetime.time.min, tzinfo=zone)
        if start >= enrollment.effective_from:
            continue
        enrollment.effective_from = start
        enrollment.save(update_fields=["effective_from"])


def noop(apps, schema_editor):
    """Nothing to undo: the old times are not worth restoring."""


class Migration(migrations.Migration):

    dependencies = [
        ("devices", "0007_device_outbox_command"),
        ("employees", "0003_alter_employeeassignment_department_and_more"),
        ("tenants", "0001_initial"),
    ]

    operations = [migrations.RunPython(pull_back_to_midnight, noop)]
