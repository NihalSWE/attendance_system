"""Audited payable overtime totals for a salary cycle; attendance stays intact."""

from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from auditlog.services import record_company_event
from common.exceptions import NotInThisCompany
from common.tenant import use_company
from payroll.models import GenerateJob, OvertimeOverride, PayrollRecord, PayrollRun
from payroll.policy import rules_for


def record_for(actor, company_id, record_id):
    from payroll.services import _preparer

    with use_company(company_id):
        record = PayrollRecord.objects.select_related(
            "employee", "payroll_run__payroll_period", "payroll_run__pay_group",
            "employee_assignment_at_period_end").filter(pk=record_id).first()
        if record is None:
            raise NotInThisCompany("Payslip not found in this company.")
        _preparer(actor, company_id, record)
        return record


def active(company_id, period, pay_group, employee_ids=None):
    with use_company(company_id):
        rows = OvertimeOverride.objects.select_related("created_by").filter(
            payroll_period=period, pay_group=pay_group, status=OvertimeOverride.Status.ACTIVE)
        if employee_ids is not None:
            rows = rows.filter(employee_id__in=employee_ids)
        return {row.employee_id: row for row in rows}


def history(company_id, record):
    with use_company(company_id):
        return list(OvertimeOverride.objects.select_related("created_by").filter(
            employee_id=record.employee_id, payroll_period=record.payroll_run.payroll_period,
            pay_group=record.payroll_run.pay_group))


def describe(row):
    if row is None:
        return None
    return {"id": row.pk, "hours": str(row.hours), "day_off_hours": str(row.day_off_hours),
            "reason": row.reason, "by": str(row.created_by) if row.created_by else "",
            "at": row.created_at.isoformat()}


def _hours(value, field, limit, step):
    try:
        value = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError({field: "Enter payable hours as a number."}) from None
    if not value.is_finite() or value < 0 or value > limit:
        raise ValidationError({field: f"Enter hours between 0 and {limit}."})
    if value != value.quantize(Decimal("0.01")):
        raise ValidationError({field: "Use at most two decimal places."})
    if step and value * 60 % step:
        raise ValidationError({field: f"Enter payable hours in {step}-minute blocks, as set for this cycle."})
    return value


def validate_for_generation(row, employee, records, rules, last):
    """Preflight before generation replaces existing draft payslips."""
    if not row.hours and not row.day_off_hours:
        return
    if not rules.pays_overtime or (employee.no_overtime_from and employee.no_overtime_from <= last):
        raise ValidationError("Overtime is now disallowed for an employee with manual cycle hours. "
                              "Return their overtime to attendance before regenerating.")
    if not any(r.attendance_status != "inactive" for r in records):
        raise ValidationError("An employee with manual overtime has no eligible shift days. "
                              "Return their overtime to attendance before regenerating.")
    if rules.overtime_step and any(hours * 60 % rules.overtime_step for hours in (row.hours, row.day_off_hours)):
        raise ValidationError("Manual overtime hours no longer match the cycle's blocks. Update the totals first.")


@transaction.atomic
def save(*, actor, company_id, record_id, hours=None, day_off_hours=0, reason="", revert=False):
    from organization.services import require_company_membership

    record = record_for(actor, company_id, record_id)
    membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        run = PayrollRun.objects.select_for_update().get(pk=record.payroll_run_id)
        if run.status != PayrollRun.Status.DRAFT:
            raise ValidationError("Overtime hours can only change while this salary cycle is a draft.")
        period = run.payroll_period
        if GenerateJob.objects.filter(status=GenerateJob.Status.RUNNING, pay_group=run.pay_group,
                                      year=period.end_date.year, month=period.end_date.month).exists():
            raise ValidationError("Salary is being generated. Wait until it finishes before changing overtime.")
        reason = (reason or "").strip()
        if not reason or len(reason) > 255:
            raise ValidationError({"reason": "Give a reason of 1 to 255 characters."})
        before = active(company_id, period, run.pay_group, [record.employee_id]).get(record.employee_id)
        values = {}
        if revert:
            if before is None:
                raise ValidationError("This cycle already uses attendance overtime.")
        else:
            rules = rules_for(company_id, period.start_date)
            if not rules.pays_overtime:
                raise ValidationError("Overtime pay is disabled for this salary cycle.")
            if record.employee.no_overtime_from and record.employee.no_overtime_from <= period.end_date:
                raise ValidationError("Overtime is disallowed for this employee during this cycle. Use daily decisions for earlier eligible days.")
            limit = Decimal((period.end_date - period.start_date).days + 1) * 24
            values = {"hours": _hours(hours, "hours", limit, rules.overtime_step),
                      "day_off_hours": _hours(day_off_hours, "day_off_hours", limit, rules.overtime_step)}
            if sum(values.values()) > limit:
                raise ValidationError("Total overtime cannot exceed the hours in this salary cycle.")
        before_snapshot = describe(before)
        if before is not None:
            before.status = OvertimeOverride.Status.REVERTED if revert else OvertimeOverride.Status.REPLACED
            before.ended_by, before.ended_at, before.updated_by = actor, timezone.now(), actor
            before.save(update_fields=["status", "ended_by", "ended_at", "updated_by", "updated_at"])
        row = before if revert else OvertimeOverride.objects.create(
            company_id=company_id, employee_id=record.employee_id, payroll_period=period,
            pay_group=run.pay_group, reason=reason, created_by=actor, updated_by=actor, **values)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="payroll.overtime_override.reverted" if revert else "payroll.overtime_override.set",
                             obj=row, before=before_snapshot or {},
                             after={"reason": reason, "source": "attendance"} if revert else describe(row))
        return row


def changed_since(company_id, run):
    """Includes reverting a saved total: the generated snapshot must match."""
    if run is None:
        return 0
    with use_company(company_id):
        stored = list(run.records.values_list("employee_id", "calculation_snapshot"))
    current = active(company_id, run.payroll_period, run.pay_group, [pk for pk, _ in stored])
    return sum((snapshot.get("overtime_override") or {}).get("id") != getattr(current.get(pk), "pk", None)
               for pk, snapshot in stored)


def rules_changed_since(company_id, run):
    if run is None:
        return 0
    current = rules_for(company_id, run.payroll_period.start_date).describe()["overtime"]
    with use_company(company_id):
        snapshots = list(run.records.values_list("calculation_snapshot", flat=True))
    changed = 0
    for snapshot in snapshots:
        old = dict(snapshot.get("rules", {}).get("overtime", {}))
        # Older versions always dropped the remainder.
        old.setdefault("block_rule", "completed_blocks")
        changed += old != current
    return changed


def require_current(company_id, run):
    if changed_since(company_id, run) or rules_changed_since(company_id, run):
        raise ValidationError("Overtime totals or rules changed since this salary was generated. "
                              "Regenerate this cycle before submitting or approving it; send it back first if necessary.")
