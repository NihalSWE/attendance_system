"""Company, branches, departments and designations (docs/api/00-PLAN.md,
phase 2; the guide: docs/api/20-company-and-branches.md).

Every endpoint mirrors a panel page: the same gate (``PanelRule``), the same
form for the input (``api/core/forms.py``) and the same service for the
change - so the API and the panel accept, refuse and record exactly alike.
"""

import base64
import binascii

from django.core.files.base import ContentFile
from django.db.models import Count, Q
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, form_data, service_errors
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.company import serializers as s
from common.choices import ActiveStatus
from organization import mail_settings as mail
from organization.adoption_forms import CopyAdoptionsForm, DepartmentAdoptionForm, DesignationForm
from organization.adoption_services import (
    adopt_department,
    copy_adoptions_between_branches,
    create_designation,
    provision_new_branch,
    set_adoption_status,
    set_designation_status,
    update_adoption,
    update_designation,
    visible_adoptions,
    visible_designations,
)
from organization.company_profile import CompanyProfileForm, save_profile
from organization.forms import BranchForm
from organization.services import (
    create_branch,
    require_company_membership,
    require_structure_manager,
    set_branch_status,
    update_branch,
    visible_branches,
)
from tenants.models import Company

AREA = "company"
ADMINS = ["Company owner or administrator"]
READERS = ["Company owner, administrator, HR or payroll"]
READ_ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
               "signature_required", "invalid_signature", "two_step_setup_required",
               "scope_missing", "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = READ_ERRORS + ["validation_error", "unknown_field"]
ID_ERRORS = ["not_found"]


def _membership(request):
    return require_company_membership(request.user, request.company_id)


def _param(name, words, example):
    return Param(name, PATH, "integer", words, required=True, example=example)


def _filters(*extra):
    return [Param("q", QUERY, "string", "Search by name or code.", example="dhaka"),
            Param("status", QUERY, "string", "active or inactive.", example="active"),
            *extra]


def _number(request, name):
    value = (request.query_params.get(name) or "").strip()
    if not value:
        return None
    if not value.isdigit():
        raise ApiError("validation_error", fields={name: ["A whole number."]})
    return int(value)


# --- company -----------------------------------------------------------------------

COMPANY_EXAMPLE = {"id": 12, "code": "ACME", "name": "Acme Textiles", "legal_name": "Acme Textiles Ltd.",
                   "contact_person": "Rahim Uddin", "email": "info@acme.com.bd",
                   "phone": "01711000000", "address": "House 12, Road 5, Dhaka",
                   "logo_url": "https://attendance.example.com/media/company_logos/acme.png",
                   "timezone": "Asia/Dhaka", "currency": "BDT", "status": "active"}


def _company_out(request, company):
    return {
        "id": company.pk, "code": company.code, "name": company.name,
        "legal_name": company.legal_name, "contact_person": company.contact_person,
        "email": company.email, "phone": company.phone, "address": company.address,
        "logo_url": request.build_absolute_uri(company.logo.url) if company.logo else None,
        "timezone": company.timezone, "currency": company.currency, "status": company.status,
    }


def _save_company(request, changes, files=None, clear_logo=False):
    company = Company.objects.get(pk=request.company_id)
    data = form_data(CompanyProfileForm, company, changes)
    if clear_logo:
        data["logo-clear"] = "on"
    form = checked(CompanyProfileForm, data, instance=company, files=files or {})
    with service_errors():
        company = save_profile(actor=request.user, company_id=request.company_id, form=form)
    return Response(s.CompanyProfileSerializer(_company_out(request, company)).data)


class CompanyView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:company_profile"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="company-get", area=AREA, title="The company",
        summary="The company's profile: name, contacts, logo.",
        what_it_does=["Answers the profile shown on Organisation → Company profile."],
        description="The code, time zone, currency and status are set by the platform and "
                    "cannot be changed here.",
        roles=ADMINS, scopes=["company:read"], response=s.CompanyProfileSerializer,
        response_example=COMPANY_EXAMPLE, errors=READ_ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        company = Company.objects.get(pk=request.company_id)
        return Response(s.CompanyProfileSerializer(_company_out(request, company)).data)

    @endpoint(
        id="company-change", area=AREA, title="Change the company",
        summary="A new name, registered name, contact person, email, phone or address.",
        what_it_does=["Changes only the fields sent.", "Records the change in the audit log."],
        description="The email and phone may not be used by another company. The logo has its "
                    "own endpoint (PUT /api/v1/company/logo).",
        roles=ADMINS, scopes=["company:write"],
        request=s.CompanyChangeSerializer, response=s.CompanyProfileSerializer,
        request_example={"contact_person": "Karim Ahmed", "phone": "01811000000"},
        response_example={**COMPANY_EXAMPLE, "contact_person": "Karim Ahmed",
                          "phone": "01811000000"},
        errors=WRITE_ERRORS,
    )
    def patch(self, request):
        data = s.CompanyChangeSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        return _save_company(request, dict(data.validated_data))


class CompanyLogoView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:company_profile"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="company-logo-put", area=AREA, title="Upload the logo",
        summary="A new logo for the panel and payslips.",
        what_it_does=["Replaces the logo with the file sent (JSON, base64)."],
        description=("Send the file in JSON - {filename, content_base64} - so the request can "
                     "be signed like any other. PNG, JPG, WEBP or SVG, at most 2 MB. Any shape "
                     "works; the panel scales it."),
        roles=ADMINS, scopes=["company:write"],
        request=s.LogoSerializer, response=s.CompanyProfileSerializer,
        request_example={"filename": "logo.png", "content_base64": "iVBORw0KGgoAAAANSUhEUg…"},
        response_example=COMPANY_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def put(self, request):
        data = s.LogoSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        try:
            content = base64.b64decode(data.validated_data["content_base64"], validate=True)
        except (binascii.Error, ValueError):
            raise ApiError("validation_error",
                           fields={"content_base64": ["This is not valid base64."]}) from None
        upload = ContentFile(content, name=data.validated_data["filename"].strip())
        return _save_company(request, {}, files={"logo": upload})

    @endpoint(
        id="company-logo-delete", area=AREA, title="Remove the logo",
        summary="The company has no logo any more.",
        what_it_does=["Removes the logo."],
        description="The panel shows the company's name instead.",
        roles=ADMINS, scopes=["company:write"], response=s.CompanyProfileSerializer,
        response_example={**COMPANY_EXAMPLE, "logo_url": None}, errors=READ_ERRORS,
    )
    def delete(self, request):
        return _save_company(request, {}, clear_logo=True)


# --- mail settings -------------------------------------------------------------------

MAIL_EXAMPLE = {"saved": True, "from_email": "hr@acme.com.bd", "from_name": "Acme Textiles",
                "host": "smtp.gmail.com", "port": 587, "security": "starttls",
                "username": "hr@acme.com.bd", "has_password": True, "is_active": True,
                "last_test": {"at": "2026-10-05T09:12:00+06:00", "ok": True, "message": ""},
                "sending_through": "company", "sending_from": "hr@acme.com.bd"}


def _mail_out(company_id):
    saved = mail.settings_for(company_id)
    how, from_email, _ = mail.sender(company_id)
    return {
        "saved": saved is not None,
        "from_email": saved.from_email if saved else "", "from_name": saved.from_name if saved else "",
        "host": saved.host if saved else "", "port": saved.port if saved else None,
        "security": saved.security if saved else "", "username": saved.username if saved else "",
        "has_password": bool(saved and saved.password_encrypted),
        "is_active": bool(saved and saved.is_active),
        "last_test": {"at": saved.last_tested_at if saved else None,
                      "ok": saved.last_test_ok if saved else None,
                      "message": saved.last_test_message if saved else ""},
        "sending_through": how, "sending_from": from_email,
    }


class MailSettingsView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:mail_settings"
    read_scope = write_scope = None          # it holds a mail password: people only
    throttle_scope = "write"

    @endpoint(
        id="company-mail-get", area=AREA, title="Email settings",
        summary="The company's own mail account, and how email goes out now.",
        what_it_does=["Answers the account (never its password) and the last test."],
        description=("Payslips, login codes and other emails go out through this account. "
                     "With none saved, or switched off, the server's account is used; with "
                     "neither, nothing can be sent (sending_through: null)."),
        roles=ADMINS, response=s.MailSettingsSerializer, response_example=MAIL_EXAMPLE,
        errors=READ_ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        return Response(s.MailSettingsSerializer(_mail_out(request.company_id)).data)

    @endpoint(
        id="company-mail-change", area=AREA, title="Change the email settings",
        summary="Save the company's mail account (SMTP).",
        what_it_does=["Saves the fields sent; the others keep their saved values.",
                      "The password is stored encrypted and never shown again."],
        description=("The first save needs from_email and host. Any provider that gives SMTP "
                     "details works (Gmail needs an App password; for SendGrid the username "
                     "is apikey and the API key is the password). Send a test after saving."),
        roles=ADMINS, request=s.MailSettingsChangeSerializer, response=s.MailSettingsSerializer,
        request_example={"from_email": "hr@acme.com.bd", "from_name": "Acme Textiles",
                         "host": "smtp.gmail.com", "port": 587, "security": "starttls",
                         "username": "hr@acme.com.bd", "password": "app-password"},
        response_example=MAIL_EXAMPLE, errors=WRITE_ERRORS,
    )
    def patch(self, request):
        data = s.MailSettingsChangeSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        changes = dict(data.validated_data)
        saved = mail.settings_for(request.company_id)
        has_password = bool(saved and saved.password_encrypted)
        values = form_data(mail.MailSettingsForm, saved, changes, has_password=has_password)
        if saved is None:
            values.setdefault("port", 587)
            values["security"] = values.get("security") or "starttls"
            if "is_active" not in changes:
                values["is_active"] = True
        form = checked(mail.MailSettingsForm, values, instance=saved, has_password=has_password)
        with service_errors():
            mail.save_settings(actor=request.user, company_id=request.company_id, form=form)
        return Response(s.MailSettingsSerializer(_mail_out(request.company_id)).data)


class MailTestView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:mail_settings"
    read_scope = write_scope = None
    throttle_scope = "write"

    @endpoint(
        id="company-mail-test", area=AREA, title="Send a test email",
        summary="A short test email through the saved account.",
        what_it_does=["Sends the test and remembers the result (see last_test)."],
        description="When it fails, the message says why, in the mail server's words.",
        roles=ADMINS, request=s.MailTestSerializer, response=s.DetailSerializer,
        request_example={"to": "owner@acme.com.bd"},
        response_example={"detail": "Test email sent to owner@acme.com.bd. Check that inbox."},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.MailTestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            to = mail.send_test(actor=request.user, company_id=request.company_id,
                                to=data.validated_data["to"])
        return Response({"detail": f"Test email sent to {to}. Check that inbox."})


# --- branches ----------------------------------------------------------------------

BRANCH_EXAMPLE = {"id": 3, "code": "CTG", "name": "Chattogram", "address": "Agrabad C/A",
                  "city": "Chattogram", "postal_code": "4100", "timezone": "Asia/Dhaka",
                  "email": "ctg@acme.com.bd", "phone": "01711000001", "is_default": False,
                  "status": "active", "opened_on": "2024-01-01", "closed_on": None,
                  "department_count": 5}
BRANCH_PARAM = _param("branch_id", "The branch id.", 3)


def _branches(request):
    return visible_branches(_membership(request)).annotate(
        department_count=Count("departments", distinct=True))


def _branch(request, branch_id):
    branch = _branches(request).filter(pk=branch_id).first()
    if branch is None:
        raise ApiError("not_found", "No such branch in this company.")
    return branch


class BranchListView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:branch_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="branches-list", area=AREA, title="Branches",
        summary="The company's branches.",
        what_it_does=["Lists the branches you may see, the default first."],
        description="Someone limited to some branches sees only those.",
        roles=READERS, scopes=["company:read"], paginated=True,
        params=_filters(), response=s.BranchSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [BRANCH_EXAMPLE]},
        errors=READ_ERRORS + ["validation_error"],
    )
    def get(self, request):
        rows = _branches(request)
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query)
                               | Q(city__icontains=query))
        status = request.query_params.get("status")
        if status in dict(ActiveStatus.choices):
            rows = rows.filter(status=status)
        return self.paginated(request, rows.order_by("-is_default", "name"), s.BranchSerializer)

    @endpoint(
        id="branches-create", area=AREA, title="Add a branch",
        summary="A new branch, with the company's departments copied into it.",
        what_it_does=[
            "Creates the branch.",
            "Copies the departments and designations of an existing branch into it, as the "
            "panel does (heads are not copied).",
        ],
        description="The code is unique in the company and stored in upper case. Making it the "
                    "default moves the default from the old one.",
        roles=ADMINS, scopes=["company:write"], response_status=201,
        request=s.BranchInputSerializer, response=s.BranchCreatedSerializer,
        request_example={"code": "CTG", "name": "Chattogram", "city": "Chattogram",
                         "address": "Agrabad C/A", "opened_on": "2024-01-01"},
        response_example={**BRANCH_EXAMPLE, "departments_copied": 5},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.BranchInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        company = Company.objects.get(pk=request.company_id)
        changes = {"status": ActiveStatus.ACTIVE, "is_default": False, **data.validated_data}
        if not changes.get("timezone"):
            changes["timezone"] = company.timezone
        form = checked(BranchForm, form_data(BranchForm, None, changes, company=company),
                       company=company)
        with service_errors():
            branch = create_branch(actor=request.user, company_id=request.company_id,
                                   values=form.cleaned_data)
            copied, _ = provision_new_branch(actor=request.user, company_id=request.company_id,
                                             branch=branch)
        out = s.BranchSerializer(_branch(request, branch.pk)).data
        return Response({**out, "departments_copied": len(copied)}, status=201)


class BranchDetailView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:branch_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="branches-get", area=AREA, title="One branch",
        summary="One branch's details.",
        what_it_does=["Answers the branch, with how many departments it has."],
        description="A branch you may not see answers not_found.",
        roles=READERS, scopes=["company:read"], params=[BRANCH_PARAM],
        response=s.BranchSerializer, response_example=BRANCH_EXAMPLE,
        errors=READ_ERRORS + ID_ERRORS,
    )
    def get(self, request, branch_id):
        return Response(s.BranchSerializer(_branch(request, branch_id)).data)

    @endpoint(
        id="branches-change", area=AREA, title="Change a branch",
        summary="New details for a branch.",
        what_it_does=["Changes only the fields sent.", "Records the change in the audit log."],
        description="To retire or reactivate a branch use its status endpoint. The default "
                    "branch stays the default until another one is made the default.",
        roles=ADMINS, scopes=["company:write"], params=[BRANCH_PARAM],
        request=s.BranchInputSerializer, response=s.BranchSerializer,
        request_example={"phone": "01711000009"},
        response_example={**BRANCH_EXAMPLE, "phone": "01711000009"},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def patch(self, request, branch_id):
        branch = _branch(request, branch_id)
        data = s.BranchInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        company = Company.objects.get(pk=request.company_id)
        values = form_data(BranchForm, branch, dict(data.validated_data), company=company)
        form = checked(BranchForm, values, instance=branch, company=company)
        with service_errors():
            update_branch(actor=request.user, company_id=request.company_id,
                          branch_id=branch.pk, values=form.cleaned_data)
        return Response(s.BranchSerializer(_branch(request, branch.pk)).data)


class BranchStatusView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:branch_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="branches-status", area=AREA, title="Retire or reactivate a branch",
        summary="Set a branch active or inactive.",
        what_it_does=["Changes the status; nothing is deleted.",
                      "Records it, with the reason, in the audit log."],
        description="A retired branch keeps its departments, employees and history. The "
                    "default branch cannot be retired - make another the default first.",
        roles=ADMINS, scopes=["company:write"], params=[BRANCH_PARAM],
        request=s.BranchStatusSerializer, response=s.BranchSerializer,
        request_example={"status": "inactive", "reason": "Office closed"},
        response_example={**BRANCH_EXAMPLE, "status": "inactive"},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def post(self, request, branch_id):
        branch = _branch(request, branch_id)
        data = s.BranchStatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            set_branch_status(actor=request.user, company_id=request.company_id,
                              branch_id=branch.pk, status=data.validated_data["status"],
                              reason=data.validated_data.get("reason", ""))
        return Response(s.BranchSerializer(_branch(request, branch.pk)).data)


# --- departments --------------------------------------------------------------------

DEPARTMENT_EXAMPLE = {"id": 8, "branch": {"id": 3, "name": "Chattogram"}, "code": "SW",
                      "name": "Software", "head": {"id": 41, "name": "Rahim Uddin"},
                      "description": "", "status": "active", "designation_count": 4,
                      "employee_count": 23}
DEPARTMENT_PARAM = _param("department_id", "The department id.", 8)
DEPARTMENT_NAMES = {"branch": "branch_id", "head": "head_employee_id"}


def _departments(request):
    return visible_adoptions(_membership(request)).annotate(
        designation_count=Count("designations", distinct=True),
        employee_count=Count("assignments", distinct=True))


def _department(request, department_id):
    department = _departments(request).filter(pk=department_id).first()
    if department is None:
        raise ApiError("not_found", "No such department in this company.")
    return department


def _department_form_kwargs(request):
    from employees.models import Employee

    membership = _membership(request)
    return {"branches": visible_branches(membership).filter(status=ActiveStatus.ACTIVE),
            "employees": Employee.objects.order_by("first_name", "last_name")}


def _department_out(department):
    data = s.DepartmentSerializer(department).data
    data["designations"] = s.TitleSerializer(department.designations.order_by("name"),
                                             many=True).data
    return data


class DepartmentListView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:adoption_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="departments-list", area=AREA, title="Departments",
        summary="The departments in the branches you may see.",
        what_it_does=["Lists them by branch, then name."],
        description="Filter by branch_id for one branch's departments (e.g. for a choice list).",
        roles=READERS, scopes=["company:read"], paginated=True,
        params=_filters(Param("branch_id", QUERY, "integer", "Only this branch.", example=3)),
        response=s.DepartmentSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [DEPARTMENT_EXAMPLE]},
        errors=READ_ERRORS + ["validation_error"],
    )
    def get(self, request):
        rows = _departments(request)
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query)
                               | Q(branch__name__icontains=query))
        status = request.query_params.get("status")
        if status in dict(ActiveStatus.choices):
            rows = rows.filter(status=status)
        branch_id = _number(request, "branch_id")
        if branch_id is not None:
            rows = rows.filter(branch_id=branch_id)
        return self.paginated(request, rows.order_by("branch__name", "name"),
                              s.DepartmentSerializer)

    @endpoint(
        id="departments-create", area=AREA, title="Add a department",
        summary="A new department in a branch, with designations if given.",
        what_it_does=["Creates the department in the branch.",
                      "Adds the designations sent with it."],
        description="The code and the name are unique in the branch. The branch cannot be "
                    "changed later: add a department with the same code to another branch "
                    "instead (or copy a whole branch's departments).",
        roles=ADMINS, scopes=["company:write"], response_status=201,
        request=s.DepartmentInputSerializer, response=s.DepartmentDetailSerializer,
        request_example={"branch_id": 3, "code": "SW", "name": "Software",
                         "head_employee_id": None,
                         "designations": [{"code": "SE", "name": "Software Engineer"}]},
        response_example={**DEPARTMENT_EXAMPLE, "head": None, "designation_count": 1,
                          "employee_count": 0, "designations": [
                              {"id": 31, "code": "SE", "name": "Software Engineer",
                               "status": "active"}]},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.DepartmentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        changes = {"status": ActiveStatus.ACTIVE, **data.validated_data}
        titles = changes.pop("designations", [])
        kwargs = _department_form_kwargs(request)
        form = checked(DepartmentAdoptionForm,
                       form_data(DepartmentAdoptionForm, None, changes, DEPARTMENT_NAMES, **kwargs),
                       DEPARTMENT_NAMES, **kwargs)
        with service_errors(DEPARTMENT_NAMES):
            department = adopt_department(actor=request.user, company_id=request.company_id,
                                          values={**form.cleaned_data, "designations": titles})
        return Response(_department_out(_department(request, department.pk)), status=201)


class DepartmentDetailView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:adoption_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="departments-get", area=AREA, title="One department",
        summary="One department, with its designations.",
        what_it_does=["Answers the department and every designation in it."],
        description="A department you may not see answers not_found.",
        roles=READERS, scopes=["company:read"], params=[DEPARTMENT_PARAM],
        response=s.DepartmentDetailSerializer,
        response_example={**DEPARTMENT_EXAMPLE, "designations": [
            {"id": 31, "code": "SE", "name": "Software Engineer", "status": "active"}]},
        errors=READ_ERRORS + ID_ERRORS,
    )
    def get(self, request, department_id):
        return Response(_department_out(_department(request, department_id)))

    @endpoint(
        id="departments-change", area=AREA, title="Change a department",
        summary="A new code, name, head, description or status.",
        what_it_does=["Changes only the fields sent.",
                      "A new head gets its own line in the audit log."],
        description="The branch is fixed (branch_id is ignored here). Designations are changed "
                    "through the designation endpoints.",
        roles=ADMINS, scopes=["company:write"], params=[DEPARTMENT_PARAM],
        request=s.DepartmentInputSerializer, response=s.DepartmentDetailSerializer,
        request_example={"head_employee_id": 41},
        response_example={**DEPARTMENT_EXAMPLE, "designations": []},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def patch(self, request, department_id):
        department = _department(request, department_id)
        data = s.DepartmentInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        changes = dict(data.validated_data)
        changes.pop("designations", None)
        kwargs = _department_form_kwargs(request)
        values = form_data(DepartmentAdoptionForm, department, changes, DEPARTMENT_NAMES, **kwargs)
        form = checked(DepartmentAdoptionForm, values, DEPARTMENT_NAMES, instance=department,
                       **kwargs)
        with service_errors(DEPARTMENT_NAMES):
            update_adoption(actor=request.user, company_id=request.company_id,
                            adoption_id=department.pk, values=form.cleaned_data)
        return Response(_department_out(_department(request, department.pk)))


class DepartmentStatusView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:adoption_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="departments-status", area=AREA, title="Deactivate or reactivate a department",
        summary="Set a department active or inactive.",
        what_it_does=["Changes the status; nothing is deleted."],
        description="A department cannot be deactivated while employees are placed in it - "
                    "move them first.",
        roles=ADMINS, scopes=["company:write"], params=[DEPARTMENT_PARAM],
        request=s.StatusSerializer, response=s.DepartmentDetailSerializer,
        request_example={"status": "inactive"},
        response_example={**DEPARTMENT_EXAMPLE, "status": "inactive", "designations": []},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def post(self, request, department_id):
        department = _department(request, department_id)
        data = s.StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            set_adoption_status(actor=request.user, company_id=request.company_id,
                                adoption_id=department.pk, status=data.validated_data["status"])
        return Response(_department_out(_department(request, department.pk)))


class DepartmentCopyView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:adoption_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="departments-copy", area=AREA, title="Copy departments between branches",
        summary="One branch's departments and designations, copied into another.",
        what_it_does=["Copies the active departments and their active designations.",
                      "Leaves alone the departments the target already has (same code)."],
        description="Heads are not copied: a head administers people at one branch. Safe to "
                    "repeat after adding a department to the source.",
        roles=ADMINS, scopes=["company:write"],
        request=s.CopySerializer, response=s.CopyResultSerializer,
        request_example={"source_branch_id": 1, "target_branch_id": 3},
        response_example={"created": [DEPARTMENT_EXAMPLE], "skipped": ["Accounts"]},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.CopySerializer(data=request.data)
        data.is_valid(raise_exception=True)
        names = {"source_branch": "source_branch_id", "target_branch": "target_branch_id"}
        branches = visible_branches(_membership(request))
        values = {"source_branch": data.validated_data["source_branch_id"],
                  "target_branch": data.validated_data["target_branch_id"]}
        form = checked(CopyAdoptionsForm, values, names, branches=branches)
        with service_errors(names):
            created, skipped = copy_adoptions_between_branches(
                actor=request.user, company_id=request.company_id,
                source_branch=form.cleaned_data["source_branch"],
                target_branch=form.cleaned_data["target_branch"])
        rows = _departments(request).filter(pk__in=[d.pk for d in created]).order_by("name")
        return Response({"created": s.DepartmentSerializer(rows, many=True).data,
                         "skipped": skipped})


# --- designations -------------------------------------------------------------------

DESIGNATION_EXAMPLE = {"id": 31, "department": {"id": 8, "name": "Software",
                                                "branch": {"id": 3, "name": "Chattogram"}},
                       "code": "SE", "name": "Software Engineer",
                       "parent": {"id": 30, "name": "Engineering Manager"},
                       "hierarchy_level": 1, "status": "active", "employee_count": 12}
DESIGNATION_PARAM = _param("designation_id", "The designation id.", 31)
DESIGNATION_NAMES = {"department": "department_id", "parent": "parent_id"}


def _designations(request):
    return visible_designations(_membership(request)).annotate(
        employee_count=Count("assignments", distinct=True))


def _designation(request, designation_id):
    designation = _designations(request).filter(pk=designation_id).first()
    if designation is None:
        raise ApiError("not_found", "No such designation in this company.")
    return designation


def _designation_form_kwargs(request):
    membership = _membership(request)
    return {"departments": visible_adoptions(membership).filter(status=ActiveStatus.ACTIVE),
            "designations": visible_designations(membership)}


class DesignationListView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:designation_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="designations-list", area=AREA, title="Designations",
        summary="The job titles in the departments you may see.",
        what_it_does=["Lists them by branch, department, then name."],
        description="Filter by department_id for one department's titles (e.g. for a choice "
                    "list), or by branch_id.",
        roles=READERS, scopes=["company:read"], paginated=True,
        params=_filters(Param("department_id", QUERY, "integer", "Only this department.",
                              example=8),
                        Param("branch_id", QUERY, "integer", "Only this branch.", example=3)),
        response=s.DesignationSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [DESIGNATION_EXAMPLE]},
        errors=READ_ERRORS + ["validation_error"],
    )
    def get(self, request):
        rows = _designations(request)
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(name__icontains=query) | Q(code__icontains=query))
        status = request.query_params.get("status")
        if status in dict(ActiveStatus.choices):
            rows = rows.filter(status=status)
        department_id = _number(request, "department_id")
        if department_id is not None:
            rows = rows.filter(department_id=department_id)
        branch_id = _number(request, "branch_id")
        if branch_id is not None:
            rows = rows.filter(department__branch_id=branch_id)
        return self.paginated(
            request, rows.order_by("department__branch__name", "department__name", "name"),
            s.DesignationSerializer)

    @endpoint(
        id="designations-create", area=AREA, title="Add a designation",
        summary="A new job title in a department.",
        what_it_does=["Creates the designation.", "Records it in the audit log."],
        description="The code is unique in the department. parent_id must be a title in the "
                    "same department. The department cannot be changed later.",
        roles=ADMINS, scopes=["company:write"], response_status=201,
        request=s.DesignationInputSerializer, response=s.DesignationSerializer,
        request_example={"department_id": 8, "code": "SE", "name": "Software Engineer",
                         "parent_id": 30},
        response_example={**DESIGNATION_EXAMPLE, "employee_count": 0},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.DesignationInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        changes = {"status": ActiveStatus.ACTIVE, **data.validated_data}
        kwargs = _designation_form_kwargs(request)
        form = checked(DesignationForm,
                       form_data(DesignationForm, None, changes, DESIGNATION_NAMES, **kwargs),
                       DESIGNATION_NAMES, **kwargs)
        with service_errors(DESIGNATION_NAMES):
            designation = create_designation(actor=request.user, company_id=request.company_id,
                                             values=form.cleaned_data)
        return Response(s.DesignationSerializer(_designation(request, designation.pk)).data,
                        status=201)


class DesignationDetailView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:designation_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="designations-get", area=AREA, title="One designation",
        summary="One job title's details.",
        what_it_does=["Answers the designation, its department and who it reports to."],
        description="A designation you may not see answers not_found.",
        roles=READERS, scopes=["company:read"], params=[DESIGNATION_PARAM],
        response=s.DesignationSerializer, response_example=DESIGNATION_EXAMPLE,
        errors=READ_ERRORS + ID_ERRORS,
    )
    def get(self, request, designation_id):
        return Response(s.DesignationSerializer(_designation(request, designation_id)).data)

    @endpoint(
        id="designations-change", area=AREA, title="Change a designation",
        summary="A new code, name, parent or status.",
        what_it_does=["Changes only the fields sent.", "Records it in the audit log."],
        description="The department is fixed (department_id is ignored here). A parent may "
                    "not create a loop.",
        roles=ADMINS, scopes=["company:write"], params=[DESIGNATION_PARAM],
        request=s.DesignationInputSerializer, response=s.DesignationSerializer,
        request_example={"name": "Senior Software Engineer"},
        response_example={**DESIGNATION_EXAMPLE, "name": "Senior Software Engineer"},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def patch(self, request, designation_id):
        designation = _designation(request, designation_id)
        data = s.DesignationInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        kwargs = _designation_form_kwargs(request)
        values = form_data(DesignationForm, designation, dict(data.validated_data),
                           DESIGNATION_NAMES, **kwargs)
        form = checked(DesignationForm, values, DESIGNATION_NAMES, instance=designation, **kwargs)
        with service_errors(DESIGNATION_NAMES):
            update_designation(actor=request.user, company_id=request.company_id,
                               designation_id=designation.pk, values=form.cleaned_data)
        return Response(s.DesignationSerializer(_designation(request, designation.pk)).data)


class DesignationStatusView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:designation_list"
    read_scope, write_scope = "company:read", "company:write"
    throttle_scope = "write"

    @endpoint(
        id="designations-status", area=AREA, title="Deactivate or reactivate a designation",
        summary="Set a designation active or inactive.",
        what_it_does=["Changes the status; nothing is deleted."],
        description="A designation cannot be deactivated while employees hold it - move them "
                    "first.",
        roles=ADMINS, scopes=["company:write"], params=[DESIGNATION_PARAM],
        request=s.StatusSerializer, response=s.DesignationSerializer,
        request_example={"status": "inactive"},
        response_example={**DESIGNATION_EXAMPLE, "status": "inactive", "employee_count": 0},
        errors=WRITE_ERRORS + ID_ERRORS,
    )
    def post(self, request, designation_id):
        designation = _designation(request, designation_id)
        data = s.StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            set_designation_status(actor=request.user, company_id=request.company_id,
                                   designation_id=designation.pk,
                                   status=data.validated_data["status"])
        return Response(s.DesignationSerializer(_designation(request, designation.pk)).data)
