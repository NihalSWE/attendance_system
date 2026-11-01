"""Generate salary in the background, with its progress on the Salary page
(2026-11-01).

A month for 2,000 people takes minutes: inside the click it ran past the
server's time limit, with nothing on screen meanwhile. Now the click only
writes a ``GenerateJob`` and returns; a thread runs ``generate_payroll`` and
writes on the job how far it is - attendance person by person, then the
payslips - and the Salary page asks every second and shows it.

Like the device load (devices/services/load_jobs.py): no Celery, the job lives
in the database. A job whose thread stopped (a deploy, a worker recycled) has
a silent heartbeat; the next time someone looks at the Salary page it is
started again from the beginning, which is safe because generating replaces
the draft.
"""

import datetime
import logging
import threading
import time

from django.conf import settings
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection, transaction
from django.utils import timezone

from common.tenant import use_company
from payroll.models import GenerateJob

logger = logging.getLogger(__name__)

#: A running job whose heartbeat is older than this lost its thread.
STALLED_AFTER = datetime.timedelta(minutes=3)
#: Progress is written at most this often (seconds).
WRITE_EVERY = 0.7
#: Attendance is the larger part of the work: the bar gives it this share.
ATTENDANCE_SHARE = 60
UNEXPECTED = "An unexpected error stopped it; the server log has the details. Press Generate again."


def _in_background():
    return getattr(settings, "PAYROLL_GENERATE_IN_BACKGROUND", True)


def _same_run(company_id, year, month, pay_group, week):
    jobs = GenerateJob.all_objects.filter(company_id=company_id, pay_group=pay_group)
    if pay_group is None:
        return jobs.filter(year=year, month=month, week__isnull=True)
    return jobs.filter(week=week)


def latest(company_id, year, month, pay_group=None, week=None):
    """The last job for this month (or week), or None."""
    return _same_run(company_id, year, month, pay_group, week).order_by("-pk").first()


def generating(company_id, year, month, pay_group=None, week=None):
    """True while this month (or week) is being generated."""
    return _same_run(company_id, year, month, pay_group, week).filter(
        status=GenerateJob.Status.RUNNING).exists()


def start(*, actor, company_id, year, month, branch_ids=None, pay_group=None, week=None):
    """Start generating; return ``(job, started)``. ``started`` is False when
    this month (or week) is already being generated: that job is returned (and
    started again if its thread had stopped), so a second click never runs it
    twice."""
    existing = _same_run(company_id, year, month, pay_group, week).filter(
        status=GenerateJob.Status.RUNNING).order_by("-pk").first()
    if existing is not None:
        resume_if_stalled(existing)
        return existing, False
    job = GenerateJob(requested_by=actor, year=year, month=month, pay_group=pay_group, week=week,
                      branch_ids=sorted(branch_ids) if branch_ids is not None else None,
                      heartbeat_at=timezone.now())
    job.company_id = company_id
    job.save()
    _launch(job.pk)
    job.refresh_from_db()   # inline (tests) it has finished by now
    return job, True


def _launch(job_id):
    """Run the job after this request's transaction commits (so the thread
    sees the row), in a thread; in tests, right here."""
    if not _in_background():
        run_job(job_id)
        return

    def run():
        try:
            run_job(job_id)
        finally:
            connection.close()

    transaction.on_commit(
        lambda: threading.Thread(target=run, name=f"payroll-generate-{job_id}", daemon=True).start())


def run_job(job_id):
    """Generate the job's month (or week), writing its progress as it goes."""
    from payroll.services import generate_payroll

    job = GenerateJob.all_objects.select_related("requested_by", "pay_group").get(pk=job_id)
    last = {"at": 0.0, "stage": None}

    def progress(stage, done, total):
        now = time.monotonic()
        # Each stage's start and end are always written; in between, at most
        # every WRITE_EVERY seconds.
        if stage == last["stage"] and done not in (0, total) and now - last["at"] < WRITE_EVERY:
            return
        last.update(at=now, stage=stage)
        GenerateJob.all_objects.filter(pk=job_id, status=GenerateJob.Status.RUNNING).update(
            stage=stage, done_count=done, total=total, heartbeat_at=timezone.now())

    try:
        actor = job.requested_by
        if actor is None or not actor.is_active:
            raise PermissionDenied("The person who started it can no longer sign in.")
        run = generate_payroll(
            actor=actor, company_id=job.company_id, year=job.year, month=job.month,
            branch_ids=job.branch_ids, pay_group=job.pay_group, week=job.week, progress=progress,
        )
    except (ValidationError, PermissionDenied) as exc:
        text = " ".join(exc.messages) if isinstance(exc, ValidationError) else str(exc)
        _finish(job_id, GenerateJob.Status.FAILED, text)
    except Exception:
        logger.exception("Salary generation job %s failed", job_id)
        _finish(job_id, GenerateJob.Status.FAILED, UNEXPECTED)
        if not _in_background():
            raise
    else:
        _finish(job_id, GenerateJob.Status.DONE, done_message(job, run))


def _finish(job_id, status, message):
    GenerateJob.all_objects.filter(pk=job_id, status=GenerateJob.Status.RUNNING).update(
        status=status, message=message, finished_at=timezone.now(), heartbeat_at=timezone.now())


def done_message(job, run):
    """What the finished job did, in words (as the Generate button said it)."""
    from organization.models import Branch
    from payroll import paygroups

    skipped = run.skipped_now
    if job.pay_group is not None:
        first, last = paygroups.week_of(job.pay_group, job.week)
        message = (
            f"{job.pay_group.name}: {paygroups.week_name(first, last)} generated for "
            f"{run.totals_snapshot['employees']} employee(s). It is a draft and can be "
            "regenerated at any time."
        )
        monthly = run.totals_snapshot.get("on_monthly_salary")
        if monthly:
            message += (f" Left out, their salary is monthly (a weekly group pays a daily "
                        f"or hourly rate): {', '.join(monthly)}.")
    elif job.branch_ids is None:
        message = (
            f"Salary generated for {run.totals_snapshot['employees']} employee(s). "
            "It is a draft and can be regenerated at any time."
        )
    else:
        # A branch's own people; other branches' payslips were not touched.
        with use_company(job.company_id):
            mine = run.records.filter(
                employee_assignment_at_period_end__branch_id__in=job.branch_ids).count()
            names = sorted(Branch.objects.filter(pk__in=job.branch_ids).values_list("name", flat=True))
        message = (
            f"Salary generated for {mine} employee(s) in {', '.join(names)}. "
            "It is a draft; the owner or company administrator finalises the month."
        )
    if skipped:
        message += f" Skipped, no salary set: {', '.join(skipped)}."
    return message


def resume_if_stalled(job):
    """Start again a running job whose thread stopped (a restart). True if
    started again."""
    if job.status != GenerateJob.Status.RUNNING or not _in_background():
        return False
    now = timezone.now()
    if job.heartbeat_at and now - job.heartbeat_at < STALLED_AFTER:
        return False
    # Only one caller wins the job: the heartbeat it read must still be there.
    claimed = GenerateJob.all_objects.filter(
        pk=job.pk, status=GenerateJob.Status.RUNNING, heartbeat_at=job.heartbeat_at,
    ).update(heartbeat_at=now, stage=GenerateJob.Stage.ATTENDANCE, done_count=0, total=0)
    if not claimed:
        return False
    logger.info("Salary generation job %s had stopped; starting it again", job.pk)
    _launch(job.pk)
    return True


def percent(job):
    """How far the whole job is, 0-100: attendance first, then the payslips."""
    if job.status != GenerateJob.Status.RUNNING:
        return 100
    part = job.done_count / job.total if job.total else 0
    if job.stage == GenerateJob.Stage.ATTENDANCE:
        return int(part * ATTENDANCE_SHARE)
    return ATTENDANCE_SHARE + int(part * (100 - ATTENDANCE_SHARE))


def describe(job):
    """The job for the Salary page's progress card (and its JSON)."""
    return {
        "id": job.pk,
        "status": job.status,
        "running": job.status == GenerateJob.Status.RUNNING,
        "stage": job.stage,
        "done": job.done_count,
        "total": job.total,
        "percent": percent(job),
        "message": job.message,
        "started": job.created_at,
        "elapsed": int((timezone.now() - job.created_at).total_seconds()) if job.created_at else 0,
    }
