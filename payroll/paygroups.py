"""Weekly pay groups (2026-10-24, docs/payroll-periods/00-PLAN.md item 4).

Everybody is "Monthly staff" unless a ``PayGroupMember`` row puts them in a
weekly ``PayGroup`` on that day: their own row first (it may also keep them
monthly), else their department's. The monthly salary pays the days someone
is monthly, a weekly run the days they are in its group, so somebody moved
from one to the other is split with no day paid twice. Somebody on a monthly
salary stays Monthly staff whatever their department (plan decision 4.1): a
weekly group pays a daily or hourly rate.
"""

import datetime
from collections import defaultdict

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from auditlog.services import record_company_event
from common.tenant import use_company
from organization.services import require_structure_manager


class Groups:
    """Who is in which pay group between two dates, loaded once."""

    def __init__(self, company_id, start, end):
        from payroll.models import PayGroupMember

        with use_company(company_id):
            rows = list(
                PayGroupMember.objects.filter(status=PayGroupMember.Status.ACTIVE,
                                              effective_from__lte=end)
                .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=start))
                .order_by("effective_from", "pk")
                .values_list("employee_id", "department_id", "pay_group_id",
                             "effective_from", "effective_to"))
        self.by_employee, self.by_department = defaultdict(list), defaultdict(list)
        for employee_id, department_id, group_id, first, until in rows:
            target = (self.by_employee[employee_id] if employee_id
                      else self.by_department[department_id])
            target.append((first, until, group_id))
        self.any = bool(rows)
        self.monthly_salary = defaultdict(list)
        if self.any:
            from employees.models import EmployeeCompensation

            with use_company(company_id):
                for employee_id, since, until in (
                    EmployeeCompensation.objects
                    .filter(pay_basis=EmployeeCompensation.PayBasis.MONTHLY)
                    .exclude(status__in=["cancelled", "draft"])
                    .values_list("employee_id", "effective_from", "effective_to")):
                    self.monthly_salary[employee_id].append(
                        (timezone.localdate(since), timezone.localdate(until) if until else None))

    @staticmethod
    def _on(rows, day):
        found = None
        for first, until, group_id in rows:
            if first <= day and (until is None or day < until):
                found = (group_id,)
        return found

    def group_id(self, employee_id, department_id, day):
        """The weekly pay group's id on ``day``, or None for Monthly staff."""
        if any(since <= day and (until is None or day < until)
               for since, until in self.monthly_salary.get(employee_id, ())):
            return None
        own = self._on(self.by_employee.get(employee_id, ()), day)
        if own is not None:
            return own[0]
        department = self._on(self.by_department.get(department_id, ()), day)
        return department[0] if department is not None else None


def week_of(group, day):
    """``(first, last)`` of the group's week that holds ``day``."""
    first = day - datetime.timedelta(days=(day.weekday() - group.week_start) % 7)
    return first, first + datetime.timedelta(days=6)


def weeks_in_month(group, year, month):
    """The group's weeks that end in this calendar month, in order (a week is
    listed under the month it ends in, so none is in two months)."""
    first, last = week_of(group, datetime.date(year, month, 1))
    weeks = []
    while last.month == month:
        weeks.append((first, last))
        first, last = first + datetime.timedelta(days=7), last + datetime.timedelta(days=7)
    return weeks


def is_last_week_of_month(last):
    """A week ending on ``last`` is the month's last week ending in it."""
    return (last + datetime.timedelta(days=7)).month != last.month


def week_name(first, last):
    return f"Week {first:%d %b} - {last:%d %b %Y}"


# --------------------------------------------------------------------------
# Setting them up
# --------------------------------------------------------------------------

def create_group(*, actor, company_id, name, week_start):
    from payroll.models import PayGroup

    membership = require_structure_manager(actor, company_id)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Give the group a name, e.g. Weekly workers."})
    with transaction.atomic(), use_company(company_id):
        if PayGroup.objects.filter(name__iexact=name).exists():
            raise ValidationError({"name": f"There is already a pay group called {name}."})
        group = PayGroup.objects.create(company=membership.company, name=name,
                                        week_start=int(week_start), created_by=actor,
                                        updated_by=actor)
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="payroll.pay_group_created", obj=group,
                             after={"name": group.name, "week_start": group.week_start})
    return group


def _finalised_on(company_id, day, group_id=None):
    """A finalised salary the change would reach into: the Monthly staff's
    month holding ``day``, or the group's week holding it."""
    from payroll.models import PayrollRun

    runs = PayrollRun.objects.filter(status=PayrollRun.Status.POSTED,
                                     payroll_period__start_date__lte=day,
                                     payroll_period__end_date__gte=day)
    monthly = runs.filter(pay_group__isnull=True).select_related("payroll_period").first()
    if monthly is not None:
        return monthly.payroll_period.name
    if group_id is not None:
        weekly = runs.filter(pay_group_id=group_id).select_related("payroll_period").first()
        if weekly is not None:
            return weekly.payroll_period.name
    return None


def set_member(*, actor, company_id, effective_from, pay_group=None, department=None,
               employee=None):
    """Put a department, or one person, in ``pay_group`` from a date; for a
    person, ``pay_group=None`` keeps them monthly. The date must be the first
    day of a week of the group they join (or leave), and not inside a
    finalised salary. A row already in force for the same department or person
    ends that day."""
    from payroll.models import PayGroupMember

    membership = require_structure_manager(actor, company_id)
    if (department is None) == (employee is None):
        raise ValidationError("Choose a department or a person.")
    if department is not None and pay_group is None:
        raise ValidationError({"pay_group": "Choose the weekly pay group for the department."})
    if effective_from is None:
        raise ValidationError({"effective_from": "Choose the first day."})
    with transaction.atomic(), use_company(company_id):
        target = {"department": department} if department is not None else {"employee": employee}
        current = (PayGroupMember.objects.select_for_update()
                   .filter(status=PayGroupMember.Status.ACTIVE, **target)
                   .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=effective_from))
                   .order_by("-effective_from").first())
        leaving = current.pay_group if current is not None else None
        anchor = pay_group or leaving
        if anchor is not None and effective_from.weekday() != anchor.week_start:
            raise ValidationError({"effective_from": (
                f"{anchor.name}'s weeks start on {anchor.get_week_start_display()}: choose a "
                f"{anchor.get_week_start_display()}.")})
        finalised = _finalised_on(company_id, effective_from,
                                  anchor.pk if anchor is not None else None)
        if finalised:
            raise ValidationError({"effective_from": (
                f"{finalised} is finalised and holds that day: choose a later start.")})
        if current is not None:
            if current.effective_from >= effective_from:
                current.status = PayGroupMember.Status.CANCELLED
            else:
                current.effective_to = effective_from
            current.updated_by = actor
            current.save(update_fields=["status", "effective_to", "updated_by", "updated_at"])
        row = PayGroupMember.objects.create(
            company=membership.company, pay_group=pay_group, effective_from=effective_from,
            created_by=actor, updated_by=actor, **target)
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.pay_group_member_set", obj=row,
            before={"pay_group": leaving.name if leaving else "Monthly staff"} if current else {},
            after={"pay_group": pay_group.name if pay_group else "Monthly staff",
                   "department": department.name if department else None,
                   "employee": employee.full_name if employee else None,
                   "from": effective_from.isoformat()})
    return row


def start_days(group, company_id, today=None, *, before=4, after=8):
    """The days someone can join or leave ``group`` from: the start of this
    week, ``before`` weeks back and ``after`` ahead, leaving out any inside a
    finalised salary. ``[(day, label)]``, this week's first (2026-10-25: a
    list to choose from instead of a date the form then refuses)."""
    today = today or timezone.localdate()
    this_week = week_of(group, today)[0]
    days = [this_week + datetime.timedelta(weeks=n) for n in range(-before, after + 1)]
    with use_company(company_id):
        days = [day for day in days if not _finalised_on(company_id, day, group.pk)]
    # The one chosen first: this week, else the first open one after it.
    chosen = next((day for day in days if day >= this_week), days[-1] if days else None)
    labelled = []
    for day in days:
        label = f"{day:%a %d %b %Y}"
        if day == this_week:
            label += " (this week)"
        elif day == chosen:
            label += " (first open week)"
        labelled.append((day, label))
    labelled.sort(key=lambda pair: (pair[0] != chosen, pair[0]))
    return labelled


def update_group(*, actor, company_id, group, name, week_start):
    """Rename a group; its week start changes only while nobody is in it and
    it has no salary yet (a week's dates must never move under a person)."""
    from payroll.models import PayGroup

    membership = require_structure_manager(actor, company_id)
    name = (name or "").strip()
    if not name:
        raise ValidationError({"name": "Give the group a name, e.g. Weekly workers."})
    with transaction.atomic(), use_company(company_id):
        group = PayGroup.objects.select_for_update().get(pk=group.pk)
        if PayGroup.objects.filter(name__iexact=name).exclude(pk=group.pk).exists():
            raise ValidationError({"name": f"There is already a pay group called {name}."})
        week_start = int(week_start)
        if week_start != group.week_start and (
                group.members.filter(status="active").exists() or group.runs.exists()):
            raise ValidationError({"week_start": (
                f"{group.name} already has people or a salary, so its weeks cannot move. "
                "Make a new group instead.")})
        before = {"name": group.name, "week_start": group.week_start}
        group.name, group.week_start, group.updated_by = name, week_start, actor
        group.save(update_fields=["name", "week_start", "updated_by", "updated_at"])
        record_company_event(actor=actor, membership=membership, company=membership.company,
                             action="payroll.pay_group_changed", obj=group, before=before,
                             after={"name": group.name, "week_start": group.week_start})
    return group


def end_member(*, actor, company_id, member, effective_to):
    """Back to monthly from ``effective_to`` (the first monthly day): the row
    ends that day, or is cancelled when that is on or before its start."""
    from payroll.models import PayGroupMember

    membership = require_structure_manager(actor, company_id)
    with transaction.atomic(), use_company(company_id):
        member = PayGroupMember.objects.select_for_update(of=("self",)).select_related(
            "pay_group", "department", "employee").get(pk=member.pk)
        group = member.pay_group
        if group is not None and effective_to.weekday() != group.week_start:
            raise ValidationError({"effective_to": (
                f"{group.name}'s weeks start on {group.get_week_start_display()}: choose a "
                f"{group.get_week_start_display()}.")})
        finalised = _finalised_on(company_id, effective_to, group.pk if group else None)
        if finalised:
            raise ValidationError({"effective_to": (
                f"{finalised} is finalised and holds that day: choose a later one.")})
        if effective_to <= member.effective_from:
            member.status = PayGroupMember.Status.CANCELLED
        else:
            member.effective_to = effective_to
        member.updated_by = actor
        member.save(update_fields=["status", "effective_to", "updated_by", "updated_at"])
        record_company_event(
            actor=actor, membership=membership, company=membership.company,
            action="payroll.pay_group_member_ended", obj=member,
            after={"pay_group": group.name if group else "Monthly staff",
                   "department": member.department.name if member.department else None,
                   "employee": member.employee.full_name if member.employee else None,
                   "until": effective_to.isoformat(), "status": member.status})
    return member
