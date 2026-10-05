"""Leave: types, policies and their versions, balances, recorded leave, and
the approval of what employees ask for (docs/api/00-PLAN.md phase 8; the
guide: docs/api/80-leave.md).

The panel's Leave pages and approval inbox, through the same gate, forms and
services (``leaves.services``, ``leaves.policy_admin``, ``leaves.policies``,
``leaves.workflow``): each service checks the role and the branches again.
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework.response import Response

from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.files import private_file, upload_from
from api.core.forms import checked, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.company.serializers import StatusSerializer
from api.v1.leave import serializers as s
from common.choices import ActiveStatus
from leaves import policies, policy_admin, services, workflow
from leaves.forms import (
    AmendLeaveForm, CancelLeaveForm, DecideLeaveForm, LeavePolicyForm, LeaveTypeForm,
    PolicyRuleForm, PolicyVersionForm, RecordLeaveForm,
)
from leaves.models import LeavePolicy, LeavePolicyVersion, LeaveRequest, LeaveType
from organization.services import require_company_membership

AREA = "leave"
ADMINS = ["Company owner or administrator"]
MEMBERS = ["Anyone in the company except an Employee or Branch-manager login"]
VIEWERS = ["The owner, company administrator or HR; anyone given View or Record leave in a "
           "branch (a branch manager in theirs); a department head for their department"]
RECORDERS = ["The owner, company administrator or HR; anyone given Record and cancel leave in "
             "a branch (a branch manager in theirs)"]
DECIDERS = ["The owner or company administrator (requests from branch managers, and from "
            "branches without one); a branch manager or anyone given Approve leave in their "
            "branches; a department head for their department"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required", "scope_missing",
          "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field"]
ONE = ["not_found"]
TYPE_PARAM = Param("leave_type_id", PATH, "integer", "The leave type id.", required=True,
                   example=1)
POLICY_PARAM = Param("policy_id", PATH, "integer", "The policy id.", required=True, example=2)
VERSION_PARAM = Param("version_id", PATH, "integer", "The version id.", required=True,
                      example=5)
LEAVE_PARAM = Param("leave_id", PATH, "integer", "The leave id.", required=True, example=31)
REQUEST_PARAM = Param("request_id", PATH, "integer", "The request id.", required=True,
                      example=31)


def _ref(row):
    return {"id": row.pk, "name": getattr(row, "full_name", None) or row.name} if row else None


class LeaveView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "leave:read", "leave:write"
    throttle_scope = "write"

    def page(self, request, rows, build, serializer):
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer([build(r) for r in chunk],
                                                           many=True).data)


# --- leave types --------------------------------------------------------------------------

def _type_out(leave_type):
    return {"id": leave_type.pk, "code": leave_type.code, "name": leave_type.name,
            "days_per_year": leave_type.days_per_year,
            "needs_document": leave_type.requires_attachment_by_default,
            "description": leave_type.description, "status": leave_type.status}


TYPE_EXAMPLE = {"id": 1, "code": "CL", "name": "Casual leave", "days_per_year": "10.00",
                "needs_document": False, "description": "", "status": "active"}
TYPE_NAMES = {"requires_attachment_by_default": "needs_document"}


def _leave_type(leave_type_id):
    leave_type = LeaveType.objects.filter(pk=leave_type_id).first()
    if leave_type is None:
        raise ApiError("not_found", "No such leave type in this company.")
    return leave_type


def _type_form_input(values):
    out = {"code": values.get("code", ""), "name": values.get("name", ""),
           "days_per_year": "" if values.get("days_per_year") is None
           else str(values["days_per_year"]),
           "description": values.get("description", "")}
    if values.get("needs_document"):
        out["requires_attachment_by_default"] = "on"
    return out


class LeaveTypeListView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "leaves:leave_type_list", "POST": "leaves:leave_type_create"}

    @endpoint(
        id="leave-types", area=AREA, title="Leave types",
        summary="Casual, sick, annual … with each one's days per year.",
        what_it_does=["Lists the company's leave types, active first."],
        description="Leave types are the company's: the same in every branch.",
        roles=MEMBERS, scopes=["leave:read"], paginated=True, response=s.LeaveTypeSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [TYPE_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        require_company_membership(request.user, request.company_id)
        rows = LeaveType.objects.order_by("status", "name")
        return self.page(request, rows, _type_out, s.LeaveTypeSerializer)

    @endpoint(
        id="leave-types-create", area=AREA, title="Add a leave type",
        summary="A new kind of leave.",
        what_it_does=["Adds it; records it in the audit log."],
        description="The code is unique in the company.",
        roles=ADMINS, scopes=["leave:write"], response_status=201,
        request=s.LeaveTypeInputSerializer, response=s.LeaveTypeSerializer,
        request_example={"code": "ML", "name": "Maternity leave", "days_per_year": "112",
                         "needs_document": True},
        response_example={**TYPE_EXAMPLE, "id": 4, "code": "ML", "name": "Maternity leave",
                          "days_per_year": "112.00", "needs_document": True},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        from organization.services import require_structure_manager

        require_structure_manager(request.user, request.company_id)
        data = s.LeaveTypeInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(LeaveTypeForm, _type_form_input(data.validated_data), TYPE_NAMES)
        with service_errors(TYPE_NAMES):
            leave_type = services.create_leave_type(actor=request.user,
                                                    company_id=request.company_id,
                                                    values=form.cleaned_data)
        return Response(s.LeaveTypeSerializer(_type_out(leave_type)).data, status=201)


class LeaveTypeDefaultsView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_type_defaults"

    @endpoint(
        id="leave-types-defaults", area=AREA, title="Add the default leave types",
        summary="Casual, sick, annual … - those the company does not have yet.",
        what_it_does=["Adds each default type missing; never changes one that exists."],
        description="Safe to call again: it adds nothing the second time.",
        roles=ADMINS, scopes=["leave:write"], response=s.DefaultsAddedSerializer,
        response_example={"added": [TYPE_EXAMPLE]}, errors=ERRORS,
    )
    def post(self, request):
        created = services.add_default_leave_types(actor=request.user,
                                                   company_id=request.company_id)
        return Response(s.DefaultsAddedSerializer({"added": [_type_out(t) for t in created]}).data)


class LeaveTypeView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "leaves:leave_type_list", "PATCH": "leaves:leave_type_edit"}

    @endpoint(
        id="leave-types-one", area=AREA, title="One leave type",
        summary="A leave type.", what_it_does=["Answers it."],
        description="Change it with PATCH; turn it off with POST …/status.",
        roles=MEMBERS, scopes=["leave:read"], params=[TYPE_PARAM],
        response=s.LeaveTypeSerializer, response_example=TYPE_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, leave_type_id):
        require_company_membership(request.user, request.company_id)
        return Response(s.LeaveTypeSerializer(_type_out(_leave_type(leave_type_id))).data)

    @endpoint(
        id="leave-types-change", area=AREA, title="Change a leave type",
        summary="Its code, name, days per year, description, or whether it needs a document.",
        what_it_does=["Changes the fields sent; records it in the audit log."],
        description="Leave already recorded keeps its days.",
        roles=ADMINS, scopes=["leave:write"], params=[TYPE_PARAM],
        request=s.LeaveTypeInputSerializer, response=s.LeaveTypeSerializer,
        request_example={"days_per_year": "12"},
        response_example={**TYPE_EXAMPLE, "days_per_year": "12.00"},
        errors=WRITE_ERRORS + ONE,
    )
    def patch(self, request, leave_type_id):
        _leave_type(leave_type_id)
        _m, leave_type = services.get_leave_type_for_edit(
            actor=request.user, company_id=request.company_id, leave_type_id=leave_type_id)
        data = s.LeaveTypeInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        merged = {**_type_out(leave_type), **data.validated_data}
        form = checked(LeaveTypeForm, _type_form_input(merged), TYPE_NAMES, instance=leave_type)
        with service_errors(TYPE_NAMES):
            leave_type = services.update_leave_type(
                actor=request.user, company_id=request.company_id, leave_type_id=leave_type.pk,
                values=form.cleaned_data)
        return Response(s.LeaveTypeSerializer(_type_out(leave_type)).data)


class LeaveTypeStatusView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_type_status"

    @endpoint(
        id="leave-types-status", area=AREA, title="Turn a leave type on or off",
        summary="An inactive type cannot be chosen for new leave.",
        what_it_does=["Sets the status; leave already recorded with it is unchanged."],
        description="status: active or inactive.", roles=ADMINS, scopes=["leave:write"], params=[TYPE_PARAM],
        request=StatusSerializer, response=s.LeaveTypeSerializer,
        request_example={"status": "inactive"},
        response_example={**TYPE_EXAMPLE, "status": "inactive"}, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, leave_type_id):
        _leave_type(leave_type_id)
        data = StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            leave_type = services.set_leave_type_status(
                actor=request.user, company_id=request.company_id, leave_type_id=leave_type_id,
                status=data.validated_data["status"])
        return Response(s.LeaveTypeSerializer(_type_out(leave_type)).data)


# --- policies -----------------------------------------------------------------------------

def _policy_out(policy):
    today = timezone.localdate()
    versions = policy.versions.prefetch_related("rules__leave_type").order_by("-effective_from")
    return {
        "id": policy.pk, "code": policy.code, "name": policy.name,
        "description": policy.description, "is_default": policy.is_default,
        "status": policy.status,
        "people": policy.employees.filter(effective_to__isnull=True).count(),
        "versions": [{
            "id": v.pk, "number": v.number, "effective_from": v.effective_from, "note": v.note,
            "started": v.effective_from <= today,
            "rules": [{
                "leave_type": _ref(rule.leave_type), "days_per_year": rule.days_per_year,
                "accrual": rule.accrual, "carry_forward_days": rule.carry_forward_days,
                "carry_forward_expires_months": rule.carry_forward_expires_months,
                "allow_half_day": rule.allow_half_day, "allow_hourly": rule.allow_hourly,
                "allow_negative": rule.allow_negative,
            } for rule in sorted(v.rules.all(), key=lambda r: r.leave_type.name)],
        } for v in versions],
    }


RULE_EXAMPLE = {"leave_type": {"id": 1, "name": "Casual leave"}, "days_per_year": "10.00",
                "accrual": "yearly", "carry_forward_days": None,
                "carry_forward_expires_months": None, "allow_half_day": True,
                "allow_hourly": True, "allow_negative": False}
POLICY_EXAMPLE = {"id": 2, "code": "STAFF", "name": "Staff", "description": "",
                  "is_default": True, "status": "active", "people": 12,
                  "versions": [{"id": 5, "number": 1, "effective_from": "2026-01-01", "note": "",
                                "started": True, "rules": [RULE_EXAMPLE]}]}


def _policy(policy_id):
    policy = LeavePolicy.objects.filter(pk=policy_id).first()
    if policy is None:
        raise ApiError("not_found", "No such leave policy in this company.")
    return policy


def _policy_form_input(values):
    out = {"code": values.get("code", ""), "name": values.get("name", ""),
           "description": values.get("description", "")}
    if values.get("is_default"):
        out["is_default"] = "on"
    return out


class PolicyListView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "leaves:leave_policy_list", "POST": "leaves:leave_policy_create"}

    @endpoint(
        id="leave-policies", area=AREA, title="Leave policies",
        summary="Groups of leave rules - days a year, earning, carry forward - by version.",
        what_it_does=["Lists the policies, the default first, each with its versions."],
        description="Without any policy, each leave type's days per year apply to everyone.",
        roles=ADMINS, scopes=["leave:read"], paginated=True, response=s.LeavePolicySerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [POLICY_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        policy_admin.require_policy_manager(request.user, request.company_id)
        rows = LeavePolicy.objects.order_by("-is_default", "status", "name")
        return self.page(request, rows, _policy_out, s.LeavePolicySerializer)

    @endpoint(
        id="leave-policies-create", area=AREA, title="Add a leave policy",
        summary="A new policy; give it its rules with a version.",
        what_it_does=["Adds the policy (no rules yet: POST …/versions)."],
        description="Making it the default gives it, from today, to everyone without a policy "
                    "of their own.",
        roles=ADMINS, scopes=["leave:write"], response_status=201,
        request=s.PolicyInputSerializer, response=s.LeavePolicySerializer,
        request_example={"code": "WORK", "name": "Workers"},
        response_example={**POLICY_EXAMPLE, "id": 3, "code": "WORK", "name": "Workers",
                          "is_default": False, "people": 0, "versions": []},
        errors=WRITE_ERRORS,
    )
    def post(self, request):
        data = s.PolicyInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(LeavePolicyForm, _policy_form_input(data.validated_data))
        with service_errors():
            policy = policy_admin.save_policy(actor=request.user, company_id=request.company_id,
                                              values=form.cleaned_data)
        return Response(s.LeavePolicySerializer(_policy_out(policy)).data, status=201)


class PolicyView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "leaves:leave_policy_detail", "PATCH": "leaves:leave_policy_edit"}

    @endpoint(
        id="leave-policies-one", area=AREA, title="One leave policy",
        summary="A policy with every version and its rules.", what_it_does=["Answers it."],
        description="started: a version that has begun can no longer change.", roles=ADMINS, scopes=["leave:read"], params=[POLICY_PARAM],
        response=s.LeavePolicySerializer, response_example=POLICY_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, policy_id):
        policy_admin.require_policy_manager(request.user, request.company_id)
        return Response(s.LeavePolicySerializer(_policy_out(_policy(policy_id))).data)

    @endpoint(
        id="leave-policies-change", area=AREA, title="Change a leave policy",
        summary="Its code, name, description, or make it the default.",
        what_it_does=["Changes the fields sent; records it in the audit log."],
        description="The rules change with versions, not here.",
        roles=ADMINS, scopes=["leave:write"], params=[POLICY_PARAM],
        request=s.PolicyInputSerializer, response=s.LeavePolicySerializer,
        request_example={"name": "Office staff"},
        response_example={**POLICY_EXAMPLE, "name": "Office staff"},
        errors=WRITE_ERRORS + ONE,
    )
    def patch(self, request, policy_id):
        policy_admin.require_policy_manager(request.user, request.company_id)
        policy = _policy(policy_id)
        data = s.PolicyInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        merged = {"code": policy.code, "name": policy.name, "description": policy.description,
                  "is_default": policy.is_default, **data.validated_data}
        form = checked(LeavePolicyForm, _policy_form_input(merged))
        with service_errors():
            policy = policy_admin.save_policy(actor=request.user, company_id=request.company_id,
                                              policy_id=policy.pk, values=form.cleaned_data)
        return Response(s.LeavePolicySerializer(_policy_out(policy)).data)


class PolicyStatusView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_policy_status"

    @endpoint(
        id="leave-policies-status", area=AREA, title="Turn a leave policy on or off",
        summary="An inactive policy cannot be given to anyone new.",
        what_it_does=["Sets the status."],
        description="The company default cannot be turned off: make another the default first.",
        roles=ADMINS, scopes=["leave:write"], params=[POLICY_PARAM],
        request=StatusSerializer, response=s.LeavePolicySerializer,
        request_example={"status": "inactive"},
        response_example={**POLICY_EXAMPLE, "is_default": False, "status": "inactive"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, policy_id):
        policy_admin.require_policy_manager(request.user, request.company_id)
        _policy(policy_id)
        data = StatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            policy = policy_admin.set_policy_status(
                actor=request.user, company_id=request.company_id, policy_id=policy_id,
                status=data.validated_data["status"])
        return Response(s.LeavePolicySerializer(_policy_out(policy)).data)


def _version_input(request):
    """``(effective_from, note, rules)`` checked by the panel's version page forms."""
    data = s.PolicyVersionInputSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    form = checked(PolicyVersionForm, {"effective_from": values["effective_from"].isoformat(),
                                       "note": values.get("note", "")})
    types = {t.pk: t for t in policy_admin.active_leave_types()}
    rules, problems = {}, {}
    for index, rule in enumerate(values["rules"]):
        leave_type = types.get(rule["leave_type_id"])
        if leave_type is None:
            problems[f"rules[{index}].leave_type_id"] = ["Not an active leave type."]
            continue
        if leave_type in rules:
            problems[f"rules[{index}].leave_type_id"] = ["Given twice."]
            continue
        rule_input = {"include": "on", "accrual": rule["accrual"],
                      "days_per_year": str(rule["days_per_year"]),
                      "carry_forward_days": "" if rule.get("carry_forward_days") is None
                      else str(rule["carry_forward_days"]),
                      "carry_forward_expires_months": rule.get("carry_forward_expires_months")
                      or ""}
        for flag in ("allow_half_day", "allow_hourly", "allow_negative"):
            if rule[flag]:
                rule_input[flag] = "on"
        rule_form = PolicyRuleForm(rule_input)
        if not rule_form.is_valid():
            for name, messages in rule_form.errors.items():
                problems[f"rules[{index}].{name}"] = [str(m) for m in messages]
            continue
        rules[leave_type] = {name: rule_form.cleaned_data.get(name)
                             for name in policy_admin.RULE_FIELDS}
    if problems:
        refuse(problems)
    return form.cleaned_data["effective_from"], form.cleaned_data["note"], rules


VERSION_REQUEST = {"effective_from": "2027-01-01", "note": "Casual leave up to 12",
                   "rules": [{"leave_type_id": 1, "days_per_year": "12", "accrual": "yearly"}]}


class PolicyVersionsView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_policy_version_create"

    @endpoint(
        id="leave-policies-versions-add", area=AREA, title="Add a version of the rules",
        summary="New rules for a policy, from a date.",
        what_it_does=["Adds the version; balances follow it from its date."],
        description=("The first version may start in the past (e.g. 1 January); a later one "
                     "starts today or later - what was given before stays. One rule per "
                     "leave type it covers."),
        roles=ADMINS, scopes=["leave:write"], params=[POLICY_PARAM], response_status=201,
        request=s.PolicyVersionInputSerializer, response=s.LeavePolicySerializer,
        request_example=VERSION_REQUEST, response_example=POLICY_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, policy_id):
        policy_admin.require_policy_manager(request.user, request.company_id)
        policy = _policy(policy_id)
        effective_from, note, rules = _version_input(request)
        with service_errors():
            policy_admin.add_version(actor=request.user, company_id=request.company_id,
                                     policy_id=policy.pk, effective_from=effective_from,
                                     note=note, rules=rules)
        return Response(s.LeavePolicySerializer(_policy_out(policy)).data, status=201)


class PolicyVersionView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"PUT": "leaves:leave_policy_version_edit",
                  "DELETE": "leaves:leave_policy_version_delete"}

    def _version(self, request, policy_id, version_id):
        policy_admin.require_policy_manager(request.user, request.company_id)
        version = LeavePolicyVersion.objects.filter(pk=version_id, policy_id=policy_id).first()
        if version is None:
            raise ApiError("not_found", "No such version of this policy.")
        return version

    @endpoint(
        id="leave-policies-versions-change", area=AREA, title="Replace a version's rules",
        summary="Change a version that has not started yet: its date, note and every rule.",
        what_it_does=["Replaces the version as sent."],
        description="A version that has started stays as it is: add a new one instead.",
        roles=ADMINS, scopes=["leave:write"], params=[POLICY_PARAM, VERSION_PARAM],
        request=s.PolicyVersionInputSerializer, response=s.LeavePolicySerializer,
        request_example=VERSION_REQUEST, response_example=POLICY_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def put(self, request, policy_id, version_id):
        version = self._version(request, policy_id, version_id)
        effective_from, note, rules = _version_input(request)
        with service_errors():
            policy_admin.edit_version(actor=request.user, company_id=request.company_id,
                                      version_id=version.pk, effective_from=effective_from,
                                      note=note, rules=rules)
        return Response(s.LeavePolicySerializer(_policy_out(_policy(policy_id))).data)

    @endpoint(
        id="leave-policies-versions-delete", area=AREA, title="Remove a version",
        summary="Remove a version that has not started yet.",
        what_it_does=["Removes it; the version before it carries on."],
        description="A version that has started cannot be removed.",
        roles=ADMINS, scopes=["leave:write"], params=[POLICY_PARAM, VERSION_PARAM],
        response=s.LeavePolicySerializer, response_example=POLICY_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def delete(self, request, policy_id, version_id):
        version = self._version(request, policy_id, version_id)
        with service_errors():
            policy_admin.delete_version(actor=request.user, company_id=request.company_id,
                                        version_id=version.pk)
        return Response(s.LeavePolicySerializer(_policy_out(_policy(policy_id))).data)


# --- balances -----------------------------------------------------------------------------

class BalanceListView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_balance_list"

    @endpoint(
        id="leave-balances", area=AREA, title="Leave balances",
        summary="Each person's days given, taken and left of each leave type in a year.",
        what_it_does=["Lists the people you see leave for, each with their balances."],
        description=("By their leave policy, or - for a type no policy covers - the type's "
                     "days per year. Adjust a balance with POST /employees/{id}/"
                     "leave-adjustments."),
        roles=VIEWERS, scopes=["leave:read"], paginated=True,
        params=[Param("year", QUERY, "integer", "The year (this year by default).",
                      example=2026),
                Param("employee_id", QUERY, "integer", "Only this person.", example=41)],
        response=s.PersonBalanceSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "employee": {"id": 41, "name": "Rahim Uddin"}, "employee_code": "E-0041",
            "branch": "Chattogram", "year": 2026, "balances": [{
                "leave_type": {"id": 1, "name": "Casual leave"}, "by_policy": True,
                "policy": "Staff", "given": "10.00", "taken": "2.50", "left": "7.50"}]}]},
        errors=ERRORS,
    )
    def get(self, request):
        from leaves.views import _list_scope
        from organization.access_services import people

        _wide, scope, _record = _list_scope(request.user, request.company_id)
        today = timezone.localdate()
        raw = request.query_params.get("year", "")
        year = int(raw) if raw.isdigit() and 2000 <= int(raw) <= today.year + 1 else today.year
        rows = people(scope).exclude(employment_status__in=["resigned", "terminated", "retired"])
        raw_employee = request.query_params.get("employee_id", "")
        if raw_employee.isdigit():
            rows = rows.filter(pk=int(raw_employee))
        has_policies = LeavePolicy.objects.exists()

        def build(employee):
            if has_policies:
                found = policies.overview(employee, year, today)
            else:
                from leaves.policy_views import _allowances

                found = next(_allowances([employee], year))[1]
            return {"employee": _ref(employee), "employee_code": employee.table_code,
                    "branch": employee.table_branch, "year": year, "balances": [{
                        "leave_type": _ref(row["leave_type"]), "by_policy": row["by_policy"],
                        "policy": (row["policy"].name if row["policy"] else None)
                        if row["by_policy"] else None,
                        "given": row["given"], "taken": row["taken"], "left": row["left"],
                    } for row in found]}
        return self.page(request, rows.order_by("first_name", "last_name", "pk"), build,
                         s.PersonBalanceSerializer)


# --- recorded leave -----------------------------------------------------------------------

def _current_segment(leave):
    from leaves.views import _current_segment as current

    return current(leave)


def _time(value):
    return value.strftime("%H:%M") if value else ""


def _record_out(leave, detail=False, actor=None, company_id=None):
    segment = _current_segment(leave)
    units = getattr(leave, "table_units", segment.requested_units)
    out = {
        "id": leave.pk, "employee": _ref(leave.employee), "leave_type": _ref(segment.leave_type),
        "start_date": segment.start_date, "end_date": segment.end_date,
        "duration": segment.duration_type or "full_day",
        "half_day_part": segment.half_day_part or "", "start_time": _time(segment.start_time),
        "end_time": _time(segment.end_time), "days": units,
        "pay_type": segment.requested_pay_type,
        "pay_percentage": segment.requested_pay_percentage
        if segment.requested_pay_type == "partial" else None,
        "status": leave.status, "reason": leave.reason, "has_document": bool(leave.attachment),
        "recorded_by": leave.submitted_by.email if leave.submitted_by_id else None,
        "recorded_at": leave.submitted_at,
    }
    if detail:
        out["days_counting"] = [day.work_date for day in services.live_days(leave)]
        out["may_change"] = _may_change(actor, company_id, leave)
    return out


def _may_change(actor, company_id, leave):
    if leave.status not in (LeaveRequest.Status.APPROVED,
                            LeaveRequest.Status.PARTIALLY_CANCELLED):
        return False
    if leave.employee.user_id and leave.employee.user_id == actor.pk:
        return False
    try:
        services.get_leave_for_edit(actor=actor, company_id=company_id, request_id=leave.pk)
    except PermissionDenied:
        return False
    return True


RECORD_EXAMPLE = {"id": 31, "employee": {"id": 41, "name": "Rahim Uddin"},
                  "leave_type": {"id": 1, "name": "Casual leave"}, "start_date": "2026-10-12",
                  "end_date": "2026-10-13", "duration": "full_day", "half_day_part": "",
                  "start_time": "", "end_time": "", "days": "2.00", "pay_type": "paid",
                  "pay_percentage": None, "status": "approved", "reason": "Family wedding",
                  "has_document": False, "recorded_by": "hr@example.com",
                  "recorded_at": "2026-10-05T10:15:00+06:00"}
DETAIL_EXAMPLE = {**RECORD_EXAMPLE, "days_counting": ["2026-10-12", "2026-10-13"],
                  "may_change": True}
RECORD_NAMES = {"employee": "employee_id", "leave_type": "leave_type_id"}


def _visible_leave(request, leave_id):
    """The leave, if it is on a Leave list this login sees."""
    from leaves.views import _list_scope, leave_rows

    _wide, scope, _record = _list_scope(request.user, request.company_id)
    leave = leave_rows(scope, datetime.date.min, datetime.date.max).filter(pk=leave_id).first()
    if leave is None:
        if LeaveRequest.objects.filter(pk=leave_id).exists():
            raise PermissionDenied("This leave is outside the branches you see.")
        raise ApiError("not_found", "No such leave in this company.")
    return leave


def _leave_form_input(values):
    """The panel form's fields from the API's."""
    out = {"leave_type": values.get("leave_type_id", ""),
           "start_date": values["start_date"].isoformat() if values.get("start_date") else "",
           "end_date": values["end_date"].isoformat() if values.get("end_date") else "",
           "duration": values.get("duration") or "full_day",
           "half_day_part": values.get("half_day_part") or "",
           "start_time": values.get("start_time") or "", "end_time": values.get("end_time") or "",
           "pay_type": values.get("pay_type") or "paid",
           "pay_percentage": "" if values.get("pay_percentage") is None
           else str(values["pay_percentage"]),
           "reason": values.get("reason") or ""}
    if "employee_id" in values:
        out["employee"] = values["employee_id"]
    return out


def _files(values):
    document = values.get("document")
    if not document:
        return {}
    return {"document": upload_from(document["filename"], document["content_base64"],
                                    field="document")}


def _active_types():
    return LeaveType.objects.filter(status=ActiveStatus.ACTIVE).order_by("name")


def _without_document(data):
    return {key: value for key, value in data.items() if key != "document"}


class RecordListView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "leaves:leave_list", "POST": "leaves:leave_record"}

    @endpoint(
        id="leave-records", area=AREA, title="Leave",
        summary="The leave of a month (or a range): recorded, approved, cancelled, asked for.",
        what_it_does=["Lists the leave touching the dates, newest first, in the branches you "
                      "see."],
        description=("A changed leave shows its current part; days is what still counts. "
                     "status: approved, partially_cancelled, cancelled - and pending, rejected "
                     "or withdrawn for what employees asked for."),
        roles=VIEWERS, scopes=["leave:read"], paginated=True,
        params=[Param("year", QUERY, "integer", "The year (this year by default).", example=2026),
                Param("month", QUERY, "integer", "The month, 1-12 (this month by default).",
                      example=10),
                Param("from", QUERY, "string (date)", "From this day (with to), instead of a "
                                                      "month.", example="2026-10-01"),
                Param("to", QUERY, "string (date)", "To this day.", example="2026-12-31"),
                Param("employee_id", QUERY, "integer", "Only this person.", example=41),
                Param("status", QUERY, "string", "Only this status.", example="approved")],
        response=s.LeaveRecordSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [RECORD_EXAMPLE]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        from attendance.views import read_month
        from leaves.views import _list_scope, leave_rows

        _wide, scope, _record = _list_scope(request.user, request.company_id)
        q = request.query_params
        if q.get("from") or q.get("to"):
            try:
                first = datetime.date.fromisoformat(q.get("from", ""))
                last = datetime.date.fromisoformat(q.get("to", ""))
            except ValueError:
                refuse({"from": ["Give from and to as YYYY-MM-DD."]})
            if last < first or (last - first).days > 366:
                refuse({"to": ["From 1 to 366 days after from."]})
        else:
            year, month = read_month(q)
            first = datetime.date(year, month, 1)
            last = (first.replace(day=28) + datetime.timedelta(days=4)).replace(day=1) \
                - datetime.timedelta(days=1)
        rows = leave_rows(scope, first, last)
        if q.get("employee_id", "").isdigit():
            rows = rows.filter(employee_id=int(q["employee_id"]))
        if q.get("status") in dict(LeaveRequest.Status.choices):
            rows = rows.filter(status=q["status"])
        return self.page(request, rows.order_by("-first_day", "-pk"), _record_out,
                         s.LeaveRecordSerializer)

    @endpoint(
        id="leave-records-create", area=AREA, title="Record leave",
        summary="Leave already agreed: full days, a half day or some hours; paid or not.",
        what_it_does=["Records it as approved; its days stop counting as absent and "
                      "attendance is worked out again.",
                      "Checks clashes with other leave, the yearly allowance (or policy "
                      "balance), the shift, and finalised salary months."],
        description=("This is also Record leave on someone's profile. Weekly offs and "
                     "holidays inside the dates are not counted. A type that needs a document "
                     "is refused without one. Not for yourself."),
        roles=RECORDERS, scopes=["leave:write"], response_status=201,
        request=s.RecordInputSerializer, response=s.LeaveRecordDetailSerializer,
        request_example={"employee_id": 41, "leave_type_id": 1, "start_date": "2026-10-12",
                         "end_date": "2026-10-13", "pay_type": "paid",
                         "reason": "Family wedding"},
        response_example=DETAIL_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def post(self, request):
        from leaves.views import recordable_employees

        _m, branches = services.recorder_branches(request.user, request.company_id)
        data = s.RecordInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form = checked(RecordLeaveForm, _leave_form_input(values), RECORD_NAMES,
                       files=_files(values), employees=recordable_employees(branches),
                       leave_types=_active_types())
        with service_errors(RECORD_NAMES):
            leave = services.record_leave(actor=request.user, company_id=request.company_id,
                                          values=_without_document(form.cleaned_data),
                                          document=form.cleaned_data.get("document"))
        leave = _visible_leave(request, leave.pk)
        return Response(s.LeaveRecordDetailSerializer(
            _record_out(leave, True, request.user, request.company_id)).data, status=201)


class RecordView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_list"

    @endpoint(
        id="leave-records-one", area=AREA, title="One leave",
        summary="A leave with the days that still count, and whether you may change it.",
        what_it_does=["Answers it."],
        description="Change it with POST …/amend, cancel it with POST …/cancel.",
        roles=VIEWERS, scopes=["leave:read"], params=[LEAVE_PARAM],
        response=s.LeaveRecordDetailSerializer, response_example=DETAIL_EXAMPLE,
        errors=ERRORS + ONE,
    )
    def get(self, request, leave_id):
        leave = _visible_leave(request, leave_id)
        return Response(s.LeaveRecordDetailSerializer(
            _record_out(leave, True, request.user, request.company_id)).data)


def _editable(request, leave_id):
    if not LeaveRequest.objects.filter(pk=leave_id).exists():
        raise ApiError("not_found", "No such leave in this company.")
    return services.get_leave_for_edit(actor=request.user, company_id=request.company_id,
                                       request_id=leave_id)[1]


class AmendView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_amend"

    @endpoint(
        id="leave-records-amend", area=AREA, title="Change a leave",
        summary="Its type, dates, half or full day, hours, or pay.",
        what_it_does=["The old days stop counting and the new ones are recorded on the same "
                      "leave, with every check recording makes."],
        description="Send only what changes. A new document replaces the old one. Approved "
                    "leave only; not your own.",
        roles=RECORDERS, scopes=["leave:write"], params=[LEAVE_PARAM],
        request=s.AmendInputSerializer, response=s.LeaveRecordDetailSerializer,
        request_example={"end_date": "2026-10-14", "reason": "One more day agreed"},
        response_example={**DETAIL_EXAMPLE, "end_date": "2026-10-14", "days": "3.00"},
        errors=WRITE_ERRORS + ONE + ["payload_too_large"],
    )
    def post(self, request, leave_id):
        leave = _editable(request, leave_id)
        data = s.AmendInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        segment = _current_segment(leave)
        current = {"leave_type_id": segment.leave_type_id, "start_date": segment.start_date,
                   "end_date": segment.end_date, "duration": segment.duration_type or "full_day",
                   "half_day_part": segment.half_day_part or "",
                   "start_time": _time(segment.start_time), "end_time": _time(segment.end_time),
                   "pay_type": segment.requested_pay_type,
                   "pay_percentage": segment.requested_pay_percentage
                   if segment.requested_pay_type == "partial" else None,
                   "reason": leave.reason}
        values = {**current, **data.validated_data}
        form = checked(AmendLeaveForm, _leave_form_input(values), RECORD_NAMES,
                       files=_files(values), employees=None, leave_types=_active_types())
        with service_errors(RECORD_NAMES):
            services.amend_leave(actor=request.user, company_id=request.company_id,
                                 request_id=leave.pk,
                                 values=_without_document(form.cleaned_data),
                                 document=form.cleaned_data.get("document"))
        leave = _visible_leave(request, leave.pk)
        return Response(s.LeaveRecordDetailSerializer(
            _record_out(leave, True, request.user, request.company_id)).data)


class CancelView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "leaves:leave_cancel"

    @endpoint(
        id="leave-records-cancel", area=AREA, title="Cancel a leave",
        summary="The whole leave, or some of its days (someone came back early).",
        what_it_does=["Those days stop counting at once and attendance is worked out again "
                      "for them; nothing is deleted."],
        description="A reason is needed when cancelling some days. Not in a finalised salary "
                    "month; not your own.",
        roles=RECORDERS, scopes=["leave:write"], params=[LEAVE_PARAM],
        request=s.CancelInputSerializer, response=s.LeaveRecordDetailSerializer,
        request_example={"days": ["2026-10-13"], "reason": "Came back a day early"},
        response_example={**DETAIL_EXAMPLE, "status": "partially_cancelled", "days": "1.00",
                          "days_counting": ["2026-10-12"]},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, leave_id):
        leave = _editable(request, leave_id)
        data = s.CancelInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        days = [day.work_date for day in services.live_days(leave)]
        form_input = {"reason": values.get("reason", "")}
        if values.get("days"):
            form_input.update(what="some", work_dates=[d.isoformat() for d in values["days"]])
        form = checked(CancelLeaveForm, form_input, {"work_dates": "days"}, days=days)
        with service_errors({"work_dates": "days"}):
            if form.cleaned_data.get("what") == "some":
                services.cancel_days(actor=request.user, company_id=request.company_id,
                                     request_id=leave.pk,
                                     work_dates=form.cleaned_data["work_dates"],
                                     reason=form.cleaned_data.get("reason", ""))
            else:
                services.cancel_leave(actor=request.user, company_id=request.company_id,
                                      request_id=leave.pk,
                                      reason=form.cleaned_data.get("reason", ""))
        leave = _visible_leave(request, leave.pk)
        return Response(s.LeaveRecordDetailSerializer(
            _record_out(leave, True, request.user, request.company_id)).data)


class DocumentView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_document"

    @endpoint(
        id="leave-records-document", area=AREA, title="A leave's document",
        summary="The certificate or letter attached to a leave or a request.",
        what_it_does=["Answers the file itself (PDF or picture), privately."],
        description="For the employee, whoever sees that leave, and whoever may decide it.",
        roles=["The employee; whoever sees the leave or may decide it"], scopes=["leave:read"],
        params=[LEAVE_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, leave_id):
        from leaves.documents import may_open

        leave = (LeaveRequest.objects.select_related("employee", "submission_assignment")
                 .filter(pk=leave_id).first())
        if leave is None:
            raise ApiError("not_found", "No such leave in this company.")
        if not may_open(request.user, request.company_id, leave):
            raise PermissionDenied("This document is not yours to open.")
        if not leave.attachment:
            raise ApiError("not_found", "This leave has no document.")
        return private_file(leave.attachment, leave.attachment_name)


# --- requests to decide -------------------------------------------------------------------

def _request_out(leave):
    segment = leave.segments.select_related("leave_type").filter(status="active").first() \
        or _current_segment(leave)
    allowance = None
    if leave.status == "pending":
        found = policies.left_for(leave.employee, segment.leave_type, segment.start_date.year)
        allowance = {"left": found[0], "given": found[1]} if found else None
    placed = leave.submission_assignment
    return {
        "id": leave.pk, "employee": _ref(leave.employee),
        "branch": _ref(placed.branch) if placed else None,
        "leave_type": _ref(segment.leave_type), "start_date": segment.start_date,
        "end_date": segment.end_date, "duration": segment.duration_type or "full_day",
        "half_day_part": segment.half_day_part or "", "start_time": _time(segment.start_time),
        "end_time": _time(segment.end_time), "days": segment.requested_units,
        "pay_type": segment.requested_pay_type, "reason": leave.reason, "status": leave.status,
        "has_document": bool(leave.attachment), "submitted_at": leave.submitted_at,
        "decided_at": leave.decided_at,
        "decision_note": (leave.decision_snapshot or {}).get("reason", "") or "",
        "allowance": allowance,
    }


REQUEST_EXAMPLE = {"id": 31, "employee": {"id": 41, "name": "Rahim Uddin"},
                   "branch": {"id": 3, "name": "Chattogram"},
                   "leave_type": {"id": 1, "name": "Casual leave"}, "start_date": "2026-10-12",
                   "end_date": "2026-10-12", "duration": "half_day", "half_day_part": "morning",
                   "start_time": "", "end_time": "", "days": "0.50", "pay_type": "paid",
                   "reason": "Doctor's appointment", "status": "pending", "has_document": False,
                   "submitted_at": "2026-10-05T09:00:00+06:00", "decided_at": None,
                   "decision_note": "", "allowance": {"left": "7.50", "given": "10.00"}}


def _reviewable(request):
    member = workflow.reviewer(request.user, request.company_id)
    return workflow.reviewable(member).select_related(
        "employee", "submission_assignment__branch")


class RequestListView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_inbox"

    @endpoint(
        id="leave-requests", area=AREA, title="Leave requests to decide",
        summary="What employees asked for, from the people whose leave you decide.",
        what_it_does=["Lists the requests (waiting by default), newest first. Never your "
                      "own."],
        description=("Each waiting request shows the person's allowance of that type this "
                     "year. Employees ask from their app (the My account area)."),
        roles=DECIDERS, scopes=["leave:read"], paginated=True,
        params=[Param("status", QUERY, "string", "pending (default), approved or rejected.",
                      example="pending")],
        response=s.LeaveRequestSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [REQUEST_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        status = request.query_params.get("status", "pending")
        if status not in ("pending", "approved", "rejected"):
            status = "pending"
        rows = _reviewable(request).filter(status=status).order_by("-submitted_at", "-pk")
        return self.page(request, rows, _request_out, s.LeaveRequestSerializer)


class RequestView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_decide"

    @endpoint(
        id="leave-requests-one", area=AREA, title="One leave request",
        summary="A request you may decide, with their allowance.", what_it_does=["Answers it."],
        description="Decide it with POST …/decide.", roles=DECIDERS, scopes=["leave:read"], params=[REQUEST_PARAM],
        response=s.LeaveRequestSerializer, response_example=REQUEST_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, request_id):
        leave = _reviewable(request).filter(pk=request_id).first()
        if leave is None:
            raise ApiError("not_found", "No such request among those you may decide.")
        return Response(s.LeaveRequestSerializer(_request_out(leave)).data)


class DecideView(LeaveView):
    permission_classes = [PanelRule]
    panel_page = "me:leave_decide"

    @endpoint(
        id="leave-requests-decide", area=AREA, title="Approve or reject a request",
        summary="Approve it (with the pay you give) or reject it (with a note).",
        what_it_does=["An approval records the leave days and works out attendance again; "
                      "the allowance and clashes are checked again first."],
        description="The employee can read the note. Not your own request.",
        roles=DECIDERS, scopes=["leave:write"], params=[REQUEST_PARAM],
        request=s.DecideInputSerializer, response=s.LeaveRequestSerializer,
        request_example={"decision": "approve", "pay_type": "paid"},
        response_example={**REQUEST_EXAMPLE, "status": "approved", "allowance": None,
                          "decided_at": "2026-10-05T11:00:00+06:00"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, request_id):
        if _reviewable(request).filter(pk=request_id).first() is None:
            raise ApiError("not_found", "No such request among those you may decide.")
        data = s.DecideInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"reason": "note"}
        form = checked(DecideLeaveForm, {
            "decision": values["decision"], "pay_type": values.get("pay_type") or "",
            "pay_percentage": "" if values.get("pay_percentage") is None
            else str(values["pay_percentage"]),
            "reason": values.get("note", "")}, names)
        cleaned = form.cleaned_data
        with service_errors(names):
            workflow.decide_request(actor=request.user, company_id=request.company_id,
                                    request_id=request_id,
                                    approve=cleaned["decision"] == "approve",
                                    pay_type=cleaned["pay_type"], reason=cleaned["reason"],
                                    pay_percentage=cleaned.get("pay_percentage"))
        leave = _reviewable(request).filter(pk=request_id).first() or (
            LeaveRequest.objects.select_related("employee", "submission_assignment__branch")
            .get(pk=request_id))
        return Response(s.LeaveRequestSerializer(_request_out(leave)).data)
