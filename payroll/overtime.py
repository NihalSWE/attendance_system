"""Overtime: approving it, day by day (plan step A9).

Attendance counts overtime on its own (``AttendanceRecord.
calculated_overtime_minutes``: in-office time after the shift's end, delayed by
the shift's "overtime after" minutes). What happens to it (Ajay, 2026-09-14):

- **approved automatically** — the person scanned out, so the time is known;
  everything counted is approved and salary pays it;
- **waiting** — nobody scanned out of it (came back after the shift and never
  scanned out, or a check-out set by rule): nothing is paid until somebody
  with the right sets the time they left, or rejects it;
- **too short to pay** — the company's minimum or blocks leave nothing to pay.

On any day admin or HR can still step in: approve fewer minutes, reject, or
undo their decision (back to automatic, or waiting).

Work on a holiday or weekly off works the same way: every minute in the
office counts, and salary pays it at the day-off rate.

Decisions are kept in ``OvertimeDecision``. Attendance puts the approved
minutes on the day whenever it recalculates (``approved_minutes`` below), and
salary reads them from there (``payroll.services``). A month whose salary is
finalised is closed.
"""

import datetime
import zoneinfo
from collections import Counter
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from access_control.branch_access import (
    ALL_BRANCHES, branches_for, branches_for_any, headed_departments,
)
from accounts.models import CompanyMembership
from attendance.models import AttendanceRecord
from attendance.services import locked_ranges, month_bounds, recalculate, refresh
from auditlog.services import record_company_event
from common.tenant import use_company
from organization.models import Branch
from organization.services import (
    STRUCTURE_ROLES,
    require_company_membership,
    visible_branches,
)
from payroll.models import OvertimeDecision
from payroll.policy import rules_for

Status = AttendanceRecord.AttendanceStatus
DAYS_OFF = (Status.HOLIDAY, Status.WEEKLY_OFF)
# Ajay, 2026-09-14: overtime is decided by "the admin or HR".
OVERTIME_ROLES = (*STRUCTURE_ROLES, CompanyMembership.Role.HR)

WAITING = "waiting"
# Scanned out, so approved without anybody pressing anything (Ajay, 2026-09-14).
AUTOMATIC = "automatic"
# Counted, but the company's minimum or blocks leave nothing to pay, so nobody
# is asked about it (Ajay, 2026-09-14: 26 days of 1–59 minutes were "waiting"
# under a 60-minute minimum).
TOO_SHORT = "too_short"
APPROVED_STATES = (AUTOMATIC, OvertimeDecision.Status.APPROVED)

# A day that could hold overtime: minutes counted after the shift, a day off
# somebody came in on, or a session nobody scanned out of.
CANDIDATES = (
    Q(calculated_overtime_minutes__gt=0)
    | Q(attendance_status__in=DAYS_OFF, first_in_at__isnull=False)
    | Q(sessions__ended_at__isnull=True, sessions__started_at__isnull=False)
)


@dataclass
class Scope:
    """Who is looking at overtime, and in which branches (A12 part 5)."""

    membership: object
    branches: object      # Branch queryset
    company_wide: bool    # owner, company admin or HR: as before A12
    decide: object        # ALL_BRANCHES or the branch ids where they may decide
    # Departments this person heads: they SEE their department's overtime and
    # never decide it, because deciding changes pay (Ajay, 2026-09-20).
    departments: frozenset = frozenset()
    # The branch dropdown may offer a head their department's branch even
    # though the branch itself is not theirs; filtering still uses `branches`.
    choice_branches: object = None

    def may_decide(self, branch_id):
        return branch_id in self.decide


def overtime_scope(actor, company_id, code="overtime.view"):
    """Owner, company admin and HR: their branches, as before. A branch manager
    or someone given access: the branches where they may view (or, for
    ``overtime.decide``, decide) overtime. Nobody else."""
    membership = require_company_membership(actor, company_id)
    if membership.role in OVERTIME_ROLES:
        with use_company(company_id):
            branches_qs = visible_branches(membership)
            return Scope(membership, branches_qs, True, ALL_BRANCHES,
                         frozenset(), branches_qs)
    decide = branches_for(actor, company_id, "overtime.decide")
    if code == "overtime.decide":
        branches = decide
    else:
        branches = branches_for_any(actor, company_id, "overtime.view", "overtime.decide")
    # A head sees their department's overtime; they never decide it.
    headed = (
        headed_departments(actor, company_id)
        if code != "overtime.decide" else set()
    )
    if not branches and not headed:
        raise PermissionDenied(
            "Deciding overtime needs owner, company administrator or HR access, "
            "or access to overtime in a branch."
        )
    with use_company(company_id):
        queryset = Branch.objects.all()
        choices = Branch.objects.all()
        if branches is not ALL_BRANCHES:
            queryset = queryset.filter(pk__in=branches)
            # The dropdown also offers the branch a headed department sits in.
            choices = choices.filter(
                Q(pk__in=branches) | Q(departments__in=headed)
            ).distinct() if headed else queryset
        return Scope(membership, queryset, False, decide, frozenset(headed), choices)


@dataclass
class Claim:
    """What a day offers as overtime."""

    record: object
    day_off: bool
    minutes: int                   # counted from sessions that have an end
    open_from: object = None       # start of a session nobody scanned out of
    overtime_from: object = None   # overtime counts from here; None on a day off

    @property
    def exists(self):
        return self.minutes > 0 or self.open_from is not None

    @property
    def needs_decision(self):
        """Nobody scanned out, so nobody knows when it ended."""
        return self.open_from is not None or self.record.check_out_by_rule

    def pays_nothing(self, rules):
        """True when the rules would pay none of it even if it were approved.

        An open session has no length until somebody sets when it ended, so
        it always needs a look.
        """
        return self.open_from is None and rules.payable_overtime(self.minutes) == 0


def state_of(claim, decision, rules):
    if decision is not None:
        return decision.status
    if claim.pays_nothing(rules):
        return TOO_SHORT
    return WAITING if claim.needs_decision else AUTOMATIC


def approved_minutes(paired, *, day_off, decided):
    """The overtime minutes a day carries as approved.

    Called by attendance each time it writes a day (``_write_day``), so the
    rule lives here, with the rest of overtime. ``paired`` is the
    ``attendance.pairing.Day``; ``decided`` is a decision's approved minutes,
    or None when nobody has decided the day.

    A decision wins. Otherwise a finished day whose overtime ends in a real
    scan is approved automatically: the time after the shift on a working day,
    every minute in the office on a day off. A day nobody scanned out of
    carries nothing until somebody sets when it ended.
    """
    if decided is not None:
        return decided
    if not paired.is_closed or paired.open_overtime or paired.check_out_by_rule:
        return 0
    return paired.in_office_minutes if day_off else paired.overtime_minutes


def claim_for(record):
    """The overtime a day offers. ``record.sessions`` should be prefetched."""
    sessions = list(record.sessions.all())
    open_session = next(
        (s for s in sessions if s.ended_at is None and s.started_at is not None), None
    )
    day_off = record.attendance_status in DAYS_OFF
    if day_off:
        minutes = sum(s.worked_minutes for s in sessions if s.ended_at is not None)
        overtime_from = None
    else:
        minutes = record.calculated_overtime_minutes
        overtime_from = record.scheduled_end_at + datetime.timedelta(
            minutes=record.shift.overtime_after_minutes
        )
    return Claim(
        record=record,
        day_off=day_off,
        minutes=minutes,
        open_from=open_session.started_at if open_session else None,
        overtime_from=overtime_from,
    )


def company_zone(company):
    try:
        return zoneinfo.ZoneInfo(company.timezone or "UTC")
    except zoneinfo.ZoneInfoNotFoundError:
        return zoneinfo.ZoneInfo("UTC")


def check_out_moment(claim, at_time, tz):
    """The first moment at ``at_time`` after the open session began."""
    started = claim.open_from.astimezone(tz)
    moment = datetime.datetime.combine(started.date(), at_time, tzinfo=tz)
    if moment <= started:
        moment += datetime.timedelta(days=1)
    return moment


def open_minutes(claim, check_out_at):
    """Overtime minutes in an open session, ended at ``check_out_at``."""
    start = claim.open_from
    if claim.overtime_from is not None and claim.overtime_from > start:
        start = claim.overtime_from
    return max(0, int((check_out_at - start).total_seconds() // 60))


def _is_locked(company_id, day):
    return any(start <= day <= end for start, end in locked_ranges(company_id))


def _snapshot(decision):
    if decision is None:
        return {"status": "not decided"}
    return {
        "work_date": decision.work_date.isoformat(),
        "status": decision.status,
        "calculated_minutes": decision.calculated_minutes,
        "approved_minutes": decision.approved_minutes,
        "check_out_at": decision.check_out_at.isoformat() if decision.check_out_at else None,
        "note": decision.note,
    }


def _record_in_scope(scope, record_id):
    record = (
        AttendanceRecord.objects.select_related(
            "employee", "shift", "branch", "employee_assignment")
        .prefetch_related("sessions")
        .filter(pk=record_id)
        .first()
    )
    if record is None:
        raise PermissionDenied("Day not found in this company.")
    if scope.branches.filter(pk=record.branch_id).exists():
        return record
    # A department head reaches their own department's day and no other, even
    # inside the same branch.
    department_id = getattr(record.employee_assignment, "department_id", None)
    if scope.departments and department_id in scope.departments:
        return record
    raise PermissionDenied("That branch is outside your assigned scope.")


@dataclass
class Row:
    record: object
    claim: Claim
    decision: object
    state: str
    paid_minutes: int = 0
    changed: bool = False

    @property
    def approved_minutes(self):
        if self.state == AUTOMATIC:
            return self.claim.minutes
        if self.state == OvertimeDecision.Status.APPROVED:
            return self.decision.approved_minutes
        return 0


def overtime_month(*, actor, company_id, year, month):
    """Every day of a month with overtime to decide, and the decisions made."""
    scope = overtime_scope(actor, company_id)
    membership = scope.membership
    first, last = month_bounds(year, month)
    # Live attendance: bring the month up to date before reading it.
    refresh(company_id, start=first, end=last)
    rules = rules_for(company_id, first)
    with use_company(company_id):
        records = list(
            AttendanceRecord.objects.select_related("employee", "shift", "branch")
            .prefetch_related("sessions")
            .filter(work_date__gte=first, work_date__lte=last, is_open=False)
            .filter(branch__in=scope.branches)
            .filter(CANDIDATES)
            .distinct()
            .order_by("work_date", "employee__first_name", "employee__last_name")
        )
        decisions = {
            (d.employee_id, d.work_date): d
            for d in OvertimeDecision.objects.select_related("decided_by").filter(
                work_date__gte=first, work_date__lte=last
            )
        }
    rows = []
    for record in records:
        claim = claim_for(record)
        if not claim.exists:
            continue
        decision = decisions.get((record.employee_id, record.work_date))
        row = Row(record=record, claim=claim, decision=decision,
                  state=state_of(claim, decision, rules))
        row.paid_minutes = rules.payable_overtime(row.approved_minutes)
        if decision is not None:
            # A scan that arrived after the decision changed what the day counts.
            row.changed = claim.open_from is None and decision.calculated_minutes != claim.minutes
        rows.append(row)
    return {
        "membership": membership,
        "branches": scope.branches,
        "rows": rows,
        "counts": Counter(row.state for row in rows),
        "rules": rules,
        "locked": _is_locked(company_id, first),
    }


def overtime_day(*, actor, company_id, record_id):
    """One day's overtime, for the decision page."""
    scope = overtime_scope(actor, company_id)
    membership = scope.membership
    with use_company(company_id):
        stored = AttendanceRecord.objects.filter(pk=record_id).values_list(
            "employee_id", "work_date"
        ).first()
    if stored is None:
        raise PermissionDenied("Day not found in this company.")
    refresh(company_id, employee_ids=[stored[0]], start=stored[1], end=stored[1])
    with use_company(company_id):
        record = (
            AttendanceRecord.objects.filter(employee_id=stored[0], work_date=stored[1])
            .values_list("pk", flat=True).first()
        )
        if record is None:
            raise PermissionDenied("That day no longer has attendance.")
        record = _record_in_scope(scope, record)
        decision = OvertimeDecision.objects.select_related("decided_by").filter(
            employee=record.employee, work_date=record.work_date
        ).first()
        scans = list(
            record.allocations.select_related("punch_event__device")
            .filter(is_included=True).order_by("sequence_number")
        )
    claim = claim_for(record)
    rules = rules_for(company_id, record.work_date.replace(day=1))
    state = state_of(claim, decision, rules)
    return {
        "membership": membership,
        "company_wide": scope.company_wide,
        "may_decide": scope.may_decide(record.branch_id),
        "record": record,
        "claim": claim,
        "decision": decision,
        "state": state,
        "paid_minutes": rules.payable_overtime(claim.minutes),
        "scans": scans,
        "rules": rules,
        "locked": _is_locked(company_id, record.work_date),
    }


@transaction.atomic
def decide_overtime(*, actor, company_id, record_id, approve, minutes=None,
                    check_out=None, note=""):
    """Approve or reject one day's overtime.

    ``minutes`` is how much of the counted overtime is approved (1 up to what
    attendance counted). For a session nobody scanned out of, ``check_out`` is
    the time they left instead, and the minutes follow from it.
    """
    scope = overtime_scope(actor, company_id, "overtime.decide")
    membership = scope.membership
    with use_company(company_id):
        record = _record_in_scope(scope, record_id)
        claim = claim_for(record)
        if record.is_open:
            raise ValidationError("This day has not finished yet. Decide its overtime once it has.")
        if not claim.exists:
            raise ValidationError("There is no overtime on this day.")
        if _is_locked(company_id, record.work_date):
            raise ValidationError("This month's salary is finalised; its overtime cannot change.")

        errors = {}
        approved, check_out_at = 0, None
        if approve and claim.open_from is not None:
            if check_out is None:
                errors["check_out"] = "Enter the time they left."
            else:
                tz = company_zone(membership.company)
                check_out_at = check_out_moment(claim, check_out, tz)
                approved = claim.minutes + open_minutes(claim, check_out_at)
                if check_out_at > timezone.now():
                    errors["check_out"] = "That time has not come yet."
                elif approved <= 0:
                    starts = (claim.overtime_from or claim.open_from).astimezone(tz)
                    errors["check_out"] = f"Overtime starts at {starts:%H:%M}; enter a later time."
        elif approve:
            if minutes is None:
                errors["minutes"] = "Enter the minutes to approve."
            elif not 1 <= minutes <= claim.minutes:
                errors["minutes"] = (
                    f"Use 1 to {claim.minutes} minutes: the overtime attendance counted."
                )
            else:
                approved = minutes
        if errors:
            raise ValidationError(errors)

        decision = (
            OvertimeDecision.objects.select_for_update()
            .filter(employee=record.employee, work_date=record.work_date)
            .first()
        )
        before = _snapshot(decision)
        if decision is None:
            decision = OvertimeDecision(
                company=membership.company, employee=record.employee, work_date=record.work_date,
            )
        decision.status = (
            OvertimeDecision.Status.APPROVED if approve else OvertimeDecision.Status.REJECTED
        )
        decision.calculated_minutes = claim.minutes
        decision.approved_minutes = approved
        decision.check_out_at = check_out_at
        decision.note = (note or "").strip()[:255]
        decision.decided_by = actor
        decision.decided_at = timezone.now()
        decision.full_clean()
        decision.save()
        _rewrite_day(company_id, record)

        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action=f"overtime.{decision.status}", obj=decision,
            before=before, after=_snapshot(decision),
        )
    return decision


def _rewrite_day(company_id, record):
    """Let attendance rewrite the day, so it carries the decision (or its
    absence) exactly as every later recalculation will."""
    recalculate(
        company_id, employee_ids=[record.employee_id],
        start=record.work_date, end=record.work_date,
    )


@transaction.atomic
def undo_overtime_decision(*, actor, company_id, record_id):
    """Drop a decision: the day goes back to automatic, or to waiting."""
    scope = overtime_scope(actor, company_id, "overtime.decide")
    membership = scope.membership
    with use_company(company_id):
        record = _record_in_scope(scope, record_id)
        if _is_locked(company_id, record.work_date):
            raise ValidationError("This month's salary is finalised; its overtime cannot change.")
        decision = (
            OvertimeDecision.objects.select_for_update()
            .filter(employee=record.employee, work_date=record.work_date)
            .first()
        )
        if decision is None:
            raise ValidationError("This day's overtime has not been decided.")
        before = _snapshot(decision)
        decision.delete()
        _rewrite_day(company_id, record)
        after = WAITING if claim_for(record).needs_decision else AUTOMATIC
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="overtime.undone", obj=record, before=before, after={"status": after},
        )
    return after


def decided_after(company_id, run, employee_ids=None):
    """How many of a month's decisions were made after the payslip they
    belong to was generated.

    Each payslip carries its own time: since A12 part 6 a branch may
    regenerate its own people and leave the others as they were. Someone
    without a payslip (no salary set) counts from the run's last generation.
    """
    if run is None or run.calculation_finished_at is None:
        return 0
    period = run.payroll_period
    with use_company(company_id):
        generated = dict(run.records.values_list("employee_id", "created_at"))
        decisions = OvertimeDecision.objects.filter(
            work_date__gte=period.start_date, work_date__lte=period.end_date
        )
        if employee_ids is not None:
            decisions = decisions.filter(employee_id__in=list(employee_ids))
        return sum(
            1 for employee_id, decided_at in decisions.values_list("employee_id", "decided_at")
            if decided_at > generated.get(employee_id, run.calculation_finished_at)
        )


def undecided_count(company_id, first, last, branch_ids=ALL_BRANCHES):
    """Days of a month still waiting for a decision (without recalculating).

    Only a day nobody scanned out of waits: the rest is approved automatically
    or too short to pay.
    """
    rules = rules_for(company_id, first)
    with use_company(company_id):
        records = (
            AttendanceRecord.objects.prefetch_related("sessions").select_related("shift")
            .filter(work_date__gte=first, work_date__lte=last, is_open=False)
            .filter(CANDIDATES).distinct()
        )
        if branch_ids is not ALL_BRANCHES:
            records = records.filter(branch_id__in=branch_ids)
        records = list(records)
        decided = set(
            OvertimeDecision.objects.filter(
                work_date__gte=first, work_date__lte=last
            ).values_list("employee_id", "work_date")
        )
    waiting = 0
    for record in records:
        if (record.employee_id, record.work_date) in decided:
            continue
        claim = claim_for(record)
        if claim.exists and state_of(claim, None, rules) == WAITING:
            waiting += 1
    return waiting
