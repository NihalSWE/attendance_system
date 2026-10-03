"""The ERP webhook (Nihal, 2026-10-01): a company's attendance pushed to its
own system - set up on Organisation → ERP webhook, sent signed and retried,
documented in a guide it can download.

Rahim (Employee ID E1) works 9:00-18:00 at Live Ltd; the receiver is a
stand-in for ``_call`` - nothing leaves the test.
"""

import datetime
import hashlib
import hmac
import json
from unittest import mock

from django.core.exceptions import ValidationError
from django.urls import reverse

from attendance.services import recalculate
from attendance.tests_live import DHAKA, LiveTestCase
from auditlog.models import AuditLog
from common.tenant import use_company
from webhooks import services
from webhooks.models import (WebhookDayState, WebhookDebugEntry, WebhookEvent,
                             WebhookSettings)

MONDAY = datetime.date(2026, 8, 10)
AFTER = datetime.datetime(2026, 8, 12, 12, tzinfo=DHAKA)       # Monday long finished
DURING = datetime.datetime(2026, 8, 10, 12, tzinfo=DHAKA)      # Monday still running
URL = "https://erp.example.com/api/webhook/attendance"


class Receiver:
    """Records what would have been sent, and answers as told."""

    def __init__(self, status=200, text='{"success": true}'):
        self.calls, self.status, self.text = [], status, text

    def __call__(self, url, *, method, body, headers):
        self.calls.append({"url": url, "method": method, "body": body, "headers": headers})
        return self.status, self.text

    @property
    def last(self):
        return json.loads(self.calls[-1]["body"])


class WebhookCase(LiveTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)

    def switch_on(self, **values):
        values = {"url": URL, "is_active": True, "secret_encrypted": services.encrypt("s3cret"),
                  "send_from": datetime.date(2026, 8, 1), **values}
        with use_company(self.company):
            row = WebhookSettings(company=self.company, **values)
            row.save()
        return row

    def work(self, *times, now=AFTER, day=MONDAY):
        for hour, minute in times:
            self.punch(day, hour, minute)
        recalculate(self.company.pk, start=day, end=day, now=now)

    def events(self):
        return list(WebhookEvent.all_objects.filter(company=self.company).order_by("pk"))


class SettingsPageTests(WebhookCase):
    url = reverse("webhooks:settings")

    def save(self, **values):
        data = {"action": "save", "url": URL, "ping_url": "",
                "secret": "s3cret", "signing_secret": "", "employee_key": "au_user_id",
                "mode": "arrive_leave", "batch": "on", "send_from": "", "is_active": "on",
                **values}
        return self.client.post(self.url, data)

    def test_the_form_asks_only_what_is_needed(self):
        # Nihal, 2026-10-01: the address, the secret key and on/off; the rest
        # behind Advanced.
        page = self.client.get(self.url)
        form = page.context["form"]
        self.assertEqual([field.name for field in form.main_fields()], ["url", "secret"])
        self.assertContains(page, "Advanced - only if your developer asks")
        self.assertContains(page, "Save and test connection")
        self.assertNotIn("auth", form.fields)

    def test_create_a_secret_key_shows_it_once_and_save_keeps_it(self):
        # Nihal, 2026-10-01: the ERP's developer puts our key in its .env.
        response = self.client.post(self.url, {
            "action": ["save", "generate"], "url": URL, "employee_key": "au_user_id",
            "mode": "arrive_leave", "batch": "on", "is_active": "on"})
        self.assertEqual(response.status_code, 200)
        key = response.context["new_secret"]
        self.assertRegex(key, r"^[0-9a-f]{64}$")
        self.assertContains(response, "Your new secret key")
        self.assertContains(response, f"ATTENDANCE_WEBHOOK_SECRET={key}")
        form = response.context["form"]
        self.assertEqual((form.initial["url"], form.initial["secret"]), (URL, key))
        self.assertFalse(WebhookSettings.all_objects.exists())      # nothing saved yet
        self.save(secret=key)
        row = WebhookSettings.all_objects.get(company=self.company)
        self.assertEqual(services.decrypt(row.secret_encrypted), key)
        # From now on it is in the Secret key box, as dots.
        self.assertContains(self.client.get(self.url),
                            f'type="password" name="secret" value="{key}"')

    def test_save_and_test_shows_the_result_once(self):
        with mock.patch.object(services, "_call", Receiver()):
            response = self.save(then="test")
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        page = self.client.get(self.url)
        self.assertContains(page, "Connected")
        self.assertContains(page, "The address and the secret key both work")
        self.assertContains(page, "alert--success")
        # Shown once: the next visit has no result box.
        self.assertNotContains(self.client.get(self.url), "data-webhook-test")

    def test_saving_keeps_the_secret_encrypted_and_hidden(self):
        response = self.save()
        self.assertEqual(response.status_code, 302)
        row = WebhookSettings.all_objects.get(company=self.company)
        self.assertTrue(row.is_active)
        self.assertNotIn("s3cret", row.secret_encrypted)
        self.assertEqual(services.decrypt(row.secret_encrypted), "s3cret")
        self.assertIsNotNone(row.send_from)            # from the day it was switched on
        page = self.client.get(self.url)
        self.assertContains(page, "Sending")
        # Nihal, 2026-10-01: the saved key stays in its box, as dots (a password
        # box), and the eye shows it.
        self.assertContains(page, 'type="password" name="secret" value="s3cret"')
        entry = AuditLog.objects.get(action="webhook_settings.saved")
        self.assertTrue(entry.after_data["secret_changed"])
        self.assertNotIn("s3cret", json.dumps(entry.after_data))
        # Saved again as it is (the box posts the same key): not a change.
        self.save()
        self.assertFalse(AuditLog.objects.filter(action="webhook_settings.saved")
                         .latest("pk").after_data["secret_changed"])
        # Saved with the box emptied: the old one stays.
        self.save(secret="")
        self.assertEqual(services.decrypt(
            WebhookSettings.all_objects.get(company=self.company).secret_encrypted), "s3cret")
        # A new one replaces it.
        self.save(secret="n3w-key")
        self.assertEqual(services.decrypt(
            WebhookSettings.all_objects.get(company=self.company).secret_encrypted), "n3w-key")

    def test_only_https_and_a_secret_to_switch_on(self):
        response = self.save(url="http://erp.example.com/hook")
        self.assertContains(response, "Use an https:// address")
        response = self.save(secret="")
        self.assertContains(response, "Enter the secret key before switching it on")
        self.assertFalse(WebhookSettings.all_objects.exists())

    def test_the_page_is_the_owner_and_admins_only(self):
        self.client.force_login(self.make_hr())
        self.assertNotEqual(self.client.get(self.url).status_code, 200)

    def make_hr(self):
        from accounts.models import CompanyMembership, User

        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(company=self.company, user=hr, role="hr",
                                             status="active")
        return hr

    def test_test_connection_calls_the_test_address_with_the_secret(self):
        self.switch_on()
        receiver = Receiver()
        with mock.patch.object(services, "_call", receiver):
            response = self.client.post(self.url, {"action": "test"}, follow=True)
        self.assertContains(response, "Connected")
        call = receiver.calls[0]
        self.assertEqual((call["method"], call["url"]), ("GET", URL + "/ping"))
        self.assertEqual(call["headers"]["X-Webhook-Secret"], "s3cret")
        self.assertEqual(call["headers"]["Authorization"], "Bearer s3cret")
        self.assertTrue(WebhookSettings.all_objects.get(company=self.company).last_test_ok)

    def test_a_failed_test_says_what_is_wrong_and_what_to_do(self):
        self.switch_on()
        for receiver, title, fix in (
            (Receiver(status=401, text="Invalid secret"), "Wrong secret key",
             "Copy it again from your developer"),
            (Receiver(status=404, text="Not Found"), "Nothing found at that address",
             "Check the address"),
            (Receiver(status=403), "This server is not allowed in", "allow this server"),
            (Receiver(status=500), "Your system had an error", "your developer can see it"),
        ):
            with self.subTest(title=title), mock.patch.object(services, "_call", receiver):
                page = self.client.post(self.url, {"action": "test"}, follow=True)
                self.assertContains(page, "alert--danger")
                self.assertContains(page, title)
                self.assertContains(page, "What to do:")
                self.assertContains(page, fix)
        self.assertFalse(WebhookSettings.all_objects.get(company=self.company).last_test_ok)

    def test_no_answer_is_explained_too(self):
        self.switch_on()
        for code, title in (("timeout", "No answer within 10 seconds"),
                            ("not_found", "The server name could not be found"),
                            ("certificate", "security certificate is not valid")):
            failing = mock.Mock(side_effect=services.WebhookError("x", code=code))
            with self.subTest(code=code), mock.patch.object(services, "_call", failing):
                result = services.test_connection(actor=self.admin, company_id=self.company.pk)
                self.assertFalse(result.ok)
                self.assertIn(title, result.title)
                self.assertTrue(result.fix)

    def test_testing_before_saving_says_what_is_missing(self):
        result = services.test_connection(actor=self.admin, company_id=self.company.pk)
        self.assertEqual(result.title, "Nothing saved yet")


class QueueTests(WebhookCase):
    def test_nothing_is_queued_without_an_active_webhook(self):
        self.work((9, 0), (18, 0))
        self.assertEqual(self.events(), [])

    def test_a_finished_day_is_one_event_with_both_times(self):
        self.switch_on()
        self.work((9, 0), (18, 0))
        [event] = self.events()
        self.assertEqual(event.kind, "check_out")
        payload = event.payload
        self.assertEqual((payload["au_user_id"], payload["work_date"]), ("E1", "2026-08-10"))
        self.assertEqual((payload["check_in"], payload["check_out"]),
                         ("2026-08-10 09:00:00", "2026-08-10 18:00:00"))
        self.assertEqual(payload["event_id"], str(event.event_id))
        # Worked out again: nothing new.
        recalculate(self.company.pk, start=MONDAY, end=MONDAY, now=AFTER)
        self.assertEqual(len(self.events()), 1)

    def test_check_in_as_they_arrive_then_check_out_when_the_day_is_finished(self):
        self.switch_on()
        self.work((9, 0), now=DURING)
        [arrived] = self.events()
        self.assertEqual(arrived.kind, "check_in")
        self.assertNotIn("check_out", arrived.payload)
        # A break while the day runs is not a check-out.
        self.work((13, 0), (13, 30), now=datetime.datetime(2026, 8, 10, 14, tzinfo=DHAKA))
        self.assertEqual(len(self.events()), 1)
        self.work((18, 0), now=AFTER)
        left = self.events()[-1]
        self.assertEqual((left.kind, left.payload["check_out"]), ("check_out", "2026-08-10 18:00:00"))
        self.assertEqual(len(self.events()), 2)

    def test_every_scan_mode_sends_the_latest_out_as_it_happens(self):
        self.switch_on(mode=WebhookSettings.Mode.EVERY_SCAN)
        self.work((9, 0), (13, 0), now=datetime.datetime(2026, 8, 10, 13, 5, tzinfo=DHAKA))
        last = self.events()[-1]
        self.assertEqual(last.payload["check_out"], "2026-08-10 13:00:00")

    def test_an_assumed_check_out_is_not_sent(self):
        # Nobody scanned out: the day closes at the shift end by rule, and only
        # the check-in goes - the ERP must not be told 18:00 as if it were real.
        self.switch_on()
        self.work((9, 0))
        [event] = self.events()
        self.assertEqual(event.kind, "check_in")
        self.assertNotIn("check_out", event.payload)

    def test_days_before_send_from_are_not_sent(self):
        self.switch_on(send_from=datetime.date(2026, 9, 1))
        self.work((9, 0), (18, 0))
        self.assertEqual(self.events(), [])

    def test_a_corrected_day_goes_again_as_an_update(self):
        self.switch_on()
        self.work((9, 30), (18, 0))
        self.work((9, 0))                                # an earlier scan arrives
        update = self.events()[-1]
        self.assertEqual((update.kind, update.payload["check_in"]),
                         ("update", "2026-08-10 09:00:00"))
        state = WebhookDayState.all_objects.get(employee=self.employee, work_date=MONDAY)
        self.assertEqual(state.check_in.astimezone(DHAKA).hour, 9)

    def test_a_custom_employee_field_name(self):
        self.switch_on(employee_key="emp_id")
        self.work((9, 0), (18, 0))
        payload = self.events()[0].payload
        self.assertEqual(payload["emp_id"], "E1")
        self.assertNotIn("au_user_id", payload)


class SendingTests(WebhookCase):
    def queue(self, **values):
        row = self.switch_on(**values)
        self.work((9, 0), (18, 0))
        return row

    def send(self, receiver):
        with mock.patch.object(services, "_call", receiver):
            return services.deliver_due(self.company.pk)

    def test_received_events_are_marked_and_the_body_is_a_batch(self):
        self.queue()
        receiver = Receiver()
        self.assertEqual(self.send(receiver), 1)
        call = receiver.calls[0]
        self.assertEqual((call["method"], call["url"]), ("POST", URL))
        self.assertEqual(call["headers"]["X-Webhook-Secret"], "s3cret")
        self.assertEqual(receiver.last["events"][0]["au_user_id"], "E1")
        event = self.events()[0]
        self.assertEqual((event.status, event.attempts), ("sent", 1))
        # Sent once: nothing goes twice.
        self.assertEqual(self.send(receiver), 0)
        self.assertEqual(len(receiver.calls), 1)

    def test_one_event_per_request_and_the_secret_both_ways(self):
        self.queue(batch=False)
        receiver = Receiver()
        self.send(receiver)
        self.assertEqual(receiver.last["au_user_id"], "E1")       # not wrapped in events
        headers = receiver.calls[0]["headers"]
        self.assertEqual((headers["X-Webhook-Secret"], headers["Authorization"]),
                         ("s3cret", "Bearer s3cret"))

    def test_a_signed_request_carries_the_body_hmac(self):
        self.queue(signing_secret_encrypted=services.encrypt("sign-me"))
        receiver = Receiver()
        self.send(receiver)
        call = receiver.calls[0]
        expected = hmac.new(b"sign-me", call["body"], hashlib.sha256).hexdigest()
        self.assertEqual(call["headers"]["X-Webhook-Signature"], f"sha256={expected}")

    def test_not_received_is_tried_again_later_then_given_up(self):
        self.queue()
        self.assertEqual(self.send(Receiver(status=500, text="boom")), 0)
        event = self.events()[0]
        self.assertEqual((event.status, event.last_status_code), ("failed", 500))
        self.assertGreater(event.next_attempt_at, event.last_attempt_at)
        # Not due yet: nothing is sent.
        receiver = Receiver()
        self.send(receiver)
        self.assertEqual(receiver.calls, [])
        WebhookEvent.all_objects.update(attempts=len(services.BACKOFF_MINUTES),
                                        next_attempt_at=event.last_attempt_at)
        self.send(Receiver(status=503))
        self.assertEqual(self.events()[0].status, "gave_up")
        self.assertEqual(services.send_again(actor=self.admin, company_id=self.company.pk), 1)
        self.assertEqual(self.send(Receiver()), 1)

    def test_the_receivers_per_event_results_are_followed(self):
        self.queue()
        answer = json.dumps({"success": False, "results": [
            {"au_user_id": "E1", "result": "skipped", "message": "Unknown au_user_id."}]})
        self.send(Receiver(status=207, text=answer))
        event = self.events()[0]
        self.assertEqual((event.status, event.last_message), ("skipped", "Unknown au_user_id."))

    def test_an_address_on_a_private_network_is_refused(self):
        with mock.patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.5", 443))]):
            with self.assertRaisesMessage(ValidationError, "private or local network"):
                services.check_address("https://erp.internal/hook")
        with self.assertRaisesMessage(ValidationError, "https://"):
            services.check_address("http://erp.example.com/hook")

    def test_nothing_goes_while_switched_off(self):
        row = self.queue()
        WebhookSettings.all_objects.filter(pk=row.pk).update(is_active=False)
        receiver = Receiver()
        self.assertEqual(self.send(receiver), 0)
        self.assertEqual(receiver.calls, [])


class SendATestTests(WebhookCase):
    """Send a test (2026-10-01): a check-in and check-out typed in by hand for
    one employee, sent now in the real format - before a device is connected."""

    url = reverse("webhooks:settings")

    def post(self, receiver, **values):
        data = {"action": "send_test_event", "test-employee_code": "E1",
                "test-work_date": "2026-10-01", "test-check_in": "09:02",
                "test-check_out": "18:20", **values}
        with mock.patch.object(services, "_call", receiver):
            return self.client.post(self.url, data, follow=True)

    def test_it_goes_in_the_real_format_and_says_what_the_receiver_did(self):
        self.switch_on()
        answer = json.dumps({"success": True, "results": [
            {"au_user_id": "E1", "result": "created", "message": None}]})
        receiver = Receiver(text=answer)
        page = self.post(receiver)
        self.assertContains(page, "Received")
        self.assertContains(page, "a new attendance row was created")
        sent = receiver.last["events"][0]
        self.assertEqual((sent["au_user_id"], sent["check_in"], sent["check_out"]),
                         ("E1", "2026-10-01 09:02:00", "2026-10-01 18:20:00"))
        self.assertTrue(sent["test"])
        self.assertEqual(receiver.calls[0]["headers"]["X-Webhook-Secret"], "s3cret")
        event = self.events()[-1]
        self.assertEqual((event.kind, event.status), ("test", "sent"))
        # A test is never sent again by the queue.
        self.assertEqual(services.deliver_due(self.company.pk), 0)

    def test_a_skipped_test_says_why(self):
        self.switch_on()
        answer = json.dumps({"success": False, "results": [
            {"au_user_id": "E1", "result": "skipped", "message": "Unknown au_user_id."}]})
        page = self.post(Receiver(status=207, text=answer))
        self.assertContains(page, "Your system received it but skipped it")
        self.assertContains(page, "Unknown au_user_id.")

    def test_the_receivers_refusal_is_explained(self):
        self.switch_on()
        page = self.post(Receiver(status=503, text='{"message":"Webhook secret is not configured"}'))
        self.assertContains(page, "Your system is not ready")
        self.assertContains(page, "Webhook secret is not configured")

    def test_an_unknown_employee_id_is_refused_here(self):
        self.switch_on()
        receiver = Receiver()
        page = self.post(receiver, **{"test-employee_code": "999"})
        self.assertContains(page, "No employee has Employee ID 999")
        self.assertEqual(receiver.calls, [])

    def test_check_in_only(self):
        self.switch_on()
        receiver = Receiver()
        self.post(receiver, **{"test-check_out": ""})
        sent = receiver.last["events"][0]
        self.assertEqual(sent["event"], "check_in")
        self.assertNotIn("check_out", sent)


class DebugMessagesTests(WebhookCase):
    """Debug messages for 15 minutes (Nihal, 2026-10-03): everything the
    webhook does, success or not, shown on the page as words and as JSON."""

    url = reverse("webhooks:settings")

    def state(self):
        response = self.client.get(reverse("webhooks:debug"))
        return response, response.json()

    def test_everything_is_shown_with_the_request_and_answer_but_never_the_key(self):
        self.switch_on()
        page = self.client.post(self.url, {"action": "debug_on"}, follow=True)
        self.assertContains(page, "Stop and clear")
        self.work((9, 0), (18, 0))                                       # queued
        answer = json.dumps({"success": True, "results": [
            {"au_user_id": "E1", "result": "created", "message": None}]})
        with mock.patch.object(services, "_call", Receiver(text=answer)):
            services.deliver_due(self.company.pk)                        # sent, received
        failing = mock.Mock(side_effect=services.WebhookError("No answer.", code="timeout"))
        with mock.patch.object(services, "_call", failing):
            services.test_connection(actor=self.admin, company_id=self.company.pk)  # failed
        response, state = self.state()
        self.assertTrue(state["active"])
        self.assertGreater(state["seconds_left"], 14 * 60)
        whats = [(e["what"], e["ok"]) for e in state["entries"]]
        self.assertEqual(whats[:3], [("Test connection", False), ("Sent to your system", True),
                                     ("Queued", None)])
        sent = state["entries"][1]["detail"]
        self.assertEqual(sent["request"]["body"]["events"][0]["au_user_id"], "E1")
        self.assertEqual(sent["response"]["status"], 200)
        self.assertEqual(sent["response"]["body"]["results"][0]["result"], "created")
        self.assertEqual(sent["events"][0]["result"], "created")
        self.assertEqual(sent["request"]["headers"]["X-Webhook-Secret"], services.HIDDEN)
        self.assertEqual(state["entries"][0]["detail"]["error"]["code"], "timeout")
        self.assertNotIn("s3cret", response.content.decode())

    def test_after_15_minutes_they_are_gone(self):
        self.switch_on()
        services.start_debug(actor=self.admin, company_id=self.company.pk)
        self.assertTrue(WebhookDebugEntry.all_objects.filter(company=self.company).exists())
        WebhookSettings.all_objects.filter(company=self.company).update(
            debug_until=datetime.datetime(2026, 1, 1, tzinfo=DHAKA))
        _, state = self.state()
        self.assertEqual((state["active"], state["entries"]), (False, []))
        self.assertFalse(WebhookDebugEntry.all_objects.filter(company=self.company).exists())
        page = self.client.get(self.url)
        self.assertContains(page, "Show debug messages (15 min)")

    def test_stop_clears_them_and_nothing_is_kept_while_off(self):
        self.switch_on()
        self.client.post(self.url, {"action": "debug_on"})
        self.client.post(self.url, {"action": "debug_off"})
        self.assertFalse(WebhookDebugEntry.all_objects.filter(company=self.company).exists())
        self.work((9, 0), (18, 0))
        self.assertEqual(len(self.events()), 1)                          # still queued
        self.assertFalse(WebhookDebugEntry.all_objects.filter(company=self.company).exists())

    def test_switched_off_says_why_nothing_is_queued(self):
        self.switch_on(is_active=False)
        services.start_debug(actor=self.admin, company_id=self.company.pk)
        self.work((9, 0), (18, 0))
        _, state = self.state()
        self.assertIn("switched off, so nothing was queued", state["entries"][0]["message"])


class GuideTests(WebhookCase):
    def test_the_guide_on_screen_and_downloaded(self):
        self.switch_on(employee_key="au_user_id")
        page = self.client.get(reverse("webhooks:guide"))
        self.assertContains(page, "X-Webhook-Secret")
        self.assertContains(page, URL)
        self.assertNotContains(page, "s3cret")
        # Plain words for the company first, then the developer's part.
        self.assertContains(page, "Part 1 - For the company")
        self.assertContains(page, "Part 2 - For the developer")
        self.assertContains(page, "Save and test connection")
        self.assertContains(page, "Wrong secret key")
        markdown = self.client.get(reverse("webhooks:guide"), {"format": "md"})
        self.assertIn("attachment", markdown["Content-Disposition"])
        text = markdown.content.decode()
        self.assertIn("# Attendance webhook", text)
        self.assertIn('"au_user_id": "445909"', text)
        self.assertNotIn("s3cret", text)
        pdf = self.client.get(reverse("webhooks:guide"), {"format": "pdf"})
        self.assertEqual(pdf["Content-Type"], "application/pdf")
        self.assertTrue(pdf.content.startswith(b"%PDF"))

    def test_the_guide_works_before_anything_is_saved(self):
        page = self.client.get(reverse("webhooks:guide"))
        self.assertContains(page, "your-erp.example.com")

    def test_the_menu_offers_it(self):
        page = self.client.get(reverse("webhooks:settings"))
        self.assertContains(page, "Download guide (PDF)")
        self.assertContains(page, reverse("webhooks:settings"))
