"""Advancing a stored punch from raw evidence to an evaluated decision.

Only resolution/processing fields move here. ``punched_at_device_raw`` and
``raw_record`` are never rewritten, and no row is ever deleted: an unauthorized
or unresolved punch stays queryable as source evidence
(DEVICE_INTEGRATION_HANDOFF.md rules 1 and 3).
"""

import logging

from django.utils import timezone

from devices.models import PunchEvent
from devices.services import authorization, resolution

logger = logging.getLogger(__name__)

# Statuses that mean "this punch does not feed attendance". They are excluded
# from allocation but remain in the table, marked with why.
EXCLUDING_STATUSES = {
    PunchEvent.AuthorizationStatus.UNAUTHORIZED_DEVICE,
    PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
    PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
    PunchEvent.AuthorizationStatus.DEPARTMENT_MISMATCH,
    PunchEvent.AuthorizationStatus.BRANCH_MISMATCH,
    PunchEvent.AuthorizationStatus.ENROLLMENT_DISABLED,
}


def _processing_status(*, authorization_status, dedupe_status):
    """Combine the dedupe verdict and the authorization verdict.

    A confirmed retransmission is excluded whatever its authorization says —
    that is what stops a resend from multiplying attendance effects. An
    ambiguous repeat is only ever sent to review, never excluded outright.
    """
    if dedupe_status == PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE:
        return PunchEvent.ProcessingStatus.EXCLUDED
    if authorization_status == PunchEvent.AuthorizationStatus.POLICY_UNRESOLVED:
        return PunchEvent.ProcessingStatus.NEEDS_REVIEW
    if authorization_status in EXCLUDING_STATUSES:
        return PunchEvent.ProcessingStatus.EXCLUDED
    if dedupe_status == PunchEvent.DedupeStatus.PROBABLE_DUPLICATE:
        return PunchEvent.ProcessingStatus.NEEDS_REVIEW
    # Authorized and unambiguous: ready for the attendance engine to allocate.
    return PunchEvent.ProcessingStatus.PENDING


def resolve_and_authorize(punch, *, policy_at=None, note=None):
    """Resolve the employee and evaluate the policy for one stored punch.

    Identity is always resolved at the punch time — device user numbers are
    reused. ``policy_at`` and ``note`` are for a re-check: the moment whose
    settings to judge by, and what to record about who asked for it.
    """
    outcome = resolution.resolve_enrollment(
        device=punch.device,
        device_user_id=punch.device_user_id,
        at=punch.punched_at_utc,
    )

    if outcome.enrollment is None:
        punch.device_enrollment = None
        punch.employee = None
        punch.authorization_status = outcome.status
        punch.authorization_snapshot = {
            "evaluated_at": timezone.now().isoformat(),
            "event_time_utc": punch.punched_at_utc.isoformat(),
            "device_id": punch.device_id,
            "decision_reason": outcome.reason,
        }
    else:
        decision = authorization.evaluate(
            punch=punch, enrollment=outcome.enrollment, policy_at=policy_at
        )
        punch.device_enrollment = outcome.enrollment
        punch.employee_id = outcome.enrollment.employee_id
        punch.authorization_status = decision.status
        punch.authorization_snapshot = decision.snapshot

    if note:
        punch.authorization_snapshot["recheck"] = note

    punch.processing_status = _processing_status(
        authorization_status=punch.authorization_status,
        dedupe_status=punch.dedupe_status,
    )
    punch.resolved_at = timezone.now()
    punch.save(update_fields=[
        "device_enrollment",
        "employee",
        "authorization_status",
        "authorization_snapshot",
        "processing_status",
        "resolved_at",
    ])
    return punch


def resolve_and_authorize_safely(punch):
    """Evaluate a punch without ever losing it if evaluation fails.

    The row is already durable, so a failure here is recorded on the punch and
    left for reprocessing rather than propagated into the device response.
    """
    try:
        return resolve_and_authorize(punch)
    except Exception as exc:  # noqa: BLE001 - the evidence must survive
        logger.exception("Resolution failed for punch %s", punch.pk)
        PunchEvent.all_objects.filter(pk=punch.pk).update(
            processing_status=PunchEvent.ProcessingStatus.FAILED,
            processing_error=f"{type(exc).__name__}: {exc}",
        )
        return punch
