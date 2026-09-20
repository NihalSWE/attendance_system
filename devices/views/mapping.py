"""Map employees to devices: the Employees list dialogs and Device users.

Thin views over devices/services/mapping.py, which checks who may map whom.
"""

import datetime

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from devices.models import BiometricDevice
from devices.services import mapping
from devices.views.ui import company_user_required


def _back(request, default="employee_list"):
    target = request.POST.get("next", "")
    if target and url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}):
        return redirect(target)
    return redirect(default)


def _day(request):
    value = (request.POST.get("start_day") or "").strip()
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _switches(request):
    return {
        "attendance_enabled": request.POST.get("attendance_enabled") == "on",
        "assigned": request.POST.get("assigned") == "on",
    }


def _describe(outcome):
    parts = [f"{outcome.employee.full_name} mapped on {outcome.device.name} "
             f"as Employee ID {outcome.enrollment.device_user_id}."]
    if outcome.uploaded:
        carried = [k for k, v in (("fingerprint", outcome.fingerprint), ("face", outcome.face)) if v]
        parts.append(
            "Sent to the device" + (f" with their {' and '.join(carried)}" if carried else
                                    " (no fingerprint or face saved yet: enrol them at the terminal)")
            + "; it applies on its next check-in."
        )
    elif outcome.note:
        parts.append(f"Not sent to the device: {outcome.note}")
    if outcome.rechecked:
        parts.append(f"{outcome.rechecked} earlier scan(s) now count.")
    elif outcome.rechecked is None:
        parts.append("Scans before today: an administrator can re-check them on "
                     "Devices → Which devices count.")
    return " ".join(parts)


@require_POST
@login_required
def employee_map(request):
    """Employees list → Map: one employee on one device of their branch."""
    from employees.models import Employee

    employee = get_object_or_404(Employee.objects, pk=request.POST.get("employee"))
    device = get_object_or_404(BiometricDevice.objects, pk=request.POST.get("device"))
    try:
        outcome = mapping.map_one(actor=request.user, device=device, employee=employee,
                                  start_day=_day(request), **_switches(request))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
    except mapping.MappingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, _describe(outcome))
    return _back(request)


@require_POST
@login_required
def employee_bulk_map(request):
    """Employees list → Bulk map: every active employee of one branch."""
    from organization.models import Branch

    branch = get_object_or_404(Branch.objects, pk=request.POST.get("branch"))
    device = None
    if request.POST.get("device"):
        device = get_object_or_404(BiometricDevice.objects, pk=request.POST["device"], branch=branch)
    try:
        result = mapping.map_branch(actor=request.user, branch=branch, device=device,
                                    start_day=_day(request), **_switches(request))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return _back(request)
    except mapping.MappingError as exc:
        messages.error(request, str(exc))
        return _back(request)

    sent = sum(1 for o in result.mapped if o.uploaded)
    summary = (f"{branch.name}: mapped {len(result.mapped)}, {sent} sent to the device"
               f"{'s' if not device else ''}; {len(result.skipped)} already mapped.")
    if result.rechecked:
        summary += f" {result.rechecked} earlier scan(s) now count."
    messages.success(request, summary)
    for employee, target, reason in result.failed[:10]:
        messages.warning(request, f"{employee.full_name} on {target.name}: {reason}")
    if len(result.failed) > 10:
        messages.warning(request, f"…and {len(result.failed) - 10} more could not be mapped.")
    return _back(request)


@require_POST
@login_required
@company_user_required
def device_users_transfer(request, public_id):
    """Device users → Copy to another device: the ticked users, or all of them."""
    from devices.services.device_roster import build_roster

    source = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    back = redirect("devices:device_users", public_id=source.public_id)
    target = BiometricDevice.objects.filter(pk=request.POST.get("target") or 0).first()
    if target is None:
        messages.error(request, "Choose the device to copy to.")
        return back
    if request.POST.get("all") == "1":
        pins = [row["pin"] for row in build_roster(source)
                if not row.get("removed_from_device") and not row.get("only_in_scans")]
    else:
        pins = request.POST.getlist("pin")
    if not pins:
        messages.error(request, "Tick at least one user to copy.")
        return back
    try:
        result = mapping.transfer_users(actor=request.user, source=source, target=target, pins=pins)
    except mapping.MappingError as exc:
        messages.error(request, str(exc))
        return back
    if result.sent:
        messages.success(
            request,
            f"Copying {len(result.sent)} user(s) to {target.name} "
            f"({result.fingerprints} fingerprint(s), {result.faces} face(s)); "
            "it takes them a few per check-in. Progress is under Commands and answers on "
            f"{target.name}'s Users page."
            + (f" Also mapped {len(result.mapped)} employee(s) there." if result.mapped else ""),
        )
    for pin, reason in result.failed[:10]:
        messages.warning(request, f"{pin}: {reason}")
    return back


@require_POST
@login_required
@company_user_required
def device_map_automatically(request, public_id):
    """Device users → Map automatically: device numbers that are Employee IDs."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    result = mapping.map_automatically(actor=request.user, device=device, pins=_picked(request))
    if result.mapped:
        messages.success(request, f"Mapped {len(result.mapped)} device user(s) by Employee ID: "
                         + ", ".join(f"{o.enrollment.device_user_id} → {o.employee.full_name}"
                                     for o in result.mapped[:10]))
    if result.skipped:
        messages.info(request, f"{len(result.skipped)} number(s) left unmapped: "
                      + ", ".join(f"{pin} ({reason})" for pin, _, reason in result.skipped[:10]))
    for employee, _, reason in result.failed[:10]:
        messages.warning(request, f"{employee.full_name}: {reason}")
    if not (result.mapped or result.skipped or result.failed):
        messages.info(request, "Every user on this device is already mapped.")
    return redirect("devices:device_users", public_id=device.public_id)


def _picked(request):
    """The ticked device user numbers, or None for "every user" (all=1 or none sent)."""
    if request.POST.get("all") == "1":
        return None
    return request.POST.getlist("pin") or None


@require_POST
@login_required
@company_user_required
def device_users_import(request, public_id):
    """Device users → Import as employees: the ticked users, or everyone."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    back = redirect("devices:device_users", public_id=device.public_id)
    try:
        result = mapping.import_users(actor=request.user, device=device, pins=_picked(request))
    except PermissionDenied as exc:
        messages.error(request, str(exc))
        return back
    if result.created:
        messages.success(
            request,
            f"Added {len(result.created)} employee(s) from {device.name}, each linked under "
            "their device number as Employee ID and filed under \"Unassigned\". "
            "Give them a department and salary from Employees → Edit when you are ready.",
        )
    if result.mapped:
        messages.success(request, f"Linked {len(result.mapped)} existing employee(s) by Employee ID.")
    skipped = [(pin, reason) for pin, reason in result.skipped if reason != "already linked"]
    if skipped:
        messages.info(request, f"{len(skipped)} left as they are: "
                      + ", ".join(f"{pin} ({reason})" for pin, reason in skipped[:10]))
    if not (result.created or result.mapped or skipped):
        messages.info(request, "Everyone on this device is already an employee.")
    return back


@require_POST
@login_required
def employees_send(request):
    """Employees list → Send to devices: the ticked employees, to their branch's devices."""
    from employees.models import Employee

    if request.POST.get("all") == "1":
        # Everyone the viewer may put on a device (the service checks each).
        employees = list(Employee.objects.exclude(employment_status__in=["resigned", "terminated"]))
    else:
        ids = [pk for pk in request.POST.getlist("employee") if pk.isdigit()]
        employees = list(Employee.objects.filter(pk__in=ids))
    if not employees:
        messages.error(request, "Tick at least one employee.")
        return _back(request)
    result = mapping.send_employees(actor=request.user, employees=employees)
    people = {employee.pk for employee, _ in result.sent}
    devices = {device.pk for _, device in result.sent}
    if result.sent:
        messages.success(
            request,
            f"Sending {len(people)} employee(s) to {len(devices)} device(s). They appear on the "
            "terminal within a minute or two; anyone without a fingerprint or face saved goes with "
            "ID and name only — enrol them at the terminal and the device reports it back.",
        )
    for employee, device, reason in result.failed[:10]:
        messages.warning(request, f"{employee.full_name}{' on ' + device.name if device else ''}: {reason}")
    return _back(request)


@require_POST
@login_required
@company_user_required
def device_load(request, public_id):
    """Device users → Load employees onto this device (a new or replaced device)."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    back = redirect("devices:device_users", public_id=device.public_id)
    try:
        result = mapping.load_device(actor=request.user, device=device)
    except (PermissionDenied, mapping.MappingError) as exc:
        messages.error(request, str(exc))
        return back
    if result.sent:
        with_bio = sum(1 for employee, _ in result.sent
                       if any(mapping._source_templates(device, mapping.employee_id_for(employee))))
        messages.success(
            request,
            f"Loading {len(result.sent)} employee(s) onto {device.name}: {with_bio} with their saved "
            "fingerprint/face, the rest with Employee ID and name to enrol at the terminal. "
            "The device takes a few per check-in; follow it under Commands and answers.",
        )
    elif not result.failed:
        messages.info(request, f"{device.branch.name} has no active employees to load.")
    for employee, _, reason in result.failed[:10]:
        messages.warning(request, f"{employee.full_name}: {reason}")
    return back


@require_POST
@login_required
@company_user_required
def device_users_remove(request, public_id):
    """Device users → Remove from device: the ticked users, or all of them.

    The last super admin is kept whatever is ticked (the service refuses it).
    """
    from devices.services.device_roster import build_roster

    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    back = redirect("devices:device_users", public_id=device.public_id)
    pins = _picked(request)
    if pins is None:
        pins = [row["pin"] for row in build_roster(device)
                if not row.get("removed_from_device") and not row.get("only_in_scans")]
    if not pins:
        messages.error(request, "Tick at least one user to remove.")
        return back
    try:
        result = mapping.remove_users(actor=request.user, device=device, pins=pins)
    except mapping.MappingError as exc:
        messages.error(request, str(exc))
        return back
    if result.removed:
        messages.success(
            request,
            f"Removing {len(result.removed)} user(s) from {device.name}; the device takes a few "
            "per check-in. Their fingerprints and faces stay saved here, so they can be sent back.",
        )
    for pin, reason in result.skipped[:10]:
        messages.warning(request, f"{pin} kept: {reason}")
    return back


@login_required
@company_user_required
def device_job_progress(request, public_id):
    """JSON for the progress card: how the device's queued writes are going."""
    from devices.services import commands as command_service

    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    return JsonResponse(command_service.job_progress(device))


@require_POST
@login_required
@company_user_required
def device_user_query(request, public_id):
    """Ask the device to send one user back, so a write can be checked."""
    from devices.services import commands as command_service

    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    entry, error = command_service.queue_user_query(
        device=device, device_user_id=request.POST.get("device_user_id", ""),
        requested_by=request.user)
    if error:
        messages.error(request, error)
    else:
        messages.success(
            request,
            f"Asked {device.name} for user {request.POST.get('device_user_id')}. It answers on its "
            "next check-in; reload in a few seconds and compare what it reports.",
        )
    return redirect("devices:device_users", public_id=device.public_id)
