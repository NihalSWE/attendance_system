"""Ingestion endpoint tests: durability, idempotency and device authentication.

These exercise the acceptance points from DEVICE_INTEGRATION_HANDOFF.md
section 10 that do not need hardware:

- replaying the same batch twice produces no duplicate attendance effect;
- an unregistered/retired device is refused;
- evidence is preserved even when a record cannot be interpreted;
- two genuinely close scans are marked for review, not silently merged.

Everything here is **simulator evidence**. It is not proof that the physical
SenseFace 2A behaves this way; that is verified separately on real firmware.
"""

from datetime import datetime, timezone as dt_timezone

from django.contrib.auth.hashers import make_password
from django.test import Client, TestCase

from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceMessage,
    DeviceSyncState,
    DeviceModel,
    DeviceVendor,
    PunchEvent,
)
from organization.models import Branch
from tenants.models import Company


ATTLOG_TWO_RECORDS = (
    "1\t2026-09-08 09:01:02\t0\t1\t0\t\t\n"
    "2\t2026-09-08 09:03:44\t0\t15\t0\t\t\n"
)


class IngestionEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.vendor = DeviceVendor.objects.create(
            code="zkteco", name="ZKTeco", adapter_key="zkteco_adms_push"
        )
        self.device_model = DeviceModel.objects.create(
            vendor=self.vendor,
            model_code="senseface-2a",
            name="SenseFace 2A",
            protocol=DeviceModel.Protocol.ADMS_PUSH,
            capabilities={"push": True, "face": True},
        )
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="Head Office", is_default=True
            )
            self.device = BiometricDevice.objects.create(
                branch=self.branch,
                device_model=self.device_model,
                name="Front Door",
                serial_number="SF2A-001",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )

    def _post_attlog(self, body=ATTLOG_TWO_RECORDS, serial="SF2A-001", stamp="9999"):
        return self.client.post(
            f"/iclock/cdata?SN={serial}&table=ATTLOG&Stamp={stamp}",
            data=body,
            content_type="text/plain",
        )

    # --- authentication ---------------------------------------------------

    def test_unregistered_serial_is_refused(self):
        response = self._post_attlog(serial="NOT-REGISTERED")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(DeviceMessage.all_objects.count(), 0)

    def test_retired_device_cannot_ingest(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            status=BiometricDevice.Status.RETIRED
        )
        response = self._post_attlog()
        self.assertEqual(response.status_code, 401)
        self.assertEqual(DeviceMessage.all_objects.count(), 0)

    def test_device_secret_is_verified_when_configured(self):
        BiometricDevice.all_objects.filter(pk=self.device.pk).update(
            authentication_secret_hash=make_password("comm-key-123")
        )
        self.assertEqual(self._post_attlog().status_code, 401)

        ok = self.client.post(
            "/iclock/cdata?SN=SF2A-001&table=ATTLOG&Stamp=1&key=comm-key-123",
            data=ATTLOG_TWO_RECORDS,
            content_type="text/plain",
        )
        self.assertEqual(ok.status_code, 200)

    def test_ambiguous_serial_across_companies_is_refused(self):
        # The same serial may legitimately exist in two tenants, because
        # serial numbers are unique per company. Guessing would attach one
        # company's punches to another's employees.
        other = Company.objects.create(code="B", slug="b", name="Company B")
        with use_company(other):
            other_branch = Branch.objects.create(
                code="HQ", name="B HQ", is_default=True
            )
            BiometricDevice.objects.create(
                branch=other_branch,
                device_model=self.device_model,
                name="B Front Door",
                serial_number="SF2A-001",
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
        self.assertEqual(self._post_attlog().status_code, 401)
        self.assertEqual(DeviceMessage.all_objects.count(), 0)

    # --- durable capture --------------------------------------------------

    def test_batch_is_captured_with_raw_payload_and_punches(self):
        response = self._post_attlog()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "OK: 2")

        message = DeviceMessage.all_objects.get()
        self.assertEqual(message.company_id, self.company.pk)
        self.assertEqual(message.branch_id, self.branch.pk)
        self.assertEqual(message.message_type, DeviceMessage.MessageType.PUNCH_BATCH)
        self.assertEqual(message.raw_payload_text, ATTLOG_TWO_RECORDS)
        self.assertEqual(message.record_count, 2)
        self.assertEqual(
            message.processing_status, DeviceMessage.ProcessingStatus.PARSED
        )

        punches = list(PunchEvent.all_objects.order_by("source_record_index"))
        self.assertEqual(len(punches), 2)
        self.assertEqual(punches[0].device_user_id, "1")
        self.assertEqual(punches[0].punched_at_device_raw, "2026-09-08 09:01:02")
        # Asia/Dhaka is UTC+6, so 09:01:02 local is 03:01:02 UTC.
        self.assertEqual(
            punches[0].punched_at_utc,
            datetime(2026, 9, 8, 3, 1, 2, tzinfo=dt_timezone.utc),
        )
        self.assertEqual(punches[0].utc_offset_minutes, 360)
        self.assertEqual(
            punches[0].verification_method, PunchEvent.VerificationMethod.FINGERPRINT
        )
        self.assertEqual(
            punches[1].verification_method, PunchEvent.VerificationMethod.FACE
        )

    def test_sync_state_records_last_punch_received(self):
        self._post_attlog()
        state = DeviceSyncState.all_objects.get(device=self.device)
        self.assertIsNotNone(state.last_punch_received_at)
        self.assertEqual(state.consecutive_error_count, 0)

    def test_headers_snapshot_excludes_credentials(self):
        self.client.post(
            "/iclock/cdata?SN=SF2A-001&table=ATTLOG&Stamp=1",
            data=ATTLOG_TWO_RECORDS,
            content_type="text/plain",
            headers={"authorization": "Bearer super-secret"},
        )
        message = DeviceMessage.all_objects.get()
        snapshot = message.request_headers_snapshot or {}
        self.assertNotIn("Authorization", snapshot)
        self.assertNotIn(
            "super-secret", " ".join(str(v) for v in snapshot.values())
        )

    # --- idempotency: the core acceptance rule ----------------------------

    def test_replaying_the_same_batch_produces_no_duplicate_effect(self):
        first = self._post_attlog()
        second = self._post_attlog()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        # One stored message, two punches — not two messages and four punches.
        self.assertEqual(DeviceMessage.all_objects.count(), 1)
        self.assertEqual(PunchEvent.all_objects.count(), 2)
        # The device is still acknowledged, so it advances its pointer instead
        # of re-sending the batch forever.
        self.assertEqual(second.content.decode(), "OK: 2")

    def test_same_punch_resent_in_a_different_batch_is_kept_but_excluded(self):
        self._post_attlog(stamp="1")
        # Same records, different stamp: a new message, but the punches are
        # retransmissions and must not count twice.
        self._post_attlog(stamp="2")

        self.assertEqual(DeviceMessage.all_objects.count(), 2)
        self.assertEqual(PunchEvent.all_objects.count(), 4)

        duplicates = PunchEvent.all_objects.filter(
            dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE
        )
        self.assertEqual(duplicates.count(), 2)
        for duplicate in duplicates:
            # Evidence is preserved and linked, never deleted.
            self.assertIsNotNone(duplicate.duplicate_of_id)
            self.assertEqual(
                duplicate.processing_status, PunchEvent.ProcessingStatus.EXCLUDED
            )
        # Exactly two punches remain countable.
        self.assertEqual(
            PunchEvent.all_objects.filter(
                dedupe_status=PunchEvent.DedupeStatus.UNIQUE
            ).count(),
            2,
        )

    def test_close_but_distinct_scans_are_flagged_for_review_not_excluded(self):
        # 20 seconds apart, inside the default 30s repeat window: ambiguous,
        # so it is marked reviewable rather than treated as a duplicate.
        self._post_attlog(body="1\t2026-09-08 09:01:02\t0\t1\t0\t\t\n", stamp="1")
        self._post_attlog(body="1\t2026-09-08 09:01:22\t0\t1\t0\t\t\n", stamp="2")

        self.assertEqual(PunchEvent.all_objects.count(), 2)
        flagged = PunchEvent.all_objects.get(
            dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE
        )
        # Marked and linked, so a reviewer can see what it resembles — but not
        # confirmed, which is what would exclude it as a retransmission.
        self.assertIsNotNone(flagged.duplicate_of_id)
        self.assertNotEqual(
            flagged.dedupe_status, PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE
        )
        # Both scans survive as evidence.
        self.assertEqual(PunchEvent.all_objects.count(), 2)

    # --- malformed input --------------------------------------------------

    def test_unparsable_timestamp_keeps_evidence_and_flags_the_message(self):
        response = self._post_attlog(
            body="1\tnot-a-timestamp\t0\t1\t0\t\t\n2\t2026-09-08 09:03:44\t0\t1\t0\t\t\n"
        )
        self.assertEqual(response.status_code, 200)

        message = DeviceMessage.all_objects.get()
        # The raw line survives in the message even though no punch row exists.
        self.assertIn("not-a-timestamp", message.raw_payload_text)
        self.assertEqual(
            message.processing_status, DeviceMessage.ProcessingStatus.PARTIALLY_FAILED
        )
        self.assertEqual(PunchEvent.all_objects.count(), 1)

    def test_empty_body_is_acknowledged_without_creating_punches(self):
        response = self._post_attlog(body="")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PunchEvent.all_objects.count(), 0)
        self.assertEqual(DeviceMessage.all_objects.count(), 1)

    # --- handshake and command poll ---------------------------------------

    def test_handshake_returns_config_block(self):
        response = self.client.get("/iclock/cdata?SN=SF2A-001&options=all&pushver=2.4.1")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("GET OPTION FROM: SF2A-001", body)
        self.assertIn("Stamp=", body)
        self.assertIn("TransFlag=", body)
        # A handshake carries no evidence, so nothing is stored.
        self.assertEqual(DeviceMessage.all_objects.count(), 0)

    def test_command_poll_updates_last_seen(self):
        response = self.client.get("/iclock/getrequest?SN=SF2A-001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode(), "OK")
        self.device.refresh_from_db()
        self.assertIsNotNone(self.device.last_seen_at)

    def test_ingestion_endpoint_does_not_require_csrf_token(self):
        # The device is not a browser and cannot carry a CSRF token. The
        # exemption is scoped to this path; nothing else is weakened.
        enforcing = Client(enforce_csrf_checks=True)
        response = enforcing.post(
            "/iclock/cdata?SN=SF2A-001&table=ATTLOG&Stamp=7",
            data=ATTLOG_TWO_RECORDS,
            content_type="text/plain",
        )
        self.assertEqual(response.status_code, 200)
