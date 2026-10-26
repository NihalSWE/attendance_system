"""Attendance → Fix setup: the one-time fix for people added from a device
(2026-10-26; the work is in ``devices.services.setup_fix``).

One list of everyone whose setup stops attendance - in the Unassigned
department, in a department with no shift, or missing from one of their
branch's devices - and four actions on the ticked people (or everyone in the
list): set their department, send them to the devices, recalculate their
attendance, and re-check the scans that were refused before they were linked.
"""

import datetime

from django import forms
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import redirect
from django.urls import reverse

from base_template.tables import Sortable, paginate_rows, render
from base_template.views import company_admin_required
from common.forms import StyledFormMixin, apply_service_errors
from devices.models import SetupFixJob
from devices.services import setup_fix
from organization.employee_forms import EmployeeCreateForm
from organization.models import Branch, Department, Designation
from tenants.models import Company

WHO = (("ticked", "The people ticked in the list"), ("all", "Everyone in the list"))


class _Who(StyledFormMixin, forms.Form):
    who = forms.ChoiceField(choices=WHO, initial="ticked", widget=forms.RadioSelect, label="Who")

    def __init__(self, *args, ticked=0, listed=0, starts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["who"].choices = (("ticked", f"The people ticked in the list ({ticked} ticked)"),
                                      ("all", f"Everyone in the list ({listed})"))
        if "start" in self.fields:
            self.fields["start"].choices = [(d.isoformat(), label) for d, label in (starts or [])]


def _start_field(label):
    return forms.ChoiceField(label=label, help_text="The first day of a salary month. Days in a "
                             "posted salary month are never changed.")


class PlacementForm(_Who):
    branch = forms.ModelChoiceField(queryset=Branch.all_objects.none(), label="Branch")
    department = forms.ModelChoiceField(queryset=Department.all_objects.none(), label="Department")
    designation = forms.ModelChoiceField(queryset=Designation.all_objects.none(), label="Designation")
    start = _start_field("Then recalculate their attendance from")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["branch"].queryset = Branch.objects.filter(status="active").order_by("name")
        self.fields["branch"].empty_label = "Select a branch"
        self.fields["department"].empty_label = "Select a department"
        self.fields["designation"].empty_label = "Select a designation"
        raw = self.data.get("branch") if self.is_bound else None
        branch = Branch.objects.filter(pk=raw).first() if raw and str(raw).isdigit() else None
        self.fields["department"].queryset = EmployeeCreateForm._departments_for(branch)
        raw = self.data.get("department") if self.is_bound else None
        department = Department.objects.filter(pk=raw).first() if raw and str(raw).isdigit() else None
        self.fields["designation"].queryset = EmployeeCreateForm._designations_for(department)


class SendForm(_Who):
    pass


class RecalculateForm(_Who):
    start = _start_field("Recalculate from")


class RecheckForm(StyledFormMixin, forms.Form):
    start = _start_field("Re-check the scans from")

    def __init__(self, *args, starts=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["start"].choices = [(d.isoformat(), label) for d, label in (starts or [])]


FORMS = {"placement": PlacementForm, "send": SendForm, "recalculate": RecalculateForm,
         "recheck": RecheckForm}
# Each modal its own element ids; Set department keeps "id_", which
# organization/js/employee_form.js looks for (#id_branch ...).
AUTO_ID = {"placement": "id_%s", "send": "send_%s", "recalculate": "recalc_%s",
           "recheck": "recheck_%s"}
DIALOGS = {"placement": "placement-dialog", "send": "send-dialog",
           "recalculate": "recalculate-dialog", "recheck": "recheck-dialog"}


def _filters(request):
    # Always from the address: the action forms post back to it.
    branch = request.GET.get("branch_filter", "")
    problem = request.GET.get("problem", "")
    return (int(branch) if branch.isdigit() else None,
            problem if problem in setup_fix.PROBLEMS else "")


def _listed(company, branch, problem):
    found = setup_fix.people(company, None if branch is None else [branch])
    return [p for p in found if (problem in p.problems if problem else p.problems)]


@login_required
@company_admin_required
def setup_fix_page(request):
    company = Company.objects.get(pk=request.company_id)
    branch, problem = _filters(request)
    listed = _listed(company, branch, problem)
    starts = setup_fix.month_starts(company)
    section = request.POST.get("section") if request.method == "POST" else None
    bound, open_dialog = None, ""
    if section in FORMS:
        result = _save(request, company, section, listed, starts)
        if result is None:
            query = request.GET.urlencode()
            return redirect(reverse("attendance:setup_fix") + (f"?{query}" if query else ""))
        bound, open_dialog = result, DIALOGS[section]

    ticked = len(request.POST.getlist("ids")) if bound is not None else 0
    kw = {"ticked": ticked, "listed": len(listed), "starts": starts}
    forms_ = {}
    for name, cls in FORMS.items():
        kwargs = {"starts": starts} if name == "recheck" else kw
        forms_[name] = bound if name == section and bound is not None else cls(
            auto_id=AUTO_ID[name], **kwargs)

    # Cell 0 is the tick box; 1-7 are shown and sorted; the rest feed the row.
    rows = [["", p.code, p.employee.full_name, p.assignment.branch.name, p.assignment.department.name,
             p.assignment.designation.name, Sortable(f"{p.on} of {p.of}", (p.on - p.of, p.on)),
             ", ".join(p.problems), p.employee.pk, ", ".join(p.missing), p.employee.public_id]
            for p in listed]
    page = paginate_rows(request, rows, columns=8)
    counts = {name: 0 for name in setup_fix.PROBLEMS}
    for person in (_listed(company, branch, "") if problem else listed):
        for name in person.problems:
            counts[name] += 1
    setup_fix.resume_if_stalled(company)
    return render(request, "attendance/setup_fix.html", {
        "page": page, "branches": Branch.objects.filter(status="active").order_by("name"),
        "branch": branch, "problem": problem, "problems": setup_fix.PROBLEMS, "counts": counts,
        "listed": len(listed), "forms": forms_, "open_dialog": open_dialog,
        "job": setup_fix.latest(company), "starts": starts,
    })


def _save(request, company, section, listed, starts):
    """Do one action. None when it was done (the page redirects); the bound
    form, with its reasons, when it was refused."""
    kwargs = {"starts": starts} if section == "recheck" else {
        "ticked": len(request.POST.getlist("ids")), "listed": len(listed), "starts": starts}
    form = FORMS[section](request.POST, auto_id=AUTO_ID[section], **kwargs)
    if not form.is_valid():
        return form
    data = form.cleaned_data
    start_day = None
    if "start" in data:
        start_day = datetime.date.fromisoformat(data["start"])
    if section == "recheck":
        return _job(request, company, SetupFixJob.Kind.RECHECK, [], start_day, form)
    if data["who"] == "all":
        chosen = [p.employee for p in listed]
    else:
        wanted = {int(pk) for pk in request.POST.getlist("ids") if pk.isdigit()}
        chosen = [p.employee for p in listed if p.employee.pk in wanted]
    if not chosen:
        form.add_error(None, "Nobody is ticked: tick people in the list, or choose Everyone in the list.")
        return form
    ids = [e.pk for e in chosen]
    if section == "placement":
        try:
            changed = setup_fix.correct_placement(
                actor=request.user, company=company, employees=chosen, branch=data["branch"],
                department=data["department"], designation=data["designation"])
        except ValidationError as exc:
            apply_service_errors(form, exc)
            return form
        messages.success(request, f"{changed} people are now in {data['department'].name} "
                                  f"({data['designation'].name}).")
        return _job(request, company, SetupFixJob.Kind.RECALCULATE, ids, start_day, form)
    kind = SetupFixJob.Kind.SEND if section == "send" else SetupFixJob.Kind.RECALCULATE
    return _job(request, company, kind, ids, start_day, form)


def _job(request, company, kind, ids, start_day, form):
    job, started = setup_fix.start(actor=request.user, company=company, kind=kind,
                                   employee_ids=ids, start_day=start_day)
    if not started:
        messages.warning(request, f"\"{job.get_kind_display()}\" is still working: this one was not "
                                  "started. Wait until it is done, then press it again.")
    elif job.status == SetupFixJob.Status.DONE:
        messages.success(request, job.message)
    else:
        messages.success(request, f"\"{job.get_kind_display()}\" started for "
                                  f"{job.total if kind != SetupFixJob.Kind.RECHECK else 'everyone'}. "
                                  "It works in the background: this page shows when it is done.")
    return None
