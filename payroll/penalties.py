"""Penalty rules: when attendance costs salary, and how much.

Two halves:

- **Evaluation** (pure, no database): given one employee's month of
  attendance records, the penalty rules in force and what a day and a minute
  of their pay are worth, find every occurrence and its amount.
- **Rules** (services): add, change and stop a rule. Versioned like the salary
  rules — a change is a new version from the 1st of a month, and a month uses
  the versions in force on its first day.

How occurrences are found:

- *Every day it happens* — each qualifying day is one penalty.
- *Every so many days in a month* — every N qualifying days in the month make
  one penalty (7 late days with N=3 make two; the seventh waits).
- *So many working days in a row* — N qualifying working days in a row make
  one penalty. Weekly offs and holidays are skipped; leave, an absent day or a
  working day that does not qualify breaks the run. Runs stay inside the month.

Rules sharing a group do not add up on the same day: the larger deduction
counts (single-day rules). A rule's monthly maximum and the company's
"penalties can take at most X% of pay" scale the amounts down to the cap.
"""

import re
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone

from attendance.models import AttendanceRecord
from auditlog.services import record_company_event
from common.services import create_validated
from common.tenant import use_company
from organization.services import require_structure_manager
from payroll.models import AttendancePenaltyRule

Rule = AttendancePenaltyRule
Status = AttendanceRecord.AttendanceStatus
CENT = Decimal("0.01")
ZERO = Decimal("0")

RULE_FIELDS = (
    "name", "metric", "operator", "threshold_minutes", "occurrence_mode",
    "required_occurrences", "deduction_method", "deduction_value",
    "exclusive_group", "maximum_deduction",
)
# Offered on the page today. Time out of the office needs the IN/OUT pairing
# (plan step N1); a rolling window needs history across months.
AVAILABLE_METRICS = (
    Rule.Metric.LATE_MINUTES, Rule.Metric.EARLY_OUT_MINUTES,
    Rule.Metric.WORKED_SHORTFALL, Rule.Metric.ABSENCE,
)
AVAILABLE_MODES = (
    Rule.OccurrenceMode.SINGLE_DAY, Rule.OccurrenceMode.WITHIN_PERIOD,
    Rule.OccurrenceMode.CONSECUTIVE_WORKDAYS,
)
COMPARE = {
    "gte": lambda value, limit: value >= limit,
    "gt": lambda value, limit: value > limit,
    "lte": lambda value, limit: value <= limit,
    "lt": lambda value, limit: value < limit,
    "equal": lambda value, limit: value == limit,
}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# --------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------

@dataclass
class Occurrence:
    """One penalty found: the rule, the days behind it, and its amount."""

    rule: object
    days: list                                # [(record, qualifying value)]
    amount: Decimal = ZERO
    minutes: int = 0
    day_fraction: Decimal = ZERO
    details: dict = field(default_factory=dict)

    @property
    def first(self):
        return self.days[0][0].work_date

    @property
    def last(self):
        return self.days[-1][0].work_date

    @property
    def identity(self):
        """Stable across regenerations: the same rule version and the same days."""
        return f"{self.rule.code}:v{self.rule.version}:{self.first:%Y%m%d}-{self.last:%Y%m%d}"

    def describe_days(self):
        dates = [record.work_date for record, _ in self.days]
        if len(dates) == 1:
            return f"{dates[0]:%d %b}"
        return ", ".join(f"{d:%d}" for d in dates[:-1]) + f", {dates[-1]:%d %b}"


def _minutes_between(later, earlier):
    return max(0, int((later - earlier).total_seconds() // 60))


def qualifying_value(rule, record, expected_minutes):
    """The value this rule measures on a day if the day qualifies, else None."""
    status = record.attendance_status
    if rule.metric == Rule.Metric.ABSENCE:
        return 1 if status == Status.ABSENT else None
    if rule.metric == Rule.Metric.LATE_MINUTES:
        if status not in (Status.PRESENT, Status.HALF_DAY, Status.INCOMPLETE):
            return None
        value = record.late_minutes
    elif rule.metric == Rule.Metric.EARLY_OUT_MINUTES:
        last_out = getattr(record, "last_out_at", None)
        if status not in (Status.PRESENT, Status.HALF_DAY) or last_out is None:
            return None
        # Like lateness: leaving within the shift's grace-out is not early.
        grace = getattr(getattr(record, "shift", None), "grace_out_minutes", 0) or 0
        value = max(0, _minutes_between(record.scheduled_end_at, last_out) - grace)
    elif rule.metric == Rule.Metric.WORKED_SHORTFALL:
        if status not in (Status.PRESENT, Status.HALF_DAY):
            return None
        value = max(0, expected_minutes(record) - record.worked_minutes)
    else:
        return None  # time out of the office: needs the IN/OUT pairing (N1)
    return value if COMPARE[rule.operator](value, rule.threshold_minutes or 0) else None


def _day_kind(record):
    status = record.attendance_status
    if status == Status.WEEKLY_OFF:
        return "weekly_off"
    if status == Status.HOLIDAY:
        return "holiday"
    if status == Status.LEAVE:
        return "leave"
    if status == Status.ABSENT:
        return "absent"
    return "work"


def find_occurrences(rule, records, expected_minutes):
    """Every occurrence of one rule in one employee's month."""
    records = sorted(records, key=lambda r: r.work_date)
    need = rule.required_occurrences
    found = []
    if rule.occurrence_mode == Rule.OccurrenceMode.SINGLE_DAY:
        for record in records:
            value = qualifying_value(rule, record, expected_minutes)
            if value is not None:
                found.append(Occurrence(rule, [(record, value)]))
    elif rule.occurrence_mode == Rule.OccurrenceMode.WITHIN_PERIOD:
        qualifying = [
            (record, value) for record in records
            if (value := qualifying_value(rule, record, expected_minutes)) is not None
        ]
        for start in range(0, len(qualifying) - need + 1, need):
            found.append(Occurrence(rule, qualifying[start:start + need]))
    elif rule.occurrence_mode == Rule.OccurrenceMode.CONSECUTIVE_WORKDAYS:
        policy = rule.sequence_policy()
        run = []
        for record in records:
            value = qualifying_value(rule, record, expected_minutes)
            if value is not None:
                run.append((record, value))
                if len(run) == need:
                    found.append(Occurrence(rule, run))
                    run = []
            elif policy.get(_day_kind(record)) == "skip":
                continue
            else:
                run = []
    return found


class PayValue:
    """What a day and a minute of one employee's pay are worth this month."""

    def __init__(self, pay_basis, rate, per_day, expected_minutes):
        self.pay_basis = pay_basis
        self.rate = Decimal(rate)
        self.per_day = per_day
        self.expected_minutes = expected_minutes

    def day(self, record):
        if self.pay_basis == "monthly":
            return self.per_day
        if self.pay_basis == "daily":
            return self.rate
        return self.rate * Decimal(self.expected_minutes(record) or 480) / Decimal("60")

    def minute(self, record):
        if self.pay_basis == "hourly":
            return self.rate / Decimal("60")
        return self.day(record) / Decimal(self.expected_minutes(record) or 480)


def _amount(occurrence, pay):
    rule, days = occurrence.rule, occurrence.days
    last_record = days[-1][0]
    method = rule.deduction_method
    if method == Rule.DeductionMethod.ACTUAL_MINUTES:
        total = ZERO
        for record, value in days:
            minutes = pay.expected_minutes(record) if rule.metric == Rule.Metric.ABSENCE else value
            occurrence.minutes += int(minutes)
            total += Decimal(minutes) * pay.minute(record)
        return total
    if method == Rule.DeductionMethod.FIXED_MINUTES:
        occurrence.minutes = int(rule.deduction_value)
        return rule.deduction_value * pay.minute(last_record)
    if method == Rule.DeductionMethod.DAY_FRACTION:
        occurrence.day_fraction = rule.deduction_value
        return rule.deduction_value * pay.day(last_record)
    if method == Rule.DeductionMethod.FULL_DAY:
        occurrence.day_fraction = Decimal("1")
        return pay.day(last_record)
    return Decimal(rule.deduction_value)


def _scale(occurrences, cap, reason):
    total = sum((o.amount for o in occurrences), ZERO)
    if cap is None or total <= cap or total == 0:
        return
    factor = Decimal(cap) / total
    for occurrence in occurrences:
        occurrence.details.setdefault("before_cap", str(money(occurrence.amount)))
        occurrence.amount = occurrence.amount * factor
        occurrence.details["capped"] = reason


def assess(penalty_rules, records, pay, gross=None, max_percent=None, waived=frozenset(),
           employee_key=""):
    """All penalties for one employee's month, amounts rounded to the cent.

    ``waived`` holds occurrence identities a person waived; they are skipped.
    """
    occurrences = []
    for rule in sorted(penalty_rules, key=lambda r: (-r.priority, r.code)):
        found = [
            o for o in find_occurrences(rule, records, pay.expected_minutes)
            if f"{employee_key}{o.identity}" not in waived
        ]
        for occurrence in found:
            occurrence.amount = _amount(occurrence, pay)
        _scale(found, rule.maximum_deduction, "rule maximum")
        occurrences.extend(found)

    # Rules in one group: on each day only the largest single-day deduction.
    best = {}
    for occurrence in occurrences:
        rule = occurrence.rule
        if rule.exclusive_group and rule.occurrence_mode == Rule.OccurrenceMode.SINGLE_DAY:
            key = (rule.exclusive_group, occurrence.first)
            if key not in best or occurrence.amount > best[key].amount:
                best[key] = occurrence
    occurrences = [
        o for o in occurrences
        if not (o.rule.exclusive_group and o.rule.occurrence_mode == Rule.OccurrenceMode.SINGLE_DAY)
        or best[(o.rule.exclusive_group, o.first)] is o
    ]

    if max_percent is not None and gross is not None:
        _scale(occurrences, Decimal(gross) * Decimal(max_percent) / Decimal("100"), "monthly limit")
    for occurrence in occurrences:
        occurrence.amount = money(occurrence.amount)
    return [o for o in occurrences if o.amount > 0]


# --------------------------------------------------------------------------
# Rules
# --------------------------------------------------------------------------

def rules_in_force(company_id, on):
    with use_company(company_id):
        return list(
            Rule.objects.filter(status=Rule.Status.ACTIVE, effective_from__lte=on)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=on))
            .order_by("name")
        )


def _code_for(name):
    base = re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:24] or "RULE"
    code, n = base, 1
    while Rule.objects.filter(code=code).exists():
        n += 1
        code = f"{base[:20]}_{n}"
    return code


def _values(values):
    unsupported = set(values) - {"effective_from", *RULE_FIELDS}
    if unsupported:
        raise ValidationError(f"Unsupported field: {', '.join(sorted(unsupported))}")
    starts = values.get("effective_from")
    if starts is None or starts.day != 1:
        raise ValidationError({"effective_from": "Penalty rules start on the 1st of a month."})
    if values.get("metric") not in AVAILABLE_METRICS:
        raise ValidationError({"metric": "Choose what the rule measures."})
    if values.get("occurrence_mode") not in AVAILABLE_MODES:
        raise ValidationError({"occurrence_mode": "Choose how often it counts."})
    clean = {key: values.get(key) for key in RULE_FIELDS}
    clean["exclusive_group"] = (clean.get("exclusive_group") or "").strip().upper() or None
    clean["name"] = (clean.get("name") or "").strip()
    # A full day or "the minutes themselves" needs no amount.
    clean["deduction_value"] = clean.get("deduction_value") or Decimal("0")
    if clean.get("required_occurrences") is None:
        clean["required_occurrences"] = 1
    return starts, clean


def _snapshot(rule):
    if rule is None:
        return {}
    return {
        "version": rule.version,
        "effective_from": rule.effective_from.isoformat(),
        **{key: str(getattr(rule, key)) for key in RULE_FIELDS},
    }


@transaction.atomic
def create_penalty_rule(*, actor, company_id, values):
    membership = require_structure_manager(actor, company_id)
    starts, clean = _values(values)
    with use_company(company_id):
        rule = create_validated(
            Rule,
            company=membership.company, created_by=actor, updated_by=actor,
            code=_code_for(clean["name"]), version=1, effective_from=starts,
            status=Rule.Status.ACTIVE, **clean,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="penalty_rule.created", obj=rule, after=_snapshot(rule),
        )
    return rule


def _current_versions(code):
    return list(
        Rule.objects.select_for_update()
        .filter(code=code, status=Rule.Status.ACTIVE)
        .order_by("effective_from")
    )


def _close_from(versions, starts, actor, what):
    """Close or replace the versions in force from ``starts``, as the salary rules do."""
    later = [v for v in versions if v.effective_from > starts]
    if later:
        raise ValidationError({
            "effective_from": (
                f"This rule already has a change from {later[-1].effective_from:%B %Y}. "
                f"Choose {later[-1].effective_from:%B %Y} or a later month to {what}."
            )
        })
    before = None
    for version in versions:
        if version.effective_from == starts:
            before = version
            version.status = Rule.Status.RETIRED
            version.updated_by = actor
            version.save(update_fields=["status", "updated_by", "updated_at"])
        elif version.effective_to is None or version.effective_to > starts:
            before = version
            version.effective_to = starts
            version.updated_by = actor
            version.save(update_fields=["effective_to", "updated_by", "updated_at"])
    return before


def get_rule_for_edit(*, actor, company_id, rule_id):
    membership = require_structure_manager(actor, company_id)
    with use_company(company_id):
        rule = Rule.objects.filter(pk=rule_id).first()
    if rule is None:
        raise PermissionDenied("Penalty rule not found in this company.")
    return membership, rule


@transaction.atomic
def change_penalty_rule(*, actor, company_id, rule_id, values):
    """A new version of a rule from a month; the one in force ends the day before."""
    membership, rule = get_rule_for_edit(actor=actor, company_id=company_id, rule_id=rule_id)
    starts, clean = _values(values)
    with use_company(company_id):
        versions = _current_versions(rule.code)
        before = _close_from(versions, starts, actor, "change it")
        number = (Rule.objects.filter(code=rule.code).aggregate(top=Max("version"))["top"] or 0) + 1
        new = create_validated(
            Rule,
            company=membership.company, created_by=actor, updated_by=actor,
            code=rule.code, version=number, effective_from=starts,
            status=Rule.Status.ACTIVE, **clean,
        )
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="penalty_rule.changed", obj=new,
            before=_snapshot(before), after=_snapshot(new),
        )
    return new


@transaction.atomic
def stop_penalty_rule(*, actor, company_id, rule_id, stops_from):
    """The rule no longer applies from the 1st of a month. Earlier months keep it."""
    membership, rule = get_rule_for_edit(actor=actor, company_id=company_id, rule_id=rule_id)
    if stops_from is None or stops_from.day != 1:
        raise ValidationError({"stops_from": "A rule stops from the 1st of a month."})
    with use_company(company_id):
        versions = _current_versions(rule.code)
        if not versions:
            raise ValidationError({"stops_from": "This rule is not in use."})
        before = _close_from(versions, stops_from, actor, "stop it")
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="penalty_rule.stopped", obj=before or rule,
            before=_snapshot(before), after={"stops_from": stops_from.isoformat()},
        )
    return before


# --------------------------------------------------------------------------
# Words for the page
# --------------------------------------------------------------------------

def describe_when(rule):
    what = Rule.Metric(rule.metric).label
    if rule.metric == Rule.Metric.ABSENCE:
        condition = what
    else:
        condition = f"{what} by {Rule.Operator(rule.operator).label} {rule.threshold_minutes} min"
    if rule.occurrence_mode == Rule.OccurrenceMode.WITHIN_PERIOD:
        return f"{condition}, every {rule.required_occurrences} days in a month"
    if rule.occurrence_mode == Rule.OccurrenceMode.CONSECUTIVE_WORKDAYS:
        return f"{condition}, {rule.required_occurrences} working days in a row"
    return f"{condition}, each day"


def describe_deduction(rule, currency=""):
    value = format(Decimal(rule.deduction_value).normalize(), "f")
    method = rule.deduction_method
    if method == Rule.DeductionMethod.ACTUAL_MINUTES:
        text = "The minutes themselves" if rule.metric != Rule.Metric.ABSENCE else "The day's shift minutes"
    elif method == Rule.DeductionMethod.FIXED_MINUTES:
        text = f"{value} minutes of pay"
    elif method == Rule.DeductionMethod.DAY_FRACTION:
        text = f"{value} day{'s' if Decimal(rule.deduction_value) > 1 else ''} of pay"
    elif method == Rule.DeductionMethod.FULL_DAY:
        text = "A full day's pay"
    else:
        text = f"{Decimal(rule.deduction_value):,.2f} {currency}".strip()
    if rule.maximum_deduction:
        text += f", at most {Decimal(rule.maximum_deduction):,.2f} {currency} a month".rstrip()
    if rule.exclusive_group:
        text += f" (group {rule.exclusive_group})"
    return text
