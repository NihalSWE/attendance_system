"""Load employees onto a device, in the background (Nihal, 2026-10-01).

"Load employees onto this device" used to do everything inside the click: for
250+ employees the request ran past the live server's time limit and the page
answered 500. Now the click only writes a ``DeviceLoadJob`` with the list of
employees and returns at once; a background thread works through the list 25
at a time (``send_employees``, exactly as before), putting each person in the
device's outbox, and the device collects the outbox on its check-ins as it
always did. The progress card shows both stages.

Why not Celery: it needs Redis (or another broker) and a worker service kept
running beside the website. The job lives in the database instead, so it
survives a restart — a run whose thread stopped (a deploy, a worker recycled)
is noticed by its silent heartbeat and picked up from where it was, the next
time the device checks in or someone looks at the progress. If more work moves
to the background later, ``run_job`` is the task a Celery worker would call.

Two threads never prepare the same people: each step locks the job row
(``skip_locked``), and the list of who is left is saved with that step.
"""

import datetime
import logging
import threading

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connection, transaction
from django.utils import timezone

from common.tenant import use_company
from devices.models import DeviceLoadJob
from devices.services import mapping

logger = logging.getLogger(__name__)

#: People prepared per step: one short transaction each, so the device starts
#: collecting the first ones while the rest are still being prepared.
STEP = 25
#: A running job whose heartbeat is older than this lost its thread.
STALLED_AFTER = datetime.timedelta(minutes=2)
#: A finished job stays on the card this long.
SHOWN_FOR = datetime.timedelta(hours=1)
#: Failures kept on the job for the card (the count is always exact).
FAILURES_KEPT = 50
UNEXPECTED = "an unexpected error stopped this one; the server log has the details"


def _in_background():
    return getattr(settings, "DEVICE_LOAD_IN_BACKGROUND", True)


def running_job(device):
    return (DeviceLoadJob.all_objects.filter(device=device, status=DeviceLoadJob.Status.RUNNING)
            .order_by("-pk").first())


def start_load(*, actor, device):
    """Start loading the device's branch employees; return ``(job, started)``.

    ``started`` is False when a load for this device is already under way —
    that one is returned (and woken up if its thread had stopped), so a second
    click never sends everybody twice.
    """
    if not mapping.may_map(actor, device.company_id, device.branch_id):
        raise PermissionDenied("You may not manage that branch.")
    if not mapping.upload_supported(device):
        raise mapping.MappingError(
            f"Writing users to {device.name} is not measured on its protocol yet.")
    existing = running_job(device)
    if existing is not None:
        resume_if_stalled(device)
        return existing, False
    ids = [employee.pk for employee in mapping.branch_employees(device)]
    job = DeviceLoadJob(device=device, requested_by=actor, total=len(ids), remaining=ids,
                        heartbeat_at=timezone.now())
    job.company_id = device.company_id
    if not ids:
        job.status, job.finished_at = DeviceLoadJob.Status.DONE, timezone.now()
    job.save()
    if ids:
        mapping._audit(actor, device, "device.load_started", device, {"employees": len(ids)})
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
        except Exception:  # noqa: BLE001 - the job is marked; the heartbeat lets it resume
            logger.exception("Device load job %s failed", job_id)
        finally:
            connection.close()

    transaction.on_commit(
        lambda: threading.Thread(target=run, name=f"device-load-{job_id}", daemon=True).start())


def run_job(job_id):
    """Work through the job, a step at a time, until nobody is left."""
    try:
        with mapping._roster_memo():
            while _step(job_id):
                pass
    except Exception as exc:
        DeviceLoadJob.all_objects.filter(pk=job_id, status=DeviceLoadJob.Status.RUNNING).update(
            status=DeviceLoadJob.Status.FAILED, error=str(exc)[:500], finished_at=timezone.now())
        raise


def _step(job_id):
    """Prepare the next ``STEP`` people. False when there is nothing more to do
    (finished, or another thread holds the job right now)."""
    from employees.models import Employee

    with transaction.atomic():
        job = (DeviceLoadJob.all_objects.select_for_update(skip_locked=True, of=("self",))
               .select_related("device", "device__branch", "device__company", "requested_by")
               .filter(pk=job_id, status=DeviceLoadJob.Status.RUNNING).first())
        if job is None:
            return False
        batch, rest = job.remaining[:STEP], job.remaining[STEP:]
        done, failures = 0, []
        if batch:
            with use_company(job.company_id):
                found = Employee.all_objects.in_bulk(batch)
                people = [found[pk] for pk in batch if pk in found]
                failures += [["Employee #%s" % pk, "no longer exists"] for pk in batch if pk not in found]
                if job.requested_by is None or not job.requested_by.is_active:
                    failures += [[e.full_name, "the person who started this can no longer sign in"]
                                 for e in people]
                else:
                    done, failed = _send(job.requested_by, job.device, people)
                    failures += failed
        job.remaining = rest
        job.done_count += done
        job.failed_count += len(failures)
        job.failures = (job.failures + failures)[:FAILURES_KEPT]
        job.heartbeat_at = timezone.now()
        if not rest:
            job.status, job.finished_at = DeviceLoadJob.Status.DONE, job.heartbeat_at
        job.save(update_fields=["remaining", "done_count", "failed_count", "failures",
                                "heartbeat_at", "status", "finished_at", "updated_at"])
        return bool(rest)


def _send(actor, device, people):
    """``send_employees`` for one step: ``(prepared count, [[name, reason]])``.

    Someone the step cannot handle for an unexpected reason does not stop the
    others: the step is retried one person at a time, and only that person is
    marked.
    """
    try:
        with transaction.atomic():
            results = [mapping.send_employees(actor=actor, employees=people, only_device=device)]
    except Exception:  # noqa: BLE001 - retried one by one below
        logger.exception("Device load: a step for %s failed; retrying one by one", device.pk)
        results = []
        for person in people:
            try:
                with transaction.atomic():
                    results.append(mapping.send_employees(actor=actor, employees=[person],
                                                          only_device=device))
            except Exception:  # noqa: BLE001
                logger.exception("Device load: employee %s failed", person.pk)
                results.append(mapping.SendResult(failed=[(person, device, UNEXPECTED)]))
    sent = {e.pk for result in results for e, _ in result.sent}
    failed = [[e.full_name, reason] for result in results for e, _, reason in result.failed
              if e.pk not in sent]
    return len(sent), failed


def resume_if_stalled(device):
    """Wake a running job whose thread stopped (a restart). True if woken."""
    job = running_job(device)
    if job is None:
        return False
    now = timezone.now()
    if job.heartbeat_at and now - job.heartbeat_at < STALLED_AFTER:
        return False
    # Only one caller wins the job: the heartbeat it read must still be there.
    claimed = DeviceLoadJob.all_objects.filter(
        pk=job.pk, status=DeviceLoadJob.Status.RUNNING, heartbeat_at=job.heartbeat_at,
    ).update(heartbeat_at=now)
    if not claimed:
        return False
    logger.info("Device load job %s had stopped; resuming with %s left", job.pk, len(job.remaining))
    _launch(job.pk)
    return True


def on_device_poll(device):
    """A device checked in: at most once a minute, wake its stalled load."""
    if not _in_background() or not cache.add(f"device-load-poll:{device.pk}", True, 60):
        return
    try:
        resume_if_stalled(device)
    except Exception:  # noqa: BLE001 - never stands between a device and its commands
        logger.exception("Device load: resume check for %s failed", device.pk)


def progress(device, now=None):
    """The load's part of the progress card: the running job, or the last one
    if it finished within the hour; None otherwise."""
    now = now or timezone.now()
    job = DeviceLoadJob.all_objects.filter(device=device).order_by("-pk").first()
    if job is None:
        return None
    running = job.status == DeviceLoadJob.Status.RUNNING
    if not running and (job.finished_at is None or now - job.finished_at > SHOWN_FOR):
        return None
    handled = job.done_count + job.failed_count
    return {
        "running": running,
        "stopped": job.status == DeviceLoadJob.Status.FAILED,
        "total": job.total,
        "prepared": job.done_count,
        "failed": job.failed_count,
        "handled": handled,
        "percent": int(round(100 * handled / job.total)) if job.total else 100,
        "failures": job.failures[:10],
        "more_failures": max(0, job.failed_count - 10),
    }
