"""Phase 7: valuation at any date, the reports, the dashboard, month-end close
and the morning alert.

Done when: the valuation report at any past date equals the ledger, and the
nightly balance check reports zero differences (``ValuationDoneWhenTests``).
"""

import datetime
from io import StringIO

from django.core import mail
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from access_control.branch_access import grant_access
from common.tenant import use_company
from employees.models import Employee
from inventory import periods, posting, valuation
from inventory.doc_common import local_today
from inventory.models import Batch, InventorySettings, Item, ItemCost, MoveType, OpeningStock, PeriodLock, StockBalance
from inventory.reports import REPORTS
from inventory.testing import D, InventoryFixture

AUG3, AUG10, AUG15, AUG20 = (datetime.date(2026, 8, d) for d in (3, 10, 15, 20))
SEP5, SEP12, SEP20 = (datetime.date(2026, 9, d) for d in (5, 12, 20))


class LedgerFixture(InventoryFixture):
    """Bearings and sugar moving through August and September 2026.

    Bearing (one average for the company): 100 in at 120, 100 at 130 (125),
    50 issued at 125, 50 in at 140 (128.75), 30 issued at 128.75. Sugar: 50 kg
    at 80, 20 kg moved to FGW. ``self.snapshots`` holds what the balances and
    averages said right after each day was posted.
    """

    def setUp(self):
        super().setUp()
        self.s1 = Batch.all_objects.create(company=self.company, item=self.sugar, batch_no="S1", source="opening",
                                           expiry_date=datetime.date(2027, 6, 30))
        self.doc = 0
        self.snapshots = {}
        self.move(AUG3, MoveType.OPENING, [(self.bearing, self.rms, "100", "120", None),
                                           (self.sugar, self.rms, "50", "80", self.s1)])
        self.move(AUG10, MoveType.RECEIPT, [(self.bearing, self.rms, "100", "130", None)])
        self.move(AUG20, MoveType.ISSUE, [(self.bearing, self.rms, "-50", None, None)])
        self.move(AUG20, MoveType.TRANSFER_OUT, [(self.sugar, self.rms, "-20", None, self.s1)])
        self.move(AUG20, MoveType.TRANSFER_IN, [(self.sugar, self.fgw, "20", "80", self.s1)])
        self.move(SEP5, MoveType.RECEIPT, [(self.bearing, self.rms, "50", "140", None)])
        self.issue = self.move(SEP12, MoveType.ISSUE, [(self.bearing, self.rms, "-30", None, None)])

    def move(self, day, move_type, lines, document_type="test"):
        self.doc += 1
        posting.post(company_id=self.company.pk, actor=self.owner, document_type=document_type,
                     document_id=self.doc, move_type=move_type, work_date=day, lines=[
                         posting.Line(item=item, warehouse=store, qty=D(qty), batch=batch,
                                      unit_cost=D(cost) if cost else None)
                         for item, store, qty, cost, batch in lines])
        self.snapshots[day] = self.held_now()
        return self.doc

    def held_now(self):
        """What the balances and the averages say: ``{item_id: (qty, value)}``."""
        held = {}
        for cost in ItemCost.all_objects.filter(company=self.company):
            qty = sum((b.qty_on_hand for b in StockBalance.all_objects.filter(item_id=cost.item_id)), D("0"))
            held[cost.item_id] = (qty, (qty * cost.avg_cost).quantize(D("0.01")))
        return held


class ValuationDoneWhenTests(LedgerFixture, TestCase):
    def valued(self, day):
        return {item_id: (qty, value.quantize(D("0.01")))
                for (item_id, _store), (qty, value) in valuation.at(self.company.pk, day).items()}

    def test_valuation_at_every_past_date_equals_the_ledger(self):
        # Worked by hand: bearing 170 at 128.75 = 21,887.50 on 12 Sep.
        self.assertEqual(self.snapshots[SEP12][self.bearing.pk], (D("170"), D("21887.50")))
        for day, held in self.snapshots.items():
            self.assertEqual(self.valued(day), held, day)
        # Between postings the stock is what the last posting left.
        self.assertEqual(self.valued(datetime.date(2026, 8, 31)), self.snapshots[AUG20])
        self.assertEqual(self.valued(datetime.date(2026, 8, 2)), {})

        # A later reversal and an entry dated back into August change only the
        # days from their dates on - the past before them stays as it was.
        posting.reverse(company_id=self.company.pk, actor=self.owner, document_type="test",
                        document_id=self.issue, work_date=SEP20)
        self.move(AUG15, MoveType.RECEIPT, [(self.bearing, self.rms, "10", "125", None)])
        for day in (AUG3, AUG10):
            self.assertEqual(self.valued(day), self.snapshots[day], day)
        self.assertEqual(self.valued(AUG20)[self.bearing.pk], (D("160"), D("20000.00")))   # 150 + 10 at 125
        # Today the ledger equals the balances, and the averages to the paisa.
        today = self.valued(datetime.date.today())
        for item_id, (qty, value) in self.held_now().items():
            self.assertEqual(today[item_id][0], qty)
            self.assertLessEqual(abs(today[item_id][1] - value), D("0.01"))

    def test_the_valuation_report_shows_the_ledger_and_the_nightly_check_is_clean(self):
        self.client.force_login(self.owner)
        page = self.client.get(reverse("inventory:report_valuation"), {"as_of": "2026-09-12"})
        result = page.context["result"]
        self.assertIn(("Value", "25,887.50"), result.summary)          # 21,887.50 bearings + 4,000 sugar
        rows = {row[0]: row for row in result.rows}
        self.assertEqual((rows["SP-0001"][5], rows["SP-0001"][6], rows["SP-0001"][7]),
                         (D("170"), D("128.7500"), D("21887.50")))
        by_store = self.client.get(reverse("inventory:report_valuation"), {"as_of": "2026-08-20", "view": "store"})
        self.assertEqual(sorted((r[0], r[4], r[6]) for r in by_store.context["result"].rows),
                         [("RM-0001", "FGW", D("20")), ("RM-0001", "RMS", D("30")), ("SP-0001", "RMS", D("150"))])
        out = StringIO()
        call_command("check_stock", company=self.company.pk, stdout=out)
        self.assertIn("no differences", out.getvalue())
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

    def test_movement_summary_adds_up_to_the_valuation(self):
        found = valuation.movement(self.company.pk, datetime.date(2026, 9, 1), datetime.date(2026, 9, 30))
        bearing = found[self.bearing.pk]
        self.assertEqual((bearing["open_q"], bearing["in_q"], bearing["out_q"], bearing["close_q"]),
                         (D("150"), D("50"), D("30"), D("170")))
        self.assertEqual(bearing["close_v"].quantize(D("0.01")), D("21887.50"))
        self.assertEqual(self.valued(datetime.date(2026, 9, 30))[self.bearing.pk], (D("170"), D("21887.50")))

    def test_aging_is_first_in_first_out(self):
        aged = valuation.aging(self.company.pk, SEP12)[(self.bearing.pk, self.rms.pk)]
        # 170 on hand: 50 from 5 Sep (7 days), 100 from 10 Aug and 20 from 3 Aug (33 and 40 days).
        self.assertEqual(aged["buckets"], [D("50"), D("120"), D("0"), D("0"), D("0"), D("0")])
        self.assertEqual(aged["oldest"], AUG3)


class ReportPageTests(LedgerFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.owner)

    def test_every_report_opens_and_downloads(self):
        for report in REPORTS:
            url = reverse(report.url_name)
            params = {"date_from": "2026-08-01", "date_to": "2026-09-30", "item": self.bearing.pk}
            page = self.client.get(url, params)
            self.assertEqual(page.status_code, 200, report.slug)
            for fmt, kind in (("xlsx", "spreadsheetml"), ("pdf", "pdf")):
                download = self.client.get(url, {**params, "format": fmt})
                self.assertEqual(download.status_code, 200, (report.slug, fmt))
                self.assertIn(kind, download["Content-Type"], (report.slug, fmt))
        index = self.client.get(reverse("inventory:reports"))
        self.assertContains(index, "Stock valuation")
        self.assertContains(index, 'data-menu="inventory"')

    def test_item_card_runs_the_balance(self):
        page = self.client.get(reverse("inventory:report_item_card"),
                               {"item": self.bearing.pk, "date_from": "2026-09-01", "date_to": "2026-09-30"})
        rows = page.context["result"].rows
        self.assertEqual([row[1] for row in rows][:1], ["Opening balance"])
        self.assertEqual([row[7] for row in rows], [D("150"), D("200"), D("170")])
        self.assertEqual(rows[-1][10], D("21887.50"))

    def test_slow_moving_reorder_and_expiry(self):
        Item.all_objects.filter(pk=self.bearing.pk).update(reorder_point=D("500"), reorder_qty=D("300"),
                                                          min_stock=D("200"))
        page = self.client.get(reverse("inventory:report_reorder"))
        [row] = page.context["result"].rows
        self.assertEqual((row[0], row[4], row[9], row[10]), ("SP-0001", D("170"), D("300"), "Below minimum"))
        slow = self.client.get(reverse("inventory:report_slow_moving"), {"days": "7"}).context["result"].rows
        # Sugar was never issued (a transfer is not use); the bearing last went out on 12 Sep.
        self.assertEqual({r[0]: r[8] for r in slow}, {"RM-0001": "Never used", "SP-0001": "Slow"})
        expiring = self.client.get(reverse("inventory:report_batch_expiry"), {"days": "3650"}).context["result"].rows
        self.assertEqual(sorted((r[2], r[3], r[8]) for r in expiring), [("S1", "FGW", D("20")), ("S1", "RMS", D("30"))])

    def test_a_viewer_sees_no_costs_and_cannot_open_the_valuation(self):
        user = self.member("keeper@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="K")
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.view", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        # The page gate sends an Employee login away from a page it may not open.
        self.assertEqual(self.client.get(reverse("inventory:report_valuation")).status_code, 302)
        page = self.client.get(reverse("inventory:report_stock_on_hand"))
        labels = [c.label for c in page.context["result"].columns]
        self.assertNotIn("Value", labels)
        self.assertContains(page, "Values are hidden")
        self.assertEqual(self.client.get(reverse("inventory:dashboard")).status_code, 200)
        self.assertEqual(self.client.get(reverse("inventory:period_close")).status_code, 302)

    def test_dashboard(self):
        Item.all_objects.filter(pk=self.bearing.pk).update(reorder_point=D("500"))
        page = self.client.get(reverse("inventory:dashboard"))
        self.assertContains(page, "Inventory dashboard")
        self.assertEqual(page.context["low_count"], 1)
        self.assertEqual(page.context["total"], D("25887.50"))
        self.assertEqual(self.client.get(reverse("inventory:home")).url, reverse("inventory:dashboard"))

    def test_without_the_module_nothing_opens(self):
        self.enable_module(False)
        self.assertEqual(self.client.get(reverse("inventory:report_valuation")).status_code, 404)
        self.assertEqual(self.client.get(reverse("inventory:dashboard")).status_code, 404)


class MonthEndTests(LedgerFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.today = local_today(self.company)
        self.last = (self.today.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        self.kw = {"actor": self.owner, "company_id": self.company.pk}

    def test_close_refuses_waiting_documents_then_locks_the_month(self):
        draft = OpeningStock.all_objects.create(company=self.company, number="OS-T-1", work_date=self.last)
        with self.assertRaisesMessage(ValidationError, "OS-T-1"):
            periods.close_month(**self.kw, month=self.last)
        OpeningStock.all_objects.filter(pk=draft.pk).update(status="cancelled")
        with self.assertRaisesMessage(ValidationError, "Only a month that has ended"):
            periods.close_month(**self.kw, month=self.today)
        lock = periods.close_month(**self.kw, month=self.last.replace(day=15), reason="Checked with accounts")
        self.assertEqual(lock.month, self.last)
        with self.assertRaisesMessage(ValidationError, "is closed for stock"):
            posting.post(company_id=self.company.pk, actor=self.owner, document_type="test", document_id=99,
                         move_type=MoveType.RECEIPT, work_date=self.last, lines=[
                             posting.Line(item=self.bearing, warehouse=self.rms, qty=D("1"), unit_cost=D("1"))])
        with self.assertRaisesMessage(ValidationError, "Say why"):
            periods.reopen_month(**self.kw, lock_id=lock.pk, reason="")
        periods.reopen_month(**self.kw, lock_id=lock.pk, reason="Late GRN found")
        self.assertFalse(PeriodLock.all_objects.exists())

    def test_close_needs_the_permission(self):
        user = self.member("acc@inv.test", "employee")
        with use_company(self.company):
            employee = Employee.objects.create(company=self.company, user=user, first_name="A")
        with self.assertRaises(PermissionDenied):
            periods.close_month(actor=user, company_id=self.company.pk, month=self.last)
        grant_access(actor=self.owner, company_id=self.company.pk, employee_id=employee.pk,
                     code="inventory.period_close", branch_ids=[self.branch.pk])
        self.client.force_login(user)
        page = self.client.get(reverse("inventory:period_close"))
        self.assertContains(page, f"Before closing {self.last:%B %Y}")
        self.client.post(reverse("inventory:period_close"), {"month": self.last.isoformat(), "reason": ""})
        self.assertTrue(PeriodLock.all_objects.filter(month=self.last).exists())
        lock = PeriodLock.all_objects.get()
        self.client.post(reverse("inventory:period_reopen", args=[lock.pk]), {"reason": "Mistake"})
        self.assertFalse(PeriodLock.all_objects.exists())


@override_settings(MAIL_CONFIGURED=True, DEFAULT_FROM_EMAIL="server@host.test",
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class AlertTests(LedgerFixture, TestCase):
    def set_addresses(self, text):
        row, _made = InventorySettings.all_objects.get_or_create(company=self.company)
        InventorySettings.all_objects.filter(pk=row.pk).update(alert_emails=text)

    def test_the_morning_alert_lists_reorder_and_expiry(self):
        self.set_addresses("store@inv.test, gm@inv.test\nstore@inv.test")
        Item.all_objects.filter(pk=self.bearing.pk).update(reorder_point=D("500"), reorder_qty=D("300"))
        out = StringIO()
        call_command("stock_alerts", date="2027-06-10", stdout=out)
        [email] = mail.outbox
        self.assertEqual(email.to, ["store@inv.test", "gm@inv.test"])
        self.assertEqual(email.subject, "Stock alert 10 Jun 2027: 1 to reorder, 2 expiring")
        self.assertIn("SP-0001 Bearing 6205: 170 PCS available", email.body)
        self.assertIn("batch S1 in RMS: 30 KG, expires in 20 day(s)", email.body)
        self.assertIn("sent", out.getvalue())

    def test_no_addresses_or_nothing_to_say_sends_nothing(self):
        call_command("stock_alerts", stdout=StringIO())
        self.set_addresses("store@inv.test")
        out = StringIO()
        call_command("stock_alerts", date="2026-10-01", stdout=out)
        self.assertEqual(mail.outbox, [])
        self.assertIn("nothing to report", out.getvalue())

    def test_the_settings_form_checks_the_addresses(self):
        from inventory.forms import SettingsForm

        form = SettingsForm(data={"valuation_scope": "company", "expiry_alert_days": 30, "slow_moving_days": 90,
                                  "alert_emails": "a@inv.test; not-an-address", "small_label": "50x25",
                                  "large_label": "100x50"})
        with use_company(self.company):
            self.assertFalse(form.is_valid())
        self.assertIn("alert_emails", form.errors)
