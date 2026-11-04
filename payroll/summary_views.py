"""Attendance -> Summary (2026-10-31, payroll/overrides.py).

Each person's days for a salary month (or a weekly pay group's week), counted
exactly as the salary counts them, and the side panel where whoever may fix
attendance sets the present days (hours for hourly pay) or goes back to the
scans. Opens for the owner, company admin, HR and branch admins; a branch
admin sees the people placed in their branches at the period's end.
"""

import datetime
from urllib.parse import urlencode

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.http import Http404
from django.shortcuts import render as plain_render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from access_control.branch_access import ALL_BRANCHES
from attendance.services import settle_due
from attendance.views import month_context, read_month
from base_template import fetch
from base_template.tables import Sortable, paginate_rows, render, sort_key
from common.tenant import use_company
from employees.models import EmployeeCompensation
from organization.models import Branch, Department
from organization.views import _company_or_redirect
from payroll import overrides, periods
from payroll.models import PayrollRun
from payroll.services import period_records
from payroll.views import _pay_choice

SHOW = (("need", "Need a look"), ("all", "Everyone"), ("hand", "Changed by hand"))


def _period(company_id, source):
    """``(year, month, pay, first, last)`` the page and its forms are about."""
    year, month = read_month(source)
    pay = _pay_choice(company_id, source, year, month)
    if pay["pay_group"] is None:
        first, last = periods.bounds(company_id, year, month)
    else:
        first, last = pay["week_first"], pay["week_last"]
    return year, month, pay, first, last


def _bases(company_id, employee_ids, last):
    """``{employee_id: pay basis}`` in force at the end of the period."""
    end = timezone.make_aware(datetime.datetime.combine(last, datetime.time.max))
    with use_company(company_id):
        rows = (EmployeeCompensation.objects.filter(employee_id__in=list(employee_ids), effective_from__lte=end)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=end))
                .exclude(status__in=["cancelled", "draft"]).order_by("effective_from")
                .values_list("employee_id", "pay_basis"))
    return dict(rows)


def _salary_state(company_id, first, last, pay_group):
    with use_company(company_id):
        run = (PayrollRun.objects.filter(payroll_period__start_date=first, payroll_period__end_date=last,
                                         pay_group=pay_group).order_by("-pk").first())
    if run is None:
        return "Not generated", "wait", False
    if run.status == PayrollRun.Status.POSTED:
        return "Finalised", "ok", True
    if run.status == PayrollRun.Status.SUBMITTED:
        return "Waiting for approval", "info", True
    return "Draft, open", "wait", False


def _people(company_id, first, last, pay_group, branches, employee_ids=None):
    """One entry per person in reach with days in the period."""
    found, _weekly = period_records(company_id, first, last, pay_group, employee_ids=employee_ids)
    people = []
    for employee, records in found.items():
        if not records:
            continue
        placement = records[-1].employee_assignment
        if not overrides.may(branches, placement.branch_id):
            continue
        people.append({"employee": employee, "records": records, "placement": placement,
                       "scans": overrides.counts(records)})
    by_hand = overrides.active(company_id, first, last, [p["employee"].pk for p in people])
    bases = _bases(company_id, [p["employee"].pk for p in people], last)
    for person in people:
        person["override"] = by_hand.get(person["employee"].pk)
        person["basis"] = bases.get(person["employee"].pk, "monthly")
        person["shown"] = _shown(person)
        scans = person["scans"]
        person["need"] = bool(person["override"] or scans["incomplete"] or scans["half_day"] or scans["absent"])
    return people


def _shown(person):
    """The counts the salary uses: the scans, or the change by hand."""
    scans, override = person["scans"], person["override"]
    shown = dict(scans)
    if override is None:
        return shown
    if override.present_days is not None:
        shown.update(present=override.present_days, half_day=0, incomplete=0,
                     absent=scans["working"] - override.present_days)
    else:
        shown.update(hours=override.hours, incomplete=0)
    return shown


def _query(request, **extra):
    keep = [(key, value) for key in ("year", "month", "group", "week", "branch", "department", "employee", "show")
            for value in request.GET.getlist(key) if value and key not in extra]
    keep += list(extra.items())
    return urlencode(keep)


@login_required
def attendance_summary(request):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    branches = overrides.reach(request.user, company_id)
    year, month, pay, first, last = _period(company_id, request.GET)
    # The days whose close has passed, worked out first (cheap when none are due).
    settle_due(company_id, start=first, end=last)
    people = _people(company_id, first, last, pay["pay_group"], branches)
    with use_company(company_id):
        branch_list = Branch.objects.filter(status="active").order_by("name")
        if branches is not ALL_BRANCHES:
            branch_list = branch_list.filter(pk__in=branches)
        branch_list = list(branch_list)
        departments = list(Department.objects.filter(branch__in=branch_list, status="active")
                           .select_related("branch").order_by("name"))
    # Several of each may be chosen (2026-10-31).
    branch = [int(v) for v in request.GET.getlist("branch") if v.isdigit()]
    department = [int(v) for v in request.GET.getlist("department") if v.isdigit()]
    show = request.GET.get("show") if request.GET.get("show") in dict(SHOW) else "need"
    if branch:
        people = [p for p in people if p["placement"].branch_id in branch]
    if department:
        people = [p for p in people if p["placement"].department_id in department]
    # The employee picker (2026-11-04): everybody in the period, before it narrows.
    choosable = sorted(((p["employee"].pk, p["placement"].employee_code or "", p["employee"].full_name)
                        for p in people), key=lambda c: (sort_key(c[1]), c[2].lower()))
    employee = [int(v) for v in request.GET.getlist("employee") if v.isdigit()]
    if employee:
        people = [p for p in people if p["employee"].pk in employee]
    counts = {"all": len(people), "need": sum(1 for p in people if p["need"]),
              "hand": sum(1 for p in people if p["override"])}
    listed = [p for p in people if show == "all" or (show == "need" and p["need"])
              or (show == "hand" and p["override"])]
    # Cells 0-11 are the columns, searched and sorted (the ID on its own,
    # sorted as a number, 2026-11-04); the rest feed the row.
    rows = []
    for p in listed:
        shown, scans, person = p["shown"], p["scans"], p["employee"]
        code = p["placement"].employee_code or ""
        rows.append([
            code, Sortable(person.full_name, person.full_name.lower()), p["placement"].department.name,
            scans["working"], Sortable(str(shown["present"]), shown["present"]), shown["half_day"],
            shown["incomplete"], shown["absent"], scans["leave"],
            Sortable(f"{shown['hours']:.2f}", shown["hours"]),
            "By hand" if p["override"] else "Scans", "",
            person.pk, scans, p["override"], p["basis"], person.public_id, person.full_name, code,
        ])
    # One cell per column on the page (12): the table's own requests - search,
    # sort, the next page - are answered with exactly those cells.
    page = paginate_rows(request, rows, columns=12)
    state, tone, locked = _salary_state(company_id, first, last, pay["pay_group"])
    return render(request, "payroll/attendance_summary.html", {
        **month_context(year, month), **pay, "first": first, "last": last, "page": page,
        "counts": counts, "show": show, "shows": SHOW, "branches": branch_list,
        "departments": departments, "branch": branch, "department": department,
        "employees": choosable, "employee": employee,
        "salary_state": state, "salary_tone": tone, "locked": locked,
        "changed": request.GET.get("changed", ""), "query": _query(request),
        "people": len(people),
    })


def _person_context(request, company_id, employee_id):
    branches = overrides.reach(request.user, company_id)
    year, month, pay, first, last = _period(company_id, request.GET if request.method == "GET" else request.POST)
    people = _people(company_id, first, last, pay["pay_group"], branches, employee_ids=[employee_id])
    if not people:
        raise Http404("Nobody with days in this period here.")
    person = people[0]
    state, _tone, locked = _salary_state(company_id, first, last, pay["pay_group"])
    return {**pay, "year": year, "month": month, "first": first, "last": last, "person": person,
            "history": overrides.history(company_id, employee_id, first, last), "locked": locked,
            "salary_state": state, "hourly": person["basis"] == "hourly",
            "back": request.GET.get("back") or request.POST.get("back", "")}


@login_required
def summary_person(request, employee_id):
    """The side panel for one person, loaded into the page."""
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    return plain_render(request, "payroll/includes/summary_person.html",
                        _person_context(request, company_id, employee_id))


def _back(request, employee_id):
    """The page the panel was opened from, with this person's row marked."""
    back = request.POST.get("back", "").split("#")[0]
    page = reverse("payroll:attendance_summary")
    if not back.startswith(page):
        back = page
    path, _, query = back.partition("?")
    keep = [part for part in query.split("&") if part and not part.startswith("changed=")]
    keep.append(f"changed={employee_id}")
    return f"{path}?{'&'.join(keep)}"


def _period_args(company_id, source):
    _year, _month, pay, first, last = _period(company_id, source)
    return {"first": first, "last": last, "pay_group": pay["pay_group"]}


@login_required
@require_POST
def summary_save(request, employee_id):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        row = overrides.set_override(
            actor=request.user, company_id=company_id, employee_id=employee_id,
            present_days=request.POST.get("present_days"), hours=request.POST.get("hours"),
            reason=request.POST.get("reason", ""), **_period_args(company_id, request.POST))
    except (ValidationError, PermissionDenied) as exc:
        if isinstance(exc, PermissionDenied):
            return fetch.refused(message=str(exc), status=403)
        return fetch.refused_errors(exc)
    value = (f"{row.present_days} present days" if row.present_days is not None
             else f"{row.hours.normalize():f} hours")
    return fetch.saved(f"Saved: {value}. Generate the salary again to pay it.", _back(request, employee_id))


@login_required
@require_POST
def summary_revert(request, employee_id):
    company_id, bail = _company_or_redirect(request)
    if bail:
        return bail
    try:
        overrides.revert(actor=request.user, company_id=company_id, employee_id=employee_id,
                         **_period_args(company_id, request.POST))
    except (ValidationError, PermissionDenied) as exc:
        if isinstance(exc, PermissionDenied):
            return fetch.refused(message=str(exc), status=403)
        return fetch.refused_errors(exc)
    return fetch.saved("Back to scans: the salary follows the check-ins and check-outs again.",
                       _back(request, employee_id))
