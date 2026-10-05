"""My account: the logged-in employee's own profile, attendance, missed
scans, leave, payslips and LFA - the employee app's screens
(docs/api/00-PLAN.md phase 10; the guide: docs/api/100-employee-app.md).

The panel's My account pages, through their own services: the employee is
always the one linked to the login, never one named in the address. Open to
every login in the company, as the panel's /me/ pages are; API keys cannot
use them - a key is a machine, not a person.
"""

import datetime
from collections import Counter
from decimal import Decimal

from django.core.exceptions import PermissionDenied
from django.db.models import Min
from django.utils import timezone
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.files import private_file, upload_from
from api.core.forms import checked, form_data, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.attendance import serializers as attendance_s
from api.v1.attendance.views import _day_out, _missed_out
from api.v1.devices.views import _moment
from api.v1.employees.serializers import EducationInputSerializer, EducationSerializer
from api.v1.me import serializers as s
from api.v1.payroll import serializers as payroll_s
from attendance import month_view
from attendance.live_status import statuses_for
from attendance.models import AttendanceRecord, MissedScanRequest
from attendance.services import month_bounds, refresh
from common.choices import ActiveStatus
from common.tenant import use_company
from employees.models import Employee, EmployeeEducation
from leaves.models import LeaveDay, LeaveRequest, LeaveType
from organization import employee_profile as profile
from organization import employee_records as records
from organization import employee_self as self_service
from tenants.models import Company

AREA = "me"
ME = ["The logged-in employee (any login linked to an employee record)"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "permission_denied", "rate_limited",
          "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field"]
ONE = ["not_found"]
NO_EMPLOYEE = "permission_denied when the login is not linked to an employee record."


def _ref(row):
    return {"id": row.pk, "name": getattr(row, "full_name", None) or row.name} if row else None


def _time(value):
    return value.strftime("%H:%M") if value else ""


def _zone_name(company_id):
    return Company.objects.filter(pk=company_id).values_list("timezone", flat=True).first() \
        or "UTC"


class MeView(ApiView):
    """The employee's own pages: open to every login (the service decides);
    never to an API key."""

    permission_classes = [PanelRule]
    company_required = True
    read_scope = write_scope = None
    throttle_scope = "write"

    def employee(self, request):
        return self_service.mine(request.user, request.company_id)[1]

    def page(self, request, rows, build, serializer):
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer([build(r) for r in chunk],
                                                           many=True).data)


# --- home and profile ---------------------------------------------------------------------

def _employee_out(employee):
    return {"id": employee.pk, "name": employee.full_name, "work_email": employee.work_email,
            "phone": employee.phone, "joining_date": employee.joining_date,
            "status": employee.employment_status, "has_photo": bool(employee.photo)}


def _placement_out(placed):
    if placed is None:
        return None
    return {"employee_code": placed.employee_code, "branch": _ref(placed.branch),
            "department": _ref(placed.department) if placed.department_id else None,
            "designation": _ref(placed.designation) if placed.designation_id else None,
            "line_manager": _ref(placed.manager) if placed.manager_id else None,
            "since": placed.effective_from}


def _now_out(status):
    if status is None:
        return None
    out = status.as_dict()
    return {"key": out["key"], "label": out["label"], "since": out["since"],
            "detail": out["detail"]}


EMPLOYEE_EXAMPLE = {"id": 41, "name": "Rahim Uddin", "work_email": "rahim@example.com",
                    "phone": "01711000000", "joining_date": "2024-03-01", "status": "active",
                    "has_photo": True}
PLACEMENT_EXAMPLE = {"employee_code": "E-0041", "branch": {"id": 3, "name": "Chattogram"},
                     "department": {"id": 8, "name": "Software"},
                     "designation": {"id": 32, "name": "Developer"},
                     "line_manager": {"id": 7, "name": "Karim Ahmed"},
                     "since": "2024-03-01T00:00:00+06:00"}


class HomeView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:home"

    @endpoint(
        id="me-home", area=AREA, title="My account",
        summary="The app's home screen: who they are, where they work, today and this month.",
        what_it_does=["Answers their record and placement, today's shift, their status right "
                      "now, this month so far, and for a branch manager their branches and the "
                      "leave waiting for them."],
        description="A login without an employee record (an administrator, say) gets "
                    "employee null.",
        roles=ME, response=s.MyHomeSerializer,
        response_example={"company": {"id": 12, "name": "Acme Ltd"}, "role": "employee",
                          "employee": EMPLOYEE_EXAMPLE, "placement": PLACEMENT_EXAMPLE,
                          "shift_today": "Day shift 09:00-18:00",
                          "now": {"key": "in", "label": "In", "since": "09:02", "detail": ""},
                          "month_summary": {"present": 3, "late": 1, "absent": 0,
                                            "worked": "26h 10m"},
                          "managed_branches": [], "leave_waiting": 0},
        errors=ERRORS,
    )
    def get(self, request):
        from base_template.me_views import _membership
        from leaves import workflow
        from scheduling.calendar import WorkCalendar

        membership = _membership(request)
        if membership is None:
            raise PermissionDenied("Your login is not active in this company.")
        today = timezone.localdate()
        employee = Employee.objects.filter(user=request.user).first()
        placed = shift = now = summary = None
        if employee is not None:
            placed = self_service.placement(request.company_id, employee)
            shift = WorkCalendar(request.company_id, today, today).shift_for(
                placed.department_id if placed else None, today, employee_id=employee.pk)
            now = statuses_for(request.company_id, employee_ids=[employee.pk]).get(employee.pk)
            first, last = month_bounds(today.year, today.month)
            refresh(request.company_id, employee_ids=[employee.pk], start=first, end=last)
            summary = month_view.build_month(
                employee=employee, year=today.year, month=today.month,
                company_timezone=membership.company.timezone or "UTC", today=today)["summary"]
        try:
            waiting = workflow.reviewable(workflow.reviewer(request.user, request.company_id)) \
                .filter(status="pending").count()
        except PermissionDenied:
            waiting = 0
        return Response(s.MyHomeSerializer({
            "company": _ref(membership.company), "role": membership.role,
            "employee": _employee_out(employee) if employee else None,
            "placement": _placement_out(placed),
            "shift_today": f"{shift.name} {shift.start_time:%H:%M}-{shift.end_time:%H:%M}"
            if shift else None,
            "now": _now_out(now), "month_summary": summary,
            "managed_branches": [_ref(b) for b in membership.allowed_branches.all()]
            if membership.role == "manager" else [],
            "leave_waiting": waiting}).data)


def _profile_out(request):
    employee = Employee.objects.get(pk=self_service.mine(request.user, request.company_id)[1].pk)
    placed = self_service.placement(request.company_id, employee)
    education = EmployeeEducation.objects.filter(employee=employee).order_by("-passing_year",
                                                                             "-pk")
    details = {}
    for name in self_service.SELF_FIELDS:
        value = getattr(employee, name)
        details[name] = value.isoformat() if hasattr(value, "isoformat") else (value or "")
    return {"employee": _employee_out(employee), "placement": _placement_out(placed),
            "details": details, "education": list(education)}


PROFILE_EXAMPLE = {"employee": EMPLOYEE_EXAMPLE, "placement": PLACEMENT_EXAMPLE,
                   "details": {"preferred_name": "Rahim", "phone": "01711000000",
                               "blood_group": "B+", "address": "Agrabad, Chattogram"},
                   "education": [{"id": 3, "qualification": "BSc in CSE",
                                  "institution": "CUET", "subject": "", "result": "3.6",
                                  "passing_year": 2019, "note": ""}]}


class ProfileView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:profile"

    @endpoint(
        id="me-profile", area=AREA, title="My profile",
        summary="Their record, placement, the details they may change, and education.",
        what_it_does=["Answers their own profile."],
        description="The name, Employee ID, work email, placement and dates are the company's "
                    "record: shown, not changed here. " + NO_EMPLOYEE,
        roles=ME, response=s.MyProfileSerializer, response_example=PROFILE_EXAMPLE,
        errors=ERRORS,
    )
    def get(self, request):
        return Response(s.MyProfileSerializer(_profile_out(request)).data)


class DetailsView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:details"

    @endpoint(
        id="me-details", area=AREA, title="Change my details",
        summary="Phone, personal email, address, emergency contact, IDs and the rest.",
        what_it_does=["Changes only the fields sent; recorded in the audit log as made by "
                      "them."],
        description="Not the name, work email, joining or confirmation date - those are the "
                    "company's.",
        roles=ME, request=s.MyDetailsInputSerializer, response=s.MyProfileSerializer,
        request_example={"phone": "01711000001", "emergency_contact_name": "Fatema Uddin"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS,
    )
    def patch(self, request):
        employee = self.employee(request)
        data = s.MyDetailsInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(self_service.MyDetailsForm,
                       form_data(self_service.MyDetailsForm, employee,
                                 dict(data.validated_data)), instance=employee)
        with service_errors():
            self_service.save_details(actor=request.user, company_id=request.company_id,
                                      form=form)
        return Response(s.MyProfileSerializer(_profile_out(request)).data)


class PhotoView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "me:photo", "PUT": "me:photo_change", "DELETE": "me:photo_change"}

    @endpoint(
        id="me-photo", area=AREA, title="My photo",
        summary="Their profile photo, as the file itself.",
        what_it_does=["Answers the picture, privately."], description="not_found without one.",
        roles=ME, errors=ERRORS + ONE,
    )
    def get(self, request):
        employee = self.employee(request)
        if not employee.photo:
            raise ApiError("not_found", "No photo yet.")
        return private_file(employee.photo)

    @endpoint(
        id="me-photo-change", area=AREA, title="Change my photo",
        summary="A new profile photo.",
        what_it_does=["Replaces it; recorded in the audit log."],
        description="PNG, JPG or WEBP, up to 3 MB, base64 in the JSON.",
        roles=ME, request=s.MyLeaveDocumentSerializer, response=s.MyProfileSerializer,
        request_example={"filename": "me.jpg", "content_base64": "/9j/4AAQSkZJRgABAQ…"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def put(self, request):
        self.employee(request)
        data = s.MyLeaveDocumentSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        upload = upload_from(data.validated_data["filename"],
                             data.validated_data["content_base64"])
        names = {"photo": "content_base64"}
        form = checked(profile.PhotoForm, {}, names, files={"photo": upload})
        with service_errors(names):
            self_service.save_photo(actor=request.user, company_id=request.company_id,
                                    upload=form.cleaned_data["photo"])
        return Response(s.MyProfileSerializer(_profile_out(request)).data)

    @endpoint(
        id="me-photo-remove", area=AREA, title="Remove my photo",
        summary="No photo; their initials are shown instead.",
        what_it_does=["Removes it; recorded in the audit log."], description="Their initials show instead.",
        roles=ME, response=s.MyProfileSerializer, response_example=PROFILE_EXAMPLE,
        errors=ERRORS,
    )
    def delete(self, request):
        self.employee(request)
        with service_errors():
            self_service.save_photo(actor=request.user, company_id=request.company_id,
                                    remove=True)
        return Response(s.MyProfileSerializer(_profile_out(request)).data)


class EducationListView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "me:profile", "POST": "me:education_add"}

    @endpoint(
        id="me-education", area=AREA, title="My education",
        summary="Their qualifications.", what_it_does=["Lists them."],
        description="Newest first.", roles=ME, paginated=True, response=EducationSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": PROFILE_EXAMPLE["education"]},
        errors=ERRORS,
    )
    def get(self, request):
        employee = self.employee(request)
        rows = EmployeeEducation.objects.filter(employee=employee).order_by("-passing_year",
                                                                            "-pk")
        return self.page(request, rows, lambda row: row, EducationSerializer)

    @endpoint(
        id="me-education-add", area=AREA, title="Add my qualification",
        summary="One more line in their education history.",
        what_it_does=["Adds it; recorded in the audit log."],
        description="qualification is needed.", roles=ME, response_status=201,
        request=EducationInputSerializer, response=EducationSerializer,
        request_example={"qualification": "MSc in CSE", "institution": "BUET",
                         "passing_year": 2024},
        response_example={**PROFILE_EXAMPLE["education"][0], "id": 4,
                          "qualification": "MSc in CSE"},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        self.employee(request)
        data = EducationInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(records.EducationForm,
                       form_data(records.EducationForm, None, dict(data.validated_data)))
        with service_errors():
            row = self_service.save_education(actor=request.user,
                                              company_id=request.company_id,
                                              values=form.cleaned_data)
        return Response(EducationSerializer(row).data, status=201)


ROW_PARAM = Param("row_id", PATH, "integer", "The qualification's id.", required=True,
                  example=3)


class EducationRowView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"PATCH": "me:education_edit", "DELETE": "me:education_remove"}

    def _row(self, request, row_id):
        employee = self.employee(request)
        row = EmployeeEducation.objects.filter(pk=row_id, employee=employee).first()
        if row is None:
            raise ApiError("not_found", "No such qualification of yours.")
        return row

    @endpoint(
        id="me-education-change", area=AREA, title="Change my qualification",
        summary="New details for one line.",
        what_it_does=["Changes only the fields sent."], description="As adding one.", roles=ME,
        params=[ROW_PARAM], request=EducationInputSerializer, response=EducationSerializer,
        request_example={"result": "3.7"},
        response_example={**PROFILE_EXAMPLE["education"][0], "result": "3.7"},
        errors=WRITE_ERRORS + ONE,
    )
    def patch(self, request, row_id):
        row = self._row(request, row_id)
        data = EducationInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(records.EducationForm,
                       form_data(records.EducationForm, row, dict(data.validated_data)),
                       instance=row)
        with service_errors():
            row = self_service.save_education(actor=request.user,
                                              company_id=request.company_id,
                                              values=form.cleaned_data, row_id=row.pk)
        return Response(EducationSerializer(row).data)

    @endpoint(
        id="me-education-remove", area=AREA, title="Remove my qualification",
        summary="Takes one line off.", what_it_does=["Removes it."],
        description="It cannot be undone.", roles=ME, params=[ROW_PARAM],
        response=EducationSerializer, response_example=PROFILE_EXAMPLE["education"][0],
        errors=ERRORS + ONE,
    )
    def delete(self, request, row_id):
        row = self._row(request, row_id)
        out = EducationSerializer(row).data
        self_service.remove_education(actor=request.user, company_id=request.company_id,
                                      row_id=row.pk)
        return Response(out)


# --- attendance ---------------------------------------------------------------------------

class AttendanceView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:attendance"

    @endpoint(
        id="me-attendance", area=AREA, title="My month",
        summary="Every day of a month - status, in, out, worked - and its totals.",
        what_it_does=["Brings the month up to date and answers it."],
        description="This month unless year and month are given.",
        roles=ME, params=[Param("year", QUERY, "integer", "The year.", example=2026),
                          Param("month", QUERY, "integer", "The month, 1-12.", example=10)],
        response=s.MyCalendarSerializer,
        response_example={"year": 2026, "month": 10, "days": [
            {"date": "2026-10-01", "status": "present", "label": "Present", "late": False,
             "check_in": "09:02", "check_out": "18:05", "worked": "8h 03m", "note": ""}],
            "summary": {"present": 20, "late": 1}},
        errors=ERRORS,
    )
    def get(self, request):
        from attendance.views import read_month

        employee = self.employee(request)
        year, month = read_month(request.query_params)
        first, last = month_bounds(year, month)
        refresh(request.company_id, employee_ids=[employee.pk], start=first, end=last)
        built = month_view.build_month(employee=employee, year=year, month=month,
                                       company_timezone=_zone_name(request.company_id),
                                       today=timezone.localdate())
        return Response(s.MyCalendarSerializer({
            "year": year, "month": month,
            "days": [{"date": d.date, "status": d.status, "label": d.status_label,
                      "late": d.is_late, "check_in": d.check_in, "check_out": d.check_out,
                      "worked": d.worked, "note": d.note} for d in built["days"]],
            "summary": built["summary"]}).data)


class AttendanceDayView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:attendance_day"

    @endpoint(
        id="me-attendance-day", area=AREA, title="My day",
        summary="One day in full: every scan and how it was read.",
        what_it_does=["Brings the day up to date and answers it."],
        description="Something missing? POST /me/missed-scans.",
        roles=ME, params=[Param("date", PATH, "string (date)", "YYYY-MM-DD.", required=True,
                                example="2026-10-05")],
        response=attendance_s.DayDetailSerializer,
        response_example={"day": None, "shift": "", "scans": [], "corrections": [],
                          "may_fix": False, "locked": False},
        errors=ERRORS + ONE,
    )
    def get(self, request, date):
        from attendance.services import _is_locked, locked_ranges

        employee = self.employee(request)
        try:
            day = datetime.date.fromisoformat(date)
        except ValueError:
            raise ApiError("not_found", "That is not a date (YYYY-MM-DD).") from None
        refresh(request.company_id, employee_ids=[employee.pk], start=day, end=day)
        record = (AttendanceRecord.objects.select_related("shift", "employee", "branch",
                                                          "employee_assignment")
                  .filter(employee=employee, work_date=day).first())
        scans, shift = [], ""
        if record is not None:
            built = month_view.build_day_detail(record=record,
                                                company_timezone=_zone_name(request.company_id))
            scans = [{"time": line["time"], "label": line["label"],
                      "device": line["device"] or "", "added_by_hand": line["is_manual"],
                      "counted": line["is_included"], "note": line["note"] or ""}
                     for line in built["scans"]]
            shift = " ".join(p for p in (built.get("shift_name"), built.get("shift_times")) if p)
        return Response(attendance_s.DayDetailSerializer({
            "day": _day_out(record) if record else None, "shift": shift, "scans": scans,
            "corrections": [], "may_fix": False,
            "locked": _is_locked(day, locked_ranges(request.company_id))}).data)


# --- missed scans -------------------------------------------------------------------------

class MissedScanListView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "me:missed_scans", "POST": "me:missed_scan_report"}

    @endpoint(
        id="me-missed-scans", area=AREA, title="My missed scans",
        summary="What they reported, and what was decided.",
        what_it_does=["Lists their requests, newest first."], description="Newest first; the approver's note is decision_note.",
        roles=ME, paginated=True, response=attendance_s.MissedScanSerializer,
        response_example={"count": 0, "next": None, "previous": None, "results": []},
        errors=ERRORS,
    )
    def get(self, request):
        employee = self.employee(request)
        rows = (MissedScanRequest.objects.filter(employee=employee)
                .select_related("employee", "branch", "decided_by")
                .order_by("-submitted_at", "-pk"))
        return self.page(request, rows, _missed_out, attendance_s.MissedScanSerializer)

    @endpoint(
        id="me-missed-scans-report", area=AREA, title="Report a missed scan",
        summary="A check-in, check-out, both, or a whole day the device did not record.",
        what_it_does=["Sends it for approval; their attendance changes once it is "
                      "approved."],
        description=("The day a scan counts for is worked out from its date and time (after "
                     "midnight on a night shift, give the next date). A whole missing day "
                     "needs work_date; the shift's times are used."),
        roles=ME, response_status=201, request=s.MyMissedScanInputSerializer,
        response=attendance_s.MissedScanSerializer,
        request_example={"kind": "check_out", "at": "2026-10-04T18:05",
                         "reason": "Left with the visitors through the side gate"},
        response_example={"id": 15, "employee": {"id": 41, "name": "Rahim Uddin"},
                          "branch": {"id": 3, "name": "Chattogram"}, "kind": "check_out",
                          "date": "2026-10-04", "scan_at": "2026-10-04T18:05:00+06:00",
                          "scan_out_at": None, "reason": "Left with the visitors",
                          "status": "pending", "submitted_at": "2026-10-05T09:10:00+06:00",
                          "decided_by": None, "decided_at": None, "decision_note": ""},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        from attendance import scan_requests
        from attendance.forms import MissedScanForm

        self.employee(request)
        data = s.MyMissedScanInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form_input = {"kind": values["kind"], "reason": values["reason"],
                      "work_date": values["work_date"].isoformat()
                      if values.get("work_date") else ""}
        for name in ("at", "at_out"):
            form_input[f"{name}_0"], form_input[f"{name}_1"] = _moment(values.get(name, ""),
                                                                       name)
        form = checked(MissedScanForm, form_input)
        cleaned = form.cleaned_data
        with service_errors():
            made = scan_requests.submit(actor=request.user, company_id=request.company_id,
                                        work_date=cleaned["work_date"], at=cleaned["at"],
                                        reason=cleaned["reason"], kind=cleaned["kind"],
                                        at_out=cleaned["at_out"])
        made = MissedScanRequest.objects.select_related("employee", "branch", "decided_by") \
            .get(pk=made.pk)
        return Response(attendance_s.MissedScanSerializer(_missed_out(made)).data, status=201)


class MissedScanWithdrawView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:missed_scan_withdraw"

    @endpoint(
        id="me-missed-scans-withdraw", area=AREA, title="Withdraw a missed scan",
        summary="Take back a report still waiting.", what_it_does=["Withdraws it."],
        description="Only while it waits.", roles=ME,
        params=[Param("request_id", PATH, "integer", "The request id.", required=True,
                      example=15)],
        response=attendance_s.MissedScanSerializer,
        response_example={"id": 15, "status": "withdrawn"}, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, request_id):
        from attendance import scan_requests

        employee = self.employee(request)
        if not MissedScanRequest.objects.filter(pk=request_id, employee=employee).exists():
            raise ApiError("not_found", "No such request of yours.")
        with service_errors():
            scan_requests.withdraw(actor=request.user, company_id=request.company_id,
                                   request_id=request_id)
        made = MissedScanRequest.objects.select_related("employee", "branch", "decided_by") \
            .get(pk=request_id)
        return Response(attendance_s.MissedScanSerializer(_missed_out(made)).data)


# --- leave --------------------------------------------------------------------------------

LEAVE_TAKEN = (LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED, LeaveDay.Status.CONSUMED)


def _my_leave_out(leave):
    segments = list(leave.segments.all())
    current = [seg for seg in segments if seg.status != "cancelled"] or segments
    segment = sorted(current, key=lambda seg: (seg.sequence_number, seg.pk))[-1]
    if leave.status in ("pending", "rejected", "withdrawn"):
        days = sum((seg.requested_units for seg in current), Decimal("0"))
    else:
        days = sum((day.balance_units for seg in segments for day in seg.days.all()
                    if day.status in LEAVE_TAKEN), Decimal("0"))
    snapshot = leave.decision_snapshot or {}
    return {
        "id": leave.pk, "leave_type": _ref(segment.leave_type),
        "start_date": segment.start_date, "end_date": segment.end_date,
        "duration": segment.duration_type or "full_day",
        "half_day_part": segment.half_day_part or "", "start_time": _time(segment.start_time),
        "end_time": _time(segment.end_time), "days": days,
        "pay_type": snapshot.get("pay_type") or segment.requested_pay_type,
        "status": leave.status, "reason": leave.reason,
        "decision_note": snapshot.get("reason", "") or "",
        "has_document": bool(leave.attachment), "submitted_at": leave.submitted_at,
        "decided_at": leave.decided_at, "may_withdraw": leave.status == "pending",
    }


MY_LEAVE_EXAMPLE = {"id": 31, "leave_type": {"id": 1, "name": "Casual leave"},
                    "start_date": "2026-10-12", "end_date": "2026-10-12",
                    "duration": "full_day", "half_day_part": "", "start_time": "",
                    "end_time": "", "days": "1.00", "pay_type": "paid", "status": "pending",
                    "reason": "Doctor's appointment", "decision_note": "",
                    "has_document": False, "submitted_at": "2026-10-05T09:00:00+06:00",
                    "decided_at": None, "may_withdraw": True}


def _my_leave_rows(employee, year=None):
    rows = (LeaveRequest.objects.prefetch_related("segments__leave_type", "segments__days")
            .filter(employee=employee))
    if year:
        rows = rows.filter(segments__start_date__lte=datetime.date(year, 12, 31),
                           segments__end_date__gte=datetime.date(year, 1, 1))
    return rows.annotate(first=Min("segments__start_date")).distinct().order_by("-pk")


class LeaveYearView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:leave"

    @endpoint(
        id="me-leave-summary", area=AREA, title="My leave this year",
        summary="The types they may ask for, their balances, and the days taken.",
        what_it_does=["Answers the year's leave picture."],
        description="Their requests: GET /me/leave.",
        roles=ME, params=[Param("year", QUERY, "integer", "The year (this year by default).",
                                example=2026)],
        response=s.MyLeaveYearSerializer,
        response_example={"year": 2026, "leave_types": [{"id": 1, "name": "Casual leave"}],
                          "balances": [{"leave_type": {"id": 1, "name": "Casual leave"},
                                        "by_policy": False, "policy": None, "given": "10.00",
                                        "taken": "2.00", "left": "8.00"}],
                          "taken": [{"leave_type": "Casual leave", "pay_type": "paid",
                                     "days": "2.00"}]},
        errors=ERRORS,
    )
    def get(self, request):
        from leaves.policies import overview

        employee = self.employee(request)
        today = timezone.localdate()
        raw = request.query_params.get("year", "")
        year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= 2100 else today.year
        taken = Counter()
        for name, pay_type, units in LeaveDay.objects.filter(
                employee=employee, status__in=LEAVE_TAKEN,
                work_date__gte=datetime.date(year, 1, 1),
                work_date__lte=datetime.date(year, 12, 31)).values_list(
                "request_segment__leave_type__name", "approved_pay_type", "balance_units"):
            taken[(name, pay_type)] += units or Decimal("1")
        return Response(s.MyLeaveYearSerializer({
            "year": year,
            "leave_types": [_ref(t) for t in LeaveType.objects.filter(
                status=ActiveStatus.ACTIVE).order_by("name")],
            "balances": [{"leave_type": _ref(row["leave_type"]), "by_policy": row["by_policy"],
                          "policy": (row["policy"].name if row["policy"] else None)
                          if row["by_policy"] else None, "given": row["given"],
                          "taken": row["taken"], "left": row["left"]}
                         for row in overview(employee, year, today)],
            "taken": [{"leave_type": name, "pay_type": pay, "days": days}
                      for (name, pay), days in sorted(taken.items())]}).data)


class LeaveListView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "me:leave", "POST": "me:leave_request"}

    @endpoint(
        id="me-leave", area=AREA, title="My leave requests",
        summary="Everything they asked for or was recorded for them, newest first.",
        what_it_does=["Lists their leave, with the approver's note."],
        description="Only one year with ?year=.", roles=ME, paginated=True,
        params=[Param("year", QUERY, "integer", "Only this year.", example=2026)],
        response=s.MyLeaveSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [MY_LEAVE_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        employee = self.employee(request)
        raw = request.query_params.get("year", "")
        rows = _my_leave_rows(employee, int(raw) if raw.isdigit() else None)
        return self.page(request, rows, _my_leave_out, s.MyLeaveSerializer)

    @endpoint(
        id="me-leave-request", area=AREA, title="Ask for leave",
        summary="Full days, a half day or some hours; the pay asked for.",
        what_it_does=["Sends it for approval - their branch manager, someone given Approve "
                      "leave, their department head, or the company."],
        description=("A reason is needed. Checked like recorded leave: clashes, the "
                     "allowance, the shift. A type that needs a document is refused without "
                     "one."),
        roles=ME, response_status=201, request=s.MyLeaveInputSerializer,
        response=s.MyLeaveSerializer,
        request_example={"leave_type_id": 1, "start_date": "2026-10-12",
                         "end_date": "2026-10-12", "reason": "Doctor's appointment"},
        response_example=MY_LEAVE_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def post(self, request):
        from api.v1.leave.views import _leave_form_input
        from leaves import workflow
        from leaves.forms import RequestLeaveForm

        employee = self.employee(request)
        data = s.MyLeaveInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        files = {}
        if values.get("document"):
            files["document"] = upload_from(values["document"]["filename"],
                                            values["document"]["content_base64"],
                                            field="document")
        names = {"leave_type": "leave_type_id"}
        form = checked(RequestLeaveForm, _leave_form_input(values), names, files=files,
                       leave_types=LeaveType.objects.filter(status=ActiveStatus.ACTIVE))
        cleaned = {key: value for key, value in form.cleaned_data.items() if key != "document"}
        with service_errors(names):
            leave = workflow.submit_request(actor=request.user, company_id=request.company_id,
                                            values=cleaned,
                                            document=form.cleaned_data.get("document"))
        leave = _my_leave_rows(employee).get(pk=leave.pk)
        return Response(s.MyLeaveSerializer(_my_leave_out(leave)).data, status=201)


LEAVE_PARAM = Param("leave_id", PATH, "integer", "The leave id.", required=True, example=31)


class LeaveWithdrawView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_withdraw"

    @endpoint(
        id="me-leave-withdraw", area=AREA, title="Withdraw a leave request",
        summary="Take back a request still waiting.", what_it_does=["Withdraws it."],
        description="Only while it waits; approved leave is cancelled by whoever records "
                    "leave.", roles=ME, params=[LEAVE_PARAM], response=s.MyLeaveSerializer,
        response_example={**MY_LEAVE_EXAMPLE, "status": "withdrawn", "may_withdraw": False},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, leave_id):
        from leaves import workflow

        employee = self.employee(request)
        if not LeaveRequest.objects.filter(pk=leave_id, employee=employee).exists():
            raise ApiError("not_found", "No such leave of yours.")
        with service_errors():
            workflow.withdraw_request(actor=request.user, company_id=request.company_id,
                                      request_id=leave_id)
        return Response(s.MyLeaveSerializer(_my_leave_out(
            _my_leave_rows(employee).get(pk=leave_id))).data)


class LeaveDocumentView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_document"

    @endpoint(
        id="me-leave-document", area=AREA, title="My leave's document",
        summary="The certificate or letter attached to their leave.",
        what_it_does=["Answers the file itself, privately."], description="not_found when there is none.", roles=ME,
        params=[LEAVE_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, leave_id):
        employee = self.employee(request)
        leave = LeaveRequest.objects.filter(pk=leave_id, employee=employee).first()
        if leave is None or not leave.attachment:
            raise ApiError("not_found", "No such document of yours.")
        return private_file(leave.attachment, leave.attachment_name)


# --- a branch manager's day ---------------------------------------------------------------

class BranchAttendanceView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:branch_attendance"

    @endpoint(
        id="me-branch-attendance", area=AREA, title="My branch today",
        summary="For a branch manager: each person placed in their branches, and their day.",
        what_it_does=["Lists the people placed in their branches on the date, with the "
                      "day's status, in, out, late and - today - where they are now."],
        description="Branch manager logins only. Today unless date is given.",
        roles=["A branch manager"], paginated=True,
        params=[Param("date", QUERY, "string (date)", "The day (today by default).",
                      example="2026-10-05"),
                Param("q", QUERY, "string", "Part of a name.", example="rah")],
        response=s.BranchDaySerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "employee": {"id": 41, "name": "Rahim Uddin"}, "employee_code": "E-0041",
            "branch": {"id": 3, "name": "Chattogram"}, "status": "present",
            "check_in": "2026-10-05T09:02:08+06:00", "check_out": None, "late_minutes": 0,
            "now": {"key": "in", "label": "In", "since": "09:02", "detail": ""}}]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        from zoneinfo import ZoneInfo

        from django.db.models import Q

        from attendance.services import recalculate
        from employees.models import EmployeeAssignment
        from leaves import workflow

        member = workflow.reviewer(request.user, request.company_id)
        if member.role != "manager":
            raise PermissionDenied("This is for branch managers.")
        zone = ZoneInfo(member.company.timezone or "UTC")
        today = timezone.now().astimezone(zone).date()
        raw = request.query_params.get("date", "")
        try:
            on = datetime.date.fromisoformat(raw) if raw else today
        except ValueError:
            refuse({"date": ["Use YYYY-MM-DD."]})
        probe = datetime.datetime.combine(on, datetime.time(12), tzinfo=zone)
        branches = workflow.branch_ids(member)
        rows = (EmployeeAssignment.objects.filter(branch_id__in=branches,
                                                  effective_from__lte=probe)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=probe))
                .exclude(status__in=["draft", "cancelled"]).select_related("employee", "branch"))
        departments = list(member.allowed_departments.values_list("pk", flat=True))
        if departments:
            rows = rows.filter(department_id__in=departments)
        query = request.query_params.get("q", "").strip()[:100]
        if query:
            rows = rows.filter(Q(employee__first_name__icontains=query)
                               | Q(employee__last_name__icontains=query))
        paginator = StandardPagination()
        chunk = list(paginator.paginate_queryset(
            rows.order_by("employee__first_name", "employee_id"), request, view=self))
        ids = [p.employee_id for p in chunk]
        if ids:
            recalculate(request.company_id, start=on, end=on, employee_ids=ids)
        days = {r.employee_id: r for r in AttendanceRecord.objects.filter(
            employee_id__in=ids, work_date=on, branch_id__in=branches)}
        live = statuses_for(request.company_id, employee_ids=ids) if ids and on == today else {}
        out = []
        for placed in chunk:
            day = days.get(placed.employee_id)
            out.append({"employee": _ref(placed.employee),
                        "employee_code": placed.employee_code, "branch": _ref(placed.branch),
                        "status": day.attendance_status if day else None,
                        "check_in": day.first_in_at if day else None,
                        "check_out": day.last_out_at if day else None,
                        "late_minutes": day.late_minutes if day else 0,
                        "now": _now_out(live.get(placed.employee_id))})
        return paginator.get_paginated_response(s.BranchDaySerializer(out, many=True).data)


# --- payslips -----------------------------------------------------------------------------

def _my_payslips(employee):
    from base_template.me_views import _my_payslips as finalised

    return finalised(employee)


def _payslip_out(record):
    from api.v1.payroll.views import _line_out, _row_out
    from payroll.views import payslip_context

    context = payslip_context(record, for_employee=True)
    period = context["period"]
    assignment = context["assignment"]
    return {
        **_row_out(record), "period": period.name, "period_start": period.start_date,
        "period_end": period.end_date, "status": record.payroll_run.status,
        "department": assignment.department.name
        if assignment and assignment.department_id else None,
        "designation": assignment.designation.name
        if assignment and assignment.designation_id else None,
        "counts": dict(context["counts"]),
        "earnings": [_line_out(line) for line in context["earnings"]],
        "deduction_lines": [_line_out(line) for line in context["deductions"]],
        "penalties": [{"id": p.pk, "rule": p.penalty_rule.name, "period_start": p.period_start,
                       "period_end": p.period_end, "occurrences": p.occurrence_count,
                       "amount": p.deduction_amount, "status": p.status}
                      for p in context["penalties"]],
        "adjustments": [], "corrections": [], "may_adjust": False, "may_correct": False,
        "may_waive": False, "may_email": False, "email_blocked": None,
    }


PAYSLIP_PARAM = Param("payslip_id", PATH, "integer", "The payslip id.", required=True,
                      example=501)


class PayslipListView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:payslips"

    @endpoint(
        id="me-payslips", area=AREA, title="My payslips",
        summary="Their payslips of finalised months, newest first.",
        what_it_does=["Lists them."],
        description="A month shows once the company has finalised it; a draft can still "
                    "change.", roles=ME, paginated=True, response=s.MyPayslipRowSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "id": 501, "period": "September 2026", "period_start": "2026-09-01",
            "currency": "BDT", "gross": "30000.00", "deductions": "1000.00",
            "net": "29000.00"}]},
        errors=ERRORS,
    )
    def get(self, request):
        employee = self.employee(request)

        def build(record):
            period = record.payroll_run.payroll_period
            return {"id": record.pk, "period": period.name, "period_start": period.start_date,
                    "currency": record.currency, "gross": record.gross_earnings,
                    "deductions": record.total_deductions, "net": record.net_pay}
        return self.page(request, _my_payslips(employee), build, s.MyPayslipRowSerializer)


class PayslipView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:payslip"

    @endpoint(
        id="me-payslips-one", area=AREA, title="My payslip",
        summary="One finalised payslip, every line.", what_it_does=["Answers it."],
        description="The may_* fields are always false here: changing a payslip is the "
                    "company's.", roles=ME, params=[PAYSLIP_PARAM],
        response=payroll_s.PayslipSerializer, response_example={"id": 501, "net": "29000.00"},
        errors=ERRORS + ONE,
    )
    def get(self, request, payslip_id):
        record = _my_payslips(self.employee(request)).filter(pk=payslip_id).first()
        if record is None:
            raise ApiError("not_found", "No such payslip of yours.")
        return Response(payroll_s.PayslipSerializer(_payslip_out(record)).data)


class PayslipPdfView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:payslip"

    @endpoint(
        id="me-payslips-pdf", area=AREA, title="Download my payslip",
        summary="The payslip as a PDF.", what_it_does=["Answers the PDF file."],
        description="Content-Type application/pdf.", roles=ME, params=[PAYSLIP_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, payslip_id):
        from payroll.payslip_export import export_payslip
        from payroll.views import payslip_context

        record = _my_payslips(self.employee(request)).filter(pk=payslip_id).first()
        if record is None:
            raise ApiError("not_found", "No such payslip of yours.")
        return export_payslip(request, payslip_context(record, for_employee=True))


# --- LFA ----------------------------------------------------------------------------------

def _my_claim_out(claim):
    adjustment = claim.payroll_adjustment
    return {"id": claim.pk, "cycle_start": claim.cycle_start, "cycle_end": claim.cycle_end,
            "note": claim.note, "has_document": bool(claim.document),
            "calculated_amount": claim.calculated_amount,
            "approved_amount": claim.approved_amount, "currency": claim.currency,
            "status": claim.status,
            "pay_month": adjustment.target_payroll_period.name if adjustment else None,
            "paid_on": claim.paid_on, "decision_note": claim.decision_note}


CLAIM_EXAMPLE = {"id": 8, "cycle_start": "2026-01-01", "cycle_end": "2026-12-31",
                 "note": "Family trip", "has_document": False, "calculated_amount": "30000.00",
                 "approved_amount": None, "currency": "BDT", "status": "pending",
                 "pay_month": None, "paid_on": None, "decision_note": ""}


def _my_claims(employee):
    from payroll.models import LfaClaim

    return LfaClaim.objects.select_related("payroll_adjustment__target_payroll_period") \
        .filter(employee=employee)


class LfaView(MeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "me:lfa", "POST": "me:lfa"}

    @endpoint(
        id="me-lfa", area=AREA, title="My LFA",
        summary="Whether they may claim Leave Fare Assistance now, how much, and why not.",
        what_it_does=["Works it out by the company's rules, today."],
        description="Their claims: GET /me/lfa/claims.", roles=ME, response=s.MyLfaSerializer,
        response_example={"settings": {"enabled": True, "name": "Leave Fare Assistance",
                                       "description": "", "requires_leave": False,
                                       "requires_document": False, "payment": "with_salary"},
                          "eligible": True, "reasons": [], "amount": "30000.00",
                          "currency": "BDT", "how": "1 month of basic salary", "leave": []},
        errors=ERRORS,
    )
    def get(self, request):
        from payroll import lfa

        employee = self.employee(request)
        settings = lfa.settings_for(request.company_id)
        found = lfa.eligibility(employee, settings, timezone.localdate())
        return Response(s.MyLfaSerializer({
            "settings": {"enabled": bool(settings.pk and settings.enabled),
                         "name": settings.name or "", "description": settings.description or "",
                         "requires_leave": settings.requires_leave,
                         "requires_document": settings.requires_document,
                         "payment": settings.payment},
            "eligible": found.ok, "reasons": found.reasons, "amount": found.amount,
            "currency": found.currency, "how": found.how,
            "leave": [{"id": r.pk, "start_date": min(seg.start_date for seg in r.segments.all()),
                       "days": str(r.lfa_units)} for r in found.leave_requests]}).data)

    @endpoint(
        id="me-lfa-claim", area=AREA, title="Claim LFA",
        summary="Send a claim for approval.",
        what_it_does=["Sends it; whoever prepares salary in their branch (or the company) "
                      "decides it."],
        description="leave_id when the company needs leave taken with it (from GET /me/lfa); "
                    "document when it needs proof.",
        roles=ME, response_status=201, request=s.MyLfaClaimInputSerializer,
        response=s.MyLfaClaimSerializer, request_example={"note": "Family trip"},
        response_example=CLAIM_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def post(self, request):
        from payroll import lfa
        from payroll.lfa_forms import LfaClaimForm

        employee = self.employee(request)
        data = s.MyLfaClaimInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        settings = lfa.settings_for(request.company_id)
        found = lfa.eligibility(employee, settings, timezone.localdate())
        files = {}
        if values.get("document"):
            files["document"] = upload_from(values["document"]["filename"],
                                            values["document"]["content_base64"],
                                            field="document")
        names = {"leave_request": "leave_id"}
        form = checked(LfaClaimForm, {"leave_request": str(values.get("leave_id") or ""),
                                      "note": values.get("note", "")}, names, files=files,
                       leave_requests=found.leave_requests, needs_leave=settings.requires_leave,
                       needs_document=settings.requires_document)
        with service_errors(names):
            claim = lfa.submit(actor=request.user, company_id=request.company_id,
                               values={"leave_request": form.cleaned_data.get("leave_request"),
                                       "note": form.cleaned_data.get("note")},
                               document=form.cleaned_data.get("document"),
                               today=timezone.localdate())
        return Response(s.MyLfaClaimSerializer(_my_claim_out(
            _my_claims(employee).get(pk=claim.pk))).data, status=201)


class LfaClaimListView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:lfa"

    @endpoint(
        id="me-lfa-claims", area=AREA, title="My LFA claims",
        summary="Their claims and what was decided.", what_it_does=["Lists them, newest "
                                                                    "first."],
        description="A claim paid with salary shows paid once its month is finalised.", roles=ME, paginated=True, response=s.MyLfaClaimSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [CLAIM_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        from payroll import lfa

        claims = _my_claims(self.employee(request)).order_by("-created_at", "-pk")
        lfa.sync_paid(request.company_id, list(claims))
        return self.page(request, claims, _my_claim_out, s.MyLfaClaimSerializer)


CLAIM_PARAM = Param("claim_id", PATH, "integer", "The claim id.", required=True, example=8)


class LfaWithdrawView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:lfa_withdraw"

    @endpoint(
        id="me-lfa-withdraw", area=AREA, title="Withdraw an LFA claim",
        summary="Take back a claim still waiting.", what_it_does=["Withdraws it."],
        description="Only while it waits.", roles=ME, params=[CLAIM_PARAM],
        response=s.MyLfaClaimSerializer,
        response_example={**CLAIM_EXAMPLE, "status": "withdrawn"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, claim_id):
        from payroll import lfa

        employee = self.employee(request)
        if not _my_claims(employee).filter(pk=claim_id).exists():
            raise ApiError("not_found", "No such claim of yours.")
        with service_errors():
            lfa.withdraw(actor=request.user, company_id=request.company_id, claim_id=claim_id)
        return Response(s.MyLfaClaimSerializer(_my_claim_out(
            _my_claims(employee).get(pk=claim_id))).data)


class LfaDocumentView(MeView):
    permission_classes = [PanelRule]
    panel_page = "me:lfa_document"

    @endpoint(
        id="me-lfa-document", area=AREA, title="My LFA proof",
        summary="The proof attached to their claim.",
        what_it_does=["Answers the file itself, privately."], description="not_found when there is none.", roles=ME,
        params=[CLAIM_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, claim_id):
        claim = _my_claims(self.employee(request)).filter(pk=claim_id).first()
        if claim is None or not claim.document:
            raise ApiError("not_found", "No such document of yours.")
        return private_file(claim.document, claim.document_name)
