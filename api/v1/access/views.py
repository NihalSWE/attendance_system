"""Organisation → Access: who has which permission, in which branch
(docs/api/20-company-and-branches.md). Built on the panel's own
``organization.access_services`` - every rule (who may give what, where, never
to yourself) stays there."""

from django.db.models import Q
from rest_framework.response import Response

from access_control import branch_access as access
from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.access import serializers as s
from employees.models import Employee
from organization import access_services as services
from organization import employee_login

AREA = "company"
ROLES = ["Whoever may give access: the owner and company administrator (every branch), "
         "a branch manager (their branches)"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required",
          "permission_denied", "rate_limited", "server_error"]
PERSON_PARAM = Param("employee_id", PATH, "integer", "The employee id.", required=True, example=41)
GRID_EXAMPLE = {
    "employee": {"id": 41, "name": "Rahim Uddin"}, "login_label": "Employee", "role_note": "",
    "is_self": False, "everything": False,
    "branches": [{"id": 1, "name": "Head Office"}, {"id": 3, "name": "Chattogram"}],
    "permissions": [{"code": "employees.view", "label": "View employees", "branches": [
        {"branch_id": 1, "granted": True, "editable": True},
        {"branch_id": 3, "granted": False, "editable": True}]}],
}


def _grid(page):
    return {
        "employee": {"id": page["employee"].pk, "name": page["employee"].full_name},
        "login_label": page["login_label"], "role_note": page["role_note"],
        "is_self": page["is_self"], "everything": page["everything"],
        "branches": [{"id": b.pk, "name": b.name} for b in page["branches"]],
        "permissions": [
            {"code": row["code"], "label": row["label"],
             "branches": [{"branch_id": cell["branch"].pk, "granted": cell["checked"],
                           "editable": cell["editable"]} for cell in row["cells"]]}
            for row in page["rows"]
        ],
    }


def _person(request, employee_id):
    if not Employee.objects.filter(pk=employee_id).exists():
        raise ApiError("not_found", "No such employee in this company.")
    return services.person(request.user, request.company_id, employee_id)


class PermissionListView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:access"
    read_scope = write_scope = None
    throttle_scope = "read"

    @endpoint(
        id="access-permissions", area=AREA, title="Access: the permissions",
        summary="Every permission that can be given by branch.",
        what_it_does=["Lists the permissions, in the order of the panel's grid."],
        description="Owners and company administrators have all of them everywhere; a branch "
                    "manager has all of them in their branches; HR has some company-wide. The "
                    "rest is given to people by hand, branch by branch.",
        roles=ROLES, paginated=True, response=s.PermissionSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [
            {"code": "employees.view", "label": "View employees"}]},
        errors=ERRORS,
    )
    def get(self, request):
        services.grant_branches(request.user, request.company_id)
        rows = [{"code": code, "label": label} for code, label, *_ in access.BRANCH_PERMISSIONS]
        return self.paginated(request, rows, s.PermissionSerializer)


class PeopleView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:access"
    read_scope = write_scope = None
    throttle_scope = "read"

    @endpoint(
        id="access-people", area=AREA, title="Access: people",
        summary="The people in the branches where you may give access, with what they hold.",
        what_it_does=["Lists the people placed now in your branches.",
                      "Shows their login, what their role gives, and the access given by hand."],
        description="Filter by branch_id, or search by name or employee ID.",
        roles=ROLES, paginated=True,
        params=[Param("branch_id", QUERY, "integer", "Only this branch.", example=3),
                Param("q", QUERY, "string", "Search by name or employee ID.", example="rahim")],
        response=s.PersonRowSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [{
            "employee": {"id": 41, "name": "Rahim Uddin"}, "employee_code": "E-0041",
            "branch": {"id": 3, "name": "Chattogram"},
            "login": {"role": "employee", "label": "Employee", "disabled": False},
            "role_note": "", "access": [{"permission": "employees.view",
                                         "label": "View employees",
                                         "branches": [{"id": 3, "name": "Chattogram"}]}]}]},
        errors=ERRORS + ["validation_error"],
    )
    def get(self, request):
        company_id = request.company_id
        branches = services.grant_branches(request.user, company_id)
        choices = services.branch_choices(company_id, branches)
        names = {b.pk: b.name for b in choices}
        rows = services.people(branches)
        branch_id = (request.query_params.get("branch_id") or "").strip()
        if branch_id:
            if not branch_id.isdigit():
                raise ApiError("validation_error", fields={"branch_id": ["A whole number."]})
            rows = rows.filter(table_branch_id=int(branch_id))
        query = (request.query_params.get("q") or "").strip()[:200]
        if query:
            rows = rows.filter(Q(first_name__icontains=query) | Q(last_name__icontains=query)
                               | Q(table_code__icontains=query))
        paginator = StandardPagination()
        page = paginator.paginate_queryset(rows.order_by("first_name", "last_name"), request,
                                           view=self)
        out = []
        for employee in page:
            membership = employee_login.login_for(company_id, employee)
            held = services.granted(employee)
            out.append({
                "employee": {"id": employee.pk, "name": employee.full_name},
                "employee_code": employee.table_code,
                "branch": {"id": employee.table_branch_id, "name": employee.table_branch},
                "login": {
                    "role": membership.role,
                    "label": services.LOGIN_LABELS.get(membership.role,
                                                       membership.get_role_display()),
                    "disabled": membership.status != "active",
                } if membership else None,
                "role_note": services.role_note(company_id, membership),
                "access": [
                    {"permission": code, "label": access.LABELS[code],
                     "branches": [{"id": b, "name": names.get(b, "another branch")}
                                  for b in sorted(branch_ids)]}
                    for code, branch_ids in sorted(held.items())
                ],
            })
        return paginator.get_paginated_response(s.PersonRowSerializer(out, many=True).data)


class PersonAccessView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    panel_page = "organization:access_person"
    read_scope = write_scope = None
    throttle_scope = "write"

    @endpoint(
        id="access-person-get", area=AREA, title="Access: one person",
        summary="One person's access grid: each permission in each branch.",
        what_it_does=["Answers the grid as the panel shows it, with what you may change."],
        description="A cell is editable where you may give access and hold that permission in "
                    "that branch. Your own access, and an owner's or administrator's, cannot be "
                    "changed.",
        roles=ROLES, params=[PERSON_PARAM], response=s.PersonAccessSerializer,
        response_example=GRID_EXAMPLE, errors=ERRORS + ["not_found"],
    )
    def get(self, request, employee_id):
        return Response(s.PersonAccessSerializer(_grid(_person(request, employee_id))).data)

    @endpoint(
        id="access-person-put", area=AREA, title="Access: change one person's",
        summary="Save the grid: give what is listed, take away what is not.",
        what_it_does=["Gives the permissions listed, in those branches.",
                      "Takes away the ones you may change that are not listed.",
                      "Records each change, with the reason, in the audit log."],
        description=("Send the whole list of what the person should have (as the panel's ticked "
                     "boxes). Cells you may not change are left as they are, whatever is sent. "
                     "Answers what was added and removed, and the new grid."),
        roles=ROLES, params=[PERSON_PARAM],
        request=s.AccessChangeSerializer, response=s.AccessChangedSerializer,
        request_example={"grants": [{"permission": "employees.view", "branch_id": 1},
                                    {"permission": "leave.view", "branch_id": 1}],
                         "reason": "Covers HR at Head Office"},
        response_example={**GRID_EXAMPLE,
                          "added": [{"permission": "leave.view", "branch_id": 1}],
                          "removed": []},
        errors=ERRORS + ["not_found", "validation_error", "unknown_field"],
    )
    def put(self, request, employee_id):
        _person(request, employee_id)
        data = s.AccessChangeSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        ticked = {(g["permission"], g["branch_id"]) for g in data.validated_data["grants"]}
        with service_errors():
            added, removed = services.save_person(request.user, request.company_id, employee_id,
                                                  ticked, data.validated_data.get("reason", ""))
        grid = _grid(services.person(request.user, request.company_id, employee_id))
        return Response(s.AccessChangedSerializer({
            **grid,
            "added": [{"permission": c, "branch_id": b} for c, b in added],
            "removed": [{"permission": c, "branch_id": b} for c, b in removed],
        }).data)
