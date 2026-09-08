"""Scenario tests: the acceptance claims, driven through the real endpoint.

These run the simulator's payloads against the ingestion view with Django's
test client, so routing, device authentication and the ack body are all
exercised. Simulator evidence only — see tests_ingestion.py for the same
caveat.
"""

from datetime import date, time

from django.test import Client, TestCase

from common.choices import DeviceAttendanceScope
from common.tenant import use_company
from devices.models import (
    BiometricDevice,
    DeviceEnrollment,
    DeviceMessage,
    DeviceModel,
    DeviceVendor,
    PunchEvent,
)
from devices.simulator import scenarios
from employees.models import Employee, EmployeeAssignment
from organization.models import Branch, Department, Designation
from scheduling.models import CompanyAttendanceSettings, Shift
from tenants.models import Company

ANCHOR = date(2026, 9, 8)
SERIAL = "SF2A-SIM"


class SimulatorScenarioTests(TestCase):
    def setUp(self):
        self.client = Client()
        vendor = DeviceVendor.objects.get_or_create(
            code="zkteco",
            defaults={"name": "ZKTeco", "adapter_key": "zkteco_adms_push"},
        )[0]
        self.device_model = DeviceModel.objects.get_or_create(
            vendor=vendor,
            model_code="senseface-2a",
            defaults={
                "name": "SenseFace 2A",
                "protocol": DeviceModel.Protocol.ADMS_PUSH,
            },
        )[0]
        self.company = Company.objects.create(code="A", slug="a", name="Company A")
        with use_company(self.company):
            self.branch = Branch.objects.create(
                code="HQ", name="HQ", is_default=True
            )
            department = Department.objects.create(
                branch=self.branch, code="SW", name="Software"
            )
            designation = Designation.objects.create(
                department=department, code="DEV", name="Developer"
            )
            shift = Shift.objects.create(
                code="GEN", name="General", start_time=time(9, 0),
                end_time=time(18, 0), scheduled_minutes=540,
            )
            CompanyAttendanceSettings.objects.create(
                company_shift=shift,
                device_attendance_scope=DeviceAttendanceScope.BRANCH_DEVICES,
                effective_from=self._past(),
            )
            self.device = BiometricDevice.objects.create(
                branch=self.branch,
                device_model=self.device_model,
                name="Front Door",
                serial_number=SERIAL,
                timezone="Asia/Dhaka",
                status=BiometricDevice.Status.ACTIVE,
            )
            self.employee = Employee.objects.create(
                first_name="Alice", last_name="Ahmed"
            )
            EmployeeAssignment.objects.create(
                employee=self.employee,
                employee_code="E-1",
                branch=self.branch,
                department=department,
                designation=designation,
                effective_from=self._past(),
            )
            DeviceEnrollment.objects.create(
                device=self.device,
                employee=self.employee,
                device_user_id="1",
                effective_from=self._past(),
                assigned_device_authorized=True,
            )

    def _past(self):
        from datetime import datetime, timezone as dt_timezone

        return datetime(2026, 1, 1, tzinfo=dt_timezone.utc)

    def _run(self, name, **kwargs):
        scenario = scenarios.build(name, anchor=ANCHOR, serial=SERIAL, **kwargs)
        responses = []
        for batch in scenario.batches:
            responses.append(
                self.client.post(
                    f"/iclock/cdata?SN={SERIAL}&table=ATTLOG&Stamp={batch.stamp}",
                    data=batch.body(),
                    content_type="text/plain",
                )
            )
        return scenario, responses

    def _punches(self):
        return PunchEvent.all_objects.filter(device=self.device)

    # --- the acceptance claims -------------------------------------------

    def test_single_day_produces_two_authorized_punches(self):
        _, responses = self._run("single_day")
        self.assertTrue(all(r.status_code == 200 for r in responses))
        self.assertEqual(self._punches().count(), 2)
        self.assertEqual(
            self._punches()
            .filter(authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED)
            .count(),
            2,
        )

    def test_replaying_the_same_fixture_twice_has_no_duplicate_effect(self):
        # The headline acceptance rule, stated as a countable fact.
        self._run("single_day")
        countable_after_first = self._countable()

        self._run("single_day")
        self.assertEqual(self._countable(), countable_after_first)

    def test_identical_batch_resend_stores_nothing_new(self):
        scenario, responses = self._run("replay")
        self.assertEqual(DeviceMessage.all_objects.count(), 1)
        self.assertEqual(self._punches().count(), 2)
        # Still acknowledged, so the device advances its pointer.
        self.assertEqual(responses[-1].status_code, 200)

    def test_resend_under_new_stamp_is_kept_but_not_counted_twice(self):
        self._run("resend_new_stamp")
        self.assertEqual(DeviceMessage.all_objects.count(), 2)
        # All four rows survive as evidence...
        self.assertEqual(self._punches().count(), 4)
        # ...but only the original two are countable.
        self.assertEqual(self._countable(), 2)
        self.assertEqual(
            self._punches()
            .filter(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .count(),
            2,
        )

    def test_rapid_repeat_is_reviewable_not_discarded(self):
        self._run("rapid_repeat")
        self.assertEqual(self._punches().count(), 2)
        self.assertEqual(
            self._punches()
            .filter(dedupe_status=PunchEvent.DedupeStatus.PROBABLE_DUPLICATE)
            .count(),
            1,
        )
        # Nothing was excluded as a duplicate on proximity alone.
        self.assertEqual(
            self._punches()
            .filter(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .count(),
            0,
        )

    def test_offline_backlog_arrives_exactly_once(self):
        scenario, responses = self._run("offline_backlog")
        self.assertTrue(all(r.status_code == 200 for r in responses))
        # 24 buffered punches, delivered in batches of 8, stored exactly once.
        self.assertEqual(self._punches().count(), 24)
        self.assertEqual(
            self._punches()
            .filter(dedupe_status=PunchEvent.DedupeStatus.CONFIRMED_DUPLICATE)
            .count(),
            0,
        )

    def test_backlog_redelivered_after_a_failed_ack_is_not_double_counted(self):
        # The realistic failure: the device did not see our OK and re-sends the
        # whole backlog. Nothing may be counted twice.
        self._run("offline_backlog")
        first_countable = self._countable()

        self._run("offline_backlog")
        self.assertEqual(self._countable(), first_countable)

    def test_unknown_user_is_preserved_for_the_unresolved_queue(self):
        self._run("unknown_user")
        punch = self._punches().get()
        self.assertEqual(
            punch.authorization_status,
            PunchEvent.AuthorizationStatus.UNKNOWN_EMPLOYEE,
        )
        self.assertIsNone(punch.employee_id)

    def test_malformed_row_does_not_lose_the_readable_ones(self):
        self._run("malformed")
        self.assertEqual(self._punches().count(), 2)
        message = DeviceMessage.all_objects.get()
        self.assertEqual(
            message.processing_status, DeviceMessage.ProcessingStatus.PARTIALLY_FAILED
        )
        self.assertIn("not-a-timestamp", message.raw_payload_text)

    def test_scenarios_are_byte_identical_when_rebuilt(self):
        # Repeatability is what makes a replay comparison meaningful.
        for name in scenarios.scenario_names():
            with self.subTest(scenario=name):
                first = scenarios.build(name, anchor=ANCHOR, serial=SERIAL)
                second = scenarios.build(name, anchor=ANCHOR, serial=SERIAL)
                self.assertEqual(
                    [b.body() for b in first.batches],
                    [b.body() for b in second.batches],
                )

    def _countable(self):
        """Punches that would reach the attendance engine."""
        return (
            self._punches()
            .filter(
                authorization_status=PunchEvent.AuthorizationStatus.AUTHORIZED,
                dedupe_status=PunchEvent.DedupeStatus.UNIQUE,
            )
            .count()
        )
