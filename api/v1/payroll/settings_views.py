"""Salary, part b: the company's salary settings and rules, allowances and
deductions (and giving them to people), penalty rules, and LFA
(docs/api/90-salary.md).

The panel's own forms and services (``payroll.policy``,
``payroll.component_services``, ``payroll.penalties``, ``payroll.lfa``):
settings, components and penalty rules are the owner's or company
administrator's; giving an allowance and LFA claims follow Prepare salary.
"""

import datetime

from django.core.exceptions import PermissionDenied
from django.utils import timezone
from rest_framework.response import Response

from access_control.branch_access import ALL_BRANCHES
from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.files import private_file, upload_from
from api.core.forms import checked, refuse, service_errors
from api.core.permissions import PanelRule
from api.v1.payroll import settings_serializers as s
from api.v1.payroll.views import ERRORS, ONE, WRITE_ERRORS, SalaryView, _email, _ref
from common.choices import ActiveStatus
from common.tenant import use_company
from organization.services import require_structure_manager
from payroll import component_services, lfa, penalties, policy
from payroll.models import (
    AttendancePenaltyRule, EmployeeSalaryComponent, LfaClaim, PayrollPolicyVersion,
)

AREA = "salary"
ADMINS = ["Company owner or administrator"]
PREPARERS = ["The owner or company administrator; anyone given Prepare salary in the person's "
             "branch"]
COMPONENT_PARAM = Param("component_id", PATH, "integer", "The component id.", required=True,
                        example=4)
RULE_PARAM = Param("rule_id", PATH, "integer", "The penalty rule (version) id.", required=True,
                   example=6)
CLAIM_PARAM = Param("claim_id", PATH, "integer", "The claim id.", required=True, example=8)
EMPLOYEE_PARAM = Param("employee_id", PATH, "integer", "The employee id.", required=True,
                       example=41)


def _month_start(value, field):
    """``YYYY-MM`` -> the 1st of that month."""
    try:
        year, month = (int(part) for part in str(value or "").split("-"))
        return datetime.date(year, month, 1)
    except (TypeError, ValueError):
        refuse({field: ["Give a month as YYYY-MM."]})


def _form_value(value):
    """A value as a posted form would carry it."""
    if value is True:
        return "on"
    if value is None or value is False:
        return ""
    return str(value)


def _form_input(values):
    return {key: _form_value(value) for key, value in values.items()
            if value is not False and value is not None}


# --- salary settings ----------------------------------------------------------------------

RULE_NAMES = ("monthly_proration_method", "monthly_divisor", "absence_deduction_method",
              "half_day_pay_percent", "incomplete_day_treatment", "daily_paid_days_off",
              "hourly_paid_days_off", "money_rounding_increment", "money_rounding_mode",
              "allow_negative_net_pay", "maximum_period_deduction_percent",
              "overtime_multiplier", "holiday_overtime_multiplier", "minimum_overtime_minutes",
              "overtime_rounding_minutes")


def _settings_out(request):
    from payroll.views import rules_summary

    today = timezone.localdate()
    page = policy.salary_settings_page(actor=request.user, company_id=request.company_id,
                                       today=today)
    from payroll.forms import SalaryRulesForm

    initial = SalaryRulesForm.initial_from(page["rules"], today)
    versions = []
    for version in page["versions"]:
        last = version.effective_to - datetime.timedelta(days=1) if version.effective_to else None
        if version.status != PayrollPolicyVersion.Status.ACTIVE:
            state = "replaced"
        elif version.effective_from > today:
            state = "upcoming"
        elif version.effective_to and version.effective_to <= today:
            state = "ended"
        else:
            state = "in_use"
        versions.append({"id": version.pk, "number": version.version_number,
                         "effective_from": version.effective_from, "last_day": last,
                         "state": state})
    current = page["current"]
    return {
        "currency": page["currency"],
        "pay_day": page["settings"].default_pay_day if page["settings"] else None,
        "rules": {name: initial[name] for name in RULE_NAMES},
        "rules_from": current.effective_from if current else None,
        "summary": [list(pair) for pair in rules_summary(page["rules"])],
        "versions": versions,
    }


RULES_EXAMPLE = {"monthly_proration_method": "fixed_30", "monthly_divisor": "30.00",
                 "absence_deduction_method": "day_fraction", "half_day_pay_percent": "50.00",
                 "incomplete_day_treatment": "pay_full", "daily_paid_days_off": True,
                 "hourly_paid_days_off": False, "money_rounding_increment": "1",
                 "money_rounding_mode": "half_up", "allow_negative_net_pay": False,
                 "maximum_period_deduction_percent": None, "overtime_multiplier": "2.00",
                 "holiday_overtime_multiplier": "2.00", "minimum_overtime_minutes": 30,
                 "overtime_rounding_minutes": 30}
SETTINGS_EXAMPLE = {"currency": "BDT", "pay_day": 7, "rules": RULES_EXAMPLE,
                    "rules_from": "2026-01-01",
                    "summary": [["One day's pay for absence deductions",
                                 "Monthly salary / 30 days"]],
                    "versions": [{"id": 3, "number": 1, "effective_from": "2026-01-01",
                                  "last_day": None, "state": "in_use"}]}


class SalarySettingsView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:salary_settings"

    @endpoint(
        id="salary-settings", area=AREA, title="Salary settings",
        summary="Currency, pay day, and the salary rules in force - with their history.",
        what_it_does=["Answers the settings, today's rules (and in plain words), and every "
                      "version of the rules."],
        description="Change the currency or pay day with PATCH; new rules with POST "
                    "…/settings/rules.",
        roles=ADMINS, scopes=["payroll:read"], response=s.SalarySettingsSerializer,
        response_example=SETTINGS_EXAMPLE, errors=ERRORS,
    )
    def get(self, request):
        return Response(s.SalarySettingsSerializer(_settings_out(request)).data)

    @endpoint(
        id="salary-settings-change", area=AREA, title="Change currency or pay day",
        summary="The currency used when a salary has none, and the usual pay day.",
        what_it_does=["Saves the fields sent; records it in the audit log."],
        description="These are plain settings, not dated rules.",
        roles=ADMINS, scopes=["payroll:write"], request=s.GeneralSettingsInputSerializer,
        response=s.SalarySettingsSerializer, request_example={"pay_day": 5},
        response_example={**SETTINGS_EXAMPLE, "pay_day": 5}, errors=WRITE_ERRORS,
    )
    def patch(self, request):
        from payroll.forms import GeneralSettingsForm

        current = _settings_out(request)
        data = s.GeneralSettingsInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = {"currency": current["currency"], "pay_day": current["pay_day"],
                  **data.validated_data}
        names = {"default_pay_day": "pay_day"}
        form = checked(GeneralSettingsForm, {
            "currency": values["currency"] or "",
            "default_pay_day": "" if values["pay_day"] is None else values["pay_day"]}, names)
        with service_errors(names):
            policy.update_general_settings(actor=request.user, company_id=request.company_id,
                                           values=form.cleaned_data)
        return Response(s.SalarySettingsSerializer(_settings_out(request)).data)


class SalaryRulesView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:salary_settings"

    @endpoint(
        id="salary-rules-change", area=AREA, title="New salary rules from a month",
        summary="How absence, half days, rounding, penalties and overtime are paid - from "
                "the 1st of a month.",
        what_it_does=["Saves the rules from that month; the rules before it stay. Saving "
                      "twice for one month corrects it."],
        description=("Send applies_from and only the rules that change; the rest keep their "
                     "current value. Refused before a change already saved for a later month. "
                     "Generate a month again to apply new rules to it."),
        roles=ADMINS, scopes=["payroll:write"], request=s.SalaryRulesInputSerializer,
        response=s.SalarySettingsSerializer,
        request_example={"applies_from": "2026-11", "overtime_multiplier": "1.5"},
        response_example=SETTINGS_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        from payroll.forms import SalaryRulesForm

        today = timezone.localdate()
        page = policy.salary_settings_page(actor=request.user, company_id=request.company_id,
                                           today=today)
        latest = next((v for v in page["versions"] if v.status == "active"), None)
        if latest and latest.effective_from > today:
            start = policy.SalaryRules.from_version(latest)
        else:
            start = page["rules"]
        data = s.SalaryRulesInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        starts = _month_start(values.pop("applies_from"), "applies_from")
        base = SalaryRulesForm.initial_from(start, starts)
        merged = {**{name: base[name] for name in RULE_NAMES}, **values}
        form_input = _form_input(merged)
        form_input.update(applies_month=starts.month, applies_year=starts.year)
        names = {"applies_month": "applies_from", "applies_year": "applies_from"}
        form = checked(SalaryRulesForm, form_input, names,
                       years=range(today.year - 1, max(today.year + 3, starts.year + 1)))
        with service_errors({"effective_from": "applies_from"}):
            policy.change_salary_rules(actor=request.user, company_id=request.company_id,
                                       values=form.service_values())
        return Response(s.SalarySettingsSerializer(_settings_out(request)).data)


# --- components ---------------------------------------------------------------------------

def _component_out(component):
    return {"id": component.pk, "code": component.code, "name": component.name,
            "kind": component.kind, "method": component.method,
            "default_amount": component.default_amount,
            "default_percent": component.default_percent,
            "description": component.description, "status": component.status}


COMPONENT_EXAMPLE = {"id": 4, "code": "HOUSE_RENT", "name": "House rent", "kind": "earning",
                     "method": "percent_of_basic", "default_amount": None,
                     "default_percent": "40.000", "description": "", "status": "active"}


def _component_input(values):
    return {"code": values.get("code", ""), "name": values.get("name", ""),
            "kind": values.get("kind", ""), "method": values.get("method", ""),
            "default_amount": _form_value(values.get("default_amount")),
            "default_percent": _form_value(values.get("default_percent")),
            "description": values.get("description") or ""}


def _component(request, component_id):
    require_structure_manager(request.user, request.company_id)
    component = component_services.components(request.company_id).filter(
        pk=component_id).first()
    if component is None:
        raise ApiError("not_found", "No such component in this company.")
    return component


class ComponentListView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:component_list"

    @endpoint(
        id="salary-components", area=AREA, title="Allowances and deductions",
        summary="The company's list: house rent, transport, a loan repayment …",
        what_it_does=["Lists them by code."],
        description="Give one to a person with POST /employees/{id}/components.",
        roles=ADMINS, scopes=["payroll:read"], paginated=True, response=s.ComponentSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [COMPONENT_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        rows = component_services.components(request.company_id).order_by("code")
        return self.page(request, rows, _component_out, s.ComponentSerializer)

    @endpoint(
        id="salary-components-create", area=AREA, title="Add an allowance or deduction",
        summary="A fixed amount or a percentage of basic, paid or deducted each month.",
        what_it_does=["Adds it to the list; records it in the audit log."],
        description="fixed needs default_amount; percent_of_basic needs default_percent.",
        roles=ADMINS, scopes=["payroll:write"], response_status=201,
        request=s.ComponentInputSerializer, response=s.ComponentSerializer,
        request_example={"code": "HOUSE_RENT", "name": "House rent", "kind": "earning",
                         "method": "percent_of_basic", "default_percent": "40"},
        response_example=COMPONENT_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        from payroll.forms import SalaryComponentForm

        require_structure_manager(request.user, request.company_id)
        data = s.ComponentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        form = checked(SalaryComponentForm, _component_input(data.validated_data))
        with service_errors():
            component = component_services.create_component(
                actor=request.user, company_id=request.company_id, values=form.cleaned_data)
        return Response(s.ComponentSerializer(_component_out(component)).data, status=201)


class ComponentView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "payroll:component_list", "PATCH": "payroll:component_edit"}

    @endpoint(
        id="salary-components-one", area=AREA, title="One allowance or deduction",
        summary="An entry of the list.", what_it_does=["Answers it."],
        description="Change it with PATCH.", roles=ADMINS, scopes=["payroll:read"],
        params=[COMPONENT_PARAM], response=s.ComponentSerializer,
        response_example=COMPONENT_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, component_id):
        return Response(s.ComponentSerializer(_component_out(
            _component(request, component_id))).data)

    @endpoint(
        id="salary-components-change", area=AREA, title="Change an allowance or deduction",
        summary="Its name, amount or percentage, or description.",
        what_it_does=["Saves the fields sent. It applies from the next time salary is "
                      "generated."],
        description="The code stays as it was given.", roles=ADMINS,
        scopes=["payroll:write"], params=[COMPONENT_PARAM],
        request=s.ComponentInputSerializer, response=s.ComponentSerializer,
        request_example={"default_percent": "45"},
        response_example={**COMPONENT_EXAMPLE, "default_percent": "45.000"},
        errors=WRITE_ERRORS + ONE,
    )
    def patch(self, request, component_id):
        from payroll.forms import SalaryComponentForm

        component = _component(request, component_id)
        data = s.ComponentInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        merged = {**_component_out(component), **data.validated_data}
        form = checked(SalaryComponentForm, _component_input(merged), instance=component)
        with service_errors():
            component = component_services.update_component(
                actor=request.user, company_id=request.company_id, component_id=component.pk,
                values=form.cleaned_data)
        return Response(s.ComponentSerializer(_component_out(component)).data)


class ComponentStatusView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:component_status"

    @endpoint(
        id="salary-components-status", area=AREA, title="Offer it or stop offering it",
        summary="An inactive one stops counting the next time salary is generated.",
        what_it_does=["Sets the status."], description="status: active or inactive.",
        roles=ADMINS, scopes=["payroll:write"], params=[COMPONENT_PARAM],
        request=s.ComponentStatusSerializer, response=s.ComponentSerializer,
        request_example={"status": "inactive"},
        response_example={**COMPONENT_EXAMPLE, "status": "inactive"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, component_id):
        _component(request, component_id)
        data = s.ComponentStatusSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            component = component_services.set_component_status(
                actor=request.user, company_id=request.company_id, component_id=component_id,
                status=data.validated_data["status"])
        return Response(s.ComponentSerializer(_component_out(component)).data)


# --- a person's allowances ----------------------------------------------------------------

def _person_row_out(row):
    return {"id": row.pk, "component": _ref(row.component), "kind": row.component.kind,
            "amount": row.amount, "percent": row.percent,
            "effective_from": row.effective_from, "effective_to": row.effective_to,
            "reason": row.reason}


ROW_EXAMPLE = {"id": 12, "component": {"id": 4, "name": "House rent"}, "kind": "earning",
               "amount": None, "percent": "40.000", "effective_from": "2026-10-01",
               "effective_to": None, "reason": ""}


def _person(request, employee_id, *codes):
    from employees.models import Employee, EmployeeAssignment
    from payroll.services import salary_branches

    employee = Employee.objects.filter(pk=employee_id).first()
    if employee is None:
        raise ApiError("not_found", "No such employee in this company.")
    _m, branches = salary_branches(request.user, request.company_id, *codes)
    placed = (EmployeeAssignment.objects.filter(employee=employee).exclude(status="cancelled")
              .order_by("-effective_from").first())
    if not branches or (branches is not ALL_BRANCHES
                        and (placed is None or placed.branch_id not in branches)):
        raise PermissionDenied("Their pay is outside the branches where you have salary "
                               "access.")
    return employee


class EmployeeComponentsView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-components", area=AREA, title="A person's allowances and deductions",
        summary="What they get or have deducted each month, from when, until when.",
        what_it_does=["Lists them, newest first."],
        description="effective_to null: still running.",
        roles=["Whoever sees salaries in their branch"], scopes=["payroll:read"],
        params=[EMPLOYEE_PARAM], paginated=True, response=s.EmployeeComponentSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [ROW_EXAMPLE]},
        errors=ERRORS + ONE,
    )
    def get(self, request, employee_id):
        employee = _person(request, employee_id, "salary.view", "salary.prepare")
        rows = component_services.employee_rows(request.company_id, employee)
        return self.page(request, rows, _person_row_out, s.EmployeeComponentSerializer)

    @endpoint(
        id="employees-components-give", area=AREA, title="Give an allowance or deduction",
        summary="From a date, at its default or another amount or percentage.",
        what_it_does=["Gives it; one they already have changes from that date (the old "
                      "amount ends the day before)."],
        description="It is on their payslip the next time salary is generated.",
        roles=PREPARERS, scopes=["payroll:write"], params=[EMPLOYEE_PARAM],
        response_status=201, request=s.GiveComponentInputSerializer,
        response=s.EmployeeComponentSerializer,
        request_example={"component_id": 4, "effective_from": "2026-10-01"},
        response_example=ROW_EXAMPLE, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, employee_id):
        from payroll.forms import GiveComponentForm

        _person(request, employee_id, "salary.prepare")
        data = s.GiveComponentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        names = {"component": "component_id"}
        form = checked(GiveComponentForm, {
            "component": values["component_id"], "amount": _form_value(values.get("amount")),
            "percent": _form_value(values.get("percent")),
            "effective_from": values["effective_from"].isoformat(),
            "reason": values.get("reason", "")}, names,
            components=component_services.components(request.company_id, active_only=True))
        with service_errors(names):
            row = component_services.give_component(
                actor=request.user, company_id=request.company_id, employee_id=employee_id,
                values=form.cleaned_data)
        return Response(s.EmployeeComponentSerializer(_person_row_out(row)).data, status=201)


class EmployeeComponentEndView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "organization:employee_edit"

    @endpoint(
        id="employees-components-end", area=AREA, title="End an allowance or deduction",
        summary="It is paid (or deducted) up to that day.",
        what_it_does=["Sets its last day."], description="Not before it starts.",
        roles=PREPARERS, scopes=["payroll:write"],
        params=[EMPLOYEE_PARAM, Param("row_id", PATH, "integer", "Their row id.",
                                      required=True, example=12)],
        request=s.EndComponentInputSerializer, response=s.EmployeeComponentSerializer,
        request_example={"last_day": "2026-12-31"},
        response_example={**ROW_EXAMPLE, "effective_to": "2026-12-31"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, employee_id, row_id):
        _person(request, employee_id, "salary.prepare")
        if not EmployeeSalaryComponent.objects.filter(pk=row_id, employee_id=employee_id,
                                                      status="active").exists():
            raise ApiError("not_found", "They have no such allowance or deduction.")
        data = s.EndComponentInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            row = component_services.end_component(
                actor=request.user, company_id=request.company_id, row_id=row_id,
                last_day=data.validated_data["last_day"])
        return Response(s.EmployeeComponentSerializer(_person_row_out(row)).data)


# --- penalty rules ------------------------------------------------------------------------

def _rule_rows(request):
    from payroll.views import _penalty_rows

    page = policy.salary_settings_page(actor=request.user, company_id=request.company_id,
                                       today=timezone.localdate())
    return _penalty_rows(request.company_id, timezone.localdate(), page["currency"])


def _rule_out(row):
    rule = row["rule"]
    return {
        "id": rule.pk, "code": rule.code, "name": rule.name, "metric": rule.metric,
        "operator": rule.operator, "threshold_minutes": rule.threshold_minutes,
        "occurrence_mode": rule.occurrence_mode,
        "required_occurrences": rule.required_occurrences,
        "deduction_method": rule.deduction_method, "deduction_value": rule.deduction_value,
        "exclusive_group": rule.exclusive_group, "maximum_deduction": rule.maximum_deduction,
        "effective_from": rule.effective_from, "last_day": row["last_day"],
        "state": "upcoming" if row["state"][0] == "Upcoming" else "in_use",
        "when": row["when"], "deducts": row["deducts"], "may_change": row["can_change"],
    }


def _one_rule(request, rule_id):
    found = next((row for row in _rule_rows(request) if row["rule"].pk == rule_id), None)
    if found is None:
        raise ApiError("not_found", "No such penalty rule in use or upcoming.")
    return found


RULE_EXAMPLE = {"id": 6, "code": "LATE_10", "name": "Late more than 10 minutes",
                "metric": "late_minutes", "operator": "gt", "threshold_minutes": 10,
                "occurrence_mode": "within_period", "required_occurrences": 3,
                "deduction_method": "day_fraction", "deduction_value": "0.5000",
                "exclusive_group": "", "maximum_deduction": None,
                "effective_from": "2026-10-01", "last_day": None, "state": "in_use",
                "when": "Late more than 10 minutes, every 3 days in a month",
                "deducts": "half a day's pay", "may_change": True}


def _rule_form(values, starts):
    from payroll.forms import PenaltyRuleForm

    form_input = _form_input({name: values.get(name) for name in penalties.RULE_FIELDS})
    form_input.update(applies_month=starts.month, applies_year=starts.year)
    form = PenaltyRuleForm(data=form_input)
    today = timezone.localdate()
    form.fields["applies_year"].choices = [(y, y) for y in range(today.year - 1,
                                                                 max(today.year + 3,
                                                                     starts.year + 1))]
    if not form.is_valid():
        refuse(form.errors, {"applies_month": "applies_from", "applies_year": "applies_from"})
    return form


def _new_rule(request, rule_code):
    from payroll.models import AttendancePenaltyRule as Rule

    rule = (Rule.objects.filter(code=rule_code, status=Rule.Status.ACTIVE)
            .order_by("-effective_from").first())
    return next((row for row in _rule_rows(request) if row["rule"].pk == rule.pk), None)


class PenaltyRuleListView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "payroll:salary_settings", "POST": "payroll:penalty_rule_create"}

    @endpoint(
        id="salary-penalty-rules", area=AREA, title="Penalty rules",
        summary="Deductions for arriving late, leaving early, working short or being absent.",
        what_it_does=["Lists the rules in use now or saved for a later month, each in plain "
                      "words."],
        description="Each change is a new version from a month; earlier months keep the old "
                    "rule.",
        roles=ADMINS, scopes=["payroll:read"], paginated=True, response=s.PenaltyRuleSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [RULE_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        return self.page(request, _rule_rows(request), _rule_out, s.PenaltyRuleSerializer)

    @endpoint(
        id="salary-penalty-rules-create", area=AREA, title="Add a penalty rule",
        summary="What it measures, when it applies, and what it deducts - from a month.",
        what_it_does=["Adds the rule from the 1st of applies_from."],
        description=("metric: late_minutes, early_out_minutes, worked_shortfall (with "
                     "threshold_minutes) or absence. occurrence_mode: every day it happens, "
                     "every so many days in a month, or so many working days in a row. "
                     "deduction_value is minutes, days of pay (0.5) or money, as "
                     "deduction_method says."),
        roles=ADMINS, scopes=["payroll:write"], response_status=201,
        request=s.PenaltyRuleInputSerializer, response=s.PenaltyRuleSerializer,
        request_example={"name": "Late more than 10 minutes", "metric": "late_minutes",
                         "operator": "gt", "threshold_minutes": 10,
                         "occurrence_mode": "within_period", "required_occurrences": 3,
                         "deduction_method": "day_fraction", "deduction_value": "0.5",
                         "applies_from": "2026-10"},
        response_example=RULE_EXAMPLE, errors=WRITE_ERRORS,
    )
    def post(self, request):
        require_structure_manager(request.user, request.company_id)
        data = s.PenaltyRuleInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        starts = _month_start(values.pop("applies_from"), "applies_from")
        form = _rule_form(values, starts)
        with service_errors({"effective_from": "applies_from"}):
            rule = penalties.create_penalty_rule(actor=request.user,
                                                 company_id=request.company_id,
                                                 values=form.service_values())
        return Response(s.PenaltyRuleSerializer(_rule_out(_new_rule(request, rule.code))).data,
                        status=201)


class PenaltyRuleView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:salary_settings"

    @endpoint(
        id="salary-penalty-rules-one", area=AREA, title="One penalty rule",
        summary="A rule in use or upcoming.", what_it_does=["Answers it."],
        description="Rules that ended are not listed.", roles=ADMINS,
        scopes=["payroll:read"], params=[RULE_PARAM],
        response=s.PenaltyRuleSerializer, response_example=RULE_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, rule_id):
        require_structure_manager(request.user, request.company_id)
        return Response(s.PenaltyRuleSerializer(_rule_out(_one_rule(request, rule_id))).data)


class PenaltyRuleChangeView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:penalty_rule_change"

    @endpoint(
        id="salary-penalty-rules-change", area=AREA, title="Change a penalty rule",
        summary="A new version of the rule from a month; earlier months keep the old one.",
        what_it_does=["Saves the change from the 1st of applies_from."],
        description="Send applies_from and what changes; the rest keeps its current value.",
        roles=ADMINS, scopes=["payroll:write"], params=[RULE_PARAM],
        request=s.PenaltyRuleInputSerializer, response=s.PenaltyRuleSerializer,
        request_example={"deduction_value": "1", "applies_from": "2026-11"},
        response_example={**RULE_EXAMPLE, "deduction_value": "1.0000",
                          "effective_from": "2026-11-01", "state": "upcoming"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, rule_id):
        require_structure_manager(request.user, request.company_id)
        row = _one_rule(request, rule_id)
        rule = row["rule"]
        data = s.PenaltyRuleInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        values = dict(data.validated_data)
        if "applies_from" not in values:
            refuse({"applies_from": ["Give the month the change applies from (YYYY-MM)."]})
        starts = _month_start(values.pop("applies_from"), "applies_from")
        current = {name: getattr(rule, name) for name in penalties.RULE_FIELDS}
        # DRF fills defaults even on a partial body: only what was sent changes.
        sent = {key: value for key, value in values.items() if key in request.data}
        form = _rule_form({**current, **sent}, starts)
        with service_errors({"effective_from": "applies_from"}):
            new = penalties.change_penalty_rule(actor=request.user,
                                                company_id=request.company_id, rule_id=rule.pk,
                                                values=form.service_values())
        return Response(s.PenaltyRuleSerializer(_rule_out(_new_rule(request, new.code))).data)


class PenaltyRuleStopView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:penalty_rule_stop"

    @endpoint(
        id="salary-penalty-rules-stop", area=AREA, title="Stop a penalty rule",
        summary="It no longer applies from a month; earlier months keep it.",
        what_it_does=["Ends the rule before the 1st of stops_from."],
        description="No content (204) once stopped.",
        roles=ADMINS, scopes=["payroll:write"], params=[RULE_PARAM],
        request=s.StopRuleInputSerializer, response=None,
        request_example={"stops_from": "2027-01"}, errors=WRITE_ERRORS + ONE,
        response_status=204,
    )
    def post(self, request, rule_id):
        require_structure_manager(request.user, request.company_id)
        rule = _one_rule(request, rule_id)["rule"]
        data = s.StopRuleInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        stops = _month_start(data.validated_data["stops_from"], "stops_from")
        with service_errors({"stops_from": "stops_from"}):
            penalties.stop_penalty_rule(actor=request.user, company_id=request.company_id,
                                        rule_id=rule.pk, stops_from=stops)
        return Response(status=204)


# --- LFA ----------------------------------------------------------------------------------

LFA_FLAGS = ("enabled", "probation_eligible", "requires_leave", "requires_document",
             "prorate_first_cycle")
LFA_PLAIN = ("name", "description", "amount_method", "fixed_amount", "months", "max_amount",
             "min_service_months", "cycle", "claims_per_cycle", "min_leave_days", "payment")


def _lfa_settings_out(settings):
    out = {name: getattr(settings, name) for name in (*LFA_FLAGS, *LFA_PLAIN)}
    out["leave_type_ids"] = [t.pk for t in settings.leave_types.all()] if settings.pk else []
    return out


LFA_SETTINGS_EXAMPLE = {"enabled": True, "name": "Leave Fare Assistance", "description": "",
                        "amount_method": "basic_months", "fixed_amount": None, "months": "1.00",
                        "max_amount": None, "min_service_months": 12,
                        "probation_eligible": False, "cycle": "calendar_year",
                        "claims_per_cycle": 1, "requires_leave": True, "leave_type_ids": [3],
                        "min_leave_days": "5.00", "requires_document": False,
                        "prorate_first_cycle": False, "payment": "with_salary"}


class LfaSettingsView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_settings"

    @endpoint(
        id="salary-lfa-settings", area=AREA, title="LFA settings",
        summary="The company's Leave Fare Assistance rules, and whether it is on.",
        what_it_does=["Answers the rules."], description="Change them with PATCH.",
        roles=ADMINS, scopes=["payroll:read"], response=s.LfaSettingsSerializer,
        response_example=LFA_SETTINGS_EXAMPLE, errors=ERRORS,
    )
    def get(self, request):
        require_structure_manager(request.user, request.company_id)
        return Response(s.LfaSettingsSerializer(
            _lfa_settings_out(lfa.settings_for(request.company_id))).data)

    @endpoint(
        id="salary-lfa-settings-change", area=AREA, title="Change the LFA settings",
        summary="How much, who may claim, how often, with leave or proof, how it is paid - "
                "and on or off.",
        what_it_does=["Saves the fields sent. Claims already made keep the rules they were "
                      "made under."],
        description=("amount_method fixed needs fixed_amount; basic_months or gross_months "
                     "need months. leave_type_ids and min_leave_days apply when "
                     "requires_leave."),
        roles=ADMINS, scopes=["payroll:write"], request=s.LfaSettingsInputSerializer,
        response=s.LfaSettingsSerializer,
        request_example={"enabled": True, "amount_method": "basic_months", "months": "1"},
        response_example=LFA_SETTINGS_EXAMPLE, errors=WRITE_ERRORS,
    )
    def patch(self, request):
        from leaves.models import LeaveType
        from payroll.lfa_forms import LfaSettingsForm

        require_structure_manager(request.user, request.company_id)
        settings = lfa.settings_for(request.company_id)
        data = s.LfaSettingsInputSerializer(data=request.data, partial=True)
        data.is_valid(raise_exception=True)
        merged = {**_lfa_settings_out(settings), **data.validated_data}
        form_input = {name: _form_value(merged.get(name)) for name in LFA_PLAIN}
        form_input.update({name: "1" if merged.get(name) else "0" for name in LFA_FLAGS})
        form_input["leave_types"] = [str(pk) for pk in merged.get("leave_type_ids") or []]
        types = LeaveType.objects.filter(status=ActiveStatus.ACTIVE)
        names = {"leave_types": "leave_type_ids"}
        form = checked(LfaSettingsForm, form_input, names, leave_types=types)
        values = dict(form.cleaned_data)
        chosen = values.pop("leave_types")
        with service_errors(names):
            lfa.save_settings(actor=request.user, company_id=request.company_id, values=values,
                              leave_types=chosen)
        return Response(s.LfaSettingsSerializer(
            _lfa_settings_out(lfa.settings_for(request.company_id))).data)


def _lfa_scope(request):
    scope = lfa.decide_scope(request.user, request.company_id)
    if not scope:
        raise PermissionDenied("LFA claims are for the owner, the company administrator or "
                               "whoever prepares salary in a branch.")
    return scope


def _lfa_employees(request, scope):
    from employees.models import Employee
    from organization.access_services import people

    rows = Employee.objects.all() if scope is ALL_BRANCHES else people(scope)
    return rows.filter(employment_status__in=["active", "probation"]).exclude(user=request.user)


def _claim_out(request, claim):
    adjustment = claim.payroll_adjustment
    return {
        "id": claim.pk, "employee": _ref(claim.employee), "cycle_start": claim.cycle_start,
        "cycle_end": claim.cycle_end, "leave_id": claim.leave_request_id, "note": claim.note,
        "has_document": bool(claim.document), "calculated_amount": claim.calculated_amount,
        "approved_amount": claim.approved_amount, "currency": claim.currency,
        "status": claim.status, "payment": claim.payment,
        "pay_month": adjustment.target_payroll_period.name if adjustment else None,
        "paid_on": claim.paid_on, "payment_reference": claim.payment_reference,
        "decided_by": _email(claim.decided_by), "decided_at": claim.decided_at,
        "decision_note": claim.decision_note,
        "may_decide": claim.status == LfaClaim.Status.PENDING
        and claim.employee.user_id != request.user.pk
        and lfa._deciders(request.user, request.company_id, claim.employee)[1],
    }


CLAIM_EXAMPLE = {"id": 8, "employee": {"id": 41, "name": "Rahim Uddin"},
                 "cycle_start": "2026-01-01", "cycle_end": "2026-12-31", "leave_id": 31,
                 "note": "Cox's Bazar trip", "has_document": False,
                 "calculated_amount": "30000.00", "approved_amount": None, "currency": "BDT",
                 "status": "pending", "payment": "with_salary", "pay_month": None,
                 "paid_on": None, "payment_reference": "", "decided_by": None,
                 "decided_at": None, "decision_note": "", "may_decide": True}


def _claims(scope):
    from organization.access_services import people

    rows = LfaClaim.objects.select_related(
        "employee", "decided_by", "payroll_adjustment__target_payroll_period")
    return rows if scope is ALL_BRANCHES else rows.filter(employee__in=people(scope))


def _claim(request, claim_id):
    scope = _lfa_scope(request)
    claim = _claims(scope).filter(pk=claim_id).first()
    if claim is None:
        raise ApiError("not_found", "No such claim among those you decide.")
    lfa.sync_paid(request.company_id, [claim])
    return claim


class LfaEligibilityView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_claim_new"

    @endpoint(
        id="salary-lfa-eligibility", area=AREA, title="May they claim LFA?",
        summary="Whether a person may claim now, why not, how much, and the leave it may "
                "go with.",
        what_it_does=["Works it out by the company's rules, today."],
        description="Use before entering a claim for them.",
        roles=PREPARERS, scopes=["payroll:read"],
        params=[Param("employee_id", QUERY, "integer", "The person.", required=True,
                      example=41)],
        response=s.LfaEligibilitySerializer,
        response_example={"eligible": True, "reasons": [], "cycle_start": "2026-01-01",
                          "cycle_end": "2026-12-31", "used": 0, "amount": "30000.00",
                          "currency": "BDT", "how": "1 month of basic salary (30000.00)",
                          "leave": [{"id": 31, "start_date": "2026-10-12", "days": "5.00"}]},
        errors=ERRORS + ONE + ["validation_error"],
    )
    def get(self, request):
        scope = _lfa_scope(request)
        raw = request.query_params.get("employee_id", "")
        if not raw.isdigit():
            refuse({"employee_id": ["Give the person's id."]})
        employee = _lfa_employees(request, scope).filter(pk=int(raw)).first()
        if employee is None:
            raise ApiError("not_found", "No such person among those you decide LFA for.")
        found = lfa.eligibility(employee, lfa.settings_for(request.company_id),
                                timezone.localdate())
        cycle = found.cycle or (None, None)
        return Response(s.LfaEligibilitySerializer({
            "eligible": found.ok, "reasons": found.reasons, "cycle_start": cycle[0],
            "cycle_end": cycle[1], "used": found.used, "amount": found.amount,
            "currency": found.currency, "how": found.how,
            "leave": [{"id": r.pk, "start_date": min(seg.start_date for seg in r.segments.all()),
                       "days": r.lfa_units} for r in found.leave_requests]}).data)


class LfaClaimListView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = {"GET": "payroll:lfa_claims", "POST": "payroll:lfa_claim_new"}

    @endpoint(
        id="salary-lfa-claims", area=AREA, title="LFA claims",
        summary="Claims from the people whose LFA you decide.",
        what_it_does=["Lists them, newest first (waiting by default)."],
        description="show: pending (default), approved, paid, closed (rejected, withdrawn or "
                    "cancelled) or all.",
        roles=PREPARERS, scopes=["payroll:read"], paginated=True,
        params=[Param("show", QUERY, "string", "pending, approved, paid, closed or all.",
                      example="pending")],
        response=s.LfaClaimSerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [CLAIM_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        scope = _lfa_scope(request)
        claims = _claims(scope)
        lfa.sync_paid(request.company_id, list(claims.filter(
            status="approved", payroll_adjustment__isnull=False)))
        show = request.query_params.get("show", "pending")
        if show == "closed":
            claims = claims.filter(status__in=("rejected", "withdrawn", "cancelled"))
        elif show != "all":
            claims = claims.filter(status=show if show in ("approved", "paid") else "pending")
        return self.page(request, claims.order_by("-created_at", "-pk"),
                         lambda claim: _claim_out(request, claim), s.LfaClaimSerializer)

    @endpoint(
        id="salary-lfa-claims-create", area=AREA, title="Enter a claim for someone",
        summary="A claim on someone's behalf - for example a person without a login.",
        what_it_does=["Enters the claim, checked by the rules as their own would be; decide "
                      "it now or later."],
        description="Not for yourself. GET …/lfa/eligibility first: leave_id comes from it.",
        roles=PREPARERS, scopes=["payroll:write"], response_status=201,
        request=s.LfaClaimInputSerializer, response=s.LfaClaimSerializer,
        request_example={"employee_id": 41, "leave_id": 31, "note": "Cox's Bazar trip"},
        response_example=CLAIM_EXAMPLE, errors=WRITE_ERRORS + ["payload_too_large"],
    )
    def post(self, request):
        from payroll.lfa_forms import LfaClaimForm

        scope = _lfa_scope(request)
        data = s.LfaClaimInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        employees = _lfa_employees(request, scope)
        settings = lfa.settings_for(request.company_id)
        employee = employees.filter(pk=values["employee_id"]).first()
        found = lfa.eligibility(employee, settings, timezone.localdate()) if employee else None
        files = {}
        if values.get("document"):
            files["document"] = upload_from(values["document"]["filename"],
                                            values["document"]["content_base64"],
                                            field="document")
        names = {"employee": "employee_id", "leave_request": "leave_id"}
        form = checked(LfaClaimForm, {
            "employee": values["employee_id"],
            "leave_request": str(values.get("leave_id") or ""),
            "note": values.get("note", "")}, names, files=files, employees=employees,
            leave_requests=found.leave_requests if found else (),
            needs_leave=settings.requires_leave, needs_document=settings.requires_document)
        with service_errors(names):
            claim = lfa.submit(actor=request.user, company_id=request.company_id,
                               employee=form.cleaned_data["employee"],
                               values={"leave_request": form.cleaned_data.get("leave_request"),
                                       "note": form.cleaned_data.get("note")},
                               document=form.cleaned_data.get("document"),
                               today=timezone.localdate())
        return Response(s.LfaClaimSerializer(_claim_out(request, _claim(request, claim.pk))).data,
                        status=201)


class LfaClaimView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_claim"

    @endpoint(
        id="salary-lfa-claims-one", area=AREA, title="One LFA claim",
        summary="A claim, and whether you may decide it.", what_it_does=["Answers it."],
        description="Decide it with POST …/decide.", roles=PREPARERS, scopes=["payroll:read"],
        params=[CLAIM_PARAM], response=s.LfaClaimSerializer, response_example=CLAIM_EXAMPLE,
        errors=ERRORS + ONE,
    )
    def get(self, request, claim_id):
        return Response(s.LfaClaimSerializer(_claim_out(request, _claim(request, claim_id))).data)


class LfaDecideView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_claim"

    @endpoint(
        id="salary-lfa-claims-decide", area=AREA, title="Approve or reject a claim",
        summary="Approve it (the amount, and the salary month it is paid with) or reject it.",
        what_it_does=["Approved with salary: an LFA line on that month's payslip, paid once "
                      "the month is finalised. Paid separately: mark it paid later."],
        description="pay_month (YYYY-MM) is needed when paid with salary. A note is needed "
                    "when rejecting. Not your own claim.",
        roles=PREPARERS, scopes=["payroll:write"], params=[CLAIM_PARAM],
        request=s.LfaDecideInputSerializer, response=s.LfaClaimSerializer,
        request_example={"decision": "approve", "pay_month": "2026-10"},
        response_example={**CLAIM_EXAMPLE, "status": "approved", "approved_amount": "30000.00",
                          "pay_month": "October 2026", "may_decide": False},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, claim_id):
        from payroll.lfa_forms import LfaDecisionForm

        claim = _claim(request, claim_id)
        data = s.LfaDecideInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        with_salary = claim.payment == "with_salary"
        form_input = {"decision": values["decision"],
                      "amount": _form_value(values.get("amount")),
                      "note": values.get("note", "")}
        if with_salary and values.get("pay_month"):
            form_input["pay_month"] = _month_start(values["pay_month"], "pay_month").isoformat()
        form = checked(LfaDecisionForm, form_input, with_salary=with_salary)
        cleaned = form.cleaned_data
        with service_errors():
            lfa.decide(actor=request.user, company_id=request.company_id, claim_id=claim.pk,
                       approve=cleaned["decision"] == "approve", amount=cleaned.get("amount"),
                       pay_month=cleaned.get("pay_month"), note=cleaned.get("note"))
        return Response(s.LfaClaimSerializer(_claim_out(request, _claim(request, claim_id))).data)


class LfaCancelView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_cancel"

    @endpoint(
        id="salary-lfa-claims-cancel", area=AREA, title="Cancel an approved claim",
        summary="Take back an approval; its payslip line is taken off.",
        what_it_does=["Cancels it, with the reason."],
        description="Not once its salary month is finalised.",
        roles=PREPARERS, scopes=["payroll:write"], params=[CLAIM_PARAM],
        request=s.LfaCancelInputSerializer, response=s.LfaClaimSerializer,
        request_example={"note": "Trip cancelled"},
        response_example={**CLAIM_EXAMPLE, "status": "cancelled", "may_decide": False},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, claim_id):
        claim = _claim(request, claim_id)
        data = s.LfaCancelInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            lfa.cancel(actor=request.user, company_id=request.company_id, claim_id=claim.pk,
                       note=data.validated_data["note"])
        return Response(s.LfaClaimSerializer(_claim_out(request, _claim(request, claim_id))).data)


class LfaPaidView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_paid"

    @endpoint(
        id="salary-lfa-claims-paid", area=AREA, title="Mark a claim paid",
        summary="For a claim paid separately: when, and the cheque or transfer reference.",
        what_it_does=["Marks it paid."],
        description="A claim paid with salary becomes paid by itself once its month is "
                    "finalised.",
        roles=PREPARERS, scopes=["payroll:write"], params=[CLAIM_PARAM],
        request=s.LfaPaidInputSerializer, response=s.LfaClaimSerializer,
        request_example={"paid_on": "2026-10-20", "reference": "TRX-5521"},
        response_example={**CLAIM_EXAMPLE, "status": "paid", "payment": "separately",
                          "paid_on": "2026-10-20", "payment_reference": "TRX-5521",
                          "may_decide": False},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, claim_id):
        claim = _claim(request, claim_id)
        data = s.LfaPaidInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with service_errors():
            lfa.mark_paid(actor=request.user, company_id=request.company_id, claim_id=claim.pk,
                          paid_on=data.validated_data["paid_on"],
                          reference=data.validated_data.get("reference", ""))
        return Response(s.LfaClaimSerializer(_claim_out(request, _claim(request, claim_id))).data)


class LfaDocumentView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:lfa_document"

    @endpoint(
        id="salary-lfa-claims-document", area=AREA, title="A claim's proof",
        summary="The ticket or receipt attached to a claim.",
        what_it_does=["Answers the file itself, privately."],
        description="For whoever decides the person's claims (and the employee).",
        roles=PREPARERS, scopes=["payroll:read"], params=[CLAIM_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, claim_id):
        claim = LfaClaim.objects.select_related("employee").filter(pk=claim_id).first()
        if claim is None:
            raise ApiError("not_found", "No such claim in this company.")
        mine = claim.employee.user_id == request.user.pk
        if not mine and not lfa._deciders(request.user, request.company_id, claim.employee)[1]:
            raise PermissionDenied("This document is not yours to open.")
        if not claim.document:
            raise ApiError("not_found", "This claim has no document.")
        return private_file(claim.document, claim.document_name)
