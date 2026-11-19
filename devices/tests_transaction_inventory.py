from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from devices.services.transaction_inventory import decode_time, inspect


def packet(index, total, lines, *, count=None):
    return SimpleNamespace(
        payload_json={"query": {"tablename": ["transaction"], "cmdid": ["132"],
                                 "packidx": [str(index)], "packcnt": [str(total)],
                                 "count": [str(len(lines) if count is None else count)]}},
        raw_payload_text="\n".join(lines),
    )


class TransactionInventoryTests(SimpleTestCase):
    def report(self, packets):
        reader = SimpleNamespace(company_id=4, timezone="UTC")
        with patch("devices.services.transaction_inventory.DeviceMessage") as model:
            chain = model.all_objects.filter.return_value.filter.return_value
            chain.only.return_value.order_by.return_value.iterator.return_value = packets
            result = inspect(reader, 132, 18, now=datetime(2000, 1, 20, tzinfo=timezone.utc))
            model.all_objects.filter.assert_called_once_with(company_id=4, device=reader)
        return result

    def test_vendor_time_is_not_unix_and_uses_31_day_months(self):
        self.assertEqual(decode_time(0, timezone.utc), datetime(2000, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(decode_time(31 * 86400, timezone.utc),
                         datetime(2000, 2, 1, tzinfo=timezone.utc))

    def test_exact_cutoff_is_retained_and_complete_read_still_cannot_delete(self):
        result = self.report([packet(1, 1, ["transaction index=1\ttime_second=0",
                                             "transaction index=2\ttime_second=86400"])])
        self.assertEqual(result["older_records"], 1)
        self.assertEqual(result["retained_records"], 1)
        self.assertEqual(result["issues"], [])
        self.assertFalse(result["cleanup_available"])

    def test_incomplete_or_huge_packet_total_is_rejected_without_allocating_range(self):
        result = self.report([packet(1, 10**12, [])])
        self.assertIn("Incomplete packet set", result["issues"])

    def test_identical_retransmission_is_not_counted_twice(self):
        p = packet(1, 1, ["transaction index=1\ttime_second=0"])
        self.assertEqual(self.report([p, p])["records"], 1)

    def test_conflicting_packet_and_count_mismatch_are_reported(self):
        result = self.report([packet(1, 1, []), packet(1, 1, ["bad"], count=2)])
        self.assertIn("Conflicting retransmitted packet", result["issues"])
        self.assertIn("Packet row count mismatch", result["issues"])
        self.assertIn("Unparseable transaction row", result["issues"])

    def test_clock_anomalies_and_repeated_indices_are_reported(self):
        result = self.report([packet(1, 1, ["transaction index=1\ttime_second=-1",
                                             "transaction index=1\ttime_second=32140800"])])
        self.assertEqual(result["pre_2000"], 1)
        self.assertEqual(result["future_dated"], 1)
        self.assertIn("Repeated transaction index", result["issues"])
        self.assertIn("Reader clock anomalies require review", result["issues"])
