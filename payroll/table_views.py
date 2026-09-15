"""Database paging for overtime, without changing its calculation services."""

from django import forms
from django.db.models import Case, CharField, Count, Exists, F, FloatField, IntegerField, OuterRef, Q, Subquery, Sum, Value, When
from django.db.models.functions import Cast, Coalesce, Floor

from attendance.models import AttendanceRecord, AttendanceSession
from attendance.services import month_bounds, refresh
from base_template.tables import paginate
from common.forms import StyledFormMixin
from common.tenant import use_company
from employees.models import Employee
from payroll import overtime
from payroll.models import OvertimeDecision
from payroll.policy import rules_for


class OvertimeFilters(StyledFormMixin, forms.Form):
    employee = forms.ModelChoiceField(queryset=Employee.all_objects.none(), required=False, empty_label="All employees")
    branch = forms.ModelChoiceField(queryset=Employee.all_objects.none(), required=False, empty_label="All branches")

    def __init__(self, *args, employees, branches, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["employee"].queryset = employees
        self.fields["employee"].label_from_instance = lambda employee: employee.full_name
        self.fields["branch"].queryset = branches


def overtime_queryset(branches, first, last, rules):
    """Express Claim.exists/state_of in SQL so counts and slices stay in SQL.

    Finished day-off sessions supply the claim; other days use attendance's
    stored calculated minutes. A decision wins, then zero payable minutes,
    then an open/by-rule checkout, just as in overtime.state_of().
    """
    sessions = AttendanceSession.objects.filter(attendance_record=OuterRef("pk"))
    closed = sessions.filter(ended_at__isnull=False).order_by().values("attendance_record").annotate(minutes=Sum("worked_minutes"))
    decisions = OvertimeDecision.objects.filter(employee_id=OuterRef("employee_id"), work_date=OuterRef("work_date"))
    queryset = AttendanceRecord.objects.select_related("employee", "shift", "branch").prefetch_related("sessions").filter(
        work_date__range=(first, last), is_open=False, branch__in=branches,
    ).annotate(
        table_open=Exists(sessions.filter(ended_at__isnull=True, started_at__isnull=False)),
        table_minutes=Case(When(attendance_status__in=overtime.DAYS_OFF, then=Coalesce(Subquery(closed.values("minutes")[:1]), 0)),
                           default=F("calculated_overtime_minutes"), output_field=IntegerField()),
        table_decision=Subquery(decisions.values("status")[:1]),
        table_decided_minutes=Subquery(decisions.values("approved_minutes")[:1]),
        table_date=Cast("work_date", CharField()),
    ).filter(Q(table_minutes__gt=0) | Q(table_open=True))
    too_short = Q(table_open=False)
    if rules.pays_overtime:
        too_short &= Q(table_minutes__lt=max(1, rules.overtime_minimum, rules.overtime_step))
    queryset = queryset.annotate(table_state=Case(
        When(table_decision__isnull=False, then=F("table_decision")),
        When(too_short, then=Value(overtime.TOO_SHORT)),
        When(Q(table_open=True) | Q(check_out_by_rule=True), then=Value(overtime.WAITING)),
        default=Value(overtime.AUTOMATIC), output_field=CharField(),
    )).annotate(table_approved=Case(
        When(table_state=overtime.AUTOMATIC, then=F("table_minutes")),
        When(table_state=OvertimeDecision.Status.APPROVED, then=F("table_decided_minutes")),
        default=Value(0), output_field=IntegerField(),
    ))
    paid = F("table_approved")
    if rules.overtime_step:
        paid = Cast(Floor(Cast(paid, output_field=FloatField()) / rules.overtime_step) * rules.overtime_step, IntegerField())
    queryset = queryset.annotate(table_paid=Case(
        When(table_approved__lt=max(1, rules.overtime_minimum), then=Value(0)),
        default=paid if rules.pays_overtime else Value(0), output_field=IntegerField(),
    ))
    return queryset.order_by("work_date", "employee__first_name", "employee__last_name")


def overtime_table(request, *, company_id, year, month, states):
    scope = overtime.overtime_scope(request.user, company_id)
    first, last = month_bounds(year, month)
    refresh(company_id, start=first, end=last)
    rules = rules_for(company_id, first)
    with use_company(company_id):
        queryset = overtime_queryset(scope.branches, first, last, rules)
        form = OvertimeFilters(request.GET, employees=Employee.objects.filter(
            pk__in=queryset.values("employee_id")).order_by("first_name", "last_name"),
            branches=scope.branches.order_by("name"))
        if form.is_valid():
            if form.cleaned_data["employee"]:
                queryset = queryset.filter(employee=form.cleaned_data["employee"])
            if form.cleaned_data["branch"]:
                queryset = queryset.filter(branch=form.cleaned_data["branch"])
        else:
            queryset = queryset.none()
        counts = dict(queryset.order_by().values("table_state").annotate(n=Count("pk")).values_list("table_state", "n"))
        if states:
            queryset = queryset.filter(table_state__in=states)
        page = paginate(request, queryset,
            search=("employee__first_name", "employee__last_name", "branch__name", "table_state", "table_date"),
            order=("work_date", ("employee__first_name", "employee__last_name"), "scheduled_end_at", "table_minutes", "table_state", "table_paid", None))
        records = list(page)
        # Only fetch decisions for the displayed employees/dates.
        decisions = {(d.employee_id, d.work_date): d for d in OvertimeDecision.objects.select_related("decided_by").filter(
            employee_id__in={r.employee_id for r in records}, work_date__in={r.work_date for r in records})}
        rows = []
        for record in records:
            claim = overtime.claim_for(record)
            decision = decisions.get((record.employee_id, record.work_date))
            row = overtime.Row(record, claim, decision, record.table_state,
                               paid_minutes=rules.payable_overtime(record.table_approved))
            row.changed = decision is not None and claim.open_from is None and decision.calculated_minutes != claim.minutes
            row.may_decide = scope.may_decide(record.branch_id)
            rows.append(row)
    return {"membership": scope.membership, "company_wide": scope.company_wide,
            "rows": rows, "counts": counts, "rules": rules,
            "filter_form": form, "locked": overtime._is_locked(company_id, first)}
