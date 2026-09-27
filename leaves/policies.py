"""Leave policies, versions, the ledger and balances (Phase E, 2026-09-27;
docs/LEAVE_FULL_DESIGN.md §2-3).

- Which rule applies to someone on a day: their own policy row covering it,
  else the company default; within the policy, the latest version started by
  then; within the version, the rule for that leave type (``Book``).
- The ledger holds what was given: accruals (monthly, or yearly prorated by
  the months left), carry-forward on 1 January (what was left, up to the
  cap), expiry of carried days not used in time, and adjustments by hand.
  Leave taken is never written here: it is the live ``LeaveDay`` units.
- ``post`` writes the automatic entries due up to a date; it can run any
  number of times. Accruals depend only on policies and dates; carry-forward
  and expiry are worked out again on each run, since they depend on leave
  taken. When a policy change reaches back, the automatic entries from that
  date are dropped and posted again (``forget_from``); adjustments made by
  hand are never removed.
- ``policy_check`` refuses leave the policy does not allow; a leave type with
  no rule for someone falls back to its days per year (``services.check_allowance``).

Salary and attendance never read any of this.
"""

import calendar
import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q, Sum
from django.utils import timezone

from accounts.models import CompanyMembership
from auditlog.services import record_company_event
from common.choices import ActiveStatus
from common.tenant import use_company
from leaves.models import (
    EmployeeLeavePolicy,
    LeaveDay,
    LeaveLedgerEntry,
    LeavePolicy,
    LeavePolicyRule,
    LeavePolicyVersion,
    LeaveType,
)

Kind = LeaveLedgerEntry.Kind
Accrual = LeavePolicyRule.Accrual
Role = CompanyMembership.Role
LIVE = (LeaveDay.Status.RESERVED, LeaveDay.Status.APPROVED, LeaveDay.Status.CONSUMED)
ZERO, HALF, CENT = Decimal("0"), Decimal("0.5"), Decimal("0.01")
#: Who sets up policies: the owner or company administrator (as leave types).
POLICY_ROLES = (Role.OWNER, Role.COMPANY_ADMIN)
#: Who gives someone a policy or adjusts a balance: those and HR.
BALANCE_ROLES = (Role.OWNER, Role.COMPANY_ADMIN, Role.HR)
#: Joining (or getting the policy) on or before this day of a month earns it.
MONTH_CUTOFF = 15


def _days(value):
    return f"{Decimal(value).normalize():f}"


def _add_months(day, months):
    month = day.month - 1 + months
    year, month = day.year + month // 12, month % 12 + 1
    return day.replace(year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1]))


def prorate(days, months):
    """``days`` for ``months`` of twelve, to the nearest half day."""
    value = Decimal(days) * Decimal(months) / Decimal(12)
    return (value * 2).quantize(Decimal("1"), ROUND_HALF_UP) / 2


# --------------------------------------------------------------------------
# Which rule applies
# --------------------------------------------------------------------------


class Book:
    """One employee's policies, versions and rules, read once. Call inside
    the company's tenant context."""

    def __init__(self, employee):
        self.employee = employee
        self.own = list(EmployeeLeavePolicy.objects.select_related("policy")
                        .filter(employee=employee).order_by("effective_from"))
        self.default = LeavePolicy.objects.filter(is_default=True).first()
        ids = {row.policy_id for row in self.own} | ({self.default.pk} if self.default else set())
        self.versions = defaultdict(list)
        self.rules = {}
        for version in (LeavePolicyVersion.objects.filter(policy_id__in=ids)
                        .select_related("policy").prefetch_related("rules__leave_type")
                        .order_by("effective_from")):
            self.versions[version.policy_id].append(version)
            self.rules[version.pk] = {rule.leave_type_id: rule for rule in version.rules.all()}

    def policy_on(self, on):
        for row in self.own:
            if row.effective_from <= on and (row.effective_to is None or on <= row.effective_to):
                return row.policy
        return self.default

    def version_on(self, policy, on):
        found = None
        for version in self.versions.get(policy.pk, ()):
            if version.effective_from <= on:
                found = version
        return found

    def rule_on(self, leave_type_id, on):
        """``(rule, version)`` for this leave type on a day, or ``(None, None)``."""
        policy = self.policy_on(on)
        version = self.version_on(policy, on) if policy is not None else None
        if version is None:
            return None, None
        rule = self.rules[version.pk].get(leave_type_id)
        return (rule, version) if rule is not None else (None, None)

    def employed(self, on):
        employee = self.employee
        return ((employee.joining_date is None or employee.joining_date <= on)
                and (employee.leaving_date is None or on <= employee.leaving_date))

    def first_day(self):
        """The first day any rule could apply to them, or None."""
        starts = []
        for row in self.own:
            versions = self.versions.get(row.policy_id)
            if versions:
                starts.append(max(row.effective_from, versions[0].effective_from))
        if self.default is not None and self.versions.get(self.default.pk):
            starts.append(self.versions[self.default.pk][0].effective_from)
        if not starts:
            return None
        first = min(starts)
        if self.employee.joining_date and self.employee.joining_date > first:
            first = self.employee.joining_date
        return first

    def marks(self, year, month):
        """The days in a month that can start an accrual: its first day, and a
        joining or policy start on or before the cut-off."""
        first = datetime.date(year, month, 1)
        days = {first}
        joined = self.employee.joining_date
        if joined and joined.year == year and joined.month == month:
            days.add(joined)
        for row in self.own:
            if row.effective_from.year == year and row.effective_from.month == month:
                days.add(row.effective_from)
        for versions in self.versions.values():
            for version in versions:
                if version.effective_from.year == year and version.effective_from.month == month:
                    days.add(version.effective_from)
        return sorted(day for day in days if day == first or day.day <= MONTH_CUTOFF)


def book_for(employee):
    return Book(employee)


# --------------------------------------------------------------------------
# Posting
# --------------------------------------------------------------------------


def _types():
    return list(LeaveType.objects.all())


def _entry(employee, leave_type, *, year, day, kind, key, units, version, note=""):
    """An automatic entry, once. Accruals are kept as first posted."""
    if units == ZERO:
        return None
    try:
        with transaction.atomic():
            entry, _ = LeaveLedgerEntry.objects.get_or_create(
                company_id=employee.company_id, employee=employee, leave_type=leave_type,
                kind=kind, period_key=key,
                defaults={"year": year, "entry_date": day, "units": units,
                          "policy_version": version, "note": note[:255]})
    except IntegrityError:
        entry = None
    return entry


def _worked_out(employee, leave_type, *, year, day, kind, key, units, version, note=""):
    """A carry-forward or expiry: worked out again on every run."""
    existing = LeaveLedgerEntry.objects.filter(employee=employee, leave_type=leave_type,
                                               kind=kind, period_key=key).first()
    if units == ZERO:
        if existing is not None:
            existing.delete()
        return None
    if existing is None:
        return _entry(employee, leave_type, year=year, day=day, kind=kind, key=key,
                      units=units, version=version, note=note)
    if (existing.units, existing.entry_date, existing.note) != (units, day, note[:255]):
        existing.units, existing.entry_date, existing.note = units, day, note[:255]
        existing.policy_version = version
        existing.save(update_fields=["units", "entry_date", "note", "policy_version"])
    return existing


def taken(employee, leave_type, year, *, before=None):
    days = LeaveDay.objects.filter(employee=employee, request_segment__leave_type=leave_type,
                                   work_date__year=year, status__in=LIVE)
    if before is not None:
        days = days.filter(work_date__lt=before)
    return days.aggregate(total=Sum("balance_units"))["total"] or ZERO


def post(employee, until=None, book=None):
    """Write the automatic entries due up to ``until`` (today by default).
    Repeatable. Call inside the company's tenant context."""
    until = until or timezone.localdate()
    book = book or Book(employee)
    start = book.first_day()
    if start is None or start > until:
        return book
    types = _types()
    for year in range(start.year, until.year + 1):
        if year > start.year:
            _carry(book, types, year, until)
        _accrue(book, types, year, until, start)
        _expire(book, types, year, until)
    return book


def _accrue(book, types, year, until, start):
    employee = book.employee
    for month in range(1, 13):
        if datetime.date(year, month, 1) > until:
            break
        for leave_type in types:
            for mark in book.marks(year, month):
                if mark > until or mark < start or not book.employed(mark):
                    continue
                rule, version = book.rule_on(leave_type.pk, mark)
                if rule is None:
                    continue
                if rule.accrual == Accrual.MONTHLY:
                    _entry(employee, leave_type, year=year, day=mark, kind=Kind.ACCRUAL,
                           key=f"{year}-{month:02d}",
                           units=(rule.days_per_year / 12).quantize(CENT, ROUND_HALF_UP),
                           version=version, note=f"{version.policy.name}: {month:02d}/{year}")
                else:
                    months = 13 - month
                    units = prorate(rule.days_per_year, months)
                    _entry(employee, leave_type, year=year, day=mark, kind=Kind.ACCRUAL,
                           key=str(year), units=units, version=version,
                           note=(f"{version.policy.name}: {year}" if months == 12 else
                                 f"{version.policy.name}: {year}, {months} of 12 months"))
                break


def _carry(book, types, year, until):
    employee = book.employee
    first = datetime.date(year, 1, 1)
    if first > until:
        return
    for leave_type in types:
        rule, version = book.rule_on(leave_type.pk, first)
        units = ZERO
        if rule is not None and rule.carry_forward_days and book.employed(first):
            left = balance(employee, leave_type, year - 1).left
            units = min(max(left, ZERO), rule.carry_forward_days)
        _worked_out(employee, leave_type, year=year, day=first, kind=Kind.CARRY_FORWARD,
                    key=f"cf-{year}", units=units, version=version,
                    note=f"Left from {year - 1}, up to {_days(rule.carry_forward_days)}"
                    if rule is not None and rule.carry_forward_days else "")


def _expire(book, types, year, until):
    employee = book.employee
    first = datetime.date(year, 1, 1)
    for entry in LeaveLedgerEntry.objects.select_related("policy_version").filter(
            employee=employee, year=year, kind=Kind.CARRY_FORWARD):
        rule = book.rules.get(entry.policy_version_id, {}).get(entry.leave_type_id)
        months = rule.carry_forward_expires_months if rule is not None else None
        units = ZERO
        day = first
        if months:
            day = _add_months(first, months)
            if day <= until:
                used = taken(employee, entry.leave_type, year, before=day)
                units = -max(ZERO, entry.units - used)
        _worked_out(employee, entry.leave_type, year=year, day=day, kind=Kind.EXPIRY,
                    key=f"exp-{year}", units=units, version=entry.policy_version,
                    note=f"Carried days not used by {day:%d %b %Y}")


def forget_from(day, employees=None):
    """Drop the automatic entries dated from ``day`` (for these employees, or
    everyone), to be posted again under what now applies. Adjustments stay."""
    entries = LeaveLedgerEntry.objects.exclude(kind=Kind.ADJUSTMENT).filter(entry_date__gte=day)
    if employees is not None:
        entries = entries.filter(employee__in=list(employees))
    entries.delete()


# --------------------------------------------------------------------------
# Balances
# --------------------------------------------------------------------------


@dataclass
class Balance:
    leave_type: LeaveType
    year: int
    accrued: Decimal = ZERO
    carried: Decimal = ZERO
    expired: Decimal = ZERO
    adjusted: Decimal = ZERO
    taken: Decimal = ZERO
    rule: object = None
    policy: object = None
    entries: list = field(default_factory=list)

    @property
    def given(self):
        return self.accrued + self.carried - self.expired + self.adjusted

    @property
    def left(self):
        return self.given - self.taken


def balance(employee, leave_type, year, as_of=None, *, with_entries=False):
    """What they have of one leave type in a year (entries dated by ``as_of``
    count; leave taken counts wherever it falls in the year)."""
    result = Balance(leave_type=leave_type, year=year)
    entries = LeaveLedgerEntry.objects.filter(employee=employee, leave_type=leave_type,
                                              year=year)
    if as_of is not None:
        entries = entries.filter(entry_date__lte=as_of)
    for kind, units in entries.values("kind").annotate(total=Sum("units")).values_list(
            "kind", "total"):
        if kind == Kind.ACCRUAL:
            result.accrued = units
        elif kind == Kind.CARRY_FORWARD:
            result.carried = units
        elif kind == Kind.EXPIRY:
            result.expired = -units
        else:
            result.adjusted = units
    result.taken = taken(employee, leave_type, year)
    if with_entries:
        result.entries = list(entries.select_related("created_by").order_by("entry_date", "pk"))
    return result


def balances(employee, year, today=None):
    """Every leave type their policy covers in ``year`` (or that has entries),
    as of today (or the year's end, for a year gone by). Call inside the
    company's tenant context."""
    today = today or timezone.localdate()
    as_of = min(today, datetime.date(year, 12, 31))
    if as_of < datetime.date(year, 1, 1):
        as_of = datetime.date(year, 1, 1)
    book = post(employee, until=as_of)
    with_entries = set(LeaveLedgerEntry.objects.filter(employee=employee, year=year)
                       .values_list("leave_type_id", flat=True))
    result = []
    for leave_type in _types():
        rule, version = book.rule_on(leave_type.pk, as_of)
        month = as_of.month
        while rule is None and month > 1:
            # Covered earlier in the year, though not any more.
            month -= 1
            rule, version = book.rule_on(leave_type.pk, datetime.date(year, month, 1))
        if rule is None and leave_type.pk not in with_entries:
            continue
        found = balance(employee, leave_type, year, as_of=as_of, with_entries=True)
        found.rule = rule
        found.policy = version.policy if version is not None else None
        result.append(found)
    return result


# --------------------------------------------------------------------------
# The check on new leave
# --------------------------------------------------------------------------


def covered_dates(employee, leave_type, dates):
    """The dates on which a policy rule decides this leave type for them."""
    with use_company(employee.company_id):
        book = Book(employee)
        return {on for on in dates if book.rule_on(leave_type.pk, on)[0] is not None}


def policy_check(employee, leave_type, days, shape, fitted):
    """Refuse leave their policy does not allow: a half day or hours where the
    rule says no, or more than their balance on those dates. No rule on a
    date: nothing here (the leave type's days per year decide)."""
    with use_company(employee.company_id):
        book = Book(employee)
        needed, last = defaultdict(lambda: ZERO), {}
        for on, *_ in days:
            rule, version = book.rule_on(leave_type.pk, on)
            if rule is None:
                continue
            if shape.is_half and not rule.allow_half_day:
                raise ValidationError({"duration": (
                    f"{version.policy.name} does not allow half days of {leave_type.name}.")})
            if shape.is_hourly and not rule.allow_hourly:
                raise ValidationError({"duration": (
                    f"{version.policy.name} does not allow {leave_type.name} by the hour.")})
            if rule.allow_negative:
                continue
            needed[on.year] += fitted[on][3]
            last[on.year] = max(last.get(on.year, on), on)
        for year, units in sorted(needed.items()):
            post(employee, until=max(last[year], timezone.localdate()), book=book)
            left = balance(employee, leave_type, year, as_of=last[year]).left
            if units > left:
                raise ValidationError({"leave_type": (
                    f"{leave_type.name}: {_days(max(left, ZERO))} days left in {year} "
                    f"by {last[year]:%d %b}; this leave needs {_days(units)}.")})


# --------------------------------------------------------------------------
# Giving someone a policy, adjusting a balance
# --------------------------------------------------------------------------


def _member(actor, company_id, roles, what):
    from accounts.services import get_active_memberships

    membership = get_active_memberships(actor).filter(company_id=company_id).select_related(
        "company").first() if actor is not None and actor.is_authenticated else None
    if membership is None or membership.role not in roles:
        raise PermissionDenied(f"{what} is for the owner, the company administrator"
                               f"{' or HR' if Role.HR in roles else ''}.")
    return membership


def may_manage_balances(actor, company_id):
    try:
        _member(actor, company_id, BALANCE_ROLES, "")
    except PermissionDenied:
        return False
    return True


@transaction.atomic
def assign_policy(*, actor, company_id, employee, policy, effective_from):
    """Give ``employee`` ``policy`` from a date (None: back to the company
    default). Their automatic entries from that date are worked out again."""
    membership = _member(actor, company_id, BALANCE_ROLES, "Giving someone a leave policy")
    with use_company(company_id):
        if policy is not None and policy.status != ActiveStatus.ACTIVE:
            raise ValidationError({"policy": "Choose an active leave policy."})
        later = EmployeeLeavePolicy.objects.filter(employee=employee,
                                                   effective_from__gt=effective_from)
        if later.exists():
            raise ValidationError({"effective_from": (
                "They already have a policy from a later date. Choose a date after it.")})
        before = [(row.policy.code, row.effective_from.isoformat()) for row in
                  EmployeeLeavePolicy.objects.select_related("policy").filter(employee=employee)]
        same_day = EmployeeLeavePolicy.objects.filter(employee=employee,
                                                      effective_from=effective_from).first()
        if same_day is not None:
            same_day.delete()
        EmployeeLeavePolicy.objects.filter(employee=employee, effective_to__isnull=True).update(
            effective_to=effective_from - datetime.timedelta(days=1))
        if policy is not None:
            row = EmployeeLeavePolicy(employee=employee, policy=policy,
                                      effective_from=effective_from, created_by=actor,
                                      updated_by=actor)
            row.company_id = company_id
            row.full_clean()
            row.save()
        forget_from(effective_from, [employee])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.policy_given", obj=employee,
            before={"policies": before},
            after={"policy": policy.code if policy else "company default",
                   "from": effective_from.isoformat()})
    return policy


@transaction.atomic
def adjust(*, actor, company_id, employee, leave_type, year, units, note):
    """Add or take away days by hand, with a reason."""
    membership = _member(actor, company_id, BALANCE_ROLES, "Adjusting a leave balance")
    note = (note or "").strip()
    if not note:
        raise ValidationError({"note": "Say why, so the next person reading this knows."})
    if not units:
        raise ValidationError({"units": "Give the days to add (or, with a minus, to take away)."})
    with use_company(company_id):
        today = timezone.localdate()
        entry = LeaveLedgerEntry(employee=employee, leave_type=leave_type, year=year,
                                 entry_date=min(today, datetime.date(year, 12, 31))
                                 if year <= today.year else datetime.date(year, 1, 1),
                                 kind=Kind.ADJUSTMENT, units=units, note=note[:255],
                                 created_by=actor)
        entry.company_id = company_id
        entry.full_clean()
        entry.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="leave.balance_adjusted", obj=employee,
            after={"leave_type": leave_type.code, "year": year, "units": str(units),
                   "note": note})
    return entry


def policy_of(employee, on=None):
    """The policy they have on a day (their own or the default), or None."""
    with use_company(employee.company_id):
        return Book(employee).policy_on(on or timezone.localdate())


# --------------------------------------------------------------------------
# What a person's leave looks like, for the pages
# --------------------------------------------------------------------------


def overview(employee, year, today=None):
    """One row per leave type they have something of in ``year``: their
    policy's balance, or - for a type no policy covers - its days per year,
    as before. Call inside the company's tenant context."""
    rows = []
    found = balances(employee, year, today)
    for item in found:
        rows.append({
            "leave_type": item.leave_type, "by_policy": True,
            "policy": item.policy, "rule": item.rule,
            "accrued": item.accrued, "carried": item.carried, "expired": item.expired,
            "adjusted": item.adjusted, "given": item.given, "taken": item.taken,
            "left": item.left, "entries": item.entries,
        })
    covered = {item.leave_type.pk for item in found}
    for leave_type in LeaveType.objects.filter(status=ActiveStatus.ACTIVE,
                                               days_per_year__isnull=False).order_by("name"):
        if leave_type.pk in covered:
            continue
        used = taken(employee, leave_type, year)
        rows.append({
            "leave_type": leave_type, "by_policy": False, "policy": None, "rule": None,
            "given": leave_type.days_per_year, "taken": used,
            "left": leave_type.days_per_year - used, "entries": [],
        })
    return rows


def left_for(employee, leave_type, year):
    """``(left, given)`` of one leave type this year, or None if unlimited."""
    with use_company(employee.company_id):
        for row in overview(employee, year):
            if row["leave_type"].pk == leave_type.pk:
                return row["left"], row["given"]
    return None
