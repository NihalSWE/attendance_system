"""The one-time "Fix setup" page (2026-10-26).

People added from a device ("Add as employees") were placed in their branch's
"Unassigned" department and designation, which has no shift, so attendance
wrote no days for them; some were never put on the branch's other devices; and
their scans from before they were linked were refused as "unknown employee".
This lists who still has one of those problems and fixes them in bulk:

- ``correct_placement``: the right branch, department and designation, written
  over the current placement (it was wrong from its first day, so it is
  corrected, not transferred - attendance reads the placement of each day);
- ``start(kind=SEND)``: put them on every device of their branch
  (``mapping.send_employees``);
- ``start(kind=RECALCULATE)``: rebuild their attendance from a salary month's
  first day;
- ``start(kind=RECHECK)``: judge the refused scans again (``recheck_punches``),
  which recalculates the days that now count.

The three slow ones run in the background like ``load_jobs``: the click writes
a ``SetupFixJob`` row and a thread works through it, a step at a time.
"""

import datetime
import logging
import threading
from dataclasses import dataclass, field

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from common.tenant import use_company
from devices.models import BiometricDevice, SetupFixJob
from devices.services import mapping

logger = logging.getLogger(__name__)

#: People per step: one short transaction each.
STEP = 10
#: A running job whose heartbeat is older than this lost its thread.
STALLED_AFTER = datetime.timedelta(minutes=5)
#: A finished job stays on the page this long.
SHOWN_FOR = datetime.timedelta(hours=2)
FAILURES_KEPT = 50
LEFT = ("resigned", "terminated", "retired", "applicant")

UNASSIGNED = "Unassigned department"
NO_SHIFT = "No shift"
DEVICES = "Not on every device"
PROBLEMS = (UNASSIGNED, NO_SHIFT, DEVICES)


def _in_background():
    return getattr(settings, "SETUP_FIX_IN_BACKGROUND", True)


def _zone(company):
    from zoneinfo import ZoneInfo

    return ZoneInfo(company.timezone or "Asia/Dhaka")


def today(company):
    return timezone.now().astimezone(_zone(company)).date()


# --- who needs fixing --------------------------------------------------------


@dataclass
class Person:
    employee: object
    assignment: object
    code: str
    problems: list = field(default_factory=list)
    missing: list = field(default_factory=list)      # device names they are not on
    on: int = 0
    of: int = 0


def people(company, branch_ids=None):
    """Every working employee, with what is wrong with their setup.

    ``branch_ids`` narrows it to those branches (None: all)."""
    from employees.models import Employee, EmployeeAssignment
    from scheduling.models import DepartmentShift, EmployeeShiftAssignment

    now = timezone.now()
    day = today(company)
    with use_company(company):
        placements = {}
        rows = (EmployeeAssignment.objects.exclude(status="cancelled")
                .filter(effective_from__lte=now).filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
                .select_related("branch", "department", "designation"))
        if branch_ids is not None:
            rows = rows.filter(branch_id__in=branch_ids)
        for row in rows.order_by("employee_id", "-effective_from"):
            placements.setdefault(row.employee_id, row)
        employees = Employee.objects.filter(pk__in=placements).exclude(employment_status__in=LEFT)
        shifted_departments = set(
            DepartmentShift.objects.filter(status="active", effective_from__lte=day)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=day))
            .values_list("department_id", flat=True))
        own_shift = set(
            EmployeeShiftAssignment.objects.filter(effective_from__lte=now)
            .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
            .exclude(status="cancelled").values_list("employee_id", flat=True))
        devices_of = {}
        for device in (BiometricDevice.objects.exclude(status__in=[BiometricDevice.Status.RETIRED,
                                                                    BiometricDevice.Status.SUSPENDED])
                       .order_by("name")):
            if mapping.upload_supported(device):
                devices_of.setdefault(device.branch_id, []).append(device)
        employees = list(employees.order_by("first_name", "last_name"))
        badges = mapping.device_badges(company.pk, [e.pk for e in employees])
    found = []
    for employee in employees:
        placement = placements[employee.pk]
        person = Person(employee=employee, assignment=placement, code=placement.employee_code)
        if placement.department.code == mapping.UNASSIGNED_CODE:
            person.problems.append(UNASSIGNED)
        if placement.department_id not in shifted_departments and employee.pk not in own_shift:
            person.problems.append(NO_SHIFT)
        linked = {link["name"] for link in badges[employee.pk]["names"]}
        branch_devices = devices_of.get(placement.branch_id, [])
        person.of = len(branch_devices)
        person.missing = [d.name for d in branch_devices if d.name not in linked]
        person.on = person.of - len(person.missing)
        if person.missing:
            person.problems.append(DEVICES)
        found.append(person)
    return found


# --- the placement -----------------------------------------------------------


@transaction.atomic
def correct_placement(*, actor, company, employees, branch, department, designation):
    """Write the right branch, department and designation over each person's
    current placement. Returns how many were corrected."""
    from auditlog.services import record_company_event
    from devices.services import panel_access
    from employees.models import EmployeeAssignment

    if department.code == mapping.UNASSIGNED_CODE:
        raise ValidationError({"department": "Choose their real department, not Unassigned."})
    if department.branch_id != branch.pk:
        raise ValidationError({"department": "That department is not in this branch."})
    if designation.department_id != department.pk:
        raise ValidationError({"designation": "That designation is not in this department."})
    membership = panel_access.managing_membership(actor, company.pk)
    now = timezone.now()
    changed = 0
    with use_company(company):
        for employee in employees:
            current = (EmployeeAssignment.objects.select_for_update()
                       .filter(employee=employee).exclude(status="cancelled")
                       .filter(effective_from__lte=now)
                       .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=now))
                       .order_by("-effective_from").first())
            if current is None:
                continue
            before = {"branch": current.branch_id, "department": current.department_id,
                      "designation": current.designation_id}
            after = {"branch": branch.pk, "department": department.pk, "designation": designation.pk}
            if before == after:
                continue
            current.branch, current.department, current.designation = branch, department, designation
            current.updated_by = actor
            current.full_clean()
            current.save()
            record_company_event(actor=actor, membership=membership, company=company,
                                 action="employee.placement_corrected", obj=current,
                                 before=before, after=after)
            changed += 1
    return changed


# --- salary months to start from ---------------------------------------------


def month_starts(company, count=3):
    """``[(first day, "26 Sep 2026 (salary month 26 Sep - 25 Oct)")]``: the
    first days of the last ``count`` salary months, newest first, leaving out
    any month whose salary is already posted (its days never change)."""
    from attendance.services import _is_locked, locked_ranges
    from payroll import periods

    locked = locked_ranges(company.pk)
    year, month = periods.month_of(company.pk, today(company) - datetime.timedelta(days=1))
    starts = []
    for _ in range(count):
        first, last = periods.bounds(company.pk, year, month)
        if not _is_locked(first, locked):
            starts.append((first, f"{first:%d %b %Y} (salary month {first:%d %b} – {last:%d %b})"))
        year, month = (year, month - 1) if month > 1 else (year - 1, 12)
    return starts


# --- people set up now whose first days are missing (2026-10-27) -------------
#
# Added from a device, a person's placement, salary, own shift and device links
# all started on the day they were added. Scans from before that day were
# refused ("unknown employee"), and those days had no placement, so attendance
# wrote nothing for them. Fixing the department later does not reach them.


def move_start_back(*, actor, company, employee, start_day):
    """Start the person's first placement, salary, own shift and device links on
    ``start_day`` when they start later, and their joining date with them.
    Returns what moved, in words. A record that cannot move (another person held
    their Employee ID, or their device number, on those days) is said, not moved."""
    from auditlog.services import record_company_event
    from devices.models import DeviceEnrollment
    from devices.services import panel_access
    from employees.models import EmployeeAssignment, EmployeeCompensation
    from scheduling.models import EmployeeShiftAssignment

    zone = _zone(company)
    start = mapping.day_start(company, start_day)
    moved, refused = [], []

    def move(row, label):
        before = row.effective_from.astimezone(zone).date()
        row.effective_from = start
        if hasattr(row, "updated_by"):
            row.updated_by = actor
        try:
            with transaction.atomic():
                row.save()
        except IntegrityError:
            refused.append(f"{label} stays from {before:%d %b}: someone else held it then")
            return
        moved.append(f"{label} from {start_day:%d %b} (was {before:%d %b})")

    for model, label in ((EmployeeAssignment, "Placement"), (EmployeeCompensation, "Salary"),
                         (EmployeeShiftAssignment, "Own shift")):
        first = (model.objects.filter(employee=employee).exclude(status="cancelled")
                 .order_by("effective_from", "pk").first())
        if first is not None and first.effective_from > start:
            move(first, label)
    links = (DeviceEnrollment.objects.filter(employee=employee)
             .exclude(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED)
             .select_related("device").order_by("device_id", "effective_from", "pk"))
    seen = set()
    for link in links:
        if link.device_id in seen:
            continue
        seen.add(link.device_id)       # only the first link on each device moves
        if link.effective_from > start:
            move(link, f"{link.device.name} link")
    if employee.joining_date is None or employee.joining_date > start_day:
        before = employee.joining_date
        employee.joining_date = start_day
        employee.save(update_fields=["joining_date", "updated_at"])
        moved.append(f"Joining date {start_day:%d %b}" + (f" (was {before:%d %b})" if before else ""))
    if moved:
        record_company_event(actor=actor, membership=panel_access.managing_membership(actor, company.pk),
                             company=company, action="employee.start_moved_back", obj=employee,
                             after={"from": start_day.isoformat(), "moved": moved, "refused": refused})
    return moved, refused


def _missing_days_step(actor, job):
    """The first step moves every chosen person's start back and judges the
    refused scans again; then their days are rebuilt ``STEP`` people at a time
    and the days still absent are counted. Returns ``(done, rest, note)``."""
    from attendance.models import AttendanceRecord
    from attendance.services import recalculate
    from django.db.models import Count
    from employees.models import Employee

    with use_company(job.company_id):
        if not job.results:
            rows = []
            for employee in (Employee.objects.filter(pk__in=job.remaining)
                             .order_by("first_name", "last_name")):
                moved, refused = move_start_back(actor=actor, company=job.company,
                                                 employee=employee, start_day=job.start)
                rows.append({"id": employee.pk, "name": employee.full_name,
                             "moved": moved or ["Already from this date"], "refused": refused,
                             "absent": None})
            job.results = rows
            _, note = _recheck(actor, job)
            return 0, job.remaining, note
        batch, rest = job.remaining[:STEP], job.remaining[STEP:]
        recalculate(job.company_id, employee_ids=batch, start=job.start, end=today(job.company))
        absent = dict(AttendanceRecord.objects.filter(
            employee_id__in=batch, work_date__gte=job.start,
            attendance_status=AttendanceRecord.AttendanceStatus.ABSENT)
            .values("employee_id").annotate(n=Count("pk")).values_list("employee_id", "n"))
        written = set(AttendanceRecord.objects.filter(employee_id__in=batch, work_date__gte=job.start)
                      .values_list("employee_id", flat=True).distinct())
        rows = []
        for row in job.results:
            if row["id"] in batch:
                row = {**row, "absent": absent.get(row["id"], 0)}
                if row["id"] not in written:
                    # No day at all: attendance has nothing to measure them by.
                    row["refused"] = row["refused"] + [
                        "No attendance days at all: their department (or they) need a shift first"]
            rows.append(row)
        job.results = rows
        return len(batch), rest, ""


# --- the background jobs -----------------------------------------------------


def running(company):
    return (SetupFixJob.all_objects.filter(company=company, status=SetupFixJob.Status.RUNNING)
            .order_by("-pk").first())


def start(*, actor, company, kind, employee_ids=(), start_day=None):
    """Start a job; return ``(job, started)``. Only one runs at a time per
    company: while one runs, that one is returned (and woken if it stopped)."""
    existing = running(company)
    if existing is not None:
        resume_if_stalled(company)
        return existing, False
    ids = list(dict.fromkeys(employee_ids))
    job = SetupFixJob(kind=kind, requested_by=actor, start=start_day,
                      total=1 if kind == SetupFixJob.Kind.RECHECK else len(ids),
                      remaining=["all"] if kind == SetupFixJob.Kind.RECHECK else ids,
                      heartbeat_at=timezone.now())
    job.company_id = company.pk
    if not job.remaining:
        job.status, job.finished_at, job.message = SetupFixJob.Status.DONE, timezone.now(), "Nobody to do."
    job.save()
    if job.remaining:
        _launch(job.pk)
        job.refresh_from_db()   # inline (tests) it has finished by now
    return job, True


def _launch(job_id):
    if not _in_background():
        run_job(job_id)
        return

    def run():
        try:
            run_job(job_id)
        except Exception:  # noqa: BLE001 - the job is marked failed
            logger.exception("Setup fix job %s failed", job_id)
        finally:
            connection.close()

    transaction.on_commit(
        lambda: threading.Thread(target=run, name=f"setup-fix-{job_id}", daemon=True).start())


def run_job(job_id):
    try:
        with mapping._roster_memo():
            while _step(job_id):
                pass
    except Exception as exc:
        SetupFixJob.all_objects.filter(pk=job_id, status=SetupFixJob.Status.RUNNING).update(
            status=SetupFixJob.Status.FAILED, message=f"Stopped by an error: {str(exc)[:400]}",
            finished_at=timezone.now())
        raise


def _step(job_id):
    """Do the next ``STEP`` people. False when nothing more is to be done."""
    from employees.models import Employee

    with transaction.atomic():
        job = (SetupFixJob.all_objects.select_for_update(skip_locked=True, of=("self",))
               .select_related("company", "requested_by")
               .filter(pk=job_id, status=SetupFixJob.Status.RUNNING).first())
        if job is None:
            return False
        batch, rest = job.remaining[:STEP], job.remaining[STEP:]
        done, failures, note = 0, [], ""
        actor = job.requested_by
        if actor is None or not actor.is_active:
            failures, rest = [["Everyone left", "the person who started this can no longer sign in"]], []
        elif job.kind == SetupFixJob.Kind.RECHECK:
            # A thread has no request, so no company: every step sets its own.
            with use_company(job.company_id):
                done, note = _recheck(actor, job)
        elif job.kind == SetupFixJob.Kind.MISSING_DAYS:
            done, rest, note = _missing_days_step(actor, job)
        else:
            with use_company(job.company_id):
                found = Employee.all_objects.in_bulk(batch)
            people = [found[pk] for pk in batch if pk in found]
            failures += [[f"Employee #{pk}", "no longer exists"] for pk in batch if pk not in found]
            if job.kind == SetupFixJob.Kind.SEND:
                with use_company(job.company_id):
                    sent, failed = _send(actor, people)
                done += sent
                failures += failed
            else:
                with use_company(job.company_id):
                    from attendance.services import recalculate

                    recalculate(job.company_id, employee_ids=[e.pk for e in people], start=job.start,
                                end=today(job.company))
                done += len(people)
        job.remaining = rest
        job.done_count += done
        job.failed_count += len(failures)
        job.failures = (job.failures + failures)[:FAILURES_KEPT]
        job.heartbeat_at = timezone.now()
        if note:
            job.message = note
        if not rest:
            job.status, job.finished_at = SetupFixJob.Status.DONE, job.heartbeat_at
            if job.kind == SetupFixJob.Kind.MISSING_DAYS:
                job.message = f"{_finished(job)} {job.message}".strip()
            job.message = job.message or _finished(job)
        job.save()
        return bool(rest)


def _send(actor, people):
    """``send_employees`` for one step: ``(people sent, [[name, reason]])``."""
    try:
        with transaction.atomic():
            results = [mapping.send_employees(actor=actor, employees=people)]
    except Exception:  # noqa: BLE001 - retried one by one below
        logger.exception("Setup fix: a send step failed; retrying one by one")
        results = []
        for person in people:
            try:
                with transaction.atomic():
                    results.append(mapping.send_employees(actor=actor, employees=[person]))
            except Exception as exc:  # noqa: BLE001
                logger.exception("Setup fix: employee %s failed", person.pk)
                results.append(mapping.SendResult(failed=[(person, None, f"{type(exc).__name__}: {exc}"[:200])]))
    sent = {e.pk for result in results for e, _ in result.sent}
    failed = [[e.full_name, (f"{device.name}: " if device else "") + reason]
              for result in results for e, device, reason in result.failed]
    return len(sent), failed


def _recheck(actor, job):
    from devices.services.attendance_rules import recheck_punches

    result = recheck_punches(actor=actor, company_id=job.company_id, start=job.start,
                             end=today(job.company))
    still = sum(result.still_excluded.values())
    if not result.checked:
        return 1, f"No refused scans from {job.start:%d %b %Y}: nothing to check again."
    return 1, (f"{result.checked} refused scans checked again: {result.now_count} now count"
               + (f", {still} still do not (see Devices → Excluded punches)" if still else "")
               + (f", {result.skipped_locked} were in a posted salary month" if result.skipped_locked else "")
               + ". Their days were recalculated.")


def _finished(job):
    if job.kind == SetupFixJob.Kind.MISSING_DAYS:
        return f"Attendance of {job.done_count} people rebuilt from {job.start:%d %b %Y}."
    # A failure is one person on one device: count the people.
    people = len({name for name, _ in job.failures}) if job.failures else job.failed_count
    if job.kind == SetupFixJob.Kind.SEND:
        return (f"{job.done_count} people sent to their branch's devices"
                + (f"; {people} could not be (below)." if job.failed_count else ".")
                + " Each device collects them when it next checks in.")
    return (f"Attendance of {job.done_count} people recalculated from {job.start:%d %b %Y}"
            + (f"; {people} could not be (below)." if job.failed_count else "."))


def resume_if_stalled(company):
    job = running(company)
    if job is None:
        return False
    now = timezone.now()
    if job.heartbeat_at and now - job.heartbeat_at < STALLED_AFTER:
        return False
    claimed = SetupFixJob.all_objects.filter(
        pk=job.pk, status=SetupFixJob.Status.RUNNING, heartbeat_at=job.heartbeat_at,
    ).update(heartbeat_at=now)
    if claimed:
        logger.info("Setup fix job %s had stopped; resuming", job.pk)
        _launch(job.pk)
    return bool(claimed)


def latest(company, now=None):
    """The running job, or the last one if it finished recently; else None."""
    now = now or timezone.now()
    job = SetupFixJob.all_objects.filter(company=company).order_by("-pk").first()
    if job is None:
        return None
    if job.status != SetupFixJob.Status.RUNNING and (
            job.finished_at is None or now - job.finished_at > SHOWN_FOR):
        return None
    return job
