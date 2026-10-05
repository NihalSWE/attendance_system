"""Phase 8: leave - types, policies, balances, recorded leave and requests
(docs/api/80-leave.md)."""

import base64
import datetime
import shutil
import tempfile

from django.test import override_settings
from django.utils import timezone

from accounts.models import CompanyMembership
from api.tests.test_attendance import AttendanceApiTestCase
from common.tenant import use_company
from leaves import workflow
from leaves.models import LeaveDay, LeaveType
from leaves.services import create_leave_type

PDF = base64.b64encode(b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF").decode()


def next_weekday(days_ahead):
    """A Monday-Thursday at least ``days_ahead`` days from today."""
    day = timezone.localdate() + datetime.timedelta(days=days_ahead)
    while day.weekday() > 3:
        day += datetime.timedelta(days=1)
    return day


class LeaveApiTestCase(AttendanceApiTestCase):
    def setUp(self):
        super().setUp()
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.casual = create_leave_type(actor=self.admin_user, company_id=self.company.pk,
                                        values={"code": "CAS", "name": "Casual",
                                                "days_per_year": 10})
        self.monday = next_weekday(7)

    def record(self, **extra):
        return self.api("POST", "/api/v1/leave/records", {
            "employee_id": self.rahim.pk, "leave_type_id": self.casual.pk,
            "start_date": str(self.monday), "end_date": str(self.monday),
            "reason": "Family wedding", **extra})


class LeaveTypeTests(LeaveApiTestCase):
    def test_add_change_and_turn_off(self):
        added = self.api("POST", "/api/v1/leave/types",
                         {"code": "ml", "name": "Maternity", "days_per_year": "112",
                          "needs_document": True})
        self.assertEqual(added.status_code, 201, added.content)
        self.assertEqual((added.json()["code"], added.json()["needs_document"]), ("ML", True))
        changed = self.api("PATCH", f"/api/v1/leave/types/{added.json()['id']}",
                           {"days_per_year": "120"})
        self.assertEqual((changed.json()["days_per_year"], changed.json()["name"]),
                         ("120.00", "Maternity"))
        twice = self.api("POST", "/api/v1/leave/types", {"code": "ML", "name": "Again"})
        self.assertEqual(twice.status_code, 422)
        off = self.api("POST", f"/api/v1/leave/types/{added.json()['id']}/status",
                       {"status": "inactive"})
        self.assertEqual(off.json()["status"], "inactive")
        listed = self.api("GET", "/api/v1/leave/types").json()
        self.assertEqual(listed["count"], 2)

    def test_the_defaults_are_added_once(self):
        first = self.api("POST", "/api/v1/leave/types/defaults")
        self.assertEqual(first.status_code, 200, first.content)
        self.assertTrue(first.json()["added"])
        again = self.api("POST", "/api/v1/leave/types/defaults").json()
        self.assertEqual(again["added"], [])


class PolicyTests(LeaveApiTestCase):
    def test_a_policy_its_versions_and_the_balances(self):
        made = self.api("POST", "/api/v1/leave/policies",
                        {"code": "staff", "name": "Staff", "is_default": True})
        self.assertEqual(made.status_code, 201, made.content)
        policy = made.json()["id"]
        first = self.api("POST", f"/api/v1/leave/policies/{policy}/versions", {
            "effective_from": f"{timezone.localdate().year}-01-01",
            "rules": [{"leave_type_id": self.casual.pk, "days_per_year": "12"}]})
        self.assertEqual(first.status_code, 201, first.content)
        version = first.json()["versions"][0]
        self.assertTrue(version["started"])
        self.assertEqual(version["rules"][0]["days_per_year"], "12.00")
        started = self.api("PUT", f"/api/v1/leave/policies/{policy}/versions/{version['id']}",
                           {"effective_from": f"{timezone.localdate().year}-01-01",
                            "rules": [{"leave_type_id": self.casual.pk, "days_per_year": "9"}]})
        self.assertEqual(started.status_code, 422)
        later = self.api("POST", f"/api/v1/leave/policies/{policy}/versions", {
            "effective_from": f"{timezone.localdate().year + 1}-01-01",
            "rules": [{"leave_type_id": self.casual.pk, "days_per_year": "14",
                       "carry_forward_expires_months": 3}]})
        self.assertIn("rules[0].carry_forward_expires_months", self.fields(later))
        bad_type = self.api("POST", f"/api/v1/leave/policies/{policy}/versions", {
            "effective_from": f"{timezone.localdate().year + 1}-01-01",
            "rules": [{"leave_type_id": 999999, "days_per_year": "14"}]})
        self.assertIn("rules[0].leave_type_id", self.fields(bad_type))
        balances = self.api("GET", "/api/v1/leave/balances",
                            query=f"employee_id={self.rahim.pk}").json()
        line = balances["results"][0]["balances"][0]
        self.assertEqual((line["policy"], line["given"]), ("Staff", "12.00"))
        default_off = self.api("POST", f"/api/v1/leave/policies/{policy}/status",
                               {"status": "inactive"})
        self.assertEqual(default_off.status_code, 422)

    def test_balances_without_a_policy_use_days_per_year(self):
        line = self.api("GET", "/api/v1/leave/balances").json()["results"][0]["balances"][0]
        self.assertEqual((line["by_policy"], line["given"], line["left"]),
                         (False, "10.00", "10.00"))

    def test_hr_cannot_set_up_policies(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        refused = self.api("GET", "/api/v1/leave/policies", session=hr)
        self.assertEqual(self.code_of(refused), "permission_denied")


class RecordTests(LeaveApiTestCase):
    def test_record_change_and_cancel(self):
        recorded = self.record(end_date=str(self.monday + datetime.timedelta(days=1)))
        self.assertEqual(recorded.status_code, 201, recorded.content)
        leave = recorded.json()
        self.assertEqual((leave["status"], leave["days"], leave["may_change"]),
                         ("approved", "2.00", True))
        listed = self.api("GET", "/api/v1/leave/records",
                          query=f"from={self.monday}&to={self.monday}").json()
        self.assertEqual([r["id"] for r in listed["results"]], [leave["id"]])
        changed = self.api("POST", f"/api/v1/leave/records/{leave['id']}/amend",
                           {"pay_type": "unpaid"})
        self.assertEqual(changed.status_code, 200, changed.content)
        self.assertEqual((changed.json()["pay_type"], changed.json()["days"]), ("unpaid", "2.00"))
        some = self.api("POST", f"/api/v1/leave/records/{leave['id']}/cancel",
                        {"days": [str(self.monday + datetime.timedelta(days=1))],
                         "reason": "Came back early"})
        self.assertEqual(some.status_code, 200, some.content)
        self.assertEqual((some.json()["status"], some.json()["days_counting"]),
                         ("partially_cancelled", [str(self.monday)]))
        whole = self.api("POST", f"/api/v1/leave/records/{leave['id']}/cancel", {})
        self.assertEqual(whole.json()["status"], "cancelled")
        with use_company(self.company):
            self.assertFalse(LeaveDay.objects.filter(status__in=["approved", "consumed"]).exists())

    def test_clashes_and_bad_input_are_refused(self):
        self.assertEqual(self.record().status_code, 201)
        clash = self.record()
        self.assertEqual(clash.status_code, 422)
        backwards = self.record(end_date=str(self.monday - datetime.timedelta(days=3)))
        self.assertIn("end_date", self.fields(backwards))
        unknown = self.record(employee_id=999999)
        self.assertIn("employee_id", self.fields(unknown))
        half = self.record(start_date=str(self.monday + datetime.timedelta(days=1)),
                           end_date=str(self.monday + datetime.timedelta(days=1)),
                           duration="half_day", half_day_part="morning")
        self.assertEqual(half.json()["days"], "0.50", half.content)

    def test_a_document_is_kept_and_opened(self):
        with use_company(self.company):
            LeaveType.objects.filter(pk=self.casual.pk).update(
                requires_attachment_by_default=True)
        missing = self.record()
        self.assertIn("document", self.fields(missing))
        recorded = self.record(document={"filename": "letter.pdf", "content_base64": PDF})
        self.assertEqual(recorded.status_code, 201, recorded.content)
        self.assertTrue(recorded.json()["has_document"])
        got = self.api("GET", f"/api/v1/leave/records/{recorded.json()['id']}/document")
        self.assertEqual((got.status_code, got["Content-Type"]), (200, "application/pdf"))
        self.assertEqual(got["Cache-Control"], "private, max-age=300")
        fake = self.record(start_date=str(self.monday + datetime.timedelta(days=1)),
                           end_date=str(self.monday + datetime.timedelta(days=1)),
                           document={"filename": "x.pdf",
                                     "content_base64": base64.b64encode(b"hello").decode()})
        self.assertIn("document", self.fields(fake))

    def test_a_branch_manager_records_only_in_their_branch(self):
        from organization.models import Branch

        manager_user = self.person("manny@example.test", role="manager")
        with use_company(self.company):
            other = Branch.objects.create(company=self.company, code="CTG", name="Chattogram")
            CompanyMembership.all_objects.get(user=manager_user).allowed_branches.set([other])
        manager = self.logged_in("manny@example.test")
        refused = self.api("POST", "/api/v1/leave/records", {
            "employee_id": self.rahim.pk, "leave_type_id": self.casual.pk,
            "start_date": str(self.monday), "end_date": str(self.monday)}, session=manager)
        # Rahim is not placed in their branch: not among those they may record for.
        self.assertIn("employee_id", self.fields(refused))

    def test_api_keys_need_the_scope(self):
        reader = self.key("leave:read")
        self.assertEqual(self.as_key(reader, "GET", "/api/v1/leave/records").status_code, 200)
        refused = self.as_key(reader, "POST", "/api/v1/leave/types/defaults")
        self.assertEqual(self.code_of(refused), "scope_missing")


class RequestTests(LeaveApiTestCase):
    def setUp(self):
        super().setUp()
        worker = self.person("worker@example.test", role="employee")
        with use_company(self.company):
            self.rahim.user = worker
            self.rahim.save(update_fields=["user"])
        self.asked = workflow.submit_request(actor=worker, company_id=self.company.pk, values={
            "leave_type": self.casual, "start_date": self.monday, "end_date": self.monday,
            "pay_type": "paid", "reason": "Doctor's appointment"})

    def test_approve(self):
        waiting = self.api("GET", "/api/v1/leave/requests").json()
        row = waiting["results"][0]
        self.assertEqual((row["id"], row["status"], row["allowance"]),
                         (self.asked.pk, "pending", {"left": "10.00", "given": "10.00"}))
        no_pay = self.api("POST", f"/api/v1/leave/requests/{self.asked.pk}/decide",
                          {"decision": "approve"})
        self.assertIn("pay_type", self.fields(no_pay))
        approved = self.api("POST", f"/api/v1/leave/requests/{self.asked.pk}/decide",
                            {"decision": "approve", "pay_type": "unpaid"})
        self.assertEqual(approved.status_code, 200, approved.content)
        self.assertEqual(approved.json()["status"], "approved")
        with use_company(self.company):
            self.assertEqual(LeaveDay.objects.get().approved_pay_type, "unpaid")
        again = self.api("POST", f"/api/v1/leave/requests/{self.asked.pk}/decide",
                         {"decision": "reject", "note": "x"})
        self.assertEqual(again.status_code, 422)
        self.assertIn("already been decided", again.json()["error"]["message"])

    def test_reject_needs_a_note(self):
        bare = self.api("POST", f"/api/v1/leave/requests/{self.asked.pk}/decide",
                        {"decision": "reject"})
        self.assertIn("note", self.fields(bare))
        rejected = self.api("POST", f"/api/v1/leave/requests/{self.asked.pk}/decide",
                            {"decision": "reject", "note": "Busy week"})
        self.assertEqual((rejected.json()["status"], rejected.json()["decision_note"]),
                         ("rejected", "Busy week"))

    def test_the_employee_cannot_decide(self):
        worker = self.logged_in("worker@example.test")
        refused = self.api("GET", "/api/v1/leave/requests", session=worker)
        self.assertEqual(self.code_of(refused), "permission_denied")
