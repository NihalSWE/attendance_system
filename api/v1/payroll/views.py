"""Salary: a month's salary (generate, submit, approve, send back, undo),
payslips (lines, corrections, PDF, email), penalties and overtime
(docs/api/00-PLAN.md phase 9; the guide: docs/api/90-salary.md).

The panel's Salary pages, through the same gate and services
(``payroll.services``, ``payroll.overtime``): who sees and prepares which
branch's pay is decided there, exactly as on the panel. HR sees no pay.
"""

from django.core.exceptions import PermissionDenied
from django.db.models import Count, Sum
from rest_framework.response import Response

from access_control.branch_access import ALL_BRANCHES, branches_for_any
from api.core.docs import PATH, QUERY, Param, endpoint
from api.core.errors import ApiError
from api.core.forms import checked, refuse, service_errors
from api.core.pagination import StandardPagination
from api.core.permissions import PanelRule
from api.core.views import ApiView
from api.v1.payroll import serializers as s
from attendance.services import month_bounds
from common.tenant import use_company
from organization.services import STRUCTURE_ROLES
from payroll import overtime, services
from payroll.models import (
    OvertimeDecision, PayrollAdjustment, PayrollRecord, PayrollRun, PenaltyAssessment,
)

AREA = "salary"
VIEWERS = ["The owner, company administrator or payroll manager; anyone given View or Prepare "
           "salary in a branch (a branch manager in theirs) - their branches' payslips"]
PREPARERS = ["The owner, company administrator or payroll manager; anyone given Prepare salary "
             "in a branch - for that branch"]
ADMINS = ["Company owner or administrator"]
OVERTIME_VIEWERS = ["The owner, company administrator or HR; anyone given View or Decide "
                    "overtime in a branch; a department head (view only)"]
OVERTIME_DECIDERS = ["The owner, company administrator or HR; anyone given Decide overtime in "
                     "the day's branch"]
ERRORS = ["not_authenticated", "invalid_token", "token_expired", "session_ended",
          "signature_required", "invalid_signature", "two_step_setup_required", "scope_missing",
          "permission_denied", "rate_limited", "server_error"]
WRITE_ERRORS = ERRORS + ["validation_error", "unknown_field"]
ONE = ["not_found"]
YEAR_PARAM = Param("year", PATH, "integer", "The year.", required=True, example=2026)
MONTH_PARAM = Param("month", PATH, "integer", "The month, 1-12.", required=True, example=10)
PAYSLIP_PARAM = Param("payslip_id", PATH, "integer", "The payslip id.", required=True,
                      example=501)
DAY_PARAM = Param("day_id", PATH, "integer", "The attendance day's id (from the overtime "
                                             "list).", required=True, example=9001)


def _ref(row):
    return {"id": row.pk, "name": getattr(row, "full_name", None) or row.name} if row else None


def _email(user):
    return user.email if user else None


def _month(year, month):
    if not (2000 <= year <= 2100 and 1 <= month <= 12):
        raise ApiError("not_found", "No such month.")
    return year, month


class SalaryView(ApiView):
    permission_classes = [PanelRule]
    company_required = True
    read_scope, write_scope = "payroll:read", "payroll:write"
    throttle_scope = "write"

    def page(self, request, rows, build, serializer):
        paginator = StandardPagination()
        chunk = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(serializer([build(r) for r in chunk],
                                                           many=True).data)


# --- salary by month ----------------------------------------------------------------------

def _scope(request):
    """The panel's Salary by month scope: ``(membership, view, prepare)``."""
    from payroll.views import _salary_scope

    membership, view, prepare, _wide = _salary_scope(request.user, request.company_id)
    return membership, view, prepare


def _run(company_id, year, month):
    first, last = month_bounds(year, month)
    with use_company(company_id):
        return (PayrollRun.objects.select_related("submitted_by", "posted_by")
                .filter(payroll_period__start_date=first, payroll_period__end_date=last)
                .order_by("-pk").first())


def _shown(run, view):
    rows = run.records.all() if run else PayrollRecord.objects.none()
    if view is not ALL_BRANCHES:
        rows = rows.filter(employee_assignment_at_period_end__branch_id__in=view)
    return rows


def _month_out(request, year, month):
    membership, view, prepare = _scope(request)
    company_id = request.company_id
    run = _run(company_id, year, month)
    first, last = month_bounds(year, month)
    with use_company(company_id):
        shown = _shown(run, view)
        totals = shown.aggregate(employees=Count("pk"), gross=Sum("gross_earnings"),
                                 deductions=Sum("total_deductions"), net=Sum("net_pay"))
        shown_ids = list(shown.values_list("employee_id", flat=True))
    if view is ALL_BRANCHES:
        decides = membership.role in overtime.OVERTIME_ROLES
        overtime_branches = ALL_BRANCHES
    else:
        overtime_branches = branches_for_any(request.user, company_id, "overtime.view",
                                             "overtime.decide")
        decides = bool(overtime_branches)
    status = run.status if run else None
    skipped = (run.totals_snapshot or {}).get("skipped_without_salary", []) if run else []
    return {
        "year": year, "month": month, "status": status,
        "generated_at": run.calculation_finished_at if run else None,
        "submitted_by": _email(run.submitted_by) if run else None,
        "submitted_at": run.submitted_at if run else None,
        "finalised_by": _email(run.posted_by) if run else None,
        "finalised_at": run.posted_at if run else None,
        "sent_back_reason": (run.return_reason or None) if run else None,
        "skipped": skipped if view is ALL_BRANCHES else [],
        "totals": totals,
        "may_generate": bool(prepare) and status in (None, PayrollRun.Status.DRAFT),
        "may_submit": bool(prepare) and status == PayrollRun.Status.DRAFT,
        "approval_blocked": services.approval_blocker(request.user, company_id, run),
        "attendance_changed": services.attendance_changed_since(company_id, run)
        if run and status != PayrollRun.Status.POSTED else 0,
        "overtime_decided_since": overtime.decided_after(
            company_id, run, None if view is ALL_BRANCHES else shown_ids)
        if run and status == PayrollRun.Status.DRAFT else 0,
        "overtime_waiting": overtime.undecided_count(company_id, first, last, overtime_branches)
        if decides else 0,
    }


MONTH_EXAMPLE = {"year": 2026, "month": 9, "status": "submitted",
                 "generated_at": "2026-10-01T10:00:00+06:00",
                 "submitted_by": "payroll@example.com",
                 "submitted_at": "2026-10-01T11:00:00+06:00", "finalised_by": None,
                 "finalised_at": None, "sent_back_reason": None, "skipped": [],
                 "totals": {"employees": 42, "gross": "1260000.00", "deductions": "18500.00",
                            "net": "1241500.00"},
                 "may_generate": False, "may_submit": False, "approval_blocked": None,
                 "attendance_changed": 0, "overtime_decided_since": 0, "overtime_waiting": 0}


class MonthView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_home"

    @endpoint(
        id="salary-month", area=AREA, title="A month's salary",
        summary="Where the month stands: draft, waiting for approval or finalised, and its "
                "totals.",
        what_it_does=["Answers the month's status, who did what, its totals over the payslips "
                      "you see, and what you may do next."],
        description=("Draft -> submit -> approve (finalised; employees see their payslips) -> "
                     "undo if needed. status is null until the month is generated. "
                     "attendance_changed > 0: a day was fixed after generating - generate "
                     "again before approving."),
        roles=VIEWERS, scopes=["payroll:read"], params=[YEAR_PARAM, MONTH_PARAM],
        response=s.PayrollMonthSerializer, response_example=MONTH_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, year, month):
        return Response(s.PayrollMonthSerializer(_month_out(request, *_month(year, month))).data)


def _row_out(record):
    snapshot = record.calculation_snapshot or {}
    assignment = record.employee_assignment_at_period_end
    rate = snapshot.get("base_rate")
    return {
        "id": record.pk, "employee": _ref(record.employee),
        "employee_code": assignment.employee_code if assignment else None,
        "branch": _ref(assignment.branch) if assignment and assignment.branch_id else None,
        "pay_basis": snapshot.get("pay_basis", ""), "base_rate": rate,
        "currency": record.currency, "gross": record.gross_earnings,
        "deductions": record.total_deductions, "net": record.net_pay,
        "counts": snapshot.get("counts", {}),
    }


ROW_EXAMPLE = {"id": 501, "employee": {"id": 41, "name": "Rahim Uddin"},
               "employee_code": "E-0041", "branch": {"id": 3, "name": "Chattogram"},
               "pay_basis": "monthly", "base_rate": "30000.00", "currency": "BDT",
               "gross": "30000.00", "deductions": "1000.00", "net": "29000.00",
               "counts": {"present": 21, "absent": 1, "late_minutes": 25}}


class PayslipListView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_home"

    @endpoint(
        id="salary-month-payslips", area=AREA, title="A month's payslips",
        summary="One row per person: pay basis, rate, gross, deductions, net, day counts.",
        what_it_does=["Lists the month's payslips you may see."],
        description="Empty until the month is generated. Open one with GET "
                    "/payroll/payslips/{id}.",
        roles=VIEWERS, scopes=["payroll:read"], paginated=True,
        params=[YEAR_PARAM, MONTH_PARAM,
                Param("employee_id", QUERY, "integer", "Only this person.", example=41)],
        response=s.PayslipRowSerializer,
        response_example={"count": 1, "next": None, "previous": None, "results": [ROW_EXAMPLE]},
        errors=ERRORS + ONE,
    )
    def get(self, request, year, month):
        _m, view, _p = _scope(request)
        run = _run(request.company_id, *_month(year, month))
        rows = _shown(run, view).select_related(
            "employee", "employee_assignment_at_period_end__branch")
        raw = request.query_params.get("employee_id", "")
        if raw.isdigit():
            rows = rows.filter(employee_id=int(raw))
        return self.page(request, rows.order_by("employee__first_name", "employee__last_name",
                                                "pk"), _row_out, s.PayslipRowSerializer)


class GenerateView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_generate"

    @endpoint(
        id="salary-month-generate", area=AREA, title="Generate a month's salary",
        summary="Work out everyone's payslip from attendance, leave, overtime and the rules.",
        what_it_does=["Builds the month as a draft (again, if it was a draft: safe to repeat).",
                      "A branch login generates only its branches' people; other payslips are "
                      "not touched."],
        description=("Attendance is brought up to date first. People without a salary are "
                     "skipped and named. Not while the month waits for approval or is "
                     "finalised."),
        roles=PREPARERS, scopes=["payroll:write"], params=[YEAR_PARAM, MONTH_PARAM],
        response=s.GeneratedSerializer,
        response_example={"month": {**MONTH_EXAMPLE, "status": "draft", "may_generate": True,
                                    "may_submit": True},
                          "generated": 42, "skipped": []},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, year, month):
        year, month = _month(year, month)
        _m, prepare = services.salary_branches(request.user, request.company_id,
                                               "salary.prepare")
        if not prepare:
            raise PermissionDenied("Generating salary needs owner or company administrator "
                                   "access, or access to prepare salary in a branch.")
        branch_ids = None if prepare is ALL_BRANCHES else prepare
        with service_errors():
            run = services.generate_payroll(actor=request.user, company_id=request.company_id,
                                            year=year, month=month, branch_ids=branch_ids)
        with use_company(request.company_id):
            mine = run.records.all()
            if branch_ids is not None:
                mine = mine.filter(employee_assignment_at_period_end__branch_id__in=branch_ids)
            count = mine.count()
        return Response(s.GeneratedSerializer({
            "month": _month_out(request, year, month), "generated": count,
            "skipped": run.skipped_now}).data)


def _month_action(view, request, year, month, service, form_reason=False):
    year, month = _month(year, month)
    extra = {}
    if form_reason:
        data = s.MonthReasonSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        extra["reason"] = data.validated_data["reason"]
    with service_errors():
        service(actor=request.user, company_id=request.company_id, year=year, month=month,
                **extra)
    return Response(s.PayrollMonthSerializer(_month_out(request, year, month)).data)


class SubmitView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_submit"

    @endpoint(
        id="salary-month-submit", area=AREA, title="Submit for approval",
        summary="The draft is ready: the owner or company administrator approves it.",
        what_it_does=["Moves the month to waiting for approval; it cannot be regenerated "
                      "until it is approved or sent back."],
        description="Whoever prepares salary submits; the owner or company administrator "
                    "approves.", roles=PREPARERS, scopes=["payroll:write"],
        params=[YEAR_PARAM, MONTH_PARAM], response=s.PayrollMonthSerializer,
        response_example=MONTH_EXAMPLE, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, year, month):
        if not services.salary_branches(request.user, request.company_id, "salary.prepare")[1]:
            raise PermissionDenied("Submitting salary needs access to prepare it.")
        return _month_action(self, request, year, month, services.submit_payroll)


class ApproveView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_finalise"

    @endpoint(
        id="salary-month-approve", area=AREA, title="Approve and finalise",
        summary="Finalise a submitted month: employees see their payslips.",
        what_it_does=["Finalises the month; its attendance, leave and overtime are locked."],
        description=("Refused while a day was fixed after the month was generated (generate "
                     "and submit again), or if the month is not waiting for approval."),
        roles=ADMINS, scopes=["payroll:write"], params=[YEAR_PARAM, MONTH_PARAM],
        response=s.PayrollMonthSerializer,
        response_example={**MONTH_EXAMPLE, "status": "posted",
                          "finalised_by": "owner@example.com",
                          "finalised_at": "2026-10-02T09:00:00+06:00",
                          "approval_blocked": "This month's salary is not waiting for "
                                              "approval."},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, year, month):
        from organization.services import require_structure_manager

        require_structure_manager(request.user, request.company_id)
        return _month_action(self, request, year, month, services.approve_payroll)


class SendBackView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_return"

    @endpoint(
        id="salary-month-send-back", area=AREA, title="Send back",
        summary="A submitted month needs changes: it is a draft again.",
        what_it_does=["Makes it a draft, with the reason shown until it is submitted again."],
        description="The owner or company administrator, or whoever submitted it.",
        roles=["Company owner or administrator, or whoever submitted it"],
        scopes=["payroll:write"], params=[YEAR_PARAM, MONTH_PARAM],
        request=s.MonthReasonSerializer, response=s.PayrollMonthSerializer,
        request_example={"reason": "Rahim's overtime is missing"},
        response_example={**MONTH_EXAMPLE, "status": "draft",
                          "sent_back_reason": "Rahim's overtime is missing"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, year, month):
        return _month_action(self, request, year, month, services.return_payroll, True)


class ReopenView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payroll_reopen"

    @endpoint(
        id="salary-month-reopen", area=AREA, title="Undo finalise",
        summary="Make a finalised month a draft again, to put something right.",
        what_it_does=["Makes it a draft; employees stop seeing these payslips until it is "
                      "finalised again."],
        description="Usually a correction in a later month is better (POST "
                    "/payroll/payslips/{id}/corrections): what was paid stays paid.",
        roles=ADMINS, scopes=["payroll:write"], params=[YEAR_PARAM, MONTH_PARAM],
        request=s.MonthReasonSerializer, response=s.PayrollMonthSerializer,
        request_example={"reason": "The wrong pay rate was used for Karim"},
        response_example={**MONTH_EXAMPLE, "status": "draft"}, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, year, month):
        from organization.services import require_structure_manager

        require_structure_manager(request.user, request.company_id)
        return _month_action(self, request, year, month, services.reopen_payroll, True)


# --- payslips -----------------------------------------------------------------------------

def _payslip(request, payslip_id, *codes):
    """``(membership, record, view, prepare)`` - the payslip, if in your branches."""
    from payroll.views import payslip_records

    membership, view = services.salary_branches(request.user, request.company_id,
                                                *(codes or ("salary.view", "salary.prepare")))
    _m, prepare = services.salary_branches(request.user, request.company_id, "salary.prepare")
    if not view:
        raise PermissionDenied("Payslips need owner or company administrator access, or salary "
                               "access in a branch.")
    with use_company(request.company_id):
        record = payslip_records().filter(pk=payslip_id).first()
    if record is None:
        raise ApiError("not_found", "No such payslip in this company.")
    if view is not ALL_BRANCHES and services.record_branch_id(record) not in view:
        raise PermissionDenied("Payslip not found in your branches.")
    return membership, record, view, prepare


def _adjustment_out(adjustment):
    paid_in = adjustment.target_payroll_period.name \
        if adjustment.source_payroll_period_id else None
    return {"id": adjustment.pk, "type": adjustment.adjustment_type, "amount": adjustment.amount,
            "reason": adjustment.reason, "paid_in": paid_in, "status": adjustment.status}


def _line_out(line):
    return {"type": line.line_type, "source": line.source_type, "code": line.code,
            "description": line.description, "quantity": line.quantity, "rate": line.rate,
            "amount": line.amount, "added_by_hand": line.is_manual}


def _payslip_out(request, payslip_id):
    from payroll.payslip_email import why_not
    from payroll.views import payslip_context

    membership, record, _view, prepare = _payslip(request, payslip_id)
    in_prepare = prepare is ALL_BRANCHES or (
        bool(prepare) and services.record_branch_id(record) in prepare)
    with use_company(request.company_id):
        context = payslip_context(record)
        period = context["period"]
        status = record.payroll_run.status
        adjustments = PayrollAdjustment.objects.select_related(
            "target_payroll_period").filter(
            employee=record.employee, target_payroll_period=period,
            status=PayrollAdjustment.Status.ACTIVE)
        corrections = list(services.corrections_for(request.company_id, record)) \
            if status == PayrollRun.Status.POSTED else []
        assignment = context["assignment"]
        out = {
            **_row_out(record),
            "period": period.name, "period_start": period.start_date,
            "period_end": period.end_date, "status": status,
            "department": assignment.department.name
            if assignment and assignment.department_id else None,
            "designation": assignment.designation.name
            if assignment and assignment.designation_id else None,
            "counts": dict(context["counts"]),
            "earnings": [_line_out(line) for line in context["earnings"]],
            "deduction_lines": [_line_out(line) for line in context["deductions"]],
            "penalties": [{
                "id": p.pk, "rule": p.penalty_rule.name, "period_start": p.period_start,
                "period_end": p.period_end, "occurrences": p.occurrence_count,
                "amount": p.deduction_amount, "status": p.status,
            } for p in context["penalties"]],
            "adjustments": [_adjustment_out(a) for a in adjustments],
            "corrections": [_adjustment_out(a) for a in corrections],
            "may_adjust": status == PayrollRun.Status.DRAFT and in_prepare,
            "may_correct": status == PayrollRun.Status.POSTED and in_prepare,
            "may_waive": context["can_waive"] and membership.role in STRUCTURE_ROLES,
            "may_email": in_prepare,
            "email_blocked": why_not(record, record.employee),
        }
    return out


PAYSLIP_EXAMPLE = {
    **ROW_EXAMPLE, "period": "October 2026", "period_start": "2026-10-01",
    "period_end": "2026-10-31", "status": "draft", "department": "Software",
    "designation": "Developer",
    "earnings": [{"type": "earning", "source": "basic", "code": "BASIC",
                  "description": "Basic salary", "quantity": None, "rate": None,
                  "amount": "30000.00", "added_by_hand": False}],
    "deduction_lines": [{"type": "deduction", "source": "absence", "code": "ABSENT",
                         "description": "Absent 1 day", "quantity": "1.0000",
                         "rate": "1000.0000", "amount": "1000.00", "added_by_hand": False}],
    "penalties": [], "adjustments": [], "corrections": [], "may_adjust": True,
    "may_correct": False, "may_waive": True, "may_email": True,
    "email_blocked": "Payslips are emailed once the month is finalised."}


class PayslipView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip"

    @endpoint(
        id="salary-payslips-one", area=AREA, title="One payslip",
        summary="Every line, the day counts, penalties, and lines added by hand.",
        what_it_does=["Answers the payslip and what you may do with it."],
        description="The day-by-day attendance behind it: GET /attendance/calendar.",
        roles=VIEWERS, scopes=["payroll:read"], params=[PAYSLIP_PARAM],
        response=s.PayslipSerializer, response_example=PAYSLIP_EXAMPLE, errors=ERRORS + ONE,
    )
    def get(self, request, payslip_id):
        return Response(s.PayslipSerializer(_payslip_out(request, payslip_id)).data)


class PayslipPdfView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip"

    @endpoint(
        id="salary-payslips-pdf", area=AREA, title="Download a payslip",
        summary="The payslip as a PDF, as printed from the panel.",
        what_it_does=["Answers the PDF file; the download is kept in the audit log."],
        description="Content-Type application/pdf.", roles=VIEWERS, scopes=["payroll:read"],
        params=[PAYSLIP_PARAM], errors=ERRORS + ONE,
    )
    def get(self, request, payslip_id):
        from payroll.payslip_export import export_payslip
        from payroll.views import payslip_context

        _m, record, _v, _p = _payslip(request, payslip_id)
        with use_company(request.company_id):
            context = payslip_context(record)
        return export_payslip(request, context)


class PayslipEmailView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip_email"

    @endpoint(
        id="salary-payslips-email", area=AREA, title="Email a payslip",
        summary="Send a finalised payslip, the PDF attached, from the company's mail account.",
        what_it_does=["Sends it to the employee's own address, or the one given."],
        description=("Needs the company's email settings, and a finalised month. Leave the "
                     "fields out for the standard wording. email_blocked on the payslip says "
                     "why it cannot be sent now."),
        roles=PREPARERS, scopes=["payroll:write"], params=[PAYSLIP_PARAM],
        request=s.EmailInputSerializer, response=s.EmailedSerializer,
        request_example={}, response_example={"sent_to": "rahim@example.com"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, payslip_id):
        from payroll.payslip_email import PayslipEmailForm, compose_initial, email_payslip
        from payroll.views import payslip_context

        membership, record, _v, prepare = _payslip(request, payslip_id, "salary.prepare")
        data = s.EmailInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with use_company(request.company_id):
            context = payslip_context(record)
        fields = {}
        if data.validated_data:
            initial = compose_initial(context, membership.company)
            form = checked(PayslipEmailForm, {**initial, **data.validated_data})
            fields = form.cleaned_data
        with service_errors(), use_company(request.company_id):
            address = email_payslip(actor=request.user, company_id=request.company_id,
                                    context=context, sent_by=request.user.get_username(),
                                    **fields)
        return Response(s.EmailedSerializer({"sent_to": address}).data)


def _line_input(request):
    data = s.PayslipLineInputSerializer(data=request.data)
    data.is_valid(raise_exception=True)
    values = data.validated_data
    if values["amount"] <= 0:
        refuse({"amount": ["Above zero."]})
    return {"adjustment_type": values["type"], "amount": values["amount"],
            "reason": values["reason"].strip()}


LINE_NAMES = {"adjustment_type": "type"}


class AdjustmentAddView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip_adjustment_add"

    @endpoint(
        id="salary-payslips-lines-add", area=AREA, title="Add a bonus or deduction",
        summary="A line by hand on a draft payslip - an Eid bonus, an advance recovery.",
        what_it_does=["Adds the line and generates the salary again with it (the whole month "
                      "for the owner or administrator, that branch for a branch login)."],
        description="Only while the month is a draft. For a finalised month use "
                    "…/corrections.",
        roles=PREPARERS, scopes=["payroll:write"], params=[PAYSLIP_PARAM], response_status=201,
        request=s.PayslipLineInputSerializer, response=s.PayslipSerializer,
        request_example={"type": "earning", "amount": "5000", "reason": "Eid bonus"},
        response_example=PAYSLIP_EXAMPLE, errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, payslip_id):
        _payslip(request, payslip_id)
        values = _line_input(request)
        with service_errors(LINE_NAMES):
            record = services.add_adjustment(actor=request.user, company_id=request.company_id,
                                             record_id=payslip_id, **values)
        return Response(s.PayslipSerializer(
            _payslip_out(request, record.pk if record else payslip_id)).data, status=201)


class CorrectionAddView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip_correction_add"

    @endpoint(
        id="salary-payslips-corrections-add", area=AREA, title="Correct a finalised month",
        summary="Put right a finalised payslip in the first month still open.",
        what_it_does=["Adds the bonus or deduction to that later month; the finalised month "
                      "stays as it was paid."],
        description="Only for a finalised month; whoever may prepare salary in its branch.",
        roles=PREPARERS, scopes=["payroll:write"], params=[PAYSLIP_PARAM],
        response_status=201, request=s.PayslipLineInputSerializer,
        response=s.PayslipCorrectionSerializer,
        request_example={"type": "earning", "amount": "1200",
                         "reason": "Overtime missed in September"},
        response_example={"correction": {"id": 77, "type": "earning", "amount": "1200.00",
                                         "reason": "Overtime missed in September",
                                         "paid_in": "October 2026", "status": "active"},
                          "payslip": {**PAYSLIP_EXAMPLE, "status": "posted"}},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, payslip_id):
        _payslip(request, payslip_id)
        values = _line_input(request)
        with service_errors(LINE_NAMES):
            adjustment = services.correct_finalised_month(
                actor=request.user, company_id=request.company_id, record_id=payslip_id,
                **values)
        with use_company(request.company_id):
            adjustment = PayrollAdjustment.objects.select_related(
                "target_payroll_period").get(pk=adjustment.pk)
        return Response(s.PayslipCorrectionSerializer({
            "correction": _adjustment_out(adjustment),
            "payslip": _payslip_out(request, payslip_id)}).data, status=201)


class AdjustmentRemoveView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:payslip_adjustment_remove"

    @endpoint(
        id="salary-adjustments-remove", area=AREA, title="Remove a line added by hand",
        summary="Take a bonus or deduction off a draft payslip.",
        what_it_does=["Removes it (kept as removed) and generates the salary again."],
        description="Only while its month is a draft.",
        roles=PREPARERS, scopes=["payroll:write"],
        params=[Param("adjustment_id", PATH, "integer", "The line's id (from the payslip's "
                                                        "adjustments).", required=True,
                      example=77)],
        response=s.PayslipSerializer, response_example=PAYSLIP_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def delete(self, request, adjustment_id):
        if not PayrollAdjustment.objects.filter(pk=adjustment_id).exists():
            raise ApiError("not_found", "No such line in this company.")
        with service_errors():
            record = services.remove_adjustment(actor=request.user,
                                                company_id=request.company_id,
                                                adjustment_id=adjustment_id)
        if record is None:
            return Response(status=204)
        return Response(s.PayslipSerializer(_payslip_out(request, record.pk)).data)


# --- penalties ----------------------------------------------------------------------------

def _penalty_action(request, penalty_id, service):
    if not PenaltyAssessment.objects.filter(pk=penalty_id).exists():
        raise ApiError("not_found", "No such penalty in this company.")
    with service_errors():
        record = service(actor=request.user, company_id=request.company_id,
                         assessment_id=penalty_id)
    if record is None:
        return Response(status=204)
    return Response(s.PayslipSerializer(_payslip_out(request, record.pk)).data)


PENALTY_PARAM = Param("penalty_id", PATH, "integer", "The penalty id (from the payslip).",
                      required=True, example=12)


class PenaltyWaiveView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:penalty_waive"

    @endpoint(
        id="salary-penalties-waive", area=AREA, title="Waive a penalty",
        summary="Do not charge this penalty; the salary is generated again without it.",
        what_it_does=["Waives it (kept across regenerations) and answers the payslip."],
        description="Only on a draft month. No content (204) if the person has no payslip "
                    "after regenerating.",
        roles=ADMINS, scopes=["payroll:write"], params=[PENALTY_PARAM],
        response=s.PayslipSerializer, response_example=PAYSLIP_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, penalty_id):
        return _penalty_action(request, penalty_id, services.waive_penalty)


class PenaltyUnwaiveView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:penalty_unwaive"

    @endpoint(
        id="salary-penalties-unwaive", area=AREA, title="Undo a waiver",
        summary="Charge the penalty again; the salary is generated again with it.",
        what_it_does=["Undoes the waiver and answers the payslip."],
        description="Only while the month is a draft.",
        roles=ADMINS, scopes=["payroll:write"], params=[PENALTY_PARAM],
        response=s.PayslipSerializer, response_example=PAYSLIP_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, penalty_id):
        return _penalty_action(request, penalty_id, services.unwaive_penalty)


# --- overtime -----------------------------------------------------------------------------

OVERTIME_SHOW = {"waiting": (overtime.WAITING,), "approved": overtime.APPROVED_STATES,
                 "rejected": (OvertimeDecision.Status.REJECTED,),
                 "too_short": (overtime.TOO_SHORT,)}


def _overtime_out(record, claim, decision, state, approved, paid, may_decide):
    return {
        "id": record.pk, "date": record.work_date, "employee": _ref(record.employee),
        "branch": _ref(record.branch) if record.branch_id else None, "day_off": claim.day_off,
        "minutes": claim.minutes, "open_from": claim.open_from, "state": state,
        "approved_minutes": approved, "paid_minutes": paid,
        "decided_by": _email(decision.decided_by) if decision else None,
        "note": decision.note if decision else "",
        "changed_since": decision is not None and claim.open_from is None
        and decision.calculated_minutes != claim.minutes,
        "may_decide": may_decide,
    }


OVERTIME_EXAMPLE = {"id": 9001, "date": "2026-10-05", "employee": {"id": 41,
                                                                   "name": "Rahim Uddin"},
                    "branch": {"id": 3, "name": "Chattogram"}, "day_off": False,
                    "minutes": 95, "open_from": None, "state": "automatic",
                    "approved_minutes": 95, "paid_minutes": 90, "decided_by": None, "note": "",
                    "changed_since": False, "may_decide": True}


class OvertimeListView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:overtime_list"

    @endpoint(
        id="salary-overtime", area=AREA, title="A month's overtime",
        summary="Every day with overtime: paid automatically, waiting for you, approved, "
                "rejected, or too short to pay.",
        what_it_does=["Brings the month's attendance up to date, then lists its overtime days "
                      "in your branches."],
        description=("A day that ends in a real scan is approved automatically. A day nobody "
                     "scanned out of waits for a decision. show: all (default), waiting, "
                     "approved, rejected, too_short."),
        roles=OVERTIME_VIEWERS, scopes=["payroll:read"], paginated=True,
        params=[Param("year", QUERY, "integer", "The year (this year by default).", example=2026),
                Param("month", QUERY, "integer", "The month (this month by default).",
                      example=10),
                Param("show", QUERY, "string", "all, waiting, approved, rejected or too_short.",
                      example="waiting"),
                Param("employee_id", QUERY, "integer", "Only this person.", example=41),
                Param("branch_id", QUERY, "integer", "Only this branch.", example=3)],
        response=s.OvertimeDaySerializer,
        response_example={"count": 1, "next": None, "previous": None,
                          "results": [OVERTIME_EXAMPLE]},
        errors=ERRORS,
    )
    def get(self, request):
        from attendance.services import refresh
        from attendance.views import read_month
        from payroll.policy import rules_for
        from payroll.table_views import overtime_queryset

        scope = overtime.overtime_scope(request.user, request.company_id)
        year, month = read_month(request.query_params)
        first, last = month_bounds(year, month)
        refresh(request.company_id, start=first, end=last)
        rules = rules_for(request.company_id, first)
        rows = overtime_queryset(scope.branches, first, last, rules, scope.departments)
        q = request.query_params
        if q.get("employee_id", "").isdigit():
            rows = rows.filter(employee_id=int(q["employee_id"]))
        if q.get("branch_id", "").isdigit():
            rows = rows.filter(branch_id=int(q["branch_id"]))
        if q.get("show") in OVERTIME_SHOW:
            rows = rows.filter(table_state__in=OVERTIME_SHOW[q["show"]])
        paginator = StandardPagination()
        chunk = list(paginator.paginate_queryset(rows, request, view=self))
        decisions = {(d.employee_id, d.work_date): d for d in
                     OvertimeDecision.objects.select_related("decided_by").filter(
                         employee_id__in={r.employee_id for r in chunk},
                         work_date__in={r.work_date for r in chunk})}
        out = []
        for record in chunk:
            claim = overtime.claim_for(record)
            out.append(_overtime_out(
                record, claim, decisions.get((record.employee_id, record.work_date)),
                record.table_state, record.table_approved,
                rules.payable_overtime(record.table_approved),
                scope.may_decide(record.branch_id)))
        return paginator.get_paginated_response(s.OvertimeDaySerializer(out, many=True).data)


def _overtime_day(request, day_id):
    from attendance.models import AttendanceRecord

    if not AttendanceRecord.objects.filter(pk=day_id).exists():
        raise ApiError("not_found", "No such day in this company.")
    page = overtime.overtime_day(actor=request.user, company_id=request.company_id,
                                 record_id=day_id)
    record, claim, decision, state = (page["record"], page["claim"], page["decision"],
                                      page["state"])
    if state == overtime.AUTOMATIC:
        approved = claim.minutes
    elif state == OvertimeDecision.Status.APPROVED:
        approved = decision.approved_minutes
    else:
        approved = 0
    return page, _overtime_out(record, claim, decision, state, approved,
                               page["rules"].payable_overtime(approved), page["may_decide"])


class OvertimeDayView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:overtime_decide"

    @endpoint(
        id="salary-overtime-one", area=AREA, title="One day's overtime",
        summary="A day's overtime, its state and whether you may decide it.",
        what_it_does=["Brings the day up to date and answers it."],
        description="The day's scans: GET /attendance/days/{employee_id}/{date}.",
        roles=OVERTIME_VIEWERS, scopes=["payroll:read"], params=[DAY_PARAM],
        response=s.OvertimeDaySerializer, response_example=OVERTIME_EXAMPLE,
        errors=ERRORS + ONE,
    )
    def get(self, request, day_id):
        return Response(s.OvertimeDaySerializer(_overtime_day(request, day_id)[1]).data)


class OvertimeDecideView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:overtime_decide"

    @endpoint(
        id="salary-overtime-decide", area=AREA, title="Approve or reject overtime",
        summary="Approve all or some of a day's overtime, or reject it.",
        what_it_does=["Records the decision; the day is worked out again with it. Generate a "
                      "draft month again to include it."],
        description=("minutes: approve fewer than counted. A day nobody scanned out of "
                     "(open_from set) needs check_out instead - when they left. Not in a "
                     "finalised month."),
        roles=OVERTIME_DECIDERS, scopes=["payroll:write"], params=[DAY_PARAM],
        request=s.OvertimeDecideInputSerializer, response=s.OvertimeDaySerializer,
        request_example={"decision": "approve", "minutes": 60, "note": "Stock count"},
        response_example={**OVERTIME_EXAMPLE, "state": "approved", "approved_minutes": 60,
                          "paid_minutes": 60, "decided_by": "hr@example.com",
                          "note": "Stock count"},
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, day_id):
        from payroll.forms import OvertimeDecisionForm

        page, _out = _overtime_day(request, day_id)
        if not page["may_decide"]:
            raise PermissionDenied("You may view this overtime, not decide it.")
        data = s.OvertimeDecideInputSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        values = data.validated_data
        form_input = {"note": values.get("note", "")}
        if values.get("minutes") is not None:
            form_input["minutes"] = values["minutes"]
        if values.get("check_out"):
            form_input["check_out"] = values["check_out"]
        form = checked(OvertimeDecisionForm, form_input, claim=page["claim"])
        with service_errors():
            overtime.decide_overtime(
                actor=request.user, company_id=request.company_id, record_id=page["record"].pk,
                approve=values["decision"] == "approve",
                minutes=form.cleaned_data.get("minutes"),
                check_out=form.cleaned_data.get("check_out"),
                note=form.cleaned_data.get("note", ""))
        return Response(s.OvertimeDaySerializer(_overtime_day(request, day_id)[1]).data)


class OvertimeUndoView(SalaryView):
    permission_classes = [PanelRule]
    panel_page = "payroll:overtime_undo"

    @endpoint(
        id="salary-overtime-undo", area=AREA, title="Undo an overtime decision",
        summary="The day goes back to paid automatically, or to waiting.",
        what_it_does=["Drops the decision; the day is worked out again."],
        description="Not in a finalised month.",
        roles=OVERTIME_DECIDERS, scopes=["payroll:write"], params=[DAY_PARAM],
        response=s.OvertimeDaySerializer, response_example=OVERTIME_EXAMPLE,
        errors=WRITE_ERRORS + ONE,
    )
    def post(self, request, day_id):
        _overtime_day(request, day_id)
        with service_errors():
            overtime.undo_overtime_decision(actor=request.user, company_id=request.company_id,
                                            record_id=day_id)
        return Response(s.OvertimeDaySerializer(_overtime_day(request, day_id)[1]).data)
