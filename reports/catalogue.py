"""Every report: its page, its menu group, what period it covers and who may
see it. The Reports menu, the Reports page and the URLs are all read from here.
"""

from dataclasses import dataclass

from reports import builders
from reports.access import ATTENDANCE, LEAVE, OVERTIME
from reports.filters import DAY, MONTH, RANGE, WEEK


@dataclass(frozen=True)
class Report:
    slug: str
    title: str
    menu: str              # its label in the Reports menu
    group: str             # the menu heading it sits under, or ""
    kind: str              # whose rules decide who sees it (reports.access)
    period: str            # day, week, month or range
    build: object
    description: str
    extras: tuple = ()     # report-specific filters
    max_days: int = 92

    @property
    def url_name(self):
        return f"reports:{self.slug.replace('-', '_')}"

    @property
    def code(self):
        """The branch permission that opens it (access_control.page_access)."""
        return {ATTENDANCE: "attendance.view", LEAVE: ("leave.view", "leave.record"),
                OVERTIME: ("overtime.view", "overtime.decide")}[self.kind]


ATTENDANCE_GROUP, ABSENT_GROUP, LATE_GROUP = "Attendance Report", "Absent Report", "Late Report"

REPORTS = (
    Report("daily-attendance", "Daily Attendance Report", "Daily", ATTENDANCE_GROUP,
           ATTENDANCE, DAY, builders.daily_attendance,
           "Everyone's day: status, in and out, hours worked, late and overtime."),
    Report("weekly-attendance", "Weekly Attendance Report", "Weekly", ATTENDANCE_GROUP,
           ATTENDANCE, WEEK, builders.weekly_attendance,
           "A week at a glance: one line per person, one letter per day, and totals."),
    Report("monthly-attendance", "Monthly Attendance Report", "Monthly", ATTENDANCE_GROUP,
           ATTENDANCE, MONTH, builders.monthly_attendance,
           "The month's register: one line per person, one letter per day, and totals."),
    Report("custom-attendance", "Customize Attendance Report", "Customize", ATTENDANCE_GROUP,
           ATTENDANCE, RANGE, builders.custom_attendance,
           "Any dates: totals per person, or every day - for one status if you like.",
           extras=("view", "status")),
    Report("leave", "Leave Report", "Leave Report", "", LEAVE, RANGE, builders.leave,
           "Leave taken or asked for in a period, by person and leave type.",
           extras=("leave_status", "leave_type"), max_days=366),
    Report("daily-absent", "Daily Absent Report", "Daily", ABSENT_GROUP,
           ATTENDANCE, DAY, builders.daily_absent, "Who was absent on a day."),
    Report("monthly-absent", "Monthly Absent Report", "Monthly", ABSENT_GROUP,
           ATTENDANCE, MONTH, builders.monthly_absent,
           "Absent days in a month, per person, and which days."),
    Report("daily-late", "Daily Late Report", "Daily", LATE_GROUP,
           ATTENDANCE, DAY, builders.daily_late,
           "Who came in late on a day, and by how much."),
    Report("monthly-late", "Monthly Late Report", "Monthly", LATE_GROUP,
           ATTENDANCE, MONTH, builders.monthly_late,
           "Late days and minutes in a month, per person."),
    Report("working-hours", "Working Hour Report", "Working Hour Report", "",
           ATTENDANCE, RANGE, builders.working_hours,
           "Hours worked against shift hours, per person, over any dates."),
    Report("short-hours", "Less than Full Working Hour Report",
           "Less than Full Working Hour Report", "", ATTENDANCE, RANGE, builders.short_hours,
           "Days someone came in but worked less than their shift."),
    Report("overtime", "Overtime Report", "Overtime Report", "", OVERTIME, RANGE,
           builders.overtime, "Overtime worked, and how much of it was approved."),
    Report("entry-logs", "Entry Logs Report", "Entry Logs Report", "", ATTENDANCE, RANGE,
           builders.entry_logs, "Every scan the terminals sent, as they sent it.",
           max_days=31),
)

BY_SLUG = {report.slug: report for report in REPORTS}
