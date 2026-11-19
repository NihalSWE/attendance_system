from datetime import timedelta
from unittest.mock import Mock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from devices.services.biometric_pilot import capture_reply


class BiometricPilotReplyTests(SimpleTestCase):
    @patch("devices.services.biometric_pilot.terminal_card.enabled", return_value=True)
    @patch("devices.services.biometric_pilot.templates.save_from_payload")
    @patch("devices.services.biometric_pilot.DeviceOutboxCommand.all_objects.filter")
    def test_reply_stores_only_the_requested_employee_and_type(self, lookup, save, _enabled):
        lookup.return_value.values_list.return_value.first.return_value = (
            "pilot_bio_read:100:9", timezone.now(),
        )
        device = Mock()
        selected = "biodata pin=100\ttype=9\ttmp=FACE"
        body = "\n".join((selected, "biodata pin=100\ttype=1\ttmp=FINGER",
                          "biodata pin=101\ttype=9\ttmp=OTHER"))

        self.assertEqual(capture_reply(device, "58", body), "biodata=3")
        save.assert_called_once_with(device, selected, refresh_capture=True)

    @patch("devices.services.biometric_pilot.terminal_card.enabled", return_value=True)
    @patch("devices.services.biometric_pilot.templates.save_from_payload")
    @patch("devices.services.biometric_pilot.DeviceOutboxCommand.all_objects.filter")
    def test_late_reply_is_acknowledged_without_storing_templates(self, lookup, save, _enabled):
        lookup.return_value.values_list.return_value.first.return_value = (
            "pilot_bio_read:100:9", timezone.now() - timedelta(minutes=3),
        )
        body = "biodata pin=100\ttype=9\ttmp=FACE"

        self.assertEqual(capture_reply(Mock(), "58", body), "biodata=0")
        save.assert_not_called()
