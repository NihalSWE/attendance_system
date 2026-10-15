"""The posting service (plan §5, §10): the most important tests of the module.

Every rule that must always hold is exercised here against the service
itself, before any screen uses it.
"""

import datetime

from django.core.exceptions import ValidationError
from django.test import TestCase

from inventory import posting
from inventory.models import (
    Batch,
    InventorySettings,
    ItemCost,
    MoveType,
    PeriodLock,
    StockBalance,
    StockMove,
)
from inventory.testing import DAY, D, InventoryFixture


class PostingTests(InventoryFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.b1 = self.batch(self.sugar, "B1", expiry=datetime.date(2027, 9, 30))

    def batch(self, item, number, status=Batch.Status.RELEASED, expiry=None):
        return Batch.all_objects.create(company=self.company, item=item, batch_no=number,
                                        source=Batch.Source.GRN, status=status, expiry_date=expiry)

    def post(self, move_type, *lines, day=DAY, doc=1):
        return posting.post(company_id=self.company.pk, actor=self.owner, document_type="test",
                            document_id=doc, move_type=move_type, lines=list(lines),
                            work_date=day)

    def line(self, qty, cost=None, batch=None, store=None):
        return posting.Line(item=self.sugar, warehouse=store or self.rms, qty=D(qty),
                            batch=batch or self.b1, unit_cost=None if cost is None else D(cost))

    def on_hand(self, item, batch=None, store=None):
        row = StockBalance.all_objects.filter(item=item, batch=batch,
                                              warehouse=store or self.rms).first()
        return row.qty_on_hand if row else D(0)

    def average(self, item, store=None):
        return ItemCost.all_objects.get(item=item, warehouse=store).avg_cost

    # --- moves, balances, average cost --------------------------------------

    def test_weighted_average_example_from_the_plan(self):
        self.post(MoveType.RECEIPT, self.line("100", "120"))
        self.post(MoveType.RECEIPT, self.line("100", "130"))
        self.assertEqual(self.average(self.sugar), D("125"))
        [issue] = self.post(MoveType.ISSUE, self.line("-50"))
        self.assertEqual(issue.unit_cost, D("125"))
        self.assertEqual(issue.value, D("-6250"))
        self.assertEqual(self.average(self.sugar), D("125"))
        self.assertEqual(self.on_hand(self.sugar, self.b1), D("150"))
        self.assertEqual(ItemCost.all_objects.get(item=self.sugar).qty_basis, D("150"))

    def test_a_receipt_writes_one_move_and_one_balance(self):
        [move] = self.post(MoveType.RECEIPT, self.line("12.5", "100"))
        self.assertEqual((move.qty, move.value, move.move_type), (D("12.5"), D("1250"), "receipt"))
        self.assertEqual(move.posted_by, self.owner)
        self.assertEqual(self.on_hand(self.sugar, self.b1), D("12.5"))

    def test_lines_in_any_order_post_the_same(self):
        b2 = self.batch(self.sugar, "B2")
        moves = self.post(MoveType.RECEIPT, self.line("5", "10", batch=b2),
                          self.line("5", "10"), self.line("5", "10", store=self.fgw))
        self.assertEqual(len(moves), 3)
        self.assertEqual(StockBalance.all_objects.filter(item=self.sugar).count(), 3)

    # --- refusals -----------------------------------------------------------

    def test_no_negative_stock_and_nothing_written(self):
        self.post(MoveType.RECEIPT, self.line("10", "100"))
        with self.assertRaisesMessage(ValidationError, "Not enough RM-0001 (batch B1) in RMS"):
            self.post(MoveType.ISSUE, self.line("-4"), self.line("-7"))
        self.assertEqual(self.on_hand(self.sugar, self.b1), D("10"))
        self.assertEqual(StockMove.all_objects.count(), 1)

    def test_no_issue_from_quarantine_blocked_or_expired(self):
        held = self.batch(self.sugar, "Q1", status=Batch.Status.QUARANTINE)
        old = self.batch(self.sugar, "OLD", expiry=DAY - datetime.timedelta(days=1))
        self.post(MoveType.RECEIPT, self.line("10", "100", batch=held),
                  self.line("10", "100", batch=old))
        with self.assertRaisesMessage(ValidationError, "Batch Q1 of RM-0001 is quarantine"):
            self.post(MoveType.ISSUE, self.line("-1", batch=held))
        with self.assertRaisesMessage(ValidationError, "Batch OLD of RM-0001 is expired"):
            self.post(MoveType.DELIVERY, self.line("-1", batch=old))
        # Moving a quarantined batch to another store is allowed.
        self.post(MoveType.TRANSFER_OUT, self.line("-10", batch=held))

    def test_batches_are_required_exactly_where_kept(self):
        with self.assertRaisesMessage(ValidationError, "keeps batches"):
            self.post(MoveType.RECEIPT, posting.Line(item=self.sugar, warehouse=self.rms,
                                                     qty=D("1"), unit_cost=D("1")))
        with self.assertRaisesMessage(ValidationError, "does not keep batches"):
            self.post(MoveType.RECEIPT, posting.Line(item=self.bearing, warehouse=self.rms,
                                                     qty=D("1"), batch=self.b1))

    def test_whole_pieces_only_for_a_unit_without_decimals(self):
        with self.assertRaisesMessage(ValidationError, "counted in PCS with 0 digits"):
            self.post(MoveType.RECEIPT, posting.Line(item=self.bearing, warehouse=self.rms,
                                                     qty=D("1.5"), unit_cost=D("450")))

    def test_reserved_stock_is_kept_for_deliveries(self):
        self.post(MoveType.RECEIPT, self.line("10", "100"))
        StockBalance.all_objects.filter(item=self.sugar).update(qty_reserved=D("8"))
        with self.assertRaisesMessage(ValidationError, "is reserved for sales orders"):
            self.post(MoveType.ISSUE, self.line("-3"))
        self.post(MoveType.DELIVERY, self.line("-3"))

    def test_a_closed_month_refuses_postings(self):
        PeriodLock.all_objects.create(company=self.company, month=DAY.replace(day=1))
        with self.assertRaisesMessage(ValidationError, "October 2026 is closed"):
            self.post(MoveType.RECEIPT, self.line("1", "1"))
        self.post(MoveType.RECEIPT, self.line("1", "1"), day=datetime.date(2026, 11, 1))

    def test_another_companys_store_is_refused(self):
        from tenants import platform_services

        other = platform_services.create_platform_company(actor=self.root,
                                                          values={"name": "Other Co"})
        self.rms.company_id = other.pk
        with self.assertRaisesMessage(ValidationError, "store belongs to another company"):
            self.post(MoveType.RECEIPT, self.line("1", "1"))

    # --- the ledger cannot be changed ---------------------------------------

    def test_moves_are_never_changed_or_deleted(self):
        [move] = self.post(MoveType.RECEIPT, self.line("1", "1"))
        move.note = "changed"
        with self.assertRaises(ValidationError):
            move.save()
        with self.assertRaises(ValidationError):
            move.delete()
        with self.assertRaises(ValidationError):
            StockMove.all_objects.filter(pk=move.pk).update(note="x")
        with self.assertRaises(ValidationError):
            StockMove.all_objects.filter(pk=move.pk).delete()

    # --- reversal -------------------------------------------------------------

    def test_reversal_undoes_stock_and_cost(self):
        self.post(MoveType.RECEIPT, self.line("100", "120"), doc=1)
        self.post(MoveType.RECEIPT, self.line("100", "130"), doc=2)
        counter = posting.reverse(company_id=self.company.pk, actor=self.owner,
                                  document_type="test", document_id=2, work_date=DAY)
        self.assertEqual([(m.qty, m.unit_cost, m.move_type) for m in counter],
                         [(D("-100"), D("130"), "reversal")])
        self.assertEqual(self.on_hand(self.sugar, self.b1), D("100"))
        self.assertEqual(self.average(self.sugar), D("120"))
        with self.assertRaisesMessage(ValidationError, "no stock moves to reverse"):
            posting.reverse(company_id=self.company.pk, actor=self.owner,
                            document_type="test", document_id=2, work_date=DAY)

    def test_reversal_refused_once_the_stock_was_used(self):
        self.post(MoveType.RECEIPT, self.line("10", "100"), doc=1)
        self.post(MoveType.ISSUE, self.line("-6"), doc=2)
        with self.assertRaisesMessage(ValidationError, "Not enough RM-0001"):
            posting.reverse(company_id=self.company.pk, actor=self.owner,
                            document_type="test", document_id=1, work_date=DAY)

    # --- the nightly check ----------------------------------------------------

    def test_ledger_equals_balances_and_a_difference_is_reported(self):
        self.post(MoveType.RECEIPT, self.line("10", "100"), self.line("4", "90", store=self.fgw))
        self.post(MoveType.ISSUE, self.line("-3"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])
        StockBalance.all_objects.filter(warehouse=self.fgw).update(qty_on_hand=D("5"))
        [difference, cost] = posting.ledger_differences(self.company.pk)
        self.assertEqual((difference["ledger"], difference["balance"]), (D("4"), D("5")))
        self.assertTrue(cost["cost"])

    # --- valuation per store --------------------------------------------------

    def test_one_average_per_store_when_chosen(self):
        InventorySettings.all_objects.create(
            company=self.company, valuation_scope=InventorySettings.ValuationScope.WAREHOUSE)
        self.post(MoveType.RECEIPT, self.line("10", "100"), self.line("10", "200", store=self.fgw))
        self.assertEqual(self.average(self.sugar, self.rms), D("100"))
        self.assertEqual(self.average(self.sugar, self.fgw), D("200"))
        self.assertEqual(posting.ledger_differences(self.company.pk), [])

    # --- numbering ------------------------------------------------------------

    def test_document_numbers_follow_on_per_type_and_year(self):
        numbers = [posting.next_number(self.company.pk, "opening_stock", "OS", 2026) for _ in range(3)]
        self.assertEqual(numbers, ["OS-2026-00001", "OS-2026-00002", "OS-2026-00003"])
        self.assertEqual(posting.next_number(self.company.pk, "opening_stock", "OS", 2027),
                         "OS-2027-00001")
        self.assertEqual(posting.next_number(self.company.pk, "issue", "SI", 2026), "SI-2026-00001")
