"""Shifts & calendar: attendance settings, shifts, department and employee
shifts, weekly offs and holidays (docs/api/00-PLAN.md phase 4; the guide:
docs/api/40-shifts-and-calendar.md).

The panel's Schedule pages, through the same gate, forms and services
(``scheduling.services``): the company's owner and administrator change them;
HR and payroll read them.
"""

import datetime
from zoneinfo import ZoneInfo

from django.db.models import Q
from django.utils import timezone
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, form_data, refuse, service_errors
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.shifts import serializers as s
from common.choices import ActiveStatus
from employees.models import Employee
from organization.employee_edit_services import get_employee_for_edit
from organization.models import Department
from organization.services import (
    require_company_membership,
    require_structure_manager,
    visible_branches,
)
from scheduling import services
from scheduling.forms import (
    AttendanceSettingsForm,
    ChangeWeeklyOffStartForm,
    DepartmentShiftForm,
    EmployeeShiftForm,
    EndEmployeeShiftForm,
    EndWeeklyOffForm,
    HolidayForm,
    ShiftForm,
)
from scheduling.models import CompanyAttendanceSettings, Holiday, Shift, WeeklyOffRule
from tenants.models import Company

AREA = "shifts"
ADMINS = ["Company owner or administrator"]
READERS = ["Company owner, administrator, HR or payroll"]
READ_ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
               "signature_required", "invalid_signature", "two_step_setup_required",
               "scope_missing", "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = READ_ERRORS + ["validation_error", "unknown_field"]
WEEKDAY_NUMBER = {name: number for number, name in enumerate(s.WEEKDAYS)}
SHIFT_EXAMPLE = {"id": 2, "code": "DAY", "name": "Day shift", "start_time": "09:00",
                 "end_time": "18:00", "ends_next_day": False, "scheduled_minutes": 540,
                 "grace_in_minutes": 10, "grace_out_minutes": 5,
                 "minimum_full_day_minutes": 420, "minimum_half_day_minutes": 240,
                 "default_break_minutes": 60, "break_is_paid": False,
                 "overtime_after_minutes": 30, "status": "active"}
SHIFT_PARAM = Param("shift_id", PATH, "integer", "The shift id.", required=True, example=2)


def _ref(row):
    return {"id": row.pk, "name": row.name} if row is not None else None


def _membership(request):
    return require_company_membership(request.user, request.company_id)


def _active_shifts():
    return Shift.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")


class ScheduleView(ApiView):
    """Shared settings: the company's Schedule pages."""

    permission_classes = [PanelRule]
    company_required = True
    panel_page = "scheduling:schedule_overview"
    read_scope, write_scope = "shifts:read", "shifts:write"
    throttle_scope = "write"


# --- overview & settings -----------------------------------------------------------------

def _settings_out(settings):
    return {"shift_mode": settings.shift_mode, "company_shift": _ref(settings.company_shift),
            "missing_punch_policy": settings.missing_punch_policy,
            "punch_pairing_strategy": settings.punch_pairing_strategy}


SETTINGS_EXAMPLE = {"shift_mode": "department_shifts",
                    "company_shift": {"id": 2, "name": "Day shift"},
                    "missing_punch_policy": "review_required",
                    "punch_pairing_strategy": "alternating"}


class OverviewView(ScheduleView):
    permission_classes = [PanelRule]

    @endpoint(
        id="schedule-overview", area=AREA, title="Schedule overview",
        summary="Is everyone covered by a shift, and what the calendar holds.",
        what_it_does=["Answers the settings, whether attendance can be worked out (ready), the "
                      "departments without a shift, today's weekly offs and the next holidays."],
        description="As the panel's Schedule page. ready is false while someone has no shift.",
        roles=READERS, scopes=["shifts:read"], response=s.OverviewSerializer,
        response_example={"settings": SETTINGS_EXAMPLE, "ready": True,
                          "departments_without_shift": [], "active_shifts": 3,
                          "weekly_offs": ["friday"], "holidays_this_year": 22,
                          "upcoming_holidays": [{"date": "2026-12-16", "name": "Victory Day"}]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        _membership(request)
        settings = services.get_attendance_settings(request.company_id)
        today = timezone.localdate()
        by_department = settings.shift_mode == CompanyAttendanceSettings.ShiftMode.DEPARTMENT_SHIFTS
        current = services.current_department_shifts(request.company_id, today)
        active = Department.objects.filter(status=ActiveStatus.ACTIVE)
        uncovered = (list(active.exclude(pk__in=list(current)).order_by("branch__name", "name"))
                     if by_department and settings.company_shift_id is None else [])
        linked = active.filter(pk__in=list(current)).exists()
        ready = (bool(settings.company_shift_id) if not by_department
                 else not uncovered and (bool(settings.company_shift_id) or linked))
        offs = (WeeklyOffRule.objects.filter(branch__isnull=True, effective_from__lte=today)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=today))
                .order_by("weekday"))
        holidays = Holiday.objects.filter(status=Holiday.Status.ACTIVE)
        return Response(s.OverviewSerializer({
            "settings": _settings_out(settings), "ready": ready,
            "departments_without_shift": [_ref(d) for d in uncovered],
            "active_shifts": _active_shifts().count(),
            "weekly_offs": [s.WEEKDAYS[rule.weekday] for rule in offs],
            "holidays_this_year": holidays.filter(holiday_date__year=today.year).count(),
            "upcoming_holidays": [{"date": h.holiday_date.isoformat(), "name": h.name}
                                  for h in holidays.filter(holiday_date__gte=today)
                                  .order_by("holiday_date")[:5]],
        }).data)


class SettingsView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:attendance_settings_edit"

    @endpoint(
        id="attendance-settings-get", area=AREA, title="Attendance settings",
        summary="How shifts are given out, and how scans become check-in and check-out.",
        what_it_does=["Answers the company's attendance settings."],
        description="Change them with PATCH.",
        roles=ADMINS, scopes=["shifts:read"], response=s.SettingsSerializer,
        response_example=SETTINGS_EXAMPLE, errors=READ_ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        return Response(s.SettingsSerializer(
            _settings_out(services.get_attendance_settings(request.company_id))).data)

    @endpoint(
        id="attendance-settings-change", area=AREA, title="Change attendance settings",
        summary="Shift mode, the company shift, missing scans, check-in and check-out.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description=("department_shifts: each person works their department's shift and the "
                     "company shift covers departments without one. company_single_shift: "
                     "everyone works the company shift (it is then required). The pairing "
                     "applies to days worked out from now."),
        roles=ADMINS, scopes=["shifts:write"], request=s.SettingsInputSerializer,
        response=s.SettingsSerializer,
        request_example={"punch_pairing_strategy": "first_last"},
        response_example={**SETTINGS_EXAMPLE, "punch_pairing_strategy": "first_last"},
        errors=WRITE_ERRORS,
    )
    def patch(self, request):
        settings = services.get_attendance_settings(request.company_id)
        data = s.SettingsInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        names = {"company_shift": "company_shift_id"}
        form = checked(AttendanceSettingsForm,
                       form_data(AttendanceSettingsForm, settings, dict(data.validated_data),
                                 names, shifts=_active_shifts()),
                       names, instance=settings, shifts=_active_shifts())
        with service_errors(names):
            services.update_attendance_settings(actor=request.user, company_id=request.company_id,
                                                values=form.cleaned_data)
        return Response(s.SettingsSerializer(
            _settings_out(services.get_attendance_settings(request.company_id))).data)


# --- shifts -------------------------------------------------------------------------------

def _shift(shift_id):
    shift = Shift.objects.filter(pk=shift_id).first()
    if shift is None:
        raise ApiError("not_found", "No such shift in this company.")
    return shift


class ShiftListView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:schedule_overview", "POST": "scheduling:shift_create"}

    @endpoint(
        id="shifts-list", area=AREA, title="Shifts",
        summary="The company's shifts.",
        what_it_does=["Lists them, active first."],
        description="Filter by status.",
        roles=READERS, scopes=["shifts:read"], paginated=True,
        params=[Param("status", QUERY, "string", "active or inactive.", example="active")],
        response=s.ShiftSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [SHIFT_EXAMPLE]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        _membership(request)
        rows = Shift.objects.order_by("status", "name")
        if request.query_params.get("status") in dict(ActiveStatus.choices):
            rows = rows.filter(status=request.query_params["status"])
        return self.paginated(request, rows, s.ShiftSerializer)

    @endpoint(
        id="shifts-create", area=AREA, title="Add a shift",
        summary="A new working-time template.",
        what_it_does=["Creates the shift; its length and whether it ends the next day follow "
                      "from the times."],
        description="The code is unique in the company. The minimum for a full day cannot be "
                    "more than the shift's length, nor the break.",
        roles=ADMINS, scopes=["shifts:write"], response_status=201,
        request=s.ShiftInputSerializer, response=s.ShiftSerializer,
        request_example={k: v for k, v in SHIFT_EXAMPLE.items()
                         if k not in ("id", "ends_next_day", "scheduled_minutes", "status")},
        response_example=SHIFT_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        require_structure_manager(request.user, request.company_id)
        data = s.ShiftInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(ShiftForm, form_data(ShiftForm, None, dict(data.validated_data)))
        with service_errors():
            shift = services.create_shift(actor=request.user, company_id=request.company_id,
                                          values=_shift_values(form))
        return Response(s.ShiftSerializer(shift).data, status=201)


def _shift_values(form):
    values = dict(form.cleaned_data)
    values["spans_next_day"] = form.instance.spans_next_day
    return values


class ShiftDetailView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:schedule_overview", "PATCH": "scheduling:shift_edit"}

    @endpoint(
        id="shifts-get", area=AREA, title="One shift",
        summary="One shift's times and rules.",
        what_it_does=["Answers the shift."],
        description="Another company's shift answers not_found.",
        roles=READERS, scopes=["shifts:read"], params=[SHIFT_PARAM],
        response=s.ShiftSerializer, response_example=SHIFT_EXAMPLE,
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, shift_id):
        _membership(request)
        return Response(s.ShiftSerializer(_shift(shift_id)).data)

    @endpoint(
        id="shifts-change", area=AREA, title="Change a shift",
        summary="New times or rules for a shift.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description="It changes how attendance is worked out from now on; recalculate a month "
                    "to apply it to past days.",
        roles=ADMINS, scopes=["shifts:write"], params=[SHIFT_PARAM],
        request=s.ShiftInputSerializer, response=s.ShiftSerializer,
        request_example={"grace_in_minutes": 15},
        response_example={**SHIFT_EXAMPLE, "grace_in_minutes": 15},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def patch(self, request, shift_id):
        shift = _shift(shift_id)
        services.get_shift_for_edit(actor=request.user, company_id=request.company_id,
                                    shift_id=shift.pk)
        data = s.ShiftInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(ShiftForm, form_data(ShiftForm, shift, dict(data.validated_data)),
                       instance=shift)
        with service_errors():
            shift = services.update_shift(actor=request.user, company_id=request.company_id,
                                          shift_id=shift.pk, values=_shift_values(form))
        return Response(s.ShiftSerializer(_shift(shift_id)).data)


class ShiftStatusView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:shift_status"

    @endpoint(
        id="shifts-status", area=AREA, title="Retire or reactivate a shift",
        summary="Set a shift active or inactive.",
        what_it_does=["Changes the status; shifts are never deleted."],
        description="Past attendance keeps the shift it was measured against. A shift in use "
                    "may be refused.",
        roles=ADMINS, scopes=["shifts:write"], params=[SHIFT_PARAM],
        request=s.StatusSerializer, response=s.ShiftSerializer,
        request_example={"status": "inactive"},
        response_example={**SHIFT_EXAMPLE, "status": "inactive"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, shift_id):
        shift = _shift(shift_id)
        data = s.StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            services.set_shift_status(actor=request.user, company_id=request.company_id,
                                      shift_id=shift.pk, status=data.validated_data["status"])
        return Response(s.ShiftSerializer(_shift(shift_id)).data)


# --- department shifts ------------------------------------------------------------------------

class DepartmentShiftView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:schedule_overview",
                  "POST": "scheduling:department_shift_set"}

    @endpoint(
        id="department-shifts-list", area=AREA, title="Department shifts",
        summary="Each active department and the shift it works on a day.",
        what_it_does=["Lists the departments with their shift on the day (today unless given)."],
        description="A department without one works the company shift.",
        roles=READERS, scopes=["shifts:read"], paginated=True,
        params=[Param("on", QUERY, "string (date)", "The day; today when left out.",
                      example="2026-11-01"),
                Param("branch_id", QUERY, "integer", "Only this branch.", example=3)],
        response=s.DepartmentShiftSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "department": {"id": 8, "name": "Software"}, "branch": {"id": 3, "name": "Chattogram"},
            "shift": {"id": 2, "name": "Day shift"}, "since": "2026-01-01"}]},
        errors=READ_ERRORS + ["validation_error"],
    )
    def get(self, request):
        membership = _membership(request)
        on = timezone.localdate()
        raw = (request.query_params.get("on") or "").strip()
        if raw:
            try:
                on = datetime.date.fromisoformat(raw)
            except ValueError:
                refuse({"on": ["A date, YYYY-MM-DD."]})
        current = services.current_department_shifts(request.company_id, on)
        rows = (Department.objects.select_related("branch")
                .filter(status=ActiveStatus.ACTIVE, branch__in=visible_branches(membership))
                .order_by("branch__name", "name"))
        branch_id = request.query_params.get("branch_id", "")
        if branch_id.isdigit():
            rows = rows.filter(branch_id=int(branch_id))
        out = []
        for department in rows:
            link = current.get(department.pk)
            out.append({"department": _ref(department), "branch": _ref(department.branch),
                        "shift": _ref(link.shift) if link else None,
                        "since": link.effective_from if link else None})
        return self.paginated(request, out, s.DepartmentShiftSerializer)

    @endpoint(
        id="department-shifts-set", area=AREA, title="Set a department's shift",
        summary="Everyone in a department works a shift from a date.",
        what_it_does=["Gives the department the shift from the date; earlier days keep the "
                      "shift they were worked on."],
        description="A later change already saved refuses an earlier one.",
        roles=ADMINS, scopes=["shifts:write"], response_status=201,
        request=s.DepartmentShiftInputSerializer, response=s.DepartmentShiftSerializer,
        request_example={"department_id": 8, "shift_id": 2, "from_date": "2026-11-01"},
        response_example={"department": {"id": 8, "name": "Software"},
                          "branch": {"id": 3, "name": "Chattogram"},
                          "shift": {"id": 2, "name": "Day shift"}, "since": "2026-11-01"},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        membership = require_structure_manager(request.user, request.company_id)
        data = s.DepartmentShiftInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"department": "department_id", "shift": "shift_id",
                 "effective_from": "from_date"}
        departments = (Department.objects.select_related("branch")
                       .filter(status=ActiveStatus.ACTIVE,
                               branch__in=visible_branches(membership)))
        form = checked(DepartmentShiftForm, {
            "department": values["department_id"], "shift": values["shift_id"],
            "effective_from": values["from_date"].isoformat()},
            names, departments=departments, shifts=_active_shifts())
        with service_errors(names):
            services.set_department_shift(actor=request.user, company_id=request.company_id,
                                          values=form.cleaned_data)
        department = form.cleaned_data["department"]
        return Response(s.DepartmentShiftSerializer({
            "department": _ref(department), "branch": _ref(department.branch),
            "shift": _ref(form.cleaned_data["shift"]), "since": values["from_date"]}).data,
            status=201)


# --- an employee's own shift ----------------------------------------------------------------

EMPLOYEE_PARAM = Param("employee_id", PATH, "integer", "The employee id.", required=True,
                       example=41)
OWN_SHIFT_EXAMPLE = {"id": 5, "shift": {"id": 3, "name": "Night shift"},
                     "kind": "temporary", "first_day": "2026-11-01", "last_day": "2026-11-30",
                     "reason": "Covering the night desk", "status": "active"}


def _own_shifts(company_id, employee):
    company = Company.objects.get(pk=company_id)
    rows = services.employee_shift_history(company_id, employee,
                                           ZoneInfo(company.timezone or "UTC"))
    return [{"id": row.pk, "shift": _ref(row.shift), "kind": row.assignment_type,
             "first_day": row.first_day, "last_day": row.last_day, "reason": row.reason,
             "status": row.status} for row in rows]


def _employee(request, employee_id, code):
    if not Employee.objects.filter(pk=employee_id).exists():
        raise ApiError("not_found", "No such employee in this company.")
    return get_employee_for_edit(actor=request.user, company_id=request.company_id,
                                 employee_id=employee_id, code=code)[1]


class EmployeeShiftView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employee-shifts-list", area=AREA, title="An employee's own shifts",
        summary="The shifts given to one person, which win over their department's.",
        what_it_does=["Lists them, newest first."],
        description="Someone with none works their department's (or the company) shift.",
        roles=["Whoever may edit the employee"], scopes=["shifts:read"], params=[EMPLOYEE_PARAM],
        paginated=True, response=s.EmployeeShiftSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [OWN_SHIFT_EXAMPLE]},
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, employee_id):
        employee = _employee(request, employee_id, "employees.edit")
        return self.paginated(request, _own_shifts(request.company_id, employee),
                              s.EmployeeShiftSerializer)

    @endpoint(
        id="employee-shifts-set", area=AREA, title="Give an employee their own shift",
        summary="A shift for one person from a day - until changed, or until a last day.",
        what_it_does=["No last day: theirs until changed. With a last day: temporary; "
                      "afterwards they are back on what they had.",
                      "It wins over the department's and the company's shift."],
        description="One at a time: a new one closes the one in force on its first day; a "
                    "change already saved for a later day refuses an earlier one.",
        roles=ADMINS, scopes=["shifts:write"], params=[EMPLOYEE_PARAM], response_status=201,
        request=s.EmployeeShiftInputSerializer, response=s.EmployeeShiftSerializer,
        request_example={"shift_id": 3, "first_day": "2026-11-01", "last_day": "2026-11-30",
                         "reason": "Covering the night desk"},
        response_example=OWN_SHIFT_EXAMPLE, errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, employee_id):
        employee = _employee(request, employee_id, "employees.edit")
        data = s.EmployeeShiftInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        last = values.get("last_day")
        names = {"shift": "shift_id"}
        form = checked(EmployeeShiftForm, {
            "shift": values["shift_id"], "first_day": values["first_day"].isoformat(),
            "last_day": last.isoformat() if last else "", "reason": values.get("reason", "")},
            names, shifts=_active_shifts())
        with service_errors(names):
            services.set_employee_shift(actor=request.user, company_id=request.company_id,
                                        values={"employee": employee, **form.cleaned_data})
        return Response(s.EmployeeShiftSerializer(
            _own_shifts(request.company_id, employee)[0]).data, status=201)


class EndEmployeeShiftView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employee-shifts-end", area=AREA, title="End an employee's own shift",
        summary="From the day after, they work their department's shift again.",
        what_it_does=["Ends it on the last day given; records it in the audit log."],
        description="Their history keeps it.",
        roles=ADMINS, scopes=["shifts:write"],
        params=[EMPLOYEE_PARAM, Param("assignment_id", PATH, "integer",
                                      "The employee-shift id.", required=True, example=5)],
        request=s.EndShiftInputSerializer, response=s.EmployeeShiftSerializer,
        request_example={"last_day": "2026-11-15"},
        response_example={**OWN_SHIFT_EXAMPLE, "last_day": "2026-11-15"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, employee_id, assignment_id):
        employee = _employee(request, employee_id, "employees.edit")
        rows = {row["id"]: row for row in _own_shifts(request.company_id, employee)}
        if assignment_id not in rows:
            raise ApiError("not_found", "No such shift of theirs.")
        data = s.EndShiftInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(EndEmployeeShiftForm, {"last_day": data.validated_data["last_day"]
                                              .isoformat()})
        with service_errors():
            services.end_employee_shift(actor=request.user, company_id=request.company_id,
                                        assignment_id=assignment_id,
                                        last_day=form.cleaned_data["last_day"])
        rows = {row["id"]: row for row in _own_shifts(request.company_id, employee)}
        return Response(s.EmployeeShiftSerializer(rows[assignment_id]).data)


# --- weekly offs -----------------------------------------------------------------------------

WEEKLY_OFF_EXAMPLE = {"id": 4, "weekday": "friday", "branch": None, "from_date": "2024-01-01",
                      "stops_from": None, "status": "active"}
RULE_PARAM = Param("rule_id", PATH, "integer", "The weekly-off id.", required=True, example=4)


def _rule_out(rule):
    return {"id": rule.pk, "weekday": s.WEEKDAYS[rule.weekday], "branch": _ref(rule.branch),
            "from_date": rule.effective_from, "stops_from": rule.effective_to,
            "status": rule.status}


def _rule(rule_id):
    rule = WeeklyOffRule.objects.select_related("branch").filter(pk=rule_id).first()
    if rule is None:
        raise ApiError("not_found", "No such weekly off in this company.")
    return rule


class WeeklyOffListView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:schedule_overview", "POST": "scheduling:weekly_off_create"}

    @endpoint(
        id="weekly-offs-list", area=AREA, title="Weekly off days",
        summary="The days off every week, company-wide or for a branch.",
        what_it_does=["Lists every weekly off, active first."],
        description="Stopped ones stay listed: past attendance still treats those days as off.",
        roles=READERS, scopes=["shifts:read"], paginated=True,
        response=s.WeeklyOffSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [WEEKLY_OFF_EXAMPLE]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        _membership(request)
        rows = [_rule_out(rule) for rule in WeeklyOffRule.objects.select_related("branch")
                .order_by("status", "weekday", "branch__name")]
        return self.paginated(request, rows, s.WeeklyOffSerializer)

    @endpoint(
        id="weekly-offs-add", area=AREA, title="Add weekly off days",
        summary="One or more days off every week, from a date.",
        what_it_does=["Adds one weekly off per day given, so each can be stopped on its own.",
                      "All or nothing: a day already off for the same branch refuses the lot."],
        description="Leave branch_id out (or null) for every branch.",
        roles=ADMINS, scopes=["shifts:write"], response_status=201,
        request=s.WeeklyOffInputSerializer, response=s.WeeklyOffSerializer,
        request_example={"weekdays": ["friday", "saturday"], "from_date": "2026-11-01"},
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [WEEKLY_OFF_EXAMPLE]},
        paginated=True, errors=WRITE_ERRORS,
    )
    def post(self, request):
        membership = require_structure_manager(request.user, request.company_id)
        data = s.WeeklyOffInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"branch": "branch_id", "effective_from": "from_date"}
        from scheduling.forms import WeeklyOffForm

        form = WeeklyOffForm(data=_multi({
            "weekdays": [str(WEEKDAY_NUMBER[d]) for d in values["weekdays"]],
            "branch": values.get("branch_id") or "",
            "effective_from": values["from_date"].isoformat()}),
            branches=visible_branches(membership))
        if not form.is_valid():
            refuse(form.errors, names)
        with service_errors(names):
            rules = services.add_weekly_offs(actor=request.user, company_id=request.company_id,
                                             values=form.cleaned_data)
        response = self.paginated(request, [_rule_out(rule) for rule in rules],
                                  s.WeeklyOffSerializer)
        response.status_code = 201
        return response


def _multi(values):
    """Form data with a list field, as a browser posts it."""
    from django.http import QueryDict

    data = QueryDict(mutable=True)
    for key, value in values.items():
        if isinstance(value, list):
            data.setlist(key, value)
        else:
            data[key] = value
    return data


class WeeklyOffStartView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:weekly_off_start"

    @endpoint(
        id="weekly-offs-start", area=AREA, title="Change when a weekly off starts",
        summary="A new first date for a weekly off.",
        what_it_does=["Moves its start; attendance is updated for the dates that change.",
                      "Finalised salary months stay as they are."],
        description="It may not overlap another weekly off on the same day.",
        roles=ADMINS, scopes=["shifts:write"], params=[RULE_PARAM],
        request=s.WeeklyOffStartSerializer, response=s.WeeklyOffSerializer,
        request_example={"from_date": "2026-10-01"},
        response_example={**WEEKLY_OFF_EXAMPLE, "from_date": "2026-10-01"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, rule_id):
        rule = _rule(rule_id)
        data = s.WeeklyOffStartSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        names = {"effective_from": "from_date"}
        form = checked(ChangeWeeklyOffStartForm,
                       {"effective_from": data.validated_data["from_date"].isoformat()}, names)
        with service_errors(names):
            services.change_weekly_off_start(actor=request.user, company_id=request.company_id,
                                             rule_id=rule.pk,
                                             effective_from=form.cleaned_data["effective_from"])
        return Response(s.WeeklyOffSerializer(_rule_out(_rule(rule_id))).data)


class WeeklyOffEndView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:weekly_off_end"

    @endpoint(
        id="weekly-offs-end", area=AREA, title="Stop a weekly off",
        summary="A weekly off no longer applies from a date.",
        what_it_does=["Stops it from the date; it is not deleted, so earlier days keep it."],
        description="Past attendance still treats it as a day off.",
        roles=ADMINS, scopes=["shifts:write"], params=[RULE_PARAM],
        request=s.WeeklyOffEndSerializer, response=s.WeeklyOffSerializer,
        request_example={"stops_from": "2027-01-01"},
        response_example={**WEEKLY_OFF_EXAMPLE, "stops_from": "2027-01-01", "status": "ended"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, rule_id):
        rule = _rule(rule_id)
        data = s.WeeklyOffEndSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        names = {"effective_to": "stops_from"}
        form = checked(EndWeeklyOffForm,
                       {"effective_to": data.validated_data["stops_from"].isoformat()}, names)
        with service_errors(names):
            services.end_weekly_off(actor=request.user, company_id=request.company_id,
                                    rule_id=rule.pk,
                                    effective_to=form.cleaned_data["effective_to"])
        return Response(s.WeeklyOffSerializer(_rule_out(_rule(rule_id))).data)


# --- holidays -----------------------------------------------------------------------------

HOLIDAY_EXAMPLE = {"id": 12, "date": "2026-12-16", "name": "Victory Day", "branch": None,
                   "description": "", "status": "active"}
HOLIDAY_PARAM = Param("holiday_id", PATH, "integer", "The holiday id.", required=True,
                      example=12)
HOLIDAY_NAMES = {"holiday_date": "date", "branch": "branch_id"}


def _holiday(holiday_id):
    holiday = Holiday.objects.select_related("branch").filter(pk=holiday_id).first()
    if holiday is None:
        raise ApiError("not_found", "No such holiday in this company.")
    return holiday


class HolidayListView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:holiday_list", "POST": "scheduling:holiday_create"}

    @endpoint(
        id="holidays-list", area=AREA, title="Holidays",
        summary="The holidays of a year.",
        what_it_does=["Lists the year's holidays by date (this year unless given)."],
        description="Filter by status (active or cancelled).",
        roles=READERS, scopes=["shifts:read"], paginated=True,
        params=[Param("year", QUERY, "integer", "The year; this year when left out.", example=2026),
                Param("status", QUERY, "string", "active or cancelled.", example="active")],
        response=s.HolidaySerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [HOLIDAY_EXAMPLE]},
        errors=READ_ERRORS,
    )
    def get(self, request):
        _membership(request)
        raw = request.query_params.get("year", "")
        year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= 2100 else timezone.localdate().year
        rows = Holiday.objects.select_related("branch").filter(holiday_date__year=year)
        if request.query_params.get("status") in dict(Holiday.Status.choices):
            rows = rows.filter(status=request.query_params["status"])
        return self.paginated(request, rows.order_by("holiday_date", "name"), s.HolidaySerializer)

    @endpoint(
        id="holidays-create", area=AREA, title="Add a holiday",
        summary="One holiday, for every branch or one.",
        what_it_does=["Adds it; the day is paid and nobody is absent on it."],
        description="A date already a holiday for the same branch (or for all) is refused. For "
                    "many at once use POST /api/v1/holidays/year.",
        roles=ADMINS, scopes=["shifts:write"], response_status=201,
        request=s.HolidayInputSerializer, response=s.HolidaySerializer,
        request_example={"date": "2026-12-16", "name": "Victory Day"},
        response_example=HOLIDAY_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        membership = require_structure_manager(request.user, request.company_id)
        data = s.HolidayInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        form = checked(HolidayForm, _holiday_form_data(None, values, membership),
                       HOLIDAY_NAMES, branches=visible_branches(membership))
        with service_errors(HOLIDAY_NAMES):
            holiday = services.create_holiday(actor=request.user, company_id=request.company_id,
                                              values=form.cleaned_data)
        return Response(s.HolidaySerializer(_holiday(holiday.pk)).data, status=201)


def _holiday_form_data(holiday, values, membership):
    changes = dict(values)
    if "date" in changes:
        changes["holiday_date"] = changes.pop("date").isoformat()
    return form_data(HolidayForm, holiday, changes, {"branch": "branch_id"},
                     branches=visible_branches(membership))


class HolidayYearView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:holiday_year"

    @endpoint(
        id="holidays-year", area=AREA, title="Add many holidays",
        summary="Many holidays at once, each with its name - the panel's year calendar.",
        what_it_does=["Adds every date given, for every branch or one.",
                      "All or nothing: a date already a holiday refuses the lot, naming it."],
        description="At most a year of dates at a time; each date once and with a name.",
        roles=ADMINS, scopes=["shifts:write"], response_status=201,
        request=s.HolidayYearSerializer, response=s.HolidaySerializer, paginated=True,
        request_example={"branch_id": None, "days": [
            {"date": "2026-02-21", "name": "Language Martyrs' Day"},
            {"date": "2026-03-26", "name": "Independence Day"}]},
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [HOLIDAY_EXAMPLE]},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        membership = require_structure_manager(request.user, request.company_id)
        data = s.HolidayYearSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        branch = None
        branch_id = data.validated_data.get("branch_id")
        if branch_id:
            branch = visible_branches(membership).filter(pk=branch_id).first()
            if branch is None:
                refuse({"branch_id": ["Choose a branch of this company you may manage."]})
        days = [(row["date"], row["name"]) for row in data.validated_data["days"]]
        with service_errors({"branch": "branch_id"}):
            added = services.add_holidays(actor=request.user, company_id=request.company_id,
                                          values={"branch": branch, "days": days})
        rows = Holiday.objects.select_related("branch").filter(
            pk__in=[h.pk for h in added]).order_by("holiday_date")
        response = self.paginated(request, rows, s.HolidaySerializer)
        response.status_code = 201
        return response


class HolidayDetailView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "scheduling:holiday_list", "PATCH": "scheduling:holiday_edit"}

    @endpoint(
        id="holidays-get", area=AREA, title="One holiday",
        summary="One holiday's details.",
        what_it_does=["Answers the holiday."],
        description="Another company's holiday answers not_found.",
        roles=READERS, scopes=["shifts:read"], params=[HOLIDAY_PARAM],
        response=s.HolidaySerializer, response_example=HOLIDAY_EXAMPLE,
        errors=READ_ERRORS + ["not_found"],
    )
    def get(self, request, holiday_id):
        _membership(request)
        return Response(s.HolidaySerializer(_holiday(holiday_id)).data)

    @endpoint(
        id="holidays-change", area=AREA, title="Change a holiday",
        summary="A new date, name, branch or description.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description="The new date may not already be a holiday for the same branch.",
        roles=ADMINS, scopes=["shifts:write"], params=[HOLIDAY_PARAM],
        request=s.HolidayInputSerializer, response=s.HolidaySerializer,
        request_example={"name": "Victory Day (observed)"},
        response_example={**HOLIDAY_EXAMPLE, "name": "Victory Day (observed)"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def patch(self, request, holiday_id):
        holiday = _holiday(holiday_id)
        membership, _h = services.get_holiday_for_edit(
            actor=request.user, company_id=request.company_id, holiday_id=holiday.pk)
        data = s.HolidayInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(HolidayForm, _holiday_form_data(holiday, dict(data.validated_data),
                                                       membership),
                       HOLIDAY_NAMES, instance=holiday, branches=visible_branches(membership))
        with service_errors(HOLIDAY_NAMES):
            services.update_holiday(actor=request.user, company_id=request.company_id,
                                    holiday_id=holiday.pk, values=form.cleaned_data)
        return Response(s.HolidaySerializer(_holiday(holiday_id)).data)


class HolidayCancelView(ScheduleView):
    permission_classes = [PanelRule]
    panel_page = "scheduling:holiday_cancel"

    @endpoint(
        id="holidays-cancel", area=AREA, title="Cancel a holiday",
        summary="The day is a working day again.",
        what_it_does=["Cancels it; it stays listed as cancelled, for the record."],
        description="Attendance for that day is worked out again.",
        roles=ADMINS, scopes=["shifts:write"], params=[HOLIDAY_PARAM],
        response=s.HolidaySerializer, response_example={**HOLIDAY_EXAMPLE, "status": "cancelled"},
        errors=WRITE_ERRORS + ["not_found"],
    )
    def post(self, request, holiday_id):
        holiday = _holiday(holiday_id)
        with service_errors():
            services.cancel_holiday(actor=request.user, company_id=request.company_id,
                                    holiday_id=holiday.pk)
        return Response(s.HolidaySerializer(_holiday(holiday_id)).data)
