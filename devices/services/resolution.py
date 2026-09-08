"""Resolving a device-reported user id to a permanent Employee.

Recognition only: this answers "who is this device user number, at the moment
the punch happened". Whether that punch *counts* is a separate decision made in
devices/services/authorization.py.

The mapping is resolved at punch time, not now, because device user numbers are
reused: number 7 may be Alice until March and Bob afterwards. An enrollment on
another device is never a substitute (DEVICE_ATTENDANCE_POLICY.md step 3).
"""

from dataclasses import dataclass

from django.db.models import BooleanField, ExpressionWrapper, Q

from devices.models import DeviceEnrollment, PunchEvent


@dataclass
class ResolutionOutcome:
    enrollment: DeviceEnrollment | None
    status: str
    reason: str


def resolve_enrollment(*, device, device_user_id, at):
    """Find the enrollment that identified ``device_user_id`` on ``device`` at ``at``.

    Distinguishes three cases the administrator needs to tell apart:
    - a number this device has never had an enrollment for -> unknown_employee;
    - a number whose enrollment interval had already ended -> expired_enrollment;
    - a live mapping -> resolved.
    """
    covering = (
        DeviceEnrollment.all_objects.select_related("employee")
        .filter(
            company_id=device.company_id,
            device=device,
            device_user_id=device_user_id,
            effective_from__lte=at,
        )
        .filter(Q(effective_to__isnull=True) | Q(effective_to__gt=at))
        # A removed mapping that still covers the instant is weaker evidence
        # than a live one, so prefer live rows deterministically.
        .annotate(
            is_removed=ExpressionWrapper(
                Q(enrollment_status=DeviceEnrollment.EnrollmentStatus.REMOVED),
                output_field=BooleanField(),
            )
        )
        .order_by("is_removed", "-effective_from", "pk")
    )
    enrollment = covering.first()
    if enrollment is not None:
        return ResolutionOutcome(
            enrollment=enrollment,
            status="resolved",
            reason=(
                f"device user id {device_user_id!r} mapped to employee "
                f"{enrollment.employee_id} by enrollment {enrollment.pk}"
            ),
        )

    ever_enrolled = DeviceEnrollment.all_objects.filter(
        company_id=device.company_id, device=device, device_user_id=device_user_id
    ).exists()

    if ever_enrolled:
        return ResolutionOutcome(
            enrollment=None,
            status=PunchEvent.AuthorizationStatus.EXPIRED_ENROLLMENT,
            reason=(
                f"device user id {device_user_id!r} has enrollments on this "
                "device, but none covering the punch instant"
            ),
        )

    return ResolutionOutcome(
        enrollment=None,
        status=PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
        reason=(
            f"device user id {device_user_id!r} has never been enrolled on this device"
        ),
    )
