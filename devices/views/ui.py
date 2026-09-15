"""Administrator screens for devices, enrollment and troubleshooting.

Every page is tenant-scoped by construction: TenantMiddleware sets the active
company and the TenantOwned managers filter each query, so a view that forgot
would raise rather than leak another company's rows.

Nothing here offers an edit or delete control for DeviceMessage or PunchEvent.
They are append-only evidence; the screens read them and explain them.
"""

import datetime
from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Page, Paginator
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from auditlog.models import AuditLog
from base_template.tables import integer, paginate, render as table_render
from common.forms import company_timezone
from devices.forms import (
    BiometricDeviceForm,
    DeviceDepartmentForm,
    DeviceEnrollmentForm,
    DeviceScopeForm,
    RecheckPunchesForm,
)
from devices.models import (
    BiometricDevice,
    DeviceDepartment,
    DeviceEnrollment,
    DeviceMessage,
    DeviceSyncState,
    PunchEvent,
)
from devices.services import (
    attendance_rules,
    connection,
    panel_access,
    protocol,
    server_address,
    setup_instructions,
)
from devices.services.commands import (
    COMMAND_LABELS,
    WRITABLE_OPTIONS,
    pending_summary,
    queue_command,
    queue_set_option,
    queue_user_delete,
    queue_user_push,
)
from devices.services.device_roster import build_roster
from devices.services.user_sync import SyncNotPossible, sync_device_users

# Punch states that need a human before they can feed attendance.
UNRESOLVED_STATUSES = (
    PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
    PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
    PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED,
)


def company_user_required(view):
    """Fail closed for restricted roles, then hand off to the shared rule.

    The rule itself lives in ``devices.services.panel_access`` so the services
    can re-check it without depending on a view decorator having run. Only the
    two answers that are page-shaped stay here.
    """

    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.user.is_superuser:
            return redirect("platform:company_list")
        if not request.company_id:
            return render(request, "base_template/no_company.html", status=200)
        panel_access.assert_may_manage_devices(request.user, request.company_id)
        return view(request, *args, **kwargs)

    return wrapped


def _paginate_rows(request, rows, *, search=(), order=()):
    """base_template.tables.paginate for a list that is not in the database.

    A device's user roster is rebuilt from the uploads it sent
    (devices/services/device_roster.py); there is no table to count, search or
    order in SQL. So this does the same work on the server over the *whole*
    roster — never just the rows on screen — and speaks the helper's exact
    contract: the same request parameters, the same limits (10–100 rows, a
    200-character literal search, server-owned sort keys, a stable tie-break),
    and it registers the same table description, so ``table_render``, the
    ``table_pagination`` include and ``tables.js`` work unchanged.
    """
    params = {"page": "page", "per_page": "per_page", "query": "table_q"}
    ajax = request.method == "GET" and request.GET.get("table") == "1"
    length = integer(request.GET.get("length" if ajax else params["per_page"]), 25, 10, 100)
    total = len(rows)

    def text(row, key):
        value = row.get(key)
        return "" if value is None else str(value)

    query = request.GET.get("search[value]" if ajax else params["query"], "").strip()[:200]
    if query and search:
        needle = query.lower()
        rows = [r for r in rows if any(needle in text(r, key).lower() for key in search)]

    if ajax:
        keys = []
        for index in range(min(len(order), 8)):
            column = integer(request.GET.get(f"order[{index}][column]"), -1)
            if 0 <= column < len(order) and order[column]:
                keys.append((order[column], request.GET.get(f"order[{index}][dir]") == "desc"))
        # Stable sorts applied last key first give a multi-column order; the
        # roster's own order (mapped first, then name, then pin) is the tie-break.
        for key, descending in reversed(keys):
            rows = sorted(rows, key=lambda r, k=key: _sort_value(r.get(k)), reverse=descending)

    paginator = Paginator(rows, length)
    if ajax:
        start = integer(request.GET.get("start"), 0)
        page = Page(rows[start:start + length], start // length + 1, paginator)
    else:
        page = paginator.get_page(request.GET.get(params["page"]))

    def link(number):
        values = request.GET.copy()
        values.pop("table", None)
        values[params["page"]] = number
        return f"?{values.urlencode()}"

    numbers = []
    if not ajax:
        for number in paginator.get_elided_page_range(page.number, on_each_side=2, on_ends=1):
            current, dots = number == page.number, number == paginator.ELLIPSIS
            numbers.append({"number": number, "current": current, "dots": dots,
                            "url": None if current or dots else link(number)})
    table = {
        "ajax": ajax, "draw": integer(request.GET.get("draw"), 0), "name": "",
        "total": total, "page": page, "query": query, "params": params,
        "orderable": ",".join(str(i) for i, key in enumerate(order) if key),
        "columns": len(order), "start": max(0, page.start_index() - 1), "numbers": numbers,
        "previous": link(page.previous_page_number()) if not ajax and page.has_previous() else None,
        "next": link(page.next_page_number()) if not ajax and page.has_next() else None,
    }
    request.server_tables = {**getattr(request, "server_tables", {}), "": table}
    request.server_table = table
    return page


def _sort_value(value):
    """Numbers as numbers (device user ids are digits), text case-blind, blanks last."""
    if value is None or value == "":
        return (2, "")
    if isinstance(value, bool):
        return (0, int(value))
    text = str(value)
    if text.isdigit():
        return (0, int(text))
    return (1, text.lower())


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

    page = paginate(
        request, queryset,
        search=("name", "serial_number", "branch__name", "device_model__name", "status"),
        order=("name", "serial_number", "branch__name", "status", "last_seen_at", None, None),
    )
    # Each row's connection, worked out once. The page's queryset is evaluated
    # here and cached, so the template (and a table draw) reads these objects.
    now = timezone.now()
    for device in page.object_list:
        device.connection = connection.connection_of(device, now)

    return table_render(request, "devices/device_list.html", {
        "page": page,
        "search": search,
        "status": status,
        "branch": branch,
        "statuses": BiometricDevice.Status.choices,
        "branches": _branch_options(),
        "stopped_devices": connection.stopped_devices(request.company_id),
    })


def _branch_options():
    from organization.models import Branch

    return Branch.objects.order_by("name")


def _detail_with_test(device):
    """The device page, starting a connection test from now.

    After registering or editing, the next thing anybody wants to know is
    whether the terminal can reach us, so the page starts waiting straight
    away rather than asking for another click.
    """
    url = reverse("devices:device_detail", args=[device.public_id])
    return redirect(f"{url}?{_test_query(timezone.now())}#connection")


def _test_query(started, command_id=None):
    """``test=<start>[&command=<id>]``, encoded.

    Encoded because an ISO time ends in "+00:00", and an unencoded "+" in a
    query string is read back as a space — the start time would not parse and
    the page would silently show no test at all.
    """
    from urllib.parse import urlencode

    params = {"test": started.isoformat()}
    if command_id:
        params["command"] = command_id
    return urlencode(params)


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
        return _detail_with_test(device)

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
    # refresh() here as well as in the status endpoint, so a first page load
    # never shows a change that timed out while nobody was watching.
    address_change = server_address.refresh(server_address.latest_change(device))

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
        # Only the requests this device's dialect has (devices/services/protocol.py).
        "command_options": [
            (k, COMMAND_LABELS[k]) for k in COMMAND_LABELS if protocol.supports(device, k)
        ],
        "dialect_label": protocol.LABELS[protocol.dialect(device)],
        "writable_options": [
            {
                "key": key,
                "help": spec[2],
                "current": (device.settings or {}).get(key, ""),
            }
            for key, spec in WRITABLE_OPTIONS.items()
        ],
        "pending_commands": pending_summary(device),
        "address_change": address_change,
        "address_status": server_address.status_payload(device, address_change),
        "address_history": server_address.history(device),
        "saved_address": server_address.current_address(device),
        "connection": connection.connection_of(device),
        "connection_test": _test_from_query(request, device),
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

        # The address change runs after the rest of the edit is saved, so a
        # failed check never rolls back a name or branch the administrator
        # also changed. It never writes the address itself either: that only
        # happens once the device has connected at the new one.
        if form.requested_address is not None:
            _start_address_change(request, device, form.requested_address)
        return _detail_with_test(device)

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
# Server address
# --------------------------------------------------------------------------


def _start_address_change(request, device, target):
    """Run steps 1 and 2, and say what happened in words about the hardware.

    Authorization is re-checked in the service rather than trusted from the
    decorator: this is the one device write that can strand hardware, and the
    form is a convenience layer, never the boundary.
    """
    try:
        attempt = server_address.request_change(
            device=device, actor=request.user, raw_address=target.text
        )
    except server_address.ServerAddressError as exc:
        messages.error(request, str(exc))
        return None

    _audit(
        request,
        "device.server_address.requested",
        attempt,
        before={"address": server_address.previous_address_text(attempt)},
        after={
            "address": target.text,
            "status": attempt.status,
            "reason": attempt.failure_reason,
        },
    )

    if attempt.status == attempt.Status.UNREACHABLE:
        messages.error(
            request,
            f"{target.text} could not be reached, so nothing was sent to "
            f"{device.name}. {attempt.failure_reason}",
        )
    else:
        messages.success(
            request,
            f"{target.text} answered. The change is queued for {device.name} "
            "and will apply on its next check-in. Its saved address stays "
            "unchanged until the device connects at the new one.",
        )
    return attempt


@login_required
@company_user_required
def device_server_address_status(request, public_id):
    """JSON for the status panel, polled every few seconds.

    Reading the status is also what evaluates the timeout — there is no
    background worker — so this endpoint is the thing that eventually turns a
    silent device into a "set it back on the terminal" instruction.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    attempt = server_address.latest_change(device)
    return JsonResponse(server_address.status_payload(device, attempt))


@require_POST
@login_required
@company_user_required
def device_server_address_cancel(request, public_id):
    """Abandon a change that has not reached the device yet."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    attempt = server_address.latest_change(device)
    if attempt is None:
        messages.error(request, "There is no server address change to cancel.")
        return redirect("devices:device_detail", public_id=device.public_id)
    try:
        server_address.cancel(attempt=attempt, actor=request.user)
    except server_address.ServerAddressError as exc:
        messages.error(request, str(exc))
    else:
        _audit(
            request, "device.server_address.cancelled", attempt,
            after={"status": attempt.status},
        )
        messages.success(
            request, "The change was cancelled before anything reached the device."
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
        try:
            # Savepointed like the enrollment writes, so a rejected insert is
            # rolled back cleanly and the render below still has a usable
            # connection.
            with transaction.atomic():
                link.save()
        except IntegrityError:
            # The form already checks the overlap; this catches the race
            # between two administrators saving at once, so the loser reads a
            # sentence instead of a 500.
            form.add_error(
                "department",
                f"{device.name} was mapped to that department while you were "
                "filling this in. Reload the device page to see the current "
                "mappings.",
            )
        else:
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

    page = paginate(
        request, queryset,
        search=("employee__first_name", "employee__last_name", "device__name", "device_user_id"),
        order=(
            ("employee__first_name", "employee__last_name"), "device__name", "device_user_id",
            "attendance_enabled", "assigned_device_authorized", "effective_from", None,
        ),
    )

    return table_render(request, "devices/enrollment_list.html", {
        "page": page,
        "search": search,
        "device": device,
        "devices": BiometricDevice.objects.order_by("name"),
    })


OVERLAP_MESSAGE = (
    "This enrollment overlaps an existing one for the same employee or user "
    "number on this device. Edit or end the existing enrollment first."
)


@login_required
@company_user_required
def enrollment_create(request):
    form = DeviceEnrollmentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        enrollment = form.save(commit=False)
        enrollment.company_id = request.company_id
        try:
            with transaction.atomic():
                enrollment.save()
        except IntegrityError:
            # The form mirrors both overlap constraints; this only catches a
            # concurrent save that slipped between the check and the write.
            form.add_error(None, OVERLAP_MESSAGE)
            return render(request, "devices/enrollment_form.html", {
                "form": form,
                "title": "Enroll an employee on a device",
                "submit_label": "Create enrollment",
            })
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
        try:
            with transaction.atomic():
                enrollment = form.save()
        except IntegrityError:
            form.add_error(None, OVERLAP_MESSAGE)
            return render(request, "devices/enrollment_form.html", {
                "form": form,
                "enrollment": enrollment,
                "title": f"Edit enrollment for {enrollment.employee}",
                "submit_label": "Save changes",
            })
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

    page = paginate(
        request, queryset,
        search=("device__name", "message_type", "processing_status"),
        order=("received_at", "device__name", "message_type", "record_count",
               "processing_status", None),
    )

    return table_render(request, "devices/message_list.html", {
        "page": page,
        "device": device,
        "status": status,
        "statuses": DeviceMessage.ProcessingStatus.choices,
        "devices": BiometricDevice.objects.order_by("name"),
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
    # Newest arrival first. Ordering on the device's own timestamp looks more
    # natural but is not trustworthy for a troubleshooting log: this firmware
    # reports 1970/1971 timestamps until its clock syncs, and fixture rows can
    # carry future dates — either buries the punch that just came in.
    # received_at is our clock, so the latest entry is always on top.
    queryset = (
        PunchEvent.objects.select_related("device", "employee", "device_message")
        .order_by("-received_at", "-id")
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

    page = paginate(
        request, queryset,
        search=("device_user_id", "employee__first_name", "employee__last_name",
                "device__name", "verification_method", "authorization_status"),
        order=("punched_at_device", ("employee__first_name", "employee__last_name"),
               "device__name", "verification_method", "authorization_status", None, None),
    )

    return table_render(request, "devices/punch_list.html", {
        "page": page,
        "device": device,
        "authorization": authorization,
        "dedupe": dedupe,
        "search": search,
        "authorization_statuses": PunchEvent.AuthorizationStatus.choices,
        "dedupe_statuses": PunchEvent.DedupeStatus.choices,
        "devices": BiometricDevice.objects.order_by("name"),
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

    page = paginate(
        request, queryset,
        search=("device_user_id", "employee__first_name", "employee__last_name",
                "device__name", "authorization_status", "dedupe_status"),
        order=("punched_at_device", "device_user_id", "device__name",
               "authorization_status", None, None),
    )

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

    return table_render(request, "devices/unresolved_queue.html", {
        "page": page,
        "reason": reason,
        "counts": counts,
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
    full_roster = build_roster(device)
    roster = full_roster

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

    page = _paginate_rows(
        request, roster,
        search=("pin", "name", "privilege_label", "card_number", "employee_name"),
        order=("pin", "name", "privilege_label", "fingerprint_count", "face_count",
               "card_number", "has_password", "has_photo", "employee_name", None,
               "counts_for_attendance"),
    )

    return table_render(request, "devices/device_users.html", {
        "device": device,
        "page": page,
        "search": search,
        "mapping": mapping,
        "unmapped_count": sum(1 for r in full_roster if not r["is_mapped"]),
        "can_refresh": protocol.supports(device, "query_users"),
        # No user writes to a 2.x device until its write form is measured.
        "user_writes": protocol.dialect(device) != protocol.ATT2,
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
    # The Device users page asks to come back to itself.
    back = (
        redirect("devices:device_users", public_id=device.public_id)
        if request.POST.get("next") == "users"
        else redirect("devices:device_detail", public_id=device.public_id)
    )

    if command_key not in COMMAND_LABELS:
        messages.error(request, "Unknown command.")
        return back
    label = COMMAND_LABELS[command_key]
    if not protocol.supports(device, command_key):
        messages.error(request, f"This device does not take “{label}”.")
        return back

    entry = queue_command(
        device=device, command_key=command_key, requested_by=request.user
    )
    if entry is None:
        messages.info(request, f"“{label}” is already queued for this device.")
    else:
        messages.success(
            request,
            f"“{label}” queued. The device collects it on its next check-in "
            "(usually within a minute); it is not sent immediately.",
        )
    return back


@require_POST
@login_required
@company_user_required
def device_users_sync(request, public_id):
    """Create draft employees for device users nobody has mapped yet.

    Deliberately conservative: the drafts are recognition-only, so a synced
    person's punches are preserved and marked rather than silently starting to
    count before anyone has checked who they are.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    pins = request.POST.getlist("pin") or None

    try:
        created, skipped, errors = sync_device_users(
            device=device, actor=request.user, pins=pins
        )
    except SyncNotPossible as exc:
        messages.error(request, str(exc))
        return redirect("devices:device_users", public_id=device.public_id)

    if created:
        for entry in created:
            _audit(
                request, "device_enrollment.synced_from_device", entry["employee"],
                after={
                    "device_user_id": entry["pin"],
                    "device": device.serial_number,
                    "assigned_device_authorized": False,
                },
            )
        messages.success(
            request,
            f"Created {len(created)} draft employee record"
            f"{'' if len(created) == 1 else 's'} and mapped them. "
            "They are recognition-only: open each enrollment to authorise "
            "attendance, and complete the employee details in Employees.",
        )
    if skipped:
        messages.info(
            request, f"{len(skipped)} user(s) were already mapped and left alone."
        )
    for err in errors:
        messages.error(
            request, f"Device user {err['pin']} could not be synced: {err['error']}"
        )
    if not created and not skipped and not errors:
        messages.info(request, "Every device user is already mapped.")

    return redirect("devices:device_users", public_id=device.public_id)


@require_POST
@login_required
@company_user_required
def device_set_option(request, public_id):
    """Queue a configuration change for the device.

    Only allowlisted options with validated values are accepted, and the change
    is audited before it leaves: a device setting that alters when punches are
    reported has to be reconstructable later.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    option_key = request.POST.get("option", "")
    value = request.POST.get("value", "")

    before = dict(device.settings or {})
    entry, error = queue_set_option(
        device=device, option_key=option_key, value=value, requested_by=request.user
    )
    if error:
        messages.error(request, error)
        return redirect("devices:device_detail", public_id=device.public_id)

    device.refresh_from_db()
    _audit(
        request, "device.option_queued", device,
        before={"settings": before},
        after={"settings": device.settings, "command": entry["body"]},
    )
    messages.success(
        request,
        f"Queued “{entry['body']}”. The device applies it on its next "
        "check-in; it is not changed immediately.",
    )
    return redirect("devices:device_detail", public_id=device.public_id)


@require_POST
@login_required
@company_user_required
def device_user_push(request, public_id):
    """Push one enrolled employee's user record onto the device.

    This creates the id the device will report when that person is
    recognised. It does not enrol biometrics: the face/fingerprint sensor
    captures those at the terminal and the template never reaches us.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    enrollment = get_object_or_404(
        DeviceEnrollment.objects.select_related("employee"),
        pk=request.POST.get("enrollment", ""),
        device=device,
    )

    entry, error = queue_user_push(
        device=device,
        device_user_id=enrollment.device_user_id,
        name=enrollment.employee.full_name,
        card_number=enrollment.card_number,
        requested_by=request.user,
    )
    if error:
        messages.error(request, error)
    else:
        _audit(
            request, "device.user_pushed", enrollment,
            after={"device_user_id": enrollment.device_user_id, "command": entry["body"]},
        )
        messages.success(
            request,
            f"Queued {enrollment.employee.full_name} as device user "
            f"{enrollment.device_user_id}. The device applies it on its next "
            "check-in. Their face or fingerprint must still be enrolled at "
            "the terminal — we never hold biometric templates.",
        )
    return redirect("devices:device_users", public_id=device.public_id)


@require_POST
@login_required
@company_user_required
def device_user_delete(request, public_id):
    """Remove one user from the device.

    Irreversible from here: deleting the user also destroys the face and
    fingerprint enrolled on that terminal, and those templates exist nowhere
    else. Their punch history is untouched — that is our evidence, not the
    device's.
    """
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)
    device_user_id = request.POST.get("device_user_id", "")

    entry, error = queue_user_delete(
        device=device, device_user_id=device_user_id, requested_by=request.user
    )
    if error:
        messages.error(request, error)
    else:
        _audit(
            request, "device.user_deleted", device,
            after={"device_user_id": device_user_id, "command": entry["body"]},
        )
        messages.warning(
            request,
            f"Queued removal of device user {device_user_id}. Their enrolled "
            "face/fingerprint on this terminal will be destroyed and cannot be "
            "restored from here — they must be enrolled again in person. "
            "Punch history is kept.",
        )
    return redirect("devices:device_users", public_id=device.public_id)


# --------------------------------------------------------------------------
# Which devices count, and re-checking punches (plan step N4)
# --------------------------------------------------------------------------

#: The range shown when the page opens: the last month, where a forgotten
#: grant or a late enrollment usually shows up.
DEFAULT_RECHECK_DAYS = 30


def _recheck_range(request):
    """The range from the query string, else the last month. Never raises."""
    # The company's today, not the server's: TIME_ZONE is UTC, and before
    # 06:00 in Dhaka that is still yesterday.
    today = timezone.now().astimezone(company_timezone()).date()
    form = RecheckPunchesForm(request.GET or None)
    if request.GET and form.is_valid():
        return form, form.cleaned_data["start"], form.cleaned_data["end"]
    start = today - datetime.timedelta(days=DEFAULT_RECHECK_DAYS - 1)
    if not request.GET:
        form = RecheckPunchesForm(initial={"start": start, "end": today})
    return form, start, today


@login_required
@company_user_required
def attendance_rules_page(request):
    """Choose which devices count, and see and re-check punches that do not."""
    company_id = request.company_id
    try:
        current = attendance_rules.company_scope(company_id)
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect("devices:device_list")

    if request.method == "POST":
        scope_form = DeviceScopeForm(request.POST)
        if scope_form.is_valid():
            try:
                _settings, changed = attendance_rules.set_company_scope(
                    actor=request.user, company_id=company_id,
                    scope=scope_form.cleaned_data["scope"],
                )
            except ValidationError as exc:
                scope_form.add_error("scope", exc.messages[0])
            else:
                label = dict(scope_form.fields["scope"].choices)[
                    scope_form.cleaned_data["scope"]
                ]
                if changed:
                    messages.success(
                        request,
                        f"Punches now count on: {label}. Punches already stored "
                        "keep their decision \u2014 re-check them below to apply "
                        "this to past days.",
                    )
                else:
                    messages.info(request, f"Punches already count on: {label}.")
                return redirect("devices:attendance_rules")
    else:
        scope_form = DeviceScopeForm(initial={"scope": current})

    range_form, start, end = _recheck_range(request)
    summary = attendance_rules.excluded_summary(company_id, start=start, end=end)
    return render(request, "devices/attendance_rules.html", {
        "scope_form": scope_form,
        "range_form": range_form,
        "start": start,
        "end": end,
        "summary": summary,
        "excluded_total": sum(row[3] for row in summary),
        "overrides": attendance_rules.overrides(company_id),
        "max_days": attendance_rules.MAX_RECHECK_DAYS,
    })


@require_POST
@login_required
@company_user_required
def attendance_recheck(request):
    """Judge the excluded punches in a range again, under today's rules."""
    form = RecheckPunchesForm(request.POST)
    back = reverse("devices:attendance_rules")
    if not form.is_valid():
        for errors in form.errors.values():
            for error in errors:
                messages.error(request, error)
        return redirect(back)

    start, end = form.cleaned_data["start"], form.cleaned_data["end"]
    back = f"{back}?start={start:%Y-%m-%d}&end={end:%Y-%m-%d}"
    try:
        result = attendance_rules.recheck_punches(
            actor=request.user, company_id=request.company_id,
            start=start, end=end,
        )
    except ValidationError as exc:
        messages.error(request, exc.messages[0])
        return redirect(back)

    span = f"{start:%d %b} \u2013 {end:%d %b %Y}"
    if not result.checked and not result.skipped_locked:
        messages.info(request, f"No punches to re-check between {span}.")
        return redirect(back)

    parts = [f"Re-checked {result.checked} punch{'es' if result.checked != 1 else ''} ({span})."]
    if result.now_count:
        parts.append(
            f"{result.now_count} now count, and attendance was rebuilt for "
            "those days."
        )
    else:
        parts.append("None of them count under the current rules.")
    still = sum(result.still_excluded.values())
    if still:
        parts.append(f"{still} still don\u2019t \u2014 the table shows why.")
    if result.skipped_locked:
        parts.append(
            f"{result.skipped_locked} in a finalised salary month were left alone."
        )
    level = messages.success if result.now_count else messages.info
    level(request, " ".join(parts))
    return redirect(back)



# --------------------------------------------------------------------------
# Connection: live badges and the connection test (plan step N8)
# --------------------------------------------------------------------------


def _parse_instant(value):
    from django.utils.dateparse import parse_datetime

    moment = parse_datetime(value or "")
    if moment is None:
        return None
    if timezone.is_naive(moment):
        moment = timezone.make_aware(moment, datetime.timezone.utc)
    return moment


def _test_from_query(request, device):
    """A test the page should show, from ``?test=<start>&command=<id>``."""
    from urllib.parse import urlencode

    started = _parse_instant(request.GET.get("test"))
    if started is None:
        return None
    command = request.GET.get("command", "")
    command_id = int(command) if command.isdigit() else None
    result = connection.test_status(device, since=started, command_id=command_id)
    return {
        "result": result,
        "command_id": command_id,
        "status_url": (
            reverse("devices:device_connection_test", args=[device.public_id])
            + "?" + urlencode(
                {"since": started.isoformat(), **({"command": command_id} if command_id else {})}
            )
        ),
        "advice": (
            connection.advice(device, setup_instructions.build(request, device))
            if result.gave_up else []
        ),
    }


@login_required
@company_user_required
def device_connections(request):
    """Live badges for the devices on a page, as JSON."""
    wanted = [v for v in request.GET.get("devices", "").split(",") if v.strip()]
    devices = BiometricDevice.objects.filter(public_id__in=_valid_uuids(wanted))
    return JsonResponse({"devices": connection.connections(devices)})


def _valid_uuids(values):
    import uuid

    valid = []
    for value in values:
        try:
            valid.append(uuid.UUID(value.strip()))
        except ValueError:
            continue
    return valid


@login_required
@company_user_required
def device_connection_test(request, public_id):
    """GET: where a test has got to, as JSON. POST: start a test."""
    device = get_object_or_404(BiometricDevice.objects, public_id=public_id)

    if request.method == "POST":
        if device.status == BiometricDevice.Status.RETIRED:
            messages.error(request, "A retired device is not tested.")
            return redirect("devices:device_detail", public_id=device.public_id)
        started = timezone.now()
        url = reverse("devices:device_detail", args=[device.public_id])
        command_id = None
        if request.POST.get("with_command"):
            entry = queue_command(
                device=device, command_key=connection.TEST_COMMAND,
                requested_by=request.user,
            )
            if entry is None:
                # Already queued and not yet picked up: follow that one.
                entry = next(
                    (e for e in pending_summary(device)
                     if e.get("key") == connection.TEST_COMMAND),
                    None,
                )
            if entry is not None:
                command_id = entry["id"]
                _audit(request, "device.command_queued", device, after={
                    "command": entry["body"], "purpose": "connection test",
                })
        return redirect(f"{url}?{_test_query(started, command_id)}#connection")

    started = _parse_instant(request.GET.get("since"))
    if started is None:
        return JsonResponse({"error": "since is required"}, status=400)
    command = request.GET.get("command", "")
    result = connection.test_status(
        device, since=started, command_id=int(command) if command.isdigit() else None,
    )
    advice = []
    if result.gave_up:
        advice = [
            {"title": item["title"], "text": item["text"],
             "values": [list(pair) for pair in item["values"]]}
            for item in connection.advice(device, setup_instructions.build(request, device))
        ]
    return JsonResponse(result.as_dict(advice))
