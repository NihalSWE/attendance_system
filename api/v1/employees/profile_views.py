"""Employees, part 2: the photo, education, documents, the login, leave
policy and balances, overtime, report visibility, who reports to them, device
permissions, and importing a file (docs/api/30-employees.md).

The same gate, form and service as the profile's modals and the Import page.
Files travel as base64 in JSON (api/core/files.py) and are checked by the
panel's own form; stored files come back only to someone who may see the
person.
"""

from django.core.exceptions import PermissionDenied
from django.http import HttpResponse
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.files import private_file, upload_from
from api.core.forms import checked, form_data, refuse, service_errors
from api.core.permissions import PanelRule
from api.v1.employees import serializers as s
from api.v1.employees.views import (
    BASE_ERRORS, EDITORS, EMPLOYEE_PARAM, ONE_ERRORS, PROFILE_EXAMPLE, VIEWERS, WRITE_ERRORS,
    EmployeeView, _exists, _for_edit, _profile_response, _ref)
from common.choices import ActiveStatus
from employees.models import EmployeeDocument, EmployeeEducation
from organization import employee_devices, employee_login
from organization import employee_actions as actions
from organization import employee_edit_services as edit
from organization import employee_profile as profile
from organization import employee_profile_info as info
from organization import employee_records as records
from organization import import_services
from organization.employee_edit_forms import GiveLoginForm, LoginPasswordForm, LoginRoleForm
from organization.services import visible_branches

AREA = "employees"
ROW_PARAM = Param("row_id", PATH, "integer", "The row id.", required=True, example=5)
DOC_PARAM = Param("document_id", PATH, "integer", "The document id.", required=True, example=9)
EDUCATION_EXAMPLE = {"id": 5, "qualification": "BSc in Computer Science",
                     "institution": "University of Chittagong", "subject": "CSE",
                     "result": "CGPA 3.6", "passing_year": 2019, "note": ""}
DOCUMENT_EXAMPLE = {"id": 9, "kind": "national_id", "title": "National ID card", "note": "",
                    "file_name": "nid.pdf", "added_at": "2026-10-05T10:00:00+06:00"}
LOGIN_EXAMPLE = {"email": "rahim@acme.com.bd", "role": "employee", "label": "Employee",
                 "active": True, "branches": []}
LOGIN_NAMES = {"login_email": "email", "login_password": "password",
               "login_password_confirm": "password_confirm", "login_role": "role",
               "login_branches": "branch_ids", "login_new_password": "password",
               "login_new_password_confirm": "password_confirm",
               # the service's own names
               "branches": "branch_ids"}
LOGIN_ERRORS = WRITE_ERRORS + ONE_ERRORS


def _viewable(request, employee_id):
    _exists(employee_id)
    return edit.get_employee_for_edit(actor=request.user, company_id=request.company_id,
                                      employee_id=employee_id, code="employees.view")


# --- photo ---------------------------------------------------------------------------

class PhotoView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "organization:employee_photo",
                  "PUT": "organization:employee_photo_change",
                  "DELETE": "organization:employee_photo_change"}

    @endpoint(
        id="employees-photo-get", area=AREA, title="An employee's photo",
        summary="The photo itself (an image), to someone who may see them.",
        what_it_does=["Answers the picture - PNG, JPEG or WEBP - never a public address."],
        description="No photo answers not_found. Cache it privately for a few minutes at most.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM],
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _m, employee, _a, _c = _viewable(request, employee_id)
        if not employee.photo:
            raise ApiError("not_found", "They have no photo.")
        return private_file(employee.photo)

    @endpoint(
        id="employees-photo-put", area=AREA, title="Upload a photo",
        summary="A new photo for their profile.",
        what_it_does=["Replaces their photo; records it in the audit log."],
        description="PNG, JPG or WEBP, up to 3 MB, and a real picture (it is opened to check).",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.FileInputSerializer, response=s.EmployeeSerializer,
        request_example={"filename": "rahim.jpg", "content_base64": "/9j/4AAQSkZJRgABAQ…"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS + ["payload_too_large"],
    )
    def put(self, request, employee_id):
        _exists(employee_id)
        profile.editable(request.user, request.company_id, employee_id)
        data = s.FileInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        upload = upload_from(data.validated_data["filename"], data.validated_data["content_base64"])
        form = checked(profile.PhotoForm, {}, {"photo": "content_base64"},
                       files={"photo": upload})
        with service_errors({"photo": "content_base64"}):
            profile.save_photo(actor=request.user, company_id=request.company_id,
                               employee_id=employee_id, upload=form.cleaned_data["photo"])
        return _profile_response(request, employee_id)

    @endpoint(
        id="employees-photo-delete", area=AREA, title="Remove the photo",
        summary="They have no photo any more.",
        what_it_does=["Removes the photo; records it in the audit log."],
        description="The profile shows their initials instead.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        response=s.EmployeeSerializer, response_example={**PROFILE_EXAMPLE, "has_photo": False},
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def delete(self, request, employee_id):
        _exists(employee_id)
        with service_errors():
            profile.save_photo(actor=request.user, company_id=request.company_id,
                               employee_id=employee_id, remove=True)
        return _profile_response(request, employee_id)


# --- education ----------------------------------------------------------------------

class EducationListView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "organization:employee_detail",
                  "POST": "organization:employee_education_add"}

    @endpoint(
        id="employees-education-list", area=AREA, title="Education history",
        summary="Their qualifications.",
        what_it_does=["Lists the profile's Education history."],
        description="Seen by whoever may see the person.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM], paginated=True,
        response=s.EducationSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [EDUCATION_EXAMPLE]},
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _m, employee, _a, _c = _viewable(request, employee_id)
        rows = records.records(request.company_id, employee)["education"]
        return self.paginated(request, list(rows), s.EducationSerializer)

    @endpoint(
        id="employees-education-add", area=AREA, title="Add a qualification",
        summary="One more line in their Education history.",
        what_it_does=["Adds it; records it in the audit log."],
        description="qualification is needed; the year passed is between 1940 and next year.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM], response_status=201,
        request=s.EducationInputSerializer, response=s.EducationSerializer,
        request_example={k: v for k, v in EDUCATION_EXAMPLE.items() if k != "id"},
        response_example=EDUCATION_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        _for_edit(request, employee_id)
        data = s.EducationInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(records.EducationForm,
                       form_data(records.EducationForm, None, dict(data.validated_data)))
        with service_errors():
            row = records.save_education(actor=request.user, company_id=request.company_id,
                                         employee_id=employee_id, values=form.cleaned_data)
        return Response(s.EducationSerializer(row).data, status=201)


class EducationRowView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = {"PATCH": "organization:employee_education_edit",
                  "DELETE": "organization:employee_education_remove"}

    def _row(self, request, employee_id, row_id):
        _for_edit(request, employee_id)
        row = EmployeeEducation.objects.filter(pk=row_id, employee_id=employee_id).first()
        if row is None:
            raise ApiError("not_found", "No such qualification on this profile.")
        return row

    @endpoint(
        id="employees-education-change", area=AREA, title="Change a qualification",
        summary="New details for one line of their Education history.",
        what_it_does=["Changes only the fields sent; records it in the audit log."],
        description="As adding one.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM, ROW_PARAM],
        request=s.EducationInputSerializer, response=s.EducationSerializer,
        request_example={"result": "CGPA 3.7"},
        response_example={**EDUCATION_EXAMPLE, "result": "CGPA 3.7"},
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def patch(self, request, employee_id, row_id):
        row = self._row(request, employee_id, row_id)
        data = s.EducationInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(records.EducationForm,
                       form_data(records.EducationForm, row, dict(data.validated_data)),
                       instance=row)
        with service_errors():
            row = records.save_education(actor=request.user, company_id=request.company_id,
                                         employee_id=employee_id, values=form.cleaned_data,
                                         row_id=row.pk)
        return Response(s.EducationSerializer(row).data)

    @endpoint(
        id="employees-education-remove", area=AREA, title="Remove a qualification",
        summary="Takes one line off their Education history.",
        what_it_does=["Removes it; records it in the audit log."],
        description="It cannot be undone; add it again if needed.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM, ROW_PARAM],
        response=s.EducationSerializer, response_example=EDUCATION_EXAMPLE,
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def delete(self, request, employee_id, row_id):
        row = self._row(request, employee_id, row_id)
        out = s.EducationSerializer(row).data
        records.remove_education(actor=request.user, company_id=request.company_id,
                                 employee_id=employee_id, row_id=row.pk)
        return Response(out)


# --- documents ------------------------------------------------------------------------

def _document_out(row):
    return {"id": row.pk, "kind": row.kind, "title": row.title, "note": row.note,
            "file_name": row.file_name, "added_at": row.created_at}


class DocumentListView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "organization:employee_document",
                  "POST": "organization:employee_document_add"}

    @endpoint(
        id="employees-documents-list", area=AREA, title="Documents",
        summary="Their documents (ID card, certificates, contract, …).",
        what_it_does=["Lists the profile's Employee documents."],
        description="Fetch one file with GET …/documents/{document_id}.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM], paginated=True,
        response=s.DocumentSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [DOCUMENT_EXAMPLE]},
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _m, employee, _a, _c = _viewable(request, employee_id)
        rows = [_document_out(row) for row in records.records(request.company_id,
                                                              employee)["documents"]]
        return self.paginated(request, rows, s.DocumentSerializer)

    @endpoint(
        id="employees-documents-add", area=AREA, title="Add a document",
        summary="A document on their profile.",
        what_it_does=["Stores the file under a random name; records it in the audit log."],
        description="PDF, JPG, PNG or WEBP, up to 5 MB, and really that (it is opened to check). "
                    "Only people who may see the employee can fetch it.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM], response_status=201,
        request=s.DocumentInputSerializer, response=s.DocumentSerializer,
        request_example={"kind": "national_id", "title": "National ID card", "note": "",
                         "filename": "nid.pdf", "content_base64": "JVBERi0xLjcK…"},
        response_example=DOCUMENT_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS + ["payload_too_large"],
    )
    def post(self, request, employee_id):
        _for_edit(request, employee_id)
        data = s.DocumentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        upload = upload_from(values["filename"], values["content_base64"])
        form = checked(records.DocumentForm,
                       {"kind": values["kind"], "title": values["title"],
                        "note": values.get("note", "")},
                       {"file": "content_base64"}, files={"file": upload})
        with service_errors({"file": "content_base64"}):
            row = records.add_document(actor=request.user, company_id=request.company_id,
                                       employee_id=employee_id, values=form.cleaned_data)
        return Response(s.DocumentSerializer(_document_out(row)).data, status=201)


class DocumentView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "organization:employee_document",
                  "DELETE": "organization:employee_document_remove"}

    @endpoint(
        id="employees-documents-get", area=AREA, title="One document",
        summary="The file itself, to someone who may see them.",
        what_it_does=["Answers the file (PDF or picture) with its original name."],
        description="Never at a public address.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM, DOC_PARAM],
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id, document_id):
        _exists(employee_id)
        if not EmployeeDocument.objects.filter(pk=document_id, employee_id=employee_id).exists():
            raise ApiError("not_found", "No such document on this profile.")
        row = records.document_for(actor=request.user, company_id=request.company_id,
                                   employee_id=employee_id, row_id=document_id)
        return private_file(row.file, row.file_name)

    @endpoint(
        id="employees-documents-remove", area=AREA, title="Remove a document",
        summary="Takes a document off their profile, and its file off the server.",
        what_it_does=["Removes it; records it in the audit log."],
        description="It cannot be undone.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM, DOC_PARAM],
        response=s.DocumentSerializer, response_example=DOCUMENT_EXAMPLE,
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def delete(self, request, employee_id, document_id):
        _for_edit(request, employee_id)
        row = EmployeeDocument.objects.filter(pk=document_id, employee_id=employee_id).first()
        if row is None:
            raise ApiError("not_found", "No such document on this profile.")
        out = s.DocumentSerializer(_document_out(row)).data
        records.remove_document(actor=request.user, company_id=request.company_id,
                                employee_id=employee_id, row_id=row.pk)
        return Response(out)


# --- the login ------------------------------------------------------------------------

def _login_out(company_id, employee):
    member = employee_login.login_for(company_id, employee)
    if member is None:
        raise ApiError("not_found", "They have no login.")
    return {"email": member.user.email, "role": member.role,
            "label": employee_login.ROLE_LABELS.get(member.role, member.get_role_display()),
            "active": member.status == "active",
            "branches": [_ref(b) for b in member.allowed_branches.order_by("name")]}


def _login_setup(request, employee_id, card):
    """The person, after the Edit employee page's checks: employees.edit for
    them, and the login card (``logins``, or ``role`` for the access level)."""
    membership, employee, assignment, _c = _for_edit(request, employee_id)
    may = edit.card_permissions(request.user, request.company_id, membership, assignment)
    if not may[card]:
        raise PermissionDenied("That part of this page is the company's to change.")
    branches = visible_branches(membership).filter(status=ActiveStatus.ACTIVE).order_by("name")
    return membership, employee, branches


class LoginView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-login-get", area=AREA, title="Their login",
        summary="The login they sign in with: email, access, enabled or not.",
        what_it_does=["Answers their login, or not_found when they have none."],
        description="Passwords are never shown.",
        roles=EDITORS, scopes=["employees:read"], params=[EMPLOYEE_PARAM],
        response=s.LoginOutSerializer, response_example=LOGIN_EXAMPLE,
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _m, employee, _a, _c = _for_edit(request, employee_id)
        return Response(s.LoginOutSerializer(_login_out(request.company_id, employee)).data)

    @endpoint(
        id="employees-login-create", area=AREA, title="Give a login",
        summary="A new login for someone who has none.",
        what_it_does=["Creates their account with the email and password given.",
                      "Links it to the employee with the access chosen."],
        description=("The email must not have a login already - nobody can reach another "
                     "person's account this way. A branch login may give Employee logins only, "
                     "to people in branches where they manage logins."),
        roles=["The owner, company administrator, or anyone given Manage logins in their branch"],
        scopes=["employees:write"], params=[EMPLOYEE_PARAM], response_status=201,
        request=s.GiveLoginSerializer, response=s.LoginOutSerializer,
        request_example={"email": "rahim@acme.com.bd", "password": "Start-2026-pass",
                         "password_confirm": "Start-2026-pass", "role": "employee"},
        response_example=LOGIN_EXAMPLE, errors=LOGIN_ERRORS,
    )
    def post(self, request, employee_id):
        _m, employee, branches = _login_setup(request, employee_id, "logins")
        data = s.GiveLoginSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form = checked(GiveLoginForm, {
            "login_email": values["email"], "login_password": values["password"],
            "login_password_confirm": values["password_confirm"], "login_role": values["role"],
            "login_branches": values["branch_ids"]}, LOGIN_NAMES, branches=branches)
        with service_errors(LOGIN_NAMES):
            employee_login.give_login(actor=request.user, company_id=request.company_id,
                                      employee_id=employee.pk, values=form.service_values())
        employee.refresh_from_db()
        return Response(s.LoginOutSerializer(_login_out(request.company_id, employee)).data,
                        status=201)

    @endpoint(
        id="employees-login-role", area=AREA, title="Change a login's access",
        summary="Employee, branch manager (with branches) or HR.",
        what_it_does=["Changes the access; records it in the audit log."],
        description="The owner or company administrator only.",
        roles=["The owner or company administrator"], scopes=["employees:write"],
        params=[EMPLOYEE_PARAM], request=s.LoginRoleInputSerializer,
        response=s.LoginOutSerializer,
        request_example={"role": "manager", "branch_ids": [3]},
        response_example={**LOGIN_EXAMPLE, "role": "manager", "label": "Branch manager",
                          "branches": [{"id": 3, "name": "Chattogram"}]},
        errors=LOGIN_ERRORS,
    )
    def patch(self, request, employee_id):
        _m, employee, branches = _login_setup(request, employee_id, "role")
        data = s.LoginRoleInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(LoginRoleForm, {"login_role": data.validated_data["role"],
                                       "login_branches": data.validated_data["branch_ids"]},
                       LOGIN_NAMES, branches=branches)
        with service_errors(LOGIN_NAMES):
            employee_login.change_login_role(actor=request.user, company_id=request.company_id,
                                             employee_id=employee.pk, values=form.role_values())
        return Response(s.LoginOutSerializer(_login_out(request.company_id, employee)).data)


class LoginPasswordView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-login-password", area=AREA, title="Set a new password for their login",
        summary="A new password, to give to them.",
        what_it_does=["Sets it; every session of that login on the API ends."],
        description="Not for someone who also belongs to another company (only they change "
                    "theirs), and not for a branch manager's login unless you are the company.",
        roles=["The owner, company administrator, or anyone given Manage logins in their branch"],
        scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.PasswordInputSerializer, response=s.LoginOutSerializer,
        request_example={"password": "New-2026-pass", "password_confirm": "New-2026-pass"},
        response_example=LOGIN_EXAMPLE, errors=LOGIN_ERRORS,
    )
    def post(self, request, employee_id):
        _m, employee, _b = _login_setup(request, employee_id, "logins")
        data = s.PasswordInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(LoginPasswordForm, {
            "login_new_password": data.validated_data["password"],
            "login_new_password_confirm": data.validated_data["password_confirm"]}, LOGIN_NAMES)
        with service_errors(LOGIN_NAMES):
            employee_login.reset_login_password(actor=request.user, company_id=request.company_id,
                                                employee_id=employee.pk,
                                                values=form.service_values())
        return Response(s.LoginOutSerializer(_login_out(request.company_id, employee)).data)


def _set_active(view, request, employee_id, active):
    _m, employee, _b = _login_setup(request, employee_id, "logins")
    with service_errors():
        employee_login.set_login_active(actor=request.user, company_id=request.company_id,
                                        employee_id=employee.pk, active=active)
    return Response(s.LoginOutSerializer(_login_out(request.company_id, employee)).data)


class LoginDisableView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-login-disable", area=AREA, title="Disable their login",
        summary="They can no longer sign in to this company.",
        what_it_does=["Suspends the login in this company (another company's is untouched)."],
        description="Enable it again with …/login/enable.",
        roles=["The owner, company administrator, or anyone given Manage logins in their branch"],
        scopes=["employees:write"], params=[EMPLOYEE_PARAM], response=s.LoginOutSerializer,
        response_example={**LOGIN_EXAMPLE, "active": False}, errors=LOGIN_ERRORS,
    )
    def post(self, request, employee_id):
        return _set_active(self, request, employee_id, False)


class LoginEnableView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-login-enable", area=AREA, title="Enable their login",
        summary="They can sign in again.",
        what_it_does=["Makes the login active in this company again."],
        description="Only for a disabled login.",
        roles=["The owner, company administrator, or anyone given Manage logins in their branch"],
        scopes=["employees:write"], params=[EMPLOYEE_PARAM], response=s.LoginOutSerializer,
        response_example=LOGIN_EXAMPLE, errors=LOGIN_ERRORS,
    )
    def post(self, request, employee_id):
        return _set_active(self, request, employee_id, True)


# --- leave, overtime, reports, line management ----------------------------------------

class LeavePolicyView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_leave_policy"
    read_scope, write_scope = "leave:read", "leave:write"

    @endpoint(
        id="employees-leave-policy", area=AREA, title="Give a leave policy",
        summary="Their leave follows this policy from a date (or the company default).",
        what_it_does=["Gives the policy from the date; their automatic leave from then is "
                      "worked out again."],
        description="The owner, company administrator or HR. Not before a policy they already "
                    "have from a later date.",
        roles=["The owner, company administrator or HR"], scopes=["leave:write"],
        params=[EMPLOYEE_PARAM], request=s.LeavePolicyInputSerializer,
        response=s.EmployeeSerializer,
        request_example={"policy_id": 2, "from_date": "2026-01-01"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def put(self, request, employee_id):
        from leaves import policies
        from leaves.forms import AssignPolicyForm
        from leaves.models import LeavePolicy

        _m, employee, _a, _c = _viewable(request, employee_id)
        data = s.LeavePolicyInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        names = {"policy": "policy_id", "effective_from": "from_date"}
        form = checked(AssignPolicyForm, {
            "policy": data.validated_data["policy_id"] or "",
            "effective_from": data.validated_data["from_date"].isoformat()},
            names, policies=LeavePolicy.objects.filter(status="active"))
        with service_errors(names):
            policies.assign_policy(actor=request.user, company_id=request.company_id,
                                   employee=employee, policy=form.cleaned_data["policy"],
                                   effective_from=form.cleaned_data["effective_from"])
        return _profile_response(request, employee_id)


class LeaveAdjustmentView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_leave_adjust"
    read_scope, write_scope = "leave:read", "leave:write"

    @endpoint(
        id="employees-leave-adjust", area=AREA, title="Adjust a leave balance",
        summary="Add or take away leave days by hand, with a reason.",
        what_it_does=["Records the days in their balance for that type and year."],
        description="The owner, company administrator or HR. days may be negative.",
        roles=["The owner, company administrator or HR"], scopes=["leave:write"],
        params=[EMPLOYEE_PARAM], response_status=201, request=s.AdjustmentInputSerializer,
        response=s.EmployeeSerializer,
        request_example={"leave_type_id": 1, "year": 2026, "days": "1.5",
                         "note": "Carried over by agreement"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def post(self, request, employee_id):
        from leaves import policies
        from leaves.forms import AdjustBalanceForm
        from leaves.models import LeaveType

        _m, employee, _a, _c = _viewable(request, employee_id)
        data = s.AdjustmentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"leave_type": "leave_type_id", "units": "days"}
        form = checked(AdjustBalanceForm, {
            "leave_type": values["leave_type_id"], "year": values["year"],
            "units": str(values["days"]), "note": values["note"]},
            names, leave_types=LeaveType.objects.filter(status="active"))
        cleaned = form.cleaned_data
        with service_errors(names):
            policies.adjust(actor=request.user, company_id=request.company_id, employee=employee,
                            leave_type=cleaned["leave_type"], year=cleaned["year"],
                            units=cleaned["units"], note=cleaned["note"])
        return _profile_response(request, employee_id, status=201)


class OvertimeView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_overtime"

    @endpoint(
        id="employees-overtime", area=AREA, title="Allow or disallow overtime",
        summary="No overtime for them from a day - or allowed again.",
        what_it_does=["Sets or clears the day; their days from then are worked out again."],
        description="Whoever decides overtime in their branch. Not inside a finalised month.",
        roles=["The owner, company administrator, or anyone given Decide overtime in their "
               "branch"],
        scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.OvertimeInputSerializer, response=s.EmployeeSerializer,
        request_example={"no_overtime_from": "2026-11-01"},
        response_example=PROFILE_EXAMPLE, errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def put(self, request, employee_id):
        from organization.employee_detail_services import company_today

        membership, _e, _a, _c = _viewable(request, employee_id)
        data = s.OvertimeInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        day = data.validated_data["no_overtime_from"]
        names = {"from_day": "no_overtime_from"}
        if day is not None:
            day = checked(actions.OvertimeForm, {"from_day": day.isoformat()},
                          names).cleaned_data["from_day"]
        with service_errors(names):
            actions.set_overtime(actor=request.user, company_id=request.company_id,
                                 employee_id=employee_id, from_day=day,
                                 today=company_today(membership.company))
        return _profile_response(request, employee_id)


class ReportVisibilityView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_report_visibility"

    @endpoint(
        id="employees-report-visibility", area=AREA, title="Leave out of the reports",
        summary="Hide them from the reports, or show them again.",
        what_it_does=["Sets whether the reports list them; their attendance still counts."],
        description="As the profile's Exclude from attendance report.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.VisibilityInputSerializer, response=s.EmployeeSerializer,
        request_example={"hidden": True}, response_example=PROFILE_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def put(self, request, employee_id):
        _exists(employee_id)
        data = s.VisibilityInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            profile.set_report_visibility(actor=request.user, company_id=request.company_id,
                                          employee_id=employee_id,
                                          hidden=data.validated_data["hidden"])
        return _profile_response(request, employee_id)


class ReportsView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_reports"

    @endpoint(
        id="employees-reports", area=AREA, title="Set as line manager",
        summary="Choose the people who report to them.",
        what_it_does=["Makes them the line manager of each person chosen."],
        description="People working in a branch you see. It does not change who approves "
                    "their leave.",
        roles=EDITORS, scopes=["employees:write"], params=[EMPLOYEE_PARAM],
        request=s.ReportsInputSerializer, response=s.EmployeeSerializer,
        request_example={"people_ids": [41, 42]}, response_example=PROFILE_EXAMPLE,
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def put(self, request, employee_id):
        _m, manager, _a, _c = _for_edit(request, employee_id)
        data = s.ReportsInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        names = {"people": "people_ids"}
        form = checked(actions.ReportsForm, {"people": data.validated_data["people_ids"]}, names,
                       choices=info.line_manager_choices(request.user, request.company_id,
                                                         manager))
        with service_errors(names):
            try:
                actions.set_reports(actor=request.user, company_id=request.company_id,
                                    manager_id=employee_id, people=form.cleaned_data["people"])
            except PermissionDenied as exc:
                refuse({"people": [str(exc)]}, names)
        return _profile_response(request, employee_id)


# --- device permissions ------------------------------------------------------------------

def _link_out(row):
    return {"id": row.pk, "device": _ref(row.device), "branch": _ref(row.device.branch),
            "user_number": row.device_user_id, "attendance_enabled": row.attendance_enabled,
            "assigned_device_authorized": row.assigned_device_authorized,
            "card_number": row.card_number, "device_privilege": row.device_privilege}


class DeviceLinksView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_detail"

    @endpoint(
        id="employees-devices", area=AREA, title="Their devices",
        summary="The devices they are on now, and their permissions there.",
        what_it_does=["Lists their current device links."],
        description="Change one with PATCH …/devices/{enrollment_id}.",
        roles=VIEWERS, scopes=["employees:read"], params=[EMPLOYEE_PARAM], paginated=True,
        response=s.DeviceLinkSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "id": 7, "device": {"id": 2, "name": "Main gate"}, "branch": {"id": 3, "name":
                                                                          "Chattogram"},
            "user_number": "41", "attendance_enabled": True, "assigned_device_authorized": True,
            "card_number": "", "device_privilege": "user"}]},
        errors=BASE_ERRORS + ONE_ERRORS,
    )
    def get(self, request, employee_id):
        _m, employee, _a, _c = _viewable(request, employee_id)
        rows = [_link_out(row) for row in employee_devices.current(request.company_id, employee)]
        return self.paginated(request, rows, s.DeviceLinkSerializer)


class DeviceLinkView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_device_permission"
    read_scope, write_scope = "devices:read", "devices:write"

    @endpoint(
        id="employees-device-change", area=AREA, title="Change their device permissions",
        summary="Whether their scans count there, assigned device, card, role on the terminal.",
        what_it_does=["Changes only the fields sent; records it in the audit log.",
                      "A new card or role is sent to the terminals they are on."],
        description="Whoever manages the company's devices.",
        roles=["Whoever manages the devices: the owner or company administrator"],
        scopes=["devices:write"],
        params=[EMPLOYEE_PARAM, Param("enrollment_id", PATH, "integer", "The device link id.",
                                      required=True, example=7)],
        request=s.DeviceLinkInputSerializer, response=s.DeviceLinkSerializer,
        request_example={"card_number": "0012345678"},
        response_example={"id": 7, "device": {"id": 2, "name": "Main gate"},
                          "branch": {"id": 3, "name": "Chattogram"}, "user_number": "41",
                          "attendance_enabled": True, "assigned_device_authorized": True,
                          "card_number": "0012345678", "device_privilege": "user"},
        errors=WRITE_ERRORS + ONE_ERRORS,
    )
    def patch(self, request, employee_id, enrollment_id):
        _m, employee, _a, _c = _viewable(request, employee_id)
        row = next((r for r in employee_devices.current(request.company_id, employee)
                    if r.pk == enrollment_id), None)
        if row is None:
            raise ApiError("not_found", "No such current device link for them.")
        data = s.DeviceLinkInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        form = checked(employee_devices.DevicePermissionForm,
                       form_data(employee_devices.DevicePermissionForm, row,
                                 dict(data.validated_data)), instance=row)
        with service_errors():
            saved, _result = employee_devices.change(
                actor=request.user, company_id=request.company_id, employee_id=employee_id,
                enrollment_id=enrollment_id, values=form.cleaned_data)
        return Response(s.DeviceLinkSerializer(_link_out(saved)).data)


# --- import ---------------------------------------------------------------------------------

class ImportView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_import"

    @endpoint(
        id="employees-import", area=AREA, title="Import employees from a file",
        summary="A .csv or Excel file of Employee ID and Name: check it, then import.",
        what_it_does=[
            "confirm false (default): reads and checks every row, writes nothing, answers "
            "what it would do and every bad row.",
            "confirm true: checks again and imports the new people into the branch - only when no "
            "row is bad, as on the panel.",
        ],
        description=("Send the same file twice: first to check, then with confirm true. "
                     "Everyone joins one branch (one you may add people to); they start in its "
                     "Unassigned department with no salary - set those afterwards. Employee IDs "
                     "are numbers. People already here are left as they are, or renamed when the "
                     "file names them differently. GET …/import/demo-file is a file to start "
                     "from."),
        roles=["Whoever may add people (as Add an employee)"], scopes=["employees:write"],
        request=s.ImportInputSerializer, response=s.ImportResultSerializer,
        request_example={"filename": "employees.xlsx", "content_base64": "UEsDBBQABgAIAAAAIQ…",
                         "branch_id": 3, "confirm": False},
        response_example={"imported": False, "branch": {"id": 3, "name": "Chattogram"},
                          "rows": 120, "new": 118, "existing": 1,
                          "bad": [{"line": 7, "employee_code": "A12", "name": "Karim",
                                   "problems": ["Employee ID “A12” is not a number."]}]},
        errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def post(self, request):
        data = s.ImportInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        upload = upload_from(values["filename"], values["content_base64"])
        names = {"branch": "branch_id", "upload": "content_base64"}
        with service_errors(names):
            branch = import_services.check_branch(request.user, request.company_id,
                                                  values.get("branch_id"))
            rows = import_services.check(request.user, request.company_id,
                                         import_services.read_file(upload))
        counts = import_services.summarise(rows)
        if values["confirm"] and counts["bad"]:
            # As the panel: Import is offered only for a file with no bad row.
            raise ApiError("validation_error",
                           f"{counts['bad']} row{'s' if counts['bad'] != 1 else ''} cannot be "
                           "imported - check the file (confirm false lists them), fix it and "
                           "send it again. Nothing was imported.")
        if values["confirm"]:
            with service_errors(names):
                import_services.commit(actor=request.user, company_id=request.company_id,
                                       rows=rows, branch_id=branch.pk)
        return Response(s.ImportResultSerializer({
            "imported": values["confirm"], "branch": _ref(branch), "rows": counts["total"],
            "new": counts["good"], "existing": counts["total"] - counts["good"] - counts["bad"],
            "bad": [{"line": row["line"], "employee_code": row.get("employee_id", ""),
                     "name": row.get("name", ""), "problems": row["errors"]}
                    for row in rows if row["errors"]],
        }).data)


class ImportDemoView(EmployeeView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_import_demo"

    @endpoint(
        id="employees-import-demo", area=AREA, title="The import demo file",
        summary="A file in the format the import reads: Employee ID and Name.",
        what_it_does=["Answers the demo file, CSV (default) or Excel (file_type=xlsx)."],
        description="Fill it in and send it to Import employees from a file.",
        roles=["Whoever may add people (as Add an employee)"], scopes=["employees:read"],
        params=[Param("file_type", QUERY, "string", "csv (default) or xlsx.", example="xlsx")],
        errors=BASE_ERRORS,
    )
    def get(self, request):
        import_services.import_scope(request.user, request.company_id)
        if request.query_params.get("file_type") == "xlsx":
            reply = HttpResponse(
                import_services.demo_xlsx(),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            reply["Content-Disposition"] = 'attachment; filename="employees-demo.xlsx"'
        else:
            reply = HttpResponse(import_services.demo_csv(), content_type="text/csv; charset=utf-8")
            reply["Content-Disposition"] = 'attachment; filename="employees-demo.csv"'
        return reply
