"""Cycle overtime uses the panel's service and salary permissions."""

from api.tests.test_salary import SalaryApiTestCase


class CycleOvertimeApiTests(SalaryApiTestCase):
    def test_save_regenerate_revert_and_locked_cycle(self):
        self.generate()
        url = f"/api/v1/payroll/payslips/{self.payslip_id()}/overtime"
        self.assertIsNone(self.api("GET", url).json()["override"])
        saved = self.api("POST", url, {"hours": "4", "day_off_hours": "0", "reason": "Checked cycle"})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertTrue(saved.json()["regenerate_required"])
        self.assertEqual(self.api("POST", f"{self.path}/submit").status_code, 422)
        self.generate()
        url = f"/api/v1/payroll/payslips/{self.payslip_id()}/overtime"
        self.assertFalse(self.api("GET", url).json()["regenerate_required"])
        reverted = self.api("POST", url, {"action": "revert", "reason": "Use attendance"})
        self.assertIsNone(reverted.json()["override"])
        self.assertTrue(reverted.json()["regenerate_required"])
        self.generate()
        url = f"/api/v1/payroll/payslips/{self.payslip_id()}/overtime"
        self.api("POST", f"{self.path}/submit")
        self.assertEqual(self.api("POST", url, {"hours": "2", "reason": "Locked"}).status_code, 422)

    def test_write_scope_and_reason_required(self):
        self.generate()
        url = f"/api/v1/payroll/payslips/{self.payslip_id()}/overtime"
        self.assertEqual(self.api("POST", url, {"hours": "2"}).status_code, 422)
        reader = self.key("payroll:read")
        self.assertEqual(self.code_of(self.as_key(reader, "POST", url, {"hours": "2", "reason": "No write"})), "scope_missing")
