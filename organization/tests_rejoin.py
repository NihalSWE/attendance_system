"""Somebody who left comes back (2026-10-19).

A test record had taken a real person's Employee ID and was then ended; the
Placement card refused it ("no current placement to change") and there was
no way back. Now a placement from a day after the last one brings them back.
"""

import datetime

from django.core.exceptions import ValidationError
from django.urls import reverse

from attendance import tests_live
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from employees.models import EmployeeAssignment
from organization import employee_edit_services as edit
from organization.tests_employee_detail import AUG_11, DetailTestCase, midnight

AUG_20 = datetime.date(2026, 8, 20)


class RejoinTests(DetailTestCase):
    record = tests_live.LiveTestCase.record

    def setUp(self):
        super().setUp()
        with use_company(self.company):
            self.first = EmployeeAssignment.objects.get(employee=self.employee)
        self.end(AUG_11)

    def values(self, day=AUG_20, code="E1"):
        return {"branch": self.first.branch, "department": self.first.department,
                "designation": self.first.designation, "employee_code": code,
                "effective_at": midnight(day), "reason": ""}

    def rejoin(self, **kwargs):
        return edit.change_placement(actor=self.admin, company_id=self.company.pk,
                                     employee_id=self.employee.pk, values=self.values(**kwargs))

    def test_a_placement_after_the_last_day_brings_them_back(self):
        assignment = self.rejoin()
        self.employee.refresh_from_db()
        self.assertEqual((self.employee.employment_status, self.employee.leaving_date),
                         ("active", None))
        self.assertIsNone(assignment.effective_to)
        rows = self.rows(EmployeeAssignment)
        self.assertEqual(len(rows), 2)                      # the old one stays as history
        self.assertEqual(rows[0].status, EmployeeAssignment.Status.ENDED)
        with use_company(self.company):
            self.assertTrue(AuditLog.objects.filter(action="employee.rejoined").exists())

    def test_not_on_or_before_the_day_they_left(self):
        for day in (AUG_11, AUG_11 - datetime.timedelta(days=3)):
            with self.subTest(day=day), self.assertRaises(ValidationError) as caught:
                self.rejoin(day=day)
            self.assertIn("left on 11 Aug 2026", " ".join(caught.exception.messages))
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "resigned")

    def test_their_scans_count_again(self):
        self.rejoin()
        self.punch(AUG_20, 9)
        self.punch(AUG_20, 18)
        recalculate(self.company.pk, employee_ids=[self.employee.pk], start=AUG_20, end=AUG_20)
        record = self.record(AUG_20)
        self.assertIsNotNone(record)
        self.assertEqual(record.attendance_status, "present")

    def test_from_the_edit_page(self):
        self.client.force_login(self.admin)
        url = reverse("organization:employee_edit", args=[self.employee.pk])
        page = self.client.get(url)
        self.assertContains(page, "left on 11 Aug 2026")
        response = self.client.post(url, {
            "section": "placement", "branch": self.first.branch_id,
            "department": self.first.department_id, "designation": self.first.designation_id,
            "employee_code": "E1", "placement_from": "2026-08-20", "placement_reason": "Back"})
        self.assertEqual(response.status_code, 302)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")
        self.assertNotContains(self.client.get(url), "left on 11 Aug 2026")
