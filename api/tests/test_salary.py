"""Phase 9, part a: a month's salary, payslips, penalties and overtime
(docs/api/90-salary.md)."""

from api.tests.test_attendance import AttendanceApiTestCase, a_weekday


class SalaryApiTestCase(AttendanceApiTestCase):
    def setUp(self):
        super().setUp()
        self.year, self.month = self.late_day.year, self.late_day.month
        self.path = f"/api/v1/payroll/months/{self.year}/{self.month}"

    def generate(self):
        made = self.api("POST", f"{self.path}/generate")
        self.assertEqual(made.status_code, 200, made.content)
        return made.json()

    def payslip_id(self):
        return self.api("GET", f"{self.path}/payslips").json()["results"][0]["id"]


class MonthTests(SalaryApiTestCase):
    def test_draft_submit_approve_and_undo(self):
        before = self.api("GET", self.path).json()
        self.assertEqual((before["status"], before["may_generate"]), (None, True))
        made = self.generate()
        self.assertEqual((made["generated"], made["month"]["status"]), (1, "draft"))
        row = self.api("GET", f"{self.path}/payslips").json()["results"][0]
        self.assertEqual((row["employee"]["name"], row["pay_basis"], row["base_rate"]),
                         ("Rahim", "monthly", "30000.00"))
        submitted = self.api("POST", f"{self.path}/submit")
        self.assertEqual(submitted.json()["status"], "submitted", submitted.content)
        again = self.api("POST", f"{self.path}/generate")
        self.assertEqual(again.status_code, 422)
        approved = self.api("POST", f"{self.path}/approve")
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual((approved.json()["status"], approved.json()["finalised_by"]),
                         ("posted", "admin@example.test"))
        no_reason = self.api("POST", f"{self.path}/reopen", {})
        self.assertIn("reason", self.fields(no_reason))
        reopened = self.api("POST", f"{self.path}/reopen", {"reason": "Wrong rate used"})
        self.assertEqual(reopened.json()["status"], "draft")

    def test_send_back(self):
        self.generate()
        self.api("POST", f"{self.path}/submit")
        back = self.api("POST", f"{self.path}/send-back", {"reason": "Overtime missing"})
        self.assertEqual((back.json()["status"], back.json()["sent_back_reason"]),
                         ("draft", "Overtime missing"))

    def test_hr_sees_no_pay(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        refused = self.api("GET", self.path, session=hr)
        self.assertEqual(self.code_of(refused), "permission_denied")

    def test_the_payroll_manager_prepares_but_does_not_approve(self):
        self.person("pay@example.test", role="payroll_manager")
        payroll = self.logged_in("pay@example.test")
        made = self.api("POST", f"{self.path}/generate", session=payroll)
        self.assertEqual(made.status_code, 200, made.content)
        self.api("POST", f"{self.path}/submit", session=payroll)
        refused = self.api("POST", f"{self.path}/approve", session=payroll)
        self.assertEqual(self.code_of(refused), "permission_denied")

    def test_api_keys_need_the_scope(self):
        reader = self.key("payroll:read")
        self.assertEqual(self.as_key(reader, "GET", self.path).status_code, 200)
        refused = self.as_key(reader, "POST", f"{self.path}/generate")
        self.assertEqual(self.code_of(refused), "scope_missing")


class PayslipTests(SalaryApiTestCase):
    def test_a_payslip_lines_and_a_correction(self):
        self.generate()
        payslip = self.payslip_id()
        one = self.api("GET", f"/api/v1/payroll/payslips/{payslip}").json()
        self.assertEqual((one["status"], one["may_adjust"], one["employee_code"]),
                         ("draft", True, "E1"))
        self.assertTrue(one["earnings"])
        net = float(one["net"])
        added = self.api("POST", f"/api/v1/payroll/payslips/{payslip}/adjustments",
                         {"type": "earning", "amount": "5000", "reason": "Eid bonus"})
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual(float(added.json()["net"]), net + 5000)
        line = added.json()["adjustments"][0]
        zero = self.api("POST", f"/api/v1/payroll/payslips/{added.json()['id']}/adjustments",
                        {"type": "earning", "amount": "0", "reason": "x"})
        self.assertIn("amount", self.fields(zero))
        removed = self.api("DELETE", f"/api/v1/payroll/adjustments/{line['id']}")
        self.assertEqual(float(removed.json()["net"]), net)
        not_yet = self.api("POST", f"/api/v1/payroll/payslips/{removed.json()['id']}/corrections",
                           {"type": "earning", "amount": "100", "reason": "x"})
        self.assertEqual(not_yet.status_code, 422)
        self.api("POST", f"{self.path}/submit")
        self.api("POST", f"{self.path}/approve")
        payslip = self.payslip_id()
        corrected = self.api("POST", f"/api/v1/payroll/payslips/{payslip}/corrections",
                             {"type": "earning", "amount": "1200", "reason": "Missed overtime"})
        self.assertEqual(corrected.status_code, 201, corrected.content)
        self.assertTrue(corrected.json()["correction"]["paid_in"])
        self.assertEqual(corrected.json()["payslip"]["status"], "posted")

    def test_pdf_and_email(self):
        self.generate()
        payslip = self.payslip_id()
        pdf = self.api("GET", f"/api/v1/payroll/payslips/{payslip}/pdf")
        self.assertEqual((pdf.status_code, pdf["Content-Type"]), (200, "application/pdf"))
        emailed = self.api("POST", f"/api/v1/payroll/payslips/{payslip}/email", {})
        self.assertEqual(emailed.status_code, 422)
        self.assertTrue(self.api("GET", f"/api/v1/payroll/payslips/{payslip}")
                        .json()["email_blocked"])

    def test_a_missing_payslip(self):
        self.assertEqual(self.api("GET", "/api/v1/payroll/payslips/999999").status_code, 404)


class OvertimeTests(SalaryApiTestCase):
    def setUp(self):
        super().setUp()
        # A day of its own: on some dates a_weekday(5) is the late day too, and
        # its 09:25-18:05 scans would turn this into a day made up after the
        # shift (2026-10-07) with no overtime left.
        self.long_day = next(day for day in map(a_weekday, range(5, 40))
                             if day not in (self.late_day, self.open_day))
        self.punch(self.long_day, 9)
        self.punch(self.long_day, 20)
        self.year, self.month = self.long_day.year, self.long_day.month

    def day(self):
        listed = self.api("GET", "/api/v1/payroll/overtime",
                          query=f"year={self.year}&month={self.month}").json()
        return [r for r in listed["results"] if r["date"] == self.long_day.isoformat()][0]

    def test_approve_some_then_undo(self):
        day = self.day()
        self.assertEqual((day["state"], day["may_decide"]), ("automatic", True))
        self.assertGreater(day["minutes"], 60)
        decided = self.api("POST", f"/api/v1/payroll/overtime/{day['id']}/decide",
                           {"decision": "approve", "minutes": 60, "note": "Stock count"})
        self.assertEqual(decided.status_code, 200, decided.content)
        self.assertEqual((decided.json()["state"], decided.json()["approved_minutes"],
                          decided.json()["decided_by"]),
                         ("approved", 60, "admin@example.test"))
        waiting = self.api("GET", "/api/v1/payroll/overtime",
                           query=f"year={self.year}&month={self.month}&show=approved").json()
        self.assertEqual(waiting["count"], 1)
        undone = self.api("POST", f"/api/v1/payroll/overtime/{day['id']}/undo")
        self.assertEqual(undone.json()["state"], "automatic")

    def test_reject(self):
        day = self.day()
        rejected = self.api("POST", f"/api/v1/payroll/overtime/{day['id']}/decide",
                            {"decision": "reject"})
        self.assertEqual((rejected.json()["state"], rejected.json()["paid_minutes"]),
                         ("rejected", 0))
