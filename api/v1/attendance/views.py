"""Attendance: the daily list, late entries, who is in now, one person's
month and day, the days to review, fixing a day, and missed scans
(docs/api/00-PLAN.md phase 7; the guide: docs/api/70-attendance.md).

Live, like the panel: the days asked for are brought up to date first
(``attendance.services.refresh``). Each endpoint uses its panel page's gate
and the panel's services (``attendance.access``, ``correction_services``,
``scan_requests``), which limit everything to the branches the caller may see
or fix.
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.attendance import serializers as s
from api.v1.devices.views import _moment
from attendance import access, correction_services, live_status, month_view, scan_requests
from attendance.forms import AddScanForm, EnterMissingForm
from attendance.models import AttendanceCorrection, AttendanceRecord
from attendance.services import _is_locked, locked_ranges, month_bounds, refresh
from employees.models import Employee
from organization.access_services import people
from tenants.models import Company

AREA = "attendance"
VIEWERS = ["Whoever may see attendance: the owner, company administrator, HR, or anyone given "
           "View attendance in a branch (a branch manager in theirs; a department head for "
           "their department)"]
FIXERS = ["Whoever may fix attendance in that day's branch: the owner, company administrator, "
          "HR, or anyone given Fix attendance there"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required", "scope_missing",
          "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field", "not_found"]
EMPLOYEE_PARAM = Param("employee_id", PATH, "integer", "The employee id.", required=True,
                       example=41)
DATE_PARAM = Param("date", PATH, "string (date)", "The working day, YYYY-MM-DD.",
                   required=True, example="2026-10-05")
DAY_EXAMPLE = {"id": 9001, "date": "2026-10-05", "employee": {"id": 41, "name": "Rahim Uddin"},
               "employee_code": "E-0041", "branch": {"id": 3, "name": "Chattogram"},
               "status": "present", "check_in": "2026-10-05T09:02:08+06:00",
               "check_out": "2026-10-05T18:05:00+06:00", "worked_minutes": 483,
               "in_office_minutes": 543, "late_minutes": 0, "early_out_minutes": 0,
               "overtime_minutes": 0, "approved_overtime_minutes": 0, "payable_fraction": "1.00",
               "needs_review": False, "note": ""}
MONTH_PARAMS = [
    Param("year", QUERY, "integer", "The year (this year when left out).", example=2026),
    Param("month", QUERY, "integer", "The month, 1-12 (this month when left out).", example=10)]


def _ref(row):
    return {"id": row.pk, "name": getattr(row, "full_name", None) or row.name} if row else None


def _day_out(record):
    assignment = record.employee_assignment
    return {
        "id": record.pk, "date": record.work_date, "employee": _ref(record.employee),
        "employee_code": assignment.employee_code if assignment else None,
        "branch": _ref(record.branch) if record.branch_id else None,
        "status": record.attendance_status, "check_in": record.first_in_at,
        "check_out": record.last_out_at, "worked_minutes": record.worked_minutes,
        "in_office_minutes": record.in_office_minutes, "late_minutes": record.late_minutes,
        "early_out_minutes": record.early_out_minutes,
        "overtime_minutes": record.calculated_overtime_minutes,
        "approved_overtime_minutes": record.approved_overtime_minutes,
        "payable_fraction": record.payable_fraction,
        "needs_review": record.review_status == "needs_review", "note": record.note,
    }


def _day(raw):
    try:
        return datetime.date.fromisoformat(str(raw))
    except ValueError:
        raise ApiError("not_found", "That is not a date (YYYY-MM-DD).") from None


def _tz(company_id):
    return Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first() or "UTC"


class _Query:
    """What the panel's Daily list query reads from a request."""

    def __init__(self, request, params):
        self.user, self.company_id, self.GET = request.user, request.company_id, params


class AttendanceView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "attendance:read", "attendance:write"
    throttle_scope = "write"

    def page(self, request, rows, build, serializer):
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer([build(r) for r in chunk],
                                                           many=True).data)


LIST_PARAMS = MONTH_PARAMS + [
    Param("on", QUERY, "string (date)", "Just this day.", example="2026-10-05"),
    Param("from", QUERY, "string (date)", "From this day (with to; at most 366 days).",
          example="2026-10-01"),
    Param("to", QUERY, "string (date)", "To this day.", example="2026-10-31"),
    Param("branch_id", QUERY, "integer", "Only this branch.", example=3),
    Param("employee_id", QUERY, "integer", "Only this person.", example=41),
    Param("status", QUERY, "string", "present, half_day, absent, leave, holiday, weekly_off, "
                                     "incomplete or inactive.", example="absent")]


def _daily(request, late_only):
    from attendance.views import daily_list_query

    q = request.query_params
    params = {"year": q.get("year", ""), "month": q.get("month", ""), "on": q.get("on", ""),
              "date_from": q.get("from", ""), "date_to": q.get("to", ""),
              "branch": q.get("branch_id", ""), "employee": q.get("employee_id", ""),
              "status": q.get("status", "")}
    daily = daily_list_query(_Query(request, params), request.company_id, late_only=late_only)
    if not daily["date_filter"].is_valid():
        names = {"date_from": "from", "date_to": "to"}
        refuse(daily["date_filter"].errors, names)
    return daily


class DailyListView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_list"

    @endpoint(
        id="attendance-list", area=AREA, title="Daily attendance",
        summary="One row per person per day: status, check-in, check-out, worked, late.",
        what_it_does=["Lists the days of a month (this month unless given), or of a date or a "
                      "range, worked out up to now.",
                      "Only the branches - and people - you may see."],
        description=("The days are brought up to date before they are answered, so a day "
                     "whose shift just ended shows its final answer. Filter by branch, person or "
                     "status."),
        roles=VIEWERS, scopes=["attendance:read"], paginated=True, params=LIST_PARAMS,
        response=s.DaySerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [DAY_EXAMPLE]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        daily = _daily(request, late_only=False)
        return self.page(request, daily["queryset"], _day_out, s.DaySerializer)


class LateListView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_late"

    @endpoint(
        id="attendance-late", area=AREA, title="Late entries",
        summary="The days someone came in late (after the shift's grace minutes).",
        what_it_does=["Lists only the late days, with the same filters as the daily list."],
        description="late_minutes is already net of the grace minutes.",
        roles=VIEWERS, scopes=["attendance:read"], paginated=True, params=LIST_PARAMS,
        response=s.DaySerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [{**DAY_EXAMPLE, "late_minutes": 12}]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        daily = _daily(request, late_only=True)
        return self.page(request, daily["queryset"], _day_out, s.DaySerializer)


class ExportView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_list"

    @endpoint(
        id="attendance-export", area=AREA, title="Download the daily list",
        summary="The daily list as an Excel (xlsx) or PDF file.",
        what_it_does=["Answers the file - the same rows, filters and limits as the list."],
        description="Large downloads are refused with a message (Excel 10,000 rows, PDF 1,500): "
                    "narrow the dates or filters.",
        roles=VIEWERS, scopes=["attendance:read"],
        params=LIST_PARAMS + [Param("file_type", QUERY, "string", "xlsx (default) or pdf.",
                                    example="xlsx"),
                              Param("late_only", QUERY, "boolean", "true: late entries only.",
                                    example="false")],
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        from attendance.exports import export_daily_list
        from common import exports

        fmt = request.query_params.get("file_type") or "xlsx"
        if fmt not in ("xlsx", "pdf"):
            refuse({"file_type": ["xlsx or pdf."]})
        daily = _daily(request, late_only=request.query_params.get("late_only") == "true")
        try:
            exports.check_size(daily["queryset"].count(), fmt, noun="attendance rows")
        except exports.TooManyRows as refusal:
            raise ApiError("validation_error", str(refusal)) from None
        return export_daily_list(request._request, request.company_id, daily, fmt)


class NowView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_now"

    @endpoint(
        id="attendance-now", area=AREA, title="Who is in now",
        summary="Each person's status right now: in, on a break, left, not in yet, …",
        what_it_does=["Works it out from today's scans; nothing is stored, so ask as often as "
                      "needed (once a minute is plenty)."],
        description="Give employee_ids (comma-separated), or leave it out for everyone you "
                    "may see.",
        roles=["Whoever may see employees or attendance (as the Employees list's Now column)"],
        scopes=["attendance:read"], paginated=True,
        params=[Param("employee_ids", QUERY, "string", "Comma-separated employee ids.",
                      example="41,42")],
        response=s.NowSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [
            {"employee_id": 41, "key": "in", "label": "In", "tone": "success",
             "since": "09:02", "detail": ""}]},
        errors=ERRORS,
    )
    def get(self, request):
        _m, visible = access.now_scope(request.user, request.company_id)
        wanted = [int(v) for v in (request.query_params.get("employee_ids") or "").split(",")
                  if v.strip().isdigit()] or None
        if not visible.is_all:
            allowed = set(people(visible).values_list("pk", flat=True))
            wanted = sorted(allowed if wanted is None else allowed.intersection(wanted))
            if not wanted:
                return self.paginated(request, [], s.NowSerializer)
        statuses = live_status.statuses_for(request.company_id, employee_ids=wanted)
        rows = [{"employee_id": pk, **status.as_dict()} for pk, status in sorted(statuses.items())]
        return self.paginated(request, rows, s.NowSerializer)


class CalendarView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_calendar"

    @endpoint(
        id="attendance-calendar", area=AREA, title="One person's month",
        summary="Every day of a month for one person, and the month's totals.",
        what_it_does=["Brings the month up to date and answers each day and the summary."],
        description="Only someone placed in a branch you may see; days worked in other "
                    "branches are left out for a branch login.",
        roles=VIEWERS, scopes=["attendance:read"],
        params=[Param("employee_id", QUERY, "integer", "The person.", required=True, example=41)]
        + MONTH_PARAMS,
        response=s.CalendarSerializer,
        response_example={"employee": {"id": 41, "name": "Rahim Uddin"}, "year": 2026,
                          "month": 10, "days": [{"date": "2026-10-01", "status": "present",
                                                 "label": "Present", "late": False,
                                                 "check_in": "09:02", "check_out": "18:05",
                                                 "worked": "8h 03m", "note": ""}],
                          "summary": {"present": 20, "late": 1, "absent": 0,
                                      "worked": "160h 10m"}},
        errors=ERRORS + ["not_found", "validation_error"],
    )
    def get(self, request):
        from attendance.views import read_month

        _m, visible = access.view_scope(request.user, request.company_id)
        raw = request.query_params.get("employee_id", "")
        if not raw.isdigit():
            refuse({"employee_id": ["Give the person's id."]})
        pickable = Employee.objects.all() if visible.is_all else people(visible)
        employee = pickable.filter(pk=int(raw)).first()
        if employee is None:
            if Employee.objects.filter(pk=int(raw)).exists():
                raise PermissionDenied("That person is not in a branch whose attendance you see.")
            raise ApiError("not_found", "No such employee in this company.")
        year, month = read_month(request.query_params)
        first, last = month_bounds(year, month)
        refresh(request.company_id, employee_ids=[employee.pk], start=first, end=last)
        built = month_view.build_month(employee=employee, year=year, month=month,
                                       company_timezone=_tz(request.company_id),
                                       today=timezone.localdate(), branches=visible)
        return Response(s.CalendarSerializer({
            "employee": _ref(employee), "year": year, "month": month,
            "days": [{"date": d.date, "status": d.status, "label": d.status_label,
                      "late": d.is_late, "check_in": d.check_in, "check_out": d.check_out,
                      "worked": d.worked, "note": d.note} for d in built["days"]],
            "summary": built["summary"]}).data)


def _detail(request, employee_id, day):
    company_tz = _tz(request.company_id)
    access.require_view_day(request.user, request.company_id, employee_id, day,
                            month_view.zone(company_tz))
    refresh(request.company_id, employee_ids=[employee_id], start=day, end=day)
    record = (AttendanceRecord.objects.select_related("shift", "employee", "branch",
                                                      "employee_assignment")
              .filter(employee_id=employee_id, work_date=day).first())
    scans, shift = [], ""
    if record is not None:
        built = month_view.build_day_detail(record=record, company_timezone=company_tz)
        scans = [{"time": line["time"], "label": line["label"], "device": line["device"] or "",
                  "added_by_hand": line["is_manual"], "counted": line["is_included"],
                  "note": line["note"] or ""} for line in built["scans"]]
        shift = " ".join(part for part in (built.get("shift_name"), built.get("shift_times"))
                         if part)
    corrections = [{
        "id": c.pk, "type": c.correction_type, "scan_at": c.proposed_event_at,
        "new_status": c.proposed_status, "reason": c.reason, "status": c.status,
        "by": (c.approved_by or c.created_by).email if (c.approved_by_id or c.created_by_id)
        else None, "at": c.created_at}
        for c in correction_services.corrections_for_day(request.company_id, employee_id, day)]
    return {"day": _day_out(record) if record else None, "shift": shift, "scans": scans,
            "corrections": corrections,
            "may_fix": correction_services.may_correct(request.user, request.company_id,
                                                       employee_id, day),
            "locked": _is_locked(day, locked_ranges(request.company_id))}


def _employee(employee_id):
    if not Employee.objects.filter(pk=employee_id).exists():
        raise ApiError("not_found", "No such employee in this company.")


DETAIL_EXAMPLE = {"day": DAY_EXAMPLE, "shift": "Day shift 09:00-18:00",
                  "scans": [{"time": "09:02:08", "label": "Check-in", "device": "Main gate",
                             "added_by_hand": False, "counted": True, "note": ""}],
                  "corrections": [], "may_fix": True, "locked": False}


class DayView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_day"

    @endpoint(
        id="attendance-day", area=AREA, title="One person's day",
        summary="One day in full: every scan and how it was read, and the fixes made.",
        what_it_does=["Brings the day up to date and answers it, its scans and corrections.",
                      "Says whether you may fix it."],
        description="A day in a branch you may not see answers permission_denied.",
        roles=VIEWERS, scopes=["attendance:read"], params=[EMPLOYEE_PARAM, DATE_PARAM],
        response=s.DayDetailSerializer, response_example=DETAIL_EXAMPLE,
        errors=ERRORS + ["not_found"],
    )
    def get(self, request, employee_id, date):
        _employee(employee_id)
        return Response(s.DayDetailSerializer(_detail(request, employee_id, _day(date))).data)


class ReviewView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_review"

    @endpoint(
        id="attendance-review", area=AREA, title="Days to review",
        summary="Days that closed without a clear answer and need a person.",
        what_it_does=["Brings this month and last up to date, then lists the days to review in "
                      "the branches where you may fix attendance."],
        description="Fix each with add-scan, change-status or accept-review.",
        roles=FIXERS, scopes=["attendance:read"], paginated=True,
        response=s.ReviewSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [
            {"day": {**DAY_EXAMPLE, "status": "incomplete", "needs_review": True},
             "reason": "check-out by rule, no scan", "department": "Software"}]},
        errors=ERRORS,
    )
    def get(self, request):
        membership, fixable = access.fix_scope(request.user, request.company_id)
        today = timezone.now().astimezone(month_view.zone(membership.company.timezone)).date()
        refresh(request.company_id,
                start=(today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1),
                end=today)
        rows = correction_services.review_queryset(request.company_id, fixable).select_related(
            "branch")

        def build(record):
            assignment = record.employee_assignment
            department = assignment.department.name if assignment and assignment.department_id \
                else ""
            return {"day": _day_out(record), "reason": record.review_reason or "",
                    "department": department}
        return self.page(request, rows, build, s.ReviewSerializer)


# --- fixing a day -------------------------------------------------------------------------

def _fix(request, employee_id, date, do):
    _employee(employee_id)
    day = _day(date)
    correction_services.require_corrector(request.user, request.company_id, employee_id, day)
    common = {"actor": request.user, "company_id": request.company_id,
              "employee_id": employee_id, "work_date": day}
    with service_errors():
        do(common)
    return Response(s.DayDetailSerializer(_detail(request, employee_id, day)).data)


FIX_PARAMS = [EMPLOYEE_PARAM, DATE_PARAM]


class AddScanView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_day_fix"

    @endpoint(
        id="attendance-add-scan", area=AREA, title="Add a missing scan",
        summary="A scan the device did not record; the day is worked out again with it.",
        what_it_does=["Adds the scan as a correction (kept, with the reason) and rebuilds the "
                      "day."],
        description="The time must belong to this day (a night shift's after-midnight scan "
                    "takes the next date). Not in a finalised salary month.",
        roles=FIXERS, scopes=["attendance:write"], params=FIX_PARAMS,
        request=s.AddScanSerializer, response=s.DayDetailSerializer,
        request_example={"at": "2026-10-05T18:02", "reason": "The front door device was offline"},
        response_example=DETAIL_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request, employee_id, date):
        data = s.AddScanSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        day_part, time_part = _moment(data.validated_data["at"], "at")
        form = checked(AddScanForm, {"at_0": day_part, "at_1": time_part,
                                     "reason": data.validated_data["reason"]})
        return _fix(request, employee_id, date, lambda c: correction_services.add_scan(
            at=form.cleaned_data["at"], reason=form.cleaned_data["reason"], **c))


class ChangeStatusView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_day_fix"

    @endpoint(
        id="attendance-change-status", area=AREA, title="Change a day's status",
        summary="Mark the day present, half day or absent, with a reason.",
        what_it_does=["Records the change as a correction; the day then counts as marked."],
        description="Not for a day still open, a leave day, or one in a finalised month.",
        roles=FIXERS, scopes=["attendance:write"], params=FIX_PARAMS,
        request=s.ChangeStatusSerializer, response=s.DayDetailSerializer,
        request_example={"status": "present", "reason": "Worked at the client's office all day"},
        response_example=DETAIL_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request, employee_id, date):
        data = s.ChangeStatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _fix(request, employee_id, date, lambda c: correction_services.change_status(
            status=data.validated_data["status"], reason=data.validated_data["reason"], **c))


class AcceptReviewView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_day_fix"

    @endpoint(
        id="attendance-accept-review", area=AREA, title="Accept a day as it is",
        summary="The day to review is right as worked out; it no longer needs a review.",
        what_it_does=["Records the acceptance, with the reason."],
        description="For the review reasons that can be accepted (e.g. a check-out by rule).",
        roles=FIXERS, scopes=["attendance:write"], params=FIX_PARAMS,
        request=s.ReasonSerializer, response=s.DayDetailSerializer,
        request_example={"reason": "Confirmed with their manager they left at 18:00"},
        response_example=DETAIL_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request, employee_id, date):
        data = s.ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return _fix(request, employee_id, date, lambda c: correction_services.accept_review(
            reason=data.validated_data["reason"], **c))


class ExcuseLateView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_late"

    @endpoint(
        id="attendance-excuse-late", area=AREA, title="Approve a late arrival",
        summary="The day counts no late minutes.",
        what_it_does=["Records the approval as a correction; the day is worked out again."],
        description="For a day they came in late, within the last two months. Not your own.",
        roles=FIXERS, scopes=["attendance:write"], params=FIX_PARAMS,
        request=s.ReasonSerializer, response=s.DayDetailSerializer,
        request_example={"reason": "Doctor's appointment, agreed beforehand"},
        response_example=DETAIL_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request, employee_id, date):
        from organization import employee_actions as actions
        from organization import employee_detail_services

        data = s.ReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        _employee(employee_id)
        day = _day(date)
        membership, employee, _a, _c = employee_detail_services.get_employee_for_edit(
            actor=request.user, company_id=request.company_id, employee_id=employee_id,
            code="employees.view")
        today = employee_detail_services.company_today(membership.company)
        late = actions.late_days(request.company_id, employee, today)
        if day not in {record.work_date for record in late}:
            refuse({"date": ["Not one of their late days of the last two months still to "
                             "approve."]})
        form = checked(actions.LateForm, {"work_date": day.isoformat(),
                                          "reason": data.validated_data["reason"]}, days=late)
        with service_errors():
            correction_services.excuse_late(actor=request.user, company_id=request.company_id,
                                            employee_id=employee_id, work_date=day,
                                            reason=form.cleaned_data["reason"])
        return Response(s.DayDetailSerializer(_detail(request, employee_id, day)).data)


class WithdrawView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:attendance_correction_withdraw"

    @endpoint(
        id="attendance-withdraw", area=AREA, title="Withdraw a correction",
        summary="Take a fix back; the day is worked out again without it.",
        what_it_does=["Withdraws the correction (it stays listed as withdrawn)."],
        description="Not in a finalised salary month.",
        roles=FIXERS, scopes=["attendance:write"],
        params=[Param("correction_id", PATH, "integer", "The correction id.", required=True,
                      example=77)],
        request=s.WithdrawSerializer, response=s.DayDetailSerializer,
        request_example={"note": "Added to the wrong day"}, response_example=DETAIL_EXAMPLE,
        errors=WRITE_ERRORS,
    )
    def post(self, request, correction_id):
        access.fix_scope(request.user, request.company_id)
        correction = AttendanceCorrection.objects.filter(pk=correction_id).first()
        if correction is None:
            raise ApiError("not_found", "No such correction in this company.")
        correction_services.require_corrector(request.user, request.company_id,
                                              correction.employee_id, correction.work_date)
        data = s.WithdrawSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            correction_services.withdraw(actor=request.user, company_id=request.company_id,
                                         correction_id=correction.pk,
                                         note=data.validated_data.get("note", ""))
        return Response(s.DayDetailSerializer(
            _detail(request, correction.employee_id, correction.work_date)).data)


# --- missed scans ---------------------------------------------------------------------------

def _missed_out(item):
    return {"id": item.pk, "employee": _ref(item.employee), "branch": _ref(item.branch),
            "kind": item.kind, "date": item.work_date, "scan_at": item.scan_at,
            "scan_out_at": item.scan_out_at, "reason": item.reason, "status": item.status,
            "submitted_at": item.submitted_at,
            "decided_by": item.decided_by.email if item.decided_by_id else None,
            "decided_at": item.decided_at, "decision_note": item.decision_note}


MISSED_EXAMPLE = {"id": 15, "employee": {"id": 41, "name": "Rahim Uddin"},
                  "branch": {"id": 3, "name": "Chattogram"}, "kind": "check_out",
                  "date": "2026-10-04", "scan_at": "2026-10-04T18:05:00+06:00",
                  "scan_out_at": None, "reason": "Left with the visitors through the side gate",
                  "status": "pending", "submitted_at": "2026-10-05T09:10:00+06:00",
                  "decided_by": None, "decided_at": None, "decision_note": ""}


class MissedScanListView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:missed_scan_list"

    @endpoint(
        id="missed-scans", area=AREA, title="Missed scans to decide",
        summary="Requests from people whose attendance you may fix.",
        what_it_does=["Lists the requests (waiting first unless status is given). Never your "
                      "own."],
        description="An employee reports their own from their app (the My account area).",
        roles=FIXERS, scopes=["attendance:read"], paginated=True,
        params=[Param("status", QUERY, "string", "pending (default), approved or rejected.",
                      example="pending")],
        response=s.MissedScanSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [MISSED_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        _m, rows = scan_requests.reviewable(request.user, request.company_id)
        status = request.query_params.get("status", "pending")
        if status not in ("pending", "approved", "rejected"):
            status = "pending"
        rows = (rows.filter(status=status).select_related("employee", "branch", "decided_by")
                .order_by("submitted_at" if status == "pending" else "-decided_at", "pk"))
        return self.page(request, rows, _missed_out, s.MissedScanSerializer)


class MissedScanDecideView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:missed_scan_decide"

    @endpoint(
        id="missed-scans-decide", area=AREA, title="Decide a missed scan",
        summary="Approve (the scan is added and the day worked out again) or reject.",
        what_it_does=["Decides the request; an approval adds the scan as a correction."],
        description="A note is needed when rejecting; the employee can read it. Not your own "
                    "request, nor one you entered.",
        roles=FIXERS, scopes=["attendance:write"],
        params=[Param("request_id", PATH, "integer", "The request id.", required=True,
                      example=15)],
        request=s.DecideSerializer, response=s.MissedScanSerializer,
        request_example={"decision": "approve", "note": "Checked with the guard"},
        response_example={**MISSED_EXAMPLE, "status": "approved"}, errors=WRITE_ERRORS,
    )
    def post(self, request, request_id):
        _m, rows = scan_requests.reviewable(request.user, request.company_id)
        item = rows.filter(pk=request_id).first()
        if item is None:
            raise ApiError("not_found", "No such request among those you may decide.")
        data = s.DecideSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            scan_requests.decide(actor=request.user, company_id=request.company_id,
                                 request_id=item.pk,
                                 approve=data.validated_data["decision"] == "approve",
                                 note=data.validated_data.get("note", ""))
        item = rows.model.objects.select_related("employee", "branch", "decided_by").get(
            pk=item.pk)
        return Response(s.MissedScanSerializer(_missed_out(item)).data)


class EnterMissingView(AttendanceView):
    permission_classes = [PanelRule]
    panel_page = "attendance:missed_scan_enter"

    @endpoint(
        id="missed-scans-enter", area=AREA, title="Enter missing attendance for someone",
        summary="A missing check-in, check-out, both, or a whole day, for a person you look after.",
        what_it_does=["Files it as a missed-scan request for someone else to approve.",
                      "The owner or company administrator may approve it at once (approve_now)."],
        description=("For HR, their branch manager or department head. A scan's day is worked "
                     "out from its date and time; a whole missing day uses the shift's times."),
        roles=["Whoever may see the person's attendance (not for yourself)"],
        scopes=["attendance:write"], params=[EMPLOYEE_PARAM], response_status=201,
        request=s.EnterMissingSerializer, response=s.MissedScanSerializer,
        request_example={"kind": "check_out", "at": "2026-10-04T18:05",
                         "reason": "Left with the visitors through the side gate"},
        response_example=MISSED_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request, employee_id):
        _employee(employee_id)
        data = s.EnterMissingSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        may_approve = scan_requests.may_approve_own(request.user, request.company_id)
        form_input = {"kind": values["kind"], "reason": values["reason"],
                      "work_date": values["work_date"].isoformat()
                      if values.get("work_date") else ""}
        for name in ("at", "at_out"):
            form_input[f"{name}_0"], form_input[f"{name}_1"] = _moment(values.get(name, ""), name)
        if may_approve and values["approve_now"]:
            form_input["approve_now"] = "on"
        form = checked(EnterMissingForm, form_input, approve_now=may_approve)
        cleaned = form.cleaned_data
        approve = may_approve and cleaned.get("approve_now")
        with service_errors():
            entered = (scan_requests.enter_and_approve if approve else scan_requests.enter_for)(
                actor=request.user, company_id=request.company_id, employee_id=employee_id,
                work_date=cleaned["work_date"], kind=cleaned["kind"], at=cleaned["at"],
                at_out=cleaned["at_out"], reason=cleaned["reason"])
        entered = type(entered).objects.select_related("employee", "branch", "decided_by").get(
            pk=entered.pk)
        return Response(s.MissedScanSerializer(_missed_out(entered)).data, status=201)
