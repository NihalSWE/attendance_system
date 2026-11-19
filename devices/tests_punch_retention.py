from datetime import timedelta

from django.urls import reverse
from django.utils import timezone

from auditlog.models import AuditLog
from devices import tests_server_address
from devices.models import DeviceMessage, DevicePunchRetention, PunchEvent
from devices.services.commands import pending_summary


class PunchRetentionTests(tests_server_address.ServerAddressTestCase):
    def test_plan_previews_only_older_hrm_punches_and_sends_no_command(self):
        self.client.force_login(self.admin)
        url = reverse("devices:device_punch_retention", args=[self.device.public_id])
        detail = reverse("devices:device_detail", args=[self.device.public_id])
        now = timezone.now()
        message = DeviceMessage.all_objects.create(
            company=self.company, device=self.device, branch=self.branch,
            message_type=DeviceMessage.MessageType.PUNCH_BATCH, received_at=now,
            raw_payload_text="sample punches for the retention preview",
        )
        for index, age in enumerate((8, 2)):
            punched_at = now - timedelta(days=age)
            PunchEvent.all_objects.create(
                company=self.company, device=self.device, device_message=message, branch=self.branch,
                device_user_id="100", source_record_index=index, punched_at_device_raw=punched_at.isoformat(),
                punched_at_device=punched_at, punched_at_utc=punched_at, received_at=now, raw_record={},
            )

        response = self.client.post(url, {"days": "7"}, follow=True)
        self.assertEqual(response.redirect_chain[-1][0], f"{detail}#punch-retention")
        self.assertEqual(response.context["punches_before_retention_cutoff"], 1)
        self.assertContains(response, "Automatic cleanup unavailable")

        response = self.client.post(url, {"days": "0"}, follow=True)
        self.assertEqual(DevicePunchRetention.all_objects.get(device=self.device).days, 7)
        self.assertContains(response, "Ensure this value is greater than or equal to 1.")

        response = self.client.post(url, {"days": ""}, follow=True)
        self.assertIsNone(response.context["retention_cutoff"])
        self.assertFalse(DevicePunchRetention.all_objects.filter(device=self.device).exists())
        self.assertEqual(PunchEvent.all_objects.filter(device=self.device).count(), 2)
        self.assertEqual(pending_summary(self.device), [])
        self.assertEqual(AuditLog.objects.filter(action="device.punch_retention_updated").count(), 2)
