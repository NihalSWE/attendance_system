"""Phase 12: the ERP webhook and the audit log (docs/api/120-integrations.md).
Nothing leaves the test: the webhook's calls go to a fake receiver."""

from unittest import mock

from api.tests.test_attendance import AttendanceApiTestCase
from webhooks import services
from webhooks.models import WebhookSettings
from webhooks.tests import Receiver

URL = "https://erp.example.com/api/webhook/attendance"


class WebhookTests(AttendanceApiTestCase):
    def test_set_up_test_and_never_show_the_secret(self):
        empty = self.api("GET", "/api/v1/webhook").json()
        self.assertEqual((empty["configured"], empty["has_secret"]), (False, False))
        no_key = self.api("PATCH", "/api/v1/webhook", {"url": URL, "is_active": True})
        self.assertIn("secret", self.fields(no_key))
        plain = self.api("PATCH", "/api/v1/webhook", {"url": "http://erp.example.com/x"})
        self.assertIn("url", self.fields(plain))
        saved = self.api("PATCH", "/api/v1/webhook",
                         {"url": URL, "secret": "s3cret", "is_active": True})
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertEqual((saved.json()["is_active"], saved.json()["has_secret"]), (True, True))
        self.assertNotIn("s3cret", saved.content.decode())
        row = WebhookSettings.all_objects.get(company=self.company)
        self.assertEqual(services.decrypt(row.secret_encrypted), "s3cret")
        new = self.api("POST", "/api/v1/webhook/secret").json()["secret"]
        row.refresh_from_db()
        self.assertEqual(services.decrypt(row.secret_encrypted), new)
        self.assertNotIn(new, self.api("GET", "/api/v1/webhook").content.decode())
        receiver = Receiver()
        with mock.patch.object(services, "_call", receiver):
            tested = self.api("POST", "/api/v1/webhook/test").json()
        self.assertTrue(tested["ok"], tested)
        self.assertEqual(receiver.calls[0]["url"], URL + "/ping")

    def test_a_test_event_and_the_events(self):
        self.api("PATCH", "/api/v1/webhook", {"url": URL, "secret": "s3cret", "is_active": True})
        receiver = Receiver()
        with mock.patch.object(services, "_call", receiver):
            sent = self.api("POST", "/api/v1/webhook/test-event", {
                "employee_code": "E1", "work_date": str(self.late_day), "check_in": "09:02",
                "check_out": "18:05"})
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertTrue(sent.json()["ok"], sent.json())
        backwards = self.api("POST", "/api/v1/webhook/test-event", {
            "employee_code": "E1", "work_date": str(self.late_day), "check_in": "18:00",
            "check_out": "09:00"})
        self.assertIn("check_out", self.fields(backwards))
        events = self.api("GET", "/api/v1/webhook/events").json()
        self.assertGreaterEqual(events["count"], 1)
        self.assertEqual(events["results"][0]["employee"]["name"], "Rahim")
        again = self.api("POST", "/api/v1/webhook/send-again").json()
        self.assertEqual(again["count"], 0)

    def test_send_now_needs_it_on(self):
        refused = self.api("POST", "/api/v1/webhook/send-now")
        self.assertEqual(refused.status_code, 422)

    def test_debug_and_the_guide(self):
        none_yet = self.api("POST", "/api/v1/webhook/debug", {"on": True})
        self.assertEqual(none_yet.status_code, 422)
        self.api("PATCH", "/api/v1/webhook", {"url": URL, "secret": "s3cret"})
        on = self.api("POST", "/api/v1/webhook/debug", {"on": True}).json()
        self.assertTrue(on["active"])
        self.assertTrue(on["entries"])
        off = self.api("POST", "/api/v1/webhook/debug", {"on": False}).json()
        self.assertFalse(off["active"])
        guide = self.api("GET", "/api/v1/webhook/guide")
        self.assertEqual(guide.status_code, 200)
        self.assertIn("text/markdown", guide["Content-Type"])
        self.assertNotIn("s3cret", guide.content.decode())
        pdf = self.api("GET", "/api/v1/webhook/guide", query="file_type=pdf")
        self.assertEqual(pdf["Content-Type"], "application/pdf")

    def test_keys_read_but_never_change_where_it_goes(self):
        key = self.key("webhook:manage")
        self.assertEqual(self.as_key(key, "GET", "/api/v1/webhook").status_code, 200)
        moved = self.as_key(key, "PATCH", "/api/v1/webhook", {"url": URL, "secret": "x"})
        self.assertEqual(self.code_of(moved), "permission_denied")
        self.assertEqual(self.code_of(self.as_key(key, "POST", "/api/v1/webhook/secret")),
                         "permission_denied")
        other = self.key("attendance:read")
        self.assertEqual(self.code_of(self.as_key(other, "GET", "/api/v1/webhook")),
                         "scope_missing")

    def test_hr_cannot_open_it(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/webhook", session=hr)),
                         "permission_denied")


class AuditLogTests(AttendanceApiTestCase):
    def test_what_happened_is_listed(self):
        self.api("POST", "/api/v1/branches", {"code": "CTG", "name": "Chattogram"})
        listed = self.api("GET", "/api/v1/audit-log", query="action=branch.").json()
        self.assertGreaterEqual(listed["count"], 1)
        entry = listed["results"][0]
        self.assertEqual((entry["actor"], entry["action"][:7]), ("admin@example.test",
                                                                 "branch."))
        mine = self.api("GET", "/api/v1/audit-log", query="actor=nobody@example.test").json()
        self.assertEqual(mine["count"], 0)
        bad = self.api("GET", "/api/v1/audit-log", query="from=yesterday")
        self.assertIn("from", self.fields(bad))

    def test_only_the_owner_or_administrator_and_never_a_key(self):
        self.person("hr@example.test", role="hr")
        hr = self.logged_in("hr@example.test")
        self.assertEqual(self.code_of(self.api("GET", "/api/v1/audit-log", session=hr)),
                         "permission_denied")
        key = self.key("company:read")
        self.assertEqual(self.code_of(self.as_key(key, "GET", "/api/v1/audit-log")),
                         "permission_denied")

    def test_another_companys_log_is_not_shown(self):
        from auditlog.models import AuditLog

        listed = self.api("GET", "/api/v1/audit-log").json()
        ours = set(AuditLog.objects.filter(company=self.company).values_list("pk", flat=True))
        self.assertTrue({entry["id"] for entry in listed["results"]} <= ours)
