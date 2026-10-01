"""An employee's profile and history, and ending their employment (plan step
N6; the profile - photo, personal information, summaries, actions - 2026-09-26)."""

import mimetypes

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import FileResponse, Http404, HttpResponseBase
from django.shortcuts import redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods, require_POST

from attendance.models import AttendanceRecord
from common.forms import apply_service_errors
from common.tenant import use_company
from organization import employee_detail_services as services
from attendance.forms import EnterMissingForm
from organization import employee_profile as profile
from organization import employee_profile_info as info
from organization.employee_edit_views import edit_sections
from organization.employee_detail_forms import EndEmploymentForm
from organization.employee_edit_services import is_company_wide
from organization.views import _company_or_redirect

#: The month summary, in the order a person reads it.
MONTH_ROWS = (
    (AttendanceRecord.AttendanceStatus.PRESENT, "Present"),
    (AttendanceRecord.AttendanceStatus.HALF_DAY, "Half day"),
    (AttendanceRecord.AttendanceStatus.ABSENT, "Absent"),
    (AttendanceRecord.AttendanceStatus.INCOMPLETE, "No check-out"),
    (AttendanceRecord.AttendanceStatus.LEAVE, "Leave"),
    (AttendanceRecord.AttendanceStatus.WEEKLY_OFF, "Weekly off"),
    (AttendanceRecord.AttendanceStatus.HOLIDAY, "Holiday"),
)


def _month(source, today):
    try:
        year, month = int(source.get("year", today.year)), int(source.get("month", today.month))
    except (TypeError, ValueError):
        return today.year, today.month
    if not (2000 <= year <= today.year + 1 and 1 <= month <= 12):
        return today.year, today.month
    return year, month


def _may_approve_own(user, company_id):
    from attendance.scan_requests import may_approve_own

    return may_approve_own(user, company_id)


def _may_record_leave(user, company_id, assignment):
    """May this login record leave for someone placed here (Record leave's rule)?"""
    from access_control.branch_access import ALL_BRANCHES, branches_for

    record = branches_for(user, company_id, "leave.record")
    return record is ALL_BRANCHES or bool(assignment and assignment.branch_id in record)


#: Where a saved profile edit goes back to: the tab its card is on.
BACK_TABS = {"shift": "roster", "allowances": "profile", "login": "profile", "": "profile"}


def _back(pk):
    url = reverse("organization:employee_detail", args=[pk])
    return lambda anchor: f"{url}#{BACK_TABS.get(anchor, 'profile')}"


def _prefixed(edit):
    """The Edit employee forms, each with ids of its own: on the profile they
    sit side by side in modals, and several share field names (last_day,
    reason). Placement keeps Django's ids - the dependent selects find it so."""
    for name in ("details", "salary", "give_component_form", "end_component_form",
                 "shift_form", "end_form", "give_login", "login_role", "login_password"):
        edit[name].auto_id = f"{name}_%s"
    return edit


def _sections(request, company_id, page, bound):
    """Approver information, education, documents, devices, the roster and
    what waits for a decision (Ajay, 2026-09-27). ``bound``: a refused form,
    by its modal's name, drawn again instead of a fresh one."""
    from access_control.branch_access import can
    from devices.models import BiometricDevice
    from devices.services.mapping import employee_id_for
    from devices.services.panel_access import may_manage_devices
    from organization import employee_devices
    from organization import employee_records as records

    employee, assignment, may, today = (page["employee"], page["assignment"], page["may"],
                                        page["today"])
    held = records.records(company_id, employee)
    education = [(row, bound.get(f"education_{row.pk}") or records.EducationForm(
        instance=row, auto_id=f"education_{row.pk}_%s")) for row in held["education"]]
    manages_devices = may_manage_devices(request.user, company_id)
    enrolled = employee_devices.current(company_id, employee)
    # Someone deleted on the terminal keeps their link (their scans are still
    # theirs), but the table says they are no longer on that device.
    from devices.services.mapping import removed_on_terminal

    gone = {}
    for row in enrolled:
        row.removed_on_terminal = row.device_user_id in removed_on_terminal(row.device, gone)
    device_rows = [(row, bound.get(f"device_{row.pk}") or employee_devices.DevicePermissionForm(
        instance=row, auto_id=f"device_{row.pk}_%s") if manages_devices else None)
        for row in enrolled]
    branch_devices = []
    if assignment is not None and may["edit"]:
        on = {row.device_id for row in enrolled}
        with use_company(company_id):
            branch_devices = [
                (device, device.pk in on) for device in BiometricDevice.objects
                .filter(branch_id=assignment.branch_id)
                .exclude(status__in=["retired", "suspended"]).order_by("name")]
    overtime_seen = bool(assignment) and (is_company_wide(page["membership"]) or can(
        request.user, company_id, "overtime.view", assignment.branch_id))
    return {
        "approvers": records.approvers(company_id, employee, assignment),
        "education": education,
        "education_new_form": bound.get("education_new") or records.EducationForm(
            auto_id="education_new_%s"),
        "documents": held["documents"],
        "document_form": bound.get("document") or records.DocumentForm(auto_id="document_%s"),
        "device_rows": device_rows,
        "manages_devices": manages_devices,
        "branch_devices": branch_devices,
        "device_pin": employee_id_for(employee),
        "roster": records.roster(company_id, employee, assignment, today),
        "pending": records.pending(company_id, employee, today,
                                   leave=page["leave_seen"], attendance=bool(may["attendance"]),
                                   overtime=overtime_seen),
    }


def _actions(request, company_id, page, bound, may_record_leave):
    """The forms behind the profile's actions (Ajay's twelve, 2026-09-27; the
    mapping is in ``organization.employee_actions``)."""
    from access_control.branch_access import can
    from organization import employee_actions as actions
    from organization.employee_detail_forms import EndEmploymentForm

    employee, assignment, may, today = (page["employee"], page["assignment"], page["may"],
                                        page["today"])
    ended = page["is_ended"]
    company_wide = is_company_wide(page["membership"])
    fixes = bool(assignment) and (company_wide or can(request.user, company_id, "attendance.fix",
                                                      assignment.branch_id))
    from leaves import policies
    from leaves.forms import AdjustBalanceForm, AssignPolicyForm
    from leaves.models import LeavePolicy, LeaveType

    # Leave policy and balances (Phase E, 2026-09-27).
    balances_by = policies.may_manage_balances(request.user, company_id) and not ended
    raw_year = str(request.GET.get("year", ""))
    leave_year = int(raw_year) if raw_year.isdigit() and 2000 <= int(raw_year) <= 2100 \
        else today.year
    book = policies.Book(employee)
    # The policy given to them by hand today, so its modal opens on it (Nihal,
    # 2026-09-29: an edit form shows what they already have). None: the default.
    own_policy = next((row.policy_id for row in book.own if row.effective_from <= today
                       and (row.effective_to is None or today <= row.effective_to)), None)
    context = {
        "leave_policy": book.policy_on(today),
        "leave_balances": policies.overview(employee, leave_year, today)
        if page["leave_seen"] else None,
        "policy_form": (bound.get("leave_policy") or AssignPolicyForm(
            policies=LeavePolicy.objects.filter(status="active").order_by("name"),
            initial={"effective_from": today, "policy": own_policy}, auto_id="leave_policy_%s"))
        if balances_by else None,
        "adjust_form": (bound.get("adjust") or AdjustBalanceForm(
            leave_types=LeaveType.objects.filter(status="active").order_by("name"),
            initial={"year": leave_year}, auto_id="adjust_%s"))
        if balances_by else None,
        "leave_form": (bound.get("leave") or actions.leave_form(company_id, employee))
        if may_record_leave and not ended else None,
        "late_form": (bound.get("late") or actions.LateForm(
            days=actions.late_days(company_id, employee, today), auto_id="late_%s"))
        if fixes and not ended and employee.user_id != request.user.pk else None,
        "reports": actions.reports_to(company_id, employee),
    }
    # Their inactive period now, or the next one planned (2026-09-29).
    from organization import employee_inactive

    planned = employee_inactive.open_periods(employee, today)
    context["inactive_now"] = next((p for p in planned if p.start_date <= today), None)
    context["inactive_next"] = next((p for p in planned if p.start_date > today), None)
    if not may["edit"]:
        return context
    context.update({
        "inactive_form": bound.get("inactive") or employee_inactive.InactiveForm(
            initial={"start_date": today}, auto_id="inactive_%s"),
        "overtime_form": (bound.get("overtime") or actions.OvertimeForm(
            initial={"from_day": today}, auto_id="overtime_%s"))
        if actions.may_set_overtime(request.user, company_id, page["membership"], assignment)
        else None,
        "reports_form": bound.get("reports") or actions.ReportsForm(
            choices=info.line_manager_choices(request.user, company_id, employee),
            initial={"people": [person.pk for person in context["reports"]]},
            auto_id="reports_%s"),
    })
    if may["end"] and not ended:
        has_login = bool(page["login"] and page["login"].status == "active" and may["logins"])
        for key, status in (("resign", "resigned"), ("delete", "terminated")):
            form = bound.get(key) or EndEmploymentForm(
                initial={"last_day": today, "status": status, "disable_login": has_login,
                         "end_device_enrollments": True},
                auto_id=f"{key}_%s")
            if not has_login:
                form.fields["disable_login"].widget = forms.HiddenInput()
                form.initial["disable_login"] = False
            context[f"{key}_form"] = form
    return context


def _profile(request, company_id, pk, *, personal=None, photo=None, open_dialog="",
             edit=None, line_manager=None, bound=None):
    """The profile page. A refused form comes back bound, so its modal opens
    again with the reasons and what was typed."""
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    employee, may = page["employee"], page["may"]
    if may["edit"] and edit is None:
        # Every Edit employee card, as modals here (Ajay, 2026-09-27).
        edit = edit_sections(request, company_id, pk, back=_back(pk))
    if edit is not None:
        _prefixed(edit)
    line_manager_form = None
    if may["edit"]:
        with use_company(company_id):
            assignment = page["assignment"]
            choices = info.line_manager_form_choices(request.user, company_id, employee,
                                                     assignment)
            line_manager_form = line_manager or info.LineManagerForm(
                choices=choices,
                initial={"manager": assignment.manager_id if assignment else None})
            line_manager_form.auto_id = "line_manager_%s"
    counts = page["month_counts"]
    today = page["today"]
    company = page["membership"].company
    year, month = _month(request.GET, today)
    summary = (profile.month_summary(company=company, scope=may["attendance"],
                                     employee=employee, year=year, month=month)
               if may["attendance"] else None)
    earlier = (year - 1, 12) if month == 1 else (year, month - 1)
    later = (year + 1, 1) if month == 12 else (year, month + 1)
    personal_form = personal or profile.PersonalForm(instance=employee)
    leave = profile.year_leave(actor=request.user, company=company, employee=employee, year=year)
    page["leave_seen"] = leave is not None
    may_record_leave = _may_record_leave(request.user, company_id, page["assignment"])
    with use_company(company_id):
        sections = _sections(request, company_id, page, bound or {})
        sections.update(_actions(request, company_id, page, bound or {}, may_record_leave))
    context = {
        **page,
        "month_rows": [(label, counts.get(status, 0)) for status, label in MONTH_ROWS],
        # The Calendar opens only people placed in branches whose attendance
        # the viewer may see (A12 part 7).
        "calendar_url": (
            reverse("attendance:attendance_calendar")
            + f"?employee={pk}&year={year}&month={month}"
        ) if may["attendance"] else "",
        "summary": summary,
        "summary_earlier": earlier,
        "summary_later": later,
        "leave": leave,
        "leave_year": year,
        "personal_form": personal_form,
        "photo_form": photo or profile.PhotoForm(),
        # What the Personal tab lists: each field's label and value, choices
        # shown as words.
        "personal_rows": [
            (personal_form.fields[name].label,
             dict(profile.CHOICES.get(name, ())).get(getattr(employee, name),
                                                     getattr(employee, name)))
            for name in profile.PERSONAL_FIELDS
        ],
        "may_record_leave": may_record_leave,
        # Entering a missing scan or day for them: whoever sees their attendance,
        # not for oneself (that is Report a missed scan), and not after they left.
        "may_enter_missing": bool(may["attendance"]) and not page["is_ended"]
        and employee.user_id != request.user.pk,
        "may_approve_own_entry": _may_approve_own(request.user, company_id),
        "missing_form": EnterMissingForm(approve_now=_may_approve_own(request.user, company_id),
                                         initial={"work_date": today, "at": today,
                                                  "at_out": today}),
        "open_dialog": open_dialog,
        "edit": edit,
        "line_manager_form": line_manager_form,
        **sections,
        "gender_label": dict(profile.CHOICES.get("gender", ())).get(employee.gender,
                                                                   employee.gender),
        "general": info.general(company_id, employee, page["assignment"], page["devices"],
                                today),
    }
    # Drawn inside the company: the modals' forms read the company's lists
    # (branches, shifts, a login's branches) as they are drawn.
    with use_company(company_id):
        return render(request, "organization/employee_detail.html", context)


@login_required
@require_http_methods(["GET"])
def employee_detail(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    return _profile(request, company_id, pk)


@login_required
@require_http_methods(["GET"])
def employee_photo(request, pk):
    """The photo, only to someone who may see this employee - never a public
    file address."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, employee, _assignment, _pay = services.get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.view")
    if not employee.photo:
        raise Http404("No photo.")
    kind = mimetypes.guess_type(employee.photo.name)[0] or "application/octet-stream"
    reply = FileResponse(employee.photo.open("rb"), content_type=kind)
    reply["Cache-Control"] = "private, max-age=300"
    reply["X-Content-Type-Options"] = "nosniff"
    return reply


@login_required
@require_POST
def employee_photo_change(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    profile.editable(request.user, company_id, pk)
    form = profile.PhotoForm(request.POST, request.FILES)
    if form.is_valid():
        try:
            profile.save_photo(actor=request.user, company_id=company_id, employee_id=pk,
                               upload=form.cleaned_data.get("photo") or None,
                               remove=form.cleaned_data.get("remove"))
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Photo saved." if form.cleaned_data.get("photo")
                             else "Photo removed.")
            return redirect("organization:employee_detail", pk=pk)
    return _profile(request, company_id, pk, photo=form, open_dialog="photo-dialog")


@login_required
@require_POST
def employee_personal(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, employee = profile.editable(request.user, company_id, pk)
    form = profile.PersonalForm(request.POST, instance=employee)
    if form.is_valid():
        try:
            profile.save_personal(actor=request.user, company_id=company_id, employee_id=pk,
                                  form=form)
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Personal information saved.")
            return redirect(reverse("organization:employee_detail", args=[pk]) + "#profile")
    return _profile(request, company_id, pk, personal=form, open_dialog="personal-dialog")


@login_required
@require_POST
def employee_profile_edit(request, pk):
    """A profile modal's Edit employee form, saved by the same code as the Edit
    employee page (``edit_sections``). Refused: the profile again, with that
    modal open, its reasons and what was typed."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    result = edit_sections(request, company_id, pk, back=_back(pk))
    if isinstance(result, HttpResponseBase):
        return result
    section = result["section"]
    dialog = (f"component_end-{request.POST.get('row')}-dialog" if section == "component_end"
              else f"{section}-dialog")
    return _profile(request, company_id, pk, edit=result, open_dialog=dialog)


@login_required
@require_POST
def employee_line_manager(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    _membership, employee = profile.editable(request.user, company_id, pk)
    from organization.employee_edit_services import get_employee_for_edit

    _membership, _employee, assignment, _pay = get_employee_for_edit(
        actor=request.user, company_id=company_id, employee_id=pk, code="employees.edit")
    with use_company(company_id):
        form = info.LineManagerForm(
            request.POST, choices=info.line_manager_form_choices(request.user, company_id,
                                                                 employee, assignment))
        valid = form.is_valid()
    if valid:
        try:
            info.set_line_manager(actor=request.user, company_id=company_id, employee_id=pk,
                                  manager=form.cleaned_data["manager"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            messages.success(request, "Line manager saved.")
            return redirect(reverse("organization:employee_detail", args=[pk]) + "#profile")
    return _profile(request, company_id, pk, line_manager=form,
                    open_dialog="line_manager-dialog")


@login_required
@require_POST
def employee_report_visibility(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    hidden = request.POST.get("hidden") == "1"
    employee = profile.set_report_visibility(actor=request.user, company_id=company_id,
                                             employee_id=pk, hidden=hidden)
    messages.success(request, (
        f"{employee.full_name} is left out of the reports. Their attendance still counts."
        if hidden else f"{employee.full_name} is shown in the reports again."))
    return redirect("organization:employee_detail", pk=pk)


@login_required
@require_http_methods(["GET", "POST"])
def employee_end(request, pk):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    page = services.employee_history(actor=request.user, company_id=company_id, employee_id=pk)
    employee = page["employee"]
    if not page["may"]["end"]:
        # end_employment refuses too; this keeps the form from being offered.
        raise PermissionDenied("Ending this person's employment is not yours to do.")
    if page["is_ended"]:
        messages.info(request, f"{employee.full_name} has already left.")
        return redirect("organization:employee_detail", pk=employee.pk)

    form = EndEmploymentForm(
        request.POST or None,
        initial={
            "last_day": page["today"], "disable_login": page["may"]["logins"],
            "end_device_enrollments": True,
        },
    )
    if request.method == "POST" and form.is_valid():
        data = form.cleaned_data
        try:
            employee, summary = services.end_employment(
                actor=request.user, company_id=company_id, employee_id=employee.pk,
                last_day=data["last_day"], status=data["status"], reason=data["reason"],
                disable_login=data["disable_login"],
                end_device_enrollments=data["end_device_enrollments"],
            )
        except ValidationError as exc:
            apply_service_errors(form, exc)
        else:
            parts = [f"{employee.full_name}'s employment ended on {data['last_day']:%d %b %Y}."]
            if summary["enrollments_ended"]:
                parts.append(
                    f"{summary['enrollments_ended']} device enrollment"
                    f"{'s' if summary['enrollments_ended'] != 1 else ''} ended."
                )
            if summary["login_disabled"]:
                parts.append("Their login is disabled.")
            if summary["devices_cleared"]:
                parts.append(
                    "Being removed from " + ", ".join(summary["devices_cleared"])
                    + " (on their next check-in); the saved fingerprint and face are kept."
                )
            messages.success(request, " ".join(parts))
            if summary["devices_by_hand"]:
                # Not pretending: these terminals have no proven delete, so a
                # person must do it there or the leaver still opens the door.
                messages.warning(request, "Delete them on the terminal itself: " + "; ".join(
                    f"{item['device']} (user {item['pin']}) — {item['reason']}"
                    for item in summary["devices_by_hand"]))
            if not is_company_wide(page["membership"]):
                # Once ended they are placed nowhere, so no longer in a branch
                # this login looks after; their page stays with the company.
                return redirect("employee_list")
            return redirect("organization:employee_detail", pk=employee.pk)

    dialog = request.POST.get("dialog", "")
    if request.method == "POST" and dialog in ("resign", "delete"):
        # Resign / Delete employee on the profile (Ajay, 2026-09-27): the
        # modal opens again with the reasons.
        form.auto_id = f"{dialog}_%s"
        return _profile(request, company_id, pk, bound={dialog: form},
                        open_dialog=f"{dialog}-dialog")
    active_devices = [d for d in page["devices"] if d.is_current]
    return render(request, "organization/employee_end.html", {
        **page,
        "form": form,
        "active_devices": active_devices,
        # A login is disabled with the ending only by someone who may manage
        # logins in the branch; the service checks the same.
        "has_active_login": bool(
            page["login"] and page["login"].status == "active" and page["may"]["logins"]
        ),
    })
