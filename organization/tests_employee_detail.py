"""An employee's history page, and ending their employment (plan step N6)."""

import datetime
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase
from django.urls import reverse

from accounts.models import CompanyMembership, User
from attendance import tests_live
from attendance.models import AttendanceRecord
from attendance.services import recalculate
from auditlog.models import AuditLog
from common.tenant import use_company
from devices.models import DeviceEnrollment
from employees.models import Employee, EmployeeAssignment, EmployeeCompensation
from employees.services import transfer_employee
from organization import employee_detail_services as services

DHAKA = datetime.timezone(datetime.timedelta(hours=6))
AUG_10 = datetime.date(2026, 8, 10)
AUG_11 = datetime.date(2026, 8, 11)
AUG_12 = datetime.date(2026, 8, 12)


def midnight(day):
    return datetime.datetime(day.year, day.month, day.day, tzinfo=DHAKA)


class DetailTestCase(TestCase):
    # The attendance live-test company: an admin, a 09:00-18:00 company shift,
    # and Rahim (placed and paid 30,000 from 1 Jan 2026) enrolled on a device.
    # Borrowed through the module so LiveTestCase is not collected twice.
    setUp_company = tests_live.LiveTestCase.setUp
    punch = tests_live.LiveTestCase.punch

    def setUp(self):
        self.setUp_company()

    def end(self, last_day=AUG_11, **kwargs):
        values = {
            "actor": self.admin, "company_id": self.company.pk,
            "employee_id": self.employee.pk, "last_day": last_day,
            "status": "resigned", "reason": "Moved abroad",
        }
        values.update(kwargs)
        return services.end_employment(**values)

    def give_login(self):
        user = User.objects.create_user(email="rahim@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=user, role=CompanyMembership.Role.EMPLOYEE,
            status=CompanyMembership.Status.ACTIVE,
        )
        Employee.all_objects.filter(pk=self.employee.pk).update(user=user)
        self.employee.refresh_from_db()
        return user

    def rows(self, model):
        with use_company(self.company):
            return list(model.objects.filter(employee=self.employee).order_by("effective_from"))


class EndEmploymentTests(DetailTestCase):
    def test_placement_and_salary_end_after_the_last_working_day(self):
        employee, _summary = self.end(AUG_11)
        self.assertEqual(employee.employment_status, "resigned")
        self.assertEqual(employee.leaving_date, AUG_11)
        [placement] = self.rows(EmployeeAssignment)
        [salary] = self.rows(EmployeeCompensation)
        self.assertEqual(placement.effective_to, midnight(AUG_12))
        self.assertEqual(salary.effective_to, midnight(AUG_12))
        self.assertEqual(placement.status, "ended")
        audit = AuditLog.objects.get(action="employee.employment_ended")
        self.assertEqual(audit.before_data["employment_status"], "active")
        self.assertEqual(audit.after_data["leaving_date"], "2026-08-11")
        self.assertEqual(audit.after_data["reason"], "Moved abroad")

    def test_the_last_day_still_counts_and_nothing_after_it(self):
        for day in (AUG_10, AUG_11, AUG_12):
            self.punch(day, 9)
            self.punch(day, 18)
        recalculate(self.company.pk, start=AUG_10, end=datetime.date(2026, 8, 20))
        self.end(AUG_11)
        with use_company(self.company):
            days = list(
                AttendanceRecord.objects.filter(employee=self.employee)
                .order_by("work_date").values_list("work_date", flat=True)
            )
        self.assertEqual(days, [AUG_10, AUG_11])

    def test_device_enrollments_end_with_an_audit_the_device_history_can_read(self):
        _employee, summary = self.end(AUG_11)
        self.assertEqual(summary["enrollments_ended"], 1)
        [enrollment] = self.rows(DeviceEnrollment)
        self.assertEqual(enrollment.effective_to, midnight(AUG_12))
        audit = AuditLog.objects.get(action="device_enrollment.ended_with_employment")
        self.assertIn("effective_to", audit.before_data)
        self.assertIsNone(audit.before_data["effective_to"])

    def test_enrollments_can_be_left_alone(self):
        _employee, summary = self.end(AUG_11, end_device_enrollments=False)
        self.assertEqual(summary["enrollments_ended"], 0)
        [enrollment] = self.rows(DeviceEnrollment)
        self.assertIsNone(enrollment.effective_to)

    def test_their_login_is_disabled_unless_asked_not_to(self):
        user = self.give_login()
        _employee, summary = self.end(AUG_11)
        self.assertTrue(summary["login_disabled"])
        self.assertEqual(
            CompanyMembership.all_objects.get(user=user, company=self.company).status,
            "suspended",
        )

    def test_the_login_can_be_kept(self):
        user = self.give_login()
        _employee, summary = self.end(AUG_11, disable_login=False)
        self.assertFalse(summary["login_disabled"])
        self.assertEqual(
            CompanyMembership.all_objects.get(user=user, company=self.company).status,
            "active",
        )

    def test_refusals(self):
        future = datetime.date(2099, 1, 1)
        cases = {
            "a future last day": {"last_day": future},
            "a day before the placement began": {"last_day": datetime.date(2025, 12, 31)},
            "no reason": {"reason": "  "},
            "a status that is not leaving": {"status": "suspended"},
        }
        for name, values in cases.items():
            with self.subTest(name):
                with self.assertRaises(ValidationError):
                    self.end(**values)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")

    def test_somebody_who_has_left_cannot_leave_again(self):
        self.end(AUG_11)
        with self.assertRaises(ValidationError):
            self.end(AUG_12)

    def test_a_finalised_salary_month_cannot_be_reopened(self):
        from payroll.models import PayrollPeriod, PayrollRun

        with use_company(self.company):
            period = PayrollPeriod.objects.create(
                company=self.company, name="Aug 2026",
                start_date=datetime.date(2026, 8, 1), end_date=datetime.date(2026, 8, 31),
            )
            PayrollRun.objects.create(
                company=self.company, payroll_period=period, status=PayrollRun.Status.POSTED,
            )
        with self.assertRaises(ValidationError) as caught:
            self.end(AUG_11)
        self.assertIn("finalised", str(caught.exception))
        self.end(datetime.date(2026, 8, 31))  # the month's last day is fine

    def test_a_salary_starting_after_the_last_day_is_refused_not_broken(self):
        from organization import employee_edit_services

        employee_edit_services.change_salary(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
            values={"pay_basis": "monthly", "base_rate": Decimal("35000"),
                    "effective_at": midnight(datetime.date(2026, 9, 1)), "reason": "Raise"},
        )
        with self.assertRaises(ValidationError) as caught:
            self.end(AUG_11)
        self.assertIn("salary starts", str(caught.exception))

    def test_hr_may_end_it_but_not_for_a_login_holder(self):
        # HR edits employees in every branch (Ajay, 2026-09-27), so it may end
        # employment - except someone with more than employee access.
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.end(AUG_11, actor=hr)
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "resigned")

    def test_another_companys_employee_is_not_found(self):
        from tenants.services import onboard_company

        other = onboard_company(code="OTH", slug="oth", name="Other Ltd")
        outsider = User.objects.create_user(email="admin@oth.test", password="pw")
        CompanyMembership.all_objects.create(
            company=other, user=outsider, role=CompanyMembership.Role.COMPANY_ADMIN,
            status=CompanyMembership.Status.ACTIVE,
        )
        with self.assertRaises(PermissionDenied):
            self.end(AUG_11, actor=outsider, company_id=other.pk)


class HistoryTests(DetailTestCase):
    def test_the_history_reads_a_login_outside_a_request(self):
        """Its branches are tenant-scoped; a service call has no middleware."""
        user = self.give_login()
        page = services.employee_history(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
        )
        self.assertEqual(page["login"].user, user)

    def test_the_history_holds_every_placement_salary_and_device(self):
        transfer_employee(
            employee=self.employee, effective_at=midnight(datetime.date(2026, 6, 1)),
            employee_code="E1-B", reason="Promoted", actor=self.admin,
        )
        page = services.employee_history(
            actor=self.admin, company_id=self.company.pk, employee_id=self.employee.pk,
        )
        placements = page["placements"]
        self.assertEqual([p.row.employee_code for p in placements], ["E1-B", "E1"])
        self.assertTrue(placements[0].is_current)
        # The old placement's last day is the day before the new one began.
        self.assertEqual(placements[1].last_day, datetime.date(2026, 5, 31))
        self.assertEqual(len(page["salaries"]), 1)
        self.assertEqual(len(page["devices"]), 1)
        self.assertFalse(page["is_ended"])


class PageTests(DetailTestCase):
    def test_bulk_fingerprint_fetches_scoped_backups_and_restores_selected_users(self):
        from cryptography.fernet import Fernet

        from devices.services import biometric_pilot, commands, verification

        self.client.force_login(self.admin)
        self.device.firmware_version = "ZAM70-NF24HA-Ver3.0.15"
        self.device.save(update_fields=["firmware_version"])
        selected = [self.employee.pk]
        for pin in ("2", "3", "4"):
            employee = Employee.all_objects.create(company=self.company, first_name=f"Employee {pin}")
            DeviceEnrollment.all_objects.create(
                company=self.company, device=self.device, employee=employee,
                device_user_id=pin, effective_from=self.enrollment.effective_from,
            )
            if pin != "4":
                selected.append(employee.pk)
        url = reverse("organization:employee_verification_bulk")
        with self.settings(TERMINAL_CARD_PILOT_SERIAL="",
                           TERMINAL_CARD_PILOT_USER_IDS=[],
                           TERMINAL_FINGERPRINT_PILOT_ENABLED=False,
                           TERMINAL_FINGERPRINT_DEVICE_WIDE_ENABLED=False,
                           BIOMETRIC_TEMPLATE_KEY=Fernet.generate_key().decode("ascii")):
            from devices.services import terminal_card

            controls = reverse("devices:device_terminal_verification", args=[self.device.public_id])
            response = self.client.post(controls, {"card": "on", "fingerprint": "on"}, follow=True)
            self.assertContains(response, "Terminal verification controls saved")
            self.device.refresh_from_db()
            self.assertTrue(terminal_card.enabled(self.device, "2"))
            self.assertTrue(biometric_pilot.fingerprint_enabled(self.device, "2"))
            self.assertEqual(commands.take_pending_commands(self.device), ("", []))
            data = {"employee": selected, "blocked_methods": ["fingerprint"]}
            response = self.client.post(url, {**data, "operation": "block"}, follow=True)
            self.assertContains(response, "Waiting for fingerprint backup")
            pending_writes = []
            block_wire = ""
            for _ in range(3):
                wire, entries = commands.take_pending_commands(self.device)
                reads = [entry for entry in entries if entry["key"].startswith("pilot_bio_read:")]
                self.assertEqual(len(reads), 1)
                self.assertNotIn("Pin=4", wire)
                pending_writes.extend(entry for entry in entries if entry not in reads)
                block_wire += "\n".join(line for line in wire.splitlines() if "Valid=0" in line)
                read = reads[0]
                pin = read["key"].split(":")[1]
                self.assertEqual(commands.take_pending_commands(self.device), ("", []))
                commands.note_results(self.device, f"ID={read['id']}&Return={int(pin != '3')}&CMD=DATA QUERY")
                if pin != "3":
                    biometric_pilot.finish_fingerprint_backups(self.device)
                    self.assertEqual(commands.take_pending_commands(self.device), ("", []))
                    biometric_pilot.capture_reply(
                        self.device, str(read["id"]),
                        f"biodata pin={pin}\ttype=1\tno=6\tindex=0\tvalid=1"
                        "\tduress=0\tmajorver=13\tminorver=0\tformat=0\ttmp=FINGER",
                    )
                biometric_pilot.finish_fingerprint_backups(self.device)
            biometric_pilot.finish_fingerprint_backups(self.device)
            wire, writes = commands.take_pending_commands(self.device)
            writes += pending_writes
            self.assertEqual(len(writes), 2)
            self.assertEqual((wire + block_wire).count("Valid=0"), 2)
            self.assertNotIn("Pin=3", wire + block_wire)
            for write in writes:
                commands.note_results(self.device, f"ID={write['id']}&Return=0&CMD=DATA UPDATE")
            response = self.client.get(reverse("employee_list"), {"blocked_method": "fingerprint"})
            self.assertContains(response, "Acknowledged · verify at the reader", count=2)
            self.assertContains(response, "HRM only")
            response = self.client.post(controls, {"card": "on"}, follow=True)
            self.assertContains(response, "Unblock the affected methods")
            self.client.post(url, {**data, "operation": "block"}, follow=True)
            wire, retry = commands.take_pending_commands(self.device)
            self.assertEqual(len(retry), 1)
            self.assertIn("Pin=3", wire)
            self.client.post(url, {**data, "operation": "unblock"}, follow=True)
            wire, restores = commands.take_pending_commands(self.device)
            self.assertEqual(len(restores), 2)
            self.assertEqual(wire.count("Valid=1"), 2)

    def test_fingerprint_pilot_changes_only_that_template_and_restores_it(self):
        from cryptography.fernet import Fernet

        from devices.models import DeviceOutboxCommand
        from devices.services import commands, templates

        self.client.force_login(self.admin)
        self.device.firmware_version = "ZAM70-NF24HA-Ver3.0.15"
        self.device.save(update_fields=["firmware_version"])
        url = reverse("organization:employee_verification_methods", args=[self.employee.public_id])
        with self.settings(TERMINAL_CARD_PILOT_SERIAL=self.device.serial_number,
                           TERMINAL_CARD_PILOT_USER_IDS=["1"], TERMINAL_FINGERPRINT_PILOT_ENABLED=True,
                           BIOMETRIC_TEMPLATE_KEY=Fernet.generate_key().decode("ascii")):
            entries, _ = commands._queue_group(
                device=self.device, commands=[("pilot_bio_read:1:1", "DATA QUERY tablename=biodata")],
            )
            commands.take_pending_commands(self.device)
            commands.note_results(self.device, f"ID={entries[0]['id']}&Return=1&CMD=DATA QUERY")
            templates.save_from_payload(
                self.device,
                "biodata pin=1\ttype=1\tno=6\tindex=0\tvalid=1\tduress=0\tmajorver=13\tminorver=0\tformat=0\ttmp=FINGER",
            )

            response = self.client.post(url, {"blocked_methods": ["fingerprint", "face"]}, follow=True)
            self.assertContains(response, "Fingerprint updates queued")
            self.assertContains(response, "Face is blocked in HRM only. The reader can still accept face scans.")
            pin_writes, _ = commands.take_pending_commands(self.device)
            self.assertIn("Pin=1\tNo=6\tIndex=0\tValid=0\tDuress=0\tType=1\t", pin_writes)
            self.assertNotIn("Type=9", pin_writes)

            self.client.post(url, {"blocked_methods": ["face"]}, follow=True)
            pin_writes, _ = commands.take_pending_commands(self.device)
            self.assertIn("Pin=1\tNo=6\tIndex=0\tValid=1\tDuress=0\tType=1\t", pin_writes)
            self.assertIn("Tmp=FINGER", pin_writes)
            self.assertEqual(DeviceOutboxCommand.all_objects.filter(key__startswith="terminal_fingerprint:").count(), 2)

    def test_card_pilot_clears_and_restores_only_the_allowed_enrollment(self):
        from devices.models import DeviceMessage, DeviceOutboxCommand, DeviceUser
        from devices.services.commands import queue_user_push, take_pending_commands
        from django.utils import timezone

        self.client.force_login(self.admin)
        self.device.firmware_version = "ZAM70-NF24HA-Ver3.0.15"
        self.device.save(update_fields=["firmware_version"])
        message = DeviceMessage.all_objects.create(
            company=self.company, device=self.device, branch=self.branch,
            message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT, received_at=timezone.now(),
            raw_payload_text="user pin=1\tname=Rahim\tcardno=412345\tgroup=1\tprivilege=0"
                             "\tdisable=0\tverify=0\tstarttime=0\tendtime=0\tpassword=",
        )
        from devices.services import device_users

        device_users.record_message(self.device, message)
        url = reverse("organization:employee_verification_methods", args=[self.employee.public_id])
        with self.settings(TERMINAL_CARD_PILOT_SERIAL=self.device.serial_number,
                           TERMINAL_CARD_PILOT_USER_IDS=["1"]):
            response = self.client.post(url, {"blocked_methods": ["card"]}, follow=True)
            self.assertContains(response, "Card updates queued")
            self.assertContains(response, "Card pilot · Waiting for the device")
            queue_user_push(device=self.device, device_user_id="1", name="Rahim", card_number="412345")
            queue_user_push(device=self.device, device_user_id="2", name="Other", card_number="654321")
            wire, _ = take_pending_commands(self.device)
            self.assertEqual(wire.count("Pin=1\tName=Rahim\tCardNo=\t"), 2)
            self.assertIn("Pin=2\tName=Other\tCardNo=654321\t", wire)

            self.client.post(url, {}, follow=True)
            wire, _ = take_pending_commands(self.device)
            self.assertIn("Pin=1\tName=Rahim\tCardNo=412345\t", wire)
            self.assertEqual(DeviceOutboxCommand.all_objects.filter(key__startswith="terminal_card:").count(), 2)
        self.assertEqual(DeviceUser.all_objects.get(device=self.device, pin="1").card_number, "412345")

    def test_verification_methods_are_saved_from_the_profile(self):
        from devices.models import DeviceMessage, DeviceOutboxCommand, DeviceUser
        from devices.services.verification import blocked_at
        from django.utils import timezone

        self.enrollment.card_number = "412345"
        self.enrollment.save(update_fields=["card_number"])
        DeviceMessage.all_objects.create(
            company=self.company, device=self.device, branch=self.branch,
            message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT,
            received_at=timezone.now(),
            raw_payload_text="user pin=1\tname=Rahim\tcardno=412345\tgrp=2\tprivilege=0",
        )
        self.client.force_login(self.admin)
        url = reverse("organization:employee_verification_methods", args=[self.employee.public_id])
        response = self.client.post(url, {"blocked_methods": ["card", "face"]}, follow=True)
        self.assertContains(response, "Card blocked")
        self.assertContains(response, "Face blocked")
        self.assertContains(response, "Fingerprint allowed")
        self.assertContains(response, "Face is blocked in HRM only.")
        self.assertContains(response, "Card blocked in HRM.")
        self.assertEqual(blocked_at(self.company.pk, self.employee.pk, timezone.now()), ["face", "card"])

        response = self.client.post(url, {"blocked_methods": ["face", "fingerprint", "card"]}, follow=True)
        self.assertContains(response, "Fingerprint blocked")
        self.assertContains(response, "Face blocked")
        self.assertContains(response, "Card blocked")
        self.assertEqual(blocked_at(self.company.pk, self.employee.pk, timezone.now()),
                         ["fingerprint", "face", "card"])

        response = self.client.post(url, {}, follow=True)
        self.assertContains(response, "Card allowed")
        self.assertContains(response, "Fingerprint, Card unblocked in HRM.")
        self.assertContains(response, "Face is allowed again for attendance in HRM.")
        self.assertContains(response, "enroll it again on each linked terminal")
        self.assertEqual(blocked_at(self.company.pk, self.employee.pk, timezone.now()), [])
        self.enrollment.refresh_from_db()
        self.assertEqual(self.enrollment.card_number, "412345")
        self.assertEqual(DeviceUser.all_objects.get(device=self.device, pin="1").card_number, "412345")
        self.assertFalse(DeviceOutboxCommand.all_objects.filter(device=self.device).exists())

    def test_bulk_verification_applies_selected_methods_and_respects_filters(self):
        from devices.models import DeviceOutboxCommand
        from devices.services.verification import blocked_at
        from django.utils import timezone

        second = Employee.all_objects.create(company=self.company, first_name="Second Employee")
        url = reverse("organization:employee_verification_bulk")
        employees_url = reverse("employee_list")
        normal = self.client.get(employees_url)
        self.assertContains(normal, "Blocked verification methods")
        self.assertNotContains(normal, 'data-verification-mode="block"')
        central = self.client.get(employees_url, {"blocked_method": "all"})
        self.assertContains(central, 'data-verification-mode="block"')
        self.assertContains(central, 'data-verification-mode="unblock"')
        self.assertEqual({row["e"].pk for row in central.context["rows"]}, {self.employee.pk, second.pk})
        self.assertContains(self.client.get(reverse("organization:employee_detail", args=[self.employee.public_id])),
                            "Manage verification methods")
        response = self.client.post(url, {"employee": [self.employee.pk, second.pk],
                                         "operation": "block", "blocked_methods": ["card"]},
                                    query_params={"blocked_method": "all"})
        self.assertEqual(response.status_code, 302)
        self.assertIn("blocked_method=all#terminal-updates", response.url)
        self.client.post(url, {"all": "1", "operation": "block", "blocked_methods": ["face"]},
                         query_params={"search[value]": "Rahim"})
        self.assertEqual(blocked_at(self.company.pk, self.employee.pk, timezone.now()), ["face", "card"])
        self.assertEqual(blocked_at(self.company.pk, second.pk, timezone.now()), ["card"])

        response = self.client.post(url, {"employee": [self.employee.pk, second.pk],
                                         "operation": "unblock", "blocked_methods": ["card", "fingerprint"]},
                                    follow=True)
        self.assertContains(response, "Card unblocked in HRM.")
        self.assertContains(response, "enroll it again on each linked terminal")
        self.assertEqual(blocked_at(self.company.pk, self.employee.pk, timezone.now()), ["face"])
        self.assertEqual(blocked_at(self.company.pk, second.pk, timezone.now()), [])
        self.assertFalse(DeviceOutboxCommand.all_objects.filter(device=self.device).exists())

    def test_bulk_card_pilot_updates_and_restores_only_selected_enrollments(self):
        from django.utils import timezone

        from devices.models import DeviceMessage
        from devices.services import biometric_pilot, commands, device_users, verification

        self.device.firmware_version = "ZAM70-NF24HA-Ver3.0.15"
        self.device.save(update_fields=["firmware_version"])
        selected = [self.employee.pk]
        for pin, name in (("2", "Second"), ("3", "Unselected"), ("4", "HRM only")):
            employee = Employee.all_objects.create(company=self.company, first_name=name)
            DeviceEnrollment.all_objects.create(
                company=self.company, device=self.device, employee=employee,
                device_user_id=pin, effective_from=self.enrollment.effective_from,
            )
            if pin == "3":
                unselected = employee
            else:
                selected.append(employee.pk)
        message = DeviceMessage.all_objects.create(
            company=self.company, device=self.device, branch=self.branch,
            message_type=DeviceMessage.MessageType.ENROLLMENT_RESULT, received_at=timezone.now(),
            raw_payload_text="\n".join(
                f"user pin={pin}\tname={name}\tcardno={card}\tgroup=1\tprivilege=0"
                "\tdisable=0\tverify=0\tstarttime=0\tendtime=0\tpassword="
                for pin, name, card in (("1", "Rahim", "123456"), ("2", "Second", "223456"),
                                        ("3", "Unselected", "323456"), ("4", "HRM only", ""))
            ),
        )
        device_users.record_message(self.device, message)
        url = reverse("organization:employee_verification_bulk")
        with self.settings(TERMINAL_CARD_PILOT_SERIAL=self.device.serial_number,
                           TERMINAL_CARD_PILOT_USER_IDS=["1"],
                           TERMINAL_CARD_ONLY_PILOT_USER_IDS=[],
                           TERMINAL_CARD_DEVICE_WIDE_ENABLED=True,
                           TERMINAL_FINGERPRINT_PILOT_ENABLED=True):
            self.assertTrue(biometric_pilot.fingerprint_enabled(self.device, "1"))
            self.assertFalse(biometric_pilot.fingerprint_enabled(self.device, "2"))
            for operation, card_one, card_two in (("block", "", ""), ("unblock", "123456", "223456")):
                selection = {"employee": selected} if operation == "block" else {"all": "1"}
                filters = {"blocked_method": "card"} if operation == "unblock" else None
                response = self.client.post(url, {**selection, "operation": operation, "blocked_methods": ["card"]},
                                            query_params=filters, follow=True)
                self.assertContains(response, "Card updates queued for 2 terminal enrollment(s).")
                self.assertTrue(response.redirect_chain[-1][0].endswith("#terminal-updates"))
                self.assertContains(response, "Waiting for the device", count=2)
                self.assertContains(response, "HRM updated · no terminal command", count=1)
                self.assertContains(response, "Reader reported no card to block" if operation == "block"
                                    else "No credential was saved by an earlier block")
                self.assertContains(response, "3 selected ·")
                wire, entries = commands.take_pending_commands(self.device)
                self.assertEqual(len(entries), 2)
                self.assertIn(f"Pin=1\tName=Rahim\tCardNo={card_one}\t", wire)
                self.assertIn(f"Pin=2\tName=Second\tCardNo={card_two}\t", wire)
                self.assertEqual(verification.blocked_at(self.company.pk, unselected.pk, timezone.now()), [])

            commands.note_results(self.device, f"ID={entries[0]['id']}&Return=0&CMD=DATA UPDATE\n"
                                              f"ID={entries[1]['id']}&Return=-1004&CMD=DATA UPDATE")
            listing = reverse("employee_list")
            refreshed = self.client.get(listing, {"blocked_method": "card"})
            self.assertContains(refreshed, "Terminal updates · Bulk unblock")
            self.assertContains(refreshed, "Acknowledged · verify at the reader", count=1)
            self.assertContains(refreshed, "Refused by the device", count=1)
            receipt = refreshed.context["bulk_terminal_updates"]
            self.assertEqual({row["user_id"] for row in receipt["rows"]}, {"1", "2", "4"})
            self.assertEqual(receipt["command_count"], 2)
            self.assertEqual(self.client.get(listing, {"blocked_method": "card", "table": "1"}).json()["data"], [])

            repeated = self.client.post(url, {"employee": selected, "operation": "unblock",
                                             "blocked_methods": ["card"]}, follow=True)
            self.assertContains(repeated, "None of the selected employees have the chosen methods blocked.")
            self.assertEqual(commands.take_pending_commands(self.device), ("", []))

    def test_blocked_methods_list_uses_current_policy_and_preserves_other_methods(self):
        from django.utils import timezone

        from devices.models import EmployeeVerificationPolicy
        from devices.services import verification

        restored = Employee.all_objects.create(company=self.company, first_name="Restored")
        face_only = Employee.all_objects.create(company=self.company, first_name="Face only")
        now = timezone.now()
        for employee, minutes, methods in (
            (self.employee, -2, ["card", "face"]),
            (self.employee, 10, []),
            (restored, -3, ["card"]),
            (restored, -1, []),
            (face_only, -2, ["face"]),
        ):
            EmployeeVerificationPolicy.all_objects.create(
                company=self.company, employee=employee, blocked_methods=methods,
                effective_at=now + datetime.timedelta(minutes=minutes),
            )
        listing = reverse("employee_list")
        response = self.client.get(listing, {"blocked_method": "any"})
        self.assertContains(response, "Unblock verification methods")
        self.assertContains(response, "Blocked employees")
        self.assertEqual(response.context["verification_scope_links"][1],
                         {"label": "Blocked employees", "url": "?blocked_method=any", "active": True})
        self.assertEqual({row["e"].pk: row["blocked_method_labels"] for row in response.context["rows"]},
                         {self.employee.pk: ["Face", "Card"], face_only.pk: ["Face"]})
        response = self.client.get(listing, {"blocked_method": "card", "table": "1"})
        self.assertEqual(response.json()["recordsFiltered"], 1)

        bulk = reverse("organization:employee_verification_bulk")
        preview = self.client.post(bulk, {"preview": "1", "all": "1"},
                                   query_params={"blocked_method": "card", "search[value]": "Rahim"})
        self.assertEqual(preview.json(), {"selected_count": 1, "counts": {"card": 1, "fingerprint": 0, "face": 1}})
        wrong = self.client.post(bulk, {"employee": [self.employee.pk], "operation": "unblock",
                                        "blocked_methods": ["fingerprint"]}, follow=True)
        self.assertContains(wrong, "None of the selected employees have the chosen methods blocked.")
        self.assertEqual(verification.blocked_at(self.company.pk, self.employee.pk, timezone.now()), ["card", "face"])

        response = self.client.post(bulk,
                                    {"all": "1", "operation": "unblock", "blocked_methods": ["card"]},
                                    query_params={"blocked_method": "card"}, follow=True)
        self.assertContains(response, "No employees match with a blocked method")
        self.assertEqual(verification.blocked_at(self.company.pk, self.employee.pk, timezone.now()), ["face"])
        self.assertEqual(verification.blocked_at(self.company.pk, face_only.pk, timezone.now()), ["face"])

    def test_employee_cannot_change_their_own_verification_methods(self):
        self.client.force_login(self.give_login())
        url = reverse("organization:employee_verification_methods", args=[self.employee.public_id])
        self.assertEqual(self.client.post(url, {"blocked_methods": ["card"]}).status_code, 403)
        bulk = reverse("organization:employee_verification_bulk")
        self.assertEqual(self.client.post(bulk, {"all": "1", "operation": "block",
                                                "blocked_methods": ["card"]}).status_code, 403)

    def setUp(self):
        super().setUp()
        self.client.force_login(self.admin)
        self.detail = reverse("organization:employee_detail", args=[self.employee.pk])
        self.end_url = reverse("organization:employee_end", args=[self.employee.pk])

    def test_the_history_page(self):
        response = self.client.get(self.detail)
        self.assertEqual(response.status_code, 200)
        for text in ("Employment history", "Salary history", "30,000.00", "Devices",
                     "Attendance report", self.end_url,
                     reverse("organization:employee_edit", args=[self.employee.pk])):
            with self.subTest(text=text):
                self.assertContains(response, text)

    def test_the_employee_list_links_to_it(self):
        self.assertContains(self.client.get(reverse("employee_list")), self.detail)

    def test_ending_employment_from_the_screen(self):
        self.give_login()
        page = self.client.get(self.end_url)
        self.assertContains(page, "Disable their login")
        self.assertContains(page, "End their device access")
        response = self.client.post(self.end_url, {
            "last_day": "2026-08-11", "status": "resigned", "reason": "Moved abroad",
            "disable_login": "on", "end_device_enrollments": "on",
        })
        self.assertRedirects(response, self.detail)
        after = self.client.get(self.detail)
        self.assertContains(after, "last working day 11 Aug 2026")
        self.assertNotContains(after, self.end_url)

    def test_a_refused_date_shows_next_to_the_field(self):
        response = self.client.post(self.end_url, {
            "last_day": "2099-01-01", "status": "resigned", "reason": "x",
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "on or after their last working day")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")

    def test_a_leaver_cannot_be_ended_twice_from_the_screen(self):
        self.end(AUG_11)
        response = self.client.get(self.end_url)
        self.assertRedirects(response, self.detail)

    def test_hr_opens_it_without_pay(self):
        # HR views and edits employees in every branch; salary only where the
        # Access page gave it (Ajay, 2026-09-27).
        hr = User.objects.create_user(email="hr@liv.test", password="pw")
        CompanyMembership.all_objects.create(
            company=self.company, user=hr, role=CompanyMembership.Role.HR,
            status=CompanyMembership.Status.ACTIVE,
        )
        self.client.force_login(hr)
        page = self.client.get(self.detail)
        self.assertEqual(page.status_code, 200)
        self.assertFalse(page.context["may"]["salary"])
        self.assertNotContains(page, "Salary history")
        self.employee.refresh_from_db()
        self.assertEqual(self.employee.employment_status, "active")
