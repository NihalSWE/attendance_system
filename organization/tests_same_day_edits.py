"""Editing someone on the day their placement began (found 2026-09-21).

A device import starts a person's placement at the moment of the import
(devices/services/mapping.py and user_sync.py), for example 14:05. Edit
employee works in whole days: "from 21 Sep" means midnight on the 21st, which
is *before* 14:05. So on the day of an import, setting the real department
"from today" was refused ("a change cannot start before it"), and the first
salary on its own default date - the day they were placed - was refused on any
day ("the salary cannot start before that"). The page offers days, so a date
on the same day the placement or salary began now means that day.

These drive the real device import, so the case is exactly the one HR meets.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.urls import reverse

from common.tenant import use_company
from devices.services import mapping
from devices.tests_mapping import MappingCase, USERS
from employees.models import EmployeeAssignment, EmployeeCompensation
from organization.employee_detail_services import company_today
from organization.employee_edit_forms import _start_of
from organization.employee_edit_services import change_placement, change_salary


class SameDayCase(MappingCase):
    def setUp(self):
        super().setUp()
        self.upload(USERS)              # 445962 Ajay, 445900 Moin, 777 Nobody
        with use_company(self.company):
            result = mapping.import_users(actor=self.admin, device=self.device, pins=["777"])
        self.imported = result.created[0]
        self.today = company_today(self.company)
        self.midnight = _start_of(self.today, self.company)
        with use_company(self.company):
            self.placement = EmployeeAssignment.objects.get(employee=self.imported)
            # The import itself starts placements at midnight now
            # (devices.services.mapping.day_start, 2026-09-22), which is what
            # stopped a person brought in after noon losing that day. A row
            # that begins mid-afternoon is still reachable - an employee
            # created some other way, or one of these rows before that fix -
            # and it is that row these tests are about, so one is made here
            # rather than relying on the import to produce it.
            self.placement.effective_from = self.midnight + datetime.timedelta(
                hours=14, minutes=5)
            self.placement.save(update_fields=["effective_from"])

    def placements(self):
        with use_company(self.company):
            return list(EmployeeAssignment.objects.filter(employee=self.imported))

    def salaries(self):
        with use_company(self.company):
            return list(EmployeeCompensation.objects.filter(employee=self.imported)
                        .order_by("effective_from"))


class PlacementTests(SameDayCase):
    def test_the_placement_under_test_really_starts_after_midnight(self):
        """The premise: without it, none of this is being tested."""
        self.assertGreater(self.placement.effective_from, self.midnight)

    def test_the_import_itself_places_people_from_midnight(self):
        """The bug these rules worked around is fixed at its source.

        A placement stamped at the moment of import left anyone brought in
        after noon with no attendance day at all (Dia, 2026-09-22).
        """
        row = ("user uid=20\tcardno=\tpin=888\tpassword=\tgroup=1\tstarttime=0"
               "\tendtime=0\tname=Newcomer\tprivilege=0\tdisable=0\tverify=0\n")
        self.upload(row, cmdid="9")
        with use_company(self.company):
            fresh = mapping.import_users(actor=self.admin, device=self.device, pins=["888"])
            placement = EmployeeAssignment.objects.get(employee=fresh.created[0])
        self.assertEqual(placement.effective_from, self.midnight)

    def test_setting_the_department_from_today_is_accepted_as_a_correction(self):
        change_placement(actor=self.admin, company_id=self.company.pk,
                         employee_id=self.imported.pk, values={
                             "branch": self.hq, "department": self.department,
                             "designation": self.designation, "employee_code": "777",
                             "effective_at": self.midnight, "reason": "",
                         })
        rows = self.placements()
        self.assertEqual(len(rows), 1)                    # corrected, no history line
        self.assertEqual(rows[0].department, self.department)
        self.assertEqual(rows[0].effective_from, self.placement.effective_from)

    def place(self, starts, department=None):
        return change_placement(actor=self.admin, company_id=self.company.pk,
                                employee_id=self.imported.pk, values={
                                    "branch": self.hq, "department": department or self.department,
                                    "designation": self.designation, "employee_code": "777",
                                    "effective_at": starts, "reason": "",
                                })

    def test_their_first_placement_can_start_earlier(self):
        # Added today, working there since 1 Jun 2023 (Nihal, 2026-10-03).
        since = _start_of(datetime.date(2023, 6, 1), self.company)
        self.place(since)
        (row,) = self.placements()                        # moved back, no history line
        self.assertEqual((row.effective_from, row.department), (since, self.department))

    def active(self):
        return sorted((r for r in self.placements() if r.status != "cancelled"),
                      key=lambda r: r.effective_from)

    def test_an_earlier_date_replaces_the_history_from_then(self):
        # The latest save wins (Nihal, 2026-10-03): a change saved for today,
        # then "since 1 Jun 2023" - the history from 2023 gives way to it.
        tomorrow = _start_of(self.today + datetime.timedelta(days=1), self.company)
        self.place(tomorrow)                              # a transfer: two rows now
        since = _start_of(datetime.date(2023, 6, 1), self.company)
        self.place(since)
        (row,) = self.active()
        self.assertEqual((row.effective_from, row.effective_to), (since, None))
        self.assertEqual(len(self.placements()), 2)       # the old one is kept, cancelled

    def test_a_date_inside_the_history_ends_the_row_running_then(self):
        later = _start_of(self.today + datetime.timedelta(days=5), self.company)
        self.place(later)
        middle = _start_of(self.today + datetime.timedelta(days=2), self.company)
        self.place(middle, department=self.department)
        first, current = self.active()
        self.assertEqual((first.effective_to, current.effective_from), (middle, middle))

    def test_never_into_a_finalised_payroll(self):
        from payroll.models import PayrollPeriod, PayrollRecord, PayrollRun

        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026", start_date=datetime.date(2026, 8, 1),
                end_date=datetime.date(2026, 8, 31))
            run = PayrollRun.objects.create(company=self.company, payroll_period=period,
                                            status=PayrollRun.Status.POSTED)
            PayrollRecord.objects.create(company=self.company, payroll_run=run,
                                         employee=self.imported)
        with self.assertRaises(ValidationError) as caught:
            self.place(_start_of(datetime.date(2026, 8, 15), self.company))
        self.assertIn("already finalised; a change can start on 01 Sep 2026", str(caught.exception))
        self.assertEqual(self.placements()[0].effective_from, self.placement.effective_from)
        self.place(_start_of(datetime.date(2026, 9, 1), self.company))   # after it: fine

    def test_from_the_edit_page(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("organization:employee_edit", args=[self.imported.pk]), {
                "section": "placement", "branch": self.hq.pk,
                "department": self.department.pk, "designation": self.designation.pk,
                "employee_code": "777", "placement_from": self.today.isoformat(),
            }, follow=True)
        self.assertContains(response, "Placement saved.")
        self.assertEqual(self.placements()[0].department, self.department)


class SalaryTests(SameDayCase):
    def salary(self, starts, rate="20000"):
        return change_salary(actor=self.admin, company_id=self.company.pk,
                             employee_id=self.imported.pk, values={
                                 "pay_basis": "monthly", "base_rate": Decimal(rate),
                                 "effective_at": starts, "reason": "",
                             })

    def test_the_first_salary_on_the_placement_day_is_accepted(self):
        self.salary(self.midnight)
        (pay,) = self.salaries()
        # It starts with the placement, never before it.
        self.assertEqual(pay.effective_from, self.placement.effective_from)
        self.assertEqual(pay.base_rate, Decimal("20000"))

    def test_the_first_salary_before_the_placement_day_is_still_refused(self):
        with self.assertRaises(ValidationError):
            self.salary(self.midnight - datetime.timedelta(days=1))
        self.assertEqual(self.salaries(), [])

    def test_their_first_salary_can_start_earlier_but_not_before_the_placement(self):
        later = _start_of(self.today + datetime.timedelta(days=5), self.company)
        self.salary(later)
        self.salary(self.midnight)                        # back to the placement day
        (pay,) = self.salaries()
        self.assertEqual(pay.effective_from, self.placement.effective_from)
        with self.assertRaises(ValidationError) as caught:
            self.salary(self.midnight - datetime.timedelta(days=1))
        self.assertIn(f"placed on {self.today:%d %b %Y}", str(caught.exception))

    def test_an_earlier_salary_date_replaces_the_history_from_then(self):
        self.salary(_start_of(self.today + datetime.timedelta(days=3), self.company))
        self.salary(_start_of(self.today + datetime.timedelta(days=6), self.company), "30000")
        self.salary(self.midnight, "25000")               # back to the placement day
        active = [p for p in self.salaries() if p.status != "cancelled"]
        self.assertEqual([(p.effective_from, p.base_rate) for p in active],
                         [(self.placement.effective_from, Decimal("25000"))])

    def test_changing_that_salary_the_same_day_corrects_it(self):
        self.salary(self.midnight)
        self.salary(self.midnight, rate="25000")
        (pay,) = self.salaries()
        self.assertEqual(pay.base_rate, Decimal("25000"))

    def test_from_the_edit_page_with_its_own_default_date(self):
        """The form's default "From" is the day they were placed."""
        self.client.force_login(self.admin)
        url = reverse("organization:employee_edit", args=[self.imported.pk])
        page = self.client.get(url)
        default = page.context["salary"].initial["salary_from"]
        self.assertEqual(default, self.today)
        response = self.client.post(url, {
            "section": "salary", "pay_basis": "monthly", "base_rate": "20000",
            "salary_from": default.isoformat(),
        }, follow=True)
        self.assertContains(response, "Salary set.")
        self.assertEqual(len(self.salaries()), 1)
