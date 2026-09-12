"""Basic salary (2026-09-12 fast-track): the formula, and punches -> salary end to end."""

import datetime
from decimal import Decimal
from types import SimpleNamespace

from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance.models import AttendanceRecord
from common.tenant import use_company
from devices.models import DeviceModel, DeviceVendor
from employees.services import create_employee
from leaves import services as leave_services
from organization.catalogue import adopt_department, adopt_designation
from organization.models import Branch
from payroll.models import PayrollRecord
from payroll.services import calculate_pay, generate_payroll
from scheduling import services as schedule
from tenants.services import onboard_company

S = AttendanceRecord.AttendanceStatus


def day(status, fraction="1", worked=480, leave_minutes=0):
    leave = SimpleNamespace(leave_minutes=leave_minutes) if status == S.LEAVE else None
    return SimpleNamespace(
        attendance_status=status, payable_fraction=Decimal(fraction),
        worked_minutes=worked, late_minutes=0, leave_day=leave,
    )


class FormulaTests(TestCase):
    """calculate_pay is pure, so the agreed formula is checked without a database."""

    def month(self):
        return (
            [day(S.PRESENT)] * 20
            + [day(S.ABSENT, "0", 0)] * 2
            + [day(S.HALF_DAY, "0.5", 260)]
            + [day(S.LEAVE, "0", 0)]                    # unpaid leave
            + [day(S.LEAVE, "1", 0, leave_minutes=480)]  # paid leave
            + [day(S.WEEKLY_OFF, "1", 0)] * 4
            + [day(S.HOLIDAY, "1", 0)]
        )

    def test_monthly_deducts_base_over_30_per_absent_unpaid_and_half_day(self):
        result = calculate_pay("monthly", Decimal("30000"), self.month())
        # 2 absent + 1 unpaid leave + 0.5 half day = 3.5 days x 1000
        self.assertEqual(result["gross"], Decimal("30000.00"))
        self.assertEqual(result["deductions"], Decimal("3500.00"))
        self.assertEqual(result["net"], Decimal("26500.00"))

    def test_daily_pays_payable_working_days_only(self):
        result = calculate_pay("daily", Decimal("1000"), self.month())
        # 20 present + 0.5 half + 1 paid leave; weekly offs and holidays unpaid.
        self.assertEqual(result["net"], Decimal("21500.00"))

    def test_hourly_pays_hours_worked_plus_paid_leave_hours(self):
        result = calculate_pay("hourly", Decimal("100"), self.month())
        # 20 x 8h + 260 min = 164.33h, plus 8h paid leave
        self.assertEqual(result["net"], Decimal("17233.33"))

    def test_salary_never_goes_negative(self):
        result = calculate_pay("monthly", Decimal("3000"), [day(S.ABSENT, "0", 0)] * 31)
        self.assertEqual(result["net"], Decimal("0.00"))


class EndToEndTests(TestCase):
    """Demo punches through the real ingestion path -> attendance -> salary."""

    def setUp(self):
        self.company = onboard_company(code="ACME", slug="acme", name="Acme Ltd")
        self.admin = User.objects.create_user(email="admin@acme.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=self.admin,
            role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with use_company(self.company):
            hq = Branch.objects.get(is_default=True)
            department = adopt_department(hq, "SW", "Software")
            designation = adopt_designation(department, "DEV", "Developer")
        start = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
        self.monthly = create_employee(
            company=self.company, first_name="Rahim", employee_code="E1", branch=hq,
            department=department, designation=designation, effective_from=start,
            pay_basis="monthly", base_rate=Decimal("30000"),
        )["employee"]
        self.daily = create_employee(
            company=self.company, first_name="Karim", employee_code="E2", branch=hq,
            department=department, designation=designation, effective_from=start,
            pay_basis="daily", base_rate=Decimal("1000"),
        )["employee"]
        shift = schedule.create_shift(actor=self.admin, company_id=self.company.pk, values={
            "code": "DAY", "name": "Day", "start_time": datetime.time(9),
            "end_time": datetime.time(18), "spans_next_day": False,
            "grace_in_minutes": 10, "minimum_full_day_minutes": 480,
            "minimum_half_day_minutes": 240,
        })
        schedule.update_attendance_settings(
            actor=self.admin, company_id=self.company.pk,
            values={"company_shift": shift, "missing_punch_policy": "review_required"},
        )
        schedule.add_weekly_offs(actor=self.admin, company_id=self.company.pk, values={
            "weekdays": [4], "branch": None, "is_paid": True,
            "effective_from": datetime.date(2026, 1, 1),
        })
        casual = leave_services.create_leave_type(
            actor=self.admin, company_id=self.company.pk,
            values={"code": "CL", "name": "Casual", "description": ""},
        )
        # Two unpaid working days in August: Mon 10 and Tue 11.
        leave_services.record_leave(actor=self.admin, company_id=self.company.pk, values={
            "employee": self.monthly, "leave_type": casual,
            "start_date": datetime.date(2026, 8, 10), "end_date": datetime.date(2026, 8, 11),
            "pay_type": "unpaid", "reason": "",
        })
        # The hardware catalogue is seeded by a migration, but a migration test
        # elsewhere in the suite flushes it; make sure this test has one.
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco", defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        DeviceModel.objects.get_or_create(
            vendor=vendor, model_code="senseface-2a",
            defaults={"name": "SenseFace 2A", "protocol": DeviceModel.Protocol.ADMS_PUSH},
        )
        call_command("seed_demo_punches", company="ACME", month="2026-08", force=True)

    def test_month_of_punches_becomes_attendance_and_salary(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            records = AttendanceRecord.objects.filter(employee=self.monthly)
            self.assertEqual(records.count(), 31)
            self.assertEqual(records.filter(attendance_status=S.WEEKLY_OFF).count(), 4)
            self.assertEqual(records.filter(attendance_status=S.LEAVE).count(), 2)
            self.assertTrue(records.filter(attendance_status=S.PRESENT).exists())
            self.assertTrue(records.filter(first_in_at__isnull=False).exists())

            monthly = PayrollRecord.objects.get(payroll_run=run, employee=self.monthly)
            counts = monthly.calculation_snapshot["counts"]
            deducted_days = (
                counts.get("absent", 0) + counts.get("unpaid_leave", 0)
                + Decimal(counts.get("half_day", 0)) / 2
            )
            self.assertEqual(
                monthly.net_pay,
                (Decimal("30000") - Decimal(deducted_days) * 1000).quantize(Decimal("0.01")),
            )
            self.assertEqual(counts["unpaid_leave"], 2)
            daily = PayrollRecord.objects.get(payroll_run=run, employee=self.daily)
            self.assertGreater(daily.net_pay, Decimal("0"))

    def test_regenerating_replaces_the_draft(self):
        first = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        second = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        self.assertEqual(first.pk, second.pk)
        with use_company(self.company):
            self.assertEqual(PayrollRecord.objects.filter(payroll_run=second).count(), 2)

    def test_pages_render(self):
        run = generate_payroll(actor=self.admin, company_id=self.company.pk, year=2026, month=8)
        with use_company(self.company):
            record = PayrollRecord.objects.filter(payroll_run=run).first()
        self.client.force_login(self.admin)
        for url in (
            reverse("attendance:attendance_list") + "?month=8&year=2026",
            reverse("payroll:payroll_home") + "?month=8&year=2026",
            reverse("payroll:payslip", args=[record.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
        self.assertContains(self.client.get(reverse("payroll:payslip", args=[record.pk])), "Net pay")
