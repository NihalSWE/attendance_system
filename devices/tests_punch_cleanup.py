"""Retention must never broaden a delete or treat acknowledgements as proof."""
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from devices.tests_server_address import ServerAddressTestCase
from devices.models import DeviceMessage, DeviceOutboxCommand, DevicePunchCleanup, DevicePunchRetention, PunchEvent
from devices.services import commands, punch_cleanup as cleanup


@override_settings(DEVICE_PUNCH_CLEANUP_SERIALS=["SN-A"])
class PunchCleanupTests(ServerAddressTestCase):
    def setUp(self):
        super().setUp()
        self.device.firmware_version = cleanup.FIRMWARE
        self.device.save(update_fields=["firmware_version"])
        self.policy = DevicePunchRetention.all_objects.create(company=self.company, device=self.device, days=18)

    def row(self, index, age=30, saved=True, event="3"):
        from zoneinfo import ZoneInfo
        at = (timezone.now() - timedelta(days=age)).astimezone(ZoneInfo(self.device.timezone)).replace(microsecond=0)
        encoded = (((((at.year-2000)*12 + at.month-1)*31 + at.day-1)*24 + at.hour)*60 + at.minute)*60 + at.second
        fields = dict(index=str(index), pin="100", time_second=str(encoded), eventtype=event)
        if saved:
            msg = DeviceMessage.all_objects.create(company=self.company, device=self.device, branch=self.branch,
                message_type="unknown", received_at=timezone.now(), raw_payload_text="fixture")
            PunchEvent.all_objects.create(company=self.company, device=self.device, device_message=msg,
                branch=self.branch, device_user_id="100", source_record_index=0,
                vendor_punch_id=f"rtlog:{index}:{at:%Y-%m-%d %H:%M:%S}",
                punched_at_device_raw=at.isoformat(), punched_at_device=at, punched_at_utc=at,
                received_at=timezone.now(), raw_record={})
        return fields

    def deliver(self, command_id, rows, count=None, total=1):
        DeviceOutboxCommand.all_objects.filter(device=self.device, command_id=command_id).update(
            status="done", return_code=str(len(rows) if count is None else count),
            sent_at=timezone.now(), answered_at=timezone.now())
        DeviceMessage.all_objects.create(company=self.company, device=self.device, branch=self.branch,
            message_type="unknown", received_at=timezone.now(),
            payload_json={"query": {"cmdid": [str(command_id)], "tablename": ["transaction"],
                "count": [str(len(rows))], "packidx": ["1"], "packcnt": [str(total)]}},
            raw_payload_text="\n".join("transaction " + "\t".join(f"{k}={v}" for k,v in r.items()) for r in rows))

    def job(self):
        return DevicePunchCleanup.all_objects.get(device=self.device)

    def prepare(self, rows):
        cleanup.advance(self.device.pk)
        self.deliver(self.job().state["before"], rows)
        cleanup.advance(self.device.pk)
        return self.job()

    def test_batch_deletes_only_saved_older_punches_and_verifies_readback(self):
        old, recent, unsaved, system = self.row(1), self.row(2, age=1), self.row(3, saved=False), self.row(4, event="9")
        job = self.prepare([old, recent, unsaved, system])
        self.assertEqual(job.phase, "deleting")
        self.assertEqual(list(job.state["targets"]), ["1"])
        writes = DeviceOutboxCommand.all_objects.filter(command_id__in=job.state["writes"], device=self.device)
        row = writes.get()
        self.assertEqual(row.body, "DATA DELETE transaction Index=1")
        self.assertEqual(cleanup.write_problem(self.device, row, row.body, timezone.now()), "")
        writes.update(status="done", return_code="0", answered_at=timezone.now())
        cleanup.advance(self.device.pk)
        self.assertEqual(self.job().phase, "verifying")
        self.assertEqual(self.job().verified_deleted, 0)
        self.deliver(self.job().state["after"], [recent, unsaved, system])
        cleanup.advance(self.device.pk)
        self.assertEqual(self.job().phase, "idle")
        self.assertEqual(self.job().verified_deleted, 1)
        self.assertEqual(PunchEvent.all_objects.filter(device=self.device).count(), 3)
        count = DeviceOutboxCommand.all_objects.filter(device=self.device).count()
        cleanup.advance(self.device.pk)
        self.assertEqual(DeviceOutboxCommand.all_objects.filter(device=self.device).count(), count)

    def test_unexpected_extra_deletion_pauses(self):
        old, recent = self.row(1), self.row(2, age=1)
        job = self.prepare([old, recent])
        DeviceOutboxCommand.all_objects.filter(device=self.device, command_id__in=job.state["writes"]).update(status="done")
        cleanup.advance(self.device.pk)
        self.deliver(self.job().state["after"], [])
        cleanup.advance(self.device.pk)
        self.assertEqual(self.job().phase, "paused")
        self.assertIn("non-target", self.job().message)
        self.assertEqual(self.job().verified_deleted, 0)

    def test_incomplete_inventory_never_deletes(self):
        cleanup.advance(self.device.pk)
        self.deliver(self.job().state["before"], [self.row(1)], total=2)
        cleanup.advance(self.device.pk)
        self.assertEqual(self.job().phase, "paused")
        self.assertFalse(DeviceOutboxCommand.all_objects.filter(key__startswith=cleanup.DELETE_PREFIX).exists())

    def test_changed_disabled_and_expired_policy_blocks_dispatch(self):
        job = self.prepare([self.row(1)])
        row = DeviceOutboxCommand.all_objects.get(device=self.device, command_id=job.state["writes"][0])
        self.assertIn("expired", cleanup.write_problem(self.device, row, row.body, timezone.now()+timedelta(minutes=11)))
        self.policy.days = 40
        self.policy.save()
        self.assertIn("changed", cleanup.write_problem(self.device, row, row.body, timezone.now()))
        self.policy.delete()
        lines, entries = commands._take_outbox(self.device, 5)
        self.assertEqual(lines, [])
        row.refresh_from_db()
        self.assertEqual(row.status, "dropped")

    def test_unsupported_firmware_does_not_start(self):
        self.device.firmware_version = "different"
        self.device.save(update_fields=["firmware_version"])
        cleanup.advance(self.device.pk)
        self.assertFalse(DeviceOutboxCommand.all_objects.filter(device=self.device).exists())

    def test_no_wildcard_or_table_injection_and_cutoff_is_strict(self):
        for value in ("*", "1 OR 1", "0", "-1", "١", "1\tuser=*", "1\n"):
            with self.assertRaises(ValueError):
                cleanup.delete_body(value)
        row = self.row(1)
        from zoneinfo import ZoneInfo
        cutoff = cleanup.decode_time(row["time_second"], ZoneInfo(self.device.timezone))
        eligible, _ = cleanup.candidates(self.device, {"1": row}, cutoff)
        self.assertEqual(eligible, [])

    def test_batch_limit_and_missing_hrm_evidence(self):
        rows = [self.row(i) for i in range(1, 29)]
        job = self.prepare(rows)
        self.assertEqual(len(job.state["targets"]), cleanup.BATCH_SIZE)
        row = DeviceOutboxCommand.all_objects.get(device=self.device, command_id=job.state["writes"][0])
        with patch.object(cleanup, "evidence", return_value=False):
            self.assertIn("could not be verified", cleanup.write_problem(self.device, row, row.body, timezone.now()))

    def test_status_page_reports_enabled_and_paused_truthfully(self):
        from django.urls import reverse
        self.client.force_login(self.admin)
        url = reverse("devices:device_detail", args=[self.device.public_id])
        self.assertContains(self.client.get(url), "Automatic cleanup enabled")
        job = self.prepare([self.row(1)])
        job.phase, job.message = "paused", "Unexpected non-target log change"
        job.save()
        response = self.client.get(url)
        self.assertContains(response, "Cleanup paused")
        self.assertNotContains(response, "Automatic cleanup enabled")

    def test_other_device_punches_are_not_evidence(self):
        row = self.row(1, saved=False)
        from zoneinfo import ZoneInfo
        at = cleanup.decode_time(row["time_second"], ZoneInfo(self.device.timezone))
        # Wrong device is excluded even when the vendor index, person and time match.
        other = self.device.__class__.all_objects.create(company=self.company, branch=self.branch,
            device_model=self.device.device_model, serial_number="OTHER", name="Other", status="active")
        message = DeviceMessage.all_objects.create(company=self.company, device=other, branch=self.branch,
            message_type="unknown", received_at=timezone.now(), raw_payload_text="other device fixture")
        PunchEvent.all_objects.create(company=self.company, device=other, device_message=message,
            branch=self.branch, device_user_id="100", source_record_index=0,
            vendor_punch_id=f"rtlog:1:{at:%Y-%m-%d %H:%M:%S}", punched_at_device_raw=at.isoformat(),
            punched_at_device=at, punched_at_utc=at, received_at=timezone.now(), raw_record={})
        self.assertFalse(cleanup.evidence(self.device, row))
        job = self.prepare([row])
        self.assertEqual(job.phase, "idle")
        self.assertEqual(job.state["skipped"], 1)
