"""Attendance bonus (2026-11-04): a bonus for being present every working day.

Optional, off until Salary settings switch it on (saved with the salary rules,
from a month, so every month recalculates with its own rules):

- **Monthly staff**: every salary month.
- **Weekly workers**: every week, *or* once a month - never both (the settings
  refuse it; if both were ever saved, the weekly bonus wins). The monthly one
  is paid in the last week that ends in the month, like their allowances, for
  the weeks that end in that month.

Who gets it: present on every working day of the period, or on at least the
number of days set (never more than the period's working days). Present days
are Present (late included) and days without a check-out; a half day, any
leave and an absence are not. Holidays and weekly offs are not working days.
Somebody who joined or left inside the period, was inactive in it, or was paid
part of it by another run (moved between monthly and weekly) does not get it.

How much: the employee's own percent or flat amount, else their department's,
else the company's percent - a percent of the period's basic pay (monthly
basic, or the pay for the days or hours worked), a flat amount per bonus. On
one row the percent wins over the amount.

It is the payslip's last earning before rounding: penalties (and their cap,
a percent of gross) are worked out before it, exactly as without it.
"""

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction

from attendance.models import AttendanceRecord
from auditlog.services import record_company_event
from common.tenant import use_company
from payroll.models import AttendanceBonusRate

Status = AttendanceRecord.AttendanceStatus
WORKING = (Status.PRESENT, Status.HALF_DAY, Status.LEAVE, Status.INCOMPLETE, Status.ABSENT)
PRESENT = (Status.PRESENT, Status.INCOMPLETE)
CENT = Decimal("0.01")
ONE = Decimal("1")
CODE = "ATTENDANCE_BONUS"
BASIC_CODES = ("BASIC", "DAYS", "HOURS")

MONTHLY, WEEKLY, WEEKLY_BY_MONTH = "monthly", "weekly", "weekly_by_month"
LABELS = {MONTHLY: "Attendance bonus", WEEKLY: "Weekly attendance bonus",
          WEEKLY_BY_MONTH: "Monthly attendance bonus"}


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def kind_for(rules, pay_group, last, is_last_week):
    """Which bonus this run pays, or None: monthly staff's, a week's, or
    weekly workers' monthly one (in the last week ending in the month)."""
    if pay_group is None:
        return MONTHLY if rules.bonus_monthly else None
    if rules.bonus_weekly:
        return WEEKLY
    if rules.bonus_weekly_by_month and is_last_week:
        return WEEKLY_BY_MONTH
    return None


def min_days_for(rules, kind):
    return rules.bonus_weekly_days if kind == WEEKLY else rules.bonus_monthly_days


def basic_of(lines):
    """The basic pay in a set of payslip lines: monthly basic, or the pay for
    days or hours worked, less basic not paid for inactive days."""
    earned = sum((amount for kind, code, *_, amount in lines if kind == "earning" and code in BASIC_CODES),
                 Decimal("0"))
    return earned - sum((amount for kind, code, *_, amount in lines
                         if kind == "deduction" and code == "INACTIVE"), Decimal("0"))


def count(records):
    """``(present, working)`` days."""
    working = sum(1 for r in records if r.attendance_status in WORKING)
    present = sum(1 for r in records if r.attendance_status in PRESENT)
    return present, working


@dataclass
class Bonus:
    """One employee's bonus for one period: ``calculate_pay(attendance_bonus=)``."""

    kind: str
    min_days: object                     # int or None (every working day)
    percent: object = None               # Decimal: of the basic pay
    amount: object = None                # Decimal: flat, per bonus
    source: str = "company"              # "employee", "department" or "company"
    blocked: str = ""                    # why not, whatever the attendance
    records: object = None               # the days it is for, when not the payslip's (weekly by month)
    basic: object = None                 # the basic pay of those days
    detail: dict = field(default_factory=dict)

    def line(self, basic_earned, records):
        """``(line or None, what was decided)`` for the payslip."""
        days = self.records if self.records is not None else records
        present, working = count(days)
        needed = working if self.min_days is None else min(int(self.min_days), working)
        detail = {"kind": self.kind, "present": present, "working": working, "needed": needed,
                  "source": self.source, "percent": _text(self.percent), "amount": _text(self.amount)}
        if self.blocked:
            detail["paid"], detail["why_not"] = False, self.blocked
            return None, detail
        if not working or present < needed:
            detail["paid"] = False
            detail["why_not"] = (f"present {present} of {working} working days" +
                                 (f", needs {needed}" if needed != working else ""))
            return None, detail
        base = self.basic if self.basic is not None else basic_earned
        if self.percent is not None:
            value = money(base * self.percent / Decimal("100"))
            how = f"{_text(self.percent)}% of basic {base:,.2f}"
            detail["base"] = str(money(base))
        else:
            value = money(self.amount)
            how = "flat amount"
        detail["paid"], detail["value"] = value > 0, str(value)
        if value <= 0:
            return None, detail
        label = f"{LABELS[self.kind]} ({present} of {working} days present, {how})"
        return ("earning", CODE, label, ONE, value, value), detail


def _text(value):
    return None if value is None else format(Decimal(value).normalize(), "f")


# --- rates -------------------------------------------------------------------------------------

def rates(company_id):
    """``({employee_id: row}, {department_id: row})``."""
    by_employee, by_department = {}, {}
    for row in AttendanceBonusRate.all_objects.filter(company_id=company_id):
        if row.employee_id:
            by_employee[row.employee_id] = row
        else:
            by_department[row.department_id] = row
    return by_employee, by_department


def pick(rules, found, employee_id, department_id):
    """``(source, percent, amount)``: the employee's, the department's or the company's."""
    by_employee, by_department = found
    for source, row in (("employee", by_employee.get(employee_id)),
                        ("department", by_department.get(department_id))):
        if row is not None:
            if row.percent is not None:
                return source, row.percent, None
            return source, None, row.amount
    return "company", rules.bonus_percent, None


def blocked_by(employee, first, last, inactive=False, other_run=False):
    """Why somebody gets no bonus for ``first``..``last`` whatever their days, or ""."""
    if employee.joining_date and employee.joining_date > first:
        return f"joined on {employee.joining_date:%d %b %Y}"
    if employee.leaving_date and employee.leaving_date < last:
        return f"left on {employee.leaving_date:%d %b %Y}"
    if inactive:
        return "inactive part of the period"
    if other_run:
        return "paid part of the period by the other pay run"
    return ""


# --- setting a department's or an employee's own -----------------------------------------------

def _value(value):
    if value in (None, ""):
        return None
    return Decimal(value)


@transaction.atomic
def set_department_rate(*, actor, company_id, department, percent=None, amount=None):
    """A department's own percent or flat amount; both empty removes it."""
    from organization.services import require_structure_manager

    membership = require_structure_manager(actor, company_id)
    return _save(actor, membership, company_id, {"department": department}, percent, amount, department)


@transaction.atomic
def set_employee_rate(*, actor, company_id, employee_id, percent=None, amount=None):
    """One employee's own percent or flat amount; both empty removes it.
    Whoever may change their salary may change this."""
    from organization.employee_edit_services import get_employee_for_edit

    membership, employee, _, _ = get_employee_for_edit(
        actor=actor, company_id=company_id, employee_id=employee_id, code="salary.prepare")
    return _save(actor, membership, company_id, {"employee": employee}, percent, amount, employee)


def _save(actor, membership, company_id, target, percent, amount, obj):
    percent, amount = _value(percent), _value(amount)
    with use_company(company_id):
        row = AttendanceBonusRate.objects.select_for_update().filter(**target).first()
        before = {"percent": _text(row.percent), "amount": _text(row.amount)} if row else {}
        if percent is None and amount is None:
            if row is None:
                return None
            row.delete()
            row = None
        else:
            if row is None:
                row = AttendanceBonusRate(company=membership.company, created_by=actor, **target)
            # The percent wins: a row keeps one of the two.
            row.percent, row.amount = percent, (None if percent is not None else amount)
            row.updated_by = actor
            try:
                row.full_clean()
            except ValidationError as exc:
                raise ValidationError({{"percent": "bonus_percent", "amount": "bonus_amount"}.get(key, key): value
                                       for key, value in exc.message_dict.items()}) from None
            row.save()
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.attendance_bonus_set", obj=obj, before=before,
            after={"percent": _text(row.percent), "amount": _text(row.amount)} if row else {})
    return row
