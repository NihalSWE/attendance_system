"""Daily attendance, calculated from device punches, leave and the calendar.

Thin slice of the 2026-09-12 salary fast-track: one ``AttendanceRecord`` per
employee per day, built from the first IN and last OUT authorized punch.
Field names follow MODEL_FIELD_DICTIONARY.md §34; sessions, breaks,
corrections, overtime approval and review are listed in docs/PHASE_STATUS.md
as not built yet.
"""

from decimal import Decimal

from django.db import models

from common.models import TenantOwned


class PunchAllocation(TenantOwned):
    """One scan, as the calculation read it.

    A PunchEvent is evidence and is never rewritten. This is the derived layer:
    the same instant, plus what the pairing decided it *meant* and whether it
    counted. Keeping them apart is what lets a day be recalculated without
    touching what the device actually reported.

    Labels come from the software, never from the device. A terminal's own
    IN/OUT or break keys are not used even when it has them
    (DEVICE_ATTENDANCE_POLICY.md step 7); ``reported_direction`` on the punch
    stays informational.
    """

    class Direction(models.TextChoices):
        IN = "in", "In"
        OUT = "out", "Out"
        IGNORED = "ignored", "Ignored"
        UNRESOLVED = "unresolved", "Unresolved"

    class Label(models.TextChoices):
        CHECK_IN = "check_in", "Check-in"
        BREAK_OUT = "break_out", "Break-out"
        BREAK_IN = "break_in", "Break-in"
        CHECK_OUT = "check_out", "Check-out"
        IGNORED = "ignored", "Ignored"

    class ExclusionReason(models.TextChoices):
        DUPLICATE = "duplicate", "Repeat scan"
        UNAUTHORIZED_DEVICE = "unauthorized_device", "Device not counted"
        OUTSIDE_WINDOW = "outside_window", "Outside the attendance window"
        SUPERSEDED = "superseded", "Superseded"
        MANUAL_EXCLUSION = "manual_exclusion", "Excluded by hand"
        OTHER = "other", "Other"

    attendance_record = models.ForeignKey(
        "attendance.AttendanceRecord", on_delete=models.CASCADE,
        related_name="allocations",
    )
    punch_event = models.ForeignKey(
        "devices.PunchEvent", null=True, blank=True, on_delete=models.PROTECT,
        related_name="allocations",
    )
    sequence_number = models.PositiveIntegerField()
    event_at = models.DateTimeField()
    interpreted_direction = models.CharField(
        max_length=16, choices=Direction.choices, default=Direction.IN
    )
    # The word the screens show. Derived from the direction and its position in
    # the day, so it is computed once here rather than in every template.
    label = models.CharField(max_length=16, choices=Label.choices, default=Label.CHECK_IN)
    is_included = models.BooleanField(default=True)
    exclusion_reason = models.CharField(
        max_length=32, choices=ExclusionReason.choices, blank=True
    )
    interpretation_note = models.CharField(max_length=255, blank=True)
    calculation_version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payroll_punch_allocation"
        ordering = ("attendance_record", "sequence_number")
        constraints = [
            models.UniqueConstraint(
                fields=["attendance_record", "calculation_version", "sequence_number"],
                name="uniq_punch_allocation_sequence",
            ),
        ]
        indexes = [models.Index(fields=["company", "event_at"])]

    def __str__(self):
        return f"{self.event_at:%Y-%m-%d %H:%M} {self.label}"


class AttendanceSession(TenantOwned):
    """One in-office stretch: an IN and the OUT that closed it.

    A day is a list of these. The gaps *between* them are the breaks, so
    in-office and out-of-office both fall out of the same pairing rather than
    being tracked separately and drifting apart.

    Both ends may come from different devices — somebody may check in at the
    front door and out at the back — so the two allocations are never required
    to share a device.
    """

    class Status(models.TextChoices):
        COMPLETE = "complete", "Complete"
        MISSING_IN = "missing_in", "Missing in"
        MISSING_OUT = "missing_out", "Missing out"
        INVALID = "invalid", "Invalid"

    attendance_record = models.ForeignKey(
        "attendance.AttendanceRecord", on_delete=models.CASCADE,
        related_name="sessions",
    )
    sequence_number = models.PositiveIntegerField()
    in_allocation = models.ForeignKey(
        PunchAllocation, null=True, blank=True, on_delete=models.PROTECT,
        related_name="opened_sessions",
    )
    out_allocation = models.ForeignKey(
        PunchAllocation, null=True, blank=True, on_delete=models.PROTECT,
        related_name="closed_sessions",
    )
    started_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    worked_minutes = models.PositiveIntegerField(default=0)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.COMPLETE
    )
    is_manual = models.BooleanField(default=False)
    calculation_version = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "payroll_attendance_session"
        ordering = ("attendance_record", "sequence_number")
        constraints = [
            models.UniqueConstraint(
                fields=["attendance_record", "calculation_version", "sequence_number"],
                name="uniq_attendance_session_sequence",
            ),
            models.CheckConstraint(
                condition=models.Q(ended_at__isnull=True)
                | models.Q(started_at__isnull=True)
                | models.Q(ended_at__gte=models.F("started_at")),
                name="attendance_session_end_after_start",
            ),
        ]
        indexes = [models.Index(fields=["company", "started_at"])]

    def __str__(self):
        return f"session {self.sequence_number} of {self.attendance_record_id}"


class ReviewStatus(models.TextChoices):
    CLEAN = "clean", "Clean"
    NEEDS_REVIEW = "needs_review", "Needs review"
    REVIEWED = "reviewed", "Reviewed"


class AttendanceRecord(TenantOwned):
    ReviewStatus = ReviewStatus

    class AttendanceStatus(models.TextChoices):
        PRESENT = "present", "Present"
        HALF_DAY = "half_day", "Half day"
        ABSENT = "absent", "Absent"
        LEAVE = "leave", "Leave"
        HOLIDAY = "holiday", "Holiday"
        WEEKLY_OFF = "weekly_off", "Weekly off"
        INCOMPLETE = "incomplete", "Incomplete"

    class PunchStatus(models.TextChoices):
        COMPLETE = "complete", "Complete"
        MISSING_OUT = "missing_out", "Missing OUT"
        NO_PUNCH = "no_punch", "No punch"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT, related_name="attendance_records"
    )
    employee_assignment = models.ForeignKey(
        "employees.EmployeeAssignment", on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="attendance_records"
    )
    work_date = models.DateField()
    shift = models.ForeignKey(
        "scheduling.Shift", on_delete=models.PROTECT, related_name="attendance_records"
    )
    scheduled_start_at = models.DateTimeField()
    scheduled_end_at = models.DateTimeField()
    first_in_at = models.DateTimeField(null=True, blank=True)
    last_out_at = models.DateTimeField(null=True, blank=True)
    # In-office time: the sum of the day's check-in -> break-out and
    # break-in -> check-out sessions, plus the paid part of the break when the
    # shift grants one. This is what payroll reads, and it is no longer simply
    # last_out - first_in: time spent outside on a break is not worked.
    worked_minutes = models.PositiveIntegerField(default=0)
    # Check-in to check-out, breaks included. The figure a person recognises as
    # "how long I was at work today".
    total_minutes = models.PositiveIntegerField(default=0)
    # Time between a break-out and the matching break-in. Two names for one
    # measurement, kept because the dictionary (section 34) names both and
    # payroll penalties are written against outside_minutes.
    break_minutes = models.PositiveIntegerField(default=0)
    outside_minutes = models.PositiveIntegerField(default=0)
    break_count = models.PositiveIntegerField(default=0)
    # Time after the scheduled end. Kept apart from worked_minutes because
    # salary pays it at a different rate and only once it is approved
    # (plan step A9); until then approved_overtime_minutes stays 0.
    calculated_overtime_minutes = models.PositiveIntegerField(default=0)
    approved_overtime_minutes = models.PositiveIntegerField(default=0)
    late_minutes = models.PositiveIntegerField(default=0)
    early_out_minutes = models.PositiveIntegerField(default=0)
    attendance_status = models.CharField(max_length=16, choices=AttendanceStatus.choices)
    punch_status = models.CharField(max_length=16, choices=PunchStatus.choices)
    # How much of the day is payable: 1 present/paid, 0.5 half day, 0 absent or
    # unpaid. Payroll reads this rather than re-deriving it from the status.
    payable_fraction = models.DecimalField(max_digits=4, decimal_places=2, default=Decimal("0"))
    leave_day = models.ForeignKey(
        "leaves.LeaveDay", null=True, blank=True, on_delete=models.PROTECT,
        related_name="attendance_records",
    )
    note = models.CharField(max_length=255, blank=True)
    # Set by the rule, not by a person: a day closed at its shift end because
    # nobody scanned out, or an overtime session left open, both need somebody
    # to look. The reason is stored so the review list can say which.
    review_status = models.CharField(
        max_length=16, choices=ReviewStatus.choices, default=ReviewStatus.CLEAN
    )
    review_reason = models.CharField(max_length=120, blank=True)
    # True when last_out_at was decided by the shift end rather than a scan.
    check_out_by_rule = models.BooleanField(default=False)
    # The day has not finished yet, so its figures are provisional.
    is_open = models.BooleanField(default=False)
    calculated_at = models.DateTimeField()

    class Meta:
        db_table = "payroll_attendance_record"
        ordering = ("work_date",)
        constraints = [
            models.UniqueConstraint(
                fields=["company", "employee", "work_date"],
                name="uniq_attendance_record_per_employee_date",
            ),
        ]
        indexes = [models.Index(fields=["company", "work_date"])]

    def __str__(self):
        return f"{self.employee_id} {self.work_date} {self.attendance_status}"

    def delete(self, *args, **kwargs):
        """Drop the derived rows in dependency order, then the record.

        Sessions and allocations both cascade from the record, but a session
        PROTECTs the allocations it points at, so Django's collector refuses
        the cascade. Sessions are cleared first, which is the only order that
        can work, and doing it here means no caller has to know that.
        """
        self.sessions.all().delete()
        self.allocations.all().delete()
        return super().delete(*args, **kwargs)
