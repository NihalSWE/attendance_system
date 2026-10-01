"""Map employees to device users, and put them on the company's devices.

Ajay's design (2026-09-15, confirmed 2026-09-19): the device user number is the
employee's code, shown as "Employee ID". Mapping creates the DeviceEnrollment
that says "number N on this device is this employee", and — when the person is
not on the device yet, or the device lacks their fingerprint/face — writes them
there through ``commands.push_to_device`` with any fingerprint and face another
device of the same model captured ("enrol once, copy to the rest").

- Map one employee (Employees list → Map): to a device of their own branch.
- Map a whole branch (Employees list → Bulk map): every active employee of the
  branch, to every active device of the branch.
- Map automatically (Device users): every unmapped device user whose number is
  an employee's Employee ID.

Who may map: the owner, the company administrator, or anyone who may edit
employees in that branch (``employees.edit``). A backdated mapping re-checks
the device's excluded punches from that day, so scans made before the mapping
start to count; that needs an administrator, and otherwise is left to one.
"""

import contextlib
import contextvars
import datetime
import zoneinfo
from dataclasses import dataclass, field

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.utils import timezone

from access_control.branch_access import can
from auditlog.models import AuditLog
from devices.models import BiometricDevice, DeviceEnrollment, DeviceUserTemplate
from devices.services import commands, protocol, templates
from devices.services.device_roster import build_roster


class MappingError(Exception):
    """The mapping cannot be made; the message says why, for the screen."""


@dataclass
class Outcome:
    """What one mapping did, in words the screen can show."""

    employee: object
    device: object
    enrollment: object = None
    uploaded: bool = False
    fingerprint: bool = False
    face: bool = False
    note: str = ""
    rechecked: int | None = 0


@dataclass
class BulkResult:
    mapped: list = field(default_factory=list)
    skipped: list = field(default_factory=list)   # (employee, device, reason)
    failed: list = field(default_factory=list)    # (employee, device, reason)
    rechecked: int = 0


def _zone(company):
    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


#: Within one batch (``_roster_memo``) each employee's placement is read once:
#: a run over 250 people read it some 600 times (2026-10-01).
_ASSIGNMENTS = contextvars.ContextVar("current_assignments", default=None)


def current_assignment(employee):
    memo = _ASSIGNMENTS.get()
    if memo is not None and employee.pk in memo:
        return memo[employee.pk]
    found = (
        employee.assignments.select_related("branch")
        .exclude(status="cancelled").order_by("-effective_from", "-pk").first()
    )
    if memo is not None:
        memo[employee.pk] = found
    return found


def employee_id_for(employee):
    """The employee's Employee ID (current code) if it can be a device number."""
    assignment = current_assignment(employee)
    code = (assignment.employee_code if assignment else "").strip()
    return code if code.isdigit() and len(code) <= 20 else ""


def may_map(user, company_id, branch_id):
    return can(user, company_id, "employees.edit", branch_id)


def live_enrollment(device, employee, at=None):
    at = at or timezone.now()
    return (
        DeviceEnrollment.all_objects.filter(device=device, employee=employee)
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .filter(effective_from__lte=at)
        .exclude(effective_to__lte=at)
        .first()
    )


def _audit(actor, device, action, obj, after):
    AuditLog.objects.create(
        company_id=device.company_id, actor_user=actor, actor_type=AuditLog.ActorType.USER,
        action=action, object_app=obj._meta.app_label, object_model=obj._meta.model_name,
        object_id=str(obj.pk), object_public_id=str(getattr(obj, "public_id", "")),
        object_display=str(obj)[:255], after_data=after,
    )


def day_start(company, day=None):
    """Company midnight of ``day`` (today by default).

    Attendance works in whole days: it asks where somebody was placed at noon
    to find their shift (attendance/services.py). A placement or mapping
    stamped at the moment it was made therefore loses that whole day when it
    is made in the afternoon - the person punches, the punch is authorised,
    and no attendance day exists to hold it (Dia, 2026-09-22, imported at
    12:54). Everything created today begins at the start of today instead,
    which is what the CSV import already does.
    """
    zone = _zone(company)
    day = day or timezone.now().astimezone(zone).date()
    return datetime.datetime.combine(day, datetime.time.min, tzinfo=zone)


def _start(device, day):
    """The mapping's start: the company's midnight of ``day``, or of today."""
    if isinstance(day, datetime.datetime):
        return day
    return day_start(device.company, day)


#: Within one batch (bulk map, import, send, copy) each device's roster is
#: built once: building it reads every user upload the device ever sent.
_ROSTERS = contextvars.ContextVar("device_rosters", default=None)


@contextlib.contextmanager
def _roster_memo():
    """One batch: rosters, placements and a device's user rows read once."""
    token = _ROSTERS.set({}) if _ROSTERS.get() is None else None
    placements = _ASSIGNMENTS.set({}) if _ASSIGNMENTS.get() is None else None
    try:
        with commands.user_rows_memo():
            yield
    finally:
        if token is not None:
            _ROSTERS.reset(token)
        if placements is not None:
            _ASSIGNMENTS.reset(placements)


def _roster(device):
    memo = _ROSTERS.get()
    if memo is None:
        return build_roster(device)
    if device.pk not in memo:
        memo[device.pk] = build_roster(device)
    return memo[device.pk]


def _on_device(device):
    """``{pin: roster row}`` for users the device reported and still holds."""
    return {row["pin"]: row for row in _roster(device) if not row.get("removed_from_device")}


def _source_templates(device, pin):
    """The newest saved fingerprint and face for ``pin`` on this device model."""
    rows = (
        DeviceUserTemplate.all_objects.filter(
            company_id=device.company_id, device_model_id=device.device_model_id,
            device_user_id=pin,
        ).order_by("-captured_at", "number")
    )
    finger = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FINGERPRINT), None)
    face = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FACE), None)
    return finger, face


def _source_user(device, pin):
    """What the company's devices last reported for ``pin`` (card, role).

    A user still on some device wins; otherwise the last report of one since
    removed (deleted on the terminal) — their card and role are still theirs,
    which is what lets a replaced or wiped device be refilled with them.
    """
    removed = {}
    others = BiometricDevice.all_objects.filter(company_id=device.company_id).exclude(
        status=BiometricDevice.Status.RETIRED)
    for other in others:
        for row in _roster(other):
            if row["pin"] != pin or row.get("only_in_scans"):
                continue
            if not row.get("removed_from_device"):
                return row
            removed = removed or row
    return removed


#: The terminal's own number for a super admin: the role that opens its menu.
SUPER_ADMIN = "14"


def privilege_of(row):
    """The enrollment privilege for a roster row: what the terminal reports.

    Recorded when the mapping is made, so the software knows an administrator
    is one. Without it every enrollment carries the model default, "normal
    user", and re-sending a changed name would quietly strip a terminal's
    administrator of the menu. A role this software has no word for (an
    enroller, a manager) is kept as OTHER and left alone.
    """
    code = str((row or {}).get("privilege_code") or "").strip()
    if code == SUPER_ADMIN:
        return DeviceEnrollment.Privilege.DEVICE_ADMIN
    if code in ("", "0"):
        return DeviceEnrollment.Privilege.NORMAL_USER
    return DeviceEnrollment.Privilege.OTHER


def copy_to_device(*, actor, device, employee, pin, role=None):
    """Write the employee to the device with what the company holds for them.

    Returns ``(uploaded, fingerprint, face, note)``. A device whose model's
    user writes are not measured (the 3A) is left alone and the note says so.
    """
    source = _source_user(device, pin)
    finger_row, face_row = _source_templates(device, pin)
    try:
        finger = templates.as_payload(finger_row) if finger_row else None
        face = templates.as_payload(face_row) if face_row else None
    except templates.TemplateKeyMissing as exc:
        return False, False, False, str(exc)
    if role is None:
        role = int(source.get("privilege_code") or 0) if str(source.get("privilege_code") or "0").isdigit() else 0
        role = role if role in commands.ROLE_PRIVILEGES else 0
    entries, error = commands.push_to_device(
        device, pin, name=employee.full_name, card=source.get("card_number") or "",
        role=role, finger_template=finger, face_template=face, requested_by=actor,
    )
    if error:
        return False, False, False, error
    return True, bool(finger), bool(face), ""


def map_employee(*, actor, device, employee, start_day=None, attendance_enabled=True,
                 assigned=True, upload=True, check_access=True):
    """Map ``employee`` to ``device`` under their Employee ID. Returns an Outcome."""
    if device.company_id != employee.company_id:
        raise MappingError("That device belongs to another company.")
    if device.status == BiometricDevice.Status.RETIRED:
        raise MappingError(f"{device.name} is retired.")
    assignment = current_assignment(employee)
    if assignment is None:
        raise MappingError(f"{employee.full_name} has no placement yet.")
    if check_access and not may_map(actor, device.company_id, assignment.branch_id):
        raise PermissionDenied("You may not map employees of that branch.")
    if assignment.branch_id != device.branch_id:
        raise MappingError(
            f"{employee.full_name} works at {assignment.branch.name}; "
            f"{device.name} is at {device.branch.name}."
        )
    pin = employee_id_for(employee)
    if not pin:
        raise MappingError(
            f"{employee.full_name}'s Employee ID ({assignment.employee_code or 'none'}) "
            "must be digits only (at most 20) to be used on a device."
        )
    if live_enrollment(device, employee):
        raise MappingError(f"{employee.full_name} is already mapped on {device.name}.")

    start = _start(device, start_day)
    on_device = _on_device(device).get(pin)
    with transaction.atomic():
        taken = (
            DeviceEnrollment.all_objects.filter(device=device, device_user_id=pin)
            .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
            .exclude(effective_to__lte=start).exclude(employee=employee).first()
        )
        if taken:
            raise MappingError(
                f"Employee ID {pin} is already mapped on {device.name} to another person."
            )
        enrollment = DeviceEnrollment(
            device=device, employee=employee, device_user_id=pin,
            card_number=(on_device or {}).get("card_number") or "",
            device_privilege=privilege_of(on_device),
            attendance_enabled=attendance_enabled, assigned_device_authorized=assigned,
            effective_from=start,
            enrollment_status=(DeviceEnrollment.EnrollmentStatus.SYNCED if on_device
                               else DeviceEnrollment.EnrollmentStatus.PENDING),
            created_by=actor,
        )
        enrollment.company_id = device.company_id
        try:
            enrollment.full_clean()
            enrollment.save()
        except (ValidationError, IntegrityError) as exc:
            raise MappingError(f"{employee.full_name} cannot be mapped from that date: {exc}") from exc

        outcome = Outcome(employee=employee, device=device, enrollment=enrollment)
        # Put them on the device when they are not there yet, or when the
        # device lacks a fingerprint/face another device captured for them.
        missing_bio = on_device is not None and not (
            on_device.get("fingerprint_count") or on_device.get("face_count"))
        if upload and (on_device is None or missing_bio):
            outcome.uploaded, outcome.fingerprint, outcome.face, outcome.note = copy_to_device(
                actor=actor, device=device, employee=employee, pin=pin,
            )
            if outcome.uploaded:
                enrollment.enrollment_status = DeviceEnrollment.EnrollmentStatus.QUEUED
                enrollment.save(update_fields=["enrollment_status", "updated_at"])
        _audit(actor, device, "device_enrollment.mapped", enrollment, {
            "device": device.serial_number, "device_user_id": pin,
            "employee_id": employee.pk, "effective_from": start.isoformat(),
            "attendance_enabled": attendance_enabled, "assigned_device_authorized": assigned,
            "uploaded": outcome.uploaded, "fingerprint": outcome.fingerprint, "face": outcome.face,
        })
    return outcome


def recheck_from(*, actor, device, start):
    """Re-judge the company's excluded punches since ``start`` (a backdated mapping).

    Returns how many punches now count, or None when the actor may not re-check.
    """
    from devices.services.attendance_rules import MAX_RECHECK_DAYS, recheck_punches

    tz = _zone(device.company)
    first = start.astimezone(tz).date()
    today = timezone.now().astimezone(tz).date()
    if first >= today:
        return 0
    first = max(first, today - datetime.timedelta(days=MAX_RECHECK_DAYS - 1))
    try:
        result = recheck_punches(actor=actor, company_id=device.company_id, start=first, end=today)
    except (PermissionDenied, ValidationError):
        return None
    return result.now_count


def map_branch(*, actor, branch, device=None, start_day=None, attendance_enabled=True,
               assigned=True):
    """Map every active employee of ``branch`` to its devices (or just ``device``)."""
    from employees.models import Employee

    if not may_map(actor, branch.company_id, branch.pk):
        raise PermissionDenied("You may not map employees of that branch.")
    devices = [device] if device else list(
        BiometricDevice.all_objects.filter(company_id=branch.company_id, branch=branch)
        .exclude(status__in=[BiometricDevice.Status.RETIRED, BiometricDevice.Status.SUSPENDED])
        .order_by("name")
    )
    if not devices:
        raise MappingError(f"{branch.name} has no device yet.")
    result = BulkResult()
    people = [
        e for e in Employee.all_objects.filter(company_id=branch.company_id)
        .exclude(employment_status__in=["resigned", "terminated"]).order_by("first_name", "last_name")
        if (a := current_assignment(e)) is not None and a.branch_id == branch.pk
    ]
    earliest = None
    with _roster_memo():
        for target in devices:
            for employee in people:
                if live_enrollment(target, employee):
                    result.skipped.append((employee, target, "already mapped"))
                    continue
                try:
                    outcome = map_employee(
                        actor=actor, device=target, employee=employee, start_day=start_day,
                        attendance_enabled=attendance_enabled, assigned=assigned,
                        check_access=False,
                    )
                except MappingError as exc:
                    result.failed.append((employee, target, str(exc)))
                    continue
                result.mapped.append(outcome)
                start = outcome.enrollment.effective_from
                earliest = start if earliest is None else min(earliest, start)
    if earliest is not None:
        result.rechecked = recheck_from(actor=actor, device=devices[0], start=earliest)
    return result


def map_one(*, actor, device, employee, start_day=None, attendance_enabled=True, assigned=True):
    """``map_employee`` plus the re-check a backdated mapping needs."""
    outcome = map_employee(actor=actor, device=device, employee=employee, start_day=start_day,
                           attendance_enabled=attendance_enabled, assigned=assigned)
    outcome.rechecked = recheck_from(actor=actor, device=device,
                                     start=outcome.enrollment.effective_from)
    return outcome


def map_automatically(*, actor, device, pins=None):
    """Map every unmapped device user whose number is an employee's Employee ID.

    They are already on the device, so nothing is written to it. Returns a
    BulkResult; ``skipped`` holds numbers no employee has, as (pin, None, reason).
    """
    from employees.models import Employee, EmployeeAssignment

    result = BulkResult()
    wanted = None if pins is None else {str(p) for p in pins}
    unmapped = [
        row for row in build_roster(device)
        if not row["is_mapped"] and not row.get("removed_from_device")
        and (wanted is None or row["pin"] in wanted)
    ]
    if not unmapped:
        return result
    pins = [row["pin"] for row in unmapped]
    codes = {}
    for assignment in (
        EmployeeAssignment.all_objects.filter(company_id=device.company_id, employee_code__in=pins)
        .exclude(status="cancelled").select_related("employee").order_by("-effective_from")
    ):
        # The employee whose *current* code it is.
        if assignment.employee_code not in codes and current_assignment(assignment.employee) == assignment:
            codes[assignment.employee_code] = assignment.employee
    for row in unmapped:
        employee = codes.get(row["pin"])
        if employee is None:
            result.skipped.append((row["pin"], None, "no employee has this Employee ID"))
            continue
        if not may_map(actor, device.company_id, current_assignment(employee).branch_id):
            result.skipped.append((row["pin"], None, "not in a branch you may map"))
            continue
        try:
            result.mapped.append(map_employee(actor=actor, device=device, employee=employee,
                                              upload=False, check_access=False))
        except MappingError as exc:
            result.failed.append((employee, device, str(exc)))
    return result


#: Why a device user is not linked to an employee, and what links them (Nihal,
#: 2026-09-29: "which are they, why are they not linked, what do they need").
#: In the order the Device users filter lists them.
UNLINKED_REASONS = {
    "ready": "Ready to link",
    "other_number": "Employee already linked under another number",
    "other_branch": "Employee works at another branch",
    "no_employee": "No employee has this Employee ID",
    "not_digits": "Device number is not digits",
}

#: What to do about each reason, in one line, for the notice on Device users.
UNLINKED_HINTS = {
    "ready": "Their number is an employee's Employee ID. Press Link to existing employees.",
    "other_number": ("The employee with this Employee ID is already linked on this device under "
                     "an old number. That old link has to end before this number can be linked."),
    "other_branch": ("The employee with this Employee ID is placed at another branch. Move them "
                     "to this branch, or use a device of their branch."),
    "no_employee": ("No employee has this number as Employee ID. Add them as employees, or give "
                    "the right employee this Employee ID."),
    "not_digits": "An Employee ID on a device is digits only. Link them by hand with Map a user.",
}


def why_not_linked(device, roster):
    """Explain every unlinked user still on the device, in place.

    Each such roster row gets ``unlinked``: ``code`` (a key of
    UNLINKED_REASONS), ``text`` (why, and what to do, in words) and
    ``employee`` (who their number points to, if anyone). It answers exactly
    the questions ``map_employee`` asks when Link to existing employees
    runs, so the screen and the button agree. Returns ``{code: count}``.
    """
    from employees.models import EmployeeAssignment

    rows = [r for r in roster if not r["is_mapped"] and not r.get("removed_from_device")]
    counts = {}
    if not rows:
        return counts
    owners = {}
    for assignment in (
        EmployeeAssignment.all_objects.filter(
            company_id=device.company_id, employee_code__in=[r["pin"] for r in rows])
        .exclude(status="cancelled").select_related("employee", "branch")
        .order_by("-effective_from")
    ):
        # The employee whose *current* Employee ID it is, as map_automatically.
        if assignment.employee_code not in owners and current_assignment(assignment.employee) == assignment:
            owners[assignment.employee_code] = assignment
    now = timezone.now()
    linked_as = {
        e.employee_id: e.device_user_id
        for e in DeviceEnrollment.all_objects.filter(
            device=device, employee_id__in=[a.employee_id for a in owners.values()])
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .filter(effective_from__lte=now).exclude(effective_to__lte=now)
    }
    for row in rows:
        pin = row["pin"]
        assignment = owners.get(pin)
        employee = assignment.employee if assignment else None
        if not pin.isdigit() or len(pin) > 20:
            code, text = "not_digits", (
                f"{pin} cannot be an Employee ID (digits only, at most 20). "
                "Link it by hand with Map a user.")
        elif employee is None:
            code, text = "no_employee", (
                f"No employee has Employee ID {pin}. Add them as an employee, give the right "
                f"employee Employee ID {pin} (Edit employee), or link by hand with Map a user.")
        elif assignment.branch_id != device.branch_id:
            code, text = "other_branch", (
                f"{employee.full_name} has Employee ID {pin} but works at "
                f"{assignment.branch.name}; this device is at {device.branch.name}. "
                "Move them to this branch, or use a device of theirs.")
        elif employee.pk in linked_as:
            code, text = "other_number", (
                f"{employee.full_name} has Employee ID {pin} but is already linked on this "
                f"device as user {linked_as[employee.pk]}, and one person has one number per "
                f"device. End that old link to link {pin}.")
        else:
            code, text = "ready", (
                f"{employee.full_name} has Employee ID {pin}. "
                "Press Link to existing employees.")
        row["unlinked"] = {"code": code, "text": text, "employee": employee}
        counts[code] = counts.get(code, 0) + 1
    return counts


@dataclass
class ReplaceResult:
    replaced: list = field(default_factory=list)   # (employee, old pin, new pin, start)
    skipped: list = field(default_factory=list)    # (pin, reason)
    rechecked: int | None = 0


def _first_scan_day(device, pin):
    """The company-local day this number first scanned on the device, or None."""
    from devices.models import PunchEvent

    first = (PunchEvent.all_objects.filter(device=device, device_user_id=pin)
             .order_by("punched_at_utc").values_list("punched_at_utc", flat=True).first())
    return first.astimezone(_zone(device.company)).date() if first else None


def replace_old_links(*, actor, device, pins=None):
    """Replace each "already linked under another number" link (Nihal,
    2026-09-29).

    Ajay was linked on this device as user 14 by the demo data, while the
    terminal knows him as 445962, his Employee ID. For each such user (the
    ticked ones, or all when ``pins`` is None): the old link ends, and the
    number the device uses is linked from the day it first scanned here -
    today if it never has - then that day's excluded punches are judged again
    so they count. The old link's history is kept: scans it covered stay
    theirs. Nothing is written to the device; the person is on it already.
    """
    wanted = None if pins is None else {str(p) for p in pins}
    roster = build_roster(device)
    why_not_linked(device, roster)
    result = ReplaceResult()
    earliest = None
    for row in roster:
        pin, why = row["pin"], row.get("unlinked") or {}
        if why.get("code") != "other_number" or (wanted is not None and pin not in wanted):
            continue
        employee = why["employee"]
        if not may_map(actor, device.company_id, current_assignment(employee).branch_id):
            result.skipped.append((pin, "not in a branch you may map"))
            continue
        old = live_enrollment(device, employee)
        if old is None:
            result.skipped.append((pin, "the old link has already ended"))
            continue
        first_day = _first_scan_day(device, pin)
        start = day_start(device.company, first_day) if first_day else day_start(device.company)
        if start <= old.effective_from:
            start = day_start(device.company)
        try:
            with transaction.atomic():
                old.effective_to = start
                old.updated_by = actor
                old.save(update_fields=["effective_to", "updated_by", "updated_at"])
                outcome = map_employee(actor=actor, device=device, employee=employee,
                                       start_day=start, upload=False, check_access=False)
                _audit(actor, device, "device_enrollment.replaced", outcome.enrollment, {
                    "device": device.serial_number, "employee_id": employee.pk,
                    "old_device_user_id": old.device_user_id, "old_enrollment": old.pk,
                    "device_user_id": pin, "effective_from": start.isoformat(),
                })
        except MappingError as exc:
            result.skipped.append((pin, str(exc)))
            continue
        result.replaced.append((employee, old.device_user_id, pin, start))
        earliest = start if earliest is None else min(earliest, start)
    if earliest is not None:
        result.rechecked = recheck_from(actor=actor, device=device, start=earliest)
    return result


UNASSIGNED_CODE = "UNASSIGNED"
UNASSIGNED_NAME = "Unassigned"


def unassigned_placement(branch, actor=None):
    """The branch's "Unassigned" department and designation, made on first use.

    People imported from a device are filed here so they can be employees (and
    count for attendance) at once; HR moves them to real departments later
    with Edit employee. A placement needs both, so this is what lets a company
    start from its devices before it has set up departments.
    """
    from organization.models import Department, Designation

    department = Department.all_objects.filter(branch=branch, code=UNASSIGNED_CODE).first()
    if department is None:
        department = Department(branch=branch, code=UNASSIGNED_CODE, name=UNASSIGNED_NAME,
                                description="People imported from a device, waiting for a department.",
                                created_by=actor)
        department.company_id = branch.company_id
        department.save()
    designation = Designation.all_objects.filter(department=department, code=UNASSIGNED_CODE).first()
    if designation is None:
        designation = Designation(department=department, code=UNASSIGNED_CODE, name=UNASSIGNED_NAME,
                                  created_by=actor)
        designation.company_id = branch.company_id
        designation.save()
    return department, designation


def _employee_with_id(company_id, pin):
    """The employee whose *current* Employee ID is ``pin``, or None."""
    from employees.models import EmployeeAssignment

    for assignment in (
        EmployeeAssignment.all_objects.filter(company_id=company_id, employee_code=pin)
        .exclude(status="cancelled").select_related("employee").order_by("-effective_from")
    ):
        if current_assignment(assignment.employee) == assignment:
            return assignment.employee
    return None


@dataclass
class ImportResult:
    created: list = field(default_factory=list)    # employees made from device users
    mapped: list = field(default_factory=list)     # existing employees linked
    skipped: list = field(default_factory=list)    # (pin, reason)


def import_users(*, actor, device, pins=None):
    """Turn device users into employees, linked to the device.

    For each user (all of them when ``pins`` is None): already linked → left
    alone; an employee already has that Employee ID → linked to them; otherwise
    a new employee is made — name from the device, Employee ID = the device
    number, placed in the device's branch under "Unassigned" — and linked.
    Their fingerprints and faces are kept (encrypted) so they can be copied to
    the company's other devices. No salary is set: Edit employee → Salary.
    """
    if not may_map(actor, device.company_id, device.branch_id):
        raise PermissionDenied("You may not add employees to that branch.")
    try:
        templates.save_from_messages(device)
    except templates.TemplateKeyMissing:
        pass  # people are still imported; the page says templates are not saved
    wanted = None if pins is None else {str(p) for p in pins}
    result = ImportResult()
    now = timezone.now()
    with _roster_memo():
        return _import_rows(actor, device, wanted, result, now)


def _import_rows(actor, device, wanted, result, now):
    from employees.models import Employee, EmployeeAssignment

    for row in list(_roster(device)):
        pin = row["pin"]
        if wanted is not None and pin not in wanted:
            continue
        if row.get("removed_from_device"):
            result.skipped.append((pin, "removed from the device"))
            continue
        if row["is_mapped"]:
            result.skipped.append((pin, "already linked"))
            continue
        if not pin.isdigit() or len(pin) > 20:
            result.skipped.append((pin, "the device number is not digits"))
            continue
        existing = _employee_with_id(device.company_id, pin)
        try:
            with transaction.atomic():
                if existing is None:
                    department, designation = unassigned_placement(device.branch, actor)
                    name = (row["name"] or "").strip()
                    employee = Employee(
                        first_name=name or f"Device user {pin}", last_name="",
                        employment_status=Employee.EmploymentStatus.ACTIVE,
                        metadata={"imported_from_device": device.serial_number,
                                  "device_user_id": pin, "needs_hr_review": True},
                    )
                    employee.company_id = device.company_id
                    employee.full_clean()
                    employee.save()
                    assignment = EmployeeAssignment(
                        employee=employee, employee_code=pin, branch=device.branch,
                        department=department, designation=designation,
                        effective_from=day_start(device.company),
                        change_reason=f"Imported from {device.name} (device user {pin}).",
                    )
                    assignment.company_id = device.company_id
                    assignment.full_clean()
                    assignment.save()
                else:
                    employee = existing
                map_employee(actor=actor, device=device, employee=employee, upload=False,
                             check_access=existing is not None)
        except (MappingError, ValidationError, IntegrityError, PermissionDenied) as exc:
            reason = exc.messages[0] if isinstance(exc, ValidationError) else str(exc)
            result.skipped.append((pin, reason))
            continue
        (result.mapped if existing else result.created).append(employee)
    if result.created or result.mapped:
        _audit(actor, device, "device.users_imported", device, {
            "created": [e.pk for e in result.created], "linked": [e.pk for e in result.mapped],
            "skipped": [pin for pin, _ in result.skipped],
        })
    return result


@dataclass
class SendResult:
    sent: list = field(default_factory=list)       # (employee, device)
    failed: list = field(default_factory=list)     # (employee, device or None, reason)


def send_employees(*, actor, employees, only_device=None):
    """Put employees on every active device of their branch.

    Not linked there yet → linked and sent (``map_employee``); already linked →
    their record, card, role, fingerprint and face are sent again, so a device
    that lost them or never had them gets them. Someone with no fingerprint or
    face saved goes with ID and name only; they enrol at the terminal and the
    device reports the templates back by itself.
    """
    with _roster_memo():
        return _send(actor, employees, SendResult(), only_device)


def _send(actor, employees, result, only_device=None):
    # Asked once per branch, not once per person (2026-10-01).
    devices_of, allowed = {}, {}
    for employee in employees:
        assignment = current_assignment(employee)
        if assignment is None:
            result.failed.append((employee, None, "no placement"))
            continue
        if assignment.branch_id not in allowed:
            allowed[assignment.branch_id] = may_map(actor, employee.company_id,
                                                    assignment.branch_id)
        if not allowed[assignment.branch_id]:
            result.failed.append((employee, None, "not in a branch you may manage"))
            continue
        if assignment.branch_id not in devices_of:
            devices_of[assignment.branch_id] = [
                d for d in branch_devices(employee.company_id, assignment.branch_id)
                if upload_supported(d) and (only_device is None or d.pk == only_device.pk)]
        devices = devices_of[assignment.branch_id]
        if not devices:
            result.failed.append((employee, None, f"{assignment.branch.name} has no device that takes users"))
            continue
        pin = employee_id_for(employee)
        for device in devices:
            try:
                if live_enrollment(device, employee):
                    uploaded, _, _, note = copy_to_device(actor=actor, device=device,
                                                          employee=employee, pin=pin)
                    if not uploaded:
                        raise MappingError(note)
                else:
                    outcome = map_employee(actor=actor, device=device, employee=employee,
                                           check_access=False)
                    if not outcome.uploaded and outcome.note:
                        raise MappingError(outcome.note)
            except MappingError as exc:
                result.failed.append((employee, device, str(exc)))
                continue
            result.sent.append((employee, device))
    return result


#: Why a device is left for a person to clear by hand.
NO_DELETE = "its protocol has no measured delete yet"
LAST_ADMIN = ("the device's only super admin — removing them would lock everyone out of the "
              "terminal's menu. Make someone else a super admin first.")


def super_admins(roster):
    """The numbers holding super admin on a terminal, from ``_on_device`` rows.

    One place, because two callers must agree: removing the last one leaves a
    terminal whose menu nobody can open, and only a factory reset gets it back.
    """
    return {pin for pin, row in roster.items() if str(row.get("privilege_code")) == SUPER_ADMIN}


@dataclass
class RemoveResult:
    removed: list = field(default_factory=list)     # pins
    skipped: list = field(default_factory=list)     # (pin, reason)


def remove_users(*, actor, device, pins):
    """Remove device users from the terminal, by their number only.

    The software keeps their saved fingerprint and face, so they can be sent
    back later; their enrollment (who the number is) and their punch history
    are untouched — this only takes them off the terminal.

    **The last super admin is never removed.** Deleting every administrator
    leaves a terminal nobody can open the menu on, and only a factory reset
    gets it back.
    """
    # No blanket refusal here: ``queue_user_delete`` decides per user, so a
    # protocol without a measured delete still lets the test user through —
    # which is how that form gets measured.
    if not (commands.measured(device, "delete")
            or any(str(p) == commands.TEST_USER_ID for p in pins)):
        raise MappingError(
            f"Removing users from {device.name} is not measured on its protocol yet; "
            "delete them on the terminal."
        )
    with _roster_memo():
        roster = _on_device(device)
        wanted = [p for p in dict.fromkeys(str(p) for p in pins)]
        admins = super_admins(roster)
        result = RemoveResult()
        for pin in wanted:
            row = roster.get(pin)
            if row is None:
                result.skipped.append((pin, "not on this device"))
                continue
            if pin in admins and len(admins) <= 1:
                result.skipped.append((pin, LAST_ADMIN))
                continue
            entry, error = commands.queue_user_delete(
                device=device, device_user_id=pin, requested_by=actor)
            if error:
                result.skipped.append((pin, error))
                continue
            admins.discard(pin)
            result.removed.append(pin)
    if result.removed:
        _audit(actor, device, "device.users_removed", device, {
            "device": device.serial_number, "removed": result.removed,
            "skipped": [pin for pin, _ in result.skipped],
        })
    return result


def remove_on_leaving(*, actor, employee, at=None):
    """Take a leaver off the terminals they are on.

    Ending employment already stops their scans counting; this takes their
    face and fingerprint off the device as well, so an ex-employee cannot open
    the door. The copies saved on the server stay, so someone who returns can
    be put back. A device whose protocol has no measured delete (the 3A) is
    listed for a human to do on the terminal instead of pretending.

    **The last super admin is never removed**, exactly as in ``remove_users``:
    a leaver who is the only administrator of a terminal is left on it and
    named, because deleting them leaves a menu nobody can open (Nihal found
    this path skipped the guard, 2026-09-21).

    Returns ``(queued, manual)``: the devices it was sent to, and the
    ``(device, pin, reason)`` triples that must be done by hand.
    """
    at = at or timezone.now()
    queued, manual = [], []
    rows = (
        DeviceEnrollment.all_objects.filter(employee=employee)
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .select_related("device", "device__branch").order_by("device__name")
    )
    seen = set()
    with _roster_memo():
        for enrollment in rows:
            key = (enrollment.device_id, enrollment.device_user_id)
            if key in seen:
                continue
            seen.add(key)
            device, pin = enrollment.device, enrollment.device_user_id
            if device.status == BiometricDevice.Status.RETIRED:
                continue
            if not commands.measured(device, "delete"):
                manual.append((device, pin, NO_DELETE))
                continue
            roster = _on_device(device)
            # An empty roster means the device has never sent its users, not
            # that it holds nobody: only judge what it has actually reported.
            if roster and pin not in roster:
                continue  # already gone from the terminal; nothing to send
            admins = super_admins(roster)
            if pin in admins and len(admins) <= 1:
                manual.append((device, pin, LAST_ADMIN))
                continue
            entry, error = commands.queue_user_delete(
                device=device, device_user_id=pin, requested_by=actor)
            if entry is None:
                manual.append((device, pin, error or NO_DELETE))
                continue
            queued.append((device, pin))
    if queued or manual:
        _audit(actor, queued[0][0] if queued else manual[0][0],
               "device.users_removed_on_leaving", employee, {
            "employee_id": employee.pk,
            "removed": [f"{d.serial_number}:{pin}" for d, pin in queued],
            "by_hand": [f"{d.serial_number}:{pin}" for d, pin, _ in manual],
        })
    return queued, manual


#: What an enrollment's privilege means to the terminal. ``OTHER`` is
#: deliberately absent: it means "whatever the device already has", so a role
#: the software has no word for is never overwritten by a name change.
PRIVILEGE_CODES = {
    DeviceEnrollment.Privilege.NORMAL_USER: 0,
    DeviceEnrollment.Privilege.DEVICE_ADMIN: int(SUPER_ADMIN),
}


@dataclass
class ResendResult:
    sent: list = field(default_factory=list)      # (device, pin)
    failed: list = field(default_factory=list)    # (device, pin, reason)


def device_role(enrollment, device):
    """The privilege number to write for this enrollment.

    The software's own word for it when it has one; otherwise what the device
    last reported for that number, so re-sending a person's name never
    silently demotes an administrator to a normal user.
    """
    code = PRIVILEGE_CODES.get(enrollment.device_privilege)
    if code is not None:
        return code
    reported = (_on_device(device).get(enrollment.device_user_id) or {}).get("privilege_code")
    reported = int(reported) if str(reported or "").isdigit() else 0
    return reported if reported in commands.ROLE_PRIVILEGES else 0


def resend_identity(*, actor, employee, only_device=None):
    """Send a person's number, name, card and role to the terminals again.

    For a changed name, card or role: the device keys people on the number, so
    writing the record again updates the person in place — their fingerprint
    and face are untouched, and nothing is duplicated. Templates are not
    re-sent; the device already has them.

    A device whose protocol has no measured user write is reported, not
    pretended at. Returns a ResendResult.
    """
    result = ResendResult()
    rows = (
        DeviceEnrollment.all_objects.filter(employee=employee)
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .select_related("device").order_by("device__name")
    )
    seen = set()
    with _roster_memo():
        for enrollment in rows:
            device, pin = enrollment.device, enrollment.device_user_id
            if only_device is not None and device.pk != only_device.pk:
                continue
            if device.status == BiometricDevice.Status.RETIRED or (device.pk, pin) in seen:
                continue
            seen.add((device.pk, pin))
            entry, error = commands.queue_user_push(
                device=device, device_user_id=pin, name=employee.full_name,
                card_number=enrollment.card_number,
                privilege=device_role(enrollment, device), requested_by=actor,
            )
            if entry is None:
                result.failed.append((device, pin, error))
                continue
            result.sent.append((device, pin))
    if result.sent:
        _audit(actor, result.sent[0][0], "device.identity_resent", employee, {
            "employee_id": employee.pk,
            "sent": [f"{d.serial_number}:{pin}" for d, pin in result.sent],
            "failed": [f"{d.serial_number}:{pin}" for d, pin, _ in result.failed],
        })
    return result


def still_on_devices(employee, at=None):
    """``[(device, pin)]`` a person is still on, for the "still on N devices" note."""
    at = at or timezone.now()
    return [
        (e.device, e.device_user_id)
        for e in DeviceEnrollment.all_objects.filter(employee=employee)
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .select_related("device").order_by("device__name")
        if e.device.status != BiometricDevice.Status.RETIRED
    ]


def transfer_targets(device):
    """The company's other devices of the same model: where users can be copied."""
    return list(
        BiometricDevice.all_objects.filter(company_id=device.company_id,
                                           device_model_id=device.device_model_id)
        .exclude(pk=device.pk)
        .exclude(status__in=[BiometricDevice.Status.RETIRED, BiometricDevice.Status.SUSPENDED])
        .select_related("branch").order_by("name")
    )


@dataclass
class TransferResult:
    sent: list = field(default_factory=list)        # pins
    fingerprints: int = 0
    faces: int = 0
    mapped: list = field(default_factory=list)      # employees mapped on the target
    failed: list = field(default_factory=list)      # (pin, reason)


def transfer_users(*, actor, source, target, pins):
    """Copy device users (record, card, role, fingerprint, face) from one device to another.

    Only between devices of the same model and company: a template only works
    on the model that captured it. A user mapped to an employee on the source
    is mapped on the target too when the target is in that employee's branch.
    """
    if target.company_id != source.company_id or target.pk == source.pk:
        raise MappingError("Choose another device of this company.")
    if target.device_model_id != source.device_model_id:
        raise MappingError(
            f"{target.name} is a {target.device_model}; fingerprints and faces only transfer "
            f"between devices of the same model ({source.device_model})."
        )
    if target.status in (BiometricDevice.Status.RETIRED, BiometricDevice.Status.SUSPENDED):
        raise MappingError(f"{target.name} is {target.get_status_display().lower()}.")
    if not upload_supported(target):
        raise MappingError(
            f"Writing users to {target.name} is not measured on its protocol yet; "
            "add them on the terminal."
        )
    try:
        templates.save_from_messages(source)
    except templates.TemplateKeyMissing as exc:
        raise MappingError(str(exc)) from exc

    with _roster_memo():
        return _transfer(actor, source, target, pins)


def _transfer(actor, source, target, pins):
    roster = _on_device(source)
    result = TransferResult()
    for pin in dict.fromkeys(str(p) for p in pins):
        row = roster.get(pin)
        if row is None:
            result.failed.append((pin, "not on the source device"))
            continue
        rows = DeviceUserTemplate.all_objects.filter(device=source, device_user_id=pin).order_by("number")
        finger = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FINGERPRINT), None)
        face = next((r for r in rows if r.bio_type == DeviceUserTemplate.BioType.FACE), None)
        role = int(row["privilege_code"]) if str(row["privilege_code"]).isdigit() else 0
        _, error = commands.push_to_device(
            target, pin, name=row["name"], card=row["card_number"] if row["card_number"] != "0" else "",
            role=role if role in commands.ROLE_PRIVILEGES else 0,
            finger_template=templates.as_payload(finger) if finger else None,
            face_template=templates.as_payload(face) if face else None,
            requested_by=actor,
        )
        if error:
            result.failed.append((pin, error))
            continue
        result.sent.append(pin)
        result.fingerprints += bool(finger)
        result.faces += bool(face)
        enrollment = row.get("enrollment")
        if enrollment and not live_enrollment(target, enrollment.employee):
            employee = enrollment.employee
            assignment = current_assignment(employee)
            if assignment and assignment.branch_id == target.branch_id and employee_id_for(employee) == pin:
                try:
                    map_employee(actor=actor, device=target, employee=employee, upload=False,
                                 attendance_enabled=enrollment.attendance_enabled,
                                 assigned=enrollment.assigned_device_authorized, check_access=False)
                    result.mapped.append(employee)
                except MappingError:
                    pass
    _audit(actor, source, "device.users_transferred", source, {
        "from": source.serial_number, "to": target.serial_number, "sent": result.sent,
        "failed": [pin for pin, _ in result.failed],
        "fingerprints": result.fingerprints, "faces": result.faces,
    })
    return result


def removed_on_terminal(device, memo=None):
    """The numbers deleted on the terminal itself, as the Device users page
    marks them (``removed_from_device``): absent from the device's latest
    complete user list. A device does not report such a delete by itself; the
    next "Refresh user list" shows it. ``memo`` (a dict) reads each device once.
    """
    from devices.services.device_roster import _removed_pins

    if memo is None:
        return _removed_pins(device)
    if device.pk not in memo:
        memo[device.pk] = _removed_pins(device)
    return memo[device.pk]


def device_badges(company_id, employee_ids):
    """``{employee_id: {"devices": n, "names": [...], "removed": [...], "fingerprint": bool,
    "face": bool}}``.

    ``names`` are the devices the employee is linked on, with their device
    number. A link whose person was deleted on the terminal (Nihal,
    2026-10-01) is not counted: it goes in ``removed``, so the list says "Not
    on any device", as it did before they were sent there.
    """
    now = timezone.now()
    badges = {pk: {"devices": 0, "names": [], "removed": [], "fingerprint": False, "face": False}
              for pk in employee_ids}
    pins, removed = {}, {}
    for enrollment in (
        DeviceEnrollment.all_objects.filter(company_id=company_id, employee_id__in=employee_ids)
        .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
        .filter(effective_from__lte=now).exclude(effective_to__lte=now)
        .select_related("device").order_by("device__name")
    ):
        if enrollment.device_user_id in removed_on_terminal(enrollment.device, removed):
            badges[enrollment.employee_id]["removed"].append(
                {"name": enrollment.device.name, "pin": enrollment.device_user_id})
            continue
        badges[enrollment.employee_id]["devices"] += 1
        badges[enrollment.employee_id]["names"].append(
            {"name": enrollment.device.name, "pin": enrollment.device_user_id})
        pins.setdefault(enrollment.device_user_id, set()).add(enrollment.employee_id)
    for pin, bio_type in DeviceUserTemplate.all_objects.filter(
        company_id=company_id, device_user_id__in=list(pins)
    ).values_list("device_user_id", "bio_type"):
        for pk in pins.get(pin, ()):
            if bio_type in ("fingerprint", "face"):
                badges[pk][bio_type] = True
    return badges


def branch_devices(company_id, branch_id):
    return list(
        BiometricDevice.all_objects.filter(company_id=company_id, branch_id=branch_id)
        .exclude(status__in=[BiometricDevice.Status.RETIRED, BiometricDevice.Status.SUSPENDED])
        .order_by("name")
    )


def upload_supported(device):
    """Whether writing a user to this device has been measured on real hardware.

    Was "anything but 2.x" until the 3A's user and template writes were read
    back off the device itself (2026-09-19/20); now it asks the one place that
    records what has been measured, so a protocol proven there is usable here.
    """
    return commands.measured(device, "user")


def branch_employees(device):
    """Active employees placed in the device's branch (who belong on it).

    Each one's current placement, as ``current_assignment`` picks it, read in
    one query for the whole company rather than one per person (2026-10-01).
    """
    from employees.models import Employee, EmployeeAssignment

    branch_of = {}
    for employee_id, branch_id in (
        EmployeeAssignment.all_objects.filter(company_id=device.company_id)
        .exclude(status="cancelled").order_by("employee_id", "-effective_from", "-pk")
        .values_list("employee_id", "branch_id")
    ):
        branch_of.setdefault(employee_id, branch_id)
    ids = [pk for pk, branch_id in branch_of.items() if branch_id == device.branch_id]
    return list(
        Employee.all_objects.filter(company_id=device.company_id, pk__in=ids)
        .exclude(employment_status__in=["resigned", "terminated"])
        .order_by("first_name", "last_name")
    )


def load_device(*, actor, device):
    """Put every employee of the device's branch on it (a new or replaced device).

    Each goes with Employee ID, name, card, role and door permission, plus the
    fingerprint and face the company keeps for them from any device of this
    model; people with none saved go with ID and name, to enrol at the terminal.
    """
    if not may_map(actor, device.company_id, device.branch_id):
        raise PermissionDenied("You may not manage that branch.")
    if not upload_supported(device):
        raise MappingError(f"Writing users to {device.name} is not measured on its protocol yet.")
    return send_employees(actor=actor, employees=branch_employees(device), only_device=device)
