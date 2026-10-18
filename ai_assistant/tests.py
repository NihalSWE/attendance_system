"""Phase 8: the AI sales order assistant, with a fake Claude and a fake inbox.

Done when: an email order in Bangla or English becomes a correct draft sales
order with stock status; a person confirms it; the managers receive the order
summary; the daily and weekly reports go out (``DoneWhenTests``).
"""

import datetime
import json
from decimal import Decimal
from email.message import EmailMessage
from io import StringIO
from types import SimpleNamespace

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from access_control.branch_access import grant_access
from ai_assistant import claude, inbox, intake, setup
from ai_assistant.models import AiCall, AiRecipient, AiReportRun, AiSettings, InboundMessage, ItemAlias
from common.tenant import use_company
from employees.models import Employee
from inventory import posting
from inventory.models import Batch, Item, MoveType, StockBalance
from inventory.testing import D
from sales.models import SalesOrder
from sales.tests_flow import SalesFixture


def extraction(lines, **extra):
    data = {"is_order": True, "language": "bn", "customer_name": "Sylhet Distribution", "customer_email": "",
            "customer_phone": "", "matched_customer_code": "", "customer_reference": "", "requested_delivery_date": "",
            "delivery_address": "", "notes": "", "confidence": "high", "lines": [
                {"product_text": text, "quantity": qty, "unit_text": unit, "suggested_item_code": code, "note": ""}
                for text, qty, unit, code in lines]}
    data.update(extra)
    return data


class FakeClient:
    """Answers each ``beta.messages.create`` with the next queued answer, and keeps the requests."""

    def __init__(self, answers, requests):
        self.answers, self.requests = answers, requests
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.requests.append(request)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        stop = "end_turn"
        if answer == "refusal":
            stop, answer = "refusal", {}
        usage = SimpleNamespace(input_tokens=1200, output_tokens=300, cache_read_input_tokens=8000,
                                cache_creation_input_tokens=0)
        return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""),
                                        SimpleNamespace(type="text", text=json.dumps(answer))],
                               usage=usage, model=request["model"], stop_reason=stop, _request_id="req_test")


class FakeImap:
    def __init__(self, mails):
        self.mails, self.seen = mails, []

    def login(self, user, password):
        self.login_as = (user, password)

    def select(self, folder):
        return "OK", [b"1"]

    def search(self, charset, criterion):
        return "OK", [" ".join(str(n + 1) for n in range(len(self.mails))).encode()]

    def fetch(self, number, what):
        return "OK", [(b"1 (RFC822)", self.mails[int(number) - 1]), b")"]

    def store(self, number, flags, flag):
        self.seen.append(number)

    def logout(self):
        pass


def email_bytes(sender, subject, body, message_id):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Message-ID"] = sender, "orders@company.test", subject, message_id
    msg["Date"] = "Fri, 16 Oct 2026 09:30:00 +0600"
    msg.set_content(body)
    return msg.as_bytes()


MAIL = override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="erp@company.test",
                         EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")


class AiFixture(SalesFixture):
    """Juice in FGW (Phase 6 fixture) and 500 kg sugar in RMS; the assistant on, five managers."""

    def setUp(self):
        super().setUp()
        Item.all_objects.filter(pk=self.juice.pk).update(default_warehouse=self.fgw, name_bn="ম্যাঙ্গো জুস")
        Item.all_objects.filter(pk=self.sugar.pk).update(default_warehouse=self.rms, sale_price=D("95"),
                                                        vat_rate=D("5"))
        self.juice, self.sugar = Item.all_objects.get(pk=self.juice.pk), Item.all_objects.get(pk=self.sugar.pk)
        s1 = Batch.all_objects.create(company=self.company, item=self.sugar, batch_no="S1", source="opening",
                                      expiry_date=datetime.date(2027, 9, 30))
        posting.post(company_id=self.company.pk, actor=self.owner, document_type="opening", document_id=2,
                     move_type=MoveType.OPENING, work_date=datetime.date(2026, 9, 1), lines=[
                         posting.Line(item=self.sugar, warehouse=self.rms, batch=s1, qty=D("500"), unit_cost=D("80"))])
        self.s1 = s1
        self.customer.email = "orders@sylhet.test"
        self.customer.save()
        self.drafter = self.member("ai-drafts@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=self.drafter, first_name="AI")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="sales.order", branch_ids=[self.branch.pk])
        self.own = setup.save_settings(actor=self.owner, company_id=self.company.pk, api_key="sk-ant-test-key", values={
            "consent_received": True, "consent_date": datetime.date(2026, 10, 10), "consent_note": "Signed letter",
            "enabled": True, "model": "claude-opus-5-5", "effort": "medium", "daily_cost_cap": D("5"),
            "draft_user": self.drafter, "inbox_on": False, "imap_host": "", "imap_port": 993, "imap_username": "",
            "imap_folder": "INBOX"})
        for n in range(5):
            setup.add_recipient(actor=self.owner, company_id=self.company.pk, values={
                "name": f"Manager {n + 1}", "designation": "", "email": f"manager{n + 1}@inv.test",
                "gets_orders": True, "gets_daily": True, "gets_weekly": n < 2})
        self.answers, self.requests = [], []
        claude.CLIENT_FACTORY = lambda key: FakeClient(self.answers, self.requests)
        self.addCleanup(setattr, claude, "CLIENT_FACTORY", None)

    def paste(self, text, *answers, sender=""):
        self.answers.extend(answers)
        return intake.paste(actor=self.owner, company_id=self.company.pk, body=text, sender=sender)


@MAIL
class DoneWhenTests(AiFixture, TestCase):
    def test_a_bangla_email_becomes_a_draft_a_person_confirms_and_the_managers_hear(self):
        body = "আসসালামু আলাইকুম। ১০০ পিস ম্যাঙ্গো জুস আর ২০০ কেজি চিনি পাঠাবেন। ধন্যবাদ, সিলেট ডিস্ট্রিবিউশন"
        fake = FakeImap([email_bytes("Sylhet Distribution <orders@sylhet.test>", "অর্ডার", body, "<o1@sylhet.test>")])
        inbox.IMAP_FACTORY = lambda host, port: fake
        self.addCleanup(setattr, inbox, "IMAP_FACTORY", None)
        setup.save_settings(actor=self.owner, company_id=self.company.pk, imap_password="inbox-pass", values={
            "consent_received": True, "consent_date": datetime.date(2026, 10, 10), "enabled": True,
            "draft_user": self.drafter, "inbox_on": True, "imap_host": "imap.company.test", "imap_port": 993,
            "imap_username": "orders@company.test", "imap_folder": "INBOX", "model": "claude-opus-5-5",
            "effort": "medium", "daily_cost_cap": D("5"), "consent_note": ""})
        self.answers.append(extraction([("ম্যাঙ্গো জুস", 100, "পিস", "FG-0001"), ("চিনি", 200, "কেজি", "RM-0001")]))
        out = StringIO()
        call_command("ai_inbox", stdout=out)
        self.assertIn("1 new email(s), 1 read, 1 to review", out.getvalue())
        self.assertEqual((fake.login_as, fake.seen), (("orders@company.test", "inbox-pass"), [b"1"]))

        message = InboundMessage.all_objects.get()
        self.assertEqual((message.status, message.customer, message.sender),
                         ("needs_review", self.customer, "Sylhet Distribution <orders@sylhet.test>"))
        order = message.sales_order
        with use_company(self.company):
            lines = sorted((l.item.code, l.warehouse.code, l.qty, l.uom.code, l.unit_price)
                           for l in order.lines.select_related("item", "warehouse", "uom"))
        # Matched to the right items, units and stores; priced by our price list, not the AI.
        self.assertEqual(lines, [("FG-0001", "FGW", D("100"), "PCS", D("22")),
                                 ("RM-0001", "RMS", D("200"), "KG", D("95"))])
        self.assertEqual((order.status, order.source, order.created_by), ("draft", "ai", self.drafter))
        # Sugar was taken on the AI's word only (no Bangla name): flagged for the reviewer.
        self.assertEqual(message.problems, ['"চিনি" was taken as RM-0001 Sugar: check it.'])

        # The request: the catalogue cached, a strict JSON answer, refusal fallback on.
        [request] = self.requests
        self.assertEqual((request["model"], request["fallbacks"], request["betas"]),
                         ("claude-opus-5-5", "default", ["server-side-fallback-2026-07-01"]))
        self.assertEqual(request["system"][0]["cache_control"], {"type": "ephemeral"})
        self.assertIn("FG-0001 | Mango juice 250 ml | ম্যাঙ্গো জুস", request["system"][0]["text"])
        self.assertEqual(request["output_config"]["format"]["type"], "json_schema")
        self.assertIn(body, request["messages"][0]["content"])
        call = AiCall.all_objects.get()
        self.assertEqual((call.input_tokens, call.cache_read_tokens, call.cost), (1200, 8000, D("0.012400")))

        # A person (not the drafting login) confirms: stock reserved, five managers get the summary.
        confirmed = intake.confirm(actor=self.owner, company_id=self.company.pk, message_id=message.pk)
        self.assertEqual(SalesOrder.all_objects.get(pk=order.pk).status, "confirmed")
        self.assertEqual(StockBalance.all_objects.get(batch=self.s1).qty_reserved, D("200"))
        self.assertEqual(InboundMessage.all_objects.get().status, "converted")
        [summary] = mail.outbox
        self.assertEqual(sorted(summary.to), [f"manager{n}@inv.test" for n in range(1, 6)])
        self.assertEqual(summary.subject, f"New order {confirmed.number}: Sylhet Distribution, 22,480.00")
        self.assertIn("RM-0001 Sugar: 200 KG from RMS at 95.00 = 19,000.00 (reserved; 300 KG still free there)",
                      summary.body)

        # The same email read again is not saved twice.
        fake.mails = [email_bytes("orders@sylhet.test", "অর্ডার", body, "<o1@sylhet.test>")]
        call_command("ai_inbox", stdout=StringIO())
        self.assertEqual(InboundMessage.all_objects.count(), 1)

    def test_daily_and_weekly_reports(self):
        self.answers.append({"summary": "A quiet day: no orders came in.", "attention": ["Juice stock is fine."]})
        out = StringIO()
        call_command("ai_reports", "daily", date="2026-10-16", stdout=out)
        [report] = mail.outbox
        self.assertEqual(len(report.to), 5)
        self.assertTrue(report.body.startswith("Daily sales and stock report, Inventory Foods Ltd, 2026-10-16"))
        self.assertIn("A quiet day: no orders came in.", report.body)
        self.assertEqual(report.attachments[0][0], "daily-report-2026-10-16.xlsx")
        self.assertEqual(json.loads(self.requests[0]["messages"][0]["content"])["orders_received"], 0)
        # The weekly one goes even when the AI fails - without its paragraph.
        self.answers.append("refusal")
        call_command("ai_reports", "weekly", date="2026-10-17", stdout=out)
        weekly = mail.outbox[1]
        self.assertEqual(sorted(weekly.to), ["manager1@inv.test", "manager2@inv.test"])
        self.assertIn("2026-10-11 to 2026-10-17", weekly.body)
        self.assertEqual(list(AiReportRun.all_objects.values_list("kind", "status")),
                         [("weekly", "sent"), ("daily", "sent")])


@MAIL
class ReviewTests(AiFixture, TestCase):
    def test_an_unknown_line_is_matched_and_remembered(self):
        message = self.paste("need 50 ctn aam juice and 100 pcs mango juice", extraction(
            [("aam juice", 50, "pcs", ""), ("mango juice", 100, "pcs", "FG-0001")]), sender="orders@sylhet.test")
        self.assertEqual([l["product_text"] for l in message.unmatched], ["aam juice"])
        with self.assertRaisesMessage(ValidationError, "not matched yet"):
            intake.confirm(actor=self.owner, company_id=self.company.pk, message_id=message.pk)
        intake.match_line(actor=self.owner, company_id=self.company.pk, message_id=message.pk, index=0,
                          item=self.juice, qty=D("50"), uom=self.pcs)
        self.assertEqual(ItemAlias.all_objects.get().normalized, "aam juice")
        # Next time the words match by themselves, whatever the AI suggests.
        again = self.paste("aam juice 10", extraction([("Aam  Juice", 10, "pcs", "")]), sender="orders@sylhet.test")
        with use_company(self.company):
            self.assertEqual((again.unmatched, again.sales_order.lines.get().item), ([], self.juice))

    def test_an_unknown_customer_is_chosen_then_the_draft_is_made(self):
        message = self.paste("Send 10 bottles mango juice. Karim, Moulvibazar",
                             extraction([("mango juice", 10, "bottles", "FG-0001")], customer_name="Karim"))
        self.assertIsNone(message.sales_order)
        self.assertIn("The customer was not found: choose them.", message.problems)
        intake.choose_customer(actor=self.owner, company_id=self.company.pk, message_id=message.pk,
                               customer=self.customer)
        message.refresh_from_db()
        self.assertEqual(message.sales_order.customer, self.customer)

    def test_repeats_short_stock_and_non_orders_are_flagged(self):
        first = self.paste("600 pcs mango juice", extraction([("mango juice", 600, "pcs", "FG-0001")]),
                           sender="orders@sylhet.test")
        self.assertIn("FG-0001: only 300 PCS free in FGW, 600 ordered.", first.problems)
        second = self.paste("600 pcs mango juice", extraction([("mango juice", 600, "pcs", "FG-0001")]),
                            sender="orders@sylhet.test")
        self.assertIn(f"It looks like a repeat of {first.sales_order.number} (same customer, day and lines).",
                      second.problems)
        question = self.paste("What is your price for juice?", extraction([], is_order=False),
                              sender="orders@sylhet.test")
        self.assertEqual((question.sales_order, question.problems[0]), (None, "This does not look like an order."))

    def test_ignoring_cancels_the_draft(self):
        message = self.paste("10 pcs mango juice", extraction([("mango juice", 10, "pcs", "FG-0001")]),
                             sender="orders@sylhet.test")
        intake.ignore(actor=self.owner, company_id=self.company.pk, message_id=message.pk, reason="Sent twice")
        self.assertEqual(SalesOrder.all_objects.get(pk=message.sales_order_id).status, "cancelled")


@MAIL
class SafeguardTests(AiFixture, TestCase):
    def test_without_consent_the_assistant_cannot_be_switched_on(self):
        with self.assertRaisesMessage(ValidationError, "Record the client's written consent first."):
            setup.save_settings(actor=self.owner, company_id=self.company.pk, values={
                "consent_received": False, "enabled": True, "draft_user": self.drafter})
        AiSettings.all_objects.filter(company=self.company).update(enabled=False)
        message = self.paste("10 pcs mango juice")
        self.assertEqual((message.status, self.requests), ("waiting", []))
        self.assertIn("off", message.note)

    def test_the_daily_limit_and_a_refusal_leave_the_order_to_a_person(self):
        AiCall.all_objects.create(company=self.company, kind="extract", model="claude-opus-5-5", cost=D("5"))
        waiting = self.paste("10 pcs mango juice")
        self.assertEqual(waiting.status, "waiting")
        self.assertIn("spending limit", waiting.note)
        AiCall.all_objects.filter(cost=D("5")).delete()
        refused = self.paste("10 pcs mango juice", "refusal")
        self.assertEqual((refused.status, refused.note), ("failed", "The AI declined to read this message."))
        self.assertEqual(AiCall.all_objects.get().error, "The AI declined to read this message.")
        self.answers.append(extraction([("mango juice", 10, "pcs", "FG-0001")]))
        intake.retry(actor=self.owner, company_id=self.company.pk, message_id=refused.pk)
        self.assertEqual(InboundMessage.all_objects.get(pk=refused.pk).status, "needs_review")

    def test_the_key_is_kept_encrypted_and_never_shown(self):
        own = AiSettings.all_objects.get(company=self.company)
        self.assertNotIn("sk-ant-test-key", own.api_key_encrypted)
        self.assertEqual(claude.decrypt(own.api_key_encrypted), "sk-ant-test-key")
        self.client.force_login(self.owner)
        page = self.client.get(reverse("ai_assistant:settings"))
        self.assertContains(page, "Key: saved")
        self.assertNotContains(page, "sk-ant-test-key")

    def test_the_drafting_login_cannot_confirm_its_own_draft(self):
        grant_access(actor=self.owner, company_id=self.company.pk,
                     employee_id=Employee.all_objects.get(user=self.drafter).pk, code="sales.approve",
                     branch_ids=[self.branch.pk])
        message = self.paste("10 pcs mango juice", extraction([("mango juice", 10, "pcs", "FG-0001")]),
                             sender="orders@sylhet.test")
        with self.assertRaises((PermissionDenied, ValidationError)):
            intake.confirm(actor=self.drafter, company_id=self.company.pk, message_id=message.pk)


@MAIL
class PageTests(AiFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_paste_review_and_confirm_through_the_pages(self):
        self.assertContains(self.client.get(reverse("ai_assistant:inbox")), "AI order inbox")
        self.answers.append(extraction([("mango juice", 12, "pcs", "FG-0001")]))
        page = self.client.post(reverse("ai_assistant:inbox"), {"sender": "orders@sylhet.test",
                                                                "body": "12 pcs mango juice please"}, follow=True)
        message = InboundMessage.all_objects.get()
        self.assertContains(page, "12 pcs mango juice please")
        self.assertContains(page, "Confirm order")
        page = self.client.post(reverse("ai_assistant:message_action", args=[message.pk, "confirm"]), follow=True)
        self.assertContains(page, "confirmed: stock reserved. Summary sent to 5 manager(s).")
        self.assertEqual(InboundMessage.all_objects.get().status, "converted")

    def test_settings_are_the_owners_and_the_inbox_needs_a_sales_role(self):
        self.client.force_login(self.drafter)
        self.assertEqual(self.client.get(reverse("ai_assistant:inbox")).status_code, 200)
        self.assertNotEqual(self.client.get(reverse("ai_assistant:settings")).status_code, 200)
        self.client.force_login(self.owner)
        self.client.post(reverse("ai_assistant:recipient_add"), {"name": "GM", "email": "gm@inv.test",
                                                                 "gets_orders": "on"})
        self.assertTrue(AiRecipient.all_objects.filter(email="gm@inv.test").exists())
