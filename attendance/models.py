"""Daily attendance, calculated from device punches, leave and the calendar.

Thin slice of the 2026-09-12 salary fast-track: one ``AttendanceRecord`` per
employee per day, built from the first IN and last OUT authorized punch.
Field names follow MODEL_FIELD_DICTIONARY.md §34; sessions, breaks,
corrections, overtime approval and review are listed in docs/PHASE_STATUS.md
as not built yet.
"""

from decimal import Decimal

from django.conf import settings
from django.db import models

from common.models import ActorTracked, TenantOwned


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
    # The other source: a scan somebody added by hand (plan step N5). Exactly
    # one of the two is set.
    attendance_correction = models.ForeignKey(
        "attendance.AttendanceCorrection", null=True, blank=True,
        on_delete=models.PROTECT, related_name="allocations",
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
            models.CheckConstraint(
                condition=(
                    models.Q(punch_event__isnull=False, attendance_correction__isnull=True)
                    | models.Q(punch_event__isnull=True, attendance_correction__isnull=False)
                ),
                name="punch_allocation_exactly_one_source",
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


class AttendanceCorrection(TenantOwned, ActorTracked):
    """Somebody fixing one employee-day by hand (plan step N5).

    Attendance recalculates itself whenever a punch arrives or the calendar
    moves, so a fix made by editing the record would be gone by the next scan.
    A correction is an input instead: every recalculation reads the corrections
    in force and builds the day with them, the same way overtime decisions are
    kept apart and read back (payroll.OvertimeDecision). A PunchEvent is never
    edited.

    Keyed by employee and date rather than by record, because the record is
    derived: a recalculation may remove a day and write it again.

    - **add_scan** — a scan the device never got. It joins the day's stream
      and is labelled by pairing like any other.
    - **change_status** — present, half day or absent, over what the scans
      say. At most one in force per day; a new one supersedes the old.
    - **accept_review** — "the check-out by rule is right". Kept only while
      the day still needs review for that same reason.

    Nothing is deleted: a mistake is withdrawn, and the audit log holds both.
    Applied straight away by an administrator or HR; there is no request and
    approval step yet.
    """

    class CorrectionType(models.TextChoices):
        ADD_SCAN = "add_scan", "Added a scan"
        CHANGE_STATUS = "change_status", "Changed the status"
        ACCEPT_REVIEW = "accept_review", "Accepted as it is"

    class Status(models.TextChoices):
        APPLIED = "applied", "In force"
        SUPERSEDED = "superseded", "Replaced by a later change"
        WITHDRAWN = "withdrawn", "Withdrawn"

    #: The statuses a person may set by hand. Leave, holidays and weekly offs
    #: come from their own pages, never from here.
    SETTABLE_STATUSES = ("present", "half_day", "absent")

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT,
        related_name="attendance_corrections",
    )
    work_date = models.DateField()
    correction_type = models.CharField(max_length=16, choices=CorrectionType.choices)
    proposed_event_at = models.DateTimeField(null=True, blank=True)
    proposed_status = models.CharField(max_length=16, blank=True)
    # For accept_review: the reason that was accepted. A different reason
    # appearing later (a new scan changed the day) needs its own look.
    accepted_review_reason = models.CharField(max_length=120, blank=True)
    reason = models.TextField()
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.APPLIED
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    withdrawn_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    withdrawn_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    before_snapshot = models.JSONField(default=dict, blank=True)
    after_snapshot = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "payroll_attendance_correction"
        ordering = ("-created_at",)
        constraints = [
            models.CheckConstraint(
                condition=(
                    ~models.Q(correction_type="add_scan")
                    | models.Q(proposed_event_at__isnull=False)
                ),
                name="attendance_correction_scan_has_time",
            ),
            models.CheckConstraint(
                condition=(
                    ~models.Q(correction_type="change_status")
                    | models.Q(proposed_status__in=["present", "half_day", "absent"])
                ),
                name="attendance_correction_status_is_settable",
            ),
            models.UniqueConstraint(
                fields=["company", "employee", "work_date"],
                condition=models.Q(correction_type="change_status", status="applied"),
                name="uniq_attendance_correction_status_in_force",
            ),
        ]
        indexes = [models.Index(fields=["company", "employee", "work_date"])]

    def __str__(self):
        return f"{self.get_correction_type_display()} {self.employee_id} {self.work_date}"


class MissedScanRequest(TenantOwned, ActorTracked):
    """An employee saying "I scanned in (or out) and the device missed it" (N11).

    Nothing counts until someone who may fix attendance in the day's branch
    approves it. Approving adds the scan as an ordinary ``AttendanceCorrection``
    (add_scan), so the day is rebuilt with it exactly as if HR had typed it on
    Fix a day, and the correction is linked here. Rejecting needs a note.
    Nobody decides their own request.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Waiting"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"
        WITHDRAWN = "withdrawn", "Withdrawn"

    employee = models.ForeignKey(
        "employees.Employee", on_delete=models.PROTECT,
        related_name="missed_scan_requests",
    )
    work_date = models.DateField()
    scan_at = models.DateTimeField()
    reason = models.TextField()
    # The day's branch when it was asked: who sees it waiting. The decision
    # checks the branch again.
    branch = models.ForeignKey(
        "organization.Branch", on_delete=models.PROTECT, related_name="+",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    submitted_at = models.DateTimeField()
    decided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="+",
    )
    decided_at = models.DateTimeField(null=True, blank=True)
    decision_note = models.TextField(blank=True)
    correction = models.ForeignKey(
        AttendanceCorrection, null=True, blank=True, on_delete=models.PROTECT,
        related_name="missed_scan_requests",
    )

    class Meta:
        db_table = "payroll_missed_scan_request"
        ordering = ("-submitted_at", "-pk")
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(status="approved") | models.Q(correction__isnull=False),
                name="missed_scan_request_approved_has_correction",
            ),
            models.UniqueConstraint(
                fields=["company", "employee", "scan_at"],
                condition=models.Q(status="pending"),
                name="uniq_missed_scan_request_pending",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "status", "branch"]),
            models.Index(fields=["company", "employee", "work_date"]),
        ]

    def __str__(self):
        return f"Missed scan {self.employee_id} {self.scan_at:%Y-%m-%d %H:%M}"
