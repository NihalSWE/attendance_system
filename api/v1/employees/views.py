"""Employees, part 1: the list, adding someone, their profile and history,
and the edits on Edit employee and the profile (docs/api/00-PLAN.md phase 3;
the guide: docs/api/30-employees.md).

Each endpoint uses its panel page's gate (``PanelRule``), the page's own form
and the panel's own service, so the API and the panel decide alike. Pay is
shown only where the caller may see pay - and, for an API key, only with the
payroll:read scope.
"""

from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied
from django.db import IntegrityError
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, form_data, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.employees import serializers as s
from common.choices import ActiveStatus
from employees.models import Employee
from organization import employee_detail_services as detail
from organization import employee_edit_services as edit
from organization import employee_inactive as inactive
from organization import employee_profile as profile
from organization import employee_profile_info as info
from organization.employee_detail_forms import EndEmploymentForm
from organization.employee_edit_forms import EmployeeDetailsForm, PlacementForm, SalaryForm
from organization.employee_forms import EmployeeCreateForm
from organization.employee_login import ROLE_LABELS
from organization.employee_views import create_from_form, creation_setup
from organization.models import Department, Designation
from tenants.models import Company

AREA = "employees"
BASE_ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
               "signature_required", "invalid_signature", "two_step_setup_required",
               "scope_missing", "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = BASE_ERRORS + ["validation_error", "unknown_field"]
ONE_ERRORS = ["not_found"]
VIEWERS = ["Whoever may view employees: the owner, company administrator, HR, or anyone given "
           "View employees in the person's branch (a branch manager in theirs)"]
EDITORS = ["Whoever may edit this employee: the owner, company administrator, HR, or anyone "
           "given Create and edit employees in their branch"]
EMPLOYEE_PARAM = Param("employee_id", PATH, "integer", "The employee id.", required=True,
                       example=41)
DATE_FIELDS = ("date_of_birth", "confirmation_date")
PLACEMENT_NAMES = {"branch": "branch_id", "department": "department_id",
                   "designation": "designation_id", "placement_from": "from_date",
                   "placement_reason": "reason"}

PROFILE_EXAMPLE = {
    "id": 41, "name": "Rahim Uddin", "first_name": "Rahim", "last_name": "Uddin",
    "work_email": "rahim@acme.com.bd", "phone": "01711000041", "joining_date": "2024-02-01",
    "leaving_date": None, "employment_status": "active", "has_left": False, "has_photo": True,
    "placement": {"employee_code": "E-0041", "branch": {"id": 3, "name": "Chattogram"},
                  "department": {"id": 8, "name": "Software"},
                  "designation": {"id": 31, "name": "Software Engineer"},
                  "line_manager": {"id": 12, "name": "Karim Ahmed"}, "since": "2024-02-01"},
    "pay": {"pay_basis": "monthly", "base_rate": "45000.00", "currency": "BDT",
            "since": "2025-01-01"},
    "login": {"role": "employee", "label": "Employee", "active": True},
    "personal": {"preferred_name": "", "date_of_birth": "1995-04-12", "gender": "male",
                 "blood_group": "B+", "marital_status": "single", "national_id": "1995…",
                 "passport_number": "", "personal_email": "", "address": "Agrabad, Chattogram",
                 "emergency_contact_name": "Fatema Uddin", "emergency_contact_phone": "01811…",
                 "emergency_contact_relation": "Mother", "confirmation_date": "2024-08-01"},
    "may": {"edit": True, "salary": True, "change_salary": True, "end": True, "logins": True,
            "attendance": True},
}


# --- helpers ------------------------------------------------------------------------

def _local(instant, company):
    if instant is None:
        return None
    return instant.astimezone(ZoneInfo(company.timezone or "UTC")).date()


def _ref(row):
    return {"id": row.pk, "name": row.name} if row is not None else None


def _person_ref(person):
    return {"id": person.pk, "name": person.full_name} if person is not None else None


def _key_sees_pay(request):
    key = getattr(request, "api_key", None)
    return key is None or "payroll:read" in key.scopes


def _pay(compensation, company):
    if compensation is None:
        return None
    return {"pay_basis": compensation.pay_basis, "base_rate": compensation.base_rate,
            "currency": compensation.currency,
            "since": _local(compensation.effective_from, company)}


def _exists(employee_id):
    if not Employee.objects.filter(pk=employee_id).exists():
        raise ApiError("not_found", "No such employee in this company.")


def _profile(request, employee_id):
    """The profile, as the panel's employee page reads it."""
    _exists(employee_id)
    page = detail.employee_history(actor=request.user, company_id=request.company_id,
                                   employee_id=employee_id)
    employee, assignment, may = page["employee"], page["assignment"], page["may"]
    company = page["membership"].company
    sees_pay = may["salary"] and _key_sees_pay(request)
    cards = edit.card_permissions(request.user, request.company_id, page["membership"],
                                  assignment)
    login = page["login"]
    return {
        "id": employee.pk, "name": employee.full_name, "first_name": employee.first_name,
        "last_name": employee.last_name, "work_email": employee.work_email,
        "phone": employee.phone, "joining_date": employee.joining_date,
        "leaving_date": employee.leaving_date, "employment_status": employee.employment_status,
        "has_left": page["is_ended"], "has_photo": bool(employee.photo),
        "placement": {
            "employee_code": assignment.employee_code, "branch": _ref(assignment.branch),
            "department": _ref(assignment.department),
            "designation": _ref(assignment.designation),
            "line_manager": _person_ref(assignment.manager),
            "since": _local(assignment.effective_from, company),
        } if assignment is not None else None,
        "pay": _pay(page["compensation"], company) if sees_pay else None,
        "login": {"role": login.role, "label": ROLE_LABELS.get(login.role, login.get_role_display()),
                  "active": login.status == "active"} if login else None,
        "personal": {name: getattr(employee, name) if name in DATE_FIELDS
                     else (getattr(employee, name) or "") for name in profile.PERSONAL_FIELDS},
        "may": {"edit": may["edit"], "salary": sees_pay,
                "change_salary": cards["salary"] and _key_sees_pay(request),
                "end": may["end"] and not page["is_ended"], "logins": may["logins"],
                "attendance": bool(may["attendance"])},
    }


def _profile_response(request, employee_id, status=200):
    return Response(s.EmployeeSerializer(_profile(request, employee_id)).data, status=status)


def _for_edit(request, employee_id):
    _exists(employee_id)
    return edit.get_employee_for_edit(actor=request.user, company_id=request.company_id,
                                      employee_id=employee_id, code="employees.edit")


class _Listing:
    """What the panel's list query reads from a request."""

    def __init__(self, request, params):
        self.user, self.company_id, self.GET = request.user, request.company_id, params


class EmployeeView(ApiView):
    """Shared settings for the employee endpoints."""

    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "employees:read", "employees:write"
    throttle_scope = "write"


# --- list, add ----------------------------------------------------------------------

class EmployeeListView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "employee_list"

    @endpoint(
        id="employees-list", area=AREA, title="Employees",
        summary="The people you may see, with their placement and (where you may) pay.",
        what_it_does=["Lists them as the panel's Employees page does - same people, same filters.",
                      "Shows pay only for people whose pay you may see."],
        description=("Filter by status, branch_id or setup (department, salary, both, complete - "
                     "what an imported employee still needs), or search q by name, email or "
                     "Employee ID. A branch manager sees their branch; a department head their "
                     "department."),
        roles=VIEWERS, scopes=["employees:read"], paginated=True,
        params=[Param("q", QUERY, "string", "Search by name, email or Employee ID.", example="rahim"),
                Param("status", QUERY, "string", "active, probation, suspended (made "
                                                 "inactive), resigned, terminated or retired.", example="active"),
                Param("branch_id", QUERY, "integer", "Only this branch.", example=3),
                Param("setup", QUERY, "string", "department, salary, both or complete.",
                      example="complete")],
        response=s.EmployeeRowSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "id": 41, "employee_code": "E-0041", "name": "Rahim Uddin",
            "work_email": "rahim@acme.com.bd", "branch": {"id": 3, "name": "Chattogram"},
            "department": {"id": 8, "name": "Software"},
            "designation": {"id": 31, "name": "Software Engineer"},
            "employment_status": "active",
            "pay": {"pay_basis": "monthly", "base_rate": "45000.00", "currency": "BDT",
                    "since": "2025-01-01"},
            "has_login": True, "needs_setup": []}]},
        errors=BASE_ERRORS,
    )
    def get(self, request):
        from access_control.branch_access import ALL_BRANCHES
        from base_template.views import employee_list_query, setup_gaps

        params = {"q": request.query_params.get("q", ""),
                  "status": request.query_params.get("status", ""),
                  "branch": request.query_params.get("branch_id", ""),
                  "setup": request.query_params.get("setup", "")}
        listing = employee_list_query(_Listing(request, params))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(listing.queryset, request, view=self)
        company = Company.objects.get(pk=request.company_id)
        sees_pay = _key_sees_pay(request)
        rows = []
        for employee in page:
            assignment = (employee.assignments.select_related("branch", "department", "designation")
                          .exclude(status="cancelled").order_by("-effective_from").first())
            compensation = (employee.compensations.exclude(status="cancelled")
                            .order_by("-effective_from").first())
            branch_id = assignment.branch_id if assignment else None
            show = sees_pay and (branch_id in listing.salary_branches if branch_id
                                 else listing.salary_branches is ALL_BRANCHES)
            rows.append({
                "id": employee.pk,
                "employee_code": assignment.employee_code if assignment else None,
                "name": employee.full_name, "work_email": employee.work_email,
                "branch": _ref(assignment.branch) if assignment else None,
                "department": _ref(assignment.department) if assignment else None,
                "designation": _ref(assignment.designation) if assignment else None,
                "employment_status": employee.employment_status,
                "pay": _pay(compensation, company) if show else None,
                "has_login": employee.user_id is not None,
                "needs_setup": setup_gaps(employee, assignment, compensation, show),
            })
        return paginator.get_paginated_response(s.EmployeeRowSerializer(rows, many=True).data)

    @endpoint(
        id="employees-create", area=AREA, title="Add an employee",
        summary="A new employee, placed in a branch, department and designation.",
        what_it_does=["Creates the person, their placement and (where you set pay) their pay, "
                      "in one go - as Create employee does."],
        description=("The Employee ID may not be held by anyone whose placement is still open. "
                     "Pay: where you prepare salary, base_rate is required; elsewhere leave it "
                     "out and whoever prepares that branch's salary sets it (GET "
                     "/api/v1/employees/choices says which applies). The answer is their profile."),
        roles=["Whoever may add people: the owner, company administrator, HR, or anyone given "
               "Create and edit employees in a branch (a branch manager in theirs)"],
        scopes=["employees:write"], response_status=201,
        request=s.CreateSerializer, response=s.EmployeeSerializer,
        request_example={"first_name": "Rahim", "last_name": "Uddin", "employee_code": "E-0041",
                         "branch_id": 3, "department_id": 8, "designation_id": 31,
                         "manager_id": 12, "start_date": "2024-02-01", "pay_basis": "monthly",
                         "base_rate": "45000.00"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.CreateSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        membership, branch_ids, pay, branches, employees = creation_setup(
            request.user, request.company_id)
        if pay == "none" and values.get("base_rate") is not None:
            refuse({"base_rate": ["You do not set pay here. Leave it out; whoever prepares "
                                  "salary sets it."]})
        rate = values.get("base_rate")
        form = EmployeeCreateForm(
            data={"first_name": values["first_name"], "last_name": values.get("last_name", ""),
                  "employee_code": values["employee_code"], "branch": values["branch_id"],
                  "department": values["department_id"],
                  "designation": values["designation_id"],
                  "manager": values.get("manager_id") or "",
                  "effective_from": values["start_date"].isoformat(),
                  "pay_basis": values.get("pay_basis") or "monthly",
                  "base_rate": "" if rate is None else str(rate)},
            company=membership.company, branches=branches, employees=employees, pay=pay)
        names = {"branch": "branch_id", "department": "department_id",
                 "designation": "designation_id", "manager": "manager_id",
                 "effective_from": "start_date"}
        result = create_from_form(form, user=request.user, company_id=request.company_id,
                                  membership=membership, branch_ids=branch_ids, pay=pay)
        if result is None:
            refuse(form.errors, names)
        return _profile_response(request, result["employee"].pk, status=201)


class EmployeeExportView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "employee_list"

    @endpoint(
        id="employees-export", area=AREA, title="Download the Employees list",
        summary="The people you may see as an Excel (xlsx) or PDF file.",
        what_it_does=["Answers the file with the same people and filters as the list; the "
                      "download is kept in the audit log."],
        description=("Pay columns only for people whose pay you may see - and none at all for "
                     "an API key without payroll:read. More than 10,000 rows (Excel) or 1,500 "
                     "(PDF) is refused: narrow the filters."),
        roles=VIEWERS, scopes=["employees:read"],
        params=[Param("file_type", QUERY, "string", "xlsx (default) or pdf.", example="xlsx"),
                Param("q", QUERY, "string", "Search by name, email or Employee ID.",
                      example="rahim"),
                Param("status", QUERY, "string", "As the list.", example="active"),
                Param("branch_id", QUERY, "integer", "Only this branch.", example=3),
                Param("setup", QUERY, "string", "department, salary, both or complete.",
                      example="salary")],
        errors=BASE_ERRORS + ["validation_error"],
    )
    def get(self, request):
        from base_template.employee_export import export_employees
        from base_template.views import employee_list_query
        from common import exports

        fmt = request.query_params.get("file_type") or "xlsx"
        if fmt not in ("xlsx", "pdf"):
            refuse({"file_type": ["xlsx or pdf."]})
        params = {"q": request.query_params.get("q", ""),
                  "status": request.query_params.get("status", ""),
                  "branch": request.query_params.get("branch_id", ""),
                  "setup": request.query_params.get("setup", "")}
        listing = employee_list_query(_Listing(request, params))
        if not _key_sees_pay(request):
            listing.show_rate_column, listing.salary_branches = False, set()
        try:
            exports.check_size(listing.queryset.count(), fmt, noun="employees")
        except exports.TooManyRows as refusal:
            raise ApiError("validation_error", str(refusal)) from None
        # The panel's export reads the company from the request it is given.
        request._request.company_id = request.company_id
        return export_employees(request._request, listing, fmt)


class ChoicesView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_branch_departments"

    @endpoint(
        id="employees-choices", area=AREA, title="Choices for adding an employee",
        summary="The branches, departments, designations and managers you may pick.",
        what_it_does=["Answers what Create employee offers you: your branches, a branch's "
                      "departments, a department's designations, the managers.",
                      "Says whether you set pay when adding someone."],
        description="Call it with branch_id for that branch's departments, and with "
                    "department_id for that department's designations.",
        roles=["Whoever may add people (as Add an employee)"], scopes=["employees:read"],
        params=[Param("branch_id", QUERY, "integer", "Its active departments.", example=3),
                Param("department_id", QUERY, "integer", "Its active designations.", example=8)],
        response=s.ChoicesSerializer,
        response_example={"branches": [{"id": 3, "name": "Chattogram"}],
                          "departments": [{"id": 8, "name": "Software"}],
                          "designations": [{"id": 31, "name": "Software Engineer"}],
                          "managers": [{"id": 12, "name": "Karim Ahmed"}], "pay": "required"},
        errors=BASE_ERRORS + ["validation_error"],
    )
    def get(self, request):
        membership, branch_ids, pay, branches, employees = creation_setup(
            request.user, request.company_id)
        allowed = list(branches.order_by("name"))
        departments, designations = [], []
        branch_id = request.query_params.get("branch_id", "")
        if branch_id.isdigit() and int(branch_id) in {b.pk for b in allowed}:
            departments = list(Department.objects.filter(
                branch_id=int(branch_id), status=ActiveStatus.ACTIVE).order_by("name"))
        department_id = request.query_params.get("department_id", "")
        if department_id.isdigit():
            department = Department.objects.filter(pk=int(department_id)).first()
            if department is not None and department.branch_id in {b.pk for b in allowed}:
                designations = list(Designation.objects.filter(
                    department=department, status=ActiveStatus.ACTIVE).order_by("name"))
        return Response(s.ChoicesSerializer({
            "branches": [_ref(b) for b in allowed],
            "departments": [_ref(d) for d in departments],
            "designations": [_ref(d) for d in designations],
            "managers": [_person_ref(e) for e in employees], "pay": pay,
        }).data)


# --- one person ------------------------------------------------------------------------

class EmployeeDetailView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_detail"

    @endpoint(
        id="employees-get", area=AREA, title="One employee",
        summary="Their profile: details, placement, pay, login, personal information.",
        what_it_does=["Answers what the profile page shows about the person.",
                      "Says what you may do for them (may)."],
        description="Pay is null when you may not see it. A person outside the branches you "
                    "see answers permission_denied; another company's, not_found.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM],
        response=s.EmployeeSerializer, response_example=PROFILE_EXAMPLE,
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        return _profile_response(request, employee_id)

    @endpoint(
        id="employees-change", area=AREA, title="Change an employee's details",
        summary="Name, work email, phone or joining date.",
        what_it_does=["Changes only the fields sent; records it in the audit log.",
                      "A new name is sent to every device they are on."],
        description="Placement, pay, personal information and line manager have their own "
                    "endpoints.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.DetailsSerializer, response=s.EmployeeSerializer,
        request_example={"phone": "01711000099"}, response_example=PROFILE_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def patch(self, request, employee_id):
        _m, employee, _a, _c = _for_edit(request, employee_id)
        data = s.DetailsSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(EmployeeDetailsForm,
                       form_data(EmployeeDetailsForm, employee, dict(data.validated_data)),
                       instance=employee)
        with service_errors():
            edit.update_employee_details(actor=request.user, company_id=request.company_id,
                                         employee_id=employee.pk, values=dict(form.cleaned_data))
        return _profile_response(request, employee.pk)


class HistoryView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_detail"

    @endpoint(
        id="employees-history", area=AREA, title="An employee's history",
        summary="Every placement, salary and device link, and the latest changes.",
        what_it_does=["Answers the dated history behind the profile."],
        description="Salaries are listed only when you may see their pay.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM],
        response=s.HistorySerializer,
        response_example={
            "placements": [{"employee_code": "E-0041", "branch": {"id": 3, "name": "Chattogram"},
                            "department": {"id": 8, "name": "Software"},
                            "designation": {"id": 31, "name": "Software Engineer"},
                            "line_manager": None, "first_day": "2024-02-01", "last_day": None}],
            "salaries": [{"pay_basis": "monthly", "base_rate": "45000.00", "reason": "Yearly raise",
                          "first_day": "2025-01-01", "last_day": None}],
            "devices": [{"device": {"id": 2, "name": "Main gate"}, "user_number": "41",
                         "first_day": "2024-02-01", "last_day": None}],
            "events": [{"at": "2025-01-01T10:00:00+06:00", "action": "employee.salary_changed",
                        "label": "Salary changed", "by": "owner@acme.com.bd"}]},
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _exists(employee_id)
        page = detail.employee_history(actor=request.user, company_id=request.company_id,
                                       employee_id=employee_id)
        sees_pay = page["may"]["salary"] and _key_sees_pay(request)
        events = [e for e in page["events"]
                  if sees_pay or e.action not in detail.SALARY_ACTIONS]
        return Response(s.HistorySerializer({
            "placements": [{
                "employee_code": p.row.employee_code, "branch": _ref(p.row.branch),
                "department": _ref(p.row.department), "designation": _ref(p.row.designation),
                "line_manager": _person_ref(p.row.manager),
                "first_day": p.first_day, "last_day": p.last_day} for p in page["placements"]],
            "salaries": [{
                "pay_basis": p.row.pay_basis, "base_rate": p.row.base_rate, "reason": p.row.reason,
                "first_day": p.first_day, "last_day": p.last_day}
                for p in page["salaries"]] if sees_pay else [],
            "devices": [{
                "device": _ref(p.row.device), "user_number": p.row.device_user_id,
                "first_day": p.first_day, "last_day": p.last_day} for p in page["devices"]],
            "events": [{"at": e.occurred_at, "action": e.action, "label": e.label,
                        "by": e.actor_user.email if e.actor_user else None} for e in events],
        }).data)


class PersonalView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_personal"

    @endpoint(
        id="employees-personal", area=AREA, title="Change personal information",
        summary="Date of birth, IDs, address, emergency contact and the rest.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description="As the profile's Personal card. The name, work email and phone are "
                    "changed with Change an employee's details.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.PersonalInputSerializer, response=s.EmployeeSerializer,
        request_example={"blood_group": "B+", "emergency_contact_name": "Fatema Uddin",
                         "emergency_contact_phone": "01811000000",
                         "emergency_contact_relation": "Mother"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def patch(self, request, employee_id):
        _exists(employee_id)
        _m, employee = profile.editable(request.user, request.company_id, employee_id)
        data = s.PersonalInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(profile.PersonalForm,
                       form_data(profile.PersonalForm, employee, dict(data.validated_data)),
                       instance=employee)
        with service_errors():
            profile.save_personal(actor=request.user, company_id=request.company_id,
                                  employee_id=employee.pk, form=form)
        return _profile_response(request, employee.pk)


class PlacementView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-placement", area=AREA, title="Change a placement",
        summary="A new branch, department, designation or Employee ID, from a date.",
        what_it_does=["A later date keeps the old placement as history.",
                      "The current placement's date or an earlier one replaces it from that "
                      "date (the latest save wins).",
                      "Attendance from that date is rebuilt for the new placement."],
        description="The branch must be one where you may edit people. Send every field, as "
                    "on Edit employee's Placement card.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.PlacementInputSerializer, response=s.EmployeeSerializer,
        request_example={"branch_id": 3, "department_id": 8, "designation_id": 32,
                         "employee_code": "E-0041", "from_date": "2026-11-01",
                         "reason": "Promoted"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        membership, employee, assignment, _c = _for_edit(request, employee_id)
        data = s.PlacementInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        may = edit.card_permissions(request.user, request.company_id, membership, assignment)
        branches = edit.placement_branches(request.user, request.company_id, membership, may)
        form = checked(PlacementForm, {
            "branch": values["branch_id"], "department": values["department_id"],
            "designation": values["designation_id"], "employee_code": values["employee_code"],
            "placement_from": values["from_date"].isoformat(),
            "placement_reason": values.get("reason", "")},
            PLACEMENT_NAMES, company=membership.company, branches=branches)
        try:
            with service_errors(PLACEMENT_NAMES):
                edit.change_placement(actor=request.user, company_id=request.company_id,
                                      employee_id=employee.pk, values=form.service_values())
        except IntegrityError:
            refuse({"employee_code": ["That Employee ID is already in use for those dates."]})
        return _profile_response(request, employee.pk)


class SalaryView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"
    read_scope, write_scope = "payroll:read", "payroll:write"

    @endpoint(
        id="employees-salary", area=AREA, title="Change pay",
        summary="A new pay basis or rate, from a date.",
        what_it_does=["A later date keeps the old salary as history.",
                      "The current salary's date or an earlier one (not before they were "
                      "placed) replaces it from that date (the latest save wins)."],
        description="Needs Prepare salary and View salaries in their branch (the owner and "
                    "company administrator have both). Regenerate the month's salary to apply it.",
        roles=["The owner, company administrator, or anyone given Prepare salary and View "
               "salaries in their branch"],
        scopes=["payroll:write"], params=[EMPLOYEE_PARAM],
        request=s.SalaryInputSerializer, response=s.EmployeeSerializer,
        request_example={"pay_basis": "monthly", "base_rate": "50000.00",
                         "from_date": "2026-11-01", "reason": "Yearly raise"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        membership, employee, assignment, _c = _for_edit(request, employee_id)
        may = edit.card_permissions(request.user, request.company_id, membership, assignment)
        if not may["salary"]:
            raise PermissionDenied("Changing this person's pay is not yours to do.")
        data = s.SalaryInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"salary_from": "from_date", "salary_reason": "reason"}
        form = checked(SalaryForm, {
            "pay_basis": values["pay_basis"], "base_rate": str(values["base_rate"]),
            "salary_from": values["from_date"].isoformat(),
            "salary_reason": values.get("reason", "")}, names, company=membership.company)
        with service_errors(names):
            edit.change_salary(actor=request.user, company_id=request.company_id,
                               employee_id=employee.pk, values=form.service_values())
        return _profile_response(request, employee.pk)


class LineManagerView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_line_manager"

    @endpoint(
        id="employees-line-manager", area=AREA, title="Set the line manager",
        summary="Who they report to now.",
        what_it_does=["Records it on their current placement; records it in the audit log."],
        description="Not dated: it is who they report to now. It does not change who approves "
                    "their leave. Choose someone working in a branch you see, or null.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.LineManagerInputSerializer, response=s.EmployeeSerializer,
        request_example={"manager_id": 12}, response_example=PROFILE_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def put(self, request, employee_id):
        _m, employee, assignment, _c = _for_edit(request, employee_id)
        data = s.LineManagerInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        manager_id = data.validated_data["manager_id"]
        choices = info.line_manager_form_choices(request.user, request.company_id, employee,
                                                 assignment)
        form = checked(info.LineManagerForm, {"manager": manager_id or ""},
                       {"manager": "manager_id"}, choices=choices)
        with service_errors({"manager": "manager_id"}):
            info.set_line_manager(actor=request.user, company_id=request.company_id,
                                  employee_id=employee.pk, manager=form.cleaned_data["manager"])
        return _profile_response(request, employee.pk)


# --- status -------------------------------------------------------------------------

class EndEmploymentView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_end"

    @endpoint(
        id="employees-end", area=AREA, title="End employment",
        summary="They left: resigned, terminated or retired, on their last working day.",
        what_it_does=[
            "Closes their placement and pay after the last day; nothing is counted for them "
            "after it.",
            "Disables their login and removes them from the devices, unless told not to.",
            "Rebuilds their attendance after the last day.",
        ],
        description=("The last day is today or earlier, not before their current placement or "
                     "salary began, and not inside a finalised salary month. A branch login may "
                     "not end someone with more than an Employee login, nor themselves. "
                     "devices_by_hand lists terminals to delete them on yourself."),
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.EndInputSerializer, response=s.EndedSerializer,
        request_example={"last_day": "2026-10-31", "status": "resigned",
                         "reason": "Resigned to study abroad; notice served"},
        response_example={"employee": {**PROFILE_EXAMPLE, "employment_status": "resigned",
                                       "has_left": True, "leaving_date": "2026-10-31"},
                          "login_disabled": True, "enrollments_ended": 1,
                          "devices_cleared": ["Main gate"], "devices_by_hand": []},
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        _exists(employee_id)
        page = detail.employee_history(actor=request.user, company_id=request.company_id,
                                       employee_id=employee_id)
        if not page["may"]["end"]:
            raise PermissionDenied("Ending this person's employment is not yours to do.")
        data = s.EndInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form = checked(EndEmploymentForm, {
            "last_day": values["last_day"].isoformat(), "status": values["status"],
            "reason": values["reason"], "disable_login": values["disable_login"],
            "end_device_enrollments": values["end_device_enrollments"]})
        cleaned = form.cleaned_data
        with service_errors():
            employee, summary = detail.end_employment(
                actor=request.user, company_id=request.company_id, employee_id=employee_id,
                last_day=cleaned["last_day"], status=cleaned["status"], reason=cleaned["reason"],
                disable_login=cleaned["disable_login"],
                end_device_enrollments=cleaned["end_device_enrollments"])
        try:
            after = _profile(request, employee.pk)
        except PermissionDenied:
            after = None          # a branch login no longer sees someone who left
        return Response(s.EndedSerializer({"employee": after, **summary}).data)


class InactiveView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_active"

    @endpoint(
        id="employees-inactive", area=AREA, title="Make inactive for a while",
        summary="Inactive from a day, until a day or until made active.",
        what_it_does=["Their scans on those days are blocked and not paid.",
                      "With an end date they are active again the day after, by themselves."],
        description="Not for someone who has left, and not for yourself.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.InactiveInputSerializer, response=s.EmployeeSerializer,
        request_example={"start_date": "2026-11-01", "end_date": "2026-11-30",
                         "reason": "Unpaid leave of absence"},
        response_example={**PROFILE_EXAMPLE, "employment_status": "inactive"},
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        _exists(employee_id)
        data = s.InactiveInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        end = values.get("end_date")
        form = checked(inactive.InactiveForm, {
            "start_date": values["start_date"].isoformat(),
            "end_date": end.isoformat() if end else "", "reason": values["reason"]})
        with service_errors():
            inactive.make_inactive(actor=request.user, company_id=request.company_id,
                                   employee_id=employee_id, **form.cleaned_data)
        return _profile_response(request, employee_id)


class ActiveView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_active"

    @endpoint(
        id="employees-active", area=AREA, title="Make active again",
        summary="Active from today; their scans from today count again.",
        what_it_does=["Ends their inactive period today."],
        description="Only for someone who is inactive; not for yourself.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        response=s.EmployeeSerializer, response_example=PROFILE_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        _exists(employee_id)
        with service_errors():
            inactive.make_active(actor=request.user, company_id=request.company_id,
                                 employee_id=employee_id)
        return _profile_response(request, employee_id)
