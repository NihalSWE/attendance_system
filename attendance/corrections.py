"""Corrections a person made to a day, as the calculation reads them (N5).

Only the reading side lives here, so ``attendance.services`` can import it
without a cycle. Making, withdrawing and listing corrections is in
``attendance.correction_services``.
"""

import datetime
from collections import defaultdict
from dataclasses import dataclass

from attendance.models import AttendanceCorrection

Type = AttendanceCorrection.CorrectionType


@dataclass(frozen=True)
class CorrectionRef:
    """Where a manually added scan came from.

    Pairing carries each scan's source id without looking at it. Device scans
    carry a PunchEvent id; a scan added by hand carries one of these, which is
    how the allocation written for it points at the correction instead.
    """

    pk: int


def source_ids(source):
    """``(punch_event_id, attendance_correction_id)`` for a scan's source."""
    if isinstance(source, CorrectionRef):
        return None, source.pk
    return source, None


def stream_order(moment):
    """Sort key for a day's scans: by time, device scans before manual ones.

    Plain tuple sorting would compare an int with a CorrectionRef on a tie.
    """
    at, source = moment
    if isinstance(source, CorrectionRef):
        return (at, 1, source.pk)
    return (at, 0, source or 0)


@dataclass
class InForce:
    """The corrections that shape a range of days, grouped for the calculation."""

    scans: dict          # employee_id -> [(at, CorrectionRef)]
    statuses: dict       # (employee_id, date) -> AttendanceCorrection
    accepted: dict       # (employee_id, date) -> accepted review reason


def in_force(employee_ids, *, since, until, start, end):
    """Everything applied for these employees.

    Scans are selected by *time* (``since``..``until``), not by the date they
    were entered against, because a day's window decides where a scan belongs.
    Status changes and acceptances belong to their date.
    """
    scans = defaultdict(list)
    statuses, accepted = {}, {}
    rows = AttendanceCorrection.objects.filter(
        employee_id__in=list(employee_ids),
        status=AttendanceCorrection.Status.APPLIED,
    )
    for correction in rows.filter(
        correction_type=Type.ADD_SCAN,
        proposed_event_at__gte=since, proposed_event_at__lt=until,
    ):
        scans[correction.employee_id].append(
            (correction.proposed_event_at, CorrectionRef(correction.pk))
        )
    for correction in rows.filter(
        correction_type__in=(Type.CHANGE_STATUS, Type.ACCEPT_REVIEW),
        work_date__gte=start, work_date__lte=end,
    ):
        key = (correction.employee_id, correction.work_date)
        if correction.correction_type == Type.CHANGE_STATUS:
            statuses[key] = correction
        else:
            accepted[key] = correction.accepted_review_reason
    return InForce(scans=dict(scans), statuses=statuses, accepted=accepted)


def manual_scans(employee_ids, *, since, until):
    """``{employee_id: [(at, CorrectionRef)]}`` — for readers such as the Now badge."""
    return in_force(
        employee_ids, since=since, until=until,
        start=datetime.date.min, end=datetime.date.min,
    ).scans
