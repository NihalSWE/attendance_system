"""Administrator screens for devices, enrollment and troubleshooting.

Every page is tenant-scoped by construction: TenantMiddleware sets the active
company and the TenantOwned managers filter each query, so a view that forgot
would raise rather than leak another company's rows.

Nothing here offers an edit or delete control for DeviceMessage or PunchEvent.
They are append-only evidence; the screens read them and explain them.
"""

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.services import get_active_memberships
from auditlog.models import AuditLog
from devices.forms import (
    BiometricDeviceForm,
    DeviceDepartmentForm,
    DeviceEnrollmentForm,
)
from devices.models import (
    BiometricDevice,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceMessage,
    DeviceSyncState,
    PunchEvent,
)
from devices.services import setup_instructions
from devices.services.commands import (
    COMMAND_LABELS,
    SAFE_COMMANDS,
    pending_summary,
    queue_command,
)
from devices.services.device_roster import build_roster

# Punch states that need a human before they can feed attendance.
UNRESOLVED_STATUSES = (
    PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
    PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
    PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED,
)


def company_user_required(view):
    """Mirror of the panel's access rule: fail closed for restricted roles."""

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("platform:company_list")
        if not request.company_id:
            return render(request, "base_template/no_company.html", status=200)
        member = get_active_memberships(request.user).filter(
            company_id=request.company_id
        ).first()
        if (
            not member
            or member.role not in ("owner", "company_admin")
            or member.allowed_branches.exists()
            or member.allowed_departments.exists()
        ):
            raise PermissionDenied(
                "Device management currently requires unrestricted company "
                "administrator access."
            )
        return view(request, *args, **kwargs)

    return wrapped


def _paginate(request, queryset, default_per_page=25):
    try:
        per_page = min(int(request.GET.get("per_page", default_per_page)), 100)
    except (TypeError, ValueError):
        per_page = default_per_page
    paginator = Paginator(queryset, per_page)
    page = paginator.get_page(request.GET.get("page"))
    return paginator, page, per_page


def _audit(request, action, obj, before=None, after=None):
    """Record a policy change so historical punches stay reconstructable.

    before_data must carry every field this change touched — the historical
    resolution in devices/services/policy_history.py depends on that contract.
    """
    return AuditLog.objects.create(
        company_id=request.company_id,
        actor_user=request.user,
        actor_type=AuditLog.ActorType.USER,
        action=action,
        object_app=obj._meta.app_label,
        object_model=obj._meta.model_name,
        object_id=str(obj.pk),
        object_public_id=str(getattr(obj, "public_id", "")),
        object_display=str(obj)[:255],
        before_data=before or {},
        after_data=after or {},
        ip_address=request.META.get("REMOTE_ADDR"),
    )


# --------------------------------------------------------------------------
# Devices
# --------------------------------------------------------------------------


@login_required
@company_user_required
def device_list(request):
    queryset = (
        BiometricDevice.objects.select_related("branch", "device_model__vendor", "sync_state")
        .order_by("name")
    )

    search = request.GET.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(name__icontains=search)
            | Q(serial_number__icontains=search)
            | Q(external_device_id__icontains=search)
        )

    status = request.GET.get("status", "").strip()
    if status:
        queryset = queryset.filter(status=status)

    branch = request.GET.get("branch", "").strip()
    if branch.isdigit():
        queryset = queryset.filter(branch_id=int(branch))

    paginator, page, per_page = _paginate(request, queryset)

    return render(request, "devices/device_list.html", {
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "search": search,
        "status": status,
        "branch": branch,
        "statuses": BiometricDevice.Status.choices,
        "branches": _branch_options(),
        "total_count": paginator.count,
    })


def _branch_options():
    from organization.models import Branch

    return Branch.objects.order_by("name")


@login_required
@company_user_required
def device_register(request):
    form = BiometricDeviceForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        device = form.save()
        _audit(request, "device.registered", device, after={
            "name": device.name,
            "serial_number": device.serial_number,
            "status": device.status,
        })
        if form.issued_comm_key:
            # Carried in the session for exactly one render: the plaintext is
            # never stored, so this is the only chance to show it.
            request.session["issued_comm_key"] = form.issued_comm_key
            request.session["issued_comm_key_device"] = str(device.public_id)
        messages.success(
            request,
            f"{device.name} registered. Enter the settings below on the device.",
        )
        return redirect("devices:device_detail", public_id=device.public_id)

    return render(request, "devices/device_form.html", {
        "form": form,
        "title": "Register a device",
        "submit_label": "Register device",
    })


@login_required
@company_user_required
def device_detail(request, public_id):
    device = get_object_or_404(
        BiometricDevice.objects.select_related("branch", "device_model__vendor"),
        public_id=public_id,
    )
    sync_state = DeviceSyncState.objects.filter(device=device).first()

    # Shown once, immediately after it was issued.
    comm_key = None
    if request.session.get("issued_comm_key_device") == str(device.public_id):
        comm_key = request.session.pop("issued_comm_key", None)
        request.session.pop("issued_comm_key_device", None)

    _, host, _ = setup_instructions.server_address(request)

    recent_messages = (
        DeviceMessage.objects.filter(device=device)
        .order_by("-received_at")[:10]
    )
    punch_counts = {
        "total": PunchEvent.objects.filter(device=device).count(),
        "authorized": PunchEvent.objects.filter(
            device=device,
            authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
        ).count(),
        "needs_review": PunchEvent.objects.filter(
            device=device,
            processing_status=PunchEvent.ProcessingStatus.NEEDS_REVIEW,
        ).count(),
    }

    return render(request, "devices/device_detail.html", {
        "device": device,
        "sync_state": sync_state,
        "setup_lines": setup_instructions.build(request, device, comm_key=comm_key),
        "endpoint_url": setup_instructions.full_endpoint(request),
        "host_unreachable": setup_instructions.is_unreachable_host(host),
        "comm_key": comm_key,
        "recent_messages": recent_messages,
        "punch_counts": punch_counts,
        "department_links": DeviceDepartment.objects.filter(device=device)
        .select_related("department")
        .order_by("-effective_from"),
        "enrollment_count": DeviceEnrollment.objects.filter(device=device).count(),
        "command_options": [(k, COMMAND_LABELS[k]) for k in SAFE_COMMANDS],
        "pending_commands": pending_summary(device),
    })


@login_required
@company_user_required
def device_edit(request, public_id):
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    before = {
        "name": device.name,
        "serial_number": device.serial_number,
        "status": device.status,
        "timezone": device.timezone,
        "branch": device.branch_id,
    }
    form = BiometricDeviceForm(request.POST or None, instance=device)
    if request.method == "POST" and form.is_valid():
        device = form.save()
        _audit(request, "device.updated", device, before=before, after={
            "name": device.name,
            "serial_number": device.serial_number,
            "status": device.status,
            "timezone": device.timezone,
            "branch": device.branch_id,
        })
        if form.issued_comm_key:
            request.session["issued_comm_key"] = form.issued_comm_key
            request.session["issued_comm_key_device"] = str(device.public_id)
        messages.success(request, f"{device.name} updated.")
        return redirect("devices:device_detail", public_id=device.public_id)

    return render(request, "devices/device_form.html", {
        "form": form,
        "device": device,
        "title": f"Edit {device.name}",
        "submit_label": "Save changes",
    })


@require_POST
@login_required
@company_user_required
def device_retire(request, public_id):
    """Retire a device without deleting any of its history."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    before = {"status": device.status, "decommissioned_at": None}
    device.status = BiometricDevice.Status.RETIRED
    device.decommissioned_at = timezone.now()
    device.save(update_fields=["status", "decommissioned_at", "updated_at"])
    _audit(request, "device.retired", device, before=before, after={
        "status": device.status,
        "decommissioned_at": device.decommissioned_at.isoformat(),
    })
    messages.success(
        request,
        f"{device.name} retired. It can no longer send data; its punch history "
        "is unchanged.",
    )
    return redirect("devices:device_detail", public_id=device.public_id)


# --------------------------------------------------------------------------
# Device/department mapping
# --------------------------------------------------------------------------


@login_required
@company_user_required
def device_department_add(request, public_id):
    device = get_object_or_404(
        BiometricDevice.objects.select_related("branch"), public_id=public_id
    )
    form = DeviceDepartmentForm(request.POST or None, device=device)
    if request.method == "POST" and form.is_valid():
        link = form.save(commit=False)
        link.device = device
        link.company_id = request.company_id
        link.save()
        _audit(request, "device_department.created", link, after={
            "device": device.pk,
            "department": link.department_id,
            "effective_from": link.effective_from.isoformat(),
        })
        messages.success(
            request,
            f"{device.name} now serves {link.department.name} in "
            "department-devices mode.",
        )
        return redirect("devices:device_detail", public_id=device.public_id)

    return render(request, "devices/device_department_form.html", {
        "form": form,
        "device": device,
        "title": f"Map {device.name} to a department",
        "submit_label": "Add mapping",
    })


@require_POST
@login_required
@company_user_required
def device_department_end(request, pk):
    link = get_object_or_404(
        DeviceDepartment.objects.select_related("device", "department"), pk=pk
    )
    before = {"status": link.status, "effective_to": None}
    link.effective_to = timezone.now()
    link.status = DeviceDepartment.Status.ENDED
    link.save(update_fields=["effective_to", "status", "updated_at"])
    _audit(request, "device_department.ended", link, before=before, after={
        "status": link.status,
        "effective_to": link.effective_to.isoformat(),
    })
    messages.success(request, f"Mapping to {link.department.name} ended.")
    return redirect("devices:device_detail", public_id=link.device.public_id)


# --------------------------------------------------------------------------
# Enrollment
# --------------------------------------------------------------------------


@login_required
@company_user_required
def enrollment_list(request):
    queryset = (
        DeviceEnrollment.objects.select_related("device", "employee")
        .order_by("-effective_from")
    )

    search = request.GET.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(device_user_id__icontains=search)
            | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
            | Q(device__name__icontains=search)
        )

    device = request.GET.get("device", "").strip()
    if device.isdigit():
        queryset = queryset.filter(device_id=int(device))

    paginator, page, per_page = _paginate(request, queryset)

    return render(request, "devices/enrollment_list.html", {
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "search": search,
        "device": device,
        "devices": BiometricDevice.objects.order_by("name"),
        "total_count": paginator.count,
    })


@login_required
@company_user_required
def enrollment_create(request):
    form = DeviceEnrollmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        enrollment = form.save(commit=False)
        enrollment.company_id = request.company_id
        enrollment.save()
        _audit(request, "device_enrollment.created", enrollment, after={
            "device": enrollment.device_id,
            "employee": enrollment.employee_id,
            "device_user_id": enrollment.device_user_id,
            "attendance_enabled": enrollment.attendance_enabled,
            "assigned_device_authorized": enrollment.assigned_device_authorized,
        })
        messages.success(
            request,
            f"{enrollment.employee} enrolled on {enrollment.device} as user "
            f"{enrollment.device_user_id}.",
        )
        return redirect("devices:enrollment_list")

    return render(request, "devices/enrollment_form.html", {
        "form": form,
        "title": "Enroll an employee on a device",
        "submit_label": "Create enrollment",
    })


@login_required
@company_user_required
def enrollment_edit(request, pk):
    enrollment = get_object_or_404(
        DeviceEnrollment.objects.select_related("device", "employee"), pk=pk
    )
    # Both policy booleans are captured, because historical resolution reads
    # before_data to decide what applied when an offline punch happened.
    before = {
        "attendance_enabled": enrollment.attendance_enabled,
        "assigned_device_authorized": enrollment.assigned_device_authorized,
        "device_user_id": enrollment.device_user_id,
        "effective_from": enrollment.effective_from.isoformat(),
        "effective_to": (
            enrollment.effective_to.isoformat() if enrollment.effective_to else None
        ),
    }
    form = DeviceEnrollmentForm(request.POST or None, instance=enrollment)
    if request.method == "POST" and form.is_valid():
        enrollment = form.save()
        _audit(request, "device_enrollment.updated", enrollment, before=before, after={
            "attendance_enabled": enrollment.attendance_enabled,
            "assigned_device_authorized": enrollment.assigned_device_authorized,
            "device_user_id": enrollment.device_user_id,
            "effective_from": enrollment.effective_from.isoformat(),
            "effective_to": (
                enrollment.effective_to.isoformat()
                if enrollment.effective_to
                else None
            ),
        })
        messages.success(request, "Enrollment updated.")
        return redirect("devices:enrollment_list")

    return render(request, "devices/enrollment_form.html", {
        "form": form,
        "enrollment": enrollment,
        "title": f"Edit enrollment for {enrollment.employee}",
        "submit_label": "Save changes",
    })


# --------------------------------------------------------------------------
# Troubleshooting
# --------------------------------------------------------------------------


@login_required
@company_user_required
def message_list(request):
    queryset = (
        DeviceMessage.objects.select_related("device")
        .order_by("-received_at")
    )

    device = request.GET.get("device", "").strip()
    if device.isdigit():
        queryset = queryset.filter(device_id=int(device))

    status = request.GET.get("status", "").strip()
    if status:
        queryset = queryset.filter(processing_status=status)

    paginator, page, per_page = _paginate(request, queryset, default_per_page=50)

    return render(request, "devices/message_list.html", {
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "device": device,
        "status": status,
        "statuses": DeviceMessage.ProcessingStatus.choices,
        "devices": BiometricDevice.objects.order_by("name"),
        "total_count": paginator.count,
    })


@login_required
@company_user_required
def message_detail(request, public_id):
    message = get_object_or_404(
        DeviceMessage.objects.select_related("device", "branch"), public_id=public_id
    )
    return render(request, "devices/message_detail.html", {
        "message": message,
        "punches": PunchEvent.objects.filter(device_message=message).order_by(
            "source_record_index"
        ),
    })


@login_required
@company_user_required
def punch_list(request):
    queryset = (
        PunchEvent.objects.select_related("device", "employee", "device_message")
        .order_by("-punched_at_utc")
    )

    device = request.GET.get("device", "").strip()
    if device.isdigit():
        queryset = queryset.filter(device_id=int(device))

    authorization = request.GET.get("authorization", "").strip()
    if authorization:
        queryset = queryset.filter(authorization_status=authorization)

    dedupe = request.GET.get("dedupe", "").strip()
    if dedupe:
        queryset = queryset.filter(dedupe_status=dedupe)

    search = request.GET.get("q", "").strip()
    if search:
        queryset = queryset.filter(
            Q(device_user_id__icontains=search)
            | Q(employee__first_name__icontains=search)
            | Q(employee__last_name__icontains=search)
        )

    paginator, page, per_page = _paginate(request, queryset, default_per_page=50)

    return render(request, "devices/punch_list.html", {
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "device": device,
        "authorization": authorization,
        "dedupe": dedupe,
        "search": search,
        "authorization_statuses": PunchEvent.AuthorizationStatus.choices,
        "dedupe_statuses": PunchEvent.DedupeStatus.choices,
        "devices": BiometricDevice.objects.order_by("name"),
        "total_count": paginator.count,
    })


@login_required
@company_user_required
def punch_detail(request, pk):
    punch = get_object_or_404(
        PunchEvent.objects.select_related(
            "device", "employee", "device_enrollment", "device_message", "branch"
        ),
        pk=pk,
    )
    # The snapshot stores the branch id as a frozen fact. Resolve it to a name
    # for display only — the snapshot itself is never rewritten.
    branch_id = (punch.authorization_snapshot or {}).get("employee_branch_id")
    employee_branch = ""
    if branch_id:
        from organization.models import Branch

        employee_branch = (
            Branch.objects.filter(pk=branch_id).values_list("name", flat=True).first()
            or f"branch {branch_id}"
        )

    return render(request, "devices/punch_detail.html", {
        "punch": punch,
        "employee_branch": employee_branch,
    })


@login_required
@company_user_required
def unresolved_queue(request):
    """Punches a human must act on before they can count."""
    queryset = (
        PunchEvent.objects.select_related("device", "employee")
        .filter(
            Q(authorization_status__in=UNRESOLVED_STATUSES)
            | Q(dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE)
        )
        .order_by("-punched_at_utc")
    )

    reason = request.GET.get("reason", "").strip()
    if reason == "probable_duplicate":
        queryset = queryset.filter(
            dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE
        )
    elif reason:
        queryset = queryset.filter(authorization_status=reason)

    paginator, page, per_page = _paginate(request, queryset)

    counts = {
        "unknown_employee": PunchEvent.objects.filter(
            authorization_status=PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE
        ).count(),
        "expired_enrollment": PunchEvent.objects.filter(
            authorization_status=PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT
        ).count(),
        "policy_unresolved": PunchEvent.objects.filter(
            authorization_status=PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED
        ).count(),
        "probable_duplicate": PunchEvent.objects.filter(
            dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE
        ).count(),
    }

    return render(request, "devices/unresolved_queue.html", {
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "reason": reason,
        "counts": counts,
        "total_count": paginator.count,
    })


@login_required
@company_user_required
def device_users(request, public_id):
    """The device's own user roster, reconciled against our enrollments.

    Answers the question an administrator actually asks when commissioning a
    terminal: *who does this device recognise, and do their punches count?*
    The left half of each row is what the device reports; the right half is
    our mapping. A user the device knows but we have not mapped is shown
    explicitly, because that is the state that silently loses attendance.
    """
    device = get_object_or_404(
        BiometricDevice.objects.select_related("branch"), public_id=public_id
    )
    roster = build_roster(device)

    search = request.GET.get("q", "").strip()
    if search:
        needle = search.lower()
        roster = [
            r for r in roster
            if needle in r["pin"].lower()
            or needle in r["name"].lower()
            or (r["employee"] and needle in r["employee"].full_name.lower())
        ]

    mapping = request.GET.get("mapping", "").strip()
    if mapping == "mapped":
        roster = [r for r in roster if r["is_mapped"]]
    elif mapping == "unmapped":
        roster = [r for r in roster if not r["is_mapped"]]

    paginator, page, per_page = _paginate(request, roster, default_per_page=25)

    return render(request, "devices/device_users.html", {
        "device": device,
        "page": page,
        "paginator": paginator,
        "per_page": per_page,
        "search": search,
        "mapping": mapping,
        "total_count": paginator.count,
        "unmapped_count": sum(1 for r in build_roster(device) if not r["is_mapped"]),
        "last_sync": (
            DeviceMessage.objects.filter(
                device=device,
                message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT,
            )
            .order_by("-received_at")
            .values_list("received_at", flat=True)
            .first()
        ),
    })


@require_POST
@login_required
@company_user_required
def device_command(request, public_id):
    """Queue a read-only refresh command for the device's next poll.

    Nothing is sent to the device here: push-mode devices are unreachable from
    the server, so the command waits until the device next asks for work. The
    message tells the administrator that explicitly rather than implying an
    instant round trip that did not happen.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    command_key = request.POST.get("command", "")

    if command_key not in SAFE_COMMANDS:
        messages.error(request, "Unknown command.")
        return redirect("devices:device_detail", public_id=device.public_id)

    entry = queue_command(
        device=device, command_key=command_key, requested_by=request.user
    )
    label = COMMAND_LABELS.get(command_key, command_key)
    if entry is None:
        messages.info(request, f"“{label}” is already queued for this device.")
    else:
        messages.success(
            request,
            f"“{label}” queued. The device collects it on its next check-in "
            "(usually within a minute); it is not sent immediately.",
        )
    return redirect("devices:device_detail", public_id=device.public_id)
