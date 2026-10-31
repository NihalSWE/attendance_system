"""Present days set by hand for a salary month or week (2026-10-31, payroll/overrides.py)."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import SimpleTestCase

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import EmployeeCompensation
from payroll import overrides
from payroll.models import DaysOverride, PayrollRun
from payroll.services import attendance_changed_since, calculate_pay, generate_payroll, submit_payroll
from payroll.tests_overtime import OvertimeBase

D = datetime.date
SHIFT = SimpleNamespace(scheduled_minutes=540, break_is_paid=False, default_break_minutes=60)


def day(on, status, worked=480, late=0, leave=None):
    return SimpleNamespace(
        attendance_status=status, payable_fraction=Decimal("1") if status in ("present", "leave") else (
            Decimal("0.5") if status == "half_day" else Decimal("0")),
        worked_minutes=worked, late_minutes=late, early_out_minutes=0, leave_day=leave, shift=SHIFT,
        work_date=D(2026, 8, on), approved_overtime_minutes=0)


def month():
    """20 working days: 15 present, 3 without a check-out, 1 half day, 1 absent; a leave day."""
    days = [day(n, "present") for n in range(1, 16)]
    days += [day(16, "incomplete", 0), day(17, "incomplete", 0), day(18, "incomplete", 0)]
    days += [day(19, "half_day", 240), day(20, "absent", 0), day(21, "leave", 0)]
    return days


def statuses(days):
    found = {}
    for one in days:
        found[one.attendance_status] = found.get(one.attendance_status, 0) + 1
    return found


class ApplyTests(SimpleTestCase):
    def test_present_days_become_full_days_and_the_rest_absent(self):
        days = month()
        self.assertEqual(overrides.counts(days)["working"], 20)
        made = overrides.apply(days, SimpleNamespace(present_days=19, hours=None))
        self.assertEqual(statuses(made), {"present": 19, "absent": 1, "leave": 1})
        # No check-out first, then the half day; the absent day stays absent.
        self.assertEqual([one.attendance_status for one in made[15:20]],
                         ["present", "present", "present", "present", "absent"])
        self.assertEqual(made[16].worked_minutes, 480)
        # The days themselves are untouched.
        self.assertEqual(statuses(days)["incomplete"], 3)

    def test_fewer_present_days_than_scanned(self):
        made = overrides.apply(month(), SimpleNamespace(present_days=10, hours=None))
        self.assertEqual(statuses(made), {"present": 10, "absent": 10, "leave": 1})
        self.assertEqual([one.work_date.day for one in made if one.attendance_status == "present"],
                         list(range(1, 11)))

    def test_a_monthly_salary_pays_the_days_set(self):
        scans = calculate_pay("monthly", "30000", month())
        by_hand = calculate_pay("monthly", "30000", overrides.apply(
            month(), SimpleNamespace(present_days=19, hours=None)))
        # 30,000 / 30 = 1,000 a day: one absent day left.
        self.assertEqual(by_hand["deductions"], Decimal("1000.00"))
        self.assertGreater(scans["deductions"], by_hand["deductions"])

    def test_hours_replace_the_scanned_hours(self):
        result = calculate_pay("hourly", "100", overrides.apply(
            month(), SimpleNamespace(present_days=None, hours=Decimal("150"))), worked_hours=Decimal("150"))
        hours = next(line for line in result["lines"] if line[1] == "HOURS")
        self.assertEqual((hours[3], hours[5]), (Decimal("150.00"), Decimal("15000.00")))
        self.assertFalse([line for line in result["lines"] if line[1] == "INCOMPLETE"])


class OverrideTests(OvertimeBase):
    FIRST, LAST = D(2026, 8, 1), D(2026, 8, 31)

    def setUp(self):
        super().setUp()
        self.work(D(2026, 8, 10), (9, 0), (18, 0))
        self.work(D(2026, 8, 11), (9, 0))

    def set(self, present=None, actor=None, **kwargs):
        kwargs.setdefault("reason", "Forgot to check out")
        return overrides.set_override(actor=actor or self.admin, company_id=self.company.pk,
                                      employee_id=self.employee.pk, first=self.FIRST, last=self.LAST,
                                      present_days=present, **kwargs)

    def generate(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            return run, run.records.get(employee=self.employee)

    def test_set_change_and_back_to_scans(self):
        run, scans = self.generate()
        scanned = scans.calculation_snapshot["counts"]["present"]
        with use_company(self.company):
            stored = list(AttendanceRecord.objects.filter(employee=self.employee).order_by("work_date")
                          .values_list("work_date", "attendance_status"))
        row = self.set(present=20)
        self.assertEqual((row.scan_present_days, row.present_days), (scanned, 20))
        self.assertEqual(attendance_changed_since(self.company.pk, run), 1)
        run, by_hand = self.generate()
        self.assertGreater(by_hand.net_pay, scans.net_pay)
        self.assertEqual(by_hand.calculation_snapshot["override"]["present_days"], 20)
        self.assertEqual(by_hand.calculation_snapshot["override"]["reason"], "Forgot to check out")
        # The attendance days are not rewritten.
        with use_company(self.company):
            self.assertEqual(list(AttendanceRecord.objects.filter(employee=self.employee)
                                  .order_by("work_date").values_list("work_date", "attendance_status")),
                             stored)
        self.set(present=21, reason="Supervisor confirmed")
        overrides.revert(actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                         first=self.FIRST, last=self.LAST)
        run, again = self.generate()
        self.assertEqual(again.net_pay, scans.net_pay)
        self.assertIsNone(again.calculation_snapshot["override"])
        self.assertEqual([row.status for row in overrides.history(self.company.pk, self.employee.pk,
                                                                 self.FIRST, self.LAST)],
                         ["reverted", "replaced"])
        self.assertEqual(AuditLog.objects.filter(action__startswith="payroll.days_override").count(), 3)

    def test_refused(self):
        with self.assertRaises(ValidationError) as caught:
            self.set(present=99, reason="")
        self.assertEqual(set(caught.exception.message_dict), {"present_days", "reason"})
        with self.assertRaisesMessage(ValidationError, "already follows the scans"):
            overrides.revert(actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
                             first=self.FIRST, last=self.LAST)
        # A login without attendance access.
        nobody = User.objects.create_user(email="emp@liv.test", password="pw")
        CompanyMembership.all_objects.create(company=self.company, user=nobody,
                                             role=CompanyMembership.Role.EMPLOYEE,
                                             status=CompanyMembership.Status.ACTIVE)
        with self.assertRaises(PermissionDenied):
            self.set(present=5, actor=nobody)
        # Waiting for approval: locked.
        self.generate()
        submit_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with self.assertRaisesMessage(ValidationError, "waits for approval or is finalised"):
            self.set(present=5)
        self.assertFalse(DaysOverride.all_objects.exists())
        with use_company(self.company):
            self.assertEqual(PayrollRun.objects.get().status, "submitted")

    def test_the_payslip_says_so(self):
        from django.urls import reverse

        from common.tests_exports import pdf_text

        self.generate()
        self.set(present=20)
        _run, record = self.generate()
        self.client.force_login(self.admin)
        page = self.client.get(reverse("payroll:payslip", args=[record.pk]))
        self.assertContains(page, "Present days set by hand: 20")
        self.assertContains(page, "Forgot to check out")
        self.assertEqual(page.context["counts"]["present"], 20)
        pdf = self.client.get(reverse("payroll:payslip", args=[record.pk]), {"format": "pdf"})
        self.assertIn("Set by hand", pdf_text(pdf.content))

    def test_hourly_pay_takes_hours(self):
        with use_company(self.company):
            EmployeeCompensation.objects.filter(employee=self.employee).update(
                pay_basis="hourly", base_rate=Decimal("200"))
        with self.assertRaises(ValidationError) as caught:
            self.set(present=5)
        self.assertIn("hours", caught.exception.message_dict)
        self.set(hours="40")
        _run, record = self.generate()
        with use_company(self.company):
            hours = record.lines.get(code="HOURS")
        self.assertEqual((hours.quantity, hours.amount), (Decimal("40.00"), Decimal("8000.00")))
