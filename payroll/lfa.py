"""Leave Fare Assistance (Nihal, 2026-09-28).

Each company sets its own rules (``LfaSettings``): how much (a fixed amount,
a share of monthly basic, or what the approver decides, with an optional
cap), after how many months of service, whether probation counts, how often
(a calendar or service year, and how many claims in it), whether it needs
leave taken with it (and which types, how many days), whether it needs proof
attached, whether the first year is prorated, and whether it is paid on the
payslip or separately. Nothing assumes a trip: with no leave and no proof
required, an eligible employee claims it once a cycle and is paid.

The flow: the employee claims it from their own panel (or someone who
decides claims enters it for them) -> an approver decides it and the amount
-> paid on the payslip (an "LFA" line in the month chosen, via
``PayrollAdjustment``; paid once that month is finalised) or separately
(marked paid with a date and reference). A claim keeps a copy of the rules it
was made under, so changing the rules never changes a claim already made.

Who decides: the owner or company admin, or whoever prepares salary in the
employee's branch - never their own claim. Who sees claims and amounts:
whoever sees salaries there.
"""

import datetime
import uuid
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Q, Sum
from django.utils import timezone

from access_control.branch_access import ALL_BRANCHES, branches_for, can
from attendance.services import month_bounds
from auditlog.services import record_company_event
from common.tenant import use_company
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from organization.services import require_company_membership, require_structure_manager
from payroll.models import (
    LfaClaim,
    LfaSettings,
    PayrollAdjustment,
    PayrollPeriod,
    PayrollRecord,
    PayrollRun,
)

Status = LfaClaim.Status
Method = LfaSettings.AmountMethod
Cycle = LfaSettings.Cycle
Payment = LfaSettings.Payment
LIVE = (Status.PENDING, Status.APPROVED, Status.PAID)
CENT = Decimal("0.01")
SETTINGS_FIELDS = (
    "enabled", "name", "description", "amount_method", "fixed_amount", "basic_percent",
    "max_amount", "min_service_months", "probation_eligible", "cycle", "claims_per_cycle",
    "requires_leave", "min_leave_days", "requires_document", "prorate_first_cycle", "payment",
)


def _money(value):
    return Decimal(value).quantize(CENT, ROUND_HALF_UP)


def _add_months(day, months):
    import calendar

    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month,
                       day=min(day.day, calendar.monthrange(year, month)[1]))


# --------------------------------------------------------------------------
# The company's rules
# --------------------------------------------------------------------------


def settings_for(company_id):
    """The company's rules; unsaved defaults (switched off) when it has none."""
    with use_company(company_id):
        found = LfaSettings.objects.prefetch_related("leave_types").first()
    if found is None:
        found = LfaSettings(company_id=company_id)
    return found


def snapshot(settings):
    data = {name: str(getattr(settings, name)) if isinstance(getattr(settings, name), Decimal)
            else getattr(settings, name) for name in SETTINGS_FIELDS}
    data["leave_types"] = ([t.code for t in settings.leave_types.all()] if settings.pk else [])
    return data


def _check_settings(values):
    method = values.get("amount_method")
    if method == Method.FIXED and not values.get("fixed_amount"):
        raise ValidationError({"fixed_amount": "Give the amount."})
    if method == Method.BASIC_PERCENT and not values.get("basic_percent"):
        raise ValidationError({"basic_percent": "Give the share of basic, e.g. 100 for one "
                                                "month's basic."})
    if method == Method.APPROVER and not values.get("max_amount"):
        raise ValidationError({"max_amount": "When the approver decides, give the most they "
                                             "may approve."})
    if not values.get("claims_per_cycle"):
        raise ValidationError({"claims_per_cycle": "At least one."})


@transaction.atomic
def save_settings(*, actor, company_id, values, leave_types=()):
    """The owner or company admin sets the rules and switches LFA on or off."""
    membership = require_structure_manager(actor, company_id)
    unknown = set(values) - set(SETTINGS_FIELDS)
    if unknown:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unknown))}")
    _check_settings(values)
    with use_company(company_id):
        settings = LfaSettings.objects.select_for_update().first()
        before = snapshot(settings) if settings else {}
        if settings is None:
            settings = LfaSettings(created_by=actor)
            settings.company_id = company_id
        for name in SETTINGS_FIELDS:
            setattr(settings, name, values.get(name))
        if not settings.requires_leave:
            settings.min_leave_days = None
        settings.updated_by = actor
        settings.full_clean()
        settings.save()
        settings.leave_types.set(list(leave_types) if settings.requires_leave else [])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="lfa.settings_saved", obj=settings, before=before,
                             after=snapshot(settings))
    return settings


# --------------------------------------------------------------------------
# Eligibility and amount
# --------------------------------------------------------------------------


def cycle_for(settings, employee, on):
    """``(first day, last day)`` of the cycle ``on`` falls in."""
    if settings.cycle == Cycle.SERVICE_YEAR and employee.joining_date:
        joined = employee.joining_date
        years = on.year - joined.year - (1 if (on.month, on.day) < (joined.month, joined.day) else 0)
        start = _add_months(joined, 12 * max(years, 0))
        return start, _add_months(start, 12) - datetime.timedelta(days=1)
    return datetime.date(on.year, 1, 1), datetime.date(on.year, 12, 31)


def service_months(employee, on):
    joined = employee.joining_date
    if joined is None or joined > on:
        return 0
    months = (on.year - joined.year) * 12 + on.month - joined.month
    return months - (1 if on.day < joined.day else 0)


def _compensation(employee, on):
    at = timezone.make_aware(datetime.datetime.combine(on, datetime.time(12)))
    return (EmployeeCompensation.objects.filter(employee=employee, effective_from__lte=at)
            .exclude(status__in=["cancelled", "draft"])
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
            .order_by("-effective_from").first())


def amount_for(settings, employee, on, cycle):
    """``(amount or None, currency, how)``: None means the approver decides."""
    compensation = _compensation(employee, on)
    currency = compensation.currency if compensation else ""
    amount, how = None, ""
    if settings.amount_method == Method.FIXED:
        amount, how = settings.fixed_amount, "fixed amount"
    elif settings.amount_method == Method.BASIC_PERCENT:
        if compensation is None:
            how = "no salary set - the approver decides"
        elif compensation.pay_basis != EmployeeCompensation.PayBasis.MONTHLY:
            how = "paid by the day or hour - the approver decides"
        else:
            amount = compensation.base_rate * settings.basic_percent / 100
            how = f"{settings.basic_percent.normalize():f}% of monthly basic"
    else:
        how = "decided by the approver"
    if amount is not None and settings.prorate_first_cycle and employee.joining_date \
            and employee.joining_date > cycle[0]:
        months = service_months(employee, cycle[1] + datetime.timedelta(days=1))
        months = max(0, min(12, months))
        amount = amount * months / 12
        how += f", {months} of 12 months"
    if amount is not None and settings.max_amount:
        amount = min(amount, settings.max_amount)
    return (_money(amount) if amount is not None else None), currency, how


@dataclass
class Eligibility:
    ok: bool
    reasons: list = field(default_factory=list)
    cycle: tuple = None
    used: int = 0
    amount: Decimal = None
    currency: str = ""
    how: str = ""
    leave_requests: list = field(default_factory=list)


def _qualifying_leave(settings, employee, cycle):
    """Approved leave in the cycle that meets the rules and no live claim uses."""
    from leaves.models import LeaveDay, LeaveRequest

    types = [t.pk for t in settings.leave_types.all()] if settings.pk else []
    used = set(LfaClaim.objects.filter(employee=employee, status__in=LIVE,
                                       leave_request__isnull=False)
               .values_list("leave_request_id", flat=True))
    found = []
    for request in (LeaveRequest.objects.filter(employee=employee, status__in=(
            "approved", "partially_cancelled")).prefetch_related("segments__leave_type")
            .order_by("-pk")):
        if request.pk in used:
            continue
        days = LeaveDay.objects.filter(request_segment__leave_request=request,
                                       status__in=("reserved", "approved", "consumed"))
        if types:
            days = days.filter(request_segment__leave_type_id__in=types)
        days = days.filter(work_date__gte=cycle[0], work_date__lte=cycle[1])
        units = days.aggregate(total=Sum("balance_units"))["total"] or Decimal("0")
        if not units or (settings.min_leave_days and units < settings.min_leave_days):
            continue
        request.lfa_units = units
        found.append(request)
    return found


def eligibility(employee, settings, on=None):
    """Whether ``employee`` may claim LFA now, and why not. Call inside the
    company's tenant context."""
    on = on or timezone.localdate()
    result = Eligibility(ok=False)
    if not settings.pk or not settings.enabled:
        result.reasons.append("LFA is not switched on for this company.")
        return result
    allowed = {"active"} | ({"probation"} if settings.probation_eligible else set())
    if employee.employment_status not in allowed:
        result.reasons.append(f"Only {' or '.join(sorted(allowed))} employees can claim it.")
    if employee.joining_date is None:
        result.reasons.append("Their joining date is not recorded.")
    elif service_months(employee, on) < settings.min_service_months:
        result.reasons.append(
            f"It needs {settings.min_service_months} months of service; "
            f"they have {service_months(employee, on)}.")
    result.cycle = cycle_for(settings, employee, on)
    result.used = LfaClaim.objects.filter(employee=employee, status__in=LIVE,
                                          cycle_start=result.cycle[0]).count()
    if result.used >= settings.claims_per_cycle:
        result.reasons.append(
            f"Already claimed {result.used} of {settings.claims_per_cycle} time(s) "
            f"from {result.cycle[0]:%d %b %Y} to {result.cycle[1]:%d %b %Y}.")
    if settings.requires_leave:
        result.leave_requests = _qualifying_leave(settings, employee, result.cycle)
        if not result.leave_requests:
            needed = (f"at least {settings.min_leave_days.normalize():f} days of "
                      if settings.min_leave_days else "")
            types = ", ".join(t.name for t in settings.leave_types.all())
            result.reasons.append(f"It needs {needed}approved leave{' (' + types + ')' if types else ''} "
                                  "in this period, not used for another claim.")
    result.amount, result.currency, result.how = amount_for(settings, employee, on, result.cycle)
    result.ok = not result.reasons
    return result


# --------------------------------------------------------------------------
# Claiming
# --------------------------------------------------------------------------


def _deciders(actor, company_id, employee):
    """True when ``actor`` may decide (or enter) this employee's claims."""
    membership = require_company_membership(actor, company_id)
    if membership.role in ("owner", "company_admin"):
        return membership, True
    with use_company(company_id):
        placed = (EmployeeAssignment.objects.filter(employee=employee, effective_to__isnull=True)
                  .exclude(status__in=["cancelled", "draft"]).first())
    return membership, bool(placed and can(actor, company_id, "salary.prepare", placed.branch_id))


def _store(claim, upload):
    if not upload:
        return
    extension = {"jpeg": "jpg"}.get(upload.kind, upload.kind)
    claim.document.save(f"{uuid.uuid4().hex}.{extension}", ContentFile(upload.read()), save=False)
    claim.document_name = upload.name[:255]


@transaction.atomic
def submit(*, actor, company_id, employee=None, values, document=None, today=None):
    """A claim - by the employee for themselves (``employee`` None), or by
    someone who decides claims, for them."""
    today = today or timezone.localdate()
    membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        if employee is None:
            employee = Employee.objects.select_for_update().filter(user=actor).first()
            if employee is None:
                raise PermissionDenied("This login is not linked to an employee record.")
            on_behalf = False
        else:
            employee = Employee.objects.select_for_update().get(pk=employee.pk)
            on_behalf = employee.user_id != actor.pk
            if on_behalf and not _deciders(actor, company_id, employee)[1]:
                raise PermissionDenied("Claims for someone else are entered by whoever "
                                       "decides them.")
        settings = settings_for(company_id)
        found = eligibility(employee, settings, today)
        if not found.ok:
            raise ValidationError(" ".join(found.reasons))
        leave = values.get("leave_request")
        if settings.requires_leave:
            if leave is None or leave.pk not in {r.pk for r in found.leave_requests}:
                raise ValidationError({"leave_request": "Choose the approved leave it goes with."})
        else:
            leave = None
        if settings.requires_document and not document:
            raise ValidationError({"document": "Attach the proof - a ticket or a receipt."})
        claim = LfaClaim(
            employee=employee, cycle_start=found.cycle[0], cycle_end=found.cycle[1],
            leave_request=leave, note=(values.get("note") or "").strip(),
            calculated_amount=found.amount, currency=found.currency,
            payment=settings.payment, submitted_by=actor,
            settings_snapshot={**snapshot(settings), "how": found.how},
            created_by=actor, updated_by=actor,
        )
        claim.company_id = company_id
        _store(claim, document)
        claim.full_clean()
        claim.save()
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="lfa.claimed", obj=claim,
                             after={"employee_id": employee.pk, "for_them": on_behalf,
                                    "amount": str(found.amount) if found.amount else None,
                                    "cycle": f"{found.cycle[0]}..{found.cycle[1]}"})
    return claim


@transaction.atomic
def withdraw(*, actor, company_id, claim_id):
    membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        claim = LfaClaim.objects.select_for_update().filter(
            pk=claim_id, employee__user=actor).first()
        if claim is None:
            raise PermissionDenied("That claim is not yours.")
        if claim.status != Status.PENDING:
            raise ValidationError("Only a claim waiting for approval can be withdrawn.")
        claim.status = Status.WITHDRAWN
        claim.updated_by = actor
        claim.save(update_fields=["status", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="lfa.withdrawn", obj=claim, after={"status": "withdrawn"})
    return claim


def _pay_period(company_id, first_of_month):
    """The payroll month the money goes into: that one, or the first after it
    not finalised. A month waiting for approval is refused, not skipped."""
    start = first_of_month
    with use_company(company_id):
        for _ in range(24):
            first, last = month_bounds(start.year, start.month)
            period, _made = PayrollPeriod.objects.get_or_create(
                company_id=company_id, start_date=first, end_date=last,
                defaults={"name": first.strftime("%B %Y")})
            runs = PayrollRun.objects.filter(payroll_period=period)
            if runs.filter(status=PayrollRun.Status.SUBMITTED).exists():
                raise ValidationError({"pay_month": (
                    f"{period.name}'s salary is waiting for approval. Choose a later month, "
                    "or send it back first.")})
            if not runs.filter(status=PayrollRun.Status.POSTED).exists():
                return period
            start = last + datetime.timedelta(days=1)
    raise ValidationError({"pay_month": "No open salary month in the next two years."})


def _refresh_payslip(actor, company_id, period, employee):
    """A draft payslip already made for that month picks up the change now."""
    from payroll.services import generate_payroll, record_branch_id

    with use_company(company_id):
        run = PayrollRun.objects.filter(payroll_period=period,
                                        status=PayrollRun.Status.DRAFT).first()
        record = (PayrollRecord.objects.select_related("employee_assignment_at_period_end")
                  .filter(payroll_run=run, employee=employee).first() if run else None)
    if record is None:
        return
    generate_payroll(actor=actor, company_id=company_id, year=period.start_date.year,
                     month=period.start_date.month, branch_ids={record_branch_id(record)})


def decide(*, actor, company_id, claim_id, approve, amount=None, pay_month=None, note=""):
    """Approve (with the amount and, paid with salary, the month) or reject."""
    with transaction.atomic():
        membership = require_company_membership(actor, company_id)
        with use_company(company_id):
            claim = LfaClaim.objects.select_for_update().select_related("employee").filter(
                pk=claim_id).first()
            if claim is None:
                raise PermissionDenied("Claim not found in this company.")
            if not _deciders(actor, company_id, claim.employee)[1]:
                raise PermissionDenied("LFA is decided by the owner, the company administrator "
                                       "or whoever prepares salary in their branch.")
            if claim.employee.user_id == actor.pk:
                raise PermissionDenied("You cannot decide your own claim.")
            if claim.status != Status.PENDING:
                raise ValidationError("This claim has already been decided.")
            note = (note or "").strip()
            period = None
            if approve:
                cap = Decimal(claim.settings_snapshot.get("max_amount") or 0) or None
                amount = amount if amount is not None else claim.calculated_amount
                if amount is None or amount <= 0:
                    raise ValidationError({"amount": "Give the amount to pay."})
                if cap and amount > cap:
                    raise ValidationError({"amount": f"The most that can be approved is {cap}."})
                claim.approved_amount = _money(amount)
                if claim.payment == Payment.WITH_SALARY:
                    if pay_month is None:
                        raise ValidationError({"pay_month": "Choose the salary month."})
                    period = _pay_period(company_id, pay_month.replace(day=1))
                    adjustment = PayrollAdjustment(
                        company_id=company_id, employee=claim.employee,
                        target_payroll_period=period,
                        adjustment_type=PayrollAdjustment.AdjustmentType.EARNING,
                        amount=claim.approved_amount, code="LFA",
                        reason=(f"{claim.settings_snapshot.get('name') or 'LFA'} "
                                f"{claim.cycle_start:%Y}"
                                + (f"-{claim.cycle_end:%y}" if claim.cycle_end.year
                                   != claim.cycle_start.year else "")),
                        created_by=actor, updated_by=actor)
                    adjustment.full_clean()
                    adjustment.save()
                    claim.payroll_adjustment = adjustment
                claim.status = Status.APPROVED
            else:
                if not note:
                    raise ValidationError({"note": "Say why it is rejected; they can read it."})
                claim.status = Status.REJECTED
            claim.decided_by, claim.decided_at = actor, timezone.now()
            claim.decision_note = note[:255]
            claim.updated_by = actor
            claim.save()
            record_company_event(
                actor=actor, membership=membership, company=membership.company,
                action=f"lfa.{claim.status}", obj=claim,
                after={"amount": str(claim.approved_amount) if approve else None,
                       "pay_month": period.name if period else None, "note": note})
    if period is not None:
        _refresh_payslip(actor, company_id, period, claim.employee)
    return claim


def cancel(*, actor, company_id, claim_id, note):
    """Take back an approved claim not yet paid (its payslip line goes too)."""
    with transaction.atomic():
        membership = require_company_membership(actor, company_id)
        note = (note or "").strip()
        if not note:
            raise ValidationError({"note": "Say why it is cancelled."})
        with use_company(company_id):
            claim = LfaClaim.objects.select_for_update(of=("self",)).select_related(
                "employee", "payroll_adjustment__target_payroll_period").filter(
                pk=claim_id).first()
            if claim is None:
                raise PermissionDenied("Claim not found in this company.")
            if not _deciders(actor, company_id, claim.employee)[1]:
                raise PermissionDenied("Only whoever decides LFA can cancel it.")
            sync_paid(company_id, [claim])
            if claim.status != Status.APPROVED:
                raise ValidationError("Only an approved claim not yet paid can be cancelled.")
            period = None
            adjustment = claim.payroll_adjustment
            if adjustment is not None:
                period = adjustment.target_payroll_period
                if PayrollRun.objects.filter(payroll_period=period,
                                             status=PayrollRun.Status.SUBMITTED).exists():
                    raise ValidationError(f"{period.name}'s salary is waiting for approval. "
                                          "Send it back first.")
                adjustment.status = PayrollAdjustment.Status.CANCELLED
                adjustment.updated_by = actor
                adjustment.save(update_fields=["status", "updated_by", "updated_at"])
            claim.status = Status.CANCELLED
            claim.decision_note = note[:255]
            claim.updated_by = actor
            claim.save(update_fields=["status", "decision_note", "updated_by", "updated_at"])
            record_company_event(actor=actor, membership=membership, company=membership.company,
                                 action="lfa.cancelled", obj=claim, after={"note": note})
    if period is not None:
        _refresh_payslip(actor, company_id, period, claim.employee)
    return claim


@transaction.atomic
def mark_paid(*, actor, company_id, claim_id, paid_on, reference=""):
    """An approved claim paid separately: when, and the reference."""
    membership = require_company_membership(actor, company_id)
    with use_company(company_id):
        claim = LfaClaim.objects.select_for_update().select_related("employee").filter(
            pk=claim_id).first()
        if claim is None:
            raise PermissionDenied("Claim not found in this company.")
        if not _deciders(actor, company_id, claim.employee)[1]:
            raise PermissionDenied("Only whoever decides LFA can mark it paid.")
        if claim.status != Status.APPROVED or claim.payment != Payment.SEPARATELY:
            raise ValidationError("Only an approved claim paid separately is marked paid here; "
                                  "one paid with salary is paid when its month is finalised.")
        claim.status, claim.paid_on = Status.PAID, paid_on
        claim.payment_reference = (reference or "").strip()[:100]
        claim.updated_by = actor
        claim.save(update_fields=["status", "paid_on", "payment_reference", "updated_by",
                                  "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="lfa.paid", obj=claim,
                             after={"paid_on": paid_on.isoformat(), "reference": reference})
    return claim


def sync_paid(company_id, claims):
    """Approved claims paid with salary become Paid once their month is
    finalised. Call inside the company's tenant context."""
    for claim in claims:
        adjustment = claim.payroll_adjustment
        if claim.status != Status.APPROVED or adjustment is None:
            continue
        period = adjustment.target_payroll_period
        if PayrollRun.objects.filter(payroll_period=period,
                                     status=PayrollRun.Status.POSTED).exists():
            claim.status, claim.paid_on = Status.PAID, period.end_date
            claim.save(update_fields=["status", "paid_on", "updated_at"])


def decide_scope(actor, company_id):
    """``ALL_BRANCHES`` or the branches where ``actor`` decides claims."""
    membership = require_company_membership(actor, company_id)
    if membership.role in ("owner", "company_admin"):
        return ALL_BRANCHES
    return branches_for(actor, company_id, "salary.prepare")
