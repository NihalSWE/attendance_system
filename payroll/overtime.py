"""Overtime: deciding it, day by day (plan step A9).

Attendance counts overtime on its own (``AttendanceRecord.
calculated_overtime_minutes``: in-office time after the shift's end, delayed by
the shift's "overtime after" minutes). Nothing is paid until somebody with the
right decides it here:

- **approve** — all of it or fewer minutes; for a session nobody scanned out
  of, the approver sets the time they left and the minutes follow from it;
- **reject** — nothing is paid;
- **undo** — back to waiting.

Work on a holiday or weekly off is decided the same way: every minute in the
office counts, and salary pays it at the day-off rate.

Decisions are kept in ``OvertimeDecision``. Attendance copies the approved
minutes onto the day whenever it recalculates, and salary reads them from
there (``payroll.services``). A month whose salary is finalised is closed.
"""

import datetime
import zoneinfo
from collections import Counter
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from accounts.models import CompanyMembership
from attendance.models import AttendanceRecord, ReviewStatus
from attendance.services import locked_ranges, month_bounds, refresh
from auditlog.services import record_company_event
from common.tenant import use_company
from organization.services import (
    STRUCTURE_ROLES,
    assert_branch_in_scope,
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
# Counted, but the company's minimum or rounding leaves nothing to pay, so
# nobody is asked to decide it (Ajay, 2026-09-14: 26 days of 1–59 minutes were
# "waiting" under a 60-minute minimum).
TOO_SHORT = "too_short"
STATES = (WAITING, OvertimeDecision.Status.APPROVED, OvertimeDecision.Status.REJECTED, TOO_SHORT)

# A day that could hold overtime: minutes counted after the shift, a day off
# somebody came in on, or a session nobody scanned out of.
CANDIDATES = (
    Q(calculated_overtime_minutes__gt=0)
    | Q(attendance_status__in=DAYS_OFF, first_in_at__isnull=False)
    | Q(sessions__ended_at__isnull=True, sessions__started_at__isnull=False)
)


def require_overtime_approver(actor, company_id):
    membership = require_company_membership(actor, company_id)
    if membership.role not in OVERTIME_ROLES:
        raise PermissionDenied("Deciding overtime needs owner, company administrator or HR access.")
    return membership


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

    def pays_nothing(self, rules):
        """True when the rules would pay none of it even if it were approved.

        An open session has no length until somebody sets when it ended, so
        it always needs a look.
        """
        return self.open_from is None and rules.payable_overtime(self.minutes) == 0


def state_of(claim, decision, rules):
    if decision is not None:
        return decision.status
    return TOO_SHORT if claim.pays_nothing(rules) else WAITING


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
        return {"status": WAITING}
    return {
        "work_date": decision.work_date.isoformat(),
        "status": decision.status,
        "calculated_minutes": decision.calculated_minutes,
        "approved_minutes": decision.approved_minutes,
        "check_out_at": decision.check_out_at.isoformat() if decision.check_out_at else None,
        "note": decision.note,
    }


def _record_in_scope(membership, record_id):
    record = (
        AttendanceRecord.objects.select_related("employee", "shift", "branch")
        .prefetch_related("sessions")
        .filter(pk=record_id)
        .first()
    )
    if record is None:
        raise PermissionDenied("Day not found in this company.")
    assert_branch_in_scope(membership, record.branch)
    return record


@dataclass
class Row:
    record: object
    claim: Claim
    decision: object
    state: str
    paid_minutes: int = 0
    changed: bool = False


def overtime_month(*, actor, company_id, year, month):
    """Every day of a month with overtime to decide, and the decisions made."""
    membership = require_overtime_approver(actor, company_id)
    first, last = month_bounds(year, month)
    # Live attendance: bring the month up to date before reading it.
    refresh(company_id, start=first, end=last)
    rules = rules_for(company_id, first)
    with use_company(company_id):
        records = list(
            AttendanceRecord.objects.select_related("employee", "shift", "branch")
            .prefetch_related("sessions")
            .filter(work_date__gte=first, work_date__lte=last, is_open=False)
            .filter(branch__in=visible_branches(membership))
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
        if decision is not None:
            if decision.status == OvertimeDecision.Status.APPROVED:
                row.paid_minutes = rules.payable_overtime(decision.approved_minutes)
            # A scan that arrived after the decision changed what the day counts.
            row.changed = claim.open_from is None and decision.calculated_minutes != claim.minutes
        rows.append(row)
    return {
        "membership": membership,
        "rows": rows,
        "counts": Counter(row.state for row in rows),
        "rules": rules,
        "locked": _is_locked(company_id, first),
    }


def overtime_day(*, actor, company_id, record_id):
    """One day's overtime, for the decision page."""
    membership = require_overtime_approver(actor, company_id)
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
        record = _record_in_scope(membership, record)
        decision = OvertimeDecision.objects.select_related("decided_by").filter(
            employee=record.employee, work_date=record.work_date
        ).first()
        scans = list(
            record.allocations.select_related("punch_event__device")
            .filter(is_included=True).order_by("sequence_number")
        )
    return {
        "membership": membership,
        "record": record,
        "claim": claim_for(record),
        "decision": decision,
        "scans": scans,
        "rules": rules_for(company_id, record.work_date.replace(day=1)),
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
    membership = require_overtime_approver(actor, company_id)
    with use_company(company_id):
        record = _record_in_scope(membership, record_id)
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

        # Mirror it onto the day now; attendance keeps it on every recalculation.
        updates = {"approved_overtime_minutes": approved}
        if claim.open_from is not None:
            updates["review_status"] = ReviewStatus.REVIEWED
        AttendanceRecord.objects.filter(pk=record.pk).update(**updates)

        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action=f"overtime.{decision.status}", obj=decision,
            before=before, after=_snapshot(decision),
        )
    return decision


@transaction.atomic
def undo_overtime_decision(*, actor, company_id, record_id):
    """Put a day's overtime back to waiting."""
    membership = require_overtime_approver(actor, company_id)
    with use_company(company_id):
        record = _record_in_scope(membership, record_id)
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
        updates = {"approved_overtime_minutes": 0}
        if claim_for(record).open_from is not None:
            updates["review_status"] = ReviewStatus.NEEDS_REVIEW
        AttendanceRecord.objects.filter(pk=record.pk).update(**updates)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="overtime.undone", obj=record, before=before, after={"status": WAITING},
        )


def decided_after(company_id, first, last, moment):
    """How many of a month's decisions were made after ``moment``."""
    if moment is None:
        return 0
    with use_company(company_id):
        return OvertimeDecision.objects.filter(
            work_date__gte=first, work_date__lte=last, decided_at__gt=moment
        ).count()


def undecided_count(company_id, first, last):
    """Days of a month still waiting for a decision (without recalculating).

    Overtime too short to pay under the month's rules is not waiting.
    """
    rules = rules_for(company_id, first)
    with use_company(company_id):
        records = list(
            AttendanceRecord.objects.prefetch_related("sessions").select_related("shift")
            .filter(work_date__gte=first, work_date__lte=last, is_open=False)
            .filter(CANDIDATES).distinct()
        )
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
        if claim.exists and not claim.pays_nothing(rules):
            waiting += 1
    return waiting
