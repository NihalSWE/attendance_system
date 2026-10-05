"""Phase 9, part b: salary settings and rules, components, penalty rules, LFA
(docs/api/90-salary.md)."""

import datetime

from django.utils import timezone

from api.tests.test_salary import SalaryApiTestCase


def this_month():
    return timezone.localdate().strftime("%Y-%m")


class SettingsTests(SalaryApiTestCase):
    def test_general_settings_and_new_rules(self):
        shown = self.api("GET", "/api/v1/payroll/settings")
        self.assertEqual(shown.status_code, 200, shown.content)
        self.assertTrue(shown.json()["summary"])
        changed = self.api("PATCH", "/api/v1/payroll/settings", {"pay_day": 5})
        self.assertEqual((changed.json()["pay_day"], changed.json()["currency"]),
                         (5, shown.json()["currency"]))
        bad = self.api("PATCH", "/api/v1/payroll/settings", {"currency": "TAKA"})
        self.assertIn("currency", self.fields(bad))
        rules = self.api("POST", "/api/v1/payroll/settings/rules",
                         {"applies_from": this_month(), "overtime_multiplier": "1.5"})
        self.assertEqual(rules.status_code, 200, rules.content)
        self.assertEqual(rules.json()["rules"]["overtime_multiplier"], "1.50")
        self.assertEqual(rules.json()["rules"]["monthly_proration_method"],
                         shown.json()["rules"]["monthly_proration_method"])
        wrong = self.api("POST", "/api/v1/payroll/settings/rules",
                         {"applies_from": "October", "overtime_multiplier": "1.5"})
        self.assertIn("applies_from", self.fields(wrong))
        too_much = self.api("POST", "/api/v1/payroll/settings/rules",
                            {"applies_from": this_month(), "overtime_multiplier": "50"})
        self.assertIn("overtime_multiplier", self.fields(too_much))

    def test_the_payroll_manager_cannot_change_settings(self):
        self.person("pay@example.test", role="payroll_manager")
        payroll = self.logged_in("pay@example.test")
        refused = self.api("GET", "/api/v1/payroll/settings", session=payroll)
        self.assertEqual(self.code_of(refused), "permission_denied")


class ComponentTests(SalaryApiTestCase):
    def test_a_component_given_to_someone_reaches_the_payslip(self):
        made = self.api("POST", "/api/v1/payroll/components", {
            "code": "TRANSPORT", "name": "Transport", "kind": "earning", "method": "fixed",
            "default_amount": "2000"})
        self.assertEqual(made.status_code, 201, made.content)
        no_amount = self.api("POST", "/api/v1/payroll/components", {
            "code": "FOOD", "name": "Food", "kind": "earning", "method": "fixed"})
        self.assertIn("default_amount", self.fields(no_amount))
        changed = self.api("PATCH", f"/api/v1/payroll/components/{made.json()['id']}",
                           {"default_amount": "2500"})
        self.assertEqual((changed.json()["default_amount"], changed.json()["code"]),
                         ("2500.00", "TRANSPORT"))
        first = datetime.date(self.year, self.month, 1)
        given = self.api("POST", f"/api/v1/employees/{self.rahim.pk}/components",
                         {"component_id": made.json()["id"], "effective_from": str(first)})
        self.assertEqual(given.status_code, 201, given.content)
        self.assertEqual(given.json()["amount"], "2500.00")
        self.generate()
        payslip = self.api("GET", f"/api/v1/payroll/payslips/{self.payslip_id()}").json()
        self.assertIn("Transport", [line["description"] for line in payslip["earnings"]])
        ended = self.api(
            "POST", f"/api/v1/employees/{self.rahim.pk}/components/{given.json()['id']}/end",
            {"last_day": str(first - datetime.timedelta(days=1))})
        self.assertIn("last_day", self.fields(ended))
        off = self.api("POST", f"/api/v1/payroll/components/{made.json()['id']}/status",
                       {"status": "inactive"})
        self.assertEqual(off.json()["status"], "inactive")
        listed = self.api("GET", f"/api/v1/employees/{self.rahim.pk}/components").json()
        self.assertEqual(listed["count"], 1)


class PenaltyRuleTests(SalaryApiTestCase):
    def test_a_rule_charges_and_can_be_waived(self):
        month = f"{self.year}-{self.month:02d}"
        made = self.api("POST", "/api/v1/payroll/penalty-rules", {
            "name": "Late", "metric": "late_minutes", "operator": "gt",
            "threshold_minutes": 0, "deduction_method": "fixed_amount",
            "deduction_value": "100", "applies_from": month})
        self.assertEqual(made.status_code, 201, made.content)
        self.assertTrue(made.json()["when"])
        self.generate()
        payslip = self.api("GET", f"/api/v1/payroll/payslips/{self.payslip_id()}").json()
        self.assertEqual(len(payslip["penalties"]), 1, payslip)
        waived = self.api("POST",
                          f"/api/v1/payroll/penalties/{payslip['penalties'][0]['id']}/waive")
        self.assertEqual(waived.status_code, 200, waived.content)
        self.assertEqual(waived.json()["penalties"][0]["status"], "waived")
        back = self.api("POST",
                        f"/api/v1/payroll/penalties/{payslip['penalties'][0]['id']}/unwaive")
        self.assertEqual(back.status_code, 200, back.content)
        next_month = (datetime.date(self.year, self.month, 28) + datetime.timedelta(days=5))
        changed = self.api("POST", f"/api/v1/payroll/penalty-rules/{made.json()['id']}/change",
                           {"deduction_value": "200",
                            "applies_from": next_month.strftime("%Y-%m")})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual((changed.json()["deduction_value"], changed.json()["state"],
                          changed.json()["name"]), ("200.0000", "upcoming", "Late"))
        no_month = self.api("POST", f"/api/v1/payroll/penalty-rules/{made.json()['id']}/change",
                            {"deduction_value": "300"})
        self.assertIn("applies_from", self.fields(no_month))
        stopped = self.api("POST",
                           f"/api/v1/payroll/penalty-rules/{changed.json()['id']}/stop",
                           {"stops_from": (next_month + datetime.timedelta(days=31))
                            .strftime("%Y-%m")})
        self.assertEqual(stopped.status_code, 204, stopped.content)


class LfaTests(SalaryApiTestCase):
    def setUp(self):
        super().setUp()
        from common.tenant import use_company
        from employees.models import Employee

        with use_company(self.company):
            Employee.objects.filter(pk=self.rahim.pk).update(
                joining_date=datetime.date(2025, 1, 1))

    def switch_on(self, **extra):
        changed = self.api("PATCH", "/api/v1/payroll/lfa/settings", {
            "enabled": True, "name": "LFA", "amount_method": "fixed", "fixed_amount": "5000",
            "min_service_months": 0, "cycle": "calendar_year", "claims_per_cycle": 1,
            "payment": "separately", **extra})
        self.assertEqual(changed.status_code, 200, changed.content)
        return changed.json()

    def test_claim_decide_and_pay(self):
        off = self.api("GET", "/api/v1/payroll/lfa/eligibility",
                       query=f"employee_id={self.rahim.pk}").json()
        self.assertFalse(off["eligible"])
        self.assertTrue(self.switch_on()["enabled"])
        found = self.api("GET", "/api/v1/payroll/lfa/eligibility",
                         query=f"employee_id={self.rahim.pk}").json()
        self.assertEqual((found["eligible"], found["amount"]), (True, "5000.00"), found)
        claim = self.api("POST", "/api/v1/payroll/lfa/claims",
                         {"employee_id": self.rahim.pk, "note": "Family trip"})
        self.assertEqual(claim.status_code, 201, claim.content)
        self.assertEqual((claim.json()["status"], claim.json()["may_decide"]), ("pending", True))
        twice = self.api("POST", "/api/v1/payroll/lfa/claims", {"employee_id": self.rahim.pk})
        self.assertEqual(twice.status_code, 422)
        reject_bare = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim.json()['id']}/decide",
                               {"decision": "reject"})
        self.assertIn("note", self.fields(reject_bare))
        approved = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim.json()['id']}/decide",
                            {"decision": "approve", "amount": "4500"})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual((approved.json()["status"], approved.json()["approved_amount"]),
                         ("approved", "4500.00"))
        paid = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim.json()['id']}/paid",
                        {"paid_on": str(timezone.localdate()), "reference": "TRX-1"})
        self.assertEqual((paid.json()["status"], paid.json()["payment_reference"]),
                         ("paid", "TRX-1"))
        listed = self.api("GET", "/api/v1/payroll/lfa/claims", query="show=paid").json()
        self.assertEqual(listed["count"], 1)

    def test_paid_with_salary_needs_the_month(self):
        self.switch_on(payment="with_salary")
        claim = self.api("POST", "/api/v1/payroll/lfa/claims",
                         {"employee_id": self.rahim.pk}).json()
        no_month = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim['id']}/decide",
                            {"decision": "approve"})
        self.assertIn("pay_month", self.fields(no_month))
        approved = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim['id']}/decide",
                            {"decision": "approve", "pay_month": this_month()})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertTrue(approved.json()["pay_month"])
        cancelled = self.api("POST", f"/api/v1/payroll/lfa/claims/{claim['id']}/cancel",
                             {"note": "Trip cancelled"})
        self.assertEqual(cancelled.json()["status"], "cancelled")

    def test_bad_settings_are_refused(self):
        bad = self.api("PATCH", "/api/v1/payroll/lfa/settings",
                       {"enabled": True, "amount_method": "basic_months", "months": None})
        self.assertEqual(bad.status_code, 422)
