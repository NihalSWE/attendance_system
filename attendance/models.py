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


class AttendanceRecord(TenantOwned):
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
    worked_minutes = models.PositiveIntegerField(default=0)
    late_minutes = models.PositiveIntegerField(default=0)
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
